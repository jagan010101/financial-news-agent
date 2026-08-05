"""
finrag.score.rubric — the scoring logic around the judge.

Two principles:
  1. Decompose the score. A bare LLM "7/10" is uncalibrated noise. We elicit
     four dimensions, then aggregate DETERMINISTICALLY so the threshold is
     stable and auditable across model upgrades.
  2. Rules override the model. Known high-materiality event types (SEBI
     enforcement, auditor resignation, rating downgrade, pledge invocation,
     exchange surveillance) FLOOR the score regardless of what the LLM says —
     you never want a small model under-scoring a fraud disclosure.
"""
from __future__ import annotations

import re

# Per-dimension weights. THIS is the calibration surface: tune against your
# labeled set to hit the precision/recall you want, then pin.
WEIGHTS = {
    "direct_relevance": 0.35,
    "materiality":      0.40,
    "urgency":          0.15,
    "credibility":      0.10,
}

# Deterministic floors. If the article text matches a high-materiality pattern
# for a held name, the composite is floored — model cannot drag it below.
RULE_FLOORS: list[tuple[str, int, str]] = [
    (r"\bSEBI\b.*(order|penalt|ban|debar|enforce|show.cause)", 8, "sebi_enforcement"),
    (r"auditor.*(resign|withdraw)|resignation of (the )?auditor", 9, "auditor_resignation"),
    (r"(rating|outlook).*(downgrad|negative|default|\bD\b)", 8, "rating_downgrade"),
    (r"pledge.*(invok|sale)|invocation of pledge", 8, "pledge_invocation"),
    (r"(ASM|GSM|surveillance) (framework|measure|list)", 7, "exchange_surveillance"),
    (r"\b(fraud|forensic audit|siphon|round.tripp)", 9, "fraud_disclosure"),
    (r"insolvency|\bNCLT\b|\bIBC\b|bankrupt", 8, "insolvency"),
    (r"(promoter|stake) (sale|pledge|encumbr)", 7, "promoter_action"),
]

SYSTEM_PROMPT = (
    "You are a buy-side risk analyst. You assess how important a news item is "
    "for an investor who HOLDS a specific stock. You are precise, skeptical of "
    "unconfirmed reports, and you do not give investment advice — you assess "
    "materiality and urgency only. Respond ONLY with the required JSON object."
)

USER_TEMPLATE = """\
HOLDING: {company} ({ticker}), sector: {sector}

NEWS ITEM
title: {title}
published: {published}
source: {source} (authority rank {authority}, lower=more authoritative)
body:
{body}

RETRIEVED CONTEXT (prior related events, company + sector, by similarity/recency; may be empty):
{context}

Rate each dimension 0-10:
- direct_relevance: Is {company} the subject (10), a close peer/sector (5-7), or only tangential (0-3)?
- materiality: Could this change fundamentals or move the stock? Earnings, M&A,
  regulatory action, debt/rating, management change, fraud, guidance = high.
  Routine/PR/already-known = low.
- urgency: Requires attention now (10) vs purely informational (0-3)?
- credibility: Primary filing/regulator (10) vs unconfirmed media report (3-5)?

event_type: one short snake_case label (e.g. earnings_result, mna, regulatory_action,
management_change, rating_action, litigation, routine_disclosure, macro).
rationale: one sentence, concrete.
"""


def build_user_prompt(*, company, ticker, sector, title, published, source,
                      authority, body, context) -> str:
    return USER_TEMPLATE.format(
        company=company, ticker=ticker, sector=sector or "n/a",
        title=title, published=published or "n/a", source=source,
        authority=authority, body=(body or "")[:4000],
        context=(context or "(none)")[:3000],
    )


def composite_score(dims: dict) -> float:
    """Weighted aggregate of the four dimensions -> 0..10, one decimal."""
    return round(sum(dims[k] * w for k, w in WEIGHTS.items()), 1)


def apply_rule_floor(text: str, composite: float) -> tuple[float, int | None, str | None]:
    """
    Scan text for high-materiality patterns. Returns possibly-raised composite,
    the floor value applied (or None), and the matched event_type (or None).
    """
    blob = text or ""
    best_floor = 0
    best_event = None
    for pattern, floor, event in RULE_FLOORS:
        if re.search(pattern, blob, flags=re.IGNORECASE):
            if floor > best_floor:
                best_floor, best_event = floor, event
    if best_floor and best_floor > composite:
        return float(best_floor), best_floor, best_event
    return composite, (best_floor or None), best_event
