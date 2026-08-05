# finrag — Financial News Intelligence Pipeline

End-to-end pipeline for buy-side portfolio monitoring: ingest Indian market news
→ resolve to holdings → score materiality with an LLM judge → validate coherence
→ deliver ranked email digests.

## Architecture

```
Ingest → Resolve → Score → Validate → Report → Email
  │         │        │         │          │
RSS/NSE   Fuzzy    4-dim    Structural  HTML +
RBI/SEBI  gazetteer LLM     + FinBERT   plain-text
          +pgvector judge   coherence   digest
```

**Design principle:** the entity resolver is the primary relevance gate — it drops
>90 % of articles before any LLM call. The judge only scores articles that are
already confirmed to link to a holding. Every score is stored with its
per-dimension breakdown, validation status, and FinBERT sentiment for auditing.

## Stack

| Concern | Choice | Notes |
|---------|--------|-------|
| Store | PostgreSQL + pgvector | metadata + vectors + holdings + audit in one DB |
| Embeddings | Qwen3-Embedding-0.6B | tops MTEB multilingual, Apache-2.0, CPU-friendly |
| LLM Judge | Ollama (normal) + Groq → Google (premium) | Ollama scores every article; premium tier only steps in for dispute resolution and report summaries |
| Sentiment | ProsusAI/FinBERT | directional signal; lazy-loaded, optional |
| Dedup | content_hash (SHA-256) | idempotent ingest across re-runs |
| Resolver | exact ISIN/ticker → alias → fuzzy → subsidiary → sector | no NER needed for fixed portfolio |

## Setup

```bash
# 1. Postgres 15+ with pgvector
createdb finrag
psql -d finrag -f sql/001_schema.sql
psql -d finrag -f sql/002_validation.sql
psql -d finrag -f sql/003_subsidiaries.sql

# 2. Python environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Configuration
cp .env.example .env
# edit .env — add GROQ_API_KEY (free at console.groq.com) and DATABASE_URL

# 4. Seed portfolio holdings
python -m scripts.seed

# 5. Verify
pytest -q -m "not slow"
```

## Running

```bash
# Full cycle: ingest → resolve → score → report
python -m scripts.run

# Individual stages
python -m scripts.ingest    # pull feeds
python -m scripts.resolve   # link articles to holdings
python -m scripts.score     # LLM judge (needs API key or Ollama)
python -m scripts.report    # render + send email digest

# Scheduled (runs every 15 min)
python -m scripts.run --schedule
```

Every stage is idempotent — safe to re-run at any time.

## Chat

Conversational RAG over the ingested article corpus — ask about any company
or industry, not just portfolio holdings. Mentioning a holding by name/ticker
automatically layers in its weight, sector, and recent scored coverage.

```bash
python -m scripts.chat                                   # interactive REPL
python -m scripts.chat "What's happening with Reliance?" # one-shot
```

Retrieval: embed the question (Qwen3), pull ANN candidates from pgvector,
rerank with the cross-encoder, ground the same normal-tier Ollama judge used
for routine scoring (see `finrag/chat/`) — chat never spends premium-tier
calls. Requires articles to already have embeddings (i.e. the resolve/score
stages have run at least once).

## Scoring

Each article–holding pair is scored on four dimensions (1–10):

| Dimension | Weight | Meaning |
|-----------|--------|---------|
| `materiality` | 40% | Would this move a rational investor's decision? |
| `direct_relevance` | 35% | Is this article directly about this holding? |
| `urgency` | 15% | Is this time-sensitive? |
| `credibility` | 10% | How authoritative is the source? |

`composite = weighted average`, then **rule floors** apply for deterministic
event types (e.g. `sebi_enforcement` floors at 8.0, `fraud_disclosure` at 9.0).

Articles with `composite > SCORE_THRESHOLD` (default 5.0) appear in the digest.
Articles with `composite > IMMEDIATE_THRESHOLD` (default 8.0) are flagged
high-priority.

**LLM judge — two tiers, not a single fallback chain:**
- **Normal** (`get_judge()`): Ollama only, every article×holding pair. No
  cloud fallback — raises loudly if Ollama isn't reachable rather than
  silently spending a premium-tier call.
- **Premium** (`get_premium_judge()`): Groq → Google, used *only* for
  dispute resolution — re-scoring a row the validator below flagged or
  rejected. Its verdict is final and is logged to
  `logs/dispute_verdicts.csv` (`finrag/score/dispute_log.py`) for audit.
  The same premium tier also writes the digest's executive summary
  (`finrag/report/summarize.py`) — the only other place a paid/free-cloud
  call happens.

## Validation

After scoring, every row is annotated with a `validation_status`:

| Status | Meaning |
|--------|---------|
| `passed` | All coherence checks clear |
| `flagged` | One or more checks fired — review recommended |
| `rejected` | Score is structurally invalid (e.g. judge error) |

**Structural checks (A–E):**
- `schema_invalid` — a dimension is out of range or `event_type` is empty (rejects the row outright)
- `event_type_unrecognized` — judge's `event_type` isn't a known label (suppressed when a rule floor fired)
- `credibility_authority_mismatch` — regulator source scored low-credibility, or a low-authority source scored very high
- `relevance_resolver_mismatch` — resolver matched by ISIN/ticker/exact alias but the judge scored `direct_relevance` low
- `floor_materiality_mismatch` — a rule floor fired but the judge scored `materiality` low

**FinBERT coherence checks (F–G):**
- `sentiment_floor_conflict` — negative event floor but FinBERT reads positive
- `sentiment_materiality_conflict` — high materiality but FinBERT reads neutral

View a live validation health report:
```bash
python -m scripts.calibration.validation_report           # last 7 days
python -m scripts.calibration.validation_report --days 30
```

## Calibration & Evaluation

```bash
# 1. Score a calibration batch (80 articles, explicit delay — no defaults changed)
python -m scripts.calibration.harvest

# 2. Export labelling CSVs
python -m scripts.calibration.export_label_dataset
# → labelling/label_sheet.csv      (fill gold_relevant, gold_material)
# → labelling/pipeline_output.csv  (all scores + flags)
# → labelling/flag_verdicts.csv    (fill verdict: tp / fp per flag)

# 3. Compute metrics after labelling
python -m scripts.calibration.compute_label_metrics
```

**Calibration results (57 labelled pairs, July 2026):**

| Metric | Value |
|--------|-------|
| Resolver precision | 100% (57/57) |
| Threshold 5.0 precision | 84.2% (32/38) |
| Threshold 5.0 recall | 91.4% (32/35) |
| Threshold 5.0 F1 | 0.877 |
| Flag precision (`event_type_unrecognized`) | 100% (11/11) |
| Flag precision (`sentiment_materiality_conflict`) | 100% (2/2, n<5) |

Evaluation notebook: `eval_metrics.ipynb`

## Module Map

```
finrag/
  config.py                 env-driven settings + source catalogue
  orchestrate.py            run_once(): sequences all stages, isolates errors
  scheduler.py              APScheduler fast/slow cycles
  store/
    db.py                   SQLAlchemy 2.0 ORM models
    holdings_seed.py        portfolio definition (edit this)
    subsidiaries_seed.py    subsidiary -> parent-holding map (edit this)
  ingest/
    base.py                 RawItem, normalisation, content_hash dedup
    http.py                 rate-limited, retrying HTTP client
    rss.py                  generic RSS adapter + feed registry
    nse.py                  NSE corporate filings adapter
    rbi.py                  RBI circulars adapter
    pipeline.py             run_source(): persist, dedup, log
  process/
    gazetteer.py            compile holdings + subsidiaries into fast matchers
    resolve.py              5-level resolution cascade
    pipeline.py             apply resolver, advance article status
    embed.py                Qwen3 embeddings + cross-encoder reranker
  score/
    judge.py                two-tier LLM judge: normal (Ollama), premium (Groq/Google)
    rubric.py               4-dim prompt, weighted composite, rule floors
    sentiment.py            FinBERT directional sentiment (lazy-loaded)
    validate.py             structural + sentiment coherence checks
    dispute_log.py          CSV audit trail for premium dispute resolution
    pipeline.py             embed -> retrieve -> judge -> floor -> validate -> dispute -> persist
  calibration/
    validate_report.py      read-only validation summary query helper
  report/
    render.py               HTML + plain-text digest renderer
    summarize.py            premium-tier executive summary for the digest
    pipeline.py             select unreported -> render -> send -> mark reported
  deliver/
    email.py                SMTP delivery with dry-run mode
  chat/
    retrieve.py             semantic search (pgvector + rerank) + holding matching
    llm.py                  normal (Ollama) / premium (Groq/Google) chat completion
    bot.py                  builds grounded prompt, calls normal-tier llm, returns answer+sources

scripts/
  seed.py                   seed sources, portfolio holdings, and subsidiaries
  ingest.py                 run ingest stage
  resolve.py                run resolve stage
  score.py                  run score stage
  report.py                 run report stage
  run.py                    full pipeline (or --schedule)
  chat.py                   interactive chatbot REPL (or one-shot question)
  calibration/
    validation_report.py      print validation health report
    harvest.py                 one-time calibration batch (explicit limit/delay)
    export_label_dataset.py    export labelling CSVs from scored rows
    compute_label_metrics.py   compute P/R/F1 from filled labelling CSVs

sql/
  001_schema.sql            base DDL (articles, holdings, scores, sources)
  002_validation.sql        validation columns migration
  003_subsidiaries.sql      subsidiaries reference table

eval_metrics.ipynb          calibration evaluation report with charts

tests/                      17 unit tests (pytest -q -m "not slow")
```

## Environment Variables

See `.env.example` for all options. Minimum required:

```
DATABASE_URL=postgresql+psycopg://user@localhost:5432/finrag
GROQ_API_KEY=gsk_...
```

Everything else has a sensible default.
