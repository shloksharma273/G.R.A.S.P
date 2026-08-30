"""A masala-chai KG shaped the way AutoGraph produces one.

Mirrors masala_chai_rulebook.md so the acceptance criteria can be checked: the
summary must show non-zero SKILL->PRIMITIVE and PRIMITIVE->STATE counts.
"""

from __future__ import annotations

ENTITY_COLLECTION = "masala_chai_Entities"
RELATION_COLLECTION = "masala_chai_Relations"

PRIMITIVES = [
    "place_pan",
    "turn_on_stove",
    "add_water",
    "boil_water",
    "add_tea_leaves",
    "add_milk",
    "add_sugar",
    "simmer",
    "strain",
    "turn_off_stove",
    "serve",
]
OBJECTS = ["stove", "pan", "water", "tea_leaves", "milk", "sugar", "strainer", "cup"]
STATES = [
    "stove_on",
    "stove_off",
    "pan_on_stove",
    "water_in_pan",
    "water_boiling",
    "tea_brewing",
    "milk_added",
    "sugar_added",
    "tea_brewed",
    "tea_in_cup",
]

# (primitive, state, description) — the effects the rulebook states.
EFFECTS = [
    ("place_pan", "pan_on_stove", "After this action, the pan is on the stove."),
    ("turn_on_stove", "stove_on", "After this action, the stove is on."),
    ("add_water", "water_in_pan", "After this action, there is water in the pan."),
    ("boil_water", "water_boiling", "After this action, the water is boiling."),
    ("add_tea_leaves", "tea_brewing", "After this action, the tea is brewing."),
    ("add_milk", "milk_added", "After this action, milk has been added."),
    ("add_sugar", "sugar_added", "After this action, sugar has been added."),
    ("simmer", "tea_brewed", "After simmering, the tea is brewed."),
    ("strain", "tea_in_cup", "After this action, the tea is in the cup."),
    ("turn_off_stove", "stove_off", "After this action, the stove is off."),
]

# (primitive, state, description) — the preconditions the rulebook states.
PRECONDITIONS = [
    ("add_water", "pan_on_stove", "This action requires that the pan is already on the stove."),
    ("boil_water", "water_in_pan", "This action requires that there is water in the pan."),
    ("boil_water", "stove_on", "This action requires that the stove is on."),
    ("add_tea_leaves", "water_boiling", "The water must be boiling before the tea leaves are added."),
    ("add_milk", "tea_brewing", "The tea must already be brewing before milk is added."),
    ("add_sugar", "tea_brewing", "The tea must already be brewing before sugar is added."),
    ("simmer", "milk_added", "This action requires that milk has been added."),
    ("simmer", "sugar_added", "This action requires that sugar has been added."),
    ("strain", "tea_brewed", "The tea must be brewed before it can be strained."),
    ("serve", "tea_in_cup", "This action requires that the tea is in the cup."),
]

# (primitive, object, description) — the tools each action acts on.
USES = [
    ("place_pan", "pan", "The robot places the pan on the stove."),
    ("turn_on_stove", "stove", "The robot turns on the stove."),
    ("add_water", "water", "The robot pours water into the pan."),
    ("add_tea_leaves", "tea_leaves", "The robot adds tea leaves to the pan."),
    ("add_milk", "milk", "The robot pours milk into the pan."),
    ("add_sugar", "sugar", "The robot adds sugar to the pan."),
    ("strain", "strainer", "The robot pours the tea through the strainer into the cup."),
    ("serve", "cup", "The robot serves the cup of chai."),
]


def entities(type_field: str = "entity_type", name_field: str = "name") -> list[dict]:
    docs = [{"_key": "make_masala_chai", name_field: "make_masala_chai", type_field: "SKILL"}]
    for group, entity_type in ((PRIMITIVES, "PRIMITIVE"), (OBJECTS, "OBJECT"), (STATES, "STATE")):
        for name in group:
            docs.append({"_key": name, name_field: name, type_field: entity_type})
    for doc in docs:
        doc["_id"] = f"{ENTITY_COLLECTION}/{doc['_key']}"
    return docs


def relations(
    type_field: str = "type",
    description_field: str = "description",
    relation_type: str = "RELATED_TO",
    include_dangling: bool = True,
    include_other_type: bool = True,
) -> list[dict]:
    docs: list[dict] = []

    def edge(source: str, target: str, description: str, rtype: str = relation_type) -> None:
        key = f"r{len(docs) + 1:03d}"
        docs.append(
            {
                "_key": key,
                "_id": f"{RELATION_COLLECTION}/{key}",
                "_from": f"{ENTITY_COLLECTION}/{source}",
                "_to": f"{ENTITY_COLLECTION}/{target}",
                type_field: rtype,
                description_field: description,
            }
        )

    for primitive in PRIMITIVES:
        edge(
            "make_masala_chai",
            primitive,
            f"The skill make_masala_chai is composed of the primitive action {primitive}.",
        )
    for primitive, state, description in EFFECTS:
        edge(primitive, state, description)
    for primitive, state, description in PRECONDITIONS:
        edge(primitive, state, description)
    for primitive, obj, description in USES:
        edge(primitive, obj, description)

    if include_other_type:
        edge("make_masala_chai", "cup", "Mentioned together in the overview.", rtype="MENTIONS")
    if include_dangling:
        key = f"r{len(docs) + 1:03d}"
        docs.append(
            {
                "_key": key,
                "_id": f"{RELATION_COLLECTION}/{key}",
                "_from": f"{ENTITY_COLLECTION}/simmer",
                "_to": f"{ENTITY_COLLECTION}/deleted_entity",
                type_field: relation_type,
                description_field: "Points at an entity that no longer exists.",
            }
        )
    return docs


def env(**overrides: str) -> dict[str, str]:
    """A valid environment for the fixture, plus any overrides."""
    base = {
        "ARANGO_URL": "http://localhost:8529",
        "ARANGO_DB": "chai_db",
        "ARANGO_USERNAME": "root",
        "ARANGO_PASSWORD": "",
        "PROJECT_NAME": "masala_chai",
    }
    base.update(overrides)
    return base
