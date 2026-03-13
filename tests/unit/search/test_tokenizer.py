"""Unit tests for Stitch search tokenizer."""

from __future__ import annotations

import pytest


class TestTokenizer:
    def test_basic_tokenization(self):
        from xstitch.search.tokenizer import tokenize
        tokens = tokenize("database migration")
        assert any(t.startswith("datab") or t == "db" for t in tokens)
        assert any("migrat" in t for t in tokens)

    def test_camel_case_splitting(self):
        from xstitch.search.tokenizer import tokenize
        tokens = tokenize("handleAuth")
        assert "handl" in tokens or "handle" in tokens
        assert "auth" in tokens

    def test_stop_words_removed(self):
        from xstitch.search.tokenizer import tokenize
        tokens = tokenize("the project is working")
        for sw in ["the", "is"]:
            assert sw not in tokens

    def test_alias_expansion(self):
        from xstitch.search.tokenizer import tokenize
        tokens = tokenize("db")
        assert "db" in tokens
        assert any(t.startswith("datab") for t in tokens)

    def test_empty_string(self):
        from xstitch.search.tokenizer import tokenize
        assert tokenize("") == []
        assert tokenize(None) == []

    def test_bigram_extraction(self):
        from xstitch.search.tokenizer import extract_bigrams
        bigrams = extract_bigrams(["rate", "limit"])
        assert "rate_limit" in bigrams

    def test_bigram_empty(self):
        from xstitch.search.tokenizer import extract_bigrams
        assert extract_bigrams([]) == []
        assert extract_bigrams(["single"]) == []

    def test_stemming(self):
        from xstitch.search.tokenizer import stem
        assert stem("running") == "runn"
        result = stem("authentication")
        assert result.startswith("authent")
        assert stem("db") == "db"  # short words unchanged

    def test_time_decay_factor(self):
        from xstitch.search.tokenizer import time_decay_factor
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        assert time_decay_factor(now) > 0.9
        assert time_decay_factor("invalid") == 0.5
        assert time_decay_factor("2020-01-01T00:00:00+00:00") >= 0.1
