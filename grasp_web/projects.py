"""Discovering AutoGraph projects in the configured database.

The rest of the project is configured for **one** project through `PROJECT_NAME`,
which is right for a CLI you run against a build you already have in mind. A UI
whose whole job is "show me what is in this database and what stage it has
reached" cannot work that way: the projects are not known when the server starts,
because the point is that you go and make one.

So this reads them out of the database instead, and it needs no new convention to
do it. AutoGraph and the bridge already name their graphs after the stage:

    {project}_CorpusGraph    the corpus graph, built in the AutoGraph UI
    {project}_kg             the knowledge graph, built in the AutoGraph UI
    {project}_PlanGraph      the PlanGraph, built here

Those three suffixes are the state machine the UI shows, in the order you reach
them. A project is listed once its KG exists, because that is the first moment
this project has anything to offer; one that has only a corpus is listed as
waiting, rather than hidden, so an empty screen is never the only feedback for
"I built the wrong thing".

Read-only, like `kg_read_harness`. Nothing here writes; the build does, and it
does it through Station 5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The three named-graph suffixes, in the order a project reaches them.
CORPUS_SUFFIX = "_CorpusGraph"
KG_SUFFIX = "_kg"
PLAN_SUFFIX = "_PlanGraph"

#: Stages, in order. A project's stage is the furthest one it has reached.
STAGE_EMPTY = "empty"
STAGE_CORPUS = "corpus"
STAGE_KG = "kg"
STAGE_PLAN = "plangraph"

STAGE_ORDER = (STAGE_EMPTY, STAGE_CORPUS, STAGE_KG, STAGE_PLAN)

STAGE_LABEL = {
    STAGE_EMPTY: "nothing built yet",
    STAGE_CORPUS: "corpus graph built - build the knowledge graph in AutoGraph",
    STAGE_KG: "knowledge graph built - ready to build the PlanGraph",
    STAGE_PLAN: "PlanGraph built - ready for questions",
}


@dataclass
class Project:
    """One AutoGraph project and how far along it is."""

    name: str
    has_corpus: bool = False
    has_kg: bool = False
    has_plangraph: bool = False
    entities: int = 0
    relations: int = 0
    skills: int = 0
    plan_edges: int = 0
    scopes: list[str] = field(default_factory=list)
    #: Entity types found in the KG, and which of them the ontology recognises.
    known_types: dict[str, str] = field(default_factory=dict)
    unknown_types: list[str] = field(default_factory=list)

    @property
    def stage(self) -> str:
        if self.has_plangraph and self.plan_edges:
            return STAGE_PLAN
        if self.has_kg:
            return STAGE_KG
        if self.has_corpus:
            return STAGE_CORPUS
        return STAGE_EMPTY

    @property
    def roles(self) -> set[str]:
        """The ontology roles this KG actually fills."""
        return set(self.known_types.values())

    @property
    def buildable(self) -> bool:
        """Whether a build could succeed, not merely whether one can be started.

        A KG needs relationships *and* an ontology the bridge understands. Both
        halves are load-bearing in practice: one live project is an insurance
        graph whose types are `insurance_claim` and `adjuster`, and another names
        its types in the plural, which `canonical_type` does not resolve. Offering
        a Build button for either is offering a button that cannot work.
        """
        return (
            self.has_kg
            and self.relations > 0
            and {"SKILL", "PRIMITIVE"} <= self.roles
        )

    @property
    def blocked_reason(self) -> str:
        """Why this project cannot be built, in terms of what to go and fix."""
        if not self.has_kg:
            return "no knowledge graph yet - build one in AutoGraph first."
        if not self.relations:
            return "the knowledge graph holds no relationships yet."
        missing = {"SKILL", "PRIMITIVE"} - self.roles
        if missing:
            found = ", ".join(sorted(self.unknown_types)[:6]) or "none"
            return (
                "this knowledge graph has no "
                + " or ".join(sorted(missing))
                + f" entities, so nothing decomposes into steps. Types found: {found}."
                + (
                    " Note that the ontology is singular - `skill`, `primitive`,"
                    " `object`, `state`."
                    if any(t.lower().endswith("s") for t in self.unknown_types)
                    else ""
                )
            )
        return ""

    @property
    def plannable(self) -> bool:
        return self.stage == STAGE_PLAN and self.skills > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stage": self.stage,
            "stage_label": STAGE_LABEL[self.stage],
            "has_corpus": self.has_corpus,
            "has_kg": self.has_kg,
            "has_plangraph": self.has_plangraph,
            "entities": self.entities,
            "relations": self.relations,
            "skills": self.skills,
            "plan_edges": self.plan_edges,
            "scopes": list(self.scopes),
            "known_types": dict(self.known_types),
            "unknown_types": list(self.unknown_types),
            "buildable": self.buildable,
            "plannable": self.plannable,
            "blocked_reason": self.blocked_reason,
        }


def graph_url(base_url: str, database: str, graph: str) -> str:
    """A deep link into ArangoDB's own graph viewer.

    Uses the platform UI's route, ``<root>/ui/<database>/graphs/<graph>``, not
    the legacy aardvark fragment. Built from the configured endpoint rather than
    guessed, and returned to the browser to follow: the server never fetches it.
    """
    return f"{base_url.rstrip('/')}/ui/{database}/graphs/{graph}"


def _count(db: Any, name: str) -> int:
    try:
        if not db.has_collection(name):
            return 0
        return int(db.collection(name).count())
    except Exception:
        # A collection we cannot count is reported as empty rather than taking
        # the listing down: one unreadable project must not hide the others.
        return 0


def _scopes(db: Any, collection: str) -> list[str]:
    """The skill scopes a PlanGraph holds - one per task it can plan."""
    try:
        if not db.has_collection(collection):
            return []
        cursor = db.aql.execute(
            "FOR s IN @@skills FILTER s.skill_scope != null "
            "RETURN DISTINCT s.skill_scope",
            bind_vars={"@skills": collection},
        )
        return sorted(str(row) for row in cursor if row)
    except Exception:
        return []


def _entity_types(db: Any, collection: str, type_field: str) -> tuple[dict[str, str], list[str]]:
    """The KG's entity types, split into those the ontology knows and those it does not.

    One DISTINCT over the collection - the vocabulary is tiny even when the graph
    is not. Reported rather than judged: the page shows the raw values, because
    "your types are called `skills`, not `skill`" is a fixable thing to be told
    and "not buildable" on its own is not.
    """
    from rule_preclassifier.detect import canonical_type

    try:
        cursor = db.aql.execute(
            "FOR e IN @@entities RETURN DISTINCT e[@field]",
            bind_vars={"@entities": collection, "field": type_field},
        )
        raw = [str(row) for row in cursor if row]
    except Exception:
        return {}, []

    known: dict[str, str] = {}
    unknown: list[str] = []
    for value in raw:
        role = canonical_type(value)
        if role:
            known[value] = role
        else:
            unknown.append(value)
    return known, sorted(unknown)


def discover(db: Any, type_field: str = "entity_type") -> list[Project]:
    """Every project in this database, by the graphs it has.

    `type_field` is the attribute AutoGraph stores the entity type in, which is
    configurable for the same reason Station 1 makes it configurable: live builds
    do not all agree on it.
    """
    try:
        graphs = [str(g["name"]) for g in db.graphs()]
    except Exception:
        graphs = []

    found: dict[str, Project] = {}

    def project(name: str) -> Project:
        return found.setdefault(name, Project(name=name))

    for graph in graphs:
        if graph.startswith("_"):
            continue  # ArangoDB's own internal graphs
        if graph.endswith(CORPUS_SUFFIX):
            project(graph[: -len(CORPUS_SUFFIX)]).has_corpus = True
        elif graph.endswith(PLAN_SUFFIX):
            project(graph[: -len(PLAN_SUFFIX)]).has_plangraph = True
        elif graph.endswith(KG_SUFFIX):
            project(graph[: -len(KG_SUFFIX)]).has_kg = True

    for name, entry in found.items():
        entry.entities = _count(db, f"{name}_Entities")
        entry.relations = _count(db, f"{name}_Relations")
        entry.skills = _count(db, f"{name}_Skills")
        entry.plan_edges = _count(db, f"{name}_PlanEdges")
        if entry.skills:
            entry.scopes = _scopes(db, f"{name}_Skills")
        if entry.entities:
            entry.known_types, entry.unknown_types = _entity_types(
                db, f"{name}_Entities", type_field
            )
        # A KG graph can exist with nothing extracted into it yet; the counts are
        # what says whether there is anything to read.
        entry.has_kg = entry.has_kg or bool(entry.entities and entry.relations)

    # Furthest along first, then alphabetically: the projects you can act on are
    # the ones you came to the page for.
    return sorted(
        found.values(),
        key=lambda p: (-STAGE_ORDER.index(p.stage), p.name.lower()),
    )


def find(db: Any, name: str, type_field: str = "entity_type") -> Project | None:
    for entry in discover(db, type_field=type_field):
        if entry.name == name:
            return entry
    return None
