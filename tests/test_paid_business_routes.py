"""Business Research: the routes. Staff only, same-origin JSON for anything that changes, a daily cap shared with Strategy & Campaign Book, a build belongs to
one agent, and downloads that open."""
import csv
import io
import json
import os
import re
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
from tests import business_fakes as B, strategy_fakes as F  # noqa: E402
from tracker import paid_business as pb, paid_gads_segments as gads, paid_strategy as ps, paid_strategy_store as store  # noqa: E402

STAFF, OTHER, ADMIN = "someone@position2.com", "another@position2.com", "reporting@position2.com"
SAME = {"Origin": "http://localhost"}
API = "/p2/paid-marketing/api/business"
SAPI = "/p2/paid-marketing/api/strategy"
FORM = {"name": "Acme Dental", "website": "acmedental.com", "account": "1234567890", "locations": "Austin, TX", "services": "dental implants", "research": {"social": False}}


class World:
    def __init__(self):
        self.store = F.FakeStrategyStore()
        self.spawned = []
        self.sci = F.FakeSci(runs={})


@pytest.fixture
def world(monkeypatch):
    w = World()
    for name in ("create", "get", "get_light", "list_recent", "begin", "set_steps", "set_step", "save_research", "save_plan_part", "save_plan", "finish", "fail", "archive", "touch",
                 "record_run", "runs_today"):
        monkeypatch.setattr(store, name, getattr(w.store, name))
    monkeypatch.setattr(appmod, "_strat_spawn", lambda fn: (w.spawned.append(fn), fn()))
    monkeypatch.setattr(appmod, "_strat_deps", lambda rec, account: B.deps(sci=w.sci, account=bool(account), email=rec["created_by"]))
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


def post(c, path, body=None, headers=SAME):
    return c.post(API + path, data=json.dumps(body if body is not None else {}), content_type="application/json", headers=headers)


def built(c, **form):
    r = post(c, "/start", dict(FORM, **form))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def delete(c, path):
    return c.delete(API + path, headers=SAME, content_type="application/json")


# ── who may use it ──────────────────────────────────────────────────────────

def test_the_page_and_every_api_are_for_position2_staff_only(world):
    anon, outsider = client_as(None), client_as("x@gmail.com")
    for path in ("/p2/paid-marketing/business-research", API + "/options", API + "/list", API + "/" + "a" * 32, API + "/" + "a" * 32 + "/status", API + "/" + "a" * 32 + "/download?file=xlsx"):
        assert anon.get(path).status_code == 302, path
        r = outsider.get(path)
        assert r.status_code == 302 and "/app" in r.headers["Location"], path
    assert anon.post(API + "/start", data="{}", content_type="application/json", headers=SAME).status_code == 302


def test_the_page_renders_for_staff(world):
    html = client_as(STAFF).get("/p2/paid-marketing/business-research").get_data(as_text=True)
    assert "<title>Business Research" in html and "business_research.js" in html and "business_research.css" in html and 'id="cards"' in html and "pm_picker.js" in html


def test_options_describe_the_cards_and_say_whether_the_model_is_there(world, monkeypatch):
    j = client_as(STAFF).get(API + "/options").get_json()
    assert [c["key"] for c in j["cards"]] == ["website", "reputation", "social", "history", "competitors", "market"] and j["modelReady"] is True
    assert j["goals"]["leads"] and j["buildsPerDay"] == 12 and "kinds" not in j
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
    assert rec["inputs"]["kind"] == "business" and rec["inputs"]["currency"] == "USD" and rec["inputs"]["account"] == "1234567890"
    assert rec["plan"]["core"]["offerings"] and rec["plan"]["audience"]["audiences"] and rec["plan"]["implications"]["questions"]
    assert "text" not in rec["research"]["website"]["data"]["pages"][0] and rec["research"]["website"]["data"]["tech"]["pages"] >= 5 and rec["research"]["reputation"]["data"]["ratings"]
    assert [s["key"] for s in rec["progress"]["steps"]][-4:] == ["a:core", "a:audience", "a:implications", "f:finish"] and "r:social" not in [s["key"] for s in rec["progress"]["steps"]]
    light = c.get(API + "/%s/status" % sid).get_json()
    assert light["status"] == "done" and "plan" not in light


def test_bad_details_are_refused_with_a_sentence(world):
    c = client_as(STAFF)
    for body, msg in (({"website": "x.com"}, "name"), (dict(FORM, goal="fame"), "goal"), (dict(FORM, competitors=["a.com", "b.com", "c.com", "d.com"]), "at most 3"), ("not an object", "form")):
        r = post(c, "/start", body)
        assert r.status_code == 400 and msg in r.get_json()["error"], body
    r = post(c, "/start", dict(FORM, account="1111111111"))
    assert r.status_code == 400 and "not one this platform can read" in r.get_json()["error"]
    assert world.store.rows == {}


def test_a_build_without_an_account_works_and_takes_the_currency_given(world):
    c = client_as(STAFF)
    rec = c.get(API + "/" + built(c, account="", currency="cad")).get_json()
    assert rec["inputs"]["currency"] == "CAD" and rec["status"] == "done" and "history" not in rec["research"]


def test_the_list_shows_this_agents_builds_newest_first_and_not_the_other_agents(world):
    c = client_as(STAFF)
    built(c, name="First Client"), built(c, name="Second Client")
    other = world.store.create(by=STAFF, inputs=F.clean(name="A Strategy Build"))
    rows = client_as(OTHER).get(API + "/list").get_json()["builds"]
    assert [r["name"] for r in rows] == ["Second Client", "First Client"] and rows[0]["status"] == "done" and rows[0]["createdBy"] == STAFF and "campaigns" not in rows[0]
    assert [r["name"] for r in client_as(OTHER).get(SAPI + "/list").get_json()["builds"]] == ["A Strategy Build"]                   # and each one's list is its own
    assert other["id"] not in [r["id"] for r in rows]


def test_a_build_belongs_to_one_agent_whichever_address_is_used(world):
    c = client_as(STAFF)
    mine = built(c)
    theirs = world.store.create(by=STAFF, inputs=F.clean())["id"]
    for path in ("", "/status", "/download?file=xlsx"):
        assert c.get(API + "/" + theirs + path).status_code == 404, path
        assert c.get(SAPI + "/" + mine + path).status_code == 404, path
    assert c.get(API + "/" + mine + "/status").status_code == 200 and c.get(SAPI + "/" + theirs + "/status").status_code == 200
    assert post(c, "/%s/rebuild" % theirs, {}).status_code == 404 and delete(c, "/" + theirs).status_code == 404
    assert c.post(SAPI + "/%s/rebuild" % mine, data="{}", headers=SAME, content_type="application/json").status_code == 404
    assert c.delete(SAPI + "/" + mine, headers=SAME, content_type="application/json").status_code == 404
    assert world.store.get(mine)["archived"] is False and world.store.get(theirs)["archived"] is False


def test_a_person_can_only_start_so_many_builds_a_day_and_the_count_is_shared(world, monkeypatch):
    monkeypatch.setattr(ps, "BUILDS_PER_DAY", 2)
    c = client_as(STAFF)
    built(c)
    world.store.record_run(STAFF)                                                   # a Strategy & Campaign Book build counts too: they spend the same credits
    r = post(c, "/start", FORM)
    assert r.status_code == 429 and r.get_json()["kind"] == "cap" and "2 builds in a day" in r.get_json()["error"]
    assert len(client_as(STAFF).get(API + "/list").get_json()["builds"]) == 1       # the refused one is not left in the list
    assert post(client_as(OTHER), "/start", FORM).status_code == 201


def test_only_the_person_who_started_a_build_or_an_admin_can_build_it_again_or_remove_it(world):
    sid = built(client_as(STAFF))
    appmod._STRAT_LAST.clear()
    r = post(client_as(OTHER), "/%s/rebuild" % sid, {"resume": False})
    assert r.status_code == 403 and "Only whoever started this build" in r.get_json()["error"] and world.store.get(sid)["plan"]["core"]
    assert delete(client_as(OTHER), "/" + sid).status_code == 403
    assert post(client_as(ADMIN), "/%s/rebuild" % sid, {"resume": True}).status_code == 200
    appmod._STRAT_LAST.clear()
    assert post(client_as(STAFF), "/%s/rebuild" % sid, {}).status_code == 200
    assert delete(client_as(STAFF), "/" + sid).get_json() == {"ok": True}
    assert client_as(STAFF).get(API + "/" + sid).status_code == 404 and client_as(STAFF).get(API + "/list").get_json()["builds"] == []
    other = built(client_as(STAFF))
    assert delete(client_as(ADMIN), "/" + other).status_code == 200


def test_build_again_is_rate_limited_and_resume_keeps_the_work(world):
    c = client_as(STAFF)
    sid = built(c)
    first = post(c, "/%s/rebuild" % sid, {"resume": False})
    assert first.status_code == 200 and first.get_json() == {"id": sid, "started": True}
    again = post(c, "/%s/rebuild" % sid, {"resume": True})
    assert again.status_code == 429 and again.get_json()["kind"] == "cooldown" and again.get_json()["retryIn"] >= 1
    appmod._STRAT_LAST.clear()
    calls = []
    appmod._strat_deps = lambda rec, account: B.deps(sci=world.sci, account=bool(account), call=B.model(calls=calls), email=rec["created_by"])
    assert post(c, "/%s/rebuild" % sid, {"resume": True}).status_code == 200 and calls == []        # everything was already done
    assert c.get(API + "/" + sid).get_json()["status"] == "done"


def test_a_build_that_is_running_is_not_started_twice(world):
    rec = world.store.create(by=STAFF, inputs=B.clean())
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


def test_excel_csv_pdf_and_zip_download_as_files_that_open(world):
    c = client_as(STAFF)
    sid = built(c)
    x = dl(c, sid, "xlsx")
    assert x.status_code == 200 and x.headers["Content-Type"].startswith("application/vnd.openxmlformats") and 'filename="Acme-Dental-business-research.xlsx"' in x.headers["Content-Disposition"]
    assert "Offerings" in openpyxl.load_workbook(io.BytesIO(x.data)).sheetnames and x.headers["Cache-Control"] == "private, no-store"
    p = dl(c, sid, "pdf")
    assert p.status_code == 200 and p.headers["Content-Type"] == "application/pdf" and p.data[:5] == b"%PDF-"
    z = dl(c, sid, "zip")
    assert z.status_code == 200 and "overview.csv" in zipfile.ZipFile(io.BytesIO(z.data)).namelist() and "all-tables.zip" in z.headers["Content-Disposition"]
    q = dl(c, sid, "csv")
    assert q.status_code == 200 and q.data.startswith(chr(0xFEFF).encode()) and 'questions-for-the-client.csv"' in q.headers["Content-Disposition"]
    assert list(csv.reader(io.StringIO(q.data.decode("utf-8-sig"))))[0] == ["Area", "Question", "Why it matters", "When"]


def test_a_download_that_cannot_be_made_is_said_in_words(world, monkeypatch):
    from tracker import paid_business_export as export
    c = client_as(STAFF)
    sid = built(c)
    assert dl(c, sid, "docx").status_code == 400 and "file should be one of" in dl(c, sid, "docx").get_json()["error"]
    monkeypatch.setattr(export, "pdf", lambda rec: (_ for _ in ()).throw(ValueError("boom")))
    r = dl(c, sid, "pdf")
    assert r.status_code == 500 and r.get_json()["error"] == "The file could not be made (ValueError)."
    rec = world.store.rows[sid]
    rec["plan"]["implications"]["questions"] = []
    assert dl(c, sid, "csv").status_code == 404
    rec["plan"] = {}
    assert dl(c, sid, "xlsx").status_code == 409
    rec["plan"] = {"core": {"summary": "half done"}}                                 # written, but not yet checked: no claims sheet, so no files
    for kind in ("xlsx", "pdf", "zip", "csv"):
        assert dl(c, sid, kind).status_code == 409, kind


# ── the wiring ──────────────────────────────────────────────────────────────

def test_every_address_the_page_script_uses_is_a_route():
    js = open(os.path.join(ROOT, "static", "js", "business_research.js"), encoding="utf-8").read()
    rules = {r.rule for r in appmod.app.url_map.iter_rules()}
    used = set(re.findall(r'API\+"(/[a-z]+)"', js)) | {"/<sid>/status", "/<sid>/rebuild", "/<sid>/download"}
    for path in used:
        assert any(r.startswith("/p2/paid-marketing/api/business") and r.endswith(path) for r in rules), path
    assert "/p2/paid-marketing/api/segments/accounts" in rules and "/p2/paid-marketing/business-research" in rules


def test_the_agent_is_on_the_paid_marketing_page_the_palette_and_the_hub_counts_it():
    root = os.path.join(ROOT, "templates")
    for name in ("paid_marketing.html", "_paid_kpal.html"):
        assert "/p2/paid-marketing/business-research" in open(os.path.join(root, name), encoding="utf-8").read(), name
    assert "Business Research" in open(os.path.join(root, "hub.html"), encoding="utf-8").read()


def test_the_knowledge_the_chat_agents_hold_describes_it():
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "Business Research (/p2/paid-marketing/business-research)" in src and "paid_business_job.py" in src


def test_the_real_dependencies_start_the_business_runner_for_a_business_build(monkeypatch):
    monkeypatch.undo()
    from tracker import paid_business_job, paid_strategy_job
    ran = []
    monkeypatch.setattr(paid_business_job, "run_build", lambda sid, **kw: ran.append(("business", sid)))
    monkeypatch.setattr(paid_strategy_job, "run_build", lambda sid, **kw: ran.append(("strategy", sid)))
    fake = F.FakeStrategyStore()
    monkeypatch.setattr(appmod, "_strat_spawn", lambda fn: fn())
    monkeypatch.setattr(appmod, "_pm_user_email", lambda: STAFF)
    for inputs, want in ((B.clean(), "business"), (F.clean(), "strategy")):
        rec = fake.create(by=STAFF, inputs=inputs)
        assert appmod._strat_begin(fake, rec) == (True, "")
        assert ran[-1] == (want, rec["id"])


def test_a_new_build_is_saved_with_this_agents_own_steps(world, monkeypatch):
    saved = []
    original = world.store.create

    def spy(**kw):
        saved.append(kw)
        return original(**kw)
    monkeypatch.setattr(store, "create", spy)
    built(client_as(STAFF))
    assert [s["key"] for s in saved[0]["steps"]][:2] == ["r:website", "r:reputation"] and saved[0]["steps"][-1]["key"] == "f:finish" and saved[0]["inputs"]["kind"] == "business"
