"""The read (FR-4, FR-5) — the function the rest of the bridge will lift out.

`read_relationship_bundles()` is the canonical entry point: give it a database
handle and a Config, get an iterator of Section-7 bundles. Station 2 (rule-based
pre-classification) is expected to call exactly this and route each bundle by its
(source.type, target.type) pair, so the signature and the yielded shape are the
stable part of this module.

Rows arrive through a server-side streaming cursor, so memory stays bounded on
10^4-scale graphs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .bundle import Bundle, Entity
from .client import run_query
from .config import Config

#: Used when an endpoint document carries no value in the configured type field.
UNKNOWN_TYPE = "UNKNOWN"

#: How many dangling-endpoint examples to retain for the end-of-run report.
MAX_SKIP_EXAMPLES = 10


@dataclass
class ReadStats:
    """Bookkeeping for one read pass."""

    rows_scanned: int = 0
    emitted: int = 0
    skipped_dangling: int = 0
    skip_examples: list[str] = field(default_factory=list)

    def note_skip(self, message: str) -> None:
        self.skipped_dangling += 1
        if len(self.skip_examples) < MAX_SKIP_EXAMPLES:
            self.skip_examples.append(message)


def build_query(config: Config) -> str:
    """Assemble the read AQL: one edge joined to both of its endpoints.

    Clauses are appended only when the corresponding option is in use, so the
    default read carries no dead filters. The text contains no write operation —
    `run_query` re-checks that before execution.
    """
    lines = [
        "FOR rel IN @@relation",
        "  FILTER rel[@relation_type_field] == @relation_type",
        "  LET src = DOCUMENT(rel._from)",
        "  LET tgt = DOCUMENT(rel._to)",
    ]
    if config.entity_type_filter:
        # Dangling rows are deliberately let through so they can be counted and
        # reported rather than silently vanishing into the type filter.
        lines.append(
            "  FILTER src == null OR tgt == null OR "
            "(LOWER(src[@entity_type_field]) IN @entity_types AND "
            "LOWER(tgt[@entity_type_field]) IN @entity_types)"
        )
    # Deterministic ordering: the same KG yields the same listing every run.
    lines.append("  SORT rel._key")
    if config.limit > 0:
        lines.append("  LIMIT @limit")
    lines.extend(
        [
            "  RETURN {",
            "    relation_key: rel._key,",
            "    from_id: rel._from,",
            "    to_id: rel._to,",
            "    source_missing: src == null,",
            "    target_missing: tgt == null,",
            "    source_name: src[@entity_name_field],",
            "    source_type: src[@entity_type_field],",
            "    target_name: tgt[@entity_name_field],",
            "    target_type: tgt[@entity_type_field],",
            "    description: rel[@description_field]",
            "  }",
        ]
    )
    return "\n".join(lines)


def build_bind_vars(config: Config) -> dict[str, Any]:
    bind_vars: dict[str, Any] = {
        "@relation": config.relation_collection,
        "relation_type_field": config.relation_type_field,
        "relation_type": config.relation_type,
        "entity_name_field": config.entity_name_field,
        "entity_type_field": config.entity_type_field,
        "description_field": config.description_field,
    }
    if config.entity_type_filter:
        # Matched case-insensitively; see _parse_entity_type_filter.
        bind_vars["entity_types"] = [t.lower() for t in config.entity_type_filter]
    if config.limit > 0:
        bind_vars["limit"] = config.limit
    return bind_vars


def row_to_bundle(row: dict[str, Any]) -> Bundle:
    """Map one AQL row onto the Section 7 bundle contract."""
    return Bundle(
        relation_key=str(row.get("relation_key", "")),
        source=Entity(
            name=_name(row.get("source_name"), row.get("from_id")),
            type=_type(row.get("source_type")),
        ),
        target=Entity(
            name=_name(row.get("target_name"), row.get("to_id")),
            type=_type(row.get("target_type")),
        ),
        description=_description(row.get("description")),
    )


def _name(value: Any, fallback_id: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if value is not None and not isinstance(value, str):
        return str(value)
    # No usable name attribute: the document id still identifies the entity.
    return str(fallback_id) if fallback_id else "(unnamed)"


def _type(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if value is not None and not isinstance(value, str):
        return str(value)
    return UNKNOWN_TYPE


def _description(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def read_relationship_bundles(
    db: Any,
    config: Config,
    stats: ReadStats | None = None,
    on_warning: Callable[[str], None] | None = None,
) -> Iterator[Bundle]:
    """Yield one Section-7 bundle per relationship edge of the configured type.

    Args:
        db: a read-only database handle from `client.connect`.
        config: the loaded configuration.
        stats: optional counters, filled in as rows are consumed.
        on_warning: optional sink for dangling-endpoint warnings.

    Rows whose endpoints no longer resolve are skipped, counted and warned about;
    the read continues (Section 12).
    """
    stats = stats if stats is not None else ReadStats()
    rows = run_query(db, build_query(config), build_bind_vars(config))

    for row in rows:
        stats.rows_scanned += 1
        if row.get("source_missing") or row.get("target_missing"):
            missing = []
            if row.get("source_missing"):
                missing.append(f"_from={row.get('from_id')}")
            if row.get("target_missing"):
                missing.append(f"_to={row.get('to_id')}")
            message = (
                f"skipped relation {row.get('relation_key')}: dangling endpoint "
                f"({', '.join(missing)})"
            )
            stats.note_skip(message)
            if on_warning is not None:
                on_warning(message)
            continue
        stats.emitted += 1
        yield row_to_bundle(row)


def read_bundles(db: Any, config: Config) -> list[Bundle]:
    """Eager convenience wrapper: the whole listing as a list of bundles."""
    return list(read_relationship_bundles(db, config))
