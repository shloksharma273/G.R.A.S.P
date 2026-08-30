"""The OpenAI-compatible transport (PRD Section 7, FR-7).

Talks to any OpenAI-compatible `/chat/completions` endpoint - the project is
configured against OpenRouter - using only the standard library, so Station 3
adds no dependency to a project that deliberately has one.

FR-7 is the whole point of this module: transient failures retry with bounded
backoff, and a persistent failure raises `ServiceError` so the caller can park
the affected items rather than aborting the batch.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .config import LLMConfig

#: Status codes worth retrying: rate limiting and server-side faults.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524})


class ServiceError(Exception):
    """The LLM was unusable for this request after every retry (FR-7)."""


class RequestCapReached(ServiceError):
    """The per-run request cap was hit before this batch could be sent."""


def build_payload(config: LLMConfig, system: str, user: str) -> dict[str, Any]:
    """The request body, with Section 7's determinism settings applied."""
    payload: dict[str, Any] = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if config.json_mode:
        payload["response_format"] = {"type": "json_object"}
    if not config.reasoning:
        # Thinking-by-default models otherwise spend the entire token budget
        # reasoning and return an empty message; this is a closed-vocabulary
        # classification, so the reasoning is not worth paying for.
        payload["reasoning"] = {"enabled": False}
    return payload


def extract_message(body: dict[str, Any]) -> str:
    """The assistant text from an OpenAI-compatible response."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        error = body.get("error")
        if error:
            raise ServiceError(f"provider returned an error: {error}")
        raise ServiceError("provider returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        finish = choices[0].get("finish_reason")
        if finish == "length":
            raise ServiceError(
                "the model hit max_tokens before emitting any content "
                "(raise LLM_MAX_TOKENS, or keep LLM_REASONING off)"
            )
        raise ServiceError(f"the model returned an empty message (finish_reason={finish!r})")
    return content


class Provider:
    """One configured chat-completions endpoint, with retries and a spend cap."""

    def __init__(self, config: LLMConfig, sleep: Callable[[float], None] | None = None) -> None:
        self.config = config
        self._sleep = sleep if sleep is not None else time.sleep
        self.requests_made = 0
        self.retries_made = 0

    @property
    def cap_reached(self) -> bool:
        """Whether the per-run request budget is spent.

        `max_requests` is a plain count, so 0 means "make no call at all" - which
        is how --dry-run is expressed. Raise it to lift the cap.
        """
        return self.requests_made >= self.config.max_requests

    def complete(self, system: str, user: str) -> str:
        """One completion, retrying transient failures with bounded backoff."""
        if self.cap_reached:
            raise RequestCapReached(
                f"per-run request cap reached (LLM_MAX_REQUESTS={self.config.max_requests})"
            )

        payload = build_payload(self.config, system, user)
        last: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            if attempt:
                self.retries_made += 1
                # Bounded exponential backoff, deterministic (no jitter): a
                # reproducible station should not have a random sleep in it.
                self._sleep(self.config.backoff_seconds * (2 ** (attempt - 1)))
            try:
                self.requests_made += 1
                return extract_message(self._post(payload))
            except ServiceError:
                # Already classified as fatal (bad key, truncated reply): a retry
                # would fail identically.
                raise
            except Exception as error:
                if not is_retryable(error):
                    raise as_service_error(error, self.config) from error
                last = error
                continue

        raise ServiceError(
            f"the LLM endpoint failed after {self.config.max_retries + 1} attempt(s): "
            f"{describe(last)}"
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The transport, and only the transport.

        Failures propagate as their own exception types; whether one is worth
        another attempt is `is_retryable`'s decision, not this method's. Keeping
        the two apart is what lets an unexpected transport fault - a reset
        connection, an SSL hiccup - retry like the ones urllib names.
        """
        request = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                # Courtesy identification for OpenRouter; harmless elsewhere.
                "X-Title": "GRASP Bridge Station 3",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def is_retryable(error: BaseException) -> bool:
    """Whether another attempt could plausibly succeed (FR-7)."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_STATUS
    return isinstance(
        error,
        (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError),
    )


def as_service_error(error: BaseException, config: LLMConfig) -> ServiceError:
    """Turn a fatal transport failure into an actionable message, never leaking the key."""
    if isinstance(error, urllib.error.HTTPError):
        detail = _read_error(error)
        if error.code in (401, 403):
            return ServiceError(
                f"the LLM endpoint rejected the API key (HTTP {error.code}). "
                "Check LLM_API_KEY / OPENROUTER_API_KEY; the key is not printed."
            )
        if error.code == 404:
            return ServiceError(
                f"the endpoint or model was not found (HTTP 404): {detail}. "
                f"Check LLM_BASE_URL ({config.base_url}) and LLM_MODEL ({config.model})."
            )
        return ServiceError(f"HTTP {error.code}: {detail}")
    return ServiceError(describe(error))


def describe(error: BaseException | None) -> str:
    if error is None:  # pragma: no cover - only reachable with max_retries < 0
        return "no detail"
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}: {_read_error(error)}"
    if isinstance(error, urllib.error.URLError):
        return f"cannot reach the endpoint: {error.reason}"
    return f"{error.__class__.__name__}: {error}"


def _read_error(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", "replace")
    except Exception:  # pragma: no cover - body already consumed
        return error.reason or "no detail"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body[:300]
    message = parsed.get("error")
    if isinstance(message, dict):
        message = message.get("message", message)
    return str(message or parsed)[:300]
