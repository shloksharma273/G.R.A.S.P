"""Knowledge Graph Read Harness — Bridge Station 1 ("Read").

A read-only harness that connects to the ArangoDB knowledge graph produced by
AutoGraph, reads every entity-to-entity relationship, and prints each as a typed
bundle: source entity, target entity, both types, and the relationship's
free-text description. It classifies nothing and writes nothing.

The reusable core is `read_relationship_bundles(db, config)`, whose yielded
`Bundle` is the canonical unit of work for every later bridge station.
"""

from .bundle import Bundle, Entity
from .config import Config, load_config
from .read import ReadStats, read_bundles, read_relationship_bundles
from .validate import ValidationReport, validate_kg

__version__ = "1.0.0"

__all__ = [
    "Bundle",
    "Entity",
    "Config",
    "load_config",
    "ReadStats",
    "read_bundles",
    "read_relationship_bundles",
    "ValidationReport",
    "validate_kg",
    "__version__",
]
