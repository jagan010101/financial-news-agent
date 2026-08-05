"""
finrag.process.resolve — entity resolution cascade.

Links an article to the holdings it is about, highest-confidence first. Stops
adding lower-confidence methods for a holding already matched by a higher one.

Cascade (per article):
  1. exact_id      : NSE symbol / BSE code / ISIN as a standalone token  (1.00)
  2. alias_exact   : curated alias as a word-boundary phrase             (0.95)
  3. alias_fuzzy   : RapidFuzz >= threshold against alias list           (score)
  4. subsidiary    : subsidiary/brand name whose PARENT is a holding     (0.90)
  5. sector        : ONLY if no company hit AND macro/sector cue present (0.40)

Returns a list of Match(holding_id, method, score). Conservative by design:
a fuzzy candidate below threshold yields NO link rather than a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from finrag.process.gazetteer import Gazetteer

FUZZY_THRESHOLD = 90        # token_set_ratio; tuneable on your labeled set

# Disambiguation: bare words that collide with DIFFERENT listed companies in the
# Indian market. If a blocker phrase is present, that group-name token must NOT
# resolve to the holding. Key = casefolded ambiguous token that maps to a
# holding; value = phrases that indicate a DIFFERENT entity.
# (e.g. "Reliance" alone is ambiguous: Reliance Power/Capital/Infra are ADAG,
#  distinct from Reliance Industries.) Extend per your portfolio.
AMBIGUOUS_BLOCKERS: dict[str, list[str]] = {
    "reliance": ["reliance power", "reliance capital", "reliance infra",
                 "reliance communications", "reliance naval", "rcom",
                 "anil ambani", "reliance home finance"],
    # Jio Financial Services was DEMERGED from Reliance Industries and listed
    # independently (Aug 2023) — bare "Jio" must not pull its news (or its own
    # subsidiaries') onto Reliance.
    "jio": ["jio financial", "jio finance limited", "jio payments bank",
            "jio insurance broking", "jio payment solutions"],
}

SECTOR_CUES = {
    "Banking": [r"\bbank(s|ing)?\b", r"\bNPA\b", r"\blender", r"\bRBI\b",
                r"capital adequacy", r"\bbasel\b", r"repo rate"],
    "IT":      [r"\bIT services\b", r"\bsoftware export", r"\bNasscom\b"],
    "Energy":  [r"\boil\b", r"\bcrude\b", r"\bgas\b", r"\brefin", r"\benergy\b"],
}


@dataclass(frozen=True)
class Match:
    holding_id: int
    method: str
    score: float


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9]+", text.upper()))


def resolve(title: str, body: str | None, gaz: Gazetteer) -> list[Match]:
    text = f"{title}\n{body or ''}"
    text_cf = text.casefold()
    toks = _tokens(text)
    matched: dict[int, Match] = {}

    def _blocked(hid: int) -> bool:
        # Veto if a blocker phrase for an ambiguous alias of this holding is
        # present AND no UNambiguous evidence (a distinct identifier/alias of
        # this holding) also appears. Protects against Indian name collisions
        # (Reliance Industries vs Reliance Power, etc.).
        # "Associated with hid" covers both the holding's own aliases AND any
        # subsidiary alias whose parent resolves to hid — a bare token like
        # "Jio" is only in the subsidiaries table now, not in Reliance's own
        # aliases, so the block must follow it there too (see Jio Financial
        # Services, an independent company since its 2023 demerger).
        for amb_token, blockers in AMBIGUOUS_BLOCKERS.items():
            via_alias = gaz.alias_index.get(amb_token) == hid
            via_subsidiary = False
            if not via_alias:
                parent_symbol = gaz.subsidiary_index.get(amb_token)
                via_subsidiary = (parent_symbol is not None and
                                  gaz.exact_ids.get(parent_symbol.upper()) == hid)
            if not (via_alias or via_subsidiary):
                continue
            if any(b in text_cf for b in blockers):
                # is there unambiguous evidence for this holding? e.g. ISIN,
                # symbol token, or a multi-word alias that ISN'T the bare token
                unambiguous = False
                for a, cf, h in gaz.alias_list:
                    if h != hid:
                        continue
                    if cf == amb_token:
                        continue
                    if re.search(rf"\b{re.escape(cf)}\b", text_cf):
                        unambiguous = True
                        break
                for ident, h in {**gaz.exact_ids, **gaz.strict_tokens}.items():
                    # the symbol/ticker that IS the ambiguous word (same string,
                    # different case) is NOT independent evidence
                    if ident.casefold() == amb_token:
                        continue
                    if h == hid and ident in toks:
                        unambiguous = True
                        break
                if not unambiguous:
                    return True
        return False

    def consider(hid: int, method: str, score: float) -> None:
        if _blocked(hid):
            return
        # keep only the highest-confidence method per holding
        if hid not in matched or score > matched[hid].score:
            matched[hid] = Match(hid, method, score)

    # 1. exact identifiers (standalone tokens only)
    for ident, hid in gaz.exact_ids.items():
        if ident in toks:
            consider(hid, "exact_id", 1.00)
    # strict tickers/acronyms (e.g. RIL, TCS) as standalone tokens
    for ident, hid in gaz.strict_tokens.items():
        if ident in toks:
            consider(hid, "exact_id", 1.00)

    # 2. alias exact (word-boundary phrase match, case-insensitive)
    for alias_cf, hid in gaz.alias_index.items():
        if hid in matched and matched[hid].method == "exact_id":
            continue
        # multi-word or normal-case aliases: word-boundary search
        if len(alias_cf) > 4 and not alias_cf.isupper():
            if re.search(rf"\b{re.escape(alias_cf)}\b", text_cf):
                consider(hid, "alias_exact", 0.95)

    # 3. fuzzy alias (only for holdings not yet matched)
    unmatched_aliases = [(a, cf, hid) for (a, cf, hid) in gaz.alias_list
                         if hid not in matched and len(cf) > 4]
    for alias, alias_cf, hid in unmatched_aliases:
        score = fuzz.token_set_ratio(alias_cf, text_cf) / 100.0
        if score * 100 >= FUZZY_THRESHOLD:
            # guard: require the alias's head word to actually appear, to avoid
            # token_set_ratio inflating on incidental shared words
            head = alias_cf.split()[0]
            if head in text_cf:
                consider(hid, "alias_fuzzy", round(score, 3))

    # 4. subsidiary match: a subsidiary/brand name in the text, resolved to its
    # DIRECT listed parent — but only if that parent is itself a current
    # holding (gaz.exact_ids). A subsidiary of a company we don't hold is a
    # no-op here, not a match.
    for ident, parent_symbol in gaz.subsidiary_strict_tokens.items():
        hid = gaz.exact_ids.get(parent_symbol.upper())
        if hid is not None and ident in toks:
            consider(hid, "subsidiary", 0.90)
    for name_cf, parent_symbol in gaz.subsidiary_index.items():
        if len(name_cf) <= 4 or name_cf.isupper():
            continue
        hid = gaz.exact_ids.get(parent_symbol.upper())
        if hid is None:
            continue
        if re.search(rf"\b{re.escape(name_cf)}\b", text_cf):
            consider(hid, "subsidiary", 0.90)

    # 5. sector fan-out — ONLY when nothing company-specific matched
    if not matched:
        for sector, cues in SECTOR_CUES.items():
            if sector not in gaz.by_sector:
                continue
            if any(re.search(c, text, flags=re.IGNORECASE) for c in cues):
                for hid in gaz.by_sector[sector]:
                    consider(hid, "sector", 0.40)

    return list(matched.values())
