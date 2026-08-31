"""Stage 1 — goal resolution (PRD Section 5, FR-2).

"Similarity search only locates the starting point; the graph's structure supplies
correctness and order." This is that small first move, and the guardrail matters
more than the ranking: below threshold, or two candidates near-tied, it must
**ask rather than guess**. A wrong goal produces a confident, fluent, entirely
wrong plan.

Two retrievers, both returning a comparable 0-1 score:

* `VectorRetriever` uses the Skills vector index Station 5 builds. It is the
  intended path.
* `LexicalRetriever` is a TF-IDF cosine over the same Skills descriptions, used
  when that index does not exist yet. Station 5 is explicit that goal resolution
  degrades until the index is built; degrading to a weaker retriever with the same
  threshold semantics is that degradation, and it keeps the guardrail intact.

Which one ran is recorded on the result, so a plan never silently claims a vector
match it did not have.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from plangraph_writer.schema import EMBEDDING_FIELD, Schema

METHOD_VECTOR = "vector"
METHOD_LEXICAL = "lexical"

#: Command filler that carries no information about which task is wanted.
#: Deliberately short: verbs like "make" appear in three of the six skill names,
#: so they do carry a little signal, and IDF is the right way to discount them —
#: deleting them would throw the signal away instead of weighting it.
STOPWORDS = frozenset(
    """
    a an the i me my mine we our you your please can could would will shall want
    wants wanted need needs like to for of and or with some any it its that this
    now then just kindly
    """.split()
)

#: A command token matches a skill token if either contains the other and both
#: are at least this long - so "plants" finds "houseplants" and "shirt" finds
#: "tshirt" without matching every short word to every other.
_MIN_FUZZY = 4

_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class Candidate:
    skill: str
    key: str
    scope: str
    score: float
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"skill": self.skill, "skill_scope": self.scope, "score": round(self.score, 4)}


@dataclass
class GoalResolution:
    """The outcome of Stage 1 — a goal, or a request to clarify."""

    command: str
    method: str
    goal: Candidate | None = None
    candidates: list[Candidate] = field(default_factory=list)
    ambiguous: bool = False
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.goal is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "method": self.method,
            "resolved": self.resolved,
            "goal": self.goal.to_dict() if self.goal else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "ambiguous": self.ambiguous,
            "reason": self.reason,
        }


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in STOPWORDS]


def _fuzzy_equal(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) < _MIN_FUZZY or len(right) < _MIN_FUZZY:
        return False
    return left in right or right in left


class LexicalRetriever:
    """TF-IDF cosine over the Skills in the graph.

    IDF is what makes this work on a corpus of tasks that all begin with "make":
    the word carries almost no signal across six skills, while "chai" identifies
    one. Skill names are weighted above their descriptions because a command
    names the task.
    """

    #: How much more a token in the skill's *name* counts than one in its prose.
    NAME_WEIGHT = 3.0

    def __init__(self, skills: list[dict[str, Any]]) -> None:
        self._skills = skills
        self._documents: dict[str, Counter[str]] = {}
        for skill in skills:
            name_tokens = tokenize(skill["name"].replace("_", " "))
            body_tokens = tokenize(skill.get(EMBEDDING_FIELD, "") or "")
            weights: Counter[str] = Counter()
            for token in name_tokens:
                weights[token] += self.NAME_WEIGHT
            for token in body_tokens:
                weights[token] += 1.0
            self._documents[skill["_key"]] = weights

        total = max(len(self._documents), 1)
        appearances: Counter[str] = Counter()
        for weights in self._documents.values():
            appearances.update(set(weights))
        self._idf = {
            token: math.log((total + 1) / (count + 0.5)) + 1.0
            for token, count in appearances.items()
        }

    @property
    def method(self) -> str:
        return METHOD_LEXICAL

    def score(self, command: str) -> list[Candidate]:
        query = Counter(tokenize(command))
        if not query:
            return []

        results: list[Candidate] = []
        query_norm = math.sqrt(
            sum((count * self._idf.get(token, 1.0)) ** 2 for token, count in query.items())
        )

        for skill in self._skills:
            weights = self._documents[skill["_key"]]
            document_norm = math.sqrt(
                sum((weight * self._idf.get(token, 1.0)) ** 2 for token, weight in weights.items())
            )
            if not document_norm or not query_norm:
                continue

            dot = 0.0
            for token, count in query.items():
                idf = self._idf.get(token, 1.0)
                weight = weights.get(token)
                if weight is None:
                    # Fall back to a fuzzy match so "plants" reaches
                    # "houseplants"; discounted, because it is a weaker signal.
                    weight = max(
                        (w for t, w in weights.items() if _fuzzy_equal(token, t)), default=0.0
                    ) * 0.75
                dot += count * idf * weight * idf

            results.append(
                Candidate(
                    skill=skill["name"],
                    key=skill["_key"],
                    scope=skill.get("skill_scope", ""),
                    score=max(0.0, min(1.0, dot / (query_norm * document_norm))),
                    description=skill.get(EMBEDDING_FIELD, "") or "",
                )
            )

        results.sort(key=lambda c: (-c.score, c.skill))
        return results


class VectorRetriever:
    """The Skills vector index Station 5 builds (FR-6).

    `available` is False when no vector index exists on the embedding field, which
    is the state Station 5 reports as `not_configured` or `pending`. The caller
    then falls back to lexical retrieval rather than failing.
    """

    def __init__(self, db: Any, schema: Schema, field_name: str = EMBEDDING_FIELD) -> None:
        self._db = db
        self._schema = schema
        self._field = field_name

    @property
    def method(self) -> str:
        return METHOD_VECTOR

    @property
    def available(self) -> bool:
        try:
            indexes = self._db.collection(self._schema.skills_collection).indexes()
        except Exception:
            return False
        return any(
            index.get("type") == "vector" and self._field in (index.get("fields") or [])
            for index in indexes
        )

    def score(self, command: str) -> list[Candidate]:  # pragma: no cover - needs a live index
        query = """
        FOR skill IN @@skills
          LET score = APPROX_NEAR_COSINE(skill[@field], @embedding)
          SORT score DESC
          LIMIT @limit
          RETURN {skill, score}
        """
        rows = self._db.aql.execute(
            query,
            bind_vars={
                "@skills": self._schema.skills_collection,
                "field": self._field,
                "embedding": self._embed(command),
                "limit": 25,
            },
        )
        return [
            Candidate(
                skill=row["skill"]["name"],
                key=row["skill"]["_key"],
                scope=row["skill"].get("skill_scope", ""),
                score=float(row["score"]),
                description=row["skill"].get(self._field, "") or "",
            )
            for row in rows
        ]

    def _embed(self, command: str) -> list[float]:  # pragma: no cover
        raise NotImplementedError(
            "embedding the command requires AutoGraph's embedding endpoint; "
            "configure AUTOGRAPH_URL and build the Skills index first"
        )


def load_skills(db: Any, schema: Schema) -> list[dict[str, Any]]:
    """Every Skill vertex, read-only (FR-8)."""
    if not db.has_collection(schema.skills_collection):
        return []
    return list(
        db.aql.execute(
            "FOR s IN @@skills SORT s._key RETURN s",
            bind_vars={"@skills": schema.skills_collection},
        )
    )


def resolve_goal(
    command: str,
    db: Any,
    schema: Schema,
    threshold: float,
    top_k: int,
    tie_margin: float,
    retriever: Any = None,
) -> GoalResolution:
    """Map a command to one skill, or ask for clarification (FR-2)."""
    if retriever is None:
        vector = VectorRetriever(db, schema)
        retriever = vector if vector.available else LexicalRetriever(load_skills(db, schema))

    candidates = retriever.score(command)
    top = candidates[: max(top_k, 1)]

    if not candidates:
        return GoalResolution(
            command=command,
            method=retriever.method,
            candidates=[],
            reason=(
                "no skills are present in the PlanGraph, so there is nothing to plan. "
                "Run the bridge (Stations 1-5) for at least one rulebook first."
            ),
        )

    best = candidates[0]
    if best.score < threshold:
        return GoalResolution(
            command=command,
            method=retriever.method,
            candidates=top,
            reason=(
                f"the best match {best.skill!r} scored {best.score:.2f}, below the "
                f"threshold of {threshold:.2f}. Rather than plan the wrong task, "
                "here are the closest skills - which did you mean?"
            ),
        )

    runner_up = candidates[1] if len(candidates) > 1 else None
    if runner_up is not None and (best.score - runner_up.score) < tie_margin:
        return GoalResolution(
            command=command,
            method=retriever.method,
            candidates=top,
            ambiguous=True,
            reason=(
                f"{best.skill!r} ({best.score:.2f}) and {runner_up.skill!r} "
                f"({runner_up.score:.2f}) are within {tie_margin:.2f} of each other, "
                "so the command does not pick one out. Which did you mean?"
            ),
        )

    return GoalResolution(command=command, method=retriever.method, goal=best, candidates=top)
