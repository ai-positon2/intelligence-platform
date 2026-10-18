"""Pacing Desk: a refresh that paces Meta as well as Google (tracker/paid_refresh.py). The real engine runs twice, once per channel, and the two dashboards are one.

Pinned here: the merge (channel on every account, a client's totals summed again, a Meta account named like a Google one is told apart, Meta's warnings said to be
Meta's, neither input changed); a Meta allocation makes the run read Meta and an account with none does not; Meta not answering is a note and Google is still published;
an unmatched allocation is said so; and the Meta read is kept for the Pacific day's run like Google's.
"""
import copy
import datetime as dt

import pytest

from tests.test_paid_refresh import ACCOUNTS, Ads, Budgets, RunStore, clock, configured, NOW  # noqa: F401
from tracker import paid_gads as base
from tracker import paid_meta
from tracker import paid_pacing as pp
from tracker import paid_pacing_budgets as budgets
from tracker import paid_refresh as rf

META = {"id": "act_5550001", "name": "Acme Social", "currency": "USD", "tz": "America/Los_Angeles", "platform": "meta"}


def acct(name, spend, allocated=1000.0, **kw):
    return dict({"name": name, "landing": "ON TARGET", "allocated": allocated, "spend": spend, "pacing": 0.5, "ideal": 0.5, "projected": spend * 2, "conv": 10.0,
                 "ambiguous": False, "capped": False, "campaigns": [], "moves": [], "categories": [], "notes": []}, **kw)


def client(name, accounts, **kw):
    solid = [a for a in accounts if not a["ambiguous"]]
    return dict({"name": name, "allocated": sum(a["allocated"] for a in solid), "spend": sum(a["spend"] for a in accounts), "spendSolid": sum(a["spend"] for a in solid),
                 "projected": sum(a["projected"] for a in solid), "conv": sum(a["conv"] for a in accounts), "cpa": None, "warnings": [], "accounts": accounts}, **kw)


def body(*clients, warnings=()):
    return {"asOf": "2026-10-12", "dataThrough": "2026-10-11", "month": "October 2026", "clients": list(clients), "portfolioWarnings": list(warnings)}


def test_the_merge_adds_meta_accounts_to_their_clients_and_sums_the_clients_totals():
    g = body(client("Acme", [acct("Acme Dental", 400.0, 3000.0)]), client("Beta", [acct("Beta Imaging", 100.0)]))
    m = body(client("Acme", [acct("Acme Social", 200.0, 900.0)]), client("Gamma", [acct("Gamma Social", 50.0)]))
    out = rf.merge_bodies(g, m)
    assert [c["name"] for c in out["clients"]] == ["Acme", "Beta", "Gamma"]
    acme = out["clients"][0]
    assert [(a["name"], a["channel"]) for a in acme["accounts"]] == [("Acme Dental", "Google"), ("Acme Social", "Meta")]
    assert (acme["allocated"], acme["spend"], acme["spendSolid"], acme["projected"], acme["conv"]) == (3900.0, 600.0, 600.0, 1200.0, 20.0) and acme["cpa"] == 30.0
    assert [a["channel"] for c in out["clients"][1:] for a in c["accounts"]] == ["Google", "Meta"]


def test_a_meta_account_named_like_a_google_one_of_the_same_client_is_told_apart_by_its_name():
    g = body(client("Acme", [acct("Acme", 400.0)]))
    out = rf.merge_bodies(g, body(client("Acme", [acct("Acme", 200.0)])))
    assert [a["name"] for a in out["clients"][0]["accounts"]] == ["Acme", "Acme (Meta)"]


def test_the_merge_says_which_warnings_are_metas_and_changes_neither_input():
    g, m = body(client("Acme", [acct("A", 1.0)]), warnings=["g warning"]), body(client("Acme", [acct("B", 1.0)]), warnings=["'X' spent $5 MTD but has no Meta budget row"])
    g0, m0 = copy.deepcopy(g), copy.deepcopy(m)
    out = rf.merge_bodies(g, m)
    assert out["portfolioWarnings"] == ["g warning", "Meta: 'X' spent $5 MTD but has no Meta budget row"] and g == g0 and m == m0
    assert out["clients"][0]["warnings"] == [] and "channel" not in g["clients"][0]["accounts"][0]


# ── A whole refresh ─────────────────────────────────────────────────────────

def meta_pages(camps=None, days=None, fail=()):
    camps = camps if camps is not None else [{"id": "1", "name": "Leads Oct", "effective_status": "ACTIVE", "objective": "OUTCOME_LEADS", "daily_budget": "10000"}]
    days = days if days is not None else [{"campaign_id": "1", "campaign_name": "Leads Oct", "objective": "OUTCOME_LEADS", "date_start": "2026-10-%02d" % d, "spend": "90",
                                          "impressions": "1000", "inline_link_clicks": "50", "actions": [{"action_type": "lead", "value": "3"}], "action_values": []} for d in range(1, 12)]

    def fetch(path, params):
        part = "campaigns" if path.endswith("/campaigns") else "adsets" if path.endswith("/adsets") else "insights"
        if part in fail:
            raise base.AdsError("Meta answered 400: (#100) permission denied")
        return list({"campaigns": camps, "adsets": [], "insights": days}[part])
    return fetch


def book_with_meta(*rows):
    return Budgets(**{"2026-10": [budgets.check_row({"client": c, "account": a, "allocated": n, "channel": ch}) for c, a, n, ch in rows]})


def run(store=None, rows=None, fetch=None, accounts=None, steps=None):
    store = store or RunStore()
    rows = rows or (("Acme", "Acme Dental", 3000, "Google"), ("Acme", "Acme Social", 2000, "Meta"))
    res = rf.refresh(store, pp, (steps.append if steps is not None else (lambda s: None)), budget_store=book_with_meta(*rows), accounts_fn=lambda: ACCOUNTS, search_fn=Ads(), now=NOW,
                     meta_accounts_fn=lambda: [META] if accounts is None else accounts, meta_fetch=fetch or meta_pages())
    return store, res


@pytest.fixture
def meta_on(monkeypatch, configured, clock):
    monkeypatch.setattr(paid_meta, "config_problem", lambda: "")


def accounts_in(store):
    _meta, clients, _latest = store.saved[0]
    return {a["name"]: a for _s, c in clients for a in c["accounts"]}


def test_a_meta_allocation_makes_the_run_read_meta_and_pace_it_next_to_google(meta_on):
    steps = []
    store, res = run(steps=steps)
    a = accounts_in(store)
    assert a["Acme Dental"]["channel"] == "Google" and a["Acme Social"]["channel"] == "Meta"
    assert a["Acme Social"]["allocated"] == 2000.0 and a["Acme Social"]["spend"] == 990.0           # 11 days of $90, read from Meta, paced against the Meta allocation
    assert a["Acme Dental"]["allocated"] == 3000.0                                                    # the Google account is not paced against the Meta allocation
    assert "Listing the Meta ad accounts" in steps and any(s.startswith("Reading 1 Meta ad account") for s in steps)
    assert res["channels"] == ["Google", "Meta"] and res["accounts"] == 2
    src = store.saved[0][0]["source"]
    assert src["channels"] == ["Google", "Meta"] and src["meta"] == {"accounts": 1, "unread": 0, "readAt": NOW.isoformat()}


def test_a_run_with_no_meta_allocation_never_asks_meta(meta_on):
    asked = []
    store, res = run(rows=(("Acme", "Acme Dental", 3000, "Google"),), fetch=lambda path, params: asked.append(path) or [], accounts=[META])
    assert asked == [] and res["channels"] == ["Google"] and "meta" not in store.saved[0][0]["source"]
    assert all(a.get("channel") == "Google" for a in accounts_in(store).values()) or "channel" not in accounts_in(store)["Acme Dental"]


def test_meta_not_answering_is_a_note_and_google_is_still_published(meta_on):
    def broken(path, params):
        raise paid_meta.AdsError("Meta answered 500: down")
    store, res = run(fetch=broken)
    assert "Acme Social" not in accounts_in(store) and "Acme Dental" in accounts_in(store) and res["channels"] == ["Google"]
    assert any("Meta Ads would not return Acme Social" in n for n in store.saved[0][0]["source"]["notes"])
    assert res["unread"] == 1


def test_meta_asking_the_app_to_wait_is_a_note_not_a_failed_run(meta_on):
    def waiting(path, params):
        raise paid_meta.OverQuota("Meta is rate limiting this app right now.")
    store, res = run(fetch=waiting)
    assert res["channels"] == ["Google"] and any("Meta Ads would not answer" in n for n in res["notes"] + store.saved[0][0]["source"]["notes"])


def test_an_allocation_that_fits_no_meta_ad_account_is_said_so(meta_on):
    store, res = run(accounts=[dict(META, name="Somebody Else")])
    assert "Acme Social" not in accounts_in(store) and any("fits no Meta ad account" in n for n in store.saved[0][0]["source"]["notes"])


def test_meta_not_connected_says_so_and_google_is_published(monkeypatch, configured, clock):
    monkeypatch.setattr(paid_meta, "config_problem", lambda: "META_ADS_ACCESS_TOKEN is not set")
    store, res = run()
    assert res["channels"] == ["Google"] and any("Meta Ads is not connected" in n for n in store.saved[0][0]["source"]["notes"])


def test_the_meta_read_is_kept_so_running_again_does_not_ask_meta_again(meta_on):
    calls = []
    base_fetch = meta_pages()

    def counting(path, params):
        calls.append(path)
        return base_fetch(path, params)
    store, _ = run(fetch=counting)
    n = len(calls)
    run(store=store, fetch=counting)
    assert n > 0 and len(calls) == n
