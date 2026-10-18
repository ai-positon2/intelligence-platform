"""Landing Page & Relevancy Check: the checks about the page itself (tracker/paid_lp_checks_page.py)."""
import pytest

from tests.lp_fakes import blocked_fetch, fetch, group, page
from tracker import paid_lp_checks as all_checks
from tracker import paid_lp_checks_page as c
from tracker import paid_lp_model as m

GOOGLE_PAGE = [(i, fn) for i, fn in c.CHECKS if i not in all_checks.META_ONLY]            # the checks a page that only Google's ads reach is held to


def run(check_id, **kw):
    fn = dict(c.CHECKS)[check_id]
    return fn(c.Ctx(page(**kw)))


def result(check_id, **kw):
    return run(check_id, **kw)["result"]


def spoiled(check_id, facts_over=None, tech_over=None, **fetch_over):
    return run(check_id, fetch_over=dict(fetch_over, facts_over=facts_over, tech_over=tech_over))


# ── The catalog and the list agree ──────────────────────────────────────────

def test_every_check_in_the_list_has_a_catalog_entry_and_every_entry_of_these_sections_is_checked():
    ids = [i for i, _f in c.CHECKS]
    assert len(ids) == len(set(ids))
    assert set(ids) <= set(m.CATALOG)
    mine = {i for i, e in m.CATALOG.items() if e["section"] in ("reach", "path", "mobile", "speed", "trust")}
    assert mine == set(ids)


def test_a_good_page_passes_everything_it_can_be_asked():
    ctx = c.Ctx(page())
    got = {i: fn(ctx)["result"] for i, fn in GOOGLE_PAGE}
    assert {r for r in got.values()} <= {"pass", "na"}, {i: r for i, r in got.items() if r not in ("pass", "na")}
    assert got["speed_lab"] == "na" and got["reach_params"] == "na"


def test_every_finding_is_made_from_its_own_catalog_entry():
    ctx = c.Ctx(page())
    for cid, fn in c.CHECKS:
        assert fn(ctx)["id"] == cid


# ── The page could not be read ──────────────────────────────────────────────

PAGE_SIDE = [i for i, _f in c.CHECKS if i not in ("reach_status", "reach_staging", "reach_adsbot", "mobile_conv_gap", "speed_google", "speed_lab")]


@pytest.mark.parametrize("cid", PAGE_SIDE)
def test_a_page_that_refused_us_leaves_every_check_that_needs_its_html_as_could_not_check(cid):
    f = run(cid, fetch_over=None) if False else dict(c.CHECKS)[cid](c.Ctx(page(fetch=blocked_fetch())))
    assert f["result"] == "unknown" and "refuses automated requests" in f["found"] and "HTTP 403" in f["found"]


@pytest.mark.parametrize("cid", PAGE_SIDE)
def test_a_page_that_was_never_fetched_is_could_not_check_too(cid):
    f = dict(c.CHECKS)[cid](c.Ctx(page(fetch=None)))
    assert f["result"] == "unknown" and "not read in time" in f["found"]


@pytest.mark.parametrize("cid", [i for i in PAGE_SIDE if i in ("path_action", "path_cta_top", "path_form_length", "path_focus", "mobile_tap_to_call", "trust_privacy", "trust_contact", "trust_proof")])
def test_a_page_built_by_scripts_cannot_be_judged_on_its_forms_buttons_and_words(cid):
    f = spoiled(cid, facts_over={"scriptBuilt": True, "wordCount": 5, "forms": [], "ctas": [], "tel": [], "phones": []})
    assert f["result"] == "unknown" and "built by scripts" in f["found"]


def test_every_page_side_check_names_a_missing_page_as_could_not_check_never_as_failed():
    for cid in PAGE_SIDE:
        f = dict(c.CHECKS)[cid](c.Ctx(page(fetch=fetch(status=404, isHtml=False, facts=None, tech=None))))
        assert f["result"] == "unknown", cid


# ── The page opens ──────────────────────────────────────────────────────────

def test_status_passes_on_200_and_fails_on_an_error_page_and_holds_the_grade():
    assert result("reach_status") == "pass"
    f = spoiled("reach_status", status=404, isHtml=False, facts=None, tech=None)
    assert f["result"] == "fail" and f["severity"] == "critical" and "HTTP 404" in f["found"] and f["caps"]
    assert spoiled("reach_status", status=500, isHtml=False, facts=None, tech=None)["result"] == "fail"


def test_status_is_could_not_check_for_a_blocked_page_and_one_not_read():
    f = spoiled("reach_status", status=403, blocked=True, isHtml=False, facts=None, tech=None)
    assert f["result"] == "unknown" and "not called broken" in f["found"]
    assert dict(c.CHECKS)["reach_status"](c.Ctx(page(fetch=None)))["result"] == "unknown"
    assert dict(c.CHECKS)["reach_status"](c.Ctx(page(fetch={"url": "u", "skipped": True, "error": "not read in time", "status": 0})))["result"] == "unknown"


def test_status_for_a_page_that_never_answered():
    gone = spoiled("reach_status", status=0, error="the address does not resolve", isHtml=False, facts=None, tech=None)
    assert gone["result"] == "fail" and gone["caps"]
    slow = spoiled("reach_status", status=0, error="timed out", isHtml=False, facts=None, tech=None)
    assert slow["result"] == "warn" and slow["severity"] == "high" and "8 seconds" in slow["found"]
    other = spoiled("reach_status", status=0, error="SSLError", isHtml=False, facts=None, tech=None)
    assert other["result"] == "unknown" and "SSLError" in other["found"]


def test_a_redirect_is_a_warning_and_a_deep_link_sent_home_is_a_high_failure():
    assert result("reach_redirect") == "pass"
    r = spoiled("reach_redirect", redirected=True, finalUrl="https://acme-dental.com/offers")
    assert r["result"] == "warn" and "https://acme-dental.com/offers" in r["found"]
    h = spoiled("reach_redirect", redirected=True, collapsed=True, finalUrl="https://acme-dental.com/")
    assert h["result"] == "fail" and h["severity"] == "high" and "home page" in h["found"]


def test_the_click_id_check_applies_only_after_a_redirect():
    assert result("reach_params") == "na"
    assert spoiled("reach_params", redirected=True, paramsSurvive=True)["result"] == "pass"
    lost = spoiled("reach_params", redirected=True, paramsSurvive=False)
    assert lost["result"] == "fail" and "gclid" in lost["found"]
    assert spoiled("reach_params", redirected=True, paramsSurvive=None)["result"] == "unknown"


def test_https():
    assert result("reach_https") == "pass"
    f = spoiled("reach_https", tech_over={"https": False}, finalUrl="http://acme-dental.com/lp")
    assert f["result"] == "fail" and "http://acme-dental.com/lp" in f["found"]


def test_a_staging_address_fails_even_when_the_page_could_not_be_read():
    assert result("reach_staging") == "pass"
    f = run("reach_staging", url="https://staging.acme-dental.com/lp")
    assert f["result"] == "fail" and f["severity"] == "critical" and "staging.acme-dental.com" in f["found"] and f["caps"]
    g = dict(c.CHECKS)["reach_staging"](c.Ctx(page(fetch=blocked_fetch(), url="https://dev.acme-dental.com/x")))
    assert g["result"] == "fail"
    final = run("reach_staging", fetch_over={"finalUrl": "https://qa.acme-dental.com/lp"})
    assert final["result"] == "fail"


def test_adsbot():
    assert result("reach_adsbot") == "pass"
    f = spoiled("reach_adsbot", adsbot=False)
    assert f["result"] == "fail" and f["severity"] == "critical" and f["caps"]
    assert spoiled("reach_adsbot", adsbot=None)["result"] == "unknown"
    assert dict(c.CHECKS)["reach_adsbot"](c.Ctx(page(fetch=None)))["result"] == "unknown"


# ── Can a visitor act ───────────────────────────────────────────────────────

def test_a_page_with_a_form_phone_link_booking_embed_chat_or_buy_button_gives_a_way_to_act():
    base = {"forms": [], "tel": [], "embeds": [], "booking": False, "commerce": False, "ctas": [], "phones": [], "mailto": [], "emails": []}
    for over, said in (({"forms": c_form(3)}, "a form"), ({"tel": ["+1617"]}, "a phone link"), ({"embeds": ["forms.hubspot.com"]}, "embedded"), ({"booking": True}, "booking"),
                       ({"commerce": True}, "buy button")):
        f = spoiled("path_action", facts_over=dict(base, **over))
        assert f["result"] == "pass" and said in f["found"], said
    chat = spoiled("path_action", facts_over=dict(base), tech_over={"tools": [{"name": "Live chat", "category": "chat", "ids": []}]})
    assert chat["result"] == "pass" and "live chat" in chat["found"]


def c_form(n, search=False):
    return [{"fields": [], "visible": n, "required": 1, "checks": 0, "search": search, "action": "", "method": "post", "phone": False, "email": False}]


def test_a_search_box_is_not_a_way_to_act_and_a_form_with_no_fields_is_not_either():
    base = {"tel": [], "embeds": [], "booking": False, "commerce": False, "ctas": [], "phones": [], "mailto": [], "emails": []}
    assert spoiled("path_action", facts_over=dict(base, forms=c_form(1, search=True)))["result"] == "fail"
    assert spoiled("path_action", facts_over=dict(base, forms=c_form(0)))["result"] == "fail"


def test_only_a_phone_number_as_text_an_email_or_a_button_is_a_high_severity_warning():
    base = {"forms": [], "tel": [], "embeds": [], "booking": False, "commerce": False, "ctas": [], "phones": [], "mailto": [], "emails": []}
    f = spoiled("path_action", facts_over=dict(base, phones=["(617) 555-0123"], emails=["a@b.test"]))
    assert f["result"] == "warn" and f["severity"] == "high" and "phone number shown as text" in f["found"] and "email" in f["found"]
    g = spoiled("path_action", facts_over=dict(base, ctas=[{"text": "Get a quote", "kind": "strong", "at": 10, "tag": "a"}]))
    assert g["result"] == "warn" and "1 button or link that asks" in g["found"]


def test_a_page_with_no_way_to_act_at_all_fails_and_holds_the_grade():
    base = {"forms": [], "tel": [], "embeds": [], "booking": False, "commerce": False, "ctas": [], "phones": [], "mailto": [], "emails": []}
    f = spoiled("path_action", facts_over=base)
    assert f["result"] == "fail" and f["severity"] == "critical" and f["caps"]


def test_a_script_loaded_form_tool_turns_that_failure_into_a_warning_that_says_why():
    base = {"forms": [], "tel": [], "embeds": [], "booking": False, "commerce": False, "ctas": [], "phones": [], "mailto": [], "emails": []}
    f = spoiled("path_action", facts_over=base, tech_over={"tools": [{"name": "HubSpot", "category": "CRM and forms", "ids": []}]})
    assert f["result"] == "warn" and "HubSpot" in f["found"] and "added by script" in f["found"]


def test_a_call_to_action_in_the_first_screen_passes_and_a_late_one_warns():
    assert result("path_cta_top") == "pass"
    late = spoiled("path_cta_top", facts_over={"ctas": [{"text": "Book now", "kind": "strong", "at": 4000, "tag": "a"}]})
    assert late["result"] == "warn" and "Book now" in late["found"] and "below the first screen" in late["found"]
    weak_only = spoiled("path_cta_top", facts_over={"ctas": [{"text": "Learn more", "kind": "weak", "at": 10, "tag": "a"}]})
    assert weak_only["result"] == "warn"                                  # a form exists, so the page can still be acted on
    none = spoiled("path_cta_top", facts_over={"ctas": [], "forms": [], "embeds": []})
    assert none["result"] == "fail" and none["severity"] == "high"


def test_the_first_call_to_action_is_the_one_nearest_the_top_whatever_the_order_read():
    f = spoiled("path_cta_top", facts_over={"ctas": [{"text": "Late", "kind": "strong", "at": 9000, "tag": "a"}, {"text": "Early", "kind": "strong", "at": 100, "tag": "a"}]})
    assert f["result"] == "pass" and "Early" in f["found"]


@pytest.mark.parametrize("fields,res", [(1, "pass"), (5, "pass"), (6, "warn"), (8, "warn"), (9, "fail"), (20, "fail")])
def test_the_form_is_judged_by_its_visible_fields(fields, res):
    forms = [{"fields": [{"kind": "text", "name": "f%d" % i, "required": i < 3, "label": ""} for i in range(fields)], "visible": fields, "required": min(3, fields), "checks": 0,
              "search": False, "action": "", "method": "post", "phone": False, "email": False}]
    f = spoiled("path_form_length", facts_over={"forms": forms})
    assert f["result"] == res and str(fields) in f["found"] or res == "pass"


def test_the_longest_real_form_is_the_one_judged_and_a_search_box_is_ignored():
    forms = c_form(2) + c_form(12) + c_form(30, search=True)
    f = spoiled("path_form_length", facts_over={"forms": forms})
    assert f["result"] == "fail" and "12 fields" in f["found"]


def test_no_form_is_not_applicable_and_an_embedded_one_cannot_be_looked_inside():
    assert spoiled("path_form_length", facts_over={"forms": [], "embeds": []})["result"] == "na"
    f = spoiled("path_form_length", facts_over={"forms": [], "embeds": ["forms.hubspot.com"]})
    assert f["result"] == "unknown" and "forms.hubspot.com" in f["found"]


@pytest.mark.parametrize("total,res", [(10, "pass"), (40, "pass"), (41, "warn"), (150, "warn"), (151, "fail")])
def test_a_page_full_of_links_is_a_low_severity_finding(total, res):
    f = spoiled("path_focus", facts_over={"links": {"total": total, "nav": 3, "outbound": 0}})
    assert f["result"] == res and f["severity"] == "low"


# ── Works on a phone ────────────────────────────────────────────────────────

def test_the_mobile_viewport():
    assert result("mobile_viewport") == "pass"
    none = spoiled("mobile_viewport", facts_over={"viewport": ""}, tech_over={"mobile": False})
    assert none["result"] == "fail" and "no viewport" in none["found"]
    fixed = spoiled("mobile_viewport", facts_over={"viewport": "width=1024"}, tech_over={"mobile": False})
    assert fixed["result"] == "fail" and "width=1024" in fixed["found"]


@pytest.mark.parametrize("vp,res", [("width=device-width, initial-scale=1", "pass"), ("width=device-width, user-scalable=no", "warn"), ("width=device-width,user-scalable=0", "warn"),
                                     ("width=device-width, maximum-scale=1", "warn"), ("width=device-width, maximum-scale=1.0", "warn"), ("width=device-width, maximum-scale=5", "pass"),
                                     ("width=device-width, maximum-scale=1.5", "pass")])
def test_blocking_pinch_zoom(vp, res):
    assert spoiled("mobile_zoom", facts_over={"viewport": vp})["result"] == res


def test_zoom_does_not_apply_without_a_viewport():
    assert spoiled("mobile_zoom", facts_over={"viewport": ""})["result"] == "na"


def test_a_phone_number_that_cannot_be_tapped_is_a_warning():
    assert result("mobile_tap_to_call") == "pass"
    f = spoiled("mobile_tap_to_call", facts_over={"tel": [], "phones": ["(617) 555-0123"]})
    assert f["result"] == "warn" and "(617) 555-0123" in f["found"] and f["items"] == ["(617) 555-0123"]
    many = spoiled("mobile_tap_to_call", facts_over={"tel": [], "phones": ["1", "2", "3"]})
    assert "3 phone numbers" in many["found"]
    assert spoiled("mobile_tap_to_call", facts_over={"tel": [], "phones": []})["result"] == "na"


def devices(m_clicks, m_conv, d_clicks, d_conv, tablet=0):
    d = {"mobile": {"clicks": m_clicks, "cost": 1.0, "conversions": m_conv}, "desktop": {"clicks": d_clicks, "cost": 1.0, "conversions": d_conv}}
    if tablet:
        d["tablet"] = {"clicks": tablet, "cost": 1.0, "conversions": 0.0}
    return d


@pytest.mark.parametrize("m_conv,res", [(30, "pass"), (21, "pass"), (20, "warn"), (12, "warn"), (11, "fail"), (0, "fail")])
def test_phone_against_desktop_conversion_rate(m_conv, res):
    f = run("mobile_conv_gap", devices=devices(300, m_conv, 300, 30))
    assert f["result"] == res
    assert "Phones convert at" in f["found"]


def test_the_conversion_gap_is_not_judged_on_too_few_clicks_or_too_few_phones_or_no_desktop_sales():
    assert run("mobile_conv_gap", devices=devices(100, 1, 300, 30))["result"] == "na"
    assert run("mobile_conv_gap", devices=devices(300, 1, 100, 30))["result"] == "na"
    assert run("mobile_conv_gap", devices=devices(200, 1, 1000, 30))["result"] == "na"          # phones are under a quarter of the clicks
    f = run("mobile_conv_gap", devices=devices(300, 5, 300, 0))
    assert f["result"] == "na" and "Desktop visitors did not convert" in f["found"]
    assert run("mobile_conv_gap", devices={})["result"] == "na"


def test_the_conversion_gap_counts_tablets_in_the_mobile_share_denominator():
    assert run("mobile_conv_gap", devices=devices(300, 1, 300, 30, tablet=100000))["result"] == "na"


# ── Loads fast ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,res", [(10, "pass"), (7, "pass"), (6, "warn"), (5, "warn"), (4, "fail"), (1, "fail")])
def test_googles_speed_score(score, res):
    assert result("speed_google", speed=score) == res


def test_no_speed_score_from_google_is_could_not_check():
    f = run("speed_google", speed=None)
    assert f["result"] == "unknown" and "enough mobile traffic" in f["found"]


@pytest.mark.parametrize("ms,res", [(100, "pass"), (1500, "pass"), (1501, "warn"), (3000, "warn"), (3001, "fail")])
def test_server_response_time(ms, res):
    assert spoiled("speed_server", responseMs=ms)["result"] == res


def test_the_server_time_says_it_was_measured_from_our_servers():
    assert "measured from our servers" in spoiled("speed_server", responseMs=3500)["found"] and "3.5 seconds" in spoiled("speed_server", responseMs=3500)["found"]
    assert "connecting and any redirect" in spoiled("speed_server", responseMs=100)["found"]


@pytest.mark.parametrize("kb,res", [(50, "pass"), (300, "pass"), (301, "warn"), (600, "warn"), (601, "fail")])
def test_html_weight(kb, res):
    assert spoiled("speed_html", bytes=kb * 1024)["result"] == res


def test_a_page_cut_off_at_the_reading_limit_is_heavy():
    f = spoiled("speed_html", bytes=800_000, truncated=True)
    assert f["result"] == "fail" and "more than" in f["found"]


def test_compression():
    for enc in ("gzip", "br", "deflate", "zstd", "GZIP"):
        assert spoiled("speed_compression", encoding=enc)["result"] == "pass"
    f = spoiled("speed_compression", encoding="", bytes=200_000)
    assert f["result"] == "fail" and "without compression" in f["found"]
    assert spoiled("speed_compression", encoding="", bytes=1000)["result"] == "na"


@pytest.mark.parametrize("n,res", [(0, "pass"), (2, "pass"), (3, "warn"), (5, "warn"), (6, "fail")])
def test_scripts_that_block_the_first_paint(n, res):
    f = spoiled("speed_blocking", facts_over={"head": {"blockingScripts": n, "stylesheets": 3, "scripts": 10, "thirdParty": []}})
    assert f["result"] == res


def test_the_blocking_sentence_agrees_with_the_count():
    one = spoiled("speed_blocking", facts_over={"head": {"blockingScripts": 1, "stylesheets": 1, "scripts": 3, "thirdParty": []}})
    assert one["found"] == "1 script in the head holds up the first paint, and 1 stylesheet loads there."
    many = spoiled("speed_blocking", facts_over={"head": {"blockingScripts": 4, "stylesheets": 2, "scripts": 3, "thirdParty": []}})
    assert many["found"] == "4 scripts in the head hold up the first paint, and 2 stylesheets load there."


@pytest.mark.parametrize("n,res", [(5, "pass"), (20, "pass"), (21, "warn"), (40, "warn"), (41, "fail")])
def test_script_count(n, res):
    f = spoiled("speed_scripts", facts_over={"head": {"blockingScripts": 0, "stylesheets": 1, "scripts": n, "thirdParty": ["a.test", "b.test"]}})
    assert f["result"] == res and "2 from other sites" in f["found"]
    if res != "pass":
        assert f["items"] == ["a.test", "b.test"]


def test_images_without_a_size_or_never_lazy_are_a_low_warning_and_no_images_is_not_applicable():
    assert spoiled("speed_images", facts_over={"images": {"count": 0, "noSize": 0, "lazy": 0, "noAlt": 0}})["result"] == "na"
    nosize = spoiled("speed_images", facts_over={"images": {"count": 10, "noSize": 8, "lazy": 5, "noAlt": 0}})
    assert nosize["result"] == "warn" and nosize["found"].startswith("8 of 10 images have no width and height") and nosize["severity"] == "low"
    few = spoiled("speed_images", facts_over={"images": {"count": 10, "noSize": 4, "lazy": 0, "noAlt": 0}})
    assert few["result"] == "pass"                                       # four without a size is under the line
    lots = spoiled("speed_images", facts_over={"images": {"count": 60, "noSize": 0, "lazy": 10, "noAlt": 0}})
    assert lots["result"] == "warn" and "lazily" in lots["found"]
    both = spoiled("speed_images", facts_over={"images": {"count": 60, "noSize": 50, "lazy": 5, "noAlt": 0}})
    assert both["result"] == "warn" and ";" in both["found"]


def test_the_lab_test_is_not_applicable_until_it_has_been_run_and_then_judged():
    assert result("speed_lab") == "na"
    assert result("speed_lab", lab={"score": None}) == "na"
    for score, res in ((95, "pass"), (90, "pass"), (89, "warn"), (50, "warn"), (49, "fail")):
        f = run("speed_lab", lab={"score": score, "lcpMs": 2400, "tbtMs": 300, "cls": 0.123})
        assert f["result"] == res and "performance score %d out of 100" % score in f["found"] and "2.4 seconds" in f["found"] and "0.12" in f["found"]


# ── Tracking and trust ──────────────────────────────────────────────────────

def test_a_google_tag_is_found_and_a_missing_one_is_a_warning_that_says_what_a_plain_read_cannot_see():
    assert result("trust_tracking") == "pass"
    for tool in ("Google Analytics 4", "Google Ads tag"):
        assert spoiled("trust_tracking", tech_over={"tools": [{"name": tool, "category": "x", "ids": []}]})["result"] == "pass"
    none = spoiled("trust_tracking", tech_over={"tools": [{"name": "Hotjar", "category": "behaviour", "ids": []}]})
    assert none["result"] == "warn" and "not seen by a plain read" in none["found"]
    banner = spoiled("trust_tracking", tech_over={"tools": []}, facts_over={"consent": True})
    assert "cookie banner" in banner["found"]


def test_a_privacy_link_is_expected_most_where_the_page_collects_details():
    assert result("trust_privacy") == "pass"
    form = spoiled("trust_privacy", facts_over={"privacyLink": False})
    assert form["result"] == "fail" and form["severity"] == "medium"
    plain = spoiled("trust_privacy", facts_over={"privacyLink": False, "forms": [], "embeds": []})
    assert plain["result"] == "warn" and plain["severity"] == "low"
    embed = spoiled("trust_privacy", facts_over={"privacyLink": False, "forms": [], "embeds": ["forms.hubspot.com"]})
    assert embed["result"] == "fail"


def test_contact_details():
    assert result("trust_contact") == "pass"
    only_address = spoiled("trust_contact", facts_over={"tel": [], "phones": [], "emails": [], "mailto": [], "address": True})
    assert only_address["result"] == "pass" and only_address["found"] == "The page shows an address."
    none = spoiled("trust_contact", facts_over={"tel": [], "phones": [], "emails": [], "mailto": [], "address": False})
    assert none["result"] == "fail"


def test_proof():
    f = spoiled("trust_proof")
    assert f["result"] == "pass" and "reviews, star rating" in f["found"]
    assert spoiled("trust_proof", facts_over={"proof": []})["result"] == "warn"


# ── Run together ────────────────────────────────────────────────────────────

def test_a_spoiled_page_scores_well_below_a_good_one():
    ctx = c.Ctx(page())
    good = m.page_summary([fn(ctx) for _i, fn in GOOGLE_PAGE])
    bad_fetch = fetch(facts_over={"forms": [], "tel": [], "ctas": [], "phones": [], "viewport": "", "privacyLink": False, "proof": [], "links": {"total": 300, "nav": 200, "outbound": 3}},
                      tech_over={"mobile": False, "tools": []}, encoding="", responseMs=4000, adsbot=False)
    ctx2 = c.Ctx(page(fetch=bad_fetch, speed=2))
    bad = m.page_summary([fn(ctx2) for _i, fn in GOOGLE_PAGE])
    assert good["score"] == 100 and good["grade"] == "A"
    assert bad["score"] < 60 and bad["grade"] in ("D", "F") and "Google's ad crawler is blocked from the page" in bad["capReasons"]


# ── A page cut at the reading limit ─────────────────────────────────────────

CUT = ("path_action", "path_cta_top", "mobile_tap_to_call", "trust_tracking", "trust_privacy", "trust_contact", "trust_proof")


def cut_page_facts(cid):
    none = {"forms": [], "tel": [], "embeds": [], "booking": False, "commerce": False, "ctas": [], "phones": [], "mailto": [], "emails": [], "privacyLink": False, "address": False, "proof": []}
    if cid == "mobile_tap_to_call":
        none["phones"] = ["(617) 555-0123"]
    return none


@pytest.mark.parametrize("cid", CUT)
def test_what_is_not_found_on_a_page_cut_at_the_reading_limit_is_could_not_check_not_a_failure(cid):
    f = spoiled(cid, facts_over=cut_page_facts(cid), tech_over={"tools": []}, truncated=True, bytes=800_000)
    assert f["result"] == "unknown" and "may be further down" in f["found"] and "800 KB" in f["found"]


@pytest.mark.parametrize("cid", CUT)
def test_the_same_page_read_in_full_does_fail_or_warn(cid):
    f = spoiled(cid, facts_over=cut_page_facts(cid), tech_over={"tools": []})
    assert f["result"] in ("fail", "warn")


@pytest.mark.parametrize("cid", ("path_action", "trust_privacy", "trust_contact"))
def test_what_is_found_on_a_cut_page_still_passes(cid):
    assert spoiled(cid, truncated=True, bytes=800_000)["result"] == "pass"
