"""Running the bridge over one project: knowledge graph in, PlanGraph out.

The five stations already exist as libraries and the CLIs already chain them by
piping JSON between processes. This drives the same five in one process, for one
project, reporting where it has got to — because a build takes long enough that a
page showing nothing would be indistinguishable from a page that has hung.

    Station 1  read        {project}_Entities / _Relations -> bundles
    Station 2  classify    bundles -> typed edges, plus the one ambiguous bucket
    Station 3  disambiguate  requires vs produces, the only station that pays a model
    Station 4  normalize   canonical direction, derived `precedes`, cycle guard
    Station 5  write       one scoped subgraph per skill

Nothing here re-implements a station. The stages are thin calls and the counts
are read off each station's own result, so the UI and the CLIs cannot disagree
about what a build did.

**This is the one place the web server writes.** Everything else in `grasp_web`
is read-only, and the PRD keeps Layer 2 that way on purpose. A build is the
exception the user asks for explicitly, so it is confirmed in the browser before
it starts, and a rebuild names the scopes it will replace before it replaces
them - Station 5's scoped write deletes a skill's existing subgraph first.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Callable

from kg_read_harness.config import Config
from kg_read_harness.errors import ConfigError, HarnessError

#: Stage labels, in order. The UI shows these verbatim, so they say what is
#: happening rather than naming a station number nobody outside this repo knows.
STAGE_READ = "reading the knowledge graph"
STAGE_CLASSIFY = "typing the relationships"
STAGE_DISAMBIGUATE = "resolving requires vs produces"
STAGE_NORMALIZE = "deriving the ordering"
STAGE_WRITE = "writing the PlanGraph"

STAGES = (STAGE_READ, STAGE_CLASSIFY, STAGE_DISAMBIGUATE, STAGE_NORMALIZE, STAGE_WRITE)


@dataclass
class StationReport:
    """What one station did, in its own terms."""

    stage: str
    detail: str = ""
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "detail": self.detail, "counts": dict(self.counts)}


@dataclass
class BuildResult:
    project: str
    dry_run: bool = False
    stations: list[StationReport] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    written: dict[str, int] = field(default_factory=dict)
    #: What the model cost, so a slow build is legible as model or database.
    llm: dict[str, Any] = field(default_factory=dict)
    parked: int = 0
    failed: str = ""
    graph: str = ""

    @property
    def ok(self) -> bool:
        return not self.failed and bool(self.scopes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "dry_run": self.dry_run,
            "ok": self.ok,
            "stations": [s.to_dict() for s in self.stations],
            "scopes": list(self.scopes),
            "replaced": list(self.replaced),
            "written": dict(self.written),
            "llm": dict(self.llm),
            "parked": self.parked,
            "failed": self.failed,
            "graph": self.graph,
        }


def project_config(base: Config, project: str) -> Config:
    """Station 1's config aimed at one project's collections.

    The collection names are re-derived rather than inherited: the environment's
    `ENTITY_COLLECTION` (if any) names one project's collection, and reusing it
    for another would read the wrong graph and report it as the right one.
    """
    return dataclasses.replace(
        base,
        project_name=project,
        entity_collection=f"{project}_Entities",
        relation_collection=f"{project}_Relations",
    )


def writer_config(project: str, dry_run: bool, env: Any = None):
    """Station 5's config for one project, with its own prefix."""
    from plangraph_writer.config import load_writer_config

    config = load_writer_config(env, dry_run=dry_run)
    return dataclasses.replace(
        config,
        arango=project_config(config.arango, project),
        prefix=project,
        skill_scope=None,  # one scope per skill, derived by the partition
    )


def build(
    project: str,
    db: Any,
    *,
    dry_run: bool = False,
    env: Any = None,
    provider: Any = None,
    write_db: Any = None,
    on_stage: Callable[[str, StationReport | None], None] | None = None,
) -> BuildResult:
    """Run all five stations over `project`. Returns what each one did.

    `db` reads; `write_db` writes, defaulting to `db`. They are separate because
    Station 1 may run as a read-only user while Station 5 needs a writing one,
    which the writer config already supports.
    """
    from direction_normalizer import normalize as normalize_edges
    from kg_read_harness.read import ReadStats, read_relationship_bundles
    from plangraph_writer import build as build_plangraph
    from plangraph_writer.partition import partition_by_skill
    from plangraph_writer.writer import write_plangraph

    announce = on_stage if on_stage is not None else (lambda _stage, _report: None)
    result = BuildResult(project=project, dry_run=dry_run)

    def finish(stage: str, detail: str, **counts: int) -> StationReport:
        report = StationReport(stage=stage, detail=detail, counts=dict(counts))
        result.stations.append(report)
        announce(stage, report)
        return report

    config = writer_config(project, dry_run=dry_run, env=env)
    result.graph = config.schema.graph_name

    # --- Station 1: read ----------------------------------------------------
    announce(STAGE_READ, None)
    stats = ReadStats()
    bundles = list(read_relationship_bundles(db, config.arango, stats=stats))
    if not bundles:
        result.failed = (
            f"{project}_Relations holds no {config.arango.relation_type} edges between "
            "known entities, so there is nothing to build from. Check that AutoGraph "
            "finished extracting the knowledge graph."
        )
        return result
    finish(
        STAGE_READ,
        f"{len(bundles)} relationship bundle(s) from {stats.rows_scanned} edge(s)",
        bundles=len(bundles),
        scanned=stats.rows_scanned,
        skipped=stats.skipped_dangling,
    )

    # --- Station 2: type by entity-type pair --------------------------------
    announce(STAGE_CLASSIFY, None)
    from rule_preclassifier import classify

    classified = classify(bundles)
    finish(
        STAGE_CLASSIFY,
        f"{len(classified.stamped)} typed by rule, {len(classified.deferred)} need a model",
        stamped=len(classified.stamped),
        deferred=len(classified.deferred),
        parked=len(classified.parked),
    )

    # --- Station 3: requires vs produces ------------------------------------
    edges = list(classified.stamped)
    if classified.deferred:
        announce(STAGE_DISAMBIGUATE, None)
        resolved = _disambiguate(classified.deferred, provider=provider, env=env)
        edges.extend(resolved.stamped)
        result.parked += len(resolved.parked)
        calls = resolved.calls
        # Where each verdict actually came from. Worth separating: on the live
        # roboticsPlanner build the pre-pass settles all 21 with no request at
        # all, and reporting that as "21 settled" beside a model name would
        # suggest a model call that never happened.
        from llm_disambiguator.model import METHOD_LEXICAL

        lexical = sum(1 for e in resolved.stamped if getattr(e, "method", "") == METHOD_LEXICAL)
        by_model = len(resolved.stamped) - lexical
        result.llm = {
            "model": resolved.model if by_model else None,
            "requests": calls.requests,
            "items_sent": calls.items_sent,
            "cache_hits": calls.cache_hits,
            "retries": calls.retries,
            "settled": len(resolved.stamped),
            "lexical": lexical,
            "by_model": by_model,
            "parked": len(resolved.parked),
        }
        detail = f"{lexical} settled by the lexical pre-pass"
        if by_model:
            detail += f", {by_model} by the model ({calls.requests} request(s)"
            detail += f", {calls.cache_hits} from cache)" if calls.cache_hits else ")"
        if resolved.parked:
            detail += f", {len(resolved.parked)} parked"
        finish(
            STAGE_DISAMBIGUATE,
            detail,
            settled=len(resolved.stamped),
            lexical=lexical,
            by_model=by_model,
            parked=len(resolved.parked),
            requests=calls.requests,
            cache_hits=calls.cache_hits,
        )
    else:
        finish(STAGE_DISAMBIGUATE, "nothing ambiguous; no model call needed")

    result.parked += len(classified.parked)

    # --- Station 4: direction and ordering ----------------------------------
    announce(STAGE_NORMALIZE, None)
    normalized = normalize_edges(edges)
    result.parked += len(normalized.parked)
    finish(
        STAGE_NORMALIZE,
        f"{len(normalized.finalized)} edge(s) oriented, {len(normalized.derived)} "
        "ordering(s) derived"
        + (
            f", {len(normalized.refused_derived)} refused by the cycle guard"
            if normalized.refused_derived
            else ""
        ),
        finalized=len(normalized.finalized),
        derived=len(normalized.derived),
        refused=len(normalized.refused_derived),
        parked=len(normalized.parked),
    )

    # --- Station 5: write one scoped subgraph per skill ----------------------
    announce(STAGE_WRITE, None)
    partitions = partition_by_skill(normalized.finalized, normalized.derived)
    if not partitions:
        result.failed = (
            "no skill in this knowledge graph decomposes into any primitive, so "
            "there is nothing that can be planned. AutoGraph extracted no "
            "SKILL -> PRIMITIVE relationships."
        )
        return result

    handle = write_db if write_db is not None else db
    totals: dict[str, int] = {}
    for partition in partitions:
        plan = build_plangraph(
            partition.finalized,
            partition.derived,
            config.schema,
            skill_scope=partition.scope,
            build_id=config.build_id,
        )
        report = write_plangraph(plan, handle, config)
        result.scopes.append(partition.scope)
        for key, value in report.written.items():
            totals[key] = totals.get(key, 0) + int(value)
        # A scope with something to purge already existed: this build replaced it
        # rather than creating it, which is the thing a rebuild has to own up to.
        if report.purged_total:
            result.replaced.append(partition.scope)

    result.written = totals
    finish(
        STAGE_WRITE,
        ("would write " if dry_run else "wrote ")
        + f"{len(result.scopes)} skill scope(s) into {config.schema.graph_name}",
        scopes=len(result.scopes),
    )
    return result


def _disambiguate(deferred: list[Any], provider: Any = None, env: Any = None):
    """Station 3, with a clear message when no model is configured.

    Without a key the deferred bucket cannot be settled at all, and a PlanGraph
    missing every precondition is worse than no PlanGraph - so this refuses
    rather than writing a subgraph whose ordering is empty.
    """
    from llm_disambiguator import disambiguate
    from llm_disambiguator.config import load_llm_config

    try:
        llm = load_llm_config(env)
    except ConfigError as error:
        raise HarnessError(
            f"{len(deferred)} relationship(s) need a model to settle whether they are "
            f"a precondition or an effect, but no LLM key is configured ({error.message})",
            "set OPENROUTER_API_KEY (or LLM_API_KEY) and build again. Without it the "
            "PlanGraph would have no preconditions, so nothing could be ordered.",
        ) from None
    return disambiguate(deferred, llm, provider=provider)
