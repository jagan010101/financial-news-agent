"""
finrag.chat.bot — ties retrieval + portfolio context + the chat LLM together.

One call to `answer()` = one turn: retrieve relevant articles (corpus-wide),
layer in portfolio context if a holding is mentioned or the question is about
the portfolio generally, build a grounded prompt, ask the chat LLM, return
the reply plus the sources it was given so the caller can show citations.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from finrag.chat.retrieve import (
    ArticleHit, HoldingRef, holding_recent_activity, load_holdings,
    match_holding_mention, semantic_search,
)
from finrag.config import settings

PORTFOLIO_KEYWORDS = ("portfolio", "my holdings", "my stocks", "my positions", "holdings")

SYSTEM_PROMPT = """\
You are a financial-news research assistant for an Indian-equities investor. \
You answer questions about any company, sector, or industry — not just the \
user's portfolio — using the retrieved news snippets given to you as context.

Rules:
- Ground claims in the numbered SOURCES. Cite them inline like [1], [2].
- If the sources don't cover the question, say so plainly, then you may add \
general knowledge — but label it clearly as not sourced from retrieved news.
- If the question touches a holding in YOUR PORTFOLIO (given below), mention \
its weight/sector and note recent scored coverage if present. Don't imply a \
company is held if it isn't listed there.
- Be concise and conversational. Prices/targets are out of scope — you only \
know about news coverage and portfolio composition, not live market data.
"""


@dataclass(frozen=True)
class ChatAnswer:
    text: str
    sources: list[ArticleHit] = field(default_factory=list)
    matched_holding: HoldingRef | None = None


def _format_sources(hits: list[ArticleHit]) -> str:
    if not hits:
        return "(no matching articles found in the corpus)"
    lines = []
    for i, h in enumerate(hits, 1):
        date = h.published_at.strftime("%Y-%m-%d") if h.published_at else "undated"
        src = h.source or "unknown source"
        lines.append(f"[{i}] {h.title} — {src}, {date} ({h.event_type or 'uncategorized'})\n"
                      f"    {h.snippet}\n"
                      f"    url: {h.url or 'n/a'}")
    return "\n".join(lines)


def _format_portfolio(holdings: list[HoldingRef]) -> str:
    if not holdings:
        return "(no active holdings on file)"
    lines = [
        f"- {h.common_name} ({h.nse_symbol or '—'}): sector={h.sector or '—'}, "
        f"industry={h.industry or '—'}, weight={h.weight if h.weight is not None else '—'}"
        for h in holdings
    ]
    return "\n".join(lines)


def _format_recent_activity(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = [
        f"- {r['title']} (composite={r['composite']}, event={r['event_type']})"
        for r in rows
    ]
    return "Recent scored coverage for this holding:\n" + "\n".join(lines)


def build_context(question: str, *, session=None) -> tuple[str, list[ArticleHit], HoldingRef | None]:
    """Returns (context_block, sources_used, matched_holding)."""
    holdings = load_holdings(session) if session is not None else _load_holdings_own_session()
    matched = match_holding_mention(question, holdings)

    hits = semantic_search(question, session=session)
    if matched is not None:
        holding_hits = semantic_search(question, holding_id=matched.id, k=4, session=session)
        seen_ids = {h.id for h in hits}
        hits = hits + [h for h in holding_hits if h.id not in seen_ids]
        hits = sorted(hits, key=lambda h: h.score, reverse=True)[:settings.chat_top_k]

    parts = [f"YOUR PORTFOLIO:\n{_format_portfolio(holdings)}"]

    if matched is not None:
        activity = holding_recent_activity(matched.id, session=session)
        activity_block = _format_recent_activity(activity)
        if activity_block:
            parts.append(activity_block)
    elif any(kw in question.casefold() for kw in PORTFOLIO_KEYWORDS):
        parts.append("The question appears to be about the portfolio broadly — "
                      "use YOUR PORTFOLIO above and the sources below.")

    parts.append(f"SOURCES:\n{_format_sources(hits)}")
    return "\n\n".join(parts), hits, matched


def _load_holdings_own_session() -> list[HoldingRef]:
    from finrag.store.db import SessionLocal
    session = SessionLocal()
    try:
        return load_holdings(session)
    finally:
        session.close()


def answer(question: str, history: list[dict] | None = None, *, session=None) -> ChatAnswer:
    from finrag.chat.llm import get_chat_llm

    history = history or []
    context, hits, matched = build_context(question, session=session)

    trimmed_history = history[-2 * settings.chat_history_turns:]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(trimmed_history)
    messages.append({"role": "user", "content": f"{context}\n\nQUESTION: {question}"})

    llm = get_chat_llm()
    text = llm.complete(messages)
    return ChatAnswer(text=text, sources=hits, matched_holding=matched)
