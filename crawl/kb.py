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
import re
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

    # Sprint 16: chunk-quality metadata tables for future rerankers.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_chunk_meta (
            chunk_id      TEXT PRIMARY KEY,
            keywords      TEXT,
            entities      TEXT,
            token_count   INTEGER,
            framework_tags TEXT
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS doc_chunk_meta (
            chunk_id      TEXT PRIMARY KEY,
            keywords      TEXT,
            entities      TEXT,
            token_count   INTEGER,
            product_tag   TEXT
        );
    """)

    # Sprint 16: deterministic anchor-to-chunk mapping.
    # provenance_origin distinguishes anchors traceable to the Session 04
    # synthesis (session_synthesis) from anchors that are not traceable to it
    # (external_public). The retrieval/answer path surfaces this value so
    # external-public anchors are never reported as Session-04 citations.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS framework_anchors (
            anchor_id         TEXT PRIMARY KEY,
            anchor            TEXT NOT NULL,
            provenance_origin TEXT NOT NULL DEFAULT 'session_synthesis',
            chunk_id          TEXT NOT NULL,
            scope             TEXT NOT NULL,
            source_url        TEXT,
            notes             TEXT,
            updated_at        TEXT NOT NULL
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_anchors_anchor ON framework_anchors(anchor);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_anchors_chunk_id ON framework_anchors(chunk_id);
    """)

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

        # Sprint 16: clear old doc_chunk_meta for this document and rewrite.
        cursor.execute("DELETE FROM doc_chunk_meta WHERE chunk_id LIKE ?;", (f"{doc_id}#%",))
        for c in chunks:
            c_id = c.chunk_id if c.chunk_id.startswith(f"{doc_id}#") else f"{doc_id}#{c.chunk_index:04d}"
            meta = _extract_chunk_meta(c.text, product_tag=product)
            cursor.execute("""
                INSERT INTO doc_chunk_meta (
                    chunk_id, keywords, entities, token_count, product_tag
                ) VALUES (?, ?, ?, ?, ?);
            """, (
                c_id, meta["keywords"], meta["entities"], meta["token_count"], meta["product_tag"]
            ))

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


# ── Sprint 16 metadata extraction (lightweight, CPU-only) ─────────────

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "and", "or", "for", "in", "on", "at", "by", "with", "from",
    "as", "it", "its", "this", "that", "these", "those", "we", "you", "your",
    "our", "their", "there", "them", "they", "he", "she", "his", "her",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "about", "into", "through",
    "during", "before", "after", "above", "below", "between", "under",
}

_ENTITY_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]+(?:\s+(?:&|and)?\s*[A-Z][a-zA-Z0-9]+)*\b")


def _extract_keywords(text: str) -> str:
    """Extract a small set of content-bearing terms (CPU-only)."""
    lowered = (text or "").lower()
    # Keep alphanumeric tokens with internal dashes (e.g. "vertex-ai")
    tokens = re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", lowered)
    counts: Dict[str, int] = {}
    for t in tokens:
        if t in _STOPWORDS or len(t) <= 2:
            continue
        counts[t] = counts.get(t, 0) + 1
    # Pick terms with frequency >= 2, longest first, capped at 12.
    frequent = [t for t, c in counts.items() if c >= 2]
    frequent.sort(key=lambda x: (-len(x), x))
    if not frequent:
        # fall back to longer singletons
        frequent = sorted(counts.keys(), key=lambda x: (-len(x), x))[:8]
    return ", ".join(frequent[:12])


def _extract_entities(text: str) -> str:
    """Extract likely named entities from title-case runs (CPU-only)."""
    seen = set()
    out = []
    for match in _ENTITY_RE.finditer(text or ""):
        ent = match.group(0).strip()
        # Drop standalone common words that can be title-cased by accident.
        if ent.lower() in _STOPWORDS or len(ent) <= 2:
            continue
        key = ent.lower()
        if key not in seen:
            seen.add(key)
            out.append(ent)
    return ", ".join(out[:10])


def _token_count_approx(text: str) -> int:
    """Approximate token count for metadata (whitespace split)."""
    return len(re.findall(r"\b\w+\b", text or ""))


def _extract_chunk_meta(text: str, product_tag: str | None = None) -> Dict[str, Any]:
    """Build a doc_chunk_meta row from chunk text."""
    return {
        "keywords": _extract_keywords(text),
        "entities": _extract_entities(text),
        "token_count": _token_count_approx(text),
        "product_tag": product_tag or "",
    }


def backfill_doc_chunk_meta(conn: sqlite3.Connection) -> int:
    """Populate missing doc_chunk_meta rows for existing doc_chunks.

    Idempotent: only inserts rows whose chunk_id is not already present.
    Uses CPU-only heuristics and reads product from the docs table.
    Returns the number of rows inserted.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT dc.chunk_id, dc.text, d.product
        FROM doc_chunks AS dc
        JOIN docs AS d ON d.doc_id = dc.doc_id
        WHERE dc.chunk_id NOT IN (SELECT chunk_id FROM doc_chunk_meta);
        """
    )
    rows = cursor.fetchall()

    now_iso = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for row in rows:
        meta = _extract_chunk_meta(str(row["text"] or ""), product_tag=str(row["product"] or ""))
        cursor.execute(
            """
            INSERT INTO doc_chunk_meta (
                chunk_id, keywords, entities, token_count, product_tag
            ) VALUES (?, ?, ?, ?, ?);
            """,
            (
                str(row["chunk_id"]),
                meta["keywords"],
                meta["entities"],
                meta["token_count"],
                meta["product_tag"],
            ),
        )
        inserted += 1

    conn.commit()
    return inserted


def upsert_session_chunk_meta(conn: sqlite3.Connection) -> int:
    """Populate session_chunk_meta for every immersion_chunks row.

    Idempotent: deletes and repopulates. Uses CPU-only heuristics.
    Returns the number of rows written.
    """
    cursor = conn.cursor()
    cursor.execute("DELETE FROM session_chunk_meta;")
    cursor.execute(
        "SELECT chunk_id, session_id, title, category, content FROM immersion_chunks;"
    )
    rows = cursor.fetchall()

    now_iso = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for row in rows:
        content = str(row["content"] or "")
        title = str(row["title"] or "")
        combined = f"{title} {content}".lower()
        tags = []
        if "five lenses" in combined:
            tags.append("Five Lenses")
        if "sean ellis" in combined:
            tags.append("Sean Ellis")
        if "gross margin" in combined or "75%" in combined:
            tags.append("Unit Economics")
        if "defensibility" in combined:
            tags.append("Defensibility Hierarchy")
        if "ten x" in combined or "10x" in combined:
            tags.append("10x Lens")
        framework_tag_str = ", ".join(tags)

        cursor.execute(
            """
            INSERT INTO session_chunk_meta (
                chunk_id, keywords, entities, token_count, framework_tags
            ) VALUES (?, ?, ?, ?, ?);
            """,
            (
                str(row["chunk_id"]),
                _extract_keywords(content),
                _extract_entities(content),
                _token_count_approx(content),
                framework_tag_str,
            ),
        )
        inserted += 1

    conn.commit()
    return inserted


def seed_framework_anchors(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Seed framework_anchors from the indexed session/doc corpus.

    Anchors map a canonical phrase to an existing chunk_id. If the target
    chunk cannot be found, the anchor is skipped and recorded in the returned
    dict so callers can decide whether to fail the sprint.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()

    # Session anchors are drawn from the session syntheses.
    session_source_file = "session_syntheses"
    session_anchors = [
        ("Five Lenses", "session_synthesis", "Five Lenses framework and deck examples"),
        ("Sean Ellis 40%", "external_public", "Sean Ellis product/market-fit 40% heuristic"),
        ("Gross Margin 75%", "session_synthesis", "Unit-economics gross-margin anchor"),
        ("Defensibility Hierarchy", "session_synthesis", "Pre-seed defensibility hierarchy"),
    ]

    inserted = 0
    skipped: List[Tuple[str, str]] = []

    cursor.execute("DELETE FROM framework_anchors WHERE scope = 'session';")

    for anchor, provenance, notes in session_anchors:
        # Match by lower-cased anchor substring in content; prefer chunks
        # whose source_file contains FRAMEWORK_ANCHORS.
        cursor.execute(
            """
            SELECT chunk_id, content, source_file
            FROM immersion_chunks
            WHERE lower(content) LIKE ? OR lower(title) LIKE ?
            ORDER BY
                CASE WHEN source_file LIKE ? THEN 0 ELSE 1 END,
                CASE WHEN lower(title) LIKE ? THEN 0 ELSE 1 END,
                length(content) DESC
            LIMIT 1;
            """,
            (f"%{anchor.lower()}%", f"%{anchor.lower()}%", "%FRAMEWORK_ANCHORS%", f"%{anchor.lower()}%"),
        )
        row = cursor.fetchone()
        if row is None:
            skipped.append(("session", anchor))
            continue
        chunk_id = str(row["chunk_id"])
        anchor_id = hashlib.sha256(f"session:{anchor}:{chunk_id}".encode()).hexdigest()
        cursor.execute(
            """
            INSERT INTO framework_anchors (
                anchor_id, anchor, provenance_origin, chunk_id, scope, source_url, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (anchor_id, anchor, provenance, chunk_id, "session", session_source_file, notes, now_iso),
        )
        inserted += 1

    # Document anchors for the new RAG/grounding products crawled in Sprint 16.
    doc_anchors = [
        ("Vertex AI RAG Engine", "Vertex AI RAG Engine", "Vertex AI RAG Engine overview"),
        ("Agent Builder RAG", "Agent Builder RAG", "Agent Builder RAG overview"),
        ("ADK RAG tool", "ADK RAG tool", "ADK RAG tool / retrieval codelab"),
        ("Gemini API grounding", "Gemini API grounding", "Gemini API grounding"),
        ("Vertex AI Search grounding", "Vertex AI Search grounding", "Vertex AI Search grounding"),
    ]

    cursor.execute("DELETE FROM framework_anchors WHERE scope = 'doc';")

    for anchor, product, notes in doc_anchors:
        # Find a chunk from a doc with the matching product.
        cursor.execute(
            """
            SELECT dc.chunk_id, d.source_url
            FROM doc_chunks AS dc
            JOIN docs AS d ON d.doc_id = dc.doc_id
            WHERE d.product = ?
            ORDER BY dc.chunk_index ASC
            LIMIT 1;
            """,
            (product,),
        )
        row = cursor.fetchone()
        if row is None:
            skipped.append(("doc", anchor))
            continue
        chunk_id = str(row["chunk_id"])
        source_url = str(row["source_url"])
        anchor_id = hashlib.sha256(f"doc:{anchor}:{chunk_id}".encode()).hexdigest()
        cursor.execute(
            """
            INSERT INTO framework_anchors (
                anchor_id, anchor, provenance_origin, chunk_id, scope, source_url, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (anchor_id, anchor, "external_public", chunk_id, "doc", source_url, notes, now_iso),
        )
        inserted += 1

    conn.commit()
    return {"inserted": inserted, "skipped": skipped}
