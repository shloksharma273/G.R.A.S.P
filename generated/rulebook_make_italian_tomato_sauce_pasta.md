# Italian Tomato Sauce with Spaghetti — Robot Skill Rulebook

## Overview

This rulebook describes making a simple rustic Italian tomato sauce, starting
from a soffritto of onion and garlic slowly melted in a generous amount of
olive oil, then simmered with canned crushed tomatoes and tomato paste until
thick and jammy. In parallel, thin spaghetti is boiled in heavily salted water
until al dente, then tossed with the sauce and finished with parmesan and
basil.

## Skill

**make_italian_tomato_sauce_pasta** is a high-level skill. It is composed of
the following primitive actions: chop onions, peel garlic, chop garlic, place
pot on stove, add olive oil, heat oil, add onions garlic, lower heat, season
soffritto, cook soffritto, add canned tomatoes, add tomato paste, stir sauce,
bring sauce to boil, simmer sauce, taste season sauce, place pasta pot on
stove, fill pasta pot with water, salt pasta water, taste pasta water, boil
pasta water, add spaghetti, stir spaghetti until reboil, cook spaghetti al
dente, drain spaghetti, mix pasta with sauce, serve pasta, grate parmesan, and
add basil.

## Objects

The objects involved in this skill are: stove, pot, large pasta pot, knife,
onions, garlic cloves, olive oil, salt, pepper, canned crushed tomatoes, tomato
paste, water, spaghetti, colander, mixing bowl, serving bowl, parmesan, grater,
and basil leaves.

## States

The relevant states of the world are: onions chopped, garlic peeled, garlic
chopped, pot on stove, oil in pot, oil hot, aromatics in pot, heat low, onions
melted, soffritto seasoned, soffritto ready, tomatoes in pot, tomato paste in
pot, sauce mixed, sauce boiling, sauce simmering, sauce thick, sauce seasoned,
pasta pot on stove, water in pasta pot, water salted, water tasted salty, water
boiling, spaghetti in water, spaghetti stirred, spaghetti al dente, spaghetti
drained, pasta sauced, pasta in serving bowl, parmesan grated on pasta, and
basil added.

## Primitive actions and their rules

**chop_onions** — The robot finely chops two onions with the knife. After this
action, onions chopped is true.

**peel_garlic** — The robot smacks four garlic cloves with the flat of the
knife so the skins pop off. After this action, garlic peeled is true.

**chop_garlic** — The robot chops the peeled garlic cloves. This action
requires that garlic peeled is already true as a precondition. After this
action, garlic chopped is true.

**place_pot_on_stove** — The robot places the sauce pot on the stove. After
this action, pot on stove is true.

**add_olive_oil** — The robot pours about 100 millilitres of olive oil into the
pot. This action requires that pot on stove is already true as a precondition.
After this action, oil in pot is true.

**heat_oil** — The robot heats the oil in the pot until it is nice and hot.
This action requires that oil in pot is already true as a precondition. This
action requires that pot on stove is already true as a precondition. After this
action, oil hot is true.

**add_onions_garlic** — The robot adds the chopped onions and chopped garlic to
the hot oil. This action requires that oil hot is already true as a
precondition. This action requires that onions chopped is already true as a
precondition. This action requires that garlic chopped is already true as a
precondition. After this action, aromatics in pot is true.

**lower_heat** — The robot turns the stove right down to a low heat. This
action requires that aromatics in pot is already true as a precondition. After
this action, heat low is true.

**season_soffritto** — The robot adds a little salt and pepper to the onions
and garlic. This action requires that aromatics in pot is already true as a
precondition. After this action, soffritto seasoned is true.

**cook_soffritto** — The robot cooks the onions and garlic gently for 10 to 15
minutes until the onions are melted with no crunch. This action requires that
aromatics in pot is already true as a precondition. This action requires that
heat low is already true as a precondition. This action requires that soffritto
seasoned is already true as a precondition. After this action, onions melted is
true. After this action, soffritto ready is true.

**add_canned_tomatoes** — The robot adds two cans of crushed Italian tomatoes
to the finished soffritto. This action requires that soffritto ready is already
true as a precondition. After this action, tomatoes in pot is true.

**add_tomato_paste** — The robot adds a generous amount of tomato paste to the
pot. This action requires that tomatoes in pot is already true as a
precondition. After this action, tomato paste in pot is true.

**stir_sauce** — The robot stirs the tomatoes and paste through the soffritto.
This action requires that tomatoes in pot is already true as a precondition.
This action requires that tomato paste in pot is already true as a
precondition. After this action, sauce mixed is true.

**bring_sauce_to_boil** — The robot raises the heat to bring the sauce back to
the boil. This action requires that sauce mixed is already true as a
precondition. After this action, sauce boiling is true.

**simmer_sauce** — The robot lowers the heat and simmers the sauce for about 20
minutes until it is thick and jammy rather than watery. This action requires
that sauce boiling is already true as a precondition. After this action, sauce
simmering is true. After this action, sauce thick is true.

**taste_season_sauce** — The robot tastes the sauce and adds salt until the
seasoning is right. This action requires that sauce thick is already true as a
precondition. After this action, sauce seasoned is true.

**place_pasta_pot_on_stove** — The robot places a very large pot on the stove
for the pasta. After this action, pasta pot on stove is true.

**fill_pasta_pot_with_water** — The robot fills the large pot right up with
water so the pasta has room to swim. This action requires that pasta pot on
stove is already true as a precondition. After this action, water in pasta pot
is true.

**salt_pasta_water** — The robot adds a large amount of salt to the pasta water
and stirs it in. This action requires that water in pasta pot is already true
as a precondition. After this action, water salted is true.

**taste_pasta_water** — The robot tastes the water and adds more salt until it
is as salty as the sea. This action requires that water salted is already true
as a precondition. After this action, water tasted salty is true.

**boil_pasta_water** — The robot heats the salted water until it is briskly
boiling. This action requires that water in pasta pot is already true as a
precondition. This action requires that water tasted salty is already true as a
precondition. After this action, water boiling is true.

**add_spaghetti** — The robot puts the thin spaghetti into the briskly boiling
salted water. This action requires that water boiling is already true as a
precondition. This action requires that water tasted salty is already true as a
precondition. After this action, spaghetti in water is true.

**stir_spaghetti_until_reboil** — The robot keeps the spaghetti moving with
stirring until the water returns to the boil so it does not stick. This action
requires that spaghetti in water is already true as a precondition. After this
action, spaghetti stirred is true.

**cook_spaghetti_al_dente** — The robot boils the spaghetti, biting a strand to
test it, until it is al dente with only a tiny white core left. This action
requires that spaghetti in water is already true as a precondition. This action
requires that spaghetti stirred is already true as a precondition. After this
action, spaghetti al dente is true.

**drain_spaghetti** — The robot lifts the spaghetti out of the pot into the
colander to drain it. This action requires that spaghetti al dente is already
true as a precondition. After this action, spaghetti drained is true.

**mix_pasta_with_sauce** — The robot puts the drained spaghetti in a mixing
bowl with some sauce and stirs it through. This action requires that spaghetti
drained is already true as a precondition. This action requires that sauce
seasoned is already true as a precondition. After this action, pasta sauced is
true.

**serve_pasta** — The robot transfers the sauced pasta into a pasta serving
bowl. This action requires that pasta sauced is already true as a precondition.
After this action, pasta in serving bowl is true.

**grate_parmesan** — The robot grates a little parmesan over the served pasta
with a microplane or grater. This action requires that pasta in serving bowl is
already true as a precondition. After this action, parmesan grated on pasta is
true.

**add_basil** — The robot scatters fresh basil leaves over the hot pasta. This
action requires that pasta in serving bowl is already true as a precondition.
After this action, basil added is true.

## Ordering rules

The onions must be chopped and the garlic peeled and chopped before they can go
into the pot. The pot must be on the stove before the olive oil is added, and
the oil must be added before it can be heated. The oil must be hot before the
onions and garlic are added. The heat must be turned down and the soffritto
seasoned before the long slow cooking of the onions. The onions must be fully
melted, with no crunch, before the tomatoes are added. The canned tomatoes and
tomato paste must be in the pot and stirred through before the sauce is brought
back to the boil. The sauce must boil before it is turned down to simmer for
about 20 minutes. The sauce must be thick and jammy before it is tasted and
finally seasoned. The pasta pot must be on the stove before it is filled with
water, and the water must be in the pot before it is salted. The water must be
tasted and confirmed as salty as the sea before it is brought to the boil and
before the pasta goes in. The water must be briskly boiling before the
spaghetti is added. The spaghetti must be stirred until the water returns to
the boil before it is left to cook. The spaghetti must be tested by biting and
be al dente before it is drained. The spaghetti must be drained and the sauce
finished and seasoned before the two are mixed together. The pasta must be
mixed with the sauce before it is served into the pasta bowl. The pasta must be
in the serving bowl before the parmesan is grated over it and the basil is
scattered on.

## Source

Generated from https://www.youtube.com/watch?v=ctRo3pmFaKQ
