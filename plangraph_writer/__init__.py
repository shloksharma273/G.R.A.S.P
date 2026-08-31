"""The PlanGraph & Writer — Bridge Station 5.

Defines the project's own typed dependency graph and the Writer that populates
it. Takes Station 4's finalized and derived edges, upserts typed vertices and
edges into dedicated ArangoDB collections, and builds the vector index Layer 2
uses for goal resolution.

The PlanGraph deliberately lives in its own collections, separate from
AutoGraph's `{project}_kg`: AutoGraph owns and rebuilds its knowledge graph, and
a rebuild would wipe anything stored there. Identity is scoped per task, so one
rulebook's edges can never bleed into another's plan.

`build(...)` plans the write purely; `write_plangraph(...)` applies it.
"""

from .build import build, infer_scope
from .config import WriterConfig, load_writer_config
from .guard import GuardedDatabase, IsolationViolation
from .identity import edge_key, scope_key, vertex_key
from .records import PlanEdge, PlanGraphBuild, PlanVertex, WriteReport
from .report import dump_json, print_report
from .schema import Schema
from .writer import ensure_schema, verify, write_plangraph

__version__ = "1.0.0"

__all__ = [
    "build",
    "infer_scope",
    "write_plangraph",
    "ensure_schema",
    "verify",
    "Schema",
    "WriterConfig",
    "load_writer_config",
    "GuardedDatabase",
    "IsolationViolation",
    "PlanGraphBuild",
    "PlanVertex",
    "PlanEdge",
    "WriteReport",
    "vertex_key",
    "edge_key",
    "scope_key",
    "print_report",
    "dump_json",
    "__version__",
]
