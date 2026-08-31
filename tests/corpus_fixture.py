"""The whole bridge, run offline over every rulebook in the corpus.

Layer 2's acceptance criteria need six PlanGraphs and only chai has been through
AutoGraph, so this drives Stations 2-5 over the parsed rulebooks and writes them
all into one in-memory database — which also exercises the thing scoping exists
for, since these tasks share state and object names with each other.

Station 3's labels come from each rulebook's own answer key rather than from a
model, so Layer 2 is measured here and Station 3 is not re-measured.
"""

from __future__ import annotations

from functools import lru_cache

from direction_normalizer import normalize
from kg_read_harness.bundle import Bundle
from llm_disambiguator.model import (
    METHOD_LLM,
    ORIENTATION_IMPLIED_BY_LABEL,
    ResolvedEdge,
)
from plangraph_writer import build, write_plangraph
from plangraph_writer.config import load_writer_config
from rule_preclassifier import classify
from rule_preclassifier.model import Orientation

from .fake_writable_arango import make_db
from .rulebook_fixture import Rulebook, all_rulebooks

ENV = {
    "ARANGO_URL": "http://offline.invalid:8529",
    "ARANGO_DB": "test_shlok",
    "ARANGO_USERNAME": "root",
    "ARANGO_PASSWORD": "",
    "PROJECT_NAME": "roboticsPlanner",
}


def _resolved(bundle: Bundle, label: str) -> ResolvedEdge:
    return ResolvedEdge(
        bundle=bundle,
        edge_type=label,
        confidence=0.95,
        rationale="ground truth from the rulebook",
        method=METHOD_LLM,
        orientation=Orientation(
            head=bundle.source.name,
            tail=bundle.target.name,
            decided_by=ORIENTATION_IMPLIED_BY_LABEL,
        ),
        model="fixture/answer-key",
    )


def stamped_edges(book: Rulebook):
    """Stations 2 and 3 over one rulebook, with Station 3's labels from the key."""
    classified = classify(book.bundles())
    key = book.answer_key()
    edges = list(classified.stamped)
    for item in classified.deferred:
        bundle = item.bundle
        label = key.get((bundle.source.name, bundle.target.name))
        assert label is not None, (
            f"{book.skill}: no answer for {bundle.source.name} -> {bundle.target.name}"
        )
        edges.append(_resolved(bundle, label))
    return edges, classified


def plangraph_for(book: Rulebook, db=None, config=None):
    """Stations 2-5 for one rulebook, written into `db`."""
    config = config or load_writer_config(ENV, dry_run=False)
    db = db if db is not None else make_db()
    edges, _ = stamped_edges(book)
    result = normalize(edges)
    plan = build(result.finalized, result.derived, config.schema, skill_scope=book.skill)
    write_plangraph(plan, db, config)
    return db, plan, result


@lru_cache(maxsize=1)
def _corpus():
    config = load_writer_config(ENV, dry_run=False)
    db = make_db()
    books = all_rulebooks()
    for book in books:
        plangraph_for(book, db=db, config=config)
    return db, config, tuple(books)


def corpus_db():
    """One database holding every rulebook's PlanGraph, each in its own scope."""
    db, config, books = _corpus()
    return db, config, list(books)


def book_named(skill: str) -> Rulebook:
    for book in all_rulebooks():
        if book.skill == skill:
            return book
    raise KeyError(skill)
