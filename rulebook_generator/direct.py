"""The direct route: a rulebook file straight into the PlanGraph.

The normal path runs a rulebook through AutoGraph, which extracts a knowledge
graph that Station 1 then reads. That is the honest path, and the one worth
demonstrating — it proves a generated rulebook survives real extraction.

This is the shortcut, and it exists because the generator already needs it: the
validation gate grades a rulebook by turning it into Station 1 bundles and
running the real bridge over them. That machinery is exactly what an ingest
needs, so exposing it costs nothing and buys a path that works with AutoGraph
unavailable, in seconds rather than a build cycle.

What it skips and what it does not:

* skipped — AutoGraph, and Station 1's read. The bundles are constructed from
  the rulebook rather than extracted from a KG.
* skipped — Station 3. The rulebook already states which state is a precondition
  and which is an effect, so there is nothing for a model to adjudicate.
* run in full — Station 2 types every relationship, Station 4 derives the
  ordering and guards the cycle, Station 5 writes the scoped subgraph.

The consequence is worth being clear about: this measures the *rulebook*, not the
extraction. A rulebook that ingests cleanly here may still lose something on the
way through AutoGraph, and only the real path will tell you that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from direction_normalizer import normalize as normalize_edges
from plangraph_writer import build as build_plangraph
from plangraph_writer.config import WriterConfig
from plangraph_writer.records import PlanGraphBuild, WriteReport
from plangraph_writer.writer import write_plangraph

from .parse import parse_file
from .schema import Rulebook
from .validate import ValidationReport, to_stamped_edges, validate


@dataclass
class IngestResult:
    """One rulebook's journey into the PlanGraph."""

    path: str
    rulebook: Rulebook | None = None
    report: ValidationReport | None = None
    plan: PlanGraphBuild | None = None
    write: WriteReport | None = None
    skipped: str = ""
    #: precedes edges Station 4 derived from the rulebook's state chain.
    derived: int = 0

    @property
    def ok(self) -> bool:
        return self.plan is not None and not self.skipped

    @property
    def scope(self) -> str:
        return self.plan.skill_scope if self.plan else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "skill": self.rulebook.skill if self.rulebook else None,
            "skipped": self.skipped or None,
            "verdict": self.report.verdict if self.report else None,
            "issues": [i.code for i in self.report.issues] if self.report else [],
            "derived_precedes": self.derived,
            "plan": self.plan.to_dict() if self.plan else None,
            "write": self.write.to_dict() if self.write else None,
        }


@dataclass
class IngestRun:
    results: list[IngestResult] = field(default_factory=list)

    @property
    def written(self) -> list[IngestResult]:
        return [r for r in self.results if r.ok]

    @property
    def skipped(self) -> list[IngestResult]:
        return [r for r in self.results if r.skipped]

    def to_dict(self) -> dict[str, Any]:
        return {"rulebooks": [r.to_dict() for r in self.results]}


def ingest_rulebook(
    path: str | Path,
    db: Any,
    config: WriterConfig,
    require_verdict: bool = True,
) -> IngestResult:
    """Read one rulebook and write its subgraph (Stations 2, 4, 5).

    Args:
        require_verdict: refuse a rulebook the validation gate rejects. A
            rejected rulebook has a cycle or loses content on rendering, so
            writing it would put a subgraph in the PlanGraph that cannot be
            planned. Pass False to write it anyway and inspect it.
    """
    result = IngestResult(path=str(path))
    rulebook = parse_file(path)
    result.rulebook = rulebook

    if not rulebook.primitives:
        result.skipped = "the file parsed to no primitives - is it a rulebook?"
        return result

    result.report = validate(rulebook)
    if require_verdict and result.report.verdict == "reject":
        fatal = [i.detail for i in result.report.issues if i.fatal]
        result.skipped = "rejected by the validation gate: " + (
            "; ".join(fatal) or "unusable"
        )
        return result

    stamped = to_stamped_edges(rulebook)
    normalized = normalize_edges(stamped)
    result.derived = len(normalized.derived)

    result.plan = build_plangraph(
        normalized.finalized,
        normalized.derived,
        config.schema,
        skill_scope=rulebook.skill,
    )
    result.write = write_plangraph(result.plan, db, config)
    return result


def ingest_all(
    paths: list[str | Path],
    db: Any,
    config: WriterConfig,
    require_verdict: bool = True,
) -> IngestRun:
    """Ingest several rulebooks, each into its own scope."""
    run = IngestRun()
    for path in paths:
        run.results.append(ingest_rulebook(path, db, config, require_verdict))
    return run


def find_rulebooks(target: str | Path) -> list[Path]:
    """A rulebook file, or every rulebook in a directory.

    A target that does not exist yields nothing rather than a path that will
    fail to open later - the caller can then say so once, about all of them.
    """
    path = Path(target)
    if path.is_dir():
        return sorted(path.glob("*.md"))
    return [path] if path.is_file() else []
