import os
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
import hashlib
import numpy as np
from dotenv import load_dotenv
from core.logger import get_logger

log = get_logger("memory")

_MEM_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fox-mem")


class FoxBrain:
    def __init__(self, user_id: str = "default", vault_path: str = "brain/vault"):
        load_dotenv()

        self.user_id = user_id
        self.vault_path = Path(vault_path)
        self.raw_path = self.vault_path / "raw"
        self.conflicts_path = self.vault_path / "conflicts"

        self.raw_path.mkdir(parents=True, exist_ok=True)
        self.conflicts_path.mkdir(parents=True, exist_ok=True)

        self._groq_key = os.getenv("GROQ_API_KEY")
        self._groq_client_obj = None
        if not self._groq_key:
            log.warning("GROQ_API_KEY not found — memory entity extraction disabled")

        # ── Lazy FastEmbed ─────────────────────────────────────────────
        # Embeddings are stored as BLOBs in the SQLite facts table and
        # recalled with numpy cosine similarity. No separate vector DB.
        self._embedding_model = None
        self._embedding_ready = threading.Event()
        self._embedding_lock = threading.Lock()
        self._embedding_init_started = False

        # Defer semantic store init until after first frame rendered.
        # Callers use start_embedding_warmup() or _ensure_embeddings().
        # Keyword-only fallback remains fully functional at all times.

        # SQLite FTS5 for exact-match keyword search and temporal validity.
        # check_same_thread=False permits worker threads (async pipeline)
        # to use the same connection; all writes serialise via _db_lock.
        self._init_sqlite_fts()

    # ── Lazy FastEmbed + ChromaDB initialisation ──────────────────────

    def start_embedding_warmup(self) -> None:
        """Kick off FastEmbed + Chroma init in a background worker thread.

        Safe to call multiple times: the initialisation only runs once.
        Memory operations gracefully fall back to keyword-only FTS until
        the embeddings subsystem is ready.
        """
        with self._embedding_lock:
            if self._embedding_init_started:
                return
            self._embedding_init_started = True
        _MEM_WORKERS.submit(self._init_embeddings_background)

    def _init_embeddings_background(self):
        try:
            from fastembed import TextEmbedding as _TE
        except Exception as e:
            log.warning("fastembed not available — semantic memory disabled: %s", e)
            self._embedding_ready.set()
            return
        try:
            log.info("embedding warmup: loading BAAI/bge-small-en-v1.5")
            self._embedding_model = _TE(model_name="BAAI/bge-small-en-v1.5")
        except Exception as e:
            log.warning("could not load FastEmbed model: %s", e)
            self._embedding_model = None
            self._embedding_ready.set()
            return
        # Embeddings live in SQLite; backfill any facts captured before the
        # embedding column existed so semantic recall covers everything.
        try:
            self._ensure_embedding_column()
            self._backfill_embeddings()
            log.info("semantic memory ready (SQLite + numpy)")
        except Exception as e:
            log.warning("could not backfill embeddings: %s — keyword-only memory", e)
        finally:
            self._embedding_ready.set()

    def _ensure_embeddings(self, timeout_s: float = 5.0):
        """Return the FastEmbed model if loaded, else wait briefly.

        Returns None if the embedding model is not ready in ``timeout_s``;
        callers should gracefully skip semantic indexing/retrieval.
        """
        if self._embedding_model is not None:
            return self._embedding_model
        if not self._embedding_init_started:
            self.start_embedding_warmup()
        self._embedding_ready.wait(timeout=timeout_s)
        return self._embedding_model

    # ── Async pipeline wrappers ───────────────────────────────────────

    def capture_async(
        self,
        text: str,
        user_id: Optional[str] = None,
        on_done: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ):
        """Run :meth:`capture` in a background worker thread, invoke callbacks via Qt event loop."""
        import sys as _sys
        from PyQt6.QtCore import QObject, pyqtSignal

        class _Bridge(QObject):
            done = pyqtSignal(list)
            error = pyqtSignal(object)

        bridge = _Bridge()
        if on_done is not None:
            bridge.done.connect(on_done)
        if on_error is not None:
            bridge.error.connect(on_error)

        def _worker():
            try:
                result = self.capture(text, user_id=user_id)
            except Exception as e:  # pragma: no cover - defensive
                log.error("capture_async worker suppressed: %s", e)
                try:
                    bridge.error.emit(e)
                except Exception:
                    pass
                return
            try:
                bridge.done.emit(result)
            except Exception:
                pass

        _MEM_WORKERS.submit(_worker)

    def retrieve_async(
        self,
        query: str,
        user_id: Optional[str] = None,
        top_k: int = 5,
        include_historical: bool = False,
        on_done: Optional[Callable[[Dict[str, List[Dict[str, Any]]]], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ):
        """Run :meth:`retrieve` in a background worker thread, marshal results back via Qt signals."""
        from PyQt6.QtCore import QObject, pyqtSignal

        class _Bridge(QObject):
            done = pyqtSignal(dict)
            error = pyqtSignal(object)

        bridge = _Bridge()
        if on_done is not None:
            bridge.done.connect(on_done)
        if on_error is not None:
            bridge.error.connect(on_error)

        def _worker():
            try:
                result = self.retrieve(
                    query, user_id=user_id, top_k=top_k, include_historical=include_historical
                )
            except Exception as e:  # pragma: no cover - defensive
                log.error("retrieve_async worker suppressed: %s", e)
                try:
                    bridge.error.emit(e)
                except Exception:
                    pass
                return
            try:
                bridge.done.emit(result)
            except Exception:
                pass

        _MEM_WORKERS.submit(_worker)

    def _init_sqlite_fts(self):
        """Initialize SQLite FTS5 for exact-match keyword search and temporal validity.

        ``check_same_thread=False`` permits the capture/retrieve worker threads
        created by the async pipeline (Phase 2 Step 2.2) to share a single
        connection safely for read operations. All writes still funnel through
        the connection-level lock serialised by CPython's GIL; for true
        concurrent insertions, replace this with a dedicated DB-worker queue.
        """
        import threading
        self.db_path = self.vault_path / "keyword_search.db"
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self._db_lock = threading.Lock()

        with self._db_lock:
            # Create main facts table with temporal validity
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    user_id TEXT NOT NULL,
                    entity_key TEXT,
                    valid_from TIMESTAMP NOT NULL,
                    valid_to TIMESTAMP NULL,
                    superseded_by TEXT NULL,
                    created_at TIMESTAMP NOT NULL,
                    embedding BLOB
                )
            """)

            # Create FTS5 virtual table for full-text search
            self.cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts 
                USING fts5(id, content, metadata, user_id, entity_key)
            """)

            # Create indexes for temporal queries
            self.cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_key 
                ON facts(entity_key, valid_to)
            """)

            self.cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_valid 
                ON facts(user_id, valid_to)
            """)

            self.conn.commit()

        # Existing databases predate the embedding column; add it now so
        # semantic recall (and INSERTs) work immediately.
        self._ensure_embedding_column()

    def _ensure_embedding_column(self):
        """Add the ``embedding`` BLOB column to ``facts`` if missing."""
        with self._db_lock:
            cols = [r["name"] for r in self.cursor.execute("PRAGMA table_info(facts)").fetchall()]
            if "embedding" not in cols:
                self.cursor.execute("ALTER TABLE facts ADD COLUMN embedding BLOB")
                self.conn.commit()
                log.info("added 'embedding' column to facts table")

    def _embed_text(self, text: str, timeout_s: float = 3.0):
        """Return a float32 numpy vector for ``text``, or None if not ready.

        Blocks briefly for the lazy FastEmbed warmup, matching the previous
        ChromaDB timeout behaviour.
        """
        embedder = self._ensure_embeddings(timeout_s=timeout_s)
        if embedder is None:
            return None
        try:
            embs = list(embedder.embed([text]))
            if not embs or embs[0] is None:
                return None
            return np.asarray(embs[0], dtype=np.float32)
        except Exception as e:
            log.warning("embedding failed: %s", e)
            return None

    def _backfill_embeddings(self):
        """Embed every stored fact that has no embedding yet."""
        embedder = self._embedding_model
        if embedder is None:
            return
        with self._db_lock:
            self.cursor.execute("SELECT id, content FROM facts WHERE embedding IS NULL")
            rows = self.cursor.fetchall()
        if not rows:
            return
        for row in rows:
            try:
                embs = list(embedder.embed([row["content"]]))
                if embs and embs[0] is not None:
                    blob = np.asarray(embs[0], dtype=np.float32).tobytes()
                    with self._db_lock:
                        self.cursor.execute(
                            "UPDATE facts SET embedding = ? WHERE id = ?", (blob, row["id"])
                        )
                        self.conn.commit()
            except Exception as e:
                log.warning("backfill embedding failed for %s: %s", row["id"], e)

    def _generate_slug(self, text: str) -> str:
        """Generate URL-friendly slug from text."""
        # Simple slug generation
        text = text.lower()
        text = ''.join(c if c.isalnum() or c in ['-', '_'] else '-' for c in text)
        text = text.strip('-')
        return text[:50]  # Limit length
        
    def _get_fact_id(self, fact: Dict[str, Any]) -> str:
        """Generate consistent ID for a fact."""
        content = fact.get('content', '')
        return hashlib.md5(content.encode()).hexdigest()[:12]

    @property
    def groq_client(self):
        """Lazily import modern Groq and create the client on first use."""
        if self._groq_client_obj is None and self._groq_key:
            from groq import Groq
            self._groq_client_obj = Groq(api_key=self._groq_key)
        return self._groq_client_obj
    
    def _extract_entity_key(self, fact_text: str) -> str:
        """Extract a normalized entity key using Groq."""
        if self.groq_client is None:
            # Fallback: simple keyword extraction
            words = fact_text.lower().split()
            if len(words) >= 2:
                return f"user.{'_'.join(words[:2])}"
            return f"user.{words[0] if words else 'unknown'}"
        
        try:
            prompt = f"""Extract a short normalized subject key (2-4 words, lowercase, e.g. 'user.location', 'user.pet_name') for this fact: {fact_text}
Respond with only the key, nothing else."""
            
            response = self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b-versatile",
                temperature=0.1,
                max_tokens=50
            )
            
            entity_key = response.choices[0].message.content.strip().lower()
            # Clean up the response
            entity_key = entity_key.replace('"', '').replace("'", "")
            return entity_key if entity_key else "user.unknown"
            
        except Exception as e:
            log.warning("Entity extraction via Groq failed: %s — falling back to simple keyword extraction", e)
            words = fact_text.lower().split()
            if len(words) >= 2:
                return f"user.{'_'.join(words[:2])}"
            return f"user.{words[0] if words else 'unknown'}"
        
    def capture(self, conversation_text: str, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Capture conversation text with conflict detection and temporal validity.
        
        Args:
            conversation_text: The conversation text to process
            user_id: User identifier (defaults to instance user_id)
            
        Returns:
            List of stored facts with their IDs
        """
        if user_id is None:
            user_id = self.user_id
            
        # Extract entity key
        entity_key = self._extract_entity_key(conversation_text)
        
        # Create fact object
        current_time = datetime.now()
        fact = {
            'content': conversation_text,
            'metadata': {
                'timestamp': current_time.isoformat(),
                'entities': [],
                'tags': ['captured']
            },
            'entity_key': entity_key
        }
        fact_id = self._get_fact_id(fact)
        fact['id'] = fact_id
        
        # Check for existing facts with same entity_key that are currently valid
        conflicting_facts = self._get_current_facts_by_entity(entity_key, user_id)
        
        # Process conflicts if any
        if conflicting_facts:
            for old_fact in conflicting_facts:
                relation = self._detect_contradiction(old_fact, fact, entity_key)
                self._apply_temporal_resolution(old_fact, fact, relation, user_id)
        
        # Embed for semantic recall (stored directly in SQLite as a BLOB)
        embedding_vec = self._embed_text(conversation_text)
        embedding_bytes = None
        if embedding_vec is not None:
            embedding_bytes = embedding_vec.tobytes()
            fact['metadata']['tags'].append('vector_indexed')

        # Insert into SQLite with temporal validity
        self._insert_fact_with_temporal(fact, user_id, current_time, embedding_bytes)
        
        # Mirror to markdown
        self._mirror_to_markdown(fact, user_id)
        
        # Add to SQLite FTS
        self._add_to_fts(fact, user_id)
        
        return [fact]
        
    def _mirror_to_markdown(self, fact: Dict[str, Any], user_id: str):
        """Mirror a fact to a markdown file in the vault."""
        timestamp = datetime.now().strftime("%Y-%m-%d")
        slug = self._generate_slug(fact.get('content', 'unknown'))
        filename = f"{timestamp}-{slug}.md"
        filepath = self.raw_path / filename
        
        # Extract entities from metadata if available
        entities = fact.get('metadata', {}).get('entities', [])
        if isinstance(entities, str):
            entities = [entities]
        
        # Create markdown content with frontmatter
        frontmatter = {
            'id': fact['id'],
            'timestamp': datetime.now().isoformat(),
            'user_id': user_id,
            'tags': fact.get('metadata', {}).get('tags', []),
            'source_entities': entities
        }
        
        # Convert frontmatter to YAML
        yaml_lines = ["---"]
        for key, value in frontmatter.items():
            if isinstance(value, list):
                yaml_lines.append(f"{key}: {json.dumps(value)}")
            else:
                yaml_lines.append(f"{key}: {value}")
        yaml_lines.append("---")
        
        content = '\n'.join(yaml_lines) + '\n\n' + fact.get('content', '')
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
            
    def _get_current_facts_by_entity(self, entity_key: str, user_id: str) -> List[Dict[str, Any]]:
        """Get currently valid facts for a given entity_key."""
        with self._db_lock:
            self.cursor.execute(
                """
                SELECT id, content, metadata, entity_key, valid_from
                FROM facts 
                WHERE entity_key = ? AND user_id = ? AND valid_to IS NULL
                """,
                (entity_key, user_id)
            )
            rows = self.cursor.fetchall()

        facts = []
        for row in rows:
            facts.append({
                'id': row[0],
                'content': row[1],
                'metadata': json.loads(row[2]) if row[2] else {},
                'entity_key': row[3],
                'valid_from': row[4]
            })
        return facts
    
    def _detect_contradiction(self, old_fact: Dict[str, Any], new_fact: Dict[str, Any], entity_key: str) -> str:
        """Use Groq to detect if new fact contradicts, updates, or is unrelated to old fact."""
        if self.groq_client is None:
            # Fallback: simple content comparison
            if old_fact['content'] == new_fact['content']:
                return "unrelated"  # Same content, no update needed
            return "update"  # Assume update if different
        
        try:
            prompt = f"""Existing fact: "{old_fact['content']}"
New fact: "{new_fact['content']}"
Same subject key: "{entity_key}"

Is the new fact a CONTRADICTION of the old one, an UPDATE (supersedes without contradiction, e.g. moved cities), or UNRELATED (false match, different sub-topic)?

Respond in JSON: {{"relation": "contradiction|update|unrelated", "reasoning": "<1 sentence>"}}"""
            
            response = self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b-versatile",
                temperature=0.1,
                max_tokens=100,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            return result.get('relation', 'unrelated')
            
        except Exception as e:
            log.warning("Groq contradiction detection failed: %s — defaulting to update relation", e)
            return "update"  # Default to update on failure
    
    def _apply_temporal_resolution(self, old_fact: Dict[str, Any], new_fact: Dict[str, Any], relation: str, user_id: str):
        """Apply temporal validity resolution based on relation type."""
        current_time = datetime.now()

        if relation in ['update', 'contradiction']:
            with self._db_lock:
                self.cursor.execute(
                    """
                    UPDATE facts 
                    SET valid_to = ?, superseded_by = ?
                    WHERE id = ?
                    """,
                    (current_time.isoformat(), new_fact['id'], old_fact['id'])
                )
                self.conn.commit()
            self._log_conflict(old_fact, new_fact, relation, user_id)

    def _insert_fact_with_temporal(self, fact: Dict[str, Any], user_id: str, current_time: datetime, embedding_bytes: Optional[bytes] = None):
        """Insert fact into SQLite with temporal validity fields."""
        with self._db_lock:
            self.cursor.execute(
                """
                INSERT INTO facts (id, content, metadata, user_id, entity_key, valid_from, valid_to, superseded_by, created_at, embedding)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    fact['id'],
                    fact.get('content', ''),
                    json.dumps(fact.get('metadata', {})),
                    user_id,
                    fact.get('entity_key', 'user.unknown'),
                    current_time.isoformat(),
                    current_time.isoformat(),
                    embedding_bytes
                )
            )
            self.conn.commit()
    
    def _log_conflict(self, old_fact: Dict[str, Any], new_fact: Dict[str, Any], relation: str, user_id: str):
        """Log conflict resolution to conflicts.md."""
        conflicts_file = self.conflicts_path / "conflicts.md"
        
        timestamp = datetime.now().isoformat()
        entry = f"""
## {timestamp}
- Old: {old_fact['content']}
- New: {new_fact['content']}
- Relation: {relation}
- Entity Key: {old_fact.get('entity_key', 'unknown')}
- Old Fact ID: {old_fact['id']}
- New Fact ID: {new_fact['id']}
- User ID: {user_id}

---
"""
        
        with open(conflicts_file, 'a', encoding='utf-8') as f:
            f.write(entry)
    
    def _add_to_fts(self, fact: Dict[str, Any], user_id: str):
        """Add a fact to SQLite FTS5 for keyword search."""
        with self._db_lock:
            self.cursor.execute(
                "SELECT id FROM facts_fts WHERE id = ?",
                (fact['id'],)
            )
            if self.cursor.fetchone():
                return  # Already exists, skip
            self.cursor.execute(
                "INSERT INTO facts_fts (id, content, metadata, user_id, entity_key) VALUES (?, ?, ?, ?, ?)",
                (
                    fact['id'],
                    fact.get('content', ''),
                    json.dumps(fact.get('metadata', {})),
                    user_id,
                    fact.get('entity_key', 'user.unknown')
                )
            )
            self.conn.commit()
        
    def retrieve(self, query: str, user_id: Optional[str] = None, top_k: int = 5, include_historical: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        """
        Hybrid retrieval: SQLite numpy-embedding search + SQLite FTS exact-match
        with temporal filtering.
        
        Args:
            query: Search query
            user_id: User identifier (defaults to instance user_id)
            top_k: Number of results to return
            include_historical: If True, include superseded facts (for "where did the user used to live" queries)
            
        Returns:
            Dictionary with 'semantic' and 'exact' results
        """
        if user_id is None:
            user_id = self.user_id
            
        # Check if query asks about history
        history_keywords = ['used to', 'previously', 'formerly', 'past', 'before', 'history', 'old']
        asks_history = any(keyword in query.lower() for keyword in history_keywords)
        
        # Semantic search via SQLite-stored embeddings + numpy cosine
        # (lazy; falls through to FTS if embeddings are unavailable).
        semantic_results = self._semantic_search(
            query, user_id, top_k, include_historical or asks_history
        )
        
        # Exact-match search via SQLite FTS with temporal filtering
        exact_results = self._fts_search(query, user_id, top_k, include_historical or asks_history)
        
        # Merge and dedupe results
        merged_results = self._merge_results(semantic_results, exact_results, top_k)
        
        return {
            'semantic': semantic_results,
            'exact': exact_results,
            'merged': merged_results
        }
    
    def _semantic_search(self, query: str, user_id: str, top_k: int, include_historical: bool = False) -> List[Dict[str, Any]]:
        """Rank stored facts by numpy-cosine similarity to ``query``.

        Facts are stored with their FastEmbed vectors as BLOBs in the SQLite
        ``facts`` table. For this personal memory scale (hundreds of facts)
        a brute-force, fully-normalised dot product over a subset of rows is
        effectively instant and needs no ANN index. Rows without an embedding
        are skipped — the FTS exact-match path still covers them.
        """
        embedder = self._ensure_embeddings(timeout_s=2.0)
        if embedder is None:
            return []
        try:
            q_embs = list(embedder.embed([query]))
            if not q_embs or q_embs[0] is None:
                return []
            qvec = np.asarray(q_embs[0], dtype=np.float32)
            qn = qvec / (float(np.linalg.norm(qvec)) or 1.0)

            if include_historical:
                where_sql = "user_id = ? AND embedding IS NOT NULL"
            else:
                where_sql = "user_id = ? AND valid_to IS NULL AND embedding IS NOT NULL"
            with self._db_lock:
                self.cursor.execute(
                    f"SELECT id, content, metadata, embedding FROM facts WHERE {where_sql}",
                    (user_id,),
                )
                rows = self.cursor.fetchall()

            ids, docs, metas, vecs = [], [], [], []
            for r in rows:
                blob = r["embedding"]
                if not blob:
                    continue
                ids.append(r["id"])
                docs.append(r["content"])
                metas.append(json.loads(r["metadata"]) if r["metadata"] else {})
                vecs.append(np.frombuffer(blob, dtype=np.float32))

            if not vecs:
                return []
            X = np.stack(vecs).astype(np.float32)
            Xn = X / np.linalg.norm(X, axis=1, keepdims=True)
            scores = Xn @ qn
            order = np.argsort(-scores)[:top_k]
            return [
                {
                    "id": ids[i],
                    "content": docs[i],
                    "metadata": metas[i],
                    "score": float(scores[i]),
                }
                for i in order
            ]
        except Exception as e:
            log.warning("semantic search failed: %s", e)
            return []

    def _get_valid_fact_ids(self, user_id: str, include_historical: bool = False) -> set:
        """Get set of valid fact IDs based on temporal validity."""
        with self._db_lock:
            if include_historical:
                self.cursor.execute(
                    "SELECT id FROM facts WHERE user_id = ?",
                    (user_id,)
                )
            else:
                self.cursor.execute(
                    "SELECT id FROM facts WHERE user_id = ? AND valid_to IS NULL",
                    (user_id,)
                )
            rows = self.cursor.fetchall()
        return {row[0] for row in rows}
        
    @staticmethod
    def _escape_fts_query(query: str) -> str:
        """Escape a string for safe use in an FTS5 MATCH expression.

        FTS5 special characters: + - " * ( ) : { } ^ ! ~ & | < >
        We wrap the whole query in double quotes (phrase search) and escape
        embedded double quotes. This disables all query operators and treats
        the input as a literal phrase, which is the safe behaviour for a
        keyword-search box driven by untrusted user input.
        """
        escaped = query.replace('"', '""')
        return '"' + escaped + '"'

    def _fts_search(self, query: str, user_id: str, limit: int, include_historical: bool = False) -> List[Dict[str, Any]]:
        """Perform exact-match search using SQLite FTS5 with temporal filtering.

        All user-supplied values are bound via parameter placeholders. The
        variable-length ``valid_ids IN (...)`` clause is generated with the
        correct number of ``?`` tokens and bound in the parameters tuple.
        """
        valid_ids = list(self._get_valid_fact_ids(user_id, include_historical))
        if not valid_ids:
            return []

        in_placeholders = ','.join('?' * len(valid_ids))
        fts_expr = self._escape_fts_query(query)

        # Try FTS5 search first
        try:
            sql = (
                "SELECT fts.id, fts.content, fts.metadata "
                "FROM facts_fts fts "
                "WHERE fts.facts_fts MATCH ? "
                "AND fts.user_id = ? "
                f"AND fts.id IN ({in_placeholders}) "
                "LIMIT ?"
            )
            params = [fts_expr, user_id, *valid_ids, limit]
            with self._db_lock:
                self.cursor.execute(sql, params)
                rows = self.cursor.fetchall()
            return [
                {
                    'id': row[0],
                    'content': row[1],
                    'metadata': json.loads(row[2]) if row[2] else {}
                }
                for row in rows
            ]
        except Exception as e:
            log.warning("FTS5 search failed, falling back to LIKE: %s", e)

            # Fallback to simple LIKE search if FTS5 fails — still fully
            # parameterised; % wildcards are concatenated inside the bound
            # value, not the SQL string.
            sql = (
                "SELECT fts.id, fts.content, fts.metadata "
                "FROM facts_fts fts "
                "WHERE fts.content LIKE ? "
                "AND fts.user_id = ? "
                f"AND fts.id IN ({in_placeholders}) "
                "LIMIT ?"
            )
            params = [f"%{query}%", user_id, *valid_ids, limit]
            with self._db_lock:
                self.cursor.execute(sql, params)
                rows = self.cursor.fetchall()
            return [
                {
                    'id': row[0],
                    'content': row[1],
                    'metadata': json.loads(row[2]) if row[2] else {}
                }
                for row in rows
            ]
        
    def _merge_results(self, semantic: List[Dict], exact: List[Dict], limit: int) -> List[Dict]:
        """Merge semantic and exact results, removing duplicates."""
        seen_ids = set()
        merged = []
        
        # Add semantic results first
        for result in semantic:
            if isinstance(result, dict):
                result_id = result.get('id', '')
                if result_id and result_id not in seen_ids:
                    seen_ids.add(result_id)
                    merged.append(result)
                    
        # Add exact results that weren't already included
        for result in exact:
            result_id = result.get('id', '')
            if result_id and result_id not in seen_ids:
                seen_ids.add(result_id)
                merged.append(result)
                
        return merged[:limit]
        
    def close(self):
        """Clean up resources."""
        self.conn.close()
