# Magic Sequence — Robot Skill Rulebook

## Overview

This rulebook describes the skill of laying out three blocks as a triangle on
the board. Each primitive action has preconditions (what must be true before it
can run) and effects (what becomes true after it runs). The robot composes these
primitives into an ordered plan.

The place names here are deliberately words, not single letters: the ingestion
parser strips "a" as an article, so "vertex a" would collapse to "vertex" and
match every sentence about any vertex. The plan bridge binds these names to the
board's real points.

## Skill

**make_magic_sequence** is a high-level skill. It is composed of the following
primitive actions: place red block at left vertex, place green block at right
vertex, and place blue block at top vertex.

## Objects

The objects involved in this skill are: red block, green block, blue block,
left vertex, right vertex, and top vertex.

## States

The relevant states of the world are: red block on left vertex, green block on
right vertex, and blue block on top vertex.

## Primitive actions and their rules

**place_red_block_at_left_vertex** — The robot moves red block to left vertex.
After this action, red block on left vertex.

**place_green_block_at_right_vertex** — The robot moves green block to right
vertex. This action requires that red block on left vertex. After this action,
green block on right vertex.

**place_blue_block_at_top_vertex** — The robot moves blue block to top vertex.
This action requires that green block on right vertex. After this action, blue
block on top vertex.
