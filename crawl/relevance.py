"""Deterministic relevance gate for the GFS Immersion hub (Sprint 21, Part B).

Decides whether an incoming chat question is about the cohort material at all, so the hub
can politely redirect general-knowledge questions instead of generating an ungrounded
answer about them.

Design constraints (see ``02_design.md`` section 2):

* **Pure.** No I/O, no network, no database, no model calls. Every lexicon is a
  module-level constant built once at import time.
* **No YAML at runtime.** ``GCP_TERMS`` is a static hand-maintained copy of the product
  names from ``seed_registry.yaml``. PyYAML is a build-plane dependency and is not present
  in the Cloud Run image.
* **Fail open.** An unrecognised question is treated as in-scope. The gate is a UX
  heuristic, not a security control; the per-IP rate limit is the abuse boundary.
* **Never raises.** Any internal error returns a low-confidence in-scope verdict.

``topic_guess`` is drawn from a closed static enum and is never a substring of user input:
the value is interpolated into a message that reaches an ``innerHTML`` sink on the client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "RelevanceVerdict",
    "classify_relevance",
    "redirect_message",
    "suggested_questions",
    "TOPIC_LABELS",
    "COHORT_TERMS",
    "GCP_TERMS",
    "VENTURE_TERMS",
    "OUT_OF_SCOPE_TERMS",
]

MAX_INPUT_CHARS = 2000

# Function words carrying no topical signal. Removed before matching so that a query like
# "how do I deploy" does not match on "how"/"do"/"i".
STOPWORDS = frozenset(
    {
        "the", "and", "how", "do", "does", "did", "i", "my", "me", "we", "our", "is",
        "are", "was", "were", "be", "been", "being", "what", "which", "who", "whom",
        "whose", "when", "where", "why", "a", "an", "of", "to", "in", "on", "at",
        "for", "with", "about", "from", "by", "as", "that", "this", "these", "those",
        "it", "its", "can", "could", "should", "would", "will", "shall", "may",
        "might", "must", "have", "has", "had", "there", "here", "you", "your", "if",
        "or", "but", "not", "no", "yes", "so", "than", "then", "them", "they",
    }
)

# --------------------------------------------------------------------------------------
# In-domain lexicons
# --------------------------------------------------------------------------------------

# The four session topics plus cohort/speaker/framework surface forms.
COHORT_TERMS = frozenset(
    {
        # cohort + programme vocabulary
        "cohort", "immersion", "session", "sessions", "antler", "gfs", "programme",
        "program", "fellowship", "speaker", "speakers", "presenter", "presentation",
        "curriculum", "workshop", "demo day", "takeaway", "takeaways", "summary",
        # the four session topics
        "go-to-market", "go to market", "gtm", "product", "engineering",
        "fundraising", "venture", "venture capital", "ai infrastructure",
        "google cloud",
        # named frameworks used in the sessions
        "five lenses", "ladder of proof", "sean ellis", "egm", "product-market fit",
        "product market fit", "pmf", "go-to-market strategy",
    }
)

# Static copy of the ``product:`` values from seed_registry.yaml (tiers A/B/C),
# lowercased and de-duplicated. Hand-maintained on purpose: the runtime image has no YAML.
GCP_TERMS = frozenset(
    {
        "adk", "agent development kit", "agent builder", "agent builder rag",
        "adk rag tool", "antigravity", "antigravity docs", "architecture center",
        "artifact registry", "bigquery", "cloud build", "cloud functions",
        "cloud logging", "cloud monitoring", "cloud run", "cloud sql",
        "cloud storage", "firebase", "firebase hosting", "firestore", "gke",
        "gemini", "gemini api", "gemini api grounding", "google agents cli",
        "google ai studio", "iam", "mcp", "mcp specification", "pub/sub", "pubsub",
        "secret manager", "sensitive data protection", "vertex ai",
        "vertex ai rag engine", "vertex ai search grounding",
        "well-architected framework", "concurrency", "codelab", "codelabs",
        "sdk", "api", "rag", "embedding", "embeddings", "serverless", "container",
        "containers", "gcp", "cloud",
    }
)

# Venture / startup-finance vocabulary.
VENTURE_TERMS = frozenset(
    {
        "venture", "vc", "pre-seed", "preseed", "seed", "series", "round",
        "valuation", "cap table", "dilution", "term sheet", "runway", "burn",
        "unit economics", "gross margin", "cac", "ltv", "arr", "mrr", "tam", "sam",
        "som", "defensibility", "moat", "traction", "growth", "startup", "startups",
        "founder", "founders", "investor", "investors", "pitch", "deck",
        "market size", "business model", "revenue", "pricing", "metrics",
    }
)

# Russian surface forms for the highest-frequency concepts.
COHORT_TERMS_RU = frozenset(
    {"сессия", "сессии", "сессий", "когорта", "когорты", "презентация", "презентации",
     "спикер", "спикеры", "программа", "итоги", "выводы", "резюме", "воркшоп"}
)
GCP_TERMS_RU = frozenset(
    {"облако", "облачные", "облака", "сервис", "сервисы", "агент", "агента", "агенты",
     "деплой", "развернуть", "задеплоить", "контейнер", "контейнеры", "хранилище",
     "база", "данные", "модель", "модели", "квоты", "квот", "стоимость", "оптимизация"}
)
VENTURE_TERMS_RU = frozenset(
    {"венчур", "венчурный", "раунд", "раунда", "оценка", "оценки", "инвестор", "инвесторы",
     "метрики", "метрик", "рентабельность", "выручка", "стартап", "стартапы",
     "защитный", "ров", "рва", "маржа"}
)

# --------------------------------------------------------------------------------------
# Out-of-scope lexicon (frozen; coverage-verified against the EN frozen fixture)
# --------------------------------------------------------------------------------------
# Size-bounded and enumerated on purpose. Any addition requires a matching row in
# tests/fixtures/relevance_corpus_en_outdomain.yaml -- the list and the fixture move
# together, then freeze. Growing this to chase arbitrary general knowledge is rejected:
# it rots and produces false rejections of legitimate cohort questions.

OUT_OF_SCOPE_TERMS = frozenset(
    {
        "passport", "visa", "tax return", "parking permit", "mental health", "hotline",
        "capital", "portugal", "country", "weather", "forecast", "recipe", "bake",
        "sourdough", "football", "sports", "premier league", "movie", "showtimes",
        "train", "schedule", "flights", "plant", "care", "pet", "adoption", "yoga",
        "laptop", "local news", "headlines", "bitcoin", "price today",
    }
)

# Maps an out-of-scope term onto one of the closed topic labels below.
_TERM_TO_TOPIC = {
    "passport": "travel_documents",
    "visa": "travel_documents",
    "flights": "travel_documents",
    "tax return": "taxes",
    "parking permit": "general_knowledge",
    "mental health": "general_knowledge",
    "hotline": "general_knowledge",
    "capital": "general_knowledge",
    "portugal": "general_knowledge",
    "country": "general_knowledge",
    "weather": "weather",
    "forecast": "weather",
    "recipe": "recipes",
    "bake": "recipes",
    "sourdough": "recipes",
    "football": "sports",
    "sports": "sports",
    "premier league": "sports",
    "movie": "general_knowledge",
    "showtimes": "general_knowledge",
    "train": "general_knowledge",
    "schedule": "general_knowledge",
    "plant": "general_knowledge",
    "care": "general_knowledge",
    "pet": "general_knowledge",
    "adoption": "general_knowledge",
    "yoga": "general_knowledge",
    "laptop": "general_knowledge",
    "local news": "general_knowledge",
    "headlines": "general_knowledge",
    "bitcoin": "cryptocurrency",
    "price today": "cryptocurrency",
}

# Closed static enum. NEVER derive a label from user text.
TOPIC_LABELS = frozenset(
    {
        "travel_documents", "weather", "general_knowledge", "recipes", "sports",
        "taxes", "cryptocurrency", "unknown",
    }
)

_UNKNOWN_TOPIC_PHRASE = "a topic outside the cohort material"

_IN_SCOPE_QUESTIONS = (
    "What GCP services are covered in the cohort?",
    "How do I deploy an ADK agent to Cloud Run?",
    "What are the Five Lenses?",
)

_TOKEN_RE = re.compile(r"[\w']+")

# Pre-split multi-word phrases so matching stays O(n + T): each phrase becomes a tuple of
# tokens, and consecutive token runs are checked against this set.
_ALL_PHRASES = frozenset(
    p for p in (
        set(COHORT_TERMS) | set(GCP_TERMS) | set(VENTURE_TERMS) | set(OUT_OF_SCOPE_TERMS)
    )
    if " " in p
)


@dataclass(frozen=True)
class RelevanceVerdict:
    """Outcome of the relevance gate.

    ``topic_guess`` is a value from :data:`TOPIC_LABELS` or ``None``.
    """

    verdict: str
    confidence: float
    matched_terms: list[str] = field(default_factory=list)
    topic_guess: str | None = None


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, capped and stopword-filtered."""
    if not isinstance(text, str):
        return []
    clipped = text[:MAX_INPUT_CHARS].lower()
    return [t for t in _TOKEN_RE.findall(clipped) if t not in STOPWORDS]


def _match(lexicon: frozenset, tokens: list[str], single_only_min_len: int = 3) -> set:
    """Intersect tokens with a lexicon.

    Single tokens shorter than ``single_only_min_len`` are skipped so that short words do
    not match by accident (``hi`` inside ``which``, ``ant`` inside ``want``). Multi-word
    phrases are matched as consecutive token runs, which is why ``cloud run`` still works.
    """
    matched: set = set()
    token_set = set(tokens)
    for term in lexicon:
        if " " in term:
            continue
        if len(term) >= single_only_min_len and term in token_set:
            matched.add(term)
    for phrase in _ALL_PHRASES:
        if phrase not in lexicon:
            continue
        parts = tuple(phrase.split())
        n = len(parts)
        for i in range(len(tokens) - n + 1):
            if tuple(tokens[i : i + n]) == parts:
                matched.add(phrase)
                break
    return matched


def _in_domain_matches(tokens: list[str]) -> set:
    matched: set = set()
    for lex in (COHORT_TERMS, GCP_TERMS, VENTURE_TERMS,
                COHORT_TERMS_RU, GCP_TERMS_RU, VENTURE_TERMS_RU):
        matched |= _match(lex, tokens)
    return matched


def _out_of_scope_matches(tokens: list[str]) -> set:
    return _match(OUT_OF_SCOPE_TERMS, tokens)


def _topic_for(out_terms: set) -> str:
    """Pick a closed-enum label for the strongest out-of-scope family present."""
    if not out_terms:
        return "unknown"
    # Prefer the family of the first term in the frozen enumeration order for determinism.
    for term in sorted(OUT_OF_SCOPE_TERMS):
        if term in out_terms:
            label = _TERM_TO_TOPIC.get(term, "unknown")
            return label if label in TOPIC_LABELS else "unknown"
    return "unknown"


def classify_relevance(text: str) -> RelevanceVerdict:
    """Classify ``text`` as in-scope or out-of-scope for the cohort hub.

    Never raises: on any internal error it fails open with confidence 0.35.
    """
    try:
        tokens = _tokenize(text)
        if not tokens:
            return RelevanceVerdict("in_scope", 0.35, [], None)

        in_terms = _in_domain_matches(tokens)
        out_terms = _out_of_scope_matches(tokens)

        if out_terms and not in_terms:
            return RelevanceVerdict(
                "out_of_scope", 0.95, sorted(out_terms), _topic_for(out_terms)
            )

        if in_terms and not out_terms:
            confidence = min(1.0, 0.5 + 0.15 * len(in_terms))
            return RelevanceVerdict("in_scope", confidence, sorted(in_terms), None)

        if in_terms and out_terms:
            # Mixed query: never reject. A cohort question that happens to mention an
            # out-of-scope word still deserves a real answer.
            return RelevanceVerdict(
                "in_scope", 0.72, sorted(in_terms | out_terms), None
            )

        # Nothing matched either lexicon: fail open with deliberately low confidence.
        return RelevanceVerdict("in_scope", 0.35, [], None)

    except Exception:  # noqa: BLE001 - the gate must never break chat (NFR-3)
        return RelevanceVerdict("in_scope", 0.35, [], None)


def redirect_message(topic_guess: str | None) -> str:
    """Polite redirect text. Interpolates ONLY a closed-enum label, never user input."""
    label = topic_guess if topic_guess in TOPIC_LABELS else None
    phrase = label.replace("_", " ") if label else _UNKNOWN_TOPIC_PHRASE
    return (
        "This hub covers the GFS Immersion 2026 cohort sessions and Google Cloud / "
        f"Antler topics. Your question looks like it's about {phrase}. "
        "A general search engine or the official documentation will serve you better."
    )


def suggested_questions() -> list[str]:
    """Three in-scope questions offered as one-click rephrase options."""
    return list(_IN_SCOPE_QUESTIONS)
