"""Unified search engine for Stitch — BM25 + fuzzy matching + optional embeddings.

Architecture decision: unified facade over multiple scoring strategies.

Why a single SearchEngine instead of separate BM25/fuzzy/embedding calls:
  - Callers get one API; they don't need to know which algorithms are available
  - Score fusion (combining BM25 + fuzzy + embedding signals) happens internally
  - Adding a new scoring strategy doesn't change any caller code
  - Disk reads are shared — one index load serves all strategies

Why BM25 as the primary engine (not vector similarity):
  - BM25's IDF rewards rare terms: "postgresql" appearing in 1/50 tasks is a
    much stronger signal than "api" appearing in 40/50. Cosine similarity
    treats all dimensions equally and would miss this.
  - Our corpus is small (~100 tasks) — BM25 is fast enough without approximation.
  - Zero dependencies required.

Why trigram fuzzy on top of BM25 (not edit distance):
  - Edit distance is O(m*n) per comparison. Trigram Jaccard is O(1) after
    the one-time set construction — much faster for vocabulary scanning.
  - Trigrams handle transpositions and multi-char typos better than
    single-edit-distance metrics.

Why optional embeddings (not mandatory):
  - Embedding models are 22MB+ downloads. Mandatory = broken offline installs.
  - Guarded import: works with `pip install xstitch` (BM25 only) or
    `pip install xstitch[search]` (BM25 + embeddings).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import Store

from .bm25 import BM25Engine, TaskDocument, FIELD_WEIGHTS, COARSE_FIELDS
from .tokenizer import tokenize, extract_bigrams
from .fuzzy import FuzzyMatcher


@dataclass
class SearchResult:
    """A single search result with scores from each engine."""
    task_id: str
    task: object  # Task dataclass — avoid circular import
    bm25_score: float = 0.0
    fuzzy_score: float = 0.0
    embedding_score: float = 0.0
    combined_score: float = 0.0
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    field_scores: dict[str, float] = field(default_factory=dict)


class SearchEngine:
    """Unified search: BM25 + trigram fuzzy + optional embeddings.

    Usage:
        engine = SearchEngine()
        results = engine.search("database migration", store)
    """

    def __init__(self):
        self._bm25 = BM25Engine()
        self._fuzzy = FuzzyMatcher()
        self._embeddings = None  # lazy load if available

    def search(self, query: str, store: "Store", top_k: int = 10) -> list[SearchResult]:
        """Search tasks by relevance, combining all available scoring strategies.

        Pipeline:
        1. BM25 scoring (always) — precision on exact/stemmed terms
        2. Fuzzy expansion (always) — recall on typos/near-misses
        3. Embedding similarity (if available) — semantic gap bridging
        4. Score fusion — weighted combination of all signals
        """
        self._bm25.index(store)

        bm25_results = self._bm25.search(query, top_k=top_k * 2)

        query_tokens = tokenize(query)
        if not query_tokens and not bm25_results:
            return []

        self._fuzzy.build_vocabulary(self._bm25)
        fuzzy_expansions = self._fuzzy.expand_query(query_tokens)

        if fuzzy_expansions:
            expanded_query = " ".join(query_tokens + list(fuzzy_expansions))
            fuzzy_results = self._bm25.search(expanded_query, top_k=top_k * 2)
        else:
            fuzzy_results = []

        embedding_results = []
        if self._embeddings is not None:
            embedding_results = self._embeddings.search(query, store, top_k=top_k)

        return self._fuse_results(
            bm25_results, fuzzy_results, embedding_results, top_k
        )

    def _fuse_results(
        self,
        bm25_results: list[dict],
        fuzzy_results: list[dict],
        embedding_results: list,
        top_k: int,
    ) -> list[SearchResult]:
        """Combine results from multiple engines using reciprocal rank fusion.

        Why reciprocal rank fusion (RRF) over score normalization:
        - Scores from different engines are not comparable (BM25 is unbounded,
          cosine is [-1,1], fuzzy Jaccard is [0,1])
        - RRF only uses rank positions, which are always comparable
        - Simple, parameter-free, proven effective in IR literature
        """
        RANK_CONSTANT = 60  # standard RRF constant

        task_scores: dict[str, dict] = {}

        for rank, r in enumerate(bm25_results):
            tid = r["task"].id
            if tid not in task_scores:
                task_scores[tid] = {
                    "task": r["task"], "bm25": 0, "fuzzy": 0, "embed": 0,
                    "evidence": r.get("evidence", []),
                    "field_scores": r.get("field_scores", {}),
                    "confidence": r.get("confidence", 0),
                }
            task_scores[tid]["bm25"] = 1.0 / (RANK_CONSTANT + rank + 1)

        for rank, r in enumerate(fuzzy_results):
            tid = r["task"].id
            if tid not in task_scores:
                task_scores[tid] = {
                    "task": r["task"], "bm25": 0, "fuzzy": 0, "embed": 0,
                    "evidence": r.get("evidence", []),
                    "field_scores": r.get("field_scores", {}),
                    "confidence": r.get("confidence", 0),
                }
            task_scores[tid]["fuzzy"] = 1.0 / (RANK_CONSTANT + rank + 1)

        results = []
        for tid, data in task_scores.items():
            combined = (
                0.6 * data["bm25"]
                + 0.3 * data["fuzzy"]
                + 0.1 * data["embed"]
            )
            results.append(SearchResult(
                task_id=tid,
                task=data["task"],
                bm25_score=data["bm25"],
                fuzzy_score=data["fuzzy"],
                embedding_score=data["embed"],
                combined_score=combined,
                confidence=data["confidence"],
                evidence=data["evidence"],
                field_scores=data["field_scores"],
            ))

        results.sort(key=lambda x: -x.combined_score)
        return results[:top_k]

    def try_load_embeddings(self):
        """Attempt to load the optional embeddings engine. No-op if unavailable."""
        try:
            from .embeddings import EmbeddingSearch
            self._embeddings = EmbeddingSearch()
        except ImportError:
            self._embeddings = None
