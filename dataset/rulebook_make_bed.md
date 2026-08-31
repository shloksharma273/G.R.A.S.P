# Make a Bed — Robot Skill Rulebook

## Overview

This rulebook describes the skill of making a bed as a sequence of primitive
robot actions. Each primitive action has preconditions (what must be true before
it can run) and effects (what becomes true after it runs). The robot composes
these primitives into an ordered plan.

## Skill

**make_bed** is a high-level skill. It is composed of the following primitive
actions: clear the bed, spread the fitted sheet, spread the flat sheet, tuck the
sheet, spread the duvet, place the pillows, and straighten.

## Objects

The objects involved in this skill are: the bed, the mattress, the fitted sheet,
the flat sheet, the duvet, and the pillows.

## States

The relevant states of the world are: bed clear, fitted sheet on, flat sheet on,
sheet tucked, duvet on, pillows placed, and bed made.

## Primitive actions and their rules

**clear_bed** — The robot removes any items from the bed. After this action, the
bed is clear.

**spread_fitted_sheet** — The robot spreads the fitted sheet over the mattress.
This action requires that the bed is clear. After this action, the fitted sheet
is on.

**spread_flat_sheet** — The robot spreads the flat sheet over the fitted sheet.
This action requires that the fitted sheet is on. After this action, the flat
sheet is on.

**tuck_sheet** — The robot tucks the flat sheet under the mattress edges. This
action requires that the flat sheet is on. After this action, the sheet is
tucked.

**spread_duvet** — The robot spreads the duvet over the bed. This action requires
that the sheet is tucked. After this action, the duvet is on.

**place_pillows** — The robot places the pillows at the head of the bed. This
action requires that the duvet is on. After this action, the pillows are placed.

**straighten** — The robot straightens the bedding for a neat finish. This action
requires that the pillows are placed. After this action, the bed is made. This is
the goal of the skill.

## Ordering rules

The bed must be clear before the fitted sheet is spread. The fitted sheet must be
on before the flat sheet is spread, and the flat sheet must be on before it is
tucked. The sheet must be tucked before the duvet is spread. The duvet must be on
before the pillows are placed, and the pillows must be placed before the bedding
is straightened.
