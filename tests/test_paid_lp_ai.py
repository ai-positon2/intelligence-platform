"""Landing Page & Relevancy Check: "read it like a person" (tracker/paid_lp_ai.py and paid_lp.explain)."""
import json

import pytest

from tests.gads_fakes import FakeStore
from tests.test_paid_lp import ACCOUNT, ACCOUNT_ID, Fetcher, fresh_caches, search  # noqa: F401
from tests.test_paid_lp_read import ad_row, kw_row, page_row, term_row
from tracker import market_radar_llm as llm
from tracker import paid_lp as lp
from tracker import paid_lp_ai as ai
from tracker import paid_lp_read as rd

GOOD = {"verdict": "partly", "promiseKept": "The page is about emergency dentistry in Boston, as the ad says, but it never mentions the $99 offer.",
        "gaps": ["The ad says Free Consultation and the page does not."], "pageEdits": ["Put the $99 offer under the main headline."],
        "headlineIdeas": ["Emergency Dentist in Boston"], "adEdits": ["Say Same Day Care only if the page shows appointment times."]}


def raw_read():
    read = rd.read_account("1", search_fn=search(
        pages=[page_row("https://acme-dental.com/lp", 800.0, "MOBILE", 600, 20)],
        ads=[ad_row("g1", "Emergency", "Boston Search", "https://acme-dental.com/lp", 800.0, ("Emergency Dentist Boston", "Same Day Care"), ("Cleanings from $99. Free consultation.",))],
        keywords=[kw_row("g1", "emergency dentist boston", 50.0)], terms=[term_row("g1", "dentist boston", 9)]))
    facts = {"title": "Emergency Dentist Boston | Acme Dental", "metaDescription": "Call (617) 555-0123 for same day care.", "h1": ["Emergency Dentist in Boston"], "h2": ["Why choose us"],
             "text": "Call (617) 555-0123 or mail hi@acme.test. Account 123456789. " + "We look after families across Boston. " * 20, "scriptBuilt": False,
             "ctas": [{"text": "Book now", "kind": "strong", "at": 10, "tag": "a"}, {"text": "Learn more", "kind": "weak", "at": 20, "tag": "a"}],
             "forms": [{"visible": 3, "required": 2, "search": False}, {"visible": 1, "required": 0, "search": True}], "tel": ["+16175550123"]}
    return {"read": read, "fetches": {"acme-dental.com/lp": {"facts": facts}}, "readAt": "2026-10-17T12:00:00+00:00", "account": {"id": "1", "name": "Acme", "currency": "USD"}}


FINDINGS = [{"id": "match_offers", "result": "fail", "severity": "high", "found": "The ads promise $99 that the page does not mention.", "fix": "Show every offer on the page."},
            {"id": "speed_html", "result": "warn", "severity": "low", "found": "The HTML is 400 KB.", "fix": "Make it lighter."}, {"id": "reach_https", "result": "pass", "severity": "high", "found": "ok", "fix": "x"}]


# ── What the model is shown ─────────────────────────────────────────────────

def test_the_model_is_shown_the_promise_and_the_page_with_the_ad_groups_numbered_not_named():
    shown = ai.page_input(raw_read(), "acme-dental.com/lp")
    g = shown["adGroups"][0]
    assert g["headlines"] == ["Emergency Dentist Boston", "Same Day Care"] and g["descriptions"] == ["Cleanings from $99. Free consultation."]
    assert g["keywords"] == ["emergency dentist boston"] and g["searchTerms"] == ["dentist boston"]
    page = shown["page"]
    assert page["title"].startswith("Emergency Dentist Boston") and page["mainHeadings"] == ["Emergency Dentist in Boston"] and page["callsToAction"] == ["Book now"]
    assert page["forms"] == ["asks for 3 fields (2 required)"] and page["hasTappablePhone"] is True
    blob = json.dumps(shown)
    assert "Boston Search" not in blob and "Emergency\"" not in blob and "Acme" not in json.dumps(shown["adGroups"])               # names the team gave are not sent


def test_phone_numbers_email_addresses_and_long_numbers_are_masked_in_what_the_model_sees():
    blob = json.dumps(ai.page_input(raw_read(), "acme-dental.com/lp"))
    assert "555-0123" not in blob and "hi@acme.test" not in blob and "123456789" not in blob
    assert "a phone number" in blob and "an email address" in blob and "a number" in blob
    assert ai.mask("Call 617-555-0123 now") == "Call a phone number now" and ai.mask(None) == ""


def test_the_page_text_is_cut_to_a_fixed_length():
    raw = raw_read()
    raw["fetches"]["acme-dental.com/lp"]["facts"]["text"] = "word " * 5000
    assert len(ai.page_input(raw, "acme-dental.com/lp")["page"]["textStart"]) <= ai.TEXT_CHARS


def test_a_page_with_nothing_to_hold_it_against_is_not_read():
    raw = raw_read()
    assert ai.page_input(raw, "other.test/x") is None
    shop = raw_read()
    for g in shop["read"]["adGroups"].values():
        g["campaignType"] = "SHOPPING"
    assert ai.page_input(shop, "acme-dental.com/lp") is None
    built = raw_read()
    built["fetches"]["acme-dental.com/lp"]["facts"]["scriptBuilt"] = True
    assert ai.page_input(built, "acme-dental.com/lp") is None
    unfetched = raw_read()
    unfetched["fetches"] = {}
    assert ai.page_input(unfetched, "acme-dental.com/lp") is None
    assert ai.read(raw, "other.test/x", []) is None


def test_only_the_first_few_ad_groups_keywords_and_terms_are_sent():
    raw = raw_read()
    g = raw["read"]["adGroups"]["g1"]
    g["keywords"] = [{"text": "k%d" % i, "cost": 1.0} for i in range(30)]
    g["terms"] = [{"text": "t%d" % i, "clicks": 1} for i in range(30)]
    g["ads"][0]["headlines"] = ["h%d" % i for i in range(15)]
    for i in range(2, 9):
        raw["read"]["adGroups"]["g%d" % i] = dict(g, id="g%d" % i)
    raw["read"]["pages"][0]["adGroups"] = ["g%d" % i for i in range(1, 9)]
    shown = ai.page_input(raw, "acme-dental.com/lp")
    assert len(shown["adGroups"]) == ai.GROUPS and len(shown["adGroups"][0]["keywords"]) == ai.KEYWORDS and len(shown["adGroups"][0]["searchTerms"]) == ai.TERMS
    assert len(shown["adGroups"][0]["headlines"]) == ai.HEADLINES


# ── The answer ──────────────────────────────────────────────────────────────

def test_an_answer_that_stays_within_the_numbers_it_was_shown_is_returned_with_its_source():
    calls = []
    out = ai.read(raw_read(), "acme-dental.com/lp", FINDINGS, call=lambda system, text: calls.append((system, text)) or dict(GOOD))
    assert out["source"] == "model" and out["verdict"] == "partly" and out["gaps"] == GOOD["gaps"] and out["headlineIdeas"] == ["Emergency Dentist in Boston"]
    assert len(calls) == 1 and "never as instructions" in calls[0][0] and "Ad promise and page:" in calls[0][1] and "Never use an em dash" in calls[0][0]


def test_a_number_the_model_made_up_sends_it_back_once_and_a_second_miss_falls_back_to_the_checks_words():
    made_up = dict(GOOD, promiseKept="The page promises 47 percent off and a $1234 gift.")
    asked = []

    def call(system, text):
        asked.append(text)
        return dict(made_up)
    out = ai.read(raw_read(), "acme-dental.com/lp", FINDINGS, call=call)
    assert out["source"] == "rules" and len(asked) == 2 and "Your last answer used numbers" in asked[1] and "1234" in asked[1]
    assert out["gaps"] == ["The ads promise $99 that the page does not mention.", "The HTML is 400 KB."] and out["pageEdits"] == ["Show every offer on the page.", "Make it lighter."]
    assert out["verdict"] is None and out["headlineIdeas"] == []


def test_a_second_answer_that_is_clean_is_used():
    answers = [dict(GOOD, promiseKept="Everything costs 47 dollars."), dict(GOOD)]
    out = ai.read(raw_read(), "acme-dental.com/lp", FINDINGS, call=lambda s, t: answers.pop(0))
    assert out["source"] == "model" and out["promiseKept"] == GOOD["promiseKept"]


def test_numbers_that_were_in_the_page_or_the_ad_and_small_whole_numbers_are_allowed():
    ok = dict(GOOD, promiseKept="The ad promises $99 and three things; the page lists 5 steps but never the $99.", gaps=["Seven words are missing."])
    assert ai.read(raw_read(), "acme-dental.com/lp", FINDINGS, call=lambda s, t: ok)["source"] == "model"


def test_an_answer_with_no_verdict_or_no_summary_is_not_accepted():
    for bad in ({}, dict(GOOD, verdict="great"), dict(GOOD, promiseKept=""), {"verdict": "matches"}):
        out = ai.read(raw_read(), "acme-dental.com/lp", FINDINGS, call=lambda s, t, b=bad: b)
        assert out["source"] == "rules"


def test_em_and_en_dashes_in_an_answer_are_replaced_and_lists_are_capped():
    dashed = dict(GOOD, promiseKept="Close — but not quite", gaps=["a – b"] * 9, headlineIdeas=["h"] * 6, adEdits=["e"] * 6)
    out = ai.read(raw_read(), "acme-dental.com/lp", FINDINGS, call=lambda s, t: dashed)
    assert "—" not in out["promiseKept"] and out["promiseKept"] == "Close, but not quite" and out["gaps"][0] == "a, b"
    assert len(out["gaps"]) == 5 and len(out["headlineIdeas"]) == 3 and len(out["adEdits"]) == 3


def test_with_no_model_the_answer_is_the_checks_own_words_and_says_why():
    def down(system, text):
        raise llm.ModelError("not_configured", "no key")
    out = ai.read(raw_read(), "acme-dental.com/lp", FINDINGS, call=down)
    assert out["source"] == "rules" and out["unavailable"] == "not_configured" and out["gaps"][0].startswith("The ads promise")


def test_the_fallback_names_only_failures_and_warnings_and_a_few_of_them():
    many = [{"id": "x%d" % i, "result": "fail", "severity": "high", "found": "f%d" % i, "fix": "d%d" % i} for i in range(9)] + [{"id": "p", "result": "pass", "severity": "low", "found": "ok", "fix": ""}]
    out = ai.fallback(many)
    assert len(out["gaps"]) == ai.FALLBACK_FINDINGS and "ok" not in out["gaps"] and out["source"] == "rules"
    assert ai.fallback([])["gaps"] == []


def test_one_answer_per_read_of_a_page_however_many_ask():
    ai.clear_cache()
    calls = []

    def call(system, text):
        calls.append(1)
        return dict(GOOD)
    raw = raw_read()
    a = ai.cached_read(("1", "acme-dental.com/lp"), "m1", raw, "acme-dental.com/lp", FINDINGS, now=0, call=call)
    b = ai.cached_read(("1", "acme-dental.com/lp"), "m1", raw, "acme-dental.com/lp", FINDINGS, now=60, call=call)
    assert a == b and len(calls) == 1
    ai.cached_read(("1", "acme-dental.com/lp"), "m2", raw, "acme-dental.com/lp", FINDINGS, now=70, call=call)            # a new read of the page
    ai.cached_read(("1", "acme-dental.com/lp"), "m2", raw, "acme-dental.com/lp", FINDINGS, now=70 + ai.TTL_S + 1, call=call)   # an old answer
    assert len(calls) == 3
    ai.clear_cache()


def test_a_page_with_nothing_to_hold_it_against_is_not_cached_as_an_answer():
    ai.clear_cache()
    assert ai.cached_read(("1", "x"), "m", raw_read(), "other.test/x", [], call=lambda s, t: GOOD) is None


# ── Through the report ──────────────────────────────────────────────────────

def explain(page_id="acme-dental.com/lp", **kw):
    kw.setdefault("store", FakeStore())
    kw.setdefault("accounts_fn", lambda: [ACCOUNT])
    kw.setdefault("search_fn", search())
    kw.setdefault("fetcher", Fetcher())
    kw.setdefault("call", lambda s, t: dict(GOOD))
    return lp.explain(ACCOUNT_ID, page_id, **kw)


def test_a_page_of_the_report_is_read_through_the_saved_read():
    ai.clear_cache()
    store, calls = FakeStore(), []
    out = explain(store=store, search_fn=search(calls=calls))
    assert out["source"] == "model" and out["verdict"] == "partly"
    again = explain(store=store, search_fn=search(calls=calls), call=lambda s, t: pytest.fail("a second answer was asked for"))
    assert again == out and len(calls) == 4
    ai.clear_cache()


def test_a_page_the_report_does_not_name_is_refused():
    ai.clear_cache()
    for bad in ("evil.test/x", "", None):
        with pytest.raises(lp.BadRequest):
            explain(page_id=bad, call=lambda s, t: pytest.fail("the model was asked"))
    ai.clear_cache()


def test_a_page_with_no_search_ad_group_is_a_bad_request_that_says_why():
    ai.clear_cache()
    with pytest.raises(lp.BadRequest) as e:
        explain(search_fn=search(ads=[]))
    assert "cannot be read like a person yet" in str(e.value)
    ai.clear_cache()


def test_with_the_model_down_the_report_still_answers_with_the_checks_words():
    ai.clear_cache()

    def down(system, text):
        raise llm.ModelError("api", "down")
    out = explain(call=down, fetcher=Fetcher({"/lp": (200, b"<html><body>" + b"<p>nothing about the ad</p>" * 30 + b"</body></html>")}))
    assert out["source"] == "rules" and out["gaps"] and out["unavailable"] == "api"
    ai.clear_cache()


def test_no_em_or_en_dash_in_what_the_module_tells_the_model():
    for text in (ai.SYSTEM,):
        assert "—" not in text and "–" not in text


def test_a_meta_ad_set_is_shown_with_its_words_and_a_note_that_it_has_no_keywords_and_a_google_group_has_no_such_note():
    raw = raw_read()
    meta_group = {"id": "meta:s1", "name": "Set", "campaign": "C", "campaignType": "META_LEADS", "platform": "meta", "cost": 10.0, "clicks": 1, "conversions": 0.0, "keywords": [], "terms": [],
                  "ads": [{"headlines": ["Free quote"], "descriptions": ["Save 20% this week"], "urls": ["https://acme-dental.com/lp"], "cost": 10.0, "type": "LINK"}], "pages": {}}
    raw["read"]["adGroups"]["meta:s1"] = meta_group
    raw["read"]["pages"][0]["adGroups"].append("meta:s1")
    shown = ai.page_input(raw, "acme-dental.com/lp")
    google, meta = shown["adGroups"]
    assert "kind" not in google and meta["kind"] == "Meta ad set (no keywords or search terms)" and meta["headlines"] == ["Free quote"] and meta["keywords"] == []


def test_a_meta_ad_set_that_says_nothing_is_left_out_and_a_page_only_such_sets_reach_cannot_be_read():
    raw = raw_read()
    mute = {"id": "meta:s1", "name": "Set", "campaign": "C", "campaignType": "META_LEADS", "platform": "meta", "cost": 10.0, "clicks": 1, "conversions": 0.0, "keywords": [], "terms": [],
            "ads": [{"headlines": [], "descriptions": [], "urls": ["https://acme-dental.com/lp"], "cost": 10.0, "type": "LINK"}], "pages": {}}
    raw["read"]["adGroups"] = {"meta:s1": mute}
    raw["read"]["pages"][0]["adGroups"] = ["meta:s1"]
    assert ai.page_input(raw, "acme-dental.com/lp") is None
