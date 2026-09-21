-- 0002: make the pipeline honest about scope, provenance and reproducibility.
--
-- Five things this migration fixes, each of which let a wrong number look right:
--
--  1. Market and language were only known via the source row, so a Japanese
--     sale could be read as European resale evidence.
--  2. Nothing separated demonstration data from real data.
--  3. Imports and monitoring cycles had no idempotency key, so a retry created
--     duplicate evidence and duplicate alerts.
--  4. Snapshots recorded results but not enough to reproduce them: no model
--     version, no configuration, no cost assumptions, no FX used.
--  5. Fair value snapshots did not record which bucket they valued, so a raw
--     value and a PSA 10 value were indistinguishable after the fact.

BEGIN;

-- ---------------------------------------------------------------- scoping --

ALTER TABLE sale
    ADD COLUMN IF NOT EXISTS market TEXT
        CHECK (market IS NULL OR market IN ('JP','EU','US','GLOBAL'));
ALTER TABLE listing
    ADD COLUMN IF NOT EXISTS market TEXT
        CHECK (market IS NULL OR market IN ('JP','EU','US','GLOBAL'));

COMMENT ON COLUMN sale.market IS
    'Market the transaction happened in. Overrides source.market when the '
    'source spans markets. A JP sale is never EU resale evidence.';

CREATE INDEX IF NOT EXISTS sale_scope
    ON sale (variant_id, market, language, sold_at DESC);
CREATE INDEX IF NOT EXISTS sale_known_at ON sale (variant_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS listing_scope
    ON listing (variant_id, market, language);

-- Fair value snapshots must say what they valued.
ALTER TABLE fair_value_snapshot
    ADD COLUMN IF NOT EXISTS grade_bucket TEXT NOT NULL DEFAULT 'raw',
    ADD COLUMN IF NOT EXISTS market TEXT,
    ADD COLUMN IF NOT EXISTS language card_language,
    ADD COLUMN IF NOT EXISTS normalized_to eu_condition;

-- ------------------------------------------------------ dataset isolation --

DO $$ BEGIN
    CREATE TYPE dataset_kind AS ENUM ('production', 'synthetic_demo');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE source
    ADD COLUMN IF NOT EXISTS dataset dataset_kind NOT NULL DEFAULT 'production';
ALTER TABLE sale
    ADD COLUMN IF NOT EXISTS dataset dataset_kind NOT NULL DEFAULT 'production';
ALTER TABLE listing
    ADD COLUMN IF NOT EXISTS dataset dataset_kind NOT NULL DEFAULT 'production';
ALTER TABLE raw_record
    ADD COLUMN IF NOT EXISTS dataset dataset_kind NOT NULL DEFAULT 'production';

COMMENT ON COLUMN sale.dataset IS
    'Synthetic demonstration rows are labelled here and filtered out of every '
    'production query. Demo data that can silently reach a recommendation is '
    'worse than no demo data.';

-- A synthetic row may only come from a synthetic source. This stops a demo
-- import from being attributed to a real marketplace.
CREATE OR REPLACE FUNCTION dataset_matches_source() RETURNS trigger AS $$
DECLARE src dataset_kind;
BEGIN
    SELECT dataset INTO src FROM source WHERE source_id = NEW.source_id;
    IF src IS DISTINCT FROM NEW.dataset THEN
        RAISE EXCEPTION
            'dataset % does not match source % which is %', NEW.dataset, NEW.source_id, src;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sale_dataset_matches ON sale;
CREATE TRIGGER sale_dataset_matches BEFORE INSERT OR UPDATE ON sale
    FOR EACH ROW EXECUTE FUNCTION dataset_matches_source();
DROP TRIGGER IF EXISTS listing_dataset_matches ON listing;
CREATE TRIGGER listing_dataset_matches BEFORE INSERT OR UPDATE ON listing
    FOR EACH ROW EXECUTE FUNCTION dataset_matches_source();

-- ------------------------------------------------------------ idempotency --

-- A manual import is identified by the content of the file, not by when it ran.
-- Re-running the same file is a no-op rather than a second set of sales.
CREATE TABLE IF NOT EXISTS import_batch (
    batch_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       TEXT NOT NULL REFERENCES source,
    content_hash    TEXT NOT NULL,
    filename        TEXT,
    imported_by     TEXT,
    evidence_url    TEXT,
    dataset         dataset_kind NOT NULL DEFAULT 'production',
    row_count       INTEGER NOT NULL DEFAULT 0,
    rows_inserted   INTEGER NOT NULL DEFAULT 0,
    rows_skipped    INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL CHECK (status IN ('ok','partial','failed')),
    error           TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    UNIQUE (source_id, content_hash)
);

-- Cycle status is persisted, so a retry resumes rather than repeats, and a
-- half-finished cycle is visible as half-finished.
CREATE TABLE IF NOT EXISTS cycle (
    cycle_id        UUID PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL CHECK (status IN ('running','ok','degraded','failed')),
    stages_total    INTEGER NOT NULL DEFAULT 0,
    stages_ok       INTEGER NOT NULL DEFAULT 0,
    stages_skipped  INTEGER NOT NULL DEFAULT 0,
    stages_unwired  INTEGER NOT NULL DEFAULT 0,
    stages_failed   INTEGER NOT NULL DEFAULT 0,
    summary         TEXT
);

CREATE TABLE IF NOT EXISTS cycle_stage (
    cycle_id        UUID NOT NULL REFERENCES cycle ON DELETE CASCADE,
    stage           TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('ok','skipped','unwired','failed')),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    records_in      INTEGER NOT NULL DEFAULT 0,
    records_out     INTEGER NOT NULL DEFAULT 0,
    -- A stage that wrote some of its rows and then failed says so here rather
    -- than reporting success.
    partial         BOOLEAN NOT NULL DEFAULT FALSE,
    detail          TEXT,
    PRIMARY KEY (cycle_id, stage)
);

-- One alert per opportunity per material state. The fingerprint is the
-- de-duplication key, enforced by the database rather than by hope.
ALTER TABLE alert
    ADD COLUMN IF NOT EXISTS fingerprint TEXT,
    ADD COLUMN IF NOT EXISTS cycle_id UUID;
CREATE UNIQUE INDEX IF NOT EXISTS alert_unique_material_state
    ON alert (opportunity_id, fingerprint)
    WHERE fingerprint IS NOT NULL;

-- ------------------------------------------------------- reproducibility --

ALTER TABLE opportunity_snapshot
    ADD COLUMN IF NOT EXISTS model_version TEXT NOT NULL DEFAULT 'unrecorded',
    ADD COLUMN IF NOT EXISTS risk_config JSONB,
    ADD COLUMN IF NOT EXISTS cost_assumptions JSONB,
    ADD COLUMN IF NOT EXISTS fx_rates_used JSONB,
    ADD COLUMN IF NOT EXISTS policy_sources JSONB,
    ADD COLUMN IF NOT EXISTS evidence_sale_ids BIGINT[],
    ADD COLUMN IF NOT EXISTS condition_adjusted_value_eur NUMERIC(14,4),
    ADD COLUMN IF NOT EXISTS capital_deployed_eur NUMERIC(14,4),
    ADD COLUMN IF NOT EXISTS capital_basis TEXT,
    ADD COLUMN IF NOT EXISTS upfront_cash_eur NUMERIC(14,4),
    ADD COLUMN IF NOT EXISTS expected_refund_eur NUMERIC(14,4),
    ADD COLUMN IF NOT EXISTS unresolved TEXT[],
    ADD COLUMN IF NOT EXISTS as_of TIMESTAMPTZ;

COMMENT ON COLUMN opportunity_snapshot.as_of IS
    'The calculation time the snapshot was computed for. Evidence known after '
    'this instant was excluded, which is what makes the snapshot replayable.';

-- A snapshot is unique per opportunity per cycle. Re-running a cycle updates
-- its snapshot instead of appending a second one. The name differs from the
-- non-unique lookup index of the same shape created in 0001.
CREATE UNIQUE INDEX IF NOT EXISTS opportunity_snapshot_unique_per_cycle
    ON opportunity_snapshot (opportunity_id, cycle_id);

COMMIT;
