"""Layer 2 — Planning.

Converts a free-text command into an ordered plan by retrieving one goal node
from the PlanGraph, traversing its dependency subgraph, topologically ordering
it, and having an LLM phrase the result into a `plan.json` contract.

Two decisions define its shape. The LLM's role is **thin** — it phrases, it never
orders or invents, and a grounding guard enforces that. And retrieval is a single
forward traversal, not a backward-chaining planner: similarity search only
locates the starting point, and the graph's structure supplies correctness and
order.

Read-only over the PlanGraph. `plan_command(command, db, config)` is the entry
point.
"""

from .compose import compose, template_descriptions
from .config import PlannerConfig, load_planner_config
from .order import CyclicPlan, Ordering, order_plan
from .pipeline import plan_command
from .plan import Clarification, Plan, Step
from .report import dump_json, print_clarification, print_plan
from .retrieve import Candidate, GoalResolution, LexicalRetriever, resolve_goal
from .traverse import IncompletePlanGraph, Subgraph, retrieve_subgraph

__version__ = "1.0.0"

__all__ = [
    "plan_command",
    "PlannerConfig",
    "load_planner_config",
    "Plan",
    "Step",
    "Clarification",
    "GoalResolution",
    "Candidate",
    "LexicalRetriever",
    "resolve_goal",
    "Subgraph",
    "retrieve_subgraph",
    "IncompletePlanGraph",
    "Ordering",
    "order_plan",
    "CyclicPlan",
    "compose",
    "template_descriptions",
    "print_plan",
    "print_clarification",
    "dump_json",
    "__version__",
]
