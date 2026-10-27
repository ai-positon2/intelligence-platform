"""Paid Marketing Agents: Competitor Analysis, finding a competitor from its name (the network is passed in, so all of it is tested without one).

The team gives only a company name, as in Social Media Intelligence. `search` answers with the companies that have a similar name (the same Apollo company search, tracker/sci_company_search.py, so
no new vendor), each with its website, which is where Google's Ads Transparency Center is searched; the team picks the one it means, as many times as it likes. A competitor's Meta ads are found by its
Facebook page, which a company search does not give: Apollo's own record of the company has it when it has it, and otherwise `find_facebook` asks Claude with web search for the company's official page,
and a page is only kept when the answer is sure of it. A page that cannot be found is said so and left for the team to write in: it is never guessed.
"""
from __future__ import annotations

import json
import logging
import os
import re

from . import paid_compete_settings as cs
from . import paid_domains as dom

logger = logging.getLogger(__name__)

MAX_RESULTS = 8
SURE = ("high", "medium")
_MODEL = "claude-sonnet-5"
_WEB_SEARCH_VERSIONS = ("web_search_20260318", "web_search_20260209", "web_search_20250305")
_SYSTEM = (
    "You find the official Facebook page of one company, to read the ads it runs. Use web search and check that the page belongs to this company: its About section or its posts link to the company's real "
    "website, and it is not a fan page, a regional office, a product page of another company or a different company with a similar name. When a company has a main page and regional ones, give the main one "
    "(the page its own website links to). If you cannot find and check a page, say confidence 'none' rather than naming a likely-looking one: a wrong page means another company's ads are read.\n\n"
    'After searching, answer with ONLY a JSON object, no other text: {"profile_url": str|null, "page_name": str|null, "confidence": "high"|"medium"|"low"|"none", "reasoning": str}. '
    '"profile_url" is the page\'s own address (https://www.facebook.com/<name or id>) and must be null when confidence is "none".'
)


# ── Searching ───────────────────────────────────────────────────────────────

def domain_of(v):
    """The website as the bare domain Google is searched by ("https://www.tandemdiabetes.com/pumps" -> "tandemdiabetes.com"), or "" for none."""
    return dom.clean_domain(v) or ""


def looks_like_site(q):
    """Whether what was typed is a website address (it has a dot and no spaces) rather than a name."""
    q = str(q or "").strip()
    return bool(q) and " " not in q and "." in q.strip(".") and bool(domain_of(q))


def _facebook_of(org):
    try:
        return cs.clean_facebook(org.get("facebook_url"))
    except cs.BadRequest:
        return ""


def _candidate(org):
    """One company of the search as the picker shows it, or None for one a competitor cannot be made of (no name, or no website and no Facebook page: nowhere to look)."""
    name = " ".join(str(org.get("name") or "").split())
    domain = domain_of(org.get("primary_domain") or org.get("domain") or org.get("website_url"))
    facebook = _facebook_of(org)
    if not name or not (domain or facebook):
        return None
    loc = ", ".join(p for p in (org.get("city"), org.get("state"), org.get("country")) if isinstance(p, str) and p.strip())
    logo = str(org.get("logo_url") or "")
    return {"name": name[:cs.MAX_NAME], "domain": domain, "website": ("https://" + domain) if domain else "", "facebook": facebook, "fbSource": "apollo" if facebook else "", "logo": logo if logo.startswith("https://") else "",
            "industry": " ".join(str(org.get("industry") or "").split())[:80], "location": loc, "description": " ".join(str(org.get("short_description") or "").split())[:200]}


def _error_of(e):
    """A failed company search as {kind, message, retryable}, in the words Social Media Intelligence uses for the same failures."""
    from . import sci_company_search as scs
    import requests
    if isinstance(e, requests.HTTPError):
        status = getattr(getattr(e, "response", None), "status_code", None)
        err = scs._err(scs.ERR_HTTP, "HTTP %s" % status, status=status)
    elif isinstance(e, requests.Timeout):
        err = scs._err(scs.ERR_TIMEOUT, str(e))
    elif isinstance(e, requests.RequestException):
        err = scs._err(scs.ERR_NETWORK, str(e))
    else:
        err = scs._err(scs.ERR_UNPARSABLE, "%s: %s" % (type(e).__name__, e))
    return {"kind": err["kind"], "message": scs.describe_error(err), "retryable": scs.is_retryable(err)}


def _apollo(q, api_key):
    from . import apollo_client
    return apollo_client.search_companies({"name": q, "max_companies": MAX_RESULTS}, api_key, per_page=MAX_RESULTS, strict=True)


def search(q, *, saved=(), search_fn=None, api_key=None):
    """{companies, error}: the companies whose name is like `q`, each as the picker shows it with `added` saying it is already one of this account's competitors (`saved`, the account's list). A website
    typed instead of a name is offered as a company of its own first, so a competitor Apollo does not know can still be added; the name search is made as well. `error` is {kind, message, retryable}
    when the search could not be made (the websites typed still work)."""
    q = " ".join(str(q or "").split())
    out = {"companies": [], "error": None}
    if len(q) < 2:
        return out
    keys = {cs.identity(c) for c in saved} | {c["name"].lower() for c in saved}
    found = []
    key = api_key if api_key is not None else os.environ.get("APOLLO_API_KEY", "")
    if not key and search_fn is None:
        out["error"] = {"kind": "not_configured", "message": "Company search is not configured on this deployment: APOLLO_API_KEY is missing. Type a website to add a competitor.", "retryable": False}
    else:
        try:
            orgs = search_fn(q) if search_fn else _apollo(q, key)
        except Exception as e:                                                       # noqa: BLE001 (a search that failed is said, never shown as "no such company")
            logger.warning("compete search failed for %r: %s", q, e)
            out["error"] = _error_of(e)
            orgs = []
        found += [c for c in (_candidate(o) for o in orgs or [] if isinstance(o, dict)) if c]
    if looks_like_site(q) and domain_of(q) not in {c["domain"] for c in found}:                # the company search's own record of the site is richer than the bare address
        d = domain_of(q)
        found.insert(0, {"name": d.split(".")[0].replace("-", " ").title(), "domain": d, "website": "https://" + d, "facebook": "", "fbSource": "", "logo": "", "industry": "", "location": "", "description": "The website you typed."})
    seen = set()
    for c in found:
        k = c["domain"] or c["facebook"].lower()
        if k in seen:
            continue
        seen.add(k)
        c["added"] = cs.identity(c) in keys or c["name"].lower() in keys
        out["companies"].append(c)
    ql = q.lower()
    out["companies"].sort(key=lambda c: (0 if c["name"].lower() == ql else 1 if c["name"].lower().startswith(ql) else 2))
    out["companies"] = out["companies"][:MAX_RESULTS]
    return out


# ── The Facebook page ───────────────────────────────────────────────────────

def _client():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=90.0, max_retries=1)


def _json_in(text):
    from . import sci_identify
    raw = sci_identify._extract_json_object(text or "")
    try:
        got = json.loads(raw) if raw else None
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def ask_claude(name, domain):
    """The raw answer of one Claude web search for a company's Facebook page: {profile_url, page_name, confidence, reasoning}, or {} when it could not be asked or answered. Never raises."""
    from . import sci_identify
    client = _client()
    if client is None:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    text = "Company: %s" % name + ("\nWebsite: %s" % domain if domain else "")
    last = None
    for version in _WEB_SEARCH_VERSIONS:
        try:
            with client.messages.stream(model=os.environ.get("ANTHROPIC_MODEL", _MODEL), max_tokens=2000, system=_SYSTEM, tools=[{"type": version, "name": "web_search", "max_uses": 4}],
                                        messages=[{"role": "user", "content": text}]) as stream:
                resp = stream.get_final_message()
        except Exception as e:                                                       # noqa: BLE001
            last = e
            if sci_identify._tool_version_unsupported(e):
                continue
            break
        got = _json_in("".join(b.text for b in resp.content if getattr(b, "type", "") == "text"))        # every text block: the answer arrives in pieces around the citations
        return got or {"error": "The search answered with something that could not be read."}
    return {"error": "The search could not be made (%s)." % (str(last)[:120] if last else "no usable web search tool")}


def find_facebook(name, domain, *, ask_fn=None):
    """{facebook, confidence, reasoning, error}: the company's Facebook page as the Ad Library is asked for it, kept only when the answer is sure of it (high or medium) and is a page address; else "" and
    why. `ask_fn(name, domain)` gives the raw answer (ask_claude, or a stand-in)."""
    got = (ask_fn or ask_claude)(name, domain) or {}
    out = {"facebook": "", "confidence": str(got.get("confidence") or "none").lower(), "reasoning": str(got.get("reasoning") or "")[:300], "pageName": str(got.get("page_name") or "")[:120], "error": str(got.get("error") or "")}
    if out["error"]:
        return dict(out, confidence="none")
    if out["confidence"] not in SURE:
        out["reasoning"] = out["reasoning"] or "No page could be checked against the company's website."
        return out
    try:
        out["facebook"] = cs.clean_facebook(got.get("profile_url"))
    except cs.BadRequest:
        out["facebook"] = ""
    if not out["facebook"]:
        out["confidence"] = "none"
        out["reasoning"] = "The page given was not a Facebook page address. " + out["reasoning"]
    return out
