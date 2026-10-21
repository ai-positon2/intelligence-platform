"""Paid Marketing Agents: Cross-Channel & Attribution Analysis, the report.

Pick a Google Ads account, a Meta ad account or one of each (the same account strings as the Account Audit, tracker/paid_accounts.py), a range and a conversion measure, and it reads, read only,
each platform's daily campaign numbers for that range and the one of the same length before it (the stored days the other agents share, tracker/paid_gads.py and paid_meta.py, so a repeat
read costs nothing) and how each platform counts a conversion (tracker/paid_xchannel_settings.py), then:

  channels         each platform and the blended total: spend, clicks, conversions, value, cost per conversion and return on ad spend, against the previous period, with each platform's
                   share of the spend beside its share of the conversions and the value (tracker/paid_xchannel_math.py)
  types            the campaign types, each with its rule-of-thumb role (captures demand, creates demand, mixed)
  decomposition    what moved the blended cost per conversion: the platforms' own costs, or the mix
  weekly, links    the weeks of both platforms and whether one platform's spend moves with the other's conversions
  attribution      the windows, models and goals each platform counts, side by side
  reconciliation   the platforms' claims against the conversions and revenue the person typed from their own system
  findings         what is worth doing something about (tracker/paid_xchannel_findings.py)

A platform that cannot be read is left out and named; the report is the other's alone. A Google and a Meta account are read together only when they bill in the same currency. Every
figure is arithmetic: no model is asked.
"""
from __future__ import annotations

import datetime as dt
import logging
import math

from . import paid_gads as base
from . import paid_xchannel_findings as fd
from . import paid_xchannel_math as m
from . import paid_xchannel_plans as plans
from . import paid_xchannel_settings as sx
from .paid_accounts import targets as _targets_of

BadRequest = m.BadRequest
log = logging.getLogger(__name__)

CACHE_VERSION = "2"                              # bump when the shape of a saved settings read changes
SETTINGS_TTL_S = 3600
MEASURES = ("primary", "all")
MEASURE_LABEL = {"primary": "Conversions (what each platform bids on)", "all": "All conversions"}
MAX_TYPED = 1e12
NOTES = [
    "Each platform's numbers are its own claim over its own window and model. Both can claim the same sale, so the two platforms' conversions are not added to a blended total as if they were "
    "separate people unless you reconcile them with your own system's numbers below.",
    "An average cost per conversion is not a marginal one: the cheaper platform may not take more budget at the same price. A suggested shift is a test, not a plan.",
    "Campaign types are labelled by what they are usually for (captures demand, creates demand, mixed). It is a rule of thumb, not a measurement: a campaign that creates demand is answered later, "
    "often by a search, and a last-click cost per conversion undercounts it.",
    "GA4, LinkedIn Ads, programmatic, a CRM and offline sales are not read. Conversions that neither platform claims cannot be seen here.",
]
NOTES_META = [
    "Meta's conversions are the results of each campaign's objective (a purchase for Sales, a lead for Leads, an install for App promotion); a campaign with another objective reports none. "
    "Meta counts a conversion up to its attribution window after a click and, where the ad set says so, after a view.",
]
NOTES_GOOGLE = [
    "Google's Conversions column counts the primary conversion actions after a click. View-through conversions are a separate column, and All conversions adds the secondary actions and cross-device conversions.",
]


def clean_actual(raw, what):
    """A number the person typed (thousands separators allowed) or None for an empty field. Negative, not a number, infinite or absurd values are refused."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        v = float(str(raw).replace(",", "").replace(" ", ""))
    except ValueError:
        raise BadRequest("%s should be a number." % what)
    if math.isnan(v) or math.isinf(v) or v < 0 or v > MAX_TYPED:
        raise BadRequest("%s should be a number of zero or more." % what)
    return v


# ── Reading ─────────────────────────────────────────────────────────────────

def _shape(target, first, last, *, store, force, notes, stamps, http=None):
    from . import paid_meta
    if target["platform"] == "meta":
        return paid_meta.campaign_days(store, target["id"], first, last, currency=target.get("currency") or "USD", force=force, notes=notes, stamps=stamps, http=http)
    return base.campaign_days(store, target["id"], first, last, force=force, notes=notes, stamps=stamps, http=http)


def read_days(targets, first, last, *, store, force=False, shape_fn=None, stages=()):
    """({platform: shape}, problems, notes, stamps) over first..last. With two platforms, one that cannot be read is a problem and the other still counts; one platform alone failing is raised.
    A spent allowance is raised however many platforms there are, so the page can say when to come back.

    `stages` are the pieces (each a (first, last) pair) that are read, and so saved day by day, one after the other before the whole range is assembled from what is saved: a platform
    stores a read only when the whole read has come back, so one long read that is cut short by a timeout would keep nothing, where two shorter ones keep the first."""
    shapes, problems, notes, stamps = {}, [], [], []
    for t in targets:
        try:
            if shape_fn:
                shapes[t["platform"]] = shape_fn(t, first, last)
            else:
                for a, b in stages:
                    _shape(t, a, b, store=store, force=force, notes=notes, stamps=stamps)
                shapes[t["platform"]] = _shape(t, first, last, store=store, force=False if stages else force, notes=notes, stamps=stamps)
        except base.OverQuota:
            raise
        except base.AdsError as e:
            if len(targets) == 1:
                raise
            problems.append("%s could not be read, so this report is the other platform's alone: %s" % (m.PLATFORM_NAME[t["platform"]], e))
    if not shapes:
        raise base.AdsError("Neither platform could be read.")
    return shapes, problems, notes, stamps


def read_settings(targets, start, end, *, store, force=False, search_fn=None, pages_fn=None, notes=None, stamps=None):
    """({"google": actions or None, "meta": summary or None}, warnings). A settings read that fails is a warning: the numbers stand without it."""
    out, warnings = {"google": None, "meta": None}, []
    for t in targets:
        plat = t["platform"]
        key = "%sxch%s:%s:%s:%s:%s" % (base.CACHE_V, CACHE_VERSION, plat, t["id"], start, end)
        try:
            if plat == "meta":
                from . import paid_meta
                pf = pages_fn or (lambda path, params, act=t["id"]: paid_meta._pages(act + "/" + path, params, store))
                out["meta"] = base._cached(store, key, SETTINGS_TTL_S, lambda: sx.read_meta(pf, start, end), notes=notes, force=force, stamps=stamps)
            else:
                sf = search_fn or (lambda gaql, cid=t["id"]: base.search(cid, gaql, store))
                out["google"] = base._cached(store, key, SETTINGS_TTL_S, lambda: sx.read_google(sf, start, end), notes=notes, force=force, stamps=stamps)
                try:                                     # how long Google's conversions lag the click: sizes the window gap, and is left out quietly when Google will not say
                    out["googleLag"] = base._cached(store, key + ":lag", SETTINGS_TTL_S, lambda: sx.read_lag(sf, start, end) or {}, notes=notes, force=force, stamps=stamps) or None
                except Exception as e:                   # noqa: BLE001
                    log.warning("xchannel: Google's conversion lag could not be read (%s)", e)
        except Exception as e:                           # noqa: BLE001 (the settings are a nicety: the numbers stand without them)
            log.warning("xchannel: %s settings could not be read (%s)", plat, e)
            warnings.append("How %s counts conversions could not be read, so the attribution comparison leaves it out (%s)." % (m.PLATFORM_NAME[plat], str(e)[:160]))
    return out, warnings


# ── Assembling ──────────────────────────────────────────────────────────────

def _fig(t):
    f = m.figures(t)
    return {k: (None if v is None else round(v, 4)) for k, v in f.items()}


def _type_rows(typed, plat_cost, all_cost):
    rows = []
    for key, t in typed.items():
        f = m.figures(t)
        rows.append({"key": key, "label": t["label"], "role": t["role"], "roleLabel": m.ROLE[t["role"]], "impr": t["impr"], "clicks": t["clicks"], "cost": round(t["cost"], 2), "conv": round(t["conv"], 2),
                     "value": round(t["value"], 2), "cpa": None if f["cpa"] is None else round(f["cpa"], 2), "roas": None if f["roas"] is None else round(f["roas"], 4),
                     "shareOfPlatform": None if plat_cost <= 0 else round(t["cost"] / plat_cost * 100, 1), "shareOfAll": None if all_cost <= 0 else round(t["cost"] / all_cost * 100, 1)})
    rows.sort(key=lambda r: (-r["cost"], r["label"]))
    return rows


def _deltas(cur, prev):
    return {k: (None if m.change(cur[k], prev[k]) is None else round(m.change(cur[k], prev[k]), 1)) for k in ("cost", "clicks", "conv", "value", "cpa", "roas", "cpc", "cvr", "ctr")}


def assemble(raw, *, measure="primary", actual_conv=None, actual_value=None, now=None):
    """The report from a raw read: {"accounts", "periods", "shapes", "settings", "problems", ...} (see read_report). Pure: nothing is read here."""
    shapes, accts = raw["shapes"], {a["platform"]: a for a in raw["accounts"]}
    cs, ce = raw["periods"]["current"]
    ps, pe = raw["periods"]["previous"]
    plats = [p for p in ("google", "meta") if p in shapes]
    warnings = list(raw.get("problems") or []) + list(raw.get("settingsWarnings") or [])
    used = {}
    tot = {}
    for p in plats:
        mu = measure
        if p == "google" and measure == "all" and 5 in (shapes[p].get("missing") or []):
            mu = "primary"
            warnings.append("Google would not give All conversions for this account, so Google Ads here is its Conversions, and the two platforms are not on the same measure.")
        used[p] = mu
        tot[p] = {"cur": m.totals(shapes[p], cs, ce, mu, p), "prev": m.totals(shapes[p], ps, pe, mu, p)}
    cur = {p: tot[p]["cur"]["all"] for p in plats}
    prev = {p: tot[p]["prev"]["all"] for p in plats}
    blended_cur = {k: sum(cur[p][k] for p in plats) for k in ("impr", "clicks", "cost", "conv", "value", "view")}
    blended_prev = {k: sum(prev[p][k] for p in plats) for k in ("impr", "clicks", "cost", "conv", "value", "view")}
    shares = m.shares(cur)
    all_cost = blended_cur["cost"]
    channels = {}
    for p in plats:
        c, pv = _fig(cur[p]), _fig(prev[p])
        types = _type_rows(tot[p]["cur"]["types"], cur[p]["cost"], all_cost)
        channels[p] = {"platform": p, "name": m.PLATFORM_NAME[p], "account": {k: accts[p].get(k) for k in ("id", "name", "currency")}, "measure": used[p], "cur": c, "prev": pv, "delta": _deltas(c, pv),
                       "share": {k: (None if v is None else round(v, 1)) for k, v in shares[p].items()}, "types": types,
                       "view": None if p == "meta" or 7 in (shapes[p].get("missing") or []) else round(cur[p]["view"], 2)}
    bc, bp = _fig(blended_cur), _fig(blended_prev)
    blended = {"cur": bc, "prev": bp, "delta": _deltas(bc, bp)}
    decomposition = m.decompose_cpa(cur, prev) if len(plats) == 2 else {"ok": False, "why": "The split needs both platforms."}
    # The weeks of both platforms over everything read (the previous period and the current), and the links between them.
    first, last = ps, ce
    weeks = {}
    for p in plats:
        weeks[p] = m.weekly(m.daily(shapes[p], used[p]), first, last)
    links = m.links(weeks) if len(plats) == 2 else {"ok": False, "weeks": min((len(w) for w in weeks.values()), default=0), "tests": [], "why": "A link between platforms needs both of them."}
    week_rows = []
    if plats:
        for i, w0 in enumerate(weeks[plats[0]]):
            row = {"start": w0["start"]}
            for p in plats:
                w = weeks[p][i] if i < len(weeks[p]) else None
                row[p] = None if w is None else {"cost": round(w["cost"], 2), "conv": round(w["conv"], 2)}
            week_rows.append(row)
    # How each platform counts a conversion.
    gsum = sx.summarise_google(raw["settings"]["google"]) if raw.get("settings", {}).get("google") else None
    msum = raw["settings"].get("meta") if raw.get("settings") else None
    meta_kinds = None
    if "meta" in plats:
        meta_kinds = {}
        for key, t in tot["meta"]["cur"]["types"].items():
            k = sx.META_KIND.get(key, "other")
            meta_kinds[k] = meta_kinds.get(k, 0.0) + t["cost"]
    gview = channels["google"]["view"] if "google" in channels else None
    attribution_notes = sx.compare(gsum, msum, meta_kinds=meta_kinds, google_view=gview) if len(plats) == 2 else []
    window = None
    if gsum and msum and gsum.get("dominantClick") is not None and (msum.get("dominant") or {}).get("click") is not None:
        gc, mc = gsum["dominantClick"], msum["dominant"]["click"]
        window = {"google": gc, "meta": mc, "later": sx.window_share((raw.get("settings") or {}).get("googleLag"), mc) if gc > mc else None}
    reconciliation = m.reconcile(cur, actual_conv, actual_value)
    currency = next((a.get("currency") for a in raw["accounts"] if a.get("currency")), "") or ""
    report = {
        "account": {"id": "+".join(a["id"] for a in raw["accounts"]), "name": " + ".join(dict.fromkeys(a["name"] for a in raw["accounts"])), "currency": currency},
        "accounts": [{k: a.get(k) for k in ("platform", "id", "name", "currency")} for a in raw["accounts"]],
        "platforms": plats, "range": {"preset": raw["preset"], "label": m.PRESET_LABEL[raw["preset"]], "start": cs, "end": ce, "days": raw["periods"]["days"]},
        "previous": {"start": ps, "end": pe}, "measure": measure, "measureLabel": MEASURE_LABEL[measure], "measureUsed": used,
        "channels": channels, "blended": blended, "decomposition": decomposition, "weeks": week_rows, "links": links,
        "attribution": {"google": gsum, "meta": msum, "notes": attribution_notes, "window": window},
        "reconciliation": reconciliation, "warnings": warnings, "stale": raw.get("stale"), "readAt": raw.get("readAt"),
        "notes": list(NOTES) + (NOTES_GOOGLE if "google" in plats else []) + (NOTES_META if "meta" in plats else []),
        "generatedAt": (now or dt.datetime.now(dt.timezone.utc)).isoformat()}
    finding_input = {"currency": currency, "channels": {p: {"cur": channels[p]["cur"], "prev": channels[p]["prev"], "types": channels[p]["types"]} for p in plats},
                     "blended": {"cur": bc, "prev": bp}, "decomposition": decomposition, "links": links, "notes": attribution_notes, "reconcile": reconciliation, "window": window}
    report["findings"] = fd.findings(finding_input)
    for f in report["findings"]:
        p = plans.plan_for(f, finding_input)
        if p:
            f["plan"] = p
    report["summary"] = {"spend": bc["cost"], "conversions": bc["conv"], "value": bc["value"], "cpa": bc["cpa"], "roas": bc["roas"], "findings": len(report["findings"]),
                         "high": sum(1 for f in report["findings"] if f["level"] == "high")}
    return report


def report_for(account, *, preset="30", measure="primary", actual_conv=None, actual_value=None, force=False, store=None, accounts_fn=None,
               shape_fn=None, search_fn=None, pages_fn=None, today=None, now=None):
    """The report for `account` (see tracker/paid_accounts.py) over `preset`. `force` (Refresh) re-reads the stored days and the settings, which the caller limits to once per
    paid_gads.REFRESH_COOLDOWN_S per account. Raises BadRequest for a bad account, range, measure or typed number, and the platform's own AdsError when nothing can be read."""
    from . import paid_store
    store = store or paid_store
    if measure not in MEASURES:
        raise BadRequest("measure should be one of: %s" % ", ".join(MEASURES))
    ac = clean_actual(actual_conv, "Conversions recorded")
    av = clean_actual(actual_value, "Revenue recorded")
    periods = m.periods(preset, today)
    key, targets = _targets_of(account, store=store, accounts_fn=accounts_fn)
    if force:
        base.refresh_guard(store, "xch:" + key)
    first, last = periods["previous"][0], periods["current"][1]
    shapes, problems, notes, stamps = read_days(targets, first, last, store=store, force=force, shape_fn=shape_fn, stages=(periods["previous"], periods["current"]))
    read_targets = [t for t in targets if t["platform"] in shapes]
    settings, swarn = read_settings(read_targets, periods["current"][0], periods["current"][1], store=store, force=force, search_fn=search_fn, pages_fn=pages_fn, notes=notes, stamps=stamps)
    raw = {"accounts": targets, "shapes": shapes, "settings": settings, "problems": problems, "settingsWarnings": swarn, "periods": periods, "preset": str(preset or "30"),
           "stale": min(notes) if notes else None, "readAt": base.oldest(stamps) if stamps else None}
    return assemble(raw, measure=measure, actual_conv=ac, actual_value=av, now=now)
