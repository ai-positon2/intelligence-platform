"""Keyword Research: what is read before anything is judged (tracker/paid_keywords_research.py): a finished Business Research, the account, Keyword Planner."""
import pytest

from tests import keyword_fakes as K, strategy_fakes as F
from tracker import paid_keywords as pk, paid_keywords_research as kr, paid_strategy_research as rs

ACCOUNT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}


# ── Business Research ───────────────────────────────────────────────────────

def test_the_latest_finished_business_research_for_the_same_site_is_found():
    store = K.Store([{"id": "1" * 32, "name": "Other", "status": "done", "inputs": {"website": "https://other.com/"}},
                     {"id": "2" * 32, "name": "Acme Dental", "status": "running", "inputs": {"website": "https://acmedental.com/"}},
                     {"id": "3" * 32, "name": "Acme Dental", "status": "done", "inputs": {"website": "https://www.acmedental.com/about"}, "stale": 1},
                     {"id": "4" * 32, "name": "Acme", "status": "done", "inputs": {"website": "https://acmedental.com/"}},
                     {"id": "5" * 32, "name": "Acme Dental old", "status": "done", "inputs": {"website": "https://acmedental.com/"}}])
    rec = kr.find_business(K.clean(), list_recent=store.list_recent, get=store.get)
    assert rec["id"] == "4" * 32 and store.asked == ["business"]                          # the newest that is done and not stale, for the same host


def test_a_business_research_is_found_by_name_when_the_site_differs_and_by_site_when_the_name_does():
    store = K.Store([{"id": "1" * 32, "name": "ACME  dental", "status": "done", "inputs": {"website": ""}}])
    assert kr.find_business(K.clean(website="x.com"), list_recent=store.list_recent, get=store.get)["id"] == "1" * 32
    store = K.Store([{"id": "1" * 32, "name": "Something else", "status": "done", "inputs": {"website": "https://acmedental.com/"}}])
    assert kr.find_business(K.clean(name="New Name"), list_recent=store.list_recent, get=store.get)["id"] == "1" * 32
    assert kr.find_business(K.clean(name="Zzz", website="zzz.com"), list_recent=store.list_recent, get=store.get) is None


def test_a_business_research_that_never_finished_writing_is_not_used():
    store = K.Store()
    assert kr.find_business(K.clean(), list_recent=store.list_recent, get=lambda sid: {"plan": {}}) is None
    assert kr.find_business(K.clean(), list_recent=store.list_recent, get=lambda sid: None) is None
    assert kr.find_business(K.clean(), list_recent=lambda kind: None, get=store.get) is None


def test_what_business_research_learned_is_read_into_the_few_parts_used():
    p = kr.read_business(K.clean(), find=lambda i: K.Store().get("b" * 32))
    d = p["data"]
    assert p["state"] == "ok" and p["note"] == "Used the Business Research of 2026-10-10." and d["builtAt"] == "2026-10-10" and d["id"] == "b" * 32
    assert [o["name"] for o in d["offerings"]] == ["Dental implants", "Teeth whitening"] and d["searches"] == ["dental implants austin, all on 4"] and d["vocabulary"] == ["smile"]
    assert d["avoidWords"] == ["painless"] and d["compliance"] == [{"topic": "Healthcare advertising", "rule": "No guaranteed results"}] and d["seasonality"] == [{"window": "January", "why": "New insurance year"}]
    assert d["summary"] == "A family dental practice in Austin."


def test_no_business_research_is_a_skip_and_not_a_failure():
    p = kr.read_business(K.clean(), find=lambda i: None)
    assert p["state"] == "skipped" and "no finished Business Research" in p["note"] and p["data"] == {}


# ── the account ─────────────────────────────────────────────────────────────

def test_the_account_is_read_into_keywords_negatives_converting_terms_and_campaigns():
    p = kr.read_account(ACCOUNT, sqr_read=K.sqr_read(), today=None)
    d = p["data"]
    assert p["state"] == "ok" and d["keywordCount"] == 5 and len(d["keywords"]) == 5 and d["negativeCount"] == 2 and d["listCount"] == 1 and d["keywordsTruncated"] is False
    assert [c["name"] for c in d["campaigns"]] == ["P2_Search_Implants_20250101", "P2_Search_Invisalign_20250101", "P2_Search_Emergency_20250101", "P2_Search_Brand_20250101"]
    assert d["campaigns"][0] == {"name": "P2_Search_Implants_20250101", "keywords": 2, "adGroups": 2}
    assert [t["term"] for t in d["terms"]["converting"]] == ["dental implants austin", "all on 4 implants austin tx", "implant dentist near me", "cheap tooth pulling"]          # most conversions first
    assert d["terms"]["converting"][1] == {"term": "all on 4 implants austin tx", "clicks": 44, "cost": 380.0, "conv": 4.0, "campaign": "P2_Search_Implants_20250101"}
    assert [t["term"] for t in d["terms"]["wasteful"]] == ["dentist jobs austin"] and d["terms"]["total"] == 5 and d["window"]["end"] and d["account"]["currency"] == "USD"
    assert "5 keywords in 4 campaigns" in p["note"] and "2 negative keywords" in p["note"] and "1 shared lists" in p["note"]


def test_the_window_is_the_last_ninety_days_before_today():
    import datetime
    seen = []
    kr.read_account(ACCOUNT, sqr_read=lambda s, a, start, end: (seen.append((start, end)), K.sqr_read()(s, a, start, end))[1], today=datetime.date(2026, 10, 16))
    assert seen == [("2026-07-18", "2026-10-15")]


def test_a_warning_from_the_read_makes_the_piece_partial_and_is_kept():
    p = kr.read_account(ACCOUNT, sqr_read=K.sqr_read(warnings=["Without the keyword list, a term the account already bids on can be suggested again."]))
    assert p["state"] == "partial" and "Without the keyword list" in p["note"] and p["data"]["warnings"] == ["Without the keyword list, a term the account already bids on can be suggested again."]


def test_an_account_that_cannot_be_read_fails_the_piece_and_says_why():
    p = kr.read_account(ACCOUNT, sqr_read=K.sqr_read(fail="403 USER_PERMISSION_DENIED"))
    assert p["state"] == "failed" and "could not be read" in p["note"] and "403 USER_PERMISSION_DENIED" in p["note"] and p["data"] == {}


def test_an_account_with_no_keywords_yet_is_read_as_such():
    p = kr.read_account(ACCOUNT, sqr_read=K.sqr_read(keywords=[], negatives=[], lists=[], terms=[]))
    assert p["state"] == "ok" and p["data"]["keywordCount"] == 0 and p["data"]["campaigns"] == [] and p["data"]["terms"]["converting"] == []


def test_a_very_large_account_keeps_its_first_keywords_and_says_it_was_cut(monkeypatch):
    monkeypatch.setattr(kr, "MAX_KEYWORD_ROWS", 3)
    rows = [{"t": "k%d" % i, "m": "EXACT", "c": "C", "g": "G"} for i in range(5)]
    p = kr.read_account(ACCOUNT, sqr_read=K.sqr_read(keywords=rows))
    assert p["state"] == "partial" and len(p["data"]["keywords"]) == 3 and p["data"]["keywordCount"] == 5 and p["data"]["keywordsTruncated"] is True and "more than 3); the rest were not compared" in p["note"]


def test_the_converting_and_wasteful_terms_are_capped_and_ordered():
    terms = [{"t": "t%d" % i, "s": "", "c": "C", "ct": "SEARCH", "g": "G", "i": 10, "k": 5, "x": float(i), "v": 1.0 if i < 100 else 0.0} for i in range(200)]
    d = kr.read_account(ACCOUNT, sqr_read=K.sqr_read(terms=terms))["data"]
    assert len(d["terms"]["converting"]) == 60 and len(d["terms"]["wasteful"]) == 30 and d["terms"]["wasteful"][0]["term"] == "t199" and d["terms"]["converting"][0]["term"] == "t0"


# ── the seeds ───────────────────────────────────────────────────────────────

def test_the_other_number_of_a_phrase_is_offered_because_planner_sometimes_answers_one_with_nothing():
    assert kr.variant("insulin pump") == "insulin pumps" and kr.variant("insulin pumps") == "insulin pump" and kr.variant("dental implants austin") == "dental implants austins"
    assert kr.variant("braces glass") == "" and kr.variant("dentist") == "dentists"
    assert kr.variant("ai") == "" and kr.variant("") == ""


def planner_research(**extra):
    base = {"website": {"data": {"pages": [{"url": "https://www.acmedental.com/", "h1": ["Your Austin Family Dentist"], "title": "Acme Dental | Dentist in Austin"},
                                           {"url": "https://www.acmedental.com/services/dental-implants", "h1": ["Dental Implants in Austin"], "title": "Dental Implants Austin | Acme Dental"},
                                           {"url": "https://www.acmedental.com/services/invisalign", "h1": ["Invisalign Clear Aligners"], "title": "Invisalign Austin | Acme Dental"}]}},
            "competitors": {"data": {"competitors": [{"name": "Smile Austin", "ok": True, "pages": [{"url": "https://smileaustin.com/"}]}, {"name": "Down", "ok": False, "pages": []},
                                                     {"name": "Two", "ok": True, "pages": [{"url": "https://two.example/"}]}]}},
            "account": {"data": {"terms": {"converting": [{"term": "all on 4 implants austin tx"}, {"term": "implant dentist near me"}]}}},
            "business": {"data": {"offerings": [{"name": "Dental implants", "priority": "high"}, {"name": "Teeth whitening", "priority": "low"}, {"name": "Veneers", "priority": "medium"}]}}}
    base.update(extra)
    return base


def test_planner_is_asked_about_each_service_each_offering_what_converts_the_pages_the_site_and_each_competitor():
    batches, urls = kr.seeds_for(K.clean(), planner_research())
    assert [b["label"] for b in batches] == ["dental implants", "invisalign", "veneers", "what already converts", "the client's pages"]
    assert batches[0]["keywords"] == ["dental implants", "dental implant"] and batches[1]["keywords"] == ["invisalign", "invisaligns"]
    assert batches[2]["keywords"] == ["veneers", "veneer"]                                   # an offering of medium priority; the low priority one is not asked about
    assert batches[3]["keywords"] == ["all on 4 implants austin tx", "implant dentist near me"]
    assert "dentist in austin acme dental" not in " ".join(batches[4]["keywords"]) and "dental implants in austin" in batches[4]["keywords"] and "invisalign clear aligners" in batches[4]["keywords"]
    assert urls == [{"label": "site", "url": "https://www.acmedental.com/"}, {"label": "rival: Smile Austin", "url": "https://smileaustin.com/"}, {"label": "rival: Two", "url": "https://two.example/"}]


def test_an_offering_that_repeats_a_service_the_client_typed_is_asked_once():
    batches, _ = kr.seeds_for(K.clean(services="dental implants"), planner_research())
    assert [b["label"] for b in batches].count("dental implants") == 1


def test_without_a_website_the_client_site_is_still_a_seed_and_without_anything_there_are_no_seeds():
    batches, urls = kr.seeds_for(K.clean(services=""), {})
    assert batches == [] and urls == [{"label": "site", "url": "https://acmedental.com/"}]
    batches, urls = kr.seeds_for(K.clean(services="", website="", account="1234567890"), {})
    assert batches == [] and urls == []


def test_a_seed_that_is_not_a_phrase_is_not_asked():
    batches, _ = kr.seeds_for(K.clean(services="a, x" + "y" * 80 + ", one two three four five six seven, $$$, dental implants"), {})
    assert [b["label"] for b in batches] == ["dental implants"]


def test_the_number_of_requests_is_capped_and_the_urls_are_kept_first(monkeypatch):
    monkeypatch.setattr(kr, "MAX_CALLS", 4)
    batches, urls = kr.seeds_for(K.clean(services="a1 x, b2 y, c3 z, d4 w"), planner_research())
    assert len(urls) == 3 and len(batches) == 1


# ── Keyword Planner ─────────────────────────────────────────────────────────

def ask(research=None, post=None, calls=None, **kw):
    inputs = kw.pop("inputs", None) or K.clean()
    return kr.read_planner(inputs, planner_research() if research is None else research, post=post or K.planner(calls), planner_account="1234567890", currency="USD", workers=1, **kw)


def test_planner_gives_the_ideas_with_their_numbers_and_what_each_was_found_from():
    calls = []
    p = ask(calls=calls)
    d = p["data"]
    assert p["state"] == "ok" and d["total"] == 25 == len(d["ideas"]) and "tooth gem" not in [i["kw"] for i in d["ideas"]]                        # 26 in the market, one below the floor
    top = d["ideas"][0]
    assert top["kw"] == "dental implant" or top["kw"] == "dental implants"
    i = next(x for x in d["ideas"] if x["kw"] == "emergency dentist near me")
    assert i["avg"] == 9900 and i["comp"] == "High" and i["lo"] == 7.0 and i["hi"] == 20.0 and len(i["m"]) == 12 and i["from"][0] == "seed: dental implants" and len(i["from"]) == 4
    assert [x["avg"] for x in d["ideas"]] == sorted([x["avg"] for x in d["ideas"]], reverse=True)
    assert "25 keyword ideas from 8 requests" in p["note"] and d["currency"] == "USD" and d["floor"] == 10 and d["belowFloor"] == 1


def test_planner_is_asked_in_english_for_search_in_the_places_given_and_never_for_adult_ideas():
    calls = []
    ask(calls=calls)
    paths = [c[0] for c in calls]
    assert paths[0] == "/geoTargetConstants:suggest" and paths.count("/customers/1234567890:generateKeywordIdeas") == 8
    body = next(b for p, b in calls if p.endswith("generateKeywordIdeas"))
    assert body["language"] == "languageConstants/1000" and body["geoTargetConstants"] == ["geoTargetConstants/1026201"] and body["keywordPlanNetwork"] == "GOOGLE_SEARCH" and body["includeAdultKeywords"] is False
    assert any((b.get("urlSeed") or {}).get("url") == "https://smileaustin.com/" for p, b in calls) and any((b.get("keywordSeed") or {}).get("keywords") == ["dental implants", "dental implant"] for p, b in calls)


def geo_of(calls):
    return next(b for p, b in calls if p.endswith("generateKeywordIdeas"))["geoTargetConstants"]


def test_without_a_place_the_country_comes_from_the_accounts_currency_and_without_an_account_it_is_the_united_states_and_says_so():
    calls = []
    p = ask(inputs=K.clean(locations="", currency="GBP"), calls=calls)
    assert geo_of(calls) == ["geoTargetConstants/2826"] and p["data"]["geoNote"] == "No place was given, so searches are counted for all of the United Kingdom."
    calls.clear()
    p = ask(inputs=K.clean(locations="", currency="XYZ"), calls=calls)
    assert geo_of(calls) == ["geoTargetConstants/2840"] and p["data"]["geoNote"] == "No place was given, so searches are counted for all of the United States (the default)."
    calls.clear()
    p = ask(inputs=K.clean(locations="", account="", currency="GBP"), calls=calls)                    # the currency of a form with no account is only the form's default: it says nothing
    assert geo_of(calls) == ["geoTargetConstants/2840"] and "(the default)" in p["data"]["geoNote"]


def test_a_place_is_looked_up_wherever_it_is_and_the_note_says_where_searches_are_counted():
    calls = []
    p = ask(calls=calls)
    suggest = next(b for path, b in calls if path.endswith("geoTargetConstants:suggest"))
    assert suggest == {"locale": "en", "locationNames": {"names": ["Austin, TX"]}}                       # no country: Toronto must not be looked for in the United States
    assert p["data"]["geoNote"] == "Searches are counted in Austin,Texas,United States." and p["data"]["geoNote"] in p["note"] and p["data"]["places"][0]["canonical"] == "Austin,Texas,United States"


def test_a_place_google_does_not_find_is_named_and_the_country_is_used_instead():
    base = K.planner()

    def post(path, body):
        if path.endswith("geoTargetConstants:suggest"):
            return {} if body["locationNames"]["names"][0] == "Nowhere" else base(path, body)
        return base(path, body)
    calls = []
    p = ask(inputs=K.clean(locations="Nowhere; Austin, TX"), post=post)
    assert p["data"]["geoNote"] == "Searches are counted in Austin,Texas,United States. Google did not find: Nowhere."
    p = ask(inputs=K.clean(locations="Nowhere"), post=post)
    assert p["data"]["geoNote"] == "No place was found, so searches are counted for all of the United States. Google did not find: Nowhere." and p["data"]["places"] == []
    down = lambda path, body: (_ for _ in ()).throw(RuntimeError("down")) if path.endswith("suggest") else base(path, body)
    assert "Google did not find: Austin, TX." in ask(post=down)["data"]["geoNote"]


def test_what_is_not_asked_about_because_there_would_be_too_many_requests_is_said(monkeypatch):
    dropped = []
    kr.seeds_for(K.clean(services="aa bb, cc dd, ee ff, gg hh, ii jj, kk ll, mm nn, oo pp"), {}, dropped)
    assert dropped == ["mm nn", "oo pp"]
    monkeypatch.setattr(kr, "MAX_CALLS", 3)
    p = ask(inputs=K.clean(services="aa bb, cc dd, ee ff"), research={})                     # the site is one request, so two of the three services fit
    assert p["data"]["notAsked"] == ["ee ff"] and "Not asked about, to keep the number of requests down: ee ff." in p["note"] and [c["label"] for c in p["data"]["calls"]] == ["aa bb", "cc dd", "site"]


def test_a_keyword_list_that_could_not_be_read_is_flagged_so_nothing_is_called_new_on_a_guess():
    read = K.sqr_read(keywords=[], warnings=["Without the keyword list, a term the account already bids on can be suggested again."])

    def failed(store, account, start, end):
        return dict(read(store, account, start, end), keywordsFailed=True, negativesFailed=True, notes=["Account-level negative keywords are not available from the Google Ads API."])
    p = kr.read_account(ACCOUNT, sqr_read=failed)
    d = p["data"]
    assert p["state"] == "partial" and d["keywordsUnknown"] is True and d["negativesUnknown"] is True and d["caveats"] == ["Account-level negative keywords are not available from the Google Ads API."]
    assert "every idea is shown as new and the file for Google Ads Editor is not made" in p["note"]
    ok = kr.read_account(ACCOUNT, sqr_read=K.sqr_read())["data"]
    assert ok["keywordsUnknown"] is False and ok["negativesUnknown"] is False and ok["caveats"] == []


def test_a_cut_list_of_keywords_or_negatives_says_so(monkeypatch):
    def cut(store, account, start, end):
        return dict(K.sqr_read()(store, account, start, end), keywordsCut=True, negativesCut=True)
    d = kr.read_account(ACCOUNT, sqr_read=cut)
    assert d["state"] == "partial" and d["data"]["keywordsTruncated"] is True and "more keywords than were read" in d["note"] and "more negative keywords than were read" in d["note"]
    monkeypatch.setattr(kr, "MAX_NEGATIVE_ROWS", 1)
    many = [{"t": "a", "m": "PHRASE", "c": ["C"]}, {"t": "b", "m": "PHRASE", "c": ["C"]}]
    p = kr.read_account(ACCOUNT, sqr_read=K.sqr_read(negatives=many))
    assert "more negative keywords than were read (more than 1)" in p["note"] and len(p["data"]["negatives"]) == 1


def test_a_request_google_answers_with_nothing_is_named_and_the_others_go_on():
    p = ask(post=K.planner(empty=("veneers",)))
    assert p["state"] == "partial" and p["data"]["emptySeeds"] == ["veneers"] and "Keyword Planner had no ideas for: veneers." in p["note"]
    assert {"label": "veneers", "ideas": 0, "note": "Google returned no ideas"} in p["data"]["calls"] and p["data"]["total"] == 25


def test_a_request_that_fails_is_named_and_the_others_go_on():
    p = ask(post=K.planner(fail=("smileaustin",)))
    assert p["state"] == "partial" and "Keyword Planner did not answer (RESOURCE_EXHAUSTED: too many requests) for rival: Smile Austin." in p["note"] and p["data"]["total"] == 25
    assert any(c["label"] == "rival: Smile Austin" and c["ideas"] == 0 and c["note"].startswith("did not answer") for c in p["data"]["calls"])


def test_when_every_request_fails_the_piece_fails():
    def down(path, body):
        if path.endswith("geoTargetConstants:suggest"):
            return {}
        raise RuntimeError("DEVELOPER_TOKEN_NOT_APPROVED")
    p = ask(post=down)
    assert p["state"] == "failed" and "DEVELOPER_TOKEN_NOT_APPROVED" in p["note"] and p["data"] == {}


def test_when_google_answers_all_of_them_with_nothing_the_piece_fails_and_says_so():
    p = ask(post=K.planner(empty=("",)))
    assert p["state"] == "failed" and "no ideas" in p["note"].lower() and "Keyword Planner had no ideas for" in p["note"]


def test_ideas_below_the_floor_are_not_kept_and_the_floor_is_the_clients():
    p = ask(inputs=K.clean(minSearches=500))
    assert all(i["avg"] >= 500 for i in p["data"]["ideas"]) and p["data"]["floor"] == 500 and p["data"]["total"] < 25
    p = ask(inputs=dict(K.clean(), minSearches=100000))
    assert p["state"] == "failed" and "at or above 100000 searches a month" in p["note"]


def test_the_ideas_of_one_request_and_of_all_are_capped_to_the_biggest(monkeypatch):
    monkeypatch.setattr(kr, "PER_CALL_IDEAS", 5)
    p = ask()
    assert p["data"]["total"] == 5 and all(i["avg"] >= 2400 for i in p["data"]["ideas"])
    monkeypatch.setattr(kr, "PER_CALL_IDEAS", 700)
    monkeypatch.setattr(kr, "MAX_IDEAS", 7)
    p = ask()
    assert len(p["data"]["ideas"]) == 7 and p["data"]["total"] == 25


def test_the_volumes_of_the_converting_terms_that_planner_did_not_suggest_are_asked_for_once():
    calls = []
    p = ask(calls=calls, research=planner_research(account={"data": {"terms": {"converting": [{"term": "all on 4 implants austin tx"}, {"term": "implant dentist near me"}, {"term": "Dental  Implants"}]}}}))
    hist = [b for path, b in calls if path.endswith("generateKeywordHistoricalMetrics")]
    assert len(hist) == 1 and hist[0]["keywords"] == ["all on 4 implants austin tx"]                  # the others are already ideas
    assert p["data"]["termMetrics"]["all on 4 implants austin tx"]["avg"] == 70 and p["data"]["termMetrics"]["all on 4 implants austin tx"]["lo"] is None


def test_when_nothing_converted_there_is_no_metrics_request_and_a_failure_of_it_is_only_noted():
    calls = []
    ask(calls=calls, research=planner_research(account={"data": {"terms": {"converting": []}}}))
    assert not [1 for path, _ in calls if path.endswith("generateKeywordHistoricalMetrics")]
    base = K.planner()

    def post(path, body):
        if path.endswith("generateKeywordHistoricalMetrics"):
            raise RuntimeError("boom")
        return base(path, body)
    p = ask(post=post)
    assert p["state"] == "partial" and "The searches a month of the converting search terms could not be read (boom)" in p["note"] and p["data"]["termMetrics"] == {} and p["data"]["total"] == 25


def test_planner_is_skipped_with_no_account_to_ask_through_or_nothing_to_start_from():
    assert kr.read_planner(K.clean(), planner_research(), post=K.planner(), planner_account=None)["state"] == "skipped"
    p = kr.read_planner(K.clean(services="", website="", account="1234567890"), {}, post=K.planner(), planner_account="1234567890")
    assert p["state"] == "skipped" and "nothing to start Keyword Planner from" in p["note"]


def test_the_requests_run_in_parallel_without_losing_or_repeating_an_idea():
    import threading
    seen, lock = set(), threading.Lock()
    base = K.planner()

    def post(path, body):
        with lock:
            seen.add(threading.get_ident())
        return base(path, body)
    p = kr.read_planner(K.clean(), planner_research(), post=post, planner_account="1234567890", workers=3)
    assert p["data"]["total"] == 25 and len({i["kw"] for i in p["data"]["ideas"]}) == 25


def test_the_page_is_not_sent_the_thousands_of_rows_only_the_build_needs():
    research = {"planner": rs.piece("planner", "ok", "n", {"ideas": [{"kw": "x"}] * 5, "termMetrics": {"a": 1}, "total": 5, "calls": [], "seeds": []}),
                "account": rs.piece("account", "ok", "n", {"keywords": [{"t": "x"}], "negatives": [{}], "lists": [{}], "keywordCount": 1, "campaigns": [{"name": "C"}]}),
                "keywords": rs.piece("keywords", "ok", "n", {"ideas": [{"text": "kept for the Campaign Book"}]})}
    pub = rs.public(research)
    assert pub["planner"]["data"] == {"total": 5, "calls": [], "seeds": []} and pub["account"]["data"] == {"keywordCount": 1, "campaigns": [{"name": "C"}]}
    assert pub["keywords"]["data"]["ideas"] == [{"text": "kept for the Campaign Book"}]
    assert "ideas" in research["planner"]["data"]                                                           # the saved piece is not touched


# ── what the mutation tests of this file found missing ─────────────────────

def test_an_account_with_exactly_the_most_keywords_is_not_called_cut(monkeypatch):
    monkeypatch.setattr(kr, "MAX_KEYWORD_ROWS", 3)
    rows = [{"t": "k%d" % i, "m": "EXACT", "c": "C", "g": "G"} for i in range(3)]
    p = kr.read_account(ACCOUNT, sqr_read=K.sqr_read(keywords=rows))
    assert p["state"] == "ok" and p["data"]["keywordsTruncated"] is False and len(p["data"]["keywords"]) == 3


def test_only_the_first_six_things_the_client_sells_are_asked_about():
    batches, _ = kr.seeds_for(K.clean(services="aa bb, cc dd, ee ff, gg hh, ii jj, kk ll, mm nn, oo pp"), {})
    assert [b["label"] for b in batches] == ["aa bb", "cc dd", "ee ff", "gg hh", "ii jj", "kk ll"]


def test_the_home_page_is_not_a_source_of_seeds_but_the_pages_after_it_are():
    batches, _ = kr.seeds_for(K.clean(services=""), planner_research())
    pages = next(b for b in batches if b["label"] == "the client's pages")["keywords"]
    assert "your austin family dentist" not in pages and "acme dental" not in pages and "dental implants in austin" in pages


def test_a_competitor_site_that_could_not_be_read_is_not_asked_about_even_if_a_page_came_back():
    research = planner_research(competitors={"data": {"competitors": [{"name": "Half", "ok": False, "pages": [{"url": "https://half.example/"}]}, {"name": "Whole", "ok": True, "pages": [{"url": "https://whole.example/"}]}]}})
    _, urls = kr.seeds_for(K.clean(), research)
    assert [u["label"] for u in urls] == ["site", "rival: Whole"]


def test_an_idea_with_exactly_the_floor_is_kept():
    p = ask(inputs=dict(K.clean(), minSearches=12))
    assert "dentist for kids" in [i["kw"] for i in p["data"]["ideas"]] and p["data"]["belowFloor"] == 1                  # 12 a month is kept; only 'tooth gem' (5) falls below
    p = ask(inputs=dict(K.clean(), minSearches=13))
    assert "dentist for kids" not in [i["kw"] for i in p["data"]["ideas"]]


def test_when_every_request_fails_the_note_is_the_failures_and_not_a_claim_of_no_ideas():
    def down(path, body):
        if path.endswith("geoTargetConstants:suggest"):
            return {}
        raise RuntimeError("DEVELOPER_TOKEN_NOT_APPROVED")
    p = ask(post=down)
    assert p["state"] == "failed" and "DEVELOPER_TOKEN_NOT_APPROVED" in p["note"] and "no ideas at or above" not in p["note"]


def test_the_converting_and_wasteful_terms_are_the_biggest_first_and_capped():
    terms = [{"t": "t%d" % i, "s": "", "c": "C", "ct": "SEARCH", "g": "G", "i": 10, "k": 5, "x": float(i), "v": 1.0 if i < 100 else 0.0} for i in range(200)]
    terms += [{"t": "cheap%d" % i, "s": "", "c": "C", "ct": "SEARCH", "g": "G", "i": 10, "k": 5, "x": 1000.0 + i, "v": 1.0} for i in range(3)]
    d = kr.read_account(ACCOUNT, sqr_read=K.sqr_read(terms=terms))["data"]
    conv = [t["term"] for t in d["terms"]["converting"]]
    assert len(conv) == 60 and conv[:3] == ["t0", "t1", "t2"] and "cheap0" not in conv[:3] and d["terms"]["wasteful"][0]["term"] == "t199"
    same = [{"t": "a", "s": "", "c": "C", "ct": "SEARCH", "g": "G", "i": 1, "k": 1, "x": 50.0, "v": 2.0}, {"t": "b", "s": "", "c": "C", "ct": "SEARCH", "g": "G", "i": 1, "k": 1, "x": 10.0, "v": 2.0}]
    assert [t["term"] for t in kr.read_account(ACCOUNT, sqr_read=K.sqr_read(terms=same))["data"]["terms"]["converting"]] == ["b", "a"]            # the same conversions: the cheaper first


# ── what the second round of mutation tests found missing ──────────────────

def test_a_seed_keeps_its_accents_and_other_scripts():
    batches, _ = kr.seeds_for(K.clean(services="dentiste montréal, 牙科 診所"), {})
    assert [b["keywords"][0] for b in batches] == ["dentiste montréal", "牙科 診所"]


def test_when_there_are_more_urls_than_requests_allowed_every_service_is_left_out_and_said_so(monkeypatch):
    monkeypatch.setattr(kr, "MAX_CALLS", 1)
    dropped = []
    batches, urls = kr.seeds_for(K.clean(services="aa bb, cc dd"), planner_research(), dropped)
    assert batches == [] and len(urls) == 3 and dropped[:2] == ["aa bb", "cc dd"] and "what already converts" in dropped


def test_a_place_that_is_found_and_one_that_is_not_are_both_said():
    base = K.planner()

    def post(path, body):
        if path.endswith("geoTargetConstants:suggest") and body["locationNames"]["names"][0] == "Nowhere":
            return {}
        return base(path, body)
    p = ask(inputs=K.clean(locations="Austin, TX; Nowhere"), post=post)
    assert p["data"]["geoNote"].endswith("Google did not find: Nowhere.") and p["data"]["geoNote"].startswith("Searches are counted in Austin")
