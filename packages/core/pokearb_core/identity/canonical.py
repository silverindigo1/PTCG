"""Canonical identity key.

The canonical key is the contract that makes "same card" a decidable question.
Two printings are the same tradeable unit if and only if their canonical keys
are equal. Everything that distinguishes value must therefore appear in the key.

The key deliberately includes ``printing``, ``edition`` and ``stamp``, which is
what keeps a holo apart from a reverse holo, a 1st Edition apart from an
Unlimited, and a Pokemon Center stamped promo apart from the plain printing.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..types import CardVariant

__all__ = [
    "normalize_set_code",
    "normalize_number",
    "normalize_name",
    "build_canonical_key",
    "SET_CODE_ALIASES",
]


#: Set-code aliases seen in the wild, normalised to the internal code.
#: Keys are already upper-cased and stripped of separators.
SET_CODE_ALIASES: dict[str, str] = {
    "SMP": "SM-P",
    "SMPROMO": "SM-P",
    "XYP": "XY-P",
    "XYPROMO": "XY-P",
    "SP": "S-P",
    "SWSHP": "S-P",
    "SVP": "SV-P",
    "SVPROMO": "SV-P",
    "BWP": "BW-P",
    "BWPROMO": "BW-P",
    "DPP": "DP-P",
    "PROMO": "PROMO",
}

_SEPARATORS = re.compile(r"[\s\u3000_/\\.\-–—]+")
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]+")


def _strip(value: str) -> str:
    """Unicode-normalise, fold full-width characters, upper-case."""
    folded = unicodedata.normalize("NFKC", value).strip()
    return folded.upper()


def normalize_set_code(raw: str | None) -> str | None:
    """Normalise a set or promo-series code.

    Handles the common shapes: ``sm-p``, ``SM P``, ``SMP``, ``ＳＭ－Ｐ``.
    Returns ``None`` for empty input rather than an empty string, so that
    "unknown set" stays distinguishable from "set is blank".
    """
    if raw is None:
        return None
    folded = _strip(raw)
    if not folded:
        return None
    compact = _NON_ALNUM.sub("", folded)
    if compact in SET_CODE_ALIASES:
        return SET_CODE_ALIASES[compact]
    # Insert the canonical hyphen for the "<series><P>" promo shape.
    m = re.fullmatch(r"([A-Z]{1,4})P", compact)
    if m:
        return f"{m.group(1)}-P"
    return _SEPARATORS.sub("-", folded)


def normalize_number(raw: str | None) -> str | None:
    """Normalise a printed card number.

    ``114/SM-P``, ``114`` and ``０１１４`` all normalise to ``114``. Leading
    zeros are stripped because they are a printing convention, not a
    discriminator. Suffix letters (``025a``) are preserved because they are.
    """
    if raw is None:
        return None
    folded = _strip(raw)
    if not folded:
        return None
    # Drop a trailing "/DENOMINATOR" or "/SET-CODE" fragment.
    folded = folded.split("/")[0].strip()
    m = re.fullmatch(r"0*(\d+)\s*([A-Z]?)", folded)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return _NON_ALNUM.sub("", folded) or None


def normalize_name(raw: str | None) -> str | None:
    """Normalise a card or Pokemon name for comparison only.

    Never used as a discriminator on its own: names are the weakest signal in
    the whole system because of cross-language aliasing.
    """
    if raw is None:
        return None
    folded = unicodedata.normalize("NFKC", raw).strip().casefold()
    folded = _SEPARATORS.sub(" ", folded)
    return folded or None


def build_canonical_key(variant: "CardVariant") -> str:
    """Deterministic identity string for a printing.

    Field order is fixed and the separator is reserved, so the key is stable
    across releases and safe to use as a database unique constraint.
    """
    parts = [
        variant.language.value,
        normalize_set_code(variant.set_code) or "?",
        normalize_number(variant.number) or "?",
        variant.printing.value,
        variant.edition.value,
        (normalize_name(variant.stamp) or "none").replace(" ", "-"),
        variant.artwork_id or "a0",
    ]
    return "|".join(parts)
