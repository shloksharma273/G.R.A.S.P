"""The LLM interaction contract (PRD Section 7).

The system instruction defines requires vs. produces in the temporal terms of
Section 5 and nothing else. Per item the model sees only the primitive name, the
state name and the description: "No other graph context is sent - the decision
must rest on the description." That restriction is what makes the verdict
auditable, and it is why `unclear` has to be a first-class option.

Parsing is deliberately strict about the closed vocabulary and forgiving about
transport noise (code fences, a bare array, a stray wrapper key), because a
provider reformatting valid content is not the same failure as a model inventing
a label.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from kg_read_harness.bundle import Bundle

from .model import LABELS, LABEL_UNCLEAR, METHOD_LLM, Verdict

SYSTEM_PROMPT = """\
You classify one relationship at a time between a robot PRIMITIVE action and a \
world STATE. Decide whether the primitive REQUIRES the state or PRODUCES it.

requires - the state must already be TRUE BEFORE the primitive can run. It is a \
precondition. Reads like: "before", "needs", "must already be", "once X is", \
"requires", "depends on".

produces - the state becomes TRUE AFTER the primitive runs. It is an effect. \
Reads like: "after", "then", "becomes", "results in", "now", "makes X true".

unclear - the description does not support either reading confidently. Use this \
whenever the text is silent, purely descriptive, or genuinely both. Do NOT guess \
between requires and produces to avoid answering; a wrong precondition corrupts \
the plan, an honest abstention does not.

Judge ONLY from the description given. Do not use world knowledge about how the \
task is normally performed, and do not infer from the names alone.

Return a JSON object and nothing else, of exactly this shape:
{"items": [{"id": "<echo the id>", "relation": "requires|produces|unclear", \
"confidence": <number 0.0-1.0>, "rationale": "<at most one short sentence>"}]}

Return exactly one item for every id given, with the id echoed verbatim."""

REPROMPT_SUFFIX = (
    "\n\nYour previous reply could not be parsed. Return ONLY the JSON object "
    'described above - no prose, no code fences, no explanation outside the '
    '"rationale" fields.'
)

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class MalformedResponse(Exception):
    """The model's reply did not match the Section 7 output contract."""


@dataclass(frozen=True)
class PromptItem:
    """One unit of work as the model sees it."""

    id: str
    primitive: str
    state: str
    description: str

    def render(self) -> str:
        return (
            f"id: {self.id}\n"
            f"primitive: {self.primitive}\n"
            f"state: {self.state}\n"
            f"description: {self.description}"
        )


def build_user_message(items: Iterable[PromptItem]) -> str:
    rendered = [item.render() for item in items]
    return (
        f"Classify each of the following {len(rendered)} relationship(s).\n\n"
        + "\n\n".join(rendered)
    )


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text.strip())


def _coerce_items(payload: Any) -> list[dict[str, Any]]:
    """Accept the documented object, a bare array, or a single item object."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("items", "results", "classifications", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        if "relation" in payload:
            return [payload]
    raise MalformedResponse(f"expected an object with an 'items' array, got {type(payload).__name__}")


def _confidence(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        # A missing or unparseable confidence is not a reason to discard an
        # otherwise valid label; the gate then decides on the neutral value.
        return 0.0
    return min(1.0, max(0.0, value))


def parse_response(text: str, model: str) -> dict[str, Verdict]:
    """Parse the model's reply into `{item id: Verdict}`.

    Raises MalformedResponse if the reply is not JSON, is not shaped like the
    contract, or uses a label outside the closed vocabulary.
    """
    if not text or not text.strip():
        raise MalformedResponse("the model returned an empty message")

    try:
        payload = json.loads(_strip_fences(text))
    except json.JSONDecodeError as error:
        raise MalformedResponse(f"reply is not valid JSON ({error})") from None

    verdicts: dict[str, Verdict] = {}
    for row in _coerce_items(payload):
        identifier = row.get("id")
        label = row.get("relation", row.get("label"))
        if identifier is None or not isinstance(label, str):
            raise MalformedResponse(f"item is missing 'id' or 'relation': {row!r}")
        normalized = label.strip().lower()
        if normalized not in LABELS:
            raise MalformedResponse(
                f"label {label!r} is outside the closed vocabulary {LABELS}"
            )
        rationale = row.get("rationale") or row.get("reason") or ""
        verdicts[str(identifier)] = Verdict(
            label=normalized,
            # An abstention carries no useful confidence; force it to 0 so it can
            # never be read as a strong signal by anything downstream.
            confidence=0.0 if normalized == LABEL_UNCLEAR else _confidence(row.get("confidence")),
            rationale=str(rationale).strip(),
            method=METHOD_LLM,
            model=model,
        )

    if not verdicts:
        raise MalformedResponse("the reply contained no items")
    return verdicts


def item_for(identifier: str, bundle: Bundle, primitive: str, state: str) -> PromptItem:
    return PromptItem(
        id=identifier,
        primitive=primitive,
        state=state,
        description=" ".join(bundle.description.split()),
    )
