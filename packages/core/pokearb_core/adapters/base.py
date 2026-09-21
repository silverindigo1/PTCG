"""Adapter framework and the compliance gate.

Every marketplace gets its own adapter converting source payloads into the
shared internal schema. Before any adapter can issue a request, its
``SourcePolicy`` must be filled in and marked verified.

The gate is not bureaucracy. The default is ``enabled=False`` with
``verification_status=UNVERIFIED``, and the collector refuses to run an
unverified source. That forces the robots and terms check to happen *before*
the first request rather than after a block, which is the difference between a
compliant system and one that finds out the hard way.

Cardmarket is the concrete case. Its documentation forbids Dedicated App users
from continuously polling public marketplace resources on consecutive days, so
``CardmarketAdapter`` additionally refuses continuous public polling when the
credential type is Dedicated, no matter what the policy row says.
"""

from __future__ import annotations

import abc
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..types import FxRate, Listing, PopulationSnapshot, Sale

__all__ = [
    "VerificationStatus",
    "SourcePolicy",
    "RawRecord",
    "SourceDisabledError",
    "PolicyViolationError",
    "Adapter",
    "CatalogueSource",
    "SalesSource",
    "ListingSource",
    "PopulationSource",
    "FxSource",
]


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED_API = "verified_api"
    VERIFIED_PERMITTED = "verified_permitted"
    BLOCKED = "blocked"


class SourceDisabledError(RuntimeError):
    """The source has not been verified and enabled."""


class PolicyViolationError(RuntimeError):
    """The requested access pattern is forbidden by the source's own terms."""


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    """Compliance record for one source. No adapter runs without one."""

    source_id: str
    display_name: str
    base_url: str
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    enabled: bool = False
    has_official_api: Optional[bool] = None
    api_docs_url: Optional[str] = None
    robots_checked_on: Optional[date] = None
    robots_allows_paths: Optional[bool] = None
    terms_reviewed_on: Optional[date] = None
    terms_url: Optional[str] = None
    #: Hard request budget. The scheduler treats this as a ceiling, not a target.
    max_requests_per_minute: int = 6
    min_seconds_between_requests: Decimal = Decimal("10")
    #: Access patterns this source explicitly forbids, by name.
    forbidden_patterns: tuple[str, ...] = ()
    credentials_required: tuple[str, ...] = ()
    notes: Optional[str] = None

    def assert_runnable(self) -> None:
        if not self.enabled:
            raise SourceDisabledError(
                f"Source '{self.source_id}' is disabled. Enabling it requires "
                "filling in robots_checked_on, terms_reviewed_on and setting "
                "verification_status away from UNVERIFIED."
            )
        if self.verification_status in (
            VerificationStatus.UNVERIFIED,
            VerificationStatus.BLOCKED,
        ):
            raise SourceDisabledError(
                f"Source '{self.source_id}' has verification_status="
                f"{self.verification_status.value}; refusing to issue requests."
            )
        if self.verification_status is VerificationStatus.VERIFIED_PERMITTED:
            if self.robots_checked_on is None or self.terms_reviewed_on is None:
                raise SourceDisabledError(
                    f"Source '{self.source_id}' claims permitted access but is "
                    "missing a robots or terms review date."
                )
            if self.robots_allows_paths is False:
                raise PolicyViolationError(
                    f"robots rules for '{self.source_id}' disallow the configured paths."
                )

    def assert_pattern_allowed(self, pattern: str) -> None:
        if pattern in self.forbidden_patterns:
            raise PolicyViolationError(
                f"Access pattern '{pattern}' is forbidden by the terms of "
                f"'{self.source_id}'. See {self.terms_url or self.api_docs_url}."
            )


@dataclass(frozen=True, slots=True)
class RawRecord:
    """Verbatim source payload, stored before any parsing.

    Keeping the raw payload is what lets improved matching logic be replayed
    over years of history without refetching anything, which the brief asks for
    explicitly.
    """

    source_id: str
    fetched_at: datetime
    request_url: str
    payload: Any
    content_hash: str = ""
    http_status: Optional[int] = None

    @staticmethod
    def hash_payload(payload: Any) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def with_hash(self) -> "RawRecord":
        return RawRecord(
            self.source_id, self.fetched_at, self.request_url, self.payload,
            self.hash_payload(self.payload), self.http_status,
        )


class Adapter(abc.ABC):
    """Base adapter. Subclasses convert payloads into the internal schema."""

    def __init__(self, policy: SourcePolicy) -> None:
        self.policy = policy

    @property
    def source_id(self) -> str:
        return self.policy.source_id

    def _guard(self, pattern: str = "default") -> None:
        self.policy.assert_runnable()
        self.policy.assert_pattern_allowed(pattern)

    @abc.abstractmethod
    def capabilities(self) -> Mapping[str, bool]:
        """What this adapter can actually supply, given current access."""


class CatalogueSource(Adapter):
    @abc.abstractmethod
    def fetch_sets(self) -> Sequence[RawRecord]: ...

    @abc.abstractmethod
    def fetch_cards(self, set_code: str) -> Sequence[RawRecord]: ...


class SalesSource(Adapter):
    @abc.abstractmethod
    def fetch_sales(self, query: str, since: datetime) -> Sequence[Sale]: ...


class ListingSource(Adapter):
    @abc.abstractmethod
    def fetch_listings(self, query: str) -> Sequence[Listing]: ...


class PopulationSource(Adapter):
    @abc.abstractmethod
    def fetch_population(self, reference: str) -> Optional[PopulationSnapshot]: ...


class FxSource(Adapter):
    @abc.abstractmethod
    def fetch_rates(self, on: Optional[date] = None) -> Sequence[FxRate]: ...
