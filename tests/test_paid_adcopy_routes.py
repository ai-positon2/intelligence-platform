"""Ad Copy & Creative Generator: the routes. Staff only, same-origin JSON for anything that changes, a daily cap shared with the other agents of this kind, a build belongs to one
agent, and downloads that open."""
import csv
import io
import json
import os
import sys
import zipfile

import openpyxl
import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from tests import adcopy_fakes as A, business_fakes as B, keyword_fakes as K, strategy_fakes as F  # noqa: E402
from tracker import paid_adcopy as pa, paid_gads_segments as gads, paid_strategy as ps, paid_strategy_store as store  # noqa: E402

STAFF, OTHER, ADMIN = "someone@position2.com", "another@position2.com", "reporting@position2.com"
SAME = {"Origin": "http://localhost"}
API = "/p2/paid-marketing/api/adcopy"
KAPI = "/p2/paid-marketing/api/keywords"
SAPI = "/p2/paid-marketing/api/strategy"
BAPI = "/p2/paid-marketing/api/business"
FORM = {"name": "Acme Dental", "website": "acmedental.com", "account": "1234567890", "product": "dental implants and invisalign", "keywords": "dental implants austin, invisalign cost",
        "facts": "Rated 4.9 stars from 312 reviews.", "formats": ["google_rsa", "meta_feed"], "competitors": "Smile Austin", "avoid": "cheap"}


class World:
    def __init__(self):
        self.store = F.FakeStrategyStore()
        self.spawned = []
        self.model = A.Model()


@pytest.fixture
def world(monkeypatch):
    w = World()
    for name in ("create", "get", "get_light", "list_recent", "begin", "set_steps", "set_step", "save_research", "save_plan_part", "save_plan", "finish", "fail", "archive", "touch",
                 "record_run", "runs_today"):
        monkeypatch.setattr(store, name, getattr(w.store, name))
    monkeypatch.setattr(appmod, "_strat_spawn", lambda fn: (w.spawned.append(fn), fn()))
    monkeypatch.setattr(appmod, "_strat_deps", lambda rec, account: A.deps(w.model, account=bool(account), email=rec["created_by"]))
    monkeypatch.setattr(gads, "list_accounts", lambda **kw: [{"id": "1234567890", "name": "Acme Dental", "currency": "USD"}])
    monkeypatch.setattr(appmod, "_STRAT_LAST", {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    return w


def client_as(email):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


def post(c, path, body=None, headers=SAME, api=API):
    return c.post(api + path, data=json.dumps(body if body is not None else {}), content_type="application/json", headers=headers)


def built(c, **form):
    r = post(c, "/start", dict(FORM, **form))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def delete(c, path):
    return c.delete(API + path, headers=SAME, content_type="application/json")


# ── who may use it ──────────────────────────────────────────────────────────

def test_the_page_and_every_api_are_for_position2_staff_only(world):
    anon, outsider = client_as(None), client_as("x@gmail.com")
    for path in ("/p2/paid-marketing/ad-copy", API + "/options", API + "/list", API + "/" + "a" * 32, API + "/" + "a" * 32 + "/status", API + "/" + "a" * 32 + "/download?file=xlsx"):
        assert anon.get(path).status_code == 302, path
        r = outsider.get(path)
        assert r.status_code == 302 and "/app" in r.headers["Location"], path
    assert anon.post(API + "/start", data="{}", content_type="application/json", headers=SAME).status_code == 302
    assert anon.post(API + "/" + "a" * 32 + "/rebuild", data="{}", content_type="application/json", headers=SAME).status_code == 302
    assert anon.delete(API + "/" + "a" * 32, headers=SAME).status_code == 302


def test_options_describe_the_formats_the_angles_and_say_whether_the_model_is_there(world, monkeypatch):
    j = client_as(STAFF).get(API + "/options").get_json()
    assert [f["key"] for f in j["formats"]] == pa.FORMAT_KEYS and j["defaultFormats"] == pa.DEFAULT_FORMATS and j["modelReady"] is True
    rsa = j["formats"][0]
    assert rsa["fields"][0] == {"key": "headlines", "label": "Headlines", "limit": 30, "min": 3, "max": 15, "optional": False} and rsa["paths"] == 15 and rsa["platform"] == "google"
    assert next(f for f in j["formats"] if f["key"] == "meta_feed")["brief"] is True
    assert set(j["angles"]) == set(pa.ANGLES) and j["defaultAngles"] == pa.DEFAULT_ANGLES and j["maxAngles"] == 5 and j["tones"]["plain"] and j["goals"]["leads"]
    assert [c["key"] for c in j["cards"]] == ["website", "account"] and j["buildsPerDay"] == 12
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert client_as(STAFF).get(API + "/options").get_json()["modelReady"] is False


def test_everything_that_changes_needs_same_origin_json(world):
    c = client_as(STAFF)
    assert c.post(API + "/start", data=json.dumps(FORM), content_type="application/json", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(API + "/start", data="name=x", content_type="application/x-www-form-urlencoded", headers=SAME).status_code == 415
    sid = built(c)
    assert c.delete(API + "/" + sid, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(API + "/%s/rebuild" % sid, data="{}", headers=SAME).status_code == 415


# ── starting a build ────────────────────────────────────────────────────────

def test_a_build_is_started_run_and_can_be_read_back(world):
    c = client_as(STAFF)
    r = post(c, "/start", FORM)
    assert r.status_code == 201 and r.get_json()["started"] is True
    sid = r.get_json()["id"]
    rec = c.get(API + "/" + sid).get_json()
    assert rec["status"] == "done" and rec["name"] == "Acme Dental" and rec["accountName"] == "Acme Dental" and rec["createdBy"] == STAFF
    assert rec["inputs"]["kind"] == "adcopy" and rec["inputs"]["account"] == "1234567890" and rec["inputs"]["formats"] == ["google_rsa", "meta_feed"]
    assert rec["plan"]["order"] == ["google_rsa", "meta_feed"] and rec["plan"]["formats"]["google_rsa"]["state"] == "ok" and rec["plan"]["checks"]["totals"]["ready"] > 20
    assert [s["key"] for s in rec["progress"]["steps"]] == ["r:website", "r:account", "a:google_rsa", "a:meta_feed", "f:finish"]
    light = c.get(API + "/%s/status" % sid).get_json()
    assert light["status"] == "done" and "plan" not in light and light["kind"] == "adcopy"


def test_the_page_is_not_sent_the_page_text_the_model_was_given(world):
    c = client_as(STAFF)
    rec = c.get(API + "/" + built(c)).get_json()
    assert rec["research"]["website"]["data"]["pages"] and "text" not in rec["research"]["website"]["data"]["pages"][0]


def test_bad_details_are_refused_with_a_sentence(world):
    c = client_as(STAFF)
    for body, msg in (({"product": "implants"}, "name"), (dict(FORM, product=""), "what the ads are for"), (dict(FORM, goal="fame"), "goal"), (dict(FORM, formats=["tiktok"]), "Unknown ad format"),
                      (dict(FORM, angles=["vibes"]), "Unknown angle"), (dict(FORM, account="act_1234"), "Only a Google Ads account"), ("not an object", "form")):
        r = post(c, "/start", body)
        assert r.status_code == 400 and msg in r.get_json()["error"], body
    r = post(c, "/start", dict(FORM, account="1111111111"))
    assert r.status_code == 400 and "not one this platform can read" in r.get_json()["error"]
    assert world.store.rows == {}


def test_a_build_without_an_account_or_a_website_works(world):
    c = client_as(STAFF)
    rec = c.get(API + "/" + built(c, account="", website="")).get_json()
    assert rec["status"] == "done" and rec["research"] == {} and rec["accountName"] == ""


def test_the_list_shows_this_agents_builds_newest_first_and_not_the_others(world):
    c = client_as(STAFF)
    built(c, name="First Client"), built(c, name="Second Client")
    world.store.create(by=STAFF, inputs=F.clean(name="A Strategy Build"))
    world.store.create(by=STAFF, inputs=B.clean(name="A Business Build"))
    world.store.create(by=STAFF, inputs=K.clean(name="A Keyword Build"))
    rows = client_as(OTHER).get(API + "/list").get_json()["builds"]
    assert [r["name"] for r in rows] == ["Second Client", "First Client"] and rows[0]["status"] == "done" and rows[0]["formats"] == ["Google Search", "Meta feed"] and "plan" not in rows[0]
    assert [r["name"] for r in client_as(OTHER).get(KAPI + "/list").get_json()["builds"]] == ["A Keyword Build"]                     # and each one's list is its own
    assert [r["name"] for r in client_as(OTHER).get(SAPI + "/list").get_json()["builds"]] == ["A Strategy Build"]


def test_a_build_belongs_to_one_agent_whichever_address_is_used(world):
    c = client_as(STAFF)
    mine = built(c)
    others = [world.store.create(by=STAFF, inputs=F.clean())["id"], world.store.create(by=STAFF, inputs=B.clean())["id"], world.store.create(by=STAFF, inputs=K.clean())["id"]]
    for theirs in others:
        for path in ("", "/status", "/download?file=xlsx"):
            assert c.get(API + "/" + theirs + path).status_code == 404, path
        assert post(c, "/%s/rebuild" % theirs, {}).status_code == 404 and delete(c, "/" + theirs).status_code == 404
    for other_api in (SAPI, BAPI, KAPI):
        for path in ("", "/status", "/download?file=xlsx"):
            assert c.get(other_api + "/" + mine + path).status_code == 404, (other_api, path)
        assert c.post(other_api + "/%s/rebuild" % mine, data="{}", headers=SAME, content_type="application/json").status_code == 404
        assert c.delete(other_api + "/" + mine, headers=SAME, content_type="application/json").status_code == 404
    assert world.store.get(mine)["archived"] is False and all(world.store.get(o)["archived"] is False for o in others)


def test_a_person_can_only_start_so_many_builds_a_day_and_the_count_is_shared(world, monkeypatch):
    monkeypatch.setattr(ps, "BUILDS_PER_DAY", 2)
    c = client_as(STAFF)
    built(c)
    world.store.record_run(STAFF)                                                   # a build of another agent counts too: they spend the same credits
    r = post(c, "/start", FORM)
    assert r.status_code == 429 and r.get_json()["kind"] == "cap" and "2 builds in a day" in r.get_json()["error"]
    assert len(client_as(STAFF).get(API + "/list").get_json()["builds"]) == 1       # the refused one is not left in the list
    assert post(client_as(OTHER), "/start", FORM).status_code == 201


def test_only_the_person_who_started_a_build_or_an_admin_can_build_it_again_or_remove_it(world):
    sid = built(client_as(STAFF))
    appmod._STRAT_LAST.clear()
    r = post(client_as(OTHER), "/%s/rebuild" % sid, {"resume": False})
    assert r.status_code == 403 and "Only whoever started this build" in r.get_json()["error"] and world.store.get(sid)["plan"]["formats"]
    assert delete(client_as(OTHER), "/" + sid).status_code == 403
    assert post(client_as(ADMIN), "/%s/rebuild" % sid, {"resume": True}).status_code == 200
    appmod._STRAT_LAST.clear()
    assert post(client_as(STAFF), "/%s/rebuild" % sid, {}).status_code == 200
    assert delete(client_as(STAFF), "/" + sid).get_json() == {"ok": True}
    assert client_as(STAFF).get(API + "/" + sid).status_code == 404 and client_as(STAFF).get(API + "/list").get_json()["builds"] == []


def test_build_again_is_rate_limited_and_resume_keeps_the_work(world):
    c = client_as(STAFF)
    sid = built(c)
    first = post(c, "/%s/rebuild" % sid, {"resume": False})
    assert first.status_code == 200 and first.get_json() == {"id": sid, "started": True}
    again = post(c, "/%s/rebuild" % sid, {"resume": True})
    assert again.status_code == 429 and again.get_json()["kind"] == "cooldown" and again.get_json()["retryIn"] >= 1
    appmod._STRAT_LAST.clear()
    world.model.calls.clear()
    assert post(c, "/%s/rebuild" % sid, {"resume": True}).status_code == 200 and world.model.calls == []        # everything was already written
    assert c.get(API + "/" + sid).get_json()["status"] == "done"


def test_a_build_that_is_running_is_not_started_twice(world):
    rec = world.store.create(by=STAFF, inputs=A.clean())
    world.store.begin(rec["id"])
    r = post(client_as(STAFF), "/%s/rebuild" % rec["id"], {})
    assert r.status_code == 409 and "already running" in r.get_json()["error"]


def test_unknown_ids_are_404(world):
    c = client_as(STAFF)
    assert c.get(API + "/" + "0" * 32 + "/status").status_code == 404 and c.get(API + "/not-an-id").status_code == 404 and c.get(API + "/" + "0" * 32).status_code == 404


def test_a_store_that_is_down_is_said_in_words(world, monkeypatch):
    def down(*a, **k):
        raise store.StoreUnavailable("no db")
    for name in ("list_recent", "create", "get", "get_light"):
        monkeypatch.setattr(store, name, down)
    c = client_as(STAFF)
    assert c.get(API + "/list").status_code == 503 and post(c, "/start", FORM).status_code == 503
    assert c.get(API + "/" + "a" * 32).status_code == 503 and c.get(API + "/" + "a" * 32 + "/status").status_code == 503
    assert "store is unavailable" in c.get(API + "/list").get_json()["error"]


# ── the downloads ───────────────────────────────────────────────────────────

def dl(c, sid, file):
    return c.get(API + "/%s/download?file=%s" % (sid, file))


def test_excel_pdf_zip_and_the_editor_file_download_as_files_that_open(world):
    c = client_as(STAFF)
    sid = built(c)
    x = dl(c, sid, "xlsx")
    assert x.status_code == 200 and x.headers["Content-Type"].startswith("application/vnd.openxmlformats") and 'filename="Acme-Dental-ad-copy.xlsx"' in x.headers["Content-Disposition"]
    assert "Google Search" in openpyxl.load_workbook(io.BytesIO(x.data)).sheetnames and x.headers["Cache-Control"] == "private, no-store"
    p = dl(c, sid, "pdf")
    assert p.status_code == 200 and p.headers["Content-Type"] == "application/pdf" and p.data[:5] == b"%PDF-"
    z = dl(c, sid, "zip")
    assert z.status_code == 200 and "google-search.csv" in zipfile.ZipFile(io.BytesIO(z.data)).namelist() and "all-tables.zip" in z.headers["Content-Disposition"]
    q = dl(c, sid, "csv")
    assert q.status_code == 200 and q.data.startswith(chr(0xFEFF).encode()) and 'ads-editor-search-ad.csv"' in q.headers["Content-Disposition"]
    assert list(csv.reader(io.StringIO(q.data.decode("utf-8-sig"))))[0][:4] == ["Campaign", "Ad group", "Ad type", "Status"]


def test_a_download_that_cannot_be_made_is_said_in_words(world, monkeypatch):
    from tracker import paid_adcopy_export as export
    c = client_as(STAFF)
    sid = built(c)
    assert dl(c, sid, "docx").status_code == 400 and "file should be one of" in dl(c, sid, "docx").get_json()["error"]
    monkeypatch.setattr(export, "pdf", lambda rec: (_ for _ in ()).throw(ValueError("boom")))
    r = dl(c, sid, "pdf")
    assert r.status_code == 500 and r.get_json()["error"] == "The file could not be made (ValueError)."
    only_meta = built(c, formats=["meta_feed"])
    r = dl(c, only_meta, "csv")
    assert r.status_code == 404 and "no search ad to import" in r.get_json()["error"]
    world.store.rows[sid]["plan"] = {}
    for kind in ("xlsx", "pdf", "zip", "csv"):
        assert dl(c, sid, kind).status_code == 409, kind


def test_the_real_dependencies_start_this_agents_own_runner(monkeypatch):
    monkeypatch.undo()
    from tracker import paid_adcopy_job, paid_business_job, paid_keywords_job, paid_strategy_job
    ran = []
    for mod, name in ((paid_adcopy_job, "adcopy"), (paid_business_job, "business"), (paid_keywords_job, "keywords"), (paid_strategy_job, "strategy")):
        monkeypatch.setattr(mod, "run_build", lambda sid, _n=name, **kw: ran.append((_n, sid)))
    fake = F.FakeStrategyStore()
    monkeypatch.setattr(appmod, "_strat_spawn", lambda fn: fn())
    monkeypatch.setattr(appmod, "_pm_user_email", lambda: STAFF)
    for inputs, want in ((A.clean(), "adcopy"), (B.clean(), "business"), (F.clean(), "strategy"), (K.clean(), "keywords")):
        rec = fake.create(by=STAFF, inputs=inputs)
        assert appmod._strat_begin(fake, rec) == (True, "")
        assert ran[-1] == (want, rec["id"])


def test_the_real_dependencies_carry_the_ad_strength_read(monkeypatch):
    monkeypatch.undo()
    from tracker import paid_adstrength
    seen = []
    monkeypatch.setattr(paid_adstrength, "report_for", lambda account_id, **kw: (seen.append(account_id), {"search": {"items": []}})[1])
    monkeypatch.setattr(gads, "list_accounts", lambda **kw: [{"id": "1234567890", "name": "Acme", "currency": "USD"}])
    d = appmod._strat_deps({"created_by": STAFF}, {"id": "1234567890", "name": "Acme", "currency": "USD"})
    assert d["ads_report"]("1234567890") == {"search": {"items": []}} and seen == ["1234567890"] and callable(d["sqr_read"])


def test_a_new_build_is_saved_with_this_agents_own_steps(world, monkeypatch):
    saved = []
    original = world.store.create

    def spy(**kw):
        saved.append(kw)
        return original(**kw)
    monkeypatch.setattr(store, "create", spy)
    built(client_as(STAFF))
    assert [s["key"] for s in saved[0]["steps"]] == ["r:website", "r:account", "a:google_rsa", "a:meta_feed", "f:finish"] and saved[0]["inputs"]["kind"] == "adcopy"
