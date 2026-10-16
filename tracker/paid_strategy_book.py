"""Paid Marketing Agents: Strategy & Campaign Book, the Campaign Book itself.

The PPC team's Campaign Book is a sheet of Field | Value | Other things to check, one row per setting a launched campaign is checked
against (the checklist in tracker/qa_checklist.json, 61 rows for a Search campaign). The strategist fills it in for each campaign it plans:
the settings that depend on the plan (type, bid strategy, devices, genders, household income, locations, age, ad schedule, final URLs, the
assets and negatives to add) come from the campaign's own plan, the rest are the team's standing answers (AI Max off, no search
partners, auto-apply recommendations off...).

The rows use the checklist's own field names, so the file a person downloads here can be added to the QA Agent unchanged, and the QA
Agent finds the rows it looks up (tracker/paid_qa_book.py reads them back).
"""
from __future__ import annotations

import csv
import io

from . import paid_qa, paid_strategy as ps
from .paid_strategy_check import text

HEADER = ["Field", "Value", "Other things to check"]

# What a person should look at when the campaign is built, for the rows that depend on the plan or that Google Ads hides.
OTHER = {
    "1.1": "Name as P2_<type>_<theme>_<launch date>, one campaign per theme.",
    "1.2": "The campaign type decides which checks apply: Search is checked against this book, the others are judged on the spot.",
    "1.3": "Search Partners and the Display Network stay off on a Search campaign.",
    "1.4": "Headlines in Start Case, brand spelled as the client writes it, no claim the client has not confirmed.",
    "1.6": "Sitelinks, callouts, structured snippets and a call asset, as listed in the Assets sheet.",
    "1.8": "Move to a target only after the campaign has the conversions the plan names.",
    "1.9": "Only the primary conversion actions count towards the goal.",
    "1.10": "Compare with the device split of the account in the strategy.",
    "1.11": "Exclusions need a reason written here.",
    "1.12": "Exclusions need a reason written here.",
    "1.14": "Presence targeting, and any place to leave out, as in the strategy.",
    "1.16": "Exclusions need a reason written here.",
    "1.18": "Add the negative lists named in the Negatives sheet before launch.",
    "1.20": "Auto-tagging on, and the tracking template or final URL suffix set at the campaign or the account.",
    "1.22": "Start on the launch date, leave the end date empty.",
    "1.24": "Check the client's opening hours and who answers the phone.",
    "1.25": "Open every final URL: it must load and match the ad.",
    "3.1": "Google does not show this switch to the API: check it in the account.",
    "5.1": "Google does not show this switch to the API: check it in the account.",
    "5.2": "Google does not show this switch to the API: check it in the account.",
    "6.1": "Signals are a hint to Google, not a target.",
    "6.2": "No competitor names, no repeats.",
    "6.3": "Google's own rating, read after the campaign has served.",
    "6.4": "Audiences that fit the business, not demographics alone.",
}


URL_SUFFIX = "utm_source=google&utm_medium=cpc&utm_campaign={campaignid}&utm_content={adgroupid}&utm_term={keyword}"


def _count(items):
    return len(items or [])


def _first_urls(build, kind, n=6):
    urls = []
    if kind == "pmax":
        urls = [g.get("finalUrl") for g in build.get("assetGroups") or []]
    else:
        for g in build.get("adGroups") or []:
            urls.append(g.get("landingPage"))
            urls += [a.get("finalUrl") for a in g.get("ads") or [] if isinstance(a, dict)]
    out = []
    for u in urls:
        if u and u not in out:
            out.append(u)
    return out[:n]


def _assets_line(build):
    a = (build or {}).get("assets") or {}
    parts = []
    for key, label in (("sitelinks", "sitelinks"), ("callouts", "callouts"), ("snippets", "structured snippets"), ("promotions", "promotions")):
        if _count(a.get(key)):
            parts.append("%d %s" % (_count(a[key]), label))
    if (a.get("call") or {}).get("use"):
        parts.append("a call asset")
    return "Added: " + ", ".join(parts) if parts else "Added: to be written (none planned yet)"


def _ads_line(build, kind):
    if kind == "search":
        ads = sum(_count(g.get("ads")) for g in build.get("adGroups") or [])
        return "Minimum 1 ad per ad group (planned: %d responsive search ad%s in %d ad group%s)" % (
            ads, "" if ads == 1 else "s", _count(build.get("adGroups")), "" if _count(build.get("adGroups")) == 1 else "s")
    if kind == "pmax":
        return "Minimum 1 ad (planned: %d asset group%s)" % (_count(build.get("assetGroups")), "" if _count(build.get("assetGroups")) == 1 else "s")
    ads = sum(_count(g.get("ads")) for g in build.get("adGroups") or [])
    return "Minimum 1 ad (planned: %d ad%s)" % (ads, "" if ads == 1 else "s")


def _negatives_line(build):
    n = (build or {}).get("negatives") or {}
    lists = [l["name"] for l in n.get("lists") or []]
    base = "Negative Keyword List Added"
    parts = []
    if lists:
        parts.append("lists: " + ", ".join(lists))
    if _count(n.get("campaign")):
        parts.append("%d campaign-level negatives" % _count(n["campaign"]))
    return base + (" (" + "; ".join(parts) + ")" if parts else "")


def _signals_line(build):
    out = []
    for g in (build or {}).get("assetGroups") or []:
        s = g.get("signals") or {}
        bits = s.get("customSegments", [])[:3] + s.get("interests", [])[:3] + s.get("yourData", [])[:2]
        out.append("%s: %s" % (g["name"], ", ".join(bits) or "none planned"))
    return "; ".join(out) or "Every asset group has audience signals that fit the business."


def _themes_line(build):
    out = []
    for g in (build or {}).get("assetGroups") or []:
        out.append("%s: %s" % (g["name"], ", ".join(g.get("searchThemes", [])[:8]) or "none planned"))
    return "; ".join(out) or "Every asset group has search themes that match what the landing page sells."


def _dg_audiences(build):
    out = []
    for g in (build or {}).get("adGroups") or []:
        out.append("%s: %s" % (g["name"], ", ".join(g.get("audiences", [])[:5]) or "none planned"))
    return "; ".join(out) or "Every ad group targets audiences that fit the business."


def rows_for(campaign, inputs, strategy=None):
    """The campaign book rows for one planned campaign: [{'id', 'section', 'field', 'value', 'other', 'source'}] in the checklist's order.
    `source` is 'plan' when the value comes from this campaign's plan and 'standard' when it is the team's standing answer."""
    kind = campaign["kind"]
    build = campaign.get("build") or {}
    st = campaign.get("settings") or {}
    conv = [c["name"] for c in ((strategy or {}).get("measurement") or {}).get("conversions", []) if c.get("primary")]
    suffix = URL_SUFFIX
    plan = {
        "1.1": campaign["name"],
        "1.2": campaign["kindLabel"],
        "1.4": _ads_line(build, kind),
        "1.6": _assets_line(build),
        "1.8": st.get("bidStrategy", {}).get("value", ""),
        "1.9": "Conversion (Account Level Default Goals)" + ((": " + ", ".join(conv)) if conv else ""),
        "1.10": st.get("devices", {}).get("value", ""),
        "1.11": st.get("genders", {}).get("value", ""),
        "1.12": st.get("income", {}).get("value", ""),
        "1.14": st.get("locations", {}).get("value", ""),
        "1.15": st.get("language", {}).get("value", ""),
        "1.16": st.get("ages", {}).get("value", ""),
        "1.18": _negatives_line(build),
        "1.20": "Tracking template or final URL suffix at campaign or account level: " + suffix,
        "1.22": "Start %s, no end date" % (inputs.get("launch") or "on the launch date"),
        "1.24": st.get("schedule", {}).get("value", ""),
        "1.25": "; ".join(_first_urls(build, kind)) or "Working final URL for every ad (to be confirmed)",
        "6.1": _signals_line(build),
        "6.2": _themes_line(build),
        "6.4": _dg_audiences(build),
    }
    why = {"1.8": "bidStrategy", "1.10": "devices", "1.11": "genders", "1.12": "income", "1.14": "locations", "1.15": "language", "1.16": "ages", "1.24": "schedule"}
    out = []
    for it in paid_qa.applicable([], [ps.KINDS[kind]["qa"]]):
        cid = it["id"]
        if cid in plan and plan[cid]:
            value, source = plan[cid], "plan"
        else:
            value, source = text(it["expected"], 300), "standard"
        other = OTHER.get(cid, "")
        if cid in why and st.get(why[cid], {}).get("why"):
            reason = st[why[cid]]["why"].strip()
            other = ((reason if reason[-1:] in ".!?" else reason + ".") + " " + other).strip()
        out.append({"id": cid, "section": it["section"], "field": it["check"], "value": value, "other": other, "source": source})
    return out


def sheet_rows(book_rows):
    """The book as a sheet: a header, then Field | Value | Other things to check, with a blank row between sections so it reads like the template."""
    out, last = [HEADER], None
    for r in book_rows:
        if last is not None and r["section"] != last:
            out.append(["", "", ""])
        if r["section"] != last:
            out.append([r["section"], "", ""])
        last = r["section"]
        out.append([r["field"], r["value"], r["other"]])
    return out


def to_csv(book_rows):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(sheet_rows(book_rows))
    return buf.getvalue()


def all_rows(campaigns):
    """One flat table of every campaign's book: Campaign, Section, Field, Value, Other things to check."""
    out = [["Campaign", "Section", "Field", "Value", "Other things to check"]]
    for c in campaigns:
        for r in c.get("book") or []:
            out.append([c["name"], r["section"], r["field"], r["value"], r["other"]])
    return out
