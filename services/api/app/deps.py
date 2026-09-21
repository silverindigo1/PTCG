"""Request context: wiring between the API and the pure core.

Everything that touches the network or the database lives here or below. The
engines in ``pokearb_core`` stay I/O-free, which is what lets the backtester
replay them over history with no risk of reading present-day state.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import asyncio

from pokearb_core.adapters.live import (
    EcbFxAdapter,
    TcgdexPricingAdapter,
    tcgdex_card_id_candidates,
)
from pokearb_core.arbitrage.engine import (
    CostAssumptionError,
    CostAssumptions,
    compute_arbitrage,
    resale_economics_fn,
)
from pokearb_core.arbitrage.policy import PolicyStore, PolicyUnverifiedError
from pokearb_core.condition.model import (
    ConditionEvidence,
    PriorStore,
    condition_confidence,
    expected_value,
    posterior,
)
from pokearb_core.identity.matcher import AliasIndex, MatchResult, ObservedCard, match_card
from pokearb_core.liquidity.model import compute_liquidity
from pokearb_core.scoring.opportunity import RiskConfig, assess_opportunity
from pokearb_core.types import (
    AcquisitionPurpose,
    Currency,
    Edition,
    EuCondition,
    Language,
    Money,
    Printing,
    Scenario,
)
from pokearb_core.types import FairValue
from pokearb_core.valuation.benchmark import (
    BASIS as AVERAGE_BASIS,
    MarketBenchmark,
    assess_market_average,
)
from pokearb_core.valuation.fair_value import (
    compute_fair_value_curve,
    condition_value_curve,
)

#: Bumped whenever a change would move a number. Persisted with every snapshot
#: so a stored result can be tied to the code that produced it.
MODEL_VERSION = "2026.09.21-1"

from .repository import Repository
from .schemas import (
    ConditionBreakdown,
    QuickCheckRequest,
    QuickCheckResponse,
    Traceable,
    Verdict,
)

#: Quick Check answers from cache below this age and refreshes in the
#: background above it. Standing in a shop, a two second wait is worse than a
#: number that is forty minutes old and says so.
CACHE_FRESH_SECONDS = 900


def _enum(cls, value: Optional[str]):
    if value is None:
        return None
    try:
        return cls(value)
    except ValueError:
        return None


class Context:
    def __init__(self, repo: Repository, pricing: Optional[Any] = None) -> None:
        self.repo = repo
        #: Source of published market averages. Injectable so tests never
        #: depend on the network; live TCGdex by default.
        self.pricing = pricing if pricing is not None else TcgdexPricingAdapter()

    def _benchmark_blocking(self, variant) -> tuple[MarketBenchmark, dict]:
        """Try each plausible TCGdex id for the variant. Runs in a thread."""
        tried: list[str] = []
        last: dict = {"observation": None, "reasons": (), "sibling_product_ids": None,
                      "sibling_ids": [], "raw": []}
        for card_id in tcgdex_card_id_candidates(variant.set_code, variant.number):
            tried.append(card_id)
            try:
                seen = self.pricing.observe(
                    card_id, printing=variant.printing, variant_id=variant.variant_id,
                    language=variant.language.value,
                )
            except Exception as exc:  # noqa: BLE001
                last = {**last, "reasons": (f"TCGdex lookup for {card_id} failed: {exc}",)}
                continue
            seen["card_id"] = card_id
            bench = assess_market_average(
                seen["observation"], as_of=datetime.now(timezone.utc),
                sibling_product_ids=seen["sibling_product_ids"],
                parser_reasons=seen["reasons"],
            )
            return bench, seen
        reasons = last["reasons"] or (
            f"no TCGdex card matched {', '.join(tried) or 'this variant'}",
        )
        return assess_market_average(
            None, as_of=datetime.now(timezone.utc), sibling_product_ids=None,
            parser_reasons=reasons,
        ), last

    async def source_status(self) -> list[dict[str, Any]]:
        return await self.repo.source_status()

    async def resolve_identity(self, req: QuickCheckRequest) -> MatchResult:
        observed = ObservedCard(
            raw_title=req.raw_title,
            language=_enum(Language, req.language),
            set_code=req.set_code,
            number=req.number,
            printing=_enum(Printing, req.printing),
            edition=_enum(Edition, req.edition),
            stamp=req.stamp,
            name=req.raw_title,
            source_id=req.shop_source_id,
        )
        universe = await self.repo.candidate_variants(
            language=observed.language, set_code=observed.set_code, number=observed.number
        )
        aliases = AliasIndex(await self.repo.alias_map())
        result = match_card(observed, universe, aliases)
        await self.repo.record_match_decision(observed, result)
        return result

    def _assumptions(self, req: QuickCheckRequest) -> CostAssumptions:
        """Build cost assumptions from the request, refusing to guess.

        An unrecognised scenario is an error. Silently substituting hand carry
        would apply a personal-use relief to a shipped consignment.
        """
        scenario = _enum(Scenario, req.scenario)
        if scenario is None:
            raise CostAssumptionError(
                f"unknown scenario {req.scenario!r}; expected one of "
                + ", ".join(s.value for s in Scenario)
            )
        purpose = _enum(AcquisitionPurpose, req.acquisition_purpose)
        if purpose is None:
            raise CostAssumptionError(
                f"unknown acquisition_purpose {req.acquisition_purpose!r}; "
                "expected resale or personal"
            )
        return CostAssumptions(
            scenario=scenario,
            acquisition_purpose=purpose,
            fx_spread_rate=req.fx_spread_rate,
            proxy_fee_rate=req.proxy_fee_rate,
            proxy_fee_fixed_jpy=req.proxy_fee_fixed_jpy,
            domestic_jp_shipping_jpy=req.domestic_jp_shipping_jpy,
            international_shipping_jpy=req.international_shipping_jpy,
            duty_rate=req.duty_rate,
            marketplace_fee_rate=req.marketplace_fee_rate,
            payment_fee_rate=req.payment_fee_rate,
            outbound_shipping_eur=req.outbound_shipping_eur,
            expected_loss_rate=req.expected_loss_rate,
            items_in_consignment=req.items_in_consignment,
            basket_value_jpy=req.basket_value_jpy,
            zero_logistics_cost_is_verified=req.zero_logistics_cost_is_verified,
        ).validated()

    async def assess(self, req: QuickCheckRequest, match: MatchResult) -> QuickCheckResponse:
        now = datetime.now(timezone.utc)
        variant = match.best.variant  # type: ignore[union-attr]
        risk_factors: list[str] = []
        links: list[str] = []

        def insufficient(headline: str, reasons: list[str], **extra) -> QuickCheckResponse:
            return QuickCheckResponse(
                verdict=Verdict.INSUFFICIENT_DATA,
                headline=headline,
                match_confidence=match.best.confidence,  # type: ignore[union-attr]
                match_outcome=match.outcome.value,
                variant_id=variant.variant_id,
                canonical_key=variant.canonical_key,
                risk_factors=risk_factors,
                reasons=reasons,
                source_links=sorted(set(links)),
                computed_at=now.isoformat(),
                **extra,
            )

        try:
            assumptions = self._assumptions(req)
        except CostAssumptionError as exc:
            return insufficient("Cost assumptions cannot produce a number", [str(exc)])

        # European resale evidence only. A Japanese sale is evidence of a
        # Japanese price, and the shop price the user just typed is the
        # Japanese observation.
        sales = await self.repo.sales_for(
            variant.variant_id, market="EU", language=variant.language,
            graded=False, known_by=now,
        )
        listings = await self.repo.active_listings_for(
            variant.variant_id, market="EU", language=variant.language,
            graded=False, as_of=now,
        )
        fx = EcbFxAdapter.as_map(await self.repo.fx_rates())
        policy: PolicyStore = await self.repo.policy_store()

        curve = compute_fair_value_curve(
            sales, variant_id=variant.variant_id, as_of=now, fx_rates=fx,
            grade_bucket="raw", market="EU", language=variant.language,
        )
        fv = curve["current"]
        links.extend(ref.source_url for ref in fv.inputs if ref.source_url)

        # Condition: the shop's own label, translated probabilistically.
        cond_dist = None
        cond_block = None
        if req.source_grade_label:
            prior = await self.repo.condition_prior(
                req.shop_source_id or "jp_shop_generic", req.source_grade_label
            )
            if prior is None:
                risk_factors.append(
                    f"no condition prior exists for grade '{req.source_grade_label}' at "
                    f"'{req.shop_source_id or 'unknown shop'}'; condition is unmodelled "
                    "rather than assumed"
                )
            else:
                evidence = ConditionEvidence(
                    has_photos=bool(req.photo_ids),
                    photo_count=len(req.photo_ids),
                    origin="quick-check upload",
                )
                cond_dist = posterior(prior, evidence)
                cond_block = ConditionBreakdown(
                    probabilities={
                        k.value: str(v.quantize(Decimal("0.0001")))
                        for k, v in cond_dist.probabilities.items()
                    },
                    confidence=str(condition_confidence(cond_dist)),
                    is_provisional=cond_dist.is_provisional,
                    source_grade_label=cond_dist.source_grade_label,
                    notes=list(cond_dist.notes),
                )
                if cond_dist.is_provisional:
                    risk_factors.append(
                        "condition mapping for this shop grade is provisional and not "
                        "yet calibrated against observed outcomes"
                    )

        liquidity = compute_liquidity(sales, listings, as_of=now)

        if not fv.sufficient or fv.value is None:
            # No sales-based value. The second-best evidence is a published
            # average, which is fetched live, checked, and if accepted produces
            # an INDICATIVE answer: a price ceiling, never an opportunity call.
            bench, seen = await asyncio.to_thread(self._benchmark_blocking, variant)
            recorder = getattr(self.repo, "record_market_average", None)
            if recorder is not None and bench.observation is not None:
                try:
                    await recorder(variant.variant_id, seen, bench)
                except Exception:  # noqa: BLE001 - persistence must not block an answer
                    risk_factors.append(
                        "the published average could not be stored; this answer "
                        "is not reproducible from the database"
                    )
            if bench.refused:
                return insufficient(
                    "No European resale benchmark: not enough completed sales, "
                    "and the published averages did not pass their checks",
                    list(fv.notes) + list(bench.reasons),
                    condition=cond_block,
                    liquidity_band=liquidity.band.value,
                )
            return self._indicative(
                req=req, match=match, variant=variant, now=now, bench=bench,
                seen=seen, sales=sales, cond_dist=cond_dist, cond_block=cond_block,
                fx=fx, policy=policy, assumptions=assumptions,
                risk_factors=risk_factors, insufficient=insufficient,
            )

        # --- condition-adjusted resale value ----------------------------
        # The NM fair value is the value of a near-mint card. Feeding it
        # straight into the arbitrage maths prices a card nobody has confirmed
        # is near mint. A confidence haircut is not a substitute: it shrinks
        # the score without moving the price the model thinks it can sell for.
        ladder = condition_value_curve(
            sales, variant_id=variant.variant_id, as_of=now, anchor=fv,
            fx_rates=fx, grade_bucket="raw", market="EU", language=variant.language,
        )
        ladder_block = [
            {
                "condition": cv.condition.value,
                "value_eur": str(cv.value.amount) if cv.value else None,
                "basis": cv.basis.value,
                "n_sales": cv.n_sales,
                "note": cv.note,
            }
            for cv in ladder.values.values()
        ]

        if cond_dist is None:
            resale_value = fv.value
            adjusted_block = Traceable(
                known=False,
                reason=(
                    "no condition distribution: the shop grade was not supplied or "
                    "has no prior, so the near-mint value is used and the card is "
                    "not assumed to be near mint"
                ),
            )
            risk_factors.append(
                "economics use the near-mint value because condition is unmodelled; "
                "a card in worse condition is worth less than this"
            )
        else:
            adjusted = expected_value(cond_dist, ladder.money_map())
            if adjusted is None:
                return insufficient(
                    "Condition-adjusted value cannot be computed",
                    [
                        "a condition carrying meaningful probability has no value "
                        "estimate; substituting the near-mint value there would "
                        "overstate the card",
                        *ladder.notes,
                    ],
                    condition=cond_block,
                    liquidity_band=liquidity.band.value,
                    condition_value_ladder=ladder_block,
                )
            resale_value = adjusted
            adjusted_block = Traceable(
                value=str(adjusted.amount), currency="EUR", as_of=now.isoformat(),
                reason=(
                    "probability-weighted across the condition ladder; "
                    + ("all ladder points measured" if not ladder.modelled
                       else f"{len(ladder.modelled)} point(s) modelled from the NM anchor")
                ),
                sources=[ref.source_url for ref in fv.inputs if ref.source_url],
            )

        try:
            arb = compute_arbitrage(
                purchase_jpy=Money(req.price_jpy, Currency.JPY),
                gross_resale_eur=resale_value,
                on=now.date(),
                assumptions=assumptions,
                policy=policy,
                fx_rates=fx,
                required_roi=req.required_roi,
            )
        except (PolicyUnverifiedError, CostAssumptionError) as exc:
            return insufficient(
                "Landed cost depends on an unverified policy value", [str(exc)],
                condition=cond_block,
                condition_adjusted_fair_value=adjusted_block,
                condition_value_ladder=ladder_block,
            )

        economics = resale_economics_fn(
            purchase_jpy=Money(req.price_jpy, Currency.JPY),
            on=now.date(), assumptions=assumptions, policy=policy, fx_rates=fx,
        )
        assessment = assess_opportunity(
            economics=economics,
            gross_resale=resale_value,
            fair_value=fv,
            liquidity=liquidity,
            condition_dist=cond_dist,
            match_confidence=match.best.confidence,  # type: ignore[union-attr]
            as_of=now,
            source_count=len({ref.source_id for ref in fv.inputs}),
            required_roi=req.required_roi,
            max_buy_jpy=arb.max_buy_jpy,
            purchase_jpy=Money(req.price_jpy, Currency.JPY),
        )

        if assessment.suppressed:
            verdict = Verdict.INSUFFICIENT_DATA
            headline = "Suppressed: " + "; ".join(assessment.suppression_detail[:2])
        elif assessment.ranking_roi is not None and assessment.ranking_roi >= Decimal("0.80"):
            verdict = Verdict.STRONG_OPPORTUNITY
            headline = f"Strong opportunity at {req.price_jpy:.0f} JPY"
        elif assessment.ranking_roi is not None and assessment.ranking_roi >= req.required_roi:
            verdict = Verdict.OPPORTUNITY
            headline = f"Opportunity at {req.price_jpy:.0f} JPY"
        else:
            verdict = Verdict.PASS
            cap = arb.max_buy_jpy.amount if arb.max_buy_jpy else None
            headline = (
                f"Pass. Max buy {cap:.0f} JPY against a {req.price_jpy:.0f} JPY ask"
                if cap is not None
                else "Pass. No price clears the required return."
            )

        return QuickCheckResponse(
            verdict=verdict,
            headline=headline,
            match_confidence=match.best.confidence,  # type: ignore[union-attr]
            match_outcome=match.outcome.value,
            variant_id=variant.variant_id,
            canonical_key=variant.canonical_key,
            japanese_market_price=Traceable(
                value=str(req.price_jpy), currency="JPY", as_of=now.isoformat(),
                reason="the shelf price supplied by the user; no Japanese feed is enabled",
            ),
            european_fair_value=Traceable(
                value=str(fv.value.amount), currency="EUR",
                as_of=now.isoformat(),
                reason=f"near-mint equivalent from {fv.n_sales} completed EU sale(s)",
                sources=[ref.source_url for ref in fv.inputs if ref.source_url],
            ),
            condition_adjusted_fair_value=adjusted_block,
            condition_value_ladder=ladder_block,
            recent_sales=[
                {
                    "sale_id": ref.record_id,
                    "source": ref.source_id,
                    "sold_at": ref.observed_at.isoformat(),
                    "weight": str(ref.weight),
                    "detail": ref.note,
                    "url": ref.source_url,
                }
                for ref in fv.inputs[:10]
            ],
            european_supply=Traceable(
                value=str(len(listings)) if listings else None,
                known=bool(listings),
                reason=None if listings else "no European listing feed is currently enabled",
            ),
            japanese_supply=Traceable(
                known=False,
                reason=(
                    "no Japanese marketplace source has been verified and enabled; "
                    "the shelf price you typed is the observation"
                ),
            ),
            expected_net_resale=Traceable(
                value=str(arb.net_proceeds_eur.amount), currency="EUR"),
            expected_profit_eur=Traceable(
                value=str(arb.expected_profit_eur.amount), currency="EUR"),
            expected_profit_dkk=Traceable(
                value=str(arb.expected_profit_dkk.amount) if arb.expected_profit_dkk else None,
                currency="DKK", known=arb.expected_profit_dkk is not None,
            ),
            capital_deployed_eur=Traceable(
                value=str(arb.capital_deployed_eur.amount), currency="EUR",
                reason=arb.capital_basis,
            ),
            capital_basis=arb.capital_basis,
            expected_tax_refund_eur=Traceable(
                value=str(arb.landed.expected_refund_eur.amount), currency="EUR",
                known=arb.landed.expected_refund_eur.amount > 0,
                reason=(
                    "contingent on completing the customs departure procedure; "
                    "excluded from the capital you must put up"
                    if arb.landed.expected_refund_eur.amount > 0
                    else "no refund modelled for this scenario and purpose"
                ),
            ),
            unresolved_costs=list(arb.unresolved),
            expected_roi=str(assessment.point_roi) if assessment.point_roi is not None else None,
            roi_p25=str(assessment.roi_p25) if assessment.roi_p25 is not None else None,
            gross_multiple=str(arb.gross_multiple.quantize(Decimal("0.01"))),
            net_multiple=str(arb.net_multiple.quantize(Decimal("0.01"))),
            break_even_resale_eur=str(arb.break_even_resale_eur.amount),
            max_buy_price_jpy=str(arb.max_buy_jpy.amount) if arb.max_buy_jpy else None,
            strong_buy_price_jpy=str(arb.strong_buy_jpy.amount) if arb.strong_buy_jpy else None,
            target_buy_price_jpy=str(arb.target_buy_jpy.amount) if arb.target_buy_jpy else None,
            do_not_buy_above_jpy=(
                str(arb.do_not_buy_above_jpy.amount) if arb.do_not_buy_above_jpy else None),
            liquidity_band=liquidity.band.value,
            liquidity_score=str(liquidity.score) if liquidity.score is not None else None,
            estimated_days_to_sell=(
                str(liquidity.expected_days_to_sell) if liquidity.expected_days_to_sell else None
            ),
            data_quality_score=str(assessment.data_quality.score),
            condition=cond_block,
            risk_factors=risk_factors + list(arb.notes) + list(ladder.notes),
            suppression_reasons=[r.value for r in assessment.suppression_reasons],
            reasons=list(assessment.suppression_detail) + list(assessment.data_quality.reasons),
            source_links=sorted(set(links)),
            computed_at=now.isoformat(),
            data_age_days=assessment.data_quality.newest_input_age_days,
        )


    def _indicative(self, *, req, match, variant, now, bench, seen, sales, cond_dist,
                    cond_block, fx, policy, assumptions, risk_factors, insufficient):
        """Economics on a published average. Same cost model, capped verdict."""
        obs = bench.observation
        anchor = FairValue(
            window_days=30, value=bench.value, sufficient=True, n_sales=0,
            n_effective=Decimal("0"), dispersion=bench.spread, method=AVERAGE_BASIS,
            notes=bench.reasons,
        )
        ladder = condition_value_curve(
            sales, variant_id=variant.variant_id, as_of=now, anchor=anchor,
            fx_rates=fx, grade_bucket="raw", market="EU", language=variant.language,
        )
        ladder_block = [
            {"condition": cv.condition.value,
             "value_eur": str(cv.value.amount) if cv.value else None,
             "basis": cv.basis.value, "n_sales": cv.n_sales, "note": cv.note}
            for cv in ladder.values.values()
        ]
        if cond_dist is None:
            resale = bench.value
            adjusted_block = Traceable(
                known=False,
                reason="no condition distribution; the average is used as a near-mint equivalent",
            )
            risk_factors.append(
                "condition unmodelled: a card in worse condition is worth less than this"
            )
        else:
            resale = expected_value(cond_dist, ladder.money_map())
            if resale is None:
                return insufficient(
                    "Condition-adjusted value cannot be computed",
                    ["a condition carrying meaningful probability has no value estimate"],
                    condition=cond_block, condition_value_ladder=ladder_block,
                )
            adjusted_block = Traceable(
                value=str(resale.amount), currency="EUR", as_of=now.isoformat(),
                reason="probability-weighted across a ladder modelled from the average",
                sources=[obs.source_url] if obs and obs.source_url else [],
            )

        try:
            arb = compute_arbitrage(
                purchase_jpy=Money(req.price_jpy, Currency.JPY),
                gross_resale_eur=resale, on=now.date(), assumptions=assumptions,
                policy=policy, fx_rates=fx, required_roi=req.required_roi,
            )
        except (PolicyUnverifiedError, CostAssumptionError) as exc:
            return insufficient(
                "Landed cost depends on an unverified policy value", [str(exc)],
                condition=cond_block, valuation_basis=AVERAGE_BASIS,
            )

        cap = arb.max_buy_jpy
        if arb.unresolved:
            headline = "Indicative only: landed cost has unresolved elements"
        elif cap is None:
            headline = "Indicative: no price clears the required return on this average"
        elif req.price_jpy <= cap.amount:
            headline = (f"Indicative: {req.price_jpy:.0f} JPY is inside the "
                        f"{cap.amount:.0f} JPY max buy (average-based, not sales)")
        else:
            headline = (f"Indicative pass: {req.price_jpy:.0f} JPY is above the "
                        f"{cap.amount:.0f} JPY max buy (average-based, not sales)")

        market_average = None
        if obs is not None:
            market_average = {
                "provider": obs.provider, "via": obs.via,
                "card_id": seen.get("card_id"), "product_id": obs.product_id,
                "finish": obs.finish,
                "figures_eur": {k: (str(v) if v is not None else None) for k, v in (
                    ("avg", obs.avg), ("avg7", obs.avg7), ("avg30", obs.avg30),
                    ("trend", obs.trend), ("low", obs.low))},
                "provider_updated_at": obs.provider_updated_at.isoformat(),
                "fetched_at": obs.known_at.isoformat(),
                "value_used_eur": str(bench.value.amount),
                "statistic": bench.statistic,
                "spread": str(bench.spread),
                "siblings_checked": len(seen.get("sibling_ids") or []),
                "sources": ["https://tcgdex.dev/markets-prices", "https://tcgdex.dev/faq"],
            }

        return QuickCheckResponse(
            verdict=Verdict.INDICATIVE,
            headline=headline,
            match_confidence=match.best.confidence,  # type: ignore[union-attr]
            match_outcome=match.outcome.value,
            variant_id=variant.variant_id,
            canonical_key=variant.canonical_key,
            valuation_basis=AVERAGE_BASIS,
            market_average=market_average,
            japanese_market_price=Traceable(
                value=str(req.price_jpy), currency="JPY", as_of=now.isoformat(),
                reason="the shelf price supplied by the user",
            ),
            european_fair_value=Traceable(
                value=str(bench.value.amount), currency="EUR", as_of=now.isoformat(),
                reason=f"Cardmarket averages via TCGdex: {bench.statistic}; not sales-based",
                sources=[obs.source_url] if obs and obs.source_url else [],
            ),
            condition_adjusted_fair_value=adjusted_block,
            condition_value_ladder=ladder_block,
            expected_net_resale=Traceable(value=str(arb.net_proceeds_eur.amount), currency="EUR"),
            expected_profit_eur=Traceable(value=str(arb.expected_profit_eur.amount), currency="EUR"),
            capital_deployed_eur=Traceable(
                value=str(arb.capital_deployed_eur.amount), currency="EUR",
                reason=arb.capital_basis,
            ),
            capital_basis=arb.capital_basis,
            unresolved_costs=list(arb.unresolved),
            expected_roi=str(arb.roi.quantize(Decimal("0.0001"))),
            break_even_resale_eur=str(arb.break_even_resale_eur.amount),
            max_buy_price_jpy=str(cap.amount) if cap else None,
            strong_buy_price_jpy=str(arb.strong_buy_jpy.amount) if arb.strong_buy_jpy else None,
            target_buy_price_jpy=str(arb.target_buy_jpy.amount) if arb.target_buy_jpy else None,
            do_not_buy_above_jpy=str(cap.amount) if cap else None,
            liquidity_band="unmeasurable",
            condition=cond_block,
            risk_factors=risk_factors + list(arb.notes) + [
                "average-based valuation: the number of sales behind the figures "
                "is unknown and liquidity cannot be measured, so this is a price "
                "ceiling and not an opportunity call",
            ],
            reasons=list(bench.reasons),
            source_links=sorted({u for u in (
                (obs.source_url if obs else None),
                "https://tcgdex.dev/markets-prices", "https://tcgdex.dev/faq",
            ) if u}),
            computed_at=now.isoformat(),
        )


_repo: Optional[Repository] = None


async def get_context() -> Context:
    global _repo
    if _repo is None:
        _repo = Repository(os.environ.get("DATABASE_URL", ""))
        await _repo.connect()
    return Context(_repo)
