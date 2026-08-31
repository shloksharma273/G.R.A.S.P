"""Rulebook Generator — video link to canonical rulebook, gated by validation.

Turns a captioned how-to video into a rulebook in the project's own format:
fetch the captions, reconstruct a structured recipe of primitives, objects,
states, preconditions and effects with a schema-constrained LLM pass, render
canonical markdown, and gate the result by running it through the project's own
bridge.

It sits entirely **upstream of Layer 1** and changes nothing downstream — its
only output is a rulebook in the format the pipeline already consumes, so
Stations 1-5 and Layer 2 cannot tell a generated rulebook from an authored one.

The hard part is not fetching captions but reconstruction: narration skips the
obvious and almost never states a precondition, so the preconditions — the
load-bearing part of a plan — are inferred. That is exactly why the validation
gate is mandatory rather than advisory.
"""

from .cache import PayloadCache
from .config import GeneratorConfig, load_generator_config
from .extract import ExtractionFailed, NotProcedural, extract
from .ingest import ingest, write_rulebook
from .parse import parse_file, parse_text
from .pipeline import GenerationResult, generate
from .render import render
from .report import dump_json, print_report
from .schema import ACCEPT, FLAG, REJECT, Primitive, Rulebook
from .transcript import NoCaptions, Transcript, from_file, from_youtube, video_id
from .validate import ValidationReport, to_bundles, validate

__version__ = "1.0.0"

__all__ = [
    "generate",
    "GenerationResult",
    "GeneratorConfig",
    "load_generator_config",
    "Rulebook",
    "Primitive",
    "render",
    "parse_text",
    "parse_file",
    "validate",
    "ValidationReport",
    "to_bundles",
    "extract",
    "ExtractionFailed",
    "NotProcedural",
    "Transcript",
    "from_youtube",
    "from_file",
    "video_id",
    "NoCaptions",
    "PayloadCache",
    "ingest",
    "write_rulebook",
    "print_report",
    "dump_json",
    "ACCEPT",
    "FLAG",
    "REJECT",
    "__version__",
]
