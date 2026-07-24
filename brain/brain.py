import os
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import hashlib
from fastembed import TextEmbedding
import chromadb
from chromadb.config import Settings
from groq import Groq
from dotenv import load_dotenv

class FoxBrain:
    def __init__(self, user_id: str = "default", vault_path: str = "brain/vault"):
        # Load environment variables
        load_dotenv()
        
        self.user_id = user_id
        self.vault_path = Path(vault_path)
        self.raw_path = self.vault_path / "raw"
        self.reflections_path = self.vault_path / "reflections"
        self.conflicts_path = self.vault_path / "conflicts"
        
        # Ensure directories exist
        self.raw_path.mkdir(parents=True, exist_ok=True)
        self.reflections_path.mkdir(parents=True, exist_ok=True)
        self.conflicts_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize Groq client for entity extraction and conflict detection
        groq_api_key = os.getenv("GROQ_API_KEY")
        if groq_api_key:
            self.groq_client = Groq(api_key=groq_api_key)
        else:
            print("Warning: GROQ_API_KEY not found in environment variables")
            self.groq_client = None
        
        # Initialize Mem0 with local FastEmbed
        self._init_mem0()
        
        # Initialize SQLite FTS5 for keyword search
        self._init_sqlite_fts()
        
    def _init_mem0(self):
        """Initialize local vector store with FastEmbed embeddings (bypassing Mem0's embedder issues)."""
        # Initialize FastEmbed with a local model
        self.embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        
        # Initialize ChromaDB directly for vector storage
        try:
            self.chroma_client = chromadb.PersistentClient(
                path=str(self.vault_path / "chroma_db"),
                settings=Settings(anonymized_telemetry=False)
            )
            self.collection = self.chroma_client.get_or_create_collection(
                name="fox_memory",
                metadata={"hnsw:space": "cosine"}
            )
            self.memory = None  # We'll use Chroma directly instead of Mem0
            print("[OK] Initialized ChromaDB with FastEmbed")
        except Exception as e:
            print(f"Warning: Could not initialize ChromaDB: {e}")
            print("Falling back to a simple memory implementation without embeddings")
            self.chroma_client = None
            self.collection = None
            self.memory = None
        
    def _init_sqlite_fts(self):
        """Initialize SQLite FTS5 for exact-match keyword search and temporal validity."""
        self.db_path = self.vault_path / "keyword_search.db"
        self.conn = sqlite3.connect(str(self.db_path))
        self.cursor = self.conn.cursor()
        
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
                created_at TIMESTAMP NOT NULL
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
            print(f"Warning: Entity extraction failed: {e}")
            # Fallback to simple extraction
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
        
        # Add to ChromaDB with FastEmbed if available
        if self.collection is not None:
            try:
                # Generate embedding using FastEmbed
                embeddings = list(self.embedding_model.embed([conversation_text]))
                embedding = embeddings[0] if embeddings else None
                
                if embedding is not None:
                    # Add to ChromaDB
                    self.collection.add(
                        ids=[fact_id],
                        embeddings=[embedding.tolist()],
                        documents=[conversation_text],
                        metadatas=[{
                            'user_id': user_id,
                            'timestamp': current_time.isoformat(),
                            'entity_key': entity_key
                        }]
                    )
                    fact['metadata']['tags'].append('vector_indexed')
            except Exception as e:
                print(f"Warning: Could not add to vector store: {e}")
        
        # Insert into SQLite with temporal validity
        self._insert_fact_with_temporal(fact, user_id, current_time)
        
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
        self.cursor.execute(
            """
            SELECT id, content, metadata, entity_key, valid_from
            FROM facts 
            WHERE entity_key = ? AND user_id = ? AND valid_to IS NULL
            """,
            (entity_key, user_id)
        )
        
        facts = []
        for row in self.cursor.fetchall():
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
            print(f"Warning: Contradiction detection failed: {e}")
            return "update"  # Default to update on failure
    
    def _apply_temporal_resolution(self, old_fact: Dict[str, Any], new_fact: Dict[str, Any], relation: str, user_id: str):
        """Apply temporal validity resolution based on relation type."""
        current_time = datetime.now()
        
        if relation in ['update', 'contradiction']:
            # Set old fact's valid_to and superseded_by
            self.cursor.execute(
                """
                UPDATE facts 
                SET valid_to = ?, superseded_by = ?
                WHERE id = ?
                """,
                (current_time.isoformat(), new_fact['id'], old_fact['id'])
            )
            self.conn.commit()
            
            # Log to conflicts.md
            self._log_conflict(old_fact, new_fact, relation, user_id)
    
    def _insert_fact_with_temporal(self, fact: Dict[str, Any], user_id: str, current_time: datetime):
        """Insert fact into SQLite with temporal validity fields."""
        self.cursor.execute(
            """
            INSERT INTO facts (id, content, metadata, user_id, entity_key, valid_from, valid_to, superseded_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (
                fact['id'],
                fact.get('content', ''),
                json.dumps(fact.get('metadata', {})),
                user_id,
                fact.get('entity_key', 'user.unknown'),
                current_time.isoformat(),
                current_time.isoformat()
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
        # Check if fact already exists to avoid duplicates
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
        Hybrid retrieval: ChromaDB vector search + SQLite FTS exact-match with temporal filtering.
        
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
        
        # Semantic search via ChromaDB with FastEmbed
        semantic_results = []
        if self.collection is not None:
            try:
                # Generate embedding for query
                embeddings = list(self.embedding_model.embed([query]))
                query_embedding = embeddings[0] if embeddings else None
                
                if query_embedding is not None:
                    # Search ChromaDB
                    results = self.collection.query(
                        query_embeddings=[query_embedding.tolist()],
                        n_results=top_k,
                        where={"user_id": user_id}
                    )
                    
                    # Format results and filter by temporal validity
                    if results['ids'] and results['ids'][0]:
                        valid_ids = self._get_valid_fact_ids(user_id, include_historical or asks_history)
                        
                        for i, doc_id in enumerate(results['ids'][0]):
                            if doc_id in valid_ids:  # Only include currently valid facts
                                semantic_results.append({
                                    'id': doc_id,
                                    'content': results['documents'][0][i],
                                    'metadata': results['metadatas'][0][i] if results['metadatas'] else {},
                                    'score': results['distances'][0][i] if results['distances'] else None
                                })
            except Exception as e:
                print(f"Warning: Semantic search failed: {e}")
                semantic_results = []
        
        # Exact-match search via SQLite FTS with temporal filtering
        exact_results = self._fts_search(query, user_id, top_k, include_historical or asks_history)
        
        # Merge and dedupe results
        merged_results = self._merge_results(semantic_results, exact_results, top_k)
        
        return {
            'semantic': semantic_results,
            'exact': exact_results,
            'merged': merged_results
        }
    
    def _get_valid_fact_ids(self, user_id: str, include_historical: bool = False) -> set:
        """Get set of valid fact IDs based on temporal validity."""
        if include_historical:
            # Return all fact IDs
            self.cursor.execute(
                "SELECT id FROM facts WHERE user_id = ?",
                (user_id,)
            )
        else:
            # Return only currently valid facts
            self.cursor.execute(
                "SELECT id FROM facts WHERE user_id = ? AND valid_to IS NULL",
                (user_id,)
            )
        
        return {row[0] for row in self.cursor.fetchall()}
        
    def _fts_search(self, query: str, user_id: str, limit: int, include_historical: bool = False) -> List[Dict[str, Any]]:
        """Perform exact-match search using SQLite FTS5 with temporal filtering."""
        # Try FTS5 search first
        try:
            # Clean the query for FTS5
            clean_query = query.replace("'", "''").replace('"', '""')
            
            # Get valid fact IDs first
            valid_ids = self._get_valid_fact_ids(user_id, include_historical)
            
            if not valid_ids:
                return []
                
            # Build IN clause for valid IDs
            valid_ids_str = ','.join(f"'{fact_id}'" for fact_id in valid_ids)
            
            self.cursor.execute(
                f"""
                SELECT fts.id, fts.content, fts.metadata 
                FROM facts_fts fts
                WHERE fts.facts_fts MATCH '{clean_query}' 
                AND fts.user_id = ? 
                AND fts.id IN ({valid_ids_str})
                LIMIT ?
                """,
                (user_id, limit)
            )
            
            results = []
            for row in self.cursor.fetchall():
                results.append({
                    'id': row[0],
                    'content': row[1],
                    'metadata': json.loads(row[2]) if row[2] else {}
                })
            
            return results
        except Exception as e:
            # Fallback to simple LIKE search if FTS5 fails
            valid_ids = self._get_valid_fact_ids(user_id, include_historical)
            
            if not valid_ids:
                return []
                
            valid_ids_str = ','.join(f"'{fact_id}'" for fact_id in valid_ids)
            
            self.cursor.execute(
                f"""
                SELECT fts.id, fts.content, fts.metadata 
                FROM facts_fts fts
                WHERE fts.content LIKE ? 
                AND fts.user_id = ? 
                AND fts.id IN ({valid_ids_str})
                LIMIT ?
                """,
                (f"%{query}%", user_id, limit)
            )
            
            results = []
            for row in self.cursor.fetchall():
                results.append({
                    'id': row[0],
                    'content': row[1],
                    'metadata': json.loads(row[2]) if row[2] else {}
                })
            
            return results
        
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
