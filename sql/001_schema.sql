-- ============================================================================
-- finrag schema  (Postgres 15+ with pgvector)
-- Design notes:
--   * One store for metadata, vectors, holdings, and audit/calibration log.
--   * Vectors live alongside articles so retrieval + filtering is one query.
--   * Every score is logged with its full rationale -> enables later
--     calibration and post-hoc precision/recall analysis.
--   * Entity resolution is a MANY-TO-MANY: one article can touch several
--     holdings (e.g. a sector circular), one holding has many articles.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- fuzzy company-name matching

-- ----------------------------------------------------------------------------
-- PORTFOLIO / HOLDINGS REGISTRY
-- The backbone. Every news item is resolved against this.
-- Keyed by multiple identifiers because sources disagree on naming.
-- ----------------------------------------------------------------------------
CREATE TABLE holdings (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    isin            TEXT UNIQUE,                 -- canonical, exchange-agnostic
    nse_symbol      TEXT,
    bse_code        TEXT,                         -- numeric scrip code as text
    legal_name      TEXT NOT NULL,
    common_name     TEXT NOT NULL,
    sector          TEXT,                         -- maps macro/sector news -> holding
    industry        TEXT,
    aliases         TEXT[] DEFAULT '{}',          -- ["HDFC Bank","HDFCBANK",...]
    is_active       BOOLEAN DEFAULT TRUE,         -- soft-delete on exit
    weight          NUMERIC,                      -- optional portfolio weight
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_holdings_nse ON holdings (nse_symbol);
CREATE INDEX idx_holdings_bse ON holdings (bse_code);
CREATE INDEX idx_holdings_sector ON holdings (sector);
-- trigram index for fuzzy alias matching during entity resolution
CREATE INDEX idx_holdings_name_trgm ON holdings USING gin (common_name gin_trgm_ops);

-- ----------------------------------------------------------------------------
-- SOURCES catalogue (controls polling + authority weighting)
-- authority_rank: 1=primary regulator/exchange filing, higher=less authoritative
-- ----------------------------------------------------------------------------
CREATE TABLE sources (
    id              SMALLINT PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,         -- 'NSE_ANN','RBI','SEBI','MONEYCONTROL'
    kind            TEXT NOT NULL,                -- 'exchange'|'regulator'|'wire'|'aggregator'
    authority_rank  SMALLINT NOT NULL,            -- lower = more authoritative
    poll_seconds    INT NOT NULL DEFAULT 900,
    is_enabled      BOOLEAN DEFAULT TRUE
);

-- ----------------------------------------------------------------------------
-- ARTICLES (raw + normalized + vector). One row per de-duplicated item.
-- content_hash is the dedup key (sha256 of normalized title+body).
-- dup_of points the losing duplicate at the surviving canonical row.
-- ----------------------------------------------------------------------------
CREATE TABLE articles (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id       SMALLINT REFERENCES sources(id),
    url             TEXT,
    external_id     TEXT,                         -- source-native id if any
    title           TEXT NOT NULL,
    body            TEXT,
    published_at    TIMESTAMPTZ,
    fetched_at      TIMESTAMPTZ DEFAULT now(),
    content_hash    TEXT NOT NULL,                -- dedup key
    minhash         BYTEA,                        -- serialized MinHash for LSH
    embedding       vector(1024),                 -- BGE-M3 = 1024 dims
    lang            TEXT DEFAULT 'en',
    raw             JSONB,                        -- original payload, audit trail
    event_type      TEXT,                         -- filled by scorer
    dup_of          BIGINT REFERENCES articles(id),
    status          TEXT DEFAULT 'ingested',      -- ingested|resolved|scored|reported|archived
    UNIQUE (content_hash)
);
CREATE INDEX idx_articles_status     ON articles (status);
CREATE INDEX idx_articles_published  ON articles (published_at DESC);
CREATE INDEX idx_articles_fts        ON articles USING gin (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(body,'')));
-- HNSW for fast cosine ANN search (pgvector >= 0.5)
CREATE INDEX idx_articles_embedding  ON articles USING hnsw (embedding vector_cosine_ops);

-- ----------------------------------------------------------------------------
-- ARTICLE <-> HOLDING links (entity resolution output)
-- match_method records HOW we linked, for auditing precision of the resolver.
-- ----------------------------------------------------------------------------
CREATE TABLE article_holdings (
    article_id      BIGINT REFERENCES articles(id) ON DELETE CASCADE,
    holding_id      BIGINT REFERENCES holdings(id) ON DELETE CASCADE,
    match_method    TEXT NOT NULL,                -- 'isin'|'symbol'|'fuzzy_name'|'sector'|'subsidiary'
    match_score     NUMERIC,                      -- fuzzy ratio etc.
    PRIMARY KEY (article_id, holding_id)
);

-- ----------------------------------------------------------------------------
-- SCORES (one row per article x holding scoring event)
-- Stores per-dimension breakdown + composite + rationale -> calibration ready.
-- model + prompt_version pin reproducibility across upgrades.
-- ----------------------------------------------------------------------------
CREATE TABLE scores (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    article_id      BIGINT REFERENCES articles(id) ON DELETE CASCADE,
    holding_id      BIGINT REFERENCES holdings(id) ON DELETE CASCADE,
    direct_relevance SMALLINT,
    materiality      SMALLINT,
    urgency          SMALLINT,
    credibility      SMALLINT,
    composite        NUMERIC NOT NULL,            -- 0-10
    rule_floor       SMALLINT,                    -- deterministic override applied, if any
    event_type       TEXT,
    rationale        TEXT,
    model            TEXT,                         -- e.g. 'ollama/qwen2.5:7b'
    prompt_version   TEXT,
    scored_at        TIMESTAMPTZ DEFAULT now(),
    -- ground-truth label you add later for calibration (NULL until labeled)
    label_material   BOOLEAN
);
CREATE INDEX idx_scores_article  ON scores (article_id);
CREATE INDEX idx_scores_composite ON scores (composite DESC);

-- ----------------------------------------------------------------------------
-- REPORTS / ALERTS sent (idempotency + don't email same item twice)
-- ----------------------------------------------------------------------------
CREATE TABLE reports (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind            TEXT NOT NULL,                -- 'digest'|'immediate'
    payload         JSONB,                        -- rendered report contents
    article_ids     BIGINT[],
    sent_at         TIMESTAMPTZ,
    delivery_status TEXT DEFAULT 'pending'        -- pending|sent|failed
);

-- ----------------------------------------------------------------------------
-- INGEST LOG (observability: every poll cycle, counts, errors)
-- ----------------------------------------------------------------------------
CREATE TABLE ingest_log (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   SMALLINT REFERENCES sources(id),
    started_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ,
    fetched     INT DEFAULT 0,
    new_items   INT DEFAULT 0,
    error       TEXT
);
