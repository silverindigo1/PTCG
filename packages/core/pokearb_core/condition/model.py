"""Probabilistic condition normalisation.

The brief is explicit that "Card Rush B equals Cardmarket EX" is unacceptable,
and it is right: a Japanese shop grade is a different measurement instrument
with a different operator, and the honest translation is a distribution.

Model
-----
Each ``(source_id, source_grade_label)`` pair owns a Dirichlet over the
European ladder. Starting parameters are weakly informative and flagged
``provisional`` with ``evidence_n = 0``. Provisional priors are usable, but
they carry low condition confidence, and that confidence propagates all the way
into the opportunity score. A provisional mapping therefore cannot on its own
produce a strong buy signal, which is the behaviour the brief asks for.

Per-listing evidence (photo defects, description keywords) shifts the prior to
a posterior for that one listing. Two cards with the same shop grade can and
should end up with different distributions.

Calibration: ``update_prior`` folds observed outcomes back into the Dirichlet,
so the mapping improves as resale results come in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Mapping, MutableMapping, Optional, Sequence

from ..types import EU_CONDITION_LADDER, ConditionDistribution, EuCondition, Money

__all__ = [
    "DirichletPrior",
    "ConditionEvidence",
    "PriorStore",
    "posterior",
    "condition_confidence",
    "expected_value",
    "WEAK_PRIOR_STRENGTH",
]

#: Total pseudo-count for a provisional prior. Deliberately small so that a
#: handful of real observations dominates it quickly.
WEAK_PRIOR_STRENGTH = Decimal("3")


@dataclass(frozen=True, slots=True)
class DirichletPrior:
    """Dirichlet parameters over the European condition ladder."""

    source_id: str
    source_grade_label: str
    alpha: Mapping[EuCondition, Decimal]
    evidence_n: int = 0
    is_provisional: bool = True
    note: Optional[str] = None

    def __post_init__(self) -> None:
        missing = set(EU_CONDITION_LADDER) - set(self.alpha)
        if missing:
            raise ValueError(f"Dirichlet prior is missing conditions: {sorted(m.value for m in missing)}")
        if any(v <= 0 for v in self.alpha.values()):
            raise ValueError("Dirichlet parameters must be strictly positive.")

    @property
    def concentration(self) -> Decimal:
        return Decimal(sum(self.alpha.values()))

    def mean(self) -> dict[EuCondition, Decimal]:
        total = self.concentration
        return {c: self.alpha[c] / total for c in EU_CONDITION_LADDER}


@dataclass(frozen=True, slots=True)
class ConditionEvidence:
    """Listing-specific evidence that shifts the prior.

    ``defect_weights`` maps a named visible defect to a severity in ``0..1``.
    Severity is a judgement, so it is recorded with its origin and never
    presented as a measurement.
    """

    defect_weights: Mapping[str, Decimal] = None  # type: ignore[assignment]
    has_photos: bool = False
    photo_count: int = 0
    seller_reliability: Optional[Decimal] = None  # 0..1
    description_flags: tuple[str, ...] = ()
    origin: str = "unspecified"

    def __post_init__(self) -> None:
        if self.defect_weights is None:
            object.__setattr__(self, "defect_weights", {})

    @property
    def severity(self) -> Decimal:
        """Aggregate visible wear severity in ``0..1``."""
        if not self.defect_weights:
            return Decimal("0")
        total = sum(self.defect_weights.values())
        return min(Decimal("1"), Decimal(total))

    @property
    def strength(self) -> Decimal:
        """How much this evidence is worth, as a pseudo-count.

        No photos means no evidence. Photos without visible defects are weak
        positive evidence, not proof of mint condition.
        """
        if not self.has_photos:
            return Decimal("0")
        base = Decimal(min(self.photo_count, 4))
        if self.seller_reliability is not None:
            base *= Decimal("0.5") + Decimal("0.5") * self.seller_reliability
        return base


class PriorStore:
    """In-memory prior lookup. The DB-backed implementation shares this API."""

    def __init__(self, priors: Optional[Sequence[DirichletPrior]] = None) -> None:
        self._priors: MutableMapping[tuple[str, str], DirichletPrior] = {}
        for prior in priors or ():
            self.put(prior)

    @staticmethod
    def _key(source_id: str, label: str) -> tuple[str, str]:
        return (source_id.strip().lower(), label.strip().lower())

    def put(self, prior: DirichletPrior) -> None:
        self._priors[self._key(prior.source_id, prior.source_grade_label)] = prior

    def get(self, source_id: str, label: str) -> Optional[DirichletPrior]:
        return self._priors.get(self._key(source_id, label))

    def update_prior(
        self,
        source_id: str,
        label: str,
        observed: EuCondition,
        weight: Decimal = Decimal("1"),
    ) -> DirichletPrior:
        """Fold a confirmed outcome back into the prior.

        Called by the calibration job once a card bought under a shop grade is
        actually assessed against the European ladder.
        """
        prior = self.get(source_id, label)
        if prior is None:
            raise KeyError(f"No prior for ({source_id}, {label}); create one explicitly.")
        alpha = dict(prior.alpha)
        alpha[observed] = alpha[observed] + weight
        updated = replace(
            prior,
            alpha=alpha,
            evidence_n=prior.evidence_n + 1,
            is_provisional=prior.evidence_n + 1 < 20,
        )
        self.put(updated)
        return updated


def _shift_toward_worse(
    probabilities: Mapping[EuCondition, Decimal], severity: Decimal
) -> dict[EuCondition, Decimal]:
    """Move probability mass down the ladder in proportion to visible wear.

    Mass only ever moves toward worse conditions. Evidence of damage can lower
    the estimate; absence of visible damage never raises it above the prior,
    because a photo cannot prove the absence of a defect.
    """
    if severity <= 0:
        return dict(probabilities)
    ladder = EU_CONDITION_LADDER
    out = {c: Decimal("0") for c in ladder}
    for idx, cond in enumerate(ladder):
        mass = probabilities[cond]
        if idx == len(ladder) - 1:
            out[cond] += mass
            continue
        moved = mass * severity
        out[cond] += mass - moved
        out[ladder[idx + 1]] += moved
    return out


def posterior(
    prior: DirichletPrior,
    evidence: Optional[ConditionEvidence] = None,
) -> ConditionDistribution:
    """Combine a prior with listing-specific evidence."""
    probs = prior.mean()
    notes: list[str] = []

    if prior.is_provisional:
        notes.append(
            f"prior for '{prior.source_grade_label}' at {prior.source_id} is provisional "
            f"(evidence_n={prior.evidence_n}); condition confidence reduced accordingly"
        )

    if evidence is not None and evidence.strength > 0:
        probs = _shift_toward_worse(probs, evidence.severity)
        if evidence.defect_weights:
            defects = ", ".join(sorted(evidence.defect_weights))
            notes.append(f"visible defects shifted mass toward lower grades: {defects}")
        else:
            notes.append("photos present, no visible defects recorded")
    elif evidence is not None:
        notes.append("no usable photo evidence; prior used unchanged")

    total = sum(probs.values())
    probs = {c: (v / total) for c, v in probs.items()}
    # Absorb rounding drift into the modal bucket so the sum is exactly 1.
    drift = Decimal("1") - sum(probs.values())
    if drift != 0:
        modal = max(probs, key=lambda c: probs[c])
        probs[modal] += drift

    return ConditionDistribution(
        probabilities=probs,
        evidence_n=prior.evidence_n,
        is_provisional=prior.is_provisional,
        source_grade_label=prior.source_grade_label,
        source_id=prior.source_id,
        notes=tuple(notes),
    )


def condition_confidence(dist: ConditionDistribution) -> Decimal:
    """Confidence in ``0..1``: sharpness of the distribution, discounted for provisionality."""
    values = [float(v) for v in dist.probabilities.values() if v > 0]
    if not values:
        return Decimal("0")
    entropy = -sum(p * math.log(p) for p in values)
    max_entropy = math.log(len(EU_CONDITION_LADDER))
    sharpness = 1.0 - (entropy / max_entropy if max_entropy else 0.0)

    # Evidence discount: a sharp distribution built on nothing is not
    # confident. Uncalibrated mappings are discounted but not crushed, because
    # the inherent spread of a shop grade is already reflected in the sharpness
    # term and should not be charged for twice.
    evidence_factor = min(1.0, 0.5 + 0.5 * (dist.evidence_n / 20.0))
    if dist.is_provisional:
        evidence_factor = min(evidence_factor, 0.75)

    return Decimal(str(round(max(0.0, sharpness) * evidence_factor, 4)))


def expected_value(
    dist: ConditionDistribution,
    value_by_condition: Mapping[EuCondition, Optional[Money]],
) -> Optional[Money]:
    """Probability-weighted resale value across the condition ladder.

    Returns ``None`` if any condition carrying meaningful probability has no
    value estimate. Substituting the NM value for a missing LP value is exactly
    the optimistic shortcut the brief forbids.
    """
    currency = None
    total = Decimal("0")
    covered = Decimal("0")

    for cond, prob in dist.probabilities.items():
        if prob <= Decimal("0.001"):
            continue
        money = value_by_condition.get(cond)
        if money is None:
            return None
        if currency is None:
            currency = money.currency
        elif currency is not money.currency:
            raise ValueError("All condition values must share one currency.")
        total += prob * money.amount
        covered += prob

    if currency is None or covered < Decimal("0.9"):
        return None
    return Money(total / covered, currency)
