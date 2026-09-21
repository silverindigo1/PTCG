"""Database access.

Deliberately thin and explicit. The repository returns core value objects, so
the engines never see a database row and the tests never need a database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from pokearb_core.arbitrage.policy import PolicyParameter, PolicyStore
from pokearb_core.condition.model import DirichletPrior
from pokearb_core.identity.matcher import MatchResult, ObservedCard
from pokearb_core.types import (
    CardVariant,
    Currency,
    Edition,
    EuCondition,
    FxRate,
    Grader,
    Language,
    Listing,
    Money,
    Printing,
    Sale,
)

try:  # asyncpg is optional so the core stays importable without a database
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]


_CONDITION_KEYS = ("nm", "ex", "gd", "lp", "pl", "po")


class Repository:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool: Any = None

    async def connect(self) -> None:
        if not self.dsn or asyncpg is None:
            return
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=8)

    async def _rows(self, sql: str, *args: Any) -> Sequence[Any]:
        if self.pool is None:
            return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(sql, *args)

    async def _execute(self, sql: str, *args: Any) -> None:
        if self.pool is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(sql, *args)

    # -------------------------------------------------------------- identity
    async def candidate_variants(
        self,
        *,
        language: Optional[Language],
        set_code: Optional[str],
        number: Optional[str],
    ) -> list[CardVariant]:
        """Blocked candidate set.

        Blocking is a performance optimisation only. It deliberately does NOT
        filter on printing, edition or stamp: those are the discriminators the
        matcher needs to see in order to detect ambiguity and reject.
        """
        rows = await self._rows(
            """
            SELECT v.*, COALESCE(array_agg(s.sibling_id::text)
                     FILTER (WHERE s.sibling_id IS NOT NULL), '{}') AS siblings
              FROM card_variant v
              LEFT JOIN variant_sibling s ON s.variant_id = v.variant_id
             WHERE ($1::card_language IS NULL OR v.language = $1)
               AND ($2::text IS NULL OR v.set_code = $2)
               AND ($3::text IS NULL OR v.number = $3)
             GROUP BY v.variant_id
             LIMIT 500
            """,
            language.value if language else None,
            set_code,
            number,
        )
        return [self._to_variant(r) for r in rows]

    @staticmethod
    def _to_variant(row: Any) -> CardVariant:
        return CardVariant(
            variant_id=str(row["variant_id"]),
            language=Language(row["language"]),
            set_code=row["set_code"],
            number=row["number"],
            printing=Printing(row["printing"]),
            edition=Edition(row["edition"]),
            stamp=row["stamp"],
            name_en=row["name_en"],
            name_ja=row["name_ja"],
            pokemon_slug=row["pokemon_slug"],
            series=row["series"],
            year=row["year"],
            illustrator=row["illustrator"],
            artwork_id=row["artwork_id"],
            release_method=row["release_method"],
            region=row["region"],
            sibling_variant_ids=tuple(row["siblings"]),
        )

    async def alias_map(self) -> dict[str, list[str]]:
        rows = await self._rows(
            "SELECT pokemon_slug, array_agg(alias) AS aliases FROM variant_alias "
            "WHERE pokemon_slug IS NOT NULL GROUP BY pokemon_slug"
        )
        return {r["pokemon_slug"]: list(r["aliases"]) for r in rows}

    async def record_match_decision(self, observed: ObservedCard, result: MatchResult) -> None:
        """Persist every decision, including rejections.

        This table is what later lets the calibration job ask whether a 0.98
        confidence actually corresponds to 98 percent correctness. Without it
        the thresholds are decoration.
        """
        if self.pool is None:
            return
        import json

        best = result.best
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO match_decision
                  (source_id, observed_title, chosen_variant_id, outcome,
                   confidence, components, penalties, gate_failures)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                """,
                observed.source_id or "quick_check",
                observed.raw_title,
                best.variant.variant_id if best else None,
                result.outcome.value,
                float(best.confidence) if best else 0.0,
                json.dumps({k: str(v) for k, v in (best.components if best else {}).items()}),
                list(best.penalties) if best else [],
                [f for c in result.rejected for f in c.gate_failures][:20],
            )

    # --------------------------------------------------------- averages ----
    async def record_market_average(self, variant_id: str, seen: dict, bench) -> None:
        """Persist a published average and the verdict of its checks.

        Refused observations are stored too, with their reasons: knowing that
        a card's figures were rejected, and why, is evidence in its own right.
        """
        obs = bench.observation
        if obs is None:
            return
        await self._execute(
            """
            INSERT INTO market_average_observation
              (variant_id, provider, via, external_card_id, product_id, finish,
               currency, provider_updated_at, known_at, avg, low, trend, avg1,
               avg7, avg30, market, language, source_url, raw_hash, accepted,
               value_used, refusal_reasons)
            VALUES ($1::uuid,$2,$3,$4,$5,$6,$7::currency_code,$8,$9,$10,$11,$12,
                    $13,$14,$15,$16,$17::card_language,$18,$19,$20,$21,$22)
            ON CONFLICT (variant_id, provider, finish, provider_updated_at) DO NOTHING
            """,
            variant_id, obs.provider, obs.via, seen.get("card_id"), obs.product_id,
            obs.finish, obs.currency.value, obs.provider_updated_at, obs.known_at,
            obs.avg, obs.low, obs.trend, obs.avg1, obs.avg7, obs.avg30, obs.market,
            obs.language.value if obs.language else None, obs.source_url, obs.raw_hash,
            bench.accepted, bench.value.amount if bench.value else None,
            None if bench.accepted else list(bench.reasons),
        )

    # ---------------------------------------------------------------- market
    async def sales_for(
        self,
        variant_id: str,
        days: int = 400,
        *,
        market: Optional[str] = None,
        language: Optional[Language] = None,
        graded: Optional[bool] = False,
        grader: Optional[str] = None,
        grade: Optional[Decimal] = None,
        known_by: Optional[datetime] = None,
    ) -> list[Sale]:
        """Load completed sales, scoped.

        Four things this query must not do, each of which it previously did:

        * flatten a graded sale into a raw one, which put PSA 10 prices into
          the value of an ungraded card;
        * coerce a condition confidence of zero into 1.0, which turned "we have
          no idea what condition this is" into "we are certain";
        * mix markets, which let a Tokyo sale stand as European resale evidence;
        * ignore when the evidence arrived, which let a historical calculation
          use a sale that had not been published yet.
        """
        clauses = [
            "s.variant_id = $1::uuid",
            "s.sold_at >= now() - ($2 || ' days')::interval",
        ]
        args: list[Any] = [variant_id, str(days)]

        def add(clause_tmpl: str, value: Any) -> None:
            args.append(value)
            clauses.append(clause_tmpl.format(n=len(args)))

        if graded is True:
            clauses.append("s.graded_item_id IS NOT NULL")
        elif graded is False:
            clauses.append("s.graded_item_id IS NULL")
        if grader is not None:
            add("g.grading_company = ${n}::grader", grader)
        if grade is not None:
            add("g.grade = ${n}::numeric", grade)
        if market is not None:
            add("upper(coalesce(s.market, src.market)) = upper(${n})", market)
        if language is not None:
            add("s.language = ${n}::card_language", language.value)
        if known_by is not None:
            add("s.observed_at <= ${n}", known_by)

        rows = await self._rows(
            """
            SELECT s.*, src.source_id AS src_id, src.market AS source_market,
                   g.grading_company AS g_grader, g.grade AS g_grade
              FROM sale s
              JOIN source src ON src.source_id = s.source_id
              LEFT JOIN graded_item g ON g.graded_item_id = s.graded_item_id
             WHERE """ + " AND ".join(clauses) + """
             ORDER BY s.sold_at DESC
            """,
            *args,
        )
        out: list[Sale] = []
        for r in rows:
            raw_conf = r["condition_confidence"]
            out.append(
                Sale(
                    sale_id=str(r["sale_id"]),
                    variant_id=variant_id,
                    source_id=r["src_id"],
                    sold_at=r["sold_at"],
                    price=Money(
                        Decimal(str(r["price_amount"])), Currency(r["price_currency"])
                    ),
                    condition=EuCondition(r["condition"]) if r["condition"] else None,
                    language=Language(r["language"]) if r["language"] else Language.JA,
                    source_url=r["source_url"],
                    is_graded=r["graded_item_id"] is not None,
                    grader=Grader(r["g_grader"]) if r["g_grader"] else None,
                    grade=Decimal(str(r["g_grade"])) if r["g_grade"] is not None else None,
                    shipping_included=bool(r["shipping_included"]),
                    # Zero is a real confidence and must survive the round trip.
                    condition_confidence=(
                        Decimal(str(raw_conf)) if raw_conf is not None else Decimal("1.0")
                    ),
                    market=(r["market"] if "market" in r.keys() else None)
                    or r["source_market"],
                    external_id=r["external_id"],
                    known_at=r["observed_at"],
                )
            )
        return out

    async def active_listings_for(
        self,
        variant_id: str,
        *,
        market: Optional[str] = None,
        language: Optional[Language] = None,
        graded: Optional[bool] = False,
        as_of: Optional[datetime] = None,
    ) -> list[Listing]:
        """Active listings with the price as at ``as_of``.

        The price comes from the observation history, not from a mutable column
        on the listing, so a card repriced from 14,800 to 10,000 yen reads back
        at whichever price was in force at the calculation time. Listings that
        had not been seen yet at ``as_of`` are not returned.
        """
        clauses = ["l.variant_id = $1::uuid"]
        args: list[Any] = [variant_id]

        def add(tmpl: str, value: Any) -> None:
            args.append(value)
            clauses.append(tmpl.format(n=len(args)))

        if as_of is None:
            clauses.append("l.ended_at IS NULL")
            obs_filter = ""
        else:
            add("l.first_seen_at <= ${n}", as_of)
            add("(l.ended_at IS NULL OR l.ended_at > ${n})", as_of)
            args.append(as_of)
            obs_filter = f"AND observed_at <= ${len(args)}"

        if graded is True:
            clauses.append("l.graded_item_id IS NOT NULL")
        elif graded is False:
            clauses.append("l.graded_item_id IS NULL")
        if market is not None:
            add("upper(coalesce(l.market, src.market)) = upper(${n})", market)
        if language is not None:
            add("l.language = ${n}::card_language", language.value)

        rows = await self._rows(
            """
            SELECT l.*, src.market AS source_market,
                   o.price_amount, o.price_currency, o.observed_at AS priced_at
              FROM listing l
              JOIN source src ON src.source_id = l.source_id
              JOIN LATERAL (
                   SELECT price_amount, price_currency, observed_at
                     FROM listing_observation
                    WHERE listing_id = l.listing_id """ + obs_filter + """
                    ORDER BY observed_at DESC LIMIT 1
              ) o ON TRUE
             WHERE """ + " AND ".join(clauses) + """
            """,
            *args,
        )
        return [
            Listing(
                listing_id=str(r["listing_id"]),
                variant_id=variant_id,
                source_id=r["source_id"],
                observed_at=r["priced_at"],
                price=Money(Decimal(str(r["price_amount"])), Currency(r["price_currency"])),
                condition=EuCondition(r["condition"]) if r["condition"] else None,
                language=Language(r["language"]) if r["language"] else Language.JA,
                source_url=r["source_url"],
                quantity=r["quantity"],
                is_graded=r["graded_item_id"] is not None,
                market=(r["market"] if "market" in r.keys() else None) or r["source_market"],
                external_id=r["external_id"],
            )
            for r in rows
        ]

    async def fx_rates(self) -> list[FxRate]:
        rows = await self._rows(
            "SELECT * FROM fx_rate WHERE as_of >= current_date - 10 ORDER BY as_of DESC"
        )
        return [
            FxRate(r["as_of"], Currency(r["base"]), Currency(r["quote"]),
                   Decimal(str(r["rate"])), r["source_url"])
            for r in rows
        ]

    async def policy_store(self) -> PolicyStore:
        rows = await self._rows("SELECT * FROM policy_parameter")
        return PolicyStore(
            [
                PolicyParameter(
                    r["key"], Decimal(str(r["value"])), r["unit"],
                    r["valid_from"], r["valid_to"], r["source_url"],
                    r["requires_verification"], r["note"],
                )
                for r in rows
            ]
        )

    async def condition_prior(self, source_id: str, label: str) -> Optional[DirichletPrior]:
        rows = await self._rows(
            "SELECT * FROM condition_prior WHERE source_id=$1 AND lower(source_grade_label)=lower($2)",
            source_id, label,
        )
        if not rows:
            return None
        r = rows[0]
        return DirichletPrior(
            source_id=r["source_id"],
            source_grade_label=r["source_grade_label"],
            alpha={
                EuCondition[k.upper()]: Decimal(str(r[f"alpha_{k}"]))
                for k in _CONDITION_KEYS
            },
            evidence_n=r["evidence_n"],
            is_provisional=r["is_provisional"],
            note=r["note"],
        )

    async def source_status(self) -> list[dict[str, Any]]:
        rows = await self._rows(
            """
            SELECT s.source_id, s.display_name, s.enabled, s.verification,
                   (SELECT max(finished_at) FROM import_log l
                     WHERE l.source_id = s.source_id AND l.status = 'ok') AS last_ok,
                   (SELECT l.error FROM import_log l
                     WHERE l.source_id = s.source_id ORDER BY l.started_at DESC LIMIT 1) AS last_error
              FROM source s ORDER BY s.source_id
            """
        )
        out = []
        for r in rows:
            last_ok = r["last_ok"]
            age = (
                int((datetime.now(timezone.utc) - last_ok).total_seconds() // 3600)
                if last_ok else None
            )
            out.append(
                {
                    "source_id": r["source_id"],
                    "name": r["display_name"],
                    "enabled": r["enabled"],
                    "verification": r["verification"],
                    "hours_since_success": age,
                    # A failed source degrades confidence. It never causes a
                    # substituted value.
                    "state": "ok" if age is not None and age < 24 else "stale_or_never_run",
                    "last_error": r["last_error"],
                }
            )
        return out
