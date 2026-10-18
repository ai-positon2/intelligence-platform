"""Landing Page & Relevancy Check: the report for a Meta ad account, or a Google Ads account and a Meta ad account together (tracker/paid_lp.py)."""
import pytest

from tests.gads_fakes import FakeStore
from tests.test_paid_lp import ACCOUNT, ACCOUNT_ID, NOW, Fetcher, fresh, search
from tests.test_paid_lp_meta_read import Graph, ins, link_ad
from tracker import paid_gads, paid_meta
from tracker import paid_gads_segments as gads
from tests.test_paid_lp_export import make_report
from tracker import paid_lp as lp
from tracker import paid_lp_export as ex

ACT = "act_1234567890"
META_ACCOUNT = {"id": ACT, "name": "Acme Dental", "currency": "USD", "tz": "America/New_York", "platform": "meta"}
BOTH = ACCOUNT_ID + "+" + ACT
LP = "https://acme-dental.com/lp"


@pytest.fixture(autouse=True)
def meta_account(monkeypatch):
    fresh()
    monkeypatch.setattr(paid_meta, "config_problem", lambda: "")
    monkeypatch.setattr(paid_meta, "account_by_id", lambda store, act, http=None: META_ACCOUNT if act == ACT else None)
    yield
    fresh()


def no_google(*_a):
    raise AssertionError("Google Ads was asked and should not have been")


def acme_graph(**kw):
    """Meta's ads of the same client: one ad to the same page Google's ad sends people to."""
    kw.setdefault("ads", [link_ad("1", LP)])
    kw.setdefault("stats", [ins("1", 20, 10, 200, leads=2)])
    return Graph(**kw)


def run(account=BOTH, graph=None, **kw):
    kw.setdefault("store", FakeStore())
    kw.setdefault("accounts_fn", lambda: [ACCOUNT])
    kw.setdefault("search_fn", search())
    kw.setdefault("fetcher", Fetcher())
    kw.setdefault("now", NOW)
    kw.setdefault("meta_pages", (graph or acme_graph()).pages)
    return lp.report_for(account, **kw)


def count(out):
    return sum(len(s["findings"]) for s in out["pages"][0]["sections"])


# ── A Meta ad account on its own ────────────────────────────────────────────

def test_a_meta_account_has_pages_from_its_ads_and_is_never_asked_of_google():
    out = run(ACT, search_fn=no_google)
    assert out["account"] == {"id": ACT, "name": "Acme Dental", "currency": "USD"}
    assert out["accounts"] == [{"platform": "meta", "id": ACT, "name": "Acme Dental", "currency": "USD"}] and out["summary"]["platforms"] == ["meta"]
    assert [p["id"] for p in out["pages"]] == ["acme-dental.com/lp"]
    p = out["pages"][0]
    assert p["platforms"] == ["meta"] and p["spend"] == 20.0 and p["clicks"] == 10 and p["conversions"] == 2.0 and p["checked"] is True and p["speed"] is None
    assert count(out) == 30
    g = p["adGroups"][0]
    assert g["platform"] == "meta" and g["name"] == "Set s1" and g["keywords"] == [] and g["terms"] == [] and g["headlines"] == ["Free quote"]
    assert out["warnings"] == [] and out["unread"] == [] and "stale" not in out


def test_the_notes_of_a_meta_report_explain_meta_and_leave_out_the_keyword_note():
    out = run(ACT, search_fn=no_google)
    assert any("A Meta ad set stands where a Google ad group stands" in n for n in out["notes"])
    assert any("pixel is looked for in the page's HTML" in n for n in out["notes"])
    assert not any("A keyword is on the page when every one of its words is" in n for n in out["notes"])
    google = run(ACCOUNT_ID, search_fn=search())
    assert any("A keyword is on the page" in n for n in google["notes"]) and not any("Meta ad set" in n for n in google["notes"])


def test_a_meta_page_is_checked_for_the_pixel_and_not_for_googles_tag():
    out = run(ACT, search_fn=no_google)
    ids = {f["id"]: f for s in out["pages"][0]["sections"] for f in s["findings"]}
    assert "trust_meta_pixel" in ids and "trust_tracking" not in ids and "match_keywords_page" not in ids and "speed_google" not in ids
    assert ids["trust_meta_pixel"]["result"] == "warn"                        # the page in the test carries a Google tag only


def test_what_meta_spent_on_ads_with_no_web_page_is_said_and_not_checked():
    ads = [link_ad("1", LP), {"id": "2", "adset_id": "s2", "creative": {"object_story_spec": {"link_data": {"message": "Fill in our form"}}}}]
    graph = acme_graph(ads=ads, stats=[ins("1", 20, 10, 200), ins("2", 30, 12, 300, adset="s2")])
    out = run(ACT, graph=graph, search_fn=no_google)
    note = next(n for n in out["notes"] if n.startswith("Meta spent"))
    assert note == "Meta spent $30 of its $50 on ads that send people to no web page of the client's (somewhere that could not be read), so there is no page to check for them."
    assert out["summary"]["spend"] == 20.0                                    # only what reached a page


def test_a_meta_account_that_cannot_be_read_is_an_error_not_an_empty_report():
    with pytest.raises(gads.GadsError):
        run(ACT, graph=acme_graph(fail=["insights"]), search_fn=no_google)
    with pytest.raises(lp.BadRequest):
        run("act_9999999999", search_fn=no_google)


# ── A Google Ads and a Meta ad account together ─────────────────────────────

def test_a_page_both_platforms_send_people_to_is_one_page_held_to_every_check():
    out = run(BOTH)
    assert [a["platform"] for a in out["accounts"]] == ["google", "meta"] and out["account"]["id"] == BOTH and out["account"]["name"] == "Acme Dental"
    assert out["summary"]["platforms"] == ["google", "meta"] and len(out["pages"]) == 1
    p = out["pages"][0]
    assert p["platforms"] == ["google", "meta"] and p["spend"] == 1020.0 and p["clicks"] == 860 and p["conversions"] == 32.0
    assert count(out) == 39
    assert sorted(g["platform"] for g in p["adGroups"]) == ["google", "meta"]
    assert any("A keyword is on the page" in n for n in out["notes"]) and any("Meta ad set" in n for n in out["notes"])


def test_the_two_platforms_flags_and_warnings_are_named_apart():
    out = run(BOTH, graph=acme_graph(fail=["rankings"]), search_fn=search(fail={"terms": gads.GadsError("api", "x")}))
    assert sorted(out["unread"]) == ["Google Ads: terms", "Meta Ads: rankings"]
    assert any(w.startswith("Meta: Meta's own ratings of the ads could not be read") for w in out["warnings"]) and any(w.startswith("The search terms could not be read") for w in out["warnings"])
    f = {f["id"]: f for s in out["pages"][0]["sections"] for f in s["findings"]}
    assert f["match_meta_conversion"]["result"] == "unknown" and "ratings of the ads could not be read" in f["match_meta_conversion"]["found"]
    assert f["match_terms"]["result"] == "unknown"


def test_when_one_platform_would_not_answer_the_report_is_the_others_with_a_warning():
    out = run(BOTH, graph=acme_graph(fail=["ads"]))
    assert out["summary"]["platforms"] == ["google"] and out["pages"][0]["platforms"] == ["google"] and count(out) == 35
    assert out["warnings"][-1].startswith("Meta Ads could not be read (Meta answered 400") and out["warnings"][-1].endswith("This report is Google Ads alone.")
    assert out["unread"] == ["Meta Ads: the whole account"]
    fresh()
    out = run(BOTH, search_fn=search(fail={"pages": gads.GadsError("api", "Google answered 500")}))
    assert out["summary"]["platforms"] == ["meta"] and out["warnings"][-1].startswith("Google Ads could not be read (Google answered 500). This report is Meta Ads alone.")
    assert out["unread"] == ["Google Ads: the whole account"]


def test_both_platforms_failing_is_an_error_naming_the_first():
    with pytest.raises(gads.GadsError) as e:
        run(BOTH, graph=acme_graph(fail=["ads"]), search_fn=search(fail={"pages": gads.GadsError("api", "Google answered 500")}))
    assert "Google answered 500" in str(e.value)


def test_a_read_that_lost_a_platform_is_kept_only_five_minutes():
    store, calls, graph = FakeStore(), [], Graph(fail=["ads"])
    run(BOTH, store=store, graph=graph, search_fn=search(calls=calls))
    n = len(calls)
    run(BOTH, store=store, graph=graph, search_fn=search(calls=calls))
    assert len(calls) == n                                                 # still fresh
    paid_gads.forget()
    store.at[lp.cache_key(BOTH)] -= lp.PARTIAL_TTL_S + 5
    out = run(BOTH, store=store, search_fn=search(calls=calls))
    assert len(calls) == 2 * n and out["summary"]["platforms"] == ["google", "meta"] and out["warnings"] == []


def test_a_platform_lost_to_its_allowance_leaves_a_whole_saved_read_to_be_served_with_its_date():
    store = FakeStore()
    first = run(BOTH, store=store)
    key = lp.cache_key(BOTH)
    store.old[key] = store.cache.pop(key)
    paid_gads.forget()
    out = run(BOTH, store=store, graph=acme_graph(fail=["insights"], over=None), search_fn=search(fail={"pages": gads.GadsError("quota", "allowance spent")}))
    assert out["stale"] == [{"platform": "google", "savedAt": "2026-10-10T08:00:00+00:00"}, {"platform": "meta", "savedAt": "2026-10-10T08:00:00+00:00"}]
    assert out["pages"][0]["platforms"] == ["google", "meta"] and out["pages"][0]["spend"] == first["pages"][0]["spend"]


def test_one_platform_lost_to_its_allowance_is_the_others_report_when_nothing_was_saved():
    class Spent(acme_graph().__class__):
        def pages(self, path, params):
            raise paid_meta.OverQuota("Meta is rate limiting this app right now.")
    out = run(BOTH, graph=Spent())
    assert out["summary"]["platforms"] == ["google"] and "Meta Ads could not be read (Meta is rate limiting" in out["warnings"][-1]
    fresh()
    store = FakeStore()
    run(BOTH, store=store)
    key = lp.cache_key(BOTH)
    store.old[key] = store.cache.pop(key)
    paid_gads.forget()
    out = run(BOTH, store=store, graph=Spent())
    assert out["summary"]["platforms"] == ["google", "meta"] and out["stale"][0]["savedAt"] == "2026-10-10T08:00:00+00:00"       # a whole old read beats half a new one


def test_two_accounts_that_bill_in_two_currencies_are_not_read_together():
    with pytest.raises(lp.BadRequest) as e:
        run(BOTH, accounts_fn=lambda: [dict(ACCOUNT, currency="EUR")])
    assert "same currency (EUR and USD" in str(e.value)
    run(BOTH, accounts_fn=lambda: [dict(ACCOUNT, currency="")])             # an account that does not say is not held against the other


def test_each_account_string_is_read_and_saved_on_its_own():
    store = FakeStore()
    run(ACCOUNT_ID, store=store)
    run(ACT, store=store, search_fn=no_google)
    run(BOTH, store=store)
    assert {k for k in store.cache if "lp3:" in k} == {lp.cache_key(ACCOUNT_ID), lp.cache_key(ACT), lp.cache_key(BOTH)}


def test_the_lab_test_and_the_model_s_read_name_the_account_the_report_was_made_for(monkeypatch):
    store = FakeStore()
    seen = {}
    monkeypatch.setattr(lp.lp_speed, "run", lambda url, get=None: {"score": 90, "url": url})
    monkeypatch.setattr(lp.lp_speed, "remember", lambda st, key, pid, result, now=None: seen.update(key=key, pid=pid) or result)
    out = lp.run_lab(BOTH, "acme-dental.com/lp", store=store, accounts_fn=lambda: [ACCOUNT], search_fn=search(), fetcher=Fetcher(), now=NOW, meta_pages=acme_graph().pages)
    assert seen == {"key": BOTH, "pid": "acme-dental.com/lp"} and out["score"] == 90
    with pytest.raises(lp.BadRequest):
        lp.run_lab(BOTH, "nope.test/x", store=store, accounts_fn=lambda: [ACCOUNT], search_fn=search(), fetcher=Fetcher(), now=NOW, meta_pages=acme_graph().pages)


def test_the_model_is_shown_a_meta_ad_set_with_no_keywords(monkeypatch):
    store, asked = FakeStore(), []

    def call(system, text):
        asked.append(text)
        return {"verdict": "partly", "promiseKept": "It keeps some of it.", "gaps": ["g"], "pageEdits": ["e"], "headlineIdeas": [], "adEdits": []}
    out = lp.explain(ACT, "acme-dental.com/lp", store=store, search_fn=no_google, fetcher=Fetcher(), call=call, now=NOW, meta_pages=acme_graph().pages)
    assert out["source"] == "model" and "Meta ad set (no keywords or search terms)" in asked[0] and "Free quote" in asked[0]


# ── The downloads ───────────────────────────────────────────────────────────

def test_a_report_that_reads_both_platforms_names_both_accounts_and_each_page_s_platforms():
    t = ex.tables(run(BOTH))
    rows = dict(t["Summary"][1])
    assert rows["Google Ads account"].endswith("(1112223334)") and rows["Meta ad account"].endswith("(act_1234567890)")
    head, pages = t["Pages"]
    assert head[-1] == "Platforms" and pages[0][-1] == "Google Ads and Meta Ads"


def test_the_meta_ads_are_listed_with_metas_ratings():
    head, rows = ex.tables(run(BOTH))["Meta ads"]
    assert head[:5] == ["Page", "Campaign", "Ad set", "Ad", "Spend"] and head[-3:] == ["Conversion rate rating", "Quality rating", "Engagement rate rating"]
    assert len(rows) == 1 and rows[0][3] == "Ad 1" and rows[0][4] == 20.0 and rows[0][5] == "Free quote" and rows[0][6:] == ["Below average", "Average", "Average"]


def test_a_google_report_has_no_meta_sheet_and_says_google_ads_as_before():
    t = ex.tables(make_report())
    assert "Meta ads" not in t and dict(t["Summary"][1])["Google Ads account"]
    assert t["Pages"][1][0][-1] == "Google Ads"


def test_a_saved_copy_of_either_platform_says_which_would_not_answer():
    report = dict(make_report(), stale=[{"platform": "google", "savedAt": "2026-10-10T08:00:00+00:00"}, {"platform": "meta", "savedAt": "2026-10-10T08:00:00+00:00"}])
    notes = [r[1] for r in ex.tables(report)["Notes"][1] if r[0] == "Saved read"]
    assert notes == ["The read is the one saved on 2026-10-10 08:00 because Google Ads would not answer now.", "The read is the one saved on 2026-10-10 08:00 because Meta Ads would not answer now."]


def test_a_both_report_opens_as_excel_pdf_and_zip():
    report = run(BOTH)
    assert ex.xlsx(report)[:2] == b"PK" and ex.pdf(report)[:4] == b"%PDF" and ex.csv_zip(report)[:2] == b"PK"
    assert ex.file_stem(report).startswith("Acme-Dental")


# ── Pinned by mutation ──────────────────────────────────────────────────────

def test_a_read_of_two_platforms_keeps_twice_the_pages_from_each_so_the_other_platform_can_lift_a_page(monkeypatch):
    seen = {}
    real_g, real_m = lp.lp_read.read_account, lp.lp_meta.read_account

    def g(*a, **kw):
        seen["google"] = kw["keep"]
        return real_g(*a, **kw)

    def m(*a, **kw):
        seen["meta"] = kw["keep"]
        return real_m(*a, **kw)
    monkeypatch.setattr(lp.lp_read, "read_account", g)
    monkeypatch.setattr(lp.lp_meta, "read_account", m)
    run(BOTH)
    assert seen == {"google": 2 * lp.lp_read.KEEP_PAGES, "meta": 2 * lp.lp_read.KEEP_PAGES}
    seen.clear()
    fresh()
    run(ACT, search_fn=no_google)
    assert seen == {"meta": lp.lp_read.KEEP_PAGES}


def test_a_report_with_no_meta_spend_off_the_page_has_no_note_about_it():
    assert not any(n.startswith("Meta spent") for n in run(ACT, search_fn=no_google)["notes"])


def test_a_fresh_read_that_lost_a_platform_is_not_replaced_by_an_old_one_that_lost_it_too():
    class Spent(acme_graph().__class__):
        def pages(self, path, params):
            raise paid_meta.OverQuota("Meta is rate limiting this app right now.")
    store = FakeStore()
    run(BOTH, store=store, graph=Spent())                                   # the saved copy lost Meta to its allowance
    key = lp.cache_key(BOTH)
    store.old[key] = store.cache.pop(key)
    paid_gads.forget()
    out = run(BOTH, store=store, graph=Spent())
    assert "stale" not in out and out["summary"]["platforms"] == ["google"]


def test_an_address_the_ad_platform_fills_in_is_said_in_the_platforms_words():
    ads = [link_ad("1", "https://{{site}}.acme-dental.com/lp")]
    note = next(n for n in run(ACT, graph=acme_graph(ads=ads), search_fn=no_google)["notes"] if "fills in when someone clicks" in n)
    assert note.startswith("1 page sends people to an address the ad platform fills in when someone clicks")


def test_a_page_that_refuses_us_is_checked_only_on_what_the_platforms_that_were_read_say():
    refuse = Fetcher({"/lp": (403, b"no")})
    note = next(n for n in run(ACT, fetcher=refuse, search_fn=no_google)["notes"] if "refused our request" in n)
    assert note == "1 page refused our request, so it was checked only on what Meta Ads says."
    fresh()
    note = next(n for n in run(BOTH, fetcher=refuse)["notes"] if "refused our request" in n)
    assert note == "1 page refused our request, so it was checked only on what Google Ads and Meta Ads say."
    fresh()
    note = next(n for n in run(ACCOUNT_ID, fetcher=refuse)["notes"] if "refused our request" in n)
    assert note == "1 page refused our request, so it was checked only on what Google Ads says."


def test_the_model_s_answer_is_kept_for_one_account_and_not_handed_to_another_that_has_a_page_of_the_same_name():
    from tracker import paid_lp_ai
    paid_lp_ai.clear_cache()
    asked = []

    def call(system, text):
        asked.append(1)
        return {"verdict": "matches", "promiseKept": "Yes.", "gaps": [], "pageEdits": [], "headlineIdeas": [], "adEdits": []}
    store = FakeStore()
    kw = dict(store=store, accounts_fn=lambda: [ACCOUNT], search_fn=search(), fetcher=Fetcher(), call=call, now=NOW, meta_pages=acme_graph().pages)
    lp.explain(ACCOUNT_ID, "acme-dental.com/lp", **kw)
    lp.explain(ACT, "acme-dental.com/lp", **kw)
    assert len(asked) == 2
    lp.explain(ACT, "acme-dental.com/lp", **kw)
    assert len(asked) == 2
    paid_lp_ai.clear_cache()


def test_a_meta_ad_in_the_report_shows_its_first_two_headlines_and_its_ratings():
    ad = link_ad("1", LP, asset_feed_spec={"titles": [{"text": "Second"}, {"text": "Third"}]})
    out = run(ACT, graph=acme_graph(ads=[ad]), search_fn=no_google)
    shown = out["pages"][0]["adGroups"][0]["ads"][0]
    assert shown["name"] == "Ad 1" and shown["cost"] == 20.0 and shown["headlines"] == ["Free quote", "Second"] and shown["ranks"]["conversion"] == "below"
    google = run(ACCOUNT_ID)
    assert "ads" not in google["pages"][0]["adGroups"][0]
