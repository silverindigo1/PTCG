# PokeArb: autonomous Pokemon TCG market intelligence platform

Phase 1 design document. Written before implementation, as required by the brief.
Every external factual claim below carries the literal URL fetched while writing
this document. Claims without a URL are engineering decisions, not facts.

---

## 1. Product understanding

The system is a **decision engine with an evidence requirement**, not a price tracker.

Its job is to answer one question repeatedly and conservatively:

> Given everything verifiable right now, what is the maximum I can pay for this
> exact card variant in this exact condition and still clear my required
> risk-adjusted return, and how confident is that number?

Three consequences drive the entire architecture:

1. **Identity is the foundation.** A wrong variant match produces a confident,
   traceable, completely wrong number. Identity errors are worse than missing data
   because they are invisible downstream. Identity therefore uses hard gates that
   reject, not soft scores that average.

2. **Completed sales are the only fair-value input.** Listings are an upper
   bound on what someone hopes to receive. They enter the model only as a sanity
   ceiling and as supply/liquidity signal, never as value.

3. **Absence of data must propagate as absence, never as a default.** Every number
   in the system is either measured with a traceable source or returned as
   `UNKNOWN`. There is no imputation layer. A missing PSA population is not zero,
   not an estimate, not a category average.

The operating asymmetry is explicit in code: a false positive costs real money at
a Japanese counter; a false negative costs nothing but a missed trade. Every
threshold is tuned in that direction, and the system suppresses opportunities
below a data-quality floor rather than displaying them with a low score.

---

## 2. Data source feasibility

This is the section that constrains the entire product. Several sources named in
the brief are **not legally or technically available** on the terms the brief
assumes. Those findings are below, with the verification URL for each.

### 2.1 Verified constraints

**PSA population data is not available through the PSA public API.**
The PSA Public API documentation states that the only available method set is cert
verification by cert number: "We currently offer access to data from Cert
Verification for single item searches by cert number." Base URL
`https://api.psacard.com/publicapi/`, OAuth 2 password grant against PSA login
credentials.
Source: https://www.psacard.com/publicapi/documentation

Implication: the brief's requirement for PSA total population, PSA 10 population,
grade ladder, and population growth over 6/12/24 months **cannot be satisfied from
the official API**. The system implements the `PopulationSource` interface, ships a
working PSA cert adapter (which the API does cover), and treats population as an
explicitly unavailable field until a licensed population feed is connected. It
never estimates a population figure.

**eBay sold-listing data is behind a gated API.**
eBay's Marketplace Insights API, the only eBay API exposing sold items, is
documented as "a (Limited Release) API available only to select developers approved
by business units," covers a sales history range of "up to 90 days in the past,"
and is further restricted because "Partner allowed categories need to be vetted by
business and then whitelisted for that partner."
Source: https://developer.ebay.com/api-docs/buy/marketplace-insights/static/overview.html

Implication: the eBay sold-sales pipeline is implemented as an adapter with full
schema, tests and mocks, and is **inactive until access is granted**. The eBay
Browse API (active listings) is separately available and is used only for supply
and liquidity signals, never for fair value.

**Cardmarket's API forbids exactly the polling pattern a steal finder needs.**
Cardmarket documentation states: "API applications and access are restricted to
professional sellers and subject to a manual approval process at this moment." For
Dedicated Apps it states: "We explicitely do not allow, that Dedicated App users
constantly only request the public Marketplace resources (products, articles,
prices, etc.) on consecutive days and especially not with exhausting the request
limits," and notes security mechanisms that revoke access on suspected abuse.
Source: https://api.cardmarket.com/ws/documentation/API:Auth_Overview

Implication: the brief's **Europe Steal Finder as specified (continuous scanning of
European marketplace listings) is not implementable against Cardmarket within its
terms.** This is a product-level constraint, not an engineering one. Three
compliant options are documented in `docs/01-data-sources.md`; the default build
ships the adapter disabled with a policy gate that refuses to run continuous public
marketplace polling on a Dedicated App credential.

**PriceCharting's API has no history and no sales.**
PriceCharting documentation: "You must have a paid subscription to access the API",
"The API is limited to 1 call every second", and critically "The API and CSV only
support current item values in various grades and conditions. Historic prices and
historic sales are not supported." Its card grade mapping is idiosyncratic
(`loose-price` = ungraded, `graded-price` = grade 9, `manual-only-price` = PSA 10,
`new-price` = grade 8 or 8.5, `cib-price` = grade 7 or 7.5).
Source: https://www.pricecharting.com/api-documentation

Implication: PriceCharting is usable as a **cross-check on current level**, not as
a sales history source and not as a primary fair value input. The adapter encodes
the grade mapping above so the system never confuses `new-price` with a sealed
product.

### 2.2 Verified usable sources

**TCGdex, canonical card data including Japanese.** Open source, REST plus GraphQL,
multi-language with Japanese among the supported Asian languages, endpoints at
`api.tcgdex.net`, documentation at `https://tcgdex.dev`.
Source: https://tcgdex.dev/

This becomes the seed for the canonical card database (Phase 2). It is a seed, not
the authority: the internal canonical model carries variant discriminators that
external catalogues generally do not model consistently.

**ECB euro reference rates, FX.** Free daily XML at
`https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml`, plus 90-day and
full history feeds. Verified live: the feed returns dated `Cube` elements carrying
JPY and DKK against EUR, which is exactly the JPY to EUR to DKK path the arbitrage
engine needs, with dated history for reproducible backtests.
Source: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml

The ECB itself notes these are published for information purposes and discourages
using them for transaction purposes, so the cost model carries a separate,
explicit FX spread parameter on top of the reference rate rather than pretending
the reference rate is executable.

### 2.3 Danish import cost parameters, verified

From Skattestyrelsen, page last modified 2026-07-07:

- Danish VAT on goods from outside the EU is 25 percent.
- Customs duty applies where the order exceeds EUR 150, roughly DKK 1,150, and
  shipping is excluded when testing that threshold.
- From 1 July 2026 a charge of EUR 3 per item applies to consignments with a total
  value of EUR 150 or less.
- Carriers typically add a handling fee of DKK 150 to 250 on top of freight.
- For goods carried personally from outside the EU, duty and VAT apply above
  DKK 3,250 when arriving by air or sea, and above DKK 2,230 by other transport.

Source: https://skat.dk/skole/onlineshopping/naar-du-koeber

These are stored as **dated policy rows with a source URL**, never as constants in
code, exactly as the brief requires. The commodity code and duty rate for
collectible trading cards are deliberately left `UNKNOWN` in the seed data with a
`requires_verification` flag, because I did not verify a tariff classification and
will not assert one.

### 2.4 Japanese consumption tax regime change, verified

Japan's tax-free shopping system changes on 1 November 2026, with no parallel
period. Until October 2026 the tax is deducted or refunded at the shop. From
November 2026 the shopper pays the tax-inclusive price in store and claims the
refund on leaving Japan, after a customs departure procedure via a customs terminal
or Visit Japan Web. The 5,000 yen per store per day minimum is retained. All goods
must be purchased within 90 days of departure. If several items are on one receipt,
all of them must be present at departure or the entire purchase loses the refund.
Source: https://www.japan-guide.com/news/tax-free-shopping.html

This materially changes Scenario A economics. The refund becomes a **contingent
receivable realised at departure, not a discount at the till**, so the model treats
it as a separate cash flow with its own realisation probability rather than netting
it off the purchase price. The regime switch date is a dated policy row.

### 2.5 Sources deliberately not automated

Mercari Japan, Yahoo Auctions Japan, Card Rush, Yuyu-tei, Hareruya, Dragon Star,
Mandarake, Surugaya, Magi, Clove and Rakuten are all in the brief. I did not verify
a public API or scraping permission for any of them in this session, so I am not
going to assert one.

The build therefore ships a **Japanese source framework** rather than Japanese
scrapers: a per-source `SourcePolicy` record holding base URL, robots status,
terms-of-service status, verified API endpoint if any, rate budget, and an
`enabled` flag that defaults to `false` with `verification_status = UNVERIFIED`.
The collector refuses to run any source whose policy record is unverified. Turning
one on is a deliberate act that requires filling in the robots and terms fields,
which forces the compliance check to happen before the first request rather than
after a block.

For the immediate travel use case this matters less than it looks. The Mobile Quick
Check flow does not need a Japanese price feed: **you are standing in front of the
price tag and typing it in.** Japanese market context is nice to have; the European
resale side is what has to be right.

---

## 3. Architecture

```
                     ┌──────────────────────────────┐
  Mobile / web  ───► │  API  (FastAPI, Python 3.12) │
                     │  quick-check, search, cards  │
                     │  watchlist, opportunities    │
                     └──────────┬───────────────────┘
                                │
                     ┌──────────▼───────────────────┐
                     │  pokearb_core (pure Python)  │
                     │  identity · condition ·      │
                     │  valuation · arbitrage ·     │
                     │  liquidity · scoring         │
                     │  no I/O, fully unit-tested   │
                     └──────────┬───────────────────┘
                                │
   ┌────────────────────────────┼───────────────────────────┐
   │                            │                           │
┌──▼──────────┐        ┌────────▼────────┐        ┌─────────▼────────┐
│ PostgreSQL  │        │ Redis (cache,   │        │ Adapters         │
│ raw + norm  │        │ locks, rate     │        │ one per source,  │
│ + history   │        │ budgets)        │        │ policy-gated     │
└─────────────┘        └─────────────────┘        └──────────────────┘
                                ▲
                     ┌──────────┴───────────────────┐
                     │  Worker (APScheduler)        │
                     │  monitoring loop, 20 stages  │
                     └──────────────────────────────┘
```

Deliberate choices:

- **`pokearb_core` performs no I/O.** Every engine takes plain dataclasses and
  returns plain dataclasses. This is what makes the backtester honest: the same
  functions run over historical snapshots with no risk of accidentally reading
  present-day state. Look-ahead bias becomes structurally impossible rather than a
  discipline.
- **Two-table ingestion.** `raw_record` stores the source payload verbatim with a
  content hash before any parsing. `normalized_*` tables are derived. Re-running
  improved matching logic over three years of history requires no refetch, which
  the brief explicitly asks for.
- **Every derived number carries its inputs.** `FairValue` carries the list of sale
  IDs it used and the weight assigned to each. Source traceability is a return
  type, not a UI feature.
- Python for the whole backend rather than the suggested Next.js plus Python split.
  The statistical core is Python; a TypeScript API layer in front of it would add a
  serialisation boundary and a second place for money arithmetic to go wrong, for
  no benefit. The frontend is a separate Next.js client consuming the API.
- **Decimal everywhere for money.** No floats in any monetary path.

---

## 4. Database model

Core entities, abbreviated. Full DDL in `migrations/`.

**Identity**
- `card_variant` — the canonical tradeable printing. Carries every discriminator:
  language, set_code, number, edition, printing, holo_type, is_reverse_holo, stamp,
  artwork_id, illustrator, year, release_method, region. Unique on
  `canonical_key`, a deterministic string built from the discriminators.
- `variant_alias` — cross-language and colloquial names, scoped to a variant or to
  a Pokemon. Aliases never override a discriminator.
- `variant_sibling` — explicit links between variants that are confusable
  (holo vs reverse of the same number). Drives the ambiguity penalty in matching.
- `graded_item` — (variant, grader, grade, cert_no). A distinct tradeable unit.

**Market**
- `source`, `source_policy` — one row per marketplace, with the compliance gate.
- `seller`
- `listing`, `listing_observation` — a listing and its price history.
- `sale` — a completed transaction. The only fair-value input.
- `population_observation` — dated grade-ladder snapshots, nullable everywhere.
- `fx_rate` — dated, so historical calculations reproduce.
- `policy_parameter` — dated tax, duty, threshold and fee rules with source URL.

**Derived**
- `fair_value_snapshot`, `fair_value_input` — the value and the sales behind it.
- `condition_prior` — Dirichlet parameters per (source, source grade label).
- `opportunity`, `opportunity_snapshot` — with full input provenance.
- `match_decision` — every automatic and manual match, for calibration.
- `alert`, `watchlist_rule`, `portfolio_position`, `import_log`.

---

## 5. Methodologies

### 5.1 Card identity matching

Three stages, in order, and the first stage can only reject.

**Stage 1, hard gates.** Any of these mismatching rejects the match outright and
the pair is never considered again:
printed number, set or promo series after alias normalisation, language, edition
(1st vs unlimited), reverse-holo status, stamp.

Mismatch requires both sides to be *known and different*. Unknown does not gate,
it penalises in stage 3.

**Stage 2, weighted score** over number, set, name (alias-aware, cross-language),
variant attributes, and illustrator/year/artwork agreement.

**Stage 3, ambiguity penalty.** This is the part that makes the thresholds mean
something. If the candidate has siblings in `variant_sibling` that are *also*
consistent with the evidence (the listing does not say holo or reverse, and both
exist), confidence is capped below the automatic threshold no matter how good the
name match is. Unresolvable ambiguity routes to manual review instead of picking
the more likely sibling.

Thresholds as specified: at or above 0.98 automatic, 0.90 to 0.98 manual review,
below 0.90 no match. Every decision is written to `match_decision` with its
component scores, which is what later lets the calibration job measure whether 0.98
actually means 98 percent.

### 5.2 Condition normalisation

No fixed mapping table. Each (source, source grade label) pair owns a Dirichlet
distribution over the European ladder {NM, EX, GD, LP, PL, PO}. Card Rush "A minus"
and a different shop's "A minus" are different objects with different parameters.

Starting parameters are weakly informative, flagged `provisional`, with
`evidence_n = 0`. Provisional priors are usable but they carry low condition
confidence, which propagates into the opportunity score, so a provisional mapping
cannot by itself produce a strong buy signal.

Per-listing evidence (photo analysis, description keywords, seller history) updates
the prior to a posterior for that specific listing. Two listings with the same shop
grade can and should end with different distributions.

Expected condition-adjusted resale value is the probability-weighted sum over the
ladder, never the NM value. Condition confidence is one minus the normalised
entropy of the posterior, scaled by evidence quality.

### 5.3 Fair value

1. Filter to the exact variant and language. No cross-variant pooling, ever.
2. Convert each sale to EUR at **that sale's date** FX rate.
3. Normalise each sale to NM-equivalent via an estimated condition multiplier
   ladder, shrunk toward a global prior per category.
4. Weight = recency decay (configurable half-life per window) times source
   reliability times condition confidence.
5. Robust centre: **weighted median**, not mean.
6. Outliers by median absolute deviation, default k = 3.5. Flagged sales are
   down-weighted to zero and recorded with a reason, never silently deleted. Low
   outliers are flagged for investigation as insistently as high ones, since a
   suspiciously cheap sale usually means the wrong variant, damage, or a fake.
7. **Minimum evidence rule.** Below three effective sales in the window, the
   window returns `INSUFFICIENT`, not a number. This is the single most important
   line of defence against the brief's Test 4.

Windows: 7, 30, 90, 365 days, plus a blended current value that prefers the
shortest window meeting the evidence rule.

### 5.4 Arbitrage and maximum buy price

Landed cost is **affine and piecewise** in the Japanese purchase price:

```
landed(P) = a + b·P      within a duty regime
```

where `b` carries VAT and proportional fees and `a` carries fixed costs. The
EUR 150 duty threshold creates a kink, so the solver works each regime separately,
checks feasibility, and returns the maximum feasible price across regimes.

Given required net ROI `r` on deployed capital:

```
(proceeds − landed(P)) / landed(P) ≥ r
  ⇒  landed(P) ≤ proceeds / (1 + r)
  ⇒  P ≤ (proceeds/(1+r) − a) / b
```

Reported in yen, because that is the number you need at the counter.

Three scenarios, each with its own cost stack: hand-carry, direct ship, proxy
service. The Japanese consumption tax refund under the post-November-2026 regime is
modelled as a contingent cash flow with a realisation probability, not as a price
reduction, per the verified rule change above.

### 5.5 Liquidity

Sales per 7/30/90 days, active listings, listing-to-sales ratio, median inter-sale
gap, price dispersion, and a spread proxy from median listing against median sale.
Composite 0 to 100 plus the five-bucket label.

Liquidity is not just a display field. It gates: below a configurable floor, an
opportunity is **suppressed entirely** rather than ranked low, because the brief's
Test 7 asks for exactly that and because an illiquid 150 percent spread is a
number, not a trade.

### 5.6 Risk-adjusted opportunity

Ranking uses a **lower quantile of the profit distribution, not the point
estimate.** Monte Carlo over: fair value uncertainty, the condition posterior,
FX drift over the expected holding period, and sale probability within the horizon.
Default ranking statistic is the 25th percentile of net ROI, configurable.

Annualised return uses the expected holding period from the liquidity model, so a
fast 500 DKK can outrank a slow 600 DKK as the brief requires.

Hard suppression gates, applied before scoring: data quality floor, match
confidence floor, liquidity floor, staleness ceiling.

### 5.7 Monitoring loop and alerts

The 20-stage cycle from the brief runs as an idempotent pipeline keyed on a
`cycle_id`. Alert de-duplication uses a **material change test** rather than a
cooldown: an already-alerted opportunity re-alerts only when price, ROI, supply,
condition information or evidence count moves past a configurable threshold.

### 5.8 Backtesting

Because `pokearb_core` has no I/O, the backtester replays `*_observation` tables up
to a cut-off timestamp and calls the identical functions. Look-ahead bias is
prevented by construction rather than by review.

---

## 6. Assumptions recorded

1. Single-user, private deployment. Authentication is API key plus session; no
   multi-tenancy.
2. Resale benchmark is Europe in EUR. DKK is a presentation currency derived via
   the dated EUR/DKK rate.
3. Grading is modelled as expected value over a grade distribution that the user
   supplies or that a calibrated model produces. The system ships **no default
   grade probabilities**, because inventing them would be exactly the fabrication
   the brief forbids.
4. Photo-based identification is a verification signal that can only lower
   confidence or confirm, never raise a match above the manual-review threshold on
   its own.
5. Population data is unavailable until a licensed feed is connected. Fields exist,
   are nullable, and render as "unknown" rather than blank or zero.

---

## 6b. Revisions, 2026-09-20

Eight defect reports were checked against the implementation and all eight were
confirmed. The changes that alter the documented design rather than merely
fixing code:

* **Acquisition purpose is now a first-class input.** Danish relief for
  travellers' goods is confined to goods for private use and is expressly
  unavailable for goods imported with a view to resale
  (https://info.skat.dk/data.aspx?oid=2230232). The cost model therefore gates
  the traveller allowance and the Japanese departure refund on
  `acquisition_purpose`, which defaults to resale. This makes the default
  hand-carry case more expensive than the original design assumed, and
  correctly so.
* **Thresholds are assessed at basket level.** The Danish value limit applies
  to the traveller's goods in aggregate and cannot be split per item, and the
  Japanese refund is all-or-nothing per receipt. `basket_value_jpy` carries
  that.
* **Capital and economic cost are separate quantities.** ROI is stated on cash
  deployed; the expected refund is a contingent receivable credited to profit,
  not netted off the capital requirement.
* **One financial model.** The scorer no longer carries its own proceeds
  assumption; it calls the arbitrage engine with the buy side frozen, and
  simulations recalculate through it.
* **The backward solver is defined by the forward model.** Price bands are cut
  at each discontinuity and the maximum is found by search against
  `compute_economics`, not by a separately derived affine form.
* **Evidence carries a known-at time and a stable identity.** Historical
  calculations exclude evidence that had not arrived, and deduplication counts
  independent transactions rather than rows.
* **A manual import route is part of the architecture, not a workaround.**
  Several sources are permanently gated; a system that only works when they are
  open does not work.

## 7. Implementation order and current state

Built in this pass, following the brief's ordering:

- Phase 1 architecture and source feasibility — this document plus
  `docs/01-data-sources.md`
- Phase 2 canonical card database — schema, canonical key, seed loader
- Phase 3 card identity matching — full engine plus tests
- Phase 4 historical market database — schema with raw-record preservation
- Phase 9 fair value engine — full engine plus tests
- Phase 10 condition model — full engine plus tests
- Phase 11 arbitrage engine — full engine, cost stack, backward solver, tests
- Phase 12 liquidity and supply models — full engine plus tests
- Phase 13 opportunity ranking — risk-adjusted scoring plus tests
- Adapter framework and policy gate, with TCGdex and ECB live

Deferred, with interfaces and mocks in place:

- Phases 5 to 8 marketplace and population integrations — blocked on the access
  constraints in section 2, not on engineering
- Phases 14 to 15 dashboard and Mobile Quick Check UI — API endpoints exist; the
  brief explicitly says not to start with the dashboard
- Phases 16 to 22
