# PokeArb

Autonomous Pokemon TCG market intelligence for Japan to Europe arbitrage.
Private, single-user, conservative by construction.

Read `docs/00-architecture.md` first. It is the Phase 1 design document and it
contains the source feasibility findings that shape everything else.

---

## The short version of what you need to know

Three of the data sources the product depends on are gated in ways that change
what can be built. Each was verified against the source's own documentation:

- **PSA population data is not in the PSA public API.** The documented method
  set is cert verification by cert number.
  https://www.psacard.com/publicapi/documentation
- **eBay sold-listing data needs Limited Release approval**, caps history at 90
  days, and requires per-partner category whitelisting.
  https://developer.ebay.com/api-docs/buy/marketplace-insights/static/overview.html
- **Cardmarket forbids the exact polling pattern a steal finder needs.** Its
  documentation disallows Dedicated App users from constantly requesting only
  the public marketplace resources on consecutive days.
  https://api.cardmarket.com/ws/documentation/API:Auth_Overview

The engines are built and tested. The adapters are built, schema-complete and
disabled. `docs/01-data-sources.md` has the full matrix and the three compliant
routes around the Cardmarket problem.

Until sales data is connected, Quick Check will correctly answer
`INSUFFICIENT_DATA` rather than produce an ROI from listing prices. That is the
system working, not failing.

---

## Setup

```bash
cp .env.example .env          # fill in POKEARB_API_KEY at minimum
make install
make test-unit                # 86 tests, no database, no network
make smoke                    # end-to-end against live ECB rates
```

Full stack, including the database:

```bash
docker compose up -d --build  # migrations and seed run on first boot
curl -H "x-api-key: $POKEARB_API_KEY" localhost:8000/health
make test                     # adds 12 database integration tests
make demo                     # one complete workflow, start to finish
```

The core package has zero runtime dependencies and needs no database, which is
why `make test-unit` and `make smoke` work on a clean checkout. The integration
tests skip loudly when no database is reachable rather than quietly passing.

### The demonstration

`make demo` runs the whole workflow against a real database with no gated API,
using clearly labelled synthetic data. It shows, in order:

1. a manually imported CSV persisted verbatim, parsed and deduplicated;
2. the identical file imported again, inserting nothing;
3. the same transaction supplied three times failing to satisfy the evidence
   minimum;
4. fair value, liquidity, landed cost, purchase limits and the risk-adjusted
   score, all from one shared cost model;
5. a snapshot persisted with the model version, configuration, cost
   assumptions, FX rates and the sale ids behind it;
6. one alert for one material state, and no second alert on a re-run;
7. production data untouched, because the demo rows are labelled
   `synthetic_demo` and a database trigger refuses to attach them to a real
   source.

---

## Layout

```
packages/core/pokearb_core/   pure engines, no I/O
  identity/                   canonical keys, matcher with hard gates
  condition/                  Dirichlet condition model
  valuation/                  fair value, robust statistics
  arbitrage/                  cost stack, dated policy, backward solver
  liquidity/                  liquidity profile and bands
  scoring/                    data quality, risk-adjusted assessment
  adapters/                   base + compliance gate, live, gated
services/api/                 FastAPI; Quick Check is the priority surface
services/worker/              20-stage monitoring loop, scheduler, rate limits
migrations/versions/          SQL schema
seed/                         sources, dated policy rows, provisional priors
tests/                        the brief's acceptance tests
docs/                         design and source feasibility
```

`pokearb_core` performs no I/O at all. That is not tidiness, it is what makes
the backtester trustworthy: replaying history cannot accidentally read
present-day state because there is nothing to read.

---

## The four design decisions that matter

**Identity rejects, it does not average.** A mismatch on printed number, set,
language, printing, edition or stamp is a hard gate. No score rescues it. When
sibling variants remain equally consistent with the evidence, confidence is
capped below the automatic threshold and the match goes to manual review rather
than picking the more likely sibling. `ピカチュウ 114/SM-P` with no holo or
reverse stated does not auto-match, and should not.

**Listings cannot reach fair value.** `compute_fair_value` has no parameter
that accepts one. The brief's Test 4 is satisfied by the type signature rather
than by a check that someone could later relax.

**Below the evidence minimum there is no number.** Fewer than three usable
sales, or a Kish effective sample size under 2.5, returns
`sufficient=False` with `value=None`. Not a wide interval, not a shaky point
estimate. A shaky point estimate is what gets spent at a counter.

**Ranking uses the 25th percentile of simulated ROI, not the point estimate.**
The point estimate always flatters the card with three sales, a provisional
condition prior and no population data. Ranking on a lower quantile demotes
exactly those. Before ranking happens, hard gates suppress opportunities
entirely rather than listing them low, because rank 40 still invites the tap.

---

## Legal thresholds are data, not code

No tax rate, duty threshold or allowance is a constant anywhere in the
codebase. They live in `policy_parameter` with a validity window and the URL
they were verified from, and the engine resolves them for the transaction date.
A 2024 backtest automatically uses 2024's rules, and a rule change is a data
edit with an audit trail.

Seeded and verified (`seed/policy.sql`, all from
https://skat.dk/skole/onlineshopping/naar-du-koeber and
https://www.japan-guide.com/news/tax-free-shopping.html):

| Key | Value |
|---|---|
| `dk.import_vat_rate` | 25 percent |
| `dk.duty_threshold_eur` | EUR 150 per order, shipping excluded |
| `dk.low_value_per_item_charge_eur` | EUR 3 per item from 1 Jul 2026 |
| `dk.traveller_allowance_air_dkk` | DKK 3,250 by air or sea |
| `jp.tax_free_minimum_jpy` | JPY 5,000 per store per day |
| `jp.tax_free_refund_at_departure_from` | departure refund from 1 Nov 2026 |

Seeded and **deliberately unverified**: `dk.duty_rate_trading_cards`. No tariff
classification was confirmed, so the engine raises `PolicyUnverifiedError`
rather than assuming zero whenever a consignment crosses the duty threshold.
Classify the commodity code before trusting any above-threshold landed cost.

The 1 November 2026 Japanese change matters more than it looks. The refund
stops being a discount at the till and becomes a contingent receivable claimed
on leaving Japan, conditional on a customs departure procedure with every item
on the receipt present. The model treats it as a separate cash flow weighted by
a realisation probability, which is the difference between a max buy price you
can act on and one that assumes money you might not get.

---

## Enabling a source

The schema will not let you skip the compliance check:

```sql
ALTER TABLE source ADD CONSTRAINT enabled_requires_review CHECK (
    NOT enabled OR (verification <> 'unverified' AND verification <> 'blocked')
);
```

To enable one, fill in `robots_checked_on`, `robots_allows_paths`,
`terms_reviewed_on` and `terms_url`, then set `verification`. The check happens
before the first request rather than after a block.

Every Japanese marketplace in the brief is seeded disabled. No public API or
scraping permission was verified for any of them, and asserting one would be
the same category of error as inventing a price.

---

## Current state against the brief's phases

| Phase | State |
|---|---|
| 1 architecture and feasibility | done, `docs/00-architecture.md` |
| 2 canonical card database | schema, canonical key, TCGdex seed adapter |
| 3 card identity matching | done, tested |
| 4 historical market database | done, raw records preserved pre-parse |
| 5-8 marketplace and population integrations | **stubs, disabled.** See the honesty table below |
| 9 fair value engine | done, tested |
| 10 condition model | done, tested, and now applied to the economics |
| 11 arbitrage engine | done, tested, solver verified against the forward model |
| 12 liquidity and supply | done, tested |
| 13 opportunity ranking | done, tested |
| 14-15 dashboard and mobile UI | API endpoints exist, UI not built |
| 16 watchlist and alerts | rule parser, material-state dedupe and persistence done; delivery channels not wired |
| 17-22 | not started |

Eleven of the twenty monitoring stages are wired to real work. The other nine
report `unwired`, which is the truth: they depend on sources that are disabled.
The cycle report never calls an unwired stage a success.

### What "integrated" means here

| Source | Parser written | Tested against | Live access |
|---|---|---|---|
| ECB reference rates | yes | real responses | **verified** |
| TCGdex | yes | real responses | **verified** |
| PriceCharting | yes, column translation | published column contract | never exercised |
| eBay Marketplace Insights | **no** | nothing | never granted |
| PSA cert lookup | **no** | nothing | never granted |
| Cardmarket | **no** | nothing | never granted |
| 11 Japanese marketplaces | no | nothing | no permission verified |

`tests/test_claims.py` asserts this table stays true: no gated adapter may
claim verified live access, and each must report `parser_implemented`
separately from what the remote API offers. A parser written from documentation
and tested against fixtures written from the same documentation proves only
that the two agree with each other.

Acceptance tests 1 to 12, 14 and 15 pass. Test 13 (grading expected value) is
not implemented: it needs grade probability distributions, and the system ships
none, because inventing default grade probabilities is exactly the fabrication
the brief forbids. The engine takes them as an input once you have a calibrated
source.

---

## Defects found and fixed, 2026-09-20

Eight reports were checked against the code. All eight were confirmed. The full
account, with the reproduction and the fix for each, is in
`docs/04-fixes-2026-09-20.md`. The three that changed behaviour you will
notice:

**The maximum buy price was wrong.** A solver that restated the cost model
beside the forward calculation had drifted from it, returning prices whose real
return was 21 to 38 percent against a 40 percent requirement. The solver now
searches directly against the forward model and verifies both that the answer
clears the bar and that the next yen does not.

**The traveller allowance is no longer assumed.** Danish relief for travellers'
goods covers goods for private use and is unavailable for goods imported with a
view to resale, so `acquisition_purpose` defaults to `RESALE` and gets neither
the allowance nor the Japanese departure refund. Above the limit with an
unverified tariff, the engine refuses rather than costing duty and VAT at zero.
Verified at https://info.skat.dk/data.aspx?oid=2230232.

**Condition now moves the price, not just the score.** The condition
distribution is applied to a per-condition value ladder to produce the resale
value used everywhere. A confidence haircut on the score was never a substitute
for that, and missing coverage produces an explicit unknown.
