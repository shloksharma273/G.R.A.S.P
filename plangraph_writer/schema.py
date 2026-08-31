"""The PlanGraph schema (PRD Section 4) — collections, edge types, named graph.

Deliberately its own set of collections, separate from AutoGraph's
`{project}_kg`: AutoGraph owns and rebuilds its knowledge graph, and a rebuild
would wipe anything we stored there (Section 2).

Everything a schema change touches is here; `writer.py` only reads it.
"""

from __future__ import annotations

from dataclasses import dataclass

from rule_preclassifier.table import DECOMPOSES_TO, PRECEDES, PRODUCES, REQUIRES, USES

#: Logical vertex collections, by the ontology type each holds (Section 4).
VERTEX_COLLECTIONS = {
    "SKILL": "Skills",
    "PRIMITIVE": "Primitives",
    "OBJECT": "Objects",
    "STATE": "States",
}

#: One edge collection carrying a `type` attribute keeps traversal filtering
#: simple; the named graph binds the endpoint types (Section 4).
EDGE_COLLECTION = "PlanEdges"

#: The vertex type each edge type runs between. Used to route an endpoint to its
#: collection and to validate what Station 4 handed us.
EDGE_ENDPOINTS = {
    DECOMPOSES_TO: ("SKILL", "PRIMITIVE"),
    REQUIRES: ("PRIMITIVE", "STATE"),
    PRODUCES: ("PRIMITIVE", "STATE"),
    PRECEDES: ("PRIMITIVE", "PRIMITIVE"),
    USES: ("PRIMITIVE", "OBJECT"),
}

#: The field the vector index is built over (Section 4, FR-6).
EMBEDDING_COLLECTION = "SKILL"
EMBEDDING_FIELD = "description"

#: Persistent indexes for fast filtered traversal (Section 4).
VERTEX_INDEX_FIELDS = (["type"], ["skill_scope"])
EDGE_INDEX_FIELDS = (["type"], ["skill_scope"], ["skill_scope", "type"])


@dataclass(frozen=True)
class Schema:
    """Concrete collection and graph names for one configured project."""

    prefix: str

    def vertex_collection(self, entity_type: str) -> str:
        try:
            return f"{self.prefix}_{VERTEX_COLLECTIONS[entity_type]}"
        except KeyError:
            raise ValueError(
                f"{entity_type!r} is not a PlanGraph vertex type "
                f"(expected one of {', '.join(VERTEX_COLLECTIONS)})"
            ) from None

    @property
    def edge_collection(self) -> str:
        return f"{self.prefix}_{EDGE_COLLECTION}"

    @property
    def graph_name(self) -> str:
        return f"{self.prefix}_PlanGraph"

    @property
    def vertex_collections(self) -> tuple[str, ...]:
        return tuple(f"{self.prefix}_{name}" for name in VERTEX_COLLECTIONS.values())

    @property
    def all_collections(self) -> tuple[str, ...]:
        """Every collection Station 5 is allowed to touch. Nothing else, ever."""
        return self.vertex_collections + (self.edge_collection,)

    @property
    def skills_collection(self) -> str:
        return self.vertex_collection(EMBEDDING_COLLECTION)

    def edge_definition(self) -> dict:
        """The named graph's single edge definition (Section 4).

        One edge collection spanning every type, with the endpoint collections
        the five edge types actually use.
        """
        from_types = {source for source, _ in EDGE_ENDPOINTS.values()}
        to_types = {target for _, target in EDGE_ENDPOINTS.values()}
        return {
            "edge_collection": self.edge_collection,
            "from_vertex_collections": sorted(self.vertex_collection(t) for t in from_types),
            "to_vertex_collections": sorted(self.vertex_collection(t) for t in to_types),
        }


def endpoint_types(edge_type: str) -> tuple[str, str]:
    try:
        return EDGE_ENDPOINTS[edge_type]
    except KeyError:
        raise ValueError(
            f"{edge_type!r} is not a PlanGraph edge type "
            f"(expected one of {', '.join(EDGE_ENDPOINTS)})"
        ) from None
