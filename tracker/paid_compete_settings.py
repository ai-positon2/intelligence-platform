"""Paid Marketing Agents: Competitor Analysis, what the team saved (no network).

Who an account's competitors are cannot be read from an ad account, so the team writes them down, per account, in the monitors' store (tracker/paid_monitor_store.py, the value
"settings:compete"). A competitor is where its ads are found:

  name              what the report calls it
  domain            its website: Google's Ads Transparency Center is searched by it (every ad that sends people to it)
  facebook          its Facebook page (an address, a page id or the name in the address): Meta's Ad Library is searched by it
  googleAdvertiser  optional, the advertiser id (AR...) from its Transparency Center page, for a competitor whose ads are not found by its domain
  region            the country whose ads are read, for the whole account (two letters, US unless set)

A competitor with none of the three is refused: nothing could be looked up. The Master Agent can hold the same list for a client (tracker/paid_master.py); an account's own saved list wins.
Each change says who made it and when. The store is any object with state_get and state_set, so tests run on a dict.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import paid_domains as dom

KEY = "settings:compete"
MAX_COMPETITORS = 8
MAX_NAME = 80
REGION_DEFAULT = "US"
_AR = re.compile(r"^AR\d{10,25}$")
_SLUG = re.compile(r"^[A-Za-z0-9.\-_]{3,80}$")
_FB_HOSTS = ("facebook.com", "fb.com", "fb.me")


class BadRequest(ValueError):
    pass


def load(store):
    s = store.state_get(KEY) or {}
    return {"accounts": dict(s.get("accounts") or {}), "changes": list(s.get("changes") or [])}


def load_for_read(store):
    """What the agent reads: its own saved lists with the Master Agent's filled in for an account that has none saved (tracker/paid_master.py). Never saved back. A master that cannot be read
    leaves the agent's own settings as they are."""
    s = load(store)
    try:
        from . import paid_master
        return paid_master.overlay_compete(s, paid_master.load(store))
    except Exception:                                                                # noqa: BLE001 (a master that cannot be read must not stop an agent)
        return s


def account_of(settings, account_id):
    a = (settings.get("accounts") or {}).get(str(account_id)) or {}
    return {"competitors": [dict(c) for c in a.get("competitors") or []], "region": a.get("region") or REGION_DEFAULT, "name": a.get("name") or ""}


def _note(s, who, what, now):
    s["changes"] = ([{"at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "by": who, "what": what}] + list(s.get("changes") or []))[:30]


# ── Cleaning what the page sent ─────────────────────────────────────────────

def clean_facebook(v):
    """A Facebook page as the address its ads are looked up by (https://www.facebook.com/<name or id>), or "" for nothing; BadRequest for something that is not a page."""
    s = str(v or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{5,20}", s):
        return "https://www.facebook.com/" + s
    if "/" not in s and "." not in s.rstrip("."):
        s = "facebook.com/" + s
    if "://" not in s:
        s = "https://" + s.lstrip("/")
    m = re.match(r"^https?://([a-z0-9.-]+)/(.*)$", s, re.I)
    host = (m.group(1).lower() if m else "")
    if not m or not any(host == h or host.endswith("." + h) for h in _FB_HOSTS):
        raise BadRequest("%s is not a Facebook page address." % str(v)[:60])
    rest = m.group(2)
    pid = re.search(r"view_all_page_id=(\d{5,20})", rest)
    if pid:
        return "https://www.facebook.com/" + pid.group(1)
    slug = re.split(r"[/?#]", rest.strip("/"))[0]
    if slug in ("profile.php",):
        pid = re.search(r"[?&]id=(\d{5,20})", rest)
        slug = pid.group(1) if pid else ""
    if not slug or slug.lower() in ("ads", "pages", "watch", "groups", "marketplace", "login") or not _SLUG.match(slug):
        raise BadRequest("%s is not a Facebook page address (use the page's own address)." % str(v)[:60])
    return "https://www.facebook.com/" + slug


def clean_competitor(c):
    c = c if isinstance(c, dict) else {}
    name = " ".join(str(c.get("name") or "").split())
    if not name:
        raise BadRequest("Give each competitor a name.")
    if len(name) > MAX_NAME:
        raise BadRequest("A competitor's name is at most %d characters." % MAX_NAME)
    raw_domain = str(c.get("domain") or "").strip()
    domain = dom.clean_domain(raw_domain) if raw_domain else ""
    if raw_domain and not domain:
        raise BadRequest("%s is not a website address." % raw_domain[:60])
    fb = clean_facebook(c.get("facebook"))
    ar = str(c.get("googleAdvertiser") or "").strip().upper()
    if ar and not _AR.match(ar):
        raise BadRequest("%s is not an advertiser id (it starts with AR, from the competitor's Ads Transparency Center address)." % ar[:40])
    if not (domain or fb or ar):
        raise BadRequest("%s needs a website, a Facebook page or an advertiser id, or there is nowhere to look." % name)
    return {"name": name, "domain": domain, "facebook": fb, "googleAdvertiser": ar}


def identity(c):
    """What makes two entries the same competitor: its website, else its Facebook page, else its advertiser id."""
    return (c.get("domain") or c.get("facebook") or c.get("googleAdvertiser") or c.get("name") or "").lower()


def parse_lines(v):
    """Competitors written one per line as `Name | website | Facebook page | advertiser id` (the last three optional) into dicts; a list of dicts is taken as it is."""
    if isinstance(v, list):
        return [x if isinstance(x, dict) else {"name": str(x)} for x in v]
    out = []
    for line in str(v or "").splitlines():
        line = line.strip()
        if not line:
            continue
        p = [x.strip() for x in line.split("|")]
        out.append({"name": p[0], "domain": p[1] if len(p) > 1 else "", "facebook": p[2] if len(p) > 2 else "", "googleAdvertiser": p[3] if len(p) > 3 else ""})
    return out


def clean_competitors(v):
    out, seen = [], set()
    for raw in parse_lines(v):
        c = clean_competitor(raw)
        key = identity(c)
        if key in seen:
            raise BadRequest("%s is listed twice." % c["name"])
        seen.add(key)
        out.append(c)
    if len(out) > MAX_COMPETITORS:
        raise BadRequest("At most %d competitors per account." % MAX_COMPETITORS)
    return out


def clean_region(v):
    r = str(v or REGION_DEFAULT).strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", r):
        raise BadRequest("The country is two letters, like US.")
    return r


def lines_of(competitors):
    """The inverse of parse_lines, for the box on the page."""
    return "\n".join(" | ".join([c["name"], c.get("domain") or "", c.get("facebook") or "", c.get("googleAdvertiser") or ""]).rstrip(" |") for c in competitors or [])


# ── Saving ──────────────────────────────────────────────────────────────────

def save_account(store, account_id, name, data, who, now=None):
    account_id = str(account_id or "").strip()
    if not re.fullmatch(r"\d{10}|act_\d{4,20}", account_id):
        raise BadRequest("Pick one account to save its competitors: they are not saved for an MCC as a whole.")
    data = data if isinstance(data, dict) else {}
    clean = {"competitors": clean_competitors(data.get("competitors")), "region": clean_region(data.get("region")), "name": str(name or "")[:120]}
    s = load(store)
    if clean["competitors"]:
        s["accounts"][account_id] = clean
    else:
        s["accounts"].pop(account_id, None)
    _note(s, who, "saved the competitors of account %s (%d)" % (account_id, len(clean["competitors"])), now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def view(store):
    s = load(store)
    return {"accounts": s["accounts"], "changes": s["changes"][:10]}
