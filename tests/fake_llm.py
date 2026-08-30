"""A stand-in for an OpenAI-compatible endpoint.

Station 3 is the only station that talks to a network service, so every test runs
against this instead. It records the prompts it was given, can be scripted to
return malformed output or to fail, and raises if a test ever manages to reach the
real transport.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from llm_disambiguator.config import LLMConfig, load_llm_config
from llm_disambiguator.prompt import LABELS
from llm_disambiguator.provider import Provider, ServiceError


def config(**overrides: Any) -> LLMConfig:
    """A valid LLM config that names no real endpoint."""
    env = {
        "LLM_API_KEY": "test-key-not-real",
        "LLM_BASE_URL": "https://llm.invalid/v1",
        "LLM_MODEL": "test/model-1",
        "LLM_CACHE": "0",
    }
    env.update({key: str(value) for key, value in overrides.items()})
    return load_llm_config(env)


class FakeProvider(Provider):
    """A Provider whose transport is a Python function.

    `responder(system, user)` returns the assistant text. Raise `ServiceError`
    from it to simulate a persistent failure; the retry/backoff path is exercised
    separately through `_post`.
    """

    def __init__(self, config: LLMConfig, responder: Callable[[str, str], str]) -> None:
        super().__init__(config, sleep=lambda _seconds: None)
        self._responder = responder
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        if self.cap_reached:
            from llm_disambiguator.provider import RequestCapReached

            raise RequestCapReached(
                f"per-run request cap reached (LLM_MAX_REQUESTS={self.config.max_requests})"
            )
        self.requests_made += 1
        self.prompts.append((system, user))
        return self._responder(system, user)

    def _post(self, payload):  # pragma: no cover - must never be reached
        raise AssertionError("a test attempted a real HTTP request")


def parse_items(user: str) -> list[dict[str, str]]:
    """The items a rendered user message contains, as `{id, primitive, state, description}`."""
    items = []
    for block in user.split("\n\n")[1:]:
        fields = {}
        for line in block.splitlines():
            if ": " in line:
                key, _, value = line.partition(": ")
                fields[key.strip()] = value.strip()
        if "id" in fields:
            items.append(fields)
    return items


def responder_from(labels: dict[str, tuple[str, float]], default=("unclear", 0.0)):
    """A responder that answers by looking each item's description up in `labels`.

    Keys are matched as substrings of the description, so a test can say
    "anything containing 'precondition' is requires" without repeating the text.
    """

    def respond(_system: str, user: str) -> str:
        answers = []
        for item in parse_items(user):
            label, confidence = default
            for needle, verdict in labels.items():
                if needle.lower() in item.get("description", "").lower():
                    label, confidence = verdict
                    break
            assert label in LABELS, f"test scripted an invalid label: {label!r}"
            answers.append(
                {
                    "id": item["id"],
                    "relation": label,
                    "confidence": confidence,
                    "rationale": f"scripted verdict for {item['primitive']}",
                }
            )
        return json.dumps({"items": answers})

    return respond


def always(label: str, confidence: float = 0.95):
    """A responder that gives every item the same verdict."""
    return responder_from({}, default=(label, confidence))


def failing(message: str = "endpoint unavailable"):
    def respond(_system: str, _user: str) -> str:
        raise ServiceError(message)

    return respond


def malformed(text: str = "Sure! Here are the classifications you asked for."):
    """A responder that never returns parseable output, on any attempt."""

    def respond(_system: str, _user: str) -> str:
        return text

    return respond


def malformed_once(then: Callable[[str, str], str]):
    """Malformed on the first call, then well-formed — the FR-4 reprompt path."""
    state = {"called": False}

    def respond(system: str, user: str) -> str:
        if not state["called"]:
            state["called"] = True
            return "I'd be happy to help with that!"
        return then(system, user)

    return respond
