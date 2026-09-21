# Manual import

The route that does not depend on anyone's permission.

Several sources the product wants are permanently gated. eBay completed sales
need Limited Release approval. Cardmarket forbids the polling pattern a steal
finder needs. PSA does not publish population at all. A system that only works
once those open does not work.

So evidence can be entered directly, from records you are entitled to use: your
own purchase and sale history, an export a marketplace gives its own seller,
or figures transcribed by hand from a page you were allowed to read.

## The contract

Print it with:

```python
from pokearb_core.ingest import schema_help
print(schema_help("sales"))       # or "listings"
```

Required columns for a sales import: `external_id`, `variant_id`, `sold_at`,
`price_amount`, `price_currency`, `market`, `language`. Everything else is
optional and blank means unknown, which is modelled as unknown.

`market` has no default. Guessing it is how Japanese prices end up standing for
European resale value.

```csv
external_id,variant_id,sold_at,price_amount,price_currency,market,language,condition,source_url
eu-101,<variant-uuid>,2026-09-16,118.00,EUR,EU,ja,nm,https://example/receipt/101
eu-102,<variant-uuid>,2026-09-09,124.50,EUR,EU,ja,nm,https://example/receipt/102
```

## Provenance is mandatory

```python
from pokearb_core.ingest import ImportProvenance, parse_sales

prov = ImportProvenance(
    source_id="manual_eu",
    imported_by="florian",                       # required
    evidence_url="https://example/export",       # required for production
    filename="cardmarket_sales_2026Q3.csv",
)
parsed = parse_sales(open("sales.csv").read(), prov)
print(parsed.summary())
for issue in parsed.issues:
    print(issue.row_number, issue.field, issue.message)
```

An imported number with no traceable origin is indistinguishable from an
invented one, so `imported_by` and `evidence_url` are refused when blank. Bad
rows are rejected individually with the reason; nothing is coerced.

## Idempotency

A batch is identified by the SHA-256 of its content, not by when it ran. Import
the same file twice and the second run inserts nothing. Rows carry the source's
own identifier, so the same sale arriving in two overlapping exports is one
sale. Fair value additionally deduplicates on a content fingerprint, so three
copies of one transaction under three row ids do not become three sales.

## Feeding it to the pipeline

```python
pipeline.queue_import(parsed, request_url=prov.evidence_url)
MonitoringLoop(pipeline.handlers(), store=store).run_cycle()
```

The payload is written to `raw_record` before anything parses it, so a parsing
bug can be fixed and replayed without re-entering the data.

## Listings, and what absence means

Listing imports keep history: each observation is a row, so a price drop from
14,800 to 10,000 yen is two observations of one listing, and a historical
calculation reads whichever price was in force then.

An import does not end the listings it omits unless you set
`PipelineConfig.feed_was_complete`. An incomplete import is not evidence that a
listing was removed. Without the assertion the omitted listings simply go
stale, which is visible and recoverable; a wrongly ended listing silently
removes supply and makes a card look scarcer than it is.

## Synthetic data

Set `synthetic=True` on the provenance. The rows are labelled
`dataset='synthetic_demo'`, production queries filter them out, and a database
trigger refuses to attach a synthetic row to a production source or the
reverse. Synthetic imports do not need an external evidence URL, because there
is nothing to check them against, and that is exactly why they must never reach
a recommendation.
