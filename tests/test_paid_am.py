"""tracker/paid_am.py: the account manager of an account as an alert names it, the saved Slack ids, and the line added to a Slack message.
Made-up clients, accounts and people only."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tracker import paid_am  # noqa: E402

ROWS = [{"client": "Acme Dental", "account": "Acme Dental", "am": "Asha", "channel": "Google"},
        {"client": "Acme Dental", "account": "Acme Ortho Group", "am": "Asha", "channel": "Google"},
        {"client": "Bolt Parts", "account": "B & K Machine LLC", "am": "Ravi", "channel": "Google"},
        {"client": "Glow Skin", "account": "Glow Skin Facebook", "am": "Mia", "channel": "Meta"},
        {"client": "Split Co", "account": "Split East", "am": "Asha", "channel": "Google"},
        {"client": "Split Co", "account": "Split West", "am": "Ravi", "channel": "Google"},
        {"client": "Nobody", "account": "Nobody Inc", "am": "", "channel": "Google"}]
OWN = paid_am.owners(ROWS)


def test_the_am_of_an_account_as_an_alert_names_it():
    am = lambda n: paid_am.am_of(n, OWN)  # noqa: E731
    assert am("Acme Ortho Group") == "Asha"
    assert am("B&amp;K Machine LLC") == "Ravi"                       # Slack-escaped, & spelled differently
    assert am("Acme Ortho Group (Acme Dental)") == "Asha"             # the default channel adds the client
    assert am("Some New Location (Acme Dental)") == "Asha"            # an account not in the sheet, its client is
    assert am("Glow Skin") == "Mia"                                   # one allocation name contains the other
    assert am("Split Co") == ""                                       # a client with two AMs is not guessed
    assert am("Nobody Inc") == "" and am("Unknown Account") == "" and am("226 P1") == ""


def test_saved_people_take_a_member_id_or_an_email():
    got = paid_am.clean_people({"people": [{"name": "Asha", "slack": "u0123abcd"}, {"name": "Ravi", "slack": "<@U0999ZZZZ|ravi>"},
                                           {"name": "Mia", "slack": "Mia@Example.com"}, {"name": "Empty", "slack": ""}]})
    assert got == {"Asha": "U0123ABCD", "Ravi": "U0999ZZZZ", "Mia": "mia@example.com"}
    with pytest.raises(paid_am.BadRequest):
        paid_am.clean_people({"people": [{"name": "Asha", "slack": "@asha"}]})
    rows = paid_am.people_list({"Old": "U0000OLD1", "Asha": "U0123ABCD"}, ["Asha", "Ravi"])
    assert [(r["name"], r["slack"], r["inAllocations"]) for r in rows] == [("Asha", "U0123ABCD", True), ("Ravi", "", True), ("Old", "U0000OLD1", False)]


def test_the_line_names_each_am_once_with_their_accounts_above_the_button():
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Paid alerts"}},
              {"type": "context", "elements": [{"type": "mrkdwn", "text": "*2 P1*"}]},
              {"type": "section", "text": {"type": "mrkdwn", "text": "\U0001F534 *Acme Dental*  ·  Google Ads\n•  Spend stopped"}},
              {"type": "section", "text": {"type": "mrkdwn", "text": "*Acme Ortho Group (Acme Dental)* (Google Ads)\nbroken link"}},
              {"type": "section", "text": {"type": "mrkdwn", "text": "*B&amp;K Machine LLC*\n•  *Unknown Account* too"}},
              {"type": "actions", "elements": []}]
    text, out = paid_am.tag("Paid alerts: 2 P1", blocks, OWN, {"Asha": "U0123ABCD"})
    line = out[-2]["elements"][0]["text"]
    assert out[-1]["type"] == "actions" and out[-2]["type"] == "context" and len(out) == len(blocks) + 1
    assert line == "Account managers: <@U0123ABCD> (Acme Dental, Acme Ortho Group)  ·  Ravi (B&K Machine LLC)"   # Ravi has no Slack id: named, not tagged
    assert text == "Paid alerts: 2 P1  <@U0123ABCD>"
    assert blocks[-1]["type"] == "actions" and len(blocks) == 6                                                        # the message handed in is not changed


def test_a_message_with_no_managed_account_is_left_alone():
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "*Unknown Account*"}}]
    assert paid_am.tag("t", blocks, OWN, {}) == ("t", blocks)


# ── app.py: the deliver wrapper and the Alert Hub's account managers box ─────

def _app():
    os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
    os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
    os.environ.setdefault("FLASK_SECRET_KEY", "test")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import app as app_module
    return app_module


class _Inner:
    NotConfigured = RuntimeError

    def __init__(self):
        self.sent = []

    def send_slack(self, channel, text, blocks):
        self.sent.append((channel, text, blocks))

    def ready(self):
        return {"slack": True}


def test_every_slack_alert_is_tagged_and_a_tagging_failure_still_sends(monkeypatch):
    a = _app()
    monkeypatch.setattr(a, "_am_owners", lambda force=False: {"own": OWN})
    monkeypatch.setattr(a, "_am_people", lambda: {"Asha": "U0123ABCD"})
    inner = _Inner()
    d = a._AmTaggingDeliver(inner)
    assert d.ready() == {"slack": True} and d.NotConfigured is RuntimeError
    d.send_slack("#paid-alerts", "x", [{"type": "section", "text": {"type": "mrkdwn", "text": "*Acme Dental*"}}])
    assert inner.sent[-1][2][-1]["elements"][0]["text"] == "Account managers: <@U0123ABCD> (Acme Dental)"
    monkeypatch.setattr(a, "_am_owners", lambda force=False: (_ for _ in ()).throw(RuntimeError("no database")))
    d.send_slack("#paid-alerts", "y", [{"type": "section", "text": {"type": "mrkdwn", "text": "*Acme Dental*"}}])
    assert inner.sent[-1][1] == "y" and len(inner.sent[-1][2]) == 1


def test_the_alert_hub_box_lists_the_ams_and_saves_member_ids(monkeypatch):
    from monitor_fakes import MemStore
    a = _app()
    s = MemStore()
    s.StoreUnavailable = type("StoreUnavailable", (Exception,), {})
    monkeypatch.setattr(a, "_mon_store", lambda: s)
    monkeypatch.setattr(a, "_am_owners", lambda force=False: {"own": OWN, "names": ["Asha", "Mia", "Ravi"], "source": "the Budget Tracker's DPR tab", "problem": ""})
    monkeypatch.setattr(a, "_slack_member_by_email", lambda e: "U0MIA0001")
    c = a.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "ana@position2.com", "name": "T"}
    got = c.get("/p2/paid-marketing/api/alerthub/ams").get_json()
    assert [p["name"] for p in got["people"]] == ["Asha", "Mia", "Ravi"] and got["source"] == "the Budget Tracker's DPR tab"
    r = c.post("/p2/paid-marketing/api/alerthub/ams", json={"people": [{"name": "Asha", "slack": "U0123ABCD"}, {"name": "Mia", "slack": "mia@example.com"}],
                                                          "sheet": "https://docs.google.com/spreadsheets/d/abcdefghijklmnopqrstuvwxyz0123/edit"})
    saved = s.state_get(paid_am.KEY)
    assert r.status_code == 200 and saved["people"] == {"Asha": "U0123ABCD", "Mia": "U0MIA0001"} and saved["sheet"].endswith("0123/edit")
    assert c.post("/p2/paid-marketing/api/alerthub/ams", json={"people": [], "sheet": "nope"}).status_code == 400
    assert c.post("/p2/paid-marketing/api/alerthub/ams", json={"people": [{"name": "Asha", "slack": "asha"}]}).status_code == 400
    html = c.get("/p2/paid-marketing/alert-hub").get_data(as_text=True)
    assert 'id="amCard"' in html and "js/alert_hub_ams.js" in html


def test_the_budget_trackers_dpr_tab_gives_the_newest_month_of_each_account():
    head = ["Client", "Account", "Allocated Budget", "Target CPA/ROAS/CPM", "Date", "Account Status", "Days Ads are Running", "AM", "Channels"]
    values = [head, ["Acme Dental", "Acme Dental", "100", "", "2026-09-01", "Active", "", "Asha", "Google"],
              ["Acme Dental", "Acme Dental", "100", "", "2026-10-01", "Active", "", "Ravi", "Google"],
              ["Glow Skin", "Glow Skin Facebook", "50", "", "2026-10-01", "Active", "", "Mia"], ["Short row"], ["X", "No AM", "", "", "2026-10-01", "", "", "", "Google"]]
    own = paid_am.owners(paid_am.parse_dpr(values))
    assert own["accounts"] == {"acmedental": "Ravi", "glowskinfacebook": "Mia"}
    assert paid_am.sheet_id("https://docs.google.com/spreadsheets/d/abcdefghijklmnopqrstuvwxyz0123/edit?gid=0#gid=0") == "abcdefghijklmnopqrstuvwxyz0123"
    with pytest.raises(paid_am.BadRequest):
        paid_am.sheet_id("not a link")
    with pytest.raises(paid_am.BadRequest):
        paid_am.parse_dpr([["Client", "Budget"]])
