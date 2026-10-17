"""Paid Marketing Agents: what a website carries for measuring and selling, read from the page's own tags.

A page says in its HTML which analytics, advertising and call-tracking tags it loads, whether it is built for a phone, and what a search engine is told
about the business. That is the evidence a new client's tracking and set-up questions start from (is there a Google Analytics tag, a Meta pixel, call
tracking, a booking tool, a consent banner). It is read from the scripts a page names and the first few inline scripts, so a tag that a consent
banner or a tag manager loads later is NOT seen here: every finding says what was seen, never that something is missing for certain.

No network and no database: `detect(page)` takes the parsed page (tracker/paid_qa_web._Page), `summarise(pages)` takes what `detect` returned for each page.
"""
from __future__ import annotations

import re

# (name, category, what to look for in a script address or in an inline script, how to find its id in the same text)
# (name, category, what to look for in a script address or an inline script, how to find its id, only_in_src)
# An id such as GTM-ABC1234 or G-ABCDE12345 is upper case: the patterns are matched case sensitively where they hold one, so `g-recaptcha-response` or `gtm-sidebar` is not a tag.
# A chat, booking or consent widget counts only when a script is loaded from its address: the address inside a page's JSON-LD (a profile link) is not the widget.
TOOLS = [
    ("Google Tag Manager", "tag manager", r"(?i:googletagmanager\.com/gtm\.js)|\bGTM-[A-Z0-9]{4,10}\b", r"\b(GTM-[A-Z0-9]{4,10})\b"),
    ("Google Analytics 4", "analytics", r"(?i:googletagmanager\.com/gtag/js\?id=G-|gtag\(\s*['\"]config['\"]\s*,\s*['\"]G-)|\bG-[A-Z0-9]{8,12}\b", r"\b(G-[A-Z0-9]{8,12})\b"),
    ("Universal Analytics (switched off by Google)", "analytics", r"(?i:google-analytics\.com/analytics\.js)|\bUA-\d{4,10}-\d{1,3}\b", r"\b(UA-\d{4,10}-\d{1,3})\b"),
    ("Google Ads tag", "advertising", r"\bAW-\d{8,12}\b|(?i:googleadservices\.com/pagead/conversion)", r"\b(AW-\d{8,12})\b"),
    ("Meta Pixel", "advertising", r"connect\.facebook\.net/[a-z_]+/fbevents\.js|\bfbq\s*\(", r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{8,20})['\"]"),
    ("LinkedIn Insight Tag", "advertising", r"snap\.licdn\.com/li\.lms-analytics|_linkedin_partner_id", r"_linkedin_partner_id\s*=\s*['\"]?(\d{4,12})"),
    ("TikTok Pixel", "advertising", r"analytics\.tiktok\.com|\bttq\.load\b", r"ttq\.load\(\s*['\"]([A-Z0-9]{8,30})['\"]"),
    ("Microsoft Advertising UET tag", "advertising", r"bat\.bing\.com/bat\.js|\buetq\b", r"ti\s*:\s*['\"]?(\d{6,12})"),
    ("Pinterest tag", "advertising", r"s\.pinimg\.com/ct/core\.js|\bpintrk\s*\(", r"pintrk\(\s*['\"]load['\"]\s*,\s*['\"](\d{8,20})['\"]"),
    ("HubSpot", "CRM and forms", r"js\.hs-scripts\.com|js\.hsforms\.net|hbspt\.forms|js\.hs-analytics\.net", r"hs-scripts\.com/(\d{4,12})\.js"),
    ("Salesforce Pardot", "CRM and forms", r"pi\.pardot\.com|piAId", r"piAId\s*=\s*['\"]?(\d{4,12})"),
    ("Marketo", "CRM and forms", r"munchkin\.marketo\.net|Munchkin\.init", r"Munchkin\.init\(\s*['\"]([0-9A-Z-]{10,24})['\"]"),
    ("CallRail", "call tracking", r"cdn\.callrail\.com|calltrk\.com", r""),
    ("CallTrackingMetrics", "call tracking", r"calltrackingmetrics\.com|tctm\.co", r""),
    ("WhatConverts", "call tracking", r"whatconverts\.com", r""),
    ("Hotjar", "behaviour", r"static\.hotjar\.com|\bhj\s*\(", r"hjid\s*:\s*(\d{5,10})"),
    ("Microsoft Clarity", "behaviour", r"clarity\.ms/tag", r""),
    ("Live chat", "chat", r"widget\.intercom\.io|js\.driftt\.com|livechatinc\.com|embed\.tawk\.to|code\.tidio\.co|zopim|podium\.com|birdeye\.com", r""),
    ("Online booking", "booking", r"calendly\.com|acuityscheduling\.com|setmore\.com|zocdoc\.com|nexhealth\.com|opentable\.com|squareup\.com/appointments|simplybook|mindbodyonline|"
                                  r"vagaro\.com|booksy\.com|jane\.app|zenoti\.com", r""),
    ("Cookie consent banner", "consent", r"cookiebot\.com|cdn\.cookielaw\.org|onetrust|osano\.com|iubenda\.com|termly\.io|cookieyes|consentmanager|usercentrics", r""),
]
_COMPILED = [(n, c, re.compile(look, 0 if "(?i:" in look else re.I), re.compile(idre) if idre else None) for n, c, look, idre in TOOLS]
SRC_ONLY = {"Live chat", "Online booking", "Cookie consent banner"}
LD_TYPE = re.compile(r'"@type"\s*:\s*"([A-Za-z]{3,40})"')
MAX_IDS = 3


def detect(page, final_url=""):
    """What one parsed page carries: {'tools': [{'name', 'category', 'ids'}], 'mobile': bool, 'schema': [types], 'https': bool}."""
    srcs = " \n".join(getattr(page, "scripts", None) or [])
    inline = " \n".join(getattr(page, "inline", None) or [])
    blob = srcs + " \n" + inline
    tools = []
    for name, cat, look, idre in _COMPILED:
        searched = srcs if name in SRC_ONLY else blob
        if not look.search(searched):
            continue
        ids = []
        if idre:
            for m in idre.finditer(blob):
                if m.group(1) not in ids:
                    ids.append(m.group(1))
        tools.append({"name": name, "category": cat, "ids": ids[:MAX_IDS]})
    types = []
    for m in LD_TYPE.finditer(inline):
        if m.group(1) not in types:
            types.append(m.group(1))
    viewport = (getattr(page, "viewport", "") or "").lower()
    return {"tools": tools, "mobile": "width=device-width" in viewport.replace(" ", ""), "schema": types[:12], "https": str(final_url).lower().startswith("https://")}


def summarise(pages):
    """What the whole site carries, from the `tech` of each page that was read: the tools found (with the ids and how many pages), the schema types, how
    many pages are built for a phone, and what was NOT seen, in words that say it is only what a plain read of the pages can tell."""
    found, schema, mobile, https, total = {}, [], 0, 0, 0
    for pg in pages or []:
        t = (pg or {}).get("tech")
        if not isinstance(t, dict):
            continue
        total += 1
        mobile += 1 if t.get("mobile") else 0
        https += 1 if t.get("https") else 0
        for x in t.get("schema") or []:
            if x not in schema:
                schema.append(x)
        for tool in t.get("tools") or []:
            row = found.setdefault(tool["name"], {"name": tool["name"], "category": tool["category"], "ids": [], "pages": 0})
            row["pages"] += 1
            for i in tool.get("ids") or []:
                if i not in row["ids"] and len(row["ids"]) < MAX_IDS:
                    row["ids"].append(i)
    tools = sorted(found.values(), key=lambda r: (r["category"], r["name"]))
    names = {r["name"] for r in tools}
    cats = {r["category"] for r in tools}
    gaps = []
    if total:
        if not ({"Google Analytics 4", "Google Tag Manager", "Universal Analytics (switched off by Google)"} & names):
            gaps.append("No Google Analytics or Google Tag Manager tag was seen on the %d page%s read." % (total, "" if total == 1 else "s"))
        elif "Universal Analytics (switched off by Google)" in names and "Google Analytics 4" not in names and "Google Tag Manager" not in names:
            gaps.append("Only the old Universal Analytics tag was seen: Google stopped collecting data for it in 2023, so check Google Analytics 4 is set up.")
        if "Google Ads tag" not in names and "Google Tag Manager" not in names:
            gaps.append("No Google Ads conversion tag was seen (it may load from a tag manager or after a consent banner).")
        if "call tracking" not in cats:
            gaps.append("No call tracking script was seen, so phone calls from the ads may not be counted unless Google forwarding numbers are used.")
        if not schema:
            gaps.append("No structured data (schema.org) was seen on the pages read.")
        if mobile < total:
            gaps.append("%d of %d pages read did not declare a mobile viewport." % (total - mobile, total))
        if https < total:
            gaps.append("%d of %d pages read were not served over https." % (total - https, total))
    return {"pages": total, "tools": tools, "schema": schema[:16], "mobilePages": mobile, "httpsPages": https, "gaps": gaps,
            "note": "Seen in the page's own scripts. A tag that loads later (after a consent choice, or from a tag manager) is not seen, so confirm each with the client." if total else ""}
