"""Whose access a hosted format is (live run 28, Gong, 2026-10-01).

A booth-oriented client's P1 was a single dinner paid for by another
vendor, graded as the client's own hosted meeting. The rule goes only to
clients that sell from a booth or as a sponsor; an audience-side client's
prompt is unchanged byte for byte.
"""
from tracker import event_intel_rubric as R
from tracker import event_intel_scorer as SC

BOOTH = {"client_name": "Gong", "classification": R.CLASS_B2B_TO_MARKETING,
         "buyer_roles": "VP Sales", "verticals": "SaaS"}
AUDIENCE = dict(BOOTH, client_name="Stripe", classification=R.CLASS_B2B_OTHER_FUNCTION)


def _system(monkeypatch, profile):
    seen = []

    def fake_ask(system, user, **kw):
        seen.append(system)
        return {"text": '{"scores": []}', "error": None, "search_count": 1, "usage": {}}
    monkeypatch.setattr(SC.claude_websearch, "ask", fake_ask)
    SC.score_batch([{"name": "X", "category": R.CAT_SIDE_EVENT, "starts_on": "2099-01-01",
                     "website": "https://x.example"}], profile)
    return seen[0]


def test_a_booth_client_is_told_a_sponsors_dinner_is_the_sponsors(monkeypatch):
    system = _system(monkeypatch, BOOTH)
    assert "hosted or paid for by another vendor gives the hosted access to that vendor" in system
    assert "grade its dm_access 0-9 and say so in the note" in system
    # Inside the dm_access bands, before engagement.
    assert system.index("Whose access it is") < system.index("- engagement, 0 to 20")
    assert system.index("Whose access it is") > system.index("- dm_access, 0 to 40")


def test_an_audience_clients_prompt_is_unchanged(monkeypatch):
    from tracker.event_intel_discover import profile_brief
    system = _system(monkeypatch, AUDIENCE)
    assert "Whose access it is" not in system
    # Exactly the prompt as it was before the rule existed.
    before = SC._SYSTEM.replace("{host_rule}", "").format(
        profile=profile_brief(AUDIENCE),
        where_buyers=R.CLASSIFICATION_WHERE_BUYERS_ARE[R.CLASS_B2B_OTHER_FUNCTION])
    assert system == before


def test_every_booth_classification_gets_it_and_no_audience_one_does():
    for cls in R.CLASSIFICATION_WHERE_BUYERS_ARE:
        booth = R.orientation_for(cls) == R.ORIENTATION_BOOTH
        assert bool(SC.host_rule({"classification": cls})) is booth, cls


def test_an_unknown_classification_gets_no_rule_rather_than_an_error():
    assert SC.host_rule({"classification": "nonsense"}) == ""
    assert SC.host_rule({}) == ""
