"""The AutoGraph pipeline: a module of rulebooks becomes a PlanGraph.

Everything upstream of Station 1 used to be done by hand in the AutoGraph UI -
upload the rulebooks, build the corpus graph, run the strategizer, fix each
cluster's ontology, orchestrate the importer. This drives those same steps
through AutoGraph's HTTP API, then hands the knowledge graph to the bridge:

    rulebooks -> File Manager -> corpus graph -> strategies -> ontology
              -> knowledge graph -> PlanGraph

Each stage checks the project overview before it acts, so a rerun resumes, and
a plain run only reports what it would do.
"""

from .client import ApiError, AutoGraph, Platform
from .config import PipelineConfig, load_pipeline_config
from .pipeline import (
    STAGES,
    Pipeline,
    PipelineError,
    PipelineResult,
    Rulebook,
    StageReport,
    encode_module,
    run_pipeline,
)

__version__ = "0.1.0"

__all__ = [
    "ApiError",
    "AutoGraph",
    "Pipeline",
    "PipelineConfig",
    "PipelineError",
    "PipelineResult",
    "Platform",
    "Rulebook",
    "STAGES",
    "StageReport",
    "encode_module",
    "load_pipeline_config",
    "run_pipeline",
]
