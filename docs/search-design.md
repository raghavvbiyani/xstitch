# Stitch Search Engine Architecture

The Stitch search engine lives in `xstitch/search/` and provides unified task retrieval combining lexical (BM25), fuzzy (trigram), and optional semantic (embeddings) scoring. This document describes the architecture, components, and design decisions.

---

## Overview

```
                    +------------------+
                    |  User Query      |
                    +--------+---------+
                             |
                             v
    +----------------------------------------------------------+
    |                    SearchEngine (facade)                   |
    +----------------------------------------------------------+
                             |
         +-------------------+-------------------+
         |                   |                   |
         v                   v                   v
    +---------+        +----------+        +-------------+
    | BM25    |        | Fuzzy    |        | Embeddings  |
    | Engine  |        | Matcher  |        | (optional)   |
    +----+----+        +----+-----+        +------+------+
         |                   |                    |
         |                   |                    |
         v                   v                    v
    +---------+        +----------+        +-------------+
    | tokenize|        | expand   |        | cosine sim  |
    | + score |        | query    |        | on vectors  |
    +----+----+        +----+-----+        +------+------+
         |                   |                    |
         +-------------------+--------------------+
                             |
                             v
                    +------------------+
                    | RRF Fusion       |
                    | (rank-based)     |
                    +--------+---------+
                             |
                             v
                    +------------------+
                    | SearchResult[]   |
                    +------------------+
```

---

## Component 1: tokenizer.py

Text processing pipeline for both documents and queries.

### Pipeline Stages

1. **CamelCase splitting** — `re.sub(r"([a-z])([A-Z])", r"\1 \2", text)` breaks "camelCase" into "camel Case"
2. **Lowercasing** — via `text.lower()`
3. **Word extraction** — `re.findall(r"[a-z0-9]+", ...)` splits on non-alphanumeric characters
4. **Stop word removal** — `STOP_WORDS` set filters common terms ("the", "and", "work", "task", "project", etc.)
5. **Suffix stemming** — `stem()` applies `_STEM_RULES` (e.g., "ization" -> 3 chars, "ing" -> 3 chars)
6. **Alias expansion** — `ALIAS_MAP` adds synonym tokens (e.g., "db" -> "databas", "database" -> "db")
7. **Bigram extraction** — `extract_bigrams()` produces compound tokens ("rate_limit" from "rate" + "limit")

### Key Data Structures

- **STOP_WORDS** — Set of ~80 terms with zero discriminative power in coding contexts (includes domain terms like "work", "project", "task")
- **_STEM_RULES** — List of (suffix, min_remaining) tuples for suffix stripping
- **ALIAS_GROUPS** — Groups like `{"db", "database"}`, `{"api", "endpoint"}`, `{"k8s", "kubernetes"}`, etc.
- **ALIAS_MAP** — Built from ALIAS_GROUPS; maps each term to its synonym set (including stemmed forms)

### Time Decay

`time_decay_factor(updated_at)` applies a 30-day half-life recency boost. Floor at 0.1 so very old tasks can still match on strong term signals.

### Design: Why Custom Stemming

- Zero dependencies (NLTK ~12MB, spaCy 200MB+)
- Developer-centric corpus ("authentication", "kubernetes", "migration") — small hand-tuned suffix table performs well
- Trades linguistic precision for recall; missing the right task is worse than imperfect stems

### Design: Why Alias Expansion at Tokenization Time

- Both documents and queries use the same pipeline
- Document mentioning "database" gets "db" as alias token; query "db" matches without query-side expansion
- Bidirectional: works regardless of which side uses the abbreviation

---

## Component 2: bm25.py

BM25 Okapi scoring engine with hierarchical field weighting.

### TaskDocument Dataclass

Builds tokenized field representations from Task objects:

| Field | Weight | Purpose |
|-------|--------|---------|
| title | 5.0 | Primary task identifier |
| objective | 4.0 | What the task aims to achieve |
| tags | 4.5 | User-assigned labels |
| decisions_problem | 3.5 | Decision context |
| decisions_chosen | 3.0 | Chosen solution |
| decisions_alternatives | 2.5 | Rejected options |
| decisions_reasoning | 2.0 | Rationale |
| current_state | 2.0 | Progress description |
| next_steps | 2.0 | Planned actions |
| blockers | 1.5 | Blocking issues |
| snapshots | 1.5 | Progress history |
| files_changed | 2.0 | Touched files |
| git_branches | 1.5 | Branch context |
| bigrams | 6.0 | Compound phrase signals |

### COARSE_FIELDS

`{"title", "objective", "tags"}` — used for quick pre-filtering and multi-level confirmation boost. A term matching both coarse and deep fields gets a multiplicative boost.

### BM25 Parameters

- **k1 = 1.5** — Term frequency saturation
- **b = 0.75** — Length normalization

### BM25Engine API

- `index(store)` — Build index from all tasks
- `search(query, top_k)` — Return ranked `{task, score, confidence, evidence, field_scores}`
- `get_all_tokens()` — Vocabulary for fuzzy matcher

### Additional Boosts

- **Active task** — 1.15x
- **Current project** — 1.25x if task.project_path contains cwd
- **Multi-level match** — 1 + 0.15 * N_terms when term appears in both coarse and deep fields
- **Time decay** — Applied to final score

### Design: Why BM25 Over TF-IDF

- Document length normalization (parameter b): fields vary wildly (title ~5 tokens, snapshots ~500). TF-IDF unfairly favors long documents.
- Rare term boosting: IDF rewards terms like "postgresql" in 1/50 tasks more than "api" in 40/50.
- Standard k1=1.5, b=0.75 work well without tuning on small corpus.

---

## Component 3: fuzzy.py

Trigram-based fuzzy matching for typo tolerance.

### Algorithm

1. **`_trigrams(word)`** — Generate character 3-grams with boundary padding: "db" -> {"$db", "db$"}
2. **`jaccard_similarity(A, B)`** — |A ∩ B| / |A ∪ B|
3. **`FuzzyMatcher.expand_query(tokens)`** — For each query token not in vocabulary, find similar tokens above threshold (default 0.3)

### Flow

```
Query: "datbase migrat"
         |
         v
   tokenize(query) -> ["datbase", "migrat"]
         |
         v
   build_vocabulary(bm25_engine)  # one-time per search
         |
         v
   expand_query(["datbase", "migrat"])
     -> find_similar("datbase") -> [("database", 0.6), ...]
     -> find_similar("migrat")  -> [("migration", 0.5), ...]
         |
         v
   Expanded query: "datbase migrat database migration"
         |
         v
   BM25.search(expanded_query)  # re-run BM25 with expanded terms
```

### Design: Why Trigram Over Levenshtein

- Edit distance is O(m*n) per comparison. With 1000 vocabulary tokens and 5-token query: 5000 O(m*n) comparisons.
- Trigram sets precomputed once. Jaccard between two sets is O(min(|A|,|B|)) via set intersection — effectively O(1) per pair after vocabulary build.
- Trigrams handle transpositions ("datbase" vs "database") naturally; edit distance counts them as 2 operations.

### Design: Why Not Phonetic (Soundex/Metaphone)

- Developer terms are abbreviations (k8s, db, api) with no phonetic representation.
- Trigrams work on character structure, matching how developers misspell.

---

## Component 4: embeddings.py

Optional semantic search via sentence-transformers.

### Guarded Import

```python
try:
    from sentence_transformers import SentenceTransformer
    AVAILABLE = True
except ImportError:
    AVAILABLE = False
```

Never imported at package level — loaded on demand by `SearchEngine.try_load_embeddings()`.

### Model

- **all-MiniLM-L6-v2** — 22MB, ~5ms per embedding on CPU
- Cosine similarity for ranking

### EmbeddingSearch API

- `search(query, store, top_k)` — Embed query and each task's title+objective, return by cosine similarity
- Results filtered to similarity > 0.2

### Design: Why Optional Embeddings

- Embedding models are 22MB+ downloads. Mandatory = broken offline installs.
- Core package: `pip install xstitch` (BM25 + fuzzy only)
- Full search: `pip install xstitch[search]` (adds sentence-transformers)

### Design: Why Not API Embeddings (OpenAI/Anthropic)

- Requires API key — breaks offline usage
- Per-request cost and network latency
- Vendor lock-in

---

## Component 5: index.py

Persistent search index for incremental updates.

### Storage

- Path: `~/.stitch/projects/<key>/search_index.json`
- JSON format, human-readable and debuggable

### PersistentIndex API

- `load()` — Load from disk
- `save()` — Atomic write via temp file + rename
- `get_entry(task_id)` — Cached entry or None
- `set_entry(task_id, entry)` — Update cache
- `remove_entry(task_id)` — Remove task
- `is_stale(task_id, meta_mtime)` — True if cached mtime < meta.json mtime

### Design: Why JSON (Not SQLite or Pickle)

- Human-readable, debuggable
- Index small (<100KB for ~50 tasks)
- Atomic writes prevent corruption
- No binary compatibility issues across Python versions

### Design: Why mtime-Based Staleness

- File mtime is free — no disk reads
- Content hashing would require reading every meta.json
- False positives (mtime changed, content unchanged) cause cheap re-tokenization of one task

---

## Component 6: __init__.py — SearchEngine Facade

Unified API combining BM25, fuzzy, and optional embeddings.

### Search Pipeline

```
1. BM25.index(store)
2. BM25.search(query, top_k*2)
3. FuzzyMatcher.build_vocabulary(bm25)
4. fuzzy_expansions = FuzzyMatcher.expand_query(query_tokens)
5. If expansions: BM25.search(expanded_query, top_k*2)
6. If embeddings loaded: EmbeddingSearch.search(query, store, top_k)
7. _fuse_results(bm25_results, fuzzy_results, embedding_results, top_k)
```

### SearchResult Dataclass

- `task_id`, `task`
- `bm25_score`, `fuzzy_score`, `embedding_score`
- `combined_score`, `confidence`
- `evidence`, `field_scores`

### Reciprocal Rank Fusion (RRF)

Scores from different engines are not comparable (BM25 unbounded, cosine [-1,1], Jaccard [0,1]). RRF uses rank positions only:

```
RRF_score(task) = 1 / (60 + rank)
```

Per-engine RRF scores are combined with weights:
- BM25: 0.6
- Fuzzy: 0.3
- Embeddings: 0.1

### Design: Why RRF Over Linear Score Combination

- Rank-based fusion is parameter-free — no score normalization needed
- Robust to different score scales and distributions
- Proven effective in information retrieval literature

### try_load_embeddings()

Attempts to load `EmbeddingSearch`. No-op if sentence-transformers unavailable. Callers can upgrade to semantic search without changing search API.

---

## Data Flow Summary

```
                    Query
                      |
                      v
              +---------------+
              |   tokenize()  |
              +-------+-------+
                      |
        +-------------+-------------+
        |                           |
        v                           v
   BM25.search()              expand_query()
   (exact/stemmed)            (fuzzy matches)
        |                           |
        |                           v
        |                    BM25.search()
        |                    (expanded query)
        |                           |
        +-------------+-------------+
                      |
                      v
              (optional) embeddings.search()
                      |
                      v
              _fuse_results() [RRF]
                      |
                      v
              SearchResult[]
```

---

## File Layout

```
xstitch/search/
  __init__.py    # SearchEngine, SearchResult, RRF fusion
  tokenizer.py   # tokenize, stem, extract_bigrams, time_decay_factor
  bm25.py        # BM25Engine, TaskDocument, FIELD_WEIGHTS
  fuzzy.py       # FuzzyMatcher, _trigrams, jaccard_similarity
  embeddings.py  # EmbeddingSearch (optional)
  index.py       # PersistentIndex (on-disk cache)
```
