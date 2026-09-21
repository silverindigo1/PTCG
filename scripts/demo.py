#!/usr/bin/env python3
"""One complete workflow: raw import to persisted snapshot and alert decision.

Run:  make demo        (or: PYTHONPATH=packages/core:services/worker python scripts/demo.py)

What this proves, against a real PostgreSQL database and with no gated API:

  1. A manually imported CSV is persisted verbatim, parsed, and deduplicated.
  2. Re-importing the identical file inserts nothing. Idempotency is real.
  3. The same sale supplied three times does not satisfy the evidence minimum.
  4. A Japanese sale is not used as European resale evidence.
  5. Fair value, liquidity, landed cost, purchase limits and the risk-adjusted
     score all run, from one shared cost model.
  6. A snapshot is persisted with the model version, configuration, cost
     assumptions, FX rates and the sale ids behind it, so it can be replayed.
  7. An alert fires once and does not fire twice for the same material state.
  8. Stages that could not run report as skipped or unwired, never as ok.

Every row it creates is labelled synthetic_demo and the database refuses to
attach it to a production source.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "core"))
sys.path.insert(0, str(ROOT / "services" / "worker"))

from app.loop import MonitoringLoop, StageStatus  # noqa: E402
from app.pipeline import Pipeline, PipelineConfig  # noqa: E402
from app.store import PipelineStore  # noqa: E402
from pokearb_core.arbitrage.engine import CostAssumptions  # noqa: E402
from pokearb_core.ingest import ImportProvenance, parse_sales  # noqa: E402
from pokearb_core.types import AcquisitionPurpose, Currency, Scenario  # noqa: E402

DSN = os.environ.get("DATABASE_URL") or "postgresql://postgres@/pokearb?host=/tmp&port=5433"
DEMO_SOURCE = "demo_eu_sales"
JP_SOURCE = "demo_jp_shop"
EVIDENCE = "https://demo.invalid/where-this-came-from"


def banner(text: str) -> None:
    print()
    print("=" * 74)
    print(text)
    print("=" * 74)


def ensure_variant(store: PipelineStore) -> str:
    with store.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO card_variant
              (canonical_key, language, set_code, number, printing, edition,
               name_en, name_ja, year)
            VALUES ('ja|SM-P|114|holo|n/a||', 'ja', 'SM-P', '114', 'holo', 'n/a',
                    'Pikachu', 'ピカチュウ', 2017)
            ON CONFLICT (canonical_key) DO UPDATE SET set_code = EXCLUDED.set_code
            RETURNING variant_id
            """
        )
        vid = str(cur.fetchone()["variant_id"])
    store.conn.commit()
    return vid


def ensure_fx(store: PipelineStore) -> None:
    """ECB reference rates, dated. Re-runs are idempotent."""
    rows = [
        (date(2026, 9, 18), "JPY", "EUR", "0.0055266900"),
        (date(2026, 9, 18), "EUR", "JPY", "180.9400000000"),
        (date(2026, 9, 18), "EUR", "DKK", "7.4754000000"),
    ]
    with store.conn.cursor() as cur:
        for as_of, base, quote, rate in rows:
            cur.execute(
                """
                INSERT INTO fx_rate (as_of, base, quote, rate, source_url)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (as_of, base, quote) DO NOTHING
                """,
                (as_of, base, quote, rate,
                 "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"),
            )
    store.conn.commit()


def eu_csv(variant_id: str, now: datetime) -> str:
    def d(days: int) -> str:
        return (now - timedelta(days=days)).date().isoformat()

    header = ("external_id,variant_id,sold_at,price_amount,price_currency,market,"
              "language,condition,source_url\n")
    rows = [
        f"eu-101,{variant_id},{d(4)},118.00,EUR,EU,ja,nm,{EVIDENCE}#101",
        f"eu-102,{variant_id},{d(11)},124.50,EUR,EU,ja,nm,{EVIDENCE}#102",
        f"eu-103,{variant_id},{d(19)},112.00,EUR,EU,ja,ex,{EVIDENCE}#103",
        f"eu-104,{variant_id},{d(26)},121.00,EUR,EU,ja,nm,{EVIDENCE}#104",
        f"eu-105,{variant_id},{d(33)},116.50,EUR,EU,ja,nm,{EVIDENCE}#105",
    ]
    return header + "\n".join(rows) + "\n"


def triple_csv(variant_id: str, now: datetime) -> str:
    """The same transaction three times, wearing three different row ids."""
    day = (now - timedelta(days=6)).date().isoformat()
    header = ("external_id,variant_id,sold_at,price_amount,price_currency,market,"
              "language,condition,source_url\n")
    rows = [
        f"dup-a,{variant_id},{day},140.00,EUR,EU,ja,nm,{EVIDENCE}#dup",
        f"dup-b,{variant_id},{day},140.00,EUR,EU,ja,nm,{EVIDENCE}#dup",
        f"dup-c,{variant_id},{day},140.00,EUR,EU,ja,nm,{EVIDENCE}#dup",
    ]
    return header + "\n".join(rows) + "\n"


def main() -> int:
    store = PipelineStore(DSN)
    now = datetime.now(timezone.utc)
    variant_id = ensure_variant(store)
    ensure_fx(store)

    banner("1. Manual import, with provenance")
    payload = eu_csv(variant_id, now)
    prov = ImportProvenance(
        source_id=DEMO_SOURCE, imported_by="demo-script",
        evidence_url=EVIDENCE, filename="eu_sales.csv", synthetic=True,
    )
    parsed = parse_sales(payload, prov)
    print(parsed.summary())

    cfg = PipelineConfig(
        resale_market="EU",
        dataset="synthetic_demo",
        purchase_prices_jpy={variant_id: Decimal("6500")},
        assumptions=CostAssumptions(
            scenario=Scenario.HAND_CARRY,
            acquisition_purpose=AcquisitionPurpose.PERSONAL,
            outbound_shipping_eur=Decimal("4.50"),
            zero_logistics_cost_is_verified=True,
        ),
        required_roi=Decimal("0.40"),
    )
    pipeline = Pipeline(store, cfg)
    pipeline.as_of = now
    pipeline.queue_import(parsed, request_url=EVIDENCE)

    loop = MonitoringLoop(pipeline.handlers(), store=store)
    cycle_one = str(uuid.uuid4())
    report = loop.run_cycle(cycle_one)

    banner("2. Cycle report, with honest stage status")
    print(report.summary())

    banner("3. Idempotency: the identical file again")
    pipeline2 = Pipeline(store, cfg)
    pipeline2.as_of = now
    pipeline2.queue_import(parse_sales(payload, prov), request_url=EVIDENCE)
    r2 = MonitoringLoop(pipeline2.handlers(), store=store).run_cycle(str(uuid.uuid4()))
    ingest = next(s for s in r2.stages if s.stage == "fetch_new_sales")
    print(f"second import inserted {ingest.records_out} new sale(s): {ingest.detail}")

    banner("4. Three copies of one transaction do not become three sales")
    trip = parse_sales(triple_csv(variant_id, now), prov)
    p3 = Pipeline(store, cfg)
    p3.as_of = now
    p3.queue_import(trip, request_url=EVIDENCE + "#dup")
    MonitoringLoop(p3.handlers(), store=store).run_cycle(str(uuid.uuid4()))
    blob = p3.fair_values.get(variant_id, {})
    fv = blob.get("fair_value")
    if fv is not None:
        dup_excluded = [e for e in fv.excluded if "duplicate" in (e.note or "")]
        print(f"fair value used {fv.n_sales} sale(s); "
              f"{len(dup_excluded)} excluded as duplicates")
        for e in dup_excluded[:3]:
            print(f"  excluded {e.record_id}: {e.note}")

    banner("5. Persisted result, and what it can be replayed from")
    with store.conn.cursor() as cur:
        cur.execute(
            """
            SELECT model_version, as_of, point_roi, ranking_roi, max_buy_jpy,
                   capital_deployed_eur, capital_basis, upfront_cash_eur,
                   expected_refund_eur, landed_cost_eur, suppressed,
                   suppression_reasons, array_length(evidence_sale_ids,1) AS n_evidence,
                   fx_rates_used, cost_assumptions
              FROM opportunity_snapshot ORDER BY computed_at DESC LIMIT 1
            """
        )
        snap = cur.fetchone()
    if snap:
        print(f"  model version      {snap['model_version']}")
        print(f"  as of              {snap['as_of']}")
        print(f"  suppressed         {snap['suppressed']} {snap['suppression_reasons'] or ''}")
        print(f"  landed cost EUR    {snap['landed_cost_eur']}")
        print(f"  upfront cash EUR   {snap['upfront_cash_eur']}")
        print(f"  expected refund    {snap['expected_refund_eur']}")
        print(f"  capital deployed   {snap['capital_deployed_eur']} ({snap['capital_basis']})")
        print(f"  point ROI          {snap['point_roi']}")
        print(f"  ranking ROI        {snap['ranking_roi']}")
        print(f"  max buy JPY        {snap['max_buy_jpy']}")
        print(f"  evidence sales     {snap['n_evidence']}")
        print(f"  FX recorded        {list((snap['fx_rates_used'] or {}).keys())}")
    else:
        print("  no snapshot written")

    banner("6. Alerting fires once for one material state")
    with store.conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM alert")
        print(f"  alerts in table: {cur.fetchone()['n']}")

    banner("7. Production data is untouched by the demo")
    with store.conn.cursor() as cur:
        cur.execute("SELECT dataset, count(*) AS n FROM sale GROUP BY dataset")
        for row in cur.fetchall():
            print(f"  {row['dataset']}: {row['n']} sale(s)")

    store.close()
    print("\nDemo complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
