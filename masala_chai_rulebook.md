# Masala Chai — Robot Skill Rulebook

## Overview

This rulebook describes the skill of making masala chai as a sequence of
primitive robot actions. Each primitive action has preconditions (what must be
true before it can run) and effects (what becomes true after it runs). The
robot composes these primitives into an ordered plan.

## Skill

**make_masala_chai** is a high-level skill. It is composed of the following
primitive actions: place pan, turn on stove, add water, boil water, add tea
leaves, add milk, add sugar, simmer, strain, turn off stove, and serve.

## Objects

The objects involved in this skill are: the stove, the pan, water, tea leaves,
milk, sugar, the strainer, and the cup.

## States

The relevant states of the world are: stove on, stove off, pan on stove, water
in pan, water boiling, tea brewing, milk added, sugar added, tea brewed, and
tea in cup.

## Primitive actions and their rules

**place_pan** — The robot places the pan on the stove. After this action, the
pan is on the stove.

**turn_on_stove** — The robot turns on the stove. After this action, the stove
is on.

**add_water** — The robot pours water into the pan. This action requires that
the pan is already on the stove. After this action, there is water in the pan.

**boil_water** — The robot heats the water until it boils. This action requires
that there is water in the pan and that the stove is on. After this action, the
water is boiling.

**add_tea_leaves** — The robot adds tea leaves to the pan. The water must be
boiling before the tea leaves are added. After this action, the tea is brewing.

**add_milk** — The robot pours milk into the pan. The tea must already be
brewing before milk is added. After this action, milk has been added.

**add_sugar** — The robot adds sugar to the pan. The tea must already be
brewing before sugar is added. After this action, sugar has been added.

**simmer** — The robot lets the mixture simmer. This action requires that milk
has been added and that sugar has been added. After simmering, the tea is
brewed.

**strain** — The robot pours the tea through the strainer into the cup. The tea
must be brewed before it can be strained. After this action, the tea is in the
cup.

**turn_off_stove** — The robot turns off the stove. The tea must be brewed
before the stove is turned off. After this action, the stove is off.

**serve** — The robot serves the cup of chai. This action requires that the tea
is in the cup. After this action, the chai is served. This is the goal of the
skill.

## Ordering rules

The pan must be placed on the stove before water is added. Water must be added
before it can be boiled. The water must boil before tea leaves are added. The
tea must be brewing before milk and sugar are added. Milk and sugar must both be
added before the mixture is simmered. The tea must be brewed by simmering before
it is strained into the cup. The chai is served last.
