"""An in-memory stand-in for a python-arango database.

It interprets exactly the queries the harness issues, so the whole CLI can be
exercised — including the acceptance criteria — without a running ArangoDB. It
also refuses every mutating call, which is how the test suite proves the harness
performs zero writes.
"""

from __future__ import annotations

import re
from typing import Any


class FakeWriteAttempt(AssertionError):
    """Raised if the harness ever tries to mutate anything."""


class FakeAQLError(Exception):
    pass


class _Aql:
    def __init__(self, db: "FakeDatabase") -> None:
        self._db = db

    def execute(self, query: str, bind_vars=None, batch_size=None, stream=None, **kwargs):
        return self._db._execute(query, dict(bind_vars or {}))


class _Collection:
    def __init__(self, name: str, documents: list[dict], edge: bool) -> None:
        self.name = name
        self.documents = documents
        self.edge = edge

    def properties(self) -> dict[str, Any]:
        return {"name": self.name, "edge": self.edge}

    # Every mutating method a caller might reach for is a tripwire.
    def _refuse(self, *args, **kwargs):
        raise FakeWriteAttempt(f"write attempted on collection {self.name!r}")

    insert = update = replace = delete = truncate = insert_many = _refuse
    update_many = replace_many = delete_many = add_index = _refuse


class FakeDatabase:
    """Holds entity and relation collections and answers the harness's AQL."""

    def __init__(self, collections: dict[str, _Collection]) -> None:
        self._collections = collections
        self.aql = _Aql(self)
        self.queries: list[str] = []

    # --- python-arango surface used by the harness -----------------------------
    def has_collection(self, name: str) -> bool:
        return name in self._collections

    def collection(self, name: str) -> _Collection:
        if name not in self._collections:
            raise FakeAQLError(f"collection not found: {name}")
        return self._collections[name]

    def create_collection(self, *args, **kwargs):
        raise FakeWriteAttempt("create_collection attempted")

    def delete_collection(self, *args, **kwargs):
        raise FakeWriteAttempt("delete_collection attempted")

    # --- tiny AQL interpreter -------------------------------------------------
    def _execute(self, query: str, bind: dict[str, Any]):
        self.queries.append(query)
        if re.search(r"\b(INSERT|UPDATE|REPLACE|REMOVE|UPSERT|TRUNCATE|CREATE|DROP)\b", query, re.I):
            raise FakeWriteAttempt(f"write query reached the database:\n{query}")

        if "FOR rel IN @@relation" in query:
            return self._read_bundles(query, bind)

        collection = self._docs(bind["@collection"])
        if "COLLECT type_value" in query:
            return self._observed_types(collection, bind)
        if "COLLECT WITH COUNT INTO n" in query:
            docs = self._filter_by_type(collection, bind) if "FILTER" in query else collection
            return [len(docs)]
        if "LIMIT 1" in query:
            docs = self._filter_by_type(collection, bind) if "FILTER" in query else collection
            return docs[:1]
        raise FakeAQLError(f"fake database does not understand:\n{query}")

    def _docs(self, name: str) -> list[dict]:
        if name not in self._collections:
            raise FakeAQLError(f"collection not found: {name}")
        return self._collections[name].documents

    @staticmethod
    def _filter_by_type(docs: list[dict], bind: dict[str, Any]) -> list[dict]:
        field, value = bind["type_field"], bind["type_value"]
        return [d for d in docs if d.get(field) == value]

    @staticmethod
    def _observed_types(docs: list[dict], bind: dict[str, Any]) -> list[dict]:
        field = bind["type_field"]
        counts: dict[Any, int] = {}
        for doc in docs[: bind["sample"]]:
            counts[doc.get(field)] = counts.get(doc.get(field), 0) + 1
        rows = [{"value": k, "count": v} for k, v in counts.items()]
        rows.sort(key=lambda r: -r["count"])
        return rows

    def _document(self, doc_id: Any) -> dict | None:
        if not isinstance(doc_id, str) or "/" not in doc_id:
            return None
        collection, _, key = doc_id.partition("/")
        if collection not in self._collections:
            return None
        for doc in self._collections[collection].documents:
            if doc.get("_key") == key:
                return doc
        return None

    def _read_bundles(self, query: str, bind: dict[str, Any]):
        edges = self._docs(bind["@relation"])
        type_field = bind["relation_type_field"]
        name_field = bind["entity_name_field"]
        entity_type_field = bind["entity_type_field"]
        description_field = bind["description_field"]
        allowed = bind.get("entity_types")

        rows = []
        for edge in sorted(edges, key=lambda e: e.get("_key", "")):
            if edge.get(type_field) != bind["relation_type"]:
                continue
            source = self._document(edge.get("_from"))
            target = self._document(edge.get("_to"))
            if allowed is not None and source is not None and target is not None:
                if (
                    str(source.get(entity_type_field)).lower() not in allowed
                    or str(target.get(entity_type_field)).lower() not in allowed
                ):
                    continue
            rows.append(
                {
                    "relation_key": edge.get("_key"),
                    "from_id": edge.get("_from"),
                    "to_id": edge.get("_to"),
                    "source_missing": source is None,
                    "target_missing": target is None,
                    "source_name": (source or {}).get(name_field),
                    "source_type": (source or {}).get(entity_type_field),
                    "target_name": (target or {}).get(name_field),
                    "target_type": (target or {}).get(entity_type_field),
                    "description": edge.get(description_field),
                }
            )
        limit = bind.get("limit")
        return rows[:limit] if limit else rows


def make_db(
    entities: list[dict],
    relations: list[dict],
    entity_collection: str = "masala_chai_Entities",
    relation_collection: str = "masala_chai_Relations",
    relation_is_edge: bool = True,
) -> FakeDatabase:
    return FakeDatabase(
        {
            entity_collection: _Collection(entity_collection, entities, edge=False),
            relation_collection: _Collection(relation_collection, relations, edge=relation_is_edge),
        }
    )
