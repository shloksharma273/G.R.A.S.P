"""The documents Station 5 writes (PRD Sections 4, 7).

Every vertex and edge carries `skill_scope` and `build_id`; every edge also
carries the provenance Section 7 lists — method, confidence, the original
RELATED_TO description as evidence, the source relation key, and for a derived
edge the state and edges it was chained from. The point is that any element of
the planning graph can be walked back to the sentence that caused it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from .schema import Schema

#: How an edge came to be (Section 7).
METHOD_RULE = "rule"
METHOD_LEXICAL = "lexical"
METHOD_LLM = "llm"
METHOD_DERIVED = "derived"


@dataclass(frozen=True)
class PlanVertex:
    """One Skill / Primitive / Object / State."""

    key: str
    name: str
    entity_type: str
    skill_scope: str
    build_id: str
    description: str = ""
    #: True when the vertex had to be created from edge metadata alone
    #: (Section 11, "endpoint vertex missing at edge write").
    inferred: bool = False

    def collection(self, schema: Schema) -> str:
        return schema.vertex_collection(self.entity_type)

    def to_document(self) -> dict[str, Any]:
        return {
            "_key": self.key,
            "name": self.name,
            "type": self.entity_type,
            "skill_scope": self.skill_scope,
            "build_id": self.build_id,
            "description": self.description,
        }


@dataclass(frozen=True)
class PlanEdge:
    """One typed, directed planning edge."""

    key: str
    edge_type: str
    from_key: str
    to_key: str
    from_collection: str
    to_collection: str
    skill_scope: str
    build_id: str
    method: str
    confidence: float | None = None
    evidence: str = ""
    src_relation: str | None = None
    via_state: str | None = None
    source_relation_keys: tuple[str, ...] = ()
    direction_method: str | None = None

    @property
    def from_id(self) -> str:
        return f"{self.from_collection}/{self.from_key}"

    @property
    def to_id(self) -> str:
        return f"{self.to_collection}/{self.to_key}"

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "_key": self.key,
            "_from": self.from_id,
            "_to": self.to_id,
            "type": self.edge_type,
            "skill_scope": self.skill_scope,
            "build_id": self.build_id,
            "method": self.method,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "src_relation": self.src_relation,
            "direction_method": self.direction_method,
        }
        if self.via_state is not None:
            document["via_state"] = self.via_state
        if self.source_relation_keys:
            document["source_relation_keys"] = list(self.source_relation_keys)
        return document


@dataclass
class PlanGraphBuild:
    """Everything one run intends to write, before any I/O happens."""

    skill_scope: str = ""
    build_id: str = ""
    vertices: list[PlanVertex] = field(default_factory=list)
    edges: list[PlanEdge] = field(default_factory=list)
    #: Edges Station 4 handed over that this schema has no home for.
    rejected: list[tuple[str, str]] = field(default_factory=list)

    def counts_by_vertex_type(self) -> Counter[str]:
        return Counter(vertex.entity_type for vertex in self.vertices)

    def counts_by_edge_type(self) -> Counter[str]:
        return Counter(edge.edge_type for edge in self.edges)

    def counts_by_method(self) -> Counter[str]:
        return Counter(edge.method for edge in self.edges)

    def inferred_vertices(self) -> list[PlanVertex]:
        return [vertex for vertex in self.vertices if vertex.inferred]

    def documents_by_collection(self, schema: Schema) -> dict[str, list[dict[str, Any]]]:
        """The write plan, grouped the way it will be sent."""
        grouped: dict[str, list[dict[str, Any]]] = {
            name: [] for name in schema.all_collections
        }
        for vertex in self.vertices:
            grouped[vertex.collection(schema)].append(vertex.to_document())
        for edge in self.edges:
            grouped[schema.edge_collection].append(edge.to_document())
        return grouped

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_scope": self.skill_scope,
            "build_id": self.build_id,
            "vertices": len(self.vertices),
            "edges": len(self.edges),
            "by_vertex_type": dict(self.counts_by_vertex_type()),
            "by_edge_type": dict(self.counts_by_edge_type()),
            "by_method": dict(self.counts_by_method()),
            "inferred_vertices": [v.name for v in self.inferred_vertices()],
            "rejected": [{"edge_type": t, "reason": r} for t, r in self.rejected],
        }


@dataclass
class WriteReport:
    """What actually happened at the database (FR-7)."""

    schema_created: list[str] = field(default_factory=list)
    graph_created: bool = False
    indexes_created: list[str] = field(default_factory=list)
    purged: dict[str, int] = field(default_factory=dict)
    written: dict[str, int] = field(default_factory=dict)
    verified: dict[str, int] = field(default_factory=dict)
    vector_index: str = "pending"
    vector_index_detail: str = ""
    dry_run: bool = False
    transactional: bool = False

    @property
    def purged_total(self) -> int:
        return sum(self.purged.values())

    @property
    def written_total(self) -> int:
        return sum(self.written.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "transactional": self.transactional,
            "schema_created": self.schema_created,
            "graph_created": self.graph_created,
            "indexes_created": self.indexes_created,
            "purged": self.purged,
            "purged_total": self.purged_total,
            "written": self.written,
            "written_total": self.written_total,
            "verified": self.verified,
            "vector_index": self.vector_index,
            "vector_index_detail": self.vector_index_detail,
        }


def iter_documents(build: PlanGraphBuild, schema: Schema) -> Iterable[tuple[str, dict[str, Any]]]:
    for vertex in build.vertices:
        yield vertex.collection(schema), vertex.to_document()
    for edge in build.edges:
        yield schema.edge_collection, edge.to_document()
