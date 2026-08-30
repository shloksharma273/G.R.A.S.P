# G.R.A.S.P

**GraphRAG → Action** — the bridge that turns an AutoGraph knowledge graph into a
typed planning graph. Three of the five bridge stations are built:

| Station | What it does | Status |
| --- | --- | --- |
| 1 · Read | KG → relationship bundles | built (`kg_read_harness/`) |
| 2 · Rule pre-classify | bundles → edge types by entity-type pair | built (`rule_preclassifier/`) |
| 3 · LLM disambiguate | resolve `requires` vs `produces` | not started |
| 4 · Normalize direction | settle head → tail | not started |
| 5 · Write | persist to the PlanGraph | not started |

---

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
read_kg.py                 Station 1 launcher
classify_kg.py             Station 2 launcher
disambiguate_kg.py         Station 3 launcher
eval_chai_live.py          Station 3 accuracy eval vs. the answer key
demo_chai_offline.py       Station 1 offline self-check
demo_classify_offline.py   Station 2 offline self-check
demo_disambiguate_offline.py  Station 3 offline self-check
tests/                     unit + end-to-end tests, fake ArangoDB, chai fixture
```

## Out of scope

Stations 4–5, the PlanGraph write model, incremental or streaming updates, a
persistent service or API, and any UI beyond the terminal.
