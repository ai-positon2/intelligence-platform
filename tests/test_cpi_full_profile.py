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


# ── Wiring: the chat panel shows it unconditionally on a real match ─────────

def _chat(monkeypatch, message="CMO of Thoughtworks", wants_contact=False,
          enriched=None):
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
    found = [{"id": "p1", "full_name": "Julie Woods-Moss",
             "title": "Chief Marketing Officer", "organization_domain": "thoughtworks.com"}]
    monkeypatch.setattr(ac, "search_people", lambda f, k, **kw: found)
    monkeypatch.setattr(appmod, "_cpi_enrich_person",
                        lambda *a, **k: enriched if enriched is not None else dict(_ENRICHED))
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    seen = {}

    def _answer(oai, facts, question, research=""):
        seen["facts"] = facts
        return "Julie Woods-Moss is the CMO of Thoughtworks."

    monkeypatch.setattr(appmod, "_cpi_grounded_answer", _answer)
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/gtm/company-people-intelligence/chat", json={"message": message})
    assert r.status_code == 200
    return r.get_json(), seen.get("facts", {})


def test_the_full_profile_is_appended_to_the_chat_answer(monkeypatch):
    body, facts = _chat(monkeypatch)
    assert "Julie Woods-Moss is the CMO of Thoughtworks." in body["answer"]
    assert "**Email:** julie.woodsmoss@thoughtworks.com" in body["answer"]
    assert "**Industry:** Information Technology & Services" in body["answer"]
    assert facts["full_apollo_profile_follows"] is True


def test_contact_fields_are_shown_even_when_not_asked_for(monkeypatch):
    """The exact behavior change requested: previously an email/phone reached
    the answer ONLY when wants_contact_info was true. The enrichment already
    spent the credit regardless, so the appended record must show them either
    way."""
    body, _facts = _chat(monkeypatch, wants_contact=False)
    assert "**Email:**" in body["answer"]
    assert "**Phone:**" in body["answer"]


def test_nothing_is_appended_when_apollo_could_not_enrich_the_match(monkeypatch):
    """search_people found a name, but the enrichment call itself came back
    unmatched (e.g. a masked/ambiguous last name Apollo could not resolve
    further) -- there is nothing paid-for to show beyond the search hit."""
    body, facts = _chat(monkeypatch, enriched={"matched": False})
    assert "Everything Apollo has on file" not in body["answer"]
    assert "full_apollo_profile_follows" not in facts


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
