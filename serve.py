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
# serve.py is published from 02_public_hub/, while the retrieval core
# (crawl.retrieve / crawl.freshness) lives in the private harvester tree.
# The deployable unit is intentionally non-self-sufficient — the SQLite index
# is staged from the private data/ tree too. We therefore add two explicit,
# documented import roots:
#   1. <repo>/01_harvester_pipeline  (local development layout)
#   2. <HUB_DIR>                     (container layout: /app with /app/crawl)
# This is a deliberate boundary shim, not dynamic/recursive path discovery.
_REPO_ROOT = os.path.dirname(HUB_DIR)
for _import_root in (os.path.join(_REPO_ROOT, "01_harvester_pipeline"), HUB_DIR):
    if _import_root not in sys.path:
        sys.path.insert(0, _import_root)

from crawl import retrieve as retrieval  # noqa: E402
from crawl.freshness import query_requires_freshness  # noqa: E402

MAX_SEARCH_TOP_K = retrieval.MAX_TOP_K
MAX_SEARCH_QUERY_LEN = retrieval.MAX_QUERY_LEN

# Lazy embedder state (design §11 guardrail 2): the model is loaded on the
# first dense search, never at container boot.
_EMBEDDER = None
_EMBEDDER_UNAVAILABLE = False
_EMBEDDER_LOCK = threading.Lock()

# ── Rate Limiting ──────────────────────────────────────────────────
# SEC2-05: bounded LRU + TTL structure. Entries are pruned by age and the
# map is capped, so spoofed/rotating identities cannot grow process memory
# without limit over the container lifetime.
CHAT_RATE_LIMIT: "OrderedDict[str, list[float]]" = OrderedDict()
MAX_CHAT_PER_IP_PER_HOUR = 20
CHAT_RATE_WINDOW_SECONDS = 3600
CHAT_RATE_LIMIT_MAX_ENTRIES = 4096
_rate_limit_lock = threading.Lock()

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


def search_hybrid(query: str, top_k: int = 10) -> dict:
    """Run the shared retrieval core and return the search payload.

    Sprint 10 / FR-1 adds the abstention trio (``answerable``, ``top_score``,
    ``min_score``) plus the supporting evidence (``evidence_documents``,
    ``top_dense_distance``, ``abstention_reason``) so a client can refuse to
    answer instead of trusting ``total`` (which only echoes ``top_k``).
    Every legacy key is preserved.
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
        citations, mode, answerability = retrieval.hybrid_search_ranked(
            conn, query, top_k=top_k, embedder=_get_embedder()
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
    return payload


# NOTE (Sprint 10 / FR-2): the legacy ``search_fts5`` session-only helper was
# removed here. It was a second query builder living outside the retrieval
# core and it made /api/chat blind to the 392-document corpus. Both API
# surfaces now go through ``retrieval.hybrid_search_ranked``.


def _session_catalog() -> dict:
    """Map ``session_id`` -> catalog session row, for URL resolution."""
    return {s["id"]: s for s in load_catalog().get("sessions", [])}


def _session_public_url(session_id: str | None, sessions: dict) -> str | None:
    """Resolve a public URL for a session chunk.

    Session chunks have ``source_url = NULL`` because they are authored
    markdown, not crawled pages. Prefer the catalog's on-demand recording URL;
    otherwise point at the portal's own session page, which always resolves.
    FR-3 requires a resolvable URL per citation, so this never returns a
    non-URL placeholder.
    """
    if not session_id:
        return None
    session = sessions.get(session_id)
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


def _excerpt_snippet(row: dict) -> str:
    """The text the client puts in front of the model for this citation."""
    if row["result_type"] == "session":
        return (row.get("content") or row.get("snippet") or "")[:500]
    return row.get("snippet") or ""


def _excerpts_from_results(results: list) -> list:
    """Build the evidence window ``[{fusion_key, title, url, snippet}]``.

    Sprint 11 / FR-1: this list *is* the chat answer now — the server no longer
    generates. One excerpt per distinct URL, in ranking order, so the numbered
    marker the model is told to use (``[1]``, ``[2]``, ...) means exactly the
    same thing in ``excerpts`` as in ``sources``. Rows without a resolvable URL
    are dropped: they could never be validated as a citation.
    """
    sessions = _session_catalog()
    excerpts: list = []
    seen_urls: set = set()
    for row in results:
        url = _result_url(row, sessions)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        excerpts.append(
            {
                "fusion_key": row.get("fusion_key"),
                "title": row.get("title"),
                "url": url,
                "snippet": _excerpt_snippet(row),
            }
        )
    return excerpts


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
    return build_system_prompt() + (
        f"\n\n{rag_context}"
        "\nGround every claim in the excerpts above. Cite each claim with "
        "the excerpt's fusion_key in square brackets (e.g. "
        "[doc:655f3836#0000]) or its excerpt number (e.g. [1]). Never "
        "invent a citation or a URL. If the excerpts do not support an "
        "answer, say so instead of guessing."
    )


ABSTENTION_MESSAGE = (
    "I don't have enough grounded material in this knowledge base to answer "
    "that reliably, so I won't guess. The hub covers the four Immersion "
    "sessions and the Google Cloud documentation it was built from — try "
    "asking about Cloud Run, ADK/SequentialAgent, Firestore, BigQuery, "
    "Secret Manager, or a specific session topic."
)

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
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def _normalize_url(url: str) -> str:
    """Compare citations on a stable shape: no scheme, no trailing slash."""
    value = (url or "").strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("#", 1)[0].split("?", 1)[0]
    return value.rstrip("/")


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


def validate_grounding(answer_text: str, retrieved_citations: list) -> dict:
    """Strip fabricated citations from a generated answer (FR-3).

    ``retrieved_citations`` is the ``sources`` list built for this response:
    ``[{"title", "url", "fusion_key"}, ...]``, in citation-ordinal order.

    Returns ``{"answer", "ok", "checked", "dropped", "restored", ...}``.
    ``ok`` is False when the surviving answer carries no supported citation, in
    which case ``answer`` is the abstention text.

    Pure function: no DB, no network, no model. That is the point — it is the
    unit under test in ``tests/test_citation_validation.py``.
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
            checked += len(keys) + len(ordinals)

            bad = [k for k in keys if k not in allowed_keys]
            bad += [f"[{n}]" for n in ordinals if not 1 <= int(n) <= ordinal_count]
            bad += [u for u in urls if _normalize_url(u) not in allowed_urls]

            if bad:
                dropped_sentences += 1
                fabricated.extend(bad)
                continue
            if keys or ordinals:
                valid_markers += len(keys) + len(ordinals)
            if sentence.strip():
                kept_sentences += 1
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

    # Degrade to abstention when nothing supported survives.
    if valid_markers == 0:
        return {
            "answer": ABSTENTION_MESSAGE,
            "ok": False,
            "checked": checked,
            "dropped": dropped_sentences,
            "valid_markers": 0,
            "fabricated": fabricated,
            "degraded": True,
            "reason": (
                "all_citations_fabricated"
                if dropped_sentences and checked
                else "no_citation_markers"
            ),
        }

    return {
        "answer": cleaned,
        "ok": True,
        "checked": checked,
        "dropped": dropped_sentences,
        "valid_markers": valid_markers,
        "fabricated": fabricated,
        "degraded": False,
        "reason": "grounded" if not dropped_sentences else "partial",
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

            # FR-2: retrieve through the SAME core as /api/search, over the
            # full 392-doc + 80-session corpus. A fetch failure is reported as
            # a retrieval error, never papered over with an ungrounded answer.
            try:
                payload = search_hybrid(user_message, top_k=CHAT_TOP_K)
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
            # FR-3 groundwork: every citation must resolve to a URL. A result
            # set with no resolvable URL cannot ground an answer.
            answerable = bool(payload["answerable"] and sources)

            if not answerable:
                # FR-2/D3: no evidence window means no prompt and no generation.
                # A model asked to answer from weak retrieval will hallucinate.
                self._send_json(
                    {
                        "excerpts": [],
                        "sources": [],
                        "system_prompt": None,
                        "answerable": False,
                        "abstained": True,
                        "abstention_reason": (
                            "no_resolvable_citation"
                            if payload["answerable"]
                            else payload["abstention_reason"]
                        ),
                        "top_score": payload["top_score"],
                        "min_score": payload["min_score"],
                        "mode": payload["mode"],
                        "prompt_version": PROMPT_VERSION,
                    }
                )
                return

            # Sprint 11 / FR-1: retrieval + prompt only. There is no outbound
            # call on this path — generation happens in the client, with the
            # user's own credential, and comes back through /api/ground.
            self._send_json(
                {
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
                }
            )

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