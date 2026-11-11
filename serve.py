#!/usr/bin/env python3
"""
Google for Startups Immersion 2026 — Public Knowledge Hub Server.
Adapted from AGNTCon conference hub architecture for GFS Immersion content.

Hosts:
1. Static Web Portal (site/index.html + assets) on port 8080.
2. REST API (/api/search, /api/page, /api/chat) backed by ``crawl.retrieve``
   (hybrid BM25 + BGE dense retrieval over the doc and session corpora).
3. Retrieval-only chat (Sprint 11 / R5). The server performs no inference: it
   returns evidence excerpts, citations and the assembled prompt, and the
   client generates with the user's own key. ``/api/ground`` exposes the single
   ``validate_grounding`` implementation so the client cannot fork it.

No server component accepts, stores, logs or forwards a user credential.
"""

import os
import sys

# Auto-resolve venv if deps missing
try:
    import httpx
except ModuleNotFoundError:
    hub_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate_venvs = [
        os.path.join(hub_parent, ".venv", "bin", "python"),
        os.path.join(os.path.expanduser("~"), ".openclaw", "venv", "bin", "python"),
    ]
    for venv_py in candidate_venvs:
        if os.path.exists(venv_py) and sys.executable != venv_py:
            os.execv(venv_py, [venv_py, *sys.argv])

import argparse
import asyncio
import datetime
import hashlib
import html
import ipaddress
import json
import logging
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

try:
    from http.server import ThreadingHTTPServer
except ImportError:
    ThreadingHTTPServer = HTTPServer

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(HUB_DIR, "site")
CATALOG_JSON = os.path.join(HUB_DIR, "data", "catalog.json")

# Path to the staged SQLite retrieval index. The default is resolved relative
# to the hub directory (SEC2-01), matching the container layout where the index
# lives at /app/data/ inside the published unit. Never resolve this against the
# repository root: in the image that would point at /data/ (outside the build
# context) and silently serve empty 200 responses.
GFS_DB_PATH = os.environ.get(
    "GFS_DB_PATH",
    os.path.join(HUB_DIR, "data", "gfs_immersion_2026.sqlite"),
)

# Request-body hard cap (SEC2-06). Applies before any body materialization.
MAX_REQUEST_BODY_BYTES = 64 * 1024


class RequestBodyTooLargeError(Exception):
    """Raised when a request body exceeds MAX_REQUEST_BODY_BYTES."""

# SEC2-04: X-Forwarded-For is only trusted when the immediate peer is inside an
# explicitly configured proxy allowlist. Empty by default (safe): the header is
# ignored and client_address is authoritative. Set GFS_TRUSTED_PROXY_CIDRS to a
# comma-separated list of CIDRs (e.g. the Cloud Run GFE ranges) to opt in.
TRUSTED_PROXY_CIDRS = [
    entry.strip()
    for entry in os.environ.get("GFS_TRUSTED_PROXY_CIDRS", "").split(",")
    if entry.strip()
]

# Service degradation state (SEC2-01 / SEC2-02). Populated by the startup
# pre-flight; /api/health reports it instead of pretending to be healthy.
_SERVICE_STATE = {
    "degraded": False,
    "reason": None,
    "database": {},
}
_PREFLIGHT_DONE = False

# ── Retrieval-core import shim (Sprint 09, design §4.3) ──────────────
# serve.py ships inside the public hub, while the retrieval core
# (crawl.retrieve / crawl.freshness) is vendored alongside it. When serve.py is
# run from a larger development checkout, the core can also live in an optional
# sibling directory. We therefore add two explicit, documented import roots:
#   1. an optional sibling core checkout (set GFS_DEV_CORE), if configured
#   2. <HUB_DIR>                     (container layout: /app with /app/crawl)
# This is a deliberate boundary shim, not dynamic/recursive path discovery.
_REPO_ROOT = os.path.dirname(HUB_DIR)
# Optional, explicit override for a larger checkout: GFS_DEV_CORE=/path/to/core
_import_roots = [p for p in (os.environ.get("GFS_DEV_CORE"), HUB_DIR) if p]
for _import_root in _import_roots:
    if _import_root not in sys.path:
        sys.path.insert(0, _import_root)

from crawl import retrieve as retrieval  # noqa: E402
from crawl.freshness import query_requires_freshness  # noqa: E402
from crawl.relevance import (  # noqa: E402
    classify_relevance,
    redirect_message,
)
from crawl.relevance import suggested_questions as relevance_suggested_questions  # noqa: E402

MAX_SEARCH_TOP_K = retrieval.MAX_TOP_K
MAX_SEARCH_QUERY_LEN = retrieval.MAX_QUERY_LEN

# Lazy embedder state (design §11 guardrail 2): the model is loaded on the
# first dense search, never at container boot.
_EMBEDDER = None
_EMBEDDER_UNAVAILABLE = False
_EMBEDDER_LOCK = threading.Lock()

# Sprint 18: lazy CPU reranker. Imported inside the getter so a missing
# optional dependency never breaks server import.
_RERANKER = None
_RERANKER_UNAVAILABLE = False
_RERANKER_LOCK = threading.Lock()

# ── Rate Limiting ──────────────────────────────────────────────────
# SEC2-05: bounded LRU + TTL structure. Entries are pruned by age and the
# map is capped, so spoofed/rotating identities cannot grow process memory
# without limit over the container lifetime.
CHAT_RATE_LIMIT: "OrderedDict[str, list[float]]" = OrderedDict()
MAX_CHAT_PER_IP_PER_HOUR = 20
CHAT_RATE_WINDOW_SECONDS = 3600
CHAT_RATE_LIMIT_MAX_ENTRIES = 4096
_rate_limit_lock = threading.Lock()

# ── Sprint 17: Web-ground fallback constants and budget state ────────
# Triggered only when both Google CSE env vars are present at runtime.
WEB_GROUND_ENABLED = bool(
    os.environ.get("GFS_SEARCH_API_KEY") and os.environ.get("GFS_SEARCH_ENGINE_ID")
)

# Page fetching is INDEPENDENT of the server holding a search key.
#
# The search itself happens in the visitor's browser with their own Google key
# (BYO mode), so the server needs no credentials at all. But the browser cannot
# read third-party pages directly (no CORS), so /api/fetch exists to return page
# text. It never receives a credential — only a URL from a fixed allowlist, which
# is SSRF-guarded — so there is no reason to gate it behind a server-side key.
#
# Without this, a BYO-only deployment would let users search but give them only
# 200-character teasers instead of the real page content.
PAGE_FETCH_ENABLED = bool(os.environ.get("GFS_PAGE_FETCH_DISABLED") != "1")

# When the server DOES hold a search key it provides its own web-ground tier.
# Keeping the flag exposed lets callers report which mode is active.
WEB_GROUND_SERVER_KEY_PRESENT = WEB_GROUND_ENABLED
WEB_GROUND_DOMAIN_ALLOWLIST = list(retrieval.WEB_GROUND_DEFAULT_ALLOWLIST)
WEB_GROUND_MAX_DAILY_CALLS = 80
WEB_GROUND_MAX_PER_IP_HOUR = 5
WEB_GROUND_MAX_RESULTS = 3
WEB_GROUND_MAX_BYTES_PER_PAGE = 50 * 1024
WEB_GROUND_FETCH_TIMEOUT_SECONDS = 10

# Per-IP rolling window for web-ground calls. Same TTL/LRU structure as
# CHAT_RATE_LIMIT so spoofed identities cannot grow memory without bound.
WEB_GROUND_IP_LIMIT: "OrderedDict[str, list[float]]" = OrderedDict()
WEB_GROUND_LIMIT_MAX_ENTRIES = 4096
_web_ground_rate_lock = threading.Lock()
_web_ground_daily_calls = 0
_web_ground_daily_lock = threading.Lock()

# ── Concurrency ────────────────────────────────────────────────────
MAX_CONCURRENT_CHATS = 2
import concurrent.futures
_chat_lock = threading.Lock()
_active_chats = 0
_chat_queue: list = []

# How many fused citations /api/chat grounds an answer on (Sprint 10 / FR-2).
# Matches the previous 5-excerpt budget; the difference is that those
# excerpts now come from the full corpus and carry resolvable URLs.
CHAT_TOP_K = 5

def load_catalog() -> dict:
    if os.path.exists(CATALOG_JSON):
        with open(CATALOG_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {"sessions": [], "concepts": [], "registry": []}


def _inspect_database(path: str) -> dict:
    """Return the physical capabilities of the staged retrieval index.

    SEC2-01/SEC2-02: a legacy session-only DB must never masquerade as a
    healthy hybrid index. We inspect the artifact itself, not just its path.
    """
    caps = {
        "path": path,
        "exists": os.path.exists(path),
        "docs": False,
        "doc_chunks": False,
        "vec_chunks": False,
        "immersion_chunks": False,
    }
    if not caps["exists"]:
        return caps

    try:
        conn = sqlite3.connect(path)
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view');"
                )
            }
            for name in ("docs", "doc_chunks", "vec_chunks", "immersion_chunks"):
                caps[name] = name in names
            # Counts only for plain/FTS tables; vec0 cannot be counted without
            # the loadable extension and its existence check is sufficient.
            if caps["docs"]:
                caps["docs_count"] = conn.execute(
                    "SELECT count(*) FROM docs;"
                ).fetchone()[0]
            if caps["doc_chunks"]:
                caps["doc_chunks_count"] = conn.execute(
                    "SELECT count(*) FROM doc_chunks;"
                ).fetchone()[0]
            if caps["immersion_chunks"]:
                caps["immersion_chunks_count"] = conn.execute(
                    "SELECT count(*) FROM immersion_chunks;"
                ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        caps["error"] = str(exc)

    return caps


def preflight_database(force: bool = False) -> dict:
    """Startup pre-flight: refuse to pretend a legacy/broken index is healthy."""
    global _PREFLIGHT_DONE

    if _PREFLIGHT_DONE and not force:
        return _SERVICE_STATE

    caps = _inspect_database(GFS_DB_PATH)
    missing = [
        name for name in ("docs", "doc_chunks", "vec_chunks") if not caps.get(name)
    ]

    if not caps["exists"]:
        reason = f"retrieval database missing: {GFS_DB_PATH}"
    elif missing:
        reason = "retrieval database missing required tables: " + ", ".join(missing)
    else:
        reason = None

    _SERVICE_STATE.update(
        {
            "degraded": reason is not None,
            "reason": reason,
            "database": caps,
        }
    )
    if reason:
        logger.critical(
            "RETRIEVAL DB PREFLIGHT FAILED — service marked DEGRADED: %s", reason
        )
    else:
        logger.info("Retrieval DB preflight OK: %s", caps)
    _PREFLIGHT_DONE = True
    return _SERVICE_STATE


def _check_and_record_chat(client_ip: str, now: float) -> bool:
    """TTL + LRU bounded chat rate limiter (SEC2-05).

    Returns False when the caller is over the per-IP hourly budget. Stale
    entries are swept on every call and the map is capped, so rotating or
    spoofed identities cannot cause unbounded memory growth.
    """
    with _rate_limit_lock:
        stale = [
            ip
            for ip, stamps in CHAT_RATE_LIMIT.items()
            if not stamps or now - stamps[-1] >= CHAT_RATE_WINDOW_SECONDS
        ]
        for ip in stale:
            CHAT_RATE_LIMIT.pop(ip, None)

        # Evict BEFORE inserting, and only when this identity is new: an
        # existing identity does not add an entry, so pruning to cap-1 would
        # discard an unrelated live window for nothing. Inserting afterwards
        # leaves len() <= CHAT_RATE_LIMIT_MAX_ENTRIES at all times.
        if client_ip not in CHAT_RATE_LIMIT:
            while len(CHAT_RATE_LIMIT) >= CHAT_RATE_LIMIT_MAX_ENTRIES:
                CHAT_RATE_LIMIT.popitem(last=False)

        window = [
            stamp
            for stamp in CHAT_RATE_LIMIT.get(client_ip, [])
            if now - stamp < CHAT_RATE_WINDOW_SECONDS
        ]
        if len(window) >= MAX_CHAT_PER_IP_PER_HOUR:
            return False

        window.append(now)
        CHAT_RATE_LIMIT[client_ip] = window
        CHAT_RATE_LIMIT.move_to_end(client_ip)
        return True


def _check_and_record_web_ground(client_ip: str, now: float) -> bool:
    """TTL + LRU bounded web-ground rate limiter (V-15).

    Returns False when the caller is over the per-IP hourly budget or when the
    shared daily cap has been reached.
    """
    global _web_ground_daily_calls

    with _web_ground_rate_lock:
        stale = [
            ip
            for ip, stamps in WEB_GROUND_IP_LIMIT.items()
            if not stamps or now - stamps[-1] >= CHAT_RATE_WINDOW_SECONDS
        ]
        for ip in stale:
            WEB_GROUND_IP_LIMIT.pop(ip, None)

        if client_ip not in WEB_GROUND_IP_LIMIT:
            while len(WEB_GROUND_IP_LIMIT) >= WEB_GROUND_LIMIT_MAX_ENTRIES:
                WEB_GROUND_IP_LIMIT.popitem(last=False)

        window = [
            stamp
            for stamp in WEB_GROUND_IP_LIMIT.get(client_ip, [])
            if now - stamp < CHAT_RATE_WINDOW_SECONDS
        ]
        if len(window) >= WEB_GROUND_MAX_PER_IP_HOUR:
            return False

        with _web_ground_daily_lock:
            if _web_ground_daily_calls >= WEB_GROUND_MAX_DAILY_CALLS:
                return False
            _web_ground_daily_calls += 1

        window.append(now)
        WEB_GROUND_IP_LIMIT[client_ip] = window
        WEB_GROUND_IP_LIMIT.move_to_end(client_ip)
        return True


def _truncate_for_cse(query: str, max_len: int = 256) -> str:
    """Cap the egressed query to a single line of at most ``max_len`` chars (V-16)."""
    single = re.sub(r"[\r\n]+", " ", query).strip()
    return single[:max_len]


def _get_embedder():
    """Lazily construct the BGE query embedder on the first dense search.

    Returns ``None`` when the model cannot be loaded. That is a declared
    request-plane degradation — the caller must report ``mode: "lexical_only"``
    and must never terminate the process (design §6).
    """
    global _EMBEDDER, _EMBEDDER_UNAVAILABLE

    if _EMBEDDER is not None or _EMBEDDER_UNAVAILABLE:
        return _EMBEDDER

    with _EMBEDDER_LOCK:
        if _EMBEDDER is not None or _EMBEDDER_UNAVAILABLE:
            return _EMBEDDER
        try:
            from crawl.embed import EmbeddingEngine

            _EMBEDDER = EmbeddingEngine()
            logger.info("Lazy-loaded embedding model on first dense search")
        except Exception as exc:  # ModelLoadError and discovery failures
            _EMBEDDER_UNAVAILABLE = True
            logger.warning(
                "Embedding model unavailable; serving lexical_only: %s", exc
            )
        return _EMBEDDER


def _get_reranker():
    """Lazily construct the CPU reranker on first use.

    Returns ``None`` when the model is missing, oversized, or cannot be
    loaded. The retrieval path then behaves exactly as it did before Sprint
    18 and honestly reports ``reranker_used: false``.
    """
    global _RERANKER, _RERANKER_UNAVAILABLE

    if _RERANKER is not None or _RERANKER_UNAVAILABLE:
        return _RERANKER

    with _RERANKER_LOCK:
        if _RERANKER is not None or _RERANKER_UNAVAILABLE:
            return _RERANKER
        try:
            from crawl.rerank import CPUReranker

            _RERANKER = CPUReranker()
            if _RERANKER.unavailable:
                _RERANKER = None
                _RERANKER_UNAVAILABLE = True
                logger.info("Reranker unavailable; hybrid path unchanged")
            else:
                logger.info("Lazy-loaded CPU reranker on first search")
        except Exception as exc:
            _RERANKER_UNAVAILABLE = True
            logger.warning("Reranker load failed; serving without reranking: %s", exc)
        return _RERANKER


def _open_search_connection() -> sqlite3.Connection:
    """Open the retrieval DB and best-effort load sqlite-vec.

    If the extension cannot be loaded, the connection is still usable for the
    lexical rankers; the dense ranker fails closed inside ``hybrid_search`` and
    the response is honestly marked ``lexical_only``.
    """
    conn = sqlite3.connect(GFS_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception as exc:
        logger.warning("sqlite-vec unavailable; dense ranker disabled: %s", exc)
    finally:
        try:
            conn.enable_load_extension(False)
        except Exception:
            pass
    return conn


def _citation_to_result(conn: sqlite3.Connection, citation) -> dict:
    """Serialize a Citation without losing the legacy session-result contract."""
    result = {
        "result_type": citation.result_type,
        "title": citation.title,
        "snippet": citation.snippet,
        "content": citation.content,
        "source_url": citation.source_url,
        "canonical_url": citation.canonical_url,
        "fetched_at": citation.fetched_at,
        "age_days": citation.age_days,
        "volatility": citation.volatility,
        "stale": citation.stale,
        "score": citation.score,
        "fusion_key": citation.fusion_key,
    }

    if citation.result_type == "session":
        chunk_id = citation.fusion_key.partition(":")[2]
        row = conn.execute(
            """
            SELECT session_id, source_file, line_start
            FROM immersion_chunks
            WHERE chunk_id = ?
            LIMIT 1;
            """,
            (chunk_id,),
        ).fetchone()
        result.update(
            {
                # CMP2-02: preserve the historic <=800-char session excerpt
                # exactly. The hybrid path must be additive, not a payload-size
                # change for existing consumers.
                "content": (
                    citation.content[:800] if citation.content is not None else None
                ),
                "session_id": row["session_id"] if row is not None else None,
                "source_file": row["source_file"] if row is not None else None,
                "line_start": row["line_start"] if row is not None else None,
            }
        )
    else:
        # R4: document full text is never exposed. Every legacy key is present
        # and null-valued so existing consumers cannot hit a KeyError.
        result.update(
            {
                "content": None,
                "session_id": None,
                "source_file": None,
                "line_start": None,
            }
        )

    return result


def search_hybrid(query: str, top_k: int = 10, corpus=None, scope=None) -> dict:
    """Run the shared retrieval core and return the search payload.

    Sprint 10 / FR-1 adds the abstention trio (``answerable``, ``top_score``,
    ``min_score``) plus the supporting evidence (``evidence_documents``,
    ``top_dense_distance``, ``abstention_reason``) so a client can refuse to
    answer instead of trusting ``total`` (which only echoes ``top_k``).
    Every legacy key is preserved.

    Sprint 13 / FR-1 additivity: ``corpus`` (``"doc"`` | ``"session"`` |
    ``None``) is forwarded verbatim to ``hybrid_search_ranked``, and ``scope``
    (``"auto"`` | ``"cohort"`` | ``"docs"``) selects the routed path
    (``search_routed``). The default (both ``None``) reproduces the pre-Sprint-13
    call exactly, so every existing caller and payload key is unchanged.
    """
    payload = {
        "query": query,
        "mode": "lexical_only",
        "freshness_required": query_requires_freshness(query),
        "answerable": False,
        "top_score": 0.0,
        "min_score": retrieval.ABSTENTION_MIN_SCORE,
        "evidence_documents": 0,
        "top_dense_distance": None,
        "abstention_reason": "no_evidence",
        "total": 0,
        "results": [],
    }

    if not os.path.exists(GFS_DB_PATH):
        _SERVICE_STATE.update(
            {
                "degraded": True,
                "reason": f"retrieval database missing: {GFS_DB_PATH}",
            }
        )
        logger.critical(
            "Search DB missing at %s; service is DEGRADED (no results served)",
            GFS_DB_PATH,
        )
        return payload

    conn = _open_search_connection()
    try:
        reranker = _get_reranker()
        if scope is not None:
            # Sprint 13: routed path (design §8) — identical return tuple to
            # the hardened path, so the payload below needs no special case.
            citations, mode, answerability = retrieval.search_routed(
                conn, query, scope=scope, top_k=top_k, embedder=_get_embedder(),
                reranker=reranker,
            )
        else:
            citations, mode, answerability = retrieval.hybrid_search_ranked(
                conn, query, top_k=top_k, embedder=_get_embedder(), corpus=corpus,
                reranker=reranker,
            )
        results = [_citation_to_result(conn, citation) for citation in citations]
    finally:
        conn.close()

    payload["mode"] = mode
    payload["answerable"] = answerability.answerable
    payload["top_score"] = round(answerability.top_score, 6)
    payload["min_score"] = answerability.min_score
    payload["evidence_documents"] = answerability.distinct_documents
    payload["top_dense_distance"] = (
        round(answerability.top_dense_distance, 6)
        if answerability.top_dense_distance is not None
        else None
    )
    payload["abstention_reason"] = answerability.reason
    payload["total"] = len(results)
    payload["results"] = results
    payload["reranker_used"] = bool(answerability.reranker_used)
    payload["reranked_count"] = int(answerability.reranked_count)
    return payload


# NOTE (Sprint 10 / FR-2): the legacy ``search_fts5`` session-only helper was
# removed here. It was a second query builder living outside the retrieval
# core and it made /api/chat blind to the 392-document corpus. Both API
# surfaces now go through ``retrieval.hybrid_search_ranked``.


def _session_catalog() -> dict:
    """Map ``session_id`` -> catalog session row, for URL resolution."""
    return {s["id"]: s for s in load_catalog().get("sessions", [])}


def _looks_like_session_fact_query(message: str) -> bool:
    """True when the question asks for structured session metadata.

    Identity/listing questions (who presented, which sessions, when, what
    organisation) cannot be answered by embedding similarity: the catalog holds
    them as exact structured fields, and a paraphrased question sits far from
    any chunk in vector space (measured dense distance ~0.85, well past the
    0.55 answerability gate). They must be answered from the catalog.
    """
    m = (message or "").lower()
    asks_meta = any(
        w in m for w in ("session 01", "session 02", "session 03", "session 04",
                         "session 1", "session 2", "session 3", "session 4",
                         "all sessions", "each session", "sessions 01", "01 to 04",
                         "01-04", "grouped by session", "the cohort", "the speakers")
    )
    # Sprint 21b: "who is" alone is a trap. It matched "who is the CEO of
    # Microsoft" and "who is the president of France" and answered them from the
    # session catalog -- a confident answer to a question this hub cannot serve.
    # The question is only answerable from the catalog when it is scoped to the
    # cohort: either it explicitly asks about a presenter/speaker, or it names a
    # session AND asks a person question.
    asks_person_about_cohort = any(
        w in m for w in ("presenter", "speaker", "who presented", "who spoke")
    )
    asks_bare_person = any(w in m for w in ("who is", "who were", "names"))
    return asks_person_about_cohort or (asks_bare_person and asks_meta) or (
        asks_meta and any(w in m for w in ("title", "date", "topic", "cover"))
    )


#: Words that mean "give me the abstract of this session" rather than "find me
#: text inside it". These are deliberately separate from the metadata words
#: because they are answered from the catalog summary + highlights, not from the
#: structured fields and not from vector search.
_SESSION_SUMMARY_INTENT = (
    "takeaway", "takeaways", "take away", "take aways",
    "summary", "summarise", "summarize", "summarised", "summarized",
    "recap", "recaps", "tl;dr", "tldr",
    "lesson", "lessons", "key point", "key points",
    "main point", "main points", "main idea", "main ideas",
    "overview", "in a nutshell", "highlights", "key highlights",
    "what did they cover", "what does it cover", "what was covered",
    "bottom line", "so what",
)


# Sprint 21d: a QUESTION CAN NAME A RANGE ("session 1-3", "sessions 2 to 4").
# The old matcher only did substring tests for single sessions, which produced two
# opposite bugs on the same input shape:
#   "session 1-3"  -> matched the substring "session 1" -> returned ONLY session 01,
#                     silently dropping 02 and 03. The model then said 02/03 "were
#                     not retrieved" -- a false gap report caused by the parser.
#   "sessions 1-3" -> the plural defeated every variant -> matched nothing ->
#                     silently fell back to ALL sessions, including 04.
# Both are the same root cause: no range parsing.
_SESSION_RANGE_RE = re.compile(
    r"sessions?\s+0?(\d)\s*(?:-|\u2010|\u2011|\u2012|\u2013|\u2014|to\b|through\b|thru\b)\s*0?(\d)",
    re.I,
)
_SESSION_LIST_RE = re.compile(
    r"sessions?\s+((?:0?\d)(?:\s*(?:,|and)\s*0?\d)+)",
    re.I,
)


def _session_ids_in_ranges(m: str) -> list:
    """Session ids named by an explicit range or comma list, in catalog order."""
    ids: list = []

    for lo_s, hi_s in _SESSION_RANGE_RE.findall(m):
        lo, hi = int(lo_s), int(hi_s)
        if lo > hi:
            lo, hi = hi, lo
        for n in range(lo, hi + 1):
            ids.append(f"session_{n:02d}")

    for run in _SESSION_LIST_RE.findall(m):
        for n in re.findall(r"\d+", run):
            ids.append(f"session_{int(n):02d}")

    catalog = set(_session_catalog())
    seen: list = []
    for sid in ids:
        if sid in catalog and sid not in seen:
            seen.append(sid)
    return sorted(seen)


def _session_ids_mentioned(m: str) -> list:
    """Session ids named in ``m``, in catalog order.

    Handles ``session 04``, ``session 4``, ``session04``, the bare-number forms,
    and explicit ranges/lists (``session 1-3``, ``sessions 2 to 4``,
    ``sessions 1, 2 and 3``). Returns ``[]`` when the question is about sessions
    in general.
    """
    found = _session_ids_in_ranges(m)
    for sid in sorted(_session_catalog()):
        num = sid.rsplit("_", 1)[-1].lstrip("0") or "0"
        variants = (sid.replace("_", " "), f"session {num}", f"session{num}",
                    f"session 0{num}", sid)
        if any(v in m for v in variants) and sid not in found:
            found.append(sid)
    return found


# Sprint 23: "each of the four sessions", "all 4 sessions", "per session", "takeaways
# for the sessions" all range over the whole programme, but the old hard-coded
# phrases ("each session", "the sessions") missed the intervening words, so
# "takeaways for each of the four sessions" skipped the catalog-summary path and
# fell through to similarity search -- returning product-doc excerpts and an
# abstention, while "takeaway for session 03" worked. This pattern requires the
# PLURAL "sessions" so a single named session is unaffected.
_MULTI_SESSION_PLURAL_RE = re.compile(
    r"\b(?:each|every|all|both|per|across)\b[^.?!]{0,30}\bsessions\b"
    r"|\b(?:the|these|those|all|both)\s+(?:four|4|\d+)\s+sessions\b"
    r"|\bper\s+sessions?\b"
    r"|\bfor\s+the\s+sessions\b",
    re.I,
)


def _asks_about_multiple_sessions(m: str) -> bool:
    """True when the question ranges over several sessions (word-order tolerant)."""
    return bool(_MULTI_SESSION_PLURAL_RE.search(m or ""))


def _asks_about_all_sessions(m: str) -> bool:
    """True when the question ranges over the whole programme, not one session."""
    return any(
        w in m for w in ("all sessions", "all these sessions", "all the sessions",
                         "each session", "every session", "across sessions",
                         "across all", "the sessions", "these sessions",
                         "the whole programme", "the whole program", "all four",
                         "both sessions", "sessions overall")
    ) or _asks_about_multiple_sessions(m)


def _is_session_summary_query(message: str) -> bool:
    """True when the question asks for a session's SUMMARY rather than details.

    Sprint 21b: "what is the takeaway from session 04?" abstained with
    ``dense_distance_above_threshold`` at 0.8508 against a 0.84 gate, even though
    the answer is the catalog summary the UI already shows on the card. The
    catalog holds that text as an authored field, so embedding similarity is the
    wrong tool: the question is abstractive, and the abstract is already written.

    This is NOT a relaxation of the answerability gate. It only decides whether
    to answer from an authored summary instead of from retrieved chunks.
    """
    m = (message or "").lower()
    if not any(w in m for w in _SESSION_SUMMARY_INTENT):
        return False
    # Either a named session, or an explicit "the sessions" plural.
    if _session_ids_mentioned(m):
        return True
    return any(
        w in m for w in ("the sessions", "all sessions", "each session",
                         "the cohort", "across sessions", "these sessions")
    ) or _asks_about_multiple_sessions(m)


# Sprint 21f: how much of a session's own content a "takeaways" answer gets.
# Fixed rules, not per-question tuning: the sessions are small enough to hand the
# model their opening synthesis/executive-summary chunks.
SESSION_DIGEST_CHUNKS = 2        # per session, programme-wide question
SESSION_DIGEST_CHUNKS_SINGLE = 3 # per session, when exactly one session is named


def _session_content_excerpts(sid: str, limit: int = SESSION_DIGEST_CHUNKS) -> list:
    """The session's OWN authored chunks, in authored order (Sprint 21f).

    The catalog card is ~250 characters. The sessions are thousands:
    session_04 holds 15,138 characters across 21 chunks, session_02 24,881 across
    33. A "what is the takeaway from each session" question was answered from the
    card alone, which is exactly why the answers read as dry -- the real content
    was already indexed and simply never selected.

    Selection is a fixed rule: the first ``limit`` chunks of the session, in
    chunk order. ``chunk_001`` is the authored "Session N Synthesis" and
    ``chunk_002`` the executive summary, so this is the session's own opening
    thesis, not a query-dependent pick.
    """
    if not os.path.exists(GFS_DB_PATH):
        return []
    s = _session_catalog().get(sid, {})
    url = s.get("on_demand_url") or f"/api/page?type=source&name={sid}"
    rows = []
    try:
        conn = sqlite3.connect(GFS_DB_PATH)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT chunk_id, title, content
                FROM immersion_chunks
                WHERE session_id = ?
                ORDER BY chunk_id
                LIMIT ?
                """,
                (sid, int(limit)),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out: list = []
    for row in rows:
        body = (row["content"] or "").strip()
        if not body:
            continue
        out.append(
            {
                # The stored chunk id IS the citable key, so the user can verify
                # this text against the session page exactly.
                "fusion_key": f"session:{row['chunk_id']}",
                "title": f"{sid.replace('_', ' ').title()}: {row['title'] or ''}".strip(),
                "url": url,
                "snippet": body,
            }
        )
    return out


def _session_summary_excerpts(m: str, limit: int = 2) -> list:
    """Citable excerpts built from the catalog summary + highlights.

    One excerpt per requested session. The text is authored cohort knowledge
    that the portal already renders on the session card, so citing it is honest:
    the user can verify the identical text on the page.
    """
    sessions = _session_catalog()
    named = _session_ids_mentioned(m)
    wanted = named or sorted(sessions)
    # Sprint 21c: when the question is about ALL sessions, "limit" must not
    # truncate the answer. It previously defaulted to 2, so "takeaway from all
    # these sessions" cited only Sessions 01-02 and the model then told the user
    # the KB had no content for 03/04 -- a false gap report about our own corpus.
    if not named and _asks_about_all_sessions(m):
        limit = max(limit, len(wanted))
    excerpts: list = []
    for sid in wanted:
        s = sessions.get(sid)
        if not s:
            continue
        summary = (s.get("summary") or "").strip()
        highlights = [h for h in (s.get("highlights") or []) if h]
        if not summary and not highlights:
            continue
        body_parts = [f"{sid.replace('_', ' ').title()} — {s.get('title', '')}"]
        if s.get("date"):
            body_parts.append(f"Date: {s['date']}")
        if s.get("speakers"):
            body_parts.append("Speakers: " + ", ".join(s["speakers"]))
        if summary:
            body_parts.append("")
            body_parts.append("Summary: " + summary)
        if highlights:
            body_parts.append("")
            body_parts.append("Key highlights:")
            body_parts.extend(f"- {h}" for h in highlights)
        url = s.get("on_demand_url") or f"/api/page?type=source&name={sid}"
        excerpts.append(
            {
                "fusion_key": f"session:{sid}::summary",
                "title": f"{sid.replace('_', ' ').title()}: {s.get('title', '')} (summary)",
                "url": url,
                "snippet": "\n".join(body_parts),
            }
        )
        # Sprint 21f: the card is the header, the session's own chunks are the
        # substance. Without this the model reasons over ~250 chars per session
        # while thousands sit unused in the index.
        per = SESSION_DIGEST_CHUNKS_SINGLE if len(wanted) == 1 else SESSION_DIGEST_CHUNKS
        excerpts.extend(_session_content_excerpts(sid, limit=per))
    return excerpts


def _anchor_excerpts(message: str, limit: int = 2) -> list:
    """Exact-name fallback against ``framework_anchors``.

    Sprint 21b: the table holds the framework names the cohort actually uses
    ("Five Lenses", "Defensibility Hierarchy", "Sean Ellis 40%") mapped to the
    chunk that defines them, but NOTHING queried it. So "explain the ladder of
    proof" abstained on dense distance (0.8899) while "explain the Five Lenses"
    passed -- an arbitrary difference in phrasing, not in coverage.

    This runs only when the dense gate would ABSTAIN, and only on an exact
    anchor-token match, so it cannot manufacture answers for questions the index
    genuinely cannot serve: a nonsense string matches no anchor and still
    abstains. It is a lookup, not a relaxation of the gate.
    """
    if not os.path.exists(GFS_DB_PATH):
        return []
    m = (message or "").lower()
    tokens = set(re.findall(r"[a-z0-9]+", m))
    if not tokens:
        return []
    try:
        conn = sqlite3.connect(GFS_DB_PATH)
        rows = conn.execute(
            "SELECT anchor, chunk_id, source_url, provenance_origin "
            "FROM framework_anchors"
        ).fetchall()
    except Exception:
        logger.warning("anchor lookup unavailable", exc_info=True)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    hits = []
    for anchor, chunk_id, source_url, origin in rows:
        a_core = _anchor_core_tokens(anchor)
        if not a_core:
            continue
        # Sprint 21g: match on the DISTINCTIVE words. The earlier rule required
        # every token of "Five Lenses" to appear, so "explain the lenses from the
        # 4th session" missed the anchor outright because the user omitted "five"
        # -- and fell through to plain similarity search, which surfaced Lens 1
        # and Lens 4 only.
        if a_core.issubset(tokens):
            hits.append((len(a_core), anchor, chunk_id, source_url, origin))
    hits.sort(reverse=True)

    excerpts = []
    for _n, anchor, chunk_id, source_url, origin in hits[:limit]:
        # Sprint 21g: an anchor names a FRAMEWORK, and a framework has member
        # sections. Returning only the anchor chunk handed the model the title
        # plus one member, so "explain the lenses" described two lenses and
        # reported the other three as missing -- while all five lens chunks sat
        # in the index. Expand to the anchor's whole section (bounded).
        excerpts.extend(
            _anchor_section_excerpts(chunk_id, anchor, source_url, origin)
        )
    return excerpts


# Sprint 21g: quantifier/ordinal words carry no identity, so they are ignored
# when deciding whether a question names a framework. "Five Lenses" is matched
# by "lenses"; "Sean Ellis 40%" by "sean ellis".
_ANCHOR_QUANTIFIERS = frozenset(
    "one two three four five six seven eight nine ten first second third fourth "
    "fifth sixth seventh eighth ninth tenth".split()
)

# Cap on how many sibling chunks one anchor may contribute. A framework section
# is a handful of members; this stops a malformed document from flooding the
# prompt.
ANCHOR_SECTION_MAX_CHUNKS = 8

# Cap on the merged excerpt list when a framework anchor is combined with the
# similarity results, so a framework section can never flood the prompt.
ANCHOR_MERGE_MAX_EXCERPTS = 10


def _anchor_core_tokens(text: str) -> set:
    """Anchor tokens that carry identity: no digits, no quantifier words."""
    return {
        t
        for t in re.findall(r"[a-z0-9%]+", (text or "").lower())
        if t not in _ANCHOR_QUANTIFIERS and not t.isdigit()
    }


def _anchor_section_excerpts(
    anchor_chunk_id: str,
    anchor: str,
    source_url,
    origin,
    max_chunks: int = ANCHOR_SECTION_MAX_CHUNKS,
) -> list:
    """The anchor's defining section: the anchor chunk plus its siblings.

    Siblings are taken in chunk order from the same source document and stop at
    the next top-level numbered section (a title matching ``^N.``). That is a
    structural rule read from the authored document, not a per-question pick, so
    it generalises to any framework whose members are sub-items of its section.
    """
    if not anchor_chunk_id or not os.path.exists(GFS_DB_PATH):
        return []
    try:
        conn = sqlite3.connect(GFS_DB_PATH)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        head = conn.execute(
            "SELECT source_file FROM immersion_chunks WHERE chunk_id = ?",
            (anchor_chunk_id,),
        ).fetchone()
        if head is None:
            # A doc anchor: no section structure to expand, keep prior behaviour.
            body = _anchor_chunk_text(anchor_chunk_id)
            return [
                {
                    "fusion_key": f"anchor:{anchor_chunk_id}",
                    "title": f"{anchor} (framework anchor)",
                    "url": source_url
                    or f"/api/page?type=source&name={anchor_chunk_id}",
                    "snippet": body or f"{anchor} — defined in the cohort sessions.",
                    "provenance_origin": origin,
                }
            ]
        siblings = conn.execute(
            "SELECT chunk_id, title, content FROM immersion_chunks "
            "WHERE source_file = ? ORDER BY chunk_id",
            (head["source_file"],),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    out: list = []
    started = False
    for row in siblings:
        if not started:
            if row["chunk_id"] != anchor_chunk_id:
                continue
            started = True
        title = (row["title"] or "").strip()
        # A new top-level numbered section ends the anchor's section.
        if out and re.match(r"^\s*\d+\.", title):
            break
        body = (row["content"] or "").strip()
        if not body:
            continue
        base = row["chunk_id"].split("::", 1)[0].replace("_anchors", "")
        session = _session_catalog().get(base, {})
        url = (
            session.get("on_demand_url")
            or source_url
            or f"/api/page?type=source&name={base}"
        )
        out.append(
            {
                "fusion_key": f"anchor:{row['chunk_id']}",
                "title": f"{anchor}: {title}".strip(),
                "url": url,
                "snippet": body,
                "provenance_origin": origin,
            }
        )
        if len(out) >= max_chunks:
            break
    return out


def _anchor_chunk_text(chunk_id: str) -> str:
    """Best-effort text for an anchor chunk (session chunk or doc chunk)."""
    if not chunk_id:
        return ""
    try:
        conn = sqlite3.connect(GFS_DB_PATH)
    except Exception:
        return ""
    try:
        row = conn.execute(
            "SELECT content FROM immersion_chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        if row:
            return str(row[0])[:1700]
        key = chunk_id.split("#", 1)[0]
        suffix = chunk_id.split("#", 1)[1] if "#" in chunk_id else None
        row = conn.execute(
            "SELECT content FROM doc_chunks WHERE doc_id = ? LIMIT 1", (key,)
        ).fetchone()
        if row:
            return str(row[0])[:1700]
    except Exception:
        return ""
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return ""


def _session_fact_excerpts(limit: int = 4) -> list:
    """Build citable excerpts straight from the session catalog.

    This is the deterministic path for metadata questions: one excerpt per
    session, containing the authoritative speakers/organisation/date/title. It
    bypasses embedding search entirely, so a correctly-worded question cannot
    abstain merely because its vector happens to be far from the chunk text.
    """
    sessions = _session_catalog()
    excerpts: list = []
    for sid in sorted(sessions):
        s = sessions[sid]
        url = s.get("on_demand_url") or f"/api/page?type=source&name={sid}"
        speakers = s.get("speakers") or []
        speak_txt = ", ".join(speakers) if speakers else "not recorded"
        org = s.get("org") or ""
        date = s.get("date") or ""
        body = (
            f"{sid.replace('_', ' ').title()} — {s.get('title', '')}\n"
            f"Speakers: {speak_txt}" + (f" ({org})" if org else "") + "\n"
            f"Date: {date}"
        )
        excerpts.append(
            {
                "fusion_key": f"session:{sid}::catalog",
                "title": f"{sid.replace('_', ' ').title()}: {s.get('title', '')}",
                "url": url,
                "snippet": body,
            }
        )
        if len(excerpts) >= limit:
            break
    return excerpts



    """Resolve a public URL for a session chunk.

    Session chunks have ``source_url = NULL`` because they are authored
    markdown, not crawled pages. Prefer the catalog's on-demand recording URL;
    otherwise point at the portal's own session page, which always resolves.
    FR-3 requires a resolvable URL per citation, so this never returns a
    non-URL placeholder.

    Derived session ids (``session_04_anchors``) are anchors extracted from the
    parent session and must resolve to the PARENT's URL. Without this they had no
    catalog entry, returned ``None``, and every anchor chunk — including the
    canonical Five Lenses text — was silently dropped from the evidence window.
    """
    if not session_id:
        return None
    session = sessions.get(session_id)
    if session is None and "_" in session_id:
        # Fall back to the parent session (session_04_anchors -> session_04).
        parent = session_id.rsplit("_", 1)[0]
        session = sessions.get(parent)
        if session is not None:
            session_id = parent
    if session and session.get("on_demand_url"):
        return session["on_demand_url"]
    if session:
        return f"/api/page?type=source&name={session_id}"
    return None


def _sources_from_results(results: list) -> list:
    """Build ``[{title, url, fusion_key}]`` citations (Sprint 10 / FR-2 + FR-3).

    Previously /api/chat returned bare titles, which cannot be resolved or
    validated. One entry per distinct URL keeps the payload honest: five
    chunks of the same page are one source, not five.
    """
    return [
        {
            "title": excerpt["title"],
            "url": excerpt["url"],
            "fusion_key": excerpt["fusion_key"],
        }
        for excerpt in _excerpts_from_results(results)
    ]


def _result_url(row: dict, sessions: dict) -> str | None:
    """Resolve the one public URL a result row is citable by."""
    if row["result_type"] == "session":
        return _session_public_url(row.get("session_id"), sessions)
    return row.get("canonical_url") or row.get("source_url")


def _session_public_url(session_id: str | None, sessions: dict) -> str | None:
    """Resolve a public URL for a session chunk.

    Session chunks have ``source_url = NULL`` because they are authored
    markdown, not crawled pages. Prefer the catalog's on-demand recording URL;
    otherwise point at the portal's own session page, which always resolves.

    Derived session ids (``session_04_anchors``) are anchors extracted from the
    parent session and must resolve to the PARENT's URL. Without that fallback
    they had no catalog entry, returned ``None``, and every anchor chunk —
    including the canonical Five Lenses text — was silently dropped.
    """
    if not session_id:
        return None
    session = sessions.get(session_id)
    if session is None and "_" in session_id:
        parent = session_id.rsplit("_", 1)[0]
        session = sessions.get(parent)
        if session is not None:
            session_id = parent
    if session and session.get("on_demand_url"):
        return session["on_demand_url"]
    if session:
        return f"/api/page?type=source&name={session_id}"
    return None


def _excerpt_snippet(row: dict) -> str:
    """The text the client puts in front of the model for this citation."""
    if row["result_type"] == "session":
        return (row.get("content") or row.get("snippet") or "")[:500]
    return row.get("snippet") or ""


def _excerpts_from_results(results: list) -> list:
    """Build the evidence window ``[{fusion_key, title, url, snippet}]``.

    Sprint 11 / FR-1: this list *is* the chat answer now — the server no longer
    generates.

    One excerpt per distinct CHUNK, in ranking order. It used to be one excerpt
    per distinct URL, which silently destroyed the evidence window: every chunk
    of a single session shares one recording URL, so a five-lens document
    collapsed to a single (often irrelevant) chunk and the model was asked to
    answer from one paragraph. Citations may legitimately repeat a URL — the
    ``[n]`` marker indexes excerpts, and ``sources`` still dedupes by page.

    Rows without a resolvable URL are dropped: they could never be validated as
    a citation.
    """
    sessions = _session_catalog()
    excerpts: list = []
    seen_keys: set = set()
    for row in results:
        url = _result_url(row, sessions)
        if not url:
            continue
        # Dedupe by chunk identity, not by URL.
        identity = row.get("fusion_key") or id(row)
        if identity in seen_keys:
            continue
        seen_keys.add(identity)
        excerpts.append(
            {
                "fusion_key": row.get("fusion_key"),
                "title": row.get("title"),
                "url": url,
                "snippet": _excerpt_snippet(row),
            }
        )
    return excerpts


def _describe_excerpt_coverage(excerpts: list) -> str:
    """One line telling the model exactly which sessions/documents it was given.

    A scoped retrieval result is not a statement about the corpus. Making the
    scope explicit is what stops the model from reporting non-existent gaps.
    """
    sessions: list = []
    docs = 0
    for e in excerpts or []:
        key = str(e.get("fusion_key") or "")
        if key.startswith("session:"):
            sid = key.split(":", 1)[1].split("::", 1)[0]
            if sid and sid not in sessions:
                sessions.append(sid)
        elif key.startswith("doc:"):
            docs += 1
    parts = []
    if sessions:
        pretty = ", ".join(sorted(s.replace("_", " ").title() for s in sessions))
        parts.append(f"{len(sessions)} session excerpt(s): {pretty}")
    if docs:
        parts.append(f"{docs} document excerpt(s)")
    if not parts:
        return "Scope: no excerpts were provided for this question.\n"
    return (
        "Scope of this answer: " + "; ".join(parts) + ". "
        "These excerpts are the complete set provided for this specific question. "
        "A topic's absence here does not mean the hub lacks it — it was not "
        "requested or retrieved for this query.\n"
    )


def build_chat_prompt(excerpts: list) -> str:
    """Assemble the exact system prompt the client sends to its own provider.

    The retrieval-only server hands this to the browser instead of calling a
    model itself (Sprint 11 / FR-1 / R5). The citation-marker grammar is stated
    here, in the prompt, because ``validate_grounding`` enforces it.
    """
    rag_context = "## Retrieved knowledge base excerpts\n\n"
    for index, excerpt in enumerate(excerpts, start=1):
        rag_context += (
            f'<excerpt n="{index}" fusion_key="{excerpt.get("fusion_key")}" '
            f'url="{excerpt.get("url") or ""}">\n'
            f'**{excerpt.get("title")}**\n{excerpt.get("snippet")}\n</excerpt>\n\n'
        )

    # Sprint 21c: tell the model what the excerpt set IS. Without this it treated a
    # deliberately-scoped result as the whole knowledge base and reported gaps that
    # did not exist ("the KB only contains summaries for Sessions 01 and 02") when
    # the caller had simply asked about one session or been handed a limit.
    coverage = _describe_excerpt_coverage(excerpts)
    return build_system_prompt() + (
        f"\n\n{rag_context}"
        f"\n{coverage}"
        "\nGround every claim in the excerpts above. Cite each claim with "
        "the excerpt's fusion_key in square brackets (e.g. "
        "[doc:655f3836#0000]) or its excerpt number (e.g. [1]). Never "
        "invent a citation or a URL.\n"
        "If the excerpts above do not answer the question, say only that this "
        "particular question is not covered by the excerpts provided — do NOT "
        "claim that the hub lacks the material, and do NOT list sessions or "
        "topics as missing. You cannot see the rest of the knowledge base, so a "
        "gap you infer from these excerpts is not evidence of a gap in the corpus."
    )


ABSTENTION_MESSAGE = (
    "I don't have enough grounded material in this knowledge base to answer "
    "that reliably, so I won't guess. The hub covers the four Immersion "
    "sessions and the Google Cloud documentation it was built from — try "
    "asking about Cloud Run, ADK/SequentialAgent, Firestore, BigQuery, "
    "Secret Manager, or a specific session topic."
)


# ── Sprint 13 / FR-6: honest abstention support ────────────────────
#
# Two additive keys ride on the abstain branch only:
#   * ``closest_material`` — the nearest chunks, each tagged with why it was
#     NOT accepted. It is a SEPARATE top-level key and is structurally barred
#     from grounding: it is never merged into ``excerpts``/``sources`` and its
#     text is never interpolated into ``system_prompt`` (which stays ``None``
#     on abstention). Weak context must not become evidence.
#   * ``suggested_questions`` — deterministic, corpus-derived questions that
#     have EACH been re-verified against the SAME abstention gate as a real
#     query. An unverified guess is dropped, never shipped.

# The validation allowlist for the optional /api/chat ``scope`` field
# (Sprint 13 / FR-2). Anything else — a typo, an absent key, a non-string —
# resolves to "auto"; an unvalidated value is never forwarded to the core.
CHAT_SCOPE_VALUES = ("auto", "cohort", "docs")

# Bound the suggestion candidate set (FR-6c: "keep it cheap"). The catalogue
# yields a handful of titles; we never probe more than this per request and we
# stop as soon as ``limit`` verified questions are found.
MAX_SUGGESTION_CANDIDATES = 12
# One template, applied to deterministic corpus titles. Candidates are DERIVED
# from the corpus, never authored by a model.
SUGGESTION_TEMPLATE = "What is {title}?"


def _resolve_chat_scope(requested, user_message: str) -> str:
    """Validate the optional ``scope`` field and resolve it to a routed scope.

    Sprint 13 / FR-2: the only accepted values are ``auto`` | ``cohort`` |
    ``docs``; an unknown, absent or non-string value resolves to ``auto``. The
    field is never forwarded unvalidated. On ``auto`` the cohort classifier
    decides the routed scope; since option B (ratified 2026-11-08) the
    session-first AUTO gate is the routed scope for classifier-classified
    queries too (it already prefers sessions and adds the broad fallback),
    while an explicit ``cohort`` scope stays strict session-only.
    """
    if requested not in CHAT_SCOPE_VALUES:
        requested = "auto"
    if requested == "auto":
        # B (ratified 2026-11-08): the cohort classifier routes classified
        # queries through the SESSION-FIRST AUTO gate, not strict "cohort".
        # search_routed(scope="auto") already prefers sessions via
        # _session_first_probe_score, and it adds the legacy BROAD fallback
        # when the session evidence is NOT answerable -- so a cohort speaker
        # question that regressed to an abstention (p3: "what did Toby Knight
        # say about agent memory", session dense 0.8495 > 0.84) ANSWERS again
        # from docs. Strict "cohort" remains for explicit API callers who
        # truly want session-only (no silent docs fallback).
        return "auto"
    return requested


def _closest_material_from_results(results: list, reason: str) -> list:
    """Nearest chunks on an abstention, each tagged with why it was refused.

    Same shape as an excerpt plus ``not_accepted_reason`` and an explicit
    ``accepted: False`` flag. This list is DISPLAY-ONLY: the caller keeps it a
    separate top-level key and never lets it into ``excerpts``/``sources`` or
    the prompt (FR-6b).
    """
    material = []
    for excerpt in _excerpts_from_results(results):
        material.append(
            {
                "fusion_key": excerpt["fusion_key"],
                "title": excerpt["title"],
                "url": excerpt["url"],
                "snippet": excerpt["snippet"],
                "accepted": False,
                "not_accepted_reason": reason,
            }
        )
    return material


def _suggestion_candidates() -> list:
    """Deterministic candidate questions derived from the published catalog.

    Bounded (``MAX_SUGGESTION_CANDIDATES``) and ordered by catalog order so the
    output is reproducible. The candidates are NOT authored by a model: they
    are session/concept titles rendered through one fixed template.
    """
    catalog = load_catalog()
    titles: list = []
    for session in catalog.get("sessions", []):
        title = (session.get("title") or "").strip()
        if title:
            titles.append(title)
    for concept in catalog.get("concepts", []):
        title = (concept.get("title") or "").strip()
        if title:
            titles.append(title)

    candidates: list = []
    seen: set = set()
    for title in titles:
        # A question ending in "?" would double up under the template.
        normalized = title.rstrip("?").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(SUGGESTION_TEMPLATE.format(title=normalized))
    return candidates[:MAX_SUGGESTION_CANDIDATES]


def suggest_questions(query=None, limit: int = 3) -> list:
    """Return up to ``limit`` corpus-derived questions that ARE answerable.

    Sprint 13 / FR-6c. Hard invariant: every returned question is FILTERED by
    the SAME gate a real query passes — ``retrieval.search_routed`` +
    ``assess_answerability``. A candidate the gate refuses is dropped; if none
    clear the gate the result is ``[]`` (a valid, honest outcome). No candidate
    is asserted answerable.

    Cost is bounded: the candidate set is capped and iteration stops once
    ``limit`` verified questions exist (usually the first few).
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 3
    if limit < 1 or not os.path.exists(GFS_DB_PATH):
        return []

    candidates = _suggestion_candidates()
    if not candidates:
        return []

    verified: list = []
    conn = _open_search_connection()
    try:
        embedder = _get_embedder()
        for candidate in candidates:
            try:
                citations, _mode, answerability = retrieval.search_routed(
                    conn, candidate, scope="auto", top_k=10, embedder=embedder
                )
            except Exception:  # a fix-up must never break the abstain path
                logger.warning(
                    "Suggestion probe failed for %r; skipping", candidate,
                    exc_info=True,
                )
                continue
            if not answerability.answerable:
                continue
            verified.append(
                {
                    "question": candidate,
                    "top_score": round(answerability.top_score, 6),
                    "min_score": answerability.min_score,
                    "reason": answerability.reason,
                    "fusion_key": (
                        citations[0].fusion_key if citations else None
                    ),
                }
            )
            if len(verified) >= limit:
                break
    finally:
        conn.close()
    return verified


# ── Versioned system prompt (Sprint 10 / FR-5) ─────────────────────
# The prompt is a shipped asset, not an in-code f-string. It is read exactly
# once at import; ``PROMPT_VERSION`` is the sha256 of the file bytes, which is
# what the API reports. The template holds {{SESSIONS}} / {{CONCEPTS}}
# placeholders that are filled from the catalog per request — the version
# identifies the template, not a rendered instance.
PROMPT_DIR = os.path.join(HUB_DIR, "prompts")
SYSTEM_PROMPT_PATH = os.path.join(PROMPT_DIR, "system_v1.md")

_FALLBACK_SYSTEM_PROMPT = (
    "You are the Google for Startups Immersion 2026 AI Assistant. Answer only "
    "from the retrieved excerpts. Cite each claim with the excerpt's "
    "fusion_key in square brackets, e.g. [doc:655f3836#0000]. Never invent a "
    "citation. If the excerpts do not support an answer, say so instead of "
    "guessing.\n\n## Sessions\n{{SESSIONS}}\n\n## Concepts\n{{CONCEPTS}}\n"
)


def _load_system_prompt() -> tuple:
    """Read the shipped prompt once; return ``(template, sha256_hex)"."""
    try:
        with open(SYSTEM_PROMPT_PATH, "rb") as handle:
            raw = handle.read()
    except OSError as exc:  # pragma: no cover - packaging regression guard
        logger.critical("System prompt missing at %s: %s", SYSTEM_PROMPT_PATH, exc)
        raw = _FALLBACK_SYSTEM_PROMPT.encode("utf-8")
        return raw.decode("utf-8"), None
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


SYSTEM_PROMPT_TEMPLATE, PROMPT_VERSION = _load_system_prompt()


# ── Citation post-validation (Sprint 10 / FR-3) ────────────────────
# Fixed marker grammar the model is instructed to use:
#   [doc:655f3836#0000]        — canonical fusion_key
#   [session:session_03::chunk_002]
#   [1]                        — 1-based ordinal into the numbered excerpts
# Any marker (or bare URL) that is not part of the retrieved citation set is
# treated as fabricated; the sentence carrying it is dropped.
FUSION_MARKER_RE = re.compile(r"\[((?:doc|session):[^\s\[\]]+)\]")
ORDINAL_MARKER_RE = re.compile(r"\[(\d{1,3})\]")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def _normalize_url(url: str) -> str:
    """Compare citations on a stable shape: no scheme, no trailing slash, no punctuation.

    Sprint 21: ``URL_RE`` greedily captures whatever follows the URL, so a valid link at a
    clause boundary arrives as ``https://host/path,`` or ``https://host/path).`` and would
    be marked fabricated. Trailing sentence punctuation is stripped symmetrically with the
    capture so a legitimate end-of-sentence URL is not punished.
    """
    value = (url or "").strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("#", 1)[0].split("?", 1)[0]
    return value.rstrip("/").rstrip(".,;:!?")


def _sentence_spans(text: str) -> list:
    """Split text into (start, end) spans on sentence boundaries.

    A boundary is end-of-text or a run of whitespace preceded by terminal
    punctuation. URLs never contain whitespace, so ``https://x.y/z`` is not
    split mid-token. Trailing markers are already bound backwards by
    ``_bind_trailing_markers`` before this runs.
    """
    spans = []
    start = 0
    for match in re.finditer(r"(?<=[.!?])\s+", text):
        spans.append((start, match.start()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _bind_trailing_markers(text: str) -> str:
    """Move a citation marker that follows a full stop back before it.

    Models often emit ``Claim. [doc:x]`` instead of ``Claim [doc:x].``. Left
    alone, the marker becomes the start of the *next* sentence and the claim
    looks uncited. Binding it backwards is the conservative reading: a marker
    after a full stop annotates the sentence that just ended.
    """
    return re.sub(
        r"([.!?])(\s+)(\[(?:doc|session):[^\s\[\]]+\]|\[\d{1,3}\])",
        r" \3\1",
        text,
    )


def _is_stub_sentence(text: str) -> bool:
    """Return True if the sentence is a stub with no substantive claim.

    A stub is a very short remainder carrying no substantive claim, such as
    "Sure!", "OK.", "Here you go.", "Thanks!", or similar acknowledgements.
    Used to distinguish rejected from partial when only stub content survives.
    """
    # A stub carries no CONTENT word: every token is an acknowledgement or a
    # function word. Word-count alone is wrong here -- "Firestore is fast." is three
    # words but it is a real claim, and treating it as a stub would push a legitimate
    # partial answer down to rejected.
    cleaned = re.sub(r"[^\w\s]", "", text or "").strip().lower()
    if not cleaned:
        return True
    tokens = cleaned.split()
    if not tokens:
        return True
    filler = {
        # acknowledgements
        "sure", "ok", "okay", "yes", "no", "yep", "nope", "thanks", "thank",
        "great", "right", "correct", "indeed", "absolutely", "noted",
        # function words that carry no claim on their own
        "here", "you", "go", "got", "it", "that", "this", "is", "are", "was",
        "were", "be", "been", "a", "an", "the", "and", "or", "but", "so", "of",
        "to", "for", "in", "on", "at", "as", "we", "i", "will", "can", "now",
    }
    return all(tok in filler for tok in tokens)


def validate_grounding(answer_text: str, retrieved_citations: list) -> dict:
    """Strip fabricated citations from a generated answer (FR-3 + Sprint 21).

    ``retrieved_citations`` is the ``sources`` list built for this response:
    ``[{"title", "url", "fusion_key"}, ...]``, in citation-ordinal order.

    Returns ``{"answer", "ok", "checked", "dropped", "valid_markers",
    "fabricated", "degraded", "reason", "verification", "kept_sentences"}``.

    ``verification`` is one of: "cited" | "uncited" | "partial" | "rejected".
    ``ok`` is True only when ``verification == "cited"``.

    Pure function: no DB, no network, no model.
    """
    retrieved = list(retrieved_citations or [])
    allowed_keys = {
        c.get("fusion_key") for c in retrieved if c.get("fusion_key")
    }
    allowed_urls = {
        _normalize_url(c.get("url")) for c in retrieved if c.get("url")
    }
    ordinal_count = len([c for c in retrieved if c.get("url")])

    text = _bind_trailing_markers(answer_text or "")
    checked = 0
    dropped_sentences = 0
    fabricated: list = []
    valid_markers = 0
    kept_sentences = 0
    kept_with_valid_marker = 0  # Non-stub sentences kept that had valid markers
    kept_non_stub = 0  # Non-stub sentences kept (regardless of markers)

    out_lines = []
    for line in text.splitlines():
        spans = _sentence_spans(line)
        if not spans:
            out_lines.append(line)
            continue
        kept_parts = []
        for start, end in spans:
            sentence = line[start:end]
            keys = FUSION_MARKER_RE.findall(sentence)
            ordinals = ORDINAL_MARKER_RE.findall(sentence)
            urls = URL_RE.findall(sentence)
            # URLs now count toward checked (Sprint 21)
            checked += len(keys) + len(ordinals) + len(urls)

            bad = [k for k in keys if k not in allowed_keys]
            bad += [f"[{n}]" for n in ordinals if not 1 <= int(n) <= ordinal_count]
            bad += [u for u in urls if _normalize_url(u) not in allowed_urls]

            if bad:
                dropped_sentences += 1
                fabricated.extend(bad)
                continue
            has_valid = bool(keys or ordinals or urls)
            if keys or ordinals:
                valid_markers += len(keys) + len(ordinals)
            stripped = sentence.strip()
            if stripped:
                kept_sentences += 1
                if not _is_stub_sentence(sentence):
                    kept_non_stub += 1
                    if has_valid:
                        kept_with_valid_marker += 1
            kept_parts.append(sentence)

        joined = " ".join(part.strip() for part in kept_parts).strip()
        if joined:
            # Preserve list/heading prefixes that lived at line start.
            prefix = re.match(r"^\s*(?:[-*+]|\d+[.)]|#{1,6})\s+", line)
            if prefix and not re.match(r"^\s*(?:[-*+]|\d+[.)]|#{1,6})\s+", joined):
                joined = prefix.group(0) + joined
            out_lines.append(joined)
        elif not line.strip():
            out_lines.append("")

    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()

    # Sprint 21 verification tier decision table
    #
    # | Condition | verification | answer returned |
    # | fabricated == [] and (valid_markers > 0 or checked > 0) -> cited | cleaned
    # | fabricated == [] and valid_markers == 0 and checked == 0 -> uncited | ORIGINAL
    # | fabricated != [] and non-empty and has substance -> partial | cleaned
    # | cleaned empty or only stubs -> rejected | ABSTENTION_MESSAGE

    has_substance = kept_non_stub > 0 or kept_with_valid_marker > 0

    if not fabricated and (valid_markers > 0 or checked > 0):
        # All citations valid (or URL-only with validated URL)
        verification = "cited"
        return {
            "answer": cleaned,
            "ok": True,
            "checked": checked,
            "dropped": dropped_sentences,
            "valid_markers": valid_markers,
            "fabricated": fabricated,
            "degraded": False,
            "reason": "grounded",
            "verification": verification,
            "kept_sentences": kept_sentences,
        }

    if not fabricated and valid_markers == 0 and checked == 0 and cleaned:
        # No citations at all - return original text unchanged (not cleaned)
        verification = "uncited"
        return {
            "answer": answer_text or "",  # ORIGINAL, not cleaned
            "ok": False,
            "checked": checked,
            "dropped": dropped_sentences,
            "valid_markers": 0,
            "fabricated": fabricated,
            "degraded": False,  # Not degraded - advisory only
            "reason": "no_citation_markers",
            "verification": verification,
            "kept_sentences": kept_sentences,
        }

    # Some fabrications occurred
    if cleaned and has_substance:
        # Partial - some valid claims survive
        verification = "partial"
        return {
            "answer": cleaned,
            "ok": False,
            "checked": checked,
            "dropped": dropped_sentences,
            "valid_markers": valid_markers,
            "fabricated": fabricated,
            "degraded": False,
            "reason": "some_citations_fabricated",
            "verification": verification,
            "kept_sentences": kept_sentences,
        }

    # Rejected - only stubs survive or nothing at all
    verification = "rejected"
    return {
        "answer": ABSTENTION_MESSAGE,
        "ok": False,
        "checked": checked,
        "dropped": dropped_sentences,
        "valid_markers": valid_markers,
        "fabricated": fabricated,
        "degraded": True,
        "reason": "all_citations_fabricated",
        "verification": verification,
        "kept_sentences": kept_sentences,
    }


def grounding_report(verdict: dict) -> dict:
    """Small, stable projection of a verdict (kept for API compatibility).

    Sprint 11: ``/api/ground`` returns the full ``validate_grounding`` result,
    so the chat response no longer embeds this. It stays because it is the
    published, tested shape of the grounding *summary*.
    """
    return {
        "checked": verdict.get("checked", 0),
        "dropped": verdict.get("dropped", 0),
        "ok": bool(verdict.get("ok")),
        "valid_markers": verdict.get("valid_markers", 0),
        "fabricated": verdict.get("fabricated", []),
        "degraded": bool(verdict.get("degraded")),
        "reason": verdict.get("reason"),
        # Sprint 21: the verification tier is part of the published summary shape.
        "verification": verdict.get("verification"),
    }


def get_page_content(page_type: str, name: str) -> dict | None:
    """Get a session synthesis or concept page."""
    catalog = load_catalog()
    if page_type == "concept":
        for c in catalog.get("concepts", []):
            if c["id"] == name:
                return {"type": "concept", **c}
    elif page_type == "source":
        for s in catalog.get("sessions", []):
            if s["id"] == name:
                return {"type": "session", **s}
        for r in catalog.get("registry", []):
            if r["id"] == name:
                return {"type": "registry", **r}
    return None

def _chunk_spans(rows) -> tuple:
    """Join chunk texts with a blank line and record each chunk's [start,end)."""
    parts, spans, pos = [], {}, 0
    for cid, text in rows:
        text = text or ""
        spans[cid] = (pos, pos + len(text))
        parts.append(text)
        pos += len(text) + 2  # the "\n\n" join separator
    return "\n\n".join(parts), spans


# Sprint 26: internal filesystem layout never ships to a reader. The indexed corpus
# carries provenance headers naming the authoring checkout ("Locus: dev/DEMOS/...").
# Excerpts are redacted on the way out, preserving the cited span.
_INTERNAL_MARKER_RE = re.compile(
    r"(?:[\w./-]*_DEVELOPMENT[\w./-]*"
    r"|[\w./-]*01_harvester_pipeline[\w./-]*"
    r"|dev/DEMOS/[\w./-]*"
    r"|/home/[\w.-]+/[\w./-]*)"
)


def redact_internal_paths(text: str, start: int, end: int) -> tuple:
    """Replace internal path tokens with a neutral marker, keeping ``[start,end)`` valid."""
    if not text:
        return text, start, end
    parts: list = []
    last = 0
    shift = 0
    for m in _INTERNAL_MARKER_RE.finditer(text):
        token = m.group(0).rstrip(".)")
        if not token:
            continue
        stop = m.start() + len(token)
        parts.append(text[last:m.start()])
        parts.append("[internal]")
        if stop <= start:
            shift += len("[internal]") - (stop - m.start())
        last = stop
    parts.append(text[last:])
    return "".join(parts), max(0, start + shift), max(0, end + shift)


def get_excerpt(key: str) -> dict | None:
    """Reconstruct the indexed text behind a citation key (Sprint 24).

    The chat cites chunks by fusion key (``session:session_04::chunk_003``,
    ``doc:<doc_id>#0000``). This returns the full indexed document text plus the
    character span of the cited chunk so the UI can render the original with the
    chunk highlighted. The text is exactly what the index holds -- verifiable
    against the corpus -- not a re-fetch of the live page.
    """
    key = (key or "").strip()
    if not key or not os.path.exists(GFS_DB_PATH):
        return None
    catalog = _session_catalog()
    try:
        conn = sqlite3.connect(GFS_DB_PATH)
        try:
            conn.row_factory = sqlite3.Row

            if key.startswith("session:"):
                sid, _, tail = key[len("session:"):].partition("::")
                sess = catalog.get(sid)
                if not sess:
                    return None
                url = sess.get("on_demand_url") or f"/api/page?type=source&name={sid}"
                if not tail or tail == "summary":
                    parts = [f"{sid.replace('_', ' ').title()} — {sess.get('title', '')}"]
                    if sess.get("summary"):
                        parts += ["", "Summary: " + sess["summary"]]
                    hl = [h for h in (sess.get("highlights") or []) if h]
                    if hl:
                        parts += ["", "Key highlights:"] + ["- " + h for h in hl]
                    text = "\n".join(parts)
                    return {
                        "kind": "session_summary",
                        "title": f"{sid.replace('_', ' ').title()}: {sess.get('title', '')}",
                        "url": url,
                        "doc_text": text,
                        "start": 0,
                        "end": len(text),
                        "chunk_id": None,
                        "session_id": sid,
                    }
                chunk_id = f"{sid}::{tail}"
                row = conn.execute(
                    "SELECT title FROM immersion_chunks WHERE chunk_id = ?",
                    (chunk_id,),
                ).fetchone()
                if row is None:
                    return None
                rows = [
                    (r["chunk_id"], r["content"])
                    for r in conn.execute(
                        "SELECT chunk_id, content FROM immersion_chunks "
                        "WHERE session_id = ? ORDER BY chunk_id",
                        (sid,),
                    ).fetchall()
                ]
                doc_text, spans = _chunk_spans(rows)
                start, end = spans.get(chunk_id, (0, 0))
                return {
                    "kind": "session",
                    "title": f"{sid.replace('_', ' ').title()}: {row['title'] or ''}",
                    "url": url,
                    "doc_text": doc_text,
                    "start": start,
                    "end": end,
                    "chunk_id": chunk_id,
                    "session_id": sid,
                }

            if key.startswith("doc:"):
                doc_id, _, idx = key[len("doc:"):].partition("#")
                chunk_id = f"{doc_id}#{idx}" if idx else None
                meta = conn.execute(
                    "SELECT title, source_url FROM docs WHERE doc_id = ?",
                    (doc_id,),
                ).fetchone()
                rows = [
                    (r["chunk_id"], r["text"])
                    for r in conn.execute(
                        "SELECT chunk_id, text FROM doc_chunks WHERE doc_id = ? "
                        "ORDER BY chunk_index, chunk_id",
                        (doc_id,),
                    ).fetchall()
                ]
                if not rows:
                    return None
                doc_text, spans = _chunk_spans(rows)
                if chunk_id is None:
                    start, end = 0, len(doc_text)
                else:
                    start, end = spans.get(chunk_id, (0, 0))
                heading = ""
                row = conn.execute(
                    "SELECT heading_path FROM doc_chunks WHERE chunk_id = ?",
                    (chunk_id,),
                ).fetchone() if chunk_id else None
                if row and row["heading_path"]:
                    heading = row["heading_path"]
                return {
                    "kind": "doc",
                    "title": (meta["title"] if meta else None) or heading or doc_id,
                    "url": (meta["source_url"] if meta else None),
                    "doc_text": doc_text,
                    "start": start,
                    "end": end,
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                }
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning("excerpt lookup failed for %r", key, exc_info=True)
        return None
    return None


def build_system_prompt() -> str:
    """Render the shipped prompt template with the catalog's session list.

    The template itself is versioned on disk (``prompts/system_v1.md``); this
    only fills the descriptive catalog placeholders. It no longer encodes
    "if asked about deployment, see Session 03" routing — retrieval decides
    what is relevant, the prompt just says how to cite it.
    """
    catalog = load_catalog()
    sessions_summary = "\n".join([
        f"- {s['id']}: {s['title']} ({s['date']}, speakers: {', '.join(s['speakers'])})"
        for s in catalog.get("sessions", [])
    ])
    concepts_summary = "\n".join([
        f"- {c['id']}: {c['title']}"
        for c in catalog.get("concepts", [])
    ])
    return (
        SYSTEM_PROMPT_TEMPLATE.replace("{{SESSIONS}}", sessions_summary)
        .replace("{{CONCEPTS}}", concepts_summary)
    )


class GFSImmersionHandler(SimpleHTTPRequestHandler):
    """HTTP request handler for GFS Immersion Hub."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SITE_DIR, **kwargs)

    def log_message(self, format, *args):
        if "/api/health" not in str(args[0]):
            super().log_message(format, *args)

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_text(self, text, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(text.encode())

    def _content_length(self) -> int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            return 0

    def _read_body(self) -> dict:
        length = self._content_length()
        if length > MAX_REQUEST_BODY_BYTES:
            raise RequestBodyTooLargeError(
                f"Content-Length {length} exceeds {MAX_REQUEST_BODY_BYTES} bytes"
            )
        if length == 0:
            return {}
        body = self.rfile.read(length)
        if not body:
            return {}
        return json.loads(body)

    def _is_trusted_proxy(self, peer_ip: str) -> bool:
        """True only when the immediate peer is inside an explicit allowlist."""
        if not TRUSTED_PROXY_CIDRS:
            return False
        try:
            peer = ipaddress.ip_address(peer_ip)
        except ValueError:
            return False
        for cidr in TRUSTED_PROXY_CIDRS:
            try:
                if peer in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def _get_client_ip(self) -> str:
        """Resolve client identity (SEC2-04).

        ``X-Forwarded-For`` is attacker-controlled unless the immediate peer is
        a configured trusted proxy. With the default (empty allowlist) the
        header is ignored and ``client_address`` is authoritative; operators
        must opt in via ``GFS_TRUSTED_PROXY_CIDRS``.
        """
        peer_ip = self.client_address[0]
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded and self._is_trusted_proxy(peer_ip):
            candidate = forwarded.split(",")[0].strip()
            if candidate:
                return candidate
        return peer_ip

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── API Routes ──────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            state = preflight_database()
            self._send_json(
                {
                    "status": "degraded" if state["degraded"] else "ok",
                    "service": "gfs-immersion-hub",
                    "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    "degraded": state["degraded"],
                    "degraded_reason": state["reason"],
                    "database": state["database"],
                }
            )
            return

        if path == "/api/queue-status":
            global _active_chats
            self._send_json({
                "active": _active_chats,
                "max_concurrent": MAX_CONCURRENT_CHATS,
                "queue_depth": len(_chat_queue),
            })
            return

        if path == "/api/catalog":
            self._send_json(load_catalog())
            return

        if path == "/api/search":
            qs = parse_qs(parsed.query)
            query = qs.get("q", [""])[0]

            if not query:
                # Preserve the legacy no-query behaviour (catalog listing).
                catalog = load_catalog()
                self._send_json(catalog.get("sessions", []))
                return

            if len(query) > MAX_SEARCH_QUERY_LEN:
                self._send_json(
                    {
                        "error": (
                            f"query exceeds maximum length of {MAX_SEARCH_QUERY_LEN}"
                        ),
                        "code": "query_too_long",
                        "max_query_length": MAX_SEARCH_QUERY_LEN,
                    },
                    422,
                )
                return

            raw_top_k = qs.get("top_k", qs.get("limit", ["10"]))[0]
            try:
                top_k = int(raw_top_k)
            except (TypeError, ValueError):
                self._send_json(
                    {
                        "error": "top_k must be an integer",
                        "code": "invalid_top_k",
                        "max_top_k": MAX_SEARCH_TOP_K,
                    },
                    422,
                )
                return

            if top_k < 1 or top_k > MAX_SEARCH_TOP_K:
                self._send_json(
                    {
                        "error": f"top_k must be between 1 and {MAX_SEARCH_TOP_K}",
                        "code": "top_k_out_of_range",
                        "max_top_k": MAX_SEARCH_TOP_K,
                    },
                    422,
                )
                return

            try:
                self._send_json(search_hybrid(query, top_k=top_k))
            except Exception as exc:
                logger.exception("Hybrid search failed for query %r", query)
                self._send_json(
                    {"error": "search_failed", "code": "search_failed", "detail": str(exc)},
                    500,
                )
            return

        if path == "/api/page":
            qs = parse_qs(parsed.query)
            page_type = qs.get("type", ["source"])[0]
            name = qs.get("name", [""])[0]
            page = get_page_content(page_type, name)
            if page:
                self._send_json(page)
            else:
                self._send_json({"error": "not found"}, 404)
            return

        if path == "/api/excerpt":
            # Sprint 24: reconstruct the indexed text behind a citation key
            # (``session:...::chunk_003`` / ``doc:<id>#0000``) so the UI can show
            # the original with the cited chunk highlighted. Additive endpoint.
            qs = parse_qs(parsed.query)
            key = qs.get("key", [""])[0]
            excerpt = get_excerpt(key)
            if excerpt:
                text, start, end = redact_internal_paths(
                    excerpt.get("doc_text", "") or "",
                    int(excerpt.get("start") or 0),
                    int(excerpt.get("end") or 0),
                )
                excerpt["doc_text"], excerpt["start"], excerpt["end"] = text, start, end
                self._send_json(excerpt)
            else:
                self._send_json({"error": "not found", "code": "excerpt_not_found"}, 404)
            return

        if path == "/api/fact":
            # CMP2-04: expose the deterministic facts lookup implemented in
            # crawl.retrieve.get_fact(); it is deliberately NOT an RRF ranker.
            qs = parse_qs(parsed.query)
            topic = qs.get("topic", [""])[0].strip()
            if not topic:
                self._send_json(
                    {"error": "topic required", "code": "topic_required"}, 400
                )
                return
            if not os.path.exists(GFS_DB_PATH):
                self._send_json({"topic": topic, "facts": [], "total": 0})
                return
            conn = _open_search_connection()
            try:
                facts = retrieval.get_fact(conn, topic)
            except sqlite3.Error:
                logger.exception("Fact lookup failed for topic %r", topic)
                self._send_json(
                    {"error": "fact_lookup_failed", "code": "fact_lookup_failed"},
                    500,
                )
                return
            finally:
                conn.close()
            self._send_json({"topic": topic, "facts": facts, "total": len(facts)})
            return

        if path == "/api/concepts":
            catalog = load_catalog()
            self._send_json(catalog.get("concepts", []))
            return

        if path == "/api/registry":
            catalog = load_catalog()
            self._send_json(catalog.get("registry", []))
            return

        if path == "/api/methodology/kit":
            kit_data = {
                "version": "1.0.0",
                "generated_from": "GFS Immersion EMEA 2026 Sessions 01-04",
                "sections": [
                    {
                        "id": "folder-layout",
                        "title": "GCP-Native 7-Zone Folder Layout",
                        "type": "tree",
                        "description": "Canonical 7-zone directory structure aligned with Google's 4-stage agentic SDLC",
                        "doc_url": "/methodology/structure/folder-layout.md",
                        "zones": [
                            {"id": "01_specs", "name": "Specifications (Plan & Design)", "desc": "PRD, user stories, product vision, NFRs"},
                            {"id": "02_design", "name": "Architecture Design (Write Spec)", "desc": "ADRs, GCP topology, Stitch mockups"},
                            {"id": "03_src", "name": "Application Source (Build)", "desc": "FastAPI/Flask runtime, ADK agents, Antigravity"},
                            {"id": "04_infra", "name": "Infrastructure as Code", "desc": "Cloud Build, Secret Manager, Terraform"},
                            {"id": "05_tests", "name": "Automated Testing & Evals", "desc": "Unit tests, integration tests, ADK CI/CD evals"},
                            {"id": "06_deploy", "name": "Deployment & Runbooks", "desc": "Cloud Run service specs, Firebase Hosting, rollback runbooks"},
                            {"id": "07_docs", "name": "Continuous Documentation", "desc": "OpenAPI specs, architecture diagrams, onboarding"}
                        ]
                    },
                    {
                        "id": "templates",
                        "title": "Document Template Pack",
                        "type": "download-pack",
                        "description": "6 Google-flavored markdown templates with Antigravity-compatible YAML frontmatter",
                        "download_zip": "/methodology/templates/templates.zip",
                        "files": [
                            {"name": "PRD_TEMPLATE.md", "stage": "Plan & Design", "session": "Session 01", "url": "/methodology/templates/PRD_TEMPLATE.md"},
                            {"name": "USER_STORIES_TEMPLATE.md", "stage": "Plan & Design", "session": "Session 01", "url": "/methodology/templates/USER_STORIES_TEMPLATE.md"},
                            {"name": "VISION_DOC_TEMPLATE.md", "stage": "Plan & Design", "session": "Session 04", "url": "/methodology/templates/VISION_DOC_TEMPLATE.md"},
                            {"name": "ADR_TEMPLATE.md", "stage": "Write Spec", "session": "Session 02", "url": "/methodology/templates/ADR_TEMPLATE.md"},
                            {"name": "INFRA_DESIGN_TEMPLATE.md", "stage": "Write Spec", "session": "Session 03", "url": "/methodology/templates/INFRA_DESIGN_TEMPLATE.md"},
                            {"name": "DEPLOYMENT_RUNBOOK_TEMPLATE.md", "stage": "Build & Review", "session": "Session 03", "url": "/methodology/templates/DEPLOYMENT_RUNBOOK_TEMPLATE.md"}
                        ]
                    },
                    {
                        "id": "bootstrapper",
                        "title": "GCP Project Bootstrapper Script",
                        "type": "script",
                        "description": "One-command bash script to scaffold folders, copy templates, configure Secret Manager bindings, and output Antigravity /grill-me prompt",
                        "script_url": "/methodology/scripts/gcp-project-init.sh",
                        "language": "bash"
                    },
                    {
                        "id": "antigravity-prompts",
                        "title": "Antigravity /grill-me Prompt Pack",
                        "type": "prompts",
                        "description": "Pre-vetted architectural review prompts for each Google SDLC stage",
                        "items": [
                            {"name": "Architecture Review", "url": "/methodology/antigravity/grill-me-architecture.md"},
                            {"name": "Security & IAM Audit", "url": "/methodology/antigravity/grill-me-security.md"},
                            {"name": "Deployment Readiness", "url": "/methodology/antigravity/grill-me-deploy-readiness.md"},
                            {"name": "Observability Setup", "url": "/methodology/antigravity/grill-me-observability.md"}
                        ]
                    },
                    {
                        "id": "adk-recipes",
                        "title": "ADK Multi-Agent Topology Starters",
                        "type": "code-blocks",
                        "description": "4 canonical starter code files for Google Agent Development Kit topologies",
                        "language": "python",
                        "items": [
                            {"name": "SequentialAgent (Linear Pipeline)", "url": "/methodology/adk/sequential_agent_starter.py"},
                            {"name": "LoopAgent (Iterative Quality Gate)", "url": "/methodology/adk/loop_agent_starter.py"},
                            {"name": "ParallelAgent (Concurrent Fan-Out/In)", "url": "/methodology/adk/parallel_agent_starter.py"},
                            {"name": "Hierarchical Coordinator (Router)", "url": "/methodology/adk/hierarchical_agent_starter.py"}
                        ]
                    },
                    {
                        "id": "deploy-blueprint",
                        "title": "Cloud Run Serverless Deploy Blueprint",
                        "type": "script",
                        "description": "Production-tested deployment script with free-tier guardrails (scale-to-zero, 512MiB, 1 vCPU)",
                        "script_url": "/methodology/scripts/deploy-cloud-run.sh",
                        "language": "bash"
                    },
                    {
                        "id": "vc-scorecard",
                        "title": "Antler VC Readiness Scorecard",
                        "type": "widget",
                        "description": "Interactive Five Lenses venture readiness assessment (Adam French framework)",
                        "widget_url": "/methodology/vc/scorecard.html"
                    }
                ]
            }
            self._send_json(kit_data)
            return

        # Fall through to static file serving
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        client_ip = self._get_client_ip()

        # SEC2-06: reject oversized bodies before reading or dispatching.
        if (
            path in {"/api/chat", "/api/ground", "/api/feedback"}
            and self._content_length() > MAX_REQUEST_BODY_BYTES
        ):
            self._send_json(
                {
                    "error": "request body too large",
                    "code": "request_too_large",
                    "max_body_bytes": MAX_REQUEST_BODY_BYTES,
                },
                413,
            )
            return

        if path == "/api/chat":
            self._handle_chat(client_ip)
            return

        if path == "/api/ground":
            self._handle_ground()
            return

        if path == "/api/feedback":
            self._handle_feedback(client_ip)
            return

        if path == "/api/fetch":
            self._handle_fetch(client_ip)
            return

        if path == "/api/telemetry":
            # Accept telemetry silently (anonymous, client-side)
            self._send_json({"ok": True})
            return

        # Fall through
        self.send_response(404)
        self.end_headers()

    # ── Chat Handler ────────────────────────────────────────────

    def _handle_chat(self, client_ip: str):
        global _active_chats

        # Rate limit check (bounded TTL/LRU structure, SEC2-05)
        now = time.time()
        if not _check_and_record_chat(client_ip, now):
            self._send_json({"error": "Rate limit reached. Please wait before sending more messages.", "retry_after": CHAT_RATE_WINDOW_SECONDS}, 429)
            return

        # Concurrency gate
        with _chat_lock:
            if _active_chats >= MAX_CONCURRENT_CHATS:
                self._send_json({"error": "Server at capacity. Please try again in a moment.", "queue_position": len(_chat_queue) + 1}, 429)
                return
            _active_chats += 1

        try:
            try:
                body = self._read_body()
            except RequestBodyTooLargeError:
                self._send_json(
                    {
                        "error": "request body too large",
                        "code": "request_too_large",
                        "max_body_bytes": MAX_REQUEST_BODY_BYTES,
                    },
                    413,
                )
                return
            except (ValueError, UnicodeDecodeError):
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            if not isinstance(body, dict):
                self._send_json({"error": "body must be a JSON object"}, 400)
                return
            # Sprint 11 / R5: the server never reads a credential. ``body`` may
            # legally carry provider fields (a client that keeps its key in the
            # request object), but nothing here touches anything but ``message``.
            user_message = body.get("message", "").strip()

            if not user_message or len(user_message) > 2000:
                self._send_json({"error": "Message required (max 2000 chars)"}, 400)
                return

            # Sprint 13 / FR-2: optional ``scope`` field (auto | cohort | docs).
            # An unknown/absent value resolves to "auto"; the value is
            # validated here and never forwarded raw to the retrieval core.
            chat_scope = _resolve_chat_scope(body.get("scope"), user_message)

            # Deterministic metadata path. Identity/listing questions (who
            # presented, which sessions, when) are answerable ONLY from the
            # session catalog: embedding similarity sits far from the chunk
            # text for such phrasing and abstains even though the facts exist.
            if _looks_like_session_fact_query(user_message):
                fact_excerpts = _session_fact_excerpts(limit=4)
                if fact_excerpts:
                    self._send_json(
                        {
                            "excerpts": fact_excerpts,
                            "sources": [
                                {
                                    "title": e["title"],
                                    "url": e["url"],
                                    "fusion_key": e["fusion_key"],
                                }
                                for e in fact_excerpts
                            ],
                            "system_prompt": build_chat_prompt(fact_excerpts),
                            "answerable": True,
                            "abstained": False,
                            "abstention_reason": None,
                            "top_score": 1.0,
                            "min_score": 0.0,
                            "mode": "catalog_fact",
                            "prompt_version": PROMPT_VERSION,
                            "source": "local_kb",
                            "grounded": "local",
                        }
                    )
                    return

            # Sprint 21b: session SUMMARY intent ("what is the takeaway from
            # session 04?"). Abstractive questions about a session sit far from
            # every chunk in vector space (measured 0.85 against a 0.84 gate) even
            # though the answer is the authored summary the card already shows.
            # Answered from the catalog so a correctly-phrased question can never
            # abstain for lack of a nearby vector.
            if _is_session_summary_query(user_message):
                summary_excerpts = _session_summary_excerpts(user_message, limit=8)
                if summary_excerpts:
                    self._send_json(
                        {
                            "excerpts": summary_excerpts,
                            "sources": [
                                {
                                    "title": e["title"],
                                    "url": e["url"],
                                    "fusion_key": e["fusion_key"],
                                }
                                for e in summary_excerpts
                            ],
                            "system_prompt": build_chat_prompt(summary_excerpts),
                            "answerable": True,
                            "abstained": False,
                            "abstention_reason": None,
                            "top_score": 1.0,
                            "min_score": 0.0,
                            "mode": "catalog_summary",
                            "prompt_version": PROMPT_VERSION,
                            "source": "local_kb",
                            "grounded": "local",
                        }
                    )
                    return

            # Sprint 21 / Part B: deterministic relevance gate. General-knowledge
            # questions are answered politely with a redirect instead of being
            # answered from unrelated cohort excerpts. This is a UX heuristic, not
            # a security control, and it fails OPEN: an error here must never
            # prevent a legitimate question from being answered (V-9b).
            try:
                relevance = classify_relevance(user_message)
            except Exception:  # noqa: BLE001 - fail open by design
                logger.warning(
                    "relevance gate failed; proceeding to retrieval", exc_info=True
                )
                relevance = None

            if relevance is not None and relevance.verdict == "out_of_scope":
                self._send_json(
                    {
                        "error": "out_of_scope",
                        "code": "out_of_scope",
                        "relevance": "out_of_scope",
                        "verification": "uncited",
                        "message": redirect_message(relevance.topic_guess),
                        "topic_guess": relevance.topic_guess,
                        # Reuse the existing corpus-verified suggestion helper so the
                        # payload shape matches every other /api/chat response, and every
                        # offered question is proven answerable by the same retrieval gate.
                        # The static list is the fallback when the DB is unavailable.
                        "suggested_questions": (
                            suggest_questions(limit=3)
                            or [
                                {"question": q}
                                for q in relevance_suggested_questions()
                            ]
                        ),
                        "excerpts": [],
                        "sources": [],
                        "answerable": True,
                        "abstained": False,
                        "abstention_reason": None,
                        "top_score": 0.0,
                        "min_score": 0.0,
                        "mode": "out_of_scope",
                        "prompt_version": PROMPT_VERSION,
                        "source": "local_kb",
                        "grounded": "local",
                    }
                )
                return

            # FR-2: retrieve through the SAME core as /api/search, over the
            # full 392-doc + 80-session corpus. A fetch failure is reported as
            # a retrieval error, never papered over with an ungrounded answer.
            try:
                payload = search_hybrid(
                    user_message, top_k=CHAT_TOP_K, scope=chat_scope
                )
            except Exception:
                logger.exception("Chat retrieval failed for %r", user_message)
                self._send_json(
                    {
                        "error": "search_failed",
                        "code": "search_failed",
                        "answerable": False,
                    },
                    500,
                )
                return

            excerpts = _excerpts_from_results(payload["results"])
            sources = _sources_from_results(payload["results"])
            answerable = bool(payload["answerable"])
            # Sprint 21g: a question that NAMES a cohort framework must cover the
            # framework's members, not merely the chunks nearest the wording.
            # "explain the lenses from the 4th session" previously returned the
            # framework intro plus Lens 1 and Lens 4, and the model correctly
            # reported Lenses 2, 3 and 5 as absent -- even though all five lens
            # chunks were indexed. The section supplies the whole set.
            _anchor_excerpts_for_set = _anchor_excerpts(user_message, limit=2)
            if _anchor_excerpts_for_set:
                _seen_keys = {e["fusion_key"] for e in excerpts}
                _missing = [
                    e
                    for e in _anchor_excerpts_for_set
                    if e["fusion_key"] not in _seen_keys
                ]
                if _missing:
                    excerpts = (_missing + excerpts)[:ANCHOR_MERGE_MAX_EXCERPTS]
                    sources = [
                        {
                            "title": e["title"],
                            "url": e["url"],
                            "fusion_key": e["fusion_key"],
                        }
                        for e in excerpts
                    ]
                    answerable = True
            # FR-3 groundwork: every citation must resolve to a URL. A result
            # set with no resolvable URL cannot ground an answer.
            answerable = bool(answerable and sources)

            # Sprint 17: build the response payload before considering web ground.
            if not answerable:
                # FR-2/D3: no evidence window means no prompt and no generation.
                # A model asked to answer from weak retrieval will hallucinate.
                abstention_reason = (
                    "no_resolvable_citation"
                    if payload["answerable"]
                    else payload["abstention_reason"]
                )
                # Sprint 13 / FR-6b: display-only nearest chunks, each tagged
                # with why it was refused. Kept in its OWN top-level key -- it
                # is never merged into excerpts/sources and its text never
                # reaches system_prompt (which stays None).
                # Sprint 21b: before abstaining, try an exact framework-anchor
                # match. The table already maps cohort framework names to their
                # defining chunk, it was simply never consulted, so identical
                # questions succeeded or failed purely on embedding distance.
                # A nonsense question matches no anchor and still abstains below.
                anchor_excerpts = _anchor_excerpts(user_message, limit=2)
                if anchor_excerpts:
                    self._send_json(
                        {
                            "excerpts": anchor_excerpts,
                            "sources": [
                                {
                                    "title": e["title"],
                                    "url": e["url"],
                                    "fusion_key": e["fusion_key"],
                                }
                                for e in anchor_excerpts
                            ],
                            "system_prompt": build_chat_prompt(anchor_excerpts),
                            "answerable": True,
                            "abstained": False,
                            "abstention_reason": None,
                            "top_score": payload["top_score"],
                            "min_score": payload["min_score"],
                            "mode": "anchor_lookup",
                            "prompt_version": PROMPT_VERSION,
                            "source": "local_kb",
                            "grounded": "local",
                        }
                    )
                    return

                closest_material = _closest_material_from_results(
                    payload["results"], abstention_reason
                )
                # Sprint 13 / FR-6c: only questions that independently clear
                # the SAME gate as a real query; [] is a valid result.
                suggested_questions = suggest_questions(
                    query=user_message, limit=3
                )
                response = {
                    "excerpts": [],
                    "sources": [],
                    "system_prompt": None,
                    "answerable": False,
                    "abstained": True,
                    "abstention_reason": abstention_reason,
                    "closest_material": closest_material,
                    "suggested_questions": suggested_questions,
                    "top_score": payload["top_score"],
                    "min_score": payload["min_score"],
                    "mode": payload["mode"],
                    "prompt_version": PROMPT_VERSION,
                    "source": "local_kb",
                    "grounded": "abstained",
                }
            else:
                # Sprint 11 / FR-1: retrieval + prompt only. There is no outbound
                # call on this path — generation happens in the client, with the
                # user's own credential, and comes back through /api/ground.
                response = {
                    "excerpts": excerpts,
                    "sources": sources,
                    "system_prompt": build_chat_prompt(excerpts),
                    "answerable": True,
                    "abstained": False,
                    "abstention_reason": None,
                    "prompt_version": PROMPT_VERSION,
                    "top_score": payload["top_score"],
                    "min_score": payload["min_score"],
                    "mode": payload["mode"],
                    "source": "local_kb",
                    "grounded": "local",
                }

            # Sprint 17: web-ground fallback (search + HTTPS fetch only; no LLM).
            # Activated when local knowledge abstains OR when the caller explicitly
            # requests live-web grounding.
            if (
                WEB_GROUND_ENABLED
                and _check_and_record_web_ground(client_ip, now)
                and (not answerable or body.get("live_web") is True)
            ):
                # V-16: truncate before egress; do not log the raw query text.
                cse_query = _truncate_for_cse(user_message)
                try:
                    web_cfg = {
                        "search_key": os.environ.get("GFS_SEARCH_API_KEY"),
                        "engine_id": os.environ.get("GFS_SEARCH_ENGINE_ID"),
                        "allowlist": WEB_GROUND_DOMAIN_ALLOWLIST,
                        "max_results": WEB_GROUND_MAX_RESULTS,
                        "max_bytes_per_page": WEB_GROUND_MAX_BYTES_PER_PAGE,
                        "fetch_timeout_seconds": WEB_GROUND_FETCH_TIMEOUT_SECONDS,
                        "fresh": query_requires_freshness(user_message),
                    }
                    web = retrieval.web_ground_fallback(cse_query, web_cfg)
                    if web.get("ok"):
                        # web_ground_fallback returns the prompt with the untrusted-content boundary.
                        response = {
                            "excerpts": web["excerpts"],
                            "sources": web["sources"],
                            "system_prompt": web["system_prompt"],
                            "answerable": True,
                            "abstained": False,
                            "abstention_reason": None,
                            "prompt_version": PROMPT_VERSION,
                            "top_score": 0.0,
                            "min_score": 0.0,
                            "mode": "live_web",
                            "source": "live_web",
                            "grounded": "web",
                            "web_meta": web.get("web_meta", {}),
                        }
                    else:
                        # Preserve honest abstain; update reason if budget-related.
                        if web.get("reason") == "live_web_budget_exhausted":
                            response["abstention_reason"] = "live_web_budget_exhausted"
                            response["grounded"] = "abstained"
                except Exception:
                    logger.exception("Web-ground fallback failed")
                    # Fail closed to the existing honest abstain.

            self._send_json(response)

        except Exception as e:
            self._send_json({"error": f"Chat error: {str(e)}"}, 500)
        finally:
            with _chat_lock:
                _active_chats -= 1

    # ── Grounding Handler (Sprint 11 / FR-2) ────────────────────

    def _handle_ground(self):
        """Validate a client-generated answer against the citations it cites.

        The client generates with its own key (R5) and posts the result here so
        grounding is enforced by ONE implementation — the pure
        ``validate_grounding`` above — instead of a drifting JS copy.

        Request: ``{"answer": str, "citations": [{"fusion_key", "url"}, ...]}``.
        Nothing else in the body is read. A credential-shaped field in the body
        is never inspected, stored or logged, and the response never echoes the
        request.
        """
        try:
            try:
                body = self._read_body()
            except RequestBodyTooLargeError:
                self._send_json(
                    {
                        "error": "request body too large",
                        "code": "request_too_large",
                        "max_body_bytes": MAX_REQUEST_BODY_BYTES,
                    },
                    413,
                )
                return
            except (ValueError, UnicodeDecodeError):
                self._send_json({"error": "invalid JSON body"}, 400)
                return

            if not isinstance(body, dict):
                self._send_json({"error": "body must be a JSON object"}, 400)
                return

            answer = body.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                self._send_json(
                    {"error": "answer required (non-empty string)"}, 400
                )
                return

            raw_citations = body.get("citations", [])
            if not isinstance(raw_citations, list):
                self._send_json({"error": "citations must be a list"}, 400)
                return

            citations: list = []
            for entry in raw_citations:
                if not isinstance(entry, dict):
                    self._send_json(
                        {"error": "each citation must be an object"}, 400
                    )
                    return
                fusion_key = entry.get("fusion_key")
                url = entry.get("url")
                if fusion_key is not None and not isinstance(fusion_key, str):
                    self._send_json(
                        {"error": "citation.fusion_key must be a string"}, 400
                    )
                    return
                if url is not None and not isinstance(url, str):
                    self._send_json(
                        {"error": "citation.url must be a string"}, 400
                    )
                    return
                citations.append({"fusion_key": fusion_key, "url": url})

            # The single, unmodified validator. No second implementation.
            self._send_json(validate_grounding(answer, citations))
        except Exception:
            logger.exception("Grounding validation failed")
            self._send_json({"error": "grounding_failed"}, 500)

    # ── Feedback Handler ─────────────────────────────────────────

    def _handle_fetch(self, client_ip: str):
        """POST /api/fetch — fetch ONE allowlisted page and return its plain text.

        Exists for the BYO live-web flow: the browser runs the search with the
        user's own Google key (which never touches this origin), then asks us to
        retrieve the body of a result page, because cloud.google.com and friends
        send no CORS headers and the browser cannot read them directly.

        What this endpoint never sees: the user's search credential or their
        question. It receives only a URL from the server-side allowlist. Reuses
        the Sprint 17 SSRF-guarded fetcher and the same per-IP/daily budget.
        """
        if not PAGE_FETCH_ENABLED:
            self._send_json(
                {
                    "ok": False,
                    "code": "fetch_disabled",
                    "reason": "Page fetch is disabled on this deployment.",
                },
                503,
            )
            return

        try:
            length = self._content_length()
            if length > MAX_REQUEST_BODY_BYTES:
                self._send_json({"error": "request body too large"}, 413)
                return
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._send_json({"error": "invalid json", "code": "bad_request"}, 400)
            return

        url = (payload.get("url") or "").strip()
        if not url:
            self._send_json({"error": "url required", "code": "url_required"}, 400)
            return

        now = time.monotonic()
        if not _check_and_record_web_ground(client_ip, now):
            self._send_json(
                {
                    "ok": False,
                    "code": "budget_exhausted",
                    "reason": "Live-web budget exhausted for this hour or today.",
                },
                429,
            )
            return

        ok, text_or_reason, final_url = retrieval.fetch_page_text(
            url,
            WEB_GROUND_DOMAIN_ALLOWLIST,
            max_bytes=WEB_GROUND_MAX_BYTES_PER_PAGE,
            timeout_seconds=WEB_GROUND_FETCH_TIMEOUT_SECONDS,
        )
        if not ok:
            self._send_json(
                {
                    "ok": False,
                    "code": "fetch_failed",
                    "reason": text_or_reason,
                },
                422,
            )
            return

        snippets = retrieval._extract_snippets(text_or_reason, max_chars=400, max_total=6000)
        self._send_json(
            {
                "ok": True,
                "url": final_url or url,
                "snippets": snippets[:12],
                "attribution": {"source_url": final_url or url},
            }
        )

    def _handle_feedback(self, client_ip: str):
        try:
            try:
                body = self._read_body()
            except RequestBodyTooLargeError:
                self._send_json(
                    {
                        "error": "request body too large",
                        "code": "request_too_large",
                        "max_body_bytes": MAX_REQUEST_BODY_BYTES,
                    },
                    413,
                )
                return
            feedback_type = body.get("type", "general")
            message = body.get("message", "").strip()
            if not message:
                self._send_json({"error": "Message required"}, 400)
                return

            # Log feedback to file
            fb_dir = os.path.join(HUB_DIR, "data", "feedback")
            os.makedirs(fb_dir, exist_ok=True)
            fb_file = os.path.join(fb_dir, f"{datetime.date.today().isoformat()}.jsonl")
            with open(fb_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                    "type": feedback_type,
                    "message": message,
                    "ip_hash": hashlib.sha256(client_ip.encode()).hexdigest()[:12],
                }) + "\n")

            self._send_json({"ok": True, "message": "Thank you for your feedback!"})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)


def main():
    parser = argparse.ArgumentParser(description="GFS Immersion 2026 Knowledge Hub Server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    print(f"🚀 GFS Immersion Hub starting on http://{args.host}:{args.port}")
    print(f"   Database: {GFS_DB_PATH} ({'✓ found' if os.path.exists(GFS_DB_PATH) else '✗ missing'})")
    catalog = load_catalog()
    print(f"   Catalog: {len(catalog.get('sessions', []))} sessions, {len(catalog.get('concepts', []))} concepts")

    state = preflight_database()
    if state["degraded"]:
        print(f"   ⚠️  DEGRADED: {state['reason']}")
    else:
        db = state["database"]
        print(
            "   Preflight: "
            f"docs={db.get('docs_count')} "
            f"doc_chunks={db.get('doc_chunks_count')} "
            f"vec_chunks={'present' if db.get('vec_chunks') else 'MISSING'}"
        )

    server = ThreadingHTTPServer((args.host, args.port), GFSImmersionHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()