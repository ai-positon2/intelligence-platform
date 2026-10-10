"""Market Radar: what the edit page shows and accepts.

A company's profile (mr_entities.profile) is the agent's reading of its
website, shared by everyone who tracks that company. A user's corrections
are THEIR view of their client, so they are stored on the client
(mr_clients.settings["profile_overrides"]) and laid over the reading every
time it is used: on the page, and as the input to competitor discovery. A
later re-read of the website therefore never undoes an edit, and one user's
edit never changes another user's client.

Overrides are a flat map of dotted field names ("hq.city") to values, each
validated here. Editing any address field re-locates the business (free,
OpenStreetMap), because the map search for nearby competitors starts there.
"""
from __future__ import annotations

import copy
import re

from . import market_radar_store as store

ARCHETYPES = ("local_single", "multi_location", "ecommerce", "b2b_services", "b2b_product",
              "manufacturer", "other")
CUSTOMER_TYPES = ("B2C", "B2B", "both")
PRICE_POSITIONS = ("budget", "mid", "premium", "luxury", "unknown")
HQ_FIELDS = ("hq.street", "hq.postal_code", "hq.city", "hq.region", "hq.country_code")
MIN_RADIUS_KM, MAX_RADIUS_KM = 0.5, 25

_DASH = re.compile(r"\s*[–—]\s*")


class EditError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join("%s: %s" % kv for kv in errors.items()))


def _text(limit):
    def check(v):
        if not isinstance(v, str):
            raise ValueError("must be text")
        v = _DASH.sub(", ", " ".join(v.split()))
        if len(v) > limit:
            raise ValueError("at most %d characters" % limit)
        return v
    return check


def _choice(options):
    def check(v):
        if v not in options:
            raise ValueError("must be one of %s" % ", ".join(options))
        return v
    return check


def _text_list(max_items, limit):
    one = _text(limit)

    def check(v):
        if isinstance(v, str):
            v = [x for x in re.split(r"[,\n]", v)]
        if not isinstance(v, list):
            raise ValueError("must be a list")
        out = []
        for x in v:
            x = one(x)
            if x and x not in out:
                out.append(x)
        if len(out) > max_items:
            raise ValueError("at most %d items" % max_items)
        return out
    return check


def _country(v):
    v = _text(2)(v).upper()
    if v and not re.fullmatch(r"[A-Z]{2}", v):
        raise ValueError("a two-letter country code, such as US or GB")
    return "GB" if v == "UK" else v


def _countries(v):
    items = _text_list(60, 3)(v)
    out = []
    for x in items:
        x = _country(x)
        if x and x not in out:
            out.append(x)
    return out


def _count(v):
    if isinstance(v, str) and v.strip() == "":
        return -1
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError("must be a whole number")
    if n < -1 or n > 100_000:
        raise ValueError("must be between 0 and 100,000")
    return n


FIELDS = {
    "name": _text(120),
    "one_liner": _text(300),
    "archetype": _choice(ARCHETYPES),
    "customer_type": _choice(CUSTOMER_TYPES),
    "offerings": _text_list(12, 80),
    "location_count": _count,
    "hq.street": _text(160),
    "hq.postal_code": _text(20),
    "hq.city": _text(80),
    "hq.region": _text(80),
    "hq.country_code": _country,
    "markets": _countries,
    "industry.plain_label": _text(120),
    "industry.keywords": _text_list(15, 60),
    "price_positioning": _choice(PRICE_POSITIONS),
    "service_area": _text(200),
}


def get_path(profile, field):
    node = profile or {}
    for part in field.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _set_path(profile, field, value):
    parts = field.split(".")
    node = profile
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def overrides_of(settings):
    return dict((settings or {}).get("profile_overrides") or {})


def effective_profile(profile, settings):
    """The agent's reading with this client's corrections laid over it."""
    out = copy.deepcopy(profile or {})
    over = overrides_of(settings)
    for field, value in over.items():
        if field in FIELDS:
            _set_path(out, field, value)
    if (settings or {}).get("hq_point_override"):
        out["hq_point"] = settings["hq_point_override"]
    out["edited_fields"] = sorted(f for f in over if f in FIELDS)
    return out


def validate(changes):
    """{field: value} -> cleaned values, or EditError listing every problem."""
    if not isinstance(changes, dict):
        raise EditError({"changes": "must be an object of field names to values"})
    clean, errors = {}, {}
    for field, value in changes.items():
        check = FIELDS.get(field)
        if check is None:
            errors[field] = "not an editable field"
            continue
        try:
            clean[field] = check(value)
        except ValueError as e:
            errors[field] = str(e)
    if errors:
        raise EditError(errors)
    return clean


def save_edits(client_id, owner_email, *, changes=None, reset=(), radius_km=False,
               locate=None, conn=None):
    """Apply a user's edits. `changes` sets fields, `reset` drops corrections
    (back to the site's reading), `radius_km` sets the nearby-search radius
    (None clears it). Returns notes for the page (such as an address the map
    could not find). Raises EditError, PermissionError, KeyError."""
    client = store.get_client(client_id, owner_email, conn=conn)
    if client is None:
        raise KeyError("no such client of yours")
    clean = validate(changes or {})
    reset = [f for f in (reset or []) if f in FIELDS]
    settings = dict(client["settings"] or {})
    over = overrides_of(settings)
    reading = client["profile"] or {}
    for field, value in clean.items():
        # A value equal to the site's own reading is not a correction.
        if value == get_path(reading, field) or (value in ("", []) and get_path(reading, field) in (None, "", [])):
            over.pop(field, None)
        else:
            over[field] = value
    for field in reset:
        over.pop(field, None)
    settings["profile_overrides"] = over
    notes = []
    if any(f in HQ_FIELDS for f in list(clean) + reset):
        if any(f in over for f in HQ_FIELDS):
            if locate is None:
                from .market_radar_profile import locate_hq as locate
            eff = effective_profile(reading, dict(settings, hq_point_override=None))
            # The user's address wins over the site's structured data.
            point = locate(dict(eff, facts={}), conn=conn)
            if point:
                settings["hq_point_override"] = point
                notes.append("Located at %s (%s)." % (point.get("label") or "the new address",
                                                     point.get("precision", "approximate")))
            else:
                notes.append("That address was not found on the map, so nearby competitors are "
                             "still searched around the previous location.")
        else:
            settings.pop("hq_point_override", None)
    if radius_km is not False and radius_km is not None:
        try:
            radius_km = float(radius_km)
        except (TypeError, ValueError):
            raise EditError({"radius_km": "must be a number of kilometres"})
        if not MIN_RADIUS_KM <= radius_km <= MAX_RADIUS_KM:
            raise EditError({"radius_km": "must be between %s and %s km" % (MIN_RADIUS_KM,
                                                                           MAX_RADIUS_KM)})
    store.update_client_settings(client_id, owner_email, settings=settings, radius_km=radius_km,
                                 conn=conn)
    return notes


# == what the page shows ===========================================================

def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _num(value):
    return float(value) if value is not None else None


def profile_view(client):
    reading = client["profile"] or {}
    eff = effective_profile(reading, client["settings"])
    shown = {f: get_path(eff, f) for f in FIELDS}
    site = {f: get_path(reading, f) for f in eff["edited_fields"]}
    facts = reading.get("facts") or {}
    return {
        "fields": shown,
        "edited": eff["edited_fields"],
        "site_reading": site,
        "has_reading": bool(reading),
        "archetype_reason": reading.get("archetype_reason"),
        "business_model": reading.get("business_model"),
        "hq_point": eff.get("hq_point"),
        "hq_country_source": reading.get("hq_country_source"),
        "evidence": reading.get("evidence") or [],
        "checks": reading.get("checks") or [],
        "unknowns": reading.get("unknowns") or [],
        "confidence": reading.get("confidence") or {},
        "facts": {k: facts.get(k) for k in ("website", "platforms", "socials", "jobs_boards",
                                            "store_locator", "read_from_archive")},
        "read_at": reading.get("read_at"),
        "model": reading.get("model"),
    }


def competitor_rows(client_id, owner_email, *, conn=None):
    rows = store.competitors(client_id, owner_email, include_removed=True, conn=conn)
    older = None
    out = []
    for r in rows:
        d = r.get("details") or {}
        if not d and r.get("found_via") != ["added by you"]:
            # Saved before details were stored: take them from the run that found it.
            if older is None:
                older = store.found_in_runs(client_id, owner_email, conn=conn)
            d = older.get(r["domain"]) or {}
        out.append({
            "entity_id": r["entity_id"], "domain": r["domain"], "name": r["name"] or r["domain"],
            "kind": r["kind"], "status": r["status"], "score": d.get("score"),
            "confidence": _num(r["confidence"]), "reason": d.get("reason"),
            "sells": d.get("sells"), "location": d.get("location"),
            "distance_km": d.get("distance_km"), "branches_nearby": d.get("branches_nearby"),
            "checked_on": d.get("checked_on"), "found": r.get("found_via") or [],
            "added_by_user": bool(d.get("added_by_user")), "updated_at": _iso(r.get("updated_at"))})
    return out


def run_view(run):
    if not run:
        return None
    summary = run.get("summary") or {}
    result = summary.get("result") or {}
    cov = result.get("coverage") or run.get("coverage") or {}
    out = {"id": run["id"], "status": run["status"], "stage": run["stage"], "error": run["error"],
           "created_at": _iso(run.get("created_at")), "finished_at": _iso(run.get("finished_at")),
           "seconds": summary.get("seconds"), "profile_note": summary.get("profile_note"),
           "unread": result.get("unread") or [], "gaps": result.get("gaps") or [],
           "left_out": (result.get("left_out") or [])[:15],
           "sources": {}}
    labels = {"site": "Named on the company's own site", "model": "Known competitors (checked)",
              "search": "Google search", "places": "Map of nearby businesses",
              "articles": "\"Best of\" articles"}
    for key, label in labels.items():
        c = cov.get(key)
        if not isinstance(c, dict):
            continue
        out["sources"][key] = {
            "label": label, "status": c.get("status") or ("ok" if key == "articles" else None),
            "found": c.get("found") if key != "articles" else c.get("added"),
            "error": c.get("error"), "note": c.get("note") or c.get("warning"),
            "results": c.get("results"), "radius_km": c.get("radius_km"),
            "location_precision": c.get("location_precision")}
    checked = cov.get("checked") or {}
    out["checked"] = {k: checked.get(k) for k in ("candidates", "read", "judged",
                                                  "read_from_archive", "unread_minor")}
    try:
        from . import market_radar_ledger as ledger
        led = ledger.summary(run["id"])
        out["cost_usd"], out["cost_partial"] = led["total_usd"], led["partial"]
    except Exception:
        out["cost_usd"], out["cost_partial"] = None, None
    return out


def client_view(client_id, owner_email, *, conn=None):
    client = store.get_client(client_id, owner_email, conn=conn)
    if client is None:
        return None
    return {
        "client": {"id": client["client_id"], "entity_id": client["entity_id"],
                   "domain": client["domain"], "name": client["name"] or client["domain"],
                   "radius_km": _num(client["radius_km"]),
                   "profile_updated_at": _iso(client["profile_updated_at"])},
        "profile": profile_view(client),
        "competitors": competitor_rows(client_id, owner_email, conn=conn),
        "last_run": run_view(store.latest_run(client_id, owner_email, conn=conn)),
    }


def client_list(owner_email, *, conn=None):
    out = []
    for c in store.list_clients(owner_email, conn=conn):
        out.append(dict(c, updated_at=_iso(c["updated_at"]), name=c["name"] or c["domain"]))
    return out


def tidy_suggestions(owner_email, *, conn=None):
    """One-off for lists built before a new search replaced old suggestions
    (2026-10-09): for each of this person's companies, keep only the
    suggestions its latest full search made. Free; touches nothing the user
    decided. Returns {domain: suggestions dropped}."""
    out = {}
    for c in store.list_clients(owner_email, conn=conn):
        with store._tx(conn) as cur:
            cur.execute("""SELECT summary FROM mr_runs WHERE client_id=%s AND status='complete'
                           AND mode <> 'collect' ORDER BY id DESC LIMIT 1""", (c["client_id"],))
            row = cur.fetchone()
        summary = (row[0] if row else None) or {}
        result = summary.get("result") or {}
        search = ((result.get("coverage") or {}).get("search") or {}).get("status")
        if result.get("status") != "ok" or search != "ok" or not summary.get("saved_entities"):
            out[c["domain"]] = "kept (latest search was not a full one)"
            continue
        out[c["domain"]] = store.retire_suggestions(c["client_id"], owner_email,
                                                    summary["saved_entities"], conn=conn)
    return out


# == competitor moves (Phase 3) =========================================================

EVENT_LABELS = {
    "new_location": "New location", "closed_location": "Location removed",
    "site_restructured": "Locations reorganised", "location_list_shrank": "Fewer locations listed",
    "product_launch": "New product", "product_removed": "Product removed",
    "price_increase": "Price up", "price_cut": "Price down", "sale_started": "On sale",
    "sold_out": "Sold out", "promotion": "New offer", "promotion_ended": "Offer ended",
    "page_changed": "Page changed", "review_growth": "More reviews",
    "announcement": "Announcement", "hiring_surge": "Hiring up",
    "hiring_slowdown": "Hiring down", "new_job_location": "Hiring in a new place",
    "senior_hire_search": "Senior role open", "nearby_opening": "Opening nearby",
    "new_entrant": "New brand", "relocation": "Moved", "acquisition": "Acquisition",
    "funding": "Funding", "leadership_change": "Leadership", "layoffs": "Job cuts",
    "hiring_push": "Hiring drive", "pricing_change": "Pricing", "partnership": "Partnership",
    "new_market": "New market", "legal_regulatory": "Legal", "financial_results": "Results",
    "rebrand": "Rebrand", "other_move": "Move", "marketing_campaign": "Campaign",
}
def _detector_labels():
    from .market_radar_collect import DETECTORS
    return {name: label for name, _r, _c, _b, label in DETECTORS}


DETECTOR_LABELS = _detector_labels()


def moves_view(client_id, owner_email, *, conn=None):
    """What this client's competitors did: the events the collections found
    (newest first), what is tracked for each competitor, and the latest
    collection's coverage. None when the client is not this person's."""
    if store.get_client(client_id, owner_email, conn=conn) is None:
        return None
    rows = store.competitors(client_id, owner_email, conn=conn)
    names = {r["entity_id"]: (r.get("name") or r["domain"], r["domain"]) for r in rows}
    ids = list(names)
    events, hidden = [], []
    scores = store.client_scores(client_id, conn=conn)
    for e in store.recent_events(ids, conn=conn):
        sources = e.get("sources") or []
        if e.get("hidden_reason"):
            hidden.append({"name": names.get(e["entity_id"], ("?",))[0], "title": e["title"],
                           "why": e["hidden_reason"], "url": next((s.get("url") for s in sources
                                                                   if s.get("url")), None)})
            continue
        sc = scores.get(e["id"]) or {}
        name, domain = names.get(e["entity_id"], ("?", "?"))
        events.append({
            "id": e["id"], "entity_id": e["entity_id"], "name": name, "domain": domain,
            "type": e["type"], "label": EVENT_LABELS.get(e["type"], e["type"].replace("_", " ")),
            "status": e["status"], "date": _iso(e["event_date"]), "title": e["title"],
            "summary": e.get("summary"), "url": next((s.get("url") for s in sources
                                                      if s.get("url")), None),
            "detectors": sorted({DETECTOR_LABELS.get(s.get("detector"), s.get("detector"))
                                 for s in sources if s.get("detector")}),
            "evidence": e["evidence_count"], "seen": _iso(e["first_seen_at"]),
            "articles": sum(1 for s in sources if s.get("detector") == "news"),
            "place": (e.get("location") or {}).get("label"),
            "score": sc.get("score"), "severity": sc.get("severity"),
            "feedback": sc.get("feedback")})
    tracked = {}
    for s in store.snapshot_summaries(ids, conn=conn):
        tracked.setdefault(s["entity_id"], {})[s["detector"]] = {
            "items": s["item_count"], "last_read": _iso(s["last_seen_at"]),
            "since": _iso(s["first_seen_at"]), "versions": s["versions"]}
    run = store.latest_run(client_id, owner_email, collect=True, conn=conn)
    last = None
    if run:
        summary = run.get("summary") or {}
        last = {"id": run["id"], "status": run["status"], "stage": run["stage"],
                "error": run["error"], "created_at": _iso(run.get("created_at")),
                "finished_at": _iso(run.get("finished_at")),
                "seconds": summary.get("seconds"), "new_events": summary.get("new_events"),
                "left_out": summary.get("left_out"),
                "news_breaker_open": summary.get("news_breaker_open"),
                "signals_note": (summary.get("signals") or {}).get("note") or
                ((summary.get("signals") or {}).get("error") and
                 "Reading the headlines failed: " + summary["signals"]["error"]),
                "severity": ((summary.get("signals") or {}).get("scored") or {}).get("severity"),
                "demoted": ((summary.get("signals") or {}).get("scored") or {}).get("demoted"),
                "coverage": [{"label": c["label"], "text": c["text"], "read": c["read"],
                              "total": c["total"]} for c in summary.get("coverage") or []],
                "companies": [{"entity_id": c["entity_id"], "domain": c["domain"],
                               "name": c.get("name") or c["domain"], "site": c.get("site"),
                               "error": c.get("error"),
                               "rows": [{"detector": r["detector"],
                                         "label": DETECTOR_LABELS.get(r["detector"],
                                                                      r["detector"]),
                                         "status": r["status"], "note": r.get("note"),
                                         "items": r.get("items"), "events": r.get("events")}
                                        for r in c.get("rows") or []]}
                              for c in summary.get("companies") or []]}
    radar = None
    if run:
        r = (run.get("summary") or {}).get("radar")
        if isinstance(r, dict):
            radar = {}
            for part in ("local", "entrants"):
                x = r.get(part)
                if isinstance(x, dict):
                    radar[part] = {k: x.get(k) for k in ("status", "note", "reused")}
                    radar[part]["left_out"] = ((x.get("coverage") or {}).get("left_out") or [])[:40]
                    radar[part]["findings"] = [
                        {k: f.get(k) for k in ("name", "category", "distance_km", "address",
                                               "website", "status", "certain", "evidence", "date",
                                               "is_competitor", "reason", "location")}
                        for f in x.get("findings") or []][:40]
                    radar[part]["news"] = [{k: h.get(k) for k in ("title", "publisher", "date",
                                                                  "link")}
                                           for h in x.get("news") or []][:25]
            for k in ("skipped", "error"):
                if r.get(k):
                    radar[k] = r[k]
    # Headlines the signal engine judged not to be moves, with its reason,
    # so what was dropped can be checked, not only counted.
    from .market_radar_signals import LEFT_OUT
    for eid in ids:
        memo = ((store.latest_snapshot(eid, "signals", conn=conn) or {}).get("payload") or {}) \
            .get("triaged") or {}
        if not memo:
            continue
        snap = store.latest_snapshot(eid, "news", conn=conn) or {}
        for it in (snap.get("payload") or {}).get("items") or []:
            verdict = memo.get(it.get("id")) or ""
            if verdict.startswith("left_out:"):
                hidden.append({"name": names[eid][0], "title": it.get("title"),
                               "why": "not a move: " + LEFT_OUT.get(verdict[9:], "other"),
                               "url": it.get("link"), "publisher": it.get("publisher"),
                               "date": it.get("date")})
    ranked = sorted((e for e in events if e["score"] is not None),
                    key=lambda e: -e["score"])
    top = [e["id"] for e in ranked if e["severity"] in ("HIGH", "MEDIUM")][:5]
    return {"events": events, "top": top, "hidden": hidden[:400], "hidden_count": len(hidden),
            "last_collect": last, "radar": radar,
            "pulse": pulse_view(client_id, owner_email, conn=conn),
            "competitors": [{"entity_id": i, "name": names[i][0], "domain": names[i][1],
                             "tracked": tracked.get(i, {})} for i in ids]}


THEME_LABELS = {"regulation": "Regulation", "costs_and_prices": "Costs and prices",
                "workforce": "Staffing", "demand": "Demand", "competition_and_deals":
                "Competition and deals", "technology": "Technology", "supply": "Supply",
                "other": "Other"}


def pulse_view(client_id, owner_email, *, conn=None):
    """The latest industry pulse for each of the client's markets
    (tracker/market_radar_pulse), or why there is none. Pulses are shared
    by industry and country, so one may come from another client's run."""
    from . import market_radar_pulse as mp
    c = store.get_client(client_id, owner_email, conn=conn)
    if c is None:
        return None
    profile = effective_profile(c["profile"] or {}, c["settings"])
    key = mp.industry_key(profile)
    if not key:
        return {"skipped": "the industry is not known; set it on the profile first", "markets": []}
    out = []
    for country in mp.markets(profile):
        row = store.latest_pulse(key, country, conn=conn)
        if not row:
            out.append({"country": country, "status": "not_read"})
            continue
        p = row["payload"] or {}
        in_themes = {a.get("link") for t in p.get("themes") or [] for a in t.get("articles") or []}
        cov = p.get("coverage") or {}
        out.append({
            "country": country, "status": p.get("status"), "note": p.get("note"),
            "edition": p.get("edition"), "label": p.get("label"), "read_at": p.get("read_at"),
            "window_days": p.get("window_days"),
            "themes": [dict({k: t.get(k) for k in ("title", "summary", "why_it_matters", "kind",
                                                    "publishers", "latest")},
                            kind_label=THEME_LABELS.get(t.get("kind"), "Other"),
                            articles=(t.get("articles") or [])[:8],
                            more=max(0, len(t.get("articles") or []) - 8))
                       for t in p.get("themes") or []],
            "regulation": (p.get("regulation") or [])[:12],
            "regulation_note": p.get("regulation_note"),
            "other_headlines": [{k: a.get(k) for k in ("title", "publisher", "date", "link")}
                                for a in p.get("articles") or []
                                if a.get("link") not in in_themes][:30],
            "articles": len(p.get("articles") or []),
            "feeds": [f.get("publisher") for f in (p.get("feeds") or {}).get("read") or []],
            "queries": [{k: q.get(k) for k in ("query", "items", "kept", "status", "note")}
                        for q in cov.get("queries") or []],
            "noise_dropped": cov.get("noise_dropped"),
            "off_topic_dropped": cov.get("off_topic_dropped")})
    return {"industry_key": key, "markets": out}


def report_view(client_id, owner_email, *, conn=None):
    """The client's latest report for the report page: the brief, the
    evidence it cites, what the check removed, the collection's cost, and
    the client's current thumbs on each move. None when the client is not
    this person's; {"status": "none"} when no report has been written."""
    c = store.get_client(client_id, owner_email, conn=conn)
    if c is None:
        return None
    row = store.latest_report(client_id, owner_email, conn=conn)
    head = {"client_id": client_id, "domain": c["domain"],
            "name": (effective_profile(c["profile"] or {}, c["settings"]).get("name")
                     or c.get("name") or c["domain"])}
    if not row:
        return dict(head, status="none")
    p = row["payload"] or {}
    out = dict(head, report_id=row["id"], created_at=_iso(row["created_at"]),
               status=p.get("status"), note=p.get("note"), brief=p.get("brief"),
               pack=p.get("pack"), removed=p.get("removed") or [], dropped=p.get("dropped") or [],
               check_note=p.get("check_note"), models=p.get("models"))
    # Thumbs given since the report was written.
    scores = store.client_scores(client_id, conn=conn)
    for m in (out.get("pack") or {}).get("moves") or []:
        m["feedback"] = (scores.get(m.get("event_id")) or {}).get("feedback")
    if row.get("run_id"):
        try:
            from . import market_radar_ledger as ledger
            led = ledger.summary(row["run_id"], conn=conn)
            out["cost"] = {"total_usd": led["total_usd"], "partial": led["partial"],
                           "by_stage": led["by_stage"]}
        except Exception:
            out["cost"] = None
    return out
