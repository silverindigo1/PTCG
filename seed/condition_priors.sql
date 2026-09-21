-- Provisional condition priors, one per (source, shop grade label).
--
-- These numbers are a DOCUMENTED STARTING POINT, not a measurement. Every row
-- is flagged is_provisional = TRUE with evidence_n = 0, which caps condition
-- confidence and therefore prevents any of them from producing a strong buy
-- signal on their own. The calibration job replaces them as real outcomes
-- arrive.
--
-- Each shop gets its own rows on purpose. 'A-' at one shop is not 'A-' at
-- another: different operator, different instrument, different distribution.

INSERT INTO condition_prior (source_id, source_grade_label,
                             alpha_nm, alpha_ex, alpha_gd, alpha_lp, alpha_pl, alpha_po,
                             evidence_n, is_provisional, note) VALUES
('cardrush', 'A',   2.10, 0.60, 0.20, 0.06, 0.03, 0.01, 0, TRUE, 'Provisional starting prior'),
('cardrush', 'A-',  1.30, 1.00, 0.40, 0.20, 0.07, 0.03, 0, TRUE, 'Provisional starting prior'),
('cardrush', 'B',   0.25, 0.90, 1.00, 0.60, 0.20, 0.05, 0, TRUE, 'Provisional starting prior'),
('cardrush', 'C',   0.05, 0.20, 0.55, 1.00, 0.80, 0.40, 0, TRUE, 'Provisional starting prior'),
('yuyutei',  'A',   1.95, 0.70, 0.22, 0.08, 0.03, 0.02, 0, TRUE, 'Provisional; separate from Card Rush A on purpose'),
('yuyutei',  'B',   0.30, 0.95, 1.00, 0.55, 0.15, 0.05, 0, TRUE, 'Provisional starting prior'),
('yuyutei',  'C',   0.05, 0.18, 0.50, 1.00, 0.85, 0.42, 0, TRUE, 'Provisional starting prior');
