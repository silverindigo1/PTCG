-- Source registry. Note that every Japanese marketplace is seeded disabled and
-- unverified. The enabled_requires_review constraint makes it impossible to
-- flip `enabled` without recording a compliance review first.

INSERT INTO source (source_id, display_name, base_url, market, verification, enabled,
                    has_official_api, api_docs_url, reliability_weight, notes) VALUES
('ecb', 'ECB euro reference rates', 'https://www.ecb.europa.eu/stats/eurofxref/', 'GLOBAL',
 'verified_api', TRUE, TRUE, 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml', 1.000,
 'Published for information purposes; an explicit FX spread is applied on top in the cost model'),

('tcgdex', 'TCGdex catalogue', 'https://api.tcgdex.net/v2', 'GLOBAL',
 'verified_api', TRUE, TRUE, 'https://tcgdex.dev/', 0.900,
 'Seed for the canonical database. Not the variant authority: printing variants are not modelled consistently upstream'),

('psa', 'PSA Public API', 'https://api.psacard.com/publicapi/', 'US',
 'verified_api', FALSE, TRUE, 'https://www.psacard.com/publicapi/documentation', 0.950,
 'Cert verification by cert number ONLY. Population report data is not exposed. Population stays unknown'),

('ebay_sold', 'eBay Marketplace Insights', 'https://api.ebay.com/buy/marketplace_insights/v1_beta', 'EU',
 'unverified', FALSE, TRUE, 'https://developer.ebay.com/api-docs/buy/marketplace-insights/static/overview.html', 0.900,
 'Limited Release, approved developers only. 90-day history cap. Categories whitelisted per partner'),

('ebay_browse', 'eBay Browse (active listings)', 'https://api.ebay.com/buy/browse/v1', 'EU',
 'unverified', FALSE, TRUE, 'https://developer.ebay.com/api-docs/buy/browse/static/overview.html', 0.600,
 'Active listings only. Supply and liquidity signal. Never a fair value input'),

('cardmarket', 'Cardmarket', 'https://api.cardmarket.com/ws/v2.0', 'EU',
 'unverified', FALSE, TRUE, 'https://api.cardmarket.com/ws/documentation/API:Auth_Overview', 0.850,
 'Professional sellers only, manual approval. Dedicated Apps forbidden from constant public marketplace polling on consecutive days'),

('pricecharting', 'PriceCharting', 'https://www.pricecharting.com/api', 'US',
 'unverified', FALSE, TRUE, 'https://www.pricecharting.com/api-documentation', 0.550,
 'Paid subscription, 1 call/sec. Current values only, no price or sales history. Cross-check only');

-- Japanese marketplaces, all disabled pending a robots and terms review.
INSERT INTO source (source_id, display_name, base_url, market, verification, enabled,
                    has_official_api, max_requests_per_minute, min_seconds_between_requests,
                    reliability_weight, notes)
SELECT s.id, s.name, s.url, 'JP', 'unverified', FALSE, NULL, 4, 15, 0.500,
       'No public API or scraping permission verified. Fill in robots_checked_on, robots_allows_paths, terms_reviewed_on and terms_url before enabling.'
FROM (VALUES
  ('cardrush',     'Card Rush',            'https://www.cardrush-pokemon.jp'),
  ('yuyutei',      'Yuyu-tei',             'https://yuyu-tei.jp'),
  ('hareruya',     'Hareruya 2',           'https://www.hareruya2.com'),
  ('dragonstar',   'Dragon Star',          'https://dragonstar-cards.com'),
  ('mandarake',    'Mandarake',            'https://order.mandarake.co.jp'),
  ('mercari_jp',   'Mercari Japan',        'https://jp.mercari.com'),
  ('yahoo_auc_jp', 'Yahoo Auctions Japan', 'https://auctions.yahoo.co.jp'),
  ('rakuten',      'Rakuten',              'https://www.rakuten.co.jp'),
  ('surugaya',     'Suruga-ya',            'https://www.suruga-ya.jp'),
  ('magi',         'magi',                 'https://magi.camp'),
  ('clove',        'Clove',                'https://clove.jp')
) AS s(id, name, url);

-- The user standing in the shop is a source. The shelf price they type is an
-- observation with a provenance, and every match decision made from it is
-- recorded against this row. Without it, Quick Check violates the foreign key
-- on match_decision the first time it tries to record what it decided.
INSERT INTO source (source_id, display_name, base_url, market, verification,
                    enabled, has_official_api, notes, reliability_weight)
VALUES ('quick_check', 'Quick Check user observation', 'internal://quick-check',
        'JP', 'manual_import', TRUE, FALSE,
        'Operator-entered shelf price and card details. Not a market feed.',
        0.500)
ON CONFLICT (source_id) DO NOTHING;
