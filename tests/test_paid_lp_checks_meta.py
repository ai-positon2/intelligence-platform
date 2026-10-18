"""Landing Page & Relevancy Check: a page Meta's ads send people to (tracker/paid_lp_checks.py and the Meta checks in the match and page modules)."""
import pytest

from tests.lp_fakes import blocked_fetch, fetch, group, meta_ad, meta_group, meta_page, page
from tracker import paid_lp_checks as checks
from tracker import paid_lp_checks_match as cm
from tracker import paid_lp_checks_page as cp
from tracker import paid_lp_model as model


def one(check_id, p):
    return {f["id"]: f for f in checks.run(p)}[check_id]


def ctx(**kw):
    return cp.Ctx(meta_page(**kw))


# ── Which checks a page is held to ──────────────────────────────────────────

def test_a_page_only_google_reaches_is_held_to_the_thirty_five_it_always_was():
    ids = {f["id"] for f in checks.run(page())}
    assert len(ids) == 35 and not ids & checks.META_ONLY


def test_a_page_only_meta_reaches_is_not_held_to_googles_keywords_quality_score_tag_or_crawler():
    found = checks.run(meta_page())
    ids = {f["id"] for f in found}
    assert len(found) == 30 and ids == set(model.CATALOG) - checks.GOOGLE_ONLY
    assert checks.META_ONLY <= ids and not ids & checks.GOOGLE_ONLY
    assert [s["key"] for s in model.sections_of(found)] == ["reach", "match", "path", "mobile", "speed", "trust"]


def test_a_page_both_reach_is_held_to_every_check():
    assert {f["id"] for f in checks.run(meta_page(platforms=["google", "meta"]))} == set(model.CATALOG)


def test_a_good_meta_page_gets_an_a_and_nothing_could_not_be_checked():
    found = checks.run(meta_page(fetch_over={"redirected": True, "keptIds": {"gclid": False, "fbclid": True, "utm": True}}))
    s = model.page_summary(found)
    assert s["grade"] == "A" and s["unknown"] == 0
    assert [f["id"] for f in found if f["result"] not in ("pass", "na")] == ["reach_redirect"]           # a redirect is a warning on its own; everything else passes


def test_a_record_without_platforms_is_a_google_page_and_the_catalog_is_split_between_the_two_platforms():
    assert cp.Ctx(page()).platforms == {"google"} and cp.Ctx(meta_page()).platforms == {"meta"}
    for cid in checks.GOOGLE_ONLY | checks.META_ONLY:
        assert cid in model.CATALOG
    assert checks.applies("reach_status", {"meta"}) and checks.applies("reach_status", {"google"})
    assert not checks.applies("trust_tracking", {"meta"}) and checks.applies("trust_tracking", {"google", "meta"})
    assert not checks.applies("trust_meta_pixel", {"google"}) and checks.applies("trust_meta_pixel", {"meta"})


# ── The click id ────────────────────────────────────────────────────────────

def test_meta_click_id_is_checked_only_after_a_redirect():
    assert one("reach_params_meta", meta_page())["result"] == "na"
    assert one("reach_params_meta", meta_page(fetch_over={"redirected": True, "keptIds": {"gclid": False, "fbclid": True, "utm": True}}))["result"] == "pass"


def test_a_redirect_that_drops_meta_click_id_or_the_utm_tag_fails_and_says_which():
    both = one("reach_params_meta", meta_page(fetch_over={"redirected": True, "keptIds": {"gclid": True, "fbclid": False, "utm": False}}))
    assert both["result"] == "fail" and "the click id (fbclid) and the UTM tag" in both["found"]
    only_id = one("reach_params_meta", meta_page(fetch_over={"redirected": True, "keptIds": {"gclid": True, "fbclid": False, "utm": True}}))
    assert only_id["result"] == "fail" and "lost the click id (fbclid) in" in only_id["found"] and "UTM" not in only_id["found"].split("lost")[1]
    only_utm = one("reach_params_meta", meta_page(fetch_over={"redirected": True, "keptIds": {"gclid": True, "fbclid": True, "utm": False}}))
    assert only_utm["result"] == "fail" and "lost the UTM tag in" in only_utm["found"]


def test_when_the_second_request_did_not_complete_meta_click_id_is_could_not_check():
    f = one("reach_params_meta", meta_page(fetch_over={"redirected": True, "keptIds": None}))
    assert f["result"] == "unknown" and "did not complete" in f["found"]
    assert one("reach_params_meta", meta_page(fetch_over={"status": 403, "blocked": True, "isHtml": False, "facts": None, "tech": None}))["result"] == "unknown"


# ── The pixel ───────────────────────────────────────────────────────────────

def test_the_pixel_is_seen_with_its_id():
    f = one("trust_meta_pixel", meta_page())
    assert f["result"] == "pass" and "Meta pixel (1234567890)" in f["found"]
    assert one("trust_meta_pixel", meta_page(fetch_over={"tech_over": {"tools": [{"name": "Meta Pixel", "category": "advertising"}]}}))["found"] == "The page loads the Meta pixel."


def test_no_pixel_is_a_warning_that_says_what_a_plain_read_cannot_see():
    f = one("trust_meta_pixel", meta_page(fetch_over={"tech_over": {"tools": []}}))
    assert f["result"] == "warn" and "not seen" in f["found"] and "Conversions API cannot be seen" in f["found"] and "Tag Manager" not in f["found"]
    gtm = one("trust_meta_pixel", meta_page(fetch_over={"tech_over": {"tools": [{"name": "Google Tag Manager", "category": "tag manager", "ids": []}]}}))
    assert "Google Tag Manager is on the page and may load the pixel" in gtm["found"]
    consent = one("trust_meta_pixel", meta_page(fetch_over={"tech_over": {"tools": []}, "facts_over": {"consent": True}}))
    assert "cookie banner" in consent["found"]


def test_a_page_cut_at_the_reading_limit_may_have_the_pixel_further_down():
    f = one("trust_meta_pixel", meta_page(fetch_over={"tech_over": {"tools": []}, "truncated": True, "bytes": 800_000}))
    assert f["result"] == "unknown" and "may be further down" in f["found"]


def test_an_unreadable_page_leaves_the_pixel_could_not_check():
    assert one("trust_meta_pixel", meta_page(fetch_over=blocked_fetch()))["result"] == "unknown"


# ── Meta's own ratings ──────────────────────────────────────────────────────

def ranked(*ads):
    return meta_page(groups=[meta_group(ads=list(ads))])


def test_ads_rated_average_or_better_pass_and_say_how_many_were_rated():
    f = one("match_meta_conversion", ranked(meta_ad("A"), meta_ad("B", ranks={"conversion": "average"})))
    assert f["result"] == "pass" and "all 2 ads rated" in f["found"] and "conversion rate" in f["found"]


def test_a_third_of_the_rated_spend_below_average_fails_a_tenth_warns_and_less_is_noise():
    below = {"conversion": "below", "quality": "below"}
    fail = one("match_meta_conversion", ranked(meta_ad("Good", cost=60.0), meta_ad("Bad", cost=40.0, ranks=below)))
    assert fail["result"] == "fail" and "40% of the rated ad spend (1 of 2 rated)" in fail["found"] and fail["items"] == ["Bad"]
    warn = one("match_meta_conversion", ranked(meta_ad("Good", cost=85.0), meta_ad("Bad", cost=15.0, ranks=below)))
    assert warn["result"] == "warn"
    noise = one("match_meta_conversion", ranked(meta_ad("Good", cost=95.0), meta_ad("Bad", cost=5.0, ranks=below)))
    assert noise["result"] == "pass" and noise["items"] == ["Bad"] and "5%" in noise["found"]
    q = one("match_meta_quality", ranked(meta_ad("Good", cost=60.0), meta_ad("Bad", cost=40.0, ranks=below)))
    assert q["result"] == "fail" and "ad quality" in q["found"]


def test_an_ad_nobody_rated_does_not_count_for_or_against_the_page():
    f = one("match_meta_conversion", ranked(meta_ad("Good", cost=50.0), meta_ad("New", cost=500.0, ranks={"quality": None, "conversion": None})))
    assert f["result"] == "pass" and "all 1 ad rated" in f["found"]


def test_no_rating_at_all_is_could_not_check_with_the_reason_meta_gives():
    f = one("match_meta_conversion", ranked(meta_ad("New", ranks={"quality": None, "conversion": None})))
    assert f["result"] == "unknown" and "only ads with enough impressions" in f["found"]
    assert one("match_meta_conversion", ranked(meta_ad("Old", ranks={})))["result"] == "unknown"


def test_when_meta_ratings_could_not_be_read_it_is_not_said_that_no_ad_was_rated():
    p = meta_page(ok={"pages": True, "ads": True, "meta:ads": True, "meta:rankings": False})
    f = one("match_meta_conversion", p)
    assert f["result"] == "unknown" and "ratings of the ads could not be read" in f["found"]


def test_no_meta_ad_to_rate_is_not_applicable_or_unread_when_the_ads_could_not_be_read():
    p = meta_page(groups=[])
    assert one("match_meta_conversion", p)["result"] == "na"
    unread = meta_page(groups=[], ok={"pages": True, "ads": True, "meta:ads": False})
    f = one("match_meta_conversion", unread)
    assert f["result"] == "unknown" and "Meta ads could not be read" in f["found"]


def test_only_the_ads_that_reach_this_page_are_rated():
    elsewhere = meta_ad("Elsewhere", urls=("https://acme-dental.com/other",), ranks={"conversion": "below"})
    f = one("match_meta_conversion", ranked(meta_ad("Here"), elsewhere))
    assert f["result"] == "pass" and "1 ad rated" in f["found"]


# ── The ads' words ──────────────────────────────────────────────────────────

def test_the_headline_and_the_offers_of_a_meta_ad_are_held_against_the_page():
    f = one("match_headline", meta_page())
    assert f["result"] == "pass" and "Ad groups and ad sets that took 100% of the spend" in f["found"] and "(1 judged)" in f["found"]
    miss = meta_page(groups=[meta_group(ads=[meta_ad(headlines=["Orthodontics Cambridge Braces"])])])
    f = one("match_headline", miss)
    assert f["result"] == "fail" and f["items"] and f["items"][0].startswith("Set 1:")
    offers = meta_page(groups=[meta_group(ads=[meta_ad(descriptions=["Cleanings only $19. 90% off whitening"])])])
    f = one("match_offers", offers)
    assert f["result"] == "fail" and "$19" in f["found"]


def test_a_meta_ad_with_only_primary_text_has_no_headline_to_echo_but_its_offers_are_still_checked():
    only_text = meta_page(groups=[meta_group(ads=[meta_ad(headlines=[], descriptions=["Free consultation this week"])])])
    f = one("match_headline", only_text)
    assert f["result"] == "na" and "ad groups and ad sets have no ad headlines to compare" in f["found"]
    assert one("match_offers", only_text)["result"] == "pass"


def test_a_google_only_page_keeps_its_old_words():
    f = one("match_headline", page(groups=[group(headlines=[])]))
    assert f["result"] == "na" and "responsive search ad headlines" in f["found"]
    assert "Meta" not in one("match_headline", page(groups=[]))["found"]


def test_a_page_no_ad_reaches_says_so_in_both_platforms_words():
    f = one("match_offers", meta_page(groups=[]))
    assert f["result"] == "na" and "No Search ad group or Meta ad set with words to compare" in f["found"]
    unread = one("match_offers", meta_page(groups=[], ok={"pages": True, "ads": True, "meta:ads": False}))
    assert unread["result"] == "unknown" and "Meta ads could not be read" in unread["found"]
    both_unread = one("match_offers", meta_page(groups=[], platforms=["google", "meta"], ok={"pages": True, "ads": False, "meta:ads": True}))
    assert both_unread["result"] == "unknown" and "keywords send people" in both_unread["found"]


def test_a_meta_ad_set_does_not_count_as_a_search_ad_group_and_both_kinds_are_judged_together():
    mixed = meta_page(platforms=["google", "meta"], groups=[group(cost=100.0), meta_group(ads=[meta_ad(headlines=["Orthodontics Cambridge Braces"], cost=100.0)])])
    assert len(cm.search_groups(cp.Ctx(mixed))) == 1 and len(cm.meta_groups(cp.Ctx(mixed))) == 1 and len(cm.ad_groups(cp.Ctx(mixed))) == 2
    f = one("match_headline", mixed)
    assert f["result"] == "warn" and "50%" in f["found"] and "(2 judged)" in f["found"]


def test_the_placeholder_message_names_the_platform_when_the_page_is_not_only_googles():
    tpl = {"templated": True, "status": 0}
    assert "that Google fills in" in cp.Ctx(page(fetch_over=tpl)).why_unread()
    assert "that the ad platform fills in" in cp.Ctx(meta_page(fetch_over=tpl)).why_unread()


def test_no_new_text_has_an_em_or_en_dash():
    for cid in checks.META_ONLY:
        for key in ("title", "why", "fix"):
            assert "—" not in model.CATALOG[cid][key] and "–" not in model.CATALOG[cid][key]
    for f in checks.run(meta_page(platforms=["google", "meta"], fetch_over={"redirected": True, "keptIds": {"gclid": True, "fbclid": False, "utm": False}})):
        assert "—" not in f["found"] and "–" not in f["found"]


def test_a_meta_ad_set_whose_ads_go_to_another_page_is_not_held_against_this_one():
    elsewhere = meta_group(ads=[meta_ad(urls=("https://acme-dental.com/other",), headlines=["Orthodontics Cambridge Braces"])])
    p = meta_page(groups=[elsewhere])
    assert one("match_headline", p)["result"] == "na" and one("match_offers", p)["result"] == "na" and cm.meta_groups(cp.Ctx(p)) == []


def test_a_meta_ad_set_whose_ads_say_nothing_is_not_held_against_the_page():
    mute = meta_group(ads=[meta_ad(headlines=[], descriptions=[])])
    p = meta_page(groups=[mute])
    assert one("match_headline", p)["result"] == "na" and one("match_offers", p)["result"] == "na" and cm.ad_groups(cp.Ctx(p)) == []


def test_the_reason_the_ads_could_not_be_read_is_the_platform_s_own():
    google_flag = meta_page(groups=[], ok={"pages": True, "ads": False, "meta:ads": True})
    f = one("match_offers", google_flag)
    assert f["result"] == "na" and "No Search ad group or Meta ad set" in f["found"]                    # Meta-only: Google's flag is not Meta's
    meta_flag_on_google = page(groups=[], ok={"pages": True, "ads": True, "meta:ads": False})
    f = one("match_offers", meta_flag_on_google)
    assert f["result"] == "na" and "No Search ad group sends people" in f["found"]                        # Google-only: Meta's flag is not Google's
