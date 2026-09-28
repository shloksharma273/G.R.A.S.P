"""Task decomposition — a compound command into ordered Layer 2 plans.

Sits in front of Layer 2. The LLM splits the command into subtasks and picks a
catalog skill for each; Layer 2 plans each one exactly as it would a single
command; the sub-plans are merged into one run with repeated bring-up left out.
Without an LLM, a deterministic splitter cuts on connectors ("then", "and") and
Layer 2's own goal resolution picks the skills.

Read-only over the PlanGraph, like Layer 2. `plan_compound(command, db, config)`
is the entry point.
"""

from .compound import CompoundPlan, SubtaskResult, goal_steps, merge, plan_compound
from .decompose import Decomposition, Subtask, decompose, split_by_rules
from .report import dump_json, print_compound, print_result

__version__ = "1.0.0"

__all__ = [
    "plan_compound",
    "CompoundPlan",
    "SubtaskResult",
    "goal_steps",
    "merge",
    "decompose",
    "split_by_rules",
    "Decomposition",
    "Subtask",
    "print_compound",
    "print_result",
    "dump_json",
    "__version__",
]
