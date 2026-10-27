"""Paid Marketing Agents: Unknown Domains Alert, the scan.

Pinned here: nothing is alerted before a baseline, and setting it trusts what accounts use (scoped to each) and marks the rest seen; a new finding alerts
once, as one digest by email and Slack; a channel that is not set is said, not hidden, and the finding waits for one that is; a send that fails leaves the
finding to be tried again; a burst of unknown domains reads as a takeover in the subject; an account that cannot be read is named and its findings are not
called gone; a fixed finding goes "gone" and a returning one is new again; dismissed stays quiet; approving for an account, its client or everyone covers
the open findings it now covers; two scans never run at once; and the settings are checked. Nothing here talks to a network.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from domains_fakes import Deliver, MemStore, Site  # noqa: E402
from tracker import paid_domains as d  # noqa: E402
from tracker import paid_domains_scan as sc  # noqa: E402

A = {"id": "1112223330", "name": "Acme Garden", "platform": "google", "group": "mcc-1"}
B = {"id": "4445556660", "name": "Bolt Dental", "platform": "google", "group": "mcc-2"}
GROUPS = [{"id": "mcc-1", "name": "Acme", "accountIds": [A["id"]]}, {"id": "mcc-2", "name": "Bolt", "accountIds": [B["id"]]}]
LINK = "https://p.example/p2/paid-marketing/unknown-domains"


class World:
    """The ads the fake accounts have, by account id: {account id: [urls]} (a Bad entry makes the read fail)."""

    def __init__(self, **ads):
        self.ads, self.accounts = dict(ads), [A, B]
        self.fail = set()
        self.reads = 0

    def inventory(self, store):
        return list(self.accounts), GROUPS, []

    def read(self, account, store):
        self.reads += 1
        if account["id"] in self.fail:
            raise RuntimeError("cannot read")
        out = [{"platform": "google", "accountId": account["id"], "accountName": account["name"], "where": "ad", "objectId": str(i), "objectName": "Ad %d" % i,
                "campaign": "C", "adGroup": "G", "url": u, "host": d.host_of(u), "redirect": False} for i, u in enumerate(self.ads.get(account["id"], []))]
        return out, {"ads": len(out), "assetGroups": 0, "assets": 0}


def strange(store):
    """The finding for strange.example (the first scan also left one for the domain that was then trusted)."""
    return next(f for f in store.found.values() if f["host"] == "strange.example")


def scan(store, world, deliver, trigger="test", site=None, master=None):
    return sc.run_scan(store, trigger=trigger, link=LINK, deliver=deliver, inventory_fn=world.inventory, read_fn=world.read, workers=2,
                       fetch_fn=site or Site(), sleep_fn=lambda s: None, master_fn=(lambda: master or []))


def configured():
    s = MemStore()
    s.state_set("settings", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def test_nothing_is_alerted_before_a_baseline():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://strange.example/"]})
    run = scan(s, w, dv)
    assert run["new"] == 1 and run["alerted"] == 0 and not dv.emails and not dv.slacks and run["baseline"] is False
    assert s.state_get("inuse")["accounts"][A["id"]]["domains"] == {"strange.example": 1}


def test_setting_the_baseline_trusts_what_is_used_and_marks_the_rest_seen():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://acme-garden.com/", "https://strange.example/"], B["id"]: ["https://bolt-dental.com/"]})
    scan(s, w, dv)
    res = sc.set_baseline(s, "me@x", [A, B], trust=[B["id"]])
    assert res["trusted"] == 1 and {(k["domain"], k["scope"]) for k in s.known} == {("bolt-dental.com", B["id"])}
    assert s.state_get("baseline")["by"] == "me@x"
    assert all(f["status"] == "approved" for f in s.found.values() if f["accountId"] == B["id"])
    left = [f for f in s.found.values() if f["accountId"] == A["id"]]
    assert left and all(f["status"] == "open" and f["alertedAt"] for f in left)         # open, but already seen: not alerted later
    run = scan(s, w, dv)
    assert run["alerted"] == 0 and not dv.emails


def test_a_new_finding_after_the_baseline_alerts_once_by_email_and_slack():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://acme-garden.com/"]})
    scan(s, w, dv)
    sc.set_baseline(s, "me", [A, B], trust="all")
    w.ads[A["id"]].append("https://strange.example/")
    run = scan(s, w, dv)
    assert run["new"] == 1 and run["alerted"] == 1
    assert len(dv.emails) == 1 and dv.emails[0]["to"] == ["ppc@example.com"] and "strange.example" in dv.emails[0]["text"]
    assert len(dv.slacks) == 1 and dv.slacks[0]["channel"] == "#alerts"
    again = scan(s, w, dv)
    assert again["new"] == 0 and again["alerted"] == 0 and len(dv.emails) == 1          # once per finding
    f = strange(s)
    assert f["alertedAt"] and f["alert"] == {"email": "sent", "slack": "sent"}


def test_a_channel_that_is_not_set_is_said_and_the_finding_waits():
    s, dv = MemStore(), Deliver()
    w = World(**{A["id"]: ["https://acme-garden.com/"]})
    scan(s, w, dv)
    sc.set_baseline(s, "me", [A], trust="all")
    w.ads[A["id"]].append("https://strange.example/")
    run = scan(s, w, dv)
    assert run["alerted"] == 0 and run["email"]["status"] == "not_configured" and run["slack"]["status"] == "not_configured"
    assert strange(s)["alertedAt"] is None
    s.state_set("settings", {"emails": [], "slackChannel": "#alerts"})
    run = scan(s, w, dv)
    assert run["alerted"] == 1 and run["email"]["status"] == "not_configured" and run["slack"]["status"] == "sent"


def test_a_failed_send_is_tried_again_next_scan():
    s, dv = configured(), Deliver(fail=("email", "slack"))
    w = World(**{A["id"]: ["https://acme-garden.com/"]})
    scan(s, w, dv)
    sc.set_baseline(s, "me", [A], trust="all")
    w.ads[A["id"]].append("https://strange.example/")
    run = scan(s, w, dv)
    assert run["alerted"] == 0 and run["email"]["status"] == "failed" and "smtp down" in run["email"]["error"]
    dv.fail.clear()
    run = scan(s, w, dv)
    assert run["alerted"] == 1 and len(dv.emails) == 1


def test_a_burst_reads_as_a_takeover():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://acme-garden.com/"]})
    scan(s, w, dv)
    sc.set_baseline(s, "me", [A], trust="all")
    w.ads[A["id"]] += ["https://one.example/", "https://two.example/", "https://three.example/"]
    run = scan(s, w, dv)
    assert run["takeover"] and run["takeover"][0]["accountName"] == "Acme Garden"
    assert dv.emails[0]["subject"].startswith("POSSIBLE ACCOUNT TAKEOVER: ") and dv.slacks[0]["text"].startswith("*POSSIBLE ACCOUNT TAKEOVER")


def test_an_unreadable_account_is_named_and_its_findings_stay_open():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://strange.example/"], B["id"]: ["https://bolt-dental.com/"]})
    scan(s, w, dv)
    w.fail.add(A["id"])
    run = scan(s, w, dv)
    assert run["read"] == 1 and run["unreadableCount"] == 1 and run["unreadable"][0]["accountId"] == A["id"] and "cannot read" in run["unreadable"][0]["error"]
    assert next(f for f in s.found.values() if f["accountId"] == A["id"])["status"] == "open" and run["gone"] == 0


def test_fixed_goes_gone_and_returning_is_new_again():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://acme-garden.com/"]})
    scan(s, w, dv)
    sc.set_baseline(s, "me", [A], trust="all")
    w.ads[A["id"]].append("https://strange.example/")
    scan(s, w, dv)
    w.ads[A["id"]].pop()
    run = scan(s, w, dv)
    assert run["gone"] == 1 and strange(s)["status"] == "gone"
    w.ads[A["id"]].append("https://strange.example/")
    run = scan(s, w, dv)
    assert run["reopened"] == 1 and run["alerted"] == 1 and len(dv.emails) == 2


def test_dismissed_stays_quiet_and_can_be_opened_again():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://acme-garden.com/"]})
    scan(s, w, dv)
    sc.set_baseline(s, "me", [A], trust="all")
    w.ads[A["id"]].append("https://strange.example/")
    scan(s, w, dv)
    fp = strange(s)["fingerprint"]
    sc.decide(s, fp, "dismissed", "me")
    w.ads[A["id"]][-1] = "https://strange.example/other-page"
    run = scan(s, w, dv)
    assert run["alerted"] == 0 and s.found[fp]["status"] == "dismissed" and len(dv.emails) == 1
    sc.decide(s, fp, "open", "me")
    assert s.found[fp]["status"] == "open"
    with pytest.raises(sc.BadRequest):
        sc.decide(s, fp, "approved", "me")
    with pytest.raises(sc.BadRequest):
        sc.decide(s, "nope", "dismissed", "me")


def test_approving_for_an_account_a_client_or_everyone():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://one.example/", "https://one.example/x"], B["id"]: ["https://one.example/"]})
    scan(s, w, dv)
    fa = next(f for f in s.found.values() if f["accountId"] == A["id"])
    assert sc.approve(s, fa["fingerprint"], "account", "me", [A, B], GROUPS) == 2       # both of A's ads on that domain; B's stays open
    assert next(f for f in s.found.values() if f["accountId"] == B["id"])["status"] == "open"
    assert {(k["domain"], k["scope"]) for k in s.known} == {("one.example", A["id"])}
    fb = next(f for f in s.found.values() if f["accountId"] == B["id"])
    sc.approve(s, fb["fingerprint"], "everyone", "me", [A, B], GROUPS)
    assert fb["fingerprint"] in s.found and s.found[fb["fingerprint"]]["status"] == "approved"
    g = next(f for f in s.found.values() if f["accountId"] == A["id"])
    s.found[g["fingerprint"]]["status"] = "open"
    sc.approve(s, g["fingerprint"], "group", "me", [A, B], GROUPS)
    assert any(k["scope"] == "mcc-1" for k in s.known)
    with pytest.raises(sc.BadRequest):
        sc.approve(s, g["fingerprint"], "group", "me", [dict(A, group="")], GROUPS)
    with pytest.raises(sc.BadRequest):
        sc.approve(s, g["fingerprint"], "nobody", "me", [A], GROUPS)


def test_removing_an_entry_opens_the_finding_again_on_the_next_scan():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://one.example/"]})
    scan(s, w, dv)
    fp = next(iter(s.found))
    sc.approve(s, fp, "account", "me", [A, B], GROUPS)
    sc.set_baseline(s, "me", [A, B])
    scan(s, w, dv)
    assert s.found[fp]["status"] == "approved"
    s.known_remove(s.known[0]["id"])
    run = scan(s, w, dv)
    assert run["reopened"] == 1 and s.found[fp]["status"] == "open" and run["alerted"] == 1


def test_two_scans_never_run_at_once_and_the_lock_is_released():
    s, dv = configured(), Deliver()
    w = World()
    s.claim(sc.LOCK, 60)
    assert scan(s, w, dv) == {"status": "busy"} and w.reads == 0
    s.release(sc.LOCK)
    assert scan(s, w, dv)["status"] == "done" and sc.LOCK not in s.locks
    assert s.state_get("progress")["running"] is False


def test_a_scan_that_breaks_is_recorded_and_the_lock_released():
    s, dv = configured(), Deliver()
    w = World()

    def boom(store):
        raise RuntimeError("inventory down")
    run = sc.run_scan(s, trigger="t", link=LINK, deliver=dv, inventory_fn=boom, read_fn=w.read, fetch_fn=Site())
    assert run["status"] == "failed" and "inventory down" in run["error"] and sc.LOCK not in s.locks
    assert s.state_get("runs")[0]["status"] == "failed"


def test_a_quota_error_stops_reading_google():
    class Quota(Exception):
        kind = "quota"
    s, dv = configured(), Deliver()
    w = World()
    seen = []

    def read(account, store):
        seen.append(account["id"])
        raise Quota("out of reads")
    run = sc.run_scan(s, trigger="t", link=LINK, deliver=dv, inventory_fn=w.inventory, read_fn=read, workers=1, fetch_fn=Site())
    assert seen == [A["id"]] and run["unreadableCount"] == 2 and "budget" in run["unreadable"][1]["error"]


def test_runs_are_kept_newest_first_and_capped():
    s, dv = configured(), Deliver()
    w = World()
    for _ in range(sc.RUNS_KEPT + 3):
        scan(s, w, dv)
    assert len(s.state_get("runs")) == sc.RUNS_KEPT


def test_settings_are_checked():
    assert sc.clean_settings({"emails": "A@x.com, b@y.org;a@x.com", "slackChannel": " #sec-alerts "}) == {"emails": ["a@x.com", "b@y.org"], "slackChannel": "#sec-alerts"}
    assert sc.clean_settings({"emails": [], "slackChannel": ""}) == {"emails": [], "slackChannel": ""}
    for bad in ({"emails": ["nope"]}, {"emails": ["a@b.co"] * 1, "slackChannel": "two words"}, {"emails": ["a%d@b.co" % i for i in range(11)]}):
        with pytest.raises(sc.BadRequest):
            sc.clean_settings(bad)


def test_the_test_alert_goes_through_the_senders():
    s, dv = configured(), Deliver()
    res = sc.test_alert(s, LINK, dv)
    assert res["sent"] and dv.emails[0]["subject"].startswith("TEST: ") and dv.slacks[0]["text"].startswith("TEST")
    assert sc.test_alert(MemStore(), LINK, dv)["sent"] is False


def test_the_database_entries():
    s = MemStore()
    row, made = sc.add_known(s, "https://www.Acme-Garden.com/", "", "main site", "me", [A], GROUPS)
    assert made and row["domain"] == "acme-garden.com" and row["scope"] == "*"
    assert sc.add_known(s, "acme-garden.com", "everyone", "", "me", [A], GROUPS)[1] is False
    with pytest.raises(sc.BadRequest):
        sc.add_known(s, "not a domain", "", "", "me", [A], GROUPS)
    with pytest.raises(sc.BadRequest):
        sc.add_known(s, "x.example", "999-999-9999", "", "me", [A], GROUPS)
    res = sc.import_known(s, [{"domain": "b.example", "scope": "mcc-2"}, {"domain": "bad one"}, {"domain": "c.example", "scope": "zzz"}, {"domain": "acme-garden.com"}],
                          "me", [A, B], GROUPS)
    assert res["added"] == 1 and res["existing"] == 1 and res["skippedCount"] == 2 and res["skipped"][0]["row"] == 2


def test_the_snapshot_has_what_the_page_needs():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://acme-garden.com/", "https://strange.example/"]})
    scan(s, w, dv)
    s.known_add("acme-garden.com", A["id"], "", "me")
    snap = sc.snapshot(s, [A, B], GROUPS, [], dv)
    assert snap["counts"]["open"] == 2 and snap["baseline"] is None and snap["senders"] == {"email": True, "slack": True}
    use = next(a for a in snap["inUse"] if a["accountId"] == A["id"])
    assert [(x["domain"], x["known"]) for x in use["domains"]] == [("strange.example", False), ("acme-garden.com", True)]
    assert snap["runs"][0]["status"] == "done" and snap["settings"]["slackChannel"] == "#alerts"



def test_parsing_a_csv_with_and_without_a_header():
    rows = sc.parse_csv("﻿Website,Client,Comment\nacme-garden.com,Acme,main\n\n,,\nbolt-dental.com,,\n")
    assert rows == [{"domain": "acme-garden.com", "scope": "Acme", "note": "main"}, {"domain": "bolt-dental.com", "scope": "", "note": ""}]
    assert sc.parse_csv("a.example,mcc-1,hello\nb.example")[1] == {"domain": "b.example", "scope": "", "note": ""}
    with pytest.raises(sc.BadRequest):
        sc.parse_csv(" \n\n")
    with pytest.raises(sc.BadRequest):
        sc.parse_csv("domain\n" + "x.example\n" * (sc.MAX_ROWS + 1))


def test_a_sheet_link_becomes_its_csv_export_and_only_google_sheets_are_fetched():
    link = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=77"
    assert sc.sheet_csv_url(link) == "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/export?format=csv&gid=77"
    for bad in ("https://evil.example/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "http://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "", None):
        with pytest.raises(sc.BadRequest):
            sc.sheet_csv_url(bad)

    class R:
        def __init__(self, status, ctype, body=b"domain\nx.example\n"):
            self.status_code, self.headers, self.content = status, {"Content-Type": ctype}, body
    assert sc.fetch_sheet(link, get=lambda *a, **k: R(200, "text/csv; charset=utf-8")) == "domain\nx.example\n"
    with pytest.raises(sc.BadRequest):                                          # a sheet that is not shared answers with a login page
        sc.fetch_sheet(link, get=lambda *a, **k: R(200, "text/html"))
    with pytest.raises(sc.BadRequest):
        sc.fetch_sheet(link, get=lambda *a, **k: R(404, "text/csv"))

    def boom(*a, **k):
        raise OSError("down")
    with pytest.raises(sc.BadRequest):
        sc.fetch_sheet(link, get=boom)


def test_the_export_names_who_each_entry_is_for():
    out = sc.export_csv([{"domain": "a.example", "scope": "*", "note": "n", "added_by": "x@y"}, {"domain": "b.example", "scope": "mcc-1", "note": "", "added_by": ""}], [A], GROUPS)
    assert out.splitlines() == ["domain,for,for_id,note,added_by", "a.example,Everyone,,n,x@y", "b.example,Acme,mcc-1,,"]


def test_adding_a_domain_approves_the_open_findings_it_covers():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://one.example/", "https://other.example/"]})
    scan(s, w, dv)
    sc.add_known(s, "one.example", A["id"], "", "me", [A, B], GROUPS)
    by_host = {f["host"]: f["status"] for f in s.found.values()}
    assert by_host == {"one.example": "approved", "other.example": "open"}
    sc.import_known(s, [{"domain": "other.example", "scope": ""}], "me", [A, B], GROUPS)
    assert {f["status"] for f in s.found.values()} == {"approved"}


def test_a_domain_added_between_scans_is_approved_not_gone():
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://one.example/"]})
    scan(s, w, dv)
    s.known_add("one.example", A["id"], "", "me")                           # added by a path that did not touch the findings
    run = scan(s, w, dv)
    assert run["gone"] == 0 and next(iter(s.found.values()))["status"] == "approved"


def test_meta_is_listed_and_read_through_the_platforms_own_store(monkeypatch):
    """paid_meta needs the platform's shared store (rate-limit block, cache), not this agent's: handing it ours made every Meta listing fail."""
    from tracker import paid_gads_segments as g
    from tracker import paid_meta, paid_store
    seen = []
    monkeypatch.setattr(g, "config_problem", lambda: "off")
    monkeypatch.setattr(paid_meta, "config_problem", lambda: None)
    monkeypatch.setattr(paid_meta, "list_accounts", lambda st, **kw: seen.append(st) or [{"id": "act_1", "name": "M", "status": "ACTIVE"}])
    monkeypatch.setattr(paid_meta, "_pages", lambda path, params, st, **kw: seen.append(st) or [])
    accounts, _groups, _problems = sc.inventory(MemStore())
    assert [a["id"] for a in accounts] == ["act_1"]
    sc.read_account(accounts[0], MemStore())
    assert seen == [paid_store, paid_store]


def test_a_domain_the_master_agent_allows_for_an_account_is_known_to_the_scan_and_for_that_account_only():
    from tracker import paid_master
    from monitor_fakes import MemStore as MonStore
    mon = MonStore()
    paid_master.save_client(mon, "", {"name": "Acme", "accounts": [{"id": A["id"]}], "shared": {"domains": "strange.example"}}, "ana@position2.com")
    master = paid_master.domain_entries(paid_master.load(mon))
    s, dv = configured(), Deliver()
    w = World(**{A["id"]: ["https://acme-garden.com/"], B["id"]: ["https://bolt-dental.com/"]})
    scan(s, w, dv)
    sc.set_baseline(s, "me", [A, B], trust="all")
    w.ads[A["id"]].append("https://strange.example/")
    w.ads[B["id"]].append("https://strange.example/")
    run = scan(s, w, dv, master=master)
    assert run["new"] == 1 and [f["accountId"] for f in s.found.values() if f["host"] == "strange.example"] == [B["id"]]      # Acme's client allowed it; Bolt's did not
    assert sc.in_use(s, master_fn=lambda: master) and next(x for x in sc.in_use(s, master_fn=lambda: master) if x["accountId"] == A["id"])["domains"][0]["known"] is True


def test_a_master_that_cannot_be_read_leaves_the_domain_database_in_charge(monkeypatch):
    from tracker import paid_master
    monkeypatch.setattr(paid_master, "load", lambda store: (_ for _ in ()).throw(RuntimeError("store down")))
    assert sc.master_domain_entries() == []
