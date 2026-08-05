"""Chatbot tests: holding-mention matching and context formatting.

Pure-logic only — no DB, no embeddings, no LLM calls (mirrors the style of
test_score_pipeline.py). Retrieval/LLM plumbing needs a live Postgres +
model weights and isn't unit-tested here.
"""
from finrag.chat.bot import _format_portfolio, _format_recent_activity, _format_sources
from finrag.chat.retrieve import ArticleHit, HoldingRef, match_holding_mention

HOLDINGS = [
    HoldingRef(id=1, common_name="HDFC Bank", nse_symbol="HDFCBANK", sector="Banking",
               industry="Private Bank", weight=0.18,
               aliases=["HDFC Bank", "HDFCBANK", "HDFC Bank Ltd"]),
    HoldingRef(id=2, common_name="Reliance", nse_symbol="RELIANCE", sector="Energy",
               industry="Oil & Gas / Conglomerate", weight=0.15,
               aliases=["Reliance", "RIL", "Reliance Industries"]),
]


def test_match_exact_ticker():
    h = match_holding_mention("What's new with HDFCBANK today?", HOLDINGS)
    assert h is not None and h.id == 1


def test_match_alias_phrase():
    h = match_holding_mention("Any updates on Reliance Industries' Q1 results?", HOLDINGS)
    assert h is not None and h.id == 2


def test_no_match_unrelated_question():
    h = match_holding_mention("What's happening in the semiconductor industry?", HOLDINGS)
    assert h is None


def test_short_ticker_requires_standalone_token():
    # "RIL" embedded inside another word must not match
    h = match_holding_mention("The gorilla escaped the zoo", HOLDINGS)
    assert h is None


def test_format_portfolio_lists_all_holdings():
    out = _format_portfolio(HOLDINGS)
    assert "HDFC Bank" in out and "Reliance" in out
    assert "0.18" in out


def test_format_sources_empty():
    assert "no matching articles" in _format_sources([])


def test_format_sources_includes_citation_index_and_url():
    hits = [ArticleHit(id=1, title="HDFC Bank posts record profit", url="http://x/1",
                        source="MONEYCONTROL", published_at=None, event_type="earnings_result",
                        snippet="Profit rose 15%...", score=0.9)]
    out = _format_sources(hits)
    assert "[1]" in out and "http://x/1" in out


def test_format_recent_activity_empty_returns_blank():
    assert _format_recent_activity([]) == ""


def test_format_recent_activity_lists_rows():
    rows = [dict(title="RBI hikes repo rate", composite=8.1, event_type="policy_action")]
    out = _format_recent_activity(rows)
    assert "RBI hikes repo rate" in out and "8.1" in out
