# Cook a Burger — Robot Skill Rulebook

## Overview

This rulebook describes the skill of cooking a cheeseburger as a sequence of
primitive robot actions. Each primitive action has preconditions (what must be
true before it can run) and effects (what becomes true after it runs). The robot
composes these primitives into an ordered plan.

## Skill

**cook_burger** is a high-level skill. It is composed of the following primitive
actions: turn on stove, preheat pan, season patty, place patty, cook patty, flip
patty, add cheese, toast buns, assemble burger, and serve.

## Objects

The objects involved in this skill are: the stove, the pan, the patty, salt, the
cheese, the buns, and the plate.

## States

The relevant states of the world are: stove on, pan hot, patty seasoned, patty in
pan, patty cooked on one side, patty cooked, cheese melted, buns toasted, burger
assembled, and burger served.

## Primitive actions and their rules

**turn_on_stove** — The robot turns on the stove. After this action, the stove is
on.

**preheat_pan** — The robot places the pan on the stove and heats it. This action
requires that the stove is on. After this action, the pan is hot.

**season_patty** — The robot seasons the patty with salt. After this action, the
patty is seasoned.

**place_patty** — The robot places the patty in the pan. This action requires
that the pan is hot and that the patty is seasoned. After this action, the patty
is in the pan.

**cook_patty** — The robot cooks the patty on the first side. This action
requires that the patty is in the pan. After this action, the patty is cooked on
one side.

**flip_patty** — The robot flips the patty to cook the other side. This action
requires that the patty is cooked on one side. After this action, the patty is
cooked.

**add_cheese** — The robot places a slice of cheese on the patty to melt. This
action requires that the patty is cooked. After this action, the cheese is
melted.

**toast_buns** — The robot toasts the buns in the pan. This action requires that
the pan is hot. After this action, the buns are toasted.

**assemble_burger** — The robot assembles the burger on the plate. This action
requires that the cheese is melted and that the buns are toasted. After this
action, the burger is assembled.

**serve** — The robot serves the burger. This action requires that the burger is
assembled. After this action, the burger is served. This is the goal of the
skill.

## Ordering rules

The stove must be on before the pan is preheated. The pan must be hot and the
patty must be seasoned before the patty is placed in the pan. The patty must be
cooked on one side before it is flipped, and cooked on both sides before the
cheese is added. The buns are toasted while the pan is hot. Both the cheese must
be melted and the buns must be toasted before the burger is assembled, and the
burger must be assembled before it is served.
