# Pour-Over Coffee — Robot Skill Rulebook

## Overview

This rulebook describes the skill of making pour-over coffee as a sequence of
primitive robot actions. Each primitive action has preconditions (what must be
true before it can run) and effects (what becomes true after it runs). The robot
composes these primitives into an ordered plan.

## Skill

**make_pour_over_coffee** is a high-level skill. It is composed of the following
primitive actions: boil water, place filter, add coffee grounds, place carafe,
bloom the grounds, pour water, pour coffee, and serve.

## Objects

The objects involved in this skill are: the kettle, water, the filter, the
dripper, coffee grounds, the carafe, and the mug.

## States

The relevant states of the world are: water boiling, filter in dripper, grounds
in filter, carafe ready, grounds bloomed, coffee brewed, and coffee in mug.

## Primitive actions and their rules

**boil_water** — The robot heats water in the kettle until it boils. After this
action, the water is boiling.

**place_filter** — The robot places a paper filter into the dripper. After this
action, the filter is in the dripper.

**add_coffee_grounds** — The robot adds coffee grounds into the filter. This
action requires that the filter is already in the dripper. After this action,
there are grounds in the filter.

**place_carafe** — The robot places the carafe under the dripper. After this
action, the carafe is ready.

**bloom_grounds** — The robot pours a small amount of hot water over the grounds
to wet them. This action requires that the water is boiling and that there are
grounds in the filter. After this action, the grounds are bloomed.

**pour_water** — The robot slowly pours the remaining hot water over the grounds.
This action requires that the grounds are bloomed and that the carafe is ready.
After this action, the coffee is brewed.

**pour_coffee** — The robot pours the brewed coffee from the carafe into the mug.
This action requires that the coffee is brewed. After this action, the coffee is
in the mug.

**serve** — The robot serves the mug of coffee. This action requires that the
coffee is in the mug. After this action, the coffee is served. This is the goal
of the skill.

## Ordering rules

The filter must be placed in the dripper before coffee grounds are added. The
water must be boiling and the grounds must be in the filter before the grounds
can be bloomed. The grounds must be bloomed and the carafe must be ready before
the remaining water is poured. The coffee must be brewed before it is poured into
the mug, and the coffee must be in the mug before it is served.
