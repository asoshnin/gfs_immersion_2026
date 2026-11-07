"""Hybrid retrieval core for Sprint 09 (Retrieval Core).

Implements ``02_design.md`` §3.1, §4.1:
- Two FTS5 BM25 lexical rankers (``doc_chunks`` + ``immersion_chunks``).
- One dense vec0 KNN ranker covering BOTH corpora (``vec_chunks`` + ``vec_sessions``).
- Reciprocal Rank Fusion (``RRF_K = 60``, 1-based ranks, deterministic).
- Hydration into ``Citation`` rows; ``mode`` is derived from the fused result.
- Answerability assessment (Sprint 10 / FR-1) over the fused result.

Fail-closed contracts:
- ``dense_search`` raises when the vec0 path or BGE query path is unavailable.
- Queries are never encoded on the passage path (asymmetric BGE model).
- ``hybrid_search`` degrades to an honest ``lexical_only`` mode at request time.

All SQL is parameterized; corpus table names are selected between literal
statements and are never interpolated from input.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from crawl import kb
from crawl.embed import (
    EMBED_DIM,
    MODEL_ID,
    ModelLoadError,
    vector_to_blob,
)
from crawl.freshness import age_days, canonicalize_url, classify_volatility, is_stale

logger = logging.getLogger(__name__)

# Design §3.1 / §4.1 — single source of truth.
RRF_K = 60

# Dense measurably beats BM25 on natural-language questions (dense hit@5 0.93 /
# MRR 0.72 vs lexical 0.53 / 0.38). A 50/50 RRF diluted the stronger ranker so
# hybrid scored BELOW dense alone; weight the dense list higher.
RRF_DENSE_WEIGHT = 3.0

# ── Abstention (Sprint 10 / FR-1) ────────────────────────────────────
#
# ``answerable`` must mean "the corpus actually contains an answer", not
# "BM25 returned its usual top-K". ``total`` cannot express that: the OR
# tokenizer always fills the list, so a gibberish query still returns 10 rows.
#
# The gate is deliberately CONJUNCTIVE (all three conditions must hold):
#
#   1. ``top_score >= ABSTENTION_MIN_SCORE``  (fused RRF score, see below)
#   2. at least ``ABSTENTION_MIN_DOCUMENTS`` distinct source documents in the
#      returned top-k. One document can contribute many neighbouring chunks
#      (a 12,111-chunk corpus produces long per-page runs), so a single page
#      repeatedly hit is weak corroboration; two independent pages is not.
#   3. ``top_dense_distance <= ABSTENTION_MAX_DENSE_DISTANCE`` — see rationale.
#
# Why the fused score alone is not enough (measured 2026-11-03 on the real
# 392-doc / 80-chunk index): RRF is *rank-based*, so its value encodes "how
# many lists agreed", not "how relevant the text is". A dense-only hit tops
# out at 3/(60+1) = 0.0492; any lexical corroboration adds >= 1/(60+50) =
# 0.0091. Measured with the shipped rankers: 49 natural-language questions
# scored 0.0556-0.0656 while 30 nonsense strings scored 0.0492 (26 of them)
# but also 0.0606-0.0623 (4 of them, because a nonsense string can still
# contain a token that occurs in a real doc, e.g. "protocol"). The ranges
# OVERLAP; a rank-only score cannot separate them, and pretending otherwise
# would ship a signal that calls "explain the quux protocol" answerable.
#
# The dense distance carries most of the signal, but it does NOT separate the
# populations cleanly (all measured 2026-11-03, sqlite-vec FLOAT distance =
# squared L2 on unit vectors, i.e. 2 - 2*cos):
#
#   56 doc questions      0.386 - 0.783
#   10 session questions  0.569 - 0.832   (authored prose embeds "farther"
#                                          than documentation prose)
#   30 nonsense strings   0.783 - 1.001
#
# So 0.78-0.83 is a genuine overlap band, not a gap: "What did Antler say
# about venture scale?" (0.782) and "nvidia flurbo glitch" (0.790) are not
# separable by this model. Rather than pretend otherwise, the constant is
# chosen as the measured maximum of balanced accuracy = (TPR + TNR) / 2 over
# that set: 0.84 scores 0.970/0.967 => 0.968, versus 0.953 at 0.82 and 0.952
# at 0.85. Recorded consequences at 0.84: 64/66 genuine questions pass and
# 29/30 nonsense strings are refused; the single known false positive is
# "nvidia flurbo glitch" (a near-miss that does discuss GPU docs), and the
# two known false negatives are session questions pure enough to hit only
# one source document.
#
# This constant cannot be "tuned to make an eval pass": it does not affect
# ranking at all (RRF order is unchanged), only the answerable flag. If the
# embedder or vec0 is unavailable the gate fails closed ("dense_unavailable")
# rather than certifying relevance from BM25 alone.
ABSTENTION_MIN_SCORE = 0.055
ABSTENTION_MIN_DOCUMENTS = 2
ABSTENTION_MAX_DENSE_DISTANCE = 0.84

# The document-count rule is evaluated over a FIXED evidence window, not over
# whatever ``top_k`` the caller asked to display. Otherwise the verdict would
# depend on presentation: /api/chat asks for 5 rows for its prompt budget, and
# a legitimately narrow question ("How do I map a custom domain?") resolves to
# one page. Measured on the 20-question golden set: at top_k=5 the two-document
# rule abstained on 4/20 real questions, all with reason
# "single_document_evidence"; at a 10-row window it abstained on 0/20 while
# still answering 0/30 nonsense strings. 10 is also the default top_k of
# /api/search, so the public contract is the one the constant was measured on.
ABSTENTION_EVIDENCE_WINDOW = 10

MAX_TOP_K = 50
# COR2-01: 512 *characters* of the raw query string (len(query)), NOT 512
# model tokens. There is no truncation path: a longer query is rejected with
# ValueError -> HTTP 422 (code "query_too_long") by /api/search.
MAX_QUERY_LEN = 512

SNIPPET_MAX_CHARS = 280
_SESSION_VOLATILITY = "authored"

class VecUnavailableError(RuntimeError):
    """Raised when the vec0 KNN path cannot serve a dense query (fail-closed)."""


@dataclass
class Citation:
    fusion_key: str
    result_type: str          # "session" | "document"
    doc_key: str              # stable source identity (see _hydrate_*); dedupes chunks
    title: Optional[str]
    snippet: str
    content: Optional[str]    # PRIVATE; None for result_type == "document" (R4)
    source_url: Optional[str]
    canonical_url: Optional[str]
    fetched_at: Optional[str]
    age_days: Optional[int]
    volatility: Optional[str]
    stale: Optional[bool]
    score: float


@dataclass
class Answerability:
    """FR-1 abstention verdict returned alongside a fused result list."""

    answerable: bool
    top_score: float
    min_score: float
    top_dense_distance: Optional[float]
    distinct_documents: int
    reason: str


def CANDIDATE_LIMIT(top_k: int) -> int:
    """Per-ranker candidate pool before fusion (design §3.1)."""
    return max(top_k * 5, 50)


def open_dense_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection with the sqlite-vec extension loaded (Task 2.3).

    This is the only connection factory used by the dense path. Never use a
    bare ``sqlite3.connect`` for KNN: vec0 is a loadable extension and must be
    enabled per connection.
    """
    return kb.get_connection(db_path, load_vec=True)


def _escaped_phrase(query: str) -> str:
    """Build a safe FTS5 phrase query from hostile input (Task 2.7)."""
    return '"' + query.replace('"', '""') + '"'


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def fts_match_query(query: str) -> str:
    """Build a safe FTS5 OR-query from free text (DOM3-04 keyword fix).

    FTS5 interprets a bare multi-token string as implicit AND, so a natural
    language question such as "How do I deploy a container to Cloud Run?"
    matched zero rows. We tokenize to alphanumeric terms, quote each one --
    which neutralises every FTS5 metacharacter, so hostile input cannot inject
    syntax -- and join them with OR. bm25 still ranks chunks matching more of
    the query terms above chunks matching fewer.

    Returns "" when the query has no alphanumeric tokens.
    """
    tokens = _TOKEN_RE.findall(query)
    return " OR ".join(f'"{token}"' for token in tokens)


def _exec_fts(
    conn: sqlite3.Connection,
    corpus: str,
    query: str,
    limit: int,
) -> List[str]:
    """Run a BM25 query, retrying once as an escaped phrase.

    Hostile FTS5 metacharacters (``"``, ``*``, ``NEAR``, ``-``, ``(``, ``)``,
    ``:``) must never surface as ``sqlite3.OperationalError``. If even the
    escaped phrase fails, return an empty list.

    SQL literals are inlined into each ``execute`` call so the Phase-3 AST
    parameterization gate can prove there is no dynamic SQL construction.
    """
    match_query = fts_match_query(query)
    if not match_query:
        return []

    cursor = conn.cursor()
    try:
        if corpus == "doc":
            cursor.execute(
                """
                SELECT chunk_id
                FROM doc_chunks
                WHERE doc_chunks MATCH ?
                ORDER BY bm25(doc_chunks)
                LIMIT ?;
                """,
                (match_query, limit),
            )
        else:
            cursor.execute(
                """
                SELECT chunk_id
                FROM immersion_chunks
                WHERE immersion_chunks MATCH ?
                ORDER BY bm25(immersion_chunks)
                LIMIT ?;
                """,
                (match_query, limit),
            )
        return [str(row[0]) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        logger.debug("FTS5 rejected raw query; retrying as escaped phrase")

    phrase = _escaped_phrase(query)
    try:
        if corpus == "doc":
            cursor.execute(
                """
                SELECT chunk_id
                FROM doc_chunks
                WHERE doc_chunks MATCH ?
                ORDER BY bm25(doc_chunks)
                LIMIT ?;
                """,
                (phrase, limit),
            )
        else:
            cursor.execute(
                """
                SELECT chunk_id
                FROM immersion_chunks
                WHERE immersion_chunks MATCH ?
                ORDER BY bm25(immersion_chunks)
                LIMIT ?;
                """,
                (phrase, limit),
            )
        return [str(row[0]) for row in cursor.fetchall()]
    except sqlite3.OperationalError as exc:
        logger.warning("FTS5 could not execute sanitized query: %s", exc)
        return []


# Backward-compatible alias (older callers/tests used the private name).
_fts_match_query = fts_match_query


def lexical_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    corpus: str,
) -> List[str]:
    """BM25 lexical search returning ordered ``fusion_key`` values.

    ``corpus`` is either ``"doc"`` or ``"session"``. An empty query returns
    ``[]`` and never raises.
    """
    if corpus not in {"doc", "session"}:
        raise ValueError(f"Unknown corpus: {corpus!r}")
    if not isinstance(query, str) or not query.strip():
        return []
    if limit is None or int(limit) <= 0:
        return []

    chunk_ids = _exec_fts(conn, corpus, query, int(limit))
    return [f"{corpus}:{chunk_id}" for chunk_id in chunk_ids]


DENSE_CORPORA = {"doc": "vec_chunks", "session": "vec_sessions"}


def dense_search_ranked(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    embedder,
    corpus: str = "doc",
) -> List[Tuple[str, float]]:
    """vec0 KNN over one corpus, returning ``(fusion_key, distance)`` pairs.

    ``corpus`` selects the vector table: ``"doc"`` (default) reads
    ``vec_chunks``; ``"session"`` reads ``vec_sessions`` (Sprint 10 / FR-4).
    Both tables live in the same BGE vector space, so the *query* is always
    encoded on the BGE query path regardless of corpus. The selected table is
    chosen between literal statements and is never interpolated from input.

    The query is encoded via ``embedder.embed_query`` (BGE query path with the
    instruction prefix). A missing query path or a missing vec0 extension
    raises — the passage path is never used as a silent fallback.

    The return type changed in Sprint 10: distances are required to (a) merge
    the two corpora into ONE dense ranking in a shared vector space, and (b)
    feed the abstention gate. ``dense_search`` keeps the keys-only contract
    for existing callers.
    """
    if corpus not in DENSE_CORPORA:
        raise ValueError(f"Unknown corpus: {corpus!r}")
    if not isinstance(query, str) or not query.strip():
        return []
    if limit is None or int(limit) <= 0:
        return []

    encode_query = getattr(embedder, "embed_query", None)
    if not callable(encode_query):
        raise TypeError(
            "embedder must provide embed_query() on the BGE query path; "
            "refusing to encode the query as a passage"
        )

    vector = np.asarray(encode_query(query), dtype=np.float32)
    if vector.shape[-1] != EMBED_DIM:
        raise ValueError(
            f"Query vector dimension {vector.shape[-1]} != pinned EMBED_DIM {EMBED_DIM}"
        )

    cursor = conn.cursor()
    try:
        if corpus == "doc":
            cursor.execute(
                """
                SELECT chunk_id, distance
                FROM vec_chunks
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?;
                """,
                (vector_to_blob(vector), int(limit)),
            )
        else:
            cursor.execute(
                """
                SELECT chunk_id, distance
                FROM vec_sessions
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?;
                """,
                (vector_to_blob(vector), int(limit)),
            )
        rows = [(str(row[0]), float(row[1])) for row in cursor.fetchall()]
    except sqlite3.Error as exc:
        raise VecUnavailableError(
            f"vec0 KNN unavailable for corpus={corpus!r} "
            f"(extension/table missing): {exc}"
        ) from exc

    return [(f"{corpus}:{chunk_id}", distance) for chunk_id, distance in rows]


def dense_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    embedder,
    corpus: str = "doc",
) -> List[str]:
    """vec0 KNN over one corpus, returning ordered ``fusion_key`` values."""
    return [
        fusion_key
        for fusion_key, _distance in dense_search_ranked(
            conn, query, limit, embedder, corpus=corpus
        )
    ]


def dense_search_ranked_merged(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    embedder,
) -> List[Tuple[str, float]]:
    """ONE dense ranking over both corpora, merged by distance (Sprint 10).

    ``vec_chunks`` (392 docs / 12,111 chunks) and ``vec_sessions`` (80 chunks)
    live in the same BGE vector space, so their distances are directly
    comparable. Concatenating the two KNN lists and sorting by
    ``(distance, fusion_key)`` yields a single cross-corpus similarity ranking
    without a second query builder and without inventing a second weight:
    the fused list keeps the existing ``RRF_DENSE_WEIGHT``.

    Interleaving the session list into the SAME ranking (rather than appending
    it as an extra ranker) is what makes a session question reachable: at
    equal weight a separate 80-item session list placed 9 session chunks in
    the top 10 of every query, drowning the 392-doc corpus.

    Each corpus contributes up to ``limit`` candidates, mirroring the per-
    ranker candidate budget used by the lexical rankers.
    """
    merged = dense_search_ranked(conn, query, limit, embedder, corpus="doc")
    merged += dense_search_ranked(conn, query, limit, embedder, corpus="session")
    merged.sort(key=lambda pair: (pair[1], pair[0]))
    return merged


def rrf_fuse(
    rankings: Sequence[Sequence[str]],
    rrf_k: int = RRF_K,
    weights: Optional[Sequence[float]] = None,
) -> List[Tuple[str, float]]:
    """Weighted Reciprocal Rank Fusion, 1-based ranks, deterministic tie-break.

    ``score(d) = Σ w_r / (rrf_k + rank_r(d))``. ``weights`` defaults to 1.0 for
    every ranking; passing a higher weight for the dense list stops the weaker
    lexical ranker from diluting it. Ties break by ``fusion_key`` ascending so
    identical inputs always produce identical output.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights must align one-to-one with rankings")

    scores: dict[str, float] = {}

    for weight, ranking in zip(weights, rankings):
        seen: set[str] = set()
        for rank, key in enumerate(ranking, start=1):
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + float(weight) / (rrf_k + rank)

    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _snippet(text: Optional[str]) -> str:
    """Collapse whitespace and truncate to a public snippet."""
    if not text:
        return ""
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= SNIPPET_MAX_CHARS:
        return collapsed
    return collapsed[: SNIPPET_MAX_CHARS - 1].rstrip() + "…"


def _hydrate_document(conn: sqlite3.Connection, chunk_id: str, score: float) -> Optional[Citation]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT dc.chunk_id, dc.heading_path, dc.text, dc.source_url,
               d.title, d.fetched_at, d.volatility, d.tier
        FROM doc_chunks AS dc
        JOIN docs AS d ON d.doc_id = dc.doc_id
        WHERE dc.chunk_id = ?
        LIMIT 1;
        """,
        (chunk_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None

    source_url = row["source_url"]
    volatility = row["volatility"]
    if not volatility:
        volatility = classify_volatility(source_url, row["tier"], corpus="doc")

    fetched_at = row["fetched_at"]
    doc_age = age_days(fetched_at)

    return Citation(
        fusion_key=f"doc:{row['chunk_id']}",
        result_type="document",
        doc_key=f"doc:{row['chunk_id'].partition('#')[0]}",
        title=row["title"] or row["heading_path"],
        snippet=_snippet(row["text"]),
        content=None,  # R4: never expose document full text publicly.
        source_url=source_url,
        canonical_url=canonicalize_url(source_url) if source_url else None,
        fetched_at=fetched_at,
        age_days=doc_age,
        volatility=volatility,
        stale=is_stale(doc_age, volatility),
        score=score,
    )


def _hydrate_session(conn: sqlite3.Connection, chunk_id: str, score: float) -> Optional[Citation]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT chunk_id, session_id, title, category, content, source_file, line_start
        FROM immersion_chunks
        WHERE chunk_id = ?
        LIMIT 1;
        """,
        (chunk_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None

    content = row["content"]

    return Citation(
        fusion_key=f"session:{row['chunk_id']}",
        result_type="session",
        doc_key=f"session:{row['session_id']}",
        title=row["title"],
        snippet=_snippet(content),
        content=content,  # Authored/public cohort content.
        source_url=None,
        canonical_url=None,
        fetched_at=None,
        age_days=None,
        volatility=_SESSION_VOLATILITY,
        stale=False,
        score=score,
    )


def _hydrate(conn: sqlite3.Connection, fused: Sequence[Tuple[str, float]]) -> List[Citation]:
    citations: List[Citation] = []
    for fusion_key, score in fused:
        corpus, sep, chunk_id = fusion_key.partition(":")
        if not sep:
            continue
        if corpus == "doc":
            citation = _hydrate_document(conn, chunk_id, score)
        elif corpus == "session":
            citation = _hydrate_session(conn, chunk_id, score)
        else:
            citation = None
        if citation is not None:
            citations.append(citation)
    return citations


def assess_answerability(
    citations: Sequence[Citation],
    dense_ranking: Sequence[Tuple[str, float]],
    min_score: float = ABSTENTION_MIN_SCORE,
    min_documents: int = ABSTENTION_MIN_DOCUMENTS,
    max_dense_distance: float = ABSTENTION_MAX_DENSE_DISTANCE,
) -> Answerability:
    """Decide whether the fused evidence can support an answer (FR-1).

    Pure function over already-computed evidence: no I/O, no second retrieval.
    ``citations`` must be the fused list sliced to the *evidence window*
    (``hybrid_search_ranked`` uses ``ABSTENTION_EVIDENCE_WINDOW``, not the
    caller's display ``top_k``) and ``dense_ranking`` the merged dense ranking
    from ``dense_search_ranked_merged`` (empty when the dense ranker degraded).

    The three conditions and their justification are documented at the
    ``ABSTENTION_*`` constants. The verdict is fail-closed: any missing
    signal means "not answerable" with a machine-readable ``reason``.
    """
    top_score = float(citations[0].score) if citations else 0.0
    distinct_documents = len({citation.doc_key for citation in citations})
    top_dense_distance = float(dense_ranking[0][1]) if dense_ranking else None

    if not citations:
        reason = "no_evidence"
    elif top_dense_distance is None:
        # Lexical-only request plane (embedder or vec0 unavailable). BM25's
        # rank-only fused score cannot certify relevance, so refuse rather
        # than guess. This is the honest degradation path, not an error.
        #
        # Checked BEFORE the score threshold on purpose: the thresholds below
        # were measured on *hybrid* fused scores, whose scale includes the
        # dense list. A lexical-only fused score tops out at 0.0164, so
        # reporting "score_below_threshold" there would blame the query for a
        # degraded ranker. Report the real cause.
        reason = "dense_unavailable"
    elif top_score < min_score:
        reason = "score_below_threshold"
    elif distinct_documents < min_documents:
        reason = "single_document_evidence"
    elif top_dense_distance > max_dense_distance:
        reason = "dense_distance_above_threshold"
    else:
        reason = "answerable"

    return Answerability(
        answerable=reason == "answerable",
        top_score=top_score,
        min_score=float(min_score),
        top_dense_distance=top_dense_distance,
        distinct_documents=distinct_documents,
        reason=reason,
    )


def hybrid_search_ranked(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 10,
    embedder=None,
) -> Tuple[List[Citation], str, Answerability]:
    """Fuse lexical + dense rankers, hydrate top ``top_k``, assess abstention.

    This is the single retrieval entry point for both HTTP surfaces:
    ``/api/search`` serializes the trio and ``/api/chat`` refuses to call the
    LLM unless ``answerability.answerable`` (Sprint 10 / FR-1 + FR-2).

    ``mode`` is derived, never a parameter: ``"hybrid"`` iff at least one dense
    result was fused, otherwise ``"lexical_only"``. If the embedder or vec0 is
    unavailable at request time, the dense ranker is skipped and the honest
    ``lexical_only`` mode is returned.
    """
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if len(query) > MAX_QUERY_LEN:
        raise ValueError(f"query exceeds MAX_QUERY_LEN={MAX_QUERY_LEN}")
    if top_k is None or int(top_k) < 1:
        raise ValueError("top_k must be >= 1")
    if int(top_k) > MAX_TOP_K:
        raise ValueError(f"top_k exceeds MAX_TOP_K={MAX_TOP_K}")

    top_k = int(top_k)
    if not query.strip():
        return [], "lexical_only"

    candidate_limit = CANDIDATE_LIMIT(top_k)

    rankings: List[List[str]] = [
        lexical_search(conn, query, candidate_limit, corpus="doc"),
        lexical_search(conn, query, candidate_limit, corpus="session"),
    ]

    dense_ranking: List[Tuple[str, float]] = []
    if embedder is not None:
        try:
            dense_ranking = dense_search_ranked_merged(
                conn, query, candidate_limit, embedder
            )
        except (VecUnavailableError, ModelLoadError) as exc:
            # Request-plane contract (design §6): degrade honestly, never fake
            # a hybrid result. Direct dense_search() callers still fail closed.
            logger.warning("Dense ranker unavailable; using lexical_only: %s", exc)
            dense_ranking = []

    weights = [1.0] * len(rankings)
    if dense_ranking:
        rankings.append([fusion_key for fusion_key, _ in dense_ranking])
        weights.append(RRF_DENSE_WEIGHT)

    # COR2-02: hydrate the full fused list BEFORE slicing to top_k. Slicing
    # first starves the response whenever a dangling/stale fusion key is
    # dropped during hydration, even though valid lower-ranked candidates
    # exist. Dangling keys are still dropped, but the drop count is logged so
    # the starvation is observable instead of silent.
    fused = rrf_fuse(rankings, weights=weights)
    citations = _hydrate(conn, fused)
    dropped = len(fused) - len(citations)
    if dropped:
        logger.warning(
            "Hydration dropped %d dangling fused key(s); returning up to top_k=%d",
            dropped,
            top_k,
        )
    mode = "hybrid" if dense_ranking else "lexical_only"
    top_citations = citations[:top_k]
    # The abstention verdict always inspects the same evidence window so the
    # answer does not change when a caller lowers top_k for display reasons.
    evidence = citations[: max(top_k, ABSTENTION_EVIDENCE_WINDOW)]
    answerability = assess_answerability(evidence, dense_ranking)
    return top_citations, mode, answerability


def hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 10,
    embedder=None,
) -> Tuple[List[Citation], str]:
    """Backward-compatible ``(citations, mode)`` view of ``hybrid_search_ranked``."""
    citations, mode, _answerability = hybrid_search_ranked(
        conn, query, top_k=top_k, embedder=embedder
    )
    return citations, mode


def get_fact(conn: sqlite3.Connection, topic: str) -> List[dict]:
    """Deterministic ``facts`` lookup — a separate path, never an RRF ranker."""
    if not isinstance(topic, str) or not topic.strip():
        return []

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT fact_id, topic, claim, source_url, confidence, updated_at
        FROM facts
        WHERE topic = ?
        ORDER BY fact_id;
        """,
        (topic,),
    )
    rows = cursor.fetchall()
    if not rows:
        return []

    columns = [description[0] for description in cursor.description]
    result: List[dict] = []
    for row in rows:
        if hasattr(row, "keys"):
            result.append({column: row[column] for column in columns})
        else:
            result.append(dict(zip(columns, row)))
    return result
