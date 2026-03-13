"""Unit tests for Stitch trigram fuzzy matching."""

from __future__ import annotations

import pytest


class TestFuzzyMatcher:
    def test_trigrams(self):
        from xstitch.search.fuzzy import _trigrams
        tri = _trigrams("database")
        assert "$da" in tri
        assert "dat" in tri
        assert "se$" in tri

    def test_trigrams_short_word(self):
        from xstitch.search.fuzzy import _trigrams
        assert _trigrams("a") == set()
        tri = _trigrams("db")
        assert len(tri) >= 1

    def test_jaccard_similarity(self):
        from xstitch.search.fuzzy import jaccard_similarity
        assert jaccard_similarity({"a", "b", "c"}, {"a", "b", "c"}) == 1.0
        assert jaccard_similarity(set(), set()) == 0.0
        sim = jaccard_similarity({"a", "b"}, {"b", "c"})
        assert 0 < sim < 1

    def test_find_similar_catches_typo(self):
        from xstitch.search.fuzzy import FuzzyMatcher
        from xstitch.search.bm25 import BM25Engine

        class FakeEngine:
            def get_all_tokens(self):
                return {"database", "migration", "authentication", "kubernetes"}

        matcher = FuzzyMatcher(threshold=0.2)
        matcher.build_vocabulary(FakeEngine())
        similar = matcher.find_similar("datbase")
        matched_tokens = [t for t, _ in similar]
        assert "database" in matched_tokens

    def test_expand_query(self):
        from xstitch.search.fuzzy import FuzzyMatcher

        class FakeEngine:
            def get_all_tokens(self):
                return {"database", "migration", "kubernetes"}

        matcher = FuzzyMatcher(threshold=0.2)
        matcher.build_vocabulary(FakeEngine())
        expansions = matcher.expand_query(["datbase"])
        assert "database" in expansions

    def test_no_expansion_for_exact_matches(self):
        from xstitch.search.fuzzy import FuzzyMatcher

        class FakeEngine:
            def get_all_tokens(self):
                return {"database"}

        matcher = FuzzyMatcher(threshold=0.2)
        matcher.build_vocabulary(FakeEngine())
        expansions = matcher.expand_query(["database"])
        assert len(expansions) == 0
