"""Paid Marketing Agents: Brand Spend Alert, what the team saved (no network).

Four things are kept for the whole team in the monitors' store (tracker/paid_monitor_store.py, the value "settings:brandspend") instead of in one person's browser:

  limit      how far from a usual week the brand spend can be before it is flagged (10 to 90%), used when a read does not ask for another
  marks      which campaigns count as brand campaigns whatever their name says: {campaign id: true (count it) or false (do not)}
  accounts   per account: a weekly floor and ceiling and a monthly budget the client agreed, and the brand words (so the searches other campaigns paid for can be found)

Each change says who made it and when. The store is any object with state_get and state_set, so tests run on a dict.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import paid_brandspend_insight as ins
from . import paid_brandspend_math as m

KEY = "settings:brandspend"
MAX_MARKS = 2000
MAX_MONEY = 10_000_000


class BadRequest(ValueError):
    pass


def load(store):
    """{limit, marks, accounts, changes} with every part present."""
    s = store.state_get(KEY) or {}
    return {"limit": s.get("limit"), "marks": dict(s.get("marks") or {}), "accounts": dict(s.get("accounts") or {}), "changes": list(s.get("changes") or [])}


def account_of(settings, account_id):
    """One account's saved figures: {weeklyMin, weeklyMax, monthlyBudget, brandTerms}, each None or empty when not saved."""
    a = (settings.get("accounts") or {}).get(str(account_id)) or {}
    return {"weeklyMin": a.get("weeklyMin"), "weeklyMax": a.get("weeklyMax"), "monthlyBudget": a.get("monthlyBudget"), "brandTerms": list(a.get("brandTerms") or [])}


def _note(s, who, what, now):
    s["changes"] = ([{"at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "by": who, "what": what}] + list(s.get("changes") or []))[:30]


def _money(v, name):
    if v in (None, ""):
        return None
    try:
        x = float(str(v).replace(",", "").replace("$", "").strip())
    except ValueError:
        raise BadRequest("%s must be a number." % name)
    if x != x or x < 0 or x > MAX_MONEY:
        raise BadRequest("%s must be between 0 and %s." % (name, format(MAX_MONEY, ",")))
    return round(x, 2) or None


def clean_account(data):
    """{weeklyMin, weeklyMax, monthlyBudget, brandTerms} from what the page sent, or BadRequest saying what is wrong."""
    data = data if isinstance(data, dict) else {}
    lo, hi, month = _money(data.get("weeklyMin"), "The weekly minimum"), _money(data.get("weeklyMax"), "The weekly maximum"), _money(data.get("monthlyBudget"), "The monthly budget")
    if lo and hi and lo > hi:
        raise BadRequest("The weekly minimum cannot be above the weekly maximum.")
    terms = data.get("brandTerms")
    terms = ins.terms_of(",".join(terms) if isinstance(terms, list) else terms)
    return {"weeklyMin": lo, "weeklyMax": hi, "monthlyBudget": month, "brandTerms": terms}


def save_account(store, account_id, data, who, now=None):
    account_id = str(account_id or "").strip()
    if not account_id.isdigit():
        raise BadRequest("Pick one account to save its limits: they are not saved for an MCC as a whole.")
    clean = clean_account(data)
    s = load(store)
    if any(v for v in clean.values()):
        s["accounts"][account_id] = clean
    else:
        s["accounts"].pop(account_id, None)
    _note(s, who, "saved the limits and brand words of account %s" % account_id, now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def set_mark(store, campaign_id, value, who, now=None):
    """Count (True) or do not count (False) a campaign as a brand campaign, or (None) go back to what its name says."""
    campaign_id = str(campaign_id or "").strip()
    if not campaign_id.isdigit():
        raise BadRequest("That is not a campaign id.")
    s = load(store)
    if value is None:
        s["marks"].pop(campaign_id, None)
    else:
        if len(s["marks"]) >= MAX_MARKS and campaign_id not in s["marks"]:
            raise BadRequest("Too many campaigns marked.")
        s["marks"][campaign_id] = bool(value)
    _note(s, who, "%s campaign %s" % ("went back to its name for" if value is None else "counted" if value else "stopped counting", campaign_id), now)
    store.state_set(KEY, s)
    return s["marks"]


def set_limit(store, value, who, now=None):
    from . import paid_brandspend as b
    s = load(store)
    s["limit"] = None if value in (None, "") else b.parse_limit(value)
    _note(s, who, "set the limit to %s" % ("the default" if s["limit"] is None else "%d%%" % round(s["limit"] * 100)), now)
    store.state_set(KEY, s)
    return s["limit"]


def view(store):
    """What the page needs: the settings and the default limit."""
    s = load(store)
    return {"limit": s["limit"], "defaultLimit": m.DEFAULT_LIMIT, "marks": s["marks"], "accounts": s["accounts"], "changes": s["changes"][:10]}
