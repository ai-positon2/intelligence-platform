"""Paid Marketing Agents: Tracking Data Health, what the team saved (no network).

Kept per account in the monitors' store (tracker/paid_monitor_store.py, the value "settings:tracking"):

  owner     who looks after the account's tracking (a name or an email), shown on the page and the findings
  windows   date ranges when a drop is expected (a site migration, a paused promotion), each with why: the days are left out of the baseline and a finding on them is lowered to low,
            at most 20, none longer than 92 days
  muted     a conversion action, a list or a pixel that is never judged, by "action:<id>", "list:<id>" or "pixel:<id>", each with why; or "rule:auto_tagging" for an account that has a
            reason to run without it. At most 100
  acks      "known, being fixed": a finding the team acknowledged, by its key, with who, when and a note. It stays open but is lowered to low (so neither alerted nor reminded) for
            ACK_DAYS (tracker/paid_tracking_math.py), then counts again

Each change says who made it and when. The store is any object with state_get and state_set, so tests run on a dict.
"""
from __future__ import annotations

import datetime as dt
import re

KEY = "settings:tracking"
MAX_WINDOWS = 20
MAX_WINDOW_DAYS = 92
MAX_MUTED = 100
MAX_ACKS = 200
MAX_NOTE = 200
_MUTE_KEY = re.compile(r"^(action|list|pixel):[A-Za-z0-9_\-]{1,40}$|^rule:auto_tagging$")
_FINDING_KEY = re.compile(r"^[a-z_]{3,20}(:[A-Za-z0-9_\-]{1,40})?$")


class BadRequest(ValueError):
    pass


def empty():
    return {"accounts": {}, "changes": []}


def load(store):
    s = store.state_get(KEY) or {}
    return {"accounts": dict(s.get("accounts") or {}), "changes": list(s.get("changes") or [])}


def account_of(settings, account_id):
    """One account's saved settings in the shape the rules read: {owner, windows: [{from, to, why}], muted: {key: why}, names: {key: name}, acks: {key: {by, at, note}}}."""
    a = (settings.get("accounts") or {}).get(str(account_id)) or {}
    muted = a.get("muted") or {}
    return {"owner": a.get("owner") or "", "windows": list(a.get("windows") or []), "muted": {k: (v or {}).get("why") or "" for k, v in muted.items()},
            "names": {k: (v or {}).get("name") or "" for k, v in muted.items()}, "acks": dict(a.get("acks") or {}), "name": a.get("name") or ""}


def _now(now):
    return now or dt.datetime.now(dt.timezone.utc)


def _note(s, who, what, now):
    s["changes"] = ([{"at": _now(now).isoformat(timespec="seconds"), "by": who, "what": what}] + list(s.get("changes") or []))[:30]


def _account_id(account_id):
    account_id = str(account_id or "").strip()
    if not (account_id.isdigit() or re.fullmatch(r"act_\d{4,20}", account_id)):
        raise BadRequest("Pick one account: these are saved per account, not for an MCC as a whole.")
    return account_id


def _why(why, what):
    why = str(why or "").strip()[:MAX_NOTE]
    if not why:
        raise BadRequest("Say why %s, so the next person knows." % what)
    return why


def _day(v, what):
    try:
        return dt.date.fromisoformat(str(v or "").strip()[:10])
    except ValueError:
        raise BadRequest("%s must be a date (YYYY-MM-DD)." % what)


def _edit(store, account_id, name, who, what, now, fn):
    """Load, change one account with fn(a), save, note the change and return the account as the rules read it."""
    account_id = _account_id(account_id)
    s = load(store)
    a = dict(s["accounts"].get(account_id) or {})
    fn(a)
    if name:
        a["name"] = str(name)[:120]
    keep = {k: v for k, v in a.items() if v not in (None, {}, "", [])}
    if keep.get("owner") or keep.get("windows") or keep.get("muted") or keep.get("acks"):
        s["accounts"][account_id] = keep
    else:
        s["accounts"].pop(account_id, None)
    _note(s, who, what.replace("{id}", account_id), now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def save_owner(store, account_id, name, owner, who, now=None):
    owner = str(owner or "").strip()[:120]
    return _edit(store, account_id, name, who, ("set the owner of account {id} to %s" % owner) if owner else "cleared the owner of account {id}", now, lambda a: a.update(owner=owner))


def add_window(store, account_id, name, start, end, why, who, now=None):
    """An expected-drop window: from and to (inclusive dates) and why."""
    a_day, b_day = _day(start, "The first day"), _day(end, "The last day")
    if b_day < a_day:
        raise BadRequest("The last day is before the first.")
    if (b_day - a_day).days + 1 > MAX_WINDOW_DAYS:
        raise BadRequest("A window can be at most %d days long." % MAX_WINDOW_DAYS)
    why = _why(why, "a drop is expected")

    def fn(a):
        wins = [w for w in a.get("windows") or [] if not (w.get("from") == a_day.isoformat() and w.get("to") == b_day.isoformat())]
        if len(wins) >= MAX_WINDOWS:
            raise BadRequest("At most %d windows per account: remove an old one first." % MAX_WINDOWS)
        wins.append({"from": a_day.isoformat(), "to": b_day.isoformat(), "why": why, "by": who})
        a["windows"] = sorted(wins, key=lambda w: w["from"])
    return _edit(store, account_id, name, who, "added an expected drop %s to %s in account {id}" % (a_day.isoformat(), b_day.isoformat()), now, fn)


def remove_window(store, account_id, start, end, who, now=None):
    a_day, b_day = _day(start, "The first day"), _day(end, "The last day")
    return _edit(store, account_id, None, who, "removed the expected drop %s to %s in account {id}" % (a_day.isoformat(), b_day.isoformat()), now,
                 lambda a: a.update(windows=[w for w in a.get("windows") or [] if not (w.get("from") == a_day.isoformat() and w.get("to") == b_day.isoformat())]))


def mute(store, account_id, key, item_name, why, who, now=None):
    """Leave one conversion action, list or pixel out of the checks (or let an account run without auto-tagging), with why."""
    key = str(key or "").strip()
    if not _MUTE_KEY.match(key):
        raise BadRequest("Only a conversion action, a list, a pixel or the auto-tagging check can be left out.")
    why = _why(why, "it is left out")

    def fn(a):
        muted = dict(a.get("muted") or {})
        if key not in muted and len(muted) >= MAX_MUTED:
            raise BadRequest("At most %d items can be left out in one account." % MAX_MUTED)
        muted[key] = {"name": str(item_name or "")[:120], "why": why}
        a["muted"] = muted
    return _edit(store, account_id, None, who, "left %s out of the checks in account {id}" % key, now, fn)


def unmute(store, account_id, key, who, now=None):
    key = str(key or "").strip()

    def fn(a):
        muted = dict(a.get("muted") or {})
        muted.pop(key, None)
        a["muted"] = muted
    return _edit(store, account_id, None, who, "put %s back into the checks in account {id}" % key, now, fn)


def acknowledge(store, account_id, key, note, who, now=None):
    """'Known, being fixed' on one finding: it stays open and on the page, and is not alerted or reminded while the acknowledgement holds."""
    key = str(key or "").strip()
    if not _FINDING_KEY.match(key):
        raise BadRequest("That is not a finding of this agent.")
    note = _why(note, "it is known")

    def fn(a):
        acks = {k: v for k, v in (a.get("acks") or {}).items()}
        if key not in acks and len(acks) >= MAX_ACKS:
            raise BadRequest("At most %d findings can be acknowledged in one account." % MAX_ACKS)
        acks[key] = {"by": who, "at": _now(now).isoformat(timespec="seconds"), "note": note}
        a["acks"] = acks
    return _edit(store, account_id, None, who, "acknowledged %s in account {id}" % key, now, fn)


def unacknowledge(store, account_id, key, who, now=None):
    key = str(key or "").strip()

    def fn(a):
        acks = dict(a.get("acks") or {})
        acks.pop(key, None)
        a["acks"] = acks
    return _edit(store, account_id, None, who, "withdrew the acknowledgement of %s in account {id}" % key, now, fn)


def view(store):
    s = load(store)
    return {"accounts": {aid: account_of(s, aid) for aid in s["accounts"]}, "changes": s["changes"][:10], "maxWindowDays": MAX_WINDOW_DAYS}
