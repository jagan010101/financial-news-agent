# finRAG — Financial News Intelligence Bot
## Technical Project Report: Architecture, Methodology & Results

**Version 1.1 · July 2026**
**Status:** Ingest ✓ · Resolve ✓ · Score ✓ · Validate ✓ · Report ✓ · Calibrated ✓

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Ingestion](#3-ingestion)
4. [Deduplication](#4-deduplication)
5. [Entity Resolution](#5-entity-resolution)
6. [Embeddings & Retrieval](#6-embeddings--retrieval)
7. [Scoring Methodology](#7-scoring-methodology)
8. [LLM Judge Infrastructure](#8-llm-judge-infrastructure)
9. [Validation & Quality Controls](#9-validation--quality-controls)
10. [Reporting & Delivery](#10-reporting--delivery)
11. [Calibration & Evaluation](#11-calibration--evaluation)
12. [Pipeline Results](#12-pipeline-results)
13. [Assumptions](#13-assumptions)
14. [Known Limitations & Future Work](#14-known-limitations--future-work)

---

## 1. Executive Summary

finRAG is an end-to-end financial news intelligence pipeline designed for buy-side portfolio monitoring. It continuously ingests articles from regulatory bodies, stock exchanges, and financial media outlets; resolves them against a curated holdings registry; scores their materiality with a structured LLM rubric; and delivers ranked digests by email.

The system is built around three core design principles:

- **Idempotency** — every stage is safe to re-run without side effects
- **Authority-awareness** — primary regulatory filings are never outranked by aggregator opinions
- **Cost efficiency** — the full stack runs on free-tier APIs and local models

| Metric | Value |
|---|---|
| Active sources | 9 (7 RSS-based, 2 custom scrapers) |
| RSS feeds | 21 across 5 publishers |
| Score threshold | 5.0 (immediate alert at 8.0) |
| Embedding dimension | 1024 (Qwen3-Embedding-0.6B) |

---

## 2. Architecture Overview

The pipeline is a linear four-stage sequence. Each stage is isolated: a failure in one does not abort already-completed work. The orchestrator (`finrag.orchestrate.run_once`) sequences them and returns a structured `CycleSummary`.

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   INGEST    │ →  │   RESOLVE   │ →  │    SCORE    │ →  │   REPORT    │
│             │    │             │    │             │    │             │
│ Pull feeds  │    │ Link to     │    │ LLM judge   │    │ Email digest│
│ Dedup       │    │ holdings    │    │ 4 dimensions│    │ of alerts   │
│ Persist     │    │             │    │             │    │             │
│ ~1,950/cycle│    │ ~16% hit    │    │ max 20/cycle│    │ score > 5.0 │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

All state is stored in PostgreSQL with pgvector. Article status flows linearly:

```
ingested → resolved | irrelevant → scored
```

Each stage queries only its own status slice, making re-runs safe and the backlog always visible.

---

## 3. Ingestion

Three adapter types collect articles from different source classes. All adapters emit the same normalised `RawItem` contract; persistence is handled centrally in `finrag.ingest.pipeline`.

### 3.1 Adapter Types

| Adapter | Mechanism | Sources | Notes |
|---|---|---|---|
| `RssAdapter` | feedparser + HTTP | SEBI, Moneycontrol, ET, Livemint, BS, BusinessLine | Generic; one instance per `FeedSpec` |
| `RbiAdapter` | HTML scraper (BeautifulSoup) | RBI press releases | RSS blocked by F5 WAF; seeds homepage cookies first |
| `NseAdapter` | JSON API | NSE corporate announcements | 24-hour lookback; brotli encoding disabled to avoid decode errors |

### 3.2 Source Catalogue

All sources are registered in `SOURCE_CATALOG` (seeded into the `sources` table). The `authority_rank` field (lower = more authoritative) flows into scoring and context retrieval.

| # | Name | Kind | Authority Rank | Poll | Feeds |
|---|---|---|---|---|---|
| 1 | NSE_ANN | exchange | 1 | 600s | JSON API |
| 2 | BSE_ANN | exchange | 1 | 600s | Disabled — browser session required |
| 3 | SEBI | regulator | 1 | 3600s | 1 |
| 4 | RBI | regulator | 1 | 3600s | HTML scraper |
| 5 | MONEYCONTROL | aggregator | 4 | 1800s | 4 |
| 6 | ECONOMIC_TIMES | wire | 3 | 1800s | 3 |
| 7 | LIVEMINT | wire | 3 | 1800s | 3 |
| 8 | BUSINESS_STANDARD | wire | 3 | 1800s | 6 |
| 9 | BUSINESSLINE | wire | 3 | 1800s | 4 |

### 3.3 Active RSS Feeds (21 total)

**SEBI (1)**
- `https://www.sebi.gov.in/sebirss.xml`

**MONEYCONTROL (4)**
- `rss/latestnews.xml`
- `rss/business.xml`
- `rss/results.xml`
- `rss/economy.xml`

**ECONOMIC_TIMES (3)**
- `markets/rssfeeds/1977021501.cms`
- `cfo.economictimes.indiatimes.com/rss/policy`
- `cfo.economictimes.indiatimes.com/rss/governance-risk-compliance`

**LIVEMINT (3)**
- `rss/markets`
- `rss/companies`
- `rss/industry`

**BUSINESSLINE (4)**
- `markets/feeder/default.rss`
- `companies/feeder/default.rss`
- `portfolio/feeder/default.rss`
- `economy/feeder/default.rss`

**BUSINESS_STANDARD (6)**
- `rss/markets-106.rss`
- `rss/companies-101.rss`
- `rss/companies/quarterly-results-10103.rss`
- `rss/industry-217.rss`
- `rss/economy-102.rss`
- `rss/finance-103.rss`

> **Body handling:** RSS feeds provide title + summary only. Full article body hydration is deliberately deferred to a later, entity-gated step so bandwidth is spent only on articles about held names.

---

## 4. Deduplication

Deduplication operates at two levels: within a single ingest batch, and across all historical ingests via a database constraint.

### 4.1 content_hash

Each article's hash is computed as:

```
SHA-256(normalised_title + "\x1f" + normalised_body)
```

Normalisation applies Unicode NFKC and collapses whitespace. The hash is stored in a `UNIQUE` column on `articles`.

```python
# persist_items — finrag/ingest/pipeline.py
stmt = insert(Article).values(rows).on_conflict_do_nothing(
    index_elements=[Article.content_hash]
).returning(Article.id)
new_ids = session.execute(stmt).scalars().all()
# len(new_ids) is the only source of truth for "new" count
```

Within a single batch, a `seen_hashes` set provides an in-memory pre-filter before the database insert.

### 4.2 Cross-source Behaviour

The same news *event* covered by four outlets produces four distinct rows in `articles`. This is intentional: an RBI circular and a Moneycontrol opinion on the same rate decision contain genuinely different information. Deduplication is not applied across source kinds.

Within the same source kind, near-duplicate detection via the `dup_of` foreign key and minhash configuration is structurally supported but not yet populated — identified as a future improvement.

> **Limitation:** Four wires publishing near-identical text about the same event will each be stored and scored separately. The context retrieval layer mitigates this (see §6.3), but LLM calls are still issued per article.

---

## 5. Entity Resolution

The resolver links each ingested article to one or more portfolio holdings. This is the critical pre-filter: only *resolved* articles proceed to LLM scoring. Articles with no holding match are marked `irrelevant` and never scored, protecting rate-limited API quotas.

### 5.1 Resolution Cascade

Four methods are tried in descending confidence order. Once a holding is matched by a higher-confidence method, lower-confidence methods are skipped for that holding.

| Priority | Method | Confidence | Mechanism |
|---|---|---|---|
| 1 | `exact_id` | 1.00 | NSE symbol / BSE code / ISIN as standalone token |
| 2 | `alias_exact` | 0.95 | Curated alias as word-boundary phrase (case-insensitive) |
| 3 | `alias_fuzzy` | score | RapidFuzz `token_set_ratio` ≥ 90, head word present |
| 4 | `sector` | 0.40 | Sector cue regex, only when no company match found |

### 5.2 Ambiguity Resolution

Indian conglomerate names frequently collide across distinct listed entities. An `AMBIGUOUS_BLOCKERS` dictionary vetoes a match when a blocker phrase is present and no unambiguous identifier independently confirms the correct entity.

For example, "Reliance" is blocked if "Reliance Power", "Reliance Capital", or "ADAG" appears without an accompanying ISIN, ticker, or multi-word alias that definitively identifies Reliance Industries.

### 5.3 Gazetteer

The `Gazetteer` is built fresh each cycle from the active holdings table. It pre-indexes three lookup structures:

| Index | Contents |
|---|---|
| `exact_ids` | ISIN, NSE symbol, BSE code → holding_id |
| `alias_index` | Casefolded alias → holding_id (phrase match) |
| `alias_list` | Flat list for fuzzy scanning of unmatched holdings only |

> **Precision over recall by design.** A fuzzy candidate below threshold yields no link rather than a speculative one. The threshold (90 `token_set_ratio`) and ambiguity blockers are tunable against a labelled set.

---

## 6. Embeddings & Retrieval

### 6.1 Embedding Model

Text embeddings are produced by **Qwen/Qwen3-Embedding-0.6B** via `sentence-transformers`. The model tops MTEB multilingual benchmarks, is Apache-2.0 licensed, and runs on CPU/MPS without a GPU. Output dimensionality is 1024, matching the `vector(1024)` pgvector column.

Fallback: `BAAI/bge-m3` (also 1024-dim, schema-compatible).

### 6.2 Retrieve-then-Rerank

The reranker (**Qwen/Qwen3-Reranker-0.6B**, a cross-encoder) jointly encodes query–document pairs to reorder bi-encoder candidates. Configuration:

| Parameter | Value |
|---|---|
| `rerank_candidates` | 50 (bi-encoder pulls this many) |
| `rerank_top_k` | 5 (cross-encoder keeps this many) |

### 6.3 Context Retrieval for Scoring

When scoring an article against a holding, up to *k* prior scored articles are retrieved as grounding context for the judge. The retrieval query enforces two quality properties:

1. **Authority penalty:** vector distance is adjusted by `(authority_rank − 1) × 0.05`, so a regulator article (rank 1) scores as if it were 0.15 closer than an aggregator (rank 4) with identical embedding similarity.

2. **Kind diversity:** `DISTINCT ON (src.kind)` ensures context slots are filled with one best article per source kind (regulator, wire, aggregator) — not three paraphrases of the same story from competing wires.

```sql
select title, event_type, composite, source_name from (
    select distinct on (src.kind)
        a.title, sc.event_type, sc.composite, src.name as source_name
    from scores sc
    join articles a on a.id = sc.article_id
    join sources src on src.id = a.source_id
    where sc.holding_id = :hid and a.id <> :aid
      and a.embedding is not null
    order by src.kind,
        (a.embedding <=> CAST(:emb AS vector))
        + (src.authority_rank - 1) * 0.05::double precision
) ranked
limit :k
```

---

## 7. Scoring Methodology

Every resolved article is scored against each linked holding by a structured LLM judge. Scores are decomposed across four dimensions, then aggregated deterministically.

### 7.1 Dimension Weights

| Dimension | Weight | Description |
|---|---|---|
| `materiality` | 40% | Could this change fundamentals or move the stock? |
| `direct_relevance` | 35% | Is the holding the subject, a peer, or tangential? |
| `urgency` | 15% | Requires attention now vs. purely informational? |
| `credibility` | 10% | Primary filing vs. unconfirmed media report? |

```
Composite = Σ(dimension × weight)   range: 0–10, one decimal place
```

### 7.2 Source Authority in Scoring

The `authority_rank` and source name are passed into the judge prompt explicitly, enabling the model to calibrate `credibility` scores appropriately. An RBI circular carries intrinsically higher credibility than a Moneycontrol editorial on the same topic without requiring hard-coded rules.

### 7.3 Rule Floors

Deterministic regex patterns override the LLM composite for known high-materiality event types. The floor always wins if it exceeds the composite.

| Floor Score | Event Type | Trigger Pattern |
|---|---|---|
| 9 | `auditor_resignation` | Auditor resigns or withdraws |
| 9 | `fraud_disclosure` | Fraud, forensic audit, siphoning, round-tripping |
| 8 | `insolvency` | NCLT, IBC, bankruptcy proceedings |
| 8 | `sebi_enforcement` | SEBI order, penalty, ban, debarment, show cause |
| 8 | `rating_downgrade` | Rating or outlook downgrade / default |
| 8 | `pledge_invocation` | Promoter pledge invoked or sold |
| 7 | `promoter_action` | Promoter stake sale, pledge, encumbrance |
| 7 | `exchange_surveillance` | ASM / GSM / surveillance framework placement |

### 7.4 Thresholds

| Threshold | Value | Action |
|---|---|---|
| `score_threshold` | 5.0 | Include in digest email |
| `immediate_threshold` | 8.0 | Flag as urgent in digest subject line |

### 7.5 Rate Limiting

- Maximum **20 articles scored per cycle** (`score_per_cycle`) to prevent backlog bursts
- **8-second inter-call delay** (`judge_call_delay`) to pace Groq's free tier at ~12k TPM
- Temperature **0.0** for deterministic, auditable scoring

---

## 8. LLM Judge Infrastructure

The judge layer is provider-agnostic. Three backends are wired in a fallback chain; all enforce structured JSON output.

| Provider | Model | Tier | Structured Output |
|---|---|---|---|
| Groq | `llama-3.3-70b-versatile` | Free (30 RPM / daily cap) | `response_format: json_object` |
| Google AI Studio | `gemini-2.5-flash` | Free (daily quota) | `responseSchema` (strict) |
| Ollama (local) | `qwen2.5:7b-instruct` | Free, offline, private | Native JSON schema format |

**Fallback logic:** `FallbackJudge` advances to the next provider on `DailyLimitError`. Transient rate limits (429 RPM) are retried with exponential backoff via tenacity (8 attempts, 10–120s wait). The active provider index persists across all articles in one cycle — a single Groq quota exhaustion causes exactly one fallback switch per run, not one per article.

**Fallback chain at runtime:**
```
groq/llama-3.3-70b-versatile → google/gemini-2.5-flash → ollama/qwen2.5:7b-instruct
```

> **Optional paid path:** An Anthropic Claude backend (`claude-sonnet-4-6`) is configured but reserved for report narrative generation rather than per-article scoring.

---

## 9. Validation & Quality Controls

After scoring, every article–holding pair is annotated with a `validation_status` (`passed`, `flagged`, `rejected`) and a list of `flag_reasons`. These are stored alongside the score for auditability and downstream filtering.

### 9.1 Structural Checks (A–E)

Five deterministic checks run on every scored row using only the score dimensions and metadata — no additional LLM calls.

| Check | Flag | Condition |
|-------|------|-----------|
| A | `credibility_low` | composite > 6.0 but credibility ≤ 3 |
| B | `regulator_credibility_mismatch` | source is regulator (rank 1) but credibility < 7 |
| C | `floor_materiality_mismatch` | rule floor fired but materiality < 5 |
| D | `sector_match_high_composite` | match_method = sector but composite > 7.0 |
| E | `negative_event_high_composite` | known-negative event type with composite > 7.0 |

### 9.2 FinBERT Sentiment Coherence Checks (F–G)

FinBERT (ProsusAI/finbert) produces a directional sentiment label (`positive`, `neutral`, `negative`) and confidence score for each article. Two coherence checks compare this signal against what the LLM judge scored.

| Check | Flag | Condition |
|-------|------|-----------|
| F | `sentiment_floor_conflict` | negative rule-floor event but FinBERT reads positive (conf > 0.6) |
| G | `sentiment_materiality_conflict` | materiality ≥ 8 but FinBERT reads neutral (conf > 0.8) |

FinBERT is **lazy-loaded** on first use and computed **once per article** (not per holding), so an article linked to three holdings incurs a single inference call. When `settings.sentiment_enabled=False` or if the model fails to load, both columns are stored as `NULL` and no sentiment flags are added — scoring proceeds exactly as before.

### 9.3 Validation-Status Distribution

From the calibration batch (124 scored rows, July 2026):

| Status | Count | % |
|--------|-------|---|
| `passed` | 99 | 79.8% |
| `flagged` | 25 | 20.2% |
| `rejected` | 0 | 0.0% |

### 9.4 Validation Report

```bash
python -m scripts.calibration.validation_report            # last 7 days
python -m scripts.calibration.validation_report --days 30  # 30-day window
python -m scripts.calibration.validation_report --days 0   # all time
```

---

## 10. Reporting & Delivery

The report stage selects all above-threshold scored articles not yet included in a sent report, renders a digest, and emails it.

**Idempotency:** article IDs are recorded in a `reports` table as `pending` before the send attempt. A crash mid-send leaves an auditable row rather than silently losing the record.

| Property | Detail |
|---|---|
| Transport | SMTP via Gmail App Password (`.env`), port 587 |
| Format | Multipart HTML + plain text fallback |
| Subject | `[Portfolio] N item(s) — M urgent` |
| Dedup | `article_ids` checked against all previously sent reports |
| Grouping | One row per article; highest composite score wins if multiple holdings matched |

---

## 11. Calibration & Evaluation

A calibration batch of 80 articles was scored and manually labelled to evaluate resolver precision, scoring threshold accuracy, and validation flag precision.

### 11.1 Dataset

| Property | Value |
|----------|-------|
| Articles scored | 80 |
| Score rows written | 124 (articles × matched holdings) |
| Pairs labelled | 57 (67 excluded — blank `gold_material`) |
| Borderlines excluded from P/R | 14 |
| Strict subset for threshold eval | 43 (35 material, 8 not_material) |
| Flag verdicts labelled | 13 |

### 11.2 Section 1 — Resolver Precision

| match_method | n | gold_relevant | precision |
|---|---|---|---|
| `exact_id` | 6 | 6 | 100% |
| `alias_exact` | 10 | 10 | 100% |
| `sector` | 41 | 41 | 100% |
| **ALL** | **57** | **57** | **100%** |

All resolver methods achieved 100% precision on this sample. The sector-based fallback (n=41) produced no false links, suggesting the sector cue regexes are sufficiently specific for the current 5-holding portfolio.

> **Caveat:** sector precision at 100% may reflect that the news corpus is already company-specific at current scale. Re-evaluate as the holding count and feed diversity grow.

### 11.3 Section 2 — Threshold Precision / Recall / F1

Base rate of material articles in strict subset: **81.4%** (35/43).

| Threshold | Precision | Recall | F1 | TP | FP | FN | TN |
|-----------|-----------|--------|----|----|----|----|-----|
| 4.0 | 0.846 (33/39) | 0.943 (33/35) | 0.892 | 33 | 6 | 2 | 2 |
| **5.0** *(default)* | **0.842 (32/38)** | **0.914 (32/35)** | **0.877** | **32** | **6** | **3** | **2** |
| 6.0 | 0.944 (17/18) | 0.486 (17/35) | 0.642 | 17 | 1 | 18 | 7 |

**Key finding:** Threshold 5.0 and 4.0 are nearly identical in precision (84.2% vs 84.6%) but 4.0 recovers 1 additional TP with no additional FP. Threshold 6.0 achieves high precision at severe recall cost — it misses 18 of 35 material events.

**Recommendation:** Lower threshold from 5.0 → 4.0. At current corpus size, the cost of missed material events outweighs false positives.

### 11.4 Section 3 — Per-Flag Precision

| Flag | n | TP | FP | Precision |
|------|---|----|----|-----------|
| `event_type_unrecognized` | 11 | 11 | 0 | 100% |
| `sentiment_materiality_conflict` | 2 | 2 | 0 | 100% (n<5) |
| **ALL** | **13** | **13** | **0** | **100%** |

All 13 labelled flag-verdicts were true positives after correcting initial mislabelling (3 `event_type_unrecognized` flags were initially marked FP, but review confirmed the flags correctly identified wrong event_type assignments on material articles).

Full evaluation notebook with charts: `eval_metrics.ipynb`

---

## 12. Pipeline Results

Run logs from two consecutive cycles during initial setup (30 June 2026).

### Cycle 1 — Initial Bulk Ingest

```
INFO  finrag.orchestrate: stage ingest ok in 36.0s
      fetched=1952  new=428  sources=23  errors=0
INFO  finrag.orchestrate: stage resolve ok in 0.2s
      processed=428  resolved=68  irrelevant=360  links=105
ERROR finrag.orchestrate: stage score FAILED
      psycopg.errors.UndefinedFunction: operator does not exist: vector + numeric
INFO  finrag.orchestrate: stage report ok — skipped_empty=True
```

| Metric | Value | Note |
|---|---|---|
| Fetched | 1,952 | Across 23 feed instances |
| New articles | 428 | 22% unique; rest already stored |
| Resolved | 68 | 16% of new articles matched holdings |
| Links created | 105 | avg 1.5 holdings per resolved article |

### Cycle 2 — Steady State (after bug fix)

```
INFO  finrag.orchestrate: stage ingest ok in 34.8s
      fetched=1973  new=24  sources=23  errors=0
INFO  finrag.orchestrate: stage resolve ok in 0.0s
      processed=24  resolved=3  irrelevant=21  links=4
INFO  finrag.score.judge: LLM fallback chain:
      groq/llama-3.3-70b-versatile → google/gemini-2.5-flash → ollama/qwen2.5:7b-instruct
INFO  finrag.orchestrate: stage score ok
INFO  finrag.orchestrate: stage report ok — skipped_empty=True
```

> **Score stage bug (fixed):** The authority-rank penalty expression `embedding <=> vec + (rank-1)*0.05` was parsed as `vec + numeric` due to operator precedence. Fixed by wrapping the distance expression: `(embedding <=> vec) + (rank-1)*0.05::double precision`.

### Resolve Rate Analysis

84% of ingested articles are correctly classified as irrelevant and filtered before the LLM stage. The 16% resolution rate on a general-purpose financial feed is consistent with a focused portfolio — the resolver is conservative by design. The sector fan-out (method 4) provides a safety net for macro articles that name no specific company but are relevant to held sectors.

---

## 13. Assumptions

**A1 — content_hash is a sufficient dedup key.**
Two articles with identical normalised title + body are assumed to be the same article. Paraphrased rewrites of the same story from different publishers hash differently and are stored as separate rows — this is the intended behaviour.

**A2 — RSS summaries are sufficient for resolution and scoring.**
Full article body hydration is deferred. The summary (typically 1–3 sentences) contains enough signal for entity resolution and a reasonable materiality judgement.

**A3 — authority_rank is correctly assigned per source kind.**
Regulators (rank 1) are treated as ground truth. The credibility dimension in the judge prompt relies on this signal — a misconfigured rank silently degrades scoring accuracy.

**A4 — The holdings registry aliases are comprehensive.**
The entire recall of the entity resolver is bounded by the alias lists in `PORTFOLIO`. A missing alias (brand name, old name, subsidiary) will silently drop material articles.

**A5 — Groq free-tier latency and quota are acceptable.**
At 8s inter-call delay and 20 articles/cycle, a full scoring batch takes ~3 minutes. If Groq daily limits are hit, the Google fallback is expected to be available.

**A6 — Source feed URLs are stable.**
RSS feed URLs are hardcoded. Publisher-side URL changes or feed deprecations produce silent zero-fetch cycles, visible only via `ingest_log` monitoring.

**A7 — DISTINCT ON (src.kind) covers all meaningful source diversity.**
Three kinds (regulator, wire, aggregator) are assumed sufficient to represent distinct informational roles. Exchange kind (NSE filings) is present but would benefit from its own context slot.

---

## 14. Known Limitations & Future Work

| Area | Status | Limitation | Potential Fix |
|------|--------|-----------|---------------|
| Dedup | Open | `dup_of` column exists but near-dup detection unimplemented — same event from 4 wires triggers 4 LLM calls | Minhash + cosine detection within same source kind; skip scoring on flagged dups |
| Hydration | Open | Only RSS summary scored, not full article body | Entity-gated full-page fetch for resolved articles above confidence threshold |
| BSE | Open | BSE corporate announcements require a browser session | Playwright adapter or BSE data vendor API |
| Threshold | Eval done | Default 5.0 misses 1 TP vs 4.0 with no extra FP | Lower `SCORE_THRESHOLD` to 4.0 (calibration-confirmed) |
| Sector fan-out | Open | Sector cues manually authored and sparse | Learned sector classifier using embedding model |
| Exchange context | Open | NSE filings share `DISTINCT ON` slot with regulators | Separate `exchange` from `regulator` kind in context retrieval |
| Score drift | Partial | Prompt version tracked; no automated regression test | Freeze labelled eval set; run scorer before/after any model change |
| Feed monitoring | Open | Silent zero-fetch on URL change — no alerting | Alert on `ingest_log` rows with `fetched=0` and no error for N consecutive cycles |
| Label coverage | Open | 67 of 124 calibration pairs have blank `gold_material` | Complete labelling pass to improve P/R confidence intervals |
