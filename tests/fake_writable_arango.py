"""A writable in-memory stand-in for ArangoDB, for Station 5.

`tests/fake_arango.py` refuses every write, which is how Stations 1-4 prove they
never mutate. Station 5 has to write, so this double accepts writes — but it is
seeded with the *other* projects' collections that share the live pilot database,
and it raises `ForbiddenWrite` the moment anything touches one of them. That is
the test of FR-8: not that the guard says no, but that the database would notice
if it ever said yes.
"""

from __future__ import annotations

import re
from typing import Any

#: Collections that exist in the live shared database and are not ours. Any write
#: to one of these is a bug that must fail loudly in tests.
FOREIGN_COLLECTIONS = (
    "roboticsPlanner_Entities",
    "roboticsPlanner_Relations",
    "roboticsPlanner_Chunks",
    "roboticsPlanner_Communities",
    "AIS-1847_test_Entities",
    "AIS-1847_test_Relations",
    "E2E_test_similarities",
    "api_test_project_sources",
)

FOREIGN_GRAPHS = ("roboticsPlanner_kg", "roboticsPlanner_CorpusGraph", "AIS-1847_test_kg")


class ForbiddenWrite(AssertionError):
    """Station 5 reached a collection it does not own."""


class FakeCollection:
    def __init__(self, name: str, edge: bool, foreign: bool = False) -> None:
        self.name = name
        self.edge = edge
        self.foreign = foreign
        self.documents: dict[str, dict[str, Any]] = {}
        self._indexes: list[dict[str, Any]] = [{"type": "primary", "fields": ["_key"]}]

    def _guard(self) -> None:
        if self.foreign:
            raise ForbiddenWrite(
                f"Station 5 attempted to write to {self.name!r}, which it does not own"
            )

    def count(self) -> int:
        return len(self.documents)

    def properties(self) -> dict[str, Any]:
        return {"name": self.name, "edge": self.edge}

    def indexes(self) -> list[dict[str, Any]]:
        return list(self._indexes)

    def add_persistent_index(self, fields, **kwargs):
        self._guard()
        self._indexes.append({"type": "persistent", "fields": list(fields)})
        return self._indexes[-1]

    def insert_many(self, documents, overwrite_mode=None, merge=True, silent=False, **kwargs):
        self._guard()
        results = []
        for document in documents:
            key = document["_key"]
            if key in self.documents and overwrite_mode == "update" and merge:
                self.documents[key] = {**self.documents[key], **document}
            else:
                self.documents[key] = dict(document)
            self.documents[key]["_id"] = f"{self.name}/{key}"
            results.append({"_key": key})
        return results

    # Mutating methods Station 5 should never reach directly.
    def _refuse(self, *args, **kwargs):
        raise ForbiddenWrite(f"unexpected direct mutation of {self.name!r}")

    insert = update = replace = delete = truncate = _refuse


class FakeAql:
    def __init__(self, db: "FakeWritableDatabase") -> None:
        self._db = db

    def execute(self, query: str, bind_vars=None, **kwargs):
        return self._db._execute(query, dict(bind_vars or {}))


class FakeTransaction:
    """A stream transaction that buffers nothing but records commit/abort."""

    def __init__(self, db: "FakeWritableDatabase") -> None:
        self._db = db
        self.committed = False
        self.aborted = False
        self.aql = FakeAql(db)

    def __getattr__(self, name):
        return getattr(self._db, name)

    def commit_transaction(self) -> None:
        self.committed = True
        self._db.transactions_committed += 1

    def abort_transaction(self) -> None:
        self.aborted = True
        self._db.transactions_aborted += 1


class FakeWritableDatabase:
    def __init__(self, name: str = "test_shlok", supports_transactions: bool = True) -> None:
        self.name = name
        self._collections: dict[str, FakeCollection] = {}
        self._graphs: list[str] = []
        self.aql = FakeAql(self)
        self.queries: list[str] = []
        self.supports_transactions = supports_transactions
        self.transactions_committed = 0
        self.transactions_aborted = 0

        for foreign in FOREIGN_COLLECTIONS:
            self._collections[foreign] = FakeCollection(
                foreign, edge=foreign.endswith(("Relations", "similarities")), foreign=True
            )
            self._collections[foreign].documents = {"seed": {"_key": "seed"}}
        self._graphs.extend(FOREIGN_GRAPHS)

    # --- python-arango surface --------------------------------------------

    def has_collection(self, name: str) -> bool:
        return name in self._collections

    def collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            raise KeyError(f"no such collection: {name}")
        return self._collections[name]

    def collections(self) -> list[dict[str, Any]]:
        return [{"name": name} for name in sorted(self._collections)]

    def create_collection(self, name: str, edge: bool = False, **kwargs) -> FakeCollection:
        if name in FOREIGN_COLLECTIONS:
            raise ForbiddenWrite(f"Station 5 attempted to recreate {name!r}")
        self._collections[name] = FakeCollection(name, edge=edge)
        return self._collections[name]

    def graphs(self) -> list[dict[str, Any]]:
        return [{"name": name} for name in self._graphs]

    def create_graph(self, name: str, edge_definitions=None, **kwargs):
        if name in FOREIGN_GRAPHS:
            raise ForbiddenWrite(f"Station 5 attempted to recreate graph {name!r}")
        self._graphs.append(name)
        return {"name": name}

    def delete_graph(self, *args, **kwargs):
        raise ForbiddenWrite("Station 5 must never delete a graph")

    def delete_collection(self, *args, **kwargs):
        raise ForbiddenWrite("Station 5 must never drop a collection")

    def begin_transaction(self, read=None, write=None, exclusive=None, **kwargs):
        if not self.supports_transactions:
            raise RuntimeError("stream transactions are not supported here")
        for name in list(write or ()) + list(exclusive or ()):
            if name in FOREIGN_COLLECTIONS:
                raise ForbiddenWrite(f"transaction requested write access to {name!r}")
        return FakeTransaction(self)

    # --- the AQL Station 5 actually issues ---------------------------------

    _COUNT_SCOPE = re.compile(
        r"FOR doc IN @@collection\s+FILTER doc\.skill_scope == @scope\s+COLLECT WITH COUNT",
        re.IGNORECASE,
    )
    _SELECT_SCOPE = re.compile(
        r"FOR doc IN @@collection\s+FILTER doc\.skill_scope == @scope\s+RETURN doc",
        re.IGNORECASE,
    )
    _TRAVERSE = re.compile(
        r"FOR v IN 1\.\.@depth OUTBOUND @start GRAPH @graph", re.IGNORECASE
    )
    _SCOPE_EDGES = re.compile(
        r"FOR e IN @@edges\s+FILTER e\.skill_scope == @scope", re.IGNORECASE
    )
    _REMOVE_SCOPE = re.compile(
        r"FOR doc IN @@collection\s+FILTER doc\.skill_scope == @scope\s+REMOVE",
        re.IGNORECASE,
    )

    def _execute(self, query: str, bind_vars: dict[str, Any]) -> list[Any]:
        self.queries.append(query)
        normalized = " ".join(query.split())
        collection_name = bind_vars.get("@collection")
        scope = bind_vars.get("scope")

        if self._TRAVERSE.search(normalized):
            return self._traverse(bind_vars)

        if self._SCOPE_EDGES.search(normalized):
            collection = self.collection(bind_vars["@edges"])
            return [
                dict(e)
                for _, e in sorted(collection.documents.items())
                if e.get("skill_scope") == scope
            ]

        if normalized.startswith("FOR s IN @@skills"):
            collection = self.collection(bind_vars["@skills"])
            return [dict(d) for _, d in sorted(collection.documents.items())]

        if self._REMOVE_SCOPE.search(normalized):
            collection = self.collection(collection_name)
            collection._guard()
            doomed = [
                key
                for key, document in collection.documents.items()
                if document.get("skill_scope") == scope
            ]
            for key in doomed:
                del collection.documents[key]
            return [1] * len(doomed)

        if self._SELECT_SCOPE.search(normalized):
            collection = self.collection(collection_name)
            return [
                dict(document)
                for document in collection.documents.values()
                if document.get("skill_scope") == scope
            ]

        if self._COUNT_SCOPE.search(normalized):
            collection = self.collection(collection_name)
            return [
                sum(
                    1
                    for document in collection.documents.values()
                    if document.get("skill_scope") == scope
                )
            ]

        raise NotImplementedError(f"the fake database does not implement: {normalized[:120]}")

    def _traverse(self, bind_vars: dict[str, Any]) -> list[Any]:
        """Breadth-first OUTBOUND walk, the one traversal shape Layer 2 issues."""
        start = bind_vars["start"]
        scope = bind_vars.get("scope")
        depth = int(bind_vars.get("depth", 8))

        outgoing: dict[str, list[dict[str, Any]]] = {}
        for collection in self._collections.values():
            if not collection.edge or collection.foreign:
                continue
            for edge in collection.documents.values():
                outgoing.setdefault(edge["_from"], []).append(edge)

        vertices: dict[str, dict[str, Any]] = {}
        for collection in self._collections.values():
            if collection.edge or collection.foreign:
                continue
            for document in collection.documents.values():
                vertices[document["_id"]] = document

        # `uniqueVertices: global` — each vertex is returned once and never
        # approached again. Reproducing that exactly is what makes this double
        # useful: a traversal that also returned every edge would hide the fact
        # that the real one does not.
        rows: list[dict[str, Any]] = []
        seen = {start}
        frontier = [start]
        for _ in range(depth):
            nxt: list[str] = []
            for node in frontier:
                for edge in sorted(outgoing.get(node, ()), key=lambda e: e["_key"]):
                    target = vertices.get(edge["_to"])
                    if target is None or edge["_to"] in seen:
                        continue
                    if scope is not None and target.get("skill_scope") != scope:
                        continue
                    seen.add(edge["_to"])
                    nxt.append(edge["_to"])
                    rows.append(dict(target))
            frontier = nxt
            if not frontier:
                break
        return rows


def make_db(**kwargs) -> FakeWritableDatabase:
    return FakeWritableDatabase(**kwargs)
