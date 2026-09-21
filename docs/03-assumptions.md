# Recorded assumptions

The brief asked for assumptions to be recorded rather than for broad questions
to be asked. These are the ones that shaped the build. Each says what would
change if the assumption turns out to be wrong.

## Product

1. **Single user, private deployment.** API key plus session, no multi-tenancy,
   no sharing. Changing this means adding a tenant column to every table that
   currently has none.

2. **Europe in EUR is the resale benchmark.** DKK is presentation only, derived
   through the dated EUR/DKK rate so historical figures reproduce. US data would
   enter as context weighted below European sales, never as the benchmark.

3. **Quick Check does not need a Japanese price feed.** Standing in a shop, the
   Japanese price is the number on the tag. This is why the absence of verified
   Japanese marketplace access is less damaging than it looks, and why the
   European sales side is the real blocker.

4. **`INSUFFICIENT_DATA` is a distinct verdict from `PASS`.** They call for
   different behaviour when you are holding the card: one means walk away, the
   other means this system cannot help you with this card right now.

## Modelling

5. **Grade probabilities are an input, not a default.** The grading engine
   computes expected value over whatever distribution you supply. It ships none.
   A default distribution would be a fabricated population statistic wearing a
   different hat.

6. **Condition multipliers are a documented prior, not a measurement.** The
   ladder in `DEFAULT_CONDITION_MULTIPLIERS` is a starting point. The calibration
   job replaces it per category, and `FairValue.method` records which was used,
   so a value computed under the prior is distinguishable from a calibrated one.

7. **Condition priors are per shop, not per grade label.** Card Rush "A minus"
   and another shop's "A minus" are different measurements by different
   operators. They get separate rows and separate Dirichlet parameters.

8. **Photos can lower a condition estimate, never raise it above the prior.** A
   photograph cannot prove the absence of a defect. Mass moves down the ladder
   with visible wear; clean photos are weak positive evidence recorded as such.

9. **Fair value normalises to NM-equivalent and then re-weights.** Pooling
   mixed-condition sales without normalisation would make fair value a function
   of which conditions happened to sell, not of the card.

10. **The duty threshold creates a kink, so the max-buy solver is piecewise.**
    Solving the affine form once and ignoring the kink produces a price that
    quietly assumes the wrong duty treatment. The solver works each regime and
    keeps only solutions that land inside the regime they were solved for.

11. **FX reference rates are not executable.** The ECB publishes for information
    and discourages transactional use, so `fx_spread_rate` is a separate explicit
    parameter rather than an optimistic zero.

## Data

12. **Population is unavailable, not zero.** Fields exist, are nullable, and
    render as "unknown" with the reason attached. `TrueScarcityModel` returns
    `UNKNOWN` for absolute and condition scarcity and computes only observed
    market scarcity from listing counts. The brief's insistence that the three
    never be merged is doing real work here, since two of them are missing.

13. **Raw payloads are preserved before parsing.** Improved matching can be
    replayed over years of history with no refetching, which also means a
    matching bug found in 2027 is fixable retroactively.

14. **A failing source degrades a cycle, never stops it and never substitutes.**
    Affected data is marked stale, staleness lowers data quality, and low data
    quality can suppress an opportunity. That chain is the entire failure
    response.

15. **Blocking on (language, set, number) only.** Deliberately not on printing,
    edition or stamp: the matcher needs to see sibling variants in order to
    detect ambiguity and reject. Blocking them out would hide exactly the cases
    that need review.

## Open, needing a decision rather than an assumption

- Cardmarket access route. The default is a narrow watchlist scan; the
  alternative is applying for a 3rd Party App. See `docs/01-data-sources.md`.
- Commodity code and duty rate for collectible trading cards. Seeded
  unverified; the engine refuses above-threshold landed costs until classified.
- Whether to buy a population feed or enter snapshots by hand. The schema
  accepts either, as long as the snapshot is dated and carries a source.

---

## Revised 2026-09-20

| Assumption | Previous | Now | Source |
|---|---|---|---|
| Traveller allowance applies to arbitrage stock | assumed yes | **no**, unless `acquisition_purpose=personal` | https://info.skat.dk/data.aspx?oid=2230232 |
| Japanese departure refund applies to resale inventory | assumed yes | **no**, unless personal use is asserted | https://www.japan-guide.com/news/tax-free-shopping.html |
| Above-allowance hand carry | duty and VAT modelled as zero | refused without a verified tariff rate | https://info.skat.dk/data.aspx?oid=2230232 |
| Thresholds | per card | per basket or receipt | both of the above |
| Unstated shipping or proxy cost | treated as zero | refused unless explicitly asserted as a measured zero | judgement, not a legal source |
| Net proceeds in scoring | 90 percent of gross | the engine's own cost stack | judgement |
| ROI denominator | economic landed cost | cash deployed, stated in `capital_basis` | judgement |
| Unknown condition | near-mint value used silently | condition-adjusted value, or an explicit unknown | judgement |

The four marked as judgement are conservatism choices, not findings. Each moves
the answer in the direction of refusing rather than recommending, which is the
stated preference of the brief. They are configurable and every one of them is
named in the output when it bites.

Still deliberately unverified: `dk.duty_rate_trading_cards`. No commodity code
has been classified, so any consignment above the duty threshold raises rather
than assuming a rate.
