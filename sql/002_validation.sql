-- ============================================================================
-- 002_validation.sql — add validation columns to scores
--
-- All columns are nullable with no default; existing rows remain untouched.
-- Apply after 001_schema.sql:
--     psql $DATABASE_URL -f sql/002_validation.sql
-- ============================================================================

ALTER TABLE scores
    ADD COLUMN IF NOT EXISTS validation_status TEXT,
    ADD COLUMN IF NOT EXISTS flag_reasons      TEXT[],
    ADD COLUMN IF NOT EXISTS finbert_label     TEXT,
    ADD COLUMN IF NOT EXISTS finbert_score     NUMERIC;

COMMENT ON COLUMN scores.validation_status IS 'passed | flagged | rejected | NULL (unvalidated)';
COMMENT ON COLUMN scores.flag_reasons      IS 'machine-readable reason codes set by validation layer';
COMMENT ON COLUMN scores.finbert_label     IS 'positive | negative | neutral — FinBERT sentiment';
COMMENT ON COLUMN scores.finbert_score     IS 'FinBERT confidence 0..1';
