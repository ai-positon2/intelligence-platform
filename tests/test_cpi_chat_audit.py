"""The same audit the search filters got, applied to the chat.

The results grid was audited filter by filter and every one that Apollo treats as
a hint rather than a rule is now enforced in code. The chat asks Apollo the same
questions through a different door, and that door had none of it.

The headline fault, reproduced live against this Apollo account before writing a
line of this: the chat put an industry into q_keywords. Asking for CMOs with the
keyword "Healthcare" returned ten people whose employers were HealthCare Global,
CU Healthcare PayCard, Serenity Healthcare, Simplify Healthcare, Invo Healthcare,
Naru Healthcare, Malu Healthcare, Metropolis Healthcare and Bronson Healthcare.
Every single one was selected for having the word in its NAME. One is a payment
card vendor. Not one hospital, insurer, pharma or biotech company appeared, and
Apollo's count for that search, 295, was reported to the reader as the number of
healthcare CMOs.

Two more filters were not merely loose but silently absent: a question that said
"200 to 500 employees" or "over $10M revenue" had the size and the revenue dropped
by the intent parser and never applied at all, while the answer listed people as
though it had honored them.

And a list of people was never title-checked. The single-person path has verified
titles in code for a while, because presenting a Marketing Manager as the CMO
states something Apollo never said. "List the VPs of sales" got no such check, so
the same error could be printed five times.

What is deliberately NOT enforced matters just as much, and is pinned here too:
seniority, person location and email status are filtered by Apollo against fields
this plan never returns to us. Re-checking those in code would mean overruling a
real filter with less information than it had.
"""

import inspect
import json as _json
import os
import sys
import types

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import app as appmod  # noqa: E402


# ══ What counts as an employer constraint ════════════════════════════════════

def test_a_question_about_nobody_in_particular_constrains_no_employer():
    """The common case, and it has to stay empty: an empty dict here is what keeps
    an ordinary question free of any paid company call."""
    assert appmod._cpi_chat_employer_filters({}) == {}
    assert appmod._cpi_chat_employer_filters(
        {"titles": ["CMO"], "company_name": "Acme"}) == {}


def test_every_employer_constraint_a_question_can_carry_is_picked_up():
    got = appmod._cpi_chat_employer_filters({
        "industries": ["healthcare"], "technologies": ["Salesforce"],
        "company_locations": ["United States"],
        "employee_min": 200, "employee_max": 500,
        "revenue_min": 10000000, "revenue_max": None,
    })
    assert got == {"industries": ["healthcare"], "technologies": ["Salesforce"],
                   "locations": ["United States"], "employee_min": 200,
                   "employee_max": 500, "revenue_min": 10000000}


def test_the_hq_constraint_is_renamed_for_the_company_endpoint():
    """search_people calls it company_locations and search_companies calls it
    locations. Sending the people spelling to the company endpoint would drop the
    filter silently, which is the class of bug this whole file is about."""
    got = appmod._cpi_chat_employer_filters({"company_locations": ["Germany"]})
    assert "locations" in got and "company_locations" not in got


@pytest.mark.parametrize("raw,expected", [
    (200, 200), ("200", 200), (200.0, 200), (" 1,000 ", 1000),
    (None, None), ("", None), ("lots", None), (True, None), (False, None),
])
def test_a_size_bound_is_only_a_bound_when_it_is_really_a_number(raw, expected):
    """A model asked for an integer can return anything. Only a real number may
    become a constraint the answer then claims to have applied. A JSON true is
    rejected too, which matters because bool is an int in Python: "employee_min":
    true must not silently become a floor of 1."""
    assert appmod._cpi_int_or_none(raw) == expected
    if expected is None:
        assert appmod._cpi_int_or_none(raw) is None


def test_junk_in_a_list_does_not_become_a_constraint():
    got = appmod._cpi_chat_employer_filters(
        {"industries": ["", "  ", None, {"a": 1}, "pharma"]})
    assert got == {"industries": ["pharma"]}


def test_constraint_lists_are_capped():
    got = appmod._cpi_chat_employer_filters({"industries": ["i%d" % i for i in range(20)]})
    assert len(got["industries"]) == 6


# ══ Saying which constraints were applied ════════════════════════════════════

@pytest.mark.parametrize("lo,hi,words", [
    (200, 500, "200 to 500"), (1000, None, "1000 or more"),
    (None, 50, "up to 50"), (None, None, ""),
])
def test_a_range_reads_the_way_a_person_would_say_it(lo, hi, words):
    assert appmod._cpi_range_words(lo, hi) == words


def test_the_constraints_are_described_in_plain_words():
    note = appmod._cpi_constraint_note({
        "industries": ["healthcare"], "locations": ["United States"],
        "employee_min": 200, "employee_max": 500, "revenue_min": 10000000})
    assert note == {"industry": "healthcare", "headquarters": "United States",
                    "employees": "200 to 500", "annual revenue": "$10000000 or more"}


def test_rejections_are_labelled_and_the_biggest_reason_comes_first():
    note = appmod._cpi_reject_note({"hq": 2, "industry": 9, "employees": 0})
    assert list(note.items()) == [("outside the industry", 9),
                                  ("headquartered elsewhere", 2)]


# ══ Revenue, the filter the results grid was also not enforcing ══════════════
# Found by auditing the chat: the page has had Min/Max revenue inputs all along,
# they were sent to Apollo, and nothing checked the figure that came back.

@pytest.mark.parametrize("value,lo,hi,ok", [
    (5_000_000, 1_000_000, 10_000_000, True),
    (500_000, 1_000_000, None, False),
    (50_000_000, None, 10_000_000, False),
    (10_000_000, 10_000_000, 10_000_000, True),
    (None, 1_000_000, None, False),
    ("", 1_000_000, None, False),
    ("not a number", 1_000_000, None, False),
])
def test_a_revenue_figure_is_checked_against_the_range(value, lo, hi, ok):
    assert appmod._cpi_num_in_range(value, lo, hi) is ok


def test_a_company_outside_the_revenue_range_is_dropped():
    rows = [{"name": "Big", "annual_revenue": 50_000_000},
            {"name": "Small", "annual_revenue": 200_000}]
    kept, dropped, _unconfirmed = appmod._cpi_verify_rows(rows, {"revenue_min": 1_000_000}, False)
    assert [r["name"] for r in kept] == ["Big"]
    assert dropped == {"revenue": 1}


def test_a_persons_employer_is_checked_against_the_revenue_range_too():
    """Same check, the other row shape. Two row shapes reaching two different
    conclusions about the same employer is the bug _cpi_org_view exists to stop."""
    rows = [{"full_name": "A", "organization_revenue": 50_000_000},
            {"full_name": "B", "organization_revenue": 200_000}]
    kept, dropped, _unconfirmed = appmod._cpi_verify_rows(rows, {"revenue_min": 1_000_000}, True)
    assert [r["full_name"] for r in kept] == ["A"]
    assert dropped == {"revenue": 1}


def test_a_company_with_no_revenue_on_file_is_not_waved_through():
    """An unverifiable row is exactly the row that produced the original
    complaint. It fails the check rather than passing it."""
    kept, dropped, _unconfirmed = appmod._cpi_verify_rows(
        [{"name": "Unknown"}], {"revenue_min": 1_000_000}, False)
    assert kept == [] and dropped == {"revenue": 1}


def test_revenue_forces_the_employer_lookup_on_the_results_grid():
    """A revenue filter cannot be honored without the employer's own record, so
    asking for one turns the paid company lookup back on, exactly as an industry
    or a size filter does."""
    src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    block = src.split("needs_employer = [k for k in (")[1].split(")]")[0]
    for key in ("industries", "employee_min", "revenue_min", "revenue_max",
                "company_locations", "technologies",
                # technologies_all/exclude_technologies are strict on Apollo's
                # own side (verified live), so leaving them out never
                # produced a wrong result -- but without this, the tech-stack
                # badge on a row matching an ALL-of or NOT-using filter just
                # never rendered, since it comes from the same paid lookup as
                # "technologies" and nothing forced that lookup on for them.
                "technologies_all", "exclude_technologies"):
        assert '"%s"' % key in block, "%s does not force the lookup" % key


# ══ Which people belong in a list answer ═════════════════════════════════════

_VP_SALES = {"id": "p1", "full_name": "Vera Poole", "title": "VP of Sales"}
_CRO = {"id": "p2", "full_name": "Cara Roe", "title": "Chief Revenue Officer"}
_AE = {"id": "p3", "full_name": "Alan Eaton", "title": "Account Executive"}
_ENG = {"id": "p4", "full_name": "Ed Ng", "title": "VP of Engineering"}


def test_the_person_who_holds_the_title_is_kept():
    kept, dropped = appmod._cpi_verify_chat_people([_VP_SALES], ["VP of Sales"])
    assert kept == [_VP_SALES] and dropped == 0


def test_someone_in_the_same_function_is_a_legitimate_answer():
    """A loosely worded ask is expanded by the parser into several candidate
    titles, and the sales leader whose title is spelled differently is exactly who
    the reader wanted. Kept a little wider than the single-person check on
    purpose."""
    kept, _ = appmod._cpi_verify_chat_people([_CRO], ["VP of Sales"])
    assert kept == [_CRO]


def test_the_junior_person_apollo_threw_in_is_dropped_and_counted():
    """include_similar_titles is a recall net. An account executive is not a VP of
    sales, and listing them as one is the whole fault."""
    kept, dropped = appmod._cpi_verify_chat_people([_VP_SALES, _AE], ["VP of Sales"])
    assert kept == [_VP_SALES] and dropped == 1


def test_a_senior_person_from_another_function_is_dropped():
    kept, dropped = appmod._cpi_verify_chat_people([_VP_SALES, _ENG], ["VP of Sales"])
    assert kept == [_VP_SALES] and dropped == 1


def test_the_director_one_rung_down_is_still_part_of_the_answer():
    """Asking for the VP of Finance is asking about finance leadership. Excluding
    the finance director would answer a narrower question than the one asked."""
    director = {"id": "p5", "full_name": "Dana Ruiz", "title": "Finance Director"}
    kept, dropped = appmod._cpi_verify_chat_people([director], ["VP Finance"])
    assert kept == [director] and dropped == 0


def test_a_manager_is_not_leadership_unless_the_question_asked_for_one():
    """Same person, two questions, two correct answers. The bar is the level that
    was asked about, floored at director."""
    manager = {"id": "p6", "full_name": "Mo Kane", "title": "Sales Manager"}
    assert appmod._cpi_verify_chat_people([manager], ["VP of Sales"]) == ([], 1)
    assert appmod._cpi_verify_chat_people([manager], ["Sales Manager"]) == ([manager], 0)


def test_a_question_with_no_title_filters_nobody_out():
    """"List the leadership at Acme" asks by seniority, which Apollo really does
    filter. Nothing here may narrow it."""
    rows = [_VP_SALES, _AE, _ENG]
    kept, dropped = appmod._cpi_verify_chat_people(rows, [])
    assert kept == rows and dropped == 0


def test_the_callers_list_is_not_mutated():
    rows = [_VP_SALES, _AE]
    appmod._cpi_verify_chat_people(rows, ["VP of Sales"])
    assert rows == [_VP_SALES, _AE]


def test_an_unclassifiable_requested_title_still_requires_a_real_match():
    """Nothing places "Growth Hacker" in a function, so there is no function to
    fall back on and only a title match counts. Failing to an empty list is the
    safe direction: it routes to the honest "nobody holds that title" answer."""
    kept, dropped = appmod._cpi_verify_chat_people([_AE], ["Growth Hacker"])
    assert kept == [] and dropped == 1


# ══ End to end, through the real /chat route ═════════════════════════════════

_HEALTH_ORGS = [
    # Apollo's own classification is what settles this. The last two are the
    # reproduced fault: real companies with the word in their name that are not
    # in the industry.
    {"id": "o1", "name": "Bronson Health System", "primary_domain": "bronsonhealth.com",
     "industry": "hospital & health care", "estimated_num_employees": 300,
     "country": "United States", "annual_revenue": 90_000_000},
    {"id": "o2", "name": "Metropolis Labs", "primary_domain": "metropolisindia.com",
     "industry": "medical practice", "estimated_num_employees": 450,
     "country": "United States", "annual_revenue": 60_000_000},
    {"id": "o3", "name": "CU Healthcare PayCard", "primary_domain": "cuhealthcarepaycard.com",
     "industry": "financial services", "estimated_num_employees": 40,
     "country": "United States", "annual_revenue": 5_000_000},
    {"id": "o4", "name": "Simplify Healthcare", "primary_domain": "simplifyhealthcare.com",
     "industry": "computer software", "estimated_num_employees": 900,
     "country": "United States", "annual_revenue": 30_000_000},
]

_CMOS = [
    {"id": "c1", "full_name": "Lori Taylor", "title": "Chief Marketing Officer",
     "organization_name": "Bronson Health System"},
    {"id": "c2", "full_name": "Mohan Menon", "title": "Chief Marketing Officer",
     "organization_name": "Metropolis Labs"},
]


def _ask(monkeypatch, message, intent, orgs=None, people=None, total=None,
         companies_raise=False, probe=None):
    """Drives the real /chat route and hands back (body, facts, calls)."""
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(),
                        raising=False)
    base = {"intent": "people_list", "titles": [], "seniorities": [],
            "company_name": "", "max_results": 10}
    base.update(intent)
    monkeypatch.setattr(appmod, "_vimi_chat_json",
                        lambda oai, msgs, mt: (_json.dumps(base), "m"))
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: probe)
    monkeypatch.setattr(appmod, "_cpi_record_industries", lambda rows: None)
    calls: dict = {"companies": [], "people": [], "enriched": []}

    def _sc(filters, api_key, **kw):
        calls["companies"].append(dict(filters))
        if companies_raise:
            raise RuntimeError("apollo down")
        return list(_HEALTH_ORGS if orgs is None else orgs)

    def _sp(filters, api_key, **kw):
        calls["people"].append(dict(filters))
        if kw.get("meta") is not None and total is not None:
            kw["meta"]["total_entries"] = total
        return list(_CMOS if people is None else people)

    monkeypatch.setattr(ac, "search_companies", _sc)
    monkeypatch.setattr(ac, "search_people", _sp)
    monkeypatch.setattr(ac, "bulk_match_people",
                        lambda ids, api_key, **_kw: calls["enriched"].extend(ids) or {})
    box: dict = {}
    monkeypatch.setattr(appmod, "_cpi_grounded_answer",
                        lambda oai, facts, q, research="":
                        box.setdefault("facts", facts) and "answer")
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/b2b-agents/company-people-intelligence/chat",
               json={"message": message})
    assert r.status_code == 200
    return r.get_json(), box.get("facts", {}), calls


_INDUSTRY_ASK = {"intent": "people_list", "titles": ["CMO", "Chief Marketing Officer"],
                 "industries": ["healthcare"]}


def test_an_industry_question_establishes_the_companies_before_the_people(monkeypatch):
    """The fix for the reported fault. The industry is a constraint on the
    employer, so the employers are settled first and the people search is scoped
    to the ones that survive."""
    _body, _facts, calls = _ask(monkeypatch, "who are the CMOs of healthcare companies",
                                _INDUSTRY_ASK)
    assert len(calls["companies"]) == 1
    assert calls["companies"][0]["industries"] == ["healthcare"]
    assert calls["people"][0]["organization_ids"] == ["o1", "o2"]


def test_the_companies_that_only_have_the_word_in_their_name_are_excluded(monkeypatch):
    """Verified live: this is what q_keywords actually returned. A payment card
    vendor and a software company are not healthcare companies, whatever their
    names say."""
    _body, facts, calls = _ask(monkeypatch, "CMOs of healthcare companies", _INDUSTRY_ASK)
    scoped = calls["people"][0]["organization_ids"]
    assert "o3" not in scoped and "o4" not in scoped
    assert facts["companies_offered_by_the_search_but_rejected_on_checking"] == \
        {"outside the industry": 2}


def test_the_industry_is_never_sent_as_a_free_text_keyword_again(monkeypatch):
    """The exact regression. q_keywords matches company NAMES, which is how a
    healthcare search returned a payment card vendor."""
    _body, _facts, calls = _ask(monkeypatch, "CMOs of healthcare companies", _INDUSTRY_ASK)
    assert "healthcare" not in _json.dumps(calls["people"]).lower()


def test_the_answer_is_told_the_list_covers_only_verified_companies(monkeypatch):
    """A list drawn from 2 confirmed companies is not "the healthcare CMOs", and
    the answer must not be able to imply that it is."""
    _body, facts, _calls = _ask(monkeypatch, "CMOs of healthcare companies",
                                _INDUSTRY_ASK)
    scope = facts["people_were_searched_only_inside_these_companies"]
    assert scope["companies"] == 2
    assert scope["constraints_verified"] == {"industry": "healthcare"}
    assert scope["examples"] == ["Bronson Health System", "Metropolis Labs"]


def test_the_one_paid_call_is_reported_to_the_user(monkeypatch):
    """One company search, billed per call rather than per company, and the reply
    says so: this page has to keep every credit it spends visible."""
    body, _facts, calls = _ask(monkeypatch, "CMOs of healthcare companies",
                               _INDUSTRY_ASK)
    assert len(calls["companies"]) == 1
    assert body["credits"] == 1


def test_a_question_about_one_named_company_still_costs_nothing(monkeypatch):
    """The company is not in question there, so there is nothing to select and no
    reason to pay. Regressing this would put a credit on every chat question."""
    body, _facts, calls = _ask(monkeypatch, "who is the CMO of Acme",
                               {"intent": "person_at_company", "titles": ["CMO"],
                                "company_name": "Acme", "industries": ["healthcare"]},
                               probe={"id": "org-acme", "name": "Acme",
                                      "primary_domain": "acme.com"})
    assert calls["companies"] == []
    assert "credits" not in body


def test_no_company_matching_the_constraints_means_no_people_list_at_all(monkeypatch):
    """Listing people from companies that failed the checks would answer a
    question nobody asked."""
    _body, facts, calls = _ask(monkeypatch, "CMOs of healthcare companies",
                               _INDUSTRY_ASK,
                               orgs=[dict(_HEALTH_ORGS[2])])
    assert calls["people"] == []
    assert facts["no_companies_on_file_match_these_constraints"] == \
        {"industry": "healthcare"}
    assert facts["companies_offered_by_the_search_but_rejected_on_checking"] == \
        {"outside the industry": 1}


def test_a_company_we_cannot_search_inside_is_not_counted_as_scope(monkeypatch):
    """A matching company with no id cannot be scoped to, and an empty id list is
    dropped by the people search, which would turn a scoped question into a global
    one whose results get reported as the industry's. Same trap the
    organization_ids comment in the route describes."""
    _body, facts, calls = _ask(monkeypatch, "CMOs of healthcare companies",
                               _INDUSTRY_ASK,
                               orgs=[dict(_HEALTH_ORGS[0], id=None)])
    assert calls["people"] == []
    assert "no_companies_on_file_match_these_constraints" in facts


def test_an_empty_company_search_spends_nothing(monkeypatch):
    """mixed_companies/search bills per call that returns a row, and 0 for none."""
    body, _facts, _calls = _ask(monkeypatch, "CMOs of healthcare companies",
                                _INDUSTRY_ASK, orgs=[])
    assert "credits" not in body


def test_apollo_being_down_for_the_company_half_is_said_out_loud(monkeypatch):
    """The people search can still run, but it then answers a LOOSER question than
    the one asked. Presenting that list as the industry's CMOs would be the
    original fault with a better excuse."""
    _body, facts, calls = _ask(monkeypatch, "CMOs of healthcare companies",
                               _INDUSTRY_ASK, companies_raise=True)
    assert calls["people"], "the answer should still be attempted"
    assert facts["employer_constraints_could_not_be_applied"] == \
        {"industry": "healthcare"}
    assert "people_were_searched_only_inside_these_companies" not in facts


# ── The constraints that were being dropped on the floor ────────────────────

def test_a_size_band_in_the_question_is_actually_applied(monkeypatch):
    """"200 to 500 employees" was parsed by nobody and applied to nothing, while
    the answer listed people as though it had honored it."""
    _body, facts, calls = _ask(
        monkeypatch, "marketing leaders at companies with 200 to 500 employees",
        {"titles": ["CMO"], "employee_min": 200, "employee_max": 500})
    assert calls["companies"][0]["employee_min"] == 200
    # o3 has 40 and o4 has 900: Apollo answers size in overlapping buckets, so
    # both really do come back for this request.
    assert calls["people"][0]["organization_ids"] == ["o1", "o2"]
    assert facts["people_were_searched_only_inside_these_companies"][
        "constraints_verified"] == {"employees": "200 to 500"}


def test_a_revenue_band_in_the_question_is_actually_applied(monkeypatch):
    _body, _facts, calls = _ask(
        monkeypatch, "CMOs at companies over $50M revenue",
        {"titles": ["CMO"], "revenue_min": 50_000_000})
    assert calls["companies"][0]["revenue_min"] == 50_000_000
    assert calls["people"][0]["organization_ids"] == ["o1", "o2"]


def test_the_hq_filter_is_not_sent_twice(monkeypatch):
    """Which companies these are already guarantees the HQ. Sending Apollo's own
    fuzzy location match as well could only take verified companies back out."""
    _body, _facts, calls = _ask(monkeypatch, "CMOs at US healthcare companies",
                                dict(_INDUSTRY_ASK, company_locations=["United States"]))
    assert calls["companies"][0]["locations"] == ["United States"]
    assert "company_locations" not in calls["people"][0]


def test_the_intent_parser_is_asked_for_the_constraints_it_was_missing():
    """A contract test on the prompt, because the extraction and the enforcement
    are useless without each other: nothing downstream can apply a bound the
    parser was never asked to produce."""
    for key in ("employee_min", "employee_max", "revenue_min", "revenue_max",
                "technologies"):
        assert key in appmod._CPI_INTENT_SYSTEM, "%s is not asked for" % key


def test_the_parser_is_told_not_to_guess_the_vendors_industry_spelling():
    """It used to be told to emit "Apollo-style keyword tags (e.g. Healthcare)".
    Nothing in that taxonomy is spelled healthcare, so the guess was wrong AND it
    bypassed the mapping that gets it right."""
    assert "Apollo-style keyword tags" not in appmod._CPI_INTENT_SYSTEM
    assert "maps the plain word" in appmod._CPI_INTENT_SYSTEM


def test_a_plain_industry_word_reaches_the_taxonomy_that_understands_it(monkeypatch):
    """The parser says "healthcare", the code maps it onto Apollo's real values.
    Proven at the boundary the search actually uses, not by inspecting a prompt."""
    from tracker.apollo_taxonomy import expand
    wanted = expand(["healthcare"])
    assert appmod._cpi_verify_rows(
        [_HEALTH_ORGS[0]], {"industries": ["healthcare"]}, False)[0], \
        "hospital & health care should match a question about healthcare"
    assert "hospitalandhealthcare" in wanted


# ── Counts ──────────────────────────────────────────────────────────────────

def test_a_loose_search_total_is_never_presented_as_the_number_of_matches(monkeypatch):
    """295 was the headline of the reported answer. It was the size of a search
    that matched company names and similar titles, then got narrowed in code."""
    _body, facts, _calls = _ask(monkeypatch, "how many healthcare CMOs are there",
                                dict(_INDUSTRY_ASK, wants_count=True), total=295)
    assert facts["apollo_loose_match_total_is_only_an_upper_bound"] == 295
    assert "total_matching_count" not in facts
    assert facts["returned_count"] == 2


def test_a_count_nothing_narrowed_is_still_reported_as_a_count(monkeypatch):
    """The honest case must survive the fix: no title filter, no industry, nothing
    dropped, so Apollo's own total is the answer."""
    _body, facts, _calls = _ask(
        monkeypatch, "how many people work at these companies",
        {"seniorities": ["vp"]}, total=42,
        people=[dict(p, title="VP Marketing") for p in _CMOS])
    assert facts["total_matching_count"] == 42
    assert "apollo_loose_match_total_is_only_an_upper_bound" not in facts


def test_the_answer_prompt_knows_an_upper_bound_is_not_a_count():
    assert "apollo_loose_match_total_is_only_an_upper_bound" in appmod._CPI_ANSWER_SYSTEM
    assert "upper bound" in appmod._CPI_ANSWER_SYSTEM


# ── Titles, seniority and the filters we cannot check ───────────────────────

def test_a_list_answer_drops_the_people_who_hold_the_wrong_title(monkeypatch):
    _body, facts, _calls = _ask(
        monkeypatch, "list the VPs of sales", {"titles": ["VP of Sales"]},
        people=[_VP_SALES, _AE, _ENG])
    assert [p["full_name"] for p in facts["people"]] == ["Vera Poole"]
    assert facts["people_offered_but_rejected_on_checking_their_titles"] == 2


def test_seniority_is_deliberately_left_to_apollo(monkeypatch):
    """Apollo filters seniority against its own classification, and a free people
    row carries no seniority field for us to check it with. Overruling that filter
    from the title alone would drop a founder whose title says "CEO". This test
    exists to stop a future audit pass from "fixing" it."""
    rows = [{"id": "s1", "full_name": "Casey Ola", "title": "CEO"}]
    _body, facts, _calls = _ask(monkeypatch, "who are the founders",
                                {"seniorities": ["founder", "owner"]}, people=rows)
    assert [p["full_name"] for p in facts["people"]] == ["Casey Ola"]


def test_an_unverifiable_person_location_is_admitted_to(monkeypatch):
    """Apollo applies it; we cannot re-check it. An answer that sounds equally
    sure about the checked and the unchecked parts is the mismatch this audit is
    about."""
    _body, facts, _calls = _ask(monkeypatch, "CMOs in Germany",
                                {"titles": ["CMO"], "person_locations": ["Germany"]})
    assert facts["person_location_asked_for_but_not_independently_verified"] == "Germany"


def test_asking_for_emails_in_a_list_says_they_are_not_included(monkeypatch):
    """Nothing is enriched for a list, by design: a credit per person on a
    question that might be idle curiosity. Silently ignoring the ask is what makes
    it look broken."""
    _body, facts, _calls = _ask(monkeypatch, "CMOs of healthcare companies with emails",
                                dict(_INDUSTRY_ASK, wants_contact_info=True))
    assert facts["contact_details_are_not_included_and_need_enriching"] is True
    assert "email" not in _json.dumps(facts["people"]).lower()


def test_a_list_answer_still_offers_a_button_per_person(monkeypatch):
    body, _facts, _calls = _ask(monkeypatch, "CMOs of healthcare companies",
                                _INDUSTRY_ASK)
    assert [c["apollo_id"] for c in body["enrich"]] == ["c1", "c2"]


# ── The paid company records teach the picker ────────────────────────────────

def test_every_industry_seen_on_a_paid_record_is_recorded(monkeypatch):
    """The picker is seeded from a written-down copy of a taxonomy nothing
    enumerates, so a real value seen on a real record is worth more than the seed.
    The chat pays for these records now, and they were being thrown away."""
    seen: list = []
    monkeypatch.setattr(appmod, "_cpi_record_industries", lambda rows: seen.append(rows))
    spend: dict = {"credits": 0}
    import tracker.apollo_client as ac
    monkeypatch.setattr(ac, "search_companies",
                        lambda *a, **k: list(_HEALTH_ORGS))
    appmod._cpi_chat_company_scope({"industries": ["healthcare"]}, "k", spend)
    assert seen and [o["industry"] for o in seen[0]][:2] == \
        ["hospital & health care", "medical practice"]
    assert spend["credits"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# Round two: the chat's CLOSED-VOCABULARY filters.
#
# The first pass fixed what the chat's filters MEANT. This pass covers what they
# are allowed to SAY. The search page got pickers so a person cannot type a value
# Apollo has no such thing as; the chat asks a language model for those same
# strings and sent whatever came back.
#
# Measured against this account on the free people endpoint, person_titles=
# ["chief marketing officer"]:
#
#   person_seniorities ["c_suite"]              68,174 people
#                      ["C_Suite"]                   0   same word, capitalized
#                      ["C-Suite"]                   0   hyphen for underscore
#                      ["executive"]                 0   not a value Apollo has
#                      ["c_suite", "executive"] 68,174   one bad value is skipped
#
# So the filter is closed AND case sensitive, and only an all-bad list breaks:
# exactly what a model produces for "the executives at Acme". The result was a
# confident "nobody matches" for a question Apollo was never really asked.
# ══════════════════════════════════════════════════════════════════════════════

def _answer_prompt():
    """The whole _CPI_ANSWER_SYSTEM literal. Sliced to the next module-level name
    rather than a fixed character count, which silently truncated as the prompt
    grew and made a rule look absent when it was simply past the window."""
    src = inspect.getsource(appmod)
    start = src.index("_CPI_ANSWER_SYSTEM = (")
    return src[start:src.index("\n_CPI_", start + 1)]


@pytest.mark.parametrize("asked,expected", [
    # The three spellings a model actually produces for the top level.
    (["C-Suite"], ["c_suite"]),
    (["C_Suite"], ["c_suite"]),
    (["executive"], ["c_suite"]),
    (["executives"], ["c_suite"]),
    (["Executives"], ["c_suite"]),
    (["c-level"], ["c_suite"]),
    (["CXO"], ["c_suite"]),
    # And the rest of the vocabulary, spelled loosely.
    (["Vice President"], ["vp"]),
    (["VPs"], ["vp"]),
    (["SVP"], ["vp"]),
    (["Founders"], ["founder"]),
    (["co-founder"], ["founder"]),
    (["Owners"], ["owner"]),
    (["Managers"], ["manager"]),
    (["Directors"], ["director"]),
    (["interns"], ["intern"]),
    (["entry level"], ["entry"]),
    # Already correct, and unchanged.
    (["c_suite", "vp", "director"], ["c_suite", "vp", "director"]),
    # De-duplicated: two spellings of one level are one filter.
    (["C-Suite", "executive", "c_suite"], ["c_suite"]),
])
def test_a_seniority_is_normalized_onto_apollos_own_nine_values(asked, expected):
    kept, dropped = appmod._cpi_clean_seniorities(asked)
    assert kept == expected
    assert dropped == []


def test_a_seniority_apollo_has_no_such_value_for_is_dropped_and_reported():
    """Dropped rather than sent, because Apollo returns zero people for an
    all-unrecognized list, and reported rather than swallowed, because the answer
    would otherwise be narrower than the reader thinks."""
    kept, dropped = appmod._cpi_clean_seniorities(["board member", "wizard"])
    assert kept == []
    assert dropped == ["board member", "wizard"]


def test_every_value_the_normalizer_produces_is_one_apollo_accepts():
    """The whole point. Anything this function emits goes straight to Apollo, and
    Apollo answers an unrecognized value with silence, not an error."""
    every = (list(appmod._CPI_APOLLO_SENIORITIES) +
             list(appmod._CPI_SENIORITY_ALIASES) +
             ["C-Suite", "Executives", "Vice President", "junior", "IC"])
    kept, _dropped = appmod._cpi_clean_seniorities(every)
    for value in kept:
        assert value in appmod._CPI_APOLLO_SENIORITIES, value


def test_the_alias_table_never_points_at_a_value_apollo_does_not_have():
    for word, target in appmod._CPI_SENIORITY_ALIASES.items():
        assert target in appmod._CPI_APOLLO_SENIORITIES, (word, target)


def test_the_nine_values_are_exactly_apollos_documented_set():
    """Written down so a future edit cannot quietly add a tenth that returns
    nothing, or drop one that works."""
    assert set(appmod._CPI_APOLLO_SENIORITIES) == {
        "owner", "founder", "c_suite", "vp", "director", "manager", "senior",
        "entry", "intern"}


def test_a_question_about_executives_reaches_apollo_as_c_suite(monkeypatch):
    """End to end through the real route: the word the model produced for
    "executives" used to go to Apollo verbatim and match nobody."""
    _body, facts, calls = _ask(
        monkeypatch, "who are the executives at Acme",
        {"intent": "people_list", "titles": [], "seniorities": ["executive"],
         "company_name": ""})
    assert calls["people"][0]["seniorities"] == ["c_suite"]
    assert "seniority_words_apollo_does_not_have_so_they_were_ignored" not in facts


def test_an_unusable_seniority_is_named_in_the_answers_facts(monkeypatch):
    """So an empty or over-broad answer can say which part of the question was not
    applied, instead of implying all of it was."""
    _body, facts, calls = _ask(
        monkeypatch, "list the board members at healthcare companies",
        {"intent": "people_list", "titles": [], "seniorities": ["board member"],
         "industries": ["healthcare"]})
    assert "seniorities" not in calls["people"][0] or \
        calls["people"][0]["seniorities"] == []
    assert facts["seniority_words_apollo_does_not_have_so_they_were_ignored"] == \
        "board member"


def test_the_answer_prompt_is_told_what_an_ignored_seniority_means():
    block = _answer_prompt()
    assert "seniority_words_apollo_does_not_have_so_they_were_ignored" in block
    assert "nobody" in block


# ── Classification codes in a question ────────────────────────────────────────

def test_a_quoted_naics_code_is_applied_for_free(monkeypatch):
    """NAICS and SIC exist on the PEOPLE endpoint and Apollo enforces both exactly,
    so a coded question needs no paid company lookup and no verification pass."""
    _body, facts, calls = _ask(
        monkeypatch, "CMOs at companies in NAICS 5415",
        {"intent": "people_list", "titles": ["CMO"], "naics_codes": ["5415"]})
    assert calls["companies"] == [], "a code question must not spend a credit"
    assert calls["people"][0]["naics_codes"] == ["5415"]
    assert facts["codes_applied_by_apollo_directly"] == {"NAICS": "5415"}


def test_a_six_digit_code_in_a_question_is_refused_with_the_rule(monkeypatch):
    """Real NAICS codes are 6 digits and Apollo takes 2 to 5, so a question quoting
    an official code was answered with an empty page and no explanation."""
    _body, facts, calls = _ask(
        monkeypatch, "CMOs at companies in NAICS 541511",
        {"intent": "people_list", "titles": ["CMO"], "naics_codes": ["541511"]})
    assert "naics_codes" not in calls["people"][0]
    note = facts["codes_that_are_not_a_valid_length_so_they_were_ignored"]
    assert note["naics"]["codes"] == "541511"
    assert "54151" in note["naics"]["rule"]


def test_a_mixed_bag_of_codes_keeps_the_good_ones(monkeypatch):
    _body, facts, calls = _ask(
        monkeypatch, "CMOs in NAICS 5415 and 541511",
        {"intent": "people_list", "titles": ["CMO"],
         "naics_codes": ["5415", "541511"], "sic_codes": ["7372"]})
    assert calls["people"][0]["naics_codes"] == ["5415"]
    assert calls["people"][0]["sic_codes"] == ["7372"]
    assert facts["codes_that_are_not_a_valid_length_so_they_were_ignored"][
        "naics"]["codes"] == "541511"


def test_the_parser_is_told_never_to_invent_a_code_from_an_industry_name():
    """A guessed code is worse than none: it is a precise-looking filter for the
    wrong industry, and the industry path already maps plain words properly."""
    src = inspect.getsource(appmod)
    block = src[src.index("_CPI_INTENT_SYSTEM"):src.index("_CPI_RESEARCH_SYSTEM")]
    assert "naics_codes" in block and "sic_codes" in block
    assert "Never derive a code from an industry name" in block


# ── Values that may be the cause of an empty answer ───────────────────────────

def test_an_unrecognized_technology_is_flagged_rather_than_read_as_a_finding(monkeypatch):
    """A model writing "SFDC" produced "no companies match", which reads as nobody
    using Salesforce. Measured: an invented technology uid returns 0 people, which
    is indistinguishable from a real one nobody uses."""
    _body, facts, _calls = _ask(
        monkeypatch, "CMOs at companies using SFDC",
        {"intent": "people_list", "titles": ["CMO"], "technologies": ["SFDC"]},
        orgs=[])
    assert "no_companies_on_file_match_these_constraints" in facts
    odd = facts["these_values_could_not_be_confirmed_as_ones_apollo_uses"]
    assert odd["technology"] == "SFDC"


def test_a_real_technology_that_simply_matched_nothing_is_not_blamed(monkeypatch):
    """The distinction has to cut both ways, or the note becomes noise attached to
    every empty result."""
    _body, facts, _calls = _ask(
        monkeypatch, "CMOs at companies using Marketo",
        {"intent": "people_list", "titles": ["CMO"], "technologies": ["Marketo"]},
        orgs=[])
    assert "no_companies_on_file_match_these_constraints" in facts
    assert "these_values_could_not_be_confirmed_as_ones_apollo_uses" not in facts


def test_an_invented_place_is_flagged_too(monkeypatch):
    _body, facts, _calls = _ask(
        monkeypatch, "CMOs at companies in Zzyzxville",
        {"intent": "people_list", "titles": ["CMO"],
         "company_locations": ["Zzyzxville"]}, orgs=[])
    odd = facts["these_values_could_not_be_confirmed_as_ones_apollo_uses"]
    assert odd["headquarters"] == "Zzyzxville"


def test_a_real_place_is_not_flagged(monkeypatch):
    _body, facts, _calls = _ask(
        monkeypatch, "CMOs at companies in Texas",
        {"intent": "people_list", "titles": ["CMO"],
         "company_locations": ["Texas"]}, orgs=[])
    assert "these_values_could_not_be_confirmed_as_ones_apollo_uses" not in facts


def test_the_flag_is_only_raised_when_nothing_matched(monkeypatch):
    """It explains an empty result. Attached to a good answer it would cast doubt
    on rows that are perfectly fine."""
    _body, facts, _calls = _ask(
        monkeypatch, "CMOs at healthcare companies using SFDC",
        {"intent": "people_list", "titles": ["CMO", "Chief Marketing Officer"],
         "industries": ["healthcare"]})
    assert "these_values_could_not_be_confirmed_as_ones_apollo_uses" not in facts


def test_the_answer_prompt_will_not_state_the_flag_as_proof_either_way():
    """The seed lists are not exhaustive, so absence is a hint. The wording has to
    stay on the right side of that."""
    block = _answer_prompt()
    assert "these_values_could_not_be_confirmed_as_ones_apollo_uses" in block
    assert "not exhaustive" in block
