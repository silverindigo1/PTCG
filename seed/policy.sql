-- Dated policy parameters. Every row carries the URL it was verified from.
-- Rows with requires_verification = TRUE will cause the engine to REFUSE to
-- produce a landed cost that depends on them, rather than assuming a value.

INSERT INTO policy_parameter (key, value, unit, valid_from, valid_to, source_url, requires_verification, note) VALUES
('dk.import_vat_rate', 0.25, 'rate', '2021-07-01', NULL,
 'https://skat.dk/skole/onlineshopping/naar-du-koeber', FALSE,
 'Danish VAT on goods purchased outside the EU'),

('dk.duty_threshold_eur', 150, 'EUR', '2021-07-01', NULL,
 'https://skat.dk/skole/onlineshopping/naar-du-koeber', FALSE,
 'Customs duty applies above EUR 150 per order, roughly DKK 1150, shipping excluded from the test'),

('dk.low_value_per_item_charge_eur', 3, 'EUR', '2026-07-01', NULL,
 'https://skat.dk/skole/onlineshopping/naar-du-koeber', FALSE,
 'From 1 July 2026, EUR 3 per item on consignments of EUR 150 or less'),

('dk.carrier_handling_fee_dkk', 200, 'DKK', '2021-07-01', NULL,
 'https://skat.dk/skole/onlineshopping/naar-du-koeber', FALSE,
 'Carriers typically add DKK 150-250; midpoint used, override per carrier'),

('dk.traveller_allowance_air_dkk', 3250, 'DKK', '2021-07-01', NULL,
 'https://skat.dk/skole/onlineshopping/naar-du-koeber', FALSE,
 'Goods carried personally, arriving by air or sea from outside the EU'),

('dk.traveller_allowance_other_dkk', 2230, 'DKK', '2021-07-01', NULL,
 'https://skat.dk/skole/onlineshopping/naar-du-koeber', FALSE,
 'Goods carried personally, arriving by other transport'),

('jp.consumption_tax_rate', 0.10, 'rate', '2019-10-01', NULL,
 'https://www.japan-guide.com/news/tax-free-shopping.html', FALSE,
 'General goods rate; 8 percent applies to food and non-alcoholic drinks'),

('jp.tax_free_minimum_jpy', 5000, 'JPY', '2019-10-01', NULL,
 'https://www.japan-guide.com/news/tax-free-shopping.html', FALSE,
 'Minimum per store per day, before tax. Retained under the new regime'),

('jp.tax_free_refund_at_departure_from', 0, 'count', '2019-10-01', '2026-10-31',
 'https://www.japan-guide.com/news/tax-free-shopping.html', FALSE,
 'Old regime: tax deducted or refunded at the shop'),

('jp.tax_free_refund_at_departure_from', 1, 'count', '2026-11-01', NULL,
 'https://www.japan-guide.com/news/tax-free-shopping.html', FALSE,
 'From 1 Nov 2026: pay tax-inclusive in store, claim the refund on leaving Japan after a customs departure procedure. No parallel period. All items on one receipt must be present at departure or the whole purchase loses the refund'),

('jp.tax_free_purchase_window_days', 90, 'count', '2026-11-01', NULL,
 'https://www.japan-guide.com/news/tax-free-shopping.html', FALSE,
 'All goods must be purchased within 90 days of departure under the new regime'),

-- UNVERIFIED. The engine raises PolicyUnverifiedError rather than assuming 0.
('dk.duty_rate_trading_cards', 0, 'rate', '2021-07-01', NULL,
 NULL, TRUE,
 'Commodity code for collectible trading cards has NOT been classified. Classify it in the EU tariff before relying on any above-threshold landed cost');
