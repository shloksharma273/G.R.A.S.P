"""Turn a rulebook markdown into Station 1 bundles — a stand-in for AutoGraph.

AutoGraph is a managed service, and only the chai rulebook has actually been run
through it. Layer 2's acceptance criteria need all five of the others in a
PlanGraph, so this parser produces the same `Bundle` shape Station 1 emits,
letting the whole bridge run end to end on every rulebook with no AutoGraph
build.

It is deliberately a **test fixture, not a product component**. It exploits the
fact that the rulebooks share one rigid template; real extraction is AutoGraph's
job and is far harder. What it buys is the ability to check that Stations 2-5 and
Layer 2 hold up on six different tasks rather than one.

The reading itself now lives in `rulebook_generator.parse`, where it is a real
component (the round-trip half of the generator's validation gate). This module
imports it rather than keeping a second copy, so the two cannot drift.

The wording it emits mirrors what AutoGraph produced for chai, so the lexical
pre-pass and the LLM see the same kind of text they would in a live build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from kg_read_harness.bundle import Bundle, Entity
from rulebook_generator.parse import (
    EFFECT as _EFFECT_PATTERN,
    PRECONDITION as _PRECONDITION_PATTERN,
    is_subsequence,
    listed as _listed_shared,
    matches as _matches_shared,
    normalize as _normalize_shared,
    section as _section_shared,
    sentences as _sentences_shared,
    tokens as _tokens_shared,
)

DATASET = Path(__file__).resolve().parent.parent / "dataset"
CHAI = Path(__file__).resolve().parent.parent / "masala_chai_rulebook.md"

#: Sentences that state an effect.
_EFFECT = re.compile(
    r"\b(after (?:this action|the action|performing|simmering|[a-z ]{0,24}),?)", re.IGNORECASE
)
#: Sentences that state a precondition.
_PRECONDITION = re.compile(
    r"\b(requires that|requires|must (?:be|already|first|have)|must\b.*\bbefore\b|before it can)",
    re.IGNORECASE,
)

_ARTICLES = {"the", "a", "an", "there", "is", "are", "be", "been", "has", "have", "of", "that"}


def normalize(phrase: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", phrase.strip().lower()).strip("_")


def _tokens(phrase: str) -> list[str]:
    """Content words, in order. Order matters: "pan on stove" and "the stove is
    on ... in the pan" contain the same words and mean different things."""
    return [t for t in normalize(phrase).split("_") if t and t not in _ARTICLES]


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    position = 0
    for token in needle:
        try:
            position = haystack.index(token, position) + 1
        except ValueError:
            return False
    return True


@dataclass
class Rulebook:
    """One parsed rulebook."""

    path: Path
    skill: str = ""
    primitives: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    #: (primitive, state, sentence)
    effects: list[tuple[str, str, str]] = field(default_factory=list)
    preconditions: list[tuple[str, str, str]] = field(default_factory=list)
    #: (primitive, object, sentence)
    uses: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.stem.replace("rulebook_", "")

    def bundles(self) -> list[Bundle]:
        """Station 1's contract: one bundle per entity-to-entity relationship."""
        bundles: list[Bundle] = []

        def add(source, source_type, target, target_type, description):
            bundles.append(
                Bundle(
                    relation_key=f"{self.name}_r{len(bundles) + 1:03d}",
                    source=Entity(source, source_type),
                    target=Entity(target, target_type),
                    description=description,
                )
            )

        for primitive in self.primitives:
            add(
                self.skill,
                "SKILL",
                primitive,
                "PRIMITIVE",
                f"The skill {self.skill} is composed of the primitive action {primitive}.",
            )
        for primitive, state, sentence in self.effects:
            add(primitive, "PRIMITIVE", state, "STATE", sentence)
        for primitive, state, sentence in self.preconditions:
            add(primitive, "PRIMITIVE", state, "STATE", sentence)
        for primitive, obj, sentence in self.uses:
            add(primitive, "PRIMITIVE", obj, "OBJECT", sentence)
        return bundles

    def answer_key(self) -> dict[tuple[str, str], str]:
        """(primitive, state) -> the correct Station 3 label, from the rulebook."""
        key = {(p, s): "produces" for p, s, _ in self.effects}
        key.update({(p, s): "requires" for p, s, _ in self.preconditions})
        return key

    def expected_order_constraints(self) -> list[tuple[str, str]]:
        """producer -> requirer, the ordering state chaining must reconstruct."""
        produced = {(state): primitive for primitive, state, _ in self.effects}
        return sorted(
            {
                (produced[state], primitive)
                for primitive, state, _ in self.preconditions
                if state in produced and produced[state] != primitive
            }
        )


def _section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


def _listed(sentence: str) -> list[str]:
    """The items of a prose list: 'a, b, c, and d.' -> [a, b, c, d]."""
    body = sentence.split(":", 1)[-1]
    body = re.sub(r"\.\s*$", "", body.strip())
    parts = re.split(r",|\band\b", body)
    return [normalize(p) for p in parts if normalize(p)]


def _sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", flat) if s.strip()]


def _matches(phrase_tokens: dict[str, list[str]], sentence: str) -> list[str]:
    """Every listed name a sentence refers to, longest match first.

    One sentence routinely names two states - "requires that the water is boiling
    and that there are grounds in the filter" - so this must return both. A name
    whose tokens are a strict subset of an already-accepted one is dropped, so
    "grounds" never shadows "grounds in filter".
    """
    words = _tokens(sentence)
    candidates = sorted(
        (
            name
            for name, tokens in phrase_tokens.items()
            if tokens and _is_subsequence(tokens, words)
        ),
        key=lambda name: -len(phrase_tokens[name]),
    )
    accepted: list[str] = []
    for name in candidates:
        current = set(phrase_tokens[name])
        if not any(current < set(phrase_tokens[other]) for other in accepted):
            accepted.append(name)
    return accepted


def parse(path: Path) -> Rulebook:
    text = path.read_text(encoding="utf-8")
    book = Rulebook(path=path)

    skill_section = _section(text, "Skill")
    skill_match = re.search(r"\*\*(.+?)\*\*", skill_section)
    book.skill = normalize(skill_match.group(1)) if skill_match else book.name

    book.objects = _listed(_section(text, "Objects"))
    book.states = _listed(_section(text, "States"))

    state_tokens = {state: _tokens(state) for state in book.states}
    object_tokens = {obj: _tokens(obj) for obj in book.objects}

    rules = _section(text, "Primitive actions and their rules")
    for block in re.split(r"\n\s*\n", rules):
        block = " ".join(block.split())
        header = re.match(r"\*\*(.+?)\*\*\s*[—–-]\s*(.*)", block)
        if not header:
            continue
        primitive = normalize(header.group(1))
        body = header.group(2)
        book.primitives.append(primitive)

        for sentence in _sentences(body):
            precondition = bool(_PRECONDITION.search(sentence))
            effect = bool(_EFFECT.search(sentence))
            # In "X must hold before Y happens", the clause after "before"
            # describes this action's own effect, not something it requires.
            # Matching the whole sentence would make add_milk require milk_added.
            scanned = sentence.split(" before ")[0] if precondition else sentence
            for state in _matches(state_tokens, scanned):
                if precondition:
                    book.preconditions.append((primitive, state, sentence))
                elif effect:
                    book.effects.append((primitive, state, sentence))

            if not precondition:
                seen = {(p, o) for p, o, _ in book.uses}
                for obj in _matches(object_tokens, sentence):
                    if (primitive, obj) not in seen:
                        book.uses.append((primitive, obj, sentence))

    return book


def all_rulebooks() -> list[Rulebook]:
    """Every rulebook in the corpus, chai included, in a stable order."""
    paths = sorted(DATASET.glob("rulebook_*.md")) + [CHAI]
    return [parse(path) for path in paths if path.exists()]


def by_name() -> dict[str, Rulebook]:
    return {book.skill: book for book in all_rulebooks()}
