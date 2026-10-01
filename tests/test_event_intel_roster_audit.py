"""The roster audit of 2026-10-01: each finding, as a test.

The roster play's promise is that it reports what an event PUBLISHES and
says plainly what it could not read. These are the places it shrank the
roster quietly, or reported more than it read.
"""
import gzip  # noqa: F401
import os
import sys

import pytest

from tracker import claude_websearch as CW
from tracker import event_intel_enrich as EN
from tracker import event_intel_evidence as E
from tracker import event_intel_harvest as H
from tracker import event_intel_pipeline as P
from tracker import event_intel_recover as RC
from tracker import event_intel_resolve as RS
from tracker import event_intel_store as S

sys.path.insert(0, os.path.dirname(__file__))


# ── 1. www. links keep their domain ──

@pytest.mark.parametrize("text,domain", [
    ("Exhibitors\nAcme Corp [https://www.acme.com/]", "acme.com"),
    ("Exhibitors\nInitech [https://www.initech.co.uk/about]", "initech.co.uk"),
    ("Exhibitors\nGlobex [https://shop.globex.com/]", "globex.com"),
    ("Exhibitors\nUmbrella umbrella.io", "umbrella.io"),
])
def test_a_linked_company_keeps_its_domain(text, domain):
    name = text.splitlines()[1].split(" [")[0].split(" umbrella")[0]
    kept, _ = E.supported_rows([dict(org_name=name, role="exhibitor", org_domain=domain)],
                               text, "exhibitors")
    assert kept and kept[0]["org_domain"] == domain


def test_a_domain_the_page_never_mentions_is_still_dropped():
    kept, _ = E.supported_rows([dict(org_name="Acme Corp", role="exhibitor", org_domain="acme.com")],
                               "Exhibitors\nAcme Corp [https://www.acmecorp.net/]", "exhibitors")
    assert kept[0]["org_domain"] is None


# ── 2. the role word may be anywhere on the page ──

def test_a_name_in_a_later_chunk_is_supported_by_the_pages_heading():
    page = "Speakers\n" + "filler line\n" * 50 + "Dana Lee, Acme"
    chunk = "Dana Lee, Acme"
    assert E.supported_rows([dict(org_name="Acme", role="speaker")], chunk, "speakers")[0] == []
    kept, _ = E.supported_rows([dict(org_name="Acme", role="speaker")], chunk, "speakers", page)
    assert kept


def test_the_name_must_still_be_in_its_own_chunk():
    kept, _ = E.supported_rows([dict(org_name="Acme", role="speaker")], "Someone else",
                               "speakers", "Speakers Acme")
    assert kept == []


# ── 16. a dense chunk is split once when its reply runs out ──

def test_a_chunk_that_runs_out_of_output_is_halved_and_retried(monkeypatch):
    calls = []

    def chunk(text, *a, **k):
        calls.append(len(text))
        if len(calls) == 1:
            return {"rows": [], "error": {"kind": CW.ERR_MAX_TOKENS, "detail": "x"}}
        names = [w for w in text.split() if w.startswith("Co")]
        return {"rows": [dict(org_name=n, role="exhibitor") for n in names], "note": "", "error": None}
    monkeypatch.setattr(H, "_extract_chunk", chunk)
    text = "Exhibitors\n" + "\n".join("Co%03d" % i for i in range(600))
    out = H.extract_participants(text, "https://e.example/x", "exhibitors", "E")
    assert len(calls) == 3 and out["error"] is None
    assert len(out["rows"]) == 600


# ── 6. a nav link to last year does not withhold this year ──

def test_a_link_to_another_editions_page_is_not_this_pages_edition():
    text = "Our 2026 Sponsors\nAcme\n2025 Sponsors [https://e.example/2025/sponsors]"
    assert E.roster_years(text) == ["2026"]


def test_a_heading_naming_another_edition_still_counts():
    assert E.roster_years("2025 Sponsors\nAcme") == ["2025"]


# ── 3 and 15. a partial read is said, first, and a later failure keeps the rest ──

def _page(text, final=None):
    return {"url": final or "https://e.example/x", "final_url": final or "https://e.example/x",
            "status": "ok", "http_status": 200, "text": text, "note": "", "truncated": False,
            "spa": None, "redirected": False, "structured_events": [], "titles": []}


def test_a_later_page_that_raises_keeps_the_pages_already_read(monkeypatch):
    pages = {"https://e.example/x": _page("Exhibitors\nAcme\nNext [https://e.example/x?page=2]")}

    def fetch(url):
        if url in pages:
            return pages[url]
        raise RuntimeError("socket closed")
    monkeypatch.setattr(H, "fetch_page", fetch)
    monkeypatch.setattr(H, "_extract_chunk", lambda text, *a, **k: {
        "rows": [dict(org_name="Acme", role="exhibitor")], "note": "", "error": None})
    got = H.harvest_page({"url": "https://e.example/x", "kind": "exhibitors"}, "E", "e.example")
    assert [r["org_name"] for r in got["rows"]] == ["Acme"]
    src = got["source"]
    assert src["partial"] is True and src["coverage"]["partial"] is True
    assert src["note"].startswith("Stopped following this listing")


def test_a_failed_chunk_is_said_before_any_chunk_note(monkeypatch):
    monkeypatch.setattr(E, "chunks", lambda text: iter(["Exhibitors Acme", "broken"]))
    monkeypatch.setattr(H, "_extract_chunk", lambda text, *a, **k: (
        {"rows": [dict(org_name="Acme", role="exhibitor")], "note": "N" * 700, "error": None}
        if "Acme" in text else {"rows": [], "error": {"kind": "timeout", "detail": "t"}}))
    out = H.extract_participants("Exhibitors Acme broken", "https://e.example", "exhibitors", "E")
    assert out["note"].startswith("1 of 2 extraction chunks failed")


# ── 9, 10, 18. what the summary counts ──

def _src(url, status, kind="exhibitors", **meta):
    return {"url": url, "kind": kind, "status": status, "rows_found": 0, "metadata": meta}


def test_the_summary_counts_roster_pages_once_and_says_which_were_partial(monkeypatch):
    sources = [
        _src("https://e.example/a", "ok"),
        _src("https://e.example/b", "ok", partial=True),
        _src("https://e.example/c", "blocked"),
        _src("https://e.example/c", "recovered", recovery_of="https://e.example/c"),
        _src("https://e.example/d", "blocked"),
        _src("https://e.example/d", "blocked", recovery_of="https://e.example/d"),
        _src("https://e.example/register", "ok", kind="access_review"),
        _src("https://e.example/a/", "duplicate"),
    ]
    monkeypatch.setattr(P.store, "get_sources", lambda rid: sources)
    monkeypatch.setattr(P.store, "get_participants", lambda rid: [
        {"org_name": "Acme Inc", "role": "exhibitor", "org_domain": "acme.com"},
        {"org_name": "Acme", "role": "sponsor", "org_domain": None},
        {"org_name": "Dana Lee", "person_name": "Dana Lee", "role": "speaker"}])
    s = P._summarise(1)
    assert (s["sources_tried"], s["sources_read"], s["sources_partial"],
            s["sources_recovered"], s["sources_unreadable"]) == (4, 2, 1, 1, 1)
    assert s["organisations"] == 1


def test_one_listing_under_three_addresses_is_harvested_once(monkeypatch):
    saved, sources = [], []
    monkeypatch.setattr(P.store, "save_source", lambda *a, **k: sources.append(a))
    monkeypatch.setattr(P.store, "save_participants", lambda rid, eid, rows: saved.extend(rows) or len(rows))
    harvested = []

    def harvest(page, *a, **k):
        harvested.append(page["url"])
        return {"source": {"url": page["url"], "kind": "exhibitors", "status": "ok",
                           "final_url": "https://e.example/exhibitors"},
                "rows": [dict(org_name="Acme", role="exhibitor")]}
    monkeypatch.setattr(P.event_intel_harvest, "harvest_page", harvest)
    P._harvest_event(1, 2, {"name": "E", "website": "https://e.example"}, [
        {"url": "https://e.example/exhibitors", "kind": "exhibitors"},
        {"url": "https://e.example/exhibitors/", "kind": "exhibitors"},
        {"url": "http://www.e.example/exhibitors", "kind": "exhibitors"},
        {"url": "https://e.example/floor", "kind": "exhibitors"}])
    assert harvested == ["https://e.example/exhibitors", "https://e.example/floor"]
    assert len(saved) == 1   # /floor landed on the same page: its rows are not counted twice
    assert any(a[4] == "duplicate" for a in sources)


# ── 8 and 11. the resolve reply ──

def _resolve(monkeypatch, reply, year=None):
    import json
    monkeypatch.setattr(RS.claude_websearch, "ask", lambda *a, **k: {
        "text": json.dumps(reply), "error": None, "search_count": 3, "usage": {}})
    return RS.resolve_event("Money20/20 USA", year)


BASE = {"confidence": "high", "name": "Money20/20 USA", "website": "https://us.money2020.com",
        "starts_on": "2026-10-18", "ends_on": "2026-10-21", "pages": []}


def test_numbers_where_text_was_asked_for_do_not_crash_the_lookup(monkeypatch):
    out = _resolve(monkeypatch, dict(BASE, edition=2026, stated_size=10000, format=None,
                                     availability_source=["a", "b"]))
    assert out["ok"] and out["event"]["edition"] == "2026" and out["event"]["stated_size"] == "10000"
    assert out["event"]["availability_source"] is None


def test_a_date_that_is_not_a_date_is_dropped_not_stored(monkeypatch):
    out = _resolve(monkeypatch, dict(BASE, starts_on="October 18th", ends_on="2026-10-21T00:00:00Z"))
    assert out["event"]["starts_on"] is None and out["event"]["ends_on"] == "2026-10-21"


def test_another_year_than_the_one_asked_for_is_refused(monkeypatch):
    out = _resolve(monkeypatch, dict(BASE, starts_on="2027-10-24"), year="2025")
    assert not out["ok"] and "2027 edition" in out["reasoning"] and "2025 edition" in out["reasoning"]
    assert _resolve(monkeypatch, BASE, year="2026")["ok"]


@pytest.mark.parametrize("kind,expected", [("exhibitor", "exhibitors"), ("Sponsor", "sponsors"),
                                           ("floor_plan", "exhibitors"), ("Speaker List", "speakers"),
                                           ("brochure", None)])
def test_page_kinds_are_read_in_the_shapes_replies_use(kind, expected):
    pages = RS._clean_pages([{"url": "https://e.example/x", "kind": kind}])
    assert [p["kind"] for p in pages] == ([expected] if expected else [])


def test_one_page_under_two_addresses_is_one_page():
    pages = RS._clean_pages([{"url": "https://e.example/x", "kind": "exhibitors"},
                             {"url": "http://www.e.example/x/", "kind": "exhibitors"}])
    assert len(pages) == 1


# ── 5 and 15. reading the page ──

class _Resp:
    def __init__(self, body, ctype="text/html"):
        self.status_code, self.url, self.headers = 200, "https://e.example/", {"Content-Type": ctype}
        self.encoding = "ISO-8859-1" if "charset" not in ctype else ctype.split("charset=")[1]
        self._b = body

    def iter_content(self, n):
        yield self._b

    def close(self):
        pass


def _fetch(monkeypatch, body, ctype="text/html"):
    monkeypatch.setattr(H, "public_get", lambda *a, **k: _Resp(body, ctype))
    return H.fetch_page("https://e.example/")


FILL = "<p>" + "Exhibitors and sponsors of the event. " * 20 + "</p>"


def test_a_meta_charset_is_honoured(monkeypatch):
    body = ('<html><head><meta charset="utf-8"></head><body>%s<p>Société Générale</p></body></html>'
            % FILL).encode("utf-8")
    assert "Société Générale" in _fetch(monkeypatch, body)["text"]


def test_a_header_charset_still_wins(monkeypatch):
    body = ("<html><body>%s<p>Société</p></body></html>" % FILL).encode("latin-1")
    assert "Société" in _fetch(monkeypatch, body, "text/html; charset=ISO-8859-1")["text"]


def test_a_script_tag_with_a_bare_type_does_not_break_the_read(monkeypatch):
    body = ("<html><body><script type>var x=1</script>%s</body></html>" % FILL).encode()
    out = _fetch(monkeypatch, body)
    assert out["status"] == "ok" and out["structured_events"] == []


# ── 7. recovered rows ──

def test_a_recovered_row_from_another_edition_is_withheld_and_off_site_is_marked(monkeypatch):
    import json
    monkeypatch.setattr(RC.claude_websearch, "ask", lambda *a, **k: {
        "text": json.dumps({"rows": [
            {"org_name": "Old Co", "role": "exhibitor", "found_at": "https://ev.example/2024/exhibitors"},
            {"org_name": "Blog Co", "role": "exhibitor", "found_at": "https://blog.example/ev-2026"},
            {"org_name": "Home Co", "role": "exhibitor", "found_at": "https://www.ev.example/exhibitors"}]}),
        "error": None, "search_count": 2, "usage": {}})
    out = RC.recover_page("https://ev.example/exhibitors", "exhibitors", "Ev", "ev.example", "2026")
    names = {r["org_name"]: r for r in out["rows"]}
    assert set(names) == {"Blog Co", "Home Co"}
    assert names["Blog Co"]["evidence"]["off_site"] is True
    assert names["Home Co"]["evidence"]["off_site"] is False
    assert "another edition" in out["source"]["note"]


# ── 13. contacts are not guessed onto the wrong company ──

def test_a_shared_label_does_not_attach_contacts_by_name(monkeypatch):
    from tracker import apollo_client
    monkeypatch.setattr(EN, "api_key", lambda: "k")
    monkeypatch.setattr(apollo_client, "search_people", lambda *a, **k: [
        {"name": "Ann", "organization_name": "Acme", "organization_domain": None},
        {"name": "Bo", "organization_name": "Beta", "organization_domain": None}])
    out = EN.find_people(["acme.com", "acme.io", "beta.com"])
    assert "acme.com" not in out["by_domain"] and "acme.io" not in out["by_domain"]
    assert out["by_domain"]["beta.com"][0]["employer_unconfirmed"] is True


# ── 20. CSV ──

@pytest.mark.parametrize("cell", ["\t=1+1", "\r=1+1"])
def test_a_formula_behind_a_tab_or_return_is_defused(cell):
    import app as appmod
    assert appmod._csv_safe(cell).startswith("'")


# ── 22. the evidence ledger is a side ledger ──

def test_an_evidence_ledger_failure_does_not_fail_the_roster(monkeypatch):
    runs = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setattr(P.store, "update_run", lambda rid, **f: runs.setdefault(rid, {}).update(f))
    monkeypatch.setattr(P.store, "save_event", lambda rid, ev: 5)
    monkeypatch.setattr(P, "durable_stage", lambda name, fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(P.event_intel_resolve, "resolve_event", lambda q, y: {
        "ok": True, "event": dict(BASE), "pages": []})
    import tracker.event_intel_evidence as EV
    monkeypatch.setattr(EV, "record_event", lambda *a: (_ for _ in ()).throw(RuntimeError("db")))
    monkeypatch.setattr(P, "_summarise", lambda rid: {})
    P._run_lookup(1, "Money20/20 USA", None)
    assert runs[1]["status"] == "complete"


def test_a_recovered_row_another_page_lists_directly_is_not_saved_twice(monkeypatch):
    """Live run 31 (IMEX America): the exhibitor directory was recovered by
    search as the partner list, and every partner was on the roster twice."""
    saved = []
    monkeypatch.setattr(P.store, "save_source", lambda *a, **k: None)
    monkeypatch.setattr(P.store, "save_participants",
                        lambda rid, eid, rows: saved.extend(rows) or len(rows))
    monkeypatch.setattr(P, "durable_stage", lambda name, fn, *a, **k: fn(*a, **k))

    def harvest(page, *a, **k):
        if "exhibitor" in page["url"]:
            return {"source": {"url": page["url"], "kind": "exhibitors", "status": "blocked"}, "rows": []}
        return {"source": {"url": page["url"], "kind": "partners", "status": "ok"},
                "rows": [dict(org_name="ASAE", role="partner", provenance="page")]}
    monkeypatch.setattr(P.event_intel_harvest, "harvest_page", harvest)
    monkeypatch.setattr(P.event_intel_recover, "should_recover", lambda src: src["status"] == "blocked")
    monkeypatch.setattr(P.event_intel_recover, "recover_page", lambda *a, **k: {
        "source": {"url": a[0], "kind": "exhibitors", "status": "recovered"},
        "rows": [dict(org_name="ASAE", role="partner", provenance="search"),
                 dict(org_name="Caesars", role="exhibitor", provenance="search")]})
    P._harvest_event(1, 2, {"name": "IMEX America", "website": "https://imexamerica.com"}, [
        {"url": "https://imexamerica.com/exhibitor-directory", "kind": "exhibitors"},
        {"url": "https://imexamerica.com/all-partners", "kind": "partners"}])
    assert [(r["org_name"], r["provenance"]) for r in saved] == [("ASAE", "page"), ("Caesars", "search")]
