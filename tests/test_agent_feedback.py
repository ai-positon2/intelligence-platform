"""Thumbs up/down feedback on Strategic Agents' generated reports.

Covers three layers: the store module fails soft with no DATABASE_URL (same
contract as every other tracker/*_store.py in this repo), the two Flask
routes validate their input and never 500 when Postgres isn't configured, and
the shared client-side widget (static/js/agent_feedback.js) is executed for
real -- not grepped -- to prove the down-then-reason flow PATCHes the same
row instead of inserting a second one, which is the one behaviour a
text-only test of this file could not tell apart from a bug that silently
double-counts every downvote with a reason.

The node-driven half is skipped, not failed, where node is unavailable, same
convention as tests/test_cpi_layout.py.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from tracker import agent_feedback  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "js", "agent_feedback.js")


@pytest.fixture
def client():
    """A real @position2.com session that is NOT in ADMIN_EMAILS -- every
    Strategic Agent route (and the feedback submit route) only requires
    position2_required, and this must stay true for a non-admin teammate."""
    assert "notanadmin@position2.com" not in appmod.ADMIN_EMAILS
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "notanadmin@position2.com", "name": "T"}
    return c


@pytest.fixture
def admin_client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "sudheer.d@position2.com", "name": "Admin"}
    return c


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """Every route test here runs with DATABASE_URL unset -- the point is
    that the routes stay well-behaved (200s with saved:false, never a 500)
    when Postgres isn't configured, exactly like every other best-effort
    datastore in this app."""
    monkeypatch.delenv("DATABASE_URL", raising=False)


# ── tracker/agent_feedback.py, direct ────────────────────────────────────────

def test_only_the_five_agents_with_a_real_generated_report_are_allowed():
    """The other five agents under Strategic Agents (Job Change Alert,
    ad-intelligence, linkedin-intelligence, sentiment-pulse, the hidden
    LinkedIn Social Researcher) show raw data or a third-party surface, not a
    generated analysis -- there is nothing there for a thumbs-down to be
    about. This is the one place that boundary is enforced, not just
    documented."""
    assert set(agent_feedback.AGENT_LABELS) == {
        "company-people-intelligence",
        "linkedin-strategy-researcher",
        "42-north-dental-slot-checker",
        "social-media-intelligence",
        "event-conference-intelligence",
    }


def test_save_fails_soft_with_no_database_url():
    assert agent_feedback.save(email="a@position2.com", agent_slug="company-people-intelligence",
                                run_id=None, section_key="chat:1", section_label=None,
                                rating="down", reason="wrong company") is None


def test_save_rejects_a_bad_rating_before_ever_touching_postgres():
    """Guards against a future caller (or a compromised client) writing a
    rating the CHECK constraint would reject anyway, but doing it in Python
    means a bad value never reaches _pg_conn() at all."""
    assert agent_feedback.save(email="a@position2.com", agent_slug="company-people-intelligence",
                                run_id=None, section_key="chat:1", section_label=None,
                                rating="sideways", reason=None) is None


def test_list_recent_and_summary_return_empty_not_none_or_a_crash():
    assert agent_feedback.list_recent() == []
    assert agent_feedback.summary() == []


def test_update_reason_returns_false_rather_than_raising():
    assert agent_feedback.update_reason(1, "a@position2.com", "still wrong") is False


# ── POST /api/agent-feedback ─────────────────────────────────────────────────

def test_feedback_requires_a_position2_session(app_client=None):
    c = appmod.app.test_client()  # no session at all
    r = c.post("/api/agent-feedback", json={
        "agent_slug": "company-people-intelligence", "rating": "up", "section_key": "chat:1"})
    assert r.status_code in (302, 401, 403)


def test_unknown_agent_slug_is_refused(client):
    r = client.post("/api/agent-feedback", json={
        "agent_slug": "not-a-real-agent", "rating": "up", "section_key": "chat:1"})
    assert r.status_code == 404
    assert r.get_json()["saved"] is False


def test_rating_must_be_up_or_down(client):
    r = client.post("/api/agent-feedback", json={
        "agent_slug": "company-people-intelligence", "rating": "meh", "section_key": "chat:1"})
    assert r.status_code == 400


def test_section_key_is_required(client):
    r = client.post("/api/agent-feedback", json={
        "agent_slug": "company-people-intelligence", "rating": "up", "section_key": ""})
    assert r.status_code == 400


def test_a_well_formed_submission_never_500s_without_postgres(client):
    """The honest failure mode when DATABASE_URL isn't set: a 200 saying the
    vote was not recorded, not a server error -- the same contract
    agent_run_history and every tracker/*_store.py module already promise."""
    r = client.post("/api/agent-feedback", json={
        "agent_slug": "event-conference-intelligence", "rating": "down",
        "run_id": 42, "section_key": "recommend:top_five",
        "section_label": "Top five", "reason": "missed an obvious event"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["saved"] is False
    assert body["id"] is None


def test_add_reason_route_also_degrades_cleanly(client):
    r = client.post("/api/agent-feedback/999/reason", json={"reason": "actually fine"})
    assert r.status_code == 200
    assert r.get_json()["saved"] is False


# ── Admin review page ────────────────────────────────────────────────────────

def test_admin_page_is_refused_to_a_non_admin_position2_user(client):
    r = client.get("/p2/admin/agent-feedback")
    assert r.status_code == 403


def test_admin_page_loads_for_an_admin(admin_client):
    r = admin_client.get("/p2/admin/agent-feedback")
    assert r.status_code == 200
    assert b"Agent Feedback" in r.data


def test_admin_data_endpoint_is_admin_only_and_shape_holds(admin_client, client):
    assert client.get("/p2/admin/agent-feedback/data").status_code == 403
    r = admin_client.get("/p2/admin/agent-feedback/data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["summary"] == []
    assert body["recent"] == []


# ── static/js/agent_feedback.js, executed for real ───────────────────────────

pytestmark_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

_DRIVER = r"""
const fs = require("fs");
const bundle = fs.readFileSync(process.argv[2], "utf8");

/* A small real DOM: enough to parse the widget's own generated HTML into a
   tree with working classList/closest/querySelector, rather than a shim that
   answers every query with null (see tests/test_cpi_layout.py's docstring
   for why that specific shortcut hides real bugs). */
function Elem(tag){
  this.tagName = tag; this.attrs = {}; this.children = []; this.parentNode = null;
  this.hidden = false; this.value = ""; this._classSet = new Set();
}
Elem.prototype.getAttribute = function(n){ return n in this.attrs ? this.attrs[n] : null; };
Elem.prototype.setAttribute = function(n, v){ this.attrs[n] = String(v); };
Elem.prototype.appendChild = function(c){ c.parentNode = this; this.children.push(c); return c; };
Elem.prototype.matches = function(sel){ return sel[0] === "." && this._classSet.has(sel.slice(1)); };
Elem.prototype.closest = function(sel){
  var n = this;
  while (n) { if (n.matches && n.matches(sel)) return n; n = n.parentNode; }
  return null;
};
Elem.prototype.querySelectorAll = function(sel){
  var out = [];
  (function walk(node){
    node.children.forEach(function(c){ if (c.matches(sel)) out.push(c); walk(c); });
  })(this);
  return out;
};
Elem.prototype.querySelector = function(sel){ return this.querySelectorAll(sel)[0] || null; };
Elem.prototype.focus = function(){};
Object.defineProperty(Elem.prototype, "classList", { get: function(){
  var self = this;
  return {
    add: function(c){ self._classSet.add(c); },
    remove: function(c){ self._classSet.delete(c); },
    contains: function(c){ return self._classSet.has(c); },
    toggle: function(c, on){ if (on === undefined) on = !self._classSet.has(c);
      if (on) self._classSet.add(c); else self._classSet.delete(c); return on; },
  };
}});

function parseHtml(html){
  var root = new Elem("root");
  var stack = [root];
  var re = /<(\/?)([a-zA-Z0-9]+)((?:\s+[a-zA-Z0-9_-]+(?:="[^"]*")?)*)\s*>/g;
  var m;
  while ((m = re.exec(html))) {
    var closing = m[1] === "/", tag = m[2], attrStr = m[3] || "";
    if (closing) { stack.pop(); continue; }
    var el = new Elem(tag);
    var attrRe = /([a-zA-Z0-9_-]+)(?:="([^"]*)")?/g, am;
    while ((am = attrRe.exec(attrStr))) {
      var name = am[1], val = am[2] === undefined ? "" : am[2];
      if (name === "class") val.split(/\s+/).filter(Boolean).forEach(function(c){ el._classSet.add(c); });
      el.attrs[name] = val;
      if (name === "hidden") el.hidden = true;
    }
    stack[stack.length - 1].appendChild(el);
    stack.push(el);
  }
  return root;
}

var CLICK_HANDLERS = [];
global.document = { addEventListener: function(type, fn){ if (type === "click") CLICK_HANDLERS.push(fn); } };
global.window = global;

var CALLS = [];
global.fetch = function(url, opts){
  CALLS.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null });
  var isReasonPatch = /\/reason$/.test(String(url));
  var payload = isReasonPatch ? { saved: true } : { saved: true, id: 77 };
  return Promise.resolve({ json: function(){ return Promise.resolve(payload); } });
};

eval(bundle);

function click(el){ CLICK_HANDLERS.forEach(function(fn){ fn({ target: el }); }); }
function flush(){ return new Promise(function(r){ setTimeout(r, 0); }); }

(async function(){
  var out = {};

  var html = window.agentFeedbackHtml({
    agentSlug: "company-people-intelligence", runId: 123,
    sectionKey: "chat:1", sectionLabel: 'A "quoted" <label>'
  });
  out.htmlEscapesTheLabel = html.indexOf('<label>') === -1 && html.indexOf("&lt;label&gt;") !== -1;
  out.htmlEscapesTheQuote = html.indexOf('"A "quoted"') === -1;

  var root = parseHtml(html).children[0];
  var upBtn = root.querySelector(".agent-fb-up");
  var downBtn = root.querySelector(".agent-fb-down");
  var reasonBox = root.querySelector(".agent-fb-reason");
  var reasonInput = root.querySelector(".agent-fb-reason-input");
  var sendBtn = root.querySelector(".agent-fb-reason-send");

  out.reasonBoxHiddenBeforeAnyClick = reasonBox.hidden;

  click(downBtn);
  await flush();
  out.reasonBoxVisibleAfterDown = !reasonBox.hidden;
  out.firstCallIsImmediatePost = CALLS.length === 1 && CALLS[0].url === "/api/agent-feedback" &&
    CALLS[0].body.rating === "down" && CALLS[0].body.reason === "" &&
    CALLS[0].body.section_key === "chat:1" && CALLS[0].body.run_id === "123";
  out.rootCarriesReturnedId = root.getAttribute("data-fb-id") === "77";

  reasonInput.value = "cited the wrong company entirely";
  click(sendBtn);
  await flush();
  out.secondCallPatchesTheSameId = CALLS.length === 2 &&
    CALLS[1].url === "/api/agent-feedback/77/reason" &&
    CALLS[1].body.reason === "cited the wrong company entirely";
  out.reasonBoxHiddenAfterSend = reasonBox.hidden;

  click(upBtn);
  await flush();
  out.thirdCallIsAPlainUpvote = CALLS.length === 3 && CALLS[2].body.rating === "up";
  out.reasonBoxHiddenAfterUpvote = reasonBox.hidden;

  console.log(JSON.stringify(out));
})();
"""


def _run_driver():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "driver.js")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(_DRIVER)
        proc = subprocess.run([shutil.which("node"), p, _JS], capture_output=True,
                               text=True, timeout=60)
    if proc.returncode != 0:
        pytest.fail("driver failed: %s" % (proc.stderr or proc.stdout)[-3000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def driven():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    return _run_driver()


def test_the_builder_escapes_a_label_that_looks_like_markup(driven):
    assert driven["htmlEscapesTheLabel"]


def test_the_builder_escapes_a_quote_inside_a_data_attribute(driven):
    assert driven["htmlEscapesTheQuote"]


def test_reason_box_starts_hidden(driven):
    assert driven["reasonBoxHiddenBeforeAnyClick"]


def test_a_thumbs_down_posts_immediately_with_no_reason_yet(driven):
    """The whole point of 'optional reason' (see the PR discussion): a
    downvote must never be lost to someone who taps it and then closes the
    drawer before typing anything."""
    assert driven["firstCallIsImmediatePost"]


def test_the_reason_box_opens_right_after_a_down_tap(driven):
    assert driven["reasonBoxVisibleAfterDown"]


def test_the_returned_id_is_kept_on_the_widget(driven):
    assert driven["rootCarriesReturnedId"]


def test_typing_a_reason_afterwards_patches_the_same_row_not_a_new_one(driven):
    """The regression this test exists to catch: a naive implementation would
    POST a second full feedback row here, silently double-counting one
    person's single downvote in every aggregate on the admin page."""
    assert driven["secondCallPatchesTheSameId"]


def test_reason_box_closes_after_sending(driven):
    assert driven["reasonBoxHiddenAfterSend"]


def test_a_thumbs_up_never_opens_the_reason_box(driven):
    assert driven["thirdCallIsAPlainUpvote"]
    assert driven["reasonBoxHiddenAfterUpvote"]
