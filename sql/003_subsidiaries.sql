-- ============================================================================
-- SUBSIDIARIES reference table
-- Maps a subsidiary/brand name to its DIRECT listed parent (by NSE ticker),
-- so entity resolution can attribute subsidiary-only news to the parent
-- holding (article_holdings.match_method = 'subsidiary'), per the design
-- note in holdings_seed.py: "news on a subsidiary is material to the parent."
--
-- parent_nse_symbol joins holdings.nse_symbol. A row whose parent isn't
-- currently in `holdings` is simply inert (no link is ever produced) until
-- that parent is added to the portfolio -- this table is a general reference
-- set, not scoped to the current holdings.
-- ============================================================================
CREATE TABLE subsidiaries (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_nse_symbol TEXT NOT NULL,          -- ticker of the DIRECT listed parent
    parent_name       TEXT NOT NULL,          -- readable name, for when the parent isn't (yet) a holding
    subsidiary_name   TEXT NOT NULL,
    aliases           TEXT[] DEFAULT '{}',    -- brand names / alternate spellings for the subsidiary
    notes             TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_subsidiaries_parent ON subsidiaries (parent_nse_symbol);
CREATE UNIQUE INDEX uq_subsidiaries_parent_subsidiary ON subsidiaries (parent_nse_symbol, subsidiary_name);
