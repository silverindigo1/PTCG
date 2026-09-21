"""Core value objects for PokeArb.

Design rules enforced here:

* Money is ``Decimal``. There is no float anywhere in a monetary path.
* Anything that can be unknown is ``Optional`` and defaults to ``None``.
  ``None`` means "not measured". It never means zero and it is never imputed.
* Every derived quantity carries the evidence used to produce it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping, Optional, Sequence

__all__ = [
    "Currency",
    "Language",
    "Edition",
    "Printing",
    "EuCondition",
    "EU_CONDITION_LADDER",
    "Grader",
    "Scenario",
    "AcquisitionPurpose",
    "ValueBasis",
    "Unknown",
    "UNKNOWN",
    "Money",
    "FxRate",
    "CardVariant",
    "Sale",
    "Listing",
    "MarketAverage",
    "PopulationSnapshot",
    "ConditionDistribution",
    "FairValue",
    "EvidenceRef",
    "DataQuality",
]


class Currency(str, enum.Enum):
    JPY = "JPY"
    EUR = "EUR"
    DKK = "DKK"
    USD = "USD"
    GBP = "GBP"


#: Currencies with no minor unit. Rounding differs.
ZERO_DECIMAL_CURRENCIES = frozenset({Currency.JPY})


class Language(str, enum.Enum):
    JA = "ja"
    EN = "en"
    DE = "de"
    FR = "fr"
    IT = "it"
    ES = "es"
    PT = "pt"
    KO = "ko"
    ZH_HANT = "zh-Hant"


class Edition(str, enum.Enum):
    FIRST = "1st"
    UNLIMITED = "unlimited"
    SHADOWLESS = "shadowless"
    NOT_APPLICABLE = "n/a"


class Printing(str, enum.Enum):
    NON_HOLO = "non-holo"
    HOLO = "holo"
    REVERSE_HOLO = "reverse-holo"
    TEXTURED = "textured"
    FOIL_OTHER = "foil-other"


class EuCondition(str, enum.Enum):
    """European marketplace condition ladder, best to worst."""

    NM = "NM"
    EX = "EX"
    GD = "GD"
    LP = "LP"
    PL = "PL"
    PO = "PO"


#: Canonical ordering, best first. Used by entropy and monotonicity checks.
EU_CONDITION_LADDER: tuple[EuCondition, ...] = (
    EuCondition.NM,
    EuCondition.EX,
    EuCondition.GD,
    EuCondition.LP,
    EuCondition.PL,
    EuCondition.PO,
)


class Grader(str, enum.Enum):
    PSA = "PSA"
    CGC = "CGC"
    BGS = "BGS"
    ACE = "ACE"
    TAG = "TAG"


class Scenario(str, enum.Enum):
    """Acquisition scenarios from the brief."""

    HAND_CARRY = "A_hand_carry"
    SHIPPED = "B_shipped"
    PROXY = "C_proxy"


class AcquisitionPurpose(str, enum.Enum):
    """Why the goods are being imported.

    This is not a cosmetic label. Danish relief for travellers' goods applies
    only to goods for private use, and goods imported with a view to resale are
    expressly outside it, so the purpose decides whether the allowance and the
    Japanese departure refund may be modelled at all. Verified at
    https://info.skat.dk/data.aspx?oid=2230232 (F.A.29 Rejsegods): the relief
    covers goods for private use, requires the import to be of non-commercial
    character, and goods cannot be treated as for own use when imported with a
    view to resale.

    ``RESALE`` is the default because this is an arbitrage system. Choosing
    ``PERSONAL`` is an assertion about the user's own facts, not a modelling
    switch, and it is recorded in the result.
    """

    RESALE = "resale"
    PERSONAL = "personal"


class ValueBasis(str, enum.Enum):
    """Whether a per-condition value was measured or derived from a model."""

    OBSERVED = "observed"
    MODELLED = "modelled"


class Unknown:
    """Sentinel distinct from ``None``.

    ``None`` is used for optional fields that were simply never populated.
    ``UNKNOWN`` is returned by engines to say "I looked and the evidence does
    not support producing a number". The two are deliberately different so that
    a UI can say "not measured" versus "insufficient evidence".
    """

    _instance: Optional["Unknown"] = None

    def __new__(cls) -> "Unknown":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return False

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "UNKNOWN"


UNKNOWN = Unknown()


@dataclass(frozen=True, slots=True)
class Money:
    """An exact monetary amount in a single currency."""

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError(
                f"Money.amount must be Decimal, got {type(self.amount).__name__}. "
                "Floats are rejected to keep monetary arithmetic exact."
            )

    def _check(self, other: "Money") -> None:
        if self.currency is not other.currency:
            raise ValueError(
                f"Refusing to combine {self.currency.value} and "
                f"{other.currency.value} without an explicit FX conversion."
            )

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Decimal | int) -> "Money":
        return Money(self.amount * Decimal(factor), self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount <= other.amount

    @classmethod
    def of(cls, amount: str | int | Decimal, currency: Currency) -> "Money":
        return cls(Decimal(str(amount)), currency)

    def quantize(self) -> "Money":
        exp = Decimal("1") if self.currency in ZERO_DECIMAL_CURRENCIES else Decimal("0.01")
        return Money(self.amount.quantize(exp), self.currency)


@dataclass(frozen=True, slots=True)
class FxRate:
    """A dated FX rate. Dated so historical calculations reproduce exactly."""

    as_of: date
    base: Currency
    quote: Currency
    rate: Decimal
    source_url: str

    def convert(self, money: Money) -> Money:
        if money.currency is not self.base:
            raise ValueError(
                f"Rate is {self.base.value}->{self.quote.value}, "
                f"cannot convert {money.currency.value}."
            )
        return Money(money.amount * self.rate, self.quote)


@dataclass(frozen=True, slots=True)
class CardVariant:
    """The canonical tradeable printing.

    ``canonical_key`` is derived, never supplied. Two variants are the same card
    if and only if their canonical keys are equal.
    """

    variant_id: str
    language: Language
    set_code: str
    number: str
    printing: Printing
    edition: Edition = Edition.NOT_APPLICABLE
    stamp: Optional[str] = None
    name_en: Optional[str] = None
    name_ja: Optional[str] = None
    pokemon_slug: Optional[str] = None
    series: Optional[str] = None
    year: Optional[int] = None
    illustrator: Optional[str] = None
    artwork_id: Optional[str] = None
    release_method: Optional[str] = None
    region: Optional[str] = None
    sibling_variant_ids: tuple[str, ...] = ()

    @property
    def canonical_key(self) -> str:
        from .identity.canonical import build_canonical_key

        return build_canonical_key(self)


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Pointer back to the record that justified a number."""

    kind: str  # "sale" | "listing" | "population" | "fx" | "policy"
    record_id: str
    source_id: str
    source_url: Optional[str]
    observed_at: datetime
    weight: Optional[Decimal] = None
    note: Optional[str] = None


@dataclass(frozen=True, slots=True)
class Sale:
    """A completed transaction. The only permitted fair-value input."""

    sale_id: str
    variant_id: str
    source_id: str
    sold_at: datetime
    price: Money
    condition: Optional[EuCondition]
    language: Language
    source_url: Optional[str] = None
    is_graded: bool = False
    grader: Optional[Grader] = None
    grade: Optional[Decimal] = None
    shipping_included: bool = False
    condition_confidence: Decimal = Decimal("1.0")
    #: Market the transaction happened in ("EU", "JP", ...). A Japanese sale is
    #: evidence of a Japanese price, never of a European resale price, so this
    #: is a scoping key and not decoration.
    market: Optional[str] = None
    #: The source's own stable identifier for the transaction. Deduplication
    #: keys on this: re-importing the same file must not manufacture evidence.
    external_id: Optional[str] = None
    #: When this sale became known to us. Distinct from ``sold_at``. A
    #: historical calculation may only use evidence that had arrived by the
    #: calculation date, and filtering on the transaction date alone does not
    #: achieve that.
    known_at: Optional[datetime] = None

    @property
    def grade_bucket(self) -> str:
        """Valuation bucket. Raw, and each grader/grade pair, price separately."""
        if not self.is_graded:
            return "raw"
        grader = self.grader.value if self.grader else "unknown_grader"
        # format(..., "f") rather than normalize(): Decimal("10").normalize()
        # renders as 1E+1, which would make "psa:10" and "psa:1E+1" different
        # buckets for the same grade.
        grade = format(self.grade, "f") if self.grade is not None else "unknown_grade"
        return f"{grader}:{grade}"

    @property
    def dedupe_key(self) -> tuple[str, str]:
        """Stable transaction identity used to count independent sales."""
        return (self.source_id, self.external_id or self.sale_id)


@dataclass(frozen=True, slots=True)
class Listing:
    """An active offer. Never a fair-value input."""

    listing_id: str
    variant_id: str
    source_id: str
    observed_at: datetime
    price: Money
    condition: Optional[EuCondition]
    language: Language
    source_url: Optional[str] = None
    seller_id: Optional[str] = None
    is_graded: bool = False
    grader: Optional[Grader] = None
    grade: Optional[Decimal] = None
    quantity: int = 1
    #: Market the offer sits in ("EU", "JP", ...). European supply and European
    #: liquidity may only be measured from European listings.
    market: Optional[str] = None
    #: The source's own listing identifier. Successive observations of one
    #: listing share it, which is what stops a repriced listing from being
    #: counted as two independent offers.
    external_id: Optional[str] = None

    @property
    def grade_bucket(self) -> str:
        if not self.is_graded:
            return "raw"
        grader = self.grader.value if self.grader else "unknown_grader"
        grade = format(self.grade, "f") if self.grade is not None else "unknown_grade"
        return f"{grader}:{grade}"

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return (self.source_id, self.external_id or self.listing_id)


@dataclass(frozen=True, slots=True)
class MarketAverage:
    """A marketplace's own published price averages for one card.

    Distinct from ``Sale`` on purpose, and never convertible into one. An
    average says nothing about how many transactions produced it: a 30-day
    average built from one sale looks exactly like one built from two hundred.
    The valuation code therefore treats this as a different, weaker kind of
    evidence with its own acceptance rules, see
    ``pokearb_core.valuation.benchmark``.

    Price fields are ``None`` when the provider did not publish a usable
    figure. A published zero is also stored as ``None``: a Cardmarket average
    of exactly zero means "no data", not "free".
    """

    variant_id: str
    provider: str                  # "cardmarket"
    via: str                       # "tcgdex": who relayed the provider's figures
    product_id: Optional[str]      # the provider's own product id, for collision checks
    finish: str                    # "base" or "reverse": which set of fields was read
    currency: Currency
    provider_updated_at: datetime  # when the provider last recomputed the figures
    known_at: datetime             # when we fetched them; the look-ahead boundary
    avg: Optional[Decimal] = None
    low: Optional[Decimal] = None
    trend: Optional[Decimal] = None
    avg1: Optional[Decimal] = None
    avg7: Optional[Decimal] = None
    avg30: Optional[Decimal] = None
    market: str = "EU"
    language: Optional[Language] = None
    source_url: Optional[str] = None
    raw_hash: Optional[str] = None


@dataclass(frozen=True, slots=True)
class PopulationSnapshot:
    """Dated grade-ladder snapshot.

    Every count is optional. A missing count is ``None``, which means the figure
    was not retrieved. It is never rendered as zero.
    """

    variant_id: str
    grader: Grader
    observed_at: datetime
    source_url: Optional[str]
    total: Optional[int] = None
    grade_10: Optional[int] = None
    grade_9: Optional[int] = None
    grade_8: Optional[int] = None
    grade_7_and_below: Optional[int] = None

    @property
    def gem_rate(self) -> Optional[Decimal]:
        if self.total is None or self.grade_10 is None or self.total == 0:
            return None
        return Decimal(self.grade_10) / Decimal(self.total)


@dataclass(frozen=True, slots=True)
class ConditionDistribution:
    """Posterior probability over the European condition ladder."""

    probabilities: Mapping[EuCondition, Decimal]
    evidence_n: int
    is_provisional: bool
    source_grade_label: Optional[str] = None
    source_id: Optional[str] = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        total = sum(self.probabilities.values())
        if abs(total - Decimal("1")) > Decimal("0.0001"):
            raise ValueError(f"Condition probabilities must sum to 1, got {total}.")


@dataclass(frozen=True, slots=True)
class DataQuality:
    """Explainable data-quality assessment attached to every opportunity."""

    score: Decimal  # 0..100
    reasons: tuple[str, ...]
    n_sales: int
    oldest_input_age_days: Optional[int]
    newest_input_age_days: Optional[int]
    source_count: int


@dataclass(frozen=True, slots=True)
class FairValue:
    """A fair-value estimate, or an explicit statement that there is not one."""

    window_days: Optional[int]
    value: Optional[Money]
    sufficient: bool
    n_sales: int
    n_effective: Decimal
    dispersion: Optional[Decimal]  # IQR / median
    method: str
    inputs: tuple[EvidenceRef, ...] = ()
    excluded: tuple[EvidenceRef, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_unknown(self) -> bool:
        return not self.sufficient or self.value is None


@dataclass(frozen=True, slots=True)
class SupplySnapshot:
    variant_id: str
    market: str  # "EU" | "JP"
    observed_at: datetime
    active_listings: Optional[int] = None
    lowest: Optional[Money] = None
    median: Optional[Money] = None
    graded_listings: Optional[int] = None
    raw_listings: Optional[int] = None
    source_urls: tuple[str, ...] = field(default_factory=tuple)
