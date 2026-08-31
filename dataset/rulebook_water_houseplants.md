# Water the Houseplants — Robot Skill Rulebook

## Overview

This rulebook describes the skill of watering the houseplants as a sequence of
primitive robot actions. Each primitive action has preconditions (what must be
true before it can run) and effects (what becomes true after it runs). The robot
composes these primitives into an ordered plan.

## Skill

**water_houseplants** is a high-level skill. It is composed of the following
primitive actions: pick up can, fill can, check soil, water plant, let drain,
empty saucer, and return can.

## Objects

The objects involved in this skill are: the watering can, the tap, water, the
plant, the soil, and the saucer.

## States

The relevant states of the world are: can in hand, can filled, soil dry, plant
watered, water drained, saucer empty, and plants done.

## Primitive actions and their rules

**pick_up_can** — The robot picks up the watering can. After this action, the can
is in hand.

**fill_can** — The robot fills the can with water at the tap. This action requires
that the can is in hand. After this action, the can is filled.

**check_soil** — The robot checks the soil of the plant and finds it dry. After
this action, the soil is dry.

**water_plant** — The robot pours water onto the plant. This action requires that
the can is filled and that the soil is dry. After this action, the plant is
watered.

**let_drain** — The robot waits for excess water to drain into the saucer. This
action requires that the plant is watered. After this action, the water is
drained.

**empty_saucer** — The robot empties the excess water from the saucer. This action
requires that the water is drained. After this action, the saucer is empty.

**return_can** — The robot returns the watering can to its place. This action
requires that the saucer is empty. After this action, the plants are done. This
is the goal of the skill.

## Ordering rules

The can must be in hand before it is filled. The can must be filled and the soil
must be dry before the plant is watered. The plant must be watered before the
water can drain, and the water must be drained before the saucer is emptied. The
saucer must be empty before the can is returned.
