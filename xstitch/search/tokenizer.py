"""Text tokenization pipeline for Stitch search.

Pipeline: camelCase split → lowercase → word extraction → stop-word removal →
suffix stemming → synonym/alias expansion → deduplication.

Design decisions:

Why custom stemming instead of NLTK/spaCy:
  - Zero dependencies. NLTK is 12MB, spaCy is 200MB+.
  - Our corpus is developer-centric ("authentication", "kubernetes",
    "migration") — a small hand-tuned suffix table performs well.
  - Trades linguistic precision for recall, which matters more in a
    small task corpus where missing the right task is worse than
    producing an imperfect stem.

Why alias expansion at tokenization time (not query time only):
  - Both documents AND queries go through the same pipeline.
  - A document mentioning "database" gets "db" as an alias token.
  - A query for "db" matches without needing query-side expansion.
  - Bidirectional: works regardless of which side uses the abbreviation.

Why stop words include task-domain terms ("work", "project", "task"):
  - In an AI coding agent context, nearly every prompt contains these.
  - They carry zero discriminative power and dilute BM25 IDF.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Stemming — lightweight suffix stripping for developer vocabulary
# ---------------------------------------------------------------------------

_STEM_RULES = [
    ("ization", 3), ("isation", 3), ("ational", 3),
    ("ation", 3), ("ition", 3), ("ution", 3),
    ("ement", 3), ("ment", 3),
    ("ness", 3), ("ity", 3),
    ("ible", 3), ("able", 3),
    ("ful", 3), ("less", 4), ("ive", 3), ("ous", 3),
    ("ing", 3), ("ion", 3),
    ("ied", 3), ("ies", 3),
    ("ate", 3),
    ("ed", 3), ("er", 3), ("es", 3),
    ("ly", 3), ("al", 3),
    ("y", 4), ("s", 4),
]


def stem(word: str) -> str:
    """Reduce a word to its approximate root via suffix stripping.

    Not linguistically perfect — trades precision for recall. For task
    matching in a small corpus, recall (finding the right task) matters
    far more than producing correct dictionary roots.
    """
    if len(word) <= 4:
        return word
    for suffix, min_remaining in _STEM_RULES:
        if word.endswith(suffix) and len(word) - len(suffix) >= min_remaining:
            return word[:-len(suffix)]
    return word


# ---------------------------------------------------------------------------
# Synonym / Alias Expansion — bridges abbreviation and vocabulary gaps
# ---------------------------------------------------------------------------

ALIAS_GROUPS: list[set[str]] = [
    {"db", "databas", "database"},
    {"auth", "authentication", "authorization", "authenticate"},
    {"api", "endpoint"},
    {"config", "configuration", "configure", "conf"},
    {"k8s", "kubernetes", "kube"},
    {"infra", "infrastructure"},
    {"repo", "repository"},
    {"impl", "implementation", "implement"},
    {"perf", "performance"},
    {"dep", "deps", "dependency"},
    {"env", "environment"},
    {"doc", "docs", "documentation"},
    {"err", "error"},
    {"req", "request"},
    {"res", "response"},
    {"pkg", "package"},
    {"fn", "func", "function"},
    {"param", "params", "parameter"},
    {"postgres", "postgresql", "psql"},
    {"mongo", "mongodb"},
    {"js", "javascript"},
    {"ts", "typescript"},
    {"py", "python"},
    {"msg", "message"},
    {"async", "asynchronous"},
    {"sync", "synchronous"},
    {"dir", "directory"},
    {"val", "validation", "validate"},
    {"init", "initialization", "initialize"},
    {"gen", "generate", "generation", "generator"},
    {"migr", "migrat", "migrate", "migration"},
    {"deploy", "deployment"},
    {"test", "testing"},
    {"debug", "debugg", "debugging"},
    {"refactor", "refactoring"},
    {"cache", "cach", "caching"},
    {"queue", "queuing"},
    {"svc", "service"},
    {"ctrl", "controller"},
    {"mw", "middleware"},
    {"ws", "websocket"},
]

ALIAS_MAP: dict[str, set[str]] = {}
for _group in ALIAS_GROUPS:
    _all_forms: set[str] = set()
    for _term in _group:
        _all_forms.add(_term)
        _s = stem(_term)
        if _s != _term:
            _all_forms.add(_s)
    for _term in _all_forms:
        ALIAS_MAP.setdefault(_term, set()).update(_all_forms - {_term})


# ---------------------------------------------------------------------------
# Bigram extraction — compound terms are stronger signals
# ---------------------------------------------------------------------------

def extract_bigrams(tokens: list[str]) -> list[str]:
    """Generate compound bigram tokens from adjacent pairs.

    "rate" + "limit" -> "rate_limit". These are highly specific signals:
    matching a bigram is much stronger evidence than matching either unigram.
    """
    if len(tokens) < 2:
        return []
    return [f"{tokens[i]}_{tokens[i + 1]}" for i in range(len(tokens) - 1)]


# ---------------------------------------------------------------------------
# Stop words — terms with zero discriminative power in coding contexts
# ---------------------------------------------------------------------------

STOP_WORDS = {
    "i", "me", "my", "we", "our", "the", "a", "an", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "can", "may", "might",
    "shall", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "about", "that", "this", "it", "its", "and", "or",
    "but", "if", "then", "so", "up", "out", "no", "not", "what", "which",
    "who", "when", "where", "how", "all", "each", "every", "both", "few",
    "more", "most", "some", "any", "let", "lets", "want", "need", "please",
    "work", "working", "task", "project", "also", "just", "like", "get",
    "make", "done", "thing", "things", "way", "start", "started", "using",
    "use", "used", "try", "tried", "help", "still", "back", "going",
}


# ---------------------------------------------------------------------------
# Recency decay — time is a relevance signal
# ---------------------------------------------------------------------------

def time_decay_factor(updated_at: str) -> float:
    """Compute recency boost with 30-day half-life.

    Tasks updated recently are more likely what the user is referring to.
    Floor at 0.1 so very old tasks can still match on strong term signals.
    """
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(updated_at)
        now = datetime.now(timezone.utc)
        days_old = max((now - dt).total_seconds() / 86400, 0)
        return max(0.5 ** (days_old / 30), 0.1)
    except (ValueError, TypeError):
        return 0.5


# ---------------------------------------------------------------------------
# Main tokenization function
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """Tokenize text for relevance matching.

    Pipeline: split -> remove stop words -> stem -> expand aliases.

    Alias-expanded terms get TF=1 per expansion (weaker than explicit
    mentions), which is semantically correct: a document mentioning
    "database" 5 times has TF=5 for "databas" but only TF=1 for the
    alias "db".
    """
    if not text:
        return []

    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    words = re.findall(r"[a-z0-9]+", text.lower())

    tokens = []
    seen_aliases: set[str] = set()

    for w in words:
        if w in STOP_WORDS or len(w) < 2:
            continue

        stemmed = stem(w)
        tokens.append(stemmed)
        seen_aliases.add(stemmed)

        for source in (w, stemmed):
            for alias in ALIAS_MAP.get(source, ()):
                a_stem = stem(alias)
                if a_stem not in seen_aliases:
                    tokens.append(a_stem)
                    seen_aliases.add(a_stem)

    return tokens
