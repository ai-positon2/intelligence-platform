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
    assert got == {"allow": ["asha@example.com", "ravi@example.com", "@example.org"], "channel": pc.DEFAULT_CHANNEL, "canvas": {}}
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
    def __init__(self, fail=False, fail_canvas=False):
        self.sent, self.fail, self.fail_canvas = [], fail, fail_canvas
        self.canvases, self._next_canvas = {}, 0

    def send_slack(self, channel, text, blocks):
        if self.fail:
            raise RuntimeError("not_in_channel")
        self.sent.append((channel, text, blocks))

    def canvas_create(self, title, channel, markdown):
        if self.fail_canvas:
            raise RuntimeError("missing_scope")
        self._next_canvas += 1
        cid = "F%d" % self._next_canvas
        self.canvases[cid] = {"title": title, "channel": channel, "markdown": [markdown]}
        return cid

    def canvas_append(self, canvas_id, markdown):
        if self.fail_canvas:
            raise RuntimeError("missing_scope")
        self.canvases[canvas_id]["markdown"].append(markdown)


ACCOUNTS = [{"id": "1111111111", "name": "Acme Dental", "platform": "google"}, {"id": "2222222222", "name": "Bolt Parts", "platform": "google"},
            {"id": "act_1", "name": "Glow Skin", "platform": "meta"}]


def _search(rows_by_account, fail=(), access=None):
    def search(cid, gaql):
        if cid in fail:
            raise RuntimeError("PERMISSION_DENIED")
        if "FROM change_event" not in gaql:
            return ((access or {}).get(cid) or {}).get(gaql.rsplit(" ", 1)[-1], [])
        assert "FROM change_event" in gaql and gaql.endswith("LIMIT %d" % pc.LIMIT)
        return rows_by_account.get(cid, [])
    return search


def test_the_canvas_accounts_setting_takes_a_dict_or_textarea_lines():
    assert pc.clean_settings({"allow": "", "channel": "", "canvas": {"Glow Skin": "C0TEST1"}})["canvas"] == {"Glow Skin": "C0TEST1"}
    assert pc.clean_settings({"allow": "", "channel": "", "canvas": "Glow Skin -> C0TEST1\n\nBolt Parts : #bolt-internal\n"})["canvas"] == \
        {"Glow Skin": "C0TEST1", "Bolt Parts": "#bolt-internal"}
    assert pc.clean_settings({"allow": "", "channel": ""})["canvas"] == {}
    with pytest.raises(pc.BadRequest):
        pc.clean_settings({"allow": "", "channel": "", "canvas": "not a pair"})
    with pytest.raises(pc.BadRequest):
        pc.clean_settings({"allow": "", "channel": "", "canvas": {"Glow Skin": "bad channel!"}})


def test_a_canvas_account_goes_only_to_its_own_canvas_never_the_shared_channel():
    s, d = Store(), Deliver()
    accounts = ACCOUNTS       # "Glow Skin" is already one of them (a Meta account here, but the setting only looks at the name)
    s.state_set(pc.KEY, {"allow": ALLOW, "channel": "C0TEST", "canvas": {"Bolt Parts": "C0BOLTINTERNAL"}})
    pc.run(s, accounts=accounts, deliver=d, search_fn=_search({}), link="https://p.example/x", trigger="schedule", now=NOW)   # baseline
    bolt1 = ev("b1", "2026-11-06 09:00:00", "ravi@agency.com", "CAMPAIGN", "UPDATE", "status", new={"campaign": {"status": "PAUSED"}}, campaign="P2-PMax")
    rows = {"1111111111": ROWS[:1], "2222222222": [bolt1]}
    rec = pc.run(s, accounts=accounts, deliver=d, search_fn=_search(rows), link="https://p.example/x", trigger="schedule", now=NOW)
    assert rec["sent"] == 2 and rec["accountsFlagged"] == 2                                       # one chat message (Acme), one canvas append (Bolt Parts)
    assert len(d.sent) == 1 and "Acme Dental" in d.sent[0][1] and "Bolt Parts" not in d.sent[0][1] and "Bolt Parts" not in d.sent[0][2][2]["text"]["text"]
    cid = s.state_get(pc.CANVAS_KEY)["Bolt Parts"]
    assert d.canvases[cid]["title"] == pc.CANVAS_TITLE and d.canvases[cid]["channel"] == "C0BOLTINTERNAL" and len(d.canvases) == 1
    assert "ravi@agency.com" in d.canvases[cid]["markdown"][1] and "Paused the campaign P2-PMax" in d.canvases[cid]["markdown"][1]
    # a second run with another Bolt Parts change appends to the same canvas rather than creating a new one
    bolt2 = ev("b2", "2026-11-06 10:00:00", "ravi@agency.com", "CAMPAIGN_BUDGET", "UPDATE", "amountMicros",
               old={"campaignBudget": {"amountMicros": "50000000"}}, new={"campaignBudget": {"amountMicros": "70000000"}}, campaign="P2-PMax")
    pc.run(s, accounts=accounts, deliver=d, search_fn=_search({"2222222222": [bolt1, bolt2]}), link="https://p.example/x", trigger="schedule", now=NOW)
    assert len(d.canvases) == 1 and len(d.canvases[cid]["markdown"]) == 3 and "70" in d.canvases[cid]["markdown"][2]
    # a canvas failure is not remembered, so it is sent again, and never falls back to the shared channel
    s2, failing = Store(), Deliver(fail_canvas=True)
    s2.state_set(pc.KEY, {"allow": ALLOW, "channel": "C0TEST", "canvas": {"Bolt Parts": "C0BOLTINTERNAL"}})
    pc.run(s2, accounts=accounts, deliver=failing, search_fn=_search({}), link="https://p.example/x", trigger="schedule", now=NOW)   # baseline
    rec = pc.run(s2, accounts=accounts, deliver=failing, search_fn=_search({"2222222222": [bolt1]}), link="https://p.example/x", trigger="schedule", now=NOW)
    assert rec["error"].startswith("Slack canvas (Bolt Parts): missing_scope") and failing.sent == [] and failing.canvases == {}


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


def _access(managers=(), users=(), conversions=()):
    return {"customer_manager_link": [{"customerManagerLink": {"managerCustomer": "customers/" + m, "status": st}} for m, st in managers],
            "customer_user_access": [{"customerUserAccess": {"emailAddress": e, "accessRole": r, "inviterUserEmailAddress": i}} for e, r, i in users],
            "conversion_action": [{"conversionAction": {"id": c, "name": n, "status": st, "primaryForGoal": pr, "countingType": ct}} for c, n, st, pr, ct in conversions]}


def test_access_manager_links_and_conversion_actions_are_compared_with_the_last_run():
    s, d = Store(), Deliver()
    s.state_set(pc.KEY, {"allow": ALLOW, "channel": "C0TEST"})
    before = {"1111111111": _access(managers=[("1234567890", "ACTIVE")], users=[("asha@example.com", "ADMIN", "")],
                                    conversions=[("7", "Lead form", "ENABLED", True, "ONE_PER_CLICK")])}
    run = lambda acc: pc.run(s, accounts=ACCOUNTS, deliver=d, search_fn=_search({}, access=acc), link="https://p.example/x", trigger="schedule", now=NOW)
    assert run(before)["baseline"] and d.sent == []
    assert run(before)["flagged"] == 0 and d.sent == []                                             # nothing changed
    after = {"1111111111": _access(managers=[("1234567890", "INACTIVE"), ("9998887776", "PENDING")],
                                   users=[("asha@example.com", "STANDARD", ""), ("new@agency.com", "ADMIN", "jane@agency.com"), ("ok@example.com", "READ_ONLY", "ravi@example.com")],
                                   conversions=[("7", "Lead form", "ENABLED", False, "ONE_PER_CLICK"), ("8", "Calls", "ENABLED", True, "MANY_PER_CLICK")])}
    rec = run(after)
    assert rec["flagged"] == 6 and len(d.sent) == 1                                                   # ok@ was added by ravi, who is allowed
    text = d.sent[0][2][2]["text"]["text"]
    assert "•  jane@agency.com: Gave new@agency.com admin access. 1 change." in text
    line = next(r for r in text.splitlines() if r.startswith("•  someone (Google does not say who):"))
    for bit in ("changed the link to the manager account 123-456-7890 from linked to unlinked", "invited the manager account 999-888-7776",
                "changed asha@example.com's access from admin to standard", 'changed the conversion action "Lead form" (made it secondary)',
                'added the conversion action "Calls"'):
        assert bit in line.lower() or bit in line, bit
    assert run(after)["flagged"] == 0                                                                # remembered once Slack took it


def test_a_part_google_refuses_is_kept_as_it_was_and_not_compared():
    old = {"managers": {"123-456-7890": "ACTIVE"}, "users": {"a@example.com": {"role": "ADMIN", "inviter": ""}}, "conversions": {}}
    new = {"managers": None, "users": {}, "conversions": None}
    out = pc.access_changes(old, new, "2026-11-06 12:00:00")
    assert [c["phrase"] for c in out] == ["removed a@example.com's access"]
    assert pc._merge_access(old, new)["managers"] == {"123-456-7890": "ACTIVE"}


def test_an_account_that_cannot_be_read_is_named_and_nothing_is_sent_without_an_allowed_list():
    s, d = Store(), Deliver()
    s.state_set(pc.SEEN_KEY, {"1111111111": {"last": "2026-11-05 00:00:00", "ids": []}})
    rec = pc.run(s, accounts=ACCOUNTS, deliver=d, search_fn=_search({"1111111111": ROWS}, fail={"2222222222"}), link="l", trigger="t", now=NOW)
    assert rec["unread"][0]["name"] == "Bolt Parts" and "PERMISSION_DENIED" in rec["unread"][0]["error"]
    assert rec["error"].startswith("No allowed addresses") and d.sent == [] and s.state_get(pc.SEEN_KEY)["1111111111"]["last"] == "2026-11-05 00:00:00"


def test_the_read_joins_nothing_and_the_campaigns_are_named_apart():
    calls = []
    rows = [{"changeEvent": dict(ROWS[0]["changeEvent"], campaign="customers/1/campaigns/5")},
            ev("x2", "2026-11-06 10:05:00", "tom@agency.com", "CAMPAIGN", "UPDATE", "targetCpa")]   # a campaign setting: no ad group

    def search(cid, gaql, refuse=False):
        calls.append(gaql)
        if "FROM change_event" in gaql:
            assert "campaign.name" not in gaql and "ad_group.name" not in gaql   # a joined name leaves out every change without one
            return rows
        if "FROM change_status" in gaql:
            return []
        assert "'customers/1/campaigns/5'" in gaql
        return [{"campaign": {"resourceName": "customers/1/campaigns/5", "name": "P2_Generic"}}]
    got = pc.read_account("1111111111", search, NOW)
    assert len(calls) == 2 and [c["id"] for c in got] == ["c1", "x2"] and got[0]["campaign"] == "P2_Generic"

    def refusing(cid, gaql):
        if "FROM campaign " in gaql:
            raise RuntimeError("PERMISSION_DENIED")
        return rows
    got = pc.read_account("1111111111", refusing, NOW)
    assert [c["id"] for c in got] == ["c1", "x2"] and got[0]["campaign"] == ""


def test_many_accounts_are_split_over_several_messages():
    found = [{"account": {"id": str(i), "name": "Acct %03d" % i}, "lines": [{"who": "x@agency.com", "via": "", "line": "Edited an ad. 1 change.", "count": 1}]} for i in range(45)]
    msgs = pc.messages(found, "l", NOW)
    assert len(msgs) == 2 and msgs[0][1][0]["text"]["text"] == "Change History (1 of 2)" and len(msgs[1][1]) == 2 + 5 + 1


def test_a_day_is_read_whole_for_the_page_and_nothing_is_sent_or_remembered():
    s = Store()
    s.state_set(pc.KEY, {"allow": ALLOW})
    asked = []

    def search(cid, gaql):
        asked.append((cid, gaql))
        return ROWS if cid == "1111111111" else []
    day = dt.date(2026, 11, 5)
    got = pc.day_report(s, accounts=ACCOUNTS, search_fn=search, day=day)
    assert "'2026-11-05 00:00:00'" in asked[0][1] and "'2026-11-05 23:59:59'" in asked[0][1] and {a for a, _ in asked} == {"1111111111", "2222222222"}
    assert got["read"] == 2 and got["flagged"] == 5 and [a["name"] for a in got["accounts"]] == ["Acme Dental"]          # Bolt Parts had nothing flagged
    acme = got["accounts"][0]
    assert acme["total"] == 6 and acme["allowed"] == 1 and [x["allowed"] for x in acme["lines"]] == [False, False]
    every = pc.day_report(s, accounts=ACCOUNTS, search_fn=search, day=day, everyone=True)["accounts"][0]["lines"]
    assert [(x["who"], x["allowed"]) for x in every][-1] == ("asha@example.com", True)
    one = pc.day_report(s, accounts=ACCOUNTS, search_fn=search, day=day, account="2222222222")
    assert one["accounts"] == [{"id": "2222222222", "name": "Bolt Parts", "total": 0, "allowed": 0, "flagged": 0, "lines": [], "unnamed": None}]
    assert pc.SEEN_KEY not in s.d and pc.RUNS_KEY not in s.d
    with pytest.raises(pc.BadRequest):
        pc.day_report(s, accounts=ACCOUNTS, search_fn=search, day=day, account="act_1")
    assert pc.clean_day("2026-10-08", dt.date(2026, 11, 6)) == dt.date(2026, 10, 8)
    for bad in ("2026-10-07", "2026-11-07", "yesterday", ""):
        with pytest.raises(pc.BadRequest):
            pc.clean_day(bad, dt.date(2026, 11, 6))


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


def test_the_day_and_accounts_routes(monkeypatch):
    from monitor_fakes import MemStore
    from tracker import paid_gads_segments as g
    a = _app()
    s = MemStore()
    s.StoreUnavailable = type("StoreUnavailable", (Exception,), {})
    monkeypatch.setattr(a, "_mon_store", lambda: s)
    monkeypatch.setattr(a, "_mon_inventory", lambda: (ACCOUNTS, [], []))
    monkeypatch.setattr(g, "list_accounts", lambda: [{"id": "1111111111", "currency": "USD"}])
    monkeypatch.setattr(g, "search", lambda cid, gaql: ROWS if cid == "1111111111" else [])
    c = a.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "ana@position2.com", "name": "T"}
    assert c.get("/p2/paid-marketing/api/changes/accounts").get_json()["accounts"] == [{"id": "1111111111", "name": "Acme Dental"}, {"id": "2222222222", "name": "Bolt Parts"}]
    yesterday = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
    got = c.post("/p2/paid-marketing/api/changes/day", json={"date": yesterday, "account": "1111111111"}).get_json()
    assert got["date"] == yesterday and got["of"] == 1 and got["noAllowList"] is True and got["flagged"] == 6
    assert c.post("/p2/paid-marketing/api/changes/day", json={"date": "2020-01-01"}).status_code == 400
    assert 'id="dayCard"' in c.get("/p2/paid-marketing/change-history").get_data(as_text=True)


def test_the_raw_check_reads_a_day_several_ways_and_counts_only():
    asked = []

    def search(cid, gaql):
        asked.append(gaql)
        if "FROM change_status" in gaql:
            return [{"changeStatus": {"resourceType": "AD_GROUP", "resourceStatus": "ENABLED"}}] * 2
        return ROWS[:3]
    out = pc.raw_check("1111111111", search, dt.date(2026, 11, 6))
    assert out["run"]["rows"] == 3 and out["noOrder"]["rows"] == 3 and "ORDER BY" not in asked[1] and "2026-11-05 00:00:00" in asked[2]
    assert out["run"]["groups"] == {"2026-11-06 | jane@agency.com | GOOGLE_ADS_EDITOR | AD_GROUP_CRITERION": 2, "2026-11-06 | jane@agency.com | GOOGLE_ADS_EDITOR | CAMPAIGN_BUDGET": 1}
    assert out["changeStatus"] == {"rows": 2, "groups": {"AD_GROUP | ENABLED": 2}}


def test_what_change_status_lists_and_change_event_does_not_explain_is_flagged_without_a_name():
    events = [{"changeEvent": dict(ROWS[0]["changeEvent"], changeResourceName="customers/1/ads/77", changeResourceType="AD", campaign="customers/1/campaigns/5")}]
    status = [{"changeStatus": {"resourceType": "AD_GROUP_AD", "resourceStatus": "CHANGED", "lastChangeDateTime": "2026-11-06 10:00:01.5",
                                "adGroupAd": "customers/1/adGroupAds/3~77", "campaign": "customers/1/campaigns/5"}}]                # explained by the AD row
    status += [{"changeStatus": {"resourceType": "AD_GROUP_AD", "resourceStatus": st, "lastChangeDateTime": "2026-11-06 11:%02d:00" % n,
                                 "adGroupAd": "customers/1/adGroupAds/3~%d" % (100 + n), "campaign": "customers/1/campaigns/5"}}
               for n, st in enumerate(["CHANGED", "CHANGED", "REMOVED"])]
    status += [{"changeStatus": {"resourceType": "ASSET_GROUP", "resourceStatus": "CHANGED", "lastChangeDateTime": "2026-11-06 11:30:00",
                                 "assetGroup": "customers/1/assetGroups/9", "campaign": "customers/1/campaigns/6"}},
               {"changeStatus": {"resourceType": "UNKNOWN", "resourceStatus": "CHANGED", "lastChangeDateTime": "2026-11-06 11:40:00",
                                 "resourceName": "customers/1/changeStatus/x1"}}]

    def search(cid, gaql):
        if "FROM change_event" in gaql:
            return events
        if "FROM change_status" in gaql:
            return status
        return [{"campaign": {"resourceName": "customers/1/campaigns/5", "name": "P2_Generic"}}, {"campaign": {"resourceName": "customers/1/campaigns/6", "name": "P2_PMax"}}]
    got = pc.read_account("1111111111", search, NOW)
    assert [c for c in got if c["client"] == "STATUS"] == []      # a run never takes change_status: it counts Google's own automatic updates too
    got = pc.read_between("1111111111", search, "2026-11-06 00:00:00", "2026-11-06 23:59:59", with_status=True)
    extra = [c for c in got if c["client"] == "STATUS"]
    assert len(got) == 6 and len(extra) == 5
    lines = pc.lines_for({"id": "1", "name": "Acme Dental"}, extra)
    assert [x["who"] for x in lines] == ["someone (Google does not say who)"]
    assert lines[0]["line"] == "Edited 2 ads, removed an ad, edited an asset group, edited an item of another kind (in P2_Generic, P2_PMax). 5 changes."
    s = Store()
    s.state_set(pc.KEY, {"allow": ALLOW})
    day = pc.day_report(s, accounts=ACCOUNTS[:1], search_fn=search, day=dt.date(2026, 11, 6), everyone=True)
    acme = day["accounts"][0]
    assert day["flagged"] == 1 and acme["total"] == 1 and acme["flagged"] == 1                     # only the change Google names a person for
    assert acme["unnamed"]["count"] == 5 and acme["unnamed"]["line"].startswith("Edited 2 ads, removed an ad")
