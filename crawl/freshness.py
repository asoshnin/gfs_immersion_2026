"""Freshness and volatility helpers for Sprint 09 (Retrieval Core).

Conforms to ``02_design.md`` §3.2–3.6:
- Ordered volatility classification (first match wins; never ``None``).
- Operational TTL table used by ``is_stale``.
- Token-boundary freshness query classifier.
- Age computation that never fabricates ``0``.
- URL canonicalization for stable cache keys.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Single source of truth for the freshness token list (FR-4).
AUTHORITATIVE_TOKENS = (
    "latest",
    "newest",
    "pricing",
    "price",
    "release notes",
    "changelog",
    "version",
    "what's new",
    "updated",
)

TTL_DAYS = {"authored": None, "stable": 30, "evolving": 7, "volatile": 1}

_VOLATILE_RELEASE_MARKERS = (
    "release-notes",
    "/changelog",
    "whats-new",
    "/what-s-new",
)
_EVOLVING_MARKERS = ("/apis/", "/reference/", "/quotas", "/limits")
_TRACKING_EXACT = {"gclid", "fbclid"}


def classify_volatility(
    source_url: str, tier: Optional[str], corpus: str = "doc"
) -> str:
    """Classify a source into authored/stable/evolving/volatile.

    Rules are applied top-down; the first match wins. The default is
    ``"stable"`` so this function never returns ``None``.
    """
    url = (source_url or "").casefold()

    if "/pricing" in url:
        return "volatile"
    if any(marker in url for marker in _VOLATILE_RELEASE_MARKERS):
        return "volatile"
    if any(marker in url for marker in _EVOLVING_MARKERS):
        return "evolving"
    if corpus == "session":
        return "authored"
    if tier in {"A", "B", "C"}:
        return "stable"
    return "stable"


def query_requires_freshness(query: str) -> bool:
    """Return True when the query contains an authoritative freshness token.

    Matching is token-boundary based (``price`` does not match ``priceless``,
    ``version`` does not match ``conversion``), case-folded, and whitespace
    collapsed.
    """
    if not query:
        return False

    normalized = " ".join(
        query.casefold().replace("\u2019", "'").split()
    )
    if not normalized:
        return False

    for token in AUTHORITATIVE_TOKENS:
        pattern = r"(?<!\w)" + re.escape(token) + r"(?!\w)"
        if re.search(pattern, normalized):
            return True
    return False


def age_days(fetched_at: Optional[str], now=None) -> Optional[int]:
    """Return whole days between ``fetched_at`` and ``now``.

    Returns ``None`` for missing or unparseable input; never fabricates ``0``.
    """
    if not fetched_at or not isinstance(fetched_at, str):
        return None

    raw = fetched_at.strip()
    if not raw:
        return None

    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"

    try:
        fetched_dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None

    if fetched_dt.tzinfo is None:
        fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)

    if now is None:
        now_dt = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        now_dt = now
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
    else:
        return None

    return (now_dt - fetched_dt).days


def is_stale(age_days: Optional[int], volatility: Optional[str]) -> Optional[bool]:
    """Evaluate staleness against the operational TTL table.

    Unknown age yields unknown staleness (``None``). Classes with no TTL
    (``authored`` / unknown) are never stale.
    """
    ttl = TTL_DAYS.get(volatility or "", None)
    if age_days is None:
        return None
    if ttl is None:
        return False
    return age_days > ttl


def canonicalize_url(raw: str) -> str:
    """Canonicalize a URL for stable deduplication/cache keys."""
    if not raw or not isinstance(raw, str):
        return ""

    raw = raw.strip()
    if not raw:
        return ""

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    path = parts.path
    if path.endswith("/"):
        path = path[:-1]

    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_EXACT:
            continue
        kept.append((key, value))
    kept.sort(key=lambda item: (item[0], item[1]))

    return urlunsplit((scheme, netloc, path, urlencode(kept, doseq=True), ""))


def backfill_volatility(conn) -> int:
    """Backfill ``docs.volatility``; return the number of rows changed.

    Idempotent: a second run over unchanged data updates ``0`` rows.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT doc_id, source_url, tier, volatility FROM docs;"
    )
    rows = cursor.fetchall()

    updates = []
    for row in rows:
        doc_id, source_url, tier, current = row[0], row[1], row[2], row[3]
        classified = classify_volatility(source_url, tier, corpus="doc")
        if current != classified:
            updates.append((classified, doc_id))

    if updates:
        cursor.executemany(
            "UPDATE docs SET volatility = ? WHERE doc_id = ?;",
            updates,
        )
    conn.commit()
    return len(updates)
