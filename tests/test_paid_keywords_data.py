"""Keyword Research: the arithmetic (tracker/paid_keywords_data.py). No network, no model: every status and every number on the page is worked out here."""
import pytest

from tests import keyword_fakes as K
from tracker import paid_keywords as pk, paid_keywords_data as kd


def idx(*kws):
    return kd.index_keywords([{"t": t, "m": m, "c": "C1", "g": "G"} for t, m in kws])


# ── words ───────────────────────────────────────────────────────────────────

def test_words_are_lower_case_letters_and_digits_and_an_apostrophe_is_dropped():
    assert kd.words("Women's Health, Austin TX!") == ["womens", "health", "austin", "tx"]
    assert kd.words(None) == [] and kd.words("  ") == [] and kd.words("24/7 dentist") == ["24", "7", "dentist"]


def test_a_plural_is_taken_off_only_to_compare():
    assert [kd.stem(w) for w in ("dentists", "implants", "studies", "glass", "analysis", "plus", "lens", "us", "gas", "bus")] == ["dentist", "implant", "study", "glass", "analysis", "plus", "len", "us", "gas", "bus"]
    assert kd.skey("Dental Implants") == kd.skey("dental implant") and kd.skey("cost of dental implants") != kd.skey("dental implants")
    assert kd.skey("implants dental") == kd.skey("dental implants")                                    # the same words in another order are the same keyword


# ── Keyword Planner results ─────────────────────────────────────────────────

def test_an_idea_and_a_historical_metric_are_read_into_the_same_row():
    idea = kd.parse_idea(K.mk("dental implants austin"))
    metric = kd.parse_idea(K.mk("dental implants austin", "keywordMetrics"))
    assert idea == metric
    assert idea["kw"] == "dental implants austin" and idea["avg"] == 1900 and idea["comp"] == "Medium" and idea["idx"] == 40 and idea["lo"] == 5.0 and idea["hi"] == 12.0
    assert len(idea["m"]) == 12 and idea["m"]["2026-01"] == 1900 and idea["m"]["2026-12"] == 1900


def test_a_result_with_gaps_is_read_without_failing():
    assert kd.parse_idea({}) is None and kd.parse_idea({"text": "  "}) is None and kd.parse_idea(None) is None and kd.parse_idea("x") is None
    r = kd.parse_idea({"text": "  Tooth   Gem ", "keywordIdeaMetrics": {"avgMonthlySearches": "n/a", "competition": "UNSPECIFIED", "lowTopOfPageBidMicros": "0",
                                                                          "monthlySearchVolumes": [{"month": "NOPE", "year": "2026", "monthlySearches": "5"}, {"month": "MAY", "year": "x"}, {}]}})
    assert r == {"kw": "tooth gem", "avg": 0, "comp": "", "idx": None, "lo": None, "hi": None, "m": {}}
    r = kd.parse_idea({"text": "x", "keywordIdeaMetrics": {"avgMonthlySearches": "12", "monthlySearchVolumes": [{"month": "may", "year": "2026", "monthlySearches": "5"}, {"month": "MAY", "year": "2026", "monthlySearches": "7"}]}})
    assert r["m"] == {"2026-05": 12}                                                                  # the same month twice is added up


# ── how a keyword moves through the year ────────────────────────────────────

def year(shape, y=2026):
    return {"%d-%02d" % (y, i + 1): v for i, v in enumerate(shape)}


def test_a_season_a_climb_a_fall_and_a_flat_year_are_told_apart():
    assert kd.pattern(year(K.SPRING))["label"] == "Peaks in May, 2.4 times the monthly average"
    assert kd.pattern(year(K.SPRING))["peak"] == "May" and kd.pattern(year(K.SPRING))["low"] == "January"
    rising = kd.pattern(year(K.RISING))
    assert rising["label"].startswith("Rising: the last three months are 152% above") and rising["trend"] == 1.52
    falling = kd.pattern(year(K.FALLING))
    assert falling["label"].startswith("Falling: the last three months are 60% below")
    assert kd.pattern(year(K.STEADY))["label"] == "Steady through the year" and kd.pattern(year(K.STEADY))["swing"] == 1.0


def test_the_peak_at_the_end_of_a_climb_is_a_trend_and_not_a_season():
    shape = [40, 45, 50, 55, 60, 70, 80, 90, 100, 110, 120, 200]                                        # the biggest month is the last, after a steady climb
    assert kd.pattern(year(shape))["label"].startswith("Rising")
    spike = [100] * 11 + [400]                                                                          # one spike in the latest month is not a climb
    assert kd.pattern(year(spike))["label"].startswith("Peaks in December")
    drop = [400] + [100] * 11
    assert kd.pattern(year(drop))["label"].startswith("Peaks in January")
    mid = [100, 100, 100, 100, 100, 300, 100, 100, 100, 100, 100, 100]
    assert kd.pattern(year(mid))["label"].startswith("Peaks in June")


def test_months_come_back_in_calendar_order_whatever_year_they_belong_to():
    m = {"2025-11": 10, "2025-12": 10, "2026-01": 10, "2026-02": 10, "2026-03": 10, "2026-04": 100, "2026-05": 10, "2026-06": 10, "2026-07": 10, "2026-08": 10, "2026-09": 10, "2026-10": 10}
    p = kd.pattern(m)
    assert p["peak"] == "April" and p["label"].startswith("Peaks in April")
    reorder = {k: m[k] for k in sorted(m, reverse=True)}
    assert kd.pattern(reorder) == p


def test_a_year_with_too_few_months_or_no_searches_has_no_pattern():
    none = {"peak": None, "low": None, "swing": None, "trend": None, "label": ""}
    assert kd.pattern({}) == none and kd.pattern(None) == none and kd.pattern(year([5, 5, 5, 5, 5])) == none and kd.pattern(year([0] * 12)) == none


# ── what the account already has ────────────────────────────────────────────

def test_a_keyword_the_account_has_is_bidding_in_any_match_type_and_plurals_do_not_hide_it():
    i = idx(("dental implants", "EXACT"))
    assert kd.status_of("dental implants", i) == ("bidding", {"kw": "dental implants", "match": "Exact", "campaign": "C1"})
    assert kd.status_of("Dental Implant", i)[0] == "bidding" and kd.status_of("implants dental", i)[0] == "bidding"
    assert kd.status_of("dental implants", idx(("dental implants", "BROAD")))[0] == "bidding"


def test_an_exact_match_keyword_covers_nothing_but_itself():
    i = idx(("dental implants", "EXACT"))
    assert kd.status_of("dental implants cost", i) == ("new", None) and kd.status_of("cheap dental implants", i)[0] == "new"


def test_a_phrase_keyword_covers_what_holds_its_words_together_in_order():
    i = idx(("dental implants", "PHRASE"))
    assert kd.status_of("dental implants cost", i)[0] == "covered" and kd.status_of("best dental implants near me", i)[0] == "covered"
    assert kd.status_of("implants dental cost", i)[0] == "new" and kd.status_of("dental cost implants", i)[0] == "new"          # the words apart, or out of order


def test_a_broad_keyword_covers_what_holds_all_its_words_in_any_order():
    i = idx(("dental implants", "BROAD"))
    assert kd.status_of("cost of implants for dental patients", i) == ("covered", {"kw": "dental implants", "match": "Broad", "campaign": "C1"})
    assert kd.status_of("dental cost", i)[0] == "new"


def test_the_longest_covering_keyword_is_the_one_named_and_a_bidding_one_wins_over_a_covering_one():
    i = idx(("implants", "BROAD"), ("dental implants", "PHRASE"), ("dental implants cost", "PHRASE"))
    assert kd.status_of("dental implants cost austin", i)[1]["kw"] == "dental implants cost"
    assert kd.status_of("dental implants", i)[0] == "bidding"
    assert kd.status_of("", i) == ("new", None) and kd.status_of("???", i) == ("new", None)


def test_an_account_with_blank_or_no_keywords_covers_nothing():
    assert kd.status_of("dental implants", kd.index_keywords(None)) == ("new", None)
    assert kd.status_of("dental implants", kd.index_keywords([{}, {"t": ""}, {"t": "!!"}])) == ("new", None)


# ── what the account already blocks ─────────────────────────────────────────

NEGS = [{"t": "jobs", "m": "PHRASE", "c": ["A", "B"]}, {"t": "dental implants", "m": "EXACT", "c": ["A"]}, {"t": "free dentist", "m": "BROAD", "c": ["B"]}]
LISTS = [{"n": "Shared", "c": ["A", "B", "C"], "k": [["dogs", "PHRASE"], ["Cats", "EXACT"]]}, {"n": "", "c": [], "k": [["orphan", "PHRASE"]]}]


def test_negative_keywords_block_by_their_match_type():
    ni = kd.index_negatives(NEGS, LISTS)
    assert [h["kw"] for h in kd.negated_by("dental assistant jobs austin", ni)] == ["jobs"]
    assert kd.negated_by("dental implants", ni)[0]["match"] == "Exact" and kd.negated_by("dental implants cost", ni) == []              # exact blocks only itself
    assert [h["kw"] for h in kd.negated_by("dentist for free", ni)] == ["free dentist"]                                                 # broad: all its words, any order
    assert kd.negated_by("free implants", ni) == [] and [h["kw"] for h in kd.negated_by("cats", ni)] == ["Cats"]
    assert kd.negated_by("tooth cats", ni) == [] and kd.negated_by("jobsite", ni) == []                                                # a negative is compared as written: no close variants


def test_a_negative_names_every_campaign_it_works_in_and_a_list_works_where_it_is_attached():
    ni = kd.index_negatives(NEGS, LISTS)
    assert kd.negated_by("dogs", ni)[0]["campaigns"] == ["A", "B", "C"] and kd.negated_by("jobs", ni)[0]["campaigns"] == ["A", "B"]


def test_blocked_everywhere_or_in_some_campaigns():
    ni = kd.index_negatives(NEGS, LISTS)
    assert kd.blocked_scope(kd.negated_by("dogs", ni), ["A", "B", "C"]) == "all"
    assert kd.blocked_scope(kd.negated_by("jobs", ni), ["A", "B", "C"]) == "some"
    assert kd.blocked_scope(kd.negated_by("tooth", ni), ["A"]) == "" and kd.blocked_scope(kd.negated_by("jobs", ni), []) == "some"             # no campaigns known: it is not called everywhere
    both = kd.negated_by("free dentist jobs", ni)
    assert kd.blocked_scope(both, ["A", "B"]) == "all"                                                                                  # two negatives together cover both campaigns


def test_blank_negatives_are_ignored():
    ni = kd.index_negatives([{}, {"t": ""}, {"t": "!!", "m": "PHRASE"}], [{"k": [["", "PHRASE"]]}])
    assert ni == {} and kd.negated_by("anything", ni) == []


# ── who is the client, who is a competitor ──────────────────────────────────

def test_a_business_is_named_by_its_whole_name_or_the_start_of_its_address():
    m = kd.name_marks("Acme Dental", "https://www.acmedental.com/")
    assert kd.names_it("acme dental austin", m) and kd.names_it("acmedental login", m) and kd.names_it("is acmedentalgroup open", m)
    assert not kd.names_it("dental acme", m) and not kd.names_it("acme", m) and not kd.names_it("dental implants", m)
    s = kd.name_marks("Smile Austin", "https://smileaustin.com/")
    assert kd.names_it("smile austin dentistry", s) and kd.names_it("smileaustin", s) and kd.names_it("is smile austin open", s) and not kd.names_it("smile dentistry austin", s)


def test_a_name_made_only_of_the_common_words_of_its_trade_is_not_matched_by_its_words():
    m = kd.name_marks("Dental Care", "https://dentalcare.com/")
    assert m["words"] == () and m["generic"] is True and not kd.names_it("dental care near me", m) and kd.names_it("dentalcare austin", m)
    assert kd.name_marks("Smile Dental Care", "")["words"] == ("smile", "dental", "care")


def test_the_stem_of_an_address_skips_the_country_part_and_a_short_one_is_not_used():
    assert kd.name_marks("x", "https://www.acme.co.uk/")["stem"] == "acme" and kd.name_marks("x", "https://shop.acme.com.au/")["stem"] == "acme"
    assert kd.name_marks("x", "https://abc.com/")["stem"] == "" and kd.name_marks("x", "")["stem"] == "" and kd.name_marks("", "")["words"] == ()
    assert kd.name_marks("x", "https://a-b-c-dental.com/")["stem"] == "abcdental"


def test_competitors_come_from_the_form_and_from_the_sites_that_were_read_without_repeats():
    inp = {"competitors": [{"name": "smileaustin.com", "url": "https://smileaustin.com/"}, {"name": "Another Dental", "url": ""}, {"name": "Another Dental", "url": ""}]}
    research = {"competitors": {"data": {"competitors": [{"name": "smileaustin.com", "url": "https://smileaustin.com/"}, {"name": "Found Dentistry", "url": "https://found-dentistry.com/"}, "x"]}}}
    marks = kd.competitor_marks(inp, research)
    assert [m["name"] for m in marks] == ["smileaustin.com", "Another Dental", "Found Dentistry"]
    assert kd.competitor_marks({}, {}) == [] and kd.competitor_marks({"competitors": [{"name": "", "url": ""}]}, None) == []


def test_an_idea_holding_every_word_of_a_phrase_to_avoid_is_avoided():
    assert kd.avoided("cheap dentures austin", ["dentures"]) and kd.avoided("dental implants cost", ["cost implants"]) and kd.avoided("Dentures", ["denture"])
    assert not kd.avoided("dental implants", ["dentures", "cheap"]) and not kd.avoided("dental implants", ["implants cost"]) and not kd.avoided("x", []) and not kd.avoided("x", ["!!"])


# ── the candidates ──────────────────────────────────────────────────────────

def research(planner=None, account=None, **extra):
    ideas = [dict(kd.parse_idea(K.mk(k)), **{"from": ["seed: dental implants"]}) for k in (planner if planner is not None else K.MARKET)]
    base = {"planner": {"data": {"ideas": ideas, "termMetrics": {}}}, "account": {"data": account if account is not None else {
        "keywords": K.ACCOUNT_KEYWORDS, "negatives": K.ACCOUNT_NEGATIVES, "lists": K.ACCOUNT_LISTS, "terms": {"converting": [{"term": t["t"], "clicks": t["k"], "cost": t["x"], "conv": t["v"], "campaign": t["c"]} for t in K.TERMS if t["v"] > 0]}}}}
    base.update(extra)
    return base


def cands(inputs=None, **kw):
    return kd.candidates(K.clean(**(inputs or {})), research(**kw))


def test_the_candidates_are_the_ideas_past_the_floor_not_avoided_not_blocked_everywhere_and_merged():
    rows, stats = cands()
    kws = [r["kw"] for r in rows]
    assert "tooth gem" not in kws                                                  # 5 a month: below the floor of 10
    assert "free dental implants" not in kws and "dental assistant jobs austin" not in kws and "dental implants for dogs" not in kws    # blocked in every campaign
    assert "dentist dallas" in kws                                                 # blocked by dallas in only one campaign: kept, and says so
    assert ("dental implants" in kws) != ("dental implant" in kws)                  # the singular and the plural are one keyword
    assert stats["belowFloor"] == 1 and stats["blockedEverywhere"] == 3 and stats["merged"] == 1 and stats["ideas"] == 26
    assert stats["considered"] == 26 - 1 + 2 and stats["terms"] == 3 and stats["judged"] == len(rows)                # the idea merged away, and two converting terms that are not ideas
    dallas = next(r for r in rows if r["kw"] == "dentist dallas")
    assert dallas["neg"]["scope"] == "some" and dallas["neg"]["by"][0]["kw"] == "dallas"


def test_a_merged_keyword_keeps_the_bigger_and_says_how_many_it_stands_for():
    rows, _ = cands(planner=["dental implants", "dental implant"], account={"keywords": [], "negatives": [], "lists": [], "terms": {}})
    assert len(rows) == 1 and rows[0]["vars"] == 2
    rows, _ = kd.candidates(K.clean(), {"planner": {"data": {"ideas": [{"kw": "tooth whitening", "avg": 100, "comp": "", "idx": None, "lo": None, "hi": None, "m": {}, "from": ["a"]},
                                                                      {"kw": "tooth whitenings", "avg": 300, "comp": "", "idx": None, "lo": None, "hi": None, "m": {}, "from": ["b", "a"]}]}}})
    assert len(rows) == 1 and rows[0]["kw"] == "tooth whitenings" and rows[0]["avg"] == 300 and rows[0]["from"] == ["a", "b"] and rows[0]["vars"] == 2


def test_each_candidate_says_whether_the_account_has_it_and_what_names_the_client_or_a_rival():
    rows, _ = cands()
    by = {r["kw"]: r for r in rows}
    assert by["dental implants austin"]["status"] == "bidding" and by["cost of dental implants"]["status"] == "covered" and by["cost of dental implants"]["by"]["kw"] == "dental implants"
    assert by["emergency dentist near me"]["status"] == "new" and by["invisalign cost"]["status"] == "covered"
    assert by["acme dental austin"]["brand"] is True and by["acme dental"]["status"] == "bidding" and by["dental implants austin"]["brand"] is False
    assert by["smile austin dentistry"]["rival"] == "smileaustin.com" and by["smile austin veneers"]["rival"] == "smileaustin.com" and by["invisalign austin"]["rival"] == ""


def test_the_floor_is_the_clients_and_a_converting_term_is_kept_below_it():
    rows, stats = cands({"minSearches": 500})
    assert all((r["avg"] or 0) >= 500 or r.get("conv") for r in rows) and stats["belowFloor"] > 5
    assert "all on 4 implants austin tx" in [r["kw"] for r in rows]                # 70 searches a month, but it converted four times


def test_a_converting_term_that_is_not_a_keyword_is_a_candidate_with_its_numbers():
    rows, stats = cands()
    t = next(r for r in rows if r["kw"] == "all on 4 implants austin tx")
    assert t["src"] == "term" and t["conv"] == {"clicks": 44, "cost": 380.0, "conv": 4.0, "campaign": "P2_Search_Implants_20250101"} and t["from"] == ["a converting search term"] and t["avg"] is None
    assert not any(r["kw"] == "dental implants austin" and r.get("conv") for r in rows)                  # a term that is already a keyword is not offered again
    both = next(r for r in rows if r["kw"] == "implant dentist near me")
    assert both["conv"]["conv"] == 3.0 and both["src"] == "idea" and both["from"] == ["seed: dental implants", "a converting search term"]
    assert stats["terms"] == 3


def test_a_converting_term_takes_its_volume_from_the_metrics_asked_for_it():
    r = research()
    r["planner"]["data"]["termMetrics"] = {"all on 4 implants austin tx": {"kw": "all on 4 implants austin tx", "avg": 70, "comp": "Low", "idx": None, "lo": 2.0, "hi": 6.0, "m": year(K.STEADY)}}
    rows, _ = kd.candidates(K.clean(), r)
    t = next(x for x in rows if x["kw"] == "all on 4 implants austin tx")
    assert t["avg"] == 70 and t["lo"] == 2.0 and t["m"]["2026-03"] == 100


def test_the_avoid_list_leaves_ideas_out_and_is_counted():
    rows, stats = cands({"avoid": "invisalign, emergency dentist"})
    assert not [r for r in rows if "invisalign" in r["kw"] or "emergency" in r["kw"]] and stats["avoided"] == 5


def test_those_that_share_a_word_with_what_the_client_sells_come_first_and_the_new_come_before_the_have():
    rows, _ = cands()
    assert next(r for r in rows if r["kw"] == "family dentist austin")["overlap"] == 0
    assert next(r for r in rows if r["kw"] == "invisalign austin")["overlap"] == 1 and next(r for r in rows if r["kw"] == "dental implants austin")["overlap"] >= 2


def test_the_slots_are_split_between_new_ideas_and_ideas_the_account_has(monkeypatch):
    monkeypatch.setattr(kd, "CANDIDATES", 6)
    monkeypatch.setattr(kd, "NEW_SLOTS", 4)
    rows, stats = cands()
    assert len(rows) == 6 and sum(1 for r in rows if r["status"] == "new") == 4 and sum(1 for r in rows if r["status"] != "new") == 2
    assert stats["newKept"] > 4 and stats["haveKept"] > 2 and [r["avg"] for r in rows if r["status"] == "new" and not r.get("conv")] == sorted([r["avg"] for r in rows if r["status"] == "new" and not r.get("conv")], reverse=True)


def test_converting_terms_have_their_own_slots_and_go_first(monkeypatch):
    monkeypatch.setattr(kd, "CANDIDATES", 4)
    monkeypatch.setattr(kd, "NEW_SLOTS", 3)
    rows, _ = cands()
    assert sum(1 for r in rows if r.get("conv")) == 3                           # all three converting terms made it, whatever their volume
    assert len(rows) == 4


def test_with_no_planner_and_no_account_there_is_nothing_to_judge():
    assert kd.candidates(K.clean(), {}) == ([], {"ideas": 0, "belowFloor": 0, "avoided": 0, "blockedEverywhere": 0, "merged": 0, "terms": 0, "poolIdeas": 0, "poolSearches": 0, "poolCovered": 0, "considered": 0, "kept": 0, "judged": 0,
                                                                         "newKept": 0, "haveKept": 0})
    rows, _ = kd.candidates(K.clean(account=""), {"planner": research()["planner"]})
    assert rows and all(r["status"] == "new" and r["neg"] is None for r in rows)       # no account: nothing is covered or blocked


def test_the_candidates_are_ordered_by_searches_and_chunked_and_signed():
    rows, _ = cands()
    assert [r["avg"] or 0 for r in rows] == sorted([r["avg"] or 0 for r in rows], reverse=True)
    parts = kd.chunks(rows, 5)
    assert [len(p) for p in parts] == [5, 5, 5, 5, 3] and sum(parts, []) == rows and kd.chunks([], 5) == []
    sig = kd.signature(rows)
    assert sig == kd.signature(list(rows)) and sig != kd.signature(rows[:-1]) and len(sig) == 16 and sig != kd.signature(rows[::-1])


# ── the judged candidates as rows, themes and coverage ──────────────────────

THEME_DEF = K.THEMES["themes"]


def theme_list():
    from tracker import paid_keywords_check as kck
    return kck.themes(K.THEMES, ["P2_Search_Implants_20250101", "P2_Search_Invisalign_20250101"])["themes"]


def assembled(**kw):
    """The candidates, judged by the stand-in model and checked the way the build checks them (a theme named by its name is put right to its key)."""
    from tracker import paid_keywords_check as kck
    rows, _ = cands(**kw)
    themes = theme_list()
    judged = {r["kw"]: dict(K.judge(r["kw"]), theme=kck.theme_key(K.judge(r["kw"])["theme"], themes)) for r in rows}
    return rows, kd.assemble(rows, judged, themes)


def test_assemble_turns_the_judgements_into_rows_with_a_status_a_pattern_and_an_include_flag():
    rows, a = assembled()
    by = {r["kw"]: r for r in a["keywords"]}
    r = by["emergency dentist near me"]
    assert r["fit"] == "yes" and r["intent"] == "local" and r["theme"] == "emergency" and r["status"] == "new" and r["include"] is True and r["avg"] == 9900 and r["pattern"] == "Steady through the year"
    assert by["invisalign cost"]["pattern"].startswith("Peaks in May") and by["invisalign cost"]["include"] is False             # covered: nothing to add
    assert by["dentist dallas"]["fit"] == "no" and by["dentist dallas"]["include"] is False
    assert by["smile austin dentistry"]["intent"] == "competitor" and by["smile austin dentistry"]["include"] is False
    assert by["teeth whitening austin"]["theme"] == "other" and by["teeth whitening austin"]["fit"] == "maybe"
    assert len(a["keywords"]) == len(rows)


def test_code_decides_brand_and_competitor_whatever_the_model_said():
    rows, _ = cands()
    judged = {r["kw"]: {"fit": "yes", "intent": "transactional", "theme": "implants", "match": "exact", "priority": "high", "note": ""} for r in rows}
    a = kd.assemble(rows, judged, theme_list())
    by = {r["kw"]: r for r in a["keywords"]}
    assert by["acme dental austin"]["intent"] == "brand" and by["smile austin dentistry"]["intent"] == "competitor" and by["smile austin dentistry"]["include"] is False
    assert by["emergency dentist near me"]["intent"] == "transactional"


def test_what_the_model_got_wrong_is_put_right_not_trusted():
    rows, _ = cands()
    a = kd.assemble(rows, {"emergency dentist near me": {"fit": "great", "intent": "???", "theme": "nope", "match": "fuzzy", "priority": "urgent", "note": "x"}}, theme_list())
    by = {r["kw"]: r for r in a["keywords"]}
    r = by["emergency dentist near me"]
    assert (r["fit"], r["intent"], r["theme"], r["match"], r["priority"]) == ("unjudged", "other", "other", "phrase", "medium") and r["include"] is False
    assert by["invisalign cost"]["fit"] == "unjudged" and a["coverage"]["unjudged"] == len(rows)


def test_a_keyword_google_ads_would_refuse_is_never_put_in_the_editor_file():
    ideas = [{"kw": "$399 dental implants", "avg": 500, "comp": "", "idx": None, "lo": None, "hi": None, "m": {}, "from": ["x"]}, {"kw": "dental implants austin tx", "avg": 400, "comp": "", "idx": None, "lo": None, "hi": None, "m": {}, "from": ["x"]}]
    rows, _ = kd.candidates(K.clean(account=""), {"planner": {"data": {"ideas": ideas}}})
    j = {r["kw"]: {"fit": "yes", "intent": "transactional", "theme": "implants", "match": "exact", "priority": "high", "note": ""} for r in rows}
    a = kd.assemble(rows, j, theme_list())
    by = {r["kw"]: r for r in a["keywords"]}
    assert by["$399 dental implants"]["include"] is False and by["$399 dental implants"]["editorOk"] is False and by["dental implants austin tx"]["include"] is True


def test_the_themes_carry_their_own_totals_and_the_campaign_they_would_go_in():
    rows, a = assembled()
    t = {x["key"]: x for x in a["themes"]}
    imp = t["implants"]
    members = [r for r in a["keywords"] if r["theme"] == "implants"]
    yes = [r for r in members if r["fit"] == "yes"]
    assert imp["keywords"] == len(members) and imp["yes"] == len(yes) and imp["searches"] == sum(r["avg"] or 0 for r in yes)
    assert imp["new"] == sum(1 for r in yes if r["status"] == "new") and imp["newSearches"] == sum(r["avg"] or 0 for r in yes if r["status"] == "new")
    assert imp["coveredSearches"] == imp["searches"] - imp["newSearches"] and imp["coverage"] == round(imp["coveredSearches"] / imp["searches"] * 100)
    assert imp["campaign"] == "P2_Search_Implants_20250101" and imp["existing"] is True and t["emergency"]["campaign"] == "P2_Search_EmergencyDentist" and t["emergency"]["existing"] is False
    assert t["invisalign"]["campaign"] == "P2_Search_Invisalign_20250101" and t["invisalign"]["pattern"].startswith("Peaks in May")
    assert imp["top"] == sorted(imp["top"], key=lambda x: -(x["avg"] or 0)) and all(x["kw"] for x in imp["top"])
    assert [x["newSearches"] for x in a["themes"]] == sorted([x["newSearches"] for x in a["themes"]], reverse=True)


def test_the_theme_months_are_the_members_added_up_and_the_bids_are_weighted_by_searches():
    rows, a = assembled()
    t = next(x for x in a["themes"] if x["key"] == "emergency")
    yes = [r for r in a["keywords"] if r["theme"] == "emergency" and r["fit"] == "yes"]
    assert t["months"]["2026-05"] == sum(r["m"]["2026-05"] for r in yes) == 9900 + 2400 and t["avgLow"] == round((7 * 9900 + 6 * 2400) / (9900 + 2400), 2)
    assert t["avgHigh"] == round((20 * 9900 + 15 * 2400) / (9900 + 2400), 2)


def test_a_theme_with_no_keywords_is_left_out_and_other_appears_only_when_something_is_in_it():
    rows, a = assembled()
    keys = [t["key"] for t in a["themes"]]
    assert "family" in keys and "other" in keys                                    # family has a maybe in it, other has the rest
    rows2, _ = cands(planner=["dental implants austin", "invisalign austin"], account={"keywords": [], "negatives": [], "lists": [], "terms": {}})
    from tracker import paid_keywords_check as kck
    b = kd.assemble(rows2, {r["kw"]: dict(K.judge(r["kw"]), theme=kck.theme_key(K.judge(r["kw"])["theme"], theme_list())) for r in rows2}, theme_list())
    assert sorted(t["key"] for t in b["themes"]) == ["implants", "invisalign"]


def test_the_coverage_is_the_share_of_the_searches_that_fit_that_the_account_already_has():
    rows, a = assembled()
    c = a["coverage"]
    yes = [r for r in a["keywords"] if r["fit"] == "yes"]
    assert c["judged"] == len(rows) and c["yes"] == len(yes) and c["searches"] == sum(r["avg"] or 0 for r in yes)
    assert c["coveredSearches"] == sum(r["avg"] or 0 for r in yes if r["status"] != "new") and c["newSearches"] == c["searches"] - c["coveredSearches"]
    assert c["pct"] == round(c["coveredSearches"] / c["searches"] * 100) and c["new"] + c["bidding"] + c["matched"] == c["yes"]
    assert c["include"] == sum(1 for r in a["keywords"] if r["include"]) and c["unjudged"] == 0 and c["yes"] + c["maybe"] + c["no"] == c["judged"]


def test_with_nothing_judged_a_fit_there_is_no_coverage_to_state():
    rows, _ = cands()
    a = kd.assemble(rows, {r["kw"]: dict(K.judge(r["kw"]), fit="no") for r in rows}, theme_list())
    assert a["coverage"]["pct"] is None and a["coverage"]["searches"] == 0 and all(t["coverage"] is None for t in a["themes"])


def test_the_editor_rows_are_the_included_keywords_paused_with_a_campaign_and_an_ad_group():
    rows, a = assembled()
    ed = kd.editor_rows(a["keywords"], a["themes"])
    included = {r["kw"] for r in a["keywords"] if r["include"]}
    assert {r[2] for r in ed} == included and len(ed) == len(included)
    assert all(r[4] == "Paused" and r[3] in ("Exact", "Phrase", "Broad") for r in ed)
    row = next(r for r in ed if r[2] == "emergency dentist near me")
    assert row == ["P2_Search_EmergencyDentist", "Emergency Dentist | Local", "emergency dentist near me", "Phrase", "Paused"]
    assert not any(r[2] in ("smile austin dentistry", "dentist dallas", "teeth whitening austin") for r in ed)
    assert ed == sorted(ed, key=lambda r: ed.index(r))                              # stable
    assert kd.editor_rows([], []) == []


def test_the_seasonal_table_has_one_row_per_theme_with_a_year_and_the_month_names():
    rows, a = assembled()
    head, rws = kd.seasonal_table(a["themes"])
    assert head == ["Jan 2026", "Feb 2026", "Mar 2026", "Apr 2026", "May 2026", "Jun 2026", "Jul 2026", "Aug 2026", "Sep 2026", "Oct 2026", "Nov 2026", "Dec 2026"]
    names = [r[0] for r in rws]
    assert "Invisalign" in names and "Family Dentist" not in names                           # a theme with no keyword that fits has no year to show
    inv = next(r for r in rws if r[0] == "Invisalign")
    assert len(inv) == 14 and inv[5] == max(inv[1:13]) and inv[13].startswith("Peaks in May")
    assert kd.seasonal_table([]) == ([], [])


def test_the_campaign_and_the_ad_group_are_named_the_way_the_qa_agent_expects():
    assert kd.campaign_for({"name": "Emergency dentist"}) == "P2_Search_EmergencyDentist" and kd.campaign_for({"name": "x", "campaign": "My Existing"}) == "My Existing"
    assert kd.campaign_for({}) == "P2_Search_Other" and kd.ad_group_for({"name": "Dental Implants"}, "cost") == "Dental Implants | Cost" and kd.ad_group_for({}, None) == "Other | Other"
    assert kd.slug("Dental Implants!") == "dental-implants" and kd.slug("") == "other" and len(kd.slug("a" * 99)) == 40


def test_the_constants_the_page_and_the_checks_rely_on_agree():
    assert kd.FITS == ("yes", "maybe", "no") and "competitor" in kd.INTENTS and "other" in kd.INTENTS and kd.MATCHES == ("exact", "phrase", "broad")
    assert kd.CHUNK * 4 >= kd.CANDIDATES and kd.NEW_SLOTS < kd.CANDIDATES and pk.DEFAULT_MIN in pk.MIN_SEARCHES


# ── what the mutation tests of this file found missing ─────────────────────

def test_the_exact_size_of_a_swing_and_a_gentle_climb_or_fall_are_told():
    assert kd.pattern(year(K.SPRING))["swing"] == 2.35 and kd.pattern(year(K.RISING))["trend"] == 1.52
    gentle_up = [100, 100, 100, 100, 100, 110, 110, 110, 120, 125, 130, 130]
    gentle_down = list(reversed(gentle_up))
    up, down = kd.pattern(year(gentle_up)), kd.pattern(year(gentle_down))
    assert up["label"] == "Rising: the last three months are 28% above the first three" and up["trend"] == 0.28
    assert down["label"] == "Falling: the last three months are 22% below the first three" and down["trend"] == -0.22
    assert kd.pattern(year([100, 100, 100, 100, 100, 105, 105, 105, 110, 110, 115, 115]))["label"] == "Steady through the year"      # a rise under a quarter
    assert kd.pattern(year(list(reversed([100, 100, 100, 100, 100, 105, 105, 105, 110, 110, 115, 115]))))["label"] == "Steady through the year"   # and the same the other way


def test_a_phrase_negative_needs_the_words_together_and_in_order_and_a_broad_one_does_not():
    ni = kd.index_negatives([{"t": "free dentist", "m": "PHRASE", "c": ["A"]}, {"t": "cheap implants", "m": "BROAD", "c": ["A"]}], [])
    assert [h["kw"] for h in kd.negated_by("free dentist near me", ni)] == ["free dentist"]
    assert kd.negated_by("dentist free", ni) == [] and kd.negated_by("free implants dentist", ni) == []                                 # in order, and together
    assert [h["kw"] for h in kd.negated_by("implants cheap", ni)] == ["cheap implants"] and kd.negated_by("cheap dental", ni) == []


def test_a_short_stem_matches_only_as_a_word_of_its_own_and_a_long_one_inside_a_longer_word():
    short = kd.name_marks("x", "https://acme.com/")
    assert short["stem"] == "acme" and kd.names_it("acme dentist", short) and not kd.names_it("acmeology courses", short)
    long_ = kd.name_marks("x", "https://acmedental.com/")
    assert kd.names_it("acmedentalgroup login", long_) and not kd.names_it("dentalgroup", long_)


def test_two_competitors_with_trade_words_for_names_are_kept_apart_by_their_addresses():
    inp = {"competitors": [{"name": "Dental Care", "url": "https://dentalcare.com/"}, {"name": "Clinic Group", "url": "https://clinicgroup.com/"}]}
    marks = kd.competitor_marks(inp, {})
    assert [m["stem"] for m in marks] == ["dentalcare", "clinicgroup"] and [m["name"] for m in marks] == ["Dental Care", "Clinic Group"]
    assert kd.competitor_marks({"competitors": [{"name": "Rival One", "url": ""}, {"name": "Rival Two", "url": ""}]}, {})[1]["words"] == ("rival", "two")


def test_what_the_client_sells_makes_a_word_relevant_but_the_words_of_every_search_do_not():
    inputs = K.clean(services="emergency dentist near me, dentures", avoid="dentures")
    research = {"website": {"data": {"pages": [{"h1": ["Your Austin Family Dentist"], "title": "Home | Acme"}, {"h1": ["Dental Implants in Austin"], "title": "Implants | Acme"}]}},
                "business": {"data": {"offerings": [{"name": "Teeth whitening"}]}}}
    seeds = kd._seed_stems(inputs, research, ["dentures"])
    assert {"emergency", "dentist", "implant", "austin", "teeth", "whitening"} <= seeds
    assert "near" not in seeds and "me" not in seeds                      # said in every search: not a sign of what the client sells
    assert "family" not in seeds and "your" not in seeds                  # the home page is not read for seeds, only the pages after it
    assert "denture" not in seeds                                         # a word on the avoid list is not a sign of what the client sells either


def test_an_idea_with_exactly_the_floor_is_kept_and_one_below_is_not():
    rows, _ = kd.candidates(dict(K.clean(), minSearches=12), research(account={"keywords": [], "negatives": [], "lists": [], "terms": {}}))
    assert "dentist for kids" in [r["kw"] for r in rows]                                                                              # 12 a month, the floor
    rows, stats = kd.candidates(dict(K.clean(), minSearches=13), research(account={"keywords": [], "negatives": [], "lists": [], "terms": {}}))
    assert "dentist for kids" not in [r["kw"] for r in rows] and stats["belowFloor"] >= 1


def one_idea(kw, avg, **extra):
    return dict({"kw": kw, "avg": avg, "comp": "", "idx": None, "lo": None, "hi": None, "m": {}, "from": ["x"]}, **extra)


def test_when_slots_are_short_the_ideas_that_share_a_word_with_what_is_sold_win_and_then_the_bigger(monkeypatch):
    monkeypatch.setattr(kd, "CANDIDATES", 2)
    monkeypatch.setattr(kd, "NEW_SLOTS", 2)
    ideas = [one_idea("huge unrelated thing", 100000), one_idea("dental implants cost", 500), one_idea("dental implants austin", 900), one_idea("dental implants tips", 300)]
    rows, _ = kd.candidates(K.clean(account=""), {"planner": {"data": {"ideas": ideas}}})
    assert [r["kw"] for r in rows] == ["dental implants austin", "dental implants cost"]


def test_when_slots_are_short_an_idea_the_account_has_that_converted_is_kept_before_one_that_did_not(monkeypatch):
    monkeypatch.setattr(kd, "CANDIDATES", 1)
    monkeypatch.setattr(kd, "NEW_SLOTS", 0)
    account = {"keywords": [{"t": "dental", "m": "BROAD", "c": "C", "g": "G"}], "negatives": [], "lists": [], "terms": {"converting": [{"term": "dental whitening gel", "clicks": 5, "cost": 9.0, "conv": 2.0, "campaign": "C"}]}}
    ideas = [one_idea("dental implants", 5000), one_idea("dental whitening gel", 20)]
    rows, _ = kd.candidates(K.clean(), {"planner": {"data": {"ideas": ideas}}, "account": {"data": account}})
    assert [r["kw"] for r in rows] == ["dental whitening gel"] and rows[0]["status"] == "covered" and rows[0]["conv"]["conv"] == 2.0


def test_a_theme_has_a_year_to_show_only_with_six_months_and_some_searches():
    few = {"name": "Few", "searches": 5, "months": {"2026-01": 5, "2026-02": 5, "2026-03": 5, "2026-04": 5, "2026-05": 5}, "pattern": ""}
    none = {"name": "None", "searches": 0, "months": year([0] * 12), "pattern": ""}
    good = {"name": "Good", "searches": 50, "months": year([10] * 6), "pattern": "Steady through the year"}
    head, rows = kd.seasonal_table([few, none, good])
    assert [r[0] for r in rows] == ["Good"] and len(head) == 12 and len(rows[0]) == 14 and rows[0][1:7] == [10] * 6 and rows[0][7:13] == [0] * 6 and rows[0][-1] == "Steady through the year"


# ── what the independent review found ───────────────────────────────────────

def test_the_coverage_of_the_market_is_over_every_kept_idea_that_shares_a_word_not_the_judged_few(monkeypatch):
    monkeypatch.setattr(kd, "CANDIDATES", 6)
    monkeypatch.setattr(kd, "NEW_SLOTS", 5)                                                                          # almost everything judged is new: the judged coverage says little
    rows, stats = cands()
    assert stats["poolIdeas"] > len(rows)
    monkeypatch.undo()
    full, full_stats = kd.candidates(K.clean(), research())
    assert len(full) > len(rows) and stats["poolSearches"] == full_stats["poolSearches"] and stats["poolCovered"] == full_stats["poolCovered"] and stats["poolCovered"] > 0
    cov = kd.pool_coverage(stats)
    assert cov["poolPct"] == round(stats["poolCovered"] / stats["poolSearches"] * 100) and cov["poolIdeas"] == stats["poolIdeas"] and cov["poolSearches"] == stats["poolSearches"]
    assert kd.pool_coverage({}) == {"poolPct": None, "poolSearches": 0, "poolCovered": 0, "poolIdeas": 0} and kd.pool_coverage({"poolSearches": 0, "poolCovered": 0})["poolPct"] is None


def test_the_market_counts_only_ideas_that_share_a_word_and_have_a_volume():
    ideas = [one_idea("dental implants austin", 1000), one_idea("plumber near me", 9000), one_idea("dental whitening kit", 500)]
    account = {"keywords": [{"t": "dental implants austin", "m": "EXACT", "c": "C", "g": "G"}], "negatives": [], "lists": [], "terms": {}}
    _, stats = kd.candidates(K.clean(), {"planner": {"data": {"ideas": ideas}}, "account": {"data": account}})
    assert stats["poolIdeas"] == 2 and stats["poolSearches"] == 1500 and stats["poolCovered"] == 1000                  # the plumber shares no word; the second is 'dental'-only but dental is a word it sells


def test_a_new_negative_is_covered_only_by_an_existing_one_that_blocks_everything_it_would():
    ni = kd.index_negatives([{"t": "jobs", "m": "EXACT", "c": ["A"]}, {"t": "free dental", "m": "PHRASE", "c": ["A"]}, {"t": "cheap", "m": "BROAD", "c": ["A"]}, {"t": "diy teeth", "m": "BROAD", "c": ["B"]}], [])
    covered = lambda text, match: [h["kw"] for h in kd.neg_covered_by(text, match, ni)]
    assert covered("jobs", "exact") == ["jobs"] and covered("jobs", "phrase") == [] and covered("jobs", "broad") == []                  # an exact [jobs] blocks one search
    assert covered("free dental", "phrase") == ["free dental"] and covered("free dental clinic", "phrase") == ["free dental"] and covered("dental free", "phrase") == []
    assert covered("free dental", "exact") == ["free dental"] and covered("free dental clinic", "exact") == ["free dental"] and covered("dental", "exact") == []
    assert covered("free dental", "broad") == [] and covered("cheap", "broad") == ["cheap"] and covered("cheap implants", "broad") == ["cheap"] and covered("cheap", "phrase") == ["cheap"]
    assert covered("teeth diy kit", "broad") == ["diy teeth"] and covered("diy teeth", "phrase") == ["diy teeth"] and covered("diy", "broad") == [] and covered("diy teeth", "exact") == ["diy teeth"]
    one = kd.index_negatives([{"t": "jobs", "m": "PHRASE", "c": ["A"]}], [])
    assert [h["kw"] for h in kd.neg_covered_by("dental jobs", "broad", one)] == ["jobs"] and [h["kw"] for h in kd.neg_covered_by("jobs", "broad", one)] == ["jobs"]       # a one word phrase is a broad one
    two = kd.index_negatives([{"t": "free dental", "m": "PHRASE", "c": ["A"]}], [])
    assert kd.neg_covered_by("free dental", "broad", two) == [] and kd.neg_covered_by("free dental", "phrase", two)[0]["campaigns"] == ["A"]


def test_a_keyword_blocked_by_a_negative_in_the_campaign_it_would_go_in_is_not_offered_for_that_campaign():
    account = {"keywords": [{"t": "dental implants austin", "m": "EXACT", "c": "P2_Search_Implants_20250101", "g": "G"}, {"t": "invisalign austin", "m": "EXACT", "c": "P2_Search_Invisalign_20250101", "g": "G"}],
               "negatives": [{"t": "emergency", "m": "PHRASE", "c": ["P2_Search_Implants_20250101"]}], "lists": [], "terms": {}}
    ideas = [one_idea("emergency dental implants", 800), one_idea("emergency invisalign", 700)]
    rows, _ = kd.candidates(K.clean(), {"planner": {"data": {"ideas": ideas}}, "account": {"data": account}})
    by = {r["kw"]: r for r in rows}
    assert by["emergency dental implants"]["neg"] == {"scope": "some", "by": [{"kw": "emergency", "match": "Phrase", "campaigns": ["P2_Search_Implants_20250101"]}], "campaigns": ["P2_Search_Implants_20250101"]}
    themes = [{"key": "implants", "name": "Dental Implants", "intent": "transactional", "priority": "high", "why": "", "campaign": "P2_Search_Implants_20250101", "existing": True},
              {"key": "inv", "name": "Invisalign", "intent": "commercial", "priority": "medium", "why": "", "campaign": "P2_Search_Invisalign_20250101", "existing": True}]
    judged = {"emergency dental implants": dict(K.judge("dental implants"), theme="implants"), "emergency invisalign": dict(K.judge("invisalign"), theme="inv")}
    a = kd.assemble(rows, judged, themes)
    out = {r["kw"]: r for r in a["keywords"]}
    assert out["emergency dental implants"]["include"] is False and out["emergency dental implants"]["blockedInTarget"] is True                  # the Implants campaign blocks it
    assert out["emergency invisalign"]["include"] is True and out["emergency invisalign"]["blockedInTarget"] is False                             # another campaign does not
    assert a["coverage"]["blockedInTarget"] == 1 and a["coverage"]["include"] == 1 
    assert kd.editor_rows(a["keywords"], a["themes"]) == [["P2_Search_Invisalign_20250101", "Invisalign | Commercial", "emergency invisalign", "Phrase", "Paused"]]


def test_a_converting_search_term_a_broader_keyword_already_matches_is_still_offered_to_add_as_exact():
    account = {"keywords": [{"t": "dental", "m": "BROAD", "c": "C", "g": "G"}], "negatives": [], "lists": [], "terms": {"converting": [{"term": "dental whitening gel", "clicks": 5, "cost": 9.0, "conv": 2.0, "campaign": "C"}]}}
    ideas = [one_idea("dental whitening gel", 20), one_idea("dental whitening strips", 30)]
    rows, _ = kd.candidates(K.clean(), {"planner": {"data": {"ideas": ideas}}, "account": {"data": account}})
    themes = [{"key": "w", "name": "Whitening", "intent": "commercial", "priority": "medium", "why": "", "campaign": "", "existing": False}]
    a = kd.assemble(rows, {r["kw"]: dict(K.judge("invisalign"), theme="w") for r in rows}, themes)
    by = {r["kw"]: r for r in a["keywords"]}
    assert by["dental whitening gel"]["status"] == "covered" and by["dental whitening gel"]["include"] is True and by["dental whitening strips"]["status"] == "covered" and by["dental whitening strips"]["include"] is False


def test_a_keyword_google_ads_takes_has_letters_digits_a_few_joiners_and_hyphens_inside_words():
    assert all(kd.editor_ok(k) for k in ("dental implants", "x-ray dentist", "women's health", "24 7 dentist", "r&d tax credit", "dentist+", "montréal dentiste", "牙科 診所"))
    assert not any(kd.editor_ok(k) for k in ("$399 dental implants", "-dental", "dental-", "dentist/orthodontist", "dental  implants", "", "!!", "a " * 11, "x" * 81, "dental - implants"))


def test_names_are_compared_by_stems_and_without_and_so_a_plural_or_an_ampersand_does_not_hide_a_rival():
    assert kd.names_it("bright smile dentist", kd.name_marks("Bright Smiles", "")) and kd.names_it("bright smiles", kd.name_marks("Bright Smile", ""))
    smith = kd.name_marks("Smith & Jones Orthodontics", "")
    assert kd.names_it("smith and jones orthodontics austin", smith) and kd.names_it("smith jones orthodontics", smith) and not kd.names_it("jones smith orthodontics", smith)


def test_a_name_that_is_only_a_web_address_or_a_trade_word_does_not_flag_a_whole_market():
    addr = kd.name_marks("smileaustin.com", "https://smileaustin.com/")
    assert addr["words"] == () and addr["generic"] is False and addr["stem"] == "smileaustin" and kd.names_it("smile austin dentistry", addr) and not kd.names_it("com dentistry", addr)
    for host in ("https://dentist.com/", "https://dentists.net/", "https://www.family.org/", "https://best.com/", "https://services.com/"):
        marks = kd.name_marks(host.split("//")[1].split("/")[0], host)
        assert marks["stem"] == "" and marks["words"] == (), host
        assert not kd.names_it("dentist near me family services best", marks)
    assert kd.name_marks("Premier Dental", "https://premierdental.com/")["stem"] == "premierdental"


def test_keywords_in_other_languages_keep_their_words_and_do_not_merge():
    assert kd.words("Montréal dentiste d’urgence") == ["montréal", "dentiste", "durgence"] and kd.words("牙科 診所 x-ray") == ["牙科", "診所", "x", "ray"]
    assert kd.skey("牙科 診所") != kd.skey("医院 牙医") and kd.skey("牙科 診所") != "" and kd.skey("!!!") == ""
    rows, stats = kd.candidates(K.clean(account=""), {"planner": {"data": {"ideas": [one_idea("牙科 診所", 500), one_idea("医院 牙医", 400), one_idea("дантист москва", 300)]}}})
    assert stats["merged"] == 0 and {r["kw"] for r in rows} == {"牙科 診所", "医院 牙医", "дантист москва"}


def test_the_account_keywords_are_filed_by_their_rarest_word_so_a_common_first_word_does_not_make_every_idea_scan_them_all():
    keywords = [{"t": "dental %s" % w, "m": "PHRASE", "c": "C", "g": "G"} for w in ("alpha", "bravo", "charlie", "delta", "echo")] + [{"t": "dental", "m": "BROAD", "c": "C", "g": "G"}]
    idx = kd.index_keywords(keywords)
    assert max(len(v) for v in idx.values()) <= 2 and sum(len(v) for v in idx.values()) == 6 and set(idx) == {"alpha", "bravo", "charlie", "delta", "echo", "dental"}
    assert kd.status_of("dental charlie clinic", idx)[1]["kw"] == "dental charlie" and kd.status_of("dental clinic", idx)[1]["kw"] == "dental" and kd.status_of("charlie", idx) == ("new", None)
    assert kd.status_of("dental alpha", idx)[0] == "bidding" and kd.status_of("alpha dental", idx)[0] == "bidding"


def test_the_keyword_named_as_covering_does_not_depend_on_the_order_the_account_listed_them():
    keywords = [{"t": "dental implants", "m": "PHRASE", "c": "C1", "g": "G"}, {"t": "implants dental", "m": "BROAD", "c": "C2", "g": "G"}, {"t": "cost implants", "m": "BROAD", "c": "C3", "g": "G"}]
    first = kd.status_of("dental implants cost", kd.index_keywords(keywords))
    second = kd.status_of("dental implants cost", kd.index_keywords(list(reversed(keywords))))
    assert first == second and first[0] == "covered"


def test_a_converting_term_has_a_slot_each_up_to_the_most_the_account_read_gives(monkeypatch):
    terms = [{"term": "term number %d" % i, "clicks": 1, "cost": 1.0, "conv": 1.0, "campaign": "C"} for i in range(60)]
    account = {"keywords": [], "negatives": [], "lists": [], "terms": {"converting": terms}}
    rows, stats = kd.candidates(K.clean(), {"planner": {"data": {"ideas": [one_idea("dental implants", 100)]}}, "account": {"data": account}})
    assert kd.TERM_SLOTS == 60 and sum(1 for r in rows if r.get("conv")) == 60 and stats["terms"] == 60


# ── what the second round of mutation tests found missing ──────────────────

def test_every_match_type_of_a_new_negative_is_compared_with_every_match_type_of_an_existing_one():
    ni = kd.index_negatives([{"t": "jobs", "m": "EXACT", "c": ["A"]}, {"t": "free dental", "m": "PHRASE", "c": ["A"]}, {"t": "diy teeth", "m": "BROAD", "c": ["A"]}], [])
    covered = lambda text, match: [h["kw"] for h in kd.neg_covered_by(text, match, ni)]
    assert covered("dental jobs", "exact") == [] and covered("dental free", "exact") == [] and covered("diy", "exact") == [] and covered("teeth", "exact") == []         # an existing one that blocks less
    assert covered("diy kit", "phrase") == [] and covered("teeth", "phrase") == [] and covered("teeth", "broad") == [] and covered("free", "broad") == []
    assert covered("diy teeth kit", "exact") == ["diy teeth"] and covered("diy teeth kit", "phrase") == ["diy teeth"] and covered("diy teeth kit", "broad") == ["diy teeth"]


def test_a_host_whose_stem_is_a_trade_word_in_the_other_number_does_not_flag_a_market_either():
    marks = kd.name_marks("lawyer.com", "https://lawyer.com/")
    assert marks["stem"] == "" and not kd.names_it("personal injury lawyer", marks)
    assert kd.name_marks("x", "https://lawyers.com/")["stem"] == "" and kd.name_marks("x", "https://smithlawyer.com/")["stem"] == "smithlawyer"


def test_a_keyword_of_ten_words_is_taken_and_one_of_eleven_is_not():
    assert kd.editor_ok("a b c d e f g h i j") and not kd.editor_ok("a b c d e f g h i j k")


def test_the_equal_length_keywords_that_cover_an_idea_are_named_the_same_whichever_the_account_listed_first():
    keywords = [{"t": "dental implants", "m": "PHRASE", "c": "C1", "g": "G"}, {"t": "implants dental", "m": "BROAD", "c": "C2", "g": "G"}]
    a = kd.status_of("dental implants cost", kd.index_keywords(keywords))
    b = kd.status_of("dental implants cost", kd.index_keywords(list(reversed(keywords))))
    assert a == b and a[1]["kw"] == "implants dental"                                                                  # the same length: the later in the alphabet, always


def test_the_market_counts_an_idea_a_broader_keyword_matches_as_covered_and_not_only_one_the_account_has_itself():
    ideas = [one_idea("dental implants austin", 1000), one_idea("dental whitening kit", 500), one_idea("dental plumbing", 200)]
    account = {"keywords": [{"t": "dental implants austin", "m": "EXACT", "c": "C", "g": "G"}, {"t": "whitening", "m": "PHRASE", "c": "C", "g": "G"}], "negatives": [], "lists": [], "terms": {}}
    _, stats = kd.candidates(K.clean(), {"planner": {"data": {"ideas": ideas}}, "account": {"data": account}})
    assert stats["poolSearches"] == 1700 and stats["poolCovered"] == 1500


def test_a_negative_that_covers_the_campaigns_it_would_go_in_blocks_the_keyword_whatever_the_scope_word_says():
    row = dict(kw="a", fit="yes", include=True, theme="t", neg={"campaigns": ["C1"]})
    themes = [{"key": "t", "name": "T", "intent": "x", "priority": "low", "why": "", "campaign": "C1", "existing": True}]
    out = kd.assemble([dict(kw="b", avg=1, comp="", idx=None, lo=None, hi=None, m={}, status="new", by=None, neg={"scope": "some", "by": [], "campaigns": ["C1"]}, conv=None, brand=False, rival="")],
                      {"b": {"fit": "yes", "intent": "local", "theme": "t", "match": "exact", "priority": "high", "note": ""}}, themes)
    assert out["keywords"][0]["include"] is False and out["keywords"][0]["blockedInTarget"] is True and row["include"] is True


def test_a_search_term_that_converted_is_always_exact_whatever_the_model_judged():
    """The plan says "added as exact"; a Broad judgement put a Broad row in the Ads Editor file for a term a Broad keyword already matched."""
    rows, _ = cands()
    judged = {r["kw"]: {"fit": "yes", "intent": "transactional", "theme": "implants", "match": "broad", "priority": "high", "note": ""} for r in rows}
    by = {r["kw"]: r for r in kd.assemble(rows, judged, theme_list())["keywords"]}
    converted = [r for r in by.values() if r["conv"]]
    assert converted and all(r["match"] == "exact" for r in converted)
    assert all(r["match"] == "broad" for r in by.values() if not r["conv"])           # the model's choice stands for the rest
