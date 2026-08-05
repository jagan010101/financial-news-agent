"""
finrag.report.render — build the digest email (HTML + plain-text).

Two tiers:
  * IMMEDIATE (composite >= immediate_threshold): urgent, shown first.
  * DIGEST    (threshold < composite < immediate): batched below.

Content is information-surfacing only — materiality/urgency, never buy/sell
advice. Each item shows: holding, score+tier, event type, the judge's rationale,
source link, timestamp. Plain-text alternative is included for deliverability
and non-HTML clients.
"""
from __future__ import annotations

import datetime as dt
import html
from dataclasses import dataclass

from finrag.config import settings


@dataclass
class ReportItem:
    article_id: int
    title: str
    url: str | None
    holding: str          # nse_symbol or common_name
    sector: str | None
    composite: float
    event_type: str
    rationale: str
    rule_floor: int | None
    published_at: dt.datetime | None
    source: str


def _tier(c: float) -> str:
    return "immediate" if c >= settings.immediate_threshold else "digest"


def _score_color(c: float) -> str:
    if c >= 8: return "#b91c1c"      # red
    if c >= 6.5: return "#c2410c"    # orange
    return "#a16207"                  # amber


def _row_html(it: ReportItem) -> str:
    title = html.escape(it.title)
    link = (f'<a href="{html.escape(it.url)}" style="color:#1d4ed8;'
            f'text-decoration:none;">{title}</a>') if it.url else title
    rationale = html.escape(it.rationale or "")
    floor = (' <span style="font-size:11px;color:#6b7280;">(rule floor)</span>'
             if it.rule_floor else "")
    when = it.published_at.strftime("%d %b %Y %H:%M") if it.published_at else ""
    sector = html.escape(it.sector or "")
    return f"""
    <tr>
      <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;">
        <table width="100%" cellpadding="0" cellspacing="0"><tr>
          <td>
            <span style="display:inline-block;min-width:42px;text-align:center;
              background:{_score_color(it.composite)};color:#fff;font-weight:700;
              border-radius:6px;padding:3px 8px;font-size:14px;">{it.composite}</span>
            <span style="font-weight:600;color:#111827;margin-left:8px;">{html.escape(it.holding)}</span>
            <span style="color:#6b7280;font-size:12px;margin-left:6px;">{sector}</span>
            <span style="color:#6b7280;font-size:12px;margin-left:6px;">· {html.escape(it.event_type)}</span>{floor}
          </td>
          <td align="right" style="color:#9ca3af;font-size:12px;white-space:nowrap;">{when}</td>
        </tr></table>
        <div style="margin:6px 0 2px;font-size:15px;line-height:1.4;">{link}</div>
        <div style="color:#4b5563;font-size:13px;line-height:1.5;">{rationale}</div>
      </td>
    </tr>"""


def _section(title: str, items: list[ReportItem]) -> str:
    if not items:
        return ""
    rows = "".join(_row_html(i) for i in items)
    return f"""
    <tr><td style="padding-top:18px;">
      <div style="font-size:13px;font-weight:700;letter-spacing:.04em;
        text-transform:uppercase;color:#374151;">{html.escape(title)}</div>
      <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
    </td></tr>"""


def _summary_html(summary: str | None) -> str:
    if not summary:
        return ""
    return f"""
    <tr><td style="padding-top:14px;font-size:14px;line-height:1.5;color:#374151;
      border-top:1px solid #e5e7eb;margin-top:14px;">
      {html.escape(summary)}
    </td></tr>"""


def render_html(items: list[ReportItem], summary: str | None = None) -> str:
    items = sorted(items, key=lambda i: i.composite, reverse=True)
    immediate = [i for i in items if _tier(i.composite) == "immediate"]
    digest = [i for i in items if _tier(i.composite) == "digest"]
    today = dt.datetime.now().strftime("%d %b %Y")
    return f"""\
<!DOCTYPE html><html><body style="margin:0;background:#f9fafb;
  font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">
    <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;
      border-radius:12px;padding:28px 32px;box-shadow:0 1px 3px rgba(0,0,0,.08);">
      <tr><td>
        <div style="font-size:20px;font-weight:800;color:#111827;">Portfolio News Digest</div>
        <div style="color:#6b7280;font-size:13px;margin-top:2px;">{today} ·
          {len(items)} item{'s' if len(items)!=1 else ''} above threshold
          ({settings.score_threshold})</div>
      </td></tr>
      {_summary_html(summary)}
      {_section("Immediate attention", immediate)}
      {_section("Digest", digest)}
      <tr><td style="padding-top:22px;color:#9ca3af;font-size:11px;line-height:1.5;">
        Materiality/urgency signals for your holdings — informational only, not
        investment advice. Scores are model-assisted with deterministic overrides
        for high-materiality events.
      </td></tr>
    </table>
  </td></tr></table>
</body></html>"""


def render_text(items: list[ReportItem], summary: str | None = None) -> str:
    items = sorted(items, key=lambda i: i.composite, reverse=True)
    lines = [f"PORTFOLIO NEWS DIGEST — {dt.datetime.now():%d %b %Y}",
             f"{len(items)} item(s) above threshold ({settings.score_threshold})", ""]
    if summary:
        lines.append(summary)
        lines.append("")
    for it in items:
        tier = "[!] " if _tier(it.composite) == "immediate" else "    "
        lines.append(f"{tier}{it.composite}  {it.holding}  ({it.event_type})")
        lines.append(f"     {it.title}")
        if it.rationale:
            lines.append(f"     {it.rationale}")
        if it.url:
            lines.append(f"     {it.url}")
        lines.append("")
    lines.append("Informational only, not investment advice.")
    return "\n".join(lines)
