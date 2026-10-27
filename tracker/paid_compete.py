"""Paid Marketing Agents: Competitor Analysis, the report and the refresh (the reads are passed in, so all of it is tested without a network).

Sheet item #39: "Look at competitor ads on platforms like Google Ads, Meta etc. Compare the same with the ads running currently in our accounts. And give us ideas to improve the ads and
creatives." The arithmetic is tracker/paid_compete_math.py, the reading of the competitors' ads tracker/paid_compete_fetch.py, what the team saved (the competitors) paid_compete_settings.py.
Our own ads are the Ad Copy Checker's saved read (tracker/paid_copycheck.py load_raw), so a report costs nothing in the ad accounts when that read is fresh.

One Google Ads account and/or one Meta ad account at a time: a manager account would mix clients, whose competitors differ.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import time

from . import paid_gads as gb
from . import paid_gads_segments as gs
from . import paid_compete_fetch as cf
from . import paid_compete_math as cm
from . import paid_compete_search as se
from . import paid_compete_settings as cs

SETTINGS_LOCK = "lock:compete:settings"


class BadRequest(ValueError):
    pass


def scope_of(google, meta):
    """({platform: account id}, key) from the accounts the page asks for; BadRequest when none, or when it is a manager account's group."""
    from . import paid_meta
    out = {}
    if google:
        g = str(google).strip()
        if not g.isdigit():
            raise BadRequest("Pick one Google Ads account.")
        out["google"] = g
    if meta:
        m = paid_meta.norm_id(meta)
        if not paid_meta.is_meta(str(m or "")):
            raise BadRequest("Pick one Meta ad account.")
        out["meta"] = m
    if not out:
        raise BadRequest("Pick a Google Ads account, a Meta ad account or both.")
    return out, "+".join("%s:%s" % (p, out[p]) for p in sorted(out))


def competitors_of(settings, accounts):
    """(competitors, region): the competitors of the accounts, each named once (the first account that has it wins), and the first region an account set."""
    seen, out, region = set(), [], None
    for aid in accounts.values():
        a = cs.account_of(settings, aid)
        for c in a["competitors"]:
            k = cs.identity(c)
            if k not in seen:
                seen.add(k)
                out.append(c)
        if region is None and (settings.get("accounts") or {}).get(str(aid), {}).get("region"):
            region = a["region"]
    return out, region or cs.REGION_DEFAULT


@contextlib.contextmanager
def _settings_lock(mon, wait_s=5.0):
    """Two people changing competitors at the same moment must not lose each other's change: a change reads the list, alters it and saves it whole, so it holds this short lock (it waits a few seconds for
    another change to finish, then says so)."""
    waited = 0.0
    while not mon.claim(SETTINGS_LOCK, 30):
        if waited >= wait_s:
            raise BadRequest("The competitors are being changed by someone else. Try again in a moment.")
        time.sleep(0.25)                                                             # looked up here, so a test can replace it
        waited += 0.25
    try:
        yield
    finally:
        mon.release(SETTINGS_LOCK)


def change(mon, accounts, names, op, who, competitor=None, region=None, now=None):
    """One change (add, remove, update, region) to the competitors of each ad account of the page's scope, each on its own list: {platform: the account's competitors as saved}. With two accounts
    (a Google Ads account and a Meta account of one client) a change that does not apply to one of them (it already has the competitor, or never had it) is passed over; it is refused only when it
    applies to none."""
    done, errors = {}, []
    with _settings_lock(mon):
        for p, aid in accounts.items():
            try:
                done[p] = cs.change_account(mon, aid, (names or {}).get(aid) or "", op, who, competitor, region, now)
            except cs.BadRequest as e:
                errors.append(str(e))
    if not done:
        raise BadRequest(errors[0])
    return done


def find_facebook(mon, accounts, names, name, who, *, ask_fn=None, force=False, now=None):
    """Look for the Facebook page of one of the account's competitors (by its name and website) and keep it when the answer is sure of it. A page already set is left alone unless `force`. Returns
    the answer {facebook, confidence, reasoning, pageName, error, kept}."""
    competitors, _region = competitors_of(cs.load_for_read(mon), accounts)
    comp = next((c for c in competitors if c["name"].lower() == str(name or "").strip().lower()), None)
    if comp is None:
        raise BadRequest("%s is not one of this account's competitors." % (str(name or "That competitor")[:60]))
    if comp.get("facebook") and not force:
        return {"facebook": comp["facebook"], "confidence": "", "reasoning": "A Facebook page is already set.", "pageName": "", "error": "", "kept": False}
    got = se.find_facebook(comp["name"], comp.get("domain") or "", ask_fn=ask_fn)
    got["kept"] = False
    if got["facebook"]:
        change(mon, accounts, names, "update", who, dict(comp, facebook=got["facebook"], fbSource="search"), now=now)
        got["kept"] = True
    return got


def own_ads(platform, account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None, meta_pages_fn=None, meta_account_fn=None):
    """([own_ad], warnings, name) for one account: the text of its running ads."""
    from . import paid_copycheck
    clock = now.timestamp() if isinstance(now, dt.datetime) else now                 # the saved read counts in epoch seconds, the report's clock is a datetime
    raw, _stale, account = paid_copycheck.load_raw(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn, search_fn=search_fn, now=clock, force=force, store=store, today=today,
                                                   meta_pages_fn=meta_pages_fn, meta_account_fn=meta_account_fn)
    if account.get("members"):
        raise BadRequest("%s is a group of accounts. Pick one account: each client has its own competitors." % (account.get("name") or account_id))
    ads = [a for acct in raw.get("accounts") or [] for a in acct.get("ads") or [] if a.get("platform") == platform]
    return [x for x in (cm.own_ad(a) for a in ads) if x], list(raw.get("warnings") or []), account.get("name") or account_id


def report_for(google, meta, *, mon, now=None, **reads):
    """The Competitor Analysis report for a Google Ads account and/or a Meta ad account. `mon` is the monitors' store (the competitors, what was read of them and the refresh). `reads` hands
    the reads of our own ads in (accounts_fn, groups_fn, search_fn, meta_pages_fn, meta_account_fn, store, today, force) for tests."""
    accounts, key = scope_of(google, meta)
    now = now or dt.datetime.now(dt.timezone.utc)
    settings = cs.load_for_read(mon)
    competitors, region = competitors_of(settings, accounts)
    platforms = sorted(accounts, key=lambda p: ("meta", "google").index(p))
    own, warnings, names = {}, [], {}
    for p in platforms:
        try:
            own[p], w, names[p] = own_ads(p, accounts[p], now=now, **reads)
        except (BadRequest, gs.GadsError, gb.AdsError, gb.RefreshTooSoon):            # the platform's own errors are answered by the route as every other page does
            raise
        except Exception as e:                                                       # noqa: BLE001 (our ads could not be read: say so, do not compare against nothing)
            raise BadRequest("The ads of %s could not be read (%s)." % (accounts[p], str(e)[:160]))
        warnings += w
    own_counts = {p: {"ads": len(own[p])} for p in platforms}
    base = {"key": key, "accounts": accounts, "names": names, "region": region, "competitors": competitors, "platforms": platforms, "warnings": warnings,
            "estimate": cf.estimate(competitors, platforms, region), "cacheDays": cf.CACHE_DAYS, "job": cf.job_of(mon, key), "limits": {"competitors": cs.MAX_COMPETITORS, "ads": cf.LIMIT}}
    if not competitors:
        return dict(base, status="no_competitors", headline="No competitors are saved for this account yet.", ideas=[], notMeasured=list(cm.NOT_MEASURED), caveats=[],
                    counts={"competitorAds": 0, "reads": 0, "gaps": 0, "ideas": 0}, analysis={}, own=own_counts)
    a = cm.analyse(own, cf.entries_for(mon, competitors, platforms, region), now=now, region=region)
    return dict(base, status=a["status"], headline=a["headline"], ideas=a["ideas"], notMeasured=a["notMeasured"], caveats=a["caveats"], counts=a["counts"], analysis=a["platforms"], own=own_counts)
