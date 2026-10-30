"""Vimi: every agent on the platform, what it holds, and the one way Vimi may read it (no network, no model).

Vimi answers from the same data the agents' own pages show, so each agent is listed here with its
page, a line on what it holds (the roster the model always sees), and its *reads*: the pages' own
read-only endpoints, or a local reader for the three sources that live in sheets and databases. A
read names the endpoint, the parameters it takes and what it returns. This is the allowlist: Vimi
cannot call a route that is not written here, cannot pass a parameter a read does not declare, and
has no read that changes anything or spends an ad platform's allowance on demand (there is no
`refresh`: a page's Refresh button is a person's decision).

The longer description of each agent is the platform knowledge text (`split_knowledge` cuts it into
one entry per agent), so the account of how an agent works is written once.

tests/test_vimi_catalog.py holds this to the app: every page under /p2/paid-marketing and
/p2/strategic-agents is either listed here or named in IGNORED with a reason, and every read's path
is a real GET route that takes the parameters it declares.
"""
from __future__ import annotations

import re

PAID = "/p2/paid-marketing"
API = PAID + "/api"
STRAT = "/p2/strategic-agents"

ACCOUNT_HELP = ("account: a 10-digit Google Ads customer id, a Meta ad account id (act_...), or both joined by + "
                "(1234567890+act_123...) for an agent that reads both. find_account returns the right string for a client name.")
RANGE_HELP = "start, end: YYYY-MM-DD (inclusive). Default to the last 30 complete days when the person names no range."


def read(view, path, about, params=None, *, account=False, admin=False, local=None):
    """One read. `params` is {name: description}; a {name} in the path is filled from the parameter of that name."""
    return {"view": view, "path": path, "about": about, "params": dict(params or {}), "account": account, "admin": admin, "local": local}


def agent(id, title, family, page, about, reads=(), *, hidden=False, know=None):
    return {"id": id, "title": title, "family": family, "page": page, "about": about, "reads": list(reads), "hidden": hidden, "know": know or id}


def _acct(**more):
    return dict({"account": ACCOUNT_HELP}, **more)


def _rng(**more):
    return dict({"account": ACCOUNT_HELP, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}, **more)


# ── Paid Marketing agents ─────────────────────────────────────────────────────

PAID_AGENTS = [
    agent("pacing-desk", "Pacing Desk", "paid", PAID + "/pacing-desk",
          "Every client's Google (and Meta) spend this month against its allocation, projected to month end, over/under/on target, with budget moves between campaign types.",
          [read("latest", API + "/pacing/snapshots", "The dates of every saved daily run and which is the latest. Read this first, then 'snapshot' with the latest date."),
           read("snapshot", API + "/pacing/snapshots/{date}", "One day's full run: every client, account and campaign with spend, projection, pacing verdict and recommended moves.", {"date": "YYYY-MM-DD from 'latest'"}),
           read("clients", API + "/pacing/clients", "The client names in the latest run, with their accounts."),
           read("moves", API + "/pacing/moves", "Which recommended budget moves the team ticked as applied, for one run date.", {"date": "YYYY-MM-DD"}),
           read("allocations", API + "/pacing/budgets", "This month's budget allocations the team saved (client, account, channel, allocated budget, target CPA, days running, status).")]),
    agent("performance-summarizer", "Performance Summarizer", "paid", PAID + "/performance-summarizer",
          "Live numbers for any Google Ads or Meta account: impressions, clicks, cost, conversions, value, impression share, by campaign and day, compared with an earlier range.",
          [read("performance", API + "/gads/performance", "Campaign-by-day numbers for the range, the account's totals and (when asked) the same for a comparison range, with impression-share columns on Google.",
                _rng(compare_start="YYYY-MM-DD, to compare with an earlier range", compare_end="YYYY-MM-DD"), account=True),
           read("breakdowns", API + "/gads/extras", "The same range split by device, network and weekday/hour.", _rng(), account=True)]),
    agent("product-analysis", "Product Analysis", "paid", PAID + "/product-analysis",
          "Ecommerce: every Shopping and Performance Max product sorted into stars, steady sellers, below target and spend-with-no-sales, with what to cut, fix or push.",
          [read("products", API + "/gads/products", "Per-product numbers, the classification of each product and the recommendations, for a range and optionally a comparison range.",
                _rng(compare_start="YYYY-MM-DD", compare_end="YYYY-MM-DD"), account=True)]),
    agent("search-query-analysis", "Search Query Analysis", "paid", PAID + "/search-query-analysis",
          "Google search terms, the account's keywords and negative keywords for a range, and which client ruleset applies.",
          [read("search_terms", API + "/sqr/data", "Search terms with clicks, cost and conversions, the keywords and the negative keywords of the account (Google only). Another call tells which terms to negate: reason it from the rows.",
                _rng(), account=True)]),
    agent("client-feedback-agent", "Client Feedback Agent", "paid", PAID + "/client-feedback-agent",
          "A chat over Slack, Gmail, meeting transcripts and the Pacing Desk allocations. Vimi does not read Slack or email: send the person to this page for client conversations.", []),
    agent("segment-analysis", "Segment Analysis", "paid", PAID + "/segment-analysis",
          "Day of week, hour, age, gender, city, device and network cuts with the segments worth pushing or cutting, for one account or a whole MCC.",
          [read("report", API + "/segments/report", "Every segment table (each cut by campaign type), the rule-based insights with evidence and confidence, and coverage caveats.",
                {"account": ACCOUNT_HELP, "from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}, account=True),
           read("accounts", API + "/segments/accounts", "Every Google Ads account (and MCC group) under the Position2 manager account, with its currency.")]),
    agent("qa-agent", "QA Agent", "paid", PAID + "/qa-agent",
          "Pre-launch QC of a campaign against the team's Campaign Book checklist; each QC is saved per campaign.",
          [read("campaigns", API + "/qa/campaigns", "The account's live campaigns, each with its saved QC state.", _acct(), account=True),
           read("qc", API + "/qa/launches/{launch_id}", "One saved QC: every check's state and the verdict.", {"launch_id": "id from 'campaigns'"}),
           read("checklist", API + "/qa/checklist", "The checklist the QC holds a campaign against.", {"platform": "meta for the Meta checklist, otherwise Google"})]),
    agent("geo-performance", "Geo Performance", "paid", PAID + "/geo-performance",
          "Results by region, city or postal code, with where to push and where to cut.",
          [read("report", API + "/geo/report", "Geographic table and insights.", {"account": ACCOUNT_HELP, "level": "region (default), city or postal", "from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}, account=True)]),
    agent("seasonality-analysis", "Seasonality Analysis", "paid", PAID + "/seasonality-analysis",
          "Monthly and weekday seasonality index against a moving average, holiday effects, year on year and a three-month budget plan.",
          [read("report", API + "/gads/seasonality", "The seasonality indices, holiday effects and budget plan for one account.", {"account": ACCOUNT_HELP, "months": "months of history to read (the page's default when omitted)"}, account=True)]),
    agent("call-transcript-analyzer", "Call Transcript Analyzer", "paid", PAID + "/call-transcript-analyzer",
          "Calls an ad account drives, from CallRail: counts, answered, appointment booked, what callers wanted. Counts and metadata only (HIPAA accounts: no call text exists here).",
          [read("accounts", API + "/calls/accounts", "The CallRail accounts that can be read."),
           read("locations", API + "/calls/locations", "The locations (companies) of a CallRail account.", {"account": "CallRail account id from 'accounts'"}),
           read("overview", API + "/calls/overview", "Call counts for a range.", {"account": "CallRail account id", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "company": "location id (optional)", "source": "traffic source name (optional)"}),
           read("analysis", API + "/calls/analysis", "What callers wanted and whether they booked, for a sample of the transcribed calls.", {"account": "CallRail account id", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "company": "location id (optional)", "source": "traffic source name (optional)"})]),
    agent("ad-strength", "Ad Strength Analysis", "paid", PAID + "/ad-strength",
          "Google's ad strength for every live responsive search ad and Performance Max asset group, with Google's own tips (Meta: ad quality rankings and frequency).",
          [read("report", API + "/adstrength/report", "Each ad or asset group with its strength, the tips and the account's roll-up.", _acct(), account=True)]),
    agent("audience-segments", "Audience Segment Analysis", "paid", PAID + "/audience-segments",
          "How each audience segment performs (Google audiences; Meta ad sets), what to expand, test or exclude.",
          [read("report", API + "/audiences/report", "Audience segments with spend, conversions and efficiency, and the insights.", {"account": ACCOUNT_HELP, "from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}, account=True)]),
    agent("asset-coverage", "Asset Coverage Audit", "paid", PAID + "/asset-coverage",
          "Every asset of live campaigns held to Google's minimums and the team's targets (Meta: ads per ad set and formats).",
          [read("report", API + "/assets/report", "Per campaign and asset kind: counts against the minimum and target, and what is missing.", _acct(), account=True),
           read("campaign", API + "/assets/campaign", "One campaign's assets (text, images, dates).", {"account": ACCOUNT_HELP, "campaign": "campaign id or name from 'report'"}, account=True),
           read("library", API + "/assets/library", "Every asset of one kind.", {"account": ACCOUNT_HELP, "kind": "asset kind from 'report'", "level": "campaign or asset group (optional)", "state": "all, active or paused (optional)"}, account=True)]),
    agent("campaign-book", "Strategy & Campaign Book", "paid", PAID + "/campaign-book",
          "Saved strategy builds: research on a client then a strategy and a per-campaign book.",
          [read("list", API + "/strategy/list", "Recent saved builds with client, account and status."),
           read("build", API + "/strategy/{sid}", "One saved strategy build: the research cards, the strategy and the campaign books.", {"sid": "build id from 'list'"})],
          know="campaign-book"),
    agent("business-research", "Business Research", "paid", PAID + "/business-research",
          "Saved business research on a client: business, audience, reputation, tags on the site, competitors, and questions for the client.",
          [read("list", API + "/business/list", "Recent saved business research."),
           read("build", API + "/business/{sid}", "One saved business research.", {"sid": "id from 'list'"})]),
    agent("keyword-research", "Keyword Research", "paid", PAID + "/keyword-research",
          "Saved keyword research: Keyword Planner ideas against the account's own keywords and negatives, themed and judged.",
          [read("list", API + "/keywords/list", "Recent saved keyword research."),
           read("build", API + "/keywords/{sid}", "One saved keyword research.", {"sid": "id from 'list'"})]),
    agent("account-audit", "Account Audit", "paid", PAID + "/account-audit",
          "About 70 checks on a Google and/or Meta account (settings, tracking, structure, bidding, keywords, ads, landing pages), each with a severity and a result, plus saved audits and what changed since.",
          [read("report", API + "/audit/report", "The live audit: every check with severity, result and evidence, and the score.", _acct(), account=True),
           read("history", API + "/audit/history", "Saved audits of the account.", _acct(), account=True),
           read("compare", API + "/audit/compare", "The audit now against a saved one: what got better and worse.", {"account": ACCOUNT_HELP, "id": "saved audit id (optional: the latest)"}, account=True)]),
    agent("landing-page-check", "Landing Page & Relevancy Check", "paid", PAID + "/landing-page-check",
          "Every landing page of the live ads fetched and checked (35 to 39 checks): message match, speed, forms, trust, tracking; fix-first list.",
          [read("report", API + "/lp/report", "Pages with their checks and the findings grouped by check.", _acct(), account=True)]),
    agent("experiment-tracker", "Test & Experiment Tracker", "paid", PAID + "/experiment-tracker",
          "Google experiments and ad tests (multi-ad ad groups, Meta A/B tests) with statistical verdicts and a needs-a-decision list.",
          [read("report", API + "/exp/report", "Every test with results, the significance verdict and what to decide.", _acct(), account=True)]),
    agent("unknown-domains", "Unknown Domains Alert", "paid", PAID + "/unknown-domains",
          "Ads and links that send people to a domain the team has not approved, and broken or redirecting links.",
          [read("state", API + "/domains/state", "Open findings, the known-domain database and what each account sends traffic to.")]),
    agent("brand-is", "Brand IS Monitor", "paid", PAID + "/brand-is",
          "Weekly brand-campaign impression share against a threshold, why it is short (budget or rank), what moved. Google only.",
          [read("report", API + "/brandis/report", "Per account and week: brand impression share, lost to budget and to rank, against the threshold.", {"account": ACCOUNT_HELP, "threshold": "impression share threshold, percent (optional)", "brand": "comma separated campaign names to treat as brand (optional)", "notbrand": "campaign names to treat as not brand (optional)"}, account=True),
           read("diagnose", API + "/brandis/diagnose", "Why brand impression share is short: campaign status, bid strategy, keywords, ads.", {"account": ACCOUNT_HELP, "threshold": "percent (optional)"}, account=True)]),
    agent("brand-spend", "Brand Spend Alert", "paid", PAID + "/brand-spend",
          "Brand campaign spend against the usual week and the client's weekly range; the search terms behind a rise. Google only.",
          [read("report", API + "/brandspend/report", "Brand spend this week against its usual, the issues, and the terms.", {"account": ACCOUNT_HELP, "limit": "rows of terms (optional)", "brand": "brand campaign names (optional)", "notbrand": "not brand campaign names (optional)"}, account=True),
           read("settings", API + "/brandspend/settings", "The saved brand terms, budgets and limits per account.")]),
    agent("disapproved-ads", "Disapproved Ads Tracker", "paid", PAID + "/disapproved-ads",
          "Disapproved or limited ads, Performance Max assets and keywords (and Meta ads), how long, what policy, what is new.",
          [read("report", API + "/disapproved/report", "Every disapproved item with its policy, age and status; new, worse, still open and fixed.", _acct(), account=True)]),
    agent("ad-copy-checker", "Ad Copy Checker", "paid", PAID + "/ad-copy-checker",
          "Proofreads live Google and Meta ad text: spelling, capitalisation, phone numbers, places, brand name, allowed words.",
          [read("report", API + "/copycheck/report", "Every ad with the problems found in its text.", _acct(), account=True),
           read("settings", API + "/copycheck/settings", "The saved phone, place, brand and allowed-word lists.")]),
    agent("spend-anomaly", "Spend Anomaly Alert", "paid", PAID + "/spend-anomaly",
          "Account spend by day and hour against its usual: spikes, surges, budget ceilings and drops, with the cause traced (budget change, bid change, campaign launch).",
          [read("report", API + "/anomaly/report", "Daily and hourly spend, the anomalies, and the likely cause of each.", _acct(), account=True),
           read("settings", API + "/anomaly/settings", "Sensitivity and saved settings per account.")]),
    agent("location-conflicts", "Location Conflicts", "paid", PAID + "/location-conflicts",
          "Campaign locations against every place named in ad groups, ads and keywords: wrong city, targeted and excluded, presence or interest, schedule and device drift.",
          [read("report", API + "/geoconf/report", "Each conflict with the campaign, the place and the fix.", _acct(), account=True),
           read("settings", API + "/geoconf/settings", "Ignored items and saved areas.")]),
    agent("kpi-monitoring", "KPI Monitoring", "paid", PAID + "/kpi-monitoring",
          "The last 7 days of each campaign against its target CPA/ROAS (the client's, or the bid strategy's own): stopped converting, learning, budget limited, delivery.",
          [read("report", API + "/kpi/report", "Each campaign's last-7-day numbers against target and the issues found.", _acct(), account=True),
           read("settings", API + "/kpi/settings", "The saved targets and muted campaigns.")]),
    agent("master-agent", "Master Agent", "paid", PAID + "/master-agent",
          "The Client Master Config: per client its Google and Meta accounts, budgets, KPI targets, locations, phone numbers, brand terms, allowed domains, competitors and offers, and where agents disagree.",
          [read("state", API + "/master/state", "Every client with its accounts and shared settings, and the drift between the agents' own saved settings and the master.")]),
    agent("alert-hub", "Alert Hub", "paid", PAID + "/alert-hub",
          "Every monitor's alerts through one layer: P1/P2/P3, issues held back while another covers them, the destinations, the weekly summary.",
          [read("state", API + "/alerthub/state", "The destinations, every open issue of every monitor and the last sends."),
           read("monitor", API + "/monitors/{agent}/state", "One monitor's own state: its open issues, last run and baseline.", {"agent": "brandspend, disapproved, copycheck, anomaly, geoconf, kpi, kvis, track, watch, creative or brandis"})]),
    agent("competitor-analysis", "Competitor Analysis", "paid", PAID + "/competitor-analysis",
          "Competitors' live ads (Meta Ad Library, Google Transparency Center) compared by message with the client's own ads, with creative ideas.",
          [read("report", API + "/compete/report", "The competitors, their ads and the comparison with the account's own.", _acct(), account=True),
           read("ads", API + "/compete/ads", "The gallery of competitor ads.", _acct(platform="meta or google (optional)", competitor="competitor name (optional)", format="ad format (optional)", q="search text (optional)", offset="paging offset", limit="page size"), account=True),
           read("brief", API + "/compete/brief", "The ideas Claude wrote for the account, if any.", _acct(), account=True)]),
    agent("creative-health", "Creative & Asset Health", "paid", PAID + "/creative-health",
          "Creative fatigue and asset health across Search, Performance Max and Meta.",
          [read("report", API + "/creative/report", "Each asset or creative with its health and fatigue.", _acct(), account=True)]),
    agent("account-watch", "Account Watch", "paid", PAID + "/account-watch",
          "Account-level changes and risks: what changed, what stopped, what is at risk.",
          [read("report", API + "/watch/report", "Findings about the account.", _acct(), account=True)]),
    agent("tracking-health", "Tracking & Data Health", "paid", PAID + "/tracking-health",
          "Conversion tracking health: conversion actions firing, tags, data freshness and gaps.",
          [read("report", API + "/track/report", "Each conversion action or pixel with its health, and the findings.", _acct(), account=True)]),
    agent("keyword-visibility", "Keyword Visibility Tracker", "paid", PAID + "/keyword-visibility",
          "Top-spend keywords' top-of-page share, rank lost, Quality Score by week, auction-insight competitors and brand hijack. Google only.",
          [read("report", API + "/kvis/report", "Keywords with visibility trends, Quality Score history and the competitors in the auction.", _acct(), account=True),
           read("settings", API + "/kvis/settings", "Ignored keywords and saved settings.")]),
    agent("ad-copy", "Ad Copy & Creative Generator", "paid", PAID + "/ad-copy",
          "Saved ad copy and creative-brief builds (Google and Meta formats), claims marked Confirm.",
          [read("list", API + "/adcopy/list", "Recent saved ad copy builds."),
           read("build", API + "/adcopy/{sid}", "One saved build: every line with length, angle and ready state, and the briefs.", {"sid": "id from 'list'"})]),
    agent("cross-channel", "Cross-Channel & Attribution Analysis", "paid", PAID + "/cross-channel",
          "Google and Meta side by side and blended: spend, conversions, value, CPA, ROAS, the change in blended cost split by platform, campaign types, and how each platform counts a conversion.",
          [read("report", API + "/xchannel/report", "Both platforms for the range against the period before, with findings.",
                {"account": ACCOUNT_HELP + " Both platforms must bill in the same currency.", "range": "30, 60, 90, last_month or month_to_date", "measure": "primary (conversions each platform bids on) or all", "actualConv": "conversions the client's own system recorded (optional)", "actualValue": "revenue the client's own system recorded (optional)"}, account=True)]),
]

# ── Strategic agents and signal sources ───────────────────────────────────────

STRATEGIC_AGENTS = [
    agent("anonymous-visitors", "Anonymous Website Visitors", "strategic", STRAT + "/anonymous-visitors",
          "Which companies (and, where a signal exists, people) visit position2.com: company, industry, size, location, pages, date.",
          [read("visitors", "", "Visitors from the sheet: companies that visited and identified people, newest first, filterable.",
                {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD", "company": "company name or domain", "seniority": "c-suite, vp, director, manager or a title word", "industry": "industry word", "limit": "max rows (default 50)"}, local="visitors")]),
    agent("abm-signal-tracker", "ABM Signal Tracker", "signals", "/p2/abm-signal-tracker/accounts",
          "Buying signals (funding, leadership change, M&A, IPO, product launch, partnerships, hiring) for the Healthcare and CSG account lists, with severity and importance.",
          [read("signals", "", "Signals for one account (healthcare or csg), newest first, filterable.",
                {"account": "healthcare or csg", "signal_type": "e.g. Funding Round, C-Suite Join, C-Suite Exit, Acquisition / M&A, News Mention, IPO Signal", "company": "company name", "severity": "HIGH or LOW", "limit": "max rows"}, local="signals"),
           read("weekly", "/api/weekly-stats/{account_id}", "Signals per week for an account.", {"account_id": "healthcare or csg"})]),
    agent("ad-intelligence", "Competitor Ad Intelligence", "strategic", STRAT + "/ad-intelligence",
          "Competitors' running ads (headline, CTA, format, keywords, messaging angle, first and last seen) from a shared sheet.",
          [read("ads", "", "Tracked competitor ads, filterable.", {"competitor": "name or domain", "ad_format": "image, text or video", "status": "active or inactive", "keyword": "word in the headline or keywords", "limit": "max ads"}, local="ads")]),
    agent("job-change-alert", "Job Change Alert", "strategic", STRAT + "/job-change-alert",
          "New job changes at people the team watches (from Apollo's Slack notifications) and the full tracked roster.",
          [read("changes", STRAT + "/job-change-alert/data", "Newly detected job changes and weekly momentum."),
           read("tracked", STRAT + "/job-change-alert/tracked", "The tracked people and companies.")]),
    agent("42-north-dental-slot-checker", "42 North Dental Slot Checker", "strategic", STRAT + "/42-north-dental-slot-checker",
          "What a new patient would be offered if they tried to book now, across the dental chain's locations. 'Gentle Dental' is still a real brand in the data.",
          [read("slots", STRAT + "/42-north-dental-slot-checker/data", "Locations with their next available slots, by brand. Reason the briefing from these rows.")]),
    agent("linkedin-intelligence", "LinkedIn Intelligence", "strategic", STRAT + "/linkedin-intelligence",
          "Position2's own LinkedIn engagement: post by post and person by person, from a Google Sheet.",
          [read("data", STRAT + "/linkedin-intelligence/data", "Posts, people and engagement."),
           read("scraper", STRAT + "/linkedin-scraper/data", "The LinkedIn scraper's data.")]),
    agent("linkedin-strategy-researcher", "LinkedIn Strategy Researcher", "strategic", STRAT + "/linkedin-strategy-researcher",
          "Competitive LinkedIn analysis of a company: profile, posts, strategy, content, messaging, scorecard; saved runs and playbooks.",
          [read("history", STRAT + "/linkedin-strategy-researcher/history", "Saved runs."),
           read("run", STRAT + "/linkedin-strategy-researcher/runs/{run_id}", "One saved run with every part of the analysis.", {"run_id": "number from 'history'"}),
           read("playbook", STRAT + "/linkedin-strategy-researcher/runs/{run_id}/playbook", "The playbook from a run.", {"run_id": "number from 'history'", "mode": "playbook mode (optional)"})]),
    agent("contact-finder", "Contact Finder", "strategic", STRAT + "/company-people-intelligence",
          "Finds people and companies through Apollo; saved searches and the working list (per person). Spending credits is always an explicit action on the page.",
          [read("history", STRAT + "/company-people-intelligence/history", "The signed-in person's recent saved searches."),
           read("search", STRAT + "/company-people-intelligence/history/{entry_id}", "One saved search with its rows.", {"entry_id": "number from 'history'"}),
           read("list", STRAT + "/company-people-intelligence/list", "The person's working list.", {"entity": "people or companies (optional)"}),
           read("credits", STRAT + "/company-people-intelligence/credits", "What the tool has spent from the shared Apollo credit pool.")],
          know="company-people-intelligence"),
    agent("event-conference-intelligence", "Event & Conference Intelligence", "strategic", STRAT + "/event-conference-intelligence",
          "Scores a client's event calendar against its ICP, looks up who an event publishes as exhibitors/sponsors/speakers, and prepares post-event follow-up.",
          [read("profiles", STRAT + "/event-conference-intelligence/profiles", "The saved client profiles."),
           read("outcomes", STRAT + "/event-conference-intelligence/outcomes", "What the team decided about each event."),
           read("run", STRAT + "/event-conference-intelligence/runs/{run_id}", "One saved run: ranked candidates, roster, outreach drafts.", {"run_id": "number"})]),
    agent("social-media-intelligence", "Social Media Intelligence", "strategic", STRAT + "/social-media-intelligence",
          "Resolves a company's social handles, reads recent posts, looks at every image and video, and reports content patterns and what correlates with engagement. Reddit as a seventh surface.",
          [read("run", STRAT + "/social-media-intelligence/runs/{run_id}", "One saved run's report.", {"run_id": "number"})],
          know="social-media-intelligence"),
    agent("thought-leader-pr", "Thought Leader Intelligence", "strategic", STRAT + "/thought-leader-pr",
          "Resolves a public figure, reads their posts, how people react, press coverage, and writes a reputation verdict.",
          [read("run", STRAT + "/thought-leader-pr/runs/{run_id}", "One saved run.", {"run_id": "number"})]),
    agent("market-radar", "Market Radar", "strategic", "/p2/admin/market-radar",
          "URL-only industry, competitor and local radar for a company: what its competitors did, weekly updates. Admin only.",
          [read("clients", "/p2/admin/market-radar/api/clients", "The companies on the radar.", admin=True),
           read("report", "/p2/admin/market-radar/api/clients/{client_id}/report", "The latest report of one company.", {"client_id": "number from 'clients'"}, admin=True),
           read("moves", "/p2/admin/market-radar/api/clients/{client_id}/moves", "What the company's competitors did.", {"client_id": "number"}, admin=True),
           read("monitor", "/p2/admin/market-radar/api/clients/{client_id}/monitor", "The weekly update settings and the latest update.", {"client_id": "number"}, admin=True)]),
]

# ── Admin analytics (the pages' own admin check applies) ──────────────────────

ADMIN_AGENTS = [
    agent("admin-analytics", "Admin dashboards", "admin", "/p2/admin/internal-usage",
          "How the platform itself is used: who uses which agent (internal and external), anonymous traffic, public page analytics, agent runs, agent feedback. Admin only.",
          [read("internal_usage", "/p2/admin/internal-usage/data", "Internal usage by person and agent.", admin=True),
           read("external_usage", "/p2/admin/external-usage/data", "Sign-ins and usage of people outside Position2.", admin=True),
           read("anonymous_traffic", "/p2/admin/anonymous-traffic/data", "The visitor de-anonymisation dashboard's aggregates.", admin=True),
           read("page_analytics", "/p2/admin/public-page-analytics/data", "Public page analytics.", admin=True),
           read("agent_usage", "/p2/admin/public-agent-usage/data", "Public agent usage.", admin=True),
           read("agent_runs", "/p2/admin/agent-runs/data", "Agent runs.", admin=True),
           read("agent_feedback", "/p2/admin/agent-feedback/data", "The thumbs up and down on agent output.", {"agent_slug": "agent slug (optional)", "rating": "up or down (optional)"}, admin=True),
           read("members", "/p2/admin/members/data", "Members.", admin=True)]),
]

AGENTS = PAID_AGENTS + STRATEGIC_AGENTS + ADMIN_AGENTS
BY_ID = {a["id"]: a for a in AGENTS}

# Pages that exist under the two sections but are not agents Vimi reads, with the reason (the test holds the list honest).
IGNORED = {
    "/p2/paid-marketing/search-query-analysis/": "alias of search-query-analysis",
    "/p2/strategic-agents/gentle-dental-slot-checker": "old name of the 42 North Dental Slot Checker",
    "/p2/strategic-agents/social-creative-intelligence": "old name of Social Media Intelligence",
    "/p2/strategic-agents/linkedin-playbook-studio": "a form that writes a playbook; holds no data of its own",
    "/p2/strategic-agents/linkedin-social-researcher": "an external embedded tool; its data is not held here",
    "/p2/strategic-agents/sentiment-pulse": "an external embedded tool",
    "/p2/strategic-agents/linkedin-scraper": "listed under LinkedIn Intelligence",
    "/p2/strategic-agents": "the section's index page",
    "/p2/paid-marketing": "the section's index page",
}


# ── looking agents up ─────────────────────────────────────────────────────────

def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def find(query):
    """The agent a name, slug or part of a title points to, or None."""
    q = norm(query)
    if not q:
        return None
    for a in AGENTS:
        if q in (norm(a["id"]), norm(a["title"])):
            return a
    hits = [a for a in AGENTS if q in norm(a["title"]) or q in norm(a["id"]) or norm(a["title"]) in q]
    return hits[0] if len(hits) == 1 else None


def search(query, limit=8):
    """Agents ranked by how many of the query's words their title, line and read descriptions contain."""
    words = [w for w in norm(query).split() if len(w) > 2]
    if not words:
        return []
    scored = []
    for a in AGENTS:
        hay = norm(" ".join([a["id"], a["title"], a["about"]] + [r["about"] for r in a["reads"]]))
        score = sum(2 if w in norm(a["title"]) else 1 for w in words if w in hay)
        if score:
            scored.append((score, a))
    return [a for _s, a in sorted(scored, key=lambda t: -t[0])[:limit]]


def roster(hidden_ids=()):
    """The compact list the model always sees: id, title and what the agent holds."""
    lines = []
    for family, label in (("paid", "PAID MARKETING AGENTS"), ("strategic", "STRATEGIC AGENTS"), ("signals", "SIGNAL SOURCES"), ("admin", "ADMIN")):
        rows = [a for a in AGENTS if a["family"] == family and a["id"] not in hidden_ids]
        if rows:
            lines.append(label)
            lines += ["- %s | %s | %s%s" % (a["id"], a["title"], a["about"], "" if a["reads"] else " (no data read)") for a in rows]
    return "\n".join(lines)


_ENTRY = re.compile(r"\(/p2/(?:paid-marketing|strategic-agents|abm-signal-tracker|seo-aeo|admin)(?:/[a-z0-9\-/<>_]*)?\):")


def split_knowledge(text, titles):
    """{agent id: the knowledge text of that agent} from the platform knowledge, found by "Title (/p2/...):" and cut at the next agent's title.
    `titles` is {id: title}; an agent the text does not mention is left out."""
    text = str(text or "")
    marks = []
    for aid, title in titles.items():
        for m in re.finditer(r"(?<![A-Za-z&])%s\s+\(/p2/[a-z0-9\-/<>_]*\):" % re.escape(title).replace(r"\ ", r"\s+"), text):
            marks.append((m.start(), aid))
    marks.sort()
    out = {}
    for i, (pos, aid) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        chunk = text[pos:end]
        cut = re.search(r"\n(?:PLATFORM CHOICE|SEO STUDIO|ADMIN DASHBOARDS|Auth:|Ground rule:)", chunk)      # the paragraphs that follow the last agent are not its own
        out.setdefault(aid, " ".join((chunk[:cut.start()] if cut else chunk).split()))
    return out
