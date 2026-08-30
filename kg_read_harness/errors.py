"""Typed failure modes for the KG read harness.

Every condition in PRD Section 12 maps to exactly one exception class here, and
every exception carries a human message plus the most likely fix (FR-8) and a
distinct non-zero exit code (Section 10, "clear exit codes").
"""

from __future__ import annotations

# Exit codes. 0 is success; everything else is a distinct failure class so the
# harness can be scripted around.
EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_CONFIG = 2
EXIT_AUTH = 3
EXIT_CONNECTIVITY = 4
EXIT_MISSING_COLLECTION = 5
EXIT_ATTRIBUTE_MISMATCH = 6
EXIT_EMPTY_RESULT = 7
EXIT_READ_ONLY_VIOLATION = 8
EXIT_INTERRUPTED = 130


class HarnessError(Exception):
    """Base class: a failure the developer can act on."""

    exit_code = EXIT_UNEXPECTED
    label = "error"

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        lines = [f"{self.label}: {self.message}"]
        if self.hint:
            lines.append(f"  likely fix: {self.hint}")
        return "\n".join(lines)


class ConfigError(HarnessError):
    exit_code = EXIT_CONFIG
    label = "configuration error"


class AuthError(HarnessError):
    exit_code = EXIT_AUTH
    label = "authentication failed"


class ConnectivityError(HarnessError):
    exit_code = EXIT_CONNECTIVITY
    label = "cannot reach ArangoDB"


class CollectionNotFoundError(HarnessError):
    exit_code = EXIT_MISSING_COLLECTION
    label = "collection not found"


class AttributeMismatchError(HarnessError):
    exit_code = EXIT_ATTRIBUTE_MISMATCH
    label = "attribute mismatch"


class EmptyResultError(HarnessError):
    exit_code = EXIT_EMPTY_RESULT
    label = "empty result"


class ReadOnlyViolation(HarnessError):
    """Internal guard: a query that could mutate the KG was assembled.

    Never expected to fire; it exists so the single most important constraint in
    Section 10 is enforced by code and not just by intent.
    """

    exit_code = EXIT_READ_ONLY_VIOLATION
    label = "read-only guard tripped"
