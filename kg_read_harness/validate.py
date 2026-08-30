"""Existence and shape validation of the KG (FR-3).

Runs before any bundle is read so that a misnamed collection or a renamed
attribute produces one clear sentence instead of an obscure empty listing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .client import collection_exists, collection_properties, run_query
from .config import Config, unknown_filter_types
from .errors import AttributeMismatchError, CollectionNotFoundError, EmptyResultError

#: How many relation documents to sample when reporting which types actually exist.
TYPE_SAMPLE_SIZE = 5000

_COUNT_ALL = "FOR doc IN @@collection COLLECT WITH COUNT INTO n RETURN n"

_COUNT_OF_TYPE = """
FOR doc IN @@collection
  FILTER doc[@type_field] == @type_value
  COLLECT WITH COUNT INTO n
  RETURN n
"""

_SAMPLE_ANY = "FOR doc IN @@collection LIMIT 1 RETURN doc"

_SAMPLE_OF_TYPE = """
FOR doc IN @@collection
  FILTER doc[@type_field] == @type_value
  LIMIT 1
  RETURN doc
"""

_OBSERVED_TYPES = """
FOR doc IN @@collection
  LIMIT @sample
  COLLECT type_value = doc[@type_field] WITH COUNT INTO n
  SORT n DESC
  RETURN {value: type_value, count: n}
"""


@dataclass
class ValidationReport:
    entity_count: int = 0
    relation_count: int = 0
    matching_relation_count: int = 0
    observed_relation_types: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def observed_types_summary(self) -> str:
        if not self.observed_relation_types:
            return "(none)"
        return ", ".join(
            f"{row['value']!r} ({row['count']})" for row in self.observed_relation_types
        )


def _count(db: Any, collection: str) -> int:
    rows = list(run_query(db, _COUNT_ALL, {"@collection": collection}))
    return int(rows[0]) if rows else 0


def _count_of_type(db: Any, collection: str, type_field: str, type_value: str) -> int:
    rows = list(
        run_query(
            db,
            _COUNT_OF_TYPE,
            {"@collection": collection, "type_field": type_field, "type_value": type_value},
        )
    )
    return int(rows[0]) if rows else 0


def _sample(db: Any, collection: str) -> dict[str, Any] | None:
    rows = list(run_query(db, _SAMPLE_ANY, {"@collection": collection}))
    return rows[0] if rows else None


def _sample_of_type(
    db: Any, collection: str, type_field: str, type_value: str
) -> dict[str, Any] | None:
    rows = list(
        run_query(
            db,
            _SAMPLE_OF_TYPE,
            {"@collection": collection, "type_field": type_field, "type_value": type_value},
        )
    )
    return rows[0] if rows else None


def _require_collections(db: Any, config: Config) -> None:
    for label, name, env_var in (
        ("entity", config.entity_collection, "ENTITY_COLLECTION"),
        ("relation", config.relation_collection, "RELATION_COLLECTION"),
    ):
        if not collection_exists(db, name):
            raise CollectionNotFoundError(
                f"the {label} collection {name!r} does not exist in database "
                f"{config.database!r}.",
                f"confirm the real name in the ArangoDB Graph Explorer and set {env_var} "
                f"(the default is derived from PROJECT_NAME={config.project_name!r}).",
            )


def _check_entity_shape(db: Any, config: Config, report: ValidationReport) -> None:
    report.entity_count = _count(db, config.entity_collection)
    if report.entity_count == 0:
        raise EmptyResultError(
            f"the entity collection {config.entity_collection!r} is empty — the KG holds "
            "no entities.",
            "this is what a VectorRAG-only AutoGraph build looks like. Re-run AutoGraph "
            "as a FullGraphRAG build so entities and RELATED_TO relations are extracted.",
        )

    sample = _sample(db, config.entity_collection)
    assert sample is not None  # non-zero count guarantees one document

    if config.entity_name_field not in sample:
        raise AttributeMismatchError(
            f"entity documents in {config.entity_collection!r} have no "
            f"{config.entity_name_field!r} attribute "
            f"(sample document keys: {_keys(sample)}).",
            "override it with ENTITY_NAME_FIELD after checking a document in the "
            "Graph Explorer.",
        )
    if config.entity_type_field not in sample:
        raise AttributeMismatchError(
            f"entity documents in {config.entity_collection!r} have no "
            f"{config.entity_type_field!r} attribute "
            f"(sample document keys: {_keys(sample)}).",
            "override it with ENTITY_TYPE_FIELD; AutoGraph builds have used both "
            "'entity_type' and 'type' depending on version.",
        )


def _check_relation_shape(db: Any, config: Config, report: ValidationReport) -> None:
    report.relation_count = _count(db, config.relation_collection)
    if report.relation_count == 0:
        raise EmptyResultError(
            f"the relation collection {config.relation_collection!r} is empty — the KG "
            "holds no relationships.",
            "the build may have extracted entities only, or RELATION_COLLECTION may name "
            "the wrong collection. Confirm both in the Graph Explorer.",
        )

    properties = collection_properties(db, config.relation_collection)
    if not properties.get("edge", False):
        report.warnings.append(
            f"{config.relation_collection!r} is not an edge collection; endpoint "
            "resolution relies on _from/_to and may fail."
        )

    report.observed_relation_types = list(
        run_query(
            db,
            _OBSERVED_TYPES,
            {
                "@collection": config.relation_collection,
                "type_field": config.relation_type_field,
                "sample": TYPE_SAMPLE_SIZE,
            },
        )
    )

    sample = _sample_of_type(
        db, config.relation_collection, config.relation_type_field, config.relation_type
    )
    # A collection can hold several relation types (RELATED_TO alongside
    # IN_COMMUNITY, MENTIONED_IN, ...) that need not share a schema. Attribute
    # checks are therefore only strict against a document of the configured type;
    # otherwise the honest diagnosis is "no relationship of that type", which the
    # read reports, and checking an unrelated document would report the wrong thing.
    strict = sample is not None
    if not strict:
        sample = _sample(db, config.relation_collection)
        assert sample is not None
        report.warnings.append(
            f"no relation document has {config.relation_type_field} == "
            f"{config.relation_type!r}; observed types: {report.observed_types_summary()}."
        )

    if config.relation_type_field not in sample:
        # Unambiguous: the type attribute itself is misnamed, so no filter could work.
        raise AttributeMismatchError(
            f"relation documents in {config.relation_collection!r} have no "
            f"{config.relation_type_field!r} attribute "
            f"(sample document keys: {_keys(sample)}).",
            "override it with RELATION_TYPE_FIELD.",
        )

    missing = [
        attribute
        for attribute in (config.description_field, "_from", "_to")
        if attribute not in sample
    ]
    for attribute in missing:
        if attribute == config.description_field:
            message = (
                f"relation documents in {config.relation_collection!r} have no "
                f"{config.description_field!r} attribute "
                f"(sample document keys: {_keys(sample)}).",
                "override it with DESCRIPTION_FIELD.",
            )
        else:
            message = (
                f"relation documents in {config.relation_collection!r} have no "
                f"{attribute!r} attribute, so endpoints cannot be resolved.",
                "RELATION_COLLECTION must name the edge collection that connects "
                "entities, not a document collection.",
            )
        if strict:
            raise AttributeMismatchError(*message)
        report.warnings.append(f"{message[0]} (checked against a document of another type)")

    report.matching_relation_count = _count_of_type(
        db, config.relation_collection, config.relation_type_field, config.relation_type
    )


def _keys(document: dict[str, Any]) -> str:
    return ", ".join(sorted(k for k in document if not k.startswith("_"))) or "(none)"


def validate_kg(db: Any, config: Config) -> ValidationReport:
    """Confirm the KG exists and has the configured shape, or raise.

    Returns a report with counts, the relation types actually present, and any
    non-fatal warnings.
    """
    report = ValidationReport()

    unknown = unknown_filter_types(config)
    if unknown:
        report.warnings.append(
            "ENTITY_TYPE_FILTER contains type(s) outside the AutoGraph ontology: "
            + ", ".join(unknown)
        )

    _require_collections(db, config)
    _check_entity_shape(db, config, report)
    _check_relation_shape(db, config, report)
    return report
