# G.R.A.S.P

**GraphRAG → Action** — an AutoGraph knowledge graph becomes a typed planning
graph, and a natural-language command becomes an ordered, executable plan.
**All five bridge stations plus Layer 2 planning are built**, with a generator
upstream that can author the rulebooks themselves from video.

| Station | What it does | Status |
| --- | --- | --- |
| **0** · Rulebook generator | video link → canonical rulebook | built (`rulebook_generator/`) |
| 1 · Read | KG → relationship bundles | built (`kg_read_harness/`) |
| 2 · Rule pre-classify | bundles → edge types by entity-type pair | built (`rule_preclassifier/`) |
| 3 · LLM disambiguate | resolve `requires` vs `produces` | not started |
| 4 · Normalize direction & order | settle head → tail, derive `precedes` | built (`direction_normalizer/`) |
| 5 · Write | persist to the PlanGraph | built (`plangraph_writer/`) |
| **Layer 2** · Planning | command → goal → subgraph → ordered `plan.json` | built (`layer2_planning/`) |
| **Decomposition** · Compound commands | command → subtasks → one Layer 2 plan each → merged run | built (`task_decomposition/`) |
| **AutoGraph pipeline** · Rulebooks → KG | upload a module, corpus → strategies → ontology → KG → PlanGraph over AutoGraph's API | built (`autograph_pipeline/`) |
| **UI** · Web front end | discover projects, build their PlanGraph, ask in a browser | built (`grasp_web/`) |

---

---

## Station 0 — Rulebook Generator

**From a video link to a canonical rulebook, gated by round-trip validation.**

Everything else in this project begins at "a rulebook enters AutoGraph". This
produces that rulebook. It sits **entirely upstream of Layer 1** and changes
nothing downstream — its only output is a rulebook in the format the pipeline
already consumes, so no station can tell a generated one from an authored one.

Implements `PRD_Rulebook_Generator.docx`.

```bash
python generate_rulebook.py https://youtu.be/VIDEOID
python generate_rulebook.py VIDEOID --show --write
python generate_rulebook.py --transcript captions.txt   # no network needed
```

### The crux is reconstruction, not transcription

Fetching captions is trivial. Turning them into a precondition graph is not.
Narration is chatty, skips the obvious, says "this" and "that", and — the part
that matters — **almost never states a precondition**. Nobody says "the water
must be boiling before you add the pasta"; they just add the pasta.

| Element | Where it comes from |
| --- | --- |
| primitives | mostly stated — find the action boundaries |
| objects | mostly stated — resolve "this" → the pan |
| states | partly stated, largely inferred |
| **`requires`** | **almost never stated — inferred** |
| `produces` | partly stated, partly inferred |

So the prompt carries the chai rulebook as a full worked example and points at
`add_water requires pan_on_stove` — "the inference you are being asked to make".
Primitives must be **grounded** in the narration; preconditions may be inferred.
That asymmetry is what makes inference safe: the model may reason downward into
states, never outward into actions it invented.

### The validation gate is the point

Because the load-bearing part is inferred, the gate is mandatory rather than
advisory. And it is not a private reimplementation of "looks right" — the
rulebook is run through **this project's own bridge**:

```
render → parse       the markdown carries the whole intermediate
→ Station 2          types every relationship
→ Station 4          derives the ordering and guards the cycle
→ checks             DAG · no orphan preconditions · producible goal · valid sort
```

Verdict is `accept`, `flag_for_review`, or `reject`. **Only an accept is ever
auto-ingested** — not configurable, because a wrong precondition becomes a plan a
robot would try to execute.

The round-trip half deserves its own note: `parse(render(x))` must recover `x`.
That is a far stronger claim than "the markdown looked fine", and it caught a real
defect on the first live video — a state the model named `pasta_oiled_and_mixed`
cannot survive a prose list that separates items with "and".

### What it does on real videos

| Video | Verdict | Why |
| --- | --- | --- |
| A single recipe | accept | passes every check |
| A "basics" compilation | reject | takes one task and flags it, then finds a cycle in the inferred preconditions |
| Rick Astley | reject | *"the lyrics of a song, not a how-to task. It contains no actions, objects, or procedural steps to reconstruct."* |

The compilation case is the gate earning its keep. Before the prompt was told
about roundups, the model fused pasta, rice, onion, salmon and knife-sharpening
into one 40-step "skill"; now it takes the first coherent task, sets
`multiple_tasks`, and the gate flags it. It is still rejected — the model inferred
a circular dependency between two onion-slicing steps — which is precisely the
kind of plausible-looking nonsense that would have reached a robot without a gate.

### One rulebook per task

Layer 2 resolves a command to one skill and plans every step in that skill's
scope, so a rulebook covering a whole robot can only ever produce one plan.
`--split` (and the **one rulebook per task** toggle on both generator pages)
builds them the way the OpenAMRobot rulebooks in `generated/` were written:

```
source ─► reference rulebook ─► tasks ─► one rulebook per task ─► the gate, each
          every operation         the model picks them    cut out by following
          (the model)             (guarded)               `requires` back (code)
```

```bash
python generate_rulebook.py --transcript driver.txt --code --split --write
```

Only the first two passes are the model's. The slice is deterministic: a task's
goal step, plus, for each state it requires, the first step in the reference
that produces it, recursively. A task rulebook therefore can't gain a step the
reference doesn't have, and it keeps the reference's names, states and execution
handles exactly. Cut from `rulebook_operate_openamrobot.md`, it reproduces the
steps of eleven of the hand-made task rulebooks exactly, and a test pins that.

A guard checks the model's task list. It refuses a goal that isn't a step in the
reference, a duplicate skill, and a skill named after one of its own steps (the
two collapse into one vertex and the step silently drops out of the plan). A
refused list is reprompted once, then replaced by one task per final step. A
task can `assume` states the user already has: undocking starts docked. So the
docking sequence isn't pulled in, and the assumption is written into the
rulebook's overview instead of vanishing.

### Reproducibility

Temperature 0, and a cache keyed on the transcript hash: the same video yields the
same rulebook and issues no second call.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `RULEBOOK_MODEL` | `LLM_MODEL` | overrides the model for extraction |
| `RULEBOOK_STRICTNESS` | `normal` | `lenient` also writes flagged rulebooks (never ingests them) |
| `RULEBOOK_OUTPUT_DIR` | `generated` | where accepted rulebooks land |
| `RULEBOOK_CACHE` / `_PATH` | on | the reproducibility store |
| `RULEBOOK_AUTO_INGEST` | off | push to AutoGraph on accept |

`youtube-transcript-api` is an **optional** dependency — `requirements.txt` still
lists one required package, and `--transcript FILE` works without it.

### Exit codes

`0` accepted · `3` rejected · `4` flagged for review.

### Ingesting without AutoGraph

The normal path takes a rulebook through AutoGraph, which extracts a KG that
Station 1 reads. That is the honest path — it proves a generated rulebook
survives real extraction. But AutoGraph is a build cycle away, and sometimes it
is down.

```bash
python ingest_rulebook.py generated/            # dry run, every rulebook there
python ingest_rulebook.py generated/ --write    # apply
```

This skips AutoGraph and Station 1 — the bundles are built from the rulebook
rather than extracted — and skips Station 3, because the rulebook already states
which state is a precondition and which is an effect. Stations 2, 4 and 5 run in
full: every relationship typed, the ordering derived and cycle-guarded, the
subgraph written under the skill's own scope.

It reuses the validation gate's own machinery rather than a second copy, so the
two cannot drift. A rejected rulebook is skipped by default — a cycle cannot be
planned, so writing it would put an unplannable subgraph in the graph — and
`--force` writes it anyway for inspection.

The trade is worth stating: **this measures the rulebook, not the extraction.** A
rulebook that lands cleanly here can still lose something through AutoGraph, and
only the real path will tell you that.


## Station 1 — Read

**From an AutoGraph KG to typed relationship bundles.**

A read-only command-line harness that connects to the ArangoDB knowledge graph
produced by AutoGraph, reads every entity-to-entity relationship, and prints each
as a structured **bundle** — source entity, target entity, both of their types,
and the relationship's free-text description.

It classifies nothing and writes nothing. It is the first buildable component of
the Edge Classifier bridge and doubles as the verification tool for confirming
what AutoGraph actually extracted.

Implements `PRD_KG_Read_Harness.docx` (Layer 1 → Layer 2 bridge, Station 1).

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # one dependency: python-arango
```

## Configure

Configuration comes only from environment variables (PRD Section 8); credentials
are never passed on the command line and never printed.

```bash
cp .env.example .env      # edit it
set -a && source .env && set +a
```

| Variable | Req. | Purpose / default |
| --- | --- | --- |
| `ARANGO_URL` | yes | Endpoint, e.g. `http://localhost:8529` |
| `ARANGO_DB` | yes | Project database that holds the KG |
| `ARANGO_USERNAME` | yes* | DB user with read access (e.g. `root`) |
| `ARANGO_PASSWORD` | yes* | Password for that user |
| `ARANGO_AUTH_TOKEN` | opt | JWT bearer, alternative to username/password |
| `PROJECT_NAME` | yes | Used to derive default collection names |
| `ENTITY_COLLECTION` | opt | Override; default `{PROJECT_NAME}_Entities` |
| `RELATION_COLLECTION` | opt | Override; default `{PROJECT_NAME}_Relations` |
| `ENTITY_TYPE_FIELD` | opt | Attribute holding entity type; default `entity_type` |
| `DESCRIPTION_FIELD` | opt | Attribute holding the relation text; default `description` |
| `RELATION_TYPE` | opt | Edge type to read; default `RELATED_TO` |
| `ENTITY_TYPE_FILTER` | opt | Comma list, e.g. `SKILL,PRIMITIVE,OBJECT,STATE` |
| `OUTPUT_FORMAT` | opt | `table` \| `json`; default `table` |
| `LIMIT` | opt | Max rows for dev; default `0` (all) |
| `ENTITY_NAME_FIELD` | opt | Attribute holding entity name; default `name` |
| `RELATION_TYPE_FIELD` | opt | Attribute holding relation type; default `type` |

\* Either username/password **or** a bearer token must be supplied.

The last two rows extend the PRD's table for the same reason it makes the others
configurable: exact attribute names must be confirmed against the running
instance in the Graph Explorer rather than assumed. Their defaults (`name`,
`type`) are exactly what the PRD's data contract describes, so leaving them unset
reproduces the specified behaviour.

## Run

```bash
python read_kg.py               # or: python -m kg_read_harness
python read_kg.py --help        # env-var reference
```

Terminal output is two views (PRD Section 9). The listing prints one readable
line — or a short block when the description is long:

```
[  12] place_pan (PRIMITIVE) ──[ After this action, the pan is on the stove. ]──▶ pan_on_stove (STATE)
[   1] make_masala_chai (SKILL) ──▶ place_pan (PRIMITIVE)
       ↳ The skill make_masala_chai is composed of the primitive action place_pan.
```

Then the summary, which is the point of the whole harness — it shows at a glance
whether the relationships needing classification were extracted at all:

```
Summary — bundles by entity-type pair
------------------------------------------------------------------------
  SOURCE TYPE  →  TARGET TYPE  COUNT
  PRIMITIVE    →  STATE           20
  SKILL        →  PRIMITIVE       11
  PRIMITIVE    →  OBJECT           8
------------------------------------------------------------------------
  TOTAL                           39
  skipped (dangling endpoints)      1
```

With `OUTPUT_FORMAT=json` the listing becomes a JSON array on stdout, ready to
pipe into later stations; the header, warnings and summary go to stderr so the
array stays machine-clean:

```bash
OUTPUT_FORMAT=json python read_kg.py > bundles.json
```

Development knobs: `LIMIT=20` for a quick look, `ENTITY_TYPE_FILTER=PRIMITIVE,STATE`
to see just the pair the classifier cares about (both endpoints must match; the
values are matched case-insensitively, because live builds emit lowercase types).

### Against a real AutoGraph build

Names differ from the defaults in practice, and the harness tells you which ones
before reading anything. On the `roboticsPlanner` build in `test_shlok`:

```bash
ARANGO_URL=https://<your-instance>
ARANGO_DB=test_shlok
ARANGO_USERNAME=root
ARANGO_PASSWORD=...
PROJECT_NAME=roboticsPlanner        # -> roboticsPlanner_Entities / _Relations
ENTITY_NAME_FIELD=entity_name       # AutoGraph uses entity_name, not name
```

That reads 35 `RELATED_TO` bundles out of 116 edges (the rest are
`IN_COMMUNITY`, `MENTIONED_IN`, `PART_OF`) and summarises:

```
  tool   →  state    21
  skill  →  tool     11
  tool   →  object    3
```

Note the ontology: that build labels entities `skill` / `tool` / `object` /
`state`, not the PRD's `SKILL` / `PRIMITIVE` / `OBJECT` / `STATE` — `tool` is
filling the `PRIMITIVE` role. So `tool→state` (21) is the pair Station 2 must
classify and `skill→tool` (11) is the decomposition pair. Station 1 reports
whatever the KG actually says and warns when a filter value falls outside the PRD
ontology; Station 2 resolves the vocabulary via an explicit
[synonym map](#ontology-synonyms), so nothing here is guessed silently.

---

## Station 2 — Rule Pre-Classifier

**From bundles to planning edge types, by entity-type pair alone.**

A pure function: no LLM, no database, no network, no randomness. It assigns an
edge *type* — never a direction — and guarantees every bundle it is handed leaves
in exactly one of three buckets.

Implements `PRD_Rule_PreClassifier.docx` (Bridge Station 2).

### The decision table

The whole station is one table, keyed on the **unordered** type pair, so a
reversed edge still matches ([rule_preclassifier/table.py](rule_preclassifier/table.py)):

| Entity-type pair | Edge type | Outcome |
| --- | --- | --- |
| SKILL + PRIMITIVE | `decomposes_to` | stamp |
| PRIMITIVE + PRIMITIVE | `precedes` | stamp (direction deferred) |
| PRIMITIVE + OBJECT | `uses` | stamp |
| PRIMITIVE + STATE | `requires` / `produces` | **defer → Station 3** |
| anything else | — | park (with a reason code) |

Adding an ontology type is adding a row: `table.py` holds the table, the reason
priority, the synonym map and the heuristic vocabulary, and nothing else in the
station encodes policy.

### Type is not direction

Station 2 answers *what kind of edge is this*, not *which way does it point*.
Where the pair implies the orientation it records the hint — a SKILL decomposes to
a PRIMITIVE, never the reverse — along with `reversed_from_input`, the fact Station
4 would otherwise lose when the pair is canonicalized. For `precedes` it records
nothing: that arrow comes from state chaining in Station 4.

### Run

```bash
python classify_kg.py                       # reads the KG (Station 1), then classifies
python classify_kg.py --format json         # the three buckets, for Stations 3 and 4
python classify_kg.py --no-listing          # summary only

OUTPUT_FORMAT=json python read_kg.py > bundles.json
python classify_kg.py --from-json bundles.json    # no database touched
```

The summary is the point — it says where the work went and what is left:

```
Summary — buckets
------------------------------------------------------------------------
  stamped       14   -> Station 4 (normalize direction)
  deferred      21   -> Station 3 (LLM disambiguate)
  parked         0   -> quality review
------------------------------------------------------------------------
  TOTAL         35

Summary — edge types
------------------------------------------------------------------------
  requires / produces (deferred)    21
  decomposes_to                     11
  uses                               3

  conservation: 35 in → 35 out  (holds)
```

### The parked pile is a diagnostic

Every unclassified bundle carries **exactly one** reason code plus both names,
both types, the description and the relation key. When several codes apply the
most actionable wins, in priority order B → C → D → A, so a mis-typed *and*
directionless edge reports as the fixable `suspect_type` rather than the blander
`ambiguous_direction`.

| Code | Group | Fires when |
| --- | --- | --- |
| `suspect_type` | B | an endpoint reads as an action but is typed OBJECT/STATE |
| `missing_type` | B | a type is null or outside the ontology |
| `ambiguous_direction` | C | a `precedes` edge has no description to resolve the arrow |
| `self_loop` | C | an entity is related to itself |
| `conflicting_edge` | C | a duplicate relation runs the other way (both are parked) |
| `empty_description` | D | PRIMITIVE→STATE with no text for Station 3 to read |
| `dangling_endpoint` | D | an endpoint never resolved to an entity |
| `alias_mismatch` | D | two spellings of one name (`THE CUP` vs `the_cup`) |
| `unmapped_pair` | A | a valid pair with no planning meaning (OBJECT→STATE, …) |

Reading the pile: **A-heavy is healthy**; B-heavy means fix the ontology or the
extraction prompt; C-heavy means the rulebook's ordering language is too vague;
D-heavy means clean the source data.

Codes split two ways in the implementation. *Blocking* codes (`missing_type`,
`self_loop`, `conflicting_edge`, `dangling_endpoint`, and the two empty-text
cases) park a bundle even when the table has a row for its pair, because the
bundle cannot be trusted as read. *Explanatory* codes (`suspect_type`,
`alias_mismatch`, `unmapped_pair`) only ever explain a pair the table already
failed to route — they never override a good classification.

### Ontology synonyms

The live `roboticsPlanner` build labels entities `skill` / `tool` / `object` /
`state`, with `tool` filling the PRIMITIVE role. Station 2 upper-cases every type
value and then applies a small, explicit synonym map:

```python
TYPE_SYNONYMS = {"TOOL": "PRIMITIVE", "ACTION": "PRIMITIVE"}
```

Casing is not a semantic change, so it is handled unconditionally. `tool` is, so
it is one editable line rather than a hidden rule — and the map is deliberately
small, because an unmapped value parks as `missing_type` (group B), which is
*visible in the summary*. Under-mapping is safe by design; over-mapping silently
invents meaning. Every rewrite is reported in the run header:

```
  type normalized       tool -> PRIMITIVE  (35 endpoint(s))
```

### Conservation

`|input| = |stamped| + |deferred| + |parked|` is asserted on every run and raises
`ConservationError` if it ever fails. Nothing is silently lost.

### Reuse in the bridge

```python
from kg_read_harness import load_config, read_relationship_bundles
from kg_read_harness.client import connect
from rule_preclassifier import classify

result = classify(read_relationship_bundles(connect(load_config()), load_config()))

for edge in result.stamped:      # -> Station 4
    ...
for item in result.deferred:     # -> Station 3
    ...
for item in result.parked:       # -> quality review
    print(item.reason_code, item.group, item.detail)
```

### Verify

```bash
python -m unittest discover -s tests -t .    # 159 tests, no database required
python demo_classify_offline.py              # Station 1 + Station 2 on the chai KG
```

On the offline chai set the answer key holds exactly: 11 `decomposes_to`,
8 `uses`, 20 deferred `PRIMITIVE→STATE`, nothing parked, 39 in → 39 out. Purity is
enforced by a test that walks the AST of the station's core modules and fails if
any of them imports a client library, a clock, a random source, or opens a file.


---

## Station 3 — LLM Disambiguator

**From a deferred `PRIMITIVE→STATE` edge to `requires` or `produces`.**

Receives the one ambiguous bucket Station 2 could not settle and decides, for
each edge, whether the primitive *requires* the state (a precondition that must
hold before it runs) or *produces* it (an effect that becomes true after it runs).
The distinction is purely temporal, and it is read from the description alone.

Implements `PRD_LLM_Disambiguator.docx` (Bridge Station 3).

This is the only station that calls an LLM, so it is built around reproducibility
and honest abstention: temperature 0, a content-hash result cache, a confidence
gate, and `unclear` as a first-class outcome. A forced binary choice on silent
text is how a wrong precondition gets into a plan; an abstention only costs a
review.

### Three stages, each shrinking the next one's load

1. **Skip empty text** — an edge with no description is parked `empty_description`
   with no call. Station 3 has nothing to read, so it does not pretend to.
2. **Lexical pre-pass** — a description carrying cues from exactly one class
   short-circuits to that label (`method = lexical`). Cues from *both* classes
   fall through: that is precisely the case the model is for. Disable with
   `LLM_LEXICAL_PREPASS=0` for a pure-LLM run.
3. **LLM + confidence gate** — what remains is deduplicated by content hash,
   checked against the cache, and batched to the model. A verdict at or above the
   threshold is stamped; below it parks `low_confidence`; `unclear` parks
   regardless of confidence.

### Run

```bash
python disambiguate_kg.py                          # Stations 1 + 2 + 3 against the KG
python disambiguate_kg.py --format json            # the two buckets, for Station 4
python disambiguate_kg.py --dry-run                # pre-pass and cache only, no call
python classify_kg.py --format json > classified.json
python disambiguate_kg.py --from-json classified.json    # from Station 2 output
```

### Provider configuration

Any OpenAI-compatible `/chat/completions` endpoint. The project is configured
against OpenRouter. Station 3 uses only the standard library, so it adds **no
dependency** — `requirements.txt` still lists exactly one.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_API_KEY` / `OPENROUTER_API_KEY` / `OPENAI_API_KEY` | — | required; first one set wins |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | any OpenAI-compatible endpoint |
| `LLM_MODEL` | `anthropic/claude-opus-5` | model id |
| `LLM_TEMPERATURE` | `0` | Section 7 determinism |
| `LLM_CONFIDENCE_THRESHOLD` | `0.75` | below this → parked `low_confidence` |
| `LLM_BATCH_SIZE` | `10` | items per request |
| `LLM_MAX_REQUESTS` | `50` | per-run spend cap; `0` makes no call at all |
| `LLM_LEXICAL_PREPASS` | `1` | `0` for a pure-LLM run |
| `LLM_CACHE` / `LLM_CACHE_PATH` | `1`, `.grasp_cache/station3.json` | the idempotency store |
| `LLM_MAX_RETRIES` | `3` | bounded backoff on transient failures |
| `LLM_MAX_TOKENS` | `4000` | response budget |
| `LLM_REASONING` | `0` | `1` to let a thinking model reason first |

The key comes only from the environment, is never a command-line flag, and never
appears in output — the run header prints `set (73 chars, not shown)`.

`LLM_REASONING` defaults to **off** for a reason worth knowing: on a
thinking-by-default model, provider-side reasoning consumes the entire token
budget and the reply comes back with `content: null` and `finish_reason: length`.
This is a closed-vocabulary classification; the reasoning is not worth paying for.
When it does happen, the error says so rather than reporting a generic failure.

### Accuracy on the chai set

Scored against `tests/chai_answer_key.py`, which is derived from the rulebook's
own wording rather than from any model output. Two targets are stated, and they
differ in kind: **precision must be 100%** — a wrongly stamped precondition
corrupts the plan ordering — while coverage may fall short, because an abstention
is a correct outcome, not a failure.

```bash
python eval_chai_live.py                # live KG, full pipeline
python eval_chai_live.py --no-prepass   # score the model alone
python demo_disambiguate_offline.py     # offline, no key and no network
```

On the live `roboticsPlanner` build (21 deferred edges):

| Configuration | Stamped | Correct | Precision | LLM requests |
| --- | --- | --- | --- | --- |
| Full pipeline | 21 | 21 | 100% | **0** (pre-pass settled all 21) |
| Model alone (`--no-prepass`) | 21 | 21 | 100% | 3 (mean confidence 0.972) |
| Model alone, warm cache | 21 | 21 | 100% | **0** (21 cache hits) |

The pre-pass covering all 21 is the intended shape, not a bypass: AutoGraph's
descriptions say "effect is that…" and "requires… as a precondition" almost
verbatim, so the cheap deterministic path is genuinely sufficient and the model
is held in reserve for corpora that are not. On the offline chai fixture, whose
wording is closer to the raw rulebook, the pre-pass settles 19 of 20 and the
twentieth falls through — which is the mechanism working as designed.

### Reproducibility

Not pure — it calls a network service — but **idempotent**: temperature 0 plus a
cache keyed on `sha256(model, primitive, state, description)` mean a second run
over the same input issues zero requests and produces identical output. That is
verifiable in the summary rather than asserted:

```
  requests           0
  cache hits        21
```

The same hash is the deduplication key, so a repeated `(primitive, state,
description)` triple is classified once and the verdict applied to every copy.
Changing `LLM_MODEL` changes the key, so a model swap correctly misses the cache
instead of reusing another model's judgement.

### Failure handling

No single edge can abort the batch. Malformed output is reprompted **once** and
then parked `service_error`; transient failures (429, 5xx, timeouts, reset
connections) retry with bounded deterministic backoff; a rejected key fails fast
without retrying and without printing the key. The per-run request cap stops
calling and parks the remainder rather than burning the rest of the budget.

Station 3 contributes three codes to the shared taxonomy — `low_confidence` and
`unclear` (group C, vague rulebook wording) and `service_error` (group D) — and
reuses `empty_description`. They live in `rule_preclassifier/table.py` with the
rest, because one quality dashboard reads every station's parked pile.

### Reuse in the bridge

```python
from llm_disambiguator import disambiguate, load_llm_config
from rule_preclassifier import classify

classified = classify(bundles)
resolved = disambiguate(classified.deferred, load_llm_config())

for edge in resolved.stamped:          # -> Station 4
    edge.edge_type      # "requires" | "produces"
    edge.orientation    # head=primitive, tail=state
    edge.confidence, edge.method, edge.model, edge.rationale
```

### Verify

```bash
python -m unittest discover -s tests -t .   # 277 tests, no network, no database
python demo_disambiguate_offline.py         # Stations 1+2+3 on the chai KG
```

Every test runs against a scripted fake endpoint whose real transport raises if a
test ever reaches it, so the suite cannot make a network call or spend money.


---

## Station 4 — Direction Normalizer & Order Resolver

**From typed edges to an ordered DAG.**

Two jobs, run in sequence. First every edge is forced to a canonical direction so
the planner can make blind assumptions. Then — the part worth demonstrating —
`precedes` ordering between primitives is **derived by state chaining**:

> for any state S, every primitive that produces S precedes every primitive
> that requires S.

That reconstructs execution order the rulebook never states. Deterministic, no
LLM: Station 3 remains the system's only model call.

Implements `PRD_Direction_Normalizer.docx` (Bridge Station 4).

### Job 1 — canonical direction

| Edge type | Canonical direction | Method |
| --- | --- | --- |
| `decomposes_to` | SKILL → PRIMITIVE | flip if reversed |
| `uses` | PRIMITIVE → OBJECT | flip if reversed |
| `requires` | PRIMITIVE → STATE | flip if reversed |
| `produces` | PRIMITIVE → STATE | flip if reversed |
| `precedes` | earlier → later | trust hierarchy (Job 2) |

A reversed edge is silently flipped — that is expected, not an error — and the
record says `flipped: true` so nothing is lost. The bundle itself is never
mutated: `head`/`tail` are added alongside the original `source`/`target`.

### Job 2 — the trust hierarchy

| Priority | Source of direction | Strength | Confidence |
| --- | --- | --- | --- |
| 1 | State chaining (produces S → requires S) | structural | 0.95 |
| 2 | Description wording (before / after cues) | textual | 0.70 |
| 3 | The orientation AutoGraph wrote | weak tiebreaker | 0.40 |
| 4 | None of the above | — | park `ambiguous_direction` |

Reconciliation follows from that ordering. A derived edge an explicit one agrees
with is **confirmed** (confidence rises to 0.99, and the arrow is emitted once,
not twice). A derived edge that **conflicts** with an explicit one wins, and the
explicit loser is parked `conflicting_edge` — parked, never dropped, because a
disagreement between the prose and the precondition graph is a finding worth
reading. An explicit edge with no chain support is kept at whatever confidence
its evidence earns.

### The cycle guard

Edges are added one at a time and any arrow whose tail already reaches its head
is refused before it goes in, so the graph is never allowed to become cyclic
rather than being audited afterwards. Only `precedes` needs guarding: in
canonical form `requires` and `produces` both run PRIMITIVE → STATE, so those
edges are bipartite with every arrow crossing the same way and cannot contain a
cycle. Whole-graph auditing is the validation wrapper's job; this is the local
guarantee that Station 4 never *introduces* one.

A derived edge refused as cyclic is reported separately rather than parked — it
is an addition, not an input, so conservation does not count it — and it means
two actions each produce a state the other requires, which is a real modelling
bug worth surfacing.

### Run

```bash
python normalize_kg.py                    # Stations 1-4 against the KG
python normalize_kg.py --format json      # finalized + derived + parked + the order
python normalize_kg.py --no-listing       # summary and plan only

python classify_kg.py --format json > s2.json
python disambiguate_kg.py --from-json s2.json --format json > s3.json
python normalize_kg.py --from-json s2.json s3.json     # no database, no API key
```

Station 2's stamped array carries `decomposes_to` / `uses` / `precedes`;
Station 3's carries `requires` / `produces`. Chaining needs the second half, so
passing Station 2's file alone derives no ordering.

### The result on the live KG

35 stamped edges in, 35 finalized, 0 parked, and **11 `precedes` edges derived
that appear nowhere in the graph**:

```
  ADD MILK        -> SIMMER           via MILK ADDED
  ADD SUGAR       -> SIMMER           via SUGAR ADDED
  ADD TEA LEAVES  -> ADD MILK         via TEA BREWING
  ADD TEA LEAVES  -> ADD SUGAR        via TEA BREWING
  ADD WATER       -> BOIL WATER       via WATER IN PAN
  BOIL WATER      -> ADD TEA LEAVES   via WATER BOILING
  PLACE PAN       -> ADD WATER        via PAN ON STOVE
  SIMMER          -> STRAIN           via TEA BREWED
  SIMMER          -> TURN OFF STOVE   via TEA BREWED
  STRAIN          -> SERVE            via TEA IN CUP
  TURN ON STOVE   -> BOIL WATER       via STOVE ON
```

A topological sort of that graph is the recipe:

```
PLACE PAN -> ADD WATER -> TURN ON STOVE -> BOIL WATER -> ADD TEA LEAVES
  -> ADD MILK -> ADD SUGAR -> SIMMER -> STRAIN -> SERVE -> TURN OFF STOVE
```

The KG contains **zero** explicit `precedes` edges — there are no
primitive-to-primitive relations in it at all — so every one of those 11 orderings
was worked out from the states the actions share. `derived_new: 11,
derived_confirming_explicit: 0` in the summary is that fact stated numerically.

### Traceability

Every derived edge names the state it came through and both relation keys that
justified it, so a surprising ordering can be walked back to the two sentences
that caused it:

```json
{
  "from": "PLACE PAN", "to": "ADD WATER", "via_state": "PAN ON STOVE",
  "source_relation_keys": ["...:764484433:...", "...:764484436:..."],
  "trace": "PLACE PAN produces PAN ON STOVE, which ADD WATER requires (... + ...)"
}
```

### Conservation, with a wrinkle

`|input| = |finalized| + |parked|` is asserted every run. **Derived edges are not
counted** — they are additions, not inputs, so folding them into the invariant
would make it meaningless. They are tracked and reported separately, which is
what Section 6 asks for.

### Verify

```bash
python -m unittest discover -s tests -t .   # 369 tests, no network, no database
python demo_normalize_offline.py            # the chai plan, offline
```

Station 4's tests are fed a complete edge set built from the chai answer key
rather than piped through Station 3's own coverage — otherwise a gap in Station
3's pre-pass would show up as a Station 4 failure, which tests the wrong thing.
Purity is enforced by the same AST walk Station 2 uses.


---

## Station 5 — The PlanGraph & Writer

**From finalized edges to a persisted, traversable planning graph.**

Defines the PlanGraph — the project's own typed dependency graph — and writes
Station 4's output into it. This is the **only part of the project that mutates
the database**, and the only one that deletes.

Implements `PRD_PlanGraph_Writer.docx` (Bridge Station 5).

### The schema

| Collection | Holds |
| --- | --- |
| `{prefix}_Skills` | high-level tasks, one per rulebook |
| `{prefix}_Primitives` | atomic robot actions |
| `{prefix}_Objects` | physical things acted on |
| `{prefix}_States` | world conditions |
| `{prefix}_PlanEdges` | every edge, carrying a `type` attribute |

One edge collection keeps traversal filtering simple; the named graph
`{prefix}_PlanGraph` binds the endpoint types. Edge types are `decomposes_to`
(Skill→Primitive), `requires` and `produces` (Primitive→State), `precedes`
(Primitive→Primitive) and `uses` (Primitive→Object).

The PlanGraph deliberately lives in its own collections, **separate from
AutoGraph's `{project}_kg`** — AutoGraph owns and rebuilds its knowledge graph,
and a rebuild would wipe anything stored there.

### Identity is scoped per task

A vertex key is `(skill_scope, normalized_name)`, e.g.
`make_masala_chai__pan_on_stove`. Different rulebooks reuse the same words —
"serve" appears in coffee, burger and chai — and merging them would let one
recipe's edges bleed into another and corrupt its plan. Scoping prevents a
coincidental shared `stove_on` from chaining actions across unrelated tasks.

Edge keys are a hash of `(skill_scope, from, to, type)`, so a rebuild dedupes
instead of accumulating. Vertex keys stay readable because a key you can read is
worth a great deal when debugging a graph; they fall back to a hash suffix only
when the readable form would exceed ArangoDB's limit.

### Isolation is an allowlist, not a denylist

The PRD asks only that AutoGraph's `{project}_kg` never be modified. But the live
pilot database is **shared** — it holds `AIS-1847_test_*`, `E2E_test_*`,
`api_test_project_*`, `plannerTest_*` and more beside our own. A denylist of
AutoGraph's names would have been a list that silently goes stale.

So every mutating call goes through `GuardedDatabase`, which permits exactly the
five PlanGraph collections and refuses everything else — creates, writes,
deletes, index builds, graph definitions, and any AQL write query naming a
collection outside the set. There is one place to audit rather than a convention
to remember. The offline test double is seeded with the other projects'
collections and raises if any of them is touched, so the guarantee is tested from
both sides.

### Running it — the default is a dry run

```bash
python normalize_kg.py --format json > s4.json
python write_plangraph.py --from-json s4.json           # DRY RUN: touches nothing
python write_plangraph.py --from-json s4.json --write   # applies
python write_plangraph.py --write                       # Stations 1-5 end to end
```

Because a scoped rebuild **deletes** the task's existing subgraph before
rewriting it, a plain run plans the whole write, reads back what would be purged,
and touches nothing. `--write` is required to apply.

### Rebuild semantics

Re-running for a task is a **scoped rebuild**: delete that `skill_scope`'s
vertices and edges, then rewrite from the current run. No stale edge survives a
rulebook change, and other tasks are untouched. Where the deployment supports
stream transactions the purge and rewrite run inside one, so a task is never left
half-written.

`build_id` is a **hash of the input**, not a timestamp. Section 10 requires that
identical input yields an identical PlanGraph, and a clock would make every
re-run differ in every document — so re-running unchanged input is a genuine
no-op rather than a no-op that rewrites every field.

### Provenance

Every edge carries `type`, `method` (`rule` | `lexical` | `llm` | `derived`),
`confidence`, `evidence` (the original RELATED_TO description), `src_relation`,
and for a derived edge the state and relation keys it was chained from. Every
vertex and edge carries `skill_scope` and `build_id`. Any element of the planning
graph can be walked back to the sentence that caused it.

### The vector index

FR-6 calls AutoGraph's `embed-field-in-collection` endpoint over the Skills
`description` field. That endpoint's contract is AutoGraph's, not ours, so its
URL and payload are configuration rather than an assumption baked into code —
with `AUTOGRAPH_URL` unset the index is reported as `not_configured`, which is the
honest state rather than a silent success.

Per Section 11, a missing or failing endpoint is **never** an error: the graph is
written and traversable, and only goal resolution is degraded until the index
exists. The skill's embedded text is its name plus the decomposition evidence —
a command names the task, so the name has to be in what gets embedded.

### The read contract

`readback.read_subgraph(db, schema, scope)` returns what Layer 2 would traverse —
the precondition-closed subgraph, ready for topological ordering. It is a
*verification* helper, not the planner (Section 3 puts planning in Layer 2), but
it is what makes acceptance criterion 4 checkable. On the chai graph it reads back
11 primitives, 10 states, 8 objects, zero unmet preconditions, and sorts to the
recipe.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PLANGRAPH_PREFIX` | `PROJECT_NAME` | collection prefix |
| `SKILL_SCOPE` | inferred | the task scope; inference requires exactly one skill |
| `PLANGRAPH_BUILD_ID` | content hash | override the build identifier |
| `PLANGRAPH_EMBEDDING_FIELD` | `description` | field the vector index covers |
| `ARANGO_WRITE_USERNAME` / `_PASSWORD` | Station 1's | a writing user, if distinct |
| `AUTOGRAPH_URL` / `AUTOGRAPH_API_KEY` | — | embed-field endpoint |

### Verify

```bash
python -m unittest discover -s tests -t .   # 482 tests, no network, no database
python demo_write_offline.py                # write, re-write, read back, offline
```


---

## Layer 2 — Planning

**From a natural-language command to an ordered `plan.json`.**

```
command -> goal resolution -> subgraph traversal -> topological sort -> plan.json
```

Read-only over the PlanGraph. Implements `PRD_Layer2_Planning.docx`.

Two decisions define its shape, and both are about restraint: **the LLM's role is
thin** — it phrases, it never orders or invents — and retrieval is a **single
forward traversal**, not a backward-chaining planner. Similarity search only
locates the starting point; the graph's structure supplies correctness and order.

### The four stages

**1 · Goal resolution.** Score the command against the Skills and take the best
match if it clears the threshold. Below threshold, or two candidates within the
tie margin, it **asks rather than guesses** — a wrong goal produces a confident,
fluent, entirely wrong plan.

**2 · Subgraph traversal.** From the goal skill, walk the named graph bounded to
that `skill_scope`, gathering the precondition-closed subgraph.

**3 · Ordering.** Topological sort over two independent sources of constraint:
the `precedes` edges Station 4 wrote, *and* the produces→requires state chain
recomputed from the subgraph. On a bridge-built graph they agree exactly —
recomputing is not redundant, it means Layer 2 still orders correctly against a
PlanGraph whose `precedes` edges are missing. Ties break by name; a cycle is a
hard error naming the offending edges.

**4 · Composition.** The order is already fixed. The LLM only phrases each step.

### The grounding guard

The composed steps must map **one-to-one and in the same order** to the primitives
from Stage 3. If they don't, the reply is rejected, reprompted once, then
abandoned for templated phrasing. A model that reorders, drops, adds or renames a
step cannot affect the plan — there is a test that hands the composer a
deliberately reversed reply and asserts the plan comes back in the correct order
with `composer: template`.

That is what `--no-llm` demonstrates directly: the same plan, plainly worded.

```bash
python plan_command.py "make me a chai"            # LLM phrasing
python plan_command.py "make me a chai" --no-llm   # identical order, templated
python plan_command.py "fold my t-shirt" --format json > plan.json
```

### Live, against the real PlanGraph

```
  command            "make me a chai"
  goal               MAKE MASALA CHAI
  match              0.64 (lexical)
  composer           llm (anthropic/claude-opus-5)
  ordering           11 constraint(s): 11 from precedes edges, 11 from the state chain

   1. Place the pan on the stove.          7. Stir sugar into the brewing tea.
   2. Pour water into the pan.             8. Let the tea simmer until brewed.
   3. Turn on the stove.                   9. Strain the tea into the cup.
   4. Heat the water until it boils.      10. Serve the cup of chai.
   5. Add tea leaves to the water.        11. Turn off the stove.
   6. Pour milk into the brewing tea.
```

The step *order* is byte-identical with and without the LLM. Only the wording
changes.

### Goal resolution without the vector index

Station 5's Skills vector index needs AutoGraph's embed-field endpoint, which
isn't configured, so `VectorRetriever` reports itself unavailable and the planner
falls back to a **TF-IDF cosine** over the same Skills descriptions.

IDF is what makes that work here: three of the six skills begin with "make", so
the word carries almost no signal while "chai" identifies one. Skill names are
weighted above their prose because a command names the task, and a token matches
a longer one containing it, so "plants" reaches `water_houseplants` and "shirt"
reaches `fold_tshirt`.

The fallback keeps the **threshold and tie-margin semantics identical**, so the
guardrail is unaffected — and `meta.match_method` records which retriever ran, so
a plan never silently claims a vector match it didn't have.

### The `plan.json` contract

```json
{
  "goal": "MAKE MASALA CHAI",
  "command": "make me a chai",
  "steps": [
    {"order": 1, "action": "PLACE PAN", "description": "Place the pan on the stove.",
     "requires": [], "produces": ["PAN ON STOVE"], "uses": []}
  ],
  "meta": {"skill_scope": "make_masala_chai", "generated_at": "...",
           "model_id": "anthropic/claude-opus-5", "match_confidence": 0.64,
           "match_method": "lexical", "composer": "llm", "ordering": {...}}
}
```

`description` is the only LLM-authored field. `order`, `action`, `requires`,
`produces` and `uses` all come straight off the graph.

### The corpus

`dataset/` holds five more rulebooks — coffee, burger, t-shirt, bed, houseplants
— and only chai has been through AutoGraph. `tests/rulebook_fixture.py` parses
the rest into Station 1 bundles so the whole bridge can run on all six offline.
It is a **test fixture standing in for AutoGraph extraction**, not a product
component: it exploits the fact that the rulebooks share one rigid template.

What it buys is a real test of per-task scoping. All six PlanGraphs live in one
database and share names — chai and burger both have `turn_on_stove` and `serve`
— and each plan contains only its own steps.

| Command | Resolves to | Match |
| --- | --- | --- |
| "make me a masala chai" | `make_masala_chai` | 0.88 |
| "I want a cup of pour over coffee" | `make_pour_over_coffee` | 0.92 |
| "fold my t-shirt" | `fold_tshirt` | 0.70 |
| "make the bed" | `make_bed` | 0.82 |
| "water the houseplants" | `water_houseplants` | 0.88 |
| "cook a burger" | `cook_burger` | 0.89 |
| "please reticulate the splines" | — | clarification |

Every plan respects every ordering constraint its rulebook states, including the
one the PRD calls out: `assemble_burger` comes after both `add_cheese` and
`toast_buns`.

### Exit codes

`0` a plan · `3` clarification needed · `4` incomplete PlanGraph · `5` a cycle.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PLANNER_THRESHOLD` | `0.35` | match score required to accept a goal |
| `PLANNER_TIE_MARGIN` | `0.05` | closer than this and the match is ambiguous |
| `PLANNER_TOP_K` | `3` | candidates offered on clarification |
| `PLANNER_MODEL` | `LLM_MODEL` | overrides the model for phrasing only |

### Verify

```bash
python -m unittest discover -s tests -t .   # 562 tests, no network, no database
python demo_plan_offline.py                 # all six rulebooks, offline, no LLM
```


---

## The AutoGraph pipeline — rulebooks to PlanGraph

**One command from a folder of rulebooks to a plannable PlanGraph.** Every step
before Station 1 used to be done by hand in the AutoGraph UI. This drives them
through AutoGraph's own HTTP API, then hands the knowledge graph to the bridge:

```
rulebooks ─► File Manager ─► corpus graph ─► strategies ─► ontology ─► knowledge graph ─► PlanGraph
             [project, category]   /corpus/builds  /analyze   PATCH     /orchestrate      Stations 1-5
```

```bash
python build_kg.py generated/ --project openAMR --category nav            # dry run
python build_kg.py generated/ --project openAMR --category nav --write    # build it
python build_kg.py generated/ --project openAMR --category nav --write --rebuild
python build_kg.py dataset/ --project kitchen --write --provision
```

**All the rulebooks go in one module.** The category is AutoGraph's unit of
isolation: its files are clustered together and imported into one set of
knowledge-graph partitions.

**The ontology is applied, not hoped for.** The strategizer asks an LLM for 8-12
entity types per cluster, but the bridge reads exactly four. So the pipeline runs
it at `complexity: very_high`, which makes every cluster FullGraphRAG. At
`moderate`, a one-cluster module rounds to zero FullGraphRAG clusters and
extracts no entities at all. It then PATCHes each of the module's clusters to
`[SKILL, PRIMITIVE, OBJECT, STATE]` before the importer runs. A cluster already
imported under another ontology is reported, not patched, because a patch after
import changes nothing.

**Every stage reads before it acts.** The project overview says whether the
corpus, the strategies and the knowledge graph are current for this category,
and a stage whose work is done says so and moves on. So a rerun resumes, which
matters here because AutoGraph refuses a full rebuild of a built category and
answers an orchestration with nothing stale as a 409.

| Stage | Reads | Acts |
| --- | --- | --- |
| connect | ACP project record → its AutoGraph service | `--provision`: create the project, deploy a service |
| upload | File Manager, scope `[project, category]` | upload what is missing |
| corpus | overview: `needs_corpus_update` | `POST /v1/corpus/builds`, poll |
| strategize | overview: `categories_without_strategies` | `POST /v1/rag-strategizer/analyze`, poll |
| ontology | `GET /v1/rag-strategizer/strategy` | `PATCH` each of the module's clusters |
| kg | overview: `new_categories` | `POST /v1/orchestrate`, poll |
| plangraph | — | `grasp_web.bridge.build`, Stations 1-5 |

**A plain run writes nothing.** It connects, reads, and reports each stage as
done, skipped, or what it would do. The rule is Station 5's, and it has more
reason here: this uploads files, spends model tokens, and can deploy services.

**Changing a built module needs `--rebuild`.** New or changed rulebooks for a
category that is already built are refused rather than uploaded. AutoGraph never
re-extracts a partition it has imported, so appending would build a corpus the
knowledge graph never catches up with. `--rebuild` deletes the category first
(`DELETE …/categories/{category}?delete_files=true`: corpus, strategies, KG
partitions and files), then builds it from scratch. "Changed" is judged by the
size File Manager reports, since that is the one comparison available without
downloading every file.

**One service per project.** An AutoGraph service is deployed for one
`genai_project_name`, and ACP keeps the record after its release is deleted.
The pipeline checks that the service's route actually answers. `--provision`
redeploys it with the project's saved model settings, or copies them from
`AUTOGRAPH_MODEL_FROM`. The embedding model is fixed for a project's lifetime,
so it is never guessed.

Labels follow the documented contract: bare category names everywhere. Older
services matched the strategizer and orchestrator against the encoded module
(`{project}_{category}`, with `_` inside a segment percent-encoded), so that is
the fallback when a bare label is refused.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `AUTOGRAPH_SERVICE_PATH` | from ACP | talk to this service path instead of the project's own |
| `AUTOGRAPH_COMPLEXITY` | `very_high` | share of clusters made FullGraphRAG |
| `AUTOGRAPH_ONTOLOGY` | `SKILL,PRIMITIVE,OBJECT,STATE` | entity types each cluster extracts |
| `AUTOGRAPH_REPLICAS` / `_MAX_RETRIES` | `1` / `3` | importer parallelism and retries |
| `AUTOGRAPH_POLL_SECONDS` | `10` | status poll interval |
| `AUTOGRAPH_{CORPUS,STRATEGIZE,ORCHESTRATE}_TIMEOUT` | 2 h / 1 h / 6 h | give up waiting (the work carries on server-side) |
| `AUTOGRAPH_MODEL_FROM` | — | `--provision`: copy model settings from this project |
| `AUTOGRAPH_FPS_RECOVERY_USERNAME` | — | `--provision`: an ArangoDB user with `rw` on the database; ACP refuses the install without one |

It reads the same `ARANGO_URL` / `ARANGO_DB` and credentials as every station:
ACP, File Manager and each AutoGraph service sit behind that one gateway, and a
password is exchanged for a JWT that is renewed when a long build outlives it.

### Exit codes

`0` done, or a dry run completed · `2` configuration · `3` auth · `4` unreachable ·
`10` a stage failed.

### Verify

```bash
python -m unittest tests.test_autograph_pipeline   # no network: a fake platform answers the real requests
```


---

## Task decomposition — compound commands

Layer 2 plans exactly one skill per command, so "go to 2,1 and then charge the
robot" needs cutting first. `task_decomposition/` sits in front of Layer 2 and
does that, and the web planning page goes through it by default:

```
command ─► decompose ─► [subtask 1, subtask 2, …] ─► Layer 2 per subtask ─► merge ─► one run
```

**The model splits, picks and copies — nothing more.** It is handed the skill
catalog the PlanGraph already holds (each skill with its steps) and returns, per
subtask, the words it covers, one catalog skill, and the values the user stated
(`x`, `y`, `distance_m`, `yaw_deg`, …). A guard rejects a skill outside the
catalog and any value that does not appear in the command — an invented
coordinate is a robot driving somewhere else — reprompts once, then falls back to
a deterministic splitter that cuts on "then" / "and" / ";" and lets Layer 2's own
goal resolution pick each skill. With no LLM configured, that splitter is the path.

**Each subtask is a normal Layer 2 plan.** Order and membership still come from
the graph. A skill the model chose is planned directly (`match_method:
"decomposer"`, with the lexical score kept for honesty); an unmatched subtask goes
through goal resolution and may come back as a clarification, in which case the
whole run is marked `executable: false`.

**Merging drops repeated bring-up, never a goal.** Every skill carries its own
prerequisites, so "charge the robot" would start localization again. A later
subtask's *supporting* step (something later in its own plan depends on it) is
left out when an earlier subtask already ran it, and reported in `skipped`. A
subtask's *goal* steps are always kept, so "go to 1,0 then go to 2,1" navigates
twice. Values land on the goal step, next to its execution handle:

```
  Task 1: "go to 2,1" ↳ go_to_location  [x=2, y=1]
     1. start_localization      ↳ service:  /lifecycle_manager_localization/is_active
     2. set_initial_pose        ↳ topic:    /initialpose
     3. start_navigation        ↳ service:  /lifecycle_manager_navigation/is_active
     4. navigate_to_goal        ↳ topic:    /goal_pose   ↳ with: x=2, y=1
  Task 2: "charge the robot" ↳ dock_at_charger
      skip start_localization, set_initial_pose, start_navigation (already run)
     5. dock_robot              ↳ topic:    /dock_trigger
```

The graph models no negative effects — nothing says undocking makes "docked"
false — so a skipped step is assumed to still hold. Right for bring-up; wrong for
a state a later task undoes.

```bash
python plan_task.py "go to 2,1 and then charge the robot"
python plan_task.py "back up 0.3 m, then rotate 90 degrees" --format json
python plan_task.py "go to location then dock at charger" --no-llm
```

A one-task command comes back as Layer 2's own `plan.json`, with the
decomposition and any extracted values under `meta`. A compound one is
`{command, executable, decomposition, subtasks[], steps[], skipped[]}`, where each
merged step is a Layer 2 step plus `subtask`, `goal` and `parameters`.


---

## The web front end

**Ask in plain English in a browser; read the plan.**

```bash
python serve_grasp.py             # http://127.0.0.1:8080
python serve_grasp.py --open      # and open a browser
python serve_grasp.py --no-llm    # templated wording, same step order
```

Reads the same environment as the CLI. Read-only over the PlanGraph, and bound to
localhost unless `--host` says otherwise — this is a developer tool sitting in a
process whose environment holds write credentials, so it should not listen on a
public interface by accident.

### It is deliberately thin

Every decision the page shows is already made by Layer 2 — the goal, the order,
the confidence, the clarification. `grasp_web/api.py` connects once, keeps the
retriever warm and serializes; it holds no planning logic, so the browser and the
CLI cannot drift apart in what they answer.

| Route | Returns |
| --- | --- |
| `GET /projects` | the project list: discover, build, view, ask |
| `GET /` | the planning page (`?project=` selects the graph) |
| `GET /generate` | the rulebook generator page (video) |
| `GET /repo` | the rulebook generator page (docs repo) |
| `GET /api/health` | database, graph, skill count, retrieval method, phrasing |
| `GET /api/skills` | every skill with its step count |
| `POST /api/plan` | `{command, use_llm}` → a plan, a clarification, or a stated error |
| `GET /api/generate/health` | model, strictness, whether captions can be fetched |
| `GET /api/generate/library` | rulebooks already on disk |
| `POST /api/generate` | `{url \| transcript}` → a job id |
| `GET /api/generate/<job>` | that job's state, stage and result |
| `POST /api/repo/tree` | `{url}` → the repo's documentation pages, ranked |
| `POST /api/repo/generate` | `{url, ref, paths}` → a job id |
| `GET /api/projects` | every project in the database and the stage it has reached |
| `POST /api/projects/build` | `{project}` → a build job id (**the one route that writes**) |
| `GET /api/projects/build/<job>` | that build's state, stage and station reports |
| `GET /api/kg/health` | whether rulebooks can be built through AutoGraph, and with what ontology |
| `POST /api/kg/build` | `{job \| files, project, category, write, rebuild, provision}` → a run id |
| `GET /api/kg/build/<job>` | that run: every stage's status, the running stage's latest message, a log |

**Standard library only.** A handful of JSON routes do not justify a web
framework, and `requirements.txt` still lists one required package.

### What the page shows

The header carries the connection as pills — database, graph, skill count, and
whether retrieval is `vector` or `lexical`, the last flagged amber when no vector
index exists yet. Each answer is a card: the goal, the match score, how many
ordering constraints produced the sequence, and whether the wording is
model-written or templated.

Every step shows its `requires` / `produces` / `uses` edges as coloured chips, so
the dependency structure is readable without opening the JSON — amber for a
precondition, green for an effect. A below-threshold command renders as a
clarification with its candidates as buttons, which is the same refusal to guess
the CLI makes, only clickable.

The **model phrasing** toggle is the honest demonstration: turn it off and the
wording gets plainer while the order does not move at all.

### The generator page

`/generate` turns a captioned video into a rulebook in the same browser. Paste a
link — or the transcript itself, if the video has no captions or
`youtube-transcript-api` is not installed.

Generation is slow: captions, then a minute or two of reconstruction. A
synchronous request would leave the page spinning with nothing to say, so it runs
as a **job** — the POST starts it and returns an id, and the page polls for the
stage it has reached. Those stages come from `rulebook_generator.pipeline` itself
via an `on_stage` callback, so the page names real boundaries rather than a
progress bar invented to look busy.

The result leads with the verdict, because that is the thing that matters:
`accept` in lime, `flag_for_review` in amber, `reject` in red. Under it, the shape
of what was extracted — primitives, states, objects, and the count of `requires`
marked *mostly inferred*, since that is the part the transcript never stated. Then
every validation issue with its code and detail, the plan the rulebook implies,
and the markdown itself with copy and download.

Download is a client-side blob, not a server write: the page should not need a
write endpoint to hand you a file. Rulebooks already on disk appear as chips, so a
demo can reopen one without re-running a two-minute generation.

### The repository page

`/repo` is the same generator pointed at a **robotics documentation repo** instead
of a video. Paste `PX4/PX4-user_guide`, pick the pages that describe one procedure,
and the manual-mode pipeline does the rest. It shares the verdict rendering with
`/generate` — both load `static/rulebook.js` — so the two pages cannot drift apart
in how they report a gate result.

The extra step over the video page is the **picking**, and it is the point. A guide
holds hundreds of pages and a rulebook describes one procedure, so the page lists
what the repo has, ranked by how procedure-shaped each path looks, with a filter
and a cap of twelve. The ranking is a hint and is labelled one: it reorders the
list, it never ticks a box. Which pages describe the procedure you mean is a
question you have a much better answer to than a keyword score does.

Only GitHub's API and raw hosts are ever fetched. That is a deliberate limit: the
server is handed a URL by whoever opens the page, and a fetcher that takes any URL
is an SSRF hole pointed at whatever the host can reach. Set `GITHUB_TOKEN` to lift
the unauthenticated rate limit from 60 requests an hour to 5,000.

**It reports what became of your selection.** Four PX4 pages — arming, takeoff,
return, landing — produce a rulebook for arming alone: the model judges the rest a
different procedure, and the gate raises nothing, because "covers less than you
selected" is not a defect in the rulebook. It is still the thing you need to know,
so the page names the pages no step was drawn from. A page counts as used only when
every word of some primitive's name appears in it — the gate's own grounding check
is looser on purpose, and a one-word rule would call every page used, since
"vehicle" is on every page of a drone manual.

### Code mode: a rulebook you can execute

The repo page reads a repository in one of two registers, chosen before the
listing:

| mode | reads | produces |
| --- | --- | --- |
| **documentation** | `.md` `.mdx` `.rst` | what a person does |
| **source code** | that, plus `.py` `.cpp` `.hpp` `.srv` `.action` `.msg` `.yaml` `.launch` | what a **program calls** |

In code mode every primitive carries an **execution handle** — the concrete thing
to invoke, with its name copied verbatim and its message type:

```
**play_external_control_program** — The robot starts the loaded External Control
program so that the driver takes over control of the arm. It is executed by
calling the service `/dashboard_client/play` of type `std_srvs/srv/Trigger`.
This action requires that program loaded is already true as a precondition.
After this action, program running is true.
```

The handle is a first-class field on the primitive (`kind`, `name`, `type`), not
a note in the prose. Three rules make it trustworthy:

- **Names are never normalized.** Every other name in a rulebook is folded to
  snake_case; a ROS name that has been folded is no longer callable, so
  `/dashboard_client/play` is kept exactly as written — and long handles are
  protected from line wrapping, because a handle broken across a line is not one.
- **Invented handles are worse than absent ones.** The prompt's second rule is
  "never invent an interface" — a guessed service name is a call that fails at
  runtime, where a missing one fails honestly at review.
- **The gate enforces the round trip.** `parse(render(x))` must recover the handle
  too; a handle that does not survive rendering is fatal, because the plan would
  then name a call the source never did.

Two checks come with it. `missing_interface` flags the steps a plan could not run,
but only in a rulebook that has handles at all — a rulebook from a video describes
what a person does and has none, and demanding them there would flag every
rulebook the generator has ever produced. `duplicate_interface` flags two steps
that resolve to the same call, which usually means one of them is wrong.

### What it does on a real driver

Pointed at `UniversalRobots/Universal_Robots_ROS2_Driver`, three usage pages:

```
verdict  flag_for_review
skill    startup_ur_driver_move_robot (9 primitives)
issues   orphan_precondition · missing_interface · duplicate_interface · multiple_tasks

load_external_control_program  service  /dashboard_client/load_program          ur_dashboard_msgs/srv/Load
play_external_control_program  service  /dashboard_client/play                  std_srvs/srv/Trigger
switch_controllers             service  /controller_manager/switch_controller   controller_manager_msgs/srv/SwitchController
execute_joint_trajectory       action   .../follow_joint_trajectory             control_msgs/action/FollowJointTrajectory
launch_driver                  —        no interface — a plan cannot run this step
```

Both new checks earn their keep on the first real run. `launch_driver`,
`verify_calibration` and `list_controllers` are things an operator types, not
things a program calls, so they are named as unrunnable rather than passed off as
steps. And `play_external_control_program` and `resume_program_after_interruption`
both resolve to `/dashboard_client/play` — one call, two steps.

Feeding it the whole dashboard service catalogue instead is the failure worth
knowing about: all 18 services come back with correct handles, and the gate
**rejects** the result on a cycle between `stop_program` and `power_off_motors`.
A catalogue is not a task, and the same discipline applies as everywhere else here
— pick the files that describe one procedure.

### The projects page

`/projects` is the entry point, and it is the whole flow in one screen:

```
corpus graph  ->  knowledge graph  ->  PlanGraph  ->  ask for a plan
  AutoGraph         AutoGraph          this page      this page
```

You build the first two in AutoGraph. The moment a project has a knowledge graph
it appears here with a **Build PlanGraph** button; when the build finishes it
offers **View in ArangoDB** — a deep link into Arango's own graph viewer — and
**Ask for a plan**, which opens the planning page scoped to that project.

**Discovery needs no new convention.** AutoGraph and the bridge already name their
graphs after the stage, so listing the named graphs *is* the state machine:

| graph | built by | means |
| --- | --- | --- |
| `{project}_CorpusGraph` | AutoGraph | the corpus graph exists |
| `{project}_kg` | AutoGraph | the knowledge graph exists |
| `{project}_PlanGraph` | this page | it can be planned against |

A project with only a corpus is listed as *waiting*, not hidden — an empty screen
is a bad way to find out you built the wrong thing.

### It checks the ontology before offering the button

A knowledge graph can exist and still hold nothing the bridge can plan, and both
failure modes are live in `test_shlok` today. One project is an insurance graph
whose entities are `insurance_claim` and `adjuster`. Another names its types in
the **plural** — `skills`, `primitives` — which `canonical_type` does not resolve,
because the ontology and its synonym table are singular. Neither can produce a
PlanGraph, so neither gets a Build button; both get the reason and the types that
were actually found.

### Building is the one thing in this UI that writes

Everything else — listing, planning, viewing — is read-only, and Layer 2 is
read-only by contract. A build is the exception, so it is guarded:

* **confirmed first**, naming the collections it will write and, on a rebuild, the
  scopes it will delete and recreate — Station 5's scoped write purges before it
  writes;
* **one at a time per project**, because two concurrent scoped writes would
  interleave a purge with a write;
* **re-checked server-side** against the live state, so a stale page cannot
  authorise a build the database no longer supports.

It runs the five stations in one process and reports each as it lands:

```
✓ reading the knowledge graph      35 relationship bundle(s) from 35 edge(s)
✓ typing the relationships         14 typed by rule, 21 need a model
✓ resolving requires vs produces   21 settled by the lexical pre-pass
✓ deriving the ordering            35 edge(s) oriented, 11 ordering(s) derived
✓ writing the PlanGraph            wrote 1 skill scope(s) into roboticsPlanner_PlanGraph
```

The third line is worth its own note. Station 3 is the paid station, so the cost
is reported — but split by where each verdict actually came from. On that build the
lexical pre-pass settles all 21 and the model is never called, and reporting "21
settled" beside a model name would imply a request that never happened.

`grasp_web/bridge.py` runs the stations and knows nothing about threads or HTTP;
`builder.py` runs it as a polled job. Same split as `generate.py` against
`rulebook_generator`, for the same reason: no pipeline logic in the server, so the
UI and the CLIs cannot disagree about what a build did.

### Building a PlanGraph from generated rulebooks

After a split generation, the page lists every task rulebook with its verdict.
Accepted ones are ticked, flagged ones are left to you, and rejected ones can't
be ticked. Under the list is a **Build the PlanGraph** panel, which also appears
on the projects page for rulebooks already on disk. Name a project and a module,
then:

1. **Check** is a dry run of the AutoGraph pipeline. It reports each of its
   seven stages as *already done* or *would run*, and writes nothing.
2. **Build PlanGraph** is offered only for exactly the inputs that were checked.
   It runs the pipeline as a job, and the page polls it. The running stage shows
   its latest report (`55% Creating similarity edges…`,
   `importing: 1/2 job(s)`), finished stages keep their outcome, and a log keeps
   the rest.

The browser names the rulebooks and never sends them. The server reads them from
the finished generation job or from disk, so what reaches File Manager is exactly
the markdown the gate graded.

### The palette

Taken from arango.ai's own brand tokens — `#044926` deep green, `#b9ff38` lime,
`#befe99` light green, `#151d25` dark slate. The lime is rationed on purpose: it
marks the one thing that matters on a surface (the goal, the send button, a step
number). Spread everywhere it would stop meaning anything.

No CDN, no external font, no third-party script — the page loads only its own two
assets, and a test asserts that. Skill names and model-written wording are
escaped before they reach the DOM; neither is trusted markup.

### Verify

```bash
python -m unittest discover -s tests -t .   # includes 30 tests for the UI
```

The route tests run a real server on a real socket rather than mocking the
handler, and cover path traversal, oversized bodies and malformed JSON.


## The bundle contract

One bundle per relationship (PRD Section 7). This is the canonical unit passed to
Station 2, so its shape is fixed:

```json
{
  "relation_key": "r012",
  "source": { "name": "place_pan",    "type": "PRIMITIVE" },
  "target": { "name": "pan_on_stove", "type": "STATE" },
  "description": "After this action, the pan is on the stove."
}
```

## Reuse in the bridge

The read is a plain function, which is what Station 2 (rule-based
pre-classification) will call — nothing about this harness has to change when
classification is added:

```python
from kg_read_harness import load_config, read_relationship_bundles
from kg_read_harness.client import connect

config = load_config()
db = connect(config)
for bundle in read_relationship_bundles(db, config):
    route(bundle.type_pair, bundle)     # ("PRIMITIVE", "STATE") -> ...
```

`read_relationship_bundles` streams through a server-side cursor, so memory stays
bounded on 10^4-scale graphs; `read_bundles(db, config)` is the eager variant.

## Read-only guarantee

The single most important constraint in the PRD is that this harness never
mutates the KG. Three things enforce it:

1. Every query passes `assert_read_only()`, which rejects any AQL text containing
   `INSERT`, `UPDATE`, `REPLACE`, `REMOVE`, `UPSERT`, `TRUNCATE`, `CREATE` or
   `DROP` — `client.run_query()` is the only path to the server.
2. No code calls a mutating python-arango method; only `aql.execute`,
   `has_collection` and `collection(...).properties()` are used.
3. The test suite runs the whole CLI against a database double that raises on any
   write attempt, and re-checks every query the run issued.

Running against a read-only DB user therefore succeeds normally.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Unexpected error |
| 2 | Configuration problem (missing/invalid env var) |
| 3 | Authentication rejected |
| 4 | Endpoint unreachable / database absent |
| 5 | Collection not found |
| 6 | Type or description attribute absent |
| 7 | Empty result (no entities, no relations, or none of the requested type) |
| 8 | Read-only guard tripped (a harness bug; never expected) |
| 130 | Interrupted |

Every failure prints what failed plus the most likely fix, and never prints a
credential.

## Verify

```bash
python -m unittest discover -s tests -t .    # 58 tests, no database required
python demo_chai_offline.py                  # full CLI against an in-memory chai KG
```

`demo_chai_offline.py` runs the real config, validation, read and output code
against a masala-chai graph built from `masala_chai_rulebook.md`, which is how the
acceptance criterion — non-zero `PRIMITIVE→STATE` and `SKILL→PRIMITIVE` counts —
is checked without an ArangoDB instance. Against a live KG, run `read_kg.py` and
read the same summary (see the section above for the equivalent live counts).

## Layout

```
kg_read_harness/          Station 1 — Read
  config.py     env → Config; fails fast, hides secrets      (FR-1)
  client.py     read-only connection + the read-only guard   (FR-2)
  validate.py   collection/attribute existence and shape     (FR-3)
  read.py       the AQL read → Bundle iterator               (FR-4, FR-5)
  bundle.py     the Section 7 contract
  output.py     table + JSON listing, type-pair summary      (FR-6, FR-7)
  errors.py     typed failures, hints, exit codes            (FR-8)
  cli.py        one-shot entry point
autograph_pipeline/       Rulebooks -> KG -> PlanGraph over AutoGraph's API
  client.py     auth, ACP, File Manager, every AutoGraph route
  config.py     complexity, ontology, polling, provisioning
  pipeline.py   the seven stages, each resuming from the overview
  plangraph.py  the last stage: grasp_web.bridge over the new KG
  cli.py        entry point; dry run unless --write
grasp_web/                Web front end
  api.py        a thin shell over Layer 2; no planning logic
  generate.py   the generator half: jobs, stages, the library
  kgbuild.py    rulebooks -> AutoGraph -> PlanGraph as a polled job
  server.py     stdlib HTTP: the JSON routes plus the static pages
  cli.py        entry point
  static/       index.html · generate.html · app.css · app.js · generate.js

rulebook_generator/       Station 0 — Rulebook Generator
  transcript.py captions -> clean prose; optional YouTube fetch  (FR-2)
  schema.py     the structured intermediate                      (Section 6)
  extract.py    schema-constrained LLM pass + worked example      (FR-3)
  render.py     intermediate -> canonical markdown                (FR-4)
  parse.py      markdown -> intermediate; the round-trip reader
  validate.py   the gate: round-trip + the real bridge            (FR-5)
  ingest.py     optional auto-ingest to AutoGraph                 (FR-8)
  config.py     model, strictness, auto-ingest
  cache.py      reproducibility by transcript hash                (FR-6)
  pipeline.py   the five stages
  split.py      one rulebook per task: reference, task choice, slice
  direct.py     the shortcut: a rulebook file -> the PlanGraph
  report.py     the run summary
  cli.py        entry point · direct_cli.py  the ingest entry point
layer2_planning/          Layer 2 — Planning
  config.py     threshold, top-k, model                       (Section 9)
  retrieve.py   Stage 1: goal resolution + the guardrail      (FR-2)
  traverse.py   Stage 2: scoped forward traversal             (FR-3)
  order.py      Stage 3: topological sort, two constraint     (FR-4)
                sources, cycle = hard error
  compose.py    Stage 4: thin LLM + the grounding guard       (FR-5, FR-6)
  plan.py       the plan.json contract                        (Section 6)
  pipeline.py   the four stages in sequence
  report.py     terminal rendering
  cli.py        entry point
plangraph_writer/         Station 5 — The PlanGraph & Writer
  schema.py     collections, edge types, the named graph      (Section 4)
  identity.py   scoped vertex keys, hashed edge keys          (Section 5, FR-2)
  config.py     env -> WriterConfig; credentials never printed
  guard.py      the allowlist that confines every write       (FR-8)
  records.py    PlanVertex, PlanEdge, the write plan          (Section 7)
  build.py      Station 4 edges -> documents; pure, no I/O    (Section 6)
  writer.py     ensure schema, purge scope, upsert, verify    (FR-1, 3, 4, 5, 7)
  embed.py      the Skills vector index, degrading to pending (FR-6)
  readback.py   the Layer 2 read contract, for verification   (Section 8)
  report.py     the run summary
  cli.py        entry point; dry run unless --write
direction_normalizer/     Station 4 — Direction Normalizer & Order Resolver
  policy.py     canonical directions, trust hierarchy, cues    (Sections 5, 8)
  model.py      InputEdge + the three output streams           (Section 6)
  adapt.py      the only place that knows Station 2/3 shapes   (FR-1)
  chaining.py   state chaining: produces S -> requires S       (FR-3)
  cycles.py     the incremental cycle guard + topological sort (FR-6)
  normalizer.py the pipeline: normalize, chain, reconcile      (FR-2, FR-4, FR-5)
  report.py     the run summary and the plan                   (FR-8)
  cli.py        entry point
llm_disambiguator/        Station 3 — LLM Disambiguator
  config.py     env -> LLMConfig; the key is never printed     (Section 10)
  cues.py       the lexical cue lists                          (Sections 5, 6)
  lexical.py    the pre-pass                                   (FR-3)
  prompt.py     system instruction, rendering, strict parsing  (Section 7, FR-4)
  provider.py   OpenAI-compatible transport, retries, cap      (FR-7)
  cache.py      the content-hash verdict cache                 (FR-5)
  model.py      Verdict, ResolvedEdge, the run result          (Section 8)
  disambiguator.py  the three-stage pipeline + the gate        (FR-1..FR-6)
  report.py     the run summary                                (FR-8)
  cli.py        entry point
rule_preclassifier/       Station 2 — Rule Pre-Classifier
  table.py      the decision table, reason priority, synonyms (Sections 6, 9)
  model.py      the three buckets Stations 3/4 consume        (Section 8)
  detect.py     reason-code detection + type canonicalization (Section 9)
  classifier.py the pure classify() function + conservation   (FR-3, FR-6)
  report.py     the run summary                               (FR-7)
  cli.py        entry point; the only part that does I/O
generate_rulebook.py       Station 0 launcher: a video link in, a rulebook out
build_kg.py                rulebooks in, knowledge graph + PlanGraph out, via AutoGraph
ingest_rulebook.py         a rulebook straight into the PlanGraph (skips AutoGraph)
read_kg.py                 Station 1 launcher
classify_kg.py             Station 2 launcher
disambiguate_kg.py         Station 3 launcher
normalize_kg.py            Station 4 launcher
write_plangraph.py         Station 5 launcher (dry run unless --write)
plan_command.py            Layer 2 launcher: a command in, plan.json out
serve_grasp.py             the web UI
eval_chai_live.py          Station 3 accuracy eval vs. the answer key
demo_chai_offline.py       Station 1 offline self-check
demo_classify_offline.py   Station 2 offline self-check
demo_disambiguate_offline.py  Station 3 offline self-check
demo_normalize_offline.py     Station 4 offline self-check
demo_write_offline.py         Station 5 offline self-check
demo_plan_offline.py          Layer 2 offline self-check, all six rulebooks
dataset/                      five more rulebooks
tests/                     unit + end-to-end tests, fake ArangoDB, chai fixture
```

## Out of scope

Layer 3 (ROS execution of `plan.json`), the whole-graph validation wrapper,
backward-chaining and goal-state commands, multi-skill composition, incremental
or streaming updates, a persistent service or API, and any UI beyond the
terminal.
