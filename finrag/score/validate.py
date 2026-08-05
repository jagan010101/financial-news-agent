"""
finrag.score.validate — deterministic, pure validation layer.

Two public functions, both pure (no I/O, no model calls):

  validate_score(...)       — structural/coherence checks on the LLM dims;
                              returns (validation_status, flag_reasons).

  validate_sentiment(...)   — FinBERT coherence checks; takes pre-computed
                              label+confidence; returns extra flag_reasons.
                              Call after sentiment() and merge into the list
                              returned by validate_score.

Keeping them separate means the deterministic checks stay independently
testable without any model dependency.
"""
from __future__ import annotations

from finrag.score.rubric import RULE_FLOORS as _RULE_FLOORS

_DIMS = ("direct_relevance", "materiality", "urgency", "credibility")

# Floor event names come directly from RULE_FLOORS so edits there propagate here.
_FLOOR_EVENT_NAMES: frozenset[str] = frozenset(event for _, _, event in _RULE_FLOORS)

# Labels come from the USER_TEMPLATE examples in rubric.py (the "event_type:"
# line of the prompt). If you add examples there, mirror them here.
_TEMPLATE_LABELS: frozenset[str] = frozenset({
    "earnings_result", "mna", "regulatory_action", "management_change",
    "rating_action", "litigation", "routine_disclosure", "macro",
})

ALLOWED_EVENT_TYPES: frozenset[str] = (
    _FLOOR_EVENT_NAMES | _TEMPLATE_LABELS | {"unknown", "judge_error"}
)

_HIGH_CONFIDENCE_METHODS = frozenset({"exact_id", "alias_exact"})


def validate_score(
    *,
    dims: dict,
    composite: float,
    rule_floor: int | None,
    floor_event: str | None,
    match_method: str,
    match_score: float | None,
    source_authority_rank: int,
    article_text: str,
) -> tuple[str, list[str]]:
    """Returns (validation_status, flag_reasons). Pure — no I/O, no models."""
    reasons: list[str] = []

    # ── A. SCHEMA / RANGE ── reject-level; return immediately ────────────────
    schema_ok = True
    for dim in _DIMS:
        v = dims.get(dim)
        if not isinstance(v, int) or not (0 <= v <= 10):
            schema_ok = False
            break
    if schema_ok and not str(dims.get("event_type", "")).strip():
        schema_ok = False
    if not schema_ok:
        return ("rejected", ["schema_invalid"])

    # ── B. EVENT_TYPE ENUM ── flag only; suppressed when rule floor overwrote ─
    if floor_event is None and dims["event_type"] not in ALLOWED_EVENT_TYPES:
        reasons.append("event_type_unrecognized")

    # ── C. CREDIBILITY vs AUTHORITY ──────────────────────────────────────────
    credibility = dims["credibility"]
    if source_authority_rank == 1 and credibility <= 4:
        reasons.append("credibility_authority_mismatch")
    elif source_authority_rank >= 4 and credibility >= 9:
        reasons.append("credibility_authority_mismatch")

    # ── D. RELEVANCE vs RESOLVER ─────────────────────────────────────────────
    if match_method in _HIGH_CONFIDENCE_METHODS and dims["direct_relevance"] <= 3:
        reasons.append("relevance_resolver_mismatch")

    # ── E. FLOOR vs MODEL ────────────────────────────────────────────────────
    if floor_event is not None and dims["materiality"] <= 3:
        reasons.append("floor_materiality_mismatch")

    status = "flagged" if reasons else "passed"
    return (status, reasons)


# ---------------------------------------------------------------------------
# Sentiment coherence checks
# ---------------------------------------------------------------------------

# Floor events that imply bad news for the company.  A positive-FinBERT result
# against one of these is a strong signal the article is a denial/dismissal
# ("company denies fraud allegations") that the regex caught by keyword only.
_NEGATIVE_FLOOR_EVENTS: frozenset[str] = frozenset({
    "fraud_disclosure",
    "sebi_enforcement",
    "rating_downgrade",
    "insolvency",
    "pledge_invocation",
    "auditor_resignation",
})


def validate_sentiment(
    *,
    finbert_label: str,
    finbert_confidence: float,
    floor_event: str | None,
    materiality: int,
) -> list[str]:
    """Return extra flag_reasons based on FinBERT vs scoring coherence.

    Pure — takes pre-computed label and confidence from sentiment(); never
    loads a model.  Never rejects; append the returned list to the reasons
    produced by validate_score to build the full flag set.
    """
    reasons: list[str] = []

    # F. SENTIMENT vs FLOOR ─────────────────────────────────────────────────
    # Negative-materiality floor fired but FinBERT reads the text as positive:
    # likely a denial/dismissal the regex matched by keyword alone.
    if (floor_event in _NEGATIVE_FLOOR_EVENTS
            and finbert_label == "positive"
            and finbert_confidence > 0.6):
        reasons.append("sentiment_floor_conflict")

    # G. SENTIMENT vs MATERIALITY ───────────────────────────────────────────
    # Judge scored high materiality but FinBERT is confident the text is
    # anodyne (neutral): the two signals disagree and the row is worth review.
    if materiality >= 8 and finbert_label == "neutral" and finbert_confidence > 0.8:
        reasons.append("sentiment_materiality_conflict")

    return reasons
