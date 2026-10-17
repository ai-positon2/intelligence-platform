"""Landing Page & Relevancy Check: the exact edges a mutation round showed were loose (a threshold moved by one, a branch joined with and instead of or).

Each test names the number or the rule it holds: where a constant is written in the module and used by its tests, the literal is held here too.
"""
import datetime as dt

import pytest

from tests.gads_fakes import FakeStore
from tests.lp_fakes import fetch, group, keyword, page, term
from tests.test_paid_lp import ACCOUNT, ACCOUNT_ID, Fetcher, GOOD_HTML, NOW, fresh, fresh_caches, run, search  # noqa: F401
from tests.test_paid_lp_page import Resp, html_bytes, parsed
from tests.test_paid_lp_read import ad_row, kw_row, page_row, term_row
from tracker import paid_lp as lp
from tracker import paid_lp_ai as ai
from tracker import paid_lp_checks_match as cm
from tracker import paid_lp_checks_page as cp
from tracker import paid_lp_match as m
from tracker import paid_lp_model as model
from tracker import paid_lp_page as p
from tracker import paid_lp_read as rd
from tracker import paid_lp_speed as sp


def check(cid, **kw):
    fn = dict(cp.CHECKS + cm.CHECKS)[cid]
    return fn(cp.Ctx(page(**kw)))


def spoiled(cid, facts_over=None, tech_over=None, **fetch_over):
    return check(cid, fetch_over=dict(fetch_over, facts_over=facts_over, tech_over=tech_over))


# ── Words: where a stem begins and ends ─────────────────────────────────────

@pytest.mark.parametrize("word, stem", [("ties", "tie"), ("cities", "city"), ("cries", "cry"), ("asses", "ass"), ("glasses", "glass"), ("yes", "yes"), ("cars", "car"), ("doing", "doing"),
                                        ("eating", "eat"), ("heating", "heat"), ("need", "need"), ("fixed", "fix"), ("licensed", "licens"), ("time", "time"), ("house", "hous"), ("dental", "dental")])
def test_stems_begin_to_cut_at_exactly_these_lengths(word, stem):
    assert m.stem(word) == stem


def test_the_literals_the_matching_is_built_on():
    assert m.MIN_WORDS == 120 and m.too_thin({"wordCount": 119}) and not m.too_thin({"wordCount": 120})
    assert m.MAX_PHRASE_WORDS == 12 and len(m.content(" ".join("w%d" % i for i in range(30)))) == 12
    assert m.PARTIAL == 0.5 and m.HEADLINE_ECHO == 0.6


def test_a_long_keyword_is_partly_on_the_page_only_when_half_its_words_are():
    prom, full = m.page_vocabs({"title": "", "h1": [], "text": "alpha beta"})
    assert m.hit("alpha gamma delta epsilon", prom, full) == "none"                # one of four is not half
    assert m.hit("alpha beta gamma delta", prom, full) == "partial"                # two of four is
    assert m.hit("alpha gamma", prom, full) == "partial" and m.hit("alpha", prom, full) == "page"


def test_a_one_word_headline_is_used_only_when_no_longer_headline_exists_and_two_words_count_as_longer():
    prom = m.vocab("boston")
    assert m.best_echo(["Boston", "Emergency Dentist"], prom) == (0.0, "Emergency Dentist")
    assert m.best_echo(["Boston"], prom) == (1.0, "Boston")


# ── The model ───────────────────────────────────────────────────────────────

def test_the_account_score_with_no_spend_anywhere_is_the_plain_average():
    assert model.weighted_score([{"score": 90, "spend": 0}, {"score": 70, "spend": 0}]) == 80 and model.weighted_score([{"score": 90, "spend": 0.5}]) == 90


def test_a_check_names_eight_pages_and_a_page_without_a_name_is_named_by_its_address():
    assert model.FIX_PAGES == 8
    cat, secs = dict(model.CATALOG), dict(model.SECTIONS)
    try:
        model.section("s1", "First", 1)
        model.CATALOG.clear()
        model.register([{"id": "T-1", "section": "s1", "title": "t", "severity": "high", "why": "w", "fix": "f"}])
        rows = model.fix_first([{"url": "https://a.test/x", "spend": 0.1, "findings": [model.make("T-1", "fail", "x")]}, {"url": "https://a.test/y", "spend": 0.2, "findings": [model.make("T-1", "fail", "x")]}])
        assert [x["pageName"] for x in rows[0]["pages"]] == ["https://a.test/y", "https://a.test/x"] and rows[0]["pages"][0]["pageId"] == "https://a.test/y"
        assert rows[0]["spend"] == 0.3                                    # 0.1 + 0.2 is 0.30000000000000004 until it is rounded
    finally:
        model.CATALOG.clear()
        model.CATALOG.update(cat)
        model.SECTIONS.clear()
        model.SECTIONS.update(secs)


# ── Checks about the page: the edges ────────────────────────────────────────

def test_the_status_edges():
    assert spoiled("reach_status", status=400, isHtml=False, facts=None, tech=None)["result"] == "fail"
    assert spoiled("reach_status", status=399, isHtml=False, facts=None, tech=None)["result"] == "pass"
    assert spoiled("reach_status", status=200)["result"] == "pass" and spoiled("reach_status", status=199, isHtml=False, facts=None, tech=None)["result"] == "unknown"
    assert spoiled("reach_status", status=0, error="", isHtml=False, facts=None, tech=None)["found"].endswith("(no answer)")
    gone = check("reach_redirect", fetch_over={"status": 400, "isHtml": False, "facts": None, "tech": None})
    assert gone["result"] == "unknown" and "HTTP 400" in gone["found"]


def test_a_page_that_answered_but_has_no_facts_is_not_a_web_page():
    f = check("reach_https", fetch_over={"facts": None})
    assert f["result"] == "unknown" and "did not return a web page" in f["found"]


def test_time_reads_in_milliseconds_below_a_second_and_seconds_from_it():
    assert cp._ms(999) == "999 ms" and cp._ms(1000) == "1.0 seconds" and cp._ms(3500) == "3.5 seconds"


def test_the_staging_check_needs_no_fetch_and_the_adsbot_check_needs_an_answer():
    f = check("reach_staging", fetch=None, url="https://staging.acme-dental.com/lp")
    assert f["result"] == "fail"
    assert check("reach_staging", fetch=None)["result"] == "pass"
    assert spoiled("reach_adsbot", status=0, error="timed out", isHtml=False, facts=None, tech=None, adsbot=True)["result"] == "unknown"


def test_the_first_call_to_action_is_in_the_first_screen_at_exactly_the_limit_and_the_word_count_is_about_a_sixth_of_the_characters():
    at = cp.ATF_CHARS
    assert at == 1500
    ok = spoiled("path_cta_top", facts_over={"ctas": [{"text": "Book now", "kind": "strong", "at": at, "tag": "a"}]})
    late = spoiled("path_cta_top", facts_over={"ctas": [{"text": "Book now", "kind": "strong", "at": at + 1, "tag": "a"}]})
    assert ok["result"] == "pass" and late["result"] == "warn"
    assert "about 500 words" in spoiled("path_cta_top", facts_over={"ctas": [{"text": "Book now", "kind": "strong", "at": 3000, "tag": "a"}]})["found"]


def test_a_form_with_one_field_is_a_way_to_act_and_the_fields_are_named_by_label_then_name_then_kind():
    one = [{"fields": [{"kind": "email", "name": "", "required": True, "label": ""}], "visible": 1, "required": 1, "checks": 0, "search": False, "action": "", "method": "post", "phone": False, "email": True}]
    base = {"tel": [], "embeds": [], "booking": False, "commerce": False, "ctas": [], "phones": [], "mailto": [], "emails": [], "forms": one}
    assert spoiled("path_action", facts_over=base)["result"] == "pass"
    fields = [{"kind": "text", "name": "fullname", "required": True, "label": "Your name"}, {"kind": "tel", "name": "phone", "required": False, "label": ""}, {"kind": "email", "name": "", "required": False, "label": ""},
              {"kind": "checkbox", "name": "consent", "required": False, "label": ""}]
    form = [{"fields": fields, "visible": 3, "required": 1, "checks": 1, "search": False, "action": "", "method": "post", "phone": True, "email": True}]
    f = spoiled("path_form_length", facts_over={"forms": form})
    assert f["items"] == ["Your name", "phone", "email"]                      # the checkbox is not a field to fill in


@pytest.mark.parametrize("fields, res", [(5, "pass"), (6, "warn"), (8, "warn"), (9, "fail")])
def test_the_form_length_result_and_the_sentence_agree(fields, res):
    form = [{"fields": [{"kind": "text", "name": "f%d" % i, "required": False, "label": ""} for i in range(fields)], "visible": fields, "required": 0, "checks": 0, "search": False, "action": "",
             "method": "post", "phone": False, "email": False}]
    f = spoiled("path_form_length", facts_over={"forms": form})
    assert f["result"] == res and ("asks for %d field" % fields) in f["found"]


def devices(m_clicks, m_conv, d_clicks, d_conv):
    return {"mobile": {"clicks": m_clicks, "cost": 1.0, "conversions": m_conv}, "desktop": {"clicks": d_clicks, "cost": 1.0, "conversions": d_conv}}


def test_the_phone_against_desktop_edges():
    assert check("mobile_conv_gap", devices=devices(150, 6, 450, 18))["result"] == "pass"             # exactly 150 clicks each way and exactly a quarter of the clicks are phones
    assert check("mobile_conv_gap", devices=devices(149, 6, 450, 18))["result"] == "na"
    assert check("mobile_conv_gap", devices=devices(150, 6, 149, 18))["result"] == "na"
    assert check("mobile_conv_gap", devices=devices(149 + 1, 6, 451, 18))["result"] == "na"             # one click under a quarter
    f = check("mobile_conv_gap", devices=devices(400, 8, 400, 40))
    assert f["found"] == "Phones convert at 2.0% of clicks and desktops at 10.0% (20% of desktop's rate)." and f["result"] == "fail"
    assert check("mobile_conv_gap", devices={"mobile": {"clicks": 400, "cost": 1, "conversions": 8}})["result"] == "na"


def test_a_page_of_exactly_five_thousand_bytes_is_worth_compressing_and_one_byte_less_is_not():
    assert spoiled("speed_compression", encoding="", bytes=4999)["result"] == "na" and spoiled("speed_compression", encoding="", bytes=5000)["result"] == "fail"


def test_image_edges():
    def im(count, no_size, lazy):
        return spoiled("speed_images", facts_over={"images": {"count": count, "noSize": no_size, "lazy": lazy, "noAlt": 0}})
    assert im(10, 5, 5)["result"] == "warn" and im(10, 4, 5)["result"] == "pass"                      # five without a size and half of them
    assert im(100, 5, 50)["result"] == "pass"                                                         # five is under half of a hundred
    assert im(11, 5, 5)["result"] == "pass"                                                           # five of eleven is under half
    assert im(41, 0, 12)["result"] == "warn" and im(41, 0, 13)["result"] == "pass" and im(40, 0, 0)["result"] == "pass"


def test_contact_details_count_when_any_one_is_there():
    base = {"tel": [], "phones": [], "emails": [], "mailto": [], "address": False}
    for over, said in (({"tel": ["+1"]}, "a phone number"), ({"phones": ["(617) 555-0123"]}, "a phone number"), ({"address": True}, "an address"), ({"emails": ["a@b.test"]}, "an email address"),
                       ({"mailto": ["a@b.test"]}, "an email address")):
        f = spoiled("trust_contact", facts_over=dict(base, **over))
        assert f["result"] == "pass" and said in f["found"], over


# ── Checks that hold the page against the ads: the edges ────────────────────

def kw_group(rows):
    return [group(keywords=rows)]


def test_the_keyword_and_term_and_headline_shares_at_exactly_their_lines():
    on, off = "emergency dentist", "zebra grooming"                          # the first is on the page, the second is not
    assert check("match_keywords_headline", groups=kw_group([keyword("emergency dentist boston", 25.0), keyword("tooth pain", 75.0)]))["result"] == "warn"
    assert check("match_keywords_headline", groups=kw_group([keyword("emergency dentist boston", 24.0), keyword("tooth pain", 76.0)]))["result"] == "fail"
    terms = [term(on, 45.0, 10), term(off, 55.0, 10), term("tooth pain", 0.01, 1)]
    assert check("match_terms", groups=[group(terms=terms)])["result"] == "warn"
    terms = [term(on, 44.0, 10), term(off, 56.0, 10), term("tooth pain", 0.01, 1)]
    assert check("match_terms", groups=[group(terms=terms)])["result"] == "fail"


def test_the_headline_echo_lines():
    good, bad = group("g1", "Emergency", cost=30.0), group("g2", "Whitening", headlines=["Teeth Whitening Deals"], cost=70.0)
    assert check("match_headline", groups=[good, bad])["result"] == "warn"                            # 30 percent is exactly the line for a warning
    good2, bad2 = group("g1", "Emergency", cost=29.0), group("g2", "Whitening", headlines=["Teeth Whitening Deals"], cost=71.0)
    assert check("match_headline", groups=[good2, bad2])["result"] == "fail"
    f = check("match_headline", groups=[group("g1", "A", cost=60.0), group("g2", "B", headlines=["Zebra Grooming"], cost=40.0), group("g3", "C", headlines=[], cost=10.0)])
    assert "(2 ad groups judged)" in f["found"]                                                       # an ad group with no headline is not judged


def test_a_group_with_no_keyword_still_counts_when_its_ad_has_a_headline_and_only_the_groups_that_reach_the_page_count():
    g = group(keywords=[])
    assert cm.search_groups(cp.Ctx(page(groups=[g]))) == [cm.search_groups(cp.Ctx(page(groups=[g])))[0]]
    other = group("g9", urls=("https://elsewhere-dental.com/x",))
    assert cm.search_groups(cp.Ctx(page(groups=[other]))) == []
    empty = group("g8", keywords=[], headlines=[])
    assert cm.search_groups(cp.Ctx(page(groups=[empty]))) == []


def test_the_h1_edges():
    assert check("match_h1", fetch_over={"facts_over": {"h1Count": 2}})["result"] == "pass"
    assert check("match_h1", fetch_over={"facts_over": {"h1Count": 3}})["result"] == "warn"
    assert check("match_h1", fetch_over={"facts_over": {"h1Count": 1, "h1": []}})["result"] == "warn"
    assert check("match_h1", fetch_over={"facts_over": {"h1Count": 0, "h1": ["x"]}})["result"] == "warn"


def test_only_eight_missing_keywords_and_six_promises_are_named_in_a_sentence():
    assert cm.SHOWN == 8
    g = group(headlines=["$1 a", "$2 b", "$3 c", "$4 d", "$5 e", "$6 f", "$7 g", "$8 h"], descriptions=[])
    f = check("match_offers", groups=[g])
    assert f["found"].count("$") == 6 and f["count"] == 8


def test_the_rating_shares_at_exactly_their_lines():
    def share(below_cost):
        return [keyword("a", 100.0 - below_cost, landingPage="above"), keyword("b", below_cost, landingPage="below")]
    assert check("match_lp_experience", groups=kw_group(share(30)))["result"] == "fail" and check("match_lp_experience", groups=kw_group(share(29)))["result"] == "warn"
    assert check("match_lp_experience", groups=kw_group(share(10)))["result"] == "warn" and check("match_lp_experience", groups=kw_group(share(9)))["result"] == "pass"
    assert cm.RATING_FAIL == 0.3 and cm.RATING_WARN == 0.1


# ── Reading Google ──────────────────────────────────────────────────────────

def test_the_read_limits():
    assert (rd.KEEP_PAGES, rd.MAX_KEYWORDS, rd.MAX_TERMS, rd.MAX_ADS, rd.MAX_HEADLINES, rd.MAX_DESCRIPTIONS) == (40, 40, 30, 6, 15, 4)


def test_conversions_keep_four_decimals_and_add_up_for_pages_ad_groups_and_terms():
    pg = rd.parse_pages([page_row("https://a.test/x", 1, "MOBILE", 10, 0.123456), page_row("https://a.test/x", 1, "MOBILE", 10, 0.000044)])[0]
    assert pg["conversions"] == 0.1235 and pg["devices"]["mobile"]["conversions"] == 0.1235
    groups = rd.parse_ads([ad_row("g1", "G", "C", "https://a.test/x"), ad_row("g1", "G", "C", "https://a.test/y")])
    assert groups["g1"]["conversions"] == 2.0
    terms = rd.parse_terms([term_row("g1", "a", 1, 1.0), term_row("g1", "a", 1, 1.0)])
    assert terms["g1"][0]["clicks"] == 2


def test_the_busiest_address_of_a_page_wins_a_tie_by_coming_first_and_the_speed_score_range_includes_its_ends():
    got = rd.parse_pages([page_row("https://a.test/x?v=1", 5), page_row("https://a.test/x?v=2", 5)])[0]
    assert got["url"] == "https://a.test/x?v=1"
    assert rd.parse_pages([page_row("https://a.test/x", 5, speed=1)])[0]["speed"] == 1 and rd.parse_pages([page_row("https://a.test/x", 5, speed=10)])[0]["speed"] == 10
    tie = rd.parse_pages([page_row("https://a.test/x?v=1", 5, speed=3), page_row("https://a.test/x?v=2", 5, speed=9)])[0]
    assert tie["speed"] == 3


def test_the_read_carries_the_search_terms():
    out = rd.read_account("1", search_fn=search())
    assert out["adGroups"]["g1"]["terms"] and out["adGroups"]["g1"]["terms"][0]["text"] == "emergency dentist boston"


# ── Reading a page: the parser's rules, one at a time ───────────────────────

def test_a_page_is_built_by_scripts_at_exactly_eighty_words_and_each_marker_alone_is_enough():
    body = lambda n: "<p>" + " ".join(["w"] * n) + "</p>"
    assert parsed('<html><body><div id="root"></div>' + body(79) + "</body></html>")["facts"]["scriptBuilt"] is True
    assert parsed('<html><body><div id="root"></div>' + body(80) + "</body></html>")["facts"]["scriptBuilt"] is False
    for marker in ('<div ng-version="17"></div>', '<div data-reactroot></div>', '<div id="__next"></div>', '<div id="app"></div>'):
        assert parsed("<html><body>" + marker + body(5) + "</body></html>")["facts"]["appRoot"] is True, marker
    assert parsed("<html><body><div id=\"main\"></div>" + body(5) + "</body></html>")["facts"]["appRoot"] is False
    six = "<html><head>" + "".join('<script src="/s%d.js"></script>' % i for i in range(6)) + "</head><body>" + body(5) + "</body></html>"
    five = six.replace('<script src="/s5.js"></script>', "")
    assert parsed(six)["facts"]["scriptBuilt"] is True and parsed(five)["facts"]["scriptBuilt"] is False


def test_a_stylesheet_counts_only_in_the_head_and_a_script_blocks_only_without_async_defer_or_module():
    h = parsed('<html><head><link rel="stylesheet" href="/a.css"></head><body><link rel="stylesheet" href="/b.css"></body></html>')["facts"]["head"]
    assert h["stylesheets"] == 1
    for attr, blocks in (("", 1), (" async", 0), (" defer", 0), (' type="module"', 0), (" nomodule", 0), (' type="text/javascript"', 1)):
        head = parsed('<html><head><script src="/a.js"%s></script></head><body></body></html>' % attr)["facts"]["head"]
        assert head["blockingScripts"] == blocks, attr
    assert parsed("<html><head><script>var a=1;</script></head></html>")["facts"]["head"]["blockingScripts"] == 0


def test_a_form_is_a_search_by_its_role_its_action_its_class_or_its_id():
    for attrs in ('role="search"', 'action="/search"', 'class="site-search"', 'id="searchform"'):
        f = parsed("<html><body><form %s><input name=\"email\" type=\"email\"><input name=\"x\"></form></body></html>" % attrs)["facts"]["forms"]
        assert f[0]["search"] is True, attrs
    plain = parsed('<html><body><form action="/send"><input name="email"><input name="x"></form></body></html>')["facts"]["forms"]
    assert plain[0]["search"] is False


def test_a_hidden_field_is_left_out_whatever_hides_it_and_a_field_is_named_by_its_name_then_its_id():
    for attr in ("hidden", 'style="display:none"', 'style="visibility: hidden"', 'aria-hidden="true"', 'tabindex="-1"'):
        f = parsed('<html><body><form><input name="a"><input name="trap" %s><input name="b"></form></body></html>' % attr)["facts"]["forms"][0]
        assert [x["name"] for x in f["fields"]] == ["a", "b"], attr
    f = parsed('<html><body><form><input id="byid"><input name="byname" id="ignored"></form></body></html>')["facts"]["forms"][0]
    assert [x["name"] for x in f["fields"]] == ["byid", "byname"]


def test_a_submit_input_with_no_label_is_no_call_to_action_and_a_submit_button_outside_a_form_is():
    assert parsed('<html><body><form><input name="a"><input type="submit"></form></body></html>')["facts"]["ctas"] == []
    out = parsed('<html><body><button type="submit">Go</button></body></html>')["facts"]["ctas"]
    assert [c["text"] for c in out] == ["Go"]


def test_a_label_of_exactly_forty_five_characters_counts_and_one_more_does_not_and_a_short_styled_link_needs_three():
    forty_five = "Book " + "x" * 40
    assert len(forty_five) == 45
    assert [c["text"] for c in parsed('<html><body><a href="/x">%s</a></body></html>' % forty_five)["facts"]["ctas"]] == [forty_five]
    assert parsed('<html><body><a href="/x">%sy</a></body></html>' % forty_five)["facts"]["ctas"] == []
    assert [c["text"] for c in parsed('<html><body><a class="btn" href="/x">Our</a></body></html>')["facts"]["ctas"]] == ["Our"]
    assert parsed('<html><body><a class="btn" href="/x">Us</a></body></html>')["facts"]["ctas"] == []
    many = "<html><body>" + "".join('<a href="/x%d">Book now %d</a>' % (i, i) for i in range(60)) + "</body></html>"
    assert len(parsed(many)["facts"]["ctas"]) == 20                          # forty are kept in the reader and the first twenty shown


def test_how_far_down_a_call_to_action_is_counts_each_visible_character_and_a_gap():
    out = parsed("<html><body><p>aaaa bbbb</p><a href='/x'>Book now</a></body></html>")["facts"]["ctas"]
    assert out[0]["at"] == len("aaaa bbbb") + 1


def test_a_repeated_phone_or_email_link_is_listed_once_and_header_links_count_as_menu_links_alone():
    f = parsed('<html><body><a href="tel:+1617">a</a><a href="tel:+1617">b</a><a href="mailto:x@y.test">c</a><a href="mailto:x@y.test">d</a></body></html>')["facts"]
    assert f["tel"] == ["+1617"] and f["mailto"] == ["x@y.test"]
    assert parsed('<html><body><header><a href="/x">x</a></header></body></html>')["facts"]["links"]["nav"] == 1
    assert parsed('<html><body><nav><a href="/x">x</a></nav></body></html>')["facts"]["links"]["nav"] == 1
    assert parsed('<html><body><div><a href="/x">x</a></div></body></html>')["facts"]["links"]["nav"] == 0


def test_a_stray_closing_tag_does_not_make_the_next_menu_uncounted_and_links_to_the_same_site_are_not_outbound():
    f = parsed('<html><body></nav><nav><a href="/x">x</a></nav></body></html>')["facts"]["links"]
    assert f["nav"] == 1
    out = parsed('<html><body><a href="https://acme.test/y">same</a><a href="https://www.acme.test/z">www</a><a href="https://m.facebook.com/x">fb</a><a href="https://other.test/q">o</a></body></html>',
                 url="https://acme.test/lp")["facts"]["links"]
    assert out["outbound"] == 1


def test_an_image_needs_both_width_and_height_or_an_aspect_ratio_to_count_as_sized_and_lazy_can_be_an_attribute():
    f = parsed('<html><body><img src=a width="1"><img src=b height="1"><img src=c width="1" height="1"><img src=d style="aspect-ratio:1/1"><img src=e style="width:10px;height:10px">'
               '<img src=f data-lazy-src="x"><img src=g data-srcset="x"><img src=h loading="eager"></body></html>')["facts"]["images"]
    assert f["count"] == 8 and f["noSize"] == 5 and f["lazy"] == 2


def test_a_self_closed_image_counts_and_a_self_closed_block_opens_nothing():
    assert parsed('<html><body><img src="a.jpg" width="1" height="1" alt="x"/></body></html>')["facts"]["images"]["count"] == 1


def test_lists_are_capped():
    h2 = "".join("<h2>Heading %d</h2>" % i for i in range(12))
    r = p._Reader()
    r.feed("<html><body>" + h2 + "</body></html>")
    assert len(r.h2) == 8
    many_forms = "".join('<form><input name="a%d"><input name="b%d"></form>' % (i, i) for i in range(12))
    assert len(parsed("<html><body>" + many_forms + "</body></html>")["facts"]["forms"]) == 8
    fields = "".join('<input name="f%d">' % i for i in range(40))
    assert len(parsed("<html><body><form>" + fields + "</form></body></html>")["facts"]["forms"][0]["fields"]) == 30
    phones = " ".join("617-555-%04d" % i for i in range(9))
    assert len(parsed("<html><body><p>" + phones + "</p></body></html>")["facts"]["phones"]) == 6


def test_rating_markup_by_either_type_is_proof_and_an_inline_script_without_text_is_not_kept():
    for typ in ("AggregateRating", "Review"):
        html = '<html><body><script type="application/ld+json">{"@type":"%s"}</script>x</body></html>' % typ
        assert "rating markup" in parsed(html)["facts"]["proof"], typ
    r = p._Reader()
    r.feed('<html><body><script src="/a.js"></script><script>   </script><script>var x=1;</script></body></html>')
    assert r.inline == ["var x=1;"]


def test_an_embedded_frame_is_named_once_and_by_its_data_src_too():
    f = parsed('<html><body><iframe src="https://forms.hubspot.com/a"></iframe><iframe data-src="https://forms.hubspot.com/b"></iframe><iframe src="https://js.hsforms.net/x"></iframe></body></html>')["facts"]
    assert f["embeds"] == ["forms.hubspot.com", "js.hsforms.net"]


# ── Fetching: the edges ─────────────────────────────────────────────────────

def test_a_page_with_no_raw_stream_is_read_from_its_content_and_cut_at_the_limit():
    class Plain:
        status_code, url, headers = 200, "https://a.test/x", {"Content-Type": "text/html"}
        content = b"<html><body>" + b"x" * 1000 + b"</body></html>"

        def close(self):
            pass
    r = p.fetch("https://a.test/x", lambda url, **kw: Plain())
    assert r["isHtml"] is True and r["truncated"] is False
    big = type("Big", (Plain,), {"content": b"x" * (p.MAX_BYTES + 5)})
    r2 = p.fetch("https://a.test/x", lambda url, **kw: big())
    assert r2["truncated"] is True and r2["bytes"] == p.MAX_BYTES
    exactly = type("Exact", (Plain,), {"content": b"x" * p.MAX_BYTES})
    assert p.fetch("https://a.test/x", lambda url, **kw: exactly())["truncated"] is False


def test_a_slow_body_stops_being_read_when_its_time_budget_is_up():
    class Slow:
        def __init__(self):
            self.calls = 0

        def read(self, n=-1, decode_content=False):
            self.calls += 1
            import time
            time.sleep(0.05)
            return b"x" * 10
    resp = type("R", (), {"raw": Slow()})()
    data, cut = p._read(resp, budget_s=0.12)
    assert 0 < len(data) < 200 and cut is False and resp.raw.calls <= 5


def test_the_click_id_is_only_kept_when_both_it_and_the_utm_tag_are_there():
    assert p._survives("https://a.test/x?gclid=1&utm_source=lp") is True
    assert p._survives("https://a.test/x?gclid=1") is False and p._survives("https://a.test/x?utm_source=lp") is False and p._survives("https://a.test/x") is False


def test_a_noindex_header_marks_the_page_and_a_page_with_no_robots_headers_is_left_alone():
    def get(url, **kw):
        return Resp(200, url, html_bytes(), {"Content-Type": "text/html", "X-Robots-Tag": "noindex, nofollow"})
    assert p.fetch("https://a.test/x", get)["facts"]["noindex"] is True
    assert p.fetch("https://a.test/x", lambda url, **kw: Resp(200, url, html_bytes(), {"Content-Type": "text/html"}))["facts"]["noindex"] is False


def test_the_page_facts_and_the_robots_check_use_the_address_the_page_ended_on():
    seen = []

    def get(url, **kw):
        return Resp(200, "https://elsewhere.test/landed", html_bytes(), {"Content-Type": "text/html"})
    r = p.fetch("https://a.test/x", get, robots=lambda u: seen.append(u) or True)
    assert seen == ["https://elsewhere.test/landed"] and r["tech"]["https"] is True


def test_no_robots_file_is_open_for_a_4xx_but_a_server_error_is_unknown():
    def robots(status):
        def get(url, **kw):
            return Resp(status, url, b"gone", {"Content-Type": "text/plain"})
        return p.make_robots(get)("https://a.test/x")
    assert robots(400) is True and robots(404) is True and robots(499) is True
    assert robots(500) is None and robots(503) is None


def test_the_click_id_test_is_not_run_when_the_page_did_not_open():
    calls = []

    def get(url, **kw):
        calls.append(url)
        return Resp(404, "https://a.test/other", b"", {"Content-Type": "text/html"})
    r = p.fetch("https://a.test/x", get)
    assert r["redirected"] is True and r["paramsSurvive"] is None and len(calls) == 1


def test_the_time_budget_constants():
    assert (p.MAX_BYTES, p.TIMEOUT_S, p.READ_BUDGET_S, p.BUDGET_S, p.WORKERS, p.ATF_CHARS) == (800_000, 8, 12, 70, 6, 1500)


# ── The report: the edges ───────────────────────────────────────────────────

def test_the_report_limits():
    assert (lp.MAX_PAGES, lp.MIN_JUDGED, lp.TTL_S, lp.PARTIAL_TTL_S, lp.SHOWN_GROUPS, lp.SHOWN_KEYWORDS, lp.SHOWN_TERMS, lp.SHOWN_HEADLINES, lp.FIX_ITEMS) == (40, 10, 3600, 300, 8, 12, 8, 6, 6)


def test_the_note_about_pages_beyond_the_cap_gives_the_share_of_the_spend_exactly():
    pages = [page_row("https://acme-dental.com/p%d" % i, 100.0) for i in range(4)]
    out = run(search_fn=search(pages=pages, ads=[]), fetcher=Fetcher(), max_pages=3)
    assert "4 landing pages took money in the last 30 days. The 3 that spent most were checked, 75% of the spend." in out["notes"]
    fresh()
    every = run(search_fn=search(pages=pages, ads=[]), fetcher=Fetcher(), max_pages=4)
    assert not any("landing pages took money" in n for n in every["notes"])                  # every page was checked: nothing to explain


def test_the_accounts_tally_is_the_sum_of_its_pages_and_starts_at_zero():
    out = run(search_fn=search(pages=[page_row("https://acme-dental.com/lp", 10.0)], ads=[]))
    t = out["summary"]["tally"]
    only = out["pages"][0]["summary"]["tally"]
    assert all(t[k] == only[k] for k in ("pass", "warn", "fail", "na", "unknown"))


def test_the_note_about_refused_pages_is_in_the_singular_and_the_plural():
    fresh()
    one = run(search_fn=search(pages=[page_row("https://acme-dental.com/lp", 10.0), page_row("https://acme-dental.com/b", 5.0)], ads=[]), fetcher=Fetcher({"/b": (403, b"no")}))
    assert "1 page refused our request, so it was checked only on what Google Ads says." in one["notes"]
    fresh()
    two = run(search_fn=search(pages=[page_row("https://acme-dental.com/a", 10.0), page_row("https://acme-dental.com/b", 5.0)], ads=[]), fetcher=Fetcher({"/a": (403, b"no"), "/b": (403, b"no")}))
    assert "2 pages refused our request, so they were checked only on what Google Ads says." in two["notes"]


def test_a_page_with_a_critical_failure_is_at_risk_whatever_its_grade():
    class Closed(Fetcher):
        def __call__(self, url, **kw):
            if url.endswith("/robots.txt"):
                return Resp(200, url, b"User-agent: AdsBot-Google\nDisallow: /\n", {"Content-Type": "text/plain"})
            return super().__call__(url, **kw)
    out = run(fetcher=Closed())
    page_ = out["pages"][0]
    assert page_["summary"]["critical"] == 1 and page_["grade"] in ("C", "B", "A") and page_["summary"]["opens"] is True
    assert out["summary"]["spendAtRisk"] == 1000.0 and out["summary"]["pagesAtRisk"] == 1


def test_the_account_sections_carry_the_pages_section_scores_and_a_third_of_the_spend_unverified_holds_the_grade():
    out = run()
    page_secs = {s["key"]: s["score"] for s in out["pages"][0]["sections"]}
    assert {s["key"]: s["score"] for s in out["sections"]} == page_secs
    fresh()
    pages = [page_row("https://acme-dental.com/lp", 70.0, "MOBILE", 600, 20), page_row("https://acme-dental.com/blocked", 30.0, "MOBILE", 400, 8)]
    ads = [ad_row("g1", "A", "C", "https://acme-dental.com/lp", 70.0), ad_row("g2", "B", "C", "https://acme-dental.com/blocked", 30.0)]
    held = run(search_fn=search(pages=pages, ads=ads), fetcher=Fetcher({"/blocked": (403, b"no")}))
    assert held["summary"]["capReasons"] == ["pages that took 30% of the spend could not be verified"] and held["summary"]["grade"] in ("C", "D", "F")
    fresh()
    pages = [page_row("https://acme-dental.com/lp", 71.0, "MOBILE", 600, 20), page_row("https://acme-dental.com/blocked", 29.0, "MOBILE", 400, 8)]
    ads = [ad_row("g1", "A", "C", "https://acme-dental.com/lp", 71.0), ad_row("g2", "B", "C", "https://acme-dental.com/blocked", 29.0)]
    free = run(search_fn=search(pages=pages, ads=ads), fetcher=Fetcher({"/blocked": (403, b"no")}))
    assert free["summary"]["capReasons"] == []


def test_a_saved_read_is_kept_for_exactly_the_hour():
    store, calls = FakeStore(), []
    run(store=store, search_fn=search(calls=calls))
    fresh()
    store.at[lp.cache_key(ACCOUNT_ID)] -= lp.TTL_S - 5
    run(store=store, search_fn=search(calls=calls))
    assert len(calls) == 4
    fresh()
    store.at[lp.cache_key(ACCOUNT_ID)] -= 10
    run(store=store, search_fn=search(calls=calls))
    assert len(calls) == 8


# ── The lab test: the edges ─────────────────────────────────────────────────

def lab_body(opportunity=None):
    audits = {"x": {"title": "Cut it", "details": {"type": "opportunity", "overallSavingsMs": opportunity}}}
    return {"lighthouseResult": {"categories": {"performance": {"score": 0.5}}, "audits": audits}}


def test_a_saving_of_exactly_three_hundred_milliseconds_is_named_and_less_is_not():
    assert sp.parse(lab_body(300))["savings"] == [{"title": "Cut it", "ms": 300}] and sp.parse(lab_body(299))["savings"] == []
    assert (sp.TIMEOUT_S, sp.MAX_SAVINGS, sp.MIN_SAVING_MS, sp.KEEP_S) == (75, 5, 300, 3 * 24 * 3600)


@pytest.mark.parametrize("status, message, kind", [
    (400, "FAILED_DOCUMENT_REQUEST", "page"), (400, "Lighthouse returned error: x", "page"), (400, "ERRORED_DOCUMENT_REQUEST", "page"),
    (400, "API key not valid", "not_configured"), (403, "has not been used in project", "not_configured"), (403, "API is disabled", "not_configured"), (403, "PERMISSION_DENIED", "not_configured"),
    (403, "something else", "api"), (400, "something else", "api"), (404, "API key", "api")])
def test_each_wording_google_uses_for_a_refused_key_or_a_page_it_could_not_load_is_named(status, message, kind):
    class R:
        status_code = status

        def json(self):
            return {"error": {"message": message}}
    with pytest.raises(sp.LabError) as e:
        sp.run("https://a.test/x", get=lambda *a, **k: R(), key="K")
    assert e.value.kind == kind


def test_a_result_of_exactly_three_days_is_kept_and_a_second_older_is_not():
    store = FakeStore()
    sp.remember(store, "1", "a.test/x", {"score": 1}, now=NOW - dt.timedelta(seconds=sp.KEEP_S))
    assert set(sp.saved(store, "1", now=NOW)) == {"a.test/x"}
    store2 = FakeStore()
    sp.remember(store2, "1", "a.test/x", {"score": 1}, now=NOW - dt.timedelta(seconds=sp.KEEP_S + 1))
    assert sp.saved(store2, "1", now=NOW) == {}
    assert sp._age_s("garbage", NOW) == sp.KEEP_S + 1


# ── The model read: the edges ───────────────────────────────────────────────

def test_the_model_read_limits_and_an_answer_is_old_at_exactly_its_time_to_live():
    assert (ai.TTL_S, ai.TEXT_CHARS, ai.GROUPS, ai.KEYWORDS, ai.TERMS, ai.HEADLINES, ai.DESCRIPTIONS, ai.FALLBACK_FINDINGS) == (900, 2500, 4, 8, 6, 8, 3, 4)
    ai.clear_cache()
    calls = []
    from tests.test_paid_lp_ai import FINDINGS, GOOD, raw_read
    raw = raw_read()

    def call(system, text):
        calls.append(1)
        return dict(GOOD)
    key = ("1", "acme-dental.com/lp")
    ai.cached_read(key, "m", raw, "acme-dental.com/lp", FINDINGS, now=0, call=call)
    ai.cached_read(key, "m", raw, "acme-dental.com/lp", FINDINGS, now=ai.TTL_S - 1, call=call)
    assert len(calls) == 1
    ai.cached_read(key, "m", raw, "acme-dental.com/lp", FINDINGS, now=ai.TTL_S, call=call)
    assert len(calls) == 2
    ai.clear_cache()


def test_only_the_newest_hundred_and_twenty_eight_answers_are_kept():
    ai.clear_cache()
    from tests.test_paid_lp_ai import FINDINGS, GOOD, raw_read
    raw = raw_read()
    for i in range(130):
        ai.cached_read(("a", "k%d" % i), "m", raw, "acme-dental.com/lp", FINDINGS, now=float(i), call=lambda s, t: dict(GOOD))
    assert len(ai._answers) == 128 and ("a", "k0") not in ai._answers and ("a", "k129") in ai._answers
    ai.clear_cache()


# ── A second round: what the mutation run still found loose ────────────────

def test_a_keyword_pointing_at_a_third_page_does_not_bring_its_ad_group_to_this_one():
    g = group(urls=("https://elsewhere-dental.com/x",), keywords=[keyword("a", url="https://third-dental.com/y")])
    assert cm.search_groups(cp.Ctx(page(groups=[g]))) == []


def test_the_headline_echo_lines_at_exactly_sixty_percent_of_the_words_and_of_the_spend():
    five = group("g1", "Five", headlines=["Emergency Dentist Boston Whitening Crowns"], cost=100.0)             # three of its five words are in the page's headings: exactly 60 percent
    assert check("match_headline", groups=[five])["result"] == "pass"
    four = group("g1", "Four", headlines=["Emergency Dentist Whitening Crowns Veneers"], cost=100.0)              # two of five
    assert check("match_headline", groups=[four])["result"] == "fail"
    good, bad = group("g1", "Emergency", cost=60.0), group("g2", "Whitening", headlines=["Teeth Whitening Deals"], cost=40.0)
    assert check("match_headline", groups=[good, bad])["result"] == "pass"                                       # 60 percent of the spend is the line for a pass
    good, bad = group("g1", "Emergency", cost=59.0), group("g2", "Whitening", headlines=["Teeth Whitening Deals"], cost=41.0)
    assert check("match_headline", groups=[good, bad])["result"] == "warn"


def test_a_pass_names_six_of_the_promises_when_there_are_more():
    g = group(headlines=["$1 a", "$2 b", "$3 c", "$4 d", "$5 e", "$6 f", "$7 g"], descriptions=[])
    text = " ".join("$%d" % i for i in range(1, 8)) + " " + "words here " * 60
    f = check("match_offers", groups=[g], fetch_over={"facts_over": {"text": text, "wordCount": 200}})
    assert f["result"] == "pass" and f["found"].count("$") == 6


def test_a_score_is_weighted_by_spend_even_when_the_spend_is_under_one():
    assert model.weighted_score([{"score": 100, "spend": 0.9}, {"score": 0, "spend": 0.1}]) == 90


def test_a_page_that_never_answered_says_why_or_that_there_was_no_answer_and_a_passing_page_says_how_long_it_took():
    f = check("path_action", fetch_over={"status": 0, "error": "timed out", "isHtml": False, "facts": None, "tech": None})
    assert f["found"] == "Could not be checked: the page could not be reached (timed out)"
    g = check("path_action", fetch_over={"status": 0, "error": "", "isHtml": False, "facts": None, "tech": None})
    assert g["found"] == "Could not be checked: the page could not be reached (no answer)"
    assert "answered with HTTP 200 in 700 ms" in check("reach_status")["found"]


def test_images_load_lazily_at_exactly_thirty_percent_is_enough_and_one_fewer_is_not():
    def im(count, lazy):
        return spoiled("speed_images", facts_over={"images": {"count": count, "noSize": 0, "lazy": lazy, "noAlt": 0}})
    assert im(100, 30)["result"] == "pass" and im(100, 29)["result"] == "warn"
