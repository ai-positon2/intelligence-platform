"""What the CURRENT question is about, when the conversation has moved on.

Reported: "Tell me about Snowflake" answered correctly, and the very next
question, "List VPs of Sales at healthcare companies in Texas", answered about
Snowflake again: "no matching people for VP of Sales at Snowflake ... nobody on
file in sales for that company."

Two independent routes led there, and both are closed:

  1. The client pins the resolved company and re-sends it every turn, so
     follow-ups like "and their VP of Sales?" do not have to name it again. A
     pinned company also SUPPRESSES the employer filters outright (see the
     `employer = {} if resolved_org` note in cpi_chat), so the industry and the
     state were dropped and the search became "sales people inside Snowflake".
  2. The intent parser reads the conversation history, so it can lift a company
     out of an earlier turn even when the latest message never names one.

The fix is one rule applied in Python, not a politer request to the model: a
company carried in from anywhere other than the latest message is not this
turn's subject when the latest message describes companies by ATTRIBUTE. An
industry, a place, a size, a revenue band or a classification code describes a
population; nobody asks for healthcare companies in Texas and means the one
company they were reading about a moment ago.

The pin is also cleared on the client, which the reported transcript needed on
its own: the server dropping a company for one answer while the client hands it
straight back on the next question just moves the bug one turn later.

Two cases must keep working, and are pinned here because they are what the pin
is FOR: a follow-up that points back at the company without naming it ("do they
have a VP of Sales?"), and a question that names the company itself.
"""

import json as _json
import os
import sys
import types

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

_URL = "/p2/b2b-agents/company-people-intelligence/chat"

# The reported pair of questions, verbatim.
_Q1 = "Tell me about Snowflake"
_Q2 = "List VPs of Sales at healthcare companies in Texas"

# What the parser returns for _Q2 once it is behaving: an industry and a place,
# and no company at all.
_POPULATION_INTENT = {
    "intent": "people_list", "titles": ["VP of Sales", "Vice President of Sales"],
    "seniorities": ["vp"], "company_name": "", "industries": ["healthcare"],
    "company_locations": ["Texas"], "max_results": 10,
}

# The other half of the same bug: the parser lifting Snowflake out of the
# history even though _Q2 never mentions it.
_POPULATION_INTENT_WITH_CARRIED_COMPANY = dict(_POPULATION_INTENT,
                                               company_name="Snowflake")

_SNOWFLAKE_PIN = {"context_org_id": "org-snow", "context_name": "Snowflake",
                  "context_domain": "snowflake.com"}


class _Calls:
    """Everything the route reached for while answering one question."""

    def __init__(self):
        self.people_filters = []
        self.employers = []
        self.resolved_names = []      # a company name that was looked up at all


@pytest.fixture
def calls(monkeypatch):
    """The chat route with every outside call captured rather than made.

    Company resolution is captured too, not stubbed out silently: a question
    about a population must not pay to resolve a company nobody asked about, and
    that is only observable by watching for the call.
    """
    import tracker.apollo_client as ac
    seen = _Calls()

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(),
                        raising=False)

    def _people(filters, key, **kw):
        seen.people_filters.append(dict(filters))
        return [{"id": "p1", "full_name": "Dana Reyes", "title": "VP of Sales",
                 "organization_name": "Baylor Health", "organization_domain": "bswhealth.org"}]

    def _scope(employer, api_key, spend):
        seen.employers.append(dict(employer))
        return ([{"id": "org-baylor", "name": "Baylor Health",
                  "primary_domain": "bswhealth.org"}], {})

    def _probe(name, key, *a, **kw):
        seen.resolved_names.append(name)
        return {"id": "org-snow", "name": "Snowflake", "primary_domain": "snowflake.com"}

    def _resolve(name, key, **kw):
        seen.resolved_names.append(name)
        return ({"id": "org-snow", "name": "Snowflake",
                 "primary_domain": "snowflake.com"}, [])

    monkeypatch.setattr(ac, "search_people", _people)
    monkeypatch.setattr(ac, "search_companies", lambda *a, **k: [])
    monkeypatch.setattr(appmod, "_cpi_chat_company_scope", _scope)
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", _probe)
    monkeypatch.setattr(appmod, "_cpi_resolve_company", _resolve)
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    monkeypatch.setattr(appmod, "_cpi_grounded_answer",
                        lambda oai, facts, q, research="": "Here are the people.")
    monkeypatch.setattr(appmod, "_cpi_history_save",
                        lambda **kw: 1)      # no Postgres in the test environment
    return seen


def _ask(monkeypatch, message, intent, **body):
    monkeypatch.setattr(appmod, "_vimi_chat_json",
                        lambda oai, msgs, mt: (_json.dumps(intent), "m"))
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    payload = {"message": message}
    payload.update(body)
    r = c.post(_URL, json=payload)
    assert r.status_code == 200
    return r.get_json()


def _scoped_org_ids(calls):
    out = []
    for f in calls.people_filters:
        out.extend(f.get("organization_ids") or [])
    return out


# ── The reported bug ─────────────────────────────────────────────────────────

def test_a_population_question_is_not_answered_about_the_pinned_company(monkeypatch, calls):
    """The exact reported transcript: Snowflake pinned, then a question about
    healthcare companies in Texas."""
    _ask(monkeypatch, _Q2, _POPULATION_INTENT, **_SNOWFLAKE_PIN)
    assert "org-snow" not in _scoped_org_ids(calls), (
        "the people search was scoped to the company from the previous question")


def test_the_industry_and_the_state_are_actually_applied(monkeypatch, calls):
    """The other half of the same failure. A pinned company does not merely add
    itself, it suppresses the employer filters, so the question the user asked
    was never run at all."""
    _ask(monkeypatch, _Q2, _POPULATION_INTENT, **_SNOWFLAKE_PIN)
    assert calls.employers, "no company search ran, so the industry was dropped"
    assert calls.employers[0].get("industries") == ["healthcare"]
    assert calls.employers[0].get("locations") == ["Texas"]


def test_the_companies_that_do_match_are_the_ones_searched(monkeypatch, calls):
    _ask(monkeypatch, _Q2, _POPULATION_INTENT, **_SNOWFLAKE_PIN)
    assert _scoped_org_ids(calls) == ["org-baylor"]


def test_a_company_the_parser_lifted_out_of_the_history_is_dropped_too(monkeypatch, calls):
    """The second route in, with no pin involved: the parser reads the
    conversation history and can return a company the latest message never
    mentions."""
    _ask(monkeypatch, _Q2, _POPULATION_INTENT_WITH_CARRIED_COMPANY)
    assert calls.resolved_names == [], (
        "a company nobody asked about in this turn was looked up: %s"
        % calls.resolved_names)
    assert "org-snow" not in _scoped_org_ids(calls)
    assert calls.employers, "the industry was dropped in favour of the carried company"


def test_the_client_is_told_to_forget_the_company(monkeypatch, calls):
    """The client re-sends the pin every turn, so dropping it server-side for one
    answer would only delay the bug by a question."""
    d = _ask(monkeypatch, _Q2, _POPULATION_INTENT, **_SNOWFLAKE_PIN)
    assert d.get("clear_context") is True


def test_dropping_a_carried_company_costs_no_credits(monkeypatch, calls):
    """Resolving a company is a paid call. The population question must not pay
    to identify a company it is not about."""
    d = _ask(monkeypatch, _Q2, _POPULATION_INTENT_WITH_CARRIED_COMPANY)
    assert "credits" not in d or d["credits"] == 0


# ── What the pin is for, and must keep doing ─────────────────────────────────

def test_a_follow_up_about_the_same_company_still_inherits_it(monkeypatch, calls):
    """No population words at all: this is the case the pin exists for."""
    _ask(monkeypatch, "and their VP of Sales?", {
        "intent": "people_list", "titles": ["VP of Sales"], "company_name": "",
        "max_results": 10}, **_SNOWFLAKE_PIN)
    assert _scoped_org_ids(calls) == ["org-snow"]


def test_a_back_reference_survives_even_next_to_an_industry_word(monkeypatch, calls):
    """"do they have a VP of Sales for healthcare?" is still about the pinned
    company. The message points back at it in so many words, so the industry is
    not evidence that the subject changed."""
    _ask(monkeypatch, "do they have a VP of Sales for healthcare?", dict(
        _POPULATION_INTENT, company_locations=[]), **_SNOWFLAKE_PIN)
    assert _scoped_org_ids(calls) == ["org-snow"]


def test_naming_the_company_keeps_it_even_with_an_industry_attached(monkeypatch, calls):
    """The parser is told not to put an industry on a question about one named
    company, but if it does, the name the user actually typed wins."""
    _ask(monkeypatch, "list VPs of Sales at Snowflake in healthcare",
         _POPULATION_INTENT_WITH_CARRIED_COMPANY, **_SNOWFLAKE_PIN)
    assert _scoped_org_ids(calls) == ["org-snow"]


def test_a_placeholder_there_is_not_a_reference_to_the_pinned_company(monkeypatch, calls):
    """"are there any..." is a population question phrased with a bare "there".
    Counting that as a back-reference would put Snowflake back on the search
    through the same door the fix just closed."""
    _ask(monkeypatch, "are there any healthcare companies in Texas hiring VPs of Sales?",
         _POPULATION_INTENT, **_SNOWFLAKE_PIN)
    assert "org-snow" not in _scoped_org_ids(calls)
    assert calls.employers, "the industry was dropped in favour of the pinned company"


def test_an_ordinary_answer_does_not_tell_the_client_to_forget_anything(monkeypatch, calls):
    d = _ask(monkeypatch, "and their VP of Sales?", {
        "intent": "people_list", "titles": ["VP of Sales"], "company_name": "",
        "max_results": 10}, **_SNOWFLAKE_PIN)
    assert "clear_context" not in d


def test_a_person_at_company_question_is_untouched(monkeypatch, calls):
    """No pin, no population: the ordinary first question about a company."""
    _ask(monkeypatch, "who is the CMO of Snowflake", {
        "intent": "person_at_company", "titles": ["CMO"],
        "company_name": "Snowflake", "max_results": 10})
    assert calls.resolved_names == ["Snowflake"]


# ── Which questions describe a population ───────────────────────────────────

@pytest.mark.parametrize("intent", [
    {"industries": ["healthcare"]},
    {"technologies": ["Salesforce"]},
    {"company_locations": ["Texas"]},
    {"employee_min": 200},
    {"employee_max": 500},
    {"revenue_min": 10000000},
    {"naics_codes": ["5415"]},
    {"sic_codes": ["7372"]},
])
def test_these_asks_describe_a_set_of_companies(intent):
    assert appmod._cpi_chat_asks_about_a_population(intent) is True


@pytest.mark.parametrize("intent", [
    {},
    {"titles": ["VP of Sales"]},
    {"seniorities": ["vp"]},
    {"keywords": "sales"},
    {"wants_contact_info": True},
    # Where the PEOPLE are, not which companies employ them: "any of them in
    # Texas?" is a legitimate follow-up about the company already pinned.
    {"person_locations": ["Texas"]},
    # Nonsense values are not constraints. A code Apollo cannot accept is
    # rejected before it becomes a filter, so it must not unpin a company
    # either.
    {"naics_codes": ["nonsense"]},
    {"sic_codes": ["7"]},
])
def test_these_asks_do_not_describe_a_set_of_companies(intent):
    assert appmod._cpi_chat_asks_about_a_population(intent) is False


# ── Pointing back at the company, or not ────────────────────────────────────

@pytest.mark.parametrize("message", [
    "do they have a VP of Sales for healthcare?",
    "is it a healthcare company?",
    "who else works there in sales?",
    "what does the company do in healthcare?",
    "list their VPs of Sales in Texas",
])
def test_these_messages_point_back_at_the_pinned_company(message):
    assert appmod._CPI_CHAT_BACKREF.search(message)


@pytest.mark.parametrize("message", [
    _Q2,
    # English uses a bare "there" as a placeholder. Reading it as a reference
    # would put the previous company back on exactly the question this guard
    # exists to let through.
    "are there any healthcare companies in Texas hiring VPs of Sales?",
    "is there a list of fintech companies over 500 employees?",
    "VPs of Sales at companies using Salesforce",
])
def test_these_messages_do_not(message):
    assert appmod._CPI_CHAT_BACKREF.search(message) is None


# ── Reading a company name out of the message ───────────────────────────────

def test_the_first_question_names_its_company():
    assert appmod._cpi_chat_names_company(_Q1, "Snowflake") is True


def test_the_second_question_does_not():
    assert appmod._cpi_chat_names_company(_Q2, "Snowflake") is False


def test_a_corrected_spelling_still_counts_as_naming_it():
    """The parser rewrites what the user typed, so the name being compared is
    often not the string in the message. What they typed is compared too."""
    assert appmod._cpi_chat_names_company("cmo of snowflke", "Snowflake",
                                          "snowflke") is True


def test_an_expanded_abbreviation_still_counts_as_naming_it():
    assert appmod._cpi_chat_names_company("who runs sales at MSFT",
                                          "Microsoft", "MSFT") is True


@pytest.mark.parametrize("message,name", [
    # A short company name lives inside ordinary English words. A substring test
    # finds "ion" in "consolidation" and "apple" in "grappling", and concludes
    # the user named a company they never mentioned, which would pin the wrong
    # subject on exactly the population questions this guard exists for.
    ("list VPs of Sales at companies doing consolidation", "Ion"),
    ("who owns metadata governance", "Meta"),
])
def test_a_name_is_matched_as_words_not_as_a_substring(message, name):
    assert appmod._cpi_chat_names_company(message, name) is False


def test_a_multi_word_name_has_to_appear_in_order():
    assert appmod._cpi_chat_names_company("about health baylor", "Baylor Health") is False
    assert appmod._cpi_chat_names_company("about baylor health", "Baylor Health") is True


def test_a_stylized_name_still_matches():
    """Apollo stores Position2 as "Position²". _cpi_norm_name folds that, and
    this rides on the same normalization rather than a second copy of the rule."""
    assert appmod._cpi_chat_names_company("tell me about position2", "Position²") is True


def test_a_legal_suffix_does_not_have_to_be_typed():
    assert appmod._cpi_chat_names_company("about tealium", "Tealium, Inc.") is True


def test_an_empty_name_never_matches():
    assert appmod._cpi_chat_names_company(_Q2, "") is False
    assert appmod._cpi_chat_names_company("", "Snowflake") is False
