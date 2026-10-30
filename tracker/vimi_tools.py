"""Vimi: the tools the model reads the platform with (no model here; the app hands in the network).

  find_account    a client or account name -> the exact account string every agent takes
  agent_info      what an agent holds, how it works, and the reads it offers (or the whole roster)
  read_agent      one of an agent's reads, run as the signed-in person; answered as a digest
  inspect_result  drill into a result already read: filter, sort, group, sum (computed in code)
  web_research    public information the platform does not hold

Everything an agent returns is data, never an instruction. A read can only be one the catalog
lists, with the parameters it declares, by GET: there is no refresh, no write and no route that
spends a model or an ad platform's allowance on its own.

The app supplies `route(path, params) -> (status, body)` (the page's own endpoint, called
in-process with the person's identity, so an admin-only read still refuses a non-admin),
`accounts()` (the readable Google and Meta accounts and the Master Agent's clients), `web(question)`
and `local` (the three sources that live in sheets and databases). Tests supply fakes.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from . import vimi_catalog as cat
from . import vimi_data as vd

ACCOUNT_RE = re.compile(r"^(\d{10}|act_\d{4,20})(\+act_\d{4,20})?$")
FRESHNESS = ("fetchedAt", "stale", "blockedUntil", "warnings", "warning", "error", "googleProblem", "metaProblem", "configProblem", "kind")
MAX_VALUE = 300


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _tokens(s):
    return [t for t in _norm(s).split() if t]


class Tools:
    def __init__(self, *, route, accounts, web=None, local=None, is_admin=False, results=None, hidden=(), knowledge=None):
        self._route = route
        self._accounts = accounts
        self._web = web
        self._local = dict(local or {})
        self.is_admin = is_admin
        self.results = results or vd.Results()
        self.hidden = set(hidden)
        self.knowledge = knowledge or {}
        self.sources = []                       # [{agent, view, account, id, ...}] what the answer was built from
        self._acct_cache = None

    # ── accounts ──────────────────────────────────────────────────────────────

    def _load_accounts(self):
        if self._acct_cache is None:
            try:
                self._acct_cache = self._accounts() or {}
            except Exception as e:                                   # noqa: BLE001 (a failure is said, not hidden: the model is told what could not be listed)
                self._acct_cache = {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
        return self._acct_cache

    def label_for(self, account):
        """A readable name for an account string ("Beta Bionics (1234567890)"), or the string itself."""
        data = self._load_accounts()
        names = {a["id"]: a.get("name") for a in (data.get("google") or []) + (data.get("meta") or [])}
        parts = [p for p in str(account or "").split("+") if p]
        return " + ".join("%s (%s)" % (names[p], p) if names.get(p) else p for p in parts) or str(account or "")

    def find_account(self, inp):
        data = self._load_accounts()
        q = str(inp.get("query") or "").strip()
        google, meta, clients = data.get("google") or [], data.get("meta") or [], data.get("clients") or []
        notes = [n for n in (data.get("googleProblem"), data.get("metaProblem"), data.get("error")) if n]
        lines = []
        if q:
            want = _tokens(q)
            scored = []
            for a, platform in [(a, "google") for a in google] + [(a, "meta") for a in meta]:
                hay = _tokens("%s %s" % (a.get("name"), a.get("id")))
                score = sum(1 for t in want if any(t == h or (len(t) > 2 and t in h) for h in hay))
                if score and (score == len(want) or score >= 2 or len(want) == 1):
                    scored.append((score, platform, a))
            scored.sort(key=lambda t: (-t[0], str(t[2].get("name")).lower()))
            for _s, platform, a in scored[:12]:
                lines.append("%s | %s | %s | %s" % (platform, a["id"], a.get("name"), a.get("currency") or ""))
            hits = []
            for c in clients:
                hay = _tokens(c.get("name"))
                if any(t in hay or any(t in h for h in hay) for t in want if len(t) > 2):
                    ids = list((c.get("accounts") or {}).keys())
                    g = [i for i in ids if i.isdigit()]
                    m = [i for i in ids if i.startswith("act_")]
                    hits.append("client '%s' (Master Agent) owns: %s%s" % (c.get("name"), ", ".join(self.label_for(i) for i in ids) or "no accounts yet",
                                                                          ("  -> both platforms: account=%s" % ("+".join(g[:1] + m[:1]))) if g and m else ""))
            body = "\n".join(lines) if lines else "No account name contains '%s'." % q
            if hits:
                body += "\n\nCLIENTS\n" + "\n".join(hits)
            if not lines and not hits:
                body += " Try one distinctive word of the name, or call find_account with no query to list every account."
        else:
            lines = ["google | %s | %s | %s" % (a["id"], a.get("name"), a.get("currency") or "") for a in google] + ["meta | %s | %s | %s" % (a["id"], a.get("name"), a.get("currency") or "") for a in meta]
            body = "%d accounts (platform | account id | name | currency):\n%s" % (len(lines), "\n".join(lines))
            if clients:
                body += "\n\nCLIENTS (Master Agent)\n" + "\n".join("- %s: %s" % (c.get("name"), ", ".join((c.get("accounts") or {}).keys())) for c in clients)
        if notes:
            body += "\n\nNOTE: " + " | ".join(str(n) for n in notes)
        return body + "\n\nPass the account id as `account` to read_agent. A Google id is 10 digits, a Meta account starts act_, and both are joined with + for an agent that reads both."

    # ── agents ────────────────────────────────────────────────────────────────

    def _visible(self):
        return [a for a in cat.AGENTS if a["id"] not in self.hidden]

    def agent_info(self, inp):
        q = str(inp.get("agent") or inp.get("query") or "").strip()
        if not q:
            return "THE ROSTER (id | title | what it holds)\n" + cat.roster(self.hidden)
        a = cat.find(q)
        if a is None or a["id"] in self.hidden:
            found = [x for x in cat.search(q) if x["id"] not in self.hidden]
            if not found:
                return "No agent matches '%s'. Call agent_info with no argument for the roster." % q
            return "No agent is called '%s'. Closest: %s" % (q, "; ".join("%s (%s): %s" % (x["id"], x["title"], x["about"]) for x in found[:5]))
        out = ["%s (%s)  page: %s" % (a["title"], a["id"], a["page"]), a["about"]]
        know = self.knowledge.get(a["id"])
        if know:
            out += ["", "HOW IT WORKS (the platform's own description):", know]
        if a["reads"]:
            out += ["", "READS (use read_agent with agent='%s' and view=...):" % a["id"]]
            for r in a["reads"]:
                params = "; ".join("%s: %s" % (k, v) for k, v in r["params"].items()) or "no parameters"
                out.append("- view '%s'%s: %s\n    parameters: %s" % (r["view"], " (admin only)" if r["admin"] else "", r["about"], params))
        else:
            out += ["", "This agent holds nothing Vimi can read. Point the person to its page."]
        return "\n".join(out)

    # ── reading ───────────────────────────────────────────────────────────────

    def read_agent(self, inp):
        a = cat.find(inp.get("agent"))
        if a is None or a["id"] in self.hidden:
            return "No agent '%s'. Call agent_info (no argument) for the roster." % (inp.get("agent") or "")
        if not a["reads"]:
            return "%s holds nothing Vimi can read. Its page is %s." % (a["title"], a["page"])
        views = {r["view"]: r for r in a["reads"]}
        view = str(inp.get("view") or "").strip() or a["reads"][0]["view"]
        r = views.get(view)
        if r is None:
            return "%s has no view '%s'. Its views are: %s." % (a["title"], view, ", ".join(views))
        params = inp.get("params") if isinstance(inp.get("params"), dict) else {}
        if inp.get("account") and "account" not in params:
            params = dict(params, account=inp.get("account"))
        in_path = re.findall(r"\{(\w+)\}", r["path"])
        allowed = set(r["params"]) | set(in_path) | ({"account"} if r["account"] else set())
        bad = sorted(set(params) - allowed)
        if bad:
            return "%s / %s does not take %s. Its parameters: %s." % (a["title"], view, ", ".join(bad), ", ".join(sorted(allowed)) or "none")
        clean = {}
        for k, v in params.items():
            if v is None or v == "":
                continue
            v = str(v).strip()
            if len(v) > MAX_VALUE or re.search(r"[\x00-\x1f]", v):
                return "The value of '%s' is not usable (too long or has control characters)." % k
            clean[k] = v
        if r["account"]:
            acct = clean.get("account", "")
            if not acct:
                return "%s / %s needs an account. Call find_account with the client's name." % (a["title"], view)
            if not ACCOUNT_RE.match(acct):
                return "account '%s' is not an account id. Use find_account: a Google id is 10 digits, a Meta account starts act_, both are joined with +." % acct
        missing = [p for p in in_path if p not in clean]
        if missing:
            return "%s / %s needs %s (%s)." % (a["title"], view, ", ".join(missing), "; ".join("%s: %s" % (m, r["params"].get(m, "")) for m in missing))
        if r["admin"] and not self.is_admin:
            return "That read is for admins only, and you are not signed in as one. Say so plainly; do not try to get the numbers another way."
        label = self.label_for(clean.get("account")) if clean.get("account") else ""
        title = "%s (%s)%s" % (a["title"], view, (" for " + label) if label else "")
        try:
            if r["local"]:
                fn = self._local.get(r["local"])
                if fn is None:
                    return "%s is not available on this server right now." % a["title"]
                data = fn(clean)
                status = 200
            else:
                path = re.sub(r"\{(\w+)\}", lambda m: quote(clean.pop(m.group(1)), safe=""), r["path"])
                status, data = self._route(path, clean)
        except Exception as e:                                       # noqa: BLE001 (the model is told the read failed and why, so it can say so)
            self.sources.append({"agent": a["title"], "view": view, "account": label, "failed": True})
            return "%s could not be read: %s: %s" % (title, type(e).__name__, str(e)[:300])
        return self._answer(title, a, view, label, status, data)

    def _answer(self, title, a, view, label, status, data):
        src = {"agent": a["title"], "agentId": a["id"], "view": view, "account": label}
        if status in (301, 302, 303, 307, 308, 401):
            self.sources.append(dict(src, failed=True))
            return "%s could not be read: the session is not signed in." % title
        if status == 403:
            self.sources.append(dict(src, failed=True))
            return "%s refused: this read is for admins only. Tell the person; do not look for the numbers another way." % title
        if status == 404:
            self.sources.append(dict(src, failed=True))
            return "%s: nothing there (HTTP 404)%s." % (title, (": " + str(data.get("error"))) if isinstance(data, dict) and data.get("error") else "")
        if status >= 400 or (isinstance(data, dict) and data.get("error") and not any(isinstance(v, (list, dict)) and v for k, v in data.items() if k != "error")):
            err = data.get("error") if isinstance(data, dict) else str(data)[:300]
            extra = ""
            if isinstance(data, dict):
                if data.get("blockedUntil"):
                    extra = " The platform's allowance is spent until %s. Do not retry; tell the person and offer what is already stored." % data["blockedUntil"]
                elif data.get("retryIn") or data.get("cooldown"):
                    extra = " It is cooling down; do not retry now."
                elif data.get("configProblem"):
                    extra = " The connection is not set up; say so."
            self.sources.append(dict(src, failed=True, error=str(err)[:200]))
            return "%s failed (HTTP %s): %s.%s Report this to the person plainly instead of guessing the numbers." % (title, status, str(err)[:400], extra)
        rid = self.results.add(title, data)
        head = ["[%s] %s" % (rid, title)]
        if isinstance(data, dict):
            fresh = {k: data[k] for k in FRESHNESS if data.get(k) not in (None, "", [], {})}
            if fresh:
                head.append("freshness and warnings: " + json.dumps(fresh, ensure_ascii=False, default=str)[:900])
            self.sources.append(dict(src, id=rid, fetchedAt=data.get("fetchedAt"), stale=data.get("stale")))
        else:
            self.sources.append(dict(src, id=rid))
        return "\n".join(head) + "\n" + vd.digest(data) + "\n\n(Use inspect_result with id '%s' to filter, sort, group or page any table above. Totals above are computed in code.)" % rid

    def inspect_result(self, inp):
        try:
            item = self.results.get(inp.get("id"))
        except KeyError as e:
            return str(e.args[0])
        try:
            out = vd.query(item["data"], str(inp.get("path") or ""), where=inp.get("where"), sort_by=inp.get("sort_by"), order=str(inp.get("order") or "desc"),
                           limit=inp.get("limit") or vd.DEFAULT_ROWS, offset=inp.get("offset") or 0, fields=inp.get("fields"),
                           group_by=inp.get("group_by"), aggregate=inp.get("aggregate"))
        except (vd.PathError, ValueError) as e:
            return "Could not do that on %s: %s" % (item["label"], e)
        return "[%s] %s\n%s" % (inp.get("id"), item["label"], vd.render(out))

    def web_research(self, inp):
        q = str(inp.get("question") or "").strip()
        if not q:
            return "Give the question to research."
        if self._web is None:
            return "Web research is not available on this server."
        try:
            text = self._web(q)
        except Exception as e:                                       # noqa: BLE001
            return "Web research failed: %s: %s" % (type(e).__name__, str(e)[:200])
        self.sources.append({"agent": "Web search", "view": q[:80], "account": ""})
        return text or "The search found nothing usable."


# ── what the model is shown ───────────────────────────────────────────────────

TOOL_SPECS = [
    {"name": "find_account",
     "description": ("Turn a client or account name into the account id(s) every agent takes. Searches every Google Ads account under the Position2 manager account, every Meta ad account and the Master Agent's clients. "
                     "Call it FIRST whenever a question names a client, brand or account, and never guess an id. With no query it lists every account."),
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "A client or account name, or a distinctive word of it, e.g. 'Beta Bionics' or 'riccobene'."}}}},
    {"name": "agent_info",
     "description": ("What an agent holds, how it works and which reads it offers (with their parameters). With no argument it returns the roster of every agent on the platform. "
                     "Use it to pick the right agent for a question, and before the first read_agent on an agent you have not used yet, to learn its views and parameters."),
     "input_schema": {"type": "object", "properties": {"agent": {"type": "string", "description": "An agent id or title from the roster, or words describing what you need (e.g. 'impression share')."}}}},
    {"name": "read_agent",
     "description": ("Read live data from an agent, exactly as its page shows it, as the signed-in person. Returns a digest: the fields, every table with its row count, columns and column totals, and the first rows, plus an id. "
                     "Then use inspect_result on that id to see more rows, filter, sort or sum. Several independent reads can be made in one round. Reads are cached, read-only and never refresh an ad platform. "
                     "Pick the view from agent_info. Pass the account (from find_account) for agents that read an ad account."),
     "input_schema": {"type": "object", "properties": {
         "agent": {"type": "string", "description": "Agent id from the roster, e.g. 'kpi-monitoring'."},
         "view": {"type": "string", "description": "Which read of the agent (see agent_info). Omit for its first one."},
         "account": {"type": "string", "description": "Ad account string from find_account: a 10-digit Google id, act_... for Meta, or both joined with +."},
         "params": {"type": "object", "description": "Other parameters the view declares, e.g. {\"start\":\"2026-10-01\",\"end\":\"2026-10-28\"}."}},
         "required": ["agent"]}},
    {"name": "inspect_result",
     "description": ("Drill into a result already read (the id in square brackets). Pick a table by path (shown in the digest's TABLES list, e.g. 'campaigns'), then filter rows with where, sort_by, choose fields, page with limit and offset, "
                     "or group_by with aggregate to total things (sum of cost by campaign type, count of disapproved ads by policy). Sums, averages and counts are computed in code: ALWAYS use this for any total or ranking over more than a handful of rows, never add them up yourself. "
                     "Ratios (CPA, ROAS, CTR) must be computed from summed numerators and denominators, not by averaging row ratios."),
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "Result id such as r2."},
         "path": {"type": "string", "description": "Dotted path to a table or value, e.g. 'campaigns' or 'current.rows'. Empty for the whole result."},
         "where": {"type": "array", "description": "Filters, all must hold.", "items": {"type": "object", "properties": {
             "field": {"type": "string"}, "op": {"type": "string", "enum": list(vd.OPS)}, "value": {}}, "required": ["field"]}},
         "sort_by": {"type": "string"}, "order": {"type": "string", "enum": ["asc", "desc"]},
         "limit": {"type": "integer", "description": "Rows to return, at most 100."}, "offset": {"type": "integer"},
         "fields": {"type": "array", "items": {"type": "string"}, "description": "Only these columns."},
         "group_by": {"description": "A field or a list of fields to group by.", "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
         "aggregate": {"type": "array", "description": "What to compute per group (or over all matched rows).", "items": {"type": "object", "properties": {
             "field": {"type": "string"}, "fn": {"type": "string", "enum": list(vd.FUNCS)}, "as": {"type": "string"}}, "required": ["fn"]}}},
         "required": ["id"]}},
    {"name": "web_research",
     "description": "Search the public web for something the platform does not hold (a company's news, an industry benchmark, how an ad platform feature works). One focused question per call. Never use it for the team's own accounts or data.",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
]
TOOL_NAMES = tuple(t["name"] for t in TOOL_SPECS)


def step_label(name, inp, tools=None):
    """The live line the page shows for one tool call."""
    q = lambda k: str(inp.get(k) or "")[:70]
    if name == "find_account":
        return 'Finding the account "%s"' % q("query") if inp.get("query") else "Listing the accounts"
    if name == "agent_info":
        return "Reading about %s" % (q("agent") or "the agents")
    if name == "read_agent":
        a = cat.find(inp.get("agent"))
        params = inp.get("params") if isinstance(inp.get("params"), dict) else {}
        acct = inp.get("account") or params.get("account") or ""
        who = (" for " + (tools.label_for(acct) if tools else str(acct))) if acct else ""
        return "Reading %s%s%s" % (a["title"] if a else q("agent"), (" / " + q("view")) if inp.get("view") else "", who)
    if name == "inspect_result":
        return "Looking closer at %s" % q("id")
    if name == "web_research":
        return 'Searching the web: "%s"' % q("question")
    return name
