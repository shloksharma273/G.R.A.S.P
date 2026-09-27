"""Turn Station 4's output into PlanGraph documents (PRD Sections 5, 6, 7).

Pure: no database, no network, no clock. Everything Station 5 intends to write is
decided here and handed to `writer.py` as a plan, which is what makes the
destructive half of this station testable offline and inspectable with --dry-run
before anything is touched.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable

from kg_read_harness.errors import ConfigError
from rule_preclassifier.detect import canonical_type, normalize_name
from rule_preclassifier.table import DECOMPOSES_TO

from .identity import build_id as make_build_id
from .identity import edge_key, scope_key, vertex_key
from .records import METHOD_DERIVED, PlanEdge, PlanGraphBuild, PlanVertex
from .schema import Schema, endpoint_types


def build(
    finalized: Iterable[Any],
    derived: Iterable[Any],
    schema: Schema,
    skill_scope: str | None = None,
    build_id: str | None = None,
) -> PlanGraphBuild:
    """Plan the write. Nothing here touches the database.

    Args:
        finalized: Station 4's `FinalizedEdge` records (or their JSON form).
        derived: Station 4's `DerivedEdge` records (or their JSON form).
        schema: the configured collection names.
        skill_scope: the task scope; inferred from the input's single skill when
            omitted (Section 5).
        build_id: overrides the deterministic content hash.
    """
    finalized_rows = [_row(edge) for edge in finalized]
    derived_rows = [_row(edge) for edge in derived]
    rows = finalized_rows + derived_rows

    scope = skill_scope or infer_scope(finalized_rows)
    identifier = build_id or make_build_id(_fingerprint(rows, scope))

    plan = PlanGraphBuild(skill_scope=scope, build_id=identifier)
    vertices: dict[str, PlanVertex] = {}
    descriptions: defaultdict[str, list[str]] = defaultdict(list)
    seen_edges: set[str] = set()

    for row in rows:
        edge_type = row["edge_type"]
        try:
            head_type, tail_type = endpoint_types(edge_type)
        except ValueError as error:
            plan.rejected.append((edge_type, str(error)))
            continue

        head_name, tail_name = row["from"], row["to"]
        if not head_name or not tail_name:
            plan.rejected.append((edge_type, "an endpoint has no name"))
            continue

        head = _vertex(vertices, scope, identifier, head_name, head_type, row)
        tail = _vertex(vertices, scope, identifier, tail_name, tail_type, row)

        # A skill's description is the text of the edges that decompose it —
        # the only account of the task the bridge carries this far, and what the
        # vector index has to match "make me a chai" against (FR-6).
        if edge_type == DECOMPOSES_TO and row.get("evidence"):
            descriptions[head.key].append(row["evidence"])

        key = edge_key(scope, head.key, tail.key, edge_type)
        if key in seen_edges:
            continue  # a rebuild must not accumulate duplicates (FR-4)
        seen_edges.add(key)

        plan.edges.append(
            PlanEdge(
                key=key,
                edge_type=edge_type,
                from_key=head.key,
                to_key=tail.key,
                from_collection=schema.vertex_collection(head_type),
                to_collection=schema.vertex_collection(tail_type),
                skill_scope=scope,
                build_id=identifier,
                method=row["method"],
                confidence=row.get("confidence"),
                evidence=row.get("evidence", ""),
                src_relation=row.get("src_relation"),
                via_state=row.get("via_state"),
                source_relation_keys=tuple(row.get("source_relation_keys") or ()),
                direction_method=row.get("direction_method"),
            )
        )

    for key, texts in descriptions.items():
        vertex = vertices[key]
        vertices[key] = PlanVertex(
            key=vertex.key,
            name=vertex.name,
            entity_type=vertex.entity_type,
            skill_scope=vertex.skill_scope,
            build_id=vertex.build_id,
            description=_skill_description(vertex.name, texts),
            inferred=vertex.inferred,
        )

    plan.vertices = sorted(vertices.values(), key=lambda v: (v.entity_type, v.key))
    plan.edges.sort(key=lambda e: (e.edge_type, e.from_key, e.to_key))
    return plan


def infer_scope(finalized_rows: list[dict[str, Any]]) -> str:
    """The task scope, from the single skill the input decomposes (Section 5)."""
    skills = sorted(
        {row["from"] for row in finalized_rows if row["edge_type"] == DECOMPOSES_TO}
    )
    if len(skills) == 1:
        return scope_key(skills[0])
    if not skills:
        raise ConfigError(
            "cannot infer a skill scope: the input contains no decomposes_to edge.",
            "set SKILL_SCOPE explicitly, so the task's subgraph is named and can be "
            "rebuilt without touching other tasks.",
        )
    raise ConfigError(
        f"the input contains {len(skills)} skills ({', '.join(skills)}), so the "
        "task scope is ambiguous.",
        "set SKILL_SCOPE explicitly, or run the bridge once per rulebook. Scoping "
        "is what keeps one recipe's edges out of another's plan.",
    )


def _vertex(
    vertices: dict[str, PlanVertex],
    scope: str,
    identifier: str,
    name: str,
    entity_type: str,
    row: dict[str, Any],
) -> PlanVertex:
    """Upsert a vertex into the plan, creating it on demand (Section 11)."""
    key = vertex_key(scope, name)
    existing = vertices.get(key)
    if existing is not None:
        return existing
    vertex = PlanVertex(
        key=key,
        name=name,
        entity_type=entity_type,
        skill_scope=scope,
        build_id=identifier,
        # Every vertex here comes from edge metadata: Station 4 hands over edges,
        # not a vertex list, so "created on demand" is the normal path, not an
        # exception. Flagged when the endpoint types disagreed with the edge type.
        inferred=_disagrees(row, name, entity_type),
    )
    vertices[key] = vertex
    return vertex


def _disagrees(row: dict[str, Any], name: str, entity_type: str) -> bool:
    """Whether the upstream bundle typed this endpoint as something else."""
    for end in ("source", "target"):
        endpoint = row.get(end) or {}
        if normalize_name(endpoint.get("name", "")) == normalize_name(name):
            declared = canonical_type(endpoint.get("type"))
            return declared is not None and declared != entity_type
    return False


#: The rulebook's "It is executed by ... `name` of type `type`." sentence.
_EXECUTION_SENTENCE = re.compile(r"\s*\bIt is executed by [^`]*`[^`]*`(?:\s+of type\s+`[^`]*`)?\.?")


def _skill_description(name: str, texts: list[str]) -> str:
    """The text the vector index embeds for goal resolution (FR-6).

    The skill's own name first — a command names the task, so it must be in the
    embedded text — then the decomposition evidence, deduplicated and in a stable
    order so the same input embeds the same string.
    """
    readable = name.replace("_", " ").strip()
    # A step's execution handle rides on the same evidence, but it says how a
    # step runs, not what the task is; embedded here it only dilutes the match.
    texts = [_EXECUTION_SENTENCE.sub("", text) for text in texts]
    unique = list(dict.fromkeys(text.strip() for text in texts if text.strip()))
    return " ".join([readable, *unique])


def _row(edge: Any) -> dict[str, Any]:
    """One Station 4 record, as a plain dict, whatever shape it arrived in."""
    if isinstance(edge, dict):
        row = dict(edge)
    else:
        row = edge.to_dict()

    method = row.get("method")
    if method is None:
        # Station 4's finalized edges keep the upstream method under its own
        # name; derived edges have no upstream and are their own method.
        method = row.get("upstream_method") or (
            METHOD_DERIVED if row.get("stream") == "derived" else "rule"
        )
    row["method"] = method
    row.setdefault("evidence", row.get("description", ""))
    row.setdefault("src_relation", row.get("relation_key"))
    row.setdefault("from", row.get("head"))
    row.setdefault("to", row.get("tail"))
    return row


def _fingerprint(rows: list[dict[str, Any]], scope: str) -> str:
    """A stable digest of what this build would write (see `identity.build_id`)."""
    payload = sorted(
        json.dumps(
            {
                "type": row.get("edge_type"),
                "from": row.get("from"),
                "to": row.get("to"),
                "method": row.get("method"),
                "evidence": row.get("evidence"),
            },
            sort_keys=True,
        )
        for row in rows
    )
    return scope + "|" + "|".join(payload)
