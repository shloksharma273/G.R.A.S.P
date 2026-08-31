"""The write pipeline (PRD Section 6) — the only part of the bridge that mutates.

Five steps: ensure the schema, purge this task's scope, upsert vertices, upsert
edges, verify. Every one of them goes through `GuardedDatabase`, so the isolation
guarantee of FR-8 is enforced at a single choke point rather than by convention.

The purge is the one genuinely destructive act in the project. It is scoped to a
single `skill_scope` (FR-5), runs inside a transaction where the deployment
supports one so a task is never left half-written (Section 10), and is skipped
entirely under `--dry-run`.
"""

from __future__ import annotations

from typing import Any

from .config import WriterConfig
from .embed import ensure_vector_index
from .guard import GuardedDatabase
from .records import PlanGraphBuild, WriteReport
from .schema import EDGE_INDEX_FIELDS, VERTEX_INDEX_FIELDS, Schema

#: Documents per request. Keeps a large rulebook off one enormous body.
BATCH_SIZE = 500


def write_plangraph(
    plan: PlanGraphBuild,
    db: Any,
    config: WriterConfig,
) -> WriteReport:
    """Persist one task's subgraph. Returns what actually happened (FR-7)."""
    schema = config.schema
    guard = GuardedDatabase(db, schema)
    report = WriteReport(dry_run=config.dry_run)

    ensure_schema(guard, schema, report, dry_run=config.dry_run)

    if config.dry_run:
        _plan_only(plan, guard, schema, report)
    else:
        _apply(plan, guard, schema, report)

    verify(guard, schema, plan, report)
    ensure_vector_index(config, report, dry_run=config.dry_run)
    return report


# --------------------------------------------------------------------------
# 1 - ensure schema (FR-1)
# --------------------------------------------------------------------------


def ensure_schema(
    guard: GuardedDatabase, schema: Schema, report: WriteReport, dry_run: bool = False
) -> None:
    """Create collections, graph and indexes only if absent; never error on re-run."""
    for name in schema.vertex_collections:
        if not guard.has_collection(name):
            report.schema_created.append(name)
            if not dry_run:
                guard.create_collection(name, edge=False)

    if not guard.has_collection(schema.edge_collection):
        report.schema_created.append(schema.edge_collection)
        if not dry_run:
            guard.create_collection(schema.edge_collection, edge=True)

    if not guard.has_graph(schema.graph_name):
        report.graph_created = True
        if not dry_run:
            guard.create_graph(schema.graph_name, [schema.edge_definition()])

    if not dry_run:
        _ensure_indexes(guard, schema, report)


def _ensure_indexes(guard: GuardedDatabase, schema: Schema, report: WriteReport) -> None:
    wanted = [(name, fields) for name in schema.vertex_collections for fields in VERTEX_INDEX_FIELDS]
    wanted += [(schema.edge_collection, fields) for fields in EDGE_INDEX_FIELDS]

    for collection, fields in wanted:
        existing = {
            tuple(index.get("fields", ())) for index in guard.indexes(collection)
        }
        if tuple(fields) not in existing:
            guard.add_persistent_index(collection, fields)
            report.indexes_created.append(f"{collection}({', '.join(fields)})")


# --------------------------------------------------------------------------
# 2-4 - purge, then upsert (FR-3, FR-4, FR-5)
# --------------------------------------------------------------------------


def _apply(
    plan: PlanGraphBuild, guard: GuardedDatabase, schema: Schema, report: WriteReport
) -> None:
    """Scoped rebuild inside a transaction where one is available."""
    transaction = guard.begin()
    report.transactional = transaction is not None
    target = transaction or guard

    try:
        _purge_scope(target, schema, plan.skill_scope, report)
        _upsert(plan, target, schema, report)
    except BaseException:
        if transaction is not None:
            transaction.abort()
        raise
    else:
        if transaction is not None:
            transaction.commit()


def _purge_scope(
    guard: GuardedDatabase, schema: Schema, skill_scope: str, report: WriteReport
) -> None:
    """Delete this task's existing subgraph so no stale edge survives (FR-5).

    Edges first: removing a vertex whose edges still point at it would leave
    dangling references for as long as the transaction is open.
    """
    for collection in (schema.edge_collection, *schema.vertex_collections):
        removed = guard.delete_scope(collection, skill_scope)
        if removed:
            report.purged[collection] = removed


def _upsert(
    plan: PlanGraphBuild, guard: GuardedDatabase, schema: Schema, report: WriteReport
) -> None:
    """Write vertices, then edges — endpoints must exist before an edge names them."""
    for collection, documents in plan.documents_by_collection(schema).items():
        if collection == schema.edge_collection:
            continue
        written = _write_batched(guard, collection, documents)
        if written:
            report.written[collection] = written

    edges = plan.documents_by_collection(schema)[schema.edge_collection]
    written = _write_batched(guard, schema.edge_collection, edges)
    if written:
        report.written[schema.edge_collection] = written


def _write_batched(guard: GuardedDatabase, collection: str, documents: list[dict]) -> int:
    total = 0
    for start in range(0, len(documents), BATCH_SIZE):
        total += guard.insert_many(collection, documents[start : start + BATCH_SIZE])
    return total


def _plan_only(
    plan: PlanGraphBuild, guard: GuardedDatabase, schema: Schema, report: WriteReport
) -> None:
    """Under --dry-run, record what would happen and touch nothing."""
    for collection, documents in plan.documents_by_collection(schema).items():
        if documents:
            report.written[collection] = len(documents)
        if guard.has_collection(collection):
            existing = guard.aql(
                "FOR doc IN @@collection FILTER doc.skill_scope == @scope "
                "COLLECT WITH COUNT INTO n RETURN n",
                {"@collection": collection, "scope": plan.skill_scope},
            )
            count = int(existing[0]) if existing else 0
            if count:
                report.purged[collection] = count


# --------------------------------------------------------------------------
# 5 - verify (FR-7)
# --------------------------------------------------------------------------


def verify(
    guard: GuardedDatabase, schema: Schema, plan: PlanGraphBuild, report: WriteReport
) -> None:
    """Read back counts for this scope and confirm the named graph exists."""
    for collection in schema.all_collections:
        if not guard.has_collection(collection):
            continue
        rows = guard.aql(
            "FOR doc IN @@collection FILTER doc.skill_scope == @scope "
            "COLLECT WITH COUNT INTO n RETURN n",
            {"@collection": collection, "scope": plan.skill_scope},
        )
        report.verified[collection] = int(rows[0]) if rows else 0
    report.verified["_graph"] = 1 if guard.has_graph(schema.graph_name) else 0
