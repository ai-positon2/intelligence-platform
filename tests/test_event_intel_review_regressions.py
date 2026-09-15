"""Regressions from the September 14 current-main review."""
import json
import pytest
from tracker import event_intel_admission as admission
from tracker import event_intel_audit as audit
from tracker import event_intel_discover as discover
from tracker import event_intel_intake as intake

@pytest.mark.parametrize("page,candidate,accepted", [
    ("04/03/2027. Dates shown DD/MM/YYYY.", "2027-04-03", False),
    ("04/03/2027. Dates shown DD/MM/YYYY.", "2027-03-04", True),
    ("04/03/2027.", "2027-04-03", False),
    ("04/03/2027. Dates shown MM/DD/YYYY.", "2027-04-03", True),
    ("14/03/2027.", "2027-03-14", True),
])
def test_numeric_date_evidence(page, candidate, accepted):
    event = dict(name="Example Summit", website="https://event.example",
                 starts_on=candidate, ends_on=candidate)
    result = admission.inspect(event, lambda _: dict(status="ok", text="Example Summit " + page))
    assert (not result["reasons"]) is accepted

def test_explicit_edition_must_match_commitment():
    keys = discover.committed_keys("Example Summit USA 2026")
    assert not discover.is_committed_same_edition("Example Summit USA 2027", keys)
    assert discover.is_committed_same_edition("Example Summit USA 2026", keys)
    assert not discover.is_committed_same_edition("Example Summit USA", keys)

def test_comparison_cycle_preserves_candidates_for_scoring():
    candidates = [dict(name=n, famous=True, category="industry_flagship") for n in ("Alpha Summit", "Beta Summit")]
    result = dict(error=None, verdicts={
        discover.name_key(c["name"]): dict(verdict="cut", alternative=candidates[1-i]["name"], why="Preferred alternative")
        for i,c in enumerate(candidates)}, cut=[])
    result["cut"] = list(result["verdicts"].values())
    retained = audit.retain_for_scoring(candidates, result)
    assert [c["name"] for c in retained] == [c["name"] for c in candidates]
    assert audit.alternatives_to_promote(result, retained) == []

def test_uncited_intake_search_is_not_usable(monkeypatch):
    monkeypatch.setattr(intake.claude_websearch, "ask", lambda *a, **k: dict(
        error=None, search_count=1, text=json.dumps(dict(
            what_they_sell="Cloud services", buyer_roles="CMOs",
            evidence={"buyer_roles":"Marketing teams"}, sources=[]))))
    assert intake._search_draft("Example", "https://example.com") is None
