"""Reading a rulebook back out of its markdown.

Two jobs. It is the round-trip half of the validation gate — `parse(render(x))`
must recover `x`, which proves the markdown carries everything the intermediate
held rather than merely looking plausible. And it is how any rulebook, generated
or hand-written, becomes Station 1 bundles for the graph checks.

The matching is deliberately literal about **word order**. A bag-of-words reader
cannot tell "pan on stove" from "water in the pan and the stove is on" — the same
words in a different arrangement, meaning different things — so a state name is
matched only where its words appear in order.
"""

from __future__ import annotations

import re
from pathlib import Path

from .schema import Primitive, Rulebook

#: Sentences that state an effect.
EFFECT = re.compile(
    r"\b(after (?:this action|the action|performing|simmering|[a-z ]{0,24}),?)", re.IGNORECASE
)
#: Sentences that state a precondition.
PRECONDITION = re.compile(
    r"\b(requires that|requires|must (?:be|already|first|have)|must\b.*\bbefore\b|before it can)",
    re.IGNORECASE,
)

#: Words that carry no identity, so two phrases differing only in these match.
ARTICLES = frozenset(
    {"the", "a", "an", "there", "is", "are", "be", "been", "has", "have", "of", "that", "true"}
)


def normalize(phrase: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (phrase or "").strip().lower()).strip("_")


def tokens(phrase: str) -> list[str]:
    """Content words, in order. Order is the whole point — see the module docstring."""
    return [t for t in normalize(phrase).split("_") if t and t not in ARTICLES]


def is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    position = 0
    for token in needle:
        try:
            position = haystack.index(token, position) + 1
        except ValueError:
            return False
    return True


def matches(phrase_tokens: dict[str, list[str]], sentence: str) -> list[str]:
    """Every listed name a sentence refers to, longest first.

    One sentence routinely names two states — "requires that the water is boiling
    and that there are grounds in the filter" — so this returns both. A name whose
    words are a strict subset of an accepted one is dropped, so "grounds" never
    shadows "grounds in filter".
    """
    words = tokens(sentence)
    candidates = sorted(
        (name for name, needle in phrase_tokens.items() if needle and is_subsequence(needle, words)),
        key=lambda name: -len(phrase_tokens[name]),
    )
    accepted: list[str] = []
    for name in candidates:
        current = set(phrase_tokens[name])
        if not any(current < set(phrase_tokens[other]) for other in accepted):
            accepted.append(name)
    return accepted


def section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


def listed(sentence: str) -> list[str]:
    """The items of a prose list: 'a, b, and c.' -> [a, b, c]."""
    body = sentence.split(":", 1)[-1]
    body = re.sub(r"\.\s*$", "", body.strip())
    parts = re.split(r",|\band\b", body)
    return [normalize(p) for p in parts if normalize(p)]


def sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", flat) if s.strip()]


def parse_text(text: str, source_url: str = "") -> Rulebook:
    """Read a canonical rulebook markdown back into the intermediate."""
    skill_section = section(text, "Skill")
    skill_match = re.search(r"\*\*(.+?)\*\*", skill_section)
    title_match = re.search(r"^#\s+(.+?)(?:\s+—.*)?$", text, re.M)

    rulebook = Rulebook(
        skill=normalize(skill_match.group(1)) if skill_match else "",
        title=title_match.group(1).strip() if title_match else "",
        overview=" ".join(section(text, "Overview").split()),
        objects=listed(section(text, "Objects")),
        states=listed(section(text, "States")),
        source_url=source_url,
    )

    state_tokens = {state: tokens(state) for state in rulebook.states}
    object_tokens = {obj: tokens(obj) for obj in rulebook.objects}

    for block in re.split(r"\n\s*\n", section(text, "Primitive actions and their rules")):
        header = re.match(r"\*\*(.+?)\*\*\s*[—–-]\s*(.*)", " ".join(block.split()))
        if not header:
            continue
        primitive = Primitive(name=normalize(header.group(1)), narration="")
        body = header.group(2)

        narration: list[str] = []
        for sentence in sentences(body):
            precondition = bool(PRECONDITION.search(sentence))
            effect = bool(EFFECT.search(sentence))

            # In "X must hold before Y happens", the clause after "before"
            # describes this action's own effect, not something it requires;
            # scanning the whole sentence would make add_milk require milk_added.
            scanned = sentence.split(" before ")[0] if precondition else sentence

            found = matches(state_tokens, scanned)
            if precondition:
                primitive.requires.extend(s for s in found if s not in primitive.requires)
            elif effect:
                primitive.produces.extend(s for s in found if s not in primitive.produces)
            else:
                narration.append(sentence)

            if not precondition:
                for obj in matches(object_tokens, sentence):
                    if obj not in primitive.uses:
                        primitive.uses.append(obj)

        primitive.narration = " ".join(narration)
        if primitive.name:
            rulebook.primitives.append(primitive)

    ordering = section(text, "Ordering rules")
    rulebook.ordering = sentences(ordering) if ordering else []
    return rulebook


def parse_file(path: str | Path, source_url: str = "") -> Rulebook:
    return parse_text(Path(path).read_text(encoding="utf-8"), source_url=source_url)
