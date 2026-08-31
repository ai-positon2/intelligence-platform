"""The two checks that stop a shortlist being the list everybody gets."""

import json

import pytest

from tracker import claude_websearch
from tracker import event_intel_audit as A
from tracker import event_intel_rubric as R

PROFILE = {"client_name": "Northwind", "classification": R.CLASS_B2B_TO_MARKETING,
           "buyer_roles": "VP Marketing", "verticals": "fintech",
           "window_months": 12}


def _c(name, famous=False, **kw):
    d = {"name": name, "famous": famous, "category": R.CAT_INDUSTRY_FLAGSHIP}
    d.update(kw)
    return d


def _stub(monkeypatch, payload=None, error=None, text=None):
    def fake_ask(system, user, **kw):
        if error:
            return {"text": "", "error": error}
        return {"text": text if text is not None else json.dumps(payload),
                "error": None}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)


# ── Step 3: the famous-event audit ────────────────────────────────────────

def test_nothing_famous_means_no_audit_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not have called the model")
    monkeypatch.setattr(claude_websearch, "ask", boom)
    out = A.audit_famous([_c("Tiny Vertical Summit")], PROFILE)
    assert out["checked"] == 0 and out["verdicts"] == {}


def test_a_justified_flagship_is_kept(monkeypatch):
    _stub(monkeypatch, {"audits": [{
        "name": "Dreamforce", "verdict": "kept",
        "alternative": "MarTechFest",
        "alternative_website": "https://mtf.example",
        "why": "Northwind's VP Marketing buyers staff booths here in volume."}]})
    out = A.audit_famous([_c("Dreamforce", famous=True)], PROFILE)
    v = out["verdicts"][A.name_key("Dreamforce")]
    assert v["verdict"] == A.VERDICT_KEPT
    assert v["alternative"] == "MarTechFest"


def test_kept_without_a_named_alternative_is_downgraded_to_cut(monkeypatch):
    """The enforcement that makes this an audit rather than a formality. A
    justification that names nothing it was weighed against is a restatement."""
    _stub(monkeypatch, {"audits": [{
        "name": "Dreamforce", "verdict": "kept", "alternative": None,
        "why": "It is the biggest event in the category and everyone attends."}]})
    out = A.audit_famous([_c("Dreamforce", famous=True)], PROFILE)
    v = out["verdicts"][A.name_key("Dreamforce")]
    assert v["verdict"] == A.VERDICT_CUT
    assert "no more targeted alternative was named" in v["why"]


def test_an_explicit_cut_is_recorded_with_its_replacement(monkeypatch):
    _stub(monkeypatch, {"audits": [{
        "name": "CES", "verdict": "cut", "alternative": "Fintech Meetup",
        "why": "CES is consumer hardware; the buyers here are not marketing leaders."}]})
    out = A.audit_famous([_c("CES", famous=True)], PROFILE)
    assert out["cut"][0]["alternative"] == "Fintech Meetup"
    assert out["kept"] == []


def test_a_non_http_alternative_website_is_dropped(monkeypatch):
    _stub(monkeypatch, {"audits": [{"name": "Dreamforce", "verdict": "kept",
                                    "alternative": "X Summit",
                                    "alternative_website": "javascript:alert(1)",
                                    "why": "w"}]})
    out = A.audit_famous([_c("Dreamforce", famous=True)], PROFILE)
    assert out["verdicts"][A.name_key("Dreamforce")]["alternative_website"] is None


def test_a_failed_audit_is_recorded_rather_than_passed_over(monkeypatch):
    _stub(monkeypatch, error={"kind": "transport", "detail": "HTTP 503"})
    out = A.audit_famous([_c("Dreamforce", famous=True)], PROFILE)
    assert out["error"] and "503" in out["error"]
    assert out["verdicts"] == {}


def test_an_unreadable_audit_reply_is_an_error(monkeypatch):
    _stub(monkeypatch, text="Sure, here are my thoughts on these conferences.")
    out = A.audit_famous([_c("Dreamforce", famous=True)], PROFILE)
    assert out["error"]


# ── applying the verdicts ─────────────────────────────────────────────────

def test_cut_events_leave_the_list_and_kept_events_stay():
    audit = {"error": None, "verdicts": {
        A.name_key("Dreamforce"): {"verdict": A.VERDICT_KEPT, "alternative": "X",
                                   "why": "dense with buyers"},
        A.name_key("CES"): {"verdict": A.VERDICT_CUT, "alternative": "Y",
                            "why": "wrong crowd"}}}
    out = A.apply_audit([_c("Dreamforce", True), _c("CES", True),
                         _c("Tiny Summit")], audit)
    assert [c["name"] for c in out] == ["Dreamforce", "Tiny Summit"]
    assert out[0]["audit_verdict"] == A.VERDICT_KEPT
    assert "Weighed against: X." in out[0]["audit_note"]


def test_a_famous_event_the_audit_never_ruled_on_is_cut():
    """"The auditor did not get to it" is not a justification, and a marquee
    name justifies its place or goes."""
    audit = {"error": None, "verdicts": {}}
    out = A.apply_audit([_c("Dreamforce", True), _c("Tiny Summit")], audit)
    assert [c["name"] for c in out] == ["Tiny Summit"]


def test_a_non_famous_event_is_marked_unaudited_not_kept():
    """Unaudited and kept are different facts and are stored as different
    values, so the report never implies a check that did not happen."""
    out = A.apply_audit([_c("Tiny Summit")], {"error": None, "verdicts": {}})
    assert out[0]["audit_verdict"] == A.VERDICT_UNAUDITED
    assert out[0]["audit_note"] is None


def test_when_the_audit_itself_failed_nothing_is_cut():
    """Cutting every flagship because of a transport error would be a silent,
    confident, wrong answer. The run says the audit did not run instead."""
    audit = {"error": "transport: HTTP 503", "verdicts": {}}
    out = A.apply_audit([_c("Dreamforce", True), _c("CES", True)], audit)
    assert len(out) == 2
    assert all(c["audit_verdict"] == A.VERDICT_UNAUDITED for c in out)
    assert "could not run" in out[0]["audit_note"]


# ── Step 6: the cross-client check, measured against real prior runs ──────

def _prior(client, names, rid=1):
    return {"id": rid, "client_name": client, "names": names,
            "classification": R.CLASS_B2B_TO_MARKETING}


def test_a_first_ever_run_says_the_check_could_not_run():
    """Reporting 0% overlap here would read as a pass for a test that never
    happened."""
    g = A.genericness(["A Summit", "B Expo"], [], this_client="Northwind")
    assert g["measured"] is False
    assert g["flagged"] is False
    assert "could not run" in g["why_not_measured"]


def test_heavy_overlap_with_another_client_is_flagged():
    prior = [_prior("Acme", ["Dreamforce", "Web Summit", "CES", "HIMSS"])]
    g = A.genericness(["Dreamforce 2026", "Web Summit", "CES 2026", "Local Dinner"],
                      prior, this_client="Northwind")
    assert g["measured"] is True
    assert g["flagged"] is True
    assert g["worst"]["overlap"] == 0.75
    assert g["worst"]["client_name"] == "Acme"
    assert "Acme" in g["advice"]


def test_a_distinct_list_is_measured_and_not_flagged():
    prior = [_prior("Acme", ["Dreamforce", "CES"])]
    g = A.genericness(["Fintech Meetup", "RevOps Co-op", "AWS Summit NYC"],
                      prior, this_client="Northwind")
    assert g["measured"] is True and g["flagged"] is False
    assert g["worst"]["overlap"] == 0.0
    assert g["advice"] == ""


def test_the_same_client_rerunning_is_not_counted_as_generic():
    """A stable list for the same client is the correct answer, not a
    symptom."""
    prior = [_prior("Northwind", ["Dreamforce", "Web Summit"])]
    g = A.genericness(["Dreamforce", "Web Summit"], prior, this_client="northwind")
    assert g["measured"] is False


def test_overlap_is_the_share_of_this_list_not_a_symmetric_score():
    """A short list wholly contained in a long one is completely generic. A
    symmetric similarity would score that as a third and let it through."""
    prior = [_prior("Acme", ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"])]
    g = A.genericness(["A", "B"], prior, this_client="Northwind")
    assert g["worst"]["overlap"] == 1.0
    assert g["flagged"] is True


def test_the_worst_prior_run_is_the_one_reported():
    prior = [_prior("Acme", ["A"], rid=1), _prior("Globex", ["A", "B", "C"], rid=2)]
    g = A.genericness(["A", "B", "C"], prior, this_client="Northwind")
    assert g["worst"]["run_id"] == 2 and g["worst"]["overlap"] == 1.0
    assert g["checked"] == 2


def test_an_empty_current_list_reports_that_it_could_not_be_measured():
    g = A.genericness([], [_prior("Acme", ["A"])], this_client="Northwind")
    assert g["measured"] is False and "no events" in g["why_not_measured"]


def test_year_variants_still_count_as_the_same_event():
    prior = [_prior("Acme", ["SaaStr Annual 2025"])]
    g = A.genericness(["SaaStr Annual 2026", "Other Thing"], prior,
                      this_client="Northwind")
    assert g["worst"]["shared_count"] == 1
