"""Match companies, the only Apollo-billed step: credits recorded as they
are spent, nothing paid for twice, and the run's stage left as it was."""
import pytest

from tracker import event_intel_enrich as E
from tracker import event_intel_pipeline as P


@pytest.fixture
def world(monkeypatch):
    box = {"participants": [], "credits": [], "stages": [], "updates": [], "asked": []}
    monkeypatch.setattr(P.store, "get_run", lambda rid, email: {"id": rid, "stage": "done"})
    monkeypatch.setattr(P.store, "get_participants", lambda rid: box["participants"])
    monkeypatch.setattr(P.store, "update_run", lambda rid, **k: box["stages"].append(k.get("stage")))
    monkeypatch.setattr(P.store, "add_credits", lambda rid, n: box["credits"].append(n))
    monkeypatch.setattr(P.store, "update_participant_resolution",
                        lambda ids, d, payload, status: box["updates"].append((tuple(ids), status, payload)))
    monkeypatch.setattr(E, "find_people", lambda d, titles=None: {
        "by_domain": {x: [{"name": "P " + x, "title": (titles or ["any"])[0]}] for x in d},
        "total": len(d), "error": None})
    return box


def test_each_billed_batch_is_recorded_as_it_returns(world, monkeypatch):
    world["participants"] = [{"id": 1, "org_domain": "acme.com"}]

    def resolve(domains, on_credit=None, **k):
        on_credit(1)
        raise TimeoutError("the web request ran out of time")
    monkeypatch.setattr(E, "resolve_companies", resolve)
    with pytest.raises(TimeoutError):
        P.resolve_run_companies(1, "me@p2.example")
    assert world["credits"] == [1], "the credit spent before the timeout was lost"


def test_a_company_already_answered_for_is_not_paid_for_again(world, monkeypatch):
    world["participants"] = [
        {"id": 1, "org_domain": "acme.com", "resolution": "matched",
         "apollo": {"name": "Acme", "contacts": [{"name": "Old"}]}},
        {"id": 2, "org_domain": "nobody.com", "resolution": "no_match"},
        {"id": 3, "org_domain": "new.com"}]

    def resolve(domains, on_credit=None, **k):
        world["asked"].append(list(domains))
        on_credit(1)
        return {"by_domain": {"new.com": {"name": "New"}}, "credits": 1,
                "unmatched": [], "unattempted": [], "error": None}
    monkeypatch.setattr(E, "resolve_companies", resolve)
    out = P.resolve_run_companies(1, "me@p2.example", titles=["CMO"])
    assert world["asked"] == [["new.com"]]
    assert out["resolved"] == 2 and world["credits"] == [1]
    acme = [u for u in world["updates"] if u[0] == (1,)][0]
    assert acme[1] == "matched" and acme[2]["name"] == "Acme"
    assert acme[2]["contacts"] == [{"name": "P acme.com", "title": "CMO"}], "new titles must re-run people"
    assert not [u for u in world["updates"] if u[0] == (2,)], "a known no-match is left as it was"


def test_nothing_new_to_look_up_bills_nothing(world, monkeypatch):
    world["participants"] = [{"id": 1, "org_domain": "acme.com", "resolution": "matched",
                              "apollo": {"name": "Acme"}}]
    monkeypatch.setattr(E, "resolve_companies", lambda *a, **k: pytest.fail("billed a known company"))
    assert P.resolve_run_companies(1, "me@p2.example")["credits"] == 0


def test_the_run_keeps_its_stage(world, monkeypatch):
    world["participants"] = [{"id": 1, "org_domain": "acme.com"}]
    monkeypatch.setattr(P.store, "get_run", lambda rid, email: {"id": rid, "stage": "roster_ready"})
    monkeypatch.setattr(E, "resolve_companies", lambda d, **k: {
        "by_domain": {}, "credits": 0, "unmatched": ["acme.com"], "unattempted": [], "error": None})
    P.resolve_run_companies(1, "me@p2.example")
    assert world["stages"] == ["resolving_companies", "roster_ready"]
