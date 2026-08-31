# Fold a T-Shirt — Robot Skill Rulebook

## Overview

This rulebook describes the skill of folding a t-shirt as a sequence of primitive
robot actions. Each primitive action has preconditions (what must be true before
it can run) and effects (what becomes true after it runs). The robot composes
these primitives into an ordered plan.

## Skill

**fold_tshirt** is a high-level skill. It is composed of the following primitive
actions: lay flat, smooth wrinkles, fold left side, fold right side, fold bottom
up, and place on stack.

## Objects

The objects involved in this skill are: the t-shirt and the table.

## States

The relevant states of the world are: shirt flat, shirt smooth, left side folded,
right side folded, shirt folded, and shirt stacked.

## Primitive actions and their rules

**lay_flat** — The robot lays the t-shirt face down on the table. After this
action, the shirt is flat.

**smooth_wrinkles** — The robot smooths out any wrinkles with its gripper. This
action requires that the shirt is flat. After this action, the shirt is smooth.

**fold_left_side** — The robot folds the left third of the shirt toward the
center. This action requires that the shirt is smooth. After this action, the
left side is folded.

**fold_right_side** — The robot folds the right third of the shirt toward the
center. This action requires that the left side is folded. After this action, the
right side is folded.

**fold_bottom_up** — The robot folds the bottom half of the shirt up to the top.
This action requires that the right side is folded. After this action, the shirt
is folded.

**place_on_stack** — The robot places the folded shirt on the stack. This action
requires that the shirt is folded. After this action, the shirt is stacked. This
is the goal of the skill.

## Ordering rules

The shirt must be laid flat before its wrinkles are smoothed. The shirt must be
smooth before the left side is folded. The left side must be folded before the
right side, and the right side must be folded before the bottom is folded up. The
shirt must be folded before it is placed on the stack.
