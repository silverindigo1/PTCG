# Data source feasibility

Every row was checked against the source's own documentation. Rows marked
**unverified** were not checked in this pass and are therefore disabled in code.
An unverified row is not an assertion that the source is unusable; it is an
assertion that nobody has looked yet, which is why the collector refuses to run
it.

## Verified

| Source | Status | What it gives | Constraint | Verified at |
|---|---|---|---|---|
| TCGdex | Live | Canonical cards and sets, 10+ languages including Japanese, REST and GraphQL | Seed only; does not model printing variants consistently | https://tcgdex.dev/ |
| ECB reference rates | Live | Daily, 90-day and full-history EUR rates including JPY and DKK | Published for information only; ECB discourages transactional use | https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml |
| PSA Public API | Adapter built, off | Cert verification by cert number | Population report data is **not** in the documented method set | https://www.psacard.com/publicapi/documentation |
| eBay Marketplace Insights | Adapter built, off | Sold items, the sales history the whole model wants | Limited Release, approved developers only; 90-day history cap; categories whitelisted per partner | https://developer.ebay.com/api-docs/buy/marketplace-insights/static/overview.html |
| Cardmarket | Adapter built, off | European listings and product data | Restricted to professional sellers with manual approval; Dedicated Apps explicitly forbidden from constantly polling only public marketplace resources on consecutive days | https://api.cardmarket.com/ws/documentation/API:Auth_Overview |
| PriceCharting | Adapter built, off | Current values across the grade ladder | Paid subscription; 1 call/second; **no price history and no sales history**; idiosyncratic column names | https://www.pricecharting.com/api-documentation |

## Unverified, therefore disabled

Card Rush, Yuyu-tei, Hareruya, Dragon Star, Mandarake, Mercari Japan, Yahoo
Auctions Japan, Rakuten, Surugaya, Magi, Clove, Pokemon Center Japan, CGC, BGS.

Each gets a `source` row seeded with `enabled = false` and
`verification = 'unverified'`. The schema enforces this: the `enabled_requires_review`
constraint makes it impossible to enable a source without a compliance review
recorded on the row.

Turning one on means filling in `robots_checked_on`, `robots_allows_paths`,
`terms_reviewed_on` and `terms_url`. That ordering is the point. The check
happens before the first request, not after a block.

## The Cardmarket problem

This is the one that changes the product, so it deserves its own section.

The brief's Europe Steal Finder continuously scans European marketplace listings
and compares each to fair value. Cardmarket is the European marketplace. Its
documentation explicitly disallows Dedicated App users from constantly requesting
only the public marketplace resources on consecutive days, and describes security
mechanisms that revoke access when abuse is detected.

So the feature as specified cannot be built against Cardmarket on a personal
credential. Three compliant routes exist:

**1. Apply for a 3rd Party App.** Requires a commercial account and a written
justification, and the documentation says a weak justification will be rejected.
This is the only route that makes the feature work as described. It is a business
decision, not an engineering one.

**2. Narrow the scan to a watchlist.** Polling a few dozen specific products on a
slow cadence as part of normal marketplace activity is a different access pattern
from scanning the market. It is defensible under the Dedicated App purpose, which
the documentation describes as supporting the user's normal activities. It gives
up breadth: it finds steals on cards you already care about, not cards you have
never heard of. The `watchlist_rule` table and the scheduler already support
exactly this shape, and it is what the default configuration does.

**3. Drop Cardmarket from steal-finding and keep it for context.** Use eBay Browse
for European supply signal once credentials exist, and treat Cardmarket as a
manual cross-check.

The build ships option 2 as the default and the code refuses option 1's access
pattern until the app type is changed, which is enforced in
`CardmarketAdapter.fetch_listings` rather than left to discipline.

## The population problem

The long-term opportunity engine in the brief leans heavily on PSA population:
total, the grade ladder, and growth over 6, 12 and 24 months. None of it is
available through PSA's public API.

Options, none of which are implemented because none were verified:

- A licensed or commercial population feed.
- Manual periodic snapshots entered by hand. The `population_observation` table
  accepts these with a source URL and a timestamp, and the scarcity model treats
  a hand-entered snapshot exactly like an API one as long as it is dated.
- Leaving population unknown.

Until one of those happens, `TrueScarcityModel` returns `absolute_scarcity =
UNKNOWN` and `condition_scarcity = UNKNOWN`, and only `observed_market_scarcity`
is computed, from listing counts. The brief is explicit that these three must
never be combined into one misleading number, and with two of the three
unavailable that separation is doing real work.

## What this means for the travel use case

Less than it looks. Standing in a Japanese shop, the Japanese price is the number
on the tag, which you type in. What has to be right is the European resale side,
and that depends on completed sales, which depends on eBay Marketplace Insights
access.

Until that access exists, the honest system behaviour is: Quick Check resolves
identity, computes the full cost stack, and returns `INSUFFICIENT_DATA` with the
reason "no European resale benchmark with enough completed sales" rather than a
confident ROI built on listing prices.

That is a worse product than the brief describes, and it is the correct one. The
alternative is a number that looks like the brief's example output and is not
supported by anything.
