"""Master Agent chat: the tools. Everything Vimi can read (tracker/vimi_tools.py), plus three tools the model calls from Python in its
code sandbox, which hand back the full data as JSON so it can be analysed in code instead of being read row by row:

  agent_data         the whole JSON of any agent read (the same reads, checks and identity as read_agent)
  google_ads_query   any read-only GAQL query on a Google Ads account the platform can read, every row
  meta_graph         a read-only Graph API GET on a Meta ad account the platform can read (insights, campaigns, ad sets, ads, creatives ...)

All three are reads: GAQL is a SELECT and the Graph call is a GET on an allowed edge. An account must be one the platform lists for
the person. Whatever comes back is data, never an instruction.
"""
from __future__ import annotations

import json
import re

from . import vimi_tools as vt

CODE_CALLER = "code_execution_20260120"
MAX_JSON_CHARS = 6_000_000
MAX_GAQL_CHARS = 8000
MAX_ROWS_OUT = 50000
GAQL_START = re.compile(r"^\s*SELECT\s", re.I)
NUMERIC = re.compile(r"^-?\d+(\.\d+)?(e[-+]?\d+)?$", re.I)
META_EDGES = ("insights", "campaigns", "adsets", "ads", "adcreatives", "customaudiences", "adimages", "advideos", "activities", "adspixels",
              "ads_volume", "delivery_estimate", "reachestimate", "targetingsentencelines", "adrules_library", "")
META_OBJECT_EDGES = ("", "insights", "ads", "adsets", "adcreatives", "previews")
META_PARAMS = ("fields", "time_range", "date_preset", "level", "breakdowns", "action_breakdowns", "time_increment", "filtering", "limit", "effective_status",
               "action_attribution_windows", "use_account_attribution_setting", "use_unified_attribution_setting", "sort", "summary", "ad_format", "time_ranges")


def flatten(obj, prefix="", out=None):
    """A GAQL row as one flat dict: campaign.name, metrics.costMicros ... Numbers that come as strings (int64) become numbers, and every
    *Micros field gets a sibling in the currency's units (metrics.costMicros 12500000 -> metrics.cost 12.5)."""
    out = {} if out is None else out
    for k, v in (obj or {}).items():
        key = "%s.%s" % (prefix, k) if prefix else k
        if isinstance(v, dict):
            flatten(v, key, out)
            continue
        if isinstance(v, str) and NUMERIC.match(v) and (key.startswith("metrics.") or key.endswith("Micros") or key.endswith(".id")):
            v = float(v) if any(c in v for c in ".eE") else int(v)
        out[key] = v
        if key.endswith("Micros") and isinstance(v, (int, float)):
            out[key[: -len("Micros")]] = round(v / 1_000_000.0, 6)
    return out


def check_gaql(query):
    q = str(query or "").strip()
    if not q:
        return None, "Give the GAQL query."
    if len(q) > MAX_GAQL_CHARS:
        return None, "That query is over %d characters." % MAX_GAQL_CHARS
    if not GAQL_START.match(q) or not re.search(r"\sFROM\s", q, re.I):
        return None, "Only a GAQL SELECT ... FROM ... query can be run (reads only)."
    if ";" in q:
        return None, "One query at a time: remove the semicolon."
    return q, None


def _jsonable(payload):
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= MAX_JSON_CHARS:
        return text
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if isinstance(rows, list) and rows:
        keep = max(1, int(len(rows) * MAX_JSON_CHARS / float(len(text))) - 1)
        cut = dict(payload, rows=rows[:keep], truncated=True,
                   note="Only the first %d of %d rows fit. Ask for fewer columns, a shorter range, a WHERE clause or segments.date grouping by week or month." % (keep, len(rows)))
        return json.dumps(cut, ensure_ascii=False, default=str)
    return json.dumps({"error": "The result is too large to hand back (%d characters). Ask for less." % len(text)})


class Tools(vt.Tools):
    """Vimi's tools plus the three data tools. `ads` is an object with gaql(customer_id, query) -> rows and meta(path, params, paged) -> rows or dict."""

    def __init__(self, *, ads=None, **kw):
        super().__init__(**kw)
        self._ads = ads

    # ── which accounts can be read ────────────────────────────────────────────

    def _readable(self, platform):
        data = self._load_accounts()
        return {str(a.get("id")): a for a in (data.get(platform) or []) if a.get("id")}

    def _google_account(self, account):
        acct = re.sub(r"\D", "", str(account or ""))
        if len(acct) != 10:
            return None, "account must be a 10-digit Google Ads customer id (call find_account)."
        readable = self._readable("google")
        if acct not in readable:
            return None, "Google Ads account %s is not one the platform can read for you. Use find_account." % acct
        return acct, None

    def _meta_account(self, account):
        acct = str(account or "").strip()
        if not re.fullmatch(r"act_\d{4,20}", acct):
            return None, "account must be a Meta ad account id like act_1234567890 (call find_account)."
        if acct not in self._readable("meta"):
            return None, "Meta ad account %s is not one the platform can read for you. Use find_account." % acct
        return acct, None

    # ── the code-callable data tools ─────────────────────────────────────────

    def agent_data(self, inp):
        got = self.fetch(inp)
        if isinstance(got, str):
            return json.dumps({"error": got})
        title, a, view, label, status, data = got
        src = {"agent": a["title"], "agentId": a["id"], "view": view, "account": label, "via": "code"}
        if status >= 400:
            self.sources.append(dict(src, failed=True))
            err = data.get("error") if isinstance(data, dict) else str(data)[:300]
            return json.dumps({"error": "%s failed (HTTP %s): %s" % (title, status, err), "status": status})
        self.sources.append(dict(src, fetchedAt=data.get("fetchedAt") if isinstance(data, dict) else None))
        return _jsonable({"title": title, "data": data})

    def google_ads_query(self, inp):
        acct, err = self._google_account(inp.get("account"))
        if err:
            return json.dumps({"error": err})
        q, err = check_gaql(inp.get("query"))
        if err:
            return json.dumps({"error": err})
        if self._ads is None:
            return json.dumps({"error": "Direct Google Ads reads are not available on this server."})
        label = self.label_for(acct)
        try:
            raw = self._ads.gaql(acct, q)
        except Exception as e:                                               # noqa: BLE001 (Google's own words go back to the code, which can fix the query)
            detail = getattr(e, "detail", "") or ""
            self.sources.append({"agent": "Google Ads (query)", "view": q[:120], "account": label, "failed": True})
            return json.dumps({"error": "%s%s" % (str(e)[:600], (" | " + str(detail)[:1500]) if detail and str(detail) not in str(e) else "")})
        rows = [flatten(r) for r in raw[:MAX_ROWS_OUT]]
        self.sources.append({"agent": "Google Ads (query)", "view": q[:120], "account": label, "rows": len(raw)})
        out = {"account": acct, "accountName": label, "query": q, "rowCount": len(raw), "rows": rows,
               "units": "Every *Micros field also has a sibling without Micros in the account's currency. Rates (ctr, conversionRate) are fractions."}
        if len(raw) > MAX_ROWS_OUT:
            out["truncated"] = True
            out["note"] = "Only the first %d of %d rows are here. Narrow the query." % (MAX_ROWS_OUT, len(raw))
        return _jsonable(out)

    def meta_graph(self, inp):
        acct, err = self._meta_account(inp.get("account"))
        if err:
            return json.dumps({"error": err})
        path = str(inp.get("path") or "").strip().strip("/")
        obj = re.fullmatch(r"(\d{5,25})(?:/(\w+))?", path)
        if obj:
            if (obj.group(2) or "") not in META_OBJECT_EDGES:
                return json.dumps({"error": "On an object id only these edges can be read: %s." % ", ".join(e or "(the object itself)" for e in META_OBJECT_EDGES)})
            target, paged = path, bool(obj.group(2)) and obj.group(2) != ""
        else:
            if path not in META_EDGES:
                return json.dumps({"error": "path must be one of %s, or a campaign/ad set/ad id (optionally /insights)." % ", ".join(e or "''" for e in META_EDGES)})
            target, paged = acct + ("/" + path if path else ""), bool(path)
        params = inp.get("params") if isinstance(inp.get("params"), dict) else {}
        bad = sorted(set(params) - set(META_PARAMS))
        if bad:
            return json.dumps({"error": "Unsupported parameters: %s. Allowed: %s." % (", ".join(bad), ", ".join(META_PARAMS))})
        clean = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in params.items() if v not in (None, "")}
        if self._ads is None:
            return json.dumps({"error": "Direct Meta reads are not available on this server."})
        label = self.label_for(acct)
        try:
            data = self._ads.meta(target, clean, paged)
        except Exception as e:                                               # noqa: BLE001
            detail = getattr(e, "detail", "") or ""
            self.sources.append({"agent": "Meta Ads (query)", "view": target[:120], "account": label, "failed": True})
            return json.dumps({"error": "%s%s" % (str(e)[:600], (" | " + str(detail)[:800]) if detail and str(detail) not in str(e) else "")})
        rows = data if isinstance(data, list) else None
        self.sources.append({"agent": "Meta Ads (query)", "view": target[:120], "account": label, "rows": len(rows) if rows is not None else 1})
        if rows is not None:
            return _jsonable({"account": acct, "accountName": label, "path": target, "rowCount": len(rows), "rows": rows[:MAX_ROWS_OUT],
                              "units": "Meta money fields (spend, cpc, cpm) are in the account's currency units; actions is a list of {action_type, value}."})
        return _jsonable({"account": acct, "accountName": label, "path": target, "data": data})


# ── what the model is shown ───────────────────────────────────────────────────

CODE_TOOLS = [
    {"name": "agent_data",
     "description": ("Call from Python in the code sandbox: `json.loads(await agent_data({...}))`. The complete JSON of one agent read (the same agents, views and "
                     "parameters as read_agent; see agent_info), for analysis in code: {title, data} or {error}. Use it when a read is big or you need every row."),
     "input_schema": {"type": "object", "properties": {
         "agent": {"type": "string"}, "view": {"type": "string"}, "account": {"type": "string"}, "params": {"type": "object"}}, "required": ["agent"]},
     "allowed_callers": [CODE_CALLER]},
    {"name": "google_ads_query",
     "description": ("Call from Python in the code sandbox: `json.loads(await google_ads_query({'account': '1234567890', 'query': 'SELECT ... FROM ... WHERE ...'}))`. "
                     "Runs one read-only GAQL query (Google Ads API v24) on an account the platform can read and returns "
                     "{account, accountName, rowCount, rows:[flat dicts keyed like 'campaign.name', 'metrics.costMicros', 'metrics.cost', 'segments.date'], units} or {error} with Google's own message (fix the query and try again). "
                     "Field names come back in camelCase (metrics.costMicros, metrics.conversionsValue). Every *Micros field also has a sibling in currency units. "
                     "Always give a date condition for metrics (segments.date DURING LAST_30_DAYS, or BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'). Use it for anything an agent does not already show."),
     "input_schema": {"type": "object", "properties": {"account": {"type": "string", "description": "10-digit Google Ads customer id"},
                                                       "query": {"type": "string", "description": "A GAQL SELECT query"}}, "required": ["account", "query"]},
     "allowed_callers": [CODE_CALLER]},
    {"name": "meta_graph",
     "description": ("Call from Python in the code sandbox: `json.loads(await meta_graph({'account': 'act_123', 'path': 'insights', 'params': {...}}))`. "
                     "One read-only Meta Graph API GET on an ad account the platform can read. path is an edge of the account (insights, campaigns, adsets, ads, adcreatives, "
                     "customaudiences, adimages, advideos, activities, adspixels, or '' for the account itself) or an object id (campaign, ad set or ad), optionally with "
                     "/insights, /ads, /adsets, /adcreatives or /previews. params: fields, time_range ({'since':'YYYY-MM-DD','until':'YYYY-MM-DD'}), date_preset, level, "
                     "breakdowns, action_breakdowns, time_increment, filtering, limit, effective_status, action_attribution_windows. Lists are followed to the end: "
                     "returns {rowCount, rows} or {data} for one object, or {error} with Meta's message."),
     "input_schema": {"type": "object", "properties": {"account": {"type": "string"}, "path": {"type": "string"}, "params": {"type": "object"}},
                      "required": ["account"]},
     "allowed_callers": [CODE_CALLER]},
]
CODE_TOOL_NAMES = tuple(t["name"] for t in CODE_TOOLS)
DIRECT_TOOLS = [dict(t) for t in vt.TOOL_SPECS]
TOOL_NAMES = tuple(t["name"] for t in DIRECT_TOOLS) + CODE_TOOL_NAMES


def tool_specs():
    return DIRECT_TOOLS + CODE_TOOLS


def step_label(name, inp, tools=None):
    inp = inp if isinstance(inp, dict) else {}
    acct = inp.get("account") or ""
    who = (" for " + (tools.label_for(acct) if tools else str(acct))) if acct else ""
    if name == "google_ads_query":
        m = re.search(r"\sFROM\s+(\w+)", str(inp.get("query") or ""), re.I)
        return "Querying Google Ads%s%s" % ((" (" + m.group(1) + ")") if m else "", who)
    if name == "meta_graph":
        return "Querying Meta Ads (%s)%s" % (str(inp.get("path") or "account")[:40], who)
    if name == "agent_info":
        from . import vimi_catalog as cat
        a = cat.find(inp.get("agent")) if inp.get("agent") else None
        return "Reading about %s" % (a["title"] if a else (str(inp.get("agent") or "the agents")[:70]))
    if name == "agent_data":
        return "Loading all of " + vt.step_label("read_agent", inp, tools)[len("Reading "):]
    return vt.step_label(name, inp, tools)
