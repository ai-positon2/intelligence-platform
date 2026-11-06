"""tracker/paid_changes.py: Change History, the Slack line for every Google Ads change made by someone not on the allowed list.
Made-up accounts, campaigns and people only."""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from tracker import paid_changes as pc  # noqa: E402

NOW = dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.timezone.utc)
ALLOW = ["asha@example.com", "ravi@example.com"]


def ev(rid, at, user, rtype, op, fields="", old=None, new=None, campaign="", client="GOOGLE_ADS_WEB_CLIENT"):
    return {"changeEvent": {"resourceName": rid, "changeDateTime": at, "userEmail": user, "clientType": client, "changeResourceType": rtype,
                            "resourceChangeOperation": op, "changedFields": fields, "oldResource": old or {}, "newResource": new or {}},
            "campaign": {"name": campaign} if campaign else {}}


ROWS = [
    ev("c1", "2026-11-06 10:00:01.5", "Jane@Agency.com", "AD_GROUP_CRITERION", "UPDATE", "status", new={"adGroupCriterion": {"status": "PAUSED", "keyword": {"text": "dentist"}}}, campaign="P2_Generic", client="GOOGLE_ADS_EDITOR"),
    ev("c2", "2026-11-06 10:00:02", "jane@agency.com", "AD_GROUP_CRITERION", "UPDATE", "status", new={"adGroupCriterion": {"status": "PAUSED", "keyword": {"text": "dentist near me"}}}, campaign="P2_Generic", client="GOOGLE_ADS_EDITOR"),
    ev("c3", "2026-11-06 10:01:00", "jane@agency.com", "CAMPAIGN_BUDGET", "UPDATE", "amountMicros", old={"campaignBudget": {"amountMicros": "50000000"}},
       new={"campaignBudget": {"amountMicros": "70000000"}}, campaign="P2_Brand", client="GOOGLE_ADS_EDITOR"),
    ev("c4", "2026-11-06 10:02:00", "jane@agency.com", "CAMPAIGN_CRITERION", "CREATE", "keyword.text,negative",
       new={"campaignCriterion": {"negative": True, "type": "KEYWORD", "keyword": {"text": "free"}}}, campaign="P2_Brand", client="GOOGLE_ADS_EDITOR"),
    ev("c5", "2026-11-06 11:00:00", "", "AD_GROUP_AD", "CREATE", "ad", new={"adGroupAd": {"status": "ENABLED"}}, campaign="P2_Brand", client="GOOGLE_ADS_RECOMMENDATIONS_SUBSCRIPTION"),
    ev("c6", "2026-11-06 11:30:00", "asha@example.com", "CAMPAIGN", "UPDATE", "status", new={"campaign": {"status": "PAUSED"}}, campaign="P2_Brand"),
]


def test_each_person_gets_one_line_saying_what_was_done():
    cs = pc.flagged(pc.read_changes(ROWS), ALLOW)
    lines = pc.lines_for({"id": "1", "name": "Acme Dental"}, cs)
    assert [(x["who"], x["via"]) for x in lines] == [("jane@agency.com", "Google Ads Editor"), ("a recommendation Google applied automatically", "")]
    assert lines[0]["line"] == 'Raised the P2_Brand budget from $50 to $70 a day, paused 2 keywords, added the negative keyword "free" (in P2_Generic). 4 changes.'
    assert lines[1]["line"] == "Added an ad (in P2_Brand). 1 change." and lines[0]["count"] == 4


def test_the_allowed_list_takes_addresses_and_whole_domains():
    assert pc.allowed("Asha@Example.com", ALLOW) and not pc.allowed("", ALLOW) and not pc.allowed("mia@example.com", ALLOW)
    assert pc.allowed("mia@example.com", ["@example.com"]) and not pc.allowed("mia@notexample.org", ["@example.com"])
    got = pc.clean_settings({"allow": "Asha@Example.com\nravi@example.com, asha@example.com\n@example.org", "channel": ""})
    assert got == {"allow": ["asha@example.com", "ravi@example.com", "@example.org"], "channel": pc.DEFAULT_CHANNEL}
    with pytest.raises(pc.BadRequest):
        pc.clean_settings({"allow": "not an address"})
    with pytest.raises(pc.BadRequest):
        pc.clean_settings({"allow": "", "channel": "bad channel!"})


def test_a_campaign_switched_off_is_named_and_many_actions_end_with_and_more():
    rows = [ev("p", "2026-11-06 09:00:00", "x@agency.com", "CAMPAIGN", "UPDATE", "status", new={"campaign": {"status": "PAUSED"}}, campaign="P2_Brand")]
    assert pc.summarize(pc.read_changes(rows)) == "Paused the campaign P2_Brand. 1 change."
    many = [ev("m%d" % i, "2026-11-06 09:00:%02d" % i, "x@agency.com", t, "CREATE") for i, t in enumerate(["AD_GROUP", "ASSET", "FEED", "AD_GROUP_AD", "CAMPAIGN_ASSET", "AD_GROUP_ASSET", "ASSET_SET", "CUSTOMER_ASSET"])]
    line = pc.summarize(pc.read_changes(many))
    assert line.startswith("Added an ad group, added an asset") and line.endswith(", and 2 more. 8 changes.")


def test_only_what_was_not_seen_is_new_and_a_first_read_is_quiet():
    cs = pc.read_changes(ROWS)
    fresh, keep = pc.new_since(cs, None)
    assert fresh == [] and keep["last"] == "2026-11-06 11:30:00" and set(keep["ids"]) == {"c1", "c2", "c3", "c4", "c5", "c6"}
    fresh, _ = pc.new_since(cs, {"last": "2026-11-06 10:01:00", "ids": ["c1", "c2", "c3"]})
    assert [c["id"] for c in fresh] == ["c4", "c5", "c6"]
    fresh, _ = pc.new_since(cs, {"last": "", "ids": []})                                  # an account that had no change at its first read
    assert len(fresh) == 6


class Store:
    def __init__(self):
        self.d = {}

    def state_get(self, k, default=None):
        return self.d.get(k, default)

    def state_set(self, k, v):
        self.d[k] = v


class Deliver:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_slack(self, channel, text, blocks):
        if self.fail:
            raise RuntimeError("not_in_channel")
        self.sent.append((channel, text, blocks))


ACCOUNTS = [{"id": "1111111111", "name": "Acme Dental", "platform": "google"}, {"id": "2222222222", "name": "Bolt Parts", "platform": "google"},
            {"id": "act_1", "name": "Glow Skin", "platform": "meta"}]


def _search(rows_by_account, fail=()):
    def search(cid, gaql):
        if cid in fail:
            raise RuntimeError("PERMISSION_DENIED")
        assert "FROM change_event" in gaql and gaql.endswith("LIMIT %d" % pc.LIMIT)
        return rows_by_account.get(cid, [])
    return search


def test_a_run_sets_the_baseline_then_sends_one_message_and_remembers_only_once_slack_took_it():
    s, d = Store(), Deliver()
    s.state_set(pc.KEY, {"allow": ALLOW, "channel": "C0TEST"})
    rec = pc.run(s, accounts=ACCOUNTS, deliver=d, search_fn=_search({"1111111111": ROWS[:2]}), link="https://p.example/x", trigger="schedule", now=NOW)
    assert rec["baseline"] and rec["read"] == 2 and rec["accounts"] == 2 and d.sent == [] and s.state_get(pc.SEEN_KEY)["2222222222"] == {"last": "", "ids": []}
    rows = {"1111111111": ROWS, "2222222222": [ev("b1", "2026-11-06 11:10:00", "ravi@example.com", "AD_GROUP", "UPDATE", "name", campaign="P2_X")]}
    failing = Deliver(fail=True)
    rec = pc.run(s, accounts=ACCOUNTS, deliver=failing, search_fn=_search(rows), link="https://p.example/x", trigger="schedule", now=NOW)
    assert rec["error"].startswith("Slack: not_in_channel") and s.state_get(pc.SEEN_KEY)["1111111111"]["last"] == "2026-11-06 10:00:02"
    rec = pc.run(s, accounts=ACCOUNTS, deliver=d, search_fn=_search(rows), link="https://p.example/x", trigger="schedule", now=NOW)
    assert rec["sent"] == 1 and rec["flagged"] == 3 and rec["accountsFlagged"] == 1                # c3, c4 by jane and c5 by Google; asha and ravi are allowed
    channel, text, blocks = d.sent[0]
    assert channel == "C0TEST" and text.startswith("Change History: 3 changes by someone other than the account managers in Acme Dental")
    acct = blocks[2]["text"]["text"]
    assert acct.startswith("*Acme Dental*\n•  jane@agency.com (Google Ads Editor): Raised the P2_Brand budget") and "•  a recommendation Google applied automatically: Added an ad" in acct
    assert blocks[-1]["type"] == "actions" and blocks[-1]["elements"][0]["url"] == "https://p.example/x"
    assert s.state_get(pc.RECENT_KEY)[0]["accountName"] == "Acme Dental"
    rec = pc.run(s, accounts=ACCOUNTS, deliver=d, search_fn=_search(rows), link="https://p.example/x", trigger="schedule", now=NOW)
    assert rec["flagged"] == 0 and len(d.sent) == 1                                                  # nothing is sent twice


def test_an_account_that_cannot_be_read_is_named_and_nothing_is_sent_without_an_allowed_list():
    s, d = Store(), Deliver()
    s.state_set(pc.SEEN_KEY, {"1111111111": {"last": "2026-11-05 00:00:00", "ids": []}})
    rec = pc.run(s, accounts=ACCOUNTS, deliver=d, search_fn=_search({"1111111111": ROWS}, fail={"2222222222"}), link="l", trigger="t", now=NOW)
    assert rec["unread"][0]["name"] == "Bolt Parts" and "PERMISSION_DENIED" in rec["unread"][0]["error"]
    assert rec["error"].startswith("No allowed addresses") and d.sent == [] and s.state_get(pc.SEEN_KEY)["1111111111"]["last"] == "2026-11-05 00:00:00"


def test_names_refused_by_google_are_read_without_them():
    calls = []

    def search(cid, gaql):
        calls.append(gaql)
        if "campaign.name" in gaql:
            raise RuntimeError("UNRECOGNIZED_FIELD")
        return ROWS[:1]
    got = pc.read_account("1111111111", search, NOW)
    assert len(calls) == 2 and "campaign.name" not in calls[1] and got[0]["id"] == "c1"


def test_many_accounts_are_split_over_several_messages():
    found = [{"account": {"id": str(i), "name": "Acct %03d" % i}, "lines": [{"who": "x@agency.com", "via": "", "line": "Edited an ad. 1 change.", "count": 1}]} for i in range(45)]
    msgs = pc.messages(found, "l", NOW)
    assert len(msgs) == 2 and msgs[0][1][0]["text"]["text"] == "Change History (1 of 2)" and len(msgs[1][1]) == 2 + 5 + 1


# ── app.py ───────────────────────────────────────────────────────────────────

def _app():
    os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
    os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
    os.environ.setdefault("FLASK_SECRET_KEY", "test")
    import app as app_module
    return app_module


def test_the_page_saves_the_allowed_list_and_the_scheduled_address_needs_the_secret(monkeypatch):
    from monitor_fakes import MemStore
    a = _app()
    s = MemStore()
    s.StoreUnavailable = type("StoreUnavailable", (Exception,), {})
    monkeypatch.setattr(a, "_mon_store", lambda: s)
    started = []
    monkeypatch.setattr(a, "_mon_spawn", lambda fn: started.append(fn))
    c = a.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "ana@position2.com", "name": "T"}
    assert 'id="setCard"' in c.get("/p2/paid-marketing/change-history").get_data(as_text=True)
    r = c.post("/p2/paid-marketing/api/changes/settings", json={"allow": "asha@example.com\nravi@example.com", "channel": "C0TEST"})
    assert r.status_code == 200 and s.state_get(pc.KEY)["allow"] == ["asha@example.com", "ravi@example.com"] and s.state_get(pc.KEY)["savedBy"] == "ana@position2.com"
    assert c.post("/p2/paid-marketing/api/changes/settings", json={"allow": "nope"}).status_code == 400
    got = c.get("/p2/paid-marketing/api/changes/state").get_json()
    assert got["settings"]["channel"] == "C0TEST" and got["runs"] == [] and got["baseline"] is False
    assert c.post("/p2/paid-marketing/api/changes/run", json={}).status_code == 202 and len(started) == 1
    monkeypatch.setenv("MONITOR_ALERT_SECRET", "shh")
    anon = a.app.test_client()
    assert anon.post("/p2/paid-marketing/api/changes/scheduled", headers={"X-Monitor-Secret": "wrong"}, json={}).status_code == 403
    assert anon.post("/p2/paid-marketing/api/changes/scheduled", headers={"X-Monitor-Secret": "shh"}, json={}).status_code == 202 and len(started) == 2
