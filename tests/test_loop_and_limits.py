"""Tests for the monitoring loop, alert de-duplication and rate limiting.

Acceptance Test 10 lives here: a watchlist trigger sends ONE alert and does not
repeat unless the market situation actually changes.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "worker"))

from app.loop import (  # noqa: E402
    MonitoringLoop,
    StageResult,
    StageStatus,
    material_change,
)
from app.scheduler import RateBudget, RateLimiter  # noqa: E402


# --------------------------------------------------------------- Test 10 ----
def test_10_identical_state_does_not_realert():
    state = {
        "price": "14800", "ranking_roi": "0.82", "eu_supply": "9",
        "n_sales": 7, "condition_fingerprint": "A-|photos:3",
    }
    should, reasons = material_change(state, dict(state))
    assert should is False
    assert reasons == []


def test_10b_noise_level_movement_does_not_realert():
    prev = {"price": "14800", "ranking_roi": "0.82", "eu_supply": "9", "n_sales": 7}
    cur = {"price": "14650", "ranking_roi": "0.84", "eu_supply": "10", "n_sales": 8}
    should, _ = material_change(prev, cur)
    assert should is False, "a 1% price move and one extra sale is not news"


@pytest.mark.parametrize(
    "cur,expected_fragment",
    [
        ({"price": "13000"}, "price dropped"),
        ({"ranking_roi": "0.95"}, "ROI improved"),
        ({"eu_supply": "5"}, "supply fell"),
        ({"n_sales": 10}, "new completed sales"),
        ({"condition_fingerprint": "A-|photos:6"}, "condition information changed"),
    ],
)
def test_10c_material_movement_realerts(cur, expected_fragment):
    prev = {
        "price": "14800", "ranking_roi": "0.82", "eu_supply": "9",
        "n_sales": 7, "condition_fingerprint": "A-|photos:3",
    }
    should, reasons = material_change(prev, {**prev, **cur})
    assert should is True
    assert any(expected_fragment in r for r in reasons)


# --------------------------------------------------------------- Test 11 ----
def test_11_failing_stage_does_not_kill_the_cycle():
    def ok(cycle_id: str) -> StageResult:
        return StageResult("fetch_new_sales", StageStatus.OK, records_out=5)

    def boom(cycle_id: str) -> StageResult:
        raise RuntimeError("source timed out")

    loop = MonitoringLoop({"fetch_new_sales": ok, "update_exchange_rates": boom})
    report = loop.run_cycle()

    assert report.finished_at is not None
    assert len(report.stages) == 20, "every stage is accounted for"
    failed = [s for s in report.stages if s.status is StageStatus.FAILED]
    assert len(failed) == 1
    assert "source timed out" in failed[0].detail
    assert report.count(StageStatus.OK) == 1
    assert report.status == "failed"


def test_unwired_stage_is_reported_as_unwired_never_as_ok():
    """The reported defect: handlers={} printed 20 stages ok.

    An empty handler map means nothing ran. A report that calls that success
    is the reason nobody notices a dead pipeline.
    """
    loop = MonitoringLoop({})
    report = loop.run_cycle()

    assert report.count(StageStatus.OK) == 0
    assert report.count(StageStatus.UNWIRED) == 20
    assert report.status == "degraded"
    summary = report.summary()
    assert "20 unwired" in summary
    assert "0 ok" in summary
    assert not any(s.status is StageStatus.OK for s in report.stages)


def test_partial_write_is_reported_as_partial_not_as_success():
    def half(cycle_id: str) -> StageResult:
        return StageResult(
            "fetch_new_sales", StageStatus.OK, records_in=10, records_out=4,
            partial=True, detail="4 of 10 rows written before the source cut off",
        )

    report = MonitoringLoop({"fetch_new_sales": half}).run_cycle()
    assert report.status == "degraded"
    assert "partial writes in fetch_new_sales" in report.summary()


def test_completed_stages_are_not_repeated_on_a_retry():
    calls = {"n": 0}

    def once(cycle_id: str) -> StageResult:
        calls["n"] += 1
        return StageResult("fetch_new_sales", StageStatus.OK, records_out=1)

    class Store:
        def __init__(self):
            self.done = set()

        def begin(self, cycle_id, started_at):
            pass

        def completed_stages(self, cycle_id):
            return set(self.done)

        def record(self, cycle_id, result):
            if result.status is StageStatus.OK:
                self.done.add(result.stage)

        def finish(self, cycle_id, report):
            pass

    store = Store()
    loop = MonitoringLoop({"fetch_new_sales": once}, store=store)
    loop.run_cycle("11111111-1111-1111-1111-111111111111")
    second = loop.run_cycle("11111111-1111-1111-1111-111111111111")

    assert calls["n"] == 1, "a retry must not repeat completed work"
    repeated = next(s for s in second.stages if s.stage == "fetch_new_sales")
    assert repeated.status is StageStatus.SKIPPED


# ------------------------------------------------------------ rate limits ----
def test_rate_limiter_enforces_minimum_gap():
    limiter = RateLimiter()
    budget = RateBudget("cardrush", max_requests_per_minute=4, min_seconds_between_requests=Decimal("15"))
    assert limiter.acquire(budget, now=0.0) == 0.0
    wait = limiter.acquire(budget, now=5.0)
    assert wait == pytest.approx(10.0)


def test_rate_limiter_enforces_per_minute_ceiling():
    limiter = RateLimiter()
    budget = RateBudget("yuyutei", max_requests_per_minute=3, min_seconds_between_requests=Decimal("1"))
    for i in range(3):
        assert limiter.acquire(budget, now=float(i * 2)) == 0.0
    wait = limiter.acquire(budget, now=6.0)
    assert wait > 0, "fourth request inside the window must wait"
