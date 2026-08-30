# G.R.A.S.P

**GraphRAG → Action, Station 1 ("Read")** — from an AutoGraph KG to typed
relationship bundles.

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
classify and `skill→tool` (11) is the decomposition pair. Either fix the
extraction prompt's type vocabulary or have Station 2 map the synonyms; the
harness reports whatever the KG actually says and warns when a filter value falls
outside the PRD ontology.

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
kg_read_harness/
  config.py     env → Config; fails fast, hides secrets      (FR-1)
  client.py     read-only connection + the read-only guard   (FR-2)
  validate.py   collection/attribute existence and shape     (FR-3)
  read.py       the AQL read → Bundle iterator               (FR-4, FR-5)
  bundle.py     the Section 7 contract
  output.py     table + JSON listing, type-pair summary      (FR-6, FR-7)
  errors.py     typed failures, hints, exit codes            (FR-8)
  cli.py        one-shot entry point
read_kg.py             launcher
demo_chai_offline.py   offline self-check
tests/                 unit + end-to-end tests, fake ArangoDB, chai fixture
```

## Out of scope

The four remaining bridge stations, the PlanGraph write model, incremental or
streaming updates, a persistent service or API, and any UI beyond the terminal.
