# Straight Line — Robot Skill Rulebook

## Overview

This rulebook describes the skill of laying out three blocks along a straight
line on the board. Each primitive action has preconditions and effects, and the
robot composes them into an ordered plan.

## Skill

**make_straight_line** is a high-level skill. It is composed of the following
primitive actions: place red block at first dot, place green block at second
dot, and place blue block at third dot.

## Objects

The objects involved in this skill are: red block, green block, blue block,
first dot, second dot, and third dot.

## States

The relevant states of the world are: red block on first dot, green block on
second dot, and blue block on third dot.

## Primitive actions and their rules

**place_red_block_at_first_dot** — The robot moves red block to first dot.
After this action, red block on first dot.

**place_green_block_at_second_dot** — The robot moves green block to second dot.
This action requires that red block on first dot. After this action, green block
on second dot.

**place_blue_block_at_third_dot** — The robot moves blue block to third dot.
This action requires that green block on second dot. After this action, blue
block on third dot.
