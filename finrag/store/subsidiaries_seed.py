"""
Subsidiaries registry seed — a general reference set, not scoped to the
current PORTFOLIO in holdings_seed.py.

Each row says: `subsidiary_name` (+ `aliases`) is a majority/wholly-owned,
DIRECT subsidiary of the listed company trading under `parent_nse_symbol`.
Entity resolution uses this to attribute subsidiary-only news to the parent
holding (match_method='subsidiary') — but ONLY when that parent is itself a
row in `holdings`. Rows whose parent isn't currently held are inert (no link
is produced) until that parent is added to the portfolio; they're kept here
so expanding the portfolio doesn't require re-researching this each time.

Scope discipline — what's deliberately EXCLUDED:
  * Sibling "group companies" under a common unlisted promoter/holding entity
    (e.g. Tata Sons, Adani family trusts) are NOT subsidiaries of each other.
    Tata Motors does not own Tata Steel; Adani Enterprises does not own Adani
    Ports post-demerger. Only include a row when there is a genuine, direct,
    majority-owned parent -> subsidiary relationship you can point to.
  * Companies that WERE subsidiaries but have since been demerged/listed
    independently are excluded, not just omitted — see the Jio Financial
    Services note below. Ownership structures shift (IPOs, demergers, stake
    sales); re-verify before trusting an old edit of this file.
  * Associates/JVs below ~50% ownership (e.g. Tech Mahindra vs Mahindra & Mahindra,
    Indus Towers vs Bharti Airtel) are excluded — "subsidiary" here means
    consolidated, majority-controlled, not merely "in the same business house".

parent_nse_symbol is the join key against holdings.nse_symbol — get it right
or the link silently never fires.
"""

SUBSIDIARIES = [
    # --- HDFC Bank (INE040A01034 / HDFCBANK) — post HDFC Ltd merger, July 2023 ---
    dict(parent_nse_symbol="HDFCBANK", parent_name="HDFC Bank",
         subsidiary_name="HDFC Life Insurance",
         aliases=["HDFC Life", "HDFC Standard Life"]),
    dict(parent_nse_symbol="HDFCBANK", parent_name="HDFC Bank",
         subsidiary_name="HDFC Asset Management Company",
         aliases=["HDFC AMC", "HDFC Mutual Fund"]),
    dict(parent_nse_symbol="HDFCBANK", parent_name="HDFC Bank",
         subsidiary_name="HDFC ERGO General Insurance",
         aliases=["HDFC Ergo"]),
    dict(parent_nse_symbol="HDFCBANK", parent_name="HDFC Bank",
         subsidiary_name="HDFC Securities", aliases=[]),
    dict(parent_nse_symbol="HDFCBANK", parent_name="HDFC Bank",
         subsidiary_name="HDB Financial Services", aliases=["HDB Fin Services"]),
    dict(parent_nse_symbol="HDFCBANK", parent_name="HDFC Bank",
         subsidiary_name="HDFC Capital Advisors", aliases=[]),

    # --- Axis Bank (INE238A01034 / AXISBANK) ---
    dict(parent_nse_symbol="AXISBANK", parent_name="Axis Bank",
         subsidiary_name="Axis Finance", aliases=[]),
    dict(parent_nse_symbol="AXISBANK", parent_name="Axis Bank",
         subsidiary_name="Axis Asset Management Company",
         aliases=["Axis AMC", "Axis Mutual Fund"]),
    dict(parent_nse_symbol="AXISBANK", parent_name="Axis Bank",
         subsidiary_name="Axis Capital", aliases=[]),
    dict(parent_nse_symbol="AXISBANK", parent_name="Axis Bank",
         subsidiary_name="Axis Securities", aliases=[]),
    dict(parent_nse_symbol="AXISBANK", parent_name="Axis Bank",
         subsidiary_name="Axis Trustee Services", aliases=[]),

    # --- Reliance Industries (INE002A01018 / RELIANCE) ---
    # NOTE: Jio Financial Services was DEMERGED from RIL and separately listed
    # in Aug 2023 — it is now an independent company, deliberately excluded
    # here (do not re-add it as a RIL subsidiary).
    dict(parent_nse_symbol="RELIANCE", parent_name="Reliance Industries",
         subsidiary_name="Reliance Jio Infocomm",
         aliases=["Jio", "Reliance Jio", "Jio Platforms"]),
    dict(parent_nse_symbol="RELIANCE", parent_name="Reliance Industries",
         subsidiary_name="Reliance Retail Ventures",
         aliases=["Reliance Retail", "Ajio", "JioMart", "Reliance Trends",
                   "Reliance Digital", "Reliance Fresh", "Reliance Smart"]),

    # --- Infosys (INE009A01021 / INFY) ---
    dict(parent_nse_symbol="INFY", parent_name="Infosys",
         subsidiary_name="EdgeVerve Systems", aliases=["EdgeVerve", "Finacle"]),
    dict(parent_nse_symbol="INFY", parent_name="Infosys",
         subsidiary_name="Infosys BPM", aliases=[]),

    # --- Bajaj Finserv (INE918I01018 / BAJAJFINSV) — not currently held ---
    dict(parent_nse_symbol="BAJAJFINSV", parent_name="Bajaj Finserv",
         subsidiary_name="Bajaj Finance", aliases=["Bajaj Finance Ltd"]),
    dict(parent_nse_symbol="BAJAJFINSV", parent_name="Bajaj Finserv",
         subsidiary_name="Bajaj Allianz Life Insurance", aliases=["Bajaj Allianz Life"]),
    dict(parent_nse_symbol="BAJAJFINSV", parent_name="Bajaj Finserv",
         subsidiary_name="Bajaj Allianz General Insurance", aliases=["Bajaj Allianz General"]),
    dict(parent_nse_symbol="BAJAJFINSV", parent_name="Bajaj Finserv",
         subsidiary_name="Bajaj Finserv Health", aliases=[]),
    dict(parent_nse_symbol="BAJAJFINSV", parent_name="Bajaj Finserv",
         subsidiary_name="Bajaj Finserv Asset Management",
         aliases=["Bajaj Finserv AMC", "Bajaj Finserv Mutual Fund"]),

    # --- Bajaj Finance (INE296A01024 / BAJFINANCE) — not currently held ---
    dict(parent_nse_symbol="BAJFINANCE", parent_name="Bajaj Finance",
         subsidiary_name="Bajaj Housing Finance", aliases=[],
         notes="Majority-owned by Bajaj Finance despite its 2024 IPO (minority public float)."),
    dict(parent_nse_symbol="BAJFINANCE", parent_name="Bajaj Finance",
         subsidiary_name="Bajaj Financial Securities", aliases=["Bajaj Broking"]),

    # --- State Bank of India (INE062A01020 / SBIN) — not currently held ---
    dict(parent_nse_symbol="SBIN", parent_name="State Bank of India",
         subsidiary_name="SBI Life Insurance", aliases=["SBI Life"]),
    dict(parent_nse_symbol="SBIN", parent_name="State Bank of India",
         subsidiary_name="SBI Cards and Payment Services", aliases=["SBI Cards"]),
    dict(parent_nse_symbol="SBIN", parent_name="State Bank of India",
         subsidiary_name="SBI General Insurance", aliases=[]),
    dict(parent_nse_symbol="SBIN", parent_name="State Bank of India",
         subsidiary_name="SBI Funds Management", aliases=["SBI Mutual Fund"]),
    dict(parent_nse_symbol="SBIN", parent_name="State Bank of India",
         subsidiary_name="SBI Capital Markets", aliases=["SBI Caps"]),

    # --- Kotak Mahindra Bank (INE237A01028 / KOTAKBANK) — not currently held ---
    dict(parent_nse_symbol="KOTAKBANK", parent_name="Kotak Mahindra Bank",
         subsidiary_name="Kotak Mahindra Asset Management Company",
         aliases=["Kotak AMC", "Kotak Mutual Fund"]),
    dict(parent_nse_symbol="KOTAKBANK", parent_name="Kotak Mahindra Bank",
         subsidiary_name="Kotak Securities", aliases=[]),
    dict(parent_nse_symbol="KOTAKBANK", parent_name="Kotak Mahindra Bank",
         subsidiary_name="Kotak Mahindra Life Insurance", aliases=["Kotak Life"]),
    dict(parent_nse_symbol="KOTAKBANK", parent_name="Kotak Mahindra Bank",
         subsidiary_name="Kotak Mahindra General Insurance", aliases=[]),
    dict(parent_nse_symbol="KOTAKBANK", parent_name="Kotak Mahindra Bank",
         subsidiary_name="Kotak Mahindra Prime", aliases=[]),

    # --- Larsen & Toubro (INE018A01030 / LT) — not currently held ---
    dict(parent_nse_symbol="LT", parent_name="Larsen & Toubro",
         subsidiary_name="LTIMindtree", aliases=["LTI Mindtree", "LTM"]),
    dict(parent_nse_symbol="LT", parent_name="Larsen & Toubro",
         subsidiary_name="L&T Technology Services", aliases=["LTTS"]),
    dict(parent_nse_symbol="LT", parent_name="Larsen & Toubro",
         subsidiary_name="L&T Finance", aliases=["L&T Finance Holdings", "LTF"]),

    # --- Bharti Airtel (INE397D01024 / BHARTIARTL) — not currently held ---
    dict(parent_nse_symbol="BHARTIARTL", parent_name="Bharti Airtel",
         subsidiary_name="Bharti Hexacom", aliases=[]),
    dict(parent_nse_symbol="BHARTIARTL", parent_name="Bharti Airtel",
         subsidiary_name="Nxtra Data", aliases=["Nxtra"]),
    dict(parent_nse_symbol="BHARTIARTL", parent_name="Bharti Airtel",
         subsidiary_name="Airtel Payments Bank", aliases=[]),

    # --- Grasim Industries (INE047A01021 / GRASIM) — not currently held ---
    dict(parent_nse_symbol="GRASIM", parent_name="Grasim Industries",
         subsidiary_name="UltraTech Cement", aliases=["Ultratech"]),
    dict(parent_nse_symbol="GRASIM", parent_name="Grasim Industries",
         subsidiary_name="Aditya Birla Capital", aliases=["ABCL"]),

    # --- Sun Pharmaceutical Industries (INE044A01036 / SUNPHARMA) — not currently held ---
    dict(parent_nse_symbol="SUNPHARMA", parent_name="Sun Pharmaceutical Industries",
         subsidiary_name="Taro Pharmaceutical Industries", aliases=["Taro Pharma", "Taro"]),

    # --- Mahindra & Mahindra (INE101A01026 / M&M) — not currently held ---
    dict(parent_nse_symbol="M&M", parent_name="Mahindra & Mahindra",
         subsidiary_name="Mahindra & Mahindra Financial Services",
         aliases=["Mahindra Finance", "MMFSL"]),
    dict(parent_nse_symbol="M&M", parent_name="Mahindra & Mahindra",
         subsidiary_name="Mahindra Lifespace Developers", aliases=["Mahindra Lifespaces"]),
    dict(parent_nse_symbol="M&M", parent_name="Mahindra & Mahindra",
         subsidiary_name="Mahindra Holidays & Resorts", aliases=["Club Mahindra"]),

    # --- Titan Company (INE280A01028 / TITAN) — not currently held ---
    dict(parent_nse_symbol="TITAN", parent_name="Titan Company",
         subsidiary_name="CaratLane Trading", aliases=["CaratLane"]),

    # --- Tata Motors (INE155A01022 / TATAMOTORS) — not currently held ---
    # (Tata Motors itself is a subsidiary of unlisted Tata Sons, not of TCS —
    # deliberately not encoded here; see module docstring.)
    dict(parent_nse_symbol="TATAMOTORS", parent_name="Tata Motors",
         subsidiary_name="Tata Technologies", aliases=[]),
    dict(parent_nse_symbol="TATAMOTORS", parent_name="Tata Motors",
         subsidiary_name="Jaguar Land Rover", aliases=["JLR"]),
]
