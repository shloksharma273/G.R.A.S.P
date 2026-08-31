"""An interactive shell for asking the PlanGraph for plans.

`plan_command.py` is one-shot: it connects, plans, prints and exits. That is the
right shape for scripting and the wrong shape for exploring, where the cost of
reconnecting and re-reading the skill list on every question is the whole latency
budget.

The shell connects once, keeps the skill list warm, and answers questions until
you leave. Everything else it offers is diagnostic: which skills exist, why a
command did not resolve, what the plan looks like as JSON, and whether the
wording came from a model or a template.

Read-only, like the rest of Layer 2.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from typing import IO, Any

from kg_read_harness.output import glyphs

from .config import PlannerConfig
from .order import CyclicPlan
from .pipeline import plan_command
from .plan import Clarification, Plan
from .report import print_clarification, print_plan
from .retrieve import LexicalRetriever, VectorRetriever, load_skills
from .traverse import IncompletePlanGraph

BANNER = """\
G.R.A.S.P — ask for a plan
Type a command in plain English, or a colon-command. :help for the list, :q to quit."""

HELP = """\
  <anything>        plan it, e.g.  make me a chai
  :skills           list the skills in the PlanGraph, with their step counts
  :show <skill>     the full subgraph for one skill
  :json             show the last plan as plan.json
  :llm on|off       phrase steps with the model, or with templates
  :threshold <x>    change the match score required to accept a goal
  :why              why the last command resolved (or did not) the way it did
  :help  :q"""


class Shell:
    """One connected session over the PlanGraph."""

    def __init__(
        self,
        db: Any,
        config: PlannerConfig,
        stdout: IO[str] | None = None,
        provider: Any = None,
    ) -> None:
        self.db = db
        self.config = config
        self.out = stdout if stdout is not None else sys.stdout
        self.provider = provider
        self.last: Plan | Clarification | None = None
        self._retriever = None
        self._skills = load_skills(db, config.schema)

    # --- setup ------------------------------------------------------------

    @property
    def retriever(self):
        """Built once. Re-reading the skill list per question is the slow part."""
        if self._retriever is None:
            vector = VectorRetriever(self.db, self.config.schema)
            self._retriever = vector if vector.available else LexicalRetriever(self._skills)
        return self._retriever

    def greet(self) -> None:
        g = glyphs(self.out)
        print(BANNER, file=self.out)
        print("", file=self.out)
        print(f"  database   {self.config.arango.database}", file=self.out)
        print(f"  graph      {self.config.schema.graph_name}", file=self.out)
        print(
            f"  skills     {len(self._skills)} "
            f"({', '.join(sorted(s['name'] for s in self._skills)[:4])}"
            + (", ..." if len(self._skills) > 4 else "")
            + ")",
            file=self.out,
        )
        print(
            f"  retrieval  {self.retriever.method}"
            + ("" if self.retriever.method == "vector" else "  (no vector index yet)"),
            file=self.out,
        )
        print(
            f"  phrasing   {'model' if self.config.use_llm else 'templates'}"
            f"  {g['em']}  step order is graph-derived either way",
            file=self.out,
        )
        if not self._skills:
            print("", file=self.out)
            print(
                "  This PlanGraph holds no skills. Run the bridge first:\n"
                "      python write_plangraph.py --write --all-skills",
                file=self.out,
            )
        print("", file=self.out)

    # --- the loop ---------------------------------------------------------

    def handle(self, line: str) -> bool:
        """Process one line. Returns False when the session should end."""
        line = line.strip()
        if not line:
            return True
        if line.startswith(":"):
            return self._colon(line[1:].strip())
        self._plan(line)
        return True

    def _colon(self, command: str) -> bool:
        verb, _, rest = command.partition(" ")
        rest = rest.strip()
        verb = verb.lower()

        if verb in ("q", "quit", "exit"):
            return False
        if verb in ("h", "help", "?"):
            print(HELP, file=self.out)
        elif verb == "skills":
            self._list_skills()
        elif verb == "show":
            self._show(rest)
        elif verb == "json":
            self._json()
        elif verb == "llm":
            self._toggle_llm(rest)
        elif verb == "threshold":
            self._set_threshold(rest)
        elif verb == "why":
            self._why()
        else:
            print(f"  unknown command :{verb} — try :help", file=self.out)
        return True

    # --- actions ----------------------------------------------------------

    def _plan(self, command: str) -> None:
        try:
            result = plan_command(
                command,
                self.db,
                self.config,
                provider=self.provider,
                retriever=self.retriever,
            )
        except IncompletePlanGraph as error:
            print(f"  incomplete PlanGraph: {error}", file=self.out)
            return
        except CyclicPlan as error:
            print(f"  cycle: {error}", file=self.out)
            return

        self.last = result
        print("", file=self.out)
        if isinstance(result, Clarification):
            print_clarification(result, self.out)
        else:
            print_plan(self.config, result, self.out)
        print("", file=self.out)

    def _list_skills(self) -> None:
        if not self._skills:
            print("  no skills in this PlanGraph", file=self.out)
            return
        schema = self.config.schema
        print("", file=self.out)
        for skill in sorted(self._skills, key=lambda s: s["name"]):
            # One query shape for reading a scope's edges, counted here. A
            # separate COUNT query would only differ by its projection.
            edges = self.db.aql.execute(
                "FOR e IN @@edges FILTER e.skill_scope == @scope RETURN e",
                bind_vars={"@edges": schema.edge_collection, "scope": skill["skill_scope"]},
            )
            count = sum(1 for e in edges if e.get("type") == "decomposes_to")
            print(f"  {skill['name']:<40} {count:>3} steps   scope: {skill['skill_scope']}",
                  file=self.out)
        print("", file=self.out)

    def _show(self, name: str) -> None:
        if not name:
            print("  usage: :show <skill>", file=self.out)
            return
        matches = [s for s in self._skills if name.lower() in s["name"].lower()]
        if not matches:
            print(f"  no skill matching {name!r} — try :skills", file=self.out)
            return
        result = plan_command(
            matches[0]["name"], self.db, self.config, provider=None, retriever=self.retriever
        )
        if isinstance(result, Clarification):
            print(f"  could not resolve {matches[0]['name']!r}", file=self.out)
            return
        print("", file=self.out)
        for step in result.steps:
            print(f"  {step.order:>2}. {step.action}", file=self.out)
            if step.requires:
                print(f"        requires {', '.join(step.requires)}", file=self.out)
            if step.produces:
                print(f"        produces {', '.join(step.produces)}", file=self.out)
        print("", file=self.out)

    def _json(self) -> None:
        if self.last is None:
            print("  nothing planned yet", file=self.out)
            return
        json.dump(self.last.to_dict(), self.out, indent=2, ensure_ascii=False)
        self.out.write("\n")

    def _toggle_llm(self, value: str) -> None:
        wanted = value.lower() in ("on", "1", "true", "yes")
        if wanted and self.config.llm is None:
            print(
                "  no LLM key configured (LLM_API_KEY / OPENROUTER_API_KEY), so "
                "phrasing stays templated. The step order is identical either way.",
                file=self.out,
            )
            return
        self.config = dataclasses.replace(self.config, use_llm=wanted)
        print(f"  phrasing: {'model' if wanted else 'templates'}", file=self.out)

    def _set_threshold(self, value: str) -> None:
        try:
            threshold = float(value)
        except ValueError:
            print(f"  :threshold needs a number between 0 and 1, got {value!r}", file=self.out)
            return
        if not 0.0 <= threshold <= 1.0:
            print("  :threshold must be between 0 and 1", file=self.out)
            return
        self.config = dataclasses.replace(self.config, threshold=threshold)
        print(f"  match threshold: {threshold:.2f}", file=self.out)

    def _why(self) -> None:
        if self.last is None:
            print("  nothing asked yet", file=self.out)
            return
        if isinstance(self.last, Clarification):
            print(f"  {self.last.reason}", file=self.out)
            for candidate in self.last.candidates:
                print(f"    {candidate['score']:.2f}  {candidate['skill']}", file=self.out)
            return
        meta = self.last.meta
        ordering = meta["ordering"]
        print(
            f"  resolved to {self.last.goal!r} at {meta['match_confidence']:.2f} "
            f"via {meta['match_method']} retrieval (threshold {self.config.threshold:.2f}).",
            file=self.out,
        )
        print(
            f"  ordered by {ordering['constraints']} constraint(s): "
            f"{ordering['from_precedes_edges']} from precedes edges, "
            f"{ordering['from_state_chain']} from the produces/requires chain, "
            f"{ordering['confirmed_by_both']} confirmed by both.",
            file=self.out,
        )
        print(
            f"  wording is {meta['composer']}"
            + (f" ({meta['model_id']})" if meta.get("model_id") else "")
            + ". The order came from the graph, not the model.",
            file=self.out,
        )
        if meta.get("composer_fallback_reason"):
            print(f"  fell back because: {meta['composer_fallback_reason']}", file=self.out)


def interact(shell: Shell, stdin: IO[str] | None = None, prompt: str = "grasp> ") -> int:
    """Read-eval-print until end of input."""
    stream = stdin if stdin is not None else sys.stdin
    shell.greet()
    while True:
        try:
            if stream is sys.stdin and stream.isatty():
                line = input(prompt)
            else:
                line = stream.readline()
                if not line:
                    break
        except (EOFError, KeyboardInterrupt):
            print("", file=shell.out)
            break
        if not shell.handle(line):
            break
    return 0
