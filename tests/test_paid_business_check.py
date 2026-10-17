"""Business Research: what of the model's writing is kept (tracker/paid_business_check.py)."""
import copy

from tests import business_fakes as B, strategy_fakes as F
from tracker import paid_business_check as bck, paid_business_research as brs, paid_strategy_check as ck, paid_strategy_research as rs

EM, EN = chr(0x2014), chr(0x2013)


def research(rivals=True, reputation=True, social=True):
    r = {"website": {"state": "ok", "data": {"site": {"url": F.SITE}, "pages": [{"url": F.SITE, "title": "Acme", "text": "Free consultation for dental implants. Voted best dentist in Texas three years running"},
                                                                                  {"url": F.SITE + "services/dental-implants", "title": "Implants", "text": "from $1,900"}]}}}
    if rivals:
        r["competitors"] = {"state": "ok", "data": {"competitors": [{"name": "Smile Austin", "url": "https://smileaustin.com/", "ok": True}]}}
    if reputation:
        r["reputation"] = {"state": "ok", "data": {"ratings": [{"platform": "Google", "rating": "4.8", "count": "1,204", "url": ""}], "awards": ["Austin Chronicle Best Dentist 2024"]}}
    if social:
        r["social"] = {"state": "ok", "data": {"platforms": [{"platform": "instagram", "posts": 4}]}}
    return r


def core(raw=None, r=None):
    r = r or research()
    return bck.core(copy.deepcopy(raw if raw is not None else B.CORE), ck.norm(rs.corpus(r)), ck.norm(brs.corpus(r)), r)


def test_offerings_get_a_real_priority_and_a_page_on_the_clients_own_site():
    c = core()
    first, second = c["offerings"]
    assert first["priority"] == "high" and first["page"] == "https://www.acmedental.com/services/dental-implants" and first["pageSeen"] is True
    assert second["priority"] == "medium" and second["page"] == "" and second["pageSeen"] is False          # "urgent" is not a priority; another site is no page of the client's
    unseen = B.CORE["offerings"][0] | {"page": "/services/whitening"}
    assert core(dict(B.CORE, offerings=[unseen]))["offerings"][0]["page"] == "https://www.acmedental.com/services/whitening"
    assert core(dict(B.CORE, offerings=[unseen]))["offerings"][0]["pageSeen"] is False


def test_a_point_of_difference_is_proven_only_when_its_quote_is_on_a_page_or_was_found_outside():
    u = core()["usps"]
    assert (u[0]["strength"], u[0]["verified"]) == ("proven", True)
    assert (u[1]["strength"], u[1]["verified"]) == ("proven", True)           # "Voted best dentist in Texas" is on the fake page
    assert (u[2]["strength"], u[2]["verified"]) == ("generic", False)
    bad = dict(B.CORE, usps=[{"usp": "Best in the world", "why_it_matters": "Trust", "evidence": "Ranked best in the whole world by everyone", "source": "", "strength": "proven"}])
    got = core(bad)["usps"][0]
    assert got["strength"] == "claimed" and got["verified"] is False            # a proof nobody can find word for word is a claim
    outside = dict(B.CORE, usps=[{"usp": "Award", "why_it_matters": "Trust", "evidence": "Austin Chronicle Best Dentist 2024", "source": "", "strength": "proven"}])
    assert core(outside)["usps"][0]["strength"] == "proven"                     # what the reputation search found counts as outside evidence
    assert core(outside, research(reputation=False))["usps"][0]["strength"] == "claimed"
    odd = dict(B.CORE, usps=[dict(B.CORE["usps"][0], strength="Excellent")])
    assert core(odd)["usps"][0]["strength"] == "claimed"


def test_a_fact_is_on_a_page_only_when_its_quote_is_word_for_word_on_a_page_of_the_client():
    f = core()["facts"]
    assert [x["verified"] for x in f] == [True, False]
    outside_only = dict(B.CORE, facts=[{"fact": "An award", "quote": "Austin Chronicle Best Dentist 2024", "source": ""}])
    assert core(outside_only)["facts"][0]["verified"] is False                  # facts an ad may state come from the client's own pages only
    assert core(dict(B.CORE, facts=[{"fact": "short", "quote": "Free", "source": ""}]))["facts"][0]["verified"] is False


def test_text_is_plain_and_the_lists_are_limited():
    raw = dict(B.CORE, summary="<b>Acme</b> " + EM + " a dentist", assumptions=["a%d" % i for i in range(40)], snapshot=[{"item": "i%d" % i, "detail": "d"} for i in range(40)],
               positioning={"promise": "Your dentist " + EN + " near you", "tone": "Warm", "wordsUsed": ["w%d" % i for i in range(40)], "wordsToAvoid": "painless"})
    c = core(raw)
    assert c["summary"] == "Acme , a dentist".replace(" ,", ",") and len(c["snapshot"]) == 10 and len(c["assumptions"]) == 10
    assert c["positioning"]["promise"] == "Your dentist, near you" and len(c["positioning"]["wordsUsed"]) == 12 and c["positioning"]["wordsToAvoid"] == ["painless"]


def test_a_core_answer_of_the_wrong_shape_becomes_an_empty_one_not_an_error():
    for raw in (None, [], "text", {"offerings": "x", "usps": 3, "facts": {"a": 1}, "positioning": "x"}):
        c = bck.core(raw, "", "", {})
        assert c["offerings"] == [] and c["usps"] == [] and c["facts"] == [] and c["summary"] == "" and c["positioning"]["promise"] == ""


def test_audiences_and_the_rest_of_the_second_answer():
    a = bck.audience(copy.deepcopy(B.AUDIENCE))
    assert [x["priority"] for x in a["audiences"]] == ["high", "medium"]                      # "sometimes" is not a priority
    assert a["journey"][0]["stage"] == "Compare" and a["triggers"] == ["A broken tooth", "A wedding"] and a["objections"][0]["proof"] == "Financing page"
    assert a["voice"]["vocabulary"] == ["smile", "family"] and a["geography"]["areas"] == [{"area": "Round Rock", "note": "North of Austin"}] and a["seasonality"][0]["window"] == "January"
    empty = bck.audience("nonsense")
    assert empty["audiences"] == [] and empty["voice"] == {"style": "", "vocabulary": [], "avoid": []} and empty["geography"] == {"summary": "", "areas": []}


def impl(raw=None, r=None):
    return bck.implications(copy.deepcopy(raw if raw is not None else B.IMPLICATIONS), research() if r is None else r)


def test_what_it_means_keeps_what_the_research_supports():
    i = impl()
    assert i["advertiseFirst"][0]["page"] == "https://www.acmedental.com/services/dental-implants" and i["advertiseFirst"][0]["pageSeen"] is True
    assert i["competitive"]["summary"].startswith("Smile Austin") and i["reputation"]["strengths"] == ["Kind staff"] and i["social"][0]["insight"].startswith("Short reels")
    assert [q["urgency"] for q in i["questions"]] == ["Before launch", "First month", "First month"]      # "whenever" is not an urgency
    assert i["nextSteps"][0]["owner"] == "Client" and len(i["summary"]) == 8


def test_nothing_is_written_about_research_that_was_not_done():
    i = impl(r=research(rivals=False, reputation=False, social=False))
    assert i["competitive"] == {} and i["reputation"] == {} and i["social"] == []
    unread = research()
    unread["competitors"]["data"]["competitors"][0]["ok"] = False
    assert impl(r=unread)["competitive"] == {}                                               # a competitor whose site could not be read was not compared
    failed = research()
    failed["reputation"]["state"] = "failed"
    assert impl(r=failed)["reputation"] == {}
    assert impl(r={})["advertiseFirst"][0]["page"] == ""                                      # no site read means no page can be the client's own


def test_the_questions_are_limited_and_the_wrong_shape_is_empty():
    raw = dict(B.IMPLICATIONS, questions=[{"area": "A", "question": "q%d" % i, "why": "w", "urgency": "later"} for i in range(50)])
    assert len(impl(raw)["questions"]) == 30 and impl(raw)["questions"][0]["urgency"] == "Later"
    for bad in (None, "x", {"summary": "text", "questions": "no", "competitive": "x"}):
        i = bck.implications(bad, research())
        assert i["questions"] == [] and i["summary"] in ([], ["text"]) and i["competitive"] == {}


def test_the_claims_scan_covers_what_could_end_up_in_an_ad():
    c, a, i = core(), bck.audience(copy.deepcopy(B.AUDIENCE)), impl()
    items = bck.claim_items(c, a, i)
    assert items[0][0] == "Summary" and items[0][1].startswith("Acme Dental is a family") and ("USP", "Free implant consultation") in items and ("Proof", "4.9 rating from 312 reviews") in items
    assert ("Pricing of Dental implants", "From $1,900 per tooth") in items and ("Angle", "Free Implant Consultation in Austin") in items and ("Objection answer", "Financing from $89 a month") in items
    assert ("Pricing of Invisalign", "not shown") in items and len([x for x in items if x[0] == "USP"]) == 3
    assert bck.claim_items({}, {}, {}) == []


def test_where_a_point_was_found_is_kept_only_if_it_is_the_clients_page_or_a_page_the_search_read():
    r = research()
    r["reputation"]["data"]["sources"] = ["https://yelp.com/biz/acme-dental-austin"]
    raw = dict(B.CORE, usps=[dict(B.CORE["usps"][0], source=src) for src in ("https://evil.example/phish", "/services/dental-implants", "https://yelp.com/biz/acme-dental-austin", "javascript:alert(1)", "")],
               proof=[{"point": "p", "source": "https://evil.example/phish"}, {"point": "q", "source": F.SITE}])
    c = core(raw, r)
    assert [u["source"] for u in c["usps"]] == ["", "https://www.acmedental.com/services/dental-implants", "https://yelp.com/biz/acme-dental-austin", "", ""]
    assert [p["source"] for p in c["proof"]] == ["", "https://www.acmedental.com/"]


def test_a_point_backed_only_by_the_search_says_so_and_a_page_quote_wins():
    r = research()
    award = dict(B.CORE["usps"][0], evidence="Austin Chronicle Best Dentist 2024", strength="proven")
    page = dict(B.CORE["usps"][0], evidence="Free consultation for dental implants", strength="proven")
    a, p = core(dict(B.CORE, usps=[award, page]), r)["usps"]
    assert (a["verified"], a["viaSearch"], a["strength"]) == (False, True, "proven") and (p["verified"], p["viaSearch"], p["strength"]) == (True, False, "proven")
    nowhere = core(dict(B.CORE, usps=[dict(award, evidence="Voted the best in the universe by everyone")]), r)["usps"][0]
    assert (nowhere["verified"], nowhere["viaSearch"], nowhere["strength"]) == (False, False, "claimed")


def test_a_wordy_priority_strength_or_urgency_is_understood():
    assert [bck._pick(v, bck.PRIORITY, "medium") for v in ("High.", "high priority", "LOW", "Medium-high", "urgent", "", None)] == ["high", "high", "low", "medium", "medium", "medium", "medium"]
    assert [bck._urgency(v) for v in ("Before launch.", "before launch, please", "Launch week", "First month", "Later", "Next quarter", "whenever", "")] == [
        "Before launch", "Before launch", "Before launch", "First month", "Later", "Later", "First month", "First month"]


def test_a_question_with_no_area_is_kept_under_general():
    raw = dict(B.IMPLICATIONS, questions=[{"area": "", "question": "Who owns the Google Ads account?", "why": "Access", "urgency": "Before launch."}, {"area": "Budget", "question": "", "why": "x", "urgency": "later"}])
    q = bck.implications(raw, research())["questions"]
    assert q == [{"question": "Who owns the Google Ads account?", "area": "General", "why": "Access", "urgency": "Before launch"}]


def test_the_claims_scan_also_covers_facts_the_short_read_the_first_move_and_the_reputation_read():
    c, a = core(), bck.audience(copy.deepcopy(B.AUDIENCE))
    i = impl()
    i["summary"] = ["Best rated, 4.9 stars from 1,200 reviews"]
    i["advertiseFirst"][0]["why"], i["advertiseFirst"][0]["evidence"] = "Save 50% on implants", "Free whitening for all"
    i["reputation"]["summary"] = "Ranked number 1 in Texas"
    c["facts"][0]["fact"] = "Free whitening and 50% off"
    items = bck.claim_items(c, a, i)
    for item in (("In short", "Best rated, 4.9 stars from 1,200 reviews"), ("Advertise first", "Save 50% on implants"), ("Advertise first", "Free whitening for all"),
                 ("Reputation", "Ranked number 1 in Texas"), ("Fact", "Free whitening and 50% off")):
        assert item in items, item


def test_a_rating_page_the_search_found_is_a_good_source_and_a_wordy_strength_is_read_from_its_start():
    r = research()
    r["reputation"]["data"]["ratings"][0]["url"] = "https://www.google.com/maps/place/acme"
    raw = dict(B.CORE, usps=[dict(B.CORE["usps"][0], source="https://www.google.com/maps/place/acme")])
    assert core(raw, r)["usps"][0]["source"] == "https://www.google.com/maps/place/acme"
    assert bck._pick("not really high priority", bck.PRIORITY, "medium") == "medium" and bck._pick("High, not low", bck.PRIORITY, "medium") == "high"


def test_a_quote_on_a_page_is_not_also_called_a_search_finding():
    r = research()
    r["website"]["data"]["pages"][0]["text"] += " Austin Chronicle Best Dentist 2024"
    usp = dict(B.CORE["usps"][0], evidence="Austin Chronicle Best Dentist 2024", strength="proven")
    got = core(dict(B.CORE, usps=[usp]), r)["usps"][0]
    assert got["verified"] is True and got["viaSearch"] is False
