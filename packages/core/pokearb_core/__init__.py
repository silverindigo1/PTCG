"""PokeArb core engines. No I/O lives in this package.

Every module here takes plain value objects and returns plain value objects.
That constraint is what makes the backtester trustworthy: replaying history
cannot accidentally read present-day state, because there is nothing to read.
"""

from .types import (  # noqa: F401
    UNKNOWN,
    CardVariant,
    ConditionDistribution,
    Currency,
    DataQuality,
    Edition,
    EU_CONDITION_LADDER,
    EuCondition,
    EvidenceRef,
    FairValue,
    FxRate,
    Grader,
    Language,
    Listing,
    Money,
    PopulationSnapshot,
    Printing,
    Sale,
    Scenario,
    SupplySnapshot,
    Unknown,
)

__version__ = "0.1.0"
