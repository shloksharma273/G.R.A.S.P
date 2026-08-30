"""Helpers for exercising Station 2 offline.

`chai_bundles()` runs the real Station 1 read against the in-memory chai KG, so
Station 2 is tested on genuine Station 1 output rather than on hand-typed dicts.
`bundle()` builds one-off bundles for the reason-code cases the chai set does not
contain.
"""

from __future__ import annotations

from kg_read_harness.bundle import Bundle, Entity
from kg_read_harness.config import load_config
from kg_read_harness.read import read_bundles

from . import chai_fixture as chai
from .fake_arango import make_db


def bundle(
    source_name: str,
    source_type: str,
    target_name: str,
    target_type: str,
    description: str = "a description",
    relation_key: str = "r001",
) -> Bundle:
    return Bundle(
        relation_key=relation_key,
        source=Entity(source_name, source_type),
        target=Entity(target_name, target_type),
        description=description,
    )


def chai_bundles(**env_overrides: str) -> list[Bundle]:
    """The 39 bundles Station 1 reads from the offline chai KG."""
    config = load_config(chai.env(**env_overrides))
    db = make_db(chai.entities(), chai.relations())
    return read_bundles(db, config)


#: The Section 13 answer key for the chai set: every type pair and where it goes.
CHAI_ANSWER_KEY = {
    ("SKILL", "PRIMITIVE"): ("stamped", "decomposes_to"),
    ("PRIMITIVE", "STATE"): ("deferred", None),
    ("PRIMITIVE", "OBJECT"): ("stamped", "uses"),
}

#: Expected bucket sizes for the chai set (11 decomposes_to, 20 defer, 8 uses).
CHAI_EXPECTED = {"stamped": 19, "deferred": 20, "parked": 0}
