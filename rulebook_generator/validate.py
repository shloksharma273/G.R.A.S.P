"""Stage 4 — the round-trip validation gate (PRD Section 6, FR-5, FR-7).

Section 5: "the generator's quality lives or dies on inference — and that is
exactly why the round-trip validation gate is mandatory, not optional." Stage 2
is a model inferring preconditions nobody said out loud. This is what stands
between that inference and a plan a robot would try to execute.

The checks run the rulebook through the project's own pipeline rather than a
private reimplementation, which is the elegant part of the PRD: the graph the
bridge builds from the generated rulebook *is* the acceptance test.

    render -> parse            the markdown carries the whole intermediate
    -> Station 2               types every relationship
    -> Station 4               derives the ordering and guards the cycle
    -> the checks below        DAG, no orphans, reachable, producible goal

A verdict is one of accept / flag_for_review / reject. Nothing is ever silently
accepted (FR-7): a flag routes to a human, and only an accept may auto-ingest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from direction_normalizer import normalize as normalize_edges
from direction_normalizer import ordering_graph
from direction_normalizer.model import DerivedEdge
from kg_read_harness.bundle import Bundle, Entity
from llm_disambiguator.model import METHOD_LLM, ORIENTATION_IMPLIED_BY_LABEL, ResolvedEdge
from rule_preclassifier import classify
from rule_preclassifier.model import Orientation

from .parse import normalize as normalize_name
from .parse import parse_text
from .render import render
from .schema import ACCEPT, FLAG, REJECT, Rulebook

#: Issues that make the rulebook unusable rather than merely suspect.
FATAL = frozenset({"no_primitives", "round_trip_mismatch", "cycle", "no_goal_state"})

#: Beyond this many steps, a single "skill" is very likely several tasks fused
#: together - a compilation or a technique roundup. The corpus's rulebooks run to
#: six or eleven steps; forty is not one recipe.
SUSPICIOUS_PRIMITIVES = 25


@dataclass
class Issue:
    code: str
    detail: str

    @property
    def fatal(self) -> bool:
        return self.code in FATAL

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "fatal": self.fatal}


@dataclass
class ValidationReport:
    verdict: str = ACCEPT
    issues: list[Issue] = field(default_factory=list)
    primitives: int = 0
    states: int = 0
    objects: int = 0
    ordering_constraints: int = 0
    plan: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.verdict == ACCEPT

    def add(self, code: str, detail: str) -> None:
        self.issues.append(Issue(code=code, detail=detail))

    def settle(self) -> str:
        if any(issue.fatal for issue in self.issues):
            self.verdict = REJECT
        elif self.issues:
            self.verdict = FLAG
        else:
            self.verdict = ACCEPT
        return self.verdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "issues": [issue.to_dict() for issue in self.issues],
            "primitives": self.primitives,
            "states": self.states,
            "objects": self.objects,
            "ordering_constraints": self.ordering_constraints,
            "plan": self.plan,
        }


def to_bundles(rulebook: Rulebook) -> list[Bundle]:
    """The rulebook as Station 1 output, so the real bridge can grade it."""
    bundles: list[Bundle] = []

    def add(source, source_type, target, target_type, description):
        bundles.append(
            Bundle(
                relation_key=f"gen_r{len(bundles) + 1:03d}",
                source=Entity(source, source_type),
                target=Entity(target, target_type),
                description=description,
            )
        )

    for primitive in rulebook.primitives:
        add(
            rulebook.skill,
            "SKILL",
            primitive.name,
            "PRIMITIVE",
            f"The skill {rulebook.skill} is composed of the primitive action {primitive.name}.",
        )
    for primitive in rulebook.primitives:
        for state in primitive.produces:
            add(primitive.name, "PRIMITIVE", state, "STATE", f"After this action, {state} is true.")
        for state in primitive.requires:
            add(
                primitive.name, "PRIMITIVE", state, "STATE",
                f"This action requires that {state} is already true as a precondition.",
            )
        for obj in primitive.uses:
            add(primitive.name, "PRIMITIVE", obj, "OBJECT", f"The robot acts on the {obj}.")
    return bundles


def resolved_edge(bundle: Bundle, label: str) -> ResolvedEdge:
    """Station 3's record, with the label taken from the rulebook itself.

    The generator already knows which edges are preconditions and which are
    effects — it wrote them — so asking a model to re-derive that here would
    measure Station 3 rather than the rulebook.
    """
    return ResolvedEdge(
        bundle=bundle,
        edge_type=label,
        confidence=1.0,
        rationale="stated by the generated rulebook",
        method=METHOD_LLM,
        orientation=Orientation(
            head=bundle.source.name, tail=bundle.target.name, decided_by=ORIENTATION_IMPLIED_BY_LABEL
        ),
        model="rulebook_generator",
    )


def validate(rulebook: Rulebook, markdown: str | None = None) -> ValidationReport:
    """Run the rulebook through the bridge and grade it (FR-5)."""
    report = ValidationReport(
        primitives=len(rulebook.primitives),
        states=len(rulebook.states),
        objects=len(rulebook.objects),
    )

    if not rulebook.primitives:
        report.add("no_primitives", "the rulebook contains no primitive actions")
        report.settle()
        return report

    _check_round_trip(rulebook, markdown, report)
    _check_declarations(rulebook, report)

    edges = _bridge(rulebook, report)
    if edges is None:
        report.settle()
        return report

    _check_orphans(rulebook, report)
    _check_interfaces(rulebook, report)
    _check_goal_state(rulebook, report)
    _check_size(rulebook, report)

    report.settle()
    return report


def _check_round_trip(rulebook: Rulebook, markdown: str | None, report: ValidationReport) -> None:
    """`parse(render(x))` must recover `x` — the markdown is the deliverable."""
    text = markdown if markdown is not None else render(rulebook)
    recovered = parse_text(text)

    if recovered.skill != rulebook.skill:
        report.add(
            "round_trip_mismatch",
            f"the rendered markdown names the skill {recovered.skill!r}, not {rulebook.skill!r}",
        )
        return
    if recovered.primitive_names != rulebook.primitive_names:
        missing = set(rulebook.primitive_names) - set(recovered.primitive_names)
        report.add(
            "round_trip_mismatch",
            "the rendered markdown does not read back the same primitives"
            + (f"; lost: {', '.join(sorted(missing))}" if missing else ""),
        )
        return

    for primitive in rulebook.primitives:
        back = recovered.by_name(primitive.name)
        if back is None:
            continue
        if sorted(back.requires) != sorted(primitive.requires):
            report.add(
                "round_trip_mismatch",
                f"{primitive.name}: preconditions do not survive rendering "
                f"({sorted(primitive.requires)} -> {sorted(back.requires)})",
            )
            return
        if sorted(back.produces) != sorted(primitive.produces):
            report.add(
                "round_trip_mismatch",
                f"{primitive.name}: effects do not survive rendering "
                f"({sorted(primitive.produces)} -> {sorted(back.produces)})",
            )
            return
        here = primitive.interface.to_dict() if primitive.interface else None
        there = back.interface.to_dict() if back.interface else None
        if here != there:
            # A handle that does not survive rendering is worse than no handle:
            # the plan would name a service that is not the one the source said.
            report.add(
                "round_trip_mismatch",
                f"{primitive.name}: the execution handle does not survive rendering "
                f"({here} -> {there})",
            )
            return


def _check_declarations(rulebook: Rulebook, report: ValidationReport) -> None:
    """Every state and object a primitive names should be declared up front."""
    declared_states = {normalize_name(s) for s in rulebook.states}
    declared_objects = {normalize_name(o) for o in rulebook.objects}

    undeclared_states = sorted(
        {normalize_name(s) for s in rulebook.required_states | rulebook.produced_states}
        - declared_states
    )
    if undeclared_states:
        report.add(
            "undeclared_state",
            "used by a primitive but missing from the States section: "
            + ", ".join(undeclared_states),
        )

    used = {normalize_name(o) for p in rulebook.primitives for o in p.uses}
    undeclared_objects = sorted(used - declared_objects)
    if undeclared_objects:
        report.add(
            "undeclared_object",
            "used by a primitive but missing from the Objects section: "
            + ", ".join(undeclared_objects),
        )


def to_stamped_edges(rulebook: Rulebook) -> list[Any]:
    """The rulebook as Stations 2 and 3 would stamp it.

    Station 3 is not consulted: the rulebook already says which state is a
    precondition and which is an effect - it wrote them - so asking a model to
    re-derive that would measure Station 3 rather than the rulebook.
    """
    classified = classify(to_bundles(rulebook))

    labels = {}
    for primitive in rulebook.primitives:
        for state in primitive.produces:
            labels[(normalize_name(primitive.name), normalize_name(state), "produces")] = "produces"
        for state in primitive.requires:
            labels[(normalize_name(primitive.name), normalize_name(state), "requires")] = "requires"

    edges = list(classified.stamped)
    for item in classified.deferred:
        bundle = item.bundle
        head, tail = normalize_name(bundle.source.name), normalize_name(bundle.target.name)
        label = (
            "requires"
            if (head, tail, "requires") in labels and "requires" in bundle.description
            else "produces" if (head, tail, "produces") in labels else None
        )
        if label is None:
            label = "requires" if "requires" in bundle.description else "produces"
        edges.append(resolved_edge(bundle, label))
    return edges


def _bridge(rulebook: Rulebook, report: ValidationReport) -> Any:
    """Stations 2 and 4 over the generated rulebook. None if it cannot be built."""
    edges = to_stamped_edges(rulebook)

    try:
        result = normalize_edges(edges)
    except Exception as error:  # pragma: no cover - the guard prevents cycles
        report.add("cycle", f"the bridge could not order this rulebook: {error}")
        return None

    if result.refused_derived:
        pairs = ", ".join(f"{e.head} -> {e.tail}" for e in result.refused_derived)
        report.add(
            "cycle",
            f"the ordering contains a cycle; these orderings were refused: {pairs}. "
            "Two actions each produce a state the other requires.",
        )
        return None

    graph = ordering_graph(result)
    order = graph.topological_order()
    if order is None:
        report.add("cycle", "no valid execution order exists for this rulebook")
        return None

    report.ordering_constraints = len(result.derived)
    unordered = [p.name for p in rulebook.primitives if p.name not in set(order)]
    report.plan = order + sorted(unordered)

    if unordered and len(unordered) == len(rulebook.primitives):
        report.add(
            "no_ordering",
            "no ordering could be derived: no primitive produces a state another requires, "
            "so the steps have no dependencies at all",
        )
    elif unordered:
        report.add(
            "unordered_primitive",
            "not connected to the dependency graph, so their position is arbitrary: "
            + ", ".join(sorted(unordered)),
        )
    return result


def _check_interfaces(rulebook: Rulebook, report: ValidationReport) -> None:
    """In an executable rulebook, every step needs a handle.

    Only checked when the rulebook claims to be one - that is, when some primitive
    carries an interface. A rulebook reconstructed from a video describes what a
    person does and has no handles at all, and demanding them there would flag
    every rulebook the generator has ever produced.

    A flag rather than a fatal: the precondition graph is still correct and still
    plannable. What it cannot do is *run*, and the step that cannot run should be
    named before a plan is handed to something that will try.
    """
    with_handle = [p for p in rulebook.primitives if p.interface is not None]
    if not with_handle:
        return

    missing = [p.name for p in rulebook.primitives if p.interface is None]
    if missing:
        report.add(
            "missing_interface",
            f"{len(with_handle)} of {len(rulebook.primitives)} steps say how to "
            "invoke them, so this is meant to be executable - but these say nothing, "
            "and a plan cannot run them: " + ", ".join(sorted(missing)),
        )

    seen: dict[str, str] = {}
    for primitive in with_handle:
        key = f"{primitive.interface.kind}:{primitive.interface.name}"
        if key in seen:
            report.add(
                "duplicate_interface",
                f"{primitive.name} and {seen[key]} are both invoked through "
                f"{primitive.interface.name}, so the plan has two steps that do the "
                "same call. One of them is probably wrong.",
            )
        seen[key] = primitive.name


def _check_orphans(rulebook: Rulebook, report: ValidationReport) -> None:
    """A precondition nothing produces cannot be satisfied (Section 10)."""
    produced = {normalize_name(s) for s in rulebook.produced_states}
    orphans = sorted(
        f"{p.name} requires {state}"
        for p in rulebook.primitives
        for state in p.requires
        if normalize_name(state) not in produced
    )
    if orphans:
        report.add(
            "orphan_precondition",
            "required by a step but produced by nothing, so the plan may not be "
            "executable: " + "; ".join(orphans),
        )


def _check_size(rulebook: Rulebook, report: ValidationReport) -> None:
    """A skill far larger than any in the corpus is probably several tasks."""
    if len(rulebook.primitives) > SUSPICIOUS_PRIMITIVES:
        report.add(
            "suspicious_size",
            f"{len(rulebook.primitives)} primitives for one skill, well beyond anything "
            f"in the corpus ({SUSPICIOUS_PRIMITIVES} is already generous). This is "
            "usually a compilation video fused into a single skill.",
        )


def _check_goal_state(rulebook: Rulebook, report: ValidationReport) -> None:
    """The last step must actually achieve something."""
    if not rulebook.produced_states:
        report.add("no_goal_state", "no primitive produces any state, so nothing is achieved")


def check_grounding(rulebook: Rulebook, transcript_text: str) -> list[Issue]:
    """Primitives must be grounded in the narration (Section 10).

    Preconditions may be inferred — that is the entire point of stage 2 — but an
    *action* the transcript never mentions is a hallucinated step. The asymmetry
    is deliberate: inference is expected downward (into states), never outward
    (into new actions).
    """
    words = set(normalize_name(transcript_text).split("_"))
    ungrounded = [
        primitive.name
        for primitive in rulebook.primitives
        if not any(token in words for token in normalize_name(primitive.name).split("_"))
    ]
    if not ungrounded:
        return []
    return [
        Issue(
            code="ungrounded_primitive",
            detail=(
                "no word of these actions appears in the transcript, so they may be "
                "invented: " + ", ".join(sorted(ungrounded))
            ),
        )
    ]
