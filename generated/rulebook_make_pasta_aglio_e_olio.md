# Pasta Aglio e Olio — Robot Skill Rulebook

## Overview

This rulebook describes making pasta aglio e olio: boiling heavily salted water
for the pasta while peeling and thinly slicing garlic and finely chopping
parsley, then toasting the garlic in olive oil with red pepper flake, tossing
in the cooked pasta with reserved pasta water, parsley and lemon juice,
seasoning, and plating it in a nest.

## Skill

**make_pasta_aglio_e_olio** is a high-level skill. It is composed of the
following primitive actions: place pot on stove, fill pot with water, salt
water heavily, turn on stove, bring water to boil, shake garlic in tupperware,
peel garlic by hand, slice garlic thinly, pick parsley leaves, chop parsley
fine, add pasta to boiling water, cook pasta, add olive oil to pan, heat oil
until shimmering, add garlic to oil, fry garlic until browning, add red pepper
flake, remove pan from heat, add pasta to pan, add pasta water, mix in parsley,
squeeze lemon juice, season to taste, stir pasta, and plate pasta nest.

## Objects

The objects involved in this skill are: pot, water, salt, stove, garlic cloves,
tupperware, knife, cutting board, parsley, pasta, olive oil, saute pan, red
pepper flake, pasta water, lemon, pepper, carving fork, and bowl.

## States

The relevant states of the world are: pot on stove, water in pot, water salted,
stove on, water boiling, garlic peeled, garlic sliced, parsley leaves picked,
parsley chopped, pasta in boiling water, pasta cooked, pasta water reserved,
oil in pan, oil shimmering, garlic in oil, garlic browning, red pepper flake
added, pan off heat, garlic golden brown, pasta in pan, pasta water added to
pan, parsley mixed in, lemon juice added, dish seasoned, dish stirred, and
pasta plated.

## Primitive actions and their rules

**place_pot_on_stove** — The robot places a large pot on the stove. After this
action, pot on stove is true.

**fill_pot_with_water** — The robot fills the pot with water. This action
requires that pot on stove is already true as a precondition. After this
action, water in pot is true.

**salt_water_heavily** — The robot adds a heavy amount of salt to the water.
This action requires that water in pot is already true as a precondition. After
this action, water salted is true.

**turn_on_stove** — The robot turns on the stove burner under the pot. This
action requires that pot on stove is already true as a precondition. After this
action, stove on is true.

**bring_water_to_boil** — The robot heats the salted water until it comes to a
rolling boil. This action requires that water in pot is already true as a
precondition. This action requires that water salted is already true as a
precondition. This action requires that stove on is already true as a
precondition. After this action, water boiling is true.

**shake_garlic_in_tupperware** — The robot puts the garlic cloves in a
tupperware and shakes it hard to loosen the skins.

**peel_garlic_by_hand** — The robot peels each garlic clove by hand with tools.
After this action, garlic peeled is true.

**slice_garlic_thinly** — The robot slices all the peeled garlic cloves very
thinly and sets them aside. This action requires that garlic peeled is already
true as a precondition. After this action, garlic sliced is true.

**pick_parsley_leaves** — The robot picks the parsley leaves off their stems,
discarding the big thick stems. After this action, parsley leaves picked is
true.

**chop_parsley_fine** — The robot chops the picked parsley very finely. This
action requires that parsley leaves picked is already true as a precondition.
After this action, parsley chopped is true.

**add_pasta_to_boiling_water** — The robot adds the pasta to the boiling salted
water. This action requires that water boiling is already true as a
precondition. After this action, pasta in boiling water is true.

**cook_pasta** — The robot boils the pasta until it is cooked. This action
requires that pasta in boiling water is already true as a precondition. This
action requires that stove on is already true as a precondition. After this
action, pasta cooked is true. After this action, pasta water reserved is true.

**add_olive_oil_to_pan** — The robot pours about half a cup of olive oil into a
large saute pan on the stove. After this action, oil in pan is true.

**heat_oil_until_shimmering** — The robot heats the olive oil until it
shimmers. This action requires that oil in pan is already true as a
precondition. After this action, oil shimmering is true.

**add_garlic_to_oil** — The robot adds the thinly sliced garlic to the
shimmering oil. This action requires that oil shimmering is already true as a
precondition. This action requires that garlic sliced is already true as a
precondition. After this action, garlic in oil is true.

**fry_garlic_until_browning** — The robot fries the garlic in the oil until it
just starts to turn brown. This action requires that garlic in oil is already
true as a precondition. After this action, garlic browning is true.

**add_red_pepper_flake** — The robot adds red pepper flake to the pan of garlic
and oil. This action requires that garlic browning is already true as a
precondition. After this action, red pepper flake added is true.

**remove_pan_from_heat** — The robot lifts the saute pan off the burner so the
garlic finishes cooking in residual heat. This action requires that red pepper
flake added is already true as a precondition. After this action, pan off heat
is true. After this action, garlic golden brown is true.

**add_pasta_to_pan** — The robot transfers the cooked pasta into the pan of
garlic oil. This action requires that pasta cooked is already true as a
precondition. This action requires that garlic golden brown is already true as
a precondition. This action requires that pan off heat is already true as a
precondition. After this action, pasta in pan is true.

**add_pasta_water** — The robot adds some reserved pasta water back into the
pan to create a cohesive sauce. This action requires that pasta in pan is
already true as a precondition. This action requires that pasta water reserved
is already true as a precondition. After this action, pasta water added to pan
is true.

**mix_in_parsley** — The robot mixes the finely chopped parsley into the pasta.
This action requires that pasta water added to pan is already true as a
precondition. This action requires that parsley chopped is already true as a
precondition. After this action, parsley mixed in is true.

**squeeze_lemon_juice** — The robot squeezes a big squeeze of lemon juice over
the pasta. This action requires that parsley mixed in is already true as a
precondition. After this action, lemon juice added is true.

**season_to_taste** — The robot checks the seasoning and adds any salt and
pepper the dish needs. This action requires that lemon juice added is already
true as a precondition. After this action, dish seasoned is true.

**stir_pasta** — The robot gives the pasta a good final stir. This action
requires that dish seasoned is already true as a precondition. After this
action, dish stirred is true.

**plate_pasta_nest** — The robot twirls the pasta with a big carving fork and
sets it as a nest in the center of a bowl. This action requires that dish
stirred is already true as a precondition. After this action, pasta plated is
true.

## Ordering rules

The pot must be on the stove before water is added, and the water must be
salted and the stove turned on before it can come to a boil. The water must be
boiling before the pasta is added. The garlic must be peeled before it can be
sliced thinly. The parsley leaves must be picked off their stems before they
can be chopped fine. Garlic peeling, slicing, and parsley preparation happen
while the water comes to a boil. The olive oil must be in the pan and
shimmering before the sliced garlic is added. The garlic must be just starting
to brown before the red pepper flake is added, and the pan is removed from the
heat immediately after. The pasta must be cooked and the garlic toasted golden
brown before the pasta is added to the pan. The pasta must be in the pan before
reserved pasta water is added. Pasta water must be added before the chopped
parsley is mixed in. Parsley is mixed in before the lemon juice, and the lemon
juice before the final seasoning check. The dish must be seasoned and stirred
before it is plated as a nest in the bowl.

## Source

Generated from https://www.youtube.com/watch?v=bJUiWdM__Qw
