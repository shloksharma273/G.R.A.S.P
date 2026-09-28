"""The JSON API behind the web UI — a thin shell over Layer 2.

Commands go through the task decomposer in front of it, so "go to 2,1 and then
charge the robot" answers as one merged run of two plans; a one-task command
answers exactly as Layer 2 alone would.

Deliberately thin. Every decision the frontend displays is already made by the
planner: the goal, the ordering, the clarification. This module connects once,
keeps the retriever warm, and serializes; it holds no planning logic of its own,
so the browser and the CLI cannot drift apart in what they answer.

Read-only, like the rest of Layer 2 (FR-8).
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Any

from layer2_planning.config import PlannerConfig
from layer2_planning.order import CyclicPlan
from layer2_planning.plan import Clarification, Plan
from layer2_planning.retrieve import LexicalRetriever, VectorRetriever, load_skills
from layer2_planning.traverse import IncompletePlanGraph
from task_decomposition.compound import CompoundPlan, plan_compound


class PlannerService:
    """One connected session, shared by every request.

    The skill list and the retriever are built once. Re-reading them per request
    would put the whole latency budget into work that never changes between
    questions — the same reason the interactive shell keeps them warm.
    """

    def __init__(self, db: Any, config: PlannerConfig, provider: Any = None) -> None:
        self.db = db
        self.config = config
        self.provider = provider
        self._skills = load_skills(db, config.schema)
        vector = VectorRetriever(db, config.schema)
        self.retriever = vector if vector.available else LexicalRetriever(self._skills)

    # --- reads --------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return {
            "database": self.config.arango.database,
            "graph": self.config.schema.graph_name,
            "skills": len(self._skills),
            "retrieval": self.retriever.method,
            "vector_index": self.retriever.method == "vector",
            "phrasing": "model" if self.config.use_llm else "templates",
            "model": self.config.llm.model if (self.config.use_llm and self.config.llm) else None,
            "threshold": self.config.threshold,
        }

    def skills(self) -> list[dict[str, Any]]:
        """Every skill, with how many steps it decomposes into."""
        schema = self.config.schema
        rows = []
        for skill in sorted(self._skills, key=lambda s: s["name"]):
            edges = self.db.aql.execute(
                "FOR e IN @@edges FILTER e.skill_scope == @scope RETURN e",
                bind_vars={"@edges": schema.edge_collection, "scope": skill["skill_scope"]},
            )
            edges = list(edges)
            rows.append(
                {
                    "name": skill["name"],
                    "scope": skill.get("skill_scope", ""),
                    "steps": sum(1 for e in edges if e.get("type") == "decomposes_to"),
                    "description": (skill.get("description") or "")[:220],
                }
            )
        return rows

    # --- the one write-shaped thing, which writes nothing --------------------

    def plan(self, command: str, use_llm: bool | None = None) -> dict[str, Any]:
        """Answer one command: a plan, a clarification, or a stated failure."""
        command = (command or "").strip()
        if not command:
            return {"kind": "error", "message": "Type a command first."}

        config = self.config
        if use_llm is not None and use_llm != config.use_llm:
            # The toggle only ever turns phrasing off, or back on when a key
            # exists. Step order is identical either way.
            config = dataclasses.replace(
                config, use_llm=use_llm and config.llm is not None
            )

        try:
            # Through the task decomposer: a one-task command comes back as the
            # same Plan or Clarification as before, a compound one as several.
            result = plan_compound(
                command,
                self.db,
                config,
                provider=self.provider,
                retriever=self.retriever,
                skills=self._skills,
            )
        except IncompletePlanGraph as error:
            return {"kind": "error", "message": str(error), "code": "incomplete_plangraph"}
        except CyclicPlan as error:
            return {"kind": "error", "message": str(error), "code": "cycle"}
        except Exception as error:  # a broken request must not take the server down
            return {
                "kind": "error",
                "message": f"{error.__class__.__name__}: {error}",
                "code": "unexpected",
            }

        if isinstance(result, Clarification):
            payload = result.to_dict()
            payload["kind"] = "clarification"
            return payload

        if isinstance(result, CompoundPlan):
            payload = result.to_dict()
            payload["kind"] = "compound"
            return payload

        assert isinstance(result, Plan)
        payload = result.to_dict()
        payload["kind"] = "plan"
        return payload


class PlannerRegistry:
    """One `PlannerService` per project, built on first use and cached.

    The CLI plans against the project in `PROJECT_NAME`, which is right for a
    command you run with one build in mind. The UI cannot: a project is chosen in
    the browser, after the server started, and a database holds several.

    Caching matters for the same reason `PlannerService` keeps its retriever warm
    - loading the skills and building the retriever is work that never changes
    between questions. It also has to be *invalidated*, because a build rewrites
    exactly what the cache holds: a service kept across a rebuild would answer
    from the subgraph that build replaced.
    """

    def __init__(self, db: Any, base: PlannerConfig, provider: Any = None) -> None:
        self.db = db
        self.base = base
        self.provider = provider
        self._services: dict[str, PlannerService] = {}
        self._lock = threading.Lock()

    def config_for(self, project: str) -> PlannerConfig:
        return dataclasses.replace(self.base, prefix=project)

    def for_project(self, project: str) -> PlannerService:
        name = (project or "").strip() or self.base.prefix
        with self._lock:
            service = self._services.get(name)
            if service is None:
                service = PlannerService(self.db, self.config_for(name), self.provider)
                self._services[name] = service
            return service

    def invalidate(self, project: str = "") -> None:
        """Forget one project's service, or all of them after a build."""
        with self._lock:
            if project:
                self._services.pop(project.strip(), None)
            else:
                self._services.clear()

    def cached(self) -> list[str]:
        with self._lock:
            return sorted(self._services)
