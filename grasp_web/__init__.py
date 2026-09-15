"""A browser front end for Layer 2 — ask in plain English, get an ordered plan.

A thin JSON API over the planner plus a single page that talks to it. Every
decision the page displays is already made by Layer 2; nothing here plans, and
nothing here writes.
"""

# Defined before the submodule imports below: `server` reads it back off this
# package, so binding it first is what keeps that from being a circular import.
__version__ = "1.0.0"

from .api import PlannerService  # noqa: E402
from .generate import GeneratorService  # noqa: E402
from .server import Handler, make_server  # noqa: E402

__all__ = [
    "PlannerService",
    "GeneratorService",
    "make_server",
    "Handler",
    "__version__",
]
