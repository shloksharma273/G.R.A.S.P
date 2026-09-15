"""Stage 3 — deterministic rendering (PRD Section 6, FR-4).

The intermediate becomes canonical rulebook markdown: the exact template the six
hand-written rulebooks use, so AutoGraph and every station downstream cannot tell
a generated rulebook from an authored one.

Deterministic on purpose. The LLM's nondeterminism is confined to stage 2; from
here on the same intermediate always renders the same bytes, which is half of
what FR-6 asks for.

The sentence templates matter more than they look. Downstream extraction keys on
this wording — Station 3's lexical pre-pass looks for "requires ... as a
precondition" and "After this action" — so rendering in the corpus's own phrasing
is what lets a generated rulebook flow through the bridge as cleanly as a
hand-written one.
"""

from __future__ import annotations

import textwrap

from .schema import Primitive, Rulebook

WRAP = 79


def humanize(name: str) -> str:
    return name.replace("_", " ").strip()


def _wrap(text: str) -> str:
    return "\n".join(textwrap.wrap(" ".join(text.split()), width=WRAP)) if text.strip() else ""


def _prose_list(names: list[str]) -> str:
    """`[a, b, c]` -> `a, b, and c`, the way the corpus writes lists."""
    readable = [humanize(n) for n in names]
    if not readable:
        return "none"
    if len(readable) == 1:
        return readable[0]
    if len(readable) == 2:
        return f"{readable[0]} and {readable[1]}"
    return ", ".join(readable[:-1]) + ", and " + readable[-1]


def render_primitive(primitive: Primitive) -> str:
    """One primitive's block: narration, then its preconditions and effects.

    Preconditions come before effects, and both are stated in the corpus's own
    phrasing rather than a terser form of our own.
    """
    sentences = [primitive.narration.strip().rstrip(".") + "." if primitive.narration.strip() else ""]

    for state in primitive.requires:
        sentences.append(
            f"This action requires that {humanize(state)} is already true as a precondition."
        )
    for state in primitive.produces:
        sentences.append(f"After this action, {humanize(state)} is true.")

    body = " ".join(s for s in sentences if s)
    return _wrap(f"**{primitive.name}** — {body}")


DEFAULT_OVERVIEW = (
    "This rulebook describes the skill of {title} as a sequence of primitive robot "
    "actions. Each primitive action has preconditions (what must be true before it "
    "can run) and effects (what becomes true after it runs). The robot composes "
    "these primitives into an ordered plan."
)


def _declared_states(rulebook: Rulebook) -> list[str]:
    """Declared states, plus any a primitive uses that the model forgot to list.

    Without this the markdown would faithfully reproduce the model's omission and
    the state would vanish on the way back out. The Objects and States sections
    are the markdown's only vocabulary; anything missing from them cannot be
    recovered. The omission is still reported by the gate - this only stops it
    from becoming data loss.
    """
    known = list(rulebook.states)
    seen = {s for s in known}
    for state in sorted(rulebook.required_states | rulebook.produced_states):
        if state not in seen:
            known.append(state)
            seen.add(state)
    return known


def _declared_objects(rulebook: Rulebook) -> list[str]:
    known = list(rulebook.objects)
    seen = set(known)
    for primitive in rulebook.primitives:
        for obj in primitive.uses:
            if obj not in seen:
                known.append(obj)
                seen.add(obj)
    return known


def render(rulebook: Rulebook) -> str:
    """The canonical rulebook markdown (FR-4)."""
    title = rulebook.title or humanize(rulebook.skill).title()
    overview = rulebook.overview.strip() or DEFAULT_OVERVIEW.format(title=title.lower())

    parts: list[str] = [f"# {title} — Robot Skill Rulebook", ""]

    parts += ["## Overview", "", _wrap(overview), ""]

    parts += [
        "## Skill",
        "",
        _wrap(
            f"**{rulebook.skill}** is a high-level skill. It is composed of the following "
            f"primitive actions: {_prose_list(rulebook.primitive_names)}."
        ),
        "",
    ]

    parts += [
        "## Objects",
        "",
        _wrap(f"The objects involved in this skill are: {_prose_list(_declared_objects(rulebook))}."),
        "",
    ]

    parts += [
        "## States",
        "",
        _wrap(f"The relevant states of the world are: {_prose_list(_declared_states(rulebook))}."),
        "",
    ]

    parts += ["## Primitive actions and their rules", ""]
    for primitive in rulebook.primitives:
        parts += [render_primitive(primitive), ""]

    parts += ["## Ordering rules", ""]
    ordering = rulebook.ordering or _derive_ordering(rulebook)
    parts += [_wrap(" ".join(ordering)) if ordering else _wrap("No ordering constraints stated."), ""]

    if rulebook.source_url:
        parts += ["## Source", "", _wrap(f"Generated from {rulebook.source_url}"), ""]

    return "\n".join(parts).rstrip() + "\n"


def _derive_ordering(rulebook: Rulebook) -> list[str]:
    """Ordering prose from the precondition graph, when the model stated none.

    The constraints are already implied by requires/produces — this only says them
    out loud, so the rendered rulebook reads like the hand-written ones.
    """
    producer = {
        state: primitive.name
        for primitive in rulebook.primitives
        for state in primitive.produces
    }
    sentences: list[str] = []
    for primitive in rulebook.primitives:
        for state in primitive.requires:
            source = producer.get(state)
            if source and source != primitive.name:
                sentences.append(
                    f"{humanize(source).capitalize()} must happen before "
                    f"{humanize(primitive.name)}, because it makes {humanize(state)} true."
                )
    return sentences
