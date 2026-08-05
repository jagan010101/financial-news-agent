"""
finrag.score.pipeline — turn resolved articles into scored articles.

For each article with status='resolved', for each linked holding:
  1. ensure the article is embedded (vector stored for future retrieval)
  2. retrieve prior context: company-specific AND sector-wide, each scope
     selected on cosine similarity OR recency (not just "N latest")
  3. run the normal-tier judge (Ollama) -> 4 structured dimensions
  4. composite (weighted) then rule-floor override
  4b. structural validation (validate_score) + sentiment coherence (validate_sentiment)
  4c. DISPUTE RESOLUTION: if 4b flagged/rejected the row, the premium tier
      (Groq/Google, via get_premium_judge()) re-scores it. Its verdict is
      final and overwrites dims/composite/event_type; the swap is logged to
      logs/dispute_verdicts.csv via score.dispute_log for audit.
  5. write a scores row; advance article status to 'scored'

Both judges are injected (get_judge()/get_premium_judge() by default) so tests
pass deterministic mocks. Embedding is optional-injectable for the same
reason. Nothing here knows which LLM provider is configured — that's the
whole point of the interface. premium_judge=None (the default for
score_article_holding, though run() defaults to get_premium_judge()) means no
escalation is attempted at all.

Sentiment is a property of the article text, not of the holding, so it is
computed ONCE per article in run() and passed into score_article_holding via
finbert_label / finbert_score kwargs.  Both are None when
settings.sentiment_enabled is False or when the model fails to load — the
scoring path proceeds exactly as before in those cases.

Failure isolation: a judge error on one (article, holding) is logged into the
score row's rationale and does not abort the batch. Same for the premium
judge — a failed dispute-resolution call just leaves the row flagged/rejected.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select, text

from finrag.config import settings
from finrag.score.dispute_log import record_dispute
from finrag.score.judge import JudgeError, get_judge, get_premium_judge, model_tag
from finrag.score.rubric import (
    SYSTEM_PROMPT, apply_rule_floor, build_user_prompt, composite_score,
)
from finrag.score.sentiment import sentiment
from finrag.score.validate import validate_score, validate_sentiment
from finrag.store.db import Article, ArticleHolding, Holding, Score, SessionLocal, Source

# Statuses that mean "the normal-tier score needs a second opinion" — anything
# the deterministic validator didn't wave through clean.
_DISPUTED_STATUSES = frozenset({"flagged", "rejected"})

log = logging.getLogger(__name__)


def _vec_literal(embedding) -> str:
    """Format an embedding (list or numpy array) as a pgvector literal.
    Must avoid numpy's str() which truncates with '...' and inserts newlines,
    producing invalid vector syntax."""
    vals = embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)
    return "[" + ",".join(repr(float(x)) for x in vals) + "]"


def _fetch_candidate_pool(session, *, embedding, exclude_article_id: int,
                          scope_join_where: str, scope_params: dict, pool_k: int) -> list:
    """Pull a candidate pool of prior scored articles for one scope (company or
    sector): the pool_k nearest by cosine distance, UNIONed with the pool_k
    most recent. Fetching both up front means the recency pass below isn't
    starved by articles that only ever get ranked on similarity."""
    rows = session.execute(text(f"""
        with scoped as (
            select a.id as article_id, a.title, sc.event_type, sc.composite,
                   src.name as source_name, a.published_at,
                   1 - (a.embedding <=> CAST(:emb AS vector)) as similarity
            from scores sc
            join articles a on a.id = sc.article_id
            join sources src on src.id = a.source_id
            {scope_join_where}
              and a.id <> :aid
              and a.embedding is not null
        )
        (select * from scoped order by similarity desc limit :pool_k)
        union
        (select * from scoped order by published_at desc nulls last limit :pool_k)
    """), {**scope_params, "aid": exclude_article_id,
           "emb": _vec_literal(embedding), "pool_k": pool_k}).all()
    return rows


def _select_by_similarity_and_recency(pool: list) -> list:
    """From a candidate pool, independently pick articles clearing the
    similarity floor and articles inside the recency window, union them
    (dedup by article_id), and backfill to context_min_k by nearest similarity
    if the thresholds alone come up short. Most-similar first among ties."""
    if not pool:
        return []
    now = dt.datetime.now(tz=dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=settings.context_recency_hours)

    by_similarity = sorted(
        (r for r in pool if r.similarity >= settings.context_similarity_min),
        key=lambda r: -r.similarity,
    )[:settings.context_max_k]
    by_recency = sorted(
        (r for r in pool if r.published_at and r.published_at >= cutoff),
        key=lambda r: r.published_at, reverse=True,
    )[:settings.context_max_k]

    seen: set[int] = set()
    selected = []
    for r in (*by_similarity, *by_recency):
        if r.article_id not in seen:
            seen.add(r.article_id)
            selected.append(r)

    if len(selected) < settings.context_min_k:
        for r in sorted(pool, key=lambda r: -r.similarity):
            if r.article_id in seen:
                continue
            seen.add(r.article_id)
            selected.append(r)
            if len(selected) >= settings.context_min_k:
                break
    return selected


def _format_context_rows(rows: list) -> str:
    return "; ".join(
        f"[{r.event_type} score={r.composite} src={r.source_name}] {r.title[:80]}"
        for r in rows
    )


def _retrieve_context(session, holding: Holding, embedding, exclude_article_id: int) -> str:
    """Ground the judge with prior related coverage, in two scopes:
      - company: other scored articles about this same holding
      - sector: scored articles about OTHER holdings in the same sector
    Each scope is judged independently on cosine similarity and recentness
    (not a fixed "N latest"), per _select_by_similarity_and_recency.
    Returns a compact context string; empty if there's no embedding or no
    prior coverage in either scope."""
    if embedding is None:
        return ""
    pool_k = max(settings.context_max_k, settings.context_min_k)

    company_pool = _fetch_candidate_pool(
        session, embedding=embedding, exclude_article_id=exclude_article_id,
        scope_join_where="where sc.holding_id = :hid",
        scope_params={"hid": holding.id}, pool_k=pool_k)
    company_ctx = _select_by_similarity_and_recency(company_pool)

    industry_ctx = []
    if holding.sector:
        industry_pool = _fetch_candidate_pool(
            session, embedding=embedding, exclude_article_id=exclude_article_id,
            scope_join_where=(
                "join holdings h2 on h2.id = sc.holding_id "
                "where h2.sector = :sector and sc.holding_id <> :hid"
            ),
            scope_params={"sector": holding.sector, "hid": holding.id}, pool_k=pool_k)
        industry_ctx = _select_by_similarity_and_recency(industry_pool)

    parts = []
    if company_ctx:
        parts.append("Company history: " + _format_context_rows(company_ctx))
    if industry_ctx:
        parts.append("Sector context: " + _format_context_rows(industry_ctx))
    return " || ".join(parts)


def score_article_holding(session, article: Article, holding: Holding,
                          judge, embed_fn, *,
                          finbert_label: str | None = None,
                          finbert_score: float | None = None,
                          premium_judge=None) -> Score:
    """Score one (article, holding) pair and return a Score (not yet committed).

    finbert_label / finbert_score are pre-computed by run() once per article so
    the model is not re-invoked for every holding.  Pass them explicitly when
    calling from tests that want to exercise the sentiment-merge path.  Leave
    them as None (the default) to skip sentiment validation entirely — the Score
    row will have NULL in both columns and no sentiment flags will be added.

    premium_judge is the DISPUTE-RESOLUTION judge (Groq/Google via
    get_premium_judge()), only ever consulted when the normal judge's row ends
    up 'flagged' or 'rejected'. Its verdict is final and overwrites the row;
    the swap is logged via score.dispute_log. None (the default) means no
    escalation — existing callers/tests that don't pass it keep today's
    behavior exactly.
    """
    # 1. ensure embedding exists
    if article.embedding is None and embed_fn is not None:
        try:
            article.embedding = embed_fn(f"{article.title}\n{article.body or ''}")
        except Exception:
            article.embedding = None

    # 2. retrieve grounding context
    context = _retrieve_context(session, holding, article.embedding, article.id)

    # 3. judge
    src = session.get(Source, article.source_id)
    user = build_user_prompt(
        company=holding.common_name, ticker=holding.nse_symbol or "",
        sector=holding.sector, title=article.title,
        published=str(article.published_at or ""),
        source=src.name if src else str(article.source_id),
        authority=src.authority_rank if src else 4,
        body=article.body, context=context,
    )
    rationale_suffix = ""
    judge_failed = False
    try:
        dims = judge.score(SYSTEM_PROMPT, user)
    except JudgeError as e:
        judge_failed = True
        # conservative fallback: don't fabricate; mark low + record the failure
        dims = {"direct_relevance": 0, "materiality": 0, "urgency": 0,
                "credibility": 0, "event_type": "judge_error", "rationale": ""}
        rationale_suffix = f" [judge_error: {e}]"

    # 4. composite + rule floor
    base = composite_score(dims)
    article_text = f"{article.title}\n{article.body or ''}"
    floored, floor_val, floor_event = apply_rule_floor(article_text, base)
    event_type = floor_event or dims.get("event_type", "unknown")

    # 4b. validation annotation (never raises; never gates the batch)
    if judge_failed:
        v_status, v_reasons = "rejected", ["judge_error"]
    else:
        ah = session.get(ArticleHolding, (article.id, holding.id))
        v_status, v_reasons = validate_score(
            dims=dims,
            composite=floored,
            rule_floor=floor_val,
            floor_event=floor_event,
            match_method=ah.match_method if ah else "unknown",
            match_score=float(ah.match_score) if ah and ah.match_score is not None else None,
            source_authority_rank=src.authority_rank if src else 4,
            article_text=article_text,
        )
        # Merge FinBERT coherence flags when pre-computed sentiment is available.
        # Skipped for rejected rows (malformed schema) — sentiment flags would only
        # add noise when the structural checks already rejected the row.
        if finbert_label is not None and v_status != "rejected":
            extra = validate_sentiment(
                finbert_label=finbert_label,
                finbert_confidence=finbert_score or 0.0,
                floor_event=floor_event,
                materiality=dims["materiality"],
            )
            v_reasons.extend(extra)
            if extra and v_status == "passed":
                v_status = "flagged"

    # 4c. dispute resolution — a flagged/rejected row gets a second opinion
    # from the premium tier. Its verdict is FINAL: it replaces dims/composite/
    # event_type below outright, and the swap is logged to CSV for audit.
    # Left as-is (still flagged/rejected) if no premium judge is configured or
    # the premium call itself fails — never crashes the batch either way.
    final_judge = judge
    if v_status in _DISPUTED_STATUSES and premium_judge is not None:
        try:
            premium_dims = premium_judge.score(SYSTEM_PROMPT, user)
        except Exception as e:
            log.warning("premium judge failed to resolve dispute (article=%s holding=%s): %s",
                        article.id, holding.id, e)
        else:
            premium_base = composite_score(premium_dims)
            p_floored, p_floor_val, p_floor_event = apply_rule_floor(article_text, premium_base)
            p_event_type = p_floor_event or premium_dims.get("event_type", "unknown")

            record_dispute(
                article_id=article.id, holding_id=holding.id,
                holding_name=holding.common_name, dispute_reasons=list(v_reasons),
                normal_provider=model_tag(judge), normal_dims=dims, normal_composite=floored,
                premium_provider=model_tag(premium_judge), premium_dims=premium_dims,
                premium_composite=p_floored, final_composite=p_floored,
                final_event_type=p_event_type,
            )

            rationale_suffix = f" [premium-resolved via {model_tag(premium_judge)}: {'|'.join(v_reasons)}]"
            dims, floored, floor_val, floor_event, event_type = (
                premium_dims, p_floored, p_floor_val, p_floor_event, p_event_type)
            v_status = "premium_resolved"
            final_judge = premium_judge

    # 5. persist
    sc = Score(
        article_id=article.id, holding_id=holding.id,
        direct_relevance=dims["direct_relevance"], materiality=dims["materiality"],
        urgency=dims["urgency"], credibility=dims["credibility"],
        composite=floored, rule_floor=floor_val, event_type=event_type,
        rationale=(dims.get("rationale", "") + rationale_suffix).strip(),
        model=model_tag(final_judge), prompt_version=settings.prompt_version,
        scored_at=dt.datetime.now(tz=dt.timezone.utc),
        validation_status=v_status, flag_reasons=v_reasons,
        finbert_label=finbert_label, finbert_score=finbert_score,
    )
    session.add(sc)
    sc._judge_failed = judge_failed  # transient flag for batch accounting
    return sc


def run(*, session=None, judge=None, premium_judge="default", embed_fn="default",
        limit: int | None = None,
        call_delay: float | None = None) -> dict:
    import time
    delay = call_delay if call_delay is not None else settings.judge_call_delay
    own = session is None
    session = session or SessionLocal()
    if judge is None:
        judge = get_judge()
    if premium_judge == "default":
        premium_judge = get_premium_judge()  # None if Groq/Google unconfigured
    if embed_fn == "default":
        from finrag.process.embed import embed_one
        embed_fn = embed_one
    # cap per-cycle scoring to avoid exhausting the premium tier's free-tier
    # quota in one burst — disputes within a cycle are the only thing that
    # spends premium calls now, but the same cap still bounds them.
    effective_limit = limit or settings.score_per_cycle
    summary = {"articles": 0, "scores": 0, "above_threshold": 0, "errors": 0}
    try:
        q = (select(Article).where(Article.status == "resolved")
             .order_by(Article.id))
        if effective_limit:
            q = q.limit(effective_limit)
        articles = list(session.scalars(q))
        for art in articles:
            holdings = list(session.scalars(
                select(Holding)
                .join(ArticleHolding, ArticleHolding.holding_id == Holding.id)
                .where(ArticleHolding.article_id == art.id)))
            if not holdings:
                continue
            summary["articles"] += 1
            # Sentiment is a property of the text, not of the holding —
            # compute once here and pass into every score for this article.
            art_text = f"{art.title}\n{art.body or ''}"
            fb_label, fb_score = sentiment(art_text)  # (None, None) if disabled/failed
            for h in holdings:
                sc = score_article_holding(session, art, h, judge, embed_fn,
                                           finbert_label=fb_label, finbert_score=fb_score,
                                           premium_judge=premium_judge)
                summary["scores"] += 1
                if getattr(sc, "_judge_failed", False):
                    summary["errors"] += 1
                if float(sc.composite) > settings.score_threshold:
                    summary["above_threshold"] += 1
                time.sleep(delay)
            art.status = "scored"
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if own:
            session.close()
    return summary
