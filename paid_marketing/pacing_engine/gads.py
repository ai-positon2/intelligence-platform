"""Adapter for the Google Ads UI campaign export ("Campaign Spends.xlsx").

This is a strictly better input than the Supermetrics `Data` tab, because it
carries three things Supermetrics does not:

  * `Budget`        - the campaign's REAL daily budget. No more inferring it
                      from run-rate, so move amounts are exact.
  * `Campaign status` - Enabled/Paused, so a dark campaign is identifiable.
  * `Search impr. share` + top/abs-top IS - headroom signal.

Expected columns:
  Day | Campaign status | Campaign | Budget | Budget name | Budget type |
  Account | Campaign type | Bid strategy type | Impr. | Search abs. top IS |
  Search top IS | Search impr. share | Cost | Conversions | Clicks | CTR | ...
"""
from __future__ import annotations

import re
from pathlib import Path

import openpyxl
import pandas as pd

from loaders import norm

# Google Ads account name -> Budget Tracker account name, where they differ.
ACCOUNT_ALIASES = {
    "daniel i. shapiro, m.d., p.c.": "Shapiro Plastic Surgery",
}

# Google Ads accounts that hold MANY budgeted locations, where the Budget
# Tracker has a row per location rather than per Google Ads account.
# The location is read out of the campaign NAME by matching it against the
# budget account names — never by splitting on a delimiter, because naming is
# inconsistent (P2_Generic_PortersNeck vs P2_Pmax_Porters_Neck).
SPLIT_ACCOUNTS = {"riccobene"}

# Google Ads accounts that hold many locations under ONE budget line.
# The account keeps its single budget; these rules only fence moves so that
# one location never donates to another.
#   callable: campaign name -> group label
LOCATION_RULES = {
    # <Location>_<Type>, e.g. Arlington_Brand, Attleboro_Display_...
    "gentle dental": lambda c: c.split("_")[0] if "_" in c else c,
    "great hill dental": lambda c: c.split("_")[0] if "_" in c else c,
}

MIN_LOC_TOKEN = 5   # shortest prefix we will trust when matching a location

# Some accounts in the auto-refreshing sheet carry a trailing Google Ads
# customer-id suffix the Budget Tracker names don't have, e.g.
# "Inspire Aesthetics (1887900641)" vs tracker's plain "Inspire Aesthetics".
# Stripped before matching so these don't fall out as unbudgeted orphans
# while their real budget row sits unmatched right next to them.
_ID_SUFFIX = re.compile(r"\s*\(\d{6,}\)\s*$")


def strip_id_suffix(name: str) -> str:
    return _ID_SUFFIX.sub("", str(name)).strip()


def match_location(campaign: str, keys: list[str]) -> str | None:
    """Find which budgeted location a campaign belongs to, by name.

    Works on the normalized strings, so underscores, spaces, hyphens and case
    all stop mattering:
        P2_Pmax_Porters_Neck  -> p2pmaxportersneck  contains  portersneck
        P2_Generic_Myrtle     -> p2genericmyrtle    starts a prefix of
                                                    myrtlebeachdenovo
    Longest match wins, so 'Cary' never steals a 'CaryWest' campaign.
    """
    c = norm(campaign)
    best, best_len = None, 0
    for k in keys:
        if len(k) >= MIN_LOC_TOKEN and k in c and len(k) > best_len:
            best, best_len = k, len(k)
    if best:
        return best
    # Fall back: the campaign carries a prefix of a longer budget name.
    for k in keys:
        for n in range(len(k), MIN_LOC_TOKEN - 1, -1):
            if k[:n] in c and n > best_len:
                best, best_len = k, n
                break
    return best


def _pct(v) -> float | None:
    """'< 10%' -> 0.05,  '> 90%' -> 0.95,  ' --' -> None,  0.3 -> 0.3"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s in ("", "--", "- -", "—") or s.startswith("--") or s == " --":
        return None
    if s.startswith("<"):
        return 0.05
    if s.startswith(">"):
        return 0.95
    s = s.rstrip("%")
    try:
        f = float(s)
        return f / 100 if f > 1 else f
    except ValueError:
        return None


def load_export_sheet(path: str | Path) -> pd.DataFrame:
    """Load the auto-refreshing "All Accounts Spend Data" Google Sheet
    (Supermetrics, `Data` tab) — same output contract as load_export(),
    built from a different but now largely-equivalent column set:

        Account | Date | Campaign name | Campaign status | Budget name |
        Bidding strategy | Configured budget | Impressions | Clicks | Cost |
        Conversions | Total conversion value | Impression share |
        Search rank lost impression share | Search budget lost impression share

    Two differences from load_export() worth knowing:
      - No `Campaign type` column. categories.py infers the category from the
        campaign NAME first and only falls back to campaign_type, so this
        costs little; campaign_type comes through as "" -> "Unknown".
      - `Budget name` is populated on EVERY row here (often equal to the
        campaign's own name when the campaign is NOT actually shared),
        instead of being blank/'--' for a non-shared campaign as in the
        primary export. A pool name identical to its own campaign name is
        cleared to "" so the shared-pool dedup logic in engine.py doesn't
        treat every campaign as its own one-member pool.
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Data"] if "Data" in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(c).strip() if c is not None else "" for c in rows[0]]
    df = pd.DataFrame(rows[1:], columns=hdr)
    df = df[df["Account"].notna()].copy()

    num = lambda c: pd.to_numeric(df[c], errors="coerce").fillna(0.0) if c in df else 0.0
    campaign = df["Campaign name"].astype(str).str.strip()
    if "Budget name" in df:
        bname = df["Budget name"].astype(str).str.strip()
        bname = bname.where(bname != campaign, "")  # own-name pool == not shared
    else:
        bname = ""

    out = pd.DataFrame({
        "date": pd.to_datetime(df["Date"], errors="coerce"),
        "account_raw": df["Account"].astype(str).str.strip(),
        "campaign": campaign,
        "campaign_type": "",
        "status": (df["Campaign status"].astype(str).str.strip()
                   if "Campaign status" in df else ""),
        "bid_strategy": (df["Bidding strategy"].astype(str).str.strip()
                         if "Bidding strategy" in df else ""),
        "budget": num("Configured budget"),
        "budget_name": bname,
        "cost": num("Cost"),
        "conversions": num("Conversions"),
        "impressions": num("Impressions"),
        "clicks": num("Clicks"),
        "conv_value": num("Total conversion value"),
        "search_is": (pd.to_numeric(df["Impression share"], errors="coerce")
                      if "Impression share" in df else None),
    })
    return out[out["date"].notna()].reset_index(drop=True)


def load_any_export(path: str | Path) -> pd.DataFrame:
    """Peek at the header row and dispatch to the matching loader, so
    callers don't need to know which shape a given file is in."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["Data"] if "Data" in wb.sheetnames else wb.worksheets[0]
    hdr_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
    hdr = {str(c).strip() for c in hdr_row if c is not None}
    wb.close()
    if "Configured budget" in hdr:
        return load_export_sheet(path)
    return load_export(path)


def load_export(path: str | Path) -> pd.DataFrame:
    """-> date | account_raw | campaign | campaign_type | status | bid_strategy
          | budget | cost | conversions | impressions | clicks | search_is
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(c).strip() if c is not None else "" for c in rows[0]]
    df = pd.DataFrame(rows[1:], columns=hdr)
    df = df[df["Day"].notna()].copy()

    num = lambda c: pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["Day"], errors="coerce"),
        "account_raw": df["Account"].astype(str).str.strip(),
        "campaign": df["Campaign"].astype(str).str.strip(),
        "campaign_type": df["Campaign type"].astype(str).str.strip(),
        "status": df["Campaign status"].astype(str).str.strip(),
        "bid_strategy": df["Bid strategy type"].astype(str).str.strip(),
        "budget": num("Budget"),
        # Shared/portfolio budgets: many campaigns draw on ONE pool. ' --'
        # means the campaign has its own budget.
        "budget_name": (df["Budget name"].astype(str).str.strip()
                        .replace({"--": "", "- -": "", "nan": "", "None": ""})
                        if "Budget name" in df else ""),
        "cost": num("Cost"),
        "conversions": num("Conversions"),
        "impressions": num("Impr."),
        "clicks": num("Clicks"),
        "conv_value": 0.0,
        "search_is": df["Search impr. share"].map(_pct) if "Search impr. share" in df else None,
    })
    return out[out["date"].notna()].reset_index(drop=True)


def resolve(df: pd.DataFrame, budgets: pd.DataFrame,
            channel: str = "Google") -> tuple[pd.DataFrame, list[str]]:
    """Attach `client`, `account` (Budget Tracker naming) and `location`.

    Rows that cannot be tied to a budgeted account are dropped and reported.
    """
    b = budgets.copy()
    if channel:
        # The export is one channel only; a Meta/Bing budget row must not
        # be matched against Google spend.
        b = b[b["channel"].str.contains(channel, case=False, na=False)]
    # Paused accounts often get renamed in the tracker at the same time their
    # budget is zeroed out (e.g. "Great Lakes" -> "Great Lakes Family
    # Dental"), so they'd fail an exact key match too — keep their names
    # (channel-matched, but not yet budget-filtered) for a substring check
    # below, to tell "paused" apart from "never budgeted at all".
    paused_names = (b.loc[b["status"].str.lower() == "paused", "account"].str.lower().tolist()
                    if "status" in b else [])
    b = b[b["allocated_budget"] > 0]

    by_acct = {k: (c, a) for k, c, a in zip(b["key"], b["client"], b["account"])}
    warns: list[str] = []

    df = df.copy()
    stripped = df["account_raw"].map(strip_id_suffix)
    df["_alias"] = stripped.str.lower().map(ACCOUNT_ALIASES).fillna(stripped)
    df["_k"] = df["_alias"].map(norm)

    # --- level 1: the Google Ads account IS a Budget Tracker account -------
    df["client"] = df["_k"].map(lambda k: by_acct.get(k, (None, None))[0])
    df["account"] = df["_k"].map(lambda k: by_acct.get(k, (None, None))[1])

    # --- level 2: the account holds many budgeted locations ---------------
    # e.g. Google Ads "Riccobene" -> Budget Tracker "Benson", "Hampstead", ...
    miss = df["account"].isna()
    if miss.any():
        for raw, grp in df[miss].groupby("account_raw"):
            if raw.lower() not in SPLIT_ACCOUNTS:
                continue
            # Only consider budget rows that could plausibly belong to this
            # Google Ads account — otherwise a short location name from
            # another client could win the match.
            same = {k: v for k, v in by_acct.items()
                    if any(norm(c) == norm(raw) for c in [v[0]])}
            keys = sorted(same or by_acct, key=len, reverse=True)
            hit = grp["campaign"].map(lambda c: match_location(c, keys))
            mapped = hit.map(lambda k: (same or by_acct).get(k, (None, None)))
            df.loc[grp.index, "client"] = [m[0] for m in mapped]
            df.loc[grp.index, "account"] = [m[1] for m in mapped]

    # --- location group (moves never cross one) ---------------------------
    def group(row):
        rule = LOCATION_RULES.get(str(row["account_raw"]).lower())
        return rule(row["campaign"]) if rule else row["account"]
    df["location"] = df.apply(group, axis=1)

    # --- report what fell out ---------------------------------------------
    lost = df[df["account"].isna()]
    if not lost.empty:
        for raw, g in lost.groupby("account_raw"):
            spend = g["cost"].sum()
            if spend <= 0:
                continue
            rule = LOCATION_RULES.get(str(raw).lower())
            if rule is not None:
                locs = sorted(set(g["campaign"].map(rule)))
                warns.append(f"'{raw}' — {len(locs)} location(s) spent ${spend:,.0f} "
                             f"MTD with no budget row: {', '.join(locs)}")
            elif (name := str(g["_alias"].iloc[0]).lower()) and any(
                    name in an or an in name for an in paused_names):
                warns.append(f"'{raw}' spent ${spend:,.0f} until it was paused.")
            else:
                warns.append(f"'{raw}' spent ${spend:,.0f} MTD but has no "
                             f"{channel} budget row in the Budget Tracker.")
    df = df[df["account"].notna()].copy()

    # budgeted accounts with no spend at all
    import config
    paused = {norm(x) for x in config.PAUSED_ACCOUNTS}
    seen = set(df["_k"]) | {norm(a) for a in df["account"]}
    for _, r in b.iterrows():
        if r["key"] not in seen and r["key"] not in paused:
            warns.append(f"{r['client']} / '{r['account']}' has a "
                         f"${r['allocated_budget']:,.0f} budget but no campaign data.")

    return df.drop(columns=["_alias", "_k"]).reset_index(drop=True), warns
