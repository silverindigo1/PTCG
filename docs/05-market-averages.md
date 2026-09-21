# Market averages via TCGdex

Added 2026-09-21. The first live price source that needs no approval.

## What it is

TCGdex relays Cardmarket's published price averages inside every card
response: EUR, updated daily, no key required
(https://tcgdex.dev/markets-prices, https://tcgdex.dev/faq). A live test on
2026-09-21 found Cardmarket pricing on 22 of 24 cards sampled across six
Japanese sets, and found that Japanese cards map to their own Cardmarket
products, separate from the English printings. The Japanese 151 Pikachu
illustration rare (SV2a-173, product 719626) averaged EUR 33.30; the English
one (sv03.5-173, product 733768) averaged EUR 81.63.

## What it is not

Individual sales. An average carries no sample size: a 30-day average from one
sale looks the same as one from two hundred. So it is a separate evidence
type, `MarketAverage`, that can never be read back as a `Sale`, and every
number derived from it carries the basis `cardmarket_average_via_tcgdex`.

## Acceptance rules

In `pokearb_core/valuation/benchmark.py`. These are judgement, not findings;
each errs towards refusing. Thresholds are in `BenchmarkConfig`.

| Rule | Refuses when | Why |
|---|---|---|
| Look-ahead | fetched after the calculation time | same rule as completed sales |
| Freshness | figures older than 3 days | the provider updates daily |
| Mapping checkable | no Cardmarket product id | the mapping to this printing cannot be verified |
| No collision | a same-name sibling in the set shares the product id | TCGdex documents that different printings can map to one listing |
| Enough figures | fewer than 2 of 7-day, 30-day and trend | one figure is not a market |
| Agreement | avg, trend, 7-day and 30-day spread over 30 percent | a market that thin has no single price |

Accepted observations are valued at the **lowest** of the 7-day average, the
30-day average and the trend, so the benchmark can only understate. The
lowest open offer (`low`) is never used as a value: it is an asking price, not
a traded one. The value is treated as a near-mint equivalent, which errs low
if the averages include worse-condition copies.

Printings are read from the card's own `variants` block. Reverse holo reads
Cardmarket's `-holo` fields; the base printing reads the plain fields only when
exactly one of normal and holo exists. A published zero is not a price: the
recorded responses carry `"trend-holo": 0` for cards with no reverse market.

## What it produces

**Quick Check** falls back to a published average when there are not enough
completed EU sales. The verdict is `INDICATIVE`: the response carries the
maximum buy price and whether the ask is inside it, but never an opportunity
call, because the number of sales behind the figures and the card's liquidity
are both unknown. The observation, and whether it passed, is stored in
`market_average_observation` (migration 0004), refusals included.

**`make price-check`** runs the same logic from the command line with no
database, fetching TCGdex and the ECB live:

```
make price-check CARD=SV2a-173 PRICE=2000
make price-check CARD=SV2a-173 PRICE=2000 ARGS="--scenario C_proxy --proxy-rate 0.10 --shipping-jpy 2500"
```

The card id is TCGdex's: set code and zero-padded number.

## Known limits

* **High-value cards will refuse.** Any basket over the EUR 150 duty threshold
  needs a verified tariff rate for trading cards, and none is configured, so
  the check stops rather than assuming a rate. The 151 Charizard ex special
  illustration rare at 45,000 yen refuses for exactly this reason. Classifying
  the commodity code is the unblocker.
* **Hand-carried resale below the threshold is modelled with VAT and the
  per-item charge, and no duty.** How customs treats cards carried in luggage
  for resale has not been verified against an official source.
* **No mapping collision was found live.** A scan of 168 cards across six
  Japanese sets turned up none, so the collision test uses a derived fixture.
  The rule stays, because TCGdex documents the defect.
* **Condition is not modelled in the CLI.** The shop-grade priors live in the
  database; Quick Check applies them, the CLI does not.
* **Not in the monitoring loop yet.** The worker's stages are unchanged.
