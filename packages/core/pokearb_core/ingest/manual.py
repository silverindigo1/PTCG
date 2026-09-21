"""Manual import of legitimately obtained sales and listings.

Every automated source is either gated, unverified, or forbids the polling
pattern the product wants. That is a permanent condition for some of them, not
a temporary one, so the system needs a route that does not depend on any of
them. This is it: a documented CSV or JSON schema that a person fills from
records they are entitled to use, such as their own purchase and sale history,
an export a marketplace gives its own seller, or figures they transcribed by
hand from a page they were allowed to read.

Three properties are load-bearing.

**Idempotent.** A batch is identified by the hash of its content, not by when
it ran. Importing the same file twice inserts nothing the second time. Rows
carry the source's own identifier, so the same sale arriving in two overlapping
exports is one sale.

**Provenance.** Every row records who imported it, from where, and under what
evidence URL. An imported number with no traceable origin is indistinguishable
from an invented one, and this system's whole claim is that it never invents.

**Synthetic data is labelled, not hidden.** Demonstration rows are marked
``synthetic_demo`` at the row level and the source level, and the database
refuses to attribute a synthetic row to a real marketplace. Demo data that can
quietly reach a recommendation is worse than no demo data.

This module does no I/O. It parses, validates and hashes; the caller persists.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..types import (
    Currency,
    EuCondition,
    Grader,
    Language,
    Listing,
    Money,
    Sale,
)

__all__ = [
    "ImportProvenance",
    "ImportIssue",
    "ParsedImport",
    "SALE_COLUMNS",
    "LISTING_COLUMNS",
    "content_hash",
    "parse_sales",
    "parse_listings",
    "schema_help",
]

#: Required then optional. Documented here because this is the contract a human
#: fills in by hand, and an undocumented contract is a guessing game.
SALE_COLUMNS: Mapping[str, str] = {
    "external_id": "required. The source's own id for the transaction. Used for deduplication.",
    "variant_id": "required. Canonical variant this sale is evidence for.",
    "sold_at": "required. ISO 8601 date or datetime the transaction completed.",
    "price_amount": "required. Numeric, no thousands separators.",
    "price_currency": "required. JPY, EUR, DKK, USD or GBP.",
    "market": "required. JP, EU, US or GLOBAL. Where the transaction happened.",
    "language": "required. Card language: ja, en, de, fr, it, es, pt, ko, zh.",
    "condition": "optional. nm, ex, gd, lp, pl, po. Blank means unknown, which is modelled as unknown.",
    "condition_confidence": "optional. 0 to 1. Blank means 1. Zero is a real value and is preserved.",
    "graded": "optional. true/false. Default false.",
    "grader": "optional. psa, bgs, cgc, sgc, ace, tag. Required when graded is true.",
    "grade": "optional. Numeric grade. Required when graded is true.",
    "shipping_included": "optional. true/false. Default false.",
    "known_at": "optional. When this became knowable. Defaults to sold_at. Drives backtest correctness.",
    "source_url": "optional but strongly preferred. Where the figure can be checked.",
}

LISTING_COLUMNS: Mapping[str, str] = {
    "external_id": "required. The source's own listing id. Successive observations share it.",
    "variant_id": "required.",
    "observed_at": "required. ISO 8601 instant this price was seen.",
    "price_amount": "required.",
    "price_currency": "required.",
    "market": "required. JP, EU, US or GLOBAL.",
    "language": "required.",
    "condition": "optional.",
    "quantity": "optional. Default 1.",
    "graded": "optional. Default false.",
    "grader": "optional.",
    "grade": "optional.",
    "seller_id": "optional.",
    "source_url": "optional but strongly preferred.",
}


@dataclass(frozen=True, slots=True)
class ImportProvenance:
    """Who imported this, from where, and on whose authority."""

    source_id: str
    imported_by: str
    evidence_url: Optional[str] = None
    filename: Optional[str] = None
    note: Optional[str] = None
    synthetic: bool = False

    def problems(self) -> tuple[str, ...]:
        issues: list[str] = []
        if not self.source_id.strip():
            issues.append("source_id is required")
        if not self.imported_by.strip():
            issues.append(
                "imported_by is required: an imported figure with no named "
                "importer cannot be audited"
            )
        if not self.synthetic and not self.evidence_url:
            issues.append(
                "evidence_url is required for production imports so the figure "
                "can be checked against its origin"
            )
        return tuple(issues)

    @property
    def dataset(self) -> str:
        return "synthetic_demo" if self.synthetic else "production"


@dataclass(frozen=True, slots=True)
class ImportIssue:
    """One row that could not be used, and exactly why."""

    row_number: int
    field: Optional[str]
    message: str
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedImport:
    """Result of parsing one file. Never partially applied by accident."""

    provenance: ImportProvenance
    content_hash: str
    sales: tuple[Sale, ...] = ()
    listings: tuple[Listing, ...] = ()
    issues: tuple[ImportIssue, ...] = ()
    row_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        kind = "synthetic" if self.provenance.synthetic else "production"
        return (
            f"{self.row_count} row(s) read, {len(self.sales)} sale(s), "
            f"{len(self.listings)} listing(s), {len(self.issues)} rejected "
            f"[{kind}, hash {self.content_hash[:12]}]"
        )


def content_hash(payload: str | bytes) -> str:
    """Stable identity of a file's content. The idempotency key."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(data).hexdigest()


def schema_help(kind: str = "sales") -> str:
    """Human-readable column contract, for the CLI and the docs."""
    cols = SALE_COLUMNS if kind == "sales" else LISTING_COLUMNS
    width = max(len(c) for c in cols)
    lines = [f"Columns for a {kind} import (CSV header or JSON object keys):", ""]
    lines += [f"  {name:<{width}}  {desc}" for name, desc in cols.items()]
    return "\n".join(lines)


# ------------------------------------------------------------- conversions --

def _rows(payload: str) -> tuple[list[dict[str, Any]], Optional[str]]:
    text = payload.strip()
    if not text:
        return [], "file is empty"
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return [], f"invalid JSON: {exc}"
        if isinstance(data, dict):
            data = data.get("rows", [])
        if not isinstance(data, list):
            return [], "JSON must be a list of row objects, or {'rows': [...]}"
        return [dict(r) for r in data], None
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader], None


def _dec(value: Any, fieldname: str) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise ValueError(f"{fieldname}: not a number ({value!r})") from exc


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "t"}


def _dt(value: Any, fieldname: str) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{fieldname}: required")
    try:
        if len(text) == 10:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{fieldname}: not an ISO 8601 date or datetime ({text!r})") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _enum(cls, value: Any, fieldname: str, required: bool = True):
    text = (str(value).strip() if value is not None else "")
    if not text:
        if required:
            raise ValueError(f"{fieldname}: required")
        return None
    for candidate in (text, text.lower(), text.upper()):
        try:
            return cls(candidate)
        except ValueError:
            continue
    allowed = ", ".join(m.value for m in cls)
    raise ValueError(f"{fieldname}: {text!r} is not one of {allowed}")


_MARKETS = {"JP", "EU", "US", "GLOBAL"}


def _market(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text not in _MARKETS:
        raise ValueError(
            f"market: {text or 'blank'!r} is not one of {', '.join(sorted(_MARKETS))}"
        )
    return text


# ------------------------------------------------------------------ parsing --

def parse_sales(payload: str, provenance: ImportProvenance) -> ParsedImport:
    """Parse a sales file into ``Sale`` objects, or say precisely what is wrong.

    Nothing is silently coerced. A missing market is an error rather than a
    default, because guessing the market is how Japanese prices end up standing
    for European resale value.
    """
    digest = content_hash(payload)
    prov_issues = provenance.problems()
    rows, err = _rows(payload)
    issues: list[ImportIssue] = [
        ImportIssue(0, None, m) for m in prov_issues
    ]
    if err:
        issues.append(ImportIssue(0, None, err))
        return ParsedImport(provenance, digest, issues=tuple(issues))

    sales: list[Sale] = []
    seen: set[str] = set()

    for n, row in enumerate(rows, start=1):
        try:
            external_id = str(row.get("external_id") or "").strip()
            if not external_id:
                raise ValueError("external_id: required for deduplication")
            if external_id in seen:
                issues.append(ImportIssue(
                    n, "external_id",
                    f"duplicate external_id {external_id!r} within this file; "
                    "the second occurrence was dropped rather than counted twice",
                    row,
                ))
                continue
            variant_id = str(row.get("variant_id") or "").strip()
            if not variant_id:
                raise ValueError("variant_id: required")

            sold_at = _dt(row.get("sold_at"), "sold_at")
            known_raw = row.get("known_at")
            known_at = _dt(known_raw, "known_at") if str(known_raw or "").strip() else sold_at
            if known_at < sold_at:
                issues.append(ImportIssue(
                    n, "known_at",
                    "known_at is before sold_at; evidence cannot be known before "
                    "the transaction, so sold_at was used",
                    row,
                ))
                known_at = sold_at

            currency = _enum(Currency, row.get("price_currency"), "price_currency")
            amount = _dec(row.get("price_amount"), "price_amount")
            if amount <= 0:
                raise ValueError("price_amount: must be positive")

            graded = _bool(row.get("graded"))
            grader = _enum(Grader, row.get("grader"), "grader", required=graded)
            grade_raw = row.get("grade")
            grade = _dec(grade_raw, "grade") if str(grade_raw or "").strip() else None
            if graded and grade is None:
                raise ValueError("grade: required when graded is true")
            if not graded and (grader is not None or grade is not None):
                raise ValueError(
                    "grader/grade supplied but graded is false; a graded card and "
                    "a raw card are different assets and must not be conflated"
                )

            conf_raw = row.get("condition_confidence")
            if str(conf_raw or "").strip() == "":
                confidence = Decimal("1.0")
            else:
                confidence = _dec(conf_raw, "condition_confidence")
                if confidence < 0 or confidence > 1:
                    raise ValueError("condition_confidence: must be between 0 and 1")

            sales.append(Sale(
                sale_id=f"{provenance.source_id}:{external_id}",
                variant_id=variant_id,
                source_id=provenance.source_id,
                sold_at=sold_at,
                price=Money(amount, currency),
                condition=_enum(EuCondition, row.get("condition"), "condition", required=False),
                language=_enum(Language, row.get("language"), "language"),
                source_url=(str(row.get("source_url") or "").strip()
                            or provenance.evidence_url or None),
                is_graded=graded,
                grader=grader,
                grade=grade,
                shipping_included=_bool(row.get("shipping_included")),
                condition_confidence=confidence,
                market=_market(row.get("market")),
                external_id=external_id,
                known_at=known_at,
            ))
            seen.add(external_id)
        except ValueError as exc:
            message = str(exc)
            field_name = message.split(":", 1)[0] if ":" in message else None
            issues.append(ImportIssue(n, field_name, message, row))

    return ParsedImport(
        provenance=provenance,
        content_hash=digest,
        sales=tuple(sales),
        issues=tuple(issues),
        row_count=len(rows),
    )


def parse_listings(payload: str, provenance: ImportProvenance) -> ParsedImport:
    """Parse a listings file. Listings are never fair-value evidence."""
    digest = content_hash(payload)
    rows, err = _rows(payload)
    issues: list[ImportIssue] = [ImportIssue(0, None, m) for m in provenance.problems()]
    if err:
        issues.append(ImportIssue(0, None, err))
        return ParsedImport(provenance, digest, issues=tuple(issues))

    listings: list[Listing] = []
    for n, row in enumerate(rows, start=1):
        try:
            external_id = str(row.get("external_id") or "").strip()
            if not external_id:
                raise ValueError("external_id: required")
            variant_id = str(row.get("variant_id") or "").strip()
            if not variant_id:
                raise ValueError("variant_id: required")
            amount = _dec(row.get("price_amount"), "price_amount")
            if amount <= 0:
                raise ValueError("price_amount: must be positive")
            graded = _bool(row.get("graded"))
            grade_raw = row.get("grade")
            listings.append(Listing(
                listing_id=f"{provenance.source_id}:{external_id}",
                variant_id=variant_id,
                source_id=provenance.source_id,
                observed_at=_dt(row.get("observed_at"), "observed_at"),
                price=Money(amount, _enum(Currency, row.get("price_currency"),
                                          "price_currency")),
                condition=_enum(EuCondition, row.get("condition"), "condition", required=False),
                language=_enum(Language, row.get("language"), "language"),
                source_url=(str(row.get("source_url") or "").strip()
                            or provenance.evidence_url or None),
                seller_id=(str(row.get("seller_id") or "").strip() or None),
                is_graded=graded,
                grader=_enum(Grader, row.get("grader"), "grader", required=graded),
                grade=_dec(grade_raw, "grade") if str(grade_raw or "").strip() else None,
                quantity=int(row.get("quantity") or 1),
                market=_market(row.get("market")),
                external_id=external_id,
            ))
        except (ValueError, TypeError) as exc:
            message = str(exc)
            field_name = message.split(":", 1)[0] if ":" in message else None
            issues.append(ImportIssue(n, field_name, message, row))

    return ParsedImport(
        provenance=provenance,
        content_hash=digest,
        listings=tuple(listings),
        issues=tuple(issues),
        row_count=len(rows),
    )
