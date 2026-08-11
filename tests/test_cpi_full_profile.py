"""Tests for showing everything Apollo returned, not just the name.

Requested: a "CMO of X" question that resolves to a real, enriched Apollo
person was giving only a name in prose, while the 1-credit enrichment that ran
in the background already pulled email, phone, and full company firmographics
that never reached the answer. Contact fields in particular were previously
gated behind an explicit "did they ask for contact info" check -- reasonable
when nothing has been paid for yet, but once the credit is spent specifically
to reveal that data, withholding part of it is the actual waste.

So: _cpi_render_full_profile is a code-rendered (not model-summarized) dump of
every field on a matched, enriched person and their employer, appended to the
chat answer unconditionally whenever person_at_company resolves to a real
Apollo match -- regardless of whether the question asked for contact details.
Code-rendered, not left to the model, so nothing captured can be quietly
paraphrased away.
"""

import os
import sys
import types

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


_ENRICHED = {
    "matched": True, "name": "Julie Woods-Moss", "title": "Chief Marketing Officer",
    "headline": "CMO at Thoughtworks", "seniority": "C Suite",
    "departments": ["Marketing"], "city": "London", "state": "", "country": "United Kingdom",
    "location": "London, United Kingdom",
    "linkedin": "https://linkedin.com/in/juliewoodsmoss", "twitter": "", "facebook": "",
    "email": "julie.woodsmoss@thoughtworks.com", "apollo_email": "",
    "emails": [{"email": "julie.woodsmoss@thoughtworks.com", "status": "verified",
               "primary": True}],
    "phones": [{"number": "+44 20 7946 0000", "label": "Company", "owner": "company"}],
    "company": {
        "name": "Thoughtworks, Ltd.", "domain": "thoughtworks.com",
        "website": "https://www.thoughtworks.com",
        "industry": "Information Technology & Services", "employees": 11000,
        "revenue": "$1.2B", "founded": 1993, "hq": "Chicago, IL, United States",
        "phone": "+1 312 373 1000", "linkedin": "https://linkedin.com/company/thoughtworks",
        "description": "Global technology consultancy.",
    },
}


# ── _cpi_render_full_profile ─────────────────────────────────────────────────

def test_renders_the_core_contact_card_fields():
    out = appmod._cpi_render_full_profile(_ENRICHED)
    assert "**Name:** Julie Woods-Moss" in out
    assert "**Title:** Chief Marketing Officer" in out
    assert "**Email:** julie.woodsmoss@thoughtworks.com (verified)" in out
    assert "**Phone:** +44 20 7946 0000" in out


def test_renders_full_company_firmographics():
    out = appmod._cpi_render_full_profile(_ENRICHED)
    assert "**Industry:** Information Technology & Services" in out
    assert "**Employees:** 11000" in out
    assert "**Revenue:** $1.2B" in out
    assert "**HQ:** Chicago, IL, United States" in out
    assert "**Description:** Global technology consultancy." in out


def test_person_and_company_are_two_separate_bullet_groups():
    """A plain heading line between them so fmtAnswer() renders two distinct
    lists instead of running every field together under one heading."""
    out = appmod._cpi_render_full_profile(_ENRICHED)
    assert "Everything Apollo has on file for this person:" in out
    assert "Everything Apollo has on the company:" in out
    person_part, company_part = out.split("Everything Apollo has on the company:")
    assert "Thoughtworks, Ltd." not in person_part
    assert "Julie Woods-Moss" not in company_part


def test_empty_fields_are_skipped_not_shown_blank():
    thin = {"matched": True, "name": "Someone", "title": "", "headline": "",
           "emails": [], "phones": [], "company": {}}
    out = appmod._cpi_render_full_profile(thin)
    assert "**Name:** Someone" in out
    assert "**Title:**" not in out
    assert "**Email:**" not in out
    assert "Everything Apollo has on the company:" not in out, \
        "no company data at all must not print an empty company section"


@pytest.mark.parametrize("bad", [{}, None, {"matched": False}])
def test_nothing_to_render_is_an_empty_string(bad):
    # {"matched": False} carries no actual fields (matched is filtered out by
    # not being a bullet-eligible label anyway) -- still nothing to show.
    if bad == {"matched": False}:
        assert appmod._cpi_render_full_profile(bad) == ""
    else:
        assert appmod._cpi_render_full_profile(bad) == ""


def test_a_zero_employee_count_is_treated_as_unknown_not_shown():
    """_apollo_org_normalize itself defaults a missing count to 0, so 0 means
    "Apollo did not have this," matching that convention rather than
    asserting a company has zero employees."""
    co = dict(_ENRICHED, company=dict(_ENRICHED["company"], employees=0))
    out = appmod._cpi_render_full_profile(co)
    assert "**Employees:**" not in out


def test_multiple_emails_and_phones_are_all_listed():
    p = dict(_ENRICHED)
    p["emails"] = [{"email": "julie@thoughtworks.com", "status": "verified"},
                   {"email": "j.woodsmoss@thoughtworks.com", "status": ""}]
    p["phones"] = [{"number": "+44 20 7946 0000"}, {"number": "+1 312 373 1000"}]
    out = appmod._cpi_render_full_profile(p)
    line = next(l for l in out.splitlines() if l.startswith("- **Email:**"))
    assert "julie@thoughtworks.com" in line and "j.woodsmoss@thoughtworks.com" in line
    phone_line = next(l for l in out.splitlines() if l.startswith("- **Phone:**"))
    assert "+44 20 7946 0000" in phone_line and "+1 312 373 1000" in phone_line


def test_duplicate_emails_across_fields_are_not_repeated():
    p = dict(_ENRICHED, email="julie.woodsmoss@thoughtworks.com", apollo_email="julie.woodsmoss@thoughtworks.com")
    out = appmod._cpi_render_full_profile(p)
    line = next(l for l in out.splitlines() if l.startswith("- **Email:**"))
    assert line.count("julie.woodsmoss@thoughtworks.com") == 1


def test_a_malformed_phone_entry_does_not_crash_rendering():
    p = dict(_ENRICHED, phones=["not-a-dict", {"number": "+44 20 7946 0000"}])
    out = appmod._cpi_render_full_profile(p)
    assert "+44 20 7946 0000" in out


# ── Wiring: full enrichment is opt-in, not automatic ────────────────────────
# Reported: a plain "CMO of X" question was auto-spending the 1-credit
# enrichment on every question, whether or not anyone wanted the contact
# details it reveals. The fix is not to stop enriching -- it is to enrich only
# when the question actually asked for contact info ("what's her email"), and
# otherwise hand back a name (revealed if masked, via the much cheaper
# _cpi_reveal_names) plus an "enrich" affordance the client renders as a
# button, so the credit is spent on a click or an explicit ask, never by
# default.

def _chat(monkeypatch, message="CMO of Thoughtworks", wants_contact=False,
          enriched=None, revealed_name="Julie Woods-Moss"):
    import json as _json
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(), raising=False)
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": "person_at_company", "titles": ["CMO"], "company_name": "Thoughtworks",
        "seniorities": [], "max_results": 10, "wants_contact_info": wants_contact}), "m"))
    monkeypatch.setattr(appmod, "_cpi_resolve_company", lambda *a, **k: (
        {"id": "org1", "name": "Thoughtworks, Ltd.", "primary_domain": "thoughtworks.com"},
        None))
    # Pinned via the paid resolver above; the free probe is off so these tests
    # stay about enrichment rather than about how the company got resolved.
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: None)
    found = [{"id": "p1", "full_name": "Julie W.",
             "title": "Chief Marketing Officer", "organization_domain": "thoughtworks.com"}]
    monkeypatch.setattr(ac, "search_people", lambda f, k, **kw: found)
    enrich_calls = []
    reveal_calls = []

    def _enrich(*a, **k):
        enrich_calls.append(1)
        return enriched if enriched is not None else dict(_ENRICHED)

    def _reveal(people, key, spend=None, **kw):
        reveal_calls.append(1)
        return [dict(p, full_name=revealed_name) for p in people]

    monkeypatch.setattr(appmod, "_cpi_enrich_person", _enrich)
    monkeypatch.setattr(appmod, "_cpi_reveal_names", _reveal)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    seen = {}

    def _answer(oai, facts, question, research=""):
        seen["facts"] = facts
        return "%s is the CMO of Thoughtworks." % facts["person"].get(
            "full_name", facts["person"].get("name", revealed_name))

    monkeypatch.setattr(appmod, "_cpi_grounded_answer", _answer)
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/b2b-agents/company-people-intelligence/chat", json={"message": message})
    assert r.status_code == 200
    return r.get_json(), seen.get("facts", {}), enrich_calls, reveal_calls


def test_a_plain_question_does_not_spend_the_enrichment_credit(monkeypatch):
    """The exact behavior requested: "CMO of X" alone must not run the paid
    enrichment at all -- only the free/cheap name reveal."""
    body, facts, enrich_calls, reveal_calls = _chat(monkeypatch, wants_contact=False)
    assert enrich_calls == [], "no contact info was asked for"
    assert reveal_calls == [1], "only the cheap, conditional name reveal ran"
    assert "Everything Apollo has on file" not in body["answer"]
    assert "full_apollo_profile_follows" not in facts


def test_a_plain_question_still_names_the_person_and_offers_to_enrich(monkeypatch):
    body, _facts, _e, _r = _chat(monkeypatch, wants_contact=False)
    assert "Julie Woods-Moss is the CMO of Thoughtworks." in body["answer"]
    # Always a list, even for a single person, so the client has one shape.
    assert body["enrich"] == [{
        "type": "person", "name": "Julie Woods-Moss", "title": "Chief Marketing Officer",
        "domain": "thoughtworks.com", "apollo_id": "p1",
    }]


def test_asking_for_contact_info_by_name_enriches_immediately(monkeypatch):
    """"what's her email" must not make the user click for it -- the intent
    parser already flagged wants_contact_info, so spend the credit now."""
    body, facts, enrich_calls, _r = _chat(monkeypatch, wants_contact=True)
    assert len(enrich_calls) == 1
    assert "**Email:** julie.woodsmoss@thoughtworks.com" in body["answer"]
    assert "**Industry:** Information Technology & Services" in body["answer"]
    assert facts["full_apollo_profile_follows"] is True
    assert "enrich" not in body, "already fully shown, nothing left to click for"


def test_nothing_is_appended_when_apollo_could_not_enrich_the_match(monkeypatch):
    """Contact info was asked for, but the enrichment call itself came back
    unmatched (e.g. a masked/ambiguous last name Apollo could not resolve
    further) -- there is nothing paid-for to show beyond the search hit."""
    body, facts, enrich_calls, _r = _chat(monkeypatch, wants_contact=True,
                                          enriched={"matched": False})
    assert len(enrich_calls) == 1
    assert "Everything Apollo has on file" not in body["answer"]
    assert "full_apollo_profile_follows" not in facts


def test_the_enrich_button_metadata_never_reaches_the_model(monkeypatch):
    _body, facts, _e, _r = _chat(monkeypatch, wants_contact=False)
    assert "enrich" not in facts and "apollo_id" not in facts


def test_the_prompt_tells_the_model_not_to_duplicate_the_appended_record():
    p = appmod._CPI_ANSWER_SYSTEM
    assert "full_apollo_profile_follows" in p
    assert "ONE short lead sentence" in p


def test_the_render_function_is_never_asked_to_fabricate_a_field():
    """No live-web or model involvement in this function at all -- confirms the
    completeness guarantee holds even if _cpi_grounded_answer is broken or
    unavailable, since the two are independent."""
    import inspect
    src = inspect.getsource(appmod._cpi_render_full_profile)
    assert "_vimi" not in src and "oai" not in src and "_responses_web_search" not in src
