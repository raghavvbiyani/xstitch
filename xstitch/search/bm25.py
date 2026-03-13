"""BM25 Okapi scoring engine with hierarchical field weighting.

Inspired by PageIndex (https://github.com/VectifyAI/PageIndex):
  RELEVANCE != SIMILARITY — matching by reasoning about structure, not distance.

Design decisions:

Why BM25 Okapi specifically (not BM25+, BM25L, or TF-IDF):
  - BM25 Okapi's length normalization (parameter B) is crucial when fields
    vary wildly in size: a title has 5 tokens, snapshots might have 500.
    TF-IDF would unfairly favor long documents.
  - K1=1.5, B=0.75 are standard values that work well without tuning.
  - BM25+ and BM25L add complexity for marginal gains on our small corpus.

Why hierarchical field weights (not flat document scoring):
  - A match in the title ("database migration") is far more relevant than
    the same match in a snapshot ("ran database migration script").
  - Field weights encode this: title=5.0 vs snapshots=1.5.
  - This mirrors PageIndex's tree traversal: broad context first, then
    drill into specifics.

Why multi-level confirmation boost:
  - PageIndex validates candidates by checking multiple tree levels.
  - We do the same: if "migration" appears in BOTH the title (coarse)
    AND decisions (deep), it's much stronger evidence than either alone.
  - The boost is multiplicative (1 + 0.15 * N_terms) to reward breadth.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import Store
    from ..models import Task

from .tokenizer import tokenize, extract_bigrams, time_decay_factor


# ---------------------------------------------------------------------------
# BM25 Configuration
# ---------------------------------------------------------------------------

BM25_K1 = 1.5
BM25_B = 0.75

COARSE_FIELDS = {"title", "objective", "tags"}

FIELD_WEIGHTS = {
    "title": 5.0,
    "objective": 4.0,
    "tags": 4.5,
    "decisions_problem": 3.5,
    "decisions_chosen": 3.0,
    "decisions_alternatives": 2.5,
    "decisions_reasoning": 2.0,
    "current_state": 2.0,
    "next_steps": 2.0,
    "blockers": 1.5,
    "snapshots": 1.5,
    "files_changed": 2.0,
    "git_branches": 1.5,
    "bigrams": 6.0,
}

MULTI_LEVEL_BOOST_PER_TERM = 0.15


# ---------------------------------------------------------------------------
# Task Document — hierarchical index entry
# ---------------------------------------------------------------------------

@dataclass
class TaskDocument:
    """A hierarchical document built from a task for BM25 scoring.

    Mirrors PageIndex: fields at different specificity levels form a tree.
    Coarse fields (title/objective/tags) are the "root nodes" — they tell
    you WHAT the task is. Deep fields (decisions/snapshots) are "leaf nodes"
    — they tell you WHAT was done.
    """
    task_id: str
    task: "Task"
    fields: dict[str, str] = field(default_factory=dict)
    field_tokens: dict[str, list[str]] = field(default_factory=dict)
    total_tokens: int = 0

    def build(self, store: "Store"):
        """Populate all fields from the task's stored data."""
        t = self.task
        self.fields = {
            "title": t.title,
            "objective": t.objective,
            "tags": " ".join(t.tags),
            "current_state": t.current_state,
            "next_steps": t.next_steps,
            "blockers": t.blockers,
        }

        decisions = store.get_decisions(t.id)
        self.fields["decisions_problem"] = " ".join(d.problem for d in decisions)
        self.fields["decisions_chosen"] = " ".join(d.chosen for d in decisions)
        self.fields["decisions_alternatives"] = " ".join(
            " ".join(d.alternatives) for d in decisions
        )
        self.fields["decisions_reasoning"] = " ".join(
            f"{d.tradeoffs} {d.reasoning}" for d in decisions
        )

        snapshots = store.get_snapshots(t.id, limit=20)
        self.fields["snapshots"] = " ".join(s.message for s in snapshots)
        self.fields["files_changed"] = " ".join(
            " ".join(s.files_changed) for s in snapshots
        )
        self.fields["git_branches"] = " ".join(
            s.git_branch for s in snapshots if s.git_branch
        )

        for fname, text in self.fields.items():
            self.field_tokens[fname] = tokenize(text)

        bigram_source = (
            self.field_tokens.get("title", [])
            + self.field_tokens.get("objective", [])
            + self.field_tokens.get("decisions_problem", [])
            + self.field_tokens.get("decisions_chosen", [])
        )
        self.field_tokens["bigrams"] = extract_bigrams(bigram_source)

        self.total_tokens = sum(len(toks) for toks in self.field_tokens.values())


# ---------------------------------------------------------------------------
# BM25 Engine
# ---------------------------------------------------------------------------

class BM25Engine:
    """BM25 Okapi scoring with PageIndex-inspired hierarchical matching."""

    def __init__(self):
        self.documents: list[TaskDocument] = []
        self.avg_doc_len: float = 0.0
        self.doc_freq: dict[str, int] = {}
        self.n_docs: int = 0

    def index(self, store: "Store"):
        """Build the index from all tasks in the store."""
        all_tasks = store.list_tasks(project_only=False)
        self.documents = []

        for task in all_tasks:
            doc = TaskDocument(task_id=task.id, task=task)
            doc.build(store)
            self.documents.append(doc)

        self.n_docs = len(self.documents)
        if self.n_docs == 0:
            return

        total_len = sum(d.total_tokens for d in self.documents)
        self.avg_doc_len = total_len / self.n_docs if self.n_docs > 0 else 1.0

        self.doc_freq = {}
        for doc in self.documents:
            seen_terms = set()
            for tokens in doc.field_tokens.values():
                for t in tokens:
                    seen_terms.add(t)
            for t in seen_terms:
                self.doc_freq[t] = self.doc_freq.get(t, 0) + 1

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """Search for relevant tasks using hierarchical BM25 scoring.

        Returns ranked list of {task, score, confidence, evidence, field_scores}.
        """
        query_tokens = tokenize(query)
        query_bigrams = extract_bigrams(query_tokens)

        if not query_tokens:
            return []

        id_match = re.search(r"\b([a-f0-9]{8,12})\b", query.lower())

        results = []
        for doc in self.documents:
            if id_match and id_match.group(1) in doc.task_id:
                results.append({
                    "task": doc.task,
                    "score": 1000.0,
                    "confidence": 1.0,
                    "evidence": ["exact_task_id_match"],
                    "field_scores": {},
                })
                continue

            total_score = 0.0
            evidence = []
            field_scores = {}
            coarse_matched: set[str] = set()
            deep_matched: set[str] = set()

            for fname, tokens in doc.field_tokens.items():
                if not tokens:
                    continue

                weight = FIELD_WEIGHTS.get(fname, 1.0)
                field_score = 0.0

                q_terms = query_bigrams if fname == "bigrams" else query_tokens

                for qt in q_terms:
                    tf = tokens.count(qt)
                    if tf == 0:
                        continue

                    n_containing = self.doc_freq.get(qt, 0)
                    idf = math.log(
                        (self.n_docs - n_containing + 0.5)
                        / (n_containing + 0.5)
                        + 1.0
                    )

                    dl = len(tokens)
                    tf_norm = (tf * (BM25_K1 + 1)) / (
                        tf
                        + BM25_K1
                        * (1 - BM25_B + BM25_B * dl / max(self.avg_doc_len, 1))
                    )

                    term_score = idf * tf_norm * weight
                    field_score += term_score
                    evidence.append(f"{fname}:{qt}")

                    if fname in COARSE_FIELDS:
                        coarse_matched.add(qt)
                    else:
                        deep_matched.add(qt)

                if field_score > 0:
                    field_scores[fname] = field_score
                    total_score += field_score

            multi_level_terms = coarse_matched & deep_matched
            if multi_level_terms:
                total_score *= 1.0 + MULTI_LEVEL_BOOST_PER_TERM * len(multi_level_terms)

            if doc.task.status == "active":
                total_score *= 1.15

            project_path = str(Path.cwd().resolve())
            if doc.task.project_path and project_path in doc.task.project_path:
                total_score *= 1.25

            total_score *= time_decay_factor(doc.task.updated_at)

            if total_score > 0:
                matched_terms = set()
                for e in evidence:
                    if ":" in e:
                        matched_terms.add(e.split(":")[1])

                all_query_terms = set(query_tokens + query_bigrams)

                results.append({
                    "task": doc.task,
                    "score": total_score,
                    "confidence": 0.0,
                    "evidence": evidence[:10],
                    "field_scores": field_scores,
                    "_matched_fraction": len(matched_terms)
                    / max(len(all_query_terms), 1),
                })

        if not results:
            return []

        max_score = max(r["score"] for r in results)
        for r in results:
            score_norm = min(r["score"] / max_score, 1.0) if max_score > 0 else 0
            r["confidence"] = score_norm * r.pop("_matched_fraction")

        results.sort(key=lambda x: -x["score"])
        return results[:top_k]

    def get_all_tokens(self) -> set[str]:
        """Return all unique tokens in the index for fuzzy matching vocabulary."""
        tokens = set()
        for doc in self.documents:
            for field_tokens in doc.field_tokens.values():
                tokens.update(field_tokens)
        return tokens
