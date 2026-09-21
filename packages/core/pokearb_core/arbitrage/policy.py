"""Dated policy parameters.

The brief says not to permanently hardcode legal thresholds. So none of them
are constants in code. Every tax rate, duty threshold and handling fee is a
``PolicyParameter`` row with a validity window and the URL it was verified
from, and the engine resolves them for the date of the transaction.

Two practical benefits beyond compliance:

* A backtest run over 2024 data automatically uses 2024's rules.
* Rule changes are a data edit with an audit trail, not a code deploy.

The seed values in ``seed/policy.yaml`` carry their source URLs. Anything not
verified is seeded with ``requires_verification: true`` and the engine refuses
to produce a landed cost that depends on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional, Sequence

__all__ = ["PolicyParameter", "PolicyStore", "PolicyUnverifiedError"]


class PolicyUnverifiedError(RuntimeError):
    """Raised when a calculation depends on an unverified policy value.

    Deliberately an exception rather than a fallback. A landed cost computed
    on a guessed duty rate is exactly the false signal the brief forbids.
    """


@dataclass(frozen=True, slots=True)
class PolicyParameter:
    key: str
    value: Decimal
    unit: str  # "rate" | "EUR" | "DKK" | "JPY" | "count"
    valid_from: date
    valid_to: Optional[date] = None
    source_url: Optional[str] = None
    requires_verification: bool = False
    note: Optional[str] = None

    def covers(self, on: date) -> bool:
        if on < self.valid_from:
            return False
        if self.valid_to is not None and on > self.valid_to:
            return False
        return True


class PolicyStore:
    """Resolve policy values as of a date."""

    def __init__(self, parameters: Optional[Sequence[PolicyParameter]] = None) -> None:
        self._params: list[PolicyParameter] = list(parameters or ())

    def add(self, parameter: PolicyParameter) -> None:
        self._params.append(parameter)

    def extend(self, parameters: Iterable[PolicyParameter]) -> None:
        self._params.extend(parameters)

    def resolve(self, key: str, on: date) -> PolicyParameter:
        matches = [p for p in self._params if p.key == key and p.covers(on)]
        if not matches:
            raise KeyError(
                f"No policy parameter '{key}' valid on {on.isoformat()}. "
                "Refusing to substitute a default."
            )
        if len(matches) > 1:
            # Most recently effective wins; overlapping rows are a data bug
            # worth surfacing in the note.
            matches.sort(key=lambda p: p.valid_from, reverse=True)
        chosen = matches[0]
        if chosen.requires_verification:
            raise PolicyUnverifiedError(
                f"Policy '{key}' is seeded but not verified"
                + (f" ({chosen.note})" if chosen.note else "")
                + ". Verify it and clear requires_verification before relying on "
                "any landed cost that uses it."
            )
        return chosen

    def value(self, key: str, on: date) -> Decimal:
        return self.resolve(key, on).value

    def try_value(self, key: str, on: date) -> Optional[Decimal]:
        """Resolve without raising. Returns ``None`` when unavailable."""
        try:
            return self.value(key, on)
        except (KeyError, PolicyUnverifiedError):
            return None

    def sources(self, keys: Iterable[str], on: date) -> dict[str, Optional[str]]:
        out: dict[str, Optional[str]] = {}
        for key in keys:
            try:
                out[key] = self.resolve(key, on).source_url
            except (KeyError, PolicyUnverifiedError):
                out[key] = None
        return out
