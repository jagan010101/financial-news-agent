"""
Holdings registry seed.

THIS IS THE BACKBONE. Every scraped news item is resolved against these rows.
Replace `PORTFOLIO` with your actual holdings. Each field earns its place:

  isin        canonical cross-exchange id; the highest-confidence match key.
  nse_symbol  used to match NSE corporate-announcement payloads directly.
  bse_code    numeric scrip code; matches BSE announcement feed.
  legal_name  full registered name (appears in SEBI/RBI orders).
  common_name short name used in headlines.
  aliases     EVERY way a source might write the name. The fuzzy resolver
              matches against this list, so be generous (ticker, old names,
              brand names, abbreviations). This single field drives most of
              your recall on aggregator headlines.
  sector      lets macro / sector news (RBI repo decision, sector circular)
              fan out to all holdings in that sector even with no name match.
  subsidiaries  NOT captured here — see subsidiaries_seed.py. That table maps
              subsidiary/brand names (e.g. "Jio", "HDFC Life") to their DIRECT
              listed parent's nse_symbol, so news about a subsidiary is
              attributed to the parent holding (match_method='subsidiary')
              without polluting the parent's own alias list.

Accuracy of THIS table caps the precision of the whole system. Garbage here
(wrong ISIN, missing alias) silently drops material news. Curate carefully.
"""

PORTFOLIO = [
    dict(
        isin="INE040A01034", nse_symbol="HDFCBANK", bse_code="500180",
        legal_name="HDFC Bank Limited", common_name="HDFC Bank",
        sector="Banking", industry="Private Bank",
        aliases=["HDFC Bank", "HDFCBANK", "HDFC Bank Ltd", "HDFC Bank Limited"],
        weight=0.18,
    ),
    dict(
        isin="INE002A01018", nse_symbol="RELIANCE", bse_code="500325",
        legal_name="Reliance Industries Limited", common_name="Reliance",
        sector="Energy", industry="Oil & Gas / Conglomerate",
        aliases=["Reliance", "RIL", "Reliance Industries", "Reliance Industries Ltd",
                 "RELIANCE"],
        weight=0.15,
    ),
    dict(
        isin="INE467B01029", nse_symbol="TCS", bse_code="532540",
        legal_name="Tata Consultancy Services Limited", common_name="TCS",
        sector="IT", industry="IT Services",
        aliases=["TCS", "Tata Consultancy Services", "Tata Consultancy"],
        weight=0.12,
    ),
    dict(
        isin="INE009A01021", nse_symbol="INFY", bse_code="500209",
        legal_name="Infosys Limited", common_name="Infosys",
        sector="IT", industry="IT Services",
        aliases=["Infosys", "INFY", "Infosys Ltd", "Infosys Limited"],
        weight=0.10,
    ),
    dict(
        isin="INE238A01034", nse_symbol="AXISBANK", bse_code="532215",
        legal_name="Axis Bank Limited", common_name="Axis Bank",
        sector="Banking", industry="Private Bank",
        aliases=["Axis Bank", "AXISBANK", "Axis Bank Ltd", "UTI Bank"],  # old name UTI Bank
        weight=0.09,
    ),
]
