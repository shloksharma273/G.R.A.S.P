"""Rule Pre-Classifier — Bridge Station 2 ("deterministic edge-type routing").

Consumes the relationship bundles emitted by Station 1 and assigns each a planning
edge type using nothing but its entity-type pair: a pure, deterministic lookup
with no LLM, no database and no network. Bundles the table settles are stamped and
go to Station 4; the one genuinely ambiguous pair (PRIMITIVE-STATE) is deferred to
Station 3; everything else is parked with exactly one reason code for quality
review. Every input leaves in exactly one bucket, and that invariant is checked.

The whole policy is `table.py`; `classify(bundles)` is the entry point.
"""

from .classifier import classify
from .model import (
    ClassificationResult,
    ConservationError,
    DeferredBundle,
    Orientation,
    ParkedBundle,
    StampedEdge,
)
from .report import dump_json, print_report
from .table import DECISION_TABLE, REASON_GROUPS, REASON_PRIORITY

__version__ = "1.0.0"

__all__ = [
    "classify",
    "ClassificationResult",
    "ConservationError",
    "DeferredBundle",
    "Orientation",
    "ParkedBundle",
    "StampedEdge",
    "print_report",
    "dump_json",
    "DECISION_TABLE",
    "REASON_PRIORITY",
    "REASON_GROUPS",
    "__version__",
]
