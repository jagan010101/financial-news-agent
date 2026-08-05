"""
finrag.report.summarize — premium-tier executive summary for the digest.

Report writing is the one place the premium LLM (Groq/Google) runs by
default, not just on dispute — the digest content itself is otherwise a
deterministic template (render.py), so this is the only "writing" happening.
Returns None (never raises) when no premium provider is configured or the
call fails: the digest still sends, just without the summary paragraph.
"""
from __future__ import annotations

import logging

from finrag.chat.llm import get_premium_chat

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You write a 2-3 sentence executive summary opening a portfolio news digest \
email. Be concrete: name the holdings and events involved, not generic \
filler. Neutral, informational tone — never investment advice. No greeting, \
no sign-off, just the summary paragraph itself.
"""


def _format_items(items) -> str:
    lines = []
    for i in items:
        lines.append(f"- {i.holding} [{i.event_type}, score={i.composite}]: {i.title}")
    return "\n".join(lines)


def write_summary(items) -> str | None:
    if not items:
        return None
    chat = get_premium_chat()
    if chat is None:
        return None
    user = f"Today's above-threshold items:\n{_format_items(items)}"
    try:
        return chat.complete([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ])
    except Exception as e:
        # never let a flaky premium call block the (already-deterministic) digest
        log.warning("premium report summary failed: %s", e)
        return None
