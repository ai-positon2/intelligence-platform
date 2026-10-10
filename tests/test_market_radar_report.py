"""Market Radar, Phase 7: the client report, no network and no model."""
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import market_radar_report as R  # noqa: E402

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
PROFILE = {"name": "Acme Dental", "one_liner": "Family dentist in Austin.", "archetype": "local_single",
           "industry": {"plain_label": "Dentist", "naics_code": "621210"},
           "hq": {"city": "Austin", "country_code": "US"}, "markets": ["US"],
           "hq_point": {"lat": 30.27, "lon": -97.74}, "offerings": ["implants", "checkups"]}


def ev(i, entity, type="new_location", hidden=None, title=None, sources=None, status="opened"):
    return {"id": i, "entity_id": entity, "type": type, "status": status,
            "event_date": date(2026, 10, i % 9 + 1), "first_seen_at": NOW,
            "title": title or "Move %d" % i, "summary": None, "location": {"label": "Round Rock, TX"},
            "sources": sources or [{"url": "https://news.example/%d" % i, "detector": "news",
                                    "publisher": "Pub", "headline": "Headline %d" % i, "date": "2026-10-08"}],
            "evidence_count": 1, "hidden_reason": hidden, "dedupe_key": "k%d" % i}


class Store:
    def __init__(self):
        self.reports = []
        self.events = [ev(1, 10, "acquisition"), ev(2, 10, "promotion"), ev(3, 11, "new_location"),
                       ev(4, 11, "announcement", hidden="not a move"), ev(5, 11, "page_changed")]
        self.scores = {1: {"score": 9.1, "severity": "HIGH"}, 2: {"score": 0.8, "severity": "LOW"},
                       3: {"score": 3.0, "severity": "MEDIUM", "distance_km": 12.5},
                       4: {"score": 1.0, "severity": "LOW"}}
        self.pulse = {"payload": {"note": "120 headlines", "themes": [
            {"title": "NHS contract reform", "summary": "s", "why_it_matters": "w", "kind": "regulation",
             "articles": [{"title": "a", "publisher": "BBC", "date": "2026-10-01", "link": "https://bbc.example/1"}]}],
            "regulation": [{"title": "Dental rule", "type": "Final rule", "agency": "HHS", "date": "2026-09-01",
                            "link": "https://fr.example/1"}]}}
        self.jobs = {10: {"payload": {"open": 42, "places": {"Austin": 3}, "by_function": {"Clinical": 30},
                                      "senior_open": ["Regional Director"]}, "last_seen_at": NOW}}

    def get_client(self, cid, owner):
        return {"client_id": cid, "entity_id": 99, "domain": "acme.example", "name": "Acme",
                "profile": PROFILE, "settings": {}, "radius_km": 5} if cid == 1 else None

    def competitors(self, cid, owner, include_removed=False):
        return [{"entity_id": 10, "domain": "big.example", "name": "BigChain", "kind": "direct",
                 "status": "confirmed", "details": {"sells": "dental care"}},
                {"entity_id": 11, "domain": "small.example", "name": "Small Smiles", "kind": "local",
                 "status": "proposed", "details": {}}]

    def client_scores(self, cid):
        return self.scores

    def recent_events(self, ids, days=120, limit=400):
        return [e for e in self.events if e["entity_id"] in ids]

    def latest_run(self, cid, owner, collect=False):
        return None

    def latest_pulse(self, key, country):
        return self.pulse if (key, country) == ("naics:621210", "US") else None

    def latest_snapshot(self, entity_id, detector):
        return self.jobs.get(entity_id) if detector == "jobs" else None

    def save_report(self, cid, payload, run_id=None):
        self.reports.append({"client_id": cid, "payload": payload, "run_id": run_id})
        return len(self.reports)


COLLECTION = {"coverage": [{"text": "News read for 2 of 2 competitors."}],
              "signals": {"note": "40 headlines read: 3 events"},
              "radar": {"local": {"status": "ok", "note": "1 new place", "findings": [
                  {"name": "Hillside Dental", "category": "dentist", "distance_km": 1.3, "lat": 30.28,
                   "lon": -97.75, "status": "opened", "certain": False, "evidence": ["domain registered 2025-11-02"],
                   "website": "https://hillside.example/"}]}}}


@pytest.fixture(autouse=True)
def plain_profile(monkeypatch):
    from tracker import market_radar_views as views
    monkeypatch.setattr(views, "effective_profile", lambda p, s: p)


def pack():
    return R.build_pack(1, "o", store=Store(), now=NOW, collection=COLLECTION, run_id=7)


# == the evidence pack ======================================================================

def test_the_pack_ranks_moves_for_this_client_and_leaves_out_hidden_and_unscored_ones():
    p = pack()
    assert [m["event_id"] for m in p["moves"]] == [1, 3, 2]
    assert [m["ref"] for m in p["moves"]] == ["M1", "M2", "M3"]
    m = p["moves"][1]
    assert m["company"] == "Small Smiles" and m["company_ref"] == "C2" and m["distance_km"] == 12.5
    assert m["label"] == "New location" and m["sources"][0]["headline"] == "Headline 3"
    assert p["competitors"][0]["sells"] == "dental care"


def test_strong_moves_are_kept_before_weak_ones_when_the_pack_is_full(monkeypatch):
    monkeypatch.setattr(R, "MAX_MOVES", 1)
    s = Store()
    s.scores[2]["score"] = 99          # a LOW with a big score still comes after any MEDIUM
    p = R.build_pack(1, "o", store=s, now=NOW, collection=COLLECTION)
    assert [m["event_id"] for m in p["moves"]] == [1]
    assert "2 lower-ranked moves were not given to the writer." in p["coverage"]


def test_the_pack_carries_the_radar_pulse_rules_hiring_and_coverage():
    p = pack()
    assert p["nearby"][0]["ref"] == "N1" and p["nearby"][0]["lat"] == 30.28
    assert p["client"]["point"] == {"lat": 30.27, "lon": -97.74} and p["client"]["radius_km"] == 5.0
    assert p["themes"][0]["ref"] == "T1" and p["themes"][0]["why"] == "w"
    assert p["rules"][0]["ref"] == "R1" and p["hiring"][0]["open"] == 42
    assert p["hiring"][0]["functions"] == [("Clinical", 30)] or p["hiring"][0]["functions"] == [["Clinical", 30]]
    assert "News read for 2 of 2 competitors." in p["coverage"]
    assert "Competitor news: 40 headlines read: 3 events" in p["coverage"]
    assert p["collect_run_id"] == 7


def test_the_writer_reads_every_item_with_its_reference():
    text = R.pack_text(pack())
    for frag in ("CLIENT: Acme Dental (acme.example)", "M1 | BigChain | Acquisition | opened",
                 "[HIGH]", "N1 | Hillside Dental, dentist, 1.3 km away | possibly new",
                 "T1 | industry theme (US): NHS contract reform", "R1 | US Federal Register Final rule",
                 "H1 | BigChain hiring: 42 open roles in 1 places", "C2 | competitor Small Smiles",
                 "WHAT WAS AND WAS NOT CHECKED"):
        assert frag in text, frag


# == the brief ==============================================================================

def test_the_brief_keeps_only_statements_that_cite_real_evidence():
    refs = R.items_by_ref(pack())
    raw = {"summary": {"text": "BigChain bought a rival — big news.", "cites": ["m1"]},
           "top": [{"title": "t%d" % i, "what_happened": "w", "so_what": "s", "action": "a",
                    "confidence": "certain", "cites": ["M1", "M1", "Z9"]} for i in range(7)] +
                  [{"title": "made up", "what_happened": "w", "so_what": "s", "action": "a",
                    "confidence": "high", "cites": ["M99"]}],
           "competitors": [{"company_ref": "c1", "summary": "s", "cites": ["M1"]},
                           {"company_ref": "M1", "summary": "not a competitor ref", "cites": ["M1"]}],
           "local": [{"text": "l", "cites": ["N1"]}], "industry": [{"title": "t", "implication": "i", "cites": ["T1"]}],
           "opportunities": [{"text": "o", "cites": []}], "threats": [{"text": "x", "cites": ["H1"]}],
           "watch": [{"text": "w", "cites": ["R1"]}]}
    brief, dropped = R.clean_brief(raw, refs)
    assert brief["summary"] == {"text": "BigChain bought a rival, big news.", "cites": ["M1"]}
    assert len(brief["top"]) == 5 and brief["top"][0]["cites"] == ["M1"] and brief["top"][0]["confidence"] == "low"
    assert [c["company_ref"] for c in brief["competitors"]] == ["C1"]
    assert brief["opportunities"] == [] and brief["threats"][0]["cites"] == ["H1"]
    assert {d["where"] for d in dropped} == {"top 8", "competitor 2", "opportunities 1"}


def statements_brief():
    return {"summary": {"text": "s", "cites": ["M1"]},
            "top": [{"title": "Bought", "what_happened": "BigChain bought X", "so_what": "s", "action": "a",
                     "confidence": "high", "cites": ["M1"]},
                    {"title": "Opened", "what_happened": "Small Smiles opened", "so_what": "s", "action": "a",
                     "confidence": "medium", "cites": ["M2"]}],
            "competitors": [{"company_ref": "C1", "summary": "c", "cites": ["M1"]}],
            "local": [], "industry": [{"title": "t", "implication": "i", "cites": ["T1"]}],
            "opportunities": [], "threats": [], "watch": [{"text": "w", "cites": ["N1"]}]}


class LLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def call_json(self, system, user, schema, **kw):
        self.calls.append(dict(kw, system=system, user=user))
        if isinstance(self.answer, Exception):
            raise self.answer
        a = self.answer[kw["stage"]] if isinstance(self.answer, dict) and "stage" in kw and kw["stage"] in self.answer else self.answer
        if isinstance(a, Exception):
            raise a
        return a, {}


class ModelError(Exception):
    kind = "truncated"
    detail = "hit the limit"


def test_the_check_removes_unsupported_statements_and_says_why():
    def v(i, ok, facts=()):
        return {"id": i, "reasoning": "r", "unsupported_facts": list(facts), "supported": ok}
    llm = LLM({"verdicts": [v("summary", True), v("top.0", True),
                            v("top.1", False, ["opened (the evidence says planned)"]),
                            v("competitors.0", False),          # false, but no fact named: kept
                            v("industry.0", False, ["no such theme", " "])]})
    out, removed, note = R.check_brief(statements_brief(), pack(), llm=llm)
    assert [t["title"] for t in out["top"]] == ["Bought"] and out["industry"] == []
    assert len(out["competitors"]) == 1
    assert [(r["where"], r["why"]) for r in removed] == [("top.1", "opened (the evidence says planned)"),
                                                         ("industry.0", "no such theme")]
    assert note == "1 statements got no verdict from the check and are shown unchecked."
    assert "CLIENT: Acme Dental (acme.example)" in llm.calls[0]["user"]
    assert out["watch"] and "STATEMENT top.1: Opened. Small Smiles opened" in llm.calls[0]["user"]
    assert "M2 | Small Smiles | New location" in llm.calls[0]["user"]
    assert llm.calls[0]["stage"] == "report_check" and llm.calls[0]["model"] == R.CHECK_MODEL


def test_a_check_that_could_not_run_keeps_the_brief_and_says_it_is_unchecked():
    b = statements_brief()
    out, removed, note = R.check_brief(b, pack(), llm=LLM(ModelError()))
    assert out == b and removed == [] and "could not run (truncated)" in note and "not checked" in note


def test_an_unsupported_summary_is_removed_too():
    llm = LLM({"verdicts": [{"id": "summary", "reasoning": "r", "unsupported_facts": ["x"], "supported": False}]})
    out, removed, _ = R.check_brief(statements_brief(), pack(), llm=llm)
    assert out["summary"] is None and removed[0]["where"] == "summary"


# == one report =============================================================================

def good_writer():
    return {"summary": {"text": "s", "cites": ["M1"]},
            "top": [{"title": "Bought", "what_happened": "w", "so_what": "s", "action": "a",
                     "confidence": "high", "cites": ["M1"]}],
            "competitors": [], "local": [], "industry": [], "opportunities": [], "threats": [], "watch": []}


def test_a_report_is_written_checked_and_stored_with_its_pack():
    s = Store()
    llm = LLM({"report_write": good_writer(),
               "report_check": {"verdicts": [{"id": "summary", "reasoning": "", "unsupported_facts": [], "supported": True},
                                             {"id": "top.0", "reasoning": "", "unsupported_facts": [], "supported": True}]}})
    out = R.write_report(1, "o", store=s, now=NOW, llm=llm, collection=COLLECTION, run_id=7)
    assert out == {"report_id": 1, "status": "ok", "note": None, "top": 1, "removed": 0, "dropped": 0,
                   "check_note": None}
    saved = s.reports[0]
    assert saved["run_id"] == 7 and saved["payload"]["brief"]["top"][0]["title"] == "Bought"
    assert saved["payload"]["pack"]["moves"][0]["ref"] == "M1" and saved["payload"]["checked"] is True
    w = next(c for c in llm.calls if c["stage"] == "report_write")
    assert w["model"] == R.WRITER_MODEL and w["effort"] == "medium" and "CLIENT: Acme Dental" in w["user"]


def test_a_report_that_could_not_be_written_is_stored_as_failed():
    s = Store()
    out = R.write_report(1, "o", store=s, now=NOW, llm=LLM({"report_write": ModelError()}), collection=COLLECTION)
    assert out["status"] == "failed" and "truncated: hit the limit" in out["note"]
    assert s.reports[0]["payload"]["brief"] is None


def test_nothing_collected_means_no_model_call():
    s = Store()
    s.events, s.pulse = [], None
    llm = LLM({})
    out = R.write_report(1, "o", store=s, now=NOW, llm=llm, collection={})
    assert out["status"] == "empty" and llm.calls == []


def test_a_report_that_breaks_does_not_lose_the_collection():
    from tracker import market_radar_run as mrun
    import tracker.market_radar_store as real
    saved = {}
    orig = real.update_run
    real.update_run = lambda run_id, **kw: saved.update(kw)
    try:
        def boom(*a, **k):
            raise RuntimeError("writer down")
        mrun.collect_job(1, 2, "o", collect=lambda *a, **k: {"companies": []}, radar=lambda *a, **k: {},
                         pulse=lambda *a, **k: {}, signals=lambda *a, **k: {}, report=boom)
    finally:
        real.update_run = orig
    assert saved["status"] == "complete" and "writer down" in saved["summary"]["report"]["error"]


def test_the_prompts_hold_the_rules():
    assert "Never state a fact" in R.WRITER_SYSTEM and "plans to" in R.WRITER_SYSTEM
    for rule in ("1 to 5", "only from the CLIENT line", "currency", "did not happen"):
        assert rule in R.WRITER_SYSTEM, rule
    assert "turns a plan into a done deal" in R.CHECK_SYSTEM
    for t in (R.WRITER_SYSTEM, R.CHECK_SYSTEM):
        assert chr(0x2014) not in t and chr(0x2013) not in t


# == the page script ========================================================================

JS = os.path.join(ROOT, "static", "js", "market_radar_report.js")
EVIL = '<img src=x onerror=alert(1)>"\'&'


def run_js(expr):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    prog = ("globalThis.window = globalThis; globalThis.location = {origin: 'https://x.example'};"
            "const body = {innerHTML: '', addEventListener() {}};"
            "globalThis.document = {getElementById: (id) => id === 'rrBody' ? body : null};"
            "require(%s); const MRR = globalThis.MRR;\n"
            "process.stdout.write(JSON.stringify((() => { %s })()));") % (json.dumps(JS), expr)
    proc = subprocess.run([shutil.which("node"), "-e", prog], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def view():
    p = json.loads(json.dumps(pack(), default=str))
    p["client"]["name"] = EVIL
    p["moves"][0]["title"] = EVIL
    p["moves"][0]["sources"][0]["url"] = "javascript:alert(1)"
    p["nearby"][0]["name"] = EVIL
    return {"client_id": 1, "name": "Acme", "domain": "acme.example", "status": "ok",
            "created_at": "2026-10-10T12:00:00", "pack": p,
            "brief": {"summary": {"text": EVIL, "cites": ["M1"]},
                      "top": [{"title": EVIL, "what_happened": EVIL, "so_what": EVIL, "action": EVIL,
                               "confidence": "high", "cites": ["M1", "T1"]}],
                      "competitors": [{"company_ref": "C1", "summary": EVIL, "cites": ["M1"]}],
                      "local": [{"text": "One new practice nearby.", "cites": ["N1"]}],
                      "industry": [{"title": EVIL, "implication": EVIL, "cites": ["T1"]}],
                      "opportunities": [{"text": EVIL, "cites": ["H1"]}], "threats": [],
                      "watch": [{"text": "w", "cites": ["R1"]}]},
            "removed": [{"where": "top.1", "text": EVIL, "why": "the evidence says planned"}],
            "dropped": [], "check_note": None, "models": {"writer": "w", "check": "c"},
            "cost": {"total_usd": 0.142, "partial": False, "by_stage": {"report_write": 0.11}}}


def test_the_report_renders_every_section_and_no_markup_from_its_sources():
    html = run_js("MRR.render(%s); return document.getElementById('rrBody').innerHTML;" % json.dumps(view()))
    assert "<img" not in html and "onerror=alert(1)>" not in html.replace("&gt;", "")
    import re
    assert not [h for h in re.findall(r'href="([^"]*)"', html) if "javascript" in h]
    for frag in ("What matters most", "So what for you", "Suggested action", "High confidence",
                 "Your company, as we read it", "Competitor moves", "Other competitors with moves (1)",
                 "Nearby and new", "aria-label=\"Map of new businesses", "Industry pulse",
                 "US federal rules naming this industry", "Hiring", "Regional Director",
                 "Opportunities and threats", "What to watch next", "Coverage and confidence",
                 "Statements removed by the citation check", "the evidence says planned", "cost $0.14",
                 'id="ev-M1"', "Evidence (2)", 'data-events="1"'):
        assert frag in html, frag


def test_the_printed_report_opens_everything_and_drops_controls_and_the_map():
    out = run_js("""
      MRR.render(%s);
      const html = document.getElementById('rrBody').innerHTML;
      return {buttons: (html.match(/data-noprint/g) || []).length};
    """ % json.dumps(view()))
    assert out["buttons"] >= 3


def test_a_missing_or_failed_report_says_so():
    out = run_js("""
      MRR.render({client_id: 3, name: 'Acme', status: 'none'}); const a = document.getElementById('rrBody').innerHTML;
      MRR.render({client_id: 3, name: 'Acme', status: 'failed', note: 'The report could not be written (refused: x).'});
      return [a, document.getElementById('rrBody').innerHTML];""")
    assert "No report has been written for this company yet" in out[0]
    assert "could not be written (refused: x)" in out[1] and "mr-callout bad" in out[1]


def test_a_newer_collection_without_a_report_is_said_above_the_last_written_one():
    v = dict(view(), latest_attempt={"created_at": "2026-10-17T07:00:00+00:00", "status": "failed",
                                     "note": EVIL})
    html = run_js("MRR.render(%s); return document.getElementById('rrBody').innerHTML;" % json.dumps(v))
    assert "A newer collection on" in html and "produced no report" in html
    assert "This is the last report that was written." in html and "<img" not in html
    assert 'mr-callout bad" data-noprint' in html and "What matters most" in html
    html = run_js("MRR.render(%s); return document.getElementById('rrBody').innerHTML;" % json.dumps(view()))
    assert "produced no report" not in html


def test_the_map_places_points_by_direction_and_needs_a_location():
    out = run_js("""
      const p = {client: {name: 'A', point: {lat: 30, lon: -97}, radius_km: 5},
                 nearby: [{ref: 'N1', name: 'North', lat: 30.03, lon: -97, distance_km: 3.3},
                          {ref: 'N2', name: 'East', lat: 30, lon: -96.97, distance_km: 2.9}]};
      return [MRR.nearbyMap(p), MRR.nearbyMap({client: {name: 'A'}, nearby: p.nearby})];""")
    import re
    pins = re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="9"', out[0])
    (nx, ny), (ex, ey) = [(float(a), float(b)) for a, b in pins]
    assert ny < 170 and abs(nx - 170) < 1          # north is up
    assert ex > 170 and abs(ey - 170) < 1          # east is right
    assert out[1] == ""


def test_a_shop_price_without_currency_never_reaches_the_writer():
    m = {"ref": "M9", "company": "AYBL", "label": "New product", "type": "product_launch",
         "status": "announced", "date": "2026-10-01", "title": "New product: Tank", "summary": "Priced 30.0 to 30.0",
         "sources": []}
    assert "Priced" not in R.evidence_text(m)
    assert "Priced" in R.evidence_text(dict(m, type="price_cut", summary="Priced 30.0 to 24.0"))


def test_the_checker_reads_what_was_checked_and_the_writer_skips_routine_competitors():
    llm = LLM({"verdicts": []})
    R.check_brief(statements_brief(), pack(), llm=llm)
    assert "News read for 2 of 2 competitors." in llm.calls[0]["user"]
    assert "Skip a competitor whose only moves are routine" in R.WRITER_SYSTEM


def test_a_long_move_summary_is_clipped_on_the_page():
    v = view()
    v["pack"]["moves"][0]["summary"] = "Added: 77 products | " + "Leggings (9) " * 60
    html = run_js("MRR.render(%s); return document.getElementById('rrBody').innerHTML;" % json.dumps(v))
    assert "Leggings (9) " * 30 not in html and "…" in html


def test_hiring_evidence_reads_the_same_after_a_trip_through_json():
    h = {"ref": "H1", "company": "BigChain", "open": 42, "places": 3, "functions": [["Clinical", 30]],
         "senior": []}
    assert "by function: Clinical 30" in R.evidence_text(h)
    assert "by function: Clinical 30" in R.evidence_text(dict(h, functions=[("Clinical", 30)]))
