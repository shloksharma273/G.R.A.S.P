"""The isolation guard (FR-8) — Station 5 writes to the PlanGraph and nowhere else.

Station 1's guard was the mirror of this one: it refused every write. Station 5
has to write, so the guarantee is narrowed rather than dropped — every mutating
call names a collection, and only the five PlanGraph collections are permitted.

It is an **allowlist, not a denylist**. The PRD only asks that AutoGraph's
`{project}_kg` never be modified, but the live database is shared: it holds
`AIS-1847_test_*`, `E2E_test_*`, `api_test_project_*` and more beside our own.
Enumerating what must not be touched would be a list that silently goes stale;
enumerating what may be touched cannot.

`writer.py` reaches the database only through `GuardedDatabase`, so there is one
place to audit rather than a convention to remember.
"""

from __future__ import annotations

from typing import Any, Iterable

from kg_read_harness.errors import HarnessError

from .schema import Schema

#: AQL operations that mutate. A write query naming a collection outside the
#: allowlist is refused before it reaches the server.
WRITE_OPERATIONS = ("INSERT", "UPDATE", "REPLACE", "REMOVE", "UPSERT", "TRUNCATE")


class IsolationViolation(HarnessError):
    """Station 5 tried to touch a collection it does not own.

    Never expected to fire. It exists so the single hard constraint of this
    station is enforced by code rather than by intent — the same reason Station 1
    has a read-only guard.
    """

    exit_code = 8
    label = "isolation guard tripped"


class GuardedDatabase:
    """A database handle that can only mutate the PlanGraph's own collections."""

    def __init__(self, db: Any, schema: Schema) -> None:
        self._db = db
        self._schema = schema
        self._allowed = frozenset(schema.all_collections)
        self.writes: list[tuple[str, str, int]] = []

    # --- the allowlist ----------------------------------------------------

    @property
    def allowed(self) -> frozenset[str]:
        return self._allowed

    def check(self, collection: str, operation: str = "write") -> None:
        """Raise unless `collection` is one of ours."""
        if collection not in self._allowed:
            raise IsolationViolation(
                f"refusing to {operation} {collection!r}: it is not a PlanGraph "
                f"collection.",
                "Station 5 may only write to "
                + ", ".join(sorted(self._allowed))
                + ". This is a harness bug, not a configuration problem: the "
                "PlanGraph is isolated from AutoGraph's collections by contract.",
            )

    def check_query(self, query: str, bind_vars: dict[str, Any] | None = None) -> None:
        """Raise if a mutating query names a collection outside the allowlist."""
        upper = query.upper()
        if not any(operation in upper for operation in WRITE_OPERATIONS):
            return
        for name, value in (bind_vars or {}).items():
            if name.startswith("@") and isinstance(value, str):
                self.check(value, "run a write query against")

    # --- read surface (unrestricted; reading is always safe) ---------------

    def has_collection(self, name: str) -> bool:
        return bool(self._db.has_collection(name))

    def has_graph(self, name: str) -> bool:
        return any(graph["name"] == name for graph in self._db.graphs())

    def count(self, collection: str) -> int:
        return int(self._db.collection(collection).count())

    def indexes(self, collection: str) -> list[dict[str, Any]]:
        return list(self._db.collection(collection).indexes())

    def aql(self, query: str, bind_vars: dict[str, Any] | None = None) -> list[Any]:
        self.check_query(query, bind_vars)
        cursor = self._db.aql.execute(query, bind_vars=dict(bind_vars or {}))
        return list(cursor)

    # --- write surface (allowlisted) --------------------------------------

    def create_collection(self, name: str, edge: bool = False) -> None:
        self.check(name, "create")
        self._db.create_collection(name, edge=edge)
        self.writes.append((name, "create_collection", 1))

    def create_graph(self, name: str, edge_definitions: list[dict]) -> None:
        if name != self._schema.graph_name:
            raise IsolationViolation(
                f"refusing to create graph {name!r}: Station 5 owns only "
                f"{self._schema.graph_name!r}.",
                "this is a harness bug; the PlanGraph's name comes from configuration.",
            )
        for definition in edge_definitions:
            self.check(definition["edge_collection"], "bind into a graph")
            for collection in definition["from_vertex_collections"]:
                self.check(collection, "bind into a graph")
            for collection in definition["to_vertex_collections"]:
                self.check(collection, "bind into a graph")
        self._db.create_graph(name, edge_definitions=edge_definitions)
        self.writes.append((name, "create_graph", 1))

    def add_persistent_index(self, collection: str, fields: list[str]) -> None:
        self.check(collection, "index")
        self._db.collection(collection).add_persistent_index(fields=fields, sparse=False)
        self.writes.append((collection, "add_index", 1))

    def insert_many(self, collection: str, documents: list[dict[str, Any]]) -> int:
        """Upsert a batch. `overwrite_mode="update"` gives last-write-wins (Section 7)."""
        self.check(collection, "write to")
        if not documents:
            return 0
        result = self._db.collection(collection).insert_many(
            documents, overwrite_mode="update", merge=True, silent=False
        )
        errors = [item for item in (result or ()) if isinstance(item, Exception)]
        if errors:
            raise HarnessError(
                f"{len(errors)} document(s) failed to write to {collection!r}: {errors[0]}",
                "check that the database user has write access to the project database.",
            )
        self.writes.append((collection, "insert_many", len(documents)))
        return len(documents)

    def delete_scope(self, collection: str, skill_scope: str) -> int:
        """Remove one task's documents from one collection (FR-5)."""
        self.check(collection, "delete from")
        rows = self.aql(
            "FOR doc IN @@collection FILTER doc.skill_scope == @scope "
            "REMOVE doc IN @@collection RETURN 1",
            {"@collection": collection, "scope": skill_scope},
        )
        removed = len(rows)
        if removed:
            self.writes.append((collection, "delete_scope", removed))
        return removed

    # --- transactions ------------------------------------------------------

    def begin(self) -> "GuardedDatabase | None":
        """A transactional view over exactly our collections, or None if unsupported.

        Section 10 asks for "transactional per task: a scoped rebuild either
        completes or leaves the prior subgraph intact". Not every deployment
        supports stream transactions, so this degrades rather than failing.
        """
        begin_transaction = getattr(self._db, "begin_transaction", None)
        if begin_transaction is None:
            return None
        try:
            handle = begin_transaction(
                read=list(self._allowed), write=list(self._allowed), exclusive=[]
            )
        except Exception:
            return None
        guarded = GuardedDatabase(handle, self._schema)
        guarded._transaction = handle  # type: ignore[attr-defined]
        return guarded

    def commit(self) -> None:
        transaction = getattr(self, "_transaction", None)
        if transaction is not None:
            transaction.commit_transaction()

    def abort(self) -> None:
        transaction = getattr(self, "_transaction", None)
        if transaction is not None:
            try:
                transaction.abort_transaction()
            except Exception:  # pragma: no cover - already rolled back or gone
                pass


def forbidden_collections(db: Any, schema: Schema) -> Iterable[str]:
    """Every collection in the database Station 5 must not touch.

    Only used to make the guarantee visible in the run header; the guard itself
    never consults it, because an allowlist does not need to know what it excludes.
    """
    allowed = set(schema.all_collections)
    for collection in db.collections():
        name = collection["name"]
        if not name.startswith("_") and name not in allowed:
            yield name
