-- PokeArb initial schema.
--
-- Two principles shape this schema.
--
-- 1. Raw payloads are preserved verbatim in raw_record before any parsing, so
--    improved matching logic can be replayed over years of history without
--    refetching anything.
-- 2. Every derived number is joined back to the records that produced it.
--    fair_value_input is not an audit nicety; it is how the UI shows its work.
--
-- Nullability is meaningful throughout: NULL means "not measured". There is no
-- imputation layer and no column defaults to zero to stand in for missing data.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ------------------------------------------------------------------ sources
CREATE TYPE verification_status AS ENUM (
    'unverified', 'verified_api', 'verified_permitted', 'blocked'
);

CREATE TABLE source (
    source_id                   TEXT PRIMARY KEY,
    display_name                TEXT NOT NULL,
    base_url                    TEXT NOT NULL,
    market                      TEXT NOT NULL CHECK (market IN ('JP','EU','US','GLOBAL')),
    verification                verification_status NOT NULL DEFAULT 'unverified',
    enabled                     BOOLEAN NOT NULL DEFAULT FALSE,
    has_official_api            BOOLEAN,
    api_docs_url                TEXT,
    robots_checked_on           DATE,
    robots_allows_paths         BOOLEAN,
    terms_reviewed_on           DATE,
    terms_url                   TEXT,
    max_requests_per_minute     INTEGER NOT NULL DEFAULT 6,
    min_seconds_between_requests NUMERIC(8,2) NOT NULL DEFAULT 10,
    forbidden_patterns          TEXT[] NOT NULL DEFAULT '{}',
    reliability_weight          NUMERIC(4,3) NOT NULL DEFAULT 0.700,
    notes                       TEXT,
    -- A source cannot be enabled without a compliance review on file.
    CONSTRAINT enabled_requires_review CHECK (
        NOT enabled
        OR (verification <> 'unverified' AND verification <> 'blocked')
    )
);

-- ----------------------------------------------------------------- identity
CREATE TYPE card_language AS ENUM
    ('ja','en','de','fr','it','es','pt','ko','zh-Hant');
CREATE TYPE card_printing AS ENUM
    ('non-holo','holo','reverse-holo','textured','foil-other');
CREATE TYPE card_edition AS ENUM ('1st','unlimited','shadowless','n/a');

CREATE TABLE card_variant (
    variant_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_key   TEXT NOT NULL UNIQUE,
    language        card_language NOT NULL,
    set_code        TEXT NOT NULL,
    set_name_en     TEXT,
    set_name_ja     TEXT,
    series          TEXT,
    number          TEXT NOT NULL,
    printing        card_printing NOT NULL,
    edition         card_edition NOT NULL DEFAULT 'n/a',
    stamp           TEXT,
    pokemon_slug    TEXT,
    name_en         TEXT,
    name_ja         TEXT,
    year            INTEGER,
    illustrator     TEXT,
    artwork_id      TEXT,
    release_method  TEXT,
    product_origin  TEXT,
    region          TEXT,
    -- Known print run. NULL means unknown and is never estimated.
    known_print_run INTEGER,
    print_run_source_url TEXT,
    image_url       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX card_variant_block_idx ON card_variant (language, set_code, number);
CREATE INDEX card_variant_pokemon_idx ON card_variant (pokemon_slug);

-- Explicit links between confusable printings. Drives the matcher's ambiguity
-- penalty: if a sibling is equally consistent with the evidence, the match is
-- routed to manual review instead of guessing.
CREATE TABLE variant_sibling (
    variant_id      UUID NOT NULL REFERENCES card_variant ON DELETE CASCADE,
    sibling_id      UUID NOT NULL REFERENCES card_variant ON DELETE CASCADE,
    differs_on      TEXT NOT NULL,
    PRIMARY KEY (variant_id, sibling_id),
    CONSTRAINT no_self_sibling CHECK (variant_id <> sibling_id)
);

CREATE TABLE variant_alias (
    alias_id        BIGSERIAL PRIMARY KEY,
    variant_id      UUID REFERENCES card_variant ON DELETE CASCADE,
    pokemon_slug    TEXT,
    alias           TEXT NOT NULL,
    alias_language  card_language,
    -- Aliases resolve names only. They may never override a discriminator.
    CONSTRAINT alias_scope CHECK (variant_id IS NOT NULL OR pokemon_slug IS NOT NULL)
);
CREATE INDEX variant_alias_lookup ON variant_alias (lower(alias));

CREATE TYPE grader AS ENUM ('PSA','CGC','BGS','ACE','TAG');

CREATE TABLE graded_item (
    graded_item_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id      UUID NOT NULL REFERENCES card_variant,
    grading_company grader NOT NULL,
    grade           NUMERIC(3,1) NOT NULL,
    cert_number     TEXT,
    UNIQUE (grading_company, cert_number)
);

-- -------------------------------------------------------------- raw records
CREATE TABLE raw_record (
    raw_id          BIGSERIAL PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES source,
    fetched_at      TIMESTAMPTZ NOT NULL,
    request_url     TEXT NOT NULL,
    http_status     INTEGER,
    content_hash    TEXT NOT NULL,
    payload         JSONB NOT NULL,
    parsed_at       TIMESTAMPTZ,
    parse_error     TEXT
);
CREATE INDEX raw_record_source_time ON raw_record (source_id, fetched_at DESC);
CREATE UNIQUE INDEX raw_record_dedupe ON raw_record (source_id, content_hash);

-- ------------------------------------------------------------------ market
CREATE TYPE eu_condition AS ENUM ('NM','EX','GD','LP','PL','PO');
CREATE TYPE currency_code AS ENUM ('JPY','EUR','DKK','USD','GBP');

CREATE TABLE seller (
    seller_id           BIGSERIAL PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES source,
    external_id         TEXT NOT NULL,
    display_name        TEXT,
    country             TEXT,
    feedback_count      INTEGER,
    feedback_percent    NUMERIC(5,2),
    account_age_days    INTEGER,
    risk_score          NUMERIC(5,2),
    UNIQUE (source_id, external_id)
);

CREATE TABLE sale (
    sale_id             BIGSERIAL PRIMARY KEY,
    raw_id              BIGINT REFERENCES raw_record,
    source_id           TEXT NOT NULL REFERENCES source,
    external_id         TEXT,
    variant_id          UUID REFERENCES card_variant,
    graded_item_id      UUID REFERENCES graded_item,
    sold_at             TIMESTAMPTZ NOT NULL,
    price_amount        NUMERIC(14,4) NOT NULL,
    price_currency      currency_code NOT NULL,
    shipping_included   BOOLEAN NOT NULL DEFAULT FALSE,
    condition           eu_condition,
    condition_confidence NUMERIC(4,3),
    language            card_language,
    source_url          TEXT,
    match_confidence    NUMERIC(4,3),
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, external_id)
);
CREATE INDEX sale_variant_time ON sale (variant_id, sold_at DESC);

CREATE TABLE listing (
    listing_id          BIGSERIAL PRIMARY KEY,
    raw_id              BIGINT REFERENCES raw_record,
    source_id           TEXT NOT NULL REFERENCES source,
    external_id         TEXT NOT NULL,
    variant_id          UUID REFERENCES card_variant,
    graded_item_id      UUID REFERENCES graded_item,
    seller_id           BIGINT REFERENCES seller,
    first_seen_at       TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ,
    condition           eu_condition,
    source_grade_label  TEXT,
    language            card_language,
    quantity            INTEGER NOT NULL DEFAULT 1,
    source_url          TEXT,
    match_confidence    NUMERIC(4,3),
    UNIQUE (source_id, external_id)
);
CREATE INDEX listing_variant_active ON listing (variant_id) WHERE ended_at IS NULL;

CREATE TABLE listing_observation (
    observation_id      BIGSERIAL PRIMARY KEY,
    listing_id          BIGINT NOT NULL REFERENCES listing ON DELETE CASCADE,
    observed_at         TIMESTAMPTZ NOT NULL,
    price_amount        NUMERIC(14,4) NOT NULL,
    price_currency      currency_code NOT NULL
);
CREATE INDEX listing_obs_time ON listing_observation (listing_id, observed_at DESC);

-- Grade ladder snapshots. Every count is nullable; NULL means not retrieved.
CREATE TABLE population_observation (
    population_id       BIGSERIAL PRIMARY KEY,
    variant_id          UUID NOT NULL REFERENCES card_variant,
    grading_company     grader NOT NULL,
    observed_at         TIMESTAMPTZ NOT NULL,
    source_id           TEXT REFERENCES source,
    source_url          TEXT,
    total               INTEGER,
    grade_10            INTEGER,
    grade_9             INTEGER,
    grade_8             INTEGER,
    grade_7_and_below   INTEGER,
    UNIQUE (variant_id, grading_company, observed_at)
);

CREATE TABLE supply_observation (
    supply_id           BIGSERIAL PRIMARY KEY,
    variant_id          UUID NOT NULL REFERENCES card_variant,
    market              TEXT NOT NULL CHECK (market IN ('JP','EU','US')),
    observed_at         TIMESTAMPTZ NOT NULL,
    active_listings     INTEGER,
    lowest_amount       NUMERIC(14,4),
    median_amount       NUMERIC(14,4),
    currency            currency_code,
    graded_listings     INTEGER,
    raw_listings        INTEGER
);
CREATE INDEX supply_variant_time ON supply_observation (variant_id, market, observed_at DESC);

-- ----------------------------------------------------------- fx and policy
CREATE TABLE fx_rate (
    as_of           DATE NOT NULL,
    base            currency_code NOT NULL,
    quote           currency_code NOT NULL,
    rate            NUMERIC(20,10) NOT NULL,
    source_url      TEXT NOT NULL,
    PRIMARY KEY (as_of, base, quote)
);

-- Dated legal and fee parameters. Nothing in this table is hardcoded in code,
-- which is what lets a 2024 backtest use 2024's rules.
CREATE TABLE policy_parameter (
    policy_id               BIGSERIAL PRIMARY KEY,
    key                     TEXT NOT NULL,
    value                   NUMERIC(18,6) NOT NULL,
    unit                    TEXT NOT NULL,
    valid_from              DATE NOT NULL,
    valid_to                DATE,
    source_url              TEXT,
    requires_verification   BOOLEAN NOT NULL DEFAULT FALSE,
    note                    TEXT,
    CONSTRAINT valid_range CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX policy_lookup ON policy_parameter (key, valid_from DESC);

-- ---------------------------------------------------------------- condition
CREATE TABLE condition_prior (
    prior_id            BIGSERIAL PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES source,
    source_grade_label  TEXT NOT NULL,
    alpha_nm            NUMERIC(10,4) NOT NULL,
    alpha_ex            NUMERIC(10,4) NOT NULL,
    alpha_gd            NUMERIC(10,4) NOT NULL,
    alpha_lp            NUMERIC(10,4) NOT NULL,
    alpha_pl            NUMERIC(10,4) NOT NULL,
    alpha_po            NUMERIC(10,4) NOT NULL,
    evidence_n          INTEGER NOT NULL DEFAULT 0,
    is_provisional      BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    note                TEXT,
    UNIQUE (source_id, source_grade_label),
    CONSTRAINT alphas_positive CHECK (
        alpha_nm > 0 AND alpha_ex > 0 AND alpha_gd > 0
        AND alpha_lp > 0 AND alpha_pl > 0 AND alpha_po > 0
    )
);

-- ------------------------------------------------------------------ derived
CREATE TABLE fair_value_snapshot (
    fair_value_id   BIGSERIAL PRIMARY KEY,
    variant_id      UUID NOT NULL REFERENCES card_variant,
    computed_at     TIMESTAMPTZ NOT NULL,
    window_days     INTEGER,
    -- NULL value with sufficient=false is the explicit "not enough evidence"
    -- state. It is a first-class result, not a failure.
    value_amount    NUMERIC(14,4),
    value_currency  currency_code,
    sufficient      BOOLEAN NOT NULL,
    n_sales         INTEGER NOT NULL,
    n_effective     NUMERIC(10,4) NOT NULL,
    dispersion      NUMERIC(10,4),
    method          TEXT NOT NULL,
    notes           TEXT[],
    CONSTRAINT value_iff_sufficient CHECK (
        (sufficient AND value_amount IS NOT NULL)
        OR (NOT sufficient AND value_amount IS NULL)
    )
);
CREATE INDEX fair_value_variant_time ON fair_value_snapshot (variant_id, computed_at DESC);

-- The provenance join. Every fair value can name the sales behind it and the
-- weight each one carried, including the ones excluded and why.
CREATE TABLE fair_value_input (
    fair_value_id   BIGINT NOT NULL REFERENCES fair_value_snapshot ON DELETE CASCADE,
    sale_id         BIGINT NOT NULL REFERENCES sale,
    weight          NUMERIC(10,6) NOT NULL,
    included        BOOLEAN NOT NULL,
    exclusion_reason TEXT,
    PRIMARY KEY (fair_value_id, sale_id)
);

CREATE TABLE match_decision (
    decision_id         BIGSERIAL PRIMARY KEY,
    raw_id              BIGINT REFERENCES raw_record,
    source_id           TEXT NOT NULL REFERENCES source,
    observed_title      TEXT NOT NULL,
    chosen_variant_id   UUID REFERENCES card_variant,
    outcome             TEXT NOT NULL CHECK (outcome IN ('auto_match','manual_review','no_match')),
    confidence          NUMERIC(4,3) NOT NULL,
    components          JSONB NOT NULL,
    penalties           TEXT[],
    gate_failures       TEXT[],
    decided_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    review_outcome      TEXT
);
CREATE INDEX match_decision_review_queue ON match_decision (outcome, decided_at)
    WHERE outcome = 'manual_review' AND reviewed_at IS NULL;

CREATE TABLE opportunity (
    opportunity_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id          UUID NOT NULL REFERENCES card_variant,
    kind                TEXT NOT NULL CHECK (kind IN
        ('jp_eu_arbitrage','eu_steal','jp_steal','long_term','grading','sealed')),
    first_detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at           TIMESTAMPTZ,
    UNIQUE (variant_id, kind)
);

CREATE TABLE opportunity_snapshot (
    snapshot_id         BIGSERIAL PRIMARY KEY,
    opportunity_id      UUID NOT NULL REFERENCES opportunity ON DELETE CASCADE,
    cycle_id            UUID NOT NULL,
    computed_at         TIMESTAMPTZ NOT NULL,
    suppressed          BOOLEAN NOT NULL,
    suppression_reasons TEXT[],
    fair_value_id       BIGINT REFERENCES fair_value_snapshot,
    landed_cost_eur     NUMERIC(14,4),
    point_roi           NUMERIC(10,4),
    roi_p10             NUMERIC(10,4),
    roi_p25             NUMERIC(10,4),
    ranking_roi         NUMERIC(10,4),
    annualised_roi      NUMERIC(10,4),
    max_buy_jpy         NUMERIC(14,0),
    liquidity_score     NUMERIC(5,1),
    data_quality_score  NUMERIC(5,1),
    match_confidence    NUMERIC(4,3),
    condition_confidence NUMERIC(4,3),
    expected_days_to_sell NUMERIC(8,1),
    explanation         JSONB NOT NULL
);
CREATE INDEX opportunity_snapshot_cycle ON opportunity_snapshot (cycle_id);
CREATE INDEX opportunity_snapshot_rank
    ON opportunity_snapshot (computed_at DESC, ranking_roi DESC)
    WHERE NOT suppressed;

-- ------------------------------------------------------- watch and portfolio
CREATE TABLE watchlist_rule (
    rule_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label           TEXT NOT NULL,
    natural_language TEXT,
    structured      JSONB NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE alert (
    alert_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id         UUID REFERENCES watchlist_rule,
    opportunity_id  UUID REFERENCES opportunity,
    channel         TEXT NOT NULL,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The de-duplication key. A repeat alert only fires when one of the
    -- material fields has moved past its threshold, not on a timer.
    material_state  JSONB NOT NULL,
    payload         JSONB NOT NULL
);
CREATE INDEX alert_dedupe ON alert (opportunity_id, sent_at DESC);

CREATE TABLE portfolio_position (
    position_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id      UUID NOT NULL REFERENCES card_variant,
    graded_item_id  UUID REFERENCES graded_item,
    condition       eu_condition,
    acquired_on     DATE NOT NULL,
    purchase_amount NUMERIC(14,4) NOT NULL,
    purchase_currency currency_code NOT NULL,
    acquisition_source TEXT,
    disposed_on     DATE,
    disposal_amount NUMERIC(14,4),
    disposal_currency currency_code,
    notes           TEXT
);

CREATE TABLE import_log (
    log_id          BIGSERIAL PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES source,
    cycle_id        UUID,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    request_count   INTEGER NOT NULL DEFAULT 0,
    records_fetched INTEGER NOT NULL DEFAULT 0,
    records_parsed  INTEGER NOT NULL DEFAULT 0,
    matches_auto    INTEGER NOT NULL DEFAULT 0,
    matches_review  INTEGER NOT NULL DEFAULT 0,
    matches_rejected INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    error           TEXT
);

COMMIT;
