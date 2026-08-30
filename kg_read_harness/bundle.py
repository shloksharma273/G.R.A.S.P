"""The canonical bundle — the unit of work every downstream bridge station consumes.

PRD Section 7. The shape is deliberately frozen here: Station 2 (rule-based
pre-classification) routes on (source.type, target.type), so changing this later
would ripple through the whole bridge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Entity:
    """An endpoint of a relationship: its name and its ontology type."""

    name: str
    type: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type}


@dataclass(frozen=True)
class Bundle:
    """One entity-to-entity relationship, resolved on both ends."""

    relation_key: str
    source: Entity
    target: Entity
    description: str

    @property
    def type_pair(self) -> tuple[str, str]:
        """The (source_type, target_type) pair Station 2 routes on."""
        return (self.source.type, self.target.type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_key": self.relation_key,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "description": self.description,
        }
