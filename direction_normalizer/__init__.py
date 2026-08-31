"""Direction Normalizer & Order Resolver — Bridge Station 4.

Takes the typed edges stamped by Stations 2 and 3 and does two things: it fixes
every edge to a canonical direction so the planner can make blind assumptions,
and it derives `precedes` ordering between primitives by **state chaining** —

    for any state S, every primitive that produces S precedes every primitive
    that requires S.

That reconstructs execution order the rulebook never states. Deterministic, no
LLM: Station 3 remains the system's only model call. `normalize(edges)` is the
entry point.
"""

from .adapt import adapt, adapt_all, stamped_edges_from_json
from .chaining import chain
from .cycles import OrderingGraph
from .model import (
    DerivedEdge,
    FinalizedEdge,
    InputEdge,
    NormalizationResult,
)
from .normalizer import normalize, ordering_graph
from .report import dump_json, print_report

__version__ = "1.0.0"

__all__ = [
    "normalize",
    "ordering_graph",
    "chain",
    "adapt",
    "adapt_all",
    "stamped_edges_from_json",
    "OrderingGraph",
    "NormalizationResult",
    "FinalizedEdge",
    "DerivedEdge",
    "InputEdge",
    "print_report",
    "dump_json",
    "__version__",
]
