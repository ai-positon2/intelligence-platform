"""Test & Experiment Tracker: Meta tests, and a Google Ads and a Meta ad account together (tracker/paid_exp.py)."""
import json

import pytest

from tests.gads_fakes import FakeStore
from tests.test_paid_exp import ACCOUNT, ACCOUNT_ID, EARLY, FLAT, LOSE, NOW, TODAY, WIN, ad, build, fresh as _fresh_fixture, group, made, read_with, search  # noqa: F401
from tests.test_paid_exp_meta_read import Graph, cell, day_row, live, stat, study, two_ads
from tracker import paid_exp as ex
from tracker import paid_geo, paid_gads, paid_meta
from tracker import paid_gads_segments as gads

ACT = "act_1234567890"
META_ACCOUNT = {"id": ACT, "name": "Acme Dental", "currency": "USD", "tz": "America/New_York", "platform": "meta"}
BOTH = ACCOUNT_ID + "+" + ACT


def fresh():
    paid_geo.clear_cache()
    paid_gads.forget()


@pytest.fixture(autouse=True)
def meta_account(monkeypatch):
    fresh()
    monkeypatch.setattr(paid_meta, "config_problem", lambda: "")
    monkeypatch.setattr(paid_meta, "account_by_id", lambda store, act, http=None: META_ACCOUNT if act == ACT else None)
    yield
    fresh()


def no_google(*_a):
    raise AssertionError("Google Ads was asked and should not have been")


def run(account=BOTH, graph=None, **kw):
    kw.setdefault("store", FakeStore())
    kw.setdefault("accounts_fn", lambda: [ACCOUNT])
    kw.setdefault("search_fn", search())
    kw.setdefault("now", NOW)
    kw.setdefault("today", TODAY)
    kw.setdefault("meta_pages", (graph or two_ads()).pages)
    return ex.report_for(account, **kw)


def meta_exp(**kw):
    e = made(**kw)
    e["platform"] = "meta"
    e["arms"][0]["name"], e["arms"][1]["name"] = "Version A", "Version B"
    e["typeLabel"] = "Meta A/B test"
    return ex.build_experiment(e, TODAY)


# ── What to do about a Meta test is said in terms of its cells ──────────────

def test_a_cell_that_is_ahead_is_kept_and_the_budget_goes_behind_it():
    r = meta_exp(**WIN)
    a = r["action"]
    assert r["platform"] == "meta" and a["kind"] == "apply" and a["label"] == "Keep the winner"
    assert a["text"].startswith("Version B is ahead of Version A on conversion rate (5% against 9%") and a["text"].endswith("End the test and put the budget behind Version B.")
    assert "Apply it to the campaign" not in a["text"]


def test_a_cell_that_is_behind_ends_the_test_and_the_first_cell_gets_the_budget():
    a = meta_exp(**LOSE)["action"]
    assert a["kind"] == "end" and a["text"].startswith("Version B is behind Version A on conversion rate") and "put the budget behind Version A: waiting will not turn" in a["text"]


def test_a_decision_past_the_end_date_is_a_decision_whatever_the_result():
    assert meta_exp(end="2026-10-10", **WIN)["action"]["kind"] == "decide" and meta_exp(end="2026-10-10", **LOSE)["action"]["kind"] == "decide"
    assert "Past its end date (Oct 10) and still running." in meta_exp(end="2026-10-10", **WIN)["action"]["text"]


def test_a_google_experiment_keeps_its_words_and_has_no_label():
    r = build(**WIN)
    assert r["platform"] == "google" and r["reason"] == "" and "label" not in r["action"] and "Apply it to the campaign" in r["action"]["text"]


def test_no_difference_and_too_early_and_not_getting_traffic_say_the_cells_ad_sets_for_meta():
    assert meta_exp(start="2026-09-20", **FLAT)["action"]["kind"] == "end"
    early = meta_exp(**dict(EARLY, start="2026-10-10", window=("2026-10-10", "2026-10-17")))["action"]
    assert early["kind"] in ("wait", "stuck") and "Too early to call" in early["text"]
    stuck = meta_exp(control={"impressions": 8000, "clicks": 40, "cost": 100.0, "conversions": 3, "value": 0.0}, trial={"impressions": 8000, "clicks": 0, "cost": 0.0, "conversions": 0, "value": 0.0},
                     start="2026-10-10", window=("2026-10-10", "2026-10-17"))["action"]
    assert stuck["kind"] == "stuck" and "Check that both cells' ad sets are on and have budget." in stuck["text"]
    assert "campaigns are enabled" not in stuck["text"]
    google = build(control={"impressions": 8000, "clicks": 40, "cost": 100.0, "conversions": 3, "value": 0.0}, trial={"impressions": 8000, "clicks": 0, "cost": 0.0, "conversions": 0, "value": 0.0},
                   start="2026-10-10", window=("2026-10-10", "2026-10-17"))["action"]
    assert "both campaigns are enabled and have budget" in google["text"]


def test_a_finished_meta_test_says_which_cell_finished_ahead():
    assert meta_exp(state="ended", **WIN)["action"] == {"kind": "record", "text": "Ended. Version B finished ahead of Version A."}
    assert meta_exp(state="ended", **LOSE)["action"]["text"] == "Ended. Version B finished behind Version A."
    assert meta_exp(state="ended", **FLAT)["action"]["text"] == "Ended. It found no difference."
    assert build(state="ended", **WIN)["action"]["text"] == "Ended. The trial finished ahead."


def test_a_meta_test_with_no_result_says_why_or_that_only_a_b_tests_are_read():
    r = meta_exp(measured=False, control=None, trial=None, window=None)
    r["reason"] = ""
    e = made(measured=False, control=None, trial=None)
    e["platform"], e["reason"], e["typeLabel"] = "meta", "Meta did not list this test's cells.", "Meta A/B test"
    a = ex.build_experiment(e, TODAY)["action"]
    assert a["kind"] == "manual" and a["text"] == "Meta did not list this test's cells. Read the result in Meta's Experiments tool."
    e["reason"], e["typeLabel"] = "", "Conversion lift study"
    assert ex.build_experiment(e, TODAY)["action"]["text"] == "This is a conversion lift study, and the tracker reads Meta A/B tests only. Read the result in Meta's Experiments tool."
    e["typeLabel"] = ""
    assert ex.build_experiment(e, TODAY)["action"]["text"].startswith("This is a test, and the tracker reads Meta A/B tests only.")
    assert ex.build_experiment(dict(e, reason="Meta did not list this test's cells.", end="2026-10-10"), TODAY)["action"]["text"].startswith("Past its end date (Oct 10) and still running. Meta did not list")


def test_an_ad_test_between_meta_ads_is_named_by_the_ads_names_and_marked_meta():
    g = group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50))
    g["platform"] = "meta"
    g["ads"][0]["name"], g["ads"][1]["name"] = "Spring video", "Spring picture"
    t = ex.build_ad_test(g)
    assert t["platform"] == "meta" and {a["label"] for a in t["ads"]} == {"Spring video", "Spring picture"} and {a["name"] for a in t["ads"]} == {"Spring video", "Spring picture"}
    plain = ex.build_ad_test(group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50)))
    assert plain["platform"] == "google" and all(a["name"] == "" for a in plain["ads"]) and all(a["label"] != "" for a in plain["ads"])


# ── A Meta ad account on its own ────────────────────────────────────────────

def test_a_meta_account_has_its_split_tests_and_its_ad_tests_and_is_never_asked_of_google():
    out = run(ACT, search_fn=no_google)
    assert out["account"] == {"id": ACT, "name": "Acme Dental", "currency": "USD"} and out["summary"]["platforms"] == ["meta"]
    assert out["accounts"] == [{"platform": "meta", "id": ACT, "name": "Acme Dental", "currency": "USD"}]
    e = out["experiments"][0]
    assert e["id"] == "meta:900" and e["platform"] == "meta" and e["state"] == "running" and e["measured"] is True and e["arms"][0]["name"] == "A"
    t = out["adTests"][0]
    assert t["id"] == "meta:s1" and t["platform"] == "meta" and t["adCount"] == 2 and {a["label"] for a in t["ads"]} == {"A", "B"}
    assert out["warnings"] == [] and out["unread"] == [] and "stale" not in out
    assert {n["ref"]["id"] for n in out["needs"]} <= {"meta:900", "meta:s1"}


def test_the_notes_of_a_meta_report_explain_meta_and_leave_out_googles():
    notes = " ".join(run(ACT, search_fn=no_google)["notes"])
    assert "A Meta A/B test splits the audience" in notes and "Meta shows the ad it expects to do best more often" in notes and "depends on the campaign's objective" in notes
    assert "Conversions are Google's own" not in notes and "Google rotates ads" not in notes
    fresh()
    google = " ".join(run(ACCOUNT_ID)["notes"])
    assert "Conversions are Google's own" in google and "Meta A/B test" not in google


def test_the_count_of_ad_tests_is_said_in_both_platforms_words_when_meta_is_read():
    raw = read_with([], [group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50))], tests=3)
    raw["read"]["platforms"] = ["meta"]
    assert any(n.startswith("3 ad groups and ad sets run two or more live ads. The 1 that spent most") for n in ex.assemble(raw, now=NOW, today=TODAY)["notes"])
    raw["read"]["platforms"] = ["google"]
    assert any(n.startswith("3 ad groups run two or more live ads. The 1 that spent most") for n in ex.assemble(raw, now=NOW, today=TODAY)["notes"])


# ── Both ────────────────────────────────────────────────────────────────────

def test_both_accounts_tests_are_one_report_with_each_marked_by_its_platform():
    out = run(BOTH)
    assert [a["platform"] for a in out["accounts"]] == ["google", "meta"] and out["account"]["id"] == BOTH and out["summary"]["platforms"] == ["google", "meta"]
    assert {e["platform"] for e in out["experiments"]} == {"google", "meta"} and {t["platform"] for t in out["adTests"]} == {"google", "meta"}
    assert out["summary"]["experiments"] == 2 and out["summary"]["adTests"] == 2
    assert any("Conversions are Google's own" in n for n in out["notes"]) and any("A Meta A/B test splits" in n for n in out["notes"])


def test_each_platforms_flags_and_warnings_are_named_apart():
    out = run(BOTH, graph=two_ads(fail=["ad_stats"]), search_fn=search(fail={"ads": gads.GadsError("api", "Google answered 500")}))
    assert sorted(out["unread"]) == ["Google Ads: ads", "Meta Ads: ads"]
    assert any(w.startswith("Meta: The ads could not be read (Meta answered 400") for w in out["warnings"]) and any(w.startswith("The ads could not be read (Google answered 500") for w in out["warnings"])


def test_when_one_platform_would_not_answer_the_report_is_the_others_with_a_warning():
    out = run(BOTH, graph=two_ads(fail=["ad_studies", "ads"]))
    assert out["summary"]["platforms"] == ["google"] and {e["platform"] for e in out["experiments"]} == {"google"}
    assert out["warnings"][-1] == "Meta Ads could not be read (Meta answered 400: (#100) bad ad_studies). This report is Google Ads alone." and out["unread"] == ["Meta Ads: the whole account"]
    fresh()
    out = run(BOTH, search_fn=search(fail={"experiments": gads.GadsError("api", "Google answered 500"), "ads": gads.GadsError("api", "Google answered 500")}))
    assert out["summary"]["platforms"] == ["meta"] and out["warnings"][-1].startswith("Google Ads could not be read (Google answered 500). This report is Meta Ads alone.")
    assert out["unread"] == ["Google Ads: the whole account"]


def test_both_platforms_failing_is_an_error():
    with pytest.raises(gads.GadsError) as e:
        run(BOTH, graph=two_ads(fail=["ad_studies", "ads"]), search_fn=search(fail={"experiments": gads.GadsError("api", "Google answered 500"), "ads": gads.GadsError("api", "Google answered 500")}))
    assert "Google answered 500" in str(e.value)


def test_a_read_that_lost_a_platform_is_kept_only_five_minutes():
    store, calls = FakeStore(), []
    g = two_ads(fail=["ad_studies", "ads"])
    run(BOTH, store=store, graph=g, search_fn=search(calls=calls))
    n = len(calls)
    run(BOTH, store=store, graph=g, search_fn=search(calls=calls))
    assert len(calls) == n
    paid_gads.forget()
    store.at[ex.cache_key(BOTH)] -= ex.PARTIAL_TTL_S + 5
    out = run(BOTH, store=store, search_fn=search(calls=calls))
    assert len(calls) == 2 * n and out["summary"]["platforms"] == ["google", "meta"] and out["warnings"] == []


def test_a_platform_lost_to_its_allowance_leaves_a_whole_saved_read_to_be_served_with_its_date():
    class Spent(Graph):
        def pages(self, path, params):
            raise paid_meta.OverQuota("Meta is rate limiting this app right now.")
    store = FakeStore()
    first = run(BOTH, store=store)
    key = ex.cache_key(BOTH)
    store.old[key] = store.cache.pop(key)
    paid_gads.forget()
    out = run(BOTH, store=store, graph=Spent())
    assert out["stale"] == [{"platform": "google", "savedAt": "2026-10-10T08:00:00+00:00"}, {"platform": "meta", "savedAt": "2026-10-10T08:00:00+00:00"}]
    assert out["summary"]["experiments"] == first["summary"]["experiments"]
    fresh()
    nothing = run(BOTH, graph=Spent())
    assert nothing["summary"]["platforms"] == ["google"] and "stale" not in nothing
    fresh()
    store = FakeStore()
    run(BOTH, store=store, graph=Spent())                                  # a saved copy that lost Meta too
    store.old[key] = store.cache.pop(key)
    paid_gads.forget()
    assert "stale" not in run(BOTH, store=store, graph=Spent())


def test_two_accounts_in_two_currencies_are_not_read_together():
    with pytest.raises(ex.BadRequest):
        run(BOTH, accounts_fn=lambda: [dict(ACCOUNT, currency="EUR")])


def test_each_account_string_is_read_and_saved_on_its_own():
    store = FakeStore()
    run(ACCOUNT_ID, store=store)
    run(ACT, store=store, search_fn=no_google)
    run(BOTH, store=store)
    assert {k for k in store.cache if ":exp" in k or "exp2:" in k} >= {ex.cache_key(ACCOUNT_ID), ex.cache_key(ACT), ex.cache_key(BOTH)}


def test_no_em_or_en_dash_anywhere_in_a_meta_report():
    text = json.dumps(run(BOTH), ensure_ascii=False)
    assert "—" not in text and "–" not in text
    for state in ("ended", "running"):
        for kw in (WIN, LOSE, FLAT, EARLY):
            assert "—" not in json.dumps(meta_exp(state=state, **kw), ensure_ascii=False)


# ── The downloads ───────────────────────────────────────────────────────────

def test_a_report_of_both_platforms_names_both_accounts_and_marks_each_test_with_its_platform():
    from tracker import paid_exp_export as export
    t = export.tables(run(BOTH))
    rows = dict(t["Summary"][1])
    assert rows["Google Ads account"].endswith("(1112223334)") and rows["Meta ad account"].endswith("(act_1234567890)")
    head, exps = t["Experiments"]
    assert head[-2:] == ["Platform", "Why there is no result"] and {r[-2] for r in exps} == {"Google Ads", "Meta Ads"}
    head, tests = t["Ad tests"]
    assert head[0] == "Ad group or ad set" and head[-1] == "Platform" and {r[-1] for r in tests} == {"Google Ads", "Meta Ads"}
    assert t["Ads"][0][0] == "Ad group or ad set"
    for name, (header, body) in t.items():
        assert all(len(r) == len(header) for r in body), name


def test_a_meta_test_with_no_result_gives_its_reason_in_the_file():
    from tracker import paid_exp_export as export
    out = run(ACT, graph=two_ads(fail=["nested", "days"]), search_fn=no_google)
    head, rows = export.tables(out)["Experiments"]
    assert rows[0][-2] == "Meta Ads" and rows[0][-1] == "Meta did not say which campaigns or ad sets each cell holds."


def test_the_needs_list_carries_a_winners_own_label_into_the_file():
    from tracker import paid_exp_export as export
    exps = [made(**WIN)]
    raw = read_with(exps, [])
    raw["read"]["experiments"][0]["platform"] = "meta"
    out = ex.assemble(raw, now=NOW, today=TODAY)
    assert out["needs"][0]["label"] == "Keep the winner"
    assert export.tables(out)["Needs a decision"][1][0][1] == "Keep the winner"
    google = ex.assemble(read_with([made(**WIN)], []), now=NOW, today=TODAY)
    assert "label" not in google["needs"][0] and export.tables(google)["Needs a decision"][1][0][1] == "Apply it"


def test_a_saved_copy_of_either_platform_says_which_would_not_answer():
    from tracker import paid_exp_export as export
    report = dict(run(ACCOUNT_ID), stale=[{"platform": "google", "savedAt": "2026-10-10T08:00:00+00:00"}, {"platform": "meta", "savedAt": "2026-10-10T08:00:00+00:00"}])
    notes = [r[1] for r in export.tables(report)["Notes"][1] if r[0] == "Saved read"]
    assert notes == ["The read is the one saved on 2026-10-10 08:00 because Google Ads would not answer now.", "The read is the one saved on 2026-10-10 08:00 because Meta Ads would not answer now."]


def test_a_both_report_opens_as_excel_pdf_and_zip():
    from tracker import paid_exp_export as export
    report = run(BOTH)
    assert export.xlsx(report)[:2] == b"PK" and export.pdf(report)[:4] == b"%PDF" and export.csv_zip(report)[:2] == b"PK"
