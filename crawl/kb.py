"""Additive SQLite knowledge base schema and operations.

Conforms to Sprint 08 / FR-07, ADR-13, ADR-15:
- Preserves existing immersion_chunks untouched (80 contractual rows).
- Additive schema: docs, doc_chunks (FTS5), vec_chunks (vec0), crawl_state.
- Loads sqlite-vec inside the build-plane process.
- Strictly parameterized SQL statements (Zero string formatting).
- Idempotent upserts.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from crawl.chunk import Chunk
from crawl.embed import EMBED_DIM, vector_to_blob

logger = logging.getLogger(__name__)


def compute_doc_id(source_url: str) -> str:
    """Generate deterministic doc_id from source_url using sha1."""
    return hashlib.sha1(source_url.strip().encode("utf-8")).hexdigest()


def compute_content_hash(text: str) -> str:
    """Generate deterministic content hash using sha256."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def get_connection(db_path: Path | str, load_vec: bool = True) -> sqlite3.Connection:
    """Connect to SQLite database and optionally load the sqlite-vec extension."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if load_vec:
        try:
            import sqlite_vec

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception as e:
            logger.critical("Failed to load sqlite-vec extension: %s", e)
            raise

    return conn


def init_knowledge_base(conn: sqlite3.Connection, embed_dim: int = EMBED_DIM) -> None:
    """Initialize additive schema in the SQLite database without touching existing tables."""
    cursor = conn.cursor()

    # 1. Document metadata table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS docs (
            doc_id       TEXT PRIMARY KEY,
            source_url   TEXT NOT NULL UNIQUE,
            title        TEXT,
            product      TEXT,
            tier         TEXT NOT NULL,
            license      TEXT DEFAULT 'unknown',
            content_hash TEXT NOT NULL,
            fetched_at   TEXT NOT NULL,
            status       TEXT DEFAULT 'ok'
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_tier ON docs(tier);")

    # 1b. Additive volatility column (guarded; re-running is a no-op)
    cursor.execute("PRAGMA table_info(docs);")
    doc_columns = {row[1] for row in cursor.fetchall()}
    if "volatility" not in doc_columns:
        cursor.execute("ALTER TABLE docs ADD COLUMN volatility TEXT DEFAULT NULL;")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_docs_volatility ON docs(volatility);"
    )

    # 1c. Deterministic facts lookup (Tier 3; not part of RRF)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            fact_id     TEXT PRIMARY KEY,
            topic       TEXT NOT NULL,
            claim       TEXT NOT NULL,
            source_url  TEXT,
            confidence  REAL DEFAULT 1.0,
            updated_at  TEXT NOT NULL
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_topic ON facts(topic);")

    # 2. Full-text search (FTS5) for crawled document chunks
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks USING fts5(
            chunk_id UNINDEXED,
            doc_id UNINDEXED,
            heading_path,
            text,
            source_url UNINDEXED,
            chunk_index UNINDEXED,
            tokenize = 'porter unicode61'
        );
    """)

    # 3. Dense vector table (vec0) for semantic retrieval
    cursor.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
            chunk_id TEXT PRIMARY KEY,
            embedding FLOAT[{embed_dim}]
        );
    """)

    # 3b. Dense vector table (vec0) for the session corpus (Sprint 10 / FR-4).
    #     Same shape and vector space as vec_chunks; the primary key is the
    #     immersion_chunks.chunk_id so fusion keys resolve during hydration.
    cursor.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_sessions USING vec0(
            chunk_id TEXT PRIMARY KEY,
            embedding FLOAT[{embed_dim}]
        );
    """)

    # 4. Crawl state for incremental re-indexing
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS crawl_state (
            source_url   TEXT PRIMARY KEY,
            content_hash TEXT,
            last_seen    TEXT,
            last_status  TEXT
        );
    """)

    conn.commit()


def populate_session_vectors(
    conn: sqlite3.Connection,
    embedder,
    batch_size: int = 64,
) -> int:
    """Embed every ``immersion_chunks`` row into ``vec_sessions`` (FR-4).

    Session content is a *document*, so this uses the passage path
    (``embedder.embed_texts``) and never the BGE query path. A missing passage
    method raises instead of silently falling back to ``embed_query``.

    Idempotent: the whole table is replaced inside one transaction, so a re-run
    converges on exactly one vector per session chunk. ``vec_chunks``,
    ``doc_chunks`` and ``immersion_chunks`` are never written here.

    Returns the number of vectors written.
    """
    embed_passages = getattr(embedder, "embed_texts", None)
    if not callable(embed_passages):
        raise TypeError(
            "embedder must provide embed_texts() (passage path); refusing to "
            "encode session passages on the query path"
        )

    cursor = conn.cursor()
    cursor.execute(
        "SELECT chunk_id, content FROM immersion_chunks ORDER BY chunk_id;"
    )
    rows = cursor.fetchall()
    if not rows:
        return 0

    chunk_ids = [str(row[0]) for row in rows]
    texts = [str(row[1] or "") for row in rows]

    vectors: List[np.ndarray] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        vectors.extend(embed_passages(batch, batch_size=batch_size))

    if len(vectors) != len(chunk_ids):
        raise ValueError(
            f"Session chunks ({len(chunk_ids)}) != embeddings ({len(vectors)})"
        )

    with conn:  # Transaction boundary: auto-rollback on exception
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vec_sessions;")
        for chunk_id, vec in zip(chunk_ids, vectors):
            v = np.asarray(vec, dtype=np.float32)
            if v.shape[-1] != EMBED_DIM:
                raise ValueError(
                    f"Session vector dimension {v.shape[-1]} != pinned EMBED_DIM {EMBED_DIM}"
                )
            cursor.execute(
                "INSERT INTO vec_sessions (chunk_id, embedding) VALUES (?, ?);",
                (chunk_id, vector_to_blob(v)),
            )

    return len(chunk_ids)


def check_immersion_chunks(conn: sqlite3.Connection) -> int:
    """Verify immersion_chunks table exists and count rows (contractual guarantee: 80)."""
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='immersion_chunks';")
    exists = cursor.fetchone()[0]
    if not exists:
        return 0

    cursor.execute("SELECT count(*) FROM immersion_chunks;")
    return cursor.fetchone()[0]


def get_crawl_state(conn: sqlite3.Connection, source_url: str) -> Optional[Dict[str, Any]]:
    """Retrieve incremental crawl state for a URL using parameterized query."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT source_url, content_hash, last_seen, last_status FROM crawl_state WHERE source_url = ?;",
        (source_url,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(row)


def update_crawl_state(
    conn: sqlite3.Connection,
    source_url: str,
    content_hash: str,
    status: str,
) -> None:
    """Update or insert crawl state record using parameterized statement."""
    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO crawl_state (source_url, content_hash, last_seen, last_status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_url) DO UPDATE SET
            content_hash = excluded.content_hash,
            last_seen    = excluded.last_seen,
            last_status  = excluded.last_status;
        """,
        (source_url, content_hash, now_iso, status),
    )
    conn.commit()


def upsert_document(
    conn: sqlite3.Connection,
    source_url: str,
    title: str,
    product: str,
    tier: str,
    license_name: str,
    text: str,
    chunks: Sequence[Chunk],
    embeddings: Sequence[np.ndarray],
    status: str = "ok",
) -> str:
    """Idempotently upsert a document, its FTS chunks, and vectors within a transaction."""
    if len(chunks) != len(embeddings):
        raise ValueError(f"Chunks count ({len(chunks)}) != embeddings count ({len(embeddings)})")

    doc_id = compute_doc_id(source_url)
    content_h = compute_content_hash(text)
    now_iso = datetime.now(timezone.utc).isoformat()

    with conn:  # Transaction boundary: auto-rollback on exception
        cursor = conn.cursor()

        # 1. Upsert document metadata
        cursor.execute(
            """
            INSERT INTO docs (
                doc_id, source_url, title, product, tier, license,
                content_hash, fetched_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                title        = excluded.title,
                product      = excluded.product,
                tier         = excluded.tier,
                license      = excluded.license,
                content_hash = excluded.content_hash,
                fetched_at   = excluded.fetched_at,
                status       = excluded.status;
            """,
            (
                doc_id,
                source_url,
                title,
                product,
                tier,
                license_name,
                content_h,
                now_iso,
                status,
            ),
        )

        # 2. Clean out old chunks for this document
        cursor.execute("DELETE FROM doc_chunks WHERE doc_id = ?;", (doc_id,))
        cursor.execute("DELETE FROM vec_chunks WHERE chunk_id LIKE ?;", (f"{doc_id}#%",))
        for c in chunks:
            cursor.execute("DELETE FROM vec_chunks WHERE chunk_id = ?;", (c.chunk_id,))

        # 3. Insert new FTS5 chunks
        for c in chunks:
            c_id = c.chunk_id if c.chunk_id.startswith(f"{doc_id}#") else f"{doc_id}#{c.chunk_index:04d}"
            cursor.execute(
                """
                INSERT INTO doc_chunks (
                    chunk_id, doc_id, heading_path, text, source_url, chunk_index
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (c_id, doc_id, c.heading_path, c.text, source_url, c.chunk_index),
            )

        # 4. Insert vectors
        for c, vec in zip(chunks, embeddings):
            c_id = c.chunk_id if c.chunk_id.startswith(f"{doc_id}#") else f"{doc_id}#{c.chunk_index:04d}"
            blob = vector_to_blob(vec)
            cursor.execute(
                "INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?);",
                (c_id, blob),
            )

        # 5. Update crawl state
        cursor.execute(
            """
            INSERT INTO crawl_state (source_url, content_hash, last_seen, last_status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_url) DO UPDATE SET
                content_hash = excluded.content_hash,
                last_seen    = excluded.last_seen,
                last_status  = excluded.last_status;
            """,
            (source_url, content_h, now_iso, status),
        )

    return doc_id
