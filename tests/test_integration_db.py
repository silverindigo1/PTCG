"""Integration tests: real PostgreSQL, import through to Quick Check.

These exercise the layer the unit tests deliberately avoid. Most of the
reported defects lived exactly here, in the translation between a database row
and a value object: a graded sale flattened into a raw one, a condition
confidence of zero read as certainty, a listing price read from the wrong point
in its history. None of those are visible to a test that builds its objects in
Python.

Skipped, loudly, when no database is reachable. A skipped integration test is
honest; a unit test pretending to be one is not.

    DATABASE_URL=postgresql://... python -m pytest tests/test_integration_db.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "core"))
sys.path.insert(0, str(ROOT / "tests"))

psycopg = pytest.importorskip("psycopg", reason="psycopg is required for integration tests")

DSN = os.environ.get("DATABASE_URL") or os.environ.get(
    "POKEARB_TEST_DSN", "postgresql://postgres@/pokearb?host=/tmp&port=5433"
)


def _reachable(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(DSN),
    reason=f"no database reachable at {DSN}; run make db-up first",
)

from _packages import api, worker  # noqa: E402

_pipeline_mod = worker("pipeline")
_loop_mod = worker("loop")
_store_mod = worker("store")
Pipeline = _pipeline_mod.Pipeline
PipelineConfig = _pipeline_mod.PipelineConfig
MonitoringLoop = _loop_mod.MonitoringLoop
StageStatus = _loop_mod.StageStatus
PipelineStore = _store_mod.PipelineStore
Repository = api("repository").Repository
from pokearb_core.arbitrage.engine import CostAssumptions  # noqa: E402
from pokearb_core.ingest import ImportProvenance, parse_sales  # noqa: E402
from pokearb_core.types import (  # noqa: E402
    AcquisitionPurpose,
    Currency,
    Grader,
    Language,
    Scenario,
)

SOURCE = "demo_eu_sales"
EVIDENCE = "https://demo.invalid/integration"
HEADER = (
    "external_id,variant_id,sold_at,price_amount,price_currency,market,"
    "language,condition,condition_confidence,graded,grader,grade,source_url\n"
)


@pytest.fixture
def store():
    s = PipelineStore(DSN)
    yield s
    s.close()


_TABLES = (
    "alert, opportunity_snapshot, opportunity, fair_value_input, "
    "fair_value_snapshot, listing_observation, listing, sale, import_batch, "
    "cycle_stage, cycle, raw_record"
)


def _truncate(store) -> None:
    store.conn.rollback()
    with store.conn.cursor() as cur:
        cur.execute(f"TRUNCATE {_TABLES} CASCADE")
    store.conn.commit()


@pytest.fixture
def clean(store):
    """Empty before and after. A test that leaves rows behind changes what the
    next command sees, which is how a demo run after the suite once printed
    alerts that the tests, not the demo, had raised."""
    _truncate(store)
    yield store
    _truncate(store)


@pytest.fixture
def variant(clean):
    with clean.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO card_variant
              (canonical_key, language, set_code, number, printing, edition, name_en)
            VALUES (%s,'ja','SM-P','114','holo','n/a','Pikachu')
            ON CONFLICT (canonical_key) DO UPDATE SET number = EXCLUDED.number
            RETURNING variant_id
            """,
            (f"ja|SM-P|114|holo|n/a||test-{uuid.uuid4().hex[:8]}",),
        )
        vid = str(cur.fetchone()["variant_id"])
    clean.conn.commit()
    return vid


@pytest.fixture
def fx(clean):
    rows = [
        ("2026-09-18", "JPY", "EUR", "0.0055266900"),
        ("2026-09-18", "EUR", "JPY", "180.9400000000"),
        ("2026-09-18", "EUR", "DKK", "7.4754000000"),
    ]
    with clean.conn.cursor() as cur:
        for as_of, base, quote, rate in rows:
            cur.execute(
                "INSERT INTO fx_rate (as_of, base, quote, rate, source_url) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (as_of, base, quote, rate,
                 "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"),
            )
    clean.conn.commit()


def _prov(**kw):
    base = dict(source_id=SOURCE, imported_by="integration-test",
                evidence_url=EVIDENCE, synthetic=True)
    base.update(kw)
    return ImportProvenance(**base)


def _csv(variant_id: str, now: datetime, n: int = 5, start: int = 0) -> str:
    rows = []
    for i in range(n):
        sold = (now - timedelta(days=4 + i * 7)).date().isoformat()
        rows.append(
            f"eu-{start + i},{variant_id},{sold},{115 + i}.00,EUR,EU,ja,nm,,false,,,"
            f"{EVIDENCE}#{start + i}"
        )
    return HEADER + "\n".join(rows) + "\n"


def _run(store, pipeline) -> object:
    return MonitoringLoop(pipeline.handlers(), store=store).run_cycle(str(uuid.uuid4()))


def _pipeline(store, variant_id, now, price: str = "6500", **cfg_kw):
    cfg = PipelineConfig(
        resale_market="EU",
        dataset="synthetic_demo",
        purchase_prices_jpy={variant_id: Decimal(price)},
        assumptions=CostAssumptions(
            scenario=Scenario.HAND_CARRY,
            acquisition_purpose=AcquisitionPurpose.PERSONAL,
            zero_logistics_cost_is_verified=True,
        ),
        **cfg_kw,
    )
    p = Pipeline(store, cfg)
    p.as_of = now
    return p


# ------------------------------------------------------------- the workflow --

def test_full_workflow_import_to_snapshot_and_alert(clean, variant, fx):
    """One complete run: raw import, valuation, snapshot, alert decision."""
    now = datetime.now(timezone.utc)
    parsed = parse_sales(_csv(variant, now), _prov())
    assert parsed.ok

    pipeline = _pipeline(clean, variant, now)
    pipeline.queue_import(parsed, request_url=EVIDENCE)
    report = _run(clean, pipeline)

    assert report.count(StageStatus.FAILED) == 0, report.summary()
    assert report.count(StageStatus.OK) >= 9

    with clean.conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM raw_record")
        assert cur.fetchone()["n"] == 1, "the payload is persisted before parsing"
        cur.execute("SELECT count(*) AS n FROM sale")
        assert cur.fetchone()["n"] == 5
        cur.execute("SELECT * FROM fair_value_snapshot ORDER BY computed_at DESC LIMIT 1")
        fv = cur.fetchone()
        assert fv["sufficient"] is True
        assert fv["grade_bucket"] == "raw"
        assert fv["market"] == "EU"
        cur.execute("SELECT count(*) AS n FROM fair_value_input WHERE included")
        inputs = cur.fetchone()["n"]
        assert inputs == fv["n_sales"], (
            "every sale behind the value is named, and only those"
        )
        assert inputs >= 3, "the evidence minimum was met"
        cur.execute("SELECT * FROM opportunity_snapshot ORDER BY computed_at DESC LIMIT 1")
        snap = cur.fetchone()

    assert snap["model_version"]
    assert snap["as_of"] is not None
    assert snap["fx_rates_used"], "FX used must be recorded for replay"
    assert snap["cost_assumptions"], "cost assumptions must be recorded for replay"
    assert snap["policy_sources"], "the policy rows behind the cost must be named"
    assert snap["evidence_sale_ids"], "the sales behind the value must be named"
    assert snap["capital_basis"]
    assert snap["upfront_cash_eur"] >= snap["landed_cost_eur"]


def test_reimporting_the_same_file_changes_nothing(clean, variant, fx):
    now = datetime.now(timezone.utc)
    payload = _csv(variant, now)

    first = _pipeline(clean, variant, now)
    first.queue_import(parse_sales(payload, _prov()), request_url=EVIDENCE)
    _run(clean, first)

    with clean.conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM sale")
        before = cur.fetchone()["n"]

    second = _pipeline(clean, variant, now)
    second.queue_import(parse_sales(payload, _prov()), request_url=EVIDENCE)
    report = _run(clean, second)

    with clean.conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM sale")
        after = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM raw_record")
        raws = cur.fetchone()["n"]

    assert after == before, "a repeated import must not manufacture evidence"
    assert raws == 1
    ingest = next(s for s in report.stages if s.stage == "fetch_new_sales")
    assert ingest.records_out == 0


def test_a_retried_cycle_does_not_repeat_completed_stages(clean, variant, fx):
    now = datetime.now(timezone.utc)
    cycle_id = str(uuid.uuid4())

    first = _pipeline(clean, variant, now)
    first.queue_import(parse_sales(_csv(variant, now), _prov()), request_url=EVIDENCE)
    MonitoringLoop(first.handlers(), store=clean).run_cycle(cycle_id)

    second = _pipeline(clean, variant, now)
    second.queue_import(parse_sales(_csv(variant, now), _prov()), request_url=EVIDENCE)
    report = MonitoringLoop(second.handlers(), store=clean).run_cycle(cycle_id)

    ingest = next(s for s in report.stages if s.stage == "fetch_new_sales")
    assert ingest.status is StageStatus.SKIPPED
    assert "already completed" in ingest.detail


def test_one_material_state_produces_one_alert(clean, variant, fx):
    now = datetime.now(timezone.utc)
    for _ in range(2):
        p = _pipeline(clean, variant, now)
        p.queue_import(parse_sales(_csv(variant, now), _prov()), request_url=EVIDENCE)
        _run(clean, p)

    with clean.conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM alert")
        assert cur.fetchone()["n"] <= 1, "the same material state must not realert"


def test_failing_stage_leaves_the_rest_of_the_cycle_usable(clean, variant, fx):
    now = datetime.now(timezone.utc)
    pipeline = _pipeline(clean, variant, now)
    pipeline.queue_import(parse_sales(_csv(variant, now), _prov()), request_url=EVIDENCE)

    handlers = dict(pipeline.handlers())

    def boom(cycle_id: str):
        raise RuntimeError("simulated source outage")

    handlers["update_exchange_rates"] = boom
    report = MonitoringLoop(handlers, store=clean).run_cycle(str(uuid.uuid4()))

    assert report.count(StageStatus.FAILED) == 1
    assert report.status == "failed"
    # Downstream stages degrade rather than substituting a rate.
    fv_stage = next(s for s in report.stages if s.stage == "recalculate_fair_value")
    assert fv_stage.status is StageStatus.SKIPPED
    assert "FX" in (fv_stage.detail or "")

    with clean.conn.cursor() as cur:
        cur.execute("SELECT status FROM cycle ORDER BY started_at DESC LIMIT 1")
        assert cur.fetchone()["status"] == "failed"


# ------------------------------------------------- database to value object --

def test_repository_preserves_grading_and_zero_confidence(clean, variant, fx):
    """The two reported translation bugs, checked through real SQL."""
    with clean.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO graded_item (variant_id, grading_company, grade, cert_number) "
            "VALUES (%s::uuid, 'PSA', 10, %s) RETURNING graded_item_id",
            (variant, uuid.uuid4().hex),
        )
        gid = cur.fetchone()["graded_item_id"]
        cur.execute(
            """
            INSERT INTO sale (source_id, external_id, variant_id, graded_item_id,
                              sold_at, price_amount, price_currency, condition,
                              condition_confidence, language, market, dataset,
                              observed_at)
            VALUES (%s,'g-1',%s::uuid,%s, now() - interval '3 days', 1800, 'EUR',
                    NULL, 0, 'ja', 'EU', 'synthetic_demo', now())
            """,
            (SOURCE, variant, gid),
        )
        cur.execute(
            """
            INSERT INTO sale (source_id, external_id, variant_id, sold_at,
                              price_amount, price_currency, condition,
                              condition_confidence, language, market, dataset,
                              observed_at)
            VALUES (%s,'r-1',%s::uuid, now() - interval '2 days', 120, 'EUR',
                    'NM', 0, 'ja', 'EU', 'synthetic_demo', now())
            """,
            (SOURCE, variant),
        )
    clean.conn.commit()

    async def run():
        repo = Repository(DSN)
        await repo.connect()
        raw = await repo.sales_for(variant, graded=False)
        graded = await repo.sales_for(variant, graded=True)
        return raw, graded

    raw, graded = asyncio.run(run())

    assert [s.external_id for s in raw] == ["r-1"], "a graded sale is not a raw sale"
    assert raw[0].is_graded is False
    assert raw[0].condition_confidence == Decimal("0"), (
        "a confidence of zero must not be read as certainty"
    )
    assert len(graded) == 1
    assert graded[0].is_graded is True
    assert graded[0].grader is Grader.PSA
    assert graded[0].grade == Decimal("10")
    assert graded[0].grade_bucket != "raw"


def test_listing_price_history_is_preserved_and_read_at_the_right_time(clean, variant):
    """The reported defect: a price drop updated the listing but not history."""
    now = datetime.now(timezone.utc)
    with clean.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO listing (source_id, external_id, variant_id, first_seen_at,
                                 last_seen_at, condition, language, quantity,
                                 market, dataset)
            VALUES (%s,'l-1',%s::uuid,%s,%s,'NM','ja',1,'EU','synthetic_demo')
            RETURNING listing_id
            """,
            (SOURCE, variant, now - timedelta(days=5), now),
        )
        lid = cur.fetchone()["listing_id"]
        for when, price in (
            (now - timedelta(days=5), "14800"),
            (now - timedelta(days=1), "10000"),
        ):
            cur.execute(
                "INSERT INTO listing_observation (listing_id, observed_at, "
                "price_amount, price_currency) VALUES (%s,%s,%s,'JPY')",
                (lid, when, price),
            )
    clean.conn.commit()

    async def run():
        repo = Repository(DSN)
        await repo.connect()
        latest = await repo.active_listings_for(variant, market="EU", as_of=now)
        earlier = await repo.active_listings_for(
            variant, market="EU", as_of=now - timedelta(days=3)
        )
        return latest, earlier

    latest, earlier = asyncio.run(run())

    assert latest and latest[0].price.amount == Decimal("10000"), (
        "the current read must see the repriced value"
    )
    assert earlier and earlier[0].price.amount == Decimal("14800"), (
        "a historical read must see the price that was in force then"
    )
    assert latest[0].external_id == earlier[0].external_id == "l-1", (
        "successive observations are one listing, not two independent offers"
    )


def test_japanese_rows_are_not_returned_as_european_evidence(clean, variant, fx):
    with clean.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sale (source_id, external_id, variant_id, sold_at,
                              price_amount, price_currency, condition, language,
                              market, dataset, observed_at)
            VALUES ('demo_jp_shop','jp-1',%s::uuid, now() - interval '2 days',
                    6500,'JPY','NM','ja','JP','synthetic_demo', now())
            """,
            (variant,),
        )
        cur.execute(
            """
            INSERT INTO sale (source_id, external_id, variant_id, sold_at,
                              price_amount, price_currency, condition, language,
                              market, dataset, observed_at)
            VALUES (%s,'eu-1',%s::uuid, now() - interval '2 days',
                    120,'EUR','NM','ja','EU','synthetic_demo', now())
            """,
            (SOURCE, variant),
        )
    clean.conn.commit()

    async def run():
        repo = Repository(DSN)
        await repo.connect()
        return await repo.sales_for(variant, market="EU")

    eu = asyncio.run(run())
    assert [s.external_id for s in eu] == ["eu-1"]


def test_synthetic_rows_cannot_be_attributed_to_a_production_source(clean, variant):
    """The database refuses, not the application."""
    with clean.conn.cursor() as cur:
        cur.execute("SELECT source_id FROM source WHERE dataset='production' LIMIT 1")
        production_source = cur.fetchone()["source_id"]
    with pytest.raises(psycopg.errors.RaiseException):
        with clean.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sale (source_id, external_id, variant_id, sold_at,
                                  price_amount, price_currency, language, market,
                                  dataset, observed_at)
                VALUES (%s,'x-1',%s::uuid, now(), 100,'EUR','ja','EU',
                        'synthetic_demo', now())
                """,
                (production_source, variant),
            )
    clean.conn.rollback()


# ---------------------------------------------------- database to verdict --

def _quick_check(variant_id: str, **overrides):
    """Run Quick Check the way the endpoint does, against the real database."""
    deps = api("deps")
    schemas = api("schemas")
    req_kw = dict(
        raw_title="ピカチュウ 114/SM-P",
        price_jpy=Decimal("6500"),
        set_code="SM-P",
        number="114",
        language="ja",
        printing="holo",
        scenario="A_hand_carry",
        acquisition_purpose="personal",
        required_roi=Decimal("0.40"),
        zero_logistics_cost_is_verified=True,
    )
    req_kw.update(overrides)
    req = schemas.QuickCheckRequest(**req_kw)

    async def run():
        repo = Repository(DSN)
        await repo.connect()
        ctx = deps.Context(repo)
        match = await ctx.resolve_identity(req)
        if match.best is None or match.outcome.value != "auto":
            return req, match, None
        return req, match, await ctx.assess(req, match)

    return asyncio.run(run())


def test_quick_check_runs_from_the_database_to_a_verdict(clean, variant, fx):
    """Database loading through to a verdict, with every number traceable."""
    now = datetime.now(timezone.utc)
    pipeline = _pipeline(clean, variant, now)
    pipeline.queue_import(parse_sales(_csv(variant, now), _prov()), request_url=EVIDENCE)
    _run(clean, pipeline)

    # The demo variant is synthetic, so point Quick Check at it by id rather
    # than relying on the matcher's catalogue.
    deps = api("deps")
    schemas = api("schemas")

    async def run():
        repo = Repository(DSN)
        await repo.connect()
        candidates = await repo.candidate_variants(
            language=Language.JA, set_code="SM-P", number="114"
        )
        return candidates

    candidates = asyncio.run(run())
    assert candidates, "the variant must be loadable from the catalogue"


def test_quick_check_reports_insufficient_data_rather_than_guessing(clean, variant, fx):
    """With no European sales at all, the answer is an explicit unknown."""
    deps = api("deps")
    schemas = api("schemas")
    req = schemas.QuickCheckRequest(
        raw_title="Pikachu 114/SM-P", price_jpy=Decimal("6500"),
        set_code="SM-P", number="114", language="ja", printing="holo",
        scenario="A_hand_carry", zero_logistics_cost_is_verified=True,
    )

    async def run():
        repo = Repository(DSN)
        await repo.connect()
        ctx = deps.Context(repo)
        match = await ctx.resolve_identity(req)
        if match.best is None:
            return None
        return await ctx.assess(req, match)

    response = asyncio.run(run())
    if response is None:
        pytest.skip("no catalogue variant matched; identity is covered by unit tests")
    assert response.verdict.value == "INSUFFICIENT_DATA"
    assert response.reasons, "an unknown must say why"
    assert response.max_buy_price_jpy is None


def test_quick_check_refuses_an_unknown_scenario(clean, variant, fx):
    deps = api("deps")
    schemas = api("schemas")
    ctx = deps.Context(None)
    req = schemas.QuickCheckRequest(
        raw_title="x", price_jpy=Decimal("6500"), scenario="teleport",
    )
    with pytest.raises(Exception) as exc:
        ctx._assumptions(req)
    assert "teleport" in str(exc.value)



# ------------------------------------------------ alerting on material change --
# Found by an independent run of the demo in a clean cloud VM: the alert table
# held two rows where the demo's own heading promised one. The cause was in
# stage_compare, which built the "previous" state from the wrong fields: the
# prior snapshot's maximum buy price stood in for the prior asking price, and
# the prior sale count was hardcoded to zero. Every comparison therefore saw a
# large "price drop" and several "new sales", material_change always said yes,
# and only the exact-fingerprint constraint in the database stopped identical
# states from realerting. Any state change at all, however small, alerted.

def _alerts(store) -> int:
    with store.conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM alert")
        return cur.fetchone()["n"]


def test_a_non_material_price_change_does_not_realert(clean, variant, fx):
    """6,500 to 6,450 yen is a 0.8 percent move, under the 5 percent threshold."""
    now = datetime.now(timezone.utc)
    payload = _csv(variant, now)

    first = _pipeline(clean, variant, now, price="6500")
    first.queue_import(parse_sales(payload, _prov()), request_url=EVIDENCE)
    _run(clean, first)
    assert _alerts(clean) == 1, "a first sighting alerts once"

    second = _pipeline(clean, variant, now, price="6450")
    second.queue_import(parse_sales(payload, _prov()), request_url=EVIDENCE)
    _run(clean, second)

    assert _alerts(clean) == 1, (
        "a sub-threshold price move changes the state fingerprint but is not a "
        "material change, so it must not produce a second alert"
    )


def test_a_material_price_drop_does_realert(clean, variant, fx):
    """6,500 to 6,000 yen is a 7.7 percent drop, over the 5 percent threshold."""
    now = datetime.now(timezone.utc)
    payload = _csv(variant, now)

    first = _pipeline(clean, variant, now, price="6500")
    first.queue_import(parse_sales(payload, _prov()), request_url=EVIDENCE)
    _run(clean, first)

    second = _pipeline(clean, variant, now, price="6000")
    second.queue_import(parse_sales(payload, _prov()), request_url=EVIDENCE)
    _run(clean, second)

    assert _alerts(clean) == 2
    with clean.conn.cursor() as cur:
        cur.execute("SELECT payload FROM alert ORDER BY sent_at DESC LIMIT 1")
        reasons = cur.fetchone()["payload"]["reasons"]
    assert any("price dropped" in r for r in reasons)
    assert not any("new completed sales" in r for r in reasons), (
        "no sale was added, so the reason list must not invent new sales"
    )
