"""Card identity matching.

Three stages, in this order:

1. **Hard gates.** A known-versus-known mismatch on any discriminator rejects
   the pair outright. Rejection is final; no score can rescue it.
2. **Weighted score** over the evidence that is present.
3. **Ambiguity penalty.** If sibling variants are *equally* consistent with the
   evidence, confidence is capped below the automatic threshold. This is the
   mechanism that turns "Pikachu 114/SM-P, holo or reverse unstated" into a
   manual review instead of a coin flip dressed up as 0.99.

The thresholds from the brief:

=================  ===========================
confidence          outcome
=================  ===========================
>= 0.98             automatic match
0.90 .. 0.98        manual review recommended
< 0.90              no match
=================  ===========================
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence

from ..types import CardVariant, Edition, Language, Printing
from .canonical import normalize_name, normalize_number, normalize_set_code

__all__ = [
    "MatchOutcome",
    "MatchCandidate",
    "MatchResult",
    "ObservedCard",
    "AliasIndex",
    "match_card",
    "AUTO_MATCH_THRESHOLD",
    "REVIEW_THRESHOLD",
]

AUTO_MATCH_THRESHOLD = Decimal("0.98")
REVIEW_THRESHOLD = Decimal("0.90")

#: Confidence ceiling applied when sibling variants remain equally plausible.
#: Deliberately one step below the review threshold's upper bound so that an
#: ambiguous pair can never be auto-matched.
AMBIGUITY_CEILING = Decimal("0.94")

_WEIGHTS: Mapping[str, Decimal] = {
    "number": Decimal("0.30"),
    "set": Decimal("0.25"),
    "name": Decimal("0.20"),
    "variant_attrs": Decimal("0.15"),
    "provenance": Decimal("0.10"),
}

#: Printings that are visually and economically distinct. A listing that does
#: not disambiguate between two of these cannot be auto-matched.
_CONFUSABLE_PRINTINGS = frozenset({Printing.HOLO, Printing.REVERSE_HOLO, Printing.NON_HOLO})


class MatchOutcome(str, Enum):
    AUTO = "auto_match"
    REVIEW = "manual_review"
    REJECT = "no_match"


@dataclass(frozen=True, slots=True)
class ObservedCard:
    """What a source actually told us. Every field may be unknown."""

    raw_title: str
    language: Optional[Language] = None
    set_code: Optional[str] = None
    number: Optional[str] = None
    printing: Optional[Printing] = None
    edition: Optional[Edition] = None
    stamp: Optional[str] = None
    name: Optional[str] = None
    year: Optional[int] = None
    illustrator: Optional[str] = None
    source_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    variant: CardVariant
    confidence: Decimal
    components: Mapping[str, Decimal]
    gate_failures: tuple[str, ...]
    penalties: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MatchResult:
    outcome: MatchOutcome
    best: Optional[MatchCandidate]
    considered: tuple[MatchCandidate, ...]
    rejected: tuple[MatchCandidate, ...]
    explanation: tuple[str, ...] = ()

    @property
    def variant_id(self) -> Optional[str]:
        if self.outcome is MatchOutcome.AUTO and self.best is not None:
            return self.best.variant.variant_id
        return None


class AliasIndex:
    """Cross-language alias lookup.

    Aliases resolve names only. They are never permitted to override a
    discriminator: ブラッキー and Umbreon may be the same Pokemon, but that
    tells you nothing about which of its twenty printings you are holding.
    """

    def __init__(self, aliases: Optional[Mapping[str, Iterable[str]]] = None) -> None:
        self._by_alias: dict[str, str] = {}
        for canonical, variants in (aliases or {}).items():
            key = normalize_name(canonical)
            if key:
                self._by_alias[key] = key
            for alias in variants:
                alias_key = normalize_name(alias)
                if alias_key and key:
                    self._by_alias[alias_key] = key

    def resolve(self, name: Optional[str]) -> Optional[str]:
        key = normalize_name(name)
        if key is None:
            return None
        return self._by_alias.get(key, key)

    def same_pokemon(self, a: Optional[str], b: Optional[str]) -> Optional[bool]:
        ra, rb = self.resolve(a), self.resolve(b)
        if ra is None or rb is None:
            return None
        return ra == rb


def _gate_failures(obs: ObservedCard, variant: CardVariant) -> tuple[str, ...]:
    """Known-versus-known mismatches. Any hit is an unconditional rejection."""
    failures: list[str] = []

    obs_num = normalize_number(obs.number)
    var_num = normalize_number(variant.number)
    if obs_num is not None and var_num is not None and obs_num != var_num:
        failures.append(f"card number differs ({obs_num} vs {var_num})")

    obs_set = normalize_set_code(obs.set_code)
    var_set = normalize_set_code(variant.set_code)
    if obs_set is not None and var_set is not None and obs_set != var_set:
        failures.append(f"set differs ({obs_set} vs {var_set})")

    if obs.language is not None and obs.language is not variant.language:
        failures.append(
            f"language differs ({obs.language.value} vs {variant.language.value})"
        )

    if obs.printing is not None and obs.printing is not variant.printing:
        failures.append(
            f"printing differs ({obs.printing.value} vs {variant.printing.value})"
        )

    if (
        obs.edition is not None
        and obs.edition is not Edition.NOT_APPLICABLE
        and variant.edition is not Edition.NOT_APPLICABLE
        and obs.edition is not variant.edition
    ):
        failures.append(f"edition differs ({obs.edition.value} vs {variant.edition.value})")

    obs_stamp = normalize_name(obs.stamp)
    var_stamp = normalize_name(variant.stamp)
    if obs_stamp is not None and var_stamp is not None and obs_stamp != var_stamp:
        failures.append(f"stamp differs ({obs_stamp} vs {var_stamp})")
    # A stamped variant matched against an explicitly unstamped observation is
    # also a mismatch, and vice versa. Treat the absent side as "none" only
    # when the observation positively asserts it.
    if obs_stamp == "none" and var_stamp not in (None, "none"):
        failures.append(f"observation states no stamp but variant has '{var_stamp}'")

    return tuple(failures)


def _name_score(obs: ObservedCard, variant: CardVariant, aliases: AliasIndex) -> Decimal:
    obs_name = obs.name or obs.raw_title
    candidates = [variant.name_en, variant.name_ja, variant.pokemon_slug]
    best = Decimal("0")
    for candidate in candidates:
        if not candidate:
            continue
        same = aliases.same_pokemon(obs_name, candidate)
        if same:
            return Decimal("1.0")
        a, b = normalize_name(obs_name), normalize_name(candidate)
        if not a or not b:
            continue
        if b in a or a in b:
            best = max(best, Decimal("0.9"))
            continue
        ratio = Decimal(str(round(difflib.SequenceMatcher(None, a, b).ratio(), 4)))
        best = max(best, ratio)
    return best


def _variant_attr_score(obs: ObservedCard, variant: CardVariant) -> tuple[Decimal, int]:
    """Agreement across variant discriminators, plus a count of relevant unknowns.

    A discriminator only counts as an unknown when it is *relevant* to this
    variant. Edition is irrelevant for a promo that has no editions, and stamp
    is irrelevant for a printing that carries no stamp, so silence about them
    is not a risk. Silence about a discriminator the variant actually carries
    is a real risk and is counted.

    Known-but-unconfirmed scores zero rather than a neutral half. Silence is
    never treated as agreement.
    """
    checks: list[Optional[bool]] = []
    relevant_unknown = 0

    # Printing is always a discriminator: holo, reverse and non-holo trade apart.
    if obs.printing is None:
        relevant_unknown += 1
    else:
        checks.append(obs.printing is variant.printing)

    # Edition only discriminates where the variant actually has one.
    if variant.edition is not Edition.NOT_APPLICABLE:
        if obs.edition is None:
            relevant_unknown += 1
        else:
            checks.append(obs.edition is variant.edition)

    # Stamp only discriminates where the variant actually carries one.
    if variant.stamp is not None:
        if obs.stamp is None:
            relevant_unknown += 1
        else:
            checks.append(normalize_name(obs.stamp) == normalize_name(variant.stamp))
    elif obs.stamp is not None:
        checks.append(normalize_name(obs.stamp) == "none")

    if not checks:
        return Decimal("0"), relevant_unknown
    agreed = sum(1 for c in checks if c)
    return Decimal(agreed) / Decimal(len(checks)), relevant_unknown


def _provenance_score(obs: ObservedCard, variant: CardVariant) -> Optional[Decimal]:
    """Year and illustrator agreement, or ``None`` when not evaluable.

    Provenance is a *confirmation* signal, not a required one. Japanese shop
    listings almost never state the illustrator, so treating its absence as a
    zero would make the 0.98 automatic threshold unreachable in exactly the
    situation the product exists for. When neither side supplies the data the
    component is dropped and its weight is redistributed proportionally over
    the components that could actually be evaluated. Disagreement, by
    contrast, still scores zero and counts fully.

    Number and set do not get this treatment: a listing that fails to state a
    card number has a real identification gap, and that gap should show up in
    the confidence rather than be normalised away.
    """
    checks: list[bool] = []
    if obs.year is not None and variant.year is not None:
        checks.append(obs.year == variant.year)
    if obs.illustrator and variant.illustrator:
        checks.append(normalize_name(obs.illustrator) == normalize_name(variant.illustrator))
    if not checks:
        return None
    return Decimal(sum(1 for c in checks if c)) / Decimal(len(checks))


def _sibling_ambiguity(
    obs: ObservedCard,
    variant: CardVariant,
    universe: Mapping[str, CardVariant],
) -> tuple[bool, tuple[str, ...]]:
    """Are sibling variants equally consistent with what we observed?

    Returns ``True`` when at least one sibling survives the same hard gates,
    which means the evidence does not pin down which printing this is.
    """
    notes: list[str] = []
    ambiguous = False
    for sibling_id in variant.sibling_variant_ids:
        sibling = universe.get(sibling_id)
        if sibling is None:
            continue
        if _gate_failures(obs, sibling):
            continue  # evidence rules this sibling out, good
        ambiguous = True
        reason = "printing" if sibling.printing is not variant.printing else "variant"
        notes.append(
            f"sibling {sibling.variant_id} ({sibling.printing.value}) is equally "
            f"consistent with the evidence; {reason} not disambiguated"
        )
    return ambiguous, tuple(notes)


def match_card(
    observed: ObservedCard,
    universe: Sequence[CardVariant],
    aliases: Optional[AliasIndex] = None,
) -> MatchResult:
    """Match an observed listing against the canonical universe.

    ``universe`` should be a pre-blocked candidate set (same language and set
    code where known) for performance, but correctness does not depend on it.
    """
    aliases = aliases or AliasIndex()
    by_id = {v.variant_id: v for v in universe}

    considered: list[MatchCandidate] = []
    rejected: list[MatchCandidate] = []

    for variant in universe:
        gates = _gate_failures(observed, variant)
        if gates:
            rejected.append(
                MatchCandidate(variant, Decimal("0"), {}, gates, ())
            )
            continue

        number_score = (
            Decimal("1")
            if normalize_number(observed.number)
            and normalize_number(observed.number) == normalize_number(variant.number)
            else Decimal("0")
        )
        set_score = (
            Decimal("1")
            if normalize_set_code(observed.set_code)
            and normalize_set_code(observed.set_code) == normalize_set_code(variant.set_code)
            else Decimal("0")
        )
        name_score = _name_score(observed, variant, aliases)
        attr_score, unknown_attrs = _variant_attr_score(observed, variant)
        prov_score = _provenance_score(observed, variant)

        components: dict[str, Decimal] = {
            "number": number_score,
            "set": set_score,
            "name": name_score,
            "variant_attrs": attr_score,
        }
        if prov_score is not None:
            components["provenance"] = prov_score

        # Redistribute the weight of any dropped optional component so the
        # weights over evaluable components still sum to one.
        active_weight = sum(_WEIGHTS[k] for k in components)
        raw = sum(
            (_WEIGHTS[k] / active_weight) * v for k, v in components.items()
        )

        penalties: list[str] = []
        confidence = Decimal(raw)

        ambiguous, ambiguity_notes = _sibling_ambiguity(observed, variant, by_id)
        if ambiguous:
            penalties.extend(ambiguity_notes)
            confidence = min(confidence, AMBIGUITY_CEILING)

        if unknown_attrs and confidence >= AUTO_MATCH_THRESHOLD:
            # Never auto-match while a discriminator is unstated, even with a
            # perfect name and number. This is the conservative bias in code.
            penalties.append(
                f"{unknown_attrs} relevant variant discriminator(s) unstated by "
                "the source"
            )
            confidence = min(confidence, AMBIGUITY_CEILING)

        considered.append(
            MatchCandidate(
                variant,
                confidence.quantize(Decimal("0.0001")),
                {k: v.quantize(Decimal("0.0001")) for k, v in components.items()},
                (),
                tuple(penalties),
            )
        )

    considered.sort(key=lambda c: c.confidence, reverse=True)

    if not considered:
        return MatchResult(
            MatchOutcome.REJECT,
            None,
            (),
            tuple(rejected),
            ("every candidate failed a hard gate",),
        )

    best = considered[0]

    # Two distinct variants tied at the top is itself ambiguity.
    if len(considered) > 1 and considered[1].confidence == best.confidence:
        best = MatchCandidate(
            best.variant,
            min(best.confidence, AMBIGUITY_CEILING),
            best.components,
            best.gate_failures,
            best.penalties + ("tied with another candidate at equal confidence",),
        )
        considered[0] = best

    if best.confidence >= AUTO_MATCH_THRESHOLD:
        outcome = MatchOutcome.AUTO
    elif best.confidence >= REVIEW_THRESHOLD:
        outcome = MatchOutcome.REVIEW
    else:
        outcome = MatchOutcome.REJECT

    explanation = tuple(
        f"{k}={v}" for k, v in sorted(best.components.items())
    ) + best.penalties

    return MatchResult(outcome, best, tuple(considered), tuple(rejected), explanation)
