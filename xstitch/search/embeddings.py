"""Optional embedding-based semantic search for Stitch.

Requires: pip install xstitch[search]  (installs sentence-transformers)

This module is NEVER imported at package level — it's loaded on demand
by SearchEngine.try_load_embeddings(). If sentence-transformers is not
installed, importing this module raises ImportError, which the caller
catches gracefully.

Design decisions:

Why sentence-transformers with all-MiniLM-L6-v2:
  - 22MB model, fast inference on CPU (~5ms per embedding)
  - Good quality for short texts (task titles, objectives)
  - Well-maintained, widely used, MIT licensed

Why not OpenAI/Anthropic API embeddings:
  - Requires API key — breaks offline usage
  - Costs money per request
  - Adds network latency
  - Vendor lock-in

Why cosine similarity (not dot product or Euclidean):
  - Normalized embeddings make cosine = dot product, but cosine is more
    intuitive (1.0 = identical, 0.0 = unrelated)
  - Length-invariant: a long objective and short title are comparable
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import Store

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


_MODEL_NAME = "all-MiniLM-L6-v2"


class EmbeddingSearch:
    """Semantic search using sentence embeddings.

    Only instantiable if sentence-transformers is installed.
    """

    def __init__(self):
        if not AVAILABLE:
            raise ImportError(
                "sentence-transformers is required for embedding search. "
                "Install with: pip install xstitch[search]"
            )
        self._model = SentenceTransformer(_MODEL_NAME)
        self._cache: dict[str, list[float]] = {}

    def _embed(self, text: str) -> list[float]:
        """Embed a text string, using cache for repeated texts."""
        if text in self._cache:
            return self._cache[text]
        embedding = self._model.encode(text, show_progress_bar=False).tolist()
        self._cache[text] = embedding
        return embedding

    def search(self, query: str, store: "Store", top_k: int = 10) -> list[dict]:
        """Search tasks by semantic similarity to the query."""
        if not AVAILABLE:
            return []

        query_emb = np.array(self._embed(query))

        all_tasks = store.list_tasks(project_only=False)
        results = []

        for task in all_tasks:
            text = f"{task.title} {task.objective}"
            task_emb = np.array(self._embed(text))

            similarity = float(np.dot(query_emb, task_emb) / (
                np.linalg.norm(query_emb) * np.linalg.norm(task_emb) + 1e-9
            ))

            if similarity > 0.2:
                results.append({
                    "task": task,
                    "score": similarity,
                    "confidence": similarity,
                    "evidence": [f"semantic_similarity:{similarity:.2f}"],
                    "field_scores": {},
                })

        results.sort(key=lambda x: -x["score"])
        return results[:top_k]
