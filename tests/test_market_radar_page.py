"""Market Radar's profile and competitor edit page: the edit rules
(tracker/market_radar_views), the storage behind them, the admin routes, and
the page script (static/js/market_radar.js) run under Node.

The page shows text taken from other companies' websites and from a model,
so the script tests feed it hostile strings and check none survives as
markup.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import market_radar_views as views  # noqa: E402
from tests.test_market_radar_store import OWNER, OTHER, pg, world  # noqa: E402,F401  (fixtures)

READING = {
    "name": "Acme Dental", "one_liner": "Family dentist.", "archetype": "local_single",
    "customer_type": "B2C", "offerings": ["checkups"], "location_count": 1,
    "hq": {"street": "", "postal_code": "", "city": "Berlin", "region": "Berlin", "country_code": "DE"},
    "markets": ["DE"], "industry": {"plain_label": "Dentist", "keywords": ["zahnarzt"]},
    "price_positioning": "mid", "service_area": "Berlin",
    "hq_point": {"lat": 52.5, "lon": 13.3, "precision": "city", "label": "Berlin"},
    "evidence": [{"field": "name", "quote": "Acme <b>Dental</b>", "url": "https://acme-dental.com/"}],
    "checks": [], "unknowns": [], "facts": {"website": "https://acme-dental.com/"},
}


# == the edit rules ========================================================================

def test_validate_cleans_each_field_and_reports_every_problem():
    clean = views.validate({"name": "  Acme — Dental ", "offerings": "implants, crowns,, implants",
                            "hq.country_code": "uk", "markets": ["de", "at"], "location_count": "3"})
    assert clean == {"name": "Acme, Dental", "offerings": ["implants", "crowns"], "hq.country_code": "GB",
                     "markets": ["DE", "AT"], "location_count": 3}
    with pytest.raises(views.EditError) as e:
        views.validate({"archetype": "spaceship", "hq.country_code": "Germany", "facts.website": "x",
                        "location_count": "many", "name": "x" * 121})
    assert set(e.value.errors) == {"archetype", "hq.country_code", "facts.website", "location_count", "name"}


def test_effective_profile_lays_corrections_over_the_reading_without_changing_it():
    settings = {"profile_overrides": {"hq.city": "Potsdam", "name": "Acme"},
                "hq_point_override": {"lat": 52.4, "lon": 13.0, "precision": "street"}}
    eff = views.effective_profile(READING, settings)
    assert eff["hq"]["city"] == "Potsdam" and eff["name"] == "Acme" and eff["hq"]["country_code"] == "DE"
    assert eff["hq_point"]["precision"] == "street" and eff["edited_fields"] == ["hq.city", "name"]
    assert READING["hq"]["city"] == "Berlin"                     # the shared reading is untouched


@pytest.fixture
def client_with_reading(world):
    from tracker import market_radar_store as store
    store.set_profile(world["entity"], READING)
    return world


def test_edits_are_stored_on_the_client_and_survive_a_new_reading(client_with_reading):
    from tracker import market_radar_store as store
    w = client_with_reading
    notes = views.save_edits(w["client"], OWNER, changes={"name": "Acme Zahnarzt", "industry.plain_label": "Dentist"},
                             locate=lambda p, conn=None: pytest.fail("no address changed"))
    assert notes == []
    c = store.get_client(w["client"], OWNER)
    # Equal to the site's own reading: not a correction.
    assert c["settings"]["profile_overrides"] == {"name": "Acme Zahnarzt"}
    store.set_profile(w["entity"], dict(READING, name="Acme Dental GmbH"))       # the site is re-read
    view = views.client_view(w["client"], OWNER)
    assert view["profile"]["fields"]["name"] == "Acme Zahnarzt"
    assert view["profile"]["site_reading"] == {"name": "Acme Dental GmbH"}
    views.save_edits(w["client"], OWNER, reset=["name"])
    assert views.client_view(w["client"], OWNER)["profile"]["fields"]["name"] == "Acme Dental GmbH"


def test_an_address_edit_relocates_the_business_and_a_miss_keeps_the_old_point(client_with_reading):
    from tracker import market_radar_store as store
    w = client_with_reading
    asked = []

    def locate(profile, conn=None):
        asked.append(profile)
        return {"lat": 52.49, "lon": 13.42, "label": "Kottbusser Damm 1", "precision": "street"}
    notes = views.save_edits(w["client"], OWNER, changes={"hq.street": "Kottbusser Damm 1"}, locate=locate)
    assert asked[0]["hq"]["street"] == "Kottbusser Damm 1" and asked[0]["facts"] == {}
    assert "street" in notes[0]
    point = views.client_view(w["client"], OWNER)["profile"]["hq_point"]
    assert point["precision"] == "street"

    notes = views.save_edits(w["client"], OWNER, changes={"hq.street": "Nowhere 9"},
                             locate=lambda p, conn=None: None)
    assert "not found on the map" in notes[0]
    assert views.client_view(w["client"], OWNER)["profile"]["hq_point"]["precision"] == "street"
    views.save_edits(w["client"], OWNER, reset=["hq.street"])
    c = store.get_client(w["client"], OWNER)
    assert "hq_point_override" not in c["settings"]          # back to the site's own location


@pytest.mark.parametrize("radius, ok", [(5, True), (0.5, True), (25, True), (0.2, False), (40, False), ("x", False)])
def test_radius_bounds(client_with_reading, radius, ok):
    w = client_with_reading
    if ok:
        views.save_edits(w["client"], OWNER, radius_km=radius)
        assert views.client_view(w["client"], OWNER)["client"]["radius_km"] == float(radius)
    else:
        with pytest.raises(views.EditError):
            views.save_edits(w["client"], OWNER, radius_km=radius)


def test_a_radius_can_be_cleared(client_with_reading):
    w = client_with_reading
    views.save_edits(w["client"], OWNER, radius_km=4)
    views.save_edits(w["client"], OWNER, radius_km=None)
    assert views.client_view(w["client"], OWNER)["client"]["radius_km"] is None


def test_another_users_client_cannot_be_read_or_edited(client_with_reading):
    w = client_with_reading
    assert views.client_view(w["client"], OTHER) is None
    with pytest.raises(KeyError):
        views.save_edits(w["client"], OTHER, changes={"name": "x"})


# == storage =================================================================================

def test_competitor_details_are_kept_and_a_hand_added_one_is_confirmed(client_with_reading):
    from tracker import market_radar_store as store
    w = client_with_reading
    rival = store.upsert_entity("rival.example", name="Rival")
    store.propose_competitor(w["client"], OWNER, rival, "local", confidence=0.8, found_via=["map"],
                             details={"reason": "Next door.", "distance_km": 0.3, "score": 80})
    store.propose_competitor(w["client"], OWNER, rival, "local", confidence=0.7, found_via=["map"],
                             details={"score": 70})
    [row] = views.competitor_rows(w["client"], OWNER)
    assert row["reason"] == "Next door." and row["score"] == 70     # details merge, newest wins
    mine = store.upsert_entity("mine.example")
    store.set_competitor_status(w["client"], OWNER, rival, "removed")
    store.add_competitor(w["client"], OWNER, rival, "direct")           # re-adding a removed one
    store.add_competitor(w["client"], OWNER, mine, "indirect")
    rows = {r["domain"]: r for r in views.competitor_rows(w["client"], OWNER)}
    assert rows["rival.example"]["status"] == "confirmed" and rows["rival.example"]["kind"] == "direct"
    assert rows["mine.example"]["added_by_user"] and rows["mine.example"]["found"] == ["added by you"]
    with pytest.raises(PermissionError):
        store.add_competitor(w["client"], OTHER, mine, "direct")


def test_client_list_counts_and_shows_the_latest_run(client_with_reading):
    from tracker import market_radar_store as store
    w = client_with_reading
    a, b = store.upsert_entity("a.example"), store.upsert_entity("b.example")
    store.propose_competitor(w["client"], OWNER, a, "direct")
    store.add_competitor(w["client"], OWNER, b, "direct")
    store.update_run(w["run"], status="running", stage="rivals_search")
    [c] = views.client_list(OWNER)
    assert (c["confirmed"], c["proposed"], c["removed"]) == (1, 1, 0)
    assert c["last_run"]["status"] == "running" and c["name"] == "Acme Dental"
    assert views.client_list(OTHER) == []


# == discovery uses the corrections ==========================================================

def test_the_background_run_searches_with_the_users_corrections_and_radius(client_with_reading):
    from tracker import market_radar_run as mrun
    w = client_with_reading
    views.save_edits(w["client"], OWNER, changes={"archetype": "multi_location"}, radius_km=7)
    seen = {}

    def found(profile, run_id=None, client=None, progress=None, radius_km=None):
        seen.update(archetype=profile["archetype"], radius=radius_km, edited=profile["edited_fields"])
        return {"status": "none_found", "competitors": [], "coverage": {}}
    mrun.job(w["run"], "https://acme-dental.com/", OWNER, w["client"], w["entity"], True, discover=found)
    assert seen == {"archetype": "multi_location", "radius": 7.0, "edited": ["archetype"]}


def test_a_users_radius_is_not_widened():
    from tracker import market_radar_rivals as rv
    from tests.test_market_radar_rivals import FakePlaces, PROFILE, place
    fp = FakePlaces([], [place("One", "https://one.example", 2)], wider=[])
    meta = rv.from_places(rv.Pool("acme.example"), PROFILE,
                          {"place_terms": ["dent"], "radius_km": 3, "radius_set_by_user": True}, places=fp)
    assert meta["radius_km"] == 3 and "widened" not in meta and len(fp.calls) == 2


# == routes ==================================================================================

ADMIN = "reporting@position2.com"
BASE = "/p2/admin/market-radar"


def _client(email):
    import app as appmod
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.mark.parametrize("method, path", [
    ("get", BASE), ("get", BASE + "/api/clients"), ("get", BASE + "/api/clients/1"),
    ("post", BASE + "/api/clients/1/profile"), ("post", BASE + "/api/clients/1/competitors"),
    ("post", BASE + "/api/clients/1/competitors/2"), ("post", BASE + "/api/runs"),
    ("get", BASE + "/api/runs/1"), ("post", BASE + "/api/clients/1/collect"),
    ("get", BASE + "/api/clients/1/moves"),
    ("post", "/p2/admin/external-usage/market-radar-detectors-check")])
@pytest.mark.parametrize("email, status", [(None, 302), ("someone@position2.com", 403)])
def test_every_route_is_admin_only(method, path, email, status):
    c = _client(email)
    resp = getattr(c, method)(path, json={}) if method == "post" else c.get(path)
    assert resp.status_code == status


@pytest.mark.parametrize("path", ["/api/clients/1/profile", "/api/clients/1/competitors",
                                  "/api/clients/1/competitors/2", "/api/runs",
                                  "/api/clients/1/collect", "/api/tidy-events"])
def test_posts_from_another_site_are_refused(path):
    resp = _client(ADMIN).post(BASE + path, json={}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_the_detector_self_test_refuses_another_site():
    resp = _client(ADMIN).post("/p2/admin/external-usage/market-radar-detectors-check", json={},
                               headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_a_collection_starts_once_and_its_moves_are_read_back(client_with_reading, monkeypatch):
    import app as appmod
    from tracker import market_radar_run as mrun
    from tracker import market_radar_store as store
    monkeypatch.setattr(appmod, "ADMIN_EMAILS", set(appmod.ADMIN_EMAILS) | {OWNER})
    started = []
    monkeypatch.setattr(mrun, "collect_job", lambda *a, **k: started.append(a))
    real_start = mrun.start_collect
    monkeypatch.setattr(mrun, "start_collect",
                        lambda cid, email: real_start(cid, email, spawn=lambda f, a: f(*a)))
    w = client_with_reading
    rival = store.upsert_entity("rival.example", name="Rival")
    store.propose_competitor(w["client"], OWNER, rival, "direct", confidence=0.7)
    c = _client(OWNER)
    r = c.post(BASE + "/api/clients/%d/collect" % w["client"], json={})
    assert r.status_code == 202 and len(started) == 1
    run_id = r.get_json()["run_id"]
    r = c.post(BASE + "/api/clients/%d/collect" % w["client"], json={})
    assert r.status_code == 409 and r.get_json()["run_id"] == run_id and len(started) == 1
    assert c.post(BASE + "/api/clients/999999/collect", json={}).status_code == 404
    # The search panel still shows the search, not the collection.
    assert c.get(BASE + "/api/clients/%d" % w["client"]).get_json()["last_run"]["id"] == w["run"]

    store.record_event(rival, "locations:loc+:x", type="new_location", title="New location page: Merced",
                       source={"url": "https://rival.example/l/merced", "detector": "locations"},
                       event_date="2026-10-09")
    store.record_event(rival, "locations:loc+:x", type="new_location", title="New location page: Merced",
                       source={"url": "https://news.example/a", "detector": "news"})
    store.save_snapshot(rival, "locations", {"places": ["rival.example/l/merced"]}, item_count=1)
    store.update_run(run_id, status="complete", stage="done", summary={
        "seconds": 12.5, "new_events": 1, "left_out": 0, "coverage": [
            {"label": "Locations", "text": "Locations read for 1 of 1 competitors.", "read": 1, "total": 1}],
        "companies": [{"entity_id": rival, "domain": "rival.example", "name": "Rival",
                       "site": {"status": "ok"}, "rows": [{"detector": "locations", "status": "ok",
                                                          "note": "1 location pages", "items": 1}]}]})
    m = c.get(BASE + "/api/clients/%d/moves" % w["client"]).get_json()
    (ev,) = m["events"]
    assert ev["label"] == "New location" and ev["name"] == "Rival" and ev["date"] == "2026-10-09"
    assert ev["detectors"] == ["Locations", "News"] and ev["evidence"] == 2
    assert m["last_collect"]["coverage"][0]["text"].startswith("Locations read for 1 of 1")
    assert m["competitors"][0]["tracked"]["locations"]["items"] == 1
    assert _client(OWNER).get(BASE + "/api/clients/999999/moves").status_code == 404


def test_the_page_renders_with_its_script_and_menu_entry():
    html = _client(ADMIN).get(BASE).get_data(as_text=True)
    assert "js/market_radar.js" in html and "css/market_radar.css" in html
    assert 'href="/p2/admin/market-radar"' in html


def test_routes_edit_flow(client_with_reading, monkeypatch):
    from tracker import market_radar_profile as prof
    monkeypatch.setattr(prof, "geocode", lambda q, conn=None: None)
    import app as appmod
    monkeypatch.setattr(appmod, "ADMIN_EMAILS", set(appmod.ADMIN_EMAILS) | {OWNER})
    c = _client(OWNER)
    w = client_with_reading
    lst = c.get(BASE + "/api/clients")
    assert lst.get_json()["clients"][0]["client_id"] == w["client"]
    r = c.post(BASE + "/api/clients/%d/profile" % w["client"], json={"changes": {"archetype": "nope"}})
    assert r.status_code == 400 and "archetype" in r.get_json()["fields"]
    r = c.post(BASE + "/api/clients/%d/profile" % w["client"], json={"changes": {"name": "Acme Z"}})
    assert r.status_code == 200 and r.get_json()["view"]["profile"]["fields"]["name"] == "Acme Z"
    r = c.post(BASE + "/api/clients/%d/competitors" % w["client"], json={"url": "acme-dental.com"})
    assert r.status_code == 400 and "own website" in r.get_json()["error"]
    r = c.post(BASE + "/api/clients/%d/competitors" % w["client"], json={"url": "https://www.rival.example/x", "kind": "local"})
    [row] = r.get_json()["competitors"]
    assert row["domain"] == "rival.example" and row["status"] == "confirmed"
    r = c.post(BASE + "/api/clients/%d/competitors/%d" % (w["client"], row["entity_id"]), json={"kind": "indirect"})
    assert r.get_json()["competitors"][0]["kind"] == "indirect" and r.get_json()["competitors"][0]["status"] == "confirmed"
    r = c.post(BASE + "/api/clients/%d/competitors/%d" % (w["client"], 999999), json={"status": "removed"})
    assert r.status_code == 404
    assert c.post(BASE + "/api/runs", json={"client_id": w["client"]}).status_code == 400   # no confirm_spend


def test_a_second_run_for_the_same_company_is_refused_while_one_is_going(client_with_reading, monkeypatch):
    import app as appmod
    from tracker import market_radar_run as mrun
    from tracker import market_radar_store as store
    monkeypatch.setattr(appmod, "ADMIN_EMAILS", set(appmod.ADMIN_EMAILS) | {OWNER})
    monkeypatch.setattr(mrun, "start", lambda *a, **k: pytest.fail("started a second paid run"))
    w = client_with_reading
    store.update_run(w["run"], status="running", stage="rivals_search")
    c = _client(OWNER)
    r = c.post(BASE + "/api/runs", json={"client_id": w["client"], "confirm_spend": True})
    assert r.status_code == 409 and r.get_json()["run_id"] == w["run"]


# == the page script under Node ==============================================================

JS = os.path.join(ROOT, "static", "js", "market_radar.js")
EVIL = '<img src=x onerror=alert(1)>"\'&'


def run_js(expr):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    prog = ("globalThis.window = globalThis; require(%s); const MR = globalThis.MR;\n"
            "process.stdout.write(JSON.stringify((() => { %s })()));") % (json.dumps(JS), expr)
    proc = subprocess.run([shutil.which("node"), "-e", prog], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _no_markup_from(html):
    assert "<img" not in html and "onerror=alert(1)>" not in html.replace("&gt;", "")


def test_script_escapes_everything_from_websites_and_the_model():
    view = {"client": {"id": 1, "domain": "acme.example", "name": EVIL, "radius_km": None},
            "profile": {"has_reading": True, "fields": {"name": EVIL, "archetype": "local_single",
                                                        "offerings": [EVIL], "hq.city": EVIL},
                        "edited": ["name"], "site_reading": {"name": EVIL}, "checks": [EVIL],
                        "unknowns": [EVIL], "archetype_reason": EVIL,
                        "evidence": [{"field": "name", "quote": EVIL, "url": "javascript:alert(1)"}],
                        "hq_point": {"precision": "city", "label": EVIL}},
            "competitors": [{"entity_id": 2, "domain": "javascript:alert(1)", "name": EVIL, "kind": "local",
                             "status": "proposed", "reason": EVIL, "location": EVIL, "found": [EVIL],
                             "checked_on": EVIL, "score": 80, "distance_km": 0.4}],
            "last_run": {"id": 3, "status": "failed", "error": EVIL, "unread": [{"domain": EVIL, "why": EVIL}],
                         "gaps": [EVIL], "left_out": [{"domain": EVIL, "why": EVIL}],
                         "sources": {"search": {"label": "Google search", "status": "failed", "error": EVIL}}}}
    out = run_js("const v = %s; return [MR.renderDetail({view: v, editing: false}), "
                 "MR.renderDetail({view: v, editing: true, errors: {name: %s}}), "
                 "MR.renderClientList([{client_id: 1, domain: 'a', name: %s, confirmed: 1}], 1)];"
                 % (json.dumps(view), json.dumps(EVIL), json.dumps(EVIL)))
    for html in out:
        _no_markup_from(html)
    import re
    hrefs = re.findall(r'href="([^"]*)"', out[0])
    assert not [h for h in hrefs if "javascript" in h or "alert" in h], hrefs
    assert "&lt;img src=x onerror=alert(1)&gt;" in out[0]


def test_script_shows_corrections_progress_and_counts():
    out = run_js("""
      const view = {client: {id: 1, domain: 'acme.example', radius_km: 4},
        profile: {has_reading: true, fields: {name: 'Acme', archetype: 'local_single', location_count: -1,
                  'hq.city': 'Berlin'}, edited: ['name'], site_reading: {name: 'Acme Dental'},
                  hq_point: {precision: 'city', label: 'Berlin'}},
        competitors: [{entity_id: 1, domain: 'a.example', name: 'A', kind: 'direct', status: 'proposed'},
                      {entity_id: 2, domain: 'b.example', name: 'B', kind: 'local', status: 'proposed'},
                      {entity_id: 3, domain: 'c.example', name: 'C', kind: 'direct', status: 'confirmed'},
                      {entity_id: 4, domain: 'd.example', name: 'D', kind: 'direct', status: 'removed'}],
        last_run: null};
      return {
        page: MR.renderDetail({view: view}),
        removed: MR.renderCompetitors(view.competitors, 'removed'),
        progress: MR.renderProgress({stage: 'rivals_verify', cost_usd: 0.031}),
        running: MR.renderDetail({view: view, run: {status: 'running', stage: 'profile'}}),
      };""")
    page = out["page"]
    assert "Your correction" in page and "The website said: Acme Dental" in page
    assert "To review 2" in page and "Confirmed 1" in page and "Removed 1" in page
    assert "Confirm all 2" in page and 'data-act="radius-auto"' in page
    assert "Add the street address" in page and "Not stated" in page        # location_count -1
    assert "Restore" in out["removed"] and "D" in out["removed"]
    assert out["progress"].count("mr-step done") == 3 and "Checking sites" in out["progress"]
    assert "$0.031" in out["progress"]
    assert "Running…" in out["running"] and "disabled" in out["running"]


def test_script_sends_only_the_fields_that_changed():
    out = run_js("""
      const cur = {name: 'Acme', offerings: ['a', 'b'], location_count: -1, 'hq.city': 'Berlin', archetype: 'local_single'};
      return [MR.formChanges({name: 'Acme', offerings: 'a, b', location_count: '', 'hq.city': 'Berlin', archetype: 'local_single'}, cur),
              MR.formChanges({name: ' Acme 2 ', offerings: 'a,\\nc', location_count: '3', 'hq.city': '', archetype: 'ecommerce'}, cur)];""")
    assert out[0] == {}
    assert out[1] == {"name": "Acme 2", "offerings": ["a", "c"], "location_count": 3, "hq.city": "",
                      "archetype": "ecommerce"}


def test_script_stage_steps_cover_every_stage_the_run_reports():
    import re
    from tracker import market_radar_rivals as rv
    src = open(os.path.join(ROOT, "tracker", "market_radar_rivals.py")).read() + \
        open(os.path.join(ROOT, "tracker", "market_radar_run.py")).read()
    stages = set(re.findall(r'(?:say|stage)\("([a-z_]+)"\)', src)) | {"queued", "done"}
    steps = run_js("return Object.keys(MR.STAGE_STEP);")
    assert stages <= set(steps), stages - set(steps)
    assert rv  # imported for the source path


def test_rows_saved_before_details_existed_borrow_them_from_their_run(client_with_reading):
    from tracker import market_radar_store as store
    w = client_with_reading
    rival = store.upsert_entity("rival.example", name="Rival")
    store.propose_competitor(w["client"], OWNER, rival, "local", confidence=0.8, found_via=["map"])
    store.update_run(w["run"], status="complete", summary={"result": {"competitors": [
        {"domain": "rival.example", "reason": "Next door.", "distance_km": 0.3, "score": 80,
         "checked_on": "its own homepage"}]}})
    [row] = views.competitor_rows(w["client"], OWNER)
    assert row["reason"] == "Next door." and row["score"] == 80 and row["checked_on"] == "its own homepage"



@pytest.mark.parametrize("status, search, retired", [("ok", "ok", True), ("ok", "partial", False),
                                                     ("ok", "failed", False)])
def test_a_full_search_replaces_old_suggestions_but_never_a_users_decision(client_with_reading, status, search, retired):
    from tracker import market_radar_rivals as rv
    from tracker import market_radar_store as store
    w = client_with_reading
    ids = {d: store.upsert_entity(d) for d in ("old.example", "kept.example", "confirmed.example",
                                                "removed.example", "mine.example")}
    for d in ("old.example", "kept.example", "confirmed.example", "removed.example"):
        store.propose_competitor(w["client"], OWNER, ids[d], "direct")
    store.set_competitor_status(w["client"], OWNER, ids["confirmed.example"], "confirmed")
    store.set_competitor_status(w["client"], OWNER, ids["removed.example"], "removed")
    store.add_competitor(w["client"], OWNER, ids["mine.example"], "direct")
    result = {"status": status, "coverage": {"search": {"status": search}},
              "competitors": [{"domain": "kept.example", "kind": "direct", "score": 70, "found": []}]}
    rv.save(result, client_id=w["client"], owner_email=OWNER)
    left = {r["domain"]: r["status"] for r in store.competitors(w["client"], OWNER, include_removed=True)}
    assert ("old.example" not in left) is retired
    assert left["kept.example"] == "proposed" and left["confirmed.example"] == "confirmed"
    assert left["removed.example"] == "removed" and left["mine.example"] == "confirmed"


def test_test_records_are_not_listed(pg):
    from tracker import market_radar_store as store
    store.upsert_client(OWNER, store.upsert_entity("mr-phase0-test.example"))
    store.upsert_client(OWNER, store.upsert_entity("real-company.com"))
    assert [c["domain"] for c in views.client_list(OWNER)] == ["real-company.com"]


def test_tidy_keeps_only_the_latest_full_searchs_suggestions(client_with_reading, monkeypatch):
    import app as appmod
    from tracker import market_radar_store as store
    monkeypatch.setattr(appmod, "ADMIN_EMAILS", set(appmod.ADMIN_EMAILS) | {OWNER})
    w = client_with_reading
    old, new, mine = (store.upsert_entity(d) for d in ("old.example", "new.example", "mine.example"))
    for e in (old, new):
        store.propose_competitor(w["client"], OWNER, e, "direct")
    store.add_competitor(w["client"], OWNER, mine, "direct")
    store.update_run(w["run"], status="complete", summary={"saved_entities": [new], "result": {
        "status": "ok", "coverage": {"search": {"status": "ok"}}}})
    r = _client(OWNER).post(BASE + "/api/tidy-suggestions", json={})
    assert r.get_json()["dropped"] == {"acme-dental.com": 1}
    left = {x["domain"] for x in store.competitors(w["client"], OWNER)}
    assert left == {"new.example", "mine.example"}
    assert _client(OWNER).post(BASE + "/api/tidy-suggestions", json={},
                               headers={"Origin": "https://evil.example"}).status_code == 403


def test_script_shows_moves_and_escapes_them():
    moves = {"events": [{"id": 1, "entity_id": 2, "name": EVIL, "domain": "r.example", "type": "new_location",
                         "label": EVIL, "status": "unknown", "date": "2026-10-09", "title": EVIL,
                         "summary": EVIL, "url": "javascript:alert(1)", "detectors": [EVIL], "evidence": 2}],
             "competitors": [{"entity_id": 2, "name": "R", "domain": "r.example", "tracked": {}}],
             "last_collect": {"id": 5, "status": "complete", "finished_at": "2026-10-09T10:00:00",
                              "coverage": [{"label": "Hiring", "text": EVIL, "read": 1, "total": 2}],
                              "left_out": 3, "news_breaker_open": True,
                              "companies": [{"entity_id": 2, "name": EVIL, "domain": EVIL,
                                             "site": {"status": "blocked", "note": EVIL},
                                             "rows": [{"detector": "jobs", "label": "Hiring", "status": "failed",
                                                       "note": EVIL}]}]}}
    out = run_js("""const m = %s; return {
        full: MR.renderMoves(m, null),
        running: MR.renderMoves(m, {status: 'running', stage: 'collect 3/10'}),
        none: MR.renderMoves({events: [], competitors: [], last_collect: null}, null),
        quiet: MR.renderMoves({events: [], competitors: [{}, {}], last_collect: {status: 'complete',
                               coverage: []}}, null)};""" % json.dumps(moves))
    _no_markup_from(out["full"])
    import re
    assert not [h for h in re.findall(r'href="([^"]*)"', out["full"]) if "javascript" in h]
    assert "2 independent sources" in out["full"] and "3 more competitors were not collected" in out["full"]
    assert "Google News stopped answering" in out["full"] and "Could not read" in out["full"]
    assert "Read 3 of 10 competitors" in out["running"] and "disabled" in out["running"]
    assert "Nothing collected yet" in out["none"] and "Collect now" in out["none"]
    assert "No moves yet" in out["quiet"] and "2 competitors are tracked" in out["quiet"]


def test_script_shows_the_latest_moves_and_folds_the_rest():
    ev = lambda i: {"id": i, "name": "R", "type": "promotion", "label": "New offer", "title": "T%d" % i,
                    "date": "2026-10-01", "detectors": [], "evidence": 1}
    html = run_js("return MR.renderMoves(%s, null);" % json.dumps(
        {"events": [ev(i) for i in range(30)], "competitors": [], "last_collect": {"status": "complete"}}))
    assert "5 earlier moves" in html and html.index("T24") < html.index("earlier moves") < html.index("T25")


def test_script_shows_the_radar_and_escapes_it():
    f = {"name": EVIL, "category": "dental_clinic", "distance_km": 1.2, "address": EVIL,
         "website": "javascript:alert(1)", "status": "planned", "certain": True, "evidence": [EVIL],
         "date": "2026-09-25", "is_competitor": True}
    weak = dict(f, name="Weak", certain=False, status="unknown", website="https://weak.example/")
    radar = {"local": {"status": "ok", "note": EVIL, "findings": [f, weak],
                       "news": [{"title": EVIL, "publisher": EVIL, "date": "2026-09-25",
                                 "link": "javascript:alert(1)"}]},
             "entrants": {"status": "ok", "note": "n", "findings": [], "news": [],
                          "left_out": [{"name": EVIL, "domain": "x.example", "why": EVIL}]}}
    out = run_js("return [MR.renderRadar(%s), MR.renderRadar({skipped: 'b2b'}), MR.renderRadar(null),"
                 "MR.renderRadar({local: {status: 'ok', note: 'n', findings: [], news: []}})];"
                 % json.dumps(radar))
    _no_markup_from(out[0])
    import re
    assert not [h for h in re.findall(r'href="([^"]*)"', out[0]) if "javascript" in h]
    assert "Coming soon" in out[0] and "Possibly new" in out[0] and "On your competitor list" in out[0]
    assert "1.2 km away" in out[0] and "Local opening news (1)" in out[0]
    assert "Considered and left out (1)" in out[0]
    assert "b2b" in out[1] and out[2] == "" and "Nothing new found" in out[3]
