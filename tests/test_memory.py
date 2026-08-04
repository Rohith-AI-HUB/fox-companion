"""Regression tests for the SQLite + numpy memory store (:class:`brain.FoxBrain`).

These guard the ChromaDB→SQLite migration: exact-match FTS retrieval, hybrid
composition, temporal validity, and entity-key supersession all continue to
work. No Groq/FastEmbed required (keyword fallback is exercised).
"""
import pytest

from brain.brain import FoxBrain


@pytest.fixture
def brain(tmp_path, monkeypatch):
    """A FoxBrain backed by a throwaway vault, fully offline."""
    b = FoxBrain(user_id="test-user", vault_path=str(tmp_path / "vault"))
    # Force the keyword-only fallback path: no embedding model, no Groq.
    b._embedding_model = None
    b._embedding_ready.set()
    b._groq_key = ""
    b._groq_client_obj = None
    monkeypatch.setattr(b, "_ensure_embeddings", lambda timeout_s=5.0: None)
    # Semantic search must degrade gracefully when embeddings are absent.
    assert b.retrieve("anything")["semantic"] == []
    yield b
    b.close()


def test_capture_then_exact_retrieve(brain):
    brain.capture("I love hiking in the mountains")
    results = brain.retrieve("hiking")
    contents = [r["content"] for r in results["exact"]]
    assert any("hiking" in c for c in contents)


def test_hybrid_merge_dedupes(brain):
    facts = brain.capture("I work at a tech company in Austin")
    fact_id = facts[0]["id"]
    merged = brain.retrieve("Austin")["merged"]
    ids = [r["id"] for r in merged]
    assert ids.count(fact_id) == 1  # no duplicate from semantic+exact merge


def test_supersession_on_same_entity(brain):
    brain.capture("I love hiking in the mountains")   # entity_key: user.i_love
    brain.capture("I love pizza now")                  # same key -> update/supersede

    # Without history: the superseded "hiking" fact must be filtered out.
    current_contents = [
        r["content"] for r in brain.retrieve("hiking", include_historical=False)["exact"]
    ]
    assert not any("hiking" in c for c in current_contents)

    # With history: the superseded fact surfaces again.
    historical_contents = [
        r["content"] for r in brain.retrieve("hiking", include_historical=True)["exact"]
    ]
    assert any("hiking" in c for c in historical_contents)


def test_old_fact_is_temporally_closed_after_update(brain):
    first = brain.capture("My name is Rohit")[0]
    brain.capture("My name is Priya")

    with brain._db_lock:
        brain.cursor.execute(
            "SELECT valid_to IS NULL FROM facts WHERE id = ?", (first["id"],)
        )
        still_current = brain.cursor.fetchone()[0]
    # SQLite returns 0 = valid_to IS NOT NULL => fact was superseded/closed.
    assert still_current == 0


def test_different_entities_do_not_clash(brain):
    a = brain.capture("I love hiking in the mountains")[0]  # user.i_love
    b = brain.capture("I really love hiking")[0]            # user.i_really
    assert a["id"] != b["id"]
    # Both remain current (different entity keys => no supersession).
    vals = brain.retrieve("hiking", include_historical=False)
    all_ids = [r["id"] for r in vals["exact"] + vals["semantic"]]
    assert a["id"] in all_ids
    assert b["id"] in all_ids


def test_escape_fts_query(brain):
    assert brain._escape_fts_query('say "hi"') == '"say ""hi"""'


def test_close_releases_db(brain):
    brain.close()
    # Second close is tolerated by sqlite3 as a no-op warning; the connection
    # object is still valid. We assert closing did not raise.
    assert brain.conn is not None