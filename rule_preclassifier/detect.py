"""Reason-code detection (PRD Section 9) and the type canonicalization it needs.

Two of the nine codes — `conflicting_edge` and `alias_mismatch` — are properties
of the *set*, not of a single bundle, so the bundle stream is indexed once up
front (`CorpusIndex`) and every per-bundle check then runs in constant time. The
whole station therefore stays a single linear pass over an in-memory iterable,
which is what FR-1 hands it.

Nothing here reads a database, calls a model, or touches the filesystem (FR-8).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable, Sequence

from kg_read_harness.bundle import Bundle
from kg_read_harness.read import UNKNOWN_TYPE

from .table import (
    ACTION_VERBS,
    ALIAS_MISMATCH,
    AMBIGUOUS_DIRECTION,
    CONFLICTING_EDGE,
    DANGLING_ENDPOINT,
    DEFER,
    EMPTY_DESCRIPTION,
    MISSING_TYPE,
    NAME_ARTICLES,
    ONTOLOGY,
    PRECEDES,
    SELF_LOOP,
    SUSPECT_TYPE,
    TYPE_SYNONYMS,
    UNMAPPED_PAIR,
    Rule,
)

#: Station 1 falls back to the raw document id when an endpoint has no name
#: attribute, which is the visible signature of an unresolved endpoint.
_DOCUMENT_ID = re.compile(r"^[A-Za-z0-9_.\-]+/[^/]+$")

_UNNAMED = "(unnamed)"

_NON_WORD = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------
# Canonicalization
# --------------------------------------------------------------------------


def canonical_type(raw: str | None) -> str | None:
    """Map a raw type value onto the ontology, or None if it is outside it.

    Casing is normalized (live builds emit `tool`, the PRD writes `PRIMITIVE`);
    genuine synonyms come from the editable `TYPE_SYNONYMS` table.
    """
    if not raw:
        return None
    value = raw.strip().upper()
    if not value or value == UNKNOWN_TYPE:
        return None
    value = TYPE_SYNONYMS.get(value, value)
    return value if value in ONTOLOGY else None


def type_rewrite(raw: str | None) -> str | None:
    """`"tool -> PRIMITIVE"` when a synonym or casing rule changed the value."""
    canonical = canonical_type(raw)
    if canonical is None or raw is None or raw.strip() == canonical:
        return None
    return f"{raw.strip()} -> {canonical}"


def normalize_name(name: str) -> str:
    """Comparison key for a name: casing, spacing, punctuation and articles out."""
    key = _NON_WORD.sub("_", (name or "").strip().lower()).strip("_")
    for article in NAME_ARTICLES:
        prefix = f"{article}_"
        if key.startswith(prefix):
            key = key[len(prefix) :]
            break
    return key


def looks_like_action(name: str) -> bool:
    """Whether a name reads as a robot action (`place_pan`, `TURN ON STOVE`)."""
    key = normalize_name(name)
    return bool(key) and key.split("_")[0] in ACTION_VERBS


def looks_unresolved(name: str) -> bool:
    """Whether Station 1 could not resolve this endpoint to a real document."""
    value = (name or "").strip()
    return not value or value == _UNNAMED or bool(_DOCUMENT_ID.match(value))


# --------------------------------------------------------------------------
# Corpus-level index
# --------------------------------------------------------------------------


class CorpusIndex:
    """One pass over the bundles, so set-level codes are O(1) per bundle."""

    def __init__(self, bundles: Sequence[Bundle]) -> None:
        self._oriented: set[tuple[str, str]] = set()
        self._names_by_key: defaultdict[str, set[str]] = defaultdict(set)

        for bundle in bundles:
            source = normalize_name(bundle.source.name)
            target = normalize_name(bundle.target.name)
            self._oriented.add((source, target))
            self._names_by_key[source].add(bundle.source.name)
            self._names_by_key[target].add(bundle.target.name)

    def has_opposite(self, bundle: Bundle) -> bool:
        """Whether some other bundle relates the same two entities the other way."""
        source = normalize_name(bundle.source.name)
        target = normalize_name(bundle.target.name)
        if source == target:
            return False  # a self-loop is not a conflict
        return (target, source) in self._oriented

    def aliases(self, name: str) -> tuple[str, ...]:
        """Other spellings of `name` present in the corpus (casing / spacing)."""
        siblings = self._names_by_key.get(normalize_name(name), set())
        return tuple(sorted(other for other in siblings if other != name))


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def detect_reasons(
    bundle: Bundle,
    source_type: str | None,
    target_type: str | None,
    rule: Rule | None,
    index: CorpusIndex,
) -> list[tuple[str, str]]:
    """All applicable `(reason_code, detail)` pairs for one bundle.

    Blocking codes are checked whether or not the table has a row, because they
    make the bundle untrustworthy either way; explanatory codes are checked only
    when the table failed, so they never override a good classification. The
    caller picks the single winner by the B->C->D->A priority (FR-5).
    """
    reasons: list[tuple[str, str]] = []

    # --- blocking: the bundle cannot be trusted as read ---------------------
    unresolved = [
        f"{end}={name!r}"
        for end, name in (("source", bundle.source.name), ("target", bundle.target.name))
        if looks_unresolved(name)
    ]
    if unresolved:
        reasons.append(
            (DANGLING_ENDPOINT, "endpoint did not resolve to an entity: " + ", ".join(unresolved))
        )

    off_ontology = [
        f"{end}={raw!r}"
        for end, raw, canonical in (
            ("source", bundle.source.type, source_type),
            ("target", bundle.target.type, target_type),
        )
        if canonical is None
    ]
    if off_ontology:
        reasons.append(
            (
                MISSING_TYPE,
                "type is null or outside the ontology "
                f"({'/'.join(ONTOLOGY)}): " + ", ".join(off_ontology),
            )
        )

    if normalize_name(bundle.source.name) == normalize_name(bundle.target.name):
        reasons.append((SELF_LOOP, f"{bundle.source.name!r} is related to itself"))

    if index.has_opposite(bundle):
        reasons.append(
            (
                CONFLICTING_EDGE,
                f"a duplicate relation runs the other way "
                f"({bundle.target.name!r} -> {bundle.source.name!r})",
            )
        )

    if rule is not None and not bundle.description.strip():
        # A description is only load-bearing where a later station must read it.
        if rule.outcome == DEFER:
            reasons.append(
                (
                    EMPTY_DESCRIPTION,
                    "PRIMITIVE-STATE edge with no text, so Station 3 has nothing to read",
                )
            )
        elif rule.edge_type == PRECEDES:
            reasons.append(
                (
                    AMBIGUOUS_DIRECTION,
                    "precedes edge with no description, so no source resolves the arrow",
                )
            )

    # --- explanatory: only when the table could not route the pair ----------
    if rule is None:
        mistyped = [
            f"{end}={name!r} is typed {raw!r}"
            for end, name, raw, canonical in (
                ("source", bundle.source.name, bundle.source.type, source_type),
                ("target", bundle.target.name, bundle.target.type, target_type),
            )
            if canonical in ("OBJECT", "STATE") and looks_like_action(name)
        ]
        if mistyped:
            reasons.append(
                (SUSPECT_TYPE, "reads as a primitive action but is not typed one: " + ", ".join(mistyped))
            )

        aliased = [
            f"{name!r} vs {', '.join(repr(a) for a in aliases)}"
            for name in (bundle.source.name, bundle.target.name)
            for aliases in (index.aliases(name),)
            if aliases
        ]
        if aliased:
            reasons.append(
                (ALIAS_MISMATCH, "names that should match do not: " + "; ".join(aliased))
            )

        if source_type is not None and target_type is not None:
            reasons.append(
                (
                    UNMAPPED_PAIR,
                    f"{source_type}-{target_type} is a valid pair with no planning meaning",
                )
            )

    return reasons


def iter_types(bundle: Bundle) -> Iterable[str | None]:
    yield bundle.source.type
    yield bundle.target.type
