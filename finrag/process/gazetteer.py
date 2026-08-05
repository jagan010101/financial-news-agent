"""
finrag.process.gazetteer — compiles the holdings registry into fast matchers.

Built once per resolver run from the DB. Structures:
  * exact_ids   : {UPPER(nse_symbol|bse_code|isin) -> holding_id} for token match
  * alias_index : {casefolded alias -> holding_id} for exact word-boundary match
  * alias_list  : [(alias, casefolded, holding_id)] for fuzzy fallback
  * by_sector   : {sector -> [holding_id]} for macro fan-out
  * subsidiary_index / subsidiary_list : same shape as alias_index/alias_list,
    but the value is the PARENT's nse_symbol (not a holding_id directly) —
    a subsidiary's parent may not itself be a current holding. resolve()
    looks the parent symbol up in exact_ids to see if it resolves to a live
    holding_id; if not, the subsidiary match is simply inert.

We DON'T fuzzy-match identifiers (an ISIN is exact or it's wrong) and we keep a
set of very short/ambiguous aliases to require stricter matching (e.g. "RIL",
"TCS" must appear as standalone uppercase tokens, not substrings of a word).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Gazetteer:
    exact_ids: dict[str, int] = field(default_factory=dict)
    alias_index: dict[str, int] = field(default_factory=dict)
    alias_list: list[tuple[str, str, int]] = field(default_factory=list)
    by_sector: dict[str, list[int]] = field(default_factory=dict)
    # aliases that are short/all-caps tickers needing a stricter standalone match
    strict_tokens: dict[str, int] = field(default_factory=dict)
    # subsidiary/brand names -> PARENT nse_symbol (resolved to a holding_id,
    # if any, at match time — see module docstring)
    subsidiary_index: dict[str, str] = field(default_factory=dict)
    subsidiary_list: list[tuple[str, str, str]] = field(default_factory=list)
    subsidiary_strict_tokens: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_holdings(cls, holdings, subsidiaries=()) -> "Gazetteer":
        g = cls()
        for h in holdings:
            for ident in (h.nse_symbol, h.bse_code, h.isin):
                if ident:
                    g.exact_ids[ident.upper()] = h.id
            if h.sector:
                g.by_sector.setdefault(h.sector, []).append(h.id)
            for alias in (h.aliases or []):
                cf = alias.casefold().strip()
                if not cf:
                    continue
                g.alias_index[cf] = h.id
                g.alias_list.append((alias, cf, h.id))
                # short or ALL-CAPS aliases (tickers/acronyms) -> strict token match
                if alias.isupper() or len(cf) <= 4:
                    g.strict_tokens[alias.upper()] = h.id
        for s in subsidiaries:
            parent_symbol = s.parent_nse_symbol
            for name in (s.subsidiary_name, *(s.aliases or [])):
                cf = name.casefold().strip()
                if not cf:
                    continue
                g.subsidiary_index[cf] = parent_symbol
                g.subsidiary_list.append((name, cf, parent_symbol))
                if name.isupper() or len(cf) <= 4:
                    g.subsidiary_strict_tokens[name.upper()] = parent_symbol
        return g
