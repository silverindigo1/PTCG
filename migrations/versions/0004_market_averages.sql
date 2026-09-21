-- 0004: published market averages, kept apart from completed sales.
--
-- A marketplace's published average is weaker evidence than a counted sale:
-- nothing says how many transactions produced it. It gets its own table so it
-- can never be read back as a sale, and every row records whether it passed
-- the acceptance rules and, if not, why.

BEGIN;

CREATE TABLE IF NOT EXISTS market_average_observation (
    observation_id       BIGSERIAL PRIMARY KEY,
    variant_id           UUID REFERENCES card_variant,
    provider             TEXT NOT NULL,              -- 'cardmarket'
    via                  TEXT NOT NULL,              -- 'tcgdex'
    external_card_id     TEXT,                       -- e.g. 'SV2a-173'
    product_id           TEXT,                       -- the provider's own product id
    finish               TEXT NOT NULL CHECK (finish IN ('base','reverse')),
    currency             currency_code NOT NULL,
    provider_updated_at  TIMESTAMPTZ NOT NULL,
    known_at             TIMESTAMPTZ NOT NULL,       -- fetch time; the look-ahead boundary
    avg                  NUMERIC(14,4),
    low                  NUMERIC(14,4),
    trend                NUMERIC(14,4),
    avg1                 NUMERIC(14,4),
    avg7                 NUMERIC(14,4),
    avg30                NUMERIC(14,4),
    market               TEXT NOT NULL DEFAULT 'EU',
    language             card_language,
    source_url           TEXT,
    raw_hash             TEXT,
    accepted             BOOLEAN NOT NULL,
    value_used           NUMERIC(14,4),
    refusal_reasons      TEXT[],
    dataset              dataset_kind NOT NULL DEFAULT 'production',
    -- One row per published update per printing. Re-checking the same card on
    -- the same day does not add rows.
    UNIQUE (variant_id, provider, finish, provider_updated_at)
);

CREATE INDEX IF NOT EXISTS market_average_lookup
    ON market_average_observation (variant_id, known_at DESC);

COMMENT ON TABLE market_average_observation IS
    'Published averages (Cardmarket via TCGdex). Never fair-value sales evidence.';

COMMIT;
