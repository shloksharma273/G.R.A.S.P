"""Identity and scoping (PRD Section 5) — the key decision of this station.

Vertices are keyed by a normalized name so repeated mentions dedupe. But
different tasks reuse the same words — "serve" appears in coffee, burger and
chai — and merging them would let one recipe's edges bleed into another and
corrupt its plan. So identity is **scoped per task**: a vertex key is
`(skill_scope, normalized_name)`.

That matters most in a shared database. The live pilot instance holds several
projects side by side; a coincidental shared state name like `stove_on` must
never chain actions across unrelated tasks.

Edges are keyed by a hash of `(skill_scope, from, to, type)` so a rebuild dedupes
cleanly instead of accumulating duplicates.
"""

from __future__ import annotations

import hashlib
import re

from rule_preclassifier.detect import normalize_name

#: ArangoDB permits letters, digits and `_-:.@()+,=;$!*'%` in a document key.
#: Normalized names are already `[a-z0-9_]`, so this only guards odd input.
_ILLEGAL = re.compile(r"[^A-Za-z0-9_\-:.@()+,=;$!*'%]")

#: ArangoDB's hard limit is 254 bytes; leave room and fall back to a hash well
#: before it so a long name degrades predictably rather than at the boundary.
MAX_KEY_LENGTH = 200

KEY_SEPARATOR = "__"


def scope_key(skill_name: str) -> str:
    """The `skill_scope` value for a task, derived from its skill's name."""
    return normalize_name(skill_name)


def vertex_key(skill_scope: str, name: str) -> str:
    """`(skill_scope, normalized_name)` as one document key (FR-2).

    Readable by design — `make_masala_chai__pan_on_stove` is far easier to debug
    than a hash — with a hash suffix only when the readable form would be too
    long or carry a character ArangoDB rejects.
    """
    scope = _sanitize(skill_scope)
    normalized = _sanitize(normalize_name(name))
    candidate = f"{scope}{KEY_SEPARATOR}{normalized}"
    if len(candidate.encode("utf-8")) <= MAX_KEY_LENGTH:
        return candidate
    digest = hashlib.sha256(f"{skill_scope}|{name}".encode("utf-8")).hexdigest()[:16]
    return f"{scope[:64]}{KEY_SEPARATOR}{normalized[:100]}{KEY_SEPARATOR}{digest}"


def edge_key(skill_scope: str, from_key: str, to_key: str, edge_type: str) -> str:
    """A hash of `(skill_scope, from, to, type)` (Section 5, FR-2).

    Hashed rather than readable because the readable form would routinely exceed
    the key limit — it would contain two full vertex keys. Determinism is what
    matters here: the same edge must produce the same key on every rebuild.
    """
    payload = "|".join((skill_scope, from_key, to_key, edge_type))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def build_id(payload: str) -> str:
    """A deterministic identifier for one build.

    A timestamp would be the obvious choice, but Section 10 requires that
    "identical input yields an identical PlanGraph" — and a clock would make every
    re-run differ in every document. So the build id is a hash of the input
    instead: it still identifies the build, and re-running unchanged input is a
    genuine no-op rather than a no-op that rewrites every `build_id` field.
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _sanitize(value: str) -> str:
    cleaned = _ILLEGAL.sub("_", value or "").strip("_")
    return cleaned or "unnamed"
