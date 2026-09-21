"""The wired pipeline: import through to a persisted snapshot and alert decision.

This is what makes the monitoring loop do real work without any gated API. It
takes manually imported evidence, matches it, values it, prices it, scores it,
persists a reproducible snapshot, and decides whether to alert.

Each stage reports honestly. A stage with nothing to do says ``skipped`` and
why. A stage with no data source says ``skipped`` and names the missing source.
Only stages that actually ran say ``ok``.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from pokearb_core.arbitrage.engine import (
    CostAssumptionError,
    CostAssumptions,
    compute_arbitrage,
    resale_economics_fn,
)
from pokearb_core.arbitrage.policy import PolicyParameter, PolicyStore, PolicyUnverifiedError
from pokearb_core.condition.model import expected_value
from pokearb_core.liquidity.model import compute_liquidity
from pokearb_core.scoring.opportunity import RiskConfig, assess_opportunity
from pokearb_core.types import (
    Currency,
    EuCondition,
    FxRate,
    Grader,
    Language,
    Listing,
    Money,
    Sale,
)
from pokearb_core.valuation.fair_value import (
    compute_fair_value_curve,
    condition_value_curve,
)

from .loop import StageResult, StageStatus, material_change, material_fingerprint
from .store import PipelineStore

log = logging.getLogger("pokearb.pipeline")

#: Bumped whenever a change would move a number.
MODEL_VERSION = "2026.09.21-1"


@dataclass
class PipelineConfig:
    """What the pipeline values and under which assumptions."""

    resale_market: str = "EU"
    dataset: str = "production"
    purchase_prices_jpy: Mapping[str, Decimal] = field(default_factory=dict)
    assumptions: Optional[CostAssumptions] = None
    risk: RiskConfig = field(default_factory=RiskConfig)
    required_roi: Decimal = Decimal("0.40")
    #: Set only when the import is known to cover the whole feed. Without it,
    #: absent listings are left open rather than inferred to be gone.
    feed_was_complete: bool = False


def _sale_from_row(row: Mapping[str, Any]) -> Sale:
    return Sale(
        sale_id=str(row["sale_id"]),
        variant_id=str(row["variant_id"]),
        source_id=row["source_id"],
        sold_at=row["sold_at"],
        price=Money(Decimal(str(row["price_amount"])), Currency(row["price_currency"])),
        condition=EuCondition(row["condition"]) if row["condition"] else None,
        language=Language(row["language"]) if row["language"] else Language.JA,
        source_url=row["source_url"],
        is_graded=row["graded_item_id"] is not None,
        grader=Grader(row["g_grader"]) if row.get("g_grader") else None,
        grade=Decimal(str(row["g_grade"])) if row.get("g_grade") is not None else None,
        shipping_included=bool(row["shipping_included"]),
        condition_confidence=(
            Decimal(str(row["condition_confidence"]))
            if row["condition_confidence"] is not None else Decimal("1.0")
        ),
        market=row.get("market") or row.get("source_market"),
        external_id=row.get("external_id"),
        known_at=row.get("observed_at"),
    )


def _listing_from_row(row: Mapping[str, Any]) -> Listing:
    return Listing(
        listing_id=str(row["listing_id"]),
        variant_id=str(row["variant_id"]),
        source_id=row["source_id"],
        observed_at=row["priced_at"],
        price=Money(Decimal(str(row["price_amount"])), Currency(row["price_currency"])),
        condition=EuCondition(row["condition"]) if row["condition"] else None,
        language=Language(row["language"]) if row["language"] else Language.JA,
        source_url=row["source_url"],
        quantity=row["quantity"],
        is_graded=row["graded_item_id"] is not None,
        market=row.get("market") or row.get("source_market"),
        external_id=row.get("external_id"),
    )


class Pipeline:
    """Holds the handlers the monitoring loop calls.

    State computed by one stage is held on the instance and consumed by the
    next, so a failed stage leaves the downstream stages with nothing rather
    than with a substituted value.
    """

    def __init__(self, store: PipelineStore, config: Optional[PipelineConfig] = None) -> None:
        self.store = store
        self.cfg = config or PipelineConfig()
        self.as_of: datetime = datetime.now(timezone.utc)
        self.fx: dict = {}
        self.policy: Optional[PolicyStore] = None
        self.fair_values: dict = {}
        self.assessments: dict = {}
        self.pending_imports: list = []

    # ------------------------------------------------------------- helpers --

    def handlers(self) -> dict:
        """Stages this pipeline can actually perform.

        Stages absent from this mapping are reported as ``unwired`` by the
        loop, which is the truth: they depend on sources that are disabled.
        """
        return {
            "fetch_new_sales": self.stage_ingest_sales,
            "fetch_new_listings": self.stage_ingest_listings,
            "update_exchange_rates": self.stage_fx,
            "normalize_records": self.stage_normalize,
            "recalculate_fair_value": self.stage_fair_value,
            "recalculate_liquidity": self.stage_liquidity,
            "recalculate_arbitrage": self.stage_arbitrage,
            "recalculate_opportunity_scores": self.stage_scores,
            "compare_with_prior_snapshots": self.stage_compare,
            "trigger_alerts": self.stage_alerts,
            "store_observations": self.stage_store,
        }

    def queue_import(self, parsed, *, request_url: str) -> None:
        """Stage a parsed manual import for the next cycle."""
        self.pending_imports.append((parsed, request_url))

    # -------------------------------------------------------------- stages --

    def stage_ingest_sales(self, cycle_id: str) -> StageResult:
        pending = [(p, u) for p, u in self.pending_imports if p.sales]
        if not pending:
            return StageResult(
                "fetch_new_sales", StageStatus.SKIPPED,
                detail=(
                    "no manual sales import queued and no automated sales source "
                    "is enabled; eBay sold data requires Limited Release approval"
                ),
            )
        total_in = inserted = skipped = 0
        partial = False
        for parsed, url in pending:
            existing = self.store.batch_already_imported(
                parsed.provenance.source_id, parsed.content_hash
            )
            if existing:
                skipped += len(parsed.sales)
                total_in += parsed.row_count
                continue
            dataset = parsed.provenance.dataset
            raw_id = self.store.store_raw(
                source_id=parsed.provenance.source_id, request_url=url,
                payload={"rows": parsed.row_count, "hash": parsed.content_hash},
                content_hash=parsed.content_hash, dataset=dataset,
            )
            batch_id = self.store.open_batch(
                source_id=parsed.provenance.source_id,
                content_hash=parsed.content_hash,
                filename=parsed.provenance.filename,
                imported_by=parsed.provenance.imported_by,
                evidence_url=parsed.provenance.evidence_url,
                dataset=dataset, row_count=parsed.row_count,
            )
            try:
                ins, dup = self.store.insert_sales(
                    parsed.sales, raw_id=raw_id, dataset=dataset
                )
            except Exception as exc:
                self.store.close_batch(batch_id, inserted=0, skipped=0,
                                       status="failed", error=str(exc))
                partial = True
                raise
            self.store.close_batch(
                batch_id, inserted=ins, skipped=dup,
                status="ok" if not parsed.issues else "partial",
                error="; ".join(i.message for i in parsed.issues[:5]) or None,
            )
            inserted += ins
            skipped += dup
            total_in += parsed.row_count
            if parsed.issues:
                partial = True
        return StageResult(
            "fetch_new_sales", StageStatus.OK, records_in=total_in,
            records_out=inserted, partial=partial,
            detail=f"{skipped} row(s) already present and not re-counted",
        )

    def stage_ingest_listings(self, cycle_id: str) -> StageResult:
        pending = [(p, u) for p, u in self.pending_imports if p.listings]
        if not pending:
            return StageResult(
                "fetch_new_listings", StageStatus.SKIPPED,
                detail=(
                    "no manual listing import queued; Cardmarket may not be polled "
                    "continuously for public marketplace data"
                ),
            )
        total_in = new = obs = 0
        for parsed, _url in pending:
            dataset = parsed.provenance.dataset
            n, o = self.store.upsert_listing_observations(parsed.listings, dataset=dataset)
            new += n
            obs += o
            total_in += parsed.row_count
            ended = self.store.end_listings_absent_from(
                source_id=parsed.provenance.source_id,
                seen_external_ids=[l.external_id for l in parsed.listings],
                as_of=self.as_of,
                feed_was_complete=self.cfg.feed_was_complete,
            )
            if not self.cfg.feed_was_complete:
                log.info("import not marked complete; no listings ended")
        return StageResult(
            "fetch_new_listings", StageStatus.OK, records_in=total_in,
            records_out=obs,
            detail=(
                f"{new} new listing(s), {obs} price observation(s); "
                + ("absent listings ended" if self.cfg.feed_was_complete
                   else "absent listings left open, the import was not asserted complete")
            ),
        )

    def stage_fx(self, cycle_id: str) -> StageResult:
        rows = self.store.fx_rows()
        if not rows:
            return StageResult(
                "update_exchange_rates", StageStatus.SKIPPED,
                detail="no FX rates stored; run scripts/fetch_fx.py against the ECB feed",
            )
        self.fx = {}
        for r in rows:
            key = (Currency(r["base"]), Currency(r["quote"]))
            if key not in self.fx:
                self.fx[key] = FxRate(
                    r["as_of"], key[0], key[1], Decimal(str(r["rate"])), r["source_url"]
                )
        return StageResult("update_exchange_rates", StageStatus.OK,
                           records_out=len(self.fx),
                           detail=f"latest as of {max(r['as_of'] for r in rows)}")

    def stage_normalize(self, cycle_id: str) -> StageResult:
        rows = self.store.policy_rows()
        if not rows:
            return StageResult("normalize_records", StageStatus.SKIPPED,
                               detail="no policy parameters seeded")
        self.policy = PolicyStore([
            PolicyParameter(
                r["key"], Decimal(str(r["value"])), r["unit"],
                r["valid_from"], r["valid_to"], r["source_url"],
                requires_verification=r["requires_verification"],
                note=r["note"],
            )
            for r in rows
        ])
        return StageResult("normalize_records", StageStatus.OK, records_out=len(rows))

    def stage_fair_value(self, cycle_id: str) -> StageResult:
        if not self.fx:
            return StageResult("recalculate_fair_value", StageStatus.SKIPPED,
                               detail="no FX rates available from the previous stage")
        variants = self.store.variants_with_evidence(self.cfg.dataset)
        if not variants:
            return StageResult("recalculate_fair_value", StageStatus.SKIPPED,
                               detail="no variant has any completed-sale evidence")
        sufficient = 0
        for v in variants:
            vid = str(v["variant_id"])
            rows = self.store.sales_rows(
                vid, market=self.cfg.resale_market, dataset=self.cfg.dataset,
                known_by=self.as_of,
            )
            sales = [_sale_from_row(r) for r in rows]
            language = Language(v["language"])
            curve = compute_fair_value_curve(
                sales, variant_id=vid, as_of=self.as_of, fx_rates=self.fx,
                grade_bucket="raw", market=self.cfg.resale_market, language=language,
            )
            fv = curve["current"]
            ladder = condition_value_curve(
                sales, variant_id=vid, as_of=self.as_of, anchor=fv, fx_rates=self.fx,
                grade_bucket="raw", market=self.cfg.resale_market, language=language,
            )
            fv_id = self.store.save_fair_value(
                vid, fv, computed_at=self.as_of, grade_bucket="raw",
                market=self.cfg.resale_market, language=language,
                normalized_to=EuCondition.NM,
            )
            self.fair_values[vid] = {
                "fair_value": fv, "ladder": ladder, "fair_value_id": fv_id,
                "sales": sales, "language": language,
            }
            if fv.sufficient:
                sufficient += 1
        return StageResult(
            "recalculate_fair_value", StageStatus.OK,
            records_in=len(variants), records_out=sufficient,
            detail=f"{len(variants) - sufficient} variant(s) lack sufficient evidence",
        )

    def stage_liquidity(self, cycle_id: str) -> StageResult:
        if not self.fair_values:
            return StageResult("recalculate_liquidity", StageStatus.SKIPPED,
                               detail="no fair values computed this cycle")
        measured = 0
        for vid, blob in self.fair_values.items():
            rows = self.store.listing_rows(
                vid, market=self.cfg.resale_market, dataset=self.cfg.dataset,
                as_of=self.as_of,
            )
            listings = [_listing_from_row(r) for r in rows]
            profile = compute_liquidity(blob["sales"], listings, as_of=self.as_of)
            blob["liquidity"] = profile
            blob["listings"] = listings
            if profile.is_measurable:
                measured += 1
        return StageResult("recalculate_liquidity", StageStatus.OK,
                           records_in=len(self.fair_values), records_out=measured)

    def stage_arbitrage(self, cycle_id: str) -> StageResult:
        if self.policy is None or not self.fair_values:
            return StageResult("recalculate_arbitrage", StageStatus.SKIPPED,
                               detail="policy or fair values unavailable")
        assumptions = self.cfg.assumptions
        if assumptions is None:
            return StageResult(
                "recalculate_arbitrage", StageStatus.SKIPPED,
                detail="no cost assumptions configured; costs are never assumed",
            )
        priced = 0
        for vid, blob in self.fair_values.items():
            price = self.cfg.purchase_prices_jpy.get(vid)
            fv = blob["fair_value"]
            if price is None or not fv.sufficient or fv.value is None:
                continue
            resale = fv.value
            blob["condition_adjusted"] = None
            try:
                arb = compute_arbitrage(
                    purchase_jpy=Money(price, Currency.JPY),
                    gross_resale_eur=resale,
                    on=self.as_of.date(),
                    assumptions=assumptions,
                    policy=self.policy,
                    fx_rates=self.fx,
                    required_roi=self.cfg.required_roi,
                )
            except (PolicyUnverifiedError, CostAssumptionError) as exc:
                blob["arbitrage_error"] = str(exc)
                continue
            blob["arbitrage"] = arb
            blob["purchase"] = Money(price, Currency.JPY)
            priced += 1
        return StageResult("recalculate_arbitrage", StageStatus.OK,
                           records_in=len(self.fair_values), records_out=priced)

    def stage_scores(self, cycle_id: str) -> StageResult:
        priced = {k: v for k, v in self.fair_values.items() if "arbitrage" in v}
        if not priced:
            return StageResult("recalculate_opportunity_scores", StageStatus.SKIPPED,
                               detail="nothing was priced this cycle")
        live = 0
        for vid, blob in priced.items():
            econ = resale_economics_fn(
                purchase_jpy=blob["purchase"], on=self.as_of.date(),
                assumptions=self.cfg.assumptions, policy=self.policy, fx_rates=self.fx,
            )
            assessment = assess_opportunity(
                economics=econ,
                gross_resale=blob["fair_value"].value,
                fair_value=blob["fair_value"],
                liquidity=blob["liquidity"],
                condition_dist=None,
                match_confidence=Decimal("1.0"),
                as_of=self.as_of,
                source_count=len({r.source_id for r in blob["fair_value"].inputs}),
                config=self.cfg.risk,
                required_roi=self.cfg.required_roi,
                max_buy_jpy=blob["arbitrage"].max_buy_jpy,
                purchase_jpy=blob["purchase"],
            )
            self.assessments[vid] = assessment
            if not assessment.suppressed:
                live += 1
        return StageResult("recalculate_opportunity_scores", StageStatus.OK,
                           records_in=len(priced), records_out=live)

    def stage_compare(self, cycle_id: str) -> StageResult:
        if not self.assessments:
            return StageResult("compare_with_prior_snapshots", StageStatus.SKIPPED,
                               detail="no assessments this cycle")
        changed = 0
        for vid, assessment in self.assessments.items():
            opp_id = self.store.upsert_opportunity(vid)
            self.fair_values[vid]["opportunity_id"] = opp_id
            prior = self.store.latest_snapshot(opp_id, before_cycle=cycle_id)
            blob = self.fair_values[vid]
            current = {
                "price": blob["purchase"].amount,
                "ranking_roi": assessment.ranking_roi,
                "eu_supply": len(blob.get("listings", [])),
                "n_sales": blob["fair_value"].n_sales,
                "condition_fingerprint": None,
            }
            blob["material_state"] = current
            if prior is None:
                blob["alert_reasons"] = ["first time this opportunity was seen"]
                changed += 1
                continue
            # Compare like with like: the state recorded with the prior
            # snapshot, field for field. The earlier version reconstructed it
            # from unrelated columns, using the prior maximum buy price as the
            # prior asking price and a hardcoded zero as the prior sale count,
            # so every comparison reported a large price drop and several new
            # sales, and any change at all realerted.
            explanation = prior.get("explanation") or {}
            previous = explanation.get("material_state") if isinstance(explanation, dict) else None
            if previous is None:
                # A prior snapshot exists but predates recorded state. Silence
                # is the default: it is not a first sighting, and there is
                # nothing reliable to compare against.
                blob["alert_reasons"] = []
                continue
            should, reasons = material_change(previous, current)
            blob["alert_reasons"] = reasons if should else []
            if should:
                changed += 1
        return StageResult("compare_with_prior_snapshots", StageStatus.OK,
                           records_in=len(self.assessments), records_out=changed)

    def stage_alerts(self, cycle_id: str) -> StageResult:
        candidates = {
            vid: a for vid, a in self.assessments.items() if not a.suppressed
        }
        if not candidates:
            return StageResult(
                "trigger_alerts", StageStatus.OK, records_in=len(self.assessments),
                records_out=0,
                detail="no opportunity cleared the gates; silence is the correct output",
            )
        sent = 0
        for vid, assessment in candidates.items():
            blob = self.fair_values[vid]
            if not blob.get("alert_reasons"):
                continue
            state = blob.get("material_state", {})
            created = self.store.record_alert(
                opportunity_id=blob["opportunity_id"], cycle_id=cycle_id,
                channel="log", fingerprint=material_fingerprint(state),
                material_state=state,
                payload={
                    "variant_id": vid,
                    "reasons": blob["alert_reasons"],
                    "ranking_roi": str(assessment.ranking_roi),
                    "max_buy_jpy": str(blob["arbitrage"].max_buy_jpy.amount)
                    if blob["arbitrage"].max_buy_jpy else None,
                },
            )
            if created:
                sent += 1
        return StageResult("trigger_alerts", StageStatus.OK,
                           records_in=len(candidates), records_out=sent,
                           detail="duplicates suppressed by material-state fingerprint")

    def stage_store(self, cycle_id: str) -> StageResult:
        if not self.assessments:
            return StageResult("store_observations", StageStatus.SKIPPED,
                               detail="nothing assessed this cycle")
        written = 0
        for vid, assessment in self.assessments.items():
            blob = self.fair_values[vid]
            arb = blob["arbitrage"]
            self.store.record_snapshot(
                opportunity_id=blob["opportunity_id"],
                cycle_id=cycle_id,
                computed_at=datetime.now(timezone.utc),
                as_of=self.as_of,
                suppressed=assessment.suppressed,
                suppression_reasons=[r.value for r in assessment.suppression_reasons],
                fair_value_id=blob["fair_value_id"],
                landed_cost_eur=arb.landed.total_eur.amount,
                upfront_cash_eur=arb.landed.upfront_cash_eur.amount,
                expected_refund_eur=arb.landed.expected_refund_eur.amount,
                capital_deployed_eur=arb.capital_deployed_eur.amount,
                capital_basis=arb.capital_basis,
                condition_adjusted_value_eur=(
                    blob["condition_adjusted"].amount
                    if blob.get("condition_adjusted") else None
                ),
                point_roi=assessment.point_roi,
                roi_p10=assessment.roi_p10,
                roi_p25=assessment.roi_p25,
                ranking_roi=assessment.ranking_roi,
                annualised_roi=assessment.annualised_roi,
                max_buy_jpy=arb.max_buy_jpy.amount if arb.max_buy_jpy else None,
                liquidity_score=blob["liquidity"].score,
                data_quality_score=assessment.data_quality.score,
                match_confidence=Decimal("1.0"),
                condition_confidence=None,
                expected_days_to_sell=blob["liquidity"].expected_days_to_sell,
                model_version=MODEL_VERSION,
                risk_config=_as_json(self.cfg.risk),
                cost_assumptions=_as_json(self.cfg.assumptions),
                fx_rates_used=_as_json({
                    f"{b.value}->{q.value}": {"rate": str(r.rate),
                                              "as_of": r.as_of.isoformat(),
                                              "source": r.source_url}
                    for (b, q), r in self.fx.items()
                }),
                policy_sources=_as_json(dict(arb.landed.policy_sources)),
                evidence_sale_ids=[
                    int(ref.record_id) for ref in blob["fair_value"].inputs
                    if str(ref.record_id).isdigit()
                ],
                unresolved=list(arb.unresolved),
                explanation=_as_json({
                    # The state the next cycle compares against. Without it,
                    # change detection has to guess what the last cycle saw.
                    "material_state": blob.get("material_state"),
                    "scores": [
                        {"name": s.name, "value": str(s.value),
                         "reasons": [r.text for r in s.reasons]}
                        for s in assessment.scores
                    ],
                    "notes": list(arb.notes),
                    "fair_value_notes": list(blob["fair_value"].notes),
                    "ladder": [
                        {"condition": cv.condition.value, "basis": cv.basis.value,
                         "value": str(cv.value.amount) if cv.value else None}
                        for cv in blob["ladder"].values.values()
                    ],
                }),
            )
            written += 1
        return StageResult("store_observations", StageStatus.OK,
                           records_in=len(self.assessments), records_out=written)


def _as_json(obj: Any) -> str:
    import json

    if obj is None:
        return json.dumps(None)
    if hasattr(obj, "__dataclass_fields__"):
        try:
            obj = asdict(obj)
        except TypeError:
            obj = {
                f: getattr(obj, f) for f in obj.__dataclass_fields__  # type: ignore[attr-defined]
            }
    return json.dumps(obj, default=str, sort_keys=True)
