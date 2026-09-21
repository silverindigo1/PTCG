-- Synthetic sources for the demonstration workflow. Not real marketplaces.
INSERT INTO source (source_id, display_name, base_url, market, verification,
                    enabled, has_official_api, notes, dataset, reliability_weight)
VALUES
  ('demo_eu_sales', 'DEMO European completed sales', 'https://demo.invalid/eu',
   'EU', 'manual_import', TRUE, FALSE,
   'Synthetic. Exists only to demonstrate the pipeline end to end.',
   'synthetic_demo', 0.700),
  ('demo_eu_listings', 'DEMO European listings', 'https://demo.invalid/eu-listings',
   'EU', 'manual_import', TRUE, FALSE,
   'Synthetic. Exists only to demonstrate the pipeline end to end.',
   'synthetic_demo', 0.700),
  ('demo_jp_shop', 'DEMO Japanese shop', 'https://demo.invalid/jp',
   'JP', 'manual_import', TRUE, FALSE,
   'Synthetic. Japanese prices, never used as European resale evidence.',
   'synthetic_demo', 0.700)
ON CONFLICT (source_id) DO NOTHING;
