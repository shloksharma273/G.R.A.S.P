# Chicken and Broccoli Rice Noodle Stir Fry — Robot Skill Rulebook

## Overview

This rulebook describes the skill of cooking a quick Asian-style stir fry with
rehydrated rice noodles, thinly sliced chicken breast, crispy garlic,
tenderstem broccoli and egg. The noodles are soaked while the chicken, garlic
and broccoli are prepared and seared in a hot wok, then everything is combined
with whisked egg and finished with lime. It is presented as a sequence of
primitive robot actions with their preconditions and effects.

## Skill

**make_chicken_rice_noodle_stir_fry** is a high-level skill. It is composed of
the following primitive actions: place noodles in bowl, pour hot water over
noodles, soak noodles, place wok on stove, turn on stove, heat wok, trim
chicken fillet, butterfly chicken, flatten chicken with rolling pin, slice
chicken into thin strips, slice garlic finely, slice broccoli, organize
ingredients, add olive oil to wok, add chicken to wok, season chicken, sear
chicken, add garlic to wok, crisp garlic, add broccoli to wok, add soy sauce,
remove chicken broccoli, wipe out wok, re oil wok, drain noodles, crack eggs
into wok, whisk eggs in wok, season eggs, add noodles to wok, return chicken
broccoli, mix stir fry, and finish with lime.

## Objects

The objects involved in this skill are: bowl, hot water, rice noodles, wok,
stove, olive oil, chicken breast, knife, chopping board, rolling pin, garlic,
broccoli, salt, pepper, soy sauce, eggs, whisk, plate, and lime.

## States

The relevant states of the world are: noodles in bowl, hot water over noodles,
noodles softened, noodles drained, wok on stove, stove on, wok hot, oil in wok,
chicken fillet removed, chicken butterflied, chicken flattened, chicken sliced
thin, garlic sliced thin, broccoli sliced, ingredients organized, chicken in
wok, chicken seasoned, chicken seared, garlic in wok, garlic crispy, broccoli
in wok, soy sauce added, stir fry seasoned, chicken broccoli removed from wok,
wok wiped, wok re oiled, eggs in wok, eggs whisked in wok, eggs seasoned, egg
cooked, noodles in wok, chicken broccoli returned to wok, dish mixed, lime
squeezed over, and dish finished.

## Primitive actions and their rules

**place_noodles_in_bowl** — The robot puts the dry rice noodles into a bowl.
After this action, noodles in bowl is true.

**pour_hot_water_over_noodles** — The robot pours hot water over the rice
noodles in the bowl. This action requires that noodles in bowl is already true
as a precondition. After this action, hot water over noodles is true.

**soak_noodles** — The robot leaves the noodles to soak and rehydrate for
twelve to fifteen minutes until softened. This action requires that hot water
over noodles is already true as a precondition. After this action, noodles
softened is true.

**place_wok_on_stove** — The robot places the wok, or a wide frying pan with
sloping sides, on the stove. After this action, wok on stove is true.

**turn_on_stove** — The robot turns the stove on. This action requires that wok
on stove is already true as a precondition. After this action, stove on is
true.

**heat_wok** — The robot heats the empty wok until it is really hot. This
action requires that wok on stove is already true as a precondition. This
action requires that stove on is already true as a precondition. After this
action, wok hot is true.

**trim_chicken_fillet** — The robot slices the small loose fillet off the
chicken breast on the board. After this action, chicken fillet removed is true.

**butterfly_chicken** — The robot holds the knife flat on the board and slices
the chicken breast almost in half to butterfly it open. This action requires
that chicken fillet removed is already true as a precondition. After this
action, chicken butterflied is true.

**flatten_chicken_with_rolling_pin** — The robot gently rolls a rolling pin
over the butterflied chicken to flatten it. This action requires that chicken
butterflied is already true as a precondition. After this action, chicken
flattened is true.

**slice_chicken_into_thin_strips** — The robot cuts the flattened chicken in
half and slices it into very thin strips. This action requires that chicken
flattened is already true as a precondition. After this action, chicken sliced
thin is true.

**slice_garlic_finely** — The robot washes the knife and finely slices the
garlic as thinly as possible. This action requires that chicken sliced thin is
already true as a precondition. After this action, garlic sliced thin is true.

**slice_broccoli** — The robot slices the young tenderstem broccoli down into
pieces. After this action, broccoli sliced is true.

**organize_ingredients** — The robot arranges all the prepared ingredients
within reach of the stove because the dish cooks in minutes. This action
requires that chicken sliced thin is already true as a precondition. This
action requires that garlic sliced thin is already true as a precondition. This
action requires that broccoli sliced is already true as a precondition. After
this action, ingredients organized is true.

**add_olive_oil_to_wok** — The robot adds a touch of olive oil to the hot wok
until it just starts to smoke. This action requires that wok hot is already
true as a precondition. After this action, oil in wok is true.

**add_chicken_to_wok** — The robot drops the thin chicken strips into the hot
oiled wok and opens out the strands. This action requires that oil in wok is
already true as a precondition. This action requires that chicken sliced thin
is already true as a precondition. This action requires that ingredients
organized is already true as a precondition. After this action, chicken in wok
is true.

**season_chicken** — The robot seasons the chicken in the wok with salt and
pepper. This action requires that chicken in wok is already true as a
precondition. After this action, chicken seasoned is true.

**sear_chicken** — The robot sears the chicken strips until they colour. This
action requires that chicken in wok is already true as a precondition. This
action requires that chicken seasoned is already true as a precondition. This
action requires that stove on is already true as a precondition. After this
action, chicken seared is true.

**add_garlic_to_wok** — The robot adds the sliced garlic to the wok with the
seared chicken and pushes chicken and garlic up the sides of the pan. This
action requires that chicken seared is already true as a precondition. This
action requires that garlic sliced thin is already true as a precondition.
After this action, garlic in wok is true.

**crisp_garlic** — The robot cooks the garlic until it is really crispy. This
action requires that garlic in wok is already true as a precondition. This
action requires that stove on is already true as a precondition. After this
action, garlic crispy is true.

**add_broccoli_to_wok** — The robot adds the raw sliced broccoli to the wok so
it keeps its crunch. This action requires that garlic crispy is already true as
a precondition. This action requires that broccoli sliced is already true as a
precondition. After this action, broccoli in wok is true.

**add_soy_sauce** — The robot pours soy sauce into the wok to season and colour
the stir fry. This action requires that broccoli in wok is already true as a
precondition. After this action, soy sauce added is true. After this action,
stir fry seasoned is true.

**remove_chicken_broccoli** — The robot takes the chicken, garlic and broccoli
out of the wok and sets them aside on a plate. This action requires that stir
fry seasoned is already true as a precondition. After this action, chicken
broccoli removed from wok is true.

**wipe_out_wok** — The robot wipes out the empty wok. This action requires that
chicken broccoli removed from wok is already true as a precondition. After this
action, wok wiped is true.

**re_oil_wok** — The robot adds a teaspoon of olive oil to the wiped wok to oil
it again. This action requires that wok wiped is already true as a
precondition. After this action, wok re oiled is true.

**drain_noodles** — The robot drains the softened rice noodles. This action
requires that noodles softened is already true as a precondition. After this
action, noodles drained is true.

**crack_eggs_into_wok** — The robot cracks two eggs into the oiled wok. This
action requires that wok re oiled is already true as a precondition. This
action requires that stove on is already true as a precondition. After this
action, eggs in wok is true.

**whisk_eggs_in_wok** — The robot whisks the eggs in the wok and spreads them
up the side of the pan. This action requires that eggs in wok is already true
as a precondition. After this action, eggs whisked in wok is true.

**season_eggs** — The robot lightly seasons the eggs with salt and pepper. This
action requires that eggs whisked in wok is already true as a precondition.
After this action, eggs seasoned is true.

**add_noodles_to_wok** — The robot adds the drained noodles to the wok with the
whisked egg. This action requires that eggs seasoned is already true as a
precondition. This action requires that noodles drained is already true as a
precondition. After this action, noodles in wok is true.

**return_chicken_broccoli** — The robot returns the chicken, garlic and
broccoli to the wok. This action requires that noodles in wok is already true
as a precondition. This action requires that chicken broccoli removed from wok
is already true as a precondition. After this action, chicken broccoli returned
to wok is true.

**mix_stir_fry** — The robot mixes everything together so the egg binds the
dish and the chicken, broccoli and garlic are evenly distributed. This action
requires that chicken broccoli returned to wok is already true as a
precondition. After this action, dish mixed is true. After this action, egg
cooked is true.

**finish_with_lime** — The robot squeezes fresh lime over the finished stir
fry. This action requires that dish mixed is already true as a precondition.
This action requires that egg cooked is already true as a precondition. After
this action, lime squeezed over is true. After this action, dish finished is
true.

## Ordering rules

The noodles must be in the bowl before hot water is poured over them, and the
water must be poured before they can soak and soften. The noodles soak for
twelve to fifteen minutes while the chicken, garlic and broccoli are prepared.
The wok must be on the stove and the stove turned on before the wok can be
heated. The wok must be hot before the olive oil is added, and the oil must be
in the wok before the chicken goes in. The loose fillet is trimmed off, then
the breast is butterflied, then flattened with the rolling pin, and only then
sliced into thin strips. The chicken must be sliced before the knife is washed
and the garlic is sliced. All ingredients must be prepared and organized within
reach before cooking begins, because the dish cooks in minutes. The chicken
goes into the wok first and is seasoned and seared before the garlic is added.
The garlic must be crispy before the raw broccoli is added. The broccoli must
be in the wok before the soy sauce is added. The chicken, garlic and broccoli
must be removed from the wok before the wok is wiped out and re-oiled. The wok
must be re-oiled before the eggs are cracked in and whisked. The noodles must
be drained before they are added to the wok. The eggs must be whisked and
seasoned in the wok before the noodles are added. The noodles must be in the
wok before the chicken, garlic and broccoli are returned. Everything must be
mixed together and the egg cooked before the dish is finished with fresh lime.

## Source

Generated from https://www.youtube.com/watch?v=mhDJNfV7hjk
