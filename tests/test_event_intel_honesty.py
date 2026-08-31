"""Event & Conference Intelligence: the claims the agent must never make.

This file is not about whether the code runs. It is about the one defect this
agent could plausibly ship and nobody would notice in review: quietly calling
a published exhibitor list an attendee list, or presenting a roster assembled
from half an event's pages as if it were the whole thing.

Both are the platform's recurring defect in a new costume, the one thirteen
Contact Finder audit rounds kept finding: a surface asserting something its
data does not support. An empty or partial result must read as a fact about
the request, never as a fact about the world.

The JS assertions here EXECUTE the page's real inline script in node and
assert on the HTML it actually produces. A text assertion on the template
could not tell a working guard from a disabled one, which is exactly the
blind spot recorded in [[feedback-testing-discipline]].
"""

import json
import os
import re
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")

import app as appmod  # noqa: E402
from tracker import event_intel_harvest as harvest  # noqa: E402
from tracker import event_intel_store as store  # noqa: E402

_PAGE = "/p2/b2b-agents/event-conference-intelligence"


def _node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def _rendered_page():
    """The page as Flask actually serves it.

    Read through the route rather than off disk, because the script block now
    carries server-injected data (the rubric's category labels) and the raw
    template is therefore not valid JavaScript. Executing the raw file would
    test a bundle that is never shipped, and would have started failing here
    for a reason unrelated to anything this file asserts.
    """
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    resp = c.get(_PAGE)
    assert resp.status_code == 200, "the page did not render (%s)" % resp.status_code
    return resp.get_data(as_text=True)


def _page_script():
    """The page's first inline script block, which is the report renderer."""
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                        _rendered_page(), re.S)
    assert blocks, "the page has no inline script"
    return blocks[0]


_IIFE_CLOSE = "\n  })();"


def _run_in_node(js_tail, run_payload):
    """Execute the page's real renderer against a run payload and return what
    the drawer body ends up containing.

    The probe is injected INSIDE the page's IIFE rather than appended after
    it, because render() is deliberately not on window and should stay that
    way. Exporting it purely so a test could reach it would change the
    shipped code to suit the test; splicing before the IIFE's closing
    `})();` runs exactly the code the browser runs.

    Anchored on the FIRST `\\n  })();` at that indent rather than on the line
    that follows it: an earlier version keyed off `function toggleMenu(){`
    and broke the moment a comment was added above that function, which is a
    test failing for a reason that has nothing to do with what it tests.
    """
    script = _page_script()
    assert _IIFE_CLOSE in script, (
        "the page's IIFE no longer closes with a two-space-indented `})();`; "
        "update _IIFE_CLOSE rather than exporting render() to window")
    at = script.index(_IIFE_CLOSE)
    script = script[:at] + "\n" + js_tail + script[at:]
    js_tail = ""
    harness = """
var __html = {};
function __el(id){
  return {
    _id: id,
    set innerHTML(v){ __html[id] = v; },
    get innerHTML(){ return __html[id] || ''; },
    set textContent(v){ __html[id] = v; },
    get textContent(){ return __html[id] || ''; },
    style: {}, classList: {add:function(){},remove:function(){},contains:function(){return false;}},
    setAttribute: function(){}, disabled: false, value: ''
  };
}
var document = {
  getElementById: __el,
  addEventListener: function(){},
  querySelector: function(){ return null; }
};
var window = {};
var fetch = function(){ return {then: function(){ return {then: function(){ return {catch: function(){}}; }, catch: function(){}}; }, catch: function(){}}; };
var setInterval = function(){ return 0; };
var clearInterval = function(){};
"""
    body = harness + script + "\n" + js_tail
    path = os.path.join(_ROOT, ".pytest_evi_probe.js")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        proc = subprocess.run([sys.executable and "node", path], capture_output=True,
                              text=True, timeout=30)
        assert proc.returncode == 0, "node failed:\n%s" % proc.stderr[:2000]
        return proc.stdout
    finally:
        if os.path.exists(path):
            os.remove(path)


def _run_fixture(**over):
    """A completed lookup run. Two of four pages unreadable ON PURPOSE: the
    whole point of the report is that this situation is visible."""
    run = {
        "id": 7, "mode": "lookup", "query": "Widget Expo", "status": "complete",
        "stage": "done", "error": None, "credits_spent": 0,
        "events": [{"id": 1, "name": "Widget Expo", "edition": "2026",
                    "website": "https://widgetexpo.com", "organizer": "Widget Media",
                    "starts_on": "2026-05-04", "ends_on": "2026-05-06",
                    "location": "Chicago, IL", "format": "in_person",
                    "stated_size": "9,000+ attendees", "fit_score": None}],
        "participants": [
            {"id": 1, "event_id": 1, "org_name": "Acme Robotics",
             "org_domain": "acme-robotics.com", "role": "exhibitor", "tier": None,
             "person_name": None, "person_title": None, "booth": "214", "note": None,
             "source_url": "https://widgetexpo.com/exhibitors",
             "resolution": "unresolved", "apollo": None, "icp_score": None},
            {"id": 2, "event_id": 1, "org_name": "Globex", "org_domain": None,
             "role": "sponsor", "tier": "Platinum", "person_name": None,
             "person_title": None, "booth": None, "note": None,
             "source_url": "https://widgetexpo.com/sponsors",
             "resolution": "unresolved", "apollo": None, "icp_score": None},
        ],
        "sources": [
            {"id": 1, "url": "https://widgetexpo.com/exhibitors", "kind": "exhibitors",
             "status": "ok", "http_status": 200, "rows_found": 1, "note": ""},
            {"id": 2, "url": "https://widgetexpo.com/sponsors", "kind": "sponsors",
             "status": "ok", "http_status": 200, "rows_found": 1, "note": ""},
            {"id": 3, "url": "https://widgetexpo.com/attendees", "kind": "attendees",
             "status": "blocked", "http_status": 403,
             "note": "Server refused the request (HTTP 403)."},
            {"id": 4, "url": "https://widgetexpo.com/speakers", "kind": "speakers",
             "status": "blocked", "http_status": 200,
             "note": "Returned only 90 characters of readable text."},
        ],
        "role_labels": dict(store.ROLE_LABELS),
        "summary": {
            "participants": 2, "organisations": 2, "resolvable_domains": 1,
            "by_role": {"exhibitor": 1, "sponsor": 1}, "declared_attendees": 0,
            "sources_tried": 4, "sources_read": 2, "sources_unreadable": 2,
            "roster_note": "PIPELINE_ROSTER_NOTE",
            "cost_estimate": {"domains": 1, "batches": 1, "max_credits": 1,
                              "note": "Up to 1 Apollo credit."},
        },
    }
    run.update(over)
    return run


# ── the wording contract ──────────────────────────────────────────────────

def test_only_one_role_is_ever_called_an_attendee():
    """ROLE_LABELS is the single place that decides what a row is called, and
    exactly one role may use the word 'attend'. If a future edit relabels
    exhibitors or sponsors as attendees, this is the tripwire."""
    attendeeish = [r for r, label in store.ROLE_LABELS.items()
                   if "attend" in label.lower()]
    assert attendeeish == [store.ROLE_ATTENDEE_DECLARED], attendeeish
    assert store.ROLE_LABELS[store.ROLE_EXHIBITOR] == "Exhibitor"
    assert store.ROLE_LABELS[store.ROLE_SPONSOR] == "Sponsor"


def test_every_role_has_a_label():
    """A role with no label would render as its raw enum value, which is how a
    wording contract quietly stops being enforced."""
    assert set(store.ROLES) == set(store.ROLE_LABELS)


def test_the_page_never_promises_an_attendee_list():
    """The copy on the page has to stay honest.

    "attendee list" is allowed to appear, but only inside a denial. A bare
    mention would read as a promise, so every occurrence must sit close after
    a negation. Phrases that cannot be honest in any context are banned
    outright.
    """
    body = _rendered_page().lower()
    for phrase in ("list of attendees", "full attendee", "everyone attending",
                   "all attendees", "complete attendee"):
        assert phrase not in body, "the page copy promises %r" % phrase

    negations = ("not ", "never", "do not", "don't", "rather than", "without")
    for m in re.finditer(r"attendee list", body):
        window = body[max(0, m.start() - 90):m.start()]
        assert any(n in window for n in negations), (
            "'attendee list' appears at offset %d without a nearby negation, "
            "which reads as a promise: ...%s" % (m.start(), body[max(0, m.start() - 90):m.end()]))


# ── the honesty contract, executed ────────────────────────────────────────

@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_report_states_the_roster_is_not_an_attendee_list():
    out = _run_in_node(
        "render(%s); console.log(JSON.stringify(__html));" % json.dumps(_run_fixture()),
        None)
    html = json.loads(out)["drawerBody"]
    assert "PIPELINE_ROSTER_NOTE" in html, (
        "the pipeline's roster caveat never reached the rendered report")


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_report_says_how_many_pages_could_not_be_read():
    """The single most important assertion in this file. A roster built from 2
    of 4 pages must say so, or a partial list reads as a complete one."""
    out = _run_in_node(
        "render(%s); console.log(JSON.stringify(__html));" % json.dumps(_run_fixture()),
        None)
    html = json.loads(out)["drawerBody"]
    assert "2 pages could not be read" in html, html[:900]
    assert "incomplete by an unknown amount" in html


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_fully_read_run_does_not_claim_pages_were_missed():
    """The mirror of the test above: the warning must be driven by the data,
    not printed unconditionally, or it becomes noise nobody reads."""
    run = _run_fixture()
    run["sources"] = [s for s in run["sources"] if s["status"] == "ok"]
    run["summary"]["sources_tried"] = 2
    run["summary"]["sources_unreadable"] = 0
    out = _run_in_node(
        "render(%s); console.log(JSON.stringify(__html));" % json.dumps(run), None)
    html = json.loads(out)["drawerBody"]
    assert "could not be read" not in html
    assert "Read 2 of 2 published pages" in html


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_an_exhibitor_renders_as_exhibitor_not_attendee():
    out = _run_in_node(
        "render(%s); console.log(JSON.stringify(__html));" % json.dumps(_run_fixture()),
        None)
    html = json.loads(out)["drawerBody"]
    assert "Acme Robotics" in html
    assert ">Exhibitor<" in html
    # The roster TABLE must never label a harvested row as attending. Bounded
    # at </table> on purpose: the Sources ledger below it legitimately shows
    # ".../attendees" as the URL of a page we tried and were blocked from,
    # and that is a fact about the fetch, not a claim about anybody.
    assert 'class="evi-table"' in html
    table = html.split('class="evi-table"', 1)[1].split("</table>", 1)[0]
    assert "attend" not in table.lower(), table[:600]


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_company_with_no_published_link_says_so_rather_than_guessing():
    """Globex has no domain in the fixture. The report must say no website was
    published, never render a guessed globex.com link. Guessing a domain from
    a name is the defect already logged against _cpi_probe_company_free."""
    out = _run_in_node(
        "render(%s); console.log(JSON.stringify(__html));" % json.dumps(_run_fixture()),
        None)
    html = json.loads(out)["drawerBody"]
    assert "no website published" in html
    assert "globex.com" not in html.lower()


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_unresolved_and_no_match_are_shown_differently():
    """'We never looked' and 'we looked and Apollo has nothing' are different
    facts, and collapsing them is how a surface starts asserting more than it
    knows."""
    run = _run_fixture()
    run["participants"][0]["resolution"] = "no_match"
    out = _run_in_node(
        "render(%s); console.log(JSON.stringify(__html));" % json.dumps(run), None)
    html = json.loads(out)["drawerBody"]
    assert "no Apollo match" in html
    assert "not looked up" in html


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_javascript_url_from_a_harvested_page_is_never_rendered_as_a_link():
    """Source URLs come from third-party pages, so they are untrusted input.
    safeUrl() must refuse anything that is not plain http(s)."""
    run = _run_fixture()
    run["participants"][0]["source_url"] = "javascript:alert(document.cookie)"
    run["sources"][0]["url"] = "javascript:alert(1)"
    out = _run_in_node(
        "render(%s); console.log(JSON.stringify(__html));" % json.dumps(run), None)
    html = json.loads(out)["drawerBody"]
    assert 'href="javascript:' not in html, html[:900]


# ── harvest-side guarantees ───────────────────────────────────────────────

def test_a_page_that_returns_almost_no_text_is_blocked_not_empty():
    """A JavaScript-rendered exhibitor directory returns a shell. Calling that
    'this event has no exhibitors' is the exact conflation this agent exists
    to avoid, so it must classify as blocked."""
    tiny = "<html><body><div id='root'></div></body></html>"
    assert len(harvest.html_to_linked_text(tiny)) < 400


def test_extractor_keeps_exhibitor_links_and_drops_script_content():
    markup = """<html><body>
      <script>var x = "<a href='https://evil.test'>ghost</a>";</script>
      <li><a href="https://acme-robotics.com/?utm=x">Acme Robotics</a> Booth 214</li>
      <li><img alt="Initech Software" src="/l.png"><a href="/x">More</a></li>
    </body></html>"""
    text = harvest.html_to_linked_text(markup, "https://expo.test/exhibitors")
    assert "https://acme-robotics.com/?utm=x" in text
    assert "Acme Robotics" in text
    assert "Booth 214" in text
    # Logo-only exhibitors survive via alt text, which is a real pattern.
    assert "Initech Software" in text
    # A link that existed only inside a <script> string is not a real link.
    assert "evil.test" not in text


def test_clean_domain_refuses_the_events_own_host_and_social_profiles():
    """Both failure modes make an entire roster point at one company."""
    assert harvest.clean_domain("https://expo.test/x", event_host="expo.test") is None
    assert harvest.clean_domain("https://www.linkedin.com/company/acme") is None
    assert harvest.clean_domain("https://twitter.com/acme") is None
    assert harvest.clean_domain("Acme Robotics") is None
    assert harvest.clean_domain("javascript:alert(1)") is None
    assert harvest.clean_domain("https://www.acme-robotics.com/a?b=c") == "acme-robotics.com"
    assert harvest.clean_domain("initech.co.uk") == "initech.co.uk"


def test_participants_with_an_unknown_role_are_dropped_not_coerced(monkeypatch):
    """A row whose role we cannot name cannot be rendered honestly. Defaulting
    it to 'exhibitor' would invent a claim about that company."""
    captured = {}

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass
        def executemany(self, sql, payload): captured["rows"] = payload

    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): pass
        def close(self): pass

    monkeypatch.setattr(store, "_pg_conn", lambda: _Conn())
    monkeypatch.setattr(store, "_ensure_tables", lambda conn: None)
    n = store.save_participants(1, 1, [
        {"org_name": "Good Co", "role": "exhibitor", "source_url": "https://e.test/a"},
        {"org_name": "Bad Co", "role": "attendee", "source_url": "https://e.test/a"},
        {"org_name": "Vague Co", "role": "", "source_url": "https://e.test/a"},
        {"org_name": "", "role": "exhibitor", "source_url": "https://e.test/a"},
        {"org_name": "No Source Co", "role": "exhibitor", "source_url": ""},
    ])
    assert n == 1, "only the well-formed row should land"
    assert [r[2] for r in captured["rows"]] == ["Good Co"]


# ── cost discipline ───────────────────────────────────────────────────────

def test_cost_estimate_batches_by_domain_rather_than_per_company():
    """mixed_companies/search bills per CALL, so 60 domains must cost 3, not
    60. If this ever reports 1 credit per company, the batching regressed and
    a large roster silently got 25x more expensive."""
    from tracker import event_intel_enrich as enrich
    est = enrich.estimate_cost(["c%d.test" % i for i in range(60)])
    assert est["domains"] == 60
    assert est["max_credits"] == 3
    assert enrich.estimate_cost([])["max_credits"] == 0
    # Duplicates are one company, not two lookups.
    assert enrich.estimate_cost(["a.test", "a.test"])["domains"] == 1


def test_the_billed_route_is_not_reachable_from_the_pipeline():
    """The harvest path must never call the credit-spending resolver. Only an
    explicit user action reaches a billed endpoint, which is the rule Contact
    Finder arrived at over thirteen audit rounds."""
    src = open(os.path.join(_ROOT, "tracker", "event_intel_pipeline.py"),
               encoding="utf-8").read()
    body = src.split("def run_job", 1)[0]
    assert "resolve_companies" not in body, (
        "the background pipeline reaches Apollo's billed company search")
    assert "find_people" not in body
