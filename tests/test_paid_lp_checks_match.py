"""Landing Page & Relevancy Check: does the page match the ad (tracker/paid_lp_checks_match.py)."""
import pytest

from tests.lp_fakes import blocked_fetch, fetch, group, keyword, page, term
from tracker import paid_lp_checks as all_checks
from tracker import paid_lp_checks_match as cm
from tracker import paid_lp_checks_page as cp
from tracker import paid_lp_model as m

CHECKS = dict(cm.CHECKS)


def run(check_id, **kw):
    return CHECKS[check_id](cp.Ctx(page(**kw)))


def with_text(text, title="Emergency Dentist Boston", h1=("Emergency Dentist in Boston",), **extra):
    return {"facts_over": dict({"text": text, "title": title, "h1": list(h1), "wordCount": max(200, len(text.split()))}, **extra)}


# ── The catalog and the list agree ──────────────────────────────────────────

def test_every_check_here_has_a_catalog_entry_and_every_match_entry_is_checked():
    ids = [i for i, _f in cm.CHECKS]
    assert len(ids) == len(set(ids)) and set(ids) <= set(m.CATALOG)
    assert {i for i, e in m.CATALOG.items() if e["section"] == "match"} == set(ids)


def test_a_good_page_passes_every_match_check():
    for cid, fn in cm.CHECKS:
        if cid in all_checks.META_ONLY:
            continue                                       # Meta's checks are for a page Meta's ads reach (tests/test_paid_lp_checks_meta.py)
        f = fn(cp.Ctx(page()))
        assert f["result"] == "pass" and f["id"] == cid, (cid, f)


# ── Nothing to compare ──────────────────────────────────────────────────────

AD_SIDE = [i for i, _f in cm.CHECKS if i not in ("match_h1", "match_content") and i not in all_checks.META_ONLY]


@pytest.mark.parametrize("cid", AD_SIDE)
def test_a_page_no_search_ad_group_reaches_has_nothing_to_hold_it_against(cid):
    f = run(cid, groups=[])
    assert f["result"] == "na" and "Shopping and Performance Max" in f["found"]


@pytest.mark.parametrize("cid", AD_SIDE)
def test_a_shopping_group_is_not_compared_either(cid):
    assert run(cid, groups=[group(ctype="SHOPPING")])["result"] == "na"


@pytest.mark.parametrize("cid", AD_SIDE)
def test_when_the_ads_could_not_be_read_it_is_not_said_that_no_ad_group_reaches_the_page(cid):
    f = run(cid, groups=[], ok={"pages": True, "ads": False, "keywords": True, "terms": True})
    assert f["result"] == "unknown" and "ads could not be read" in f["found"]


@pytest.mark.parametrize("cid", AD_SIDE)
def test_an_unread_page_leaves_the_match_checks_as_could_not_check(cid):
    if cid in ("match_lp_experience", "match_ad_relevance"):
        return                                             # Google's own ratings do not need the page
    assert dict(cm.CHECKS)[cid](cp.Ctx(page(fetch=blocked_fetch())))["result"] == "unknown"
    assert dict(cm.CHECKS)[cid](cp.Ctx(page(fetch=None)))["result"] == "unknown"


@pytest.mark.parametrize("cid", AD_SIDE)
def test_a_page_built_by_scripts_is_not_failed_for_words_it_may_add_later(cid):
    if cid in ("match_lp_experience", "match_ad_relevance"):
        return
    f = run(cid, fetch_over={"facts_over": {"scriptBuilt": True, "wordCount": 5, "text": ""}})
    assert f["result"] == "unknown" and "built by scripts" in f["found"]


def test_googles_ratings_are_judged_even_when_the_page_could_not_be_read():
    assert dict(cm.CHECKS)["match_lp_experience"](cp.Ctx(page(fetch=blocked_fetch())))["result"] == "pass"


# ── Keywords ────────────────────────────────────────────────────────────────

def kw_group(rows, **kw):
    return [group(keywords=rows, **kw)]


@pytest.mark.parametrize("on,off,res", [(80, 20, "pass"), (75, 25, "pass"), (74, 26, "warn"), (50, 50, "warn"), (49, 51, "fail"), (0, 100, "fail")])
def test_the_share_of_keyword_spend_that_is_on_the_page(on, off, res):
    rows = [keyword("emergency dentist", float(on) or 0.001), keyword("root canal", float(off) or 0.001)]
    f = run("match_keywords_page", groups=kw_group(rows))
    assert f["result"] == res


def test_the_finding_names_the_biggest_missing_keywords_with_the_share_and_the_count_judged():
    rows = [keyword("emergency dentist", 40.0), keyword("root canal", 30.0), keyword("braces", 20.0), keyword("veneers", 10.0)]
    f = run("match_keywords_page", groups=kw_group(rows))
    assert f["result"] == "fail" and "40%" in f["found"] and "4 keywords judged" in f["found"]
    assert 'Missing: "root canal", "braces", "veneers".' in f["found"] and f["items"] == ["root canal", "braces", "veneers"] and f["count"] == 3


def test_partly_present_keywords_are_listed_but_not_called_missing():
    rows = [keyword("emergency dentist", 50.0), keyword("dental crowns", 50.0)]
    f = run("match_keywords_page", groups=kw_group(rows))
    assert f["items"] == ["dental crowns"] and "Missing" not in f["found"]


def test_keywords_with_no_words_to_look_for_leave_nothing_to_judge():
    f = run("match_keywords_page", groups=kw_group([keyword("near me", 10.0)]))
    assert f["result"] == "na"
    assert run("match_keywords_headline", groups=kw_group([keyword("near me", 10.0)]))["result"] == "na"


def test_keywords_that_could_not_be_read_are_could_not_check_not_a_pass():
    for cid in ("match_keywords_page", "match_keywords_headline", "match_terms", "match_lp_experience", "match_ad_relevance"):
        f = run(cid, ok={"pages": True, "ads": True, "keywords": False, "terms": False})
        assert f["result"] == "unknown", cid


@pytest.mark.parametrize("head,rest,res", [(50, 50, "pass"), (49, 51, "warn"), (25, 75, "warn"), (24, 76, "fail")])
def test_the_share_of_keyword_spend_in_the_page_headline(head, rest, res):
    rows = [keyword("emergency dentist boston", float(head)), keyword("tooth pain", float(rest))]            # the first is in the title, the second only in the text
    assert run("match_keywords_headline", groups=kw_group(rows))["result"] == res


def test_a_keyword_with_its_own_address_is_counted_on_that_page_not_on_the_ad_groups_other_pages():
    rows = [keyword("emergency dentist", 10.0), keyword("root canal", 90.0, url="https://elsewhere-dental.com/rc")]
    f = run("match_keywords_page", groups=kw_group(rows))
    assert f["result"] == "pass"                                      # root canal goes to its own page, so this page is judged on the other keyword only
    rows2 = [keyword("root canal", 90.0, url="https://acme-dental.com/lp")]
    g = group(urls=("https://elsewhere-dental.com/x",), keywords=rows2)
    assert run("match_keywords_page", groups=[g])["result"] == "fail"           # an ad group whose keyword points here reaches the page by that keyword


# ── Search terms ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("on,off,res", [(70, 30, "pass"), (69, 31, "warn"), (45, 55, "warn"), (44, 56, "fail")])
def test_the_share_of_search_term_spend_that_is_on_the_page(on, off, res):
    terms = [term("emergency dentist", float(on), 10), term("zebra grooming", float(off), 10), term("tooth pain", 0.01, 1)]
    f = run("match_terms", groups=[group(terms=terms)])
    assert f["result"] == res


def test_too_few_search_terms_to_judge_is_not_applicable():
    f = run("match_terms", groups=[group(terms=[term("emergency dentist"), term("zebra")])])
    assert f["result"] == "na" and "Fewer than 3" in f["found"]


def test_search_terms_of_a_group_that_reaches_the_page_only_by_a_keyword_address_are_not_counted_here():
    g = group(urls=("https://elsewhere-dental.com/x",), keywords=[keyword("emergency dentist", url="https://acme-dental.com/lp")],
              terms=[term("zebra"), term("llama"), term("giraffe")])
    assert run("match_terms", groups=[g])["result"] == "na"


# ── Headlines ───────────────────────────────────────────────────────────────

def test_a_page_whose_headings_carry_none_of_the_ads_headlines_fails_and_names_the_ad_groups():
    g = group(name="Whitening", headlines=["Teeth Whitening Deals", "Brighter Smile Today"])
    f = run("match_headline", groups=[g])
    assert f["result"] == "fail" and f["items"][0].startswith('Whitening: "') and "of its words are in the page's headings" in f["items"][0]


def test_the_headline_check_weighs_ad_groups_by_spend():
    good = group("g1", "Emergency", cost=80.0)
    bad = group("g2", "Whitening", headlines=["Teeth Whitening Deals"], cost=20.0)
    f = run("match_headline", groups=[good, bad])
    assert f["result"] == "pass" and "80%" in f["found"] and f["count"] == 1
    f2 = run("match_headline", groups=[group("g1", "Emergency", cost=10.0), group("g2", "Whitening", headlines=["Teeth Whitening Deals"], cost=90.0)])
    assert f2["result"] == "fail"


def test_ads_with_no_headlines_cannot_be_compared():
    g = group(headlines=[])
    f = run("match_headline", groups=[g])
    assert f["result"] == "na"


# ── Offers ──────────────────────────────────────────────────────────────────

def test_an_offer_the_page_does_not_mention_fails_and_is_named():
    g = group(headlines=["$49 Whitening Special", "Emergency Dentist Boston"], descriptions=["Free X-rays this week"])
    f = run("match_offers", groups=[g])
    assert f["result"] == "fail" and "$49" in f["found"] and "free x-rays" in f["found"].lower() and f["count"] == 2


def test_a_missing_service_promise_alone_is_only_a_warning():
    g = group(headlines=["Open 24/7", "Emergency Dentist Boston"], descriptions=[])
    page_ = page(groups=[g], fetch_over={"facts_over": {"text": "Emergency dentist in Boston. Call (617) 555-0123. " * 40}})
    f = CHECKS["match_offers"](cp.Ctx(page_))
    assert f["result"] == "warn" and "24/7" in f["found"]


def test_ads_that_promise_nothing_have_nothing_to_find():
    g = group(headlines=["Emergency Dentist Boston"], descriptions=["Trusted care"])
    assert run("match_offers", groups=[g])["result"] == "na"


def test_offers_of_an_ad_group_that_reaches_the_page_only_through_a_keyword_address_are_not_held_against_it():
    g = group(urls=("https://elsewhere-dental.com/x",), headlines=["$1 Cleanings"], keywords=[keyword("emergency dentist", url="https://acme-dental.com/lp")])
    assert run("match_offers", groups=[g])["result"] == "na"


# ── The H1 and the amount to read ───────────────────────────────────────────

def test_the_main_headline():
    assert run("match_h1", fetch_over={"facts_over": {"h1Count": 2}})["result"] == "pass"
    none = run("match_h1", fetch_over={"facts_over": {"h1Count": 0, "h1": []}})
    assert none["result"] == "warn" and "no H1" in none["found"]
    many = run("match_h1", fetch_over={"facts_over": {"h1Count": 7}})
    assert many["result"] == "warn" and "7 H1" in many["found"]


def test_the_h1_and_content_checks_apply_to_a_page_with_no_search_ad_group():
    assert run("match_h1", groups=[])["result"] == "pass" and run("match_content", groups=[])["result"] == "pass"


@pytest.mark.parametrize("words,res", [(400, "pass"), (150, "pass"), (149, "warn"), (50, "warn"), (49, "fail")])
def test_the_amount_of_text(words, res):
    f = run("match_content", fetch_over={"facts_over": {"wordCount": words, "scriptBuilt": False}})
    assert f["result"] == res and f["severity"] == "low"


# ── Google's ratings ────────────────────────────────────────────────────────

def test_the_share_of_rated_spend_below_average():
    rows = [keyword("a", 70.0, landingPage="above"), keyword("b", 30.0, landingPage="below")]
    f = run("match_lp_experience", groups=kw_group(rows))
    assert f["result"] == "fail" and "30%" in f["found"] and f["items"] == ["b"]
    rows = [keyword("a", 80.0, landingPage="above"), keyword("b", 20.0, landingPage="below")]
    assert run("match_lp_experience", groups=kw_group(rows))["result"] == "warn"
    rows = [keyword("a", 91.0, landingPage="above"), keyword("b", 9.0, landingPage="below")]
    small = run("match_lp_experience", groups=kw_group(rows))
    assert small["result"] == "pass" and small["items"] == ["b"] and "9%" in small["found"]            # too small a share to ask anyone to act on
    rows = [keyword("a", 90.0, landingPage="above"), keyword("b", 10.0, landingPage="below")]
    assert run("match_lp_experience", groups=kw_group(rows))["result"] == "warn"
    rows = [keyword("a", 80.0, landingPage="average"), keyword("b", 20.0, landingPage="above")]
    assert run("match_lp_experience", groups=kw_group(rows))["result"] == "pass"


def test_the_two_ratings_are_read_from_their_own_field():
    rows = [keyword("a", 50.0, landingPage="above", adRelevance="below")]
    assert run("match_lp_experience", groups=kw_group(rows))["result"] == "pass"
    assert run("match_ad_relevance", groups=kw_group(rows))["result"] == "fail"


def test_keywords_google_did_not_rate_are_left_out_and_no_ratings_at_all_is_could_not_check():
    rows = [keyword("a", 50.0, landingPage=None), keyword("b", 50.0, landingPage="below")]
    f = run("match_lp_experience", groups=kw_group(rows))
    assert f["result"] == "fail" and "1 of 1 rated" in f["found"]
    none = run("match_lp_experience", groups=kw_group([keyword("a", 50.0, landingPage=None)]))
    assert none["result"] == "unknown" and "enough impressions" in none["found"]


def test_the_rating_check_names_the_biggest_offenders_first_and_caps_the_list():
    rows = [keyword("kw%d" % i, float(100 - i), landingPage="below") for i in range(20)]
    f = run("match_lp_experience", groups=kw_group(rows))
    assert f["items"][0] == "kw0" and len(f["items"]) == cm.SHOWN and f["count"] == 20


def test_the_same_keyword_in_two_ad_groups_is_named_once():
    g1 = group("g1", "One", keywords=[keyword("dentist near me", 60.0, landingPage="below")])
    g2 = group("g2", "Two", keywords=[keyword("dentist near me", 40.0, landingPage="below")])
    f = run("match_lp_experience", groups=[g1, g2])
    assert f["items"] == ["dentist near me"] and f["count"] == 2


def test_a_page_reached_by_several_ad_groups_pools_their_keywords():
    g1 = group("g1", "One", keywords=[keyword("emergency dentist", 50.0)])
    g2 = group("g2", "Two", keywords=[keyword("root canal", 50.0)])
    f = run("match_keywords_page", groups=[g1, g2])
    assert f["result"] == "warn" and "2 keywords judged" in f["found"]
