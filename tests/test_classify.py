"""Classifier tests, driven by titles recorded from the live shop feeds.

The recorded group in tests/fixtures/recorded_titles.json is the important one:
those strings were read from the shops' own catalogue endpoints on 2026-09-22,
quoted exactly. The constructed group fills gaps the recorded sample did not
happen to cover.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pokearb.classify import (
    SetMatcher,
    classify,
    detect_language,
    detect_type,
    extract_set_candidate,
    normalise,
    rejection_reason,
)
from pokearb.models import Language, ProductType

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "recorded_titles.json").read_text("utf-8")
)

# A small stand-in for the live TCGdex list, so the tests never touch the
# network. Names and ids follow TCGdex's own shape.
FAKE_SETS = [
    {"id": "sv08", "name": "Surging Sparks", "releaseDate": "2024-11-08"},
    {"id": "sv08", "name": "Rivalen am Abgrund", "releaseDate": "2024-11-08"},
    {"id": "sv08.5", "name": "Prismatic Evolutions", "releaseDate": "2025-01-17"},
    {"id": "sv09", "name": "Journey Together", "releaseDate": "2025-03-28"},
    {"id": "sv10", "name": "Destined Rivals", "releaseDate": "2025-05-30"},
    {"id": "sv07", "name": "Stellar Crown", "releaseDate": "2024-09-13"},
    {"id": "sv06.5", "name": "Shrouded Fable", "releaseDate": "2024-08-02"},
    {"id": "sv03.5", "name": "151", "releaseDate": "2023-09-22"},
    {"id": "m3", "name": "Nihil Zero", "releaseDate": "2026-06-12"},
    {"id": "cel30", "name": "30th Celebration", "releaseDate": "2026-02-27"},
    {"id": "tf", "name": "Terastal Festival", "releaseDate": "2025-10-10"},
    {"id": "sv06", "name": "Twilight Masquerade", "releaseDate": "2024-05-24"},
    {"id": "meg", "name": "Mega Evolution", "releaseDate": "2026-09-26"},
    {"id": "sv4a", "name": "Shiny Treasure ex", "releaseDate": "2023-12-01"},
    {"id": "m2", "name": "Inferno X", "releaseDate": "2026-04-24"},
    {"id": "m4", "name": "Ninja Spinner", "releaseDate": "2026-08-08"},
    {"id": "m6", "name": "Storm Emeralda", "releaseDate": "2026-09-19"},
    {"id": "sv9a", "name": "Heat Wave Arena", "releaseDate": "2025-02-28"},
    {"id": "sv02", "name": "Paldea Evolved", "releaseDate": "2023-06-09"},
    # The Spanish TCGdex name for the same set, as an alias would carry it.
    {"id": "sv02", "name": "Evoluciones en Paldea", "releaseDate": "2023-06-09"},
    {"id": "me2", "name": "Delta Reign", "releaseDate": "2026-05-01"},
]


@pytest.fixture(scope="module")
def matcher() -> SetMatcher:
    aliases = {normalise(s["name"]): s for s in FAKE_SETS}
    return SetMatcher(aliases)


def _cases(group: str):
    return [c for c in FIXTURES[group] if isinstance(c, dict)]


# --------------------------------------------------------------------------
# Recorded titles
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", _cases("recorded"), ids=lambda c: f"{c['shop']}:{c['title'][:40]}"
)
def test_recorded_titles(case, matcher):
    result = classify(case["title"], matcher)
    if case["expect"] == "accept":
        assert result.accepted, f"burde vaere accepteret: {case['why']} ({result.reason})"
        assert result.product_type == ProductType(case["type"])
        assert result.language == Language(case["language"])
    else:
        assert not result.accepted, (
            f"burde vaere afvist ({case['why']}), men blev {result.product_type} "
            f"for saettet {result.set_name!r}"
        )


@pytest.mark.parametrize(
    "case", _cases("constructed"), ids=lambda c: c["title"][:45]
)
def test_constructed_titles(case, matcher):
    result = classify(case["title"], matcher)
    if case["expect"] == "accept":
        assert result.accepted, f"blev afvist: {result.reason}"
        assert result.product_type == ProductType(case["type"])
        assert result.language == Language(case["language"])
    else:
        assert not result.accepted, f"burde vaere afvist: {case['why']}"


# --------------------------------------------------------------------------
# Unit-level behaviour
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Pokemon Surging Sparks Elite Trainer Box", ProductType.ETB),
        ("Pokemon KP08 Top-Trainer-Box", ProductType.ETB),
        ("Pokemon Prismatic Pokemon Center Elite Trainer Box", ProductType.ETB_POKEMON_CENTER),
        ("Pokemon 151 Booster Box", ProductType.BOOSTER_BOX),
        ("Pokemon Destined Rivals Display", ProductType.BOOSTER_BOX),
        ("Pokemon Journey Together Booster Bundle", ProductType.BOOSTER_BUNDLE),
        ("Pokemon Ultra Premium Collection Charizard", ProductType.UPC),
        ("Pokemon Surging Sparks Booster Pack", None),
    ],
)
def test_detect_type(title, expected):
    assert detect_type(normalise(title)) is expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Pokemon 151 Display (JP)", Language.JA),
        ("Pokemon 151 Display Japansk", Language.JA),
        ("Pokemon Display Deutsch", Language.DE),
        ("Pokemon Display (DE)", Language.DE),
        ("Pokemon Display (KOR)", Language.KO),
        ("Pokemon Display Kinesisk", Language.ZH),
        ("Pokemon Display (FR)", Language.FR),
        ("Pokemon Surging Sparks Display", None),
    ],
)
def test_detect_language(title, expected):
    # None means the title declares no language; classify() then falls back
    # to the matched set name's language, and finally to English.
    assert detect_language(normalise(title), title) is expected


def test_language_code_needs_to_stand_alone():
    # "de" inside "Destined" must not read as German.
    title = "Pokemon Destined Rivals Display"
    assert detect_language(normalise(title), title) is None
    # A Danish title saying "Engelsk" is English, not Danish-tagged noise.
    title = "Pokemon 151 Display Engelsk"
    assert detect_language(normalise(title), title) is Language.EN


@pytest.mark.parametrize(
    "title",
    [
        "Pokemon Elite Trainer Box Sleeves 65 stk",
        "Pokemon ETB PSA 10",
        "Pokemon Display Master Case",
        "Pokemon Booster Box b-vare",
        "Pokemon Elite Trainer Box tom aeske",
        "Charizard 004/165 151",
    ],
)
def test_rejection_reasons_fire(title):
    # The raw title is passed too, because the card-number test needs the
    # slash that normalise() strips.
    assert rejection_reason(normalise(title), title) is not None


def test_set_candidate_strips_noise():
    candidate = extract_set_candidate(
        normalise("Pokémon TCG SV08 Surging Sparks Booster Display (EN) Sealed")
    )
    assert candidate == "surging sparks"


def test_german_set_name_resolves_to_english_record(matcher):
    result = classify("Pokemon KP08 Rivalen am Abgrund Display", matcher)
    assert result.accepted
    assert result.set_id == "sv08"
    assert result.set_name == "Rivalen am Abgrund"


def test_unmatched_set_is_kept_verbatim_not_guessed(matcher):
    result = classify("Pokemon Zarude Legends Untold Elite Trainer Box", matcher)
    assert result.accepted
    assert result.set_matched is False
    assert result.set_id is None
    # The raw words survive rather than being snapped to a real set.
    assert "zarude" in result.set_name.lower()


def test_non_pokemon_with_real_set_word_is_still_rejected(matcher):
    assert not classify("Topps Match Attax UCC 2026/27 Booster Box", matcher).accepted


def test_accessory_beats_product_type(matcher):
    # Contains "Elite Trainer Box" but is a plastic case.
    result = classify("Acrylic Display Case for Pokemon Elite Trainer Box", matcher)
    assert not result.accepted
    assert "tilbeh" in result.reason


def test_classification_is_stable_across_punctuation(matcher):
    a = classify("Pokémon TCG: Surging Sparks - Elite Trainer Box", matcher)
    b = classify("POKEMON  TCG   SURGING SPARKS   ELITE TRAINER BOX", matcher)
    assert (a.set_id, a.product_type, a.language) == (b.set_id, b.product_type, b.language)


# --------------------------------------------------------------------------
# Set codes from the Japanese, Korean and Chinese lines
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Pokemon TCG: Cyber Judge (sv5m) - Display (JP)", "cyber judge"),
        ("Pokemon TCG | Legendary Heartbeat (S3a) | Display (KOR)", "legendary heartbeat"),
        ("Pokemon TCG: Heat Wave Arena (SV9a) - Display (KOR)", "heat wave arena"),
        ("Pokemon TCG | Inferno X (M2) | Display (KOR)", "inferno x"),
        ("Pokemon TCG: Fearless Terastal (CSV3C) - Slim Display (CHN)", "fearless terastal slim"),
                # The volume number is part of the name: Vol. 4 and Vol. 5 are
        # different products, and dropping the digit merged them.
        ("Pokemon TCG | Gem Pack Vol. 5 (CBB5C) | Display (CHN)", "gem pack vol 5"),
        ("Pokemon Destined Rivals Booster Box 36 pakker", "destined rivals"),
        ("Pokemon TCG: Black Bolt Display (SV11B) (KOR)", "black bolt"),
        ("Pokemon TCG SV08 Surging Sparks Booster Display (EN)", "surging sparks"),
        # Names that merely look like codes must survive untouched.
        ("Pokemon 30th Celebration Booster Box", "30th celebration"),
        ("Pokemon 151 Elite Trainer Box", "151"),
    ],
)
def test_set_codes_are_stripped_without_eating_the_name(title, expected):
    assert extract_set_candidate(normalise(title)) == expected


# --------------------------------------------------------------------------
# Regressions found by running all 13 shops, 2026-09-22
# --------------------------------------------------------------------------


def test_extra_words_block_a_set_match(matcher):
    """The set list has "Mega Evolution". These are five different boxes.

    token_set_ratio ignores words the candidate has and the set name does not,
    so all five matched the same set and the site reported a 44 percent
    saving between two products that are not the same thing.
    """
    variants = [
        "Pokemon Mega Evolution: Chaos Rising Booster Box",
        "Pokemon Mega Evolution: Pitch Black Booster Box",
        "Pokemon Mega Evolution: Perfect Order Booster Box",
        "Pokemon Mega Evolution: Phantasmal Flames Booster Box",
    ]
    names = set()
    for title in variants:
        result = classify(title, matcher)
        assert result.accepted
        assert result.set_id != "meg", f"{title} maa ikke matche selve saettet Mega Evolution"
        names.add(result.set_name.lower())
    assert len(names) == 4, "de fire varianter skal give fire forskellige produkter"

    # The plain box still matches the set itself.
    plain = classify("Pokemon Mega Evolution Booster Box", matcher)
    assert plain.set_id == "meg"


def test_half_displays_are_rejected(matcher):
    # TcgReus listed a half box at 1.288 kr beside full boxes at 3.000 kr.
    result = classify("Pokemon TCG - Mega Evolution Halve Booster Box", matcher)
    assert not result.accepted
    assert "halv" in result.reason


def test_a_case_of_boxes_is_rejected(matcher):
    result = classify("Pokemon Mega Evolution: Pitch Black Booster Box Case", matcher)
    assert not result.accepted


def test_short_leftover_cannot_claim_a_set(matcher):
    # "Violet" is stripped as a Scarlet & Violet noise word, leaving "ex",
    # which matched "Shiny Treasure ex" and put two unrelated Japanese
    # displays in the same table.
    result = classify("Pokemon Violet EX Display Japanisch", matcher)
    assert result.set_id != "sv4a"


@pytest.mark.parametrize(
    "title,fragment",
    [
        # Each of these reached the live site before being caught.
        ("Pokemon Shrouded Fable 60X Packs Booster Bundle", "flere enheder"),
        ("Pokemon Paradox Rift Case Med 6 Booster Bokse", "kasse"),
        ("Pokemon Fusion Strike Pokemon Center Elite Trainer Box Kosmetisk Skade", "beskadiget"),
        ("Pokemon Mega Evolution Pitch Black Booster Box Case", "kasse"),
    ],
)
def test_defects_found_in_the_first_full_run(title, fragment, matcher):
    result = classify(title, matcher)
    assert not result.accepted, f"burde vaere afvist, blev {result.product_type}"
    assert fragment in result.reason, f"forkert aarsag: {result.reason}"


def test_one_letter_spelling_difference_still_matches(matcher):
    # Shops spell the Japanese set M6 both ways. Both must reach one product.
    a = classify("[JP] Pokémon Display - Storm Emerald (M6)", matcher)
    b = classify("Pre-Order | Pokemon - Storm Emeralda Display (M6) (JP)", matcher)
    assert a.set_id == b.set_id == "m6"


def test_near_match_does_not_reopen_the_mega_evolution_hole(matcher):
    # The tolerance is per word and needs long words, so a different subset
    # name still blocks the match.
    result = classify("Pokemon Mega Evolution: Pitch Black Booster Box", matcher)
    assert result.set_id != "meg"


@pytest.mark.parametrize(
    "title,set_id",
    [
        # Recorded at Pokemillon 2026-09-23: Spanish name, then English name.
        ("ETB Evoluciones de Paldea | Élite Paldea Evolved Pokémon Center - Inglés", "sv02"),
        # Recorded at Lichcards 2026-09-23: series and set joined by an em dash.
        ("Pokémon TCG: Mega Evolution—Delta Reign Elite Trainer Box – English - English / Normal", "me2"),
    ],
)
def test_dual_name_titles_match_on_a_segment(title, set_id, matcher):
    result = classify(title, matcher)
    assert result.accepted
    assert result.set_id == set_id


def test_segment_matching_keeps_the_leftover_rule(matcher):
    # "Pitch Black" must still not be absorbed into "Mega Evolution" just
    # because the title has a separator.
    result = classify("Pokémon TCG: Mega Evolution: Pitch Black - Booster Box", matcher)
    assert result.set_id != "meg"


def test_series_prefix_is_dropped_only_when_a_set_follows(matcher):
    assert classify("Pokemon Mega Evolution Delta Reign Booster Box", matcher).set_id == "me2"
    # Nothing recognisable after the series name: the base set, or nothing.
    assert classify("Pokemon Mega Evolution Booster Box", matcher).set_id == "meg"
    assert classify("Pokemon Mega Evolution Enhanced Booster Box", matcher).set_id is None
