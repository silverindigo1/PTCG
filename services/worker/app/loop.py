"""Autonomous monitoring loop.

The 20 stages from the brief, as an idempotent pipeline keyed on ``cycle_id``.

The previous version reported every stage as ``ok`` with a ``degraded`` flag
when no handler was wired, so a worker started with ``handlers={}`` printed
"20 stages ok" while doing nothing at all. A pipeline that reports success for
work it did not do is worse than one that crashes, because nobody investigates
it. Stage status is now four-valued and counted separately:

``ok``
    The handler ran and finished.
``skipped``
    The handler ran and had nothing to do, for a stated reason, for example a
    source disabled pending compliance review.
``unwired``
    No handler exists. A configuration gap, never a success.
``failed``
    The handler raised. The cycle continues; anything downstream sees stale
    data, which lowers confidence, which may suppress an opportunity. Nothing
    is substituted.

A stage that wrote some of its rows and then failed sets ``partial``, and the
report says so rather than reporting success.

Cycles are resumable: stage results are persisted under ``cycle_id``, so a
re-run skips stages that already completed instead of duplicating their writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

log = logging.getLogger("pokearb.loop")

__all__ = [
    "STAGES",
    "StageStatus",
    "StageResult",
    "CycleReport",
    "CycleStore",
    "MonitoringLoop",
    "material_change",
    "material_fingerprint",
]


class StageStatus(str, Enum):
    OK = "ok"
    SKIPPED = "skipped"
    UNWIRED = "unwired"
    FAILED = "failed"


STAGES: tuple[str, ...] = (
    "fetch_new_listings",
    "fetch_listing_updates",
    "fetch_new_sales",
    "fetch_population_changes",
    "update_exchange_rates",
    "normalize_records",
    "match_to_canonical_cards",
    "validate_variant_identity",
    "estimate_condition",
    "recalculate_fair_value",
    "recalculate_arbitrage",
    "recalculate_liquidity",
    "recalculate_supply",
    "recalculate_demand",
    "recalculate_opportunity_scores",
    "compare_with_prior_snapshots",
    "identify_anomalies",
    "identify_new_opportunities",
    "trigger_alerts",
    "store_observations",
)


@dataclass(slots=True)
class StageResult:
    stage: str
    status: StageStatus
    records_in: int = 0
    records_out: int = 0
    partial: bool = False
    detail: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @property
    def ok(self) -> bool:
        return self.status is StageStatus.OK

    def line(self) -> str:
        bits = [f"{self.stage}: {self.status.value}"]
        if self.records_in or self.records_out:
            bits.append(f"in={self.records_in} out={self.records_out}")
        if self.partial:
            bits.append("PARTIAL WRITE")
        if self.detail:
            bits.append(self.detail)
        return "  " + " | ".join(bits)


@dataclass(slots=True)
class CycleReport:
    cycle_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    stages: list[StageResult] = field(default_factory=list)

    def count(self, status: StageStatus) -> int:
        return sum(1 for s in self.stages if s.status is status)

    @property
    def status(self) -> str:
        if self.count(StageStatus.FAILED):
            return "failed"
        if self.count(StageStatus.UNWIRED) or any(s.partial for s in self.stages):
            return "degraded"
        return "ok"

    def summary(self) -> str:
        """An accurate one-liner. It does not say ok for work not done."""
        head = (
            f"cycle {self.cycle_id[:8]} {self.status}: "
            f"{self.count(StageStatus.OK)} ok, "
            f"{self.count(StageStatus.SKIPPED)} skipped, "
            f"{self.count(StageStatus.UNWIRED)} unwired, "
            f"{self.count(StageStatus.FAILED)} failed "
            f"of {len(self.stages)} stage(s)"
        )
        partial = [s.stage for s in self.stages if s.partial]
        if partial:
            head += f"; partial writes in {', '.join(partial)}"
        return "\n".join([head, *(s.line() for s in self.stages)])


class CycleStore(Protocol):
    """Persistence for cycle status. Optional; the loop runs without one."""

    def begin(self, cycle_id: str, started_at: datetime) -> None: ...

    def completed_stages(self, cycle_id: str) -> set: ...

    def record(self, cycle_id: str, result: StageResult) -> None: ...

    def finish(self, cycle_id: str, report: CycleReport) -> None: ...


#: A handler takes the cycle id and returns a result. Raising is allowed and is
#: recorded as a failure rather than killing the cycle.
Handler = Callable[[str], StageResult]


class MonitoringLoop:
    def __init__(
        self,
        handlers: Mapping[str, Handler],
        store: Optional[CycleStore] = None,
        stages: Sequence[str] = STAGES,
    ) -> None:
        self.handlers = dict(handlers)
        self.store = store
        self.stages = tuple(stages)
        unknown = set(self.handlers) - set(self.stages)
        if unknown:
            raise ValueError(
                "handlers supplied for stages that do not exist: "
                + ", ".join(sorted(unknown))
            )

    def run_cycle(self, cycle_id: Optional[str] = None) -> CycleReport:
        cycle_id = cycle_id or str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        report = CycleReport(cycle_id, started)

        done: set = set()
        if self.store is not None:
            self.store.begin(cycle_id, started)
            done = self.store.completed_stages(cycle_id)

        for stage in self.stages:
            if stage in done:
                report.stages.append(
                    StageResult(
                        stage, StageStatus.SKIPPED,
                        detail="already completed in this cycle; not repeated",
                    )
                )
                continue

            handler = self.handlers.get(stage)
            if handler is None:
                report.stages.append(
                    StageResult(
                        stage, StageStatus.UNWIRED,
                        detail="no handler configured for this stage",
                    )
                )
                continue

            began = datetime.now(timezone.utc)
            try:
                result = handler(cycle_id)
                result.started_at = result.started_at or began
                result.finished_at = result.finished_at or datetime.now(timezone.utc)
            except Exception as exc:  # noqa: BLE001 - a failing stage must not kill the cycle
                log.exception("stage %s failed", stage)
                result = StageResult(
                    stage, StageStatus.FAILED, detail=str(exc),
                    started_at=began, finished_at=datetime.now(timezone.utc),
                )
            report.stages.append(result)
            if self.store is not None:
                try:
                    self.store.record(cycle_id, result)
                except Exception:  # noqa: BLE001
                    log.exception("could not persist stage %s", stage)

        report.finished_at = datetime.now(timezone.utc)
        if self.store is not None:
            try:
                self.store.finish(cycle_id, report)
            except Exception:  # noqa: BLE001
                log.exception("could not persist cycle %s", cycle_id)
        log.info(report.summary())
        return report


# -------------------------------------------------------------- alerting ----

def material_fingerprint(state: Mapping[str, Any]) -> str:
    """Stable key for one material state, used for database-level dedupe."""
    canonical = json.dumps(
        {k: (str(v) if isinstance(v, Decimal) else v) for k, v in sorted(state.items())},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def material_change(previous: dict, current: dict) -> tuple:
    """Should an already-alerted opportunity alert again?

    Returns ``(should_alert, reasons)``. Silence is the default.
    """
    reasons: list = []

    def num(d: dict, k: str):
        v = d.get(k)
        return Decimal(str(v)) if v is not None else None

    prev_price, cur_price = num(previous, "price"), num(current, "price")
    if prev_price and cur_price and prev_price > 0:
        drop = (prev_price - cur_price) / prev_price
        if drop >= Decimal("0.05"):
            reasons.append(f"price dropped {drop:.1%}")

    prev_roi, cur_roi = num(previous, "ranking_roi"), num(current, "ranking_roi")
    if prev_roi is not None and cur_roi is not None and cur_roi - prev_roi >= Decimal("0.10"):
        reasons.append(f"risk-adjusted ROI improved by {cur_roi - prev_roi:.1%}")

    prev_sup, cur_sup = num(previous, "eu_supply"), num(current, "eu_supply")
    if prev_sup and cur_sup and prev_sup > 0:
        change = abs(cur_sup - prev_sup) / prev_sup
        if change >= Decimal("0.30"):
            direction = "fell" if cur_sup < prev_sup else "rose"
            reasons.append(f"European supply {direction} {change:.0%}")

    prev_n, cur_n = previous.get("n_sales", 0), current.get("n_sales", 0)
    if cur_n - prev_n >= 2:
        reasons.append(f"{cur_n - prev_n} new completed sales improve confidence")

    if previous.get("condition_fingerprint") != current.get("condition_fingerprint"):
        reasons.append("condition information changed")

    return bool(reasons), reasons
