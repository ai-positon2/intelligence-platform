"""Paid Marketing Agents: Spend Anomaly Alert, what the team saved (no network).

The sheet gives no limits, so an account's usual spend is its own last 14 days. What a team knows that the numbers cannot is kept for everyone in the monitors' store
(tracker/paid_monitor_store.py, the value "settings:anomaly") instead of in one person's browser:

  sensitivity   low, normal or high: how far above usual before it is flagged (2.5x, 1.75x or 1.4x for a medium finding)
  per account   usualDaily   what the account usually spends a day (used while fewer than 4 days of spend are known)
                maxDaily     a ceiling: any day that reaches it is a high finding whatever the usual is
                minSwing     the smallest swing worth a word, in the account's currency (25 when not saved)
                mutedUntil   a planned push (a sale, a launch): spikes up to this day are shown as expected and never alerted; at most 45 days ahead, so a mute cannot be forgotten
                note         why (shown beside the mute and in the history)

Each change says who made it and when. The store is any object with state_get and state_set, so tests run on a dict.
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone

from . import paid_anomaly_math as m

KEY = "settings:anomaly"
MAX_MONEY = 10_000_000
MAX_MUTE_DAYS = 45
MAX_NOTE = 200


class BadRequest(ValueError):
    pass


def empty():
    return {"sensitivity": None, "accounts": {}, "changes": []}


def load(store):
    s = store.state_get(KEY) or {}
    return {"sensitivity": s.get("sensitivity") if s.get("sensitivity") in m.SENSITIVITY else None, "accounts": dict(s.get("accounts") or {}), "changes": list(s.get("changes") or [])}


def account_of(settings, account_id):
    """One account's saved figures: {usualDaily, maxDaily, minSwing, mutedUntil, note}, each None when not saved."""
    a = (settings.get("accounts") or {}).get(str(account_id)) or {}
    return {k: a.get(k) or None for k in ("usualDaily", "maxDaily", "minSwing", "mutedUntil", "note")}


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


def clean_account(data, today):
    """{usualDaily, maxDaily, minSwing, mutedUntil, note} from what the page sent, or BadRequest saying what is wrong."""
    data = data if isinstance(data, dict) else {}
    usual, ceiling, swing = _money(data.get("usualDaily"), "The usual daily spend"), _money(data.get("maxDaily"), "The daily ceiling"), _money(data.get("minSwing"), "The smallest swing")
    if usual and ceiling and ceiling < usual:
        raise BadRequest("The ceiling cannot be below the usual daily spend.")
    until, note = str(data.get("mutedUntil") or "").strip(), str(data.get("note") or "").strip()[:MAX_NOTE]
    if until:
        try:
            day = dt.date.fromisoformat(until)
        except ValueError:
            raise BadRequest("The day to mute until must look like 2026-11-30.")
        if day > today + dt.timedelta(days=MAX_MUTE_DAYS):
            raise BadRequest("Alerts can be muted for at most %d days ahead; mute again when it passes." % MAX_MUTE_DAYS)
        until = day.isoformat()
    return {"usualDaily": usual, "maxDaily": ceiling, "minSwing": swing, "mutedUntil": until or None, "note": note or None}


def save_account(store, account_id, data, who, today=None, now=None):
    account_id = str(account_id or "").strip()
    if not account_id or not all(ch.isalnum() or ch == "_" for ch in account_id):
        raise BadRequest("Pick one account to save its figures: they are not saved for an MCC as a whole.")
    clean = clean_account(data, today or dt.date.today())
    s = load(store)
    if any(v for v in clean.values()):
        s["accounts"][account_id] = clean
    else:
        s["accounts"].pop(account_id, None)
    _note(s, who, "saved the figures of account %s" % account_id, now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def set_sensitivity(store, value, who, now=None):
    if value not in (None, "") and value not in m.SENSITIVITY:
        raise BadRequest("The sensitivity must be low, normal or high.")
    s = load(store)
    s["sensitivity"] = value or None
    _note(s, who, "set the sensitivity to %s" % (value or "the default (normal)"), now)
    store.state_set(KEY, s)
    return s["sensitivity"]


def view(store):
    """What the page needs: the settings and the defaults."""
    s = load(store)
    return {"sensitivity": s["sensitivity"], "defaultSensitivity": m.DEFAULT_SENSITIVITY, "ratios": {k: list(v) for k, v in m.SENSITIVITY.items()}, "minSwing": m.MIN_SWING,
            "maxMuteDays": MAX_MUTE_DAYS, "accounts": s["accounts"], "changes": s["changes"][:10]}
