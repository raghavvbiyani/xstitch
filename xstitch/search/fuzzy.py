"""Trigram-based fuzzy matching for typo tolerance in search queries.

Design decisions:

Why trigram Jaccard similarity (not Levenshtein edit distance):
  - Edit distance is O(m*n) per pair. With a vocabulary of 1000 tokens
    and a 5-token query, that's 5000 comparisons of O(m*n) each.
  - Trigram sets are precomputed once. Jaccard similarity between two sets
    is O(min(|A|,|B|)) via set intersection — effectively O(1) per pair.
  - Trigrams handle transpositions ("datbase" vs "database") naturally,
    while edit distance counts them as 2 operations.

Why threshold 0.3 (not higher or lower):
  - 0.3 catches common single-character typos and transpositions.
  - Lower thresholds produce too many false positives (matching unrelated words).
  - Higher thresholds miss multi-character typos.
  - Validated empirically on developer-centric vocabulary.

Why not phonetic matching (Soundex/Metaphone):
  - Developer terms are often abbreviations (k8s, db, api) that have no
    phonetic representation.
  - Trigrams work on character structure, which matches how developers
    actually misspell things.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bm25 import BM25Engine


def _trigrams(word: str) -> set[str]:
    """Generate character trigrams from a word.

    Pads with boundary markers so short words still produce trigrams:
    "db" -> {"$db", "db$"}, "api" -> {"$ap", "api", "pi$"}
    """
    if len(word) < 2:
        return set()
    padded = f"${word}$"
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity: |intersection| / |union|. Returns 0.0 if both empty."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


class FuzzyMatcher:
    """Finds approximate matches for misspelled query tokens.

    Usage:
        matcher = FuzzyMatcher()
        matcher.build_vocabulary(bm25_engine)
        expansions = matcher.expand_query(["datbase", "migrat"])
        # -> {"database", "migration"} (fuzzy matches for typos)
    """

    def __init__(self, threshold: float = 0.3):
        self._threshold = threshold
        self._vocab: dict[str, set[str]] = {}  # token -> trigram set

    def build_vocabulary(self, engine: "BM25Engine"):
        """Build trigram index from the BM25 engine's vocabulary.

        Called once per search — the vocabulary is the set of all tokens
        that appear in any indexed document.
        """
        all_tokens = engine.get_all_tokens()
        self._vocab = {token: _trigrams(token) for token in all_tokens if len(token) >= 2}

    def find_similar(self, token: str, top_k: int = 3) -> list[tuple[str, float]]:
        """Find vocabulary tokens similar to the input token.

        Returns list of (token, similarity) pairs sorted by similarity descending.
        Only returns matches above the threshold.
        """
        if len(token) < 2:
            return []

        query_trigrams = _trigrams(token)
        matches = []

        for vocab_token, vocab_trigrams in self._vocab.items():
            if vocab_token == token:
                continue
            sim = jaccard_similarity(query_trigrams, vocab_trigrams)
            if sim >= self._threshold:
                matches.append((vocab_token, sim))

        matches.sort(key=lambda x: -x[1])
        return matches[:top_k]

    def expand_query(self, query_tokens: list[str]) -> set[str]:
        """Expand query tokens with fuzzy matches from the vocabulary.

        For each query token, finds similar vocabulary tokens and returns
        the union of all expansions (excluding tokens already in the query).
        """
        query_set = set(query_tokens)
        expansions: set[str] = set()

        for token in query_tokens:
            if token in self._vocab:
                continue
            similar = self.find_similar(token)
            for match_token, _sim in similar:
                if match_token not in query_set:
                    expansions.add(match_token)

        return expansions
