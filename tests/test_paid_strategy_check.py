"""Strategy & Campaign Book: what the writing is held to (tracker/paid_strategy_check.py). The model is never called here."""
import datetime as dt

import pytest

from tracker import paid_strategy as ps, paid_strategy_check as ck

EM, EN = chr(0x2014), chr(0x2013)        # built, never typed: nothing in this repo is written with a dash like these


def inputs(**kw):
    base = {"name": "Acme Dental", "website": "https://www.acmedental.com/", "budget": 6000.0, "currency": "USD", "launch": "2026-11-02", "campaignTypes": [], "locations": "Austin, TX"}
    base.update(kw)
    return base


# ── text ────────────────────────────────────────────────────────────────────

def test_text_has_no_markup_no_dashes_and_one_line():
    assert ck.text("  Hello <b>world</b> " + EM + " and" + EN + "more \n there ") == "Hello world, and, more there"
    assert ck.text(None) == "" and ck.text(5) == "5"
    assert ck.text("alpha beta gamma delta", 12) == "alpha beta"
    assert EM not in ck.para("one " + EM + " two\n\n\n\nthree") and ck.para("one\n\n\n\nthree") == "one\n\nthree"


def test_lines_and_rows_keep_only_well_formed_items():
    assert ck.lines(["a", "A", " ", "b", {"x": 1}, None, "c"], n=3) == ["a", "b", "c"] and ck.lines("one") == ["one"] and ck.lines(5) == []
    assert ck.lines(["a", "b", "c", "d", "e"], n=3) == ["a", "b", "c"]
    got = ck.rows([{"a": "1", "b": "2"}, {"a": "", "b": "x"}, "junk", {"b": "only b"}], ["a", "b"])
    assert got == [{"a": "1", "b": "2"}]


def test_numbers_are_parsed_with_their_limits():
    assert ck.number("1,250.5") == 1250.5 and ck.number("30%") == 30 and ck.number("$12") == 12
    assert ck.number("abc", default=9) == 9 and ck.number(-1, lo=0, default=0) == 0 and ck.number(101, hi=100, default=100) == 100


def test_start_case_leaves_brands_and_acronyms_alone():
    assert ck.start_case("free implant consultation") == "Free Implant Consultation"
    assert ck.start_case("the iLet pump for T1D and more") == "The iLet Pump for T1D and More"
    assert ck.start_case("24/7 care in austin") == "24/7 Care in Austin" and ck.start_case("$89 a month") == "$89 a Month"


def test_fit_drops_what_is_over_the_limit_and_counts_it():
    drops = {}
    got = ck.fit(["short one", "x" * 31, "Short One", "another good one"], 30, 10, cased=True, drops=drops, what="headlines")
    assert got == ["Short One", "Another Good One"] and drops == {"headlines": 1}
    assert ck.fit(["a", "b", "c", "d"], 30, 2) == ["a", "b"] and ck.fit("not a list", 30, 2) == []
    assert ck.fit(["x" * 30], 30, 2) == ["x" * 30]                 # exactly at the limit stays


# ── claims ──────────────────────────────────────────────────────────────────

PAGES = ck.norm("Free consultation for dental implants. Implants from $1,900 per tooth. Rated 4.9 stars from 312 reviews. Serving Austin since 2009. Same day emergency visits.")


def test_quotes_must_really_be_on_the_pages():
    assert ck.in_corpus("Free consultation for dental implants", PAGES)
    assert not ck.in_corpus("Voted best dentist in Texas", PAGES) and not ck.in_corpus("short", PAGES) and not ck.in_corpus("", PAGES)
    assert "austin" in PAGES and not ck.in_corpus("Austin", PAGES) and not ck.in_corpus("since 2009", PAGES)       # on a page, but too short to prove anything
    assert not ck.in_corpus("consultation for dental implan", PAGES)           # whole words only


@pytest.mark.parametrize("text,claims", [("Implants From $1,900", []), ("Implants from $2,500", ["$2,500"]), ("Rated 4.9 By 312 Patients", []), ("Rated 4.8 By 312 Patients", ["Rated 4.8"]),
                                         ("Free Implant Consultation", []), ("Free Whitening", ["Free Whitening"]), ("Best Dentist In Austin", ["Best"]), ("Same Day Emergency Visits", []),
                                         ("We Guarantee Results", ["Guarantee"]), ("Open 24/7", ["24/7"]), ("Save 30% Today", ["30%"]), ("Serving Austin", []), ("A 4.9-Star Dentist", []),
                                         ("A 4.1-Star Dentist", ["4.1-Star"])])
def test_claims_not_on_the_pages_are_listed(text, claims):
    assert [c["claim"] for c in ck.claims_to_confirm([("ad", text)], PAGES)] == claims


def test_a_claim_with_two_numbers_needs_both_on_the_pages():
    assert [c["claim"] for c in ck.claims_to_confirm([("a", "Open 24/7")], ck.norm("Open 24 hours on weekdays"))] == ["24/7"]
    assert ck.claims_to_confirm([("a", "Open 24/7")], ck.norm("Open 24 hours, 7 days")) == []


def test_a_claim_in_the_brief_counts_as_confirmed_and_each_place_is_listed_once():
    assert ck.claims_to_confirm([("ad", "Open 24/7")], PAGES, ck.norm("The client is open 24/7")) == []
    got = ck.claims_to_confirm([("a", "Open 24/7"), ("b", "Open 24/7"), ("a", "Open 24/7")], PAGES)
    assert [g["where"] for g in got] == ["a", "b"]


# ── keywords and negatives ──────────────────────────────────────────────────

def test_keywords_are_cleaned_and_deduplicated_by_text_and_match():
    got = ck.keyword_rows([{"text": "Dental Implants, Austin!", "match": "[Exact]", "intent": "Service"}, {"text": "dental implants austin", "match": "exact"},
                           {"text": "dental implants austin", "match": "phrase"}, {"text": "x" * 100, "match": "exact"}, {"text": "", "match": "exact"}, {"text": "a b", "match": "weird"},
                           "junk"])
    assert [(k["text"], k["match"]) for k in got] == [("dental implants austin", "exact"), ("dental implants austin", "phrase"), ("a b", "phrase")]
    assert got[0]["intent"] == "service"
    assert ck.kw_text("one two three four five six seven eight nine ten eleven") == ""


def test_the_match_type_is_known_or_phrase():
    assert [ck.match_of(x) for x in ("EXACT", '"phrase"', "[exact]", "broad", "modified", None)] == ["exact", "phrase", "exact", "broad", "phrase", "phrase"]


@pytest.mark.parametrize("neg,kw,blocks", [(("free", "phrase"), "free dental implants", True), (("free", "phrase"), "freedom dental", False), (("dental implants", "phrase"), "dental implants austin", True),
                                            (("implants dental", "phrase"), "dental implants austin", False), (("implants dental", "broad"), "dental implants austin", True), (("implants jobs", "broad"), "dental implants austin", False),
                                            (("dentist", "exact"), "dentist", True), (("dentist", "exact"), "dentist austin", False), (("jobs", "phrase"), "dentist austin", False)])
def test_when_a_negative_blocks_a_keyword(neg, kw, blocks):
    assert ck.blocks({"text": neg[0], "match": neg[1]}, {"text": kw, "match": "exact"}) is blocks


def test_a_negative_that_would_block_the_campaigns_own_keywords_is_removed():
    kws = [{"text": "dental implants austin", "match": "exact"}, {"text": "implant dentist", "match": "phrase"}]
    negs = [{"text": "free", "match": "phrase", "reason": ""}, {"text": "dental implants", "match": "phrase", "reason": ""}, {"text": "implant", "match": "exact", "reason": ""}]
    kept, removed = ck.negative_conflicts(kws, negs)
    assert [n["text"] for n in kept] == ["free", "implant"] and [(n["text"], k["text"]) for n, k in removed] == [("dental implants", "dental implants austin")]


# ── addresses ───────────────────────────────────────────────────────────────

def test_a_landing_page_has_to_be_on_the_clients_site():
    site, known = "https://www.acmedental.com/", {"acmedental.com/services/implants", "acmedental.com"}
    assert ck.own_url("/services/implants", site, known) == ("https://www.acmedental.com/services/implants", True)
    assert ck.own_url("https://acmedental.com/services/implants/", site, known) == ("https://acmedental.com/services/implants/", True)        # www or not, slash or not
    assert ck.own_url("https://acmedental.com/new-page", site, known) == ("https://acmedental.com/new-page", False)
    assert ck.own_url("https://book.acmedental.com/x", site, known)[0] == "https://book.acmedental.com/x"
    for bad in ("https://evil.com/x", "https://acmedental.com.evil.com/", "https://evilacmedental.com/x", "javascript:alert(1)", "", "ftp://acmedental.com/"):
        assert ck.own_url(bad, site, known) == ("", False), bad
    assert ck.own_url("/x", "", known) == ("", False)


def test_known_urls_are_the_pages_the_research_read():
    research = {"website": {"data": {"pages": [{"url": "https://www.acmedental.com/"}, {"url": "https://acmedental.com/a//b/?x=1#y"}]}}}
    assert ck.known_urls(research) == {"acmedental.com", "acmedental.com/a/b"} and ck.known_urls({}) == set()


# ── the profile ─────────────────────────────────────────────────────────────

def test_a_fact_is_verified_only_when_its_quote_is_on_a_page():
    raw = {"summary": "S", "facts": [{"fact": "free consult", "quote": "Free consultation for dental implants", "source": "u"}, {"fact": "invented", "quote": "Voted best in Texas", "source": "u"}],
           "offerings": [{"name": "Implants", "description": "d", "priority": "HIGH"}, {"name": "x", "description": "d", "priority": "urgent"}]}
    p = ck.profile(raw, PAGES)
    assert [f["verified"] for f in p["facts"]] == [True, False]
    assert [o["priority"] for o in p["offerings"]] == ["high", "medium"] and p["positioning"] == {"promise": "", "differentiators": [], "tone": ""}
    assert ck.profile(None, PAGES)["facts"] == []


# ── the competitors ─────────────────────────────────────────────────────────

def test_only_competitors_that_were_read_survive():
    research = {"competitors": {"data": {"competitors": [{"name": "Smile Austin", "url": "https://smileaustin.com/", "ok": True}, {"name": "Down", "url": "https://down.com/", "ok": False}]}}}
    raw = {"landscape": "L", "competitors": [{"name": "Smile Austin", "url": "https://www.smileaustin.com/", "positioning": "P", "offers": ["$99 special"]},
                                              {"name": "Invented", "url": "https://invented.example/"}, {"name": "Down", "url": "https://down.com/"}, {"name": "smile austin", "url": ""}]}
    got = ck.competitive(raw, research)
    assert [c["name"] for c in got["competitors"]] == ["Smile Austin", "smile austin"] and got["competitors"][0]["url"] == "https://smileaustin.com/"
    assert ck.competitive(None, {})["competitors"] == []


# ── the strategy ────────────────────────────────────────────────────────────

def test_the_strategy_keeps_the_budget_from_the_inputs_not_the_model():
    raw = {"summary": ["a"], "budget": {"pacing": "even", "scaleRule": "x", "monthly": 999999}, "channels": [{"channel": "Search", "why": "w", "share": "70%", "priority": "High", "phase": "Launch"}],
           "measurement": {"conversions": [{"name": "Form", "type": "Web", "primary": "Yes", "how": "tag"}, {"name": "Click", "type": "Web", "primary": "no", "how": ""}]},
           "roadmap": [{"phase": "Days 1 to 30", "actions": ["a", "b"]}, {"phase": "", "actions": ["lost"]}]}
    st = ck.strategy(raw, inputs())
    assert st["budget"]["monthly"] == 6000.0 and st["budget"]["daily"] == 197.37 and st["budget"]["pacing"] == "even"
    assert st["channels"][0]["share"] == 70 and [c["primary"] for c in st["measurement"]["conversions"]] == [True, False]
    assert st["roadmap"] == [{"phase": "Days 1 to 30", "actions": ["a", "b"]}]
    assert ck.strategy(None, inputs())["budget"]["monthly"] == 6000.0


# ── the architecture ────────────────────────────────────────────────────────

def camp(kind="search", theme="Implants", share=50, **kw):
    return dict({"kind": kind, "theme": theme, "purpose": "p", "share": share, "landingPage": "/x", "keywordFocus": ["k"], "audience": "a", "notes": "n", "settings": {}}, **kw)


def test_campaigns_are_named_the_teams_way_and_the_budget_is_split():
    arch = ck.architecture({"campaigns": [camp(share=7), camp("Performance Max", "Local", 3)]}, inputs())
    a, b = arch["campaigns"]
    assert (a["name"], b["name"]) == ("P2_Search_Implants_20261102", "P2_PMax_Local_20261102") and (a["kind"], b["kind"]) == ("search", "pmax")
    assert round(a["daily"] + b["daily"], 2) == 197.37 and a["share"] == 70.0 and b["share"] == 30.0 and a["monthly"] == round(a["daily"] * 30.4)
    assert a["key"] != b["key"]


def test_only_the_asked_for_kinds_are_planned_and_at_most_eight():
    arch = ck.architecture({"campaigns": [camp("search"), camp("pmax", "P"), camp("video", "V")]}, inputs(campaignTypes=["search"]))
    assert [c["kind"] for c in arch["campaigns"]] == ["search"]
    many = ck.architecture({"campaigns": [camp("search", "T%d" % i) for i in range(12)]}, inputs())
    assert len(many["campaigns"]) == 8
    assert ck.architecture({"campaigns": [camp("tv")]}, inputs()) == {"campaigns": []} and ck.architecture(None, inputs()) == {"campaigns": []}


def test_two_campaigns_with_the_same_theme_get_different_keys():
    arch = ck.architecture({"campaigns": [camp(), camp()]}, inputs())
    assert len({c["key"] for c in arch["campaigns"]}) == 2


def test_settings_the_model_left_empty_get_the_teams_standing_answers():
    c = ck.architecture({"campaigns": [camp(settings={"bidStrategy": {"value": "", "why": ""}, "devices": {"value": "Mobile +10%", "why": "Calls"}})]}, inputs(locations="Austin, TX"))["campaigns"][0]
    s = c["settings"]
    assert s["devices"] == {"value": "Mobile +10%", "why": "Calls"} and s["genders"]["value"] == "All genders (none excluded)" and s["language"]["value"] == "English"
    assert s["bidStrategy"]["value"] == "Maximize conversions" and s["locations"]["value"] == "Austin, TX" and s["schedule"]["value"] == "All day, every day"


def test_without_a_budget_the_campaigns_have_no_daily_amount():
    c = ck.architecture({"campaigns": [camp()]}, inputs(budget=None))["campaigns"][0]
    assert c["daily"] is None and c["monthly"] is None


# ── what is built ───────────────────────────────────────────────────────────

CTX = {"site": "https://www.acmedental.com/", "known": {"acmedental.com/services/implants", "acmedental.com/about"}}


def heads(n, limit=30):
    return ["Headline Number %d" % i for i in range(1, n + 1)]


def search_raw(**over):
    raw = {"adGroups": [{"name": "Implants", "theme": "t", "intent": "Service", "landingPage": "/services/implants",
                         "keywords": [{"text": "dental implants austin", "match": "exact"}, {"text": "implant dentist", "match": "phrase"}],
                         "ads": [{"headlines": heads(15), "descriptions": ["Description one is here.", "Description two is here.", "Three", "Four"], "path1": "implants", "path2": "austin",
                                  "pins": [{"text": "Headline Number 1", "position": 1}, {"text": "Not A Headline", "position": 2}, {"text": "Headline Number 2", "position": 7}]}]}],
           "negatives": {"campaign": [{"text": "free", "match": "phrase", "reason": "r"}, {"text": "dental implants", "match": "phrase", "reason": "blocks"}],
                         "lists": [{"name": "Jobs", "keywords": ["jobs", "careers", ""], "reason": "r"}]},
           "assets": {"sitelinks": [{"text": "Dental Implants", "desc1": "Replace teeth", "desc2": "Free consult", "url": "/services/implants"}, {"text": "About Us", "desc1": "a", "desc2": "b", "url": "/about"}],
                      "callouts": ["Free Consultation"], "snippets": [{"header": "services", "values": ["A", "B", "C"]}], "call": {"use": 1, "note": "n"}, "promotions": []}}
    raw.update(over)
    return raw


def test_a_search_campaign_is_built_inside_googles_limits():
    built, drops = ck.build("search", search_raw(), CTX)
    g = built["adGroups"][0]
    assert g["landingPage"] == "https://www.acmedental.com/services/implants" and g["landingVerified"] is True and g["intent"] == "service"
    ad = g["ads"][0]
    assert len(ad["headlines"]) == 15 and ad["pins"] == [{"text": "Headline Number 1", "position": 1}] and ad["path1"] == "implants" and ad["short"] is False
    assert [n["text"] for n in built["negatives"]["campaign"]] == ["free"] and built["negatives"]["removed"] == [{"negative": "dental implants", "match": "phrase", "blocked": "dental implants austin"}]
    assert built["negatives"]["lists"] == [{"name": "Jobs", "keywords": ["jobs", "careers"], "reason": "r"}]
    assert built["assets"]["snippets"] == [{"header": "Services", "values": ["A", "B", "C"]}] and built["assets"]["call"]["use"] is True
    assert drops == {} and ck.shortfalls("search", built) == []


def test_text_over_a_limit_is_dropped_never_cut():
    raw = search_raw()
    raw["adGroups"][0]["ads"][0]["headlines"] = ["x" * 31, "Fine Headline One", "Fine Headline Two", "Fine Headline Three"]
    raw["adGroups"][0]["ads"][0]["descriptions"] = ["d" * 91, "Description okay one.", "Description okay two."]
    raw["adGroups"][0]["ads"][0]["path1"] = "this path is too long"
    raw["assets"]["callouts"] = ["c" * 26, "Okay Callout"]
    raw["assets"]["sitelinks"] = [{"text": "t" * 26, "desc1": "a", "desc2": "b", "url": "/about"}, {"text": "Fine", "desc1": "x" * 36, "desc2": "b", "url": "/about"}, {"text": "Good", "desc1": "x" * 35, "desc2": "b", "url": "/about"}]
    built, drops = ck.build("search", raw, CTX)
    ad = built["adGroups"][0]["ads"][0]
    assert ad["headlines"] == ["Fine Headline One", "Fine Headline Two", "Fine Headline Three"] and len(ad["descriptions"]) == 2 and ad["path1"] == "" and ad["path2"] == ""
    assert built["assets"]["callouts"] == ["Okay Callout"] and [s["text"] for s in built["assets"]["sitelinks"]] == ["Good"]
    assert drops == {"headlines": 1, "descriptions": 1, "callouts": 1, "sitelinks": 2}


def test_a_page_on_another_site_is_refused_everywhere():
    raw = search_raw()
    raw["adGroups"][0]["landingPage"] = "https://evil.com/x"
    raw["assets"]["sitelinks"] = [{"text": "Evil", "desc1": "a", "desc2": "b", "url": "https://evil.com/"}]
    built, drops = ck.build("search", raw, CTX)
    assert built["adGroups"][0]["landingPage"] == "" and built["assets"]["sitelinks"] == [] and drops["landing pages"] == 1 and drops["sitelinks"] == 1


def test_a_page_that_was_not_read_is_kept_but_not_verified():
    raw = search_raw()
    raw["assets"]["sitelinks"] = [{"text": "New Page", "desc1": "a", "desc2": "b", "url": "/brand-new"}]
    built, _ = ck.build("search", raw, CTX)
    assert built["assets"]["sitelinks"][0]["url"] == "https://www.acmedental.com/brand-new" and built["assets"]["sitelinks"][0]["verified"] is False


def test_what_a_build_is_short_of_is_said():
    raw = search_raw()
    raw["adGroups"][0]["ads"][0]["headlines"] = ["One Headline", "Two Headline"]
    raw["adGroups"].append({"name": "Empty", "keywords": [], "ads": []})
    built, _ = ck.build("search", raw, CTX)
    short = ck.shortfalls("search", built)
    assert any("2 headlines" in s and "needs at least 3" in s for s in short) and "Ad group Empty has no keywords." in short and "Ad group Empty has no ad." in short
    assert ck.shortfalls("search", ck.build("search", {}, CTX)[0]) == ["No ad groups were written."]


def pmax_raw():
    return {"assetGroups": [{"name": "Group", "finalUrl": "/about", "headlines": heads(5), "longHeadlines": ["L" * 91, "Long headline that fits the limit"], "descriptions": ["D" * 91, "Short one"],
                             "businessName": "Acme Dental", "cta": "Book now", "images": [{"ratio": "1:1", "concept": "c"}], "videos": [{"concept": "v"}],
                             "signals": {"customSegments": ["dental implants cost"], "interests": [], "yourData": [], "demographics": "Adults"}, "searchThemes": ["a", "b", "a"]}],
            "assets": {}}


def test_a_performance_max_campaign_keeps_its_asset_groups_inside_the_limits():
    built, drops = ck.build("pmax", pmax_raw(), CTX)
    g = built["assetGroups"][0]
    assert g["longHeadlines"] == ["Long Headline That Fits the Limit"] and g["searchThemes"] == ["a", "b"] and g["businessName"] == "Acme Dental" and drops == {"long headlines": 1, "descriptions": 1}
    assert ck.shortfalls("pmax", built) and "short of text" in ck.shortfalls("pmax", built)[0]      # 5 headlines is enough, 1 description is not
    big = pmax_raw()
    big["assetGroups"][0]["businessName"] = "B" * 26
    assert ck.build("pmax", big, CTX)[0]["assetGroups"][0]["businessName"] == ""


def test_a_group_needs_a_description_that_fits_sixty_characters():
    raw = pmax_raw()
    raw["assetGroups"][0]["descriptions"] = ["D" * 80, "E" * 85]
    built, drops = ck.build("pmax", raw, CTX)
    assert drops["descriptions without a short one"] == 1


def test_demand_gen_headlines_may_be_forty_characters_but_display_ones_may_not():
    raw = {"adGroups": [{"name": "G", "audiences": ["in-market"], "ads": [{"format": "Image", "headlines": ["h" * 40, "h" * 41], "longHeadline": "L" * 91, "descriptions": ["d"], "cta": "Book now", "concept": "c", "finalUrl": "/about"}]}]}
    dg, drops = ck.build("demandgen", raw, CTX)
    assert dg["adGroups"][0]["ads"][0]["headlines"] == ["H" + "h" * 39]
    assert drops["headlines"] == 1 and drops["long headlines"] == 1
    disp, _ = ck.build("display", raw, CTX)
    assert disp["adGroups"][0]["ads"][0]["headlines"] == []


def test_every_piece_of_ad_text_is_listed_for_the_claim_scan():
    built, _ = ck.build("search", search_raw(), CTX)
    texts = ck.ad_texts("search", built)
    assert ("Implants, ad 1 headline", "Headline Number 1") in texts and any(w == "callout" for w, _t in texts) and any(w == "sitelink description" for w, _t in texts)
    pm, _ = ck.build("pmax", pmax_raw(), CTX)
    assert any("long headline" in w for w, _t in ck.ad_texts("pmax", pm))


# ── what the review of the first version found too loose ────────────────────

def test_a_less_than_or_greater_than_sign_is_not_a_tag_but_a_real_tag_still_goes():
    assert ck.text("Under <$50 and >$20") == "Under <$50 and >$20" and ck.text("a < b > c") == "a < b > c"
    assert ck.text("Hello <b>there</b> <script>x</script> <br/>") == "Hello there x"


def test_control_characters_never_get_into_text():
    bell, nul = chr(7), chr(0)
    assert ck.text("a" + nul + "b" + bell + "c") == "abc" and ck.para("x" + nul + "\ny") == "x\ny"


def test_an_address_with_a_log_in_or_a_port_is_not_the_clients_page():
    site, known = "https://www.acmedental.com/", set()
    for bad in ("https://user:pw@acmedental.com/x", "https://acmedental.com:8080/x", "https://acmedental.com:99999999/x", "https://user@acmedental.com/"):
        assert ck.own_url(bad, site, known) == ("", False), bad
    assert ck.own_url("https://acmedental.com:443/x", site, known)[0] == "https://acmedental.com/x"


RAW = "dental implants from $1,900 per tooth. 50% off whitening. we have placed over 2,000 implants. rated 4.9 stars from 312 reviews."


@pytest.mark.parametrize("text,flagged", [("Save 50% On Whitening", False), ("Save 50% On Implants", False), ("Save 30% Today", True), ("From $1,900 A Tooth", False), ("From $2,000 A Tooth", True),
                                          ("Over 2,000 Implants Placed", False), ("Save 2,000%", True), ("Save 312%", True)])
def test_a_price_or_a_percentage_must_be_on_the_pages_with_its_own_sign(text, flagged):
    pages = ck.norm(RAW)
    assert bool(ck.claims_to_confirm([("ad", text)], pages, "", RAW)) is flagged


def test_a_percentage_is_not_proved_by_the_same_number_without_its_sign():
    pages = ck.norm("we placed 50 implants last year")
    assert [c["claim"] for c in ck.claims_to_confirm([("ad", "Save 50% Today")], pages, "", "we placed 50 implants last year")] == ["50%"]
    assert ck.claims_to_confirm([("ad", "Save 50% Today")], ck.norm("take 50 percent off"), "", "take 50 percent off") == []


def test_keywords_in_other_languages_keep_their_letters_and_a_lone_plus_is_not_a_keyword():
    assert ck.kw_text("Dentista niños") == "dentista niños" and ck.kw_text("café über") == "café über" and ck.kw_text("+") == "" and ck.kw_text("  ++  ") == ""
    assert ck.kw_text("snake_case word") == "snake case word"


def test_a_pin_matches_the_headline_even_when_the_model_wrote_it_in_lower_case():
    raw = search_raw()
    raw["adGroups"][0]["ads"][0]["headlines"] = ["acme dental care", "second headline", "third headline"]
    raw["adGroups"][0]["ads"][0]["pins"] = [{"text": "acme dental care", "position": 1}, {"text": "ACME Dental Care", "position": 2}]
    ad = ck.build("search", raw, CTX)[0]["adGroups"][0]["ads"][0]
    assert ad["headlines"][0] == "Acme Dental Care" and ad["pins"][0] == {"text": "Acme Dental Care", "position": 1}


def test_a_negative_list_may_not_block_the_campaigns_own_keywords_either():
    raw = search_raw()
    raw["negatives"]["lists"] = [{"name": "Cheap", "keywords": ["free", "dental implants", "jobs"], "reason": "r"}]
    built, _ = ck.build("search", raw, CTX)
    assert built["negatives"]["lists"][0]["keywords"] == ["free", "jobs"]
    assert {"negative": "dental implants", "match": "phrase (list Cheap)", "blocked": "dental implants austin"} in built["negatives"]["removed"]


def test_a_sitelink_has_both_description_lines_or_neither():
    raw = search_raw()
    raw["assets"]["sitelinks"] = [{"text": "One Line", "desc1": "only the first", "desc2": "", "url": "/about"}, {"text": "Both", "desc1": "first", "desc2": "second", "url": "/about"}]
    links = ck.build("search", raw, CTX)[0]["assets"]["sitelinks"]
    assert (links[0]["desc1"], links[0]["desc2"]) == ("", "") and (links[1]["desc1"], links[1]["desc2"]) == ("first", "second")


def test_what_used_to_only_be_counted_is_now_a_shortfall():
    raw = pmax_raw()
    raw["assetGroups"][0]["descriptions"] = ["D" * 80, "E" * 85]
    built, _ = ck.build("pmax", raw, CTX)
    assert any("no description of 60 characters or fewer" in s for s in ck.shortfalls("pmax", built))
    dg = {"adGroups": [{"name": "Empty", "audiences": ["a"], "ads": []}, {"name": "Half", "audiences": [], "ads": [{"headlines": ["Only A Headline"], "descriptions": [], "finalUrl": "/about"}]},
                       {"name": "Full", "audiences": [], "ads": [{"headlines": ["A Headline"], "descriptions": ["A description"], "finalUrl": "/about"}]}]}
    short = ck.shortfalls("demandgen", ck.build("demandgen", dg, CTX)[0])
    assert short == ["Ad group Empty has no ad with a headline and a description.", "Ad group Half has no ad with a headline and a description."]


def test_a_percentage_of_any_size_is_a_claim():
    for text, claim in (("Save 2000% Today", "2000%"), ("Save 1,500% Today", "1,500%"), ("Save 12.5% Today", "12.5%"), ("Save 7 % Today", "7 %"), ("Save 100% Today", "100%")):
        assert [c["claim"] for c in ck.claims_to_confirm([("ad", text)], ck.norm("nothing relevant"), "", "nothing relevant")] == [claim], text


def test_a_campaign_gets_at_least_one_unit_of_the_currency_a_day_when_the_budget_allows_it():
    arch = ck.architecture({"campaigns": [camp(share=1), camp("search", "Other", 99)]}, inputs(budget=100.0))["campaigns"]
    assert arch[0]["daily"] >= 1.0 and round(sum(c["daily"] for c in arch), 2) == 3.29


def test_a_snippet_needs_a_real_header_and_three_values():
    raw = search_raw()
    raw["assets"]["snippets"] = [{"header": "Services", "values": ["A", "B"]}, {"header": "Not A Header", "values": ["A", "B", "C"]}, {"header": "types", "values": ["A", "B", "C", "D"]}]
    built, drops = ck.build("search", raw, CTX)
    assert built["assets"]["snippets"] == [{"header": "Types", "values": ["A", "B", "C", "D"]}] and drops["snippets"] == 2


def test_channel_shares_that_add_to_more_than_a_whole_budget_are_scaled_to_100():
    """The prompt says the shares add to 100; a model answered 60, 50 and 30 and the sheet and PDF showed 140%."""
    raw = {"channels": [{"channel": c, "why": "w", "share": s, "priority": "High", "phase": "Launch"} for c, s in (("Search", 60), ("PMax", 50), ("Meta", 30))]}
    shares = [c["share"] for c in ck.strategy(raw, inputs())["channels"]]
    assert shares == [42.9, 35.7, 21.4] and abs(sum(shares) - 100) < 0.2
    fine = {"channels": [{"channel": "Search", "why": "w", "share": 70, "priority": "High", "phase": "Launch"}, {"channel": "PMax", "why": "w", "share": 30, "priority": "", "phase": ""}]}
    assert [c["share"] for c in ck.strategy(fine, inputs())["channels"]] == [70, 30]            # a split that is right is left alone


def test_two_campaigns_on_the_same_theme_do_not_get_the_same_name():
    arch = ck.architecture({"campaigns": [camp("search", "Brand"), camp("search", "brand"), camp("search", "Brand")]}, inputs())
    names = [c["name"] for c in arch["campaigns"]]
    assert len(set(names)) == 3 and names[0] == "P2_Search_Brand_20261102" and names[1].startswith("P2_Search_Brand_20261102_")
    assert len({c["key"] for c in arch["campaigns"]}) == 3
