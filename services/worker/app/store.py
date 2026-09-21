"""Synchronous persistence for the monitoring pipeline.

Separate from the API's async repository on purpose. The worker is a batch
process with a clear transaction story per stage, and expressing that story in
plain synchronous SQL makes the story auditable. Each method states its
transaction policy, because "partial writes must not be reported as success"
is only enforceable if each write has a defined boundary.

Every write here is idempotent. Re-running a cycle, or re-importing the same
file, must not create a second copy of any evidence. The uniqueness is enforced
by the database, not by application checks that race.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

import psycopg
from psycopg.rows import dict_row

from .loop import CycleReport, StageResult, StageStatus


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


class PipelineStore:
    """Owns the connection. One instance per worker process."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)

    def close(self) -> None:
        self.conn.close()

    def _write(self, sql: str, params) -> Optional[dict]:
        """Run a write in its own transaction, rolling back on failure.

        Rolling back matters beyond this statement: an aborted transaction in
        PostgreSQL makes every later statement on the connection fail with a
        misleading error, which would make one broken stage look like eight
        broken stages in the cycle report.
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone() if cur.description else None
            self.conn.commit()
            return row
        except Exception:
            self.conn.rollback()
            raise

    def _read(self, sql: str, params: tuple = ()) -> list:
        """Run a read, and leave the connection usable if it fails.

        A failed statement aborts the transaction in PostgreSQL, and every
        later statement on the same connection then fails with a misleading
        error. One broken stage must not make the remaining stages look broken
        too: the loop degrades per stage, and this is what makes that true at
        the connection level.
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        except Exception:
            self.conn.rollback()
            raise

    # -------------------------------------------------------------- cycles --

    def begin(self, cycle_id: str, started_at: datetime) -> None:
        """Own transaction. A cycle row must exist before any stage writes."""
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cycle (cycle_id, started_at, status, stages_total)
                    VALUES (%s, %s, 'running', 0)
                    ON CONFLICT (cycle_id) DO NOTHING
                    """,
                    (cycle_id, started_at),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


    def completed_stages(self, cycle_id: str) -> set:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT stage FROM cycle_stage WHERE cycle_id = %s AND status = 'ok'",
                (cycle_id,),
            )
            return {r["stage"] for r in cur.fetchall()}

    def record(self, cycle_id: str, result: StageResult) -> None:
        """Own transaction, so a later stage failing cannot erase this record."""
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cycle_stage
                      (cycle_id, stage, status, started_at, finished_at,
                       records_in, records_out, partial, detail)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (cycle_id, stage) DO UPDATE SET
                        status = EXCLUDED.status,
                        finished_at = EXCLUDED.finished_at,
                        records_in = EXCLUDED.records_in,
                        records_out = EXCLUDED.records_out,
                        partial = EXCLUDED.partial,
                        detail = EXCLUDED.detail
                    """,
                    (
                        cycle_id, result.stage, result.status.value,
                        result.started_at, result.finished_at,
                        result.records_in, result.records_out, result.partial, result.detail,
                    ),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


    def finish(self, cycle_id: str, report: CycleReport) -> None:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cycle SET finished_at=%s, status=%s, stages_total=%s,
                           stages_ok=%s, stages_skipped=%s, stages_unwired=%s,
                           stages_failed=%s, summary=%s
                     WHERE cycle_id=%s
                    """,
                    (
                        report.finished_at, report.status, len(report.stages),
                        report.count(StageStatus.OK),
                        report.count(StageStatus.SKIPPED),
                        report.count(StageStatus.UNWIRED),
                        report.count(StageStatus.FAILED),
                        report.summary(), cycle_id,
                    ),
                )
            self.conn.commit()

        # -------------------------------------------------------------- import --
        except Exception:
            self.conn.rollback()
            raise


    def source_dataset(self, source_id: str) -> Optional[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT dataset FROM source WHERE source_id=%s", (source_id,))
            row = cur.fetchone()
            return row["dataset"] if row else None

    def batch_already_imported(self, source_id: str, content_hash: str) -> Optional[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM import_batch WHERE source_id=%s AND content_hash=%s",
                (source_id, content_hash),
            )
            return cur.fetchone()

    def open_batch(
        self, *, source_id: str, content_hash: str, filename: Optional[str],
        imported_by: str, evidence_url: Optional[str], dataset: str, row_count: int,
    ) -> str:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO import_batch
                      (source_id, content_hash, filename, imported_by, evidence_url,
                       dataset, row_count, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'partial')
                    RETURNING batch_id
                    """,
                    (source_id, content_hash, filename, imported_by, evidence_url,
                     dataset, row_count),
                )
                batch_id = cur.fetchone()["batch_id"]
            self.conn.commit()
            return str(batch_id)
        except Exception:
            self.conn.rollback()
            raise


    def close_batch(
        self, batch_id: str, *, inserted: int, skipped: int, status: str,
        error: Optional[str] = None,
    ) -> None:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE import_batch SET rows_inserted=%s, rows_skipped=%s,
                           status=%s, error=%s, finished_at=now()
                     WHERE batch_id=%s
                    """,
                    (inserted, skipped, status, error, batch_id),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


    def store_raw(
        self, *, source_id: str, request_url: str, payload: Any,
        content_hash: str, dataset: str, fetched_at: Optional[datetime] = None,
    ) -> Optional[int]:
        """Persist the payload verbatim before anything parses it.

        Returns the raw id, or the existing one. Raw records are the reason a
        parsing bug can be fixed and replayed without refetching.
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT raw_id FROM raw_record WHERE source_id=%s AND content_hash=%s",
                    (source_id, content_hash),
                )
                row = cur.fetchone()
                if row:
                    return row["raw_id"]
                cur.execute(
                    """
                    INSERT INTO raw_record
                      (source_id, fetched_at, request_url, content_hash, payload, dataset)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s)
                    RETURNING raw_id
                    """,
                    (source_id, fetched_at or datetime.now(timezone.utc), request_url,
                     content_hash, _json(payload), dataset),
                )
                raw_id = cur.fetchone()["raw_id"]
            self.conn.commit()
            return raw_id
        except Exception:
            self.conn.rollback()
            raise


    def insert_sales(self, sales: Sequence, *, raw_id: Optional[int], dataset: str) -> tuple:
        """All-or-nothing per call. Returns (inserted, skipped_as_duplicate).

        Transaction policy: one transaction for the whole batch. A batch that
        fails inserts nothing, which is why a failure can be reported as a
        failure rather than as a partial success of unknown extent.
        """
        inserted = skipped = 0
        try:
            with self.conn.cursor() as cur:
                for s in sales:
                    cur.execute(
                        """
                        INSERT INTO sale
                          (raw_id, source_id, external_id, variant_id, sold_at,
                           price_amount, price_currency, shipping_included,
                           condition, condition_confidence, language, source_url,
                           market, dataset, observed_at)
                        VALUES (%s,%s,%s,%s::uuid,%s,%s,%s::currency_code,%s,
                                %s::eu_condition,%s,%s::card_language,%s,%s,%s,%s)
                        ON CONFLICT (source_id, external_id) DO NOTHING
                        """,
                        (
                            raw_id, s.source_id, s.external_id, s.variant_id, s.sold_at,
                            s.price.amount, s.price.currency.value, s.shipping_included,
                            s.condition.value if s.condition else None,
                            s.condition_confidence, s.language.value, s.source_url,
                            s.market, dataset, s.known_at or s.sold_at,
                        ),
                    )
                    if cur.rowcount:
                        inserted += 1
                    else:
                        skipped += 1
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return inserted, skipped

    def upsert_listing_observations(self, listings: Sequence, *, dataset: str) -> tuple:
        """Listings keep their history. One row per listing, many observations.

        A repriced listing gets a new observation, not a mutated price, so a
        historical calculation reads the price that was in force at the time.
        Successive observations of one listing are one listing, never two
        independent offers.
        """
        inserted_obs = new_listings = 0
        try:
            with self.conn.cursor() as cur:
                for l in listings:
                    cur.execute(
                        """
                        INSERT INTO listing
                          (source_id, external_id, variant_id, first_seen_at,
                           last_seen_at, condition, language, quantity, source_url,
                           market, dataset)
                        VALUES (%s,%s,%s::uuid,%s,%s,%s::eu_condition,
                                %s::card_language,%s,%s,%s,%s)
                        ON CONFLICT (source_id, external_id) DO UPDATE SET
                            last_seen_at = GREATEST(listing.last_seen_at, EXCLUDED.last_seen_at),
                            ended_at = NULL
                        RETURNING listing_id, (xmax = 0) AS is_new
                        """,
                        (
                            l.source_id, l.external_id, l.variant_id, l.observed_at,
                            l.observed_at, l.condition.value if l.condition else None,
                            l.language.value, l.quantity, l.source_url, l.market, dataset,
                        ),
                    )
                    row = cur.fetchone()
                    listing_id = row["listing_id"]
                    if row["is_new"]:
                        new_listings += 1
                    cur.execute(
                        """
                        INSERT INTO listing_observation
                          (listing_id, observed_at, price_amount, price_currency)
                        SELECT %s,%s,%s,%s::currency_code
                         WHERE NOT EXISTS (
                            SELECT 1 FROM listing_observation
                             WHERE listing_id=%s AND observed_at=%s
                               AND price_amount=%s
                         )
                        """,
                        (listing_id, l.observed_at, l.price.amount, l.price.currency.value,
                         listing_id, l.observed_at, l.price.amount),
                    )
                    inserted_obs += cur.rowcount
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return new_listings, inserted_obs

    def end_listings_absent_from(
        self, *, source_id: str, seen_external_ids: Iterable, as_of: datetime,
        feed_was_complete: bool,
    ) -> int:
        """Mark listings gone, but only when the feed is known to be complete.

        An incomplete import is not evidence that a listing was removed. If the
        caller cannot assert completeness, nothing is ended and the count is
        zero: the listings simply go stale, which is visible and recoverable,
        whereas a wrongly ended listing silently removes supply.
        """
        if not feed_was_complete:
            return 0
        ids = list(seen_external_ids)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE listing SET ended_at=%s
                 WHERE source_id=%s AND ended_at IS NULL
                   AND NOT (external_id = ANY(%s))
                """,
                (as_of, source_id, ids),
            )
            ended = cur.rowcount
        self.conn.commit()
        return ended

    # ------------------------------------------------------------ snapshots --

    def save_fair_value(self, variant_id: str, fv, *, computed_at: datetime,
                        grade_bucket: str, market: Optional[str], language,
                        normalized_to) -> int:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fair_value_snapshot
                      (variant_id, computed_at, window_days, value_amount, value_currency,
                       sufficient, n_sales, n_effective, dispersion, method, notes,
                       grade_bucket, market, language, normalized_to)
                    VALUES (%s::uuid,%s,%s,%s,%s::currency_code,%s,%s,%s,%s,%s,%s,
                            %s,%s,%s::card_language,%s::eu_condition)
                    RETURNING fair_value_id
                    """,
                    (
                        variant_id, computed_at, fv.window_days,
                        fv.value.amount if fv.value else None,
                        fv.value.currency.value if fv.value else None,
                        fv.sufficient, fv.n_sales, fv.n_effective, fv.dispersion,
                        fv.method, list(fv.notes), grade_bucket, market,
                        language.value if language else None,
                        normalized_to.value if normalized_to else None,
                    ),
                )
                fv_id = cur.fetchone()["fair_value_id"]
                # Provenance: which sales went in, which were set aside and why.
                # Excluded inputs are recorded, never deleted.
                for ref, included, reason in (
                    [(r, True, None) for r in fv.inputs]
                    + [(r, False, r.note) for r in fv.excluded]
                ):
                    if not str(ref.record_id).isdigit():
                        continue
                    cur.execute(
                        """
                        INSERT INTO fair_value_input
                          (fair_value_id, sale_id, weight, included, exclusion_reason)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (fair_value_id, sale_id) DO NOTHING
                        """,
                        (fv_id, int(ref.record_id), ref.weight, included, reason),
                    )
            self.conn.commit()
            return fv_id
        except Exception:
            self.conn.rollback()
            raise


    def upsert_opportunity(self, variant_id: str, kind: str = "jp_eu_arbitrage") -> str:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO opportunity (variant_id, kind)
                    VALUES (%s::uuid, %s)
                    ON CONFLICT (variant_id, kind) DO UPDATE SET closed_at = NULL
                    RETURNING opportunity_id
                    """,
                    (variant_id, kind),
                )
                opp = cur.fetchone()["opportunity_id"]
            self.conn.commit()
            return str(opp)
        except Exception:
            self.conn.rollback()
            raise


    def record_snapshot(self, **kw) -> int:
        """Persist everything needed to reproduce this result.

        Observations, model version, configuration, cost assumptions and the FX
        rates actually used. A snapshot that records only outputs can be shown
        but not explained, and cannot be replayed at all.
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO opportunity_snapshot
                      (opportunity_id, cycle_id, computed_at, as_of, suppressed,
                       suppression_reasons, fair_value_id, landed_cost_eur,
                       upfront_cash_eur, expected_refund_eur, capital_deployed_eur,
                       capital_basis, condition_adjusted_value_eur,
                       point_roi, roi_p10, roi_p25, ranking_roi, annualised_roi,
                       max_buy_jpy, liquidity_score, data_quality_score,
                       match_confidence, condition_confidence, expected_days_to_sell,
                       model_version, risk_config, cost_assumptions, fx_rates_used,
                       policy_sources, evidence_sale_ids, unresolved, explanation)
                    VALUES (%(opportunity_id)s::uuid, %(cycle_id)s::uuid, %(computed_at)s,
                            %(as_of)s, %(suppressed)s, %(suppression_reasons)s,
                            %(fair_value_id)s, %(landed_cost_eur)s, %(upfront_cash_eur)s,
                            %(expected_refund_eur)s, %(capital_deployed_eur)s,
                            %(capital_basis)s, %(condition_adjusted_value_eur)s,
                            %(point_roi)s, %(roi_p10)s, %(roi_p25)s, %(ranking_roi)s,
                            %(annualised_roi)s, %(max_buy_jpy)s, %(liquidity_score)s,
                            %(data_quality_score)s, %(match_confidence)s,
                            %(condition_confidence)s, %(expected_days_to_sell)s,
                            %(model_version)s, %(risk_config)s::jsonb,
                            %(cost_assumptions)s::jsonb, %(fx_rates_used)s::jsonb,
                            %(policy_sources)s::jsonb, %(evidence_sale_ids)s,
                            %(unresolved)s, %(explanation)s::jsonb)
                    ON CONFLICT (opportunity_id, cycle_id) DO UPDATE SET
                        computed_at = EXCLUDED.computed_at,
                        suppressed = EXCLUDED.suppressed,
                        explanation = EXCLUDED.explanation
                    RETURNING snapshot_id
                    """,
                    kw,
                )
                snap = cur.fetchone()["snapshot_id"]
            self.conn.commit()
            return snap
        except Exception:
            self.conn.rollback()
            raise


    def latest_snapshot(self, opportunity_id: str, before_cycle: Optional[str] = None):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM opportunity_snapshot
                 WHERE opportunity_id=%s::uuid
                   AND (%s::uuid IS NULL OR cycle_id <> %s::uuid)
                 ORDER BY computed_at DESC LIMIT 1
                """,
                (opportunity_id, before_cycle, before_cycle),
            )
            return cur.fetchone()

    def record_alert(
        self, *, opportunity_id: str, cycle_id: str, channel: str,
        fingerprint: str, material_state: Mapping, payload: Mapping,
    ) -> bool:
        """Returns True when an alert was actually created.

        The unique index on (opportunity_id, fingerprint) is what stops a
        re-run from alerting twice for the same material state. Dedupe belongs
        in the database because two workers can race an application check.
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alert
                      (opportunity_id, cycle_id, channel, fingerprint,
                       material_state, payload)
                    VALUES (%s::uuid,%s::uuid,%s,%s,%s::jsonb,%s::jsonb)
                    ON CONFLICT (opportunity_id, fingerprint) WHERE fingerprint IS NOT NULL DO NOTHING
                    RETURNING alert_id
                    """,
                    (opportunity_id, cycle_id, channel, fingerprint,
                     _json(material_state), _json(payload)),
                )
                created = cur.fetchone() is not None
            self.conn.commit()
            return created

        # ----------------------------------------------------------- read side --
        except Exception:
            self.conn.rollback()
            raise


    def variants_with_evidence(self, dataset: str = "production") -> list:
        return self._read(
            """
            SELECT DISTINCT v.variant_id, v.canonical_key, v.language
              FROM card_variant v JOIN sale s ON s.variant_id = v.variant_id
             WHERE s.dataset = %s
            """,
            (dataset,),
        )

    def fx_rows(self) -> list:
        return self._read("SELECT * FROM fx_rate ORDER BY as_of DESC")

    def policy_rows(self) -> list:
        return self._read("SELECT * FROM policy_parameter")

    def sales_rows(self, variant_id: str, *, market: Optional[str], dataset: str,
                   known_by: Optional[datetime] = None) -> list:
        return self._read(
            """
                SELECT s.*, src.market AS source_market,
                       g.grading_company AS g_grader, g.grade AS g_grade
                  FROM sale s
                  JOIN source src ON src.source_id = s.source_id
                  LEFT JOIN graded_item g ON g.graded_item_id = s.graded_item_id
                 WHERE s.variant_id = %s::uuid
                   AND s.dataset = %s
                   AND (%s::text IS NULL
                        OR upper(coalesce(s.market, src.market)) = upper(%s))
                   AND (%s::timestamptz IS NULL OR s.observed_at <= %s)
             ORDER BY s.sold_at DESC
            """,
            (variant_id, dataset, market, market, known_by, known_by),
        )

    def listing_rows(self, variant_id: str, *, market: Optional[str], dataset: str,
                     as_of: Optional[datetime] = None) -> list:
        return self._read(
            """
                SELECT l.*, src.market AS source_market,
                       o.price_amount, o.price_currency, o.observed_at AS priced_at
                  FROM listing l
                  JOIN source src ON src.source_id = l.source_id
                  JOIN LATERAL (
                       SELECT price_amount, price_currency, observed_at
                         FROM listing_observation
                        WHERE listing_id = l.listing_id
                          AND (%s::timestamptz IS NULL OR observed_at <= %s)
                        ORDER BY observed_at DESC LIMIT 1
                  ) o ON TRUE
                 WHERE l.variant_id = %s::uuid
                   AND l.dataset = %s
                   AND (%s::timestamptz IS NULL OR (l.ended_at IS NULL OR l.ended_at > %s))
                   AND (%s::text IS NULL
                    OR upper(coalesce(l.market, src.market)) = upper(%s))
            """,
            (as_of, as_of, variant_id, dataset, as_of, as_of, market, market),
        )


def _numeric_or_blank(value: str) -> str:
    return value if str(value).isdigit() else ""
