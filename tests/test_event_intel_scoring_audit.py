"""Promotion after the famous-event audit: one event is promoted once, and a
promoted event is scored on the same footing as the event it replaced.
Model calls and lookups are stubbed.
"""

import datetime
import json

from tracker import claude_websearch
from tracker import event_intel_audit as A
from tracker import event_intel_rubric as R

PROFILE = {"client_name": "Northwind", "classification": R.CLASS_B2B_TO_MARKETING,
           "buyer_roles": "VP Marketing", "verticals": "payments",
           "window_months": 12, "geo_scope": "Global"}


def _soon(days=90):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _cut(name, alternative):
    return {"name": name, "verdict": A.VERDICT_CUT, "alternative": alternative,
            "alternative_website": None, "alternative_note": "Denser floor.",
            "why": "why"}


def _event(name, **kw):
    ev = {"name": name, "website": "https://pay360.example/event",
          "starts_on": _soon(90), "ends_on": _soon(91), "city": "London",
          "country": "UK", "format": "in_person", "confidence": "high"}
    ev.update(kw)
    return ev


def _resolver(mapping):
    def _r(name):
        ev = mapping.get(name)
        if ev is None:
            return {"ok": False, "event": None, "reasoning": "Not found."}
        return {"ok": True, "confidence": "high", "reasoning": "ok",
                "event": ev, "pages": [], "error": None}
    return _r


def _pool(*names):
    return [{"name": n, "famous": True, "category": R.CAT_INDUSTRY_FLAGSHIP,
             "starts_on": _soon(40)} for n in names]


# ── 3. deduped on what the lookup found ──────────────────────────────────

def test_two_wordings_of_one_event_are_promoted_once():
    """Reproduced: both resolved to PAY360 and were promoted twice."""
    audit = {"cut": [_cut("Money20/20 Europe", "The Payments Association PAY360"),
                     _cut("Sibos", "PAY360 Awards and Conference")],
             "checked": 2, "error": None}
    pool = _pool("Money20/20 Europe", "Sibos")
    out = A.promote_alternatives(audit, pool, replaced_from=pool, profile=PROFILE,
                                 resolver=_resolver({
                                     "The Payments Association PAY360": _event("PAY360"),
                                     "PAY360 Awards and Conference": _event("PAY360")}))
    assert [c["name"] for c in out["promoted"]] == ["PAY360"]
    assert [d["same_as"] for d in out["duplicates"]] == ["PAY360"]


def test_an_alternative_that_resolves_to_a_pool_event_is_not_promoted():
    """Reproduced: "Canada's payments summit" resolved to an event already in
    the pool and was promoted as a second copy."""
    pool = _pool("Sibos") + [{"name": "Payments Canada SUMMIT", "famous": False,
                              "category": R.CAT_REGIONAL_FLAGSHIP,
                              "starts_on": _soon(120),
                              "website": "https://summit.payments.ca"}]
    audit = {"cut": [_cut("Sibos", "Canada's payments summit")], "checked": 1, "error": None}
    out = A.promote_alternatives(audit, pool, replaced_from=pool, profile=PROFILE,
                                 resolver=_resolver({"Canada's payments summit": _event(
                                     "Payments Canada SUMMIT", starts_on=_soon(120),
                                     ends_on=_soon(122), website="https://summit.payments.ca",
                                     city="Toronto", country="Canada")}))
    assert out["promoted"] == []
    assert out["duplicates"][0]["same_as"] == "Payments Canada SUMMIT"


def test_a_different_edition_of_the_same_series_is_not_a_duplicate():
    a = {"name": "PAY360", "starts_on": "2027-03-10", "website": "https://pay360.example"}
    b = {"name": "PAY360", "starts_on": "2028-03-09", "website": "https://pay360.example"}
    assert not A.same_event(a, b)
    assert A.same_event(a, dict(a, name="The PAY360 Event"))
    assert A.same_event(a, dict(b, starts_on=None))


# ── 8. a promoted event is scored like any other ─────────────────────────

def test_a_promoted_event_carries_the_lookups_matchmaking_claim():
    """It was forced False, so a promoted event could never earn the +10 the
    marquee event it replaced kept."""
    pool = _pool("Sibos")
    audit = {"cut": [_cut("Sibos", "PAY360")], "checked": 1, "error": None}
    out = A.promote_alternatives(audit, pool, replaced_from=pool, profile=PROFILE,
                                 resolver=_resolver({"PAY360": _event(
                                     "PAY360", organizer_run=True,
                                     matchmaking_evidence="The organiser runs a hosted-buyer programme.")}))
    got = out["promoted"][0]
    assert got["organizer_run"] is True
    assert "hosted-buyer" in got["matchmaking_evidence"]
    assert R.score(30, 30, 10, organizer_run=got["organizer_run"],
                   matchmaking_evidence=got["matchmaking_evidence"])["matchmaking"] == 10


def test_a_lookup_without_a_claim_still_gets_no_bonus():
    pool = _pool("Sibos")
    audit = {"cut": [_cut("Sibos", "PAY360")], "checked": 1, "error": None}
    out = A.promote_alternatives(audit, pool, replaced_from=pool, profile=PROFILE,
                                 resolver=_resolver({"PAY360": _event("PAY360")}))
    got = out["promoted"][0]
    assert got["organizer_run"] is False and got["matchmaking_evidence"] is None


def test_what_the_scorer_is_told_about_a_promoted_event_is_neutral():
    pool = _pool("Sibos")
    audit = {"cut": [_cut("Sibos", "PAY360")], "checked": 1, "error": None}
    got = A.promote_alternatives(audit, pool, replaced_from=pool, profile=PROFILE,
                                 resolver=_resolver({"PAY360": _event("PAY360")}))["promoted"][0]
    assert "more targeted" not in got["category_fit"]
    # The provenance the reader sees is unchanged.
    assert "more targeted alternative" in got["audit_note"]


def test_the_audit_prompt_labels_the_website_as_the_website(monkeypatch):
    seen = []

    def fake_ask(system, user, **kw):
        seen.append(user)
        return {"text": json.dumps({"verdict": "kept", "alternative": "X", "why": "w"}),
                "error": None, "search_count": 2}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    A.audit_famous([{"name": "Sibos", "famous": True, "website": "https://sibos.com",
                     "organizer": "Swift", "starts_on": _soon(40)}], PROFILE)
    assert "Organizer: https" not in seen[0]
    assert "Official site: https://sibos.com" in seen[0]
    assert "Organiser: Swift" in seen[0]


# ── 6. the audit's own error text ────────────────────────────────────────

def test_an_audit_failure_is_stored_in_reader_english(monkeypatch):
    def fake_ask(system, user, **kw):
        return {"text": "", "error": {
            "kind": "max_tokens",
            "detail": "Ran out of output budget before finishing "
                      "(stop_reason=max_tokens). Raise max_tokens or lower max_uses."}}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    out = A.audit_famous([{"name": "Sibos", "famous": True}], PROFILE)
    for text in [out["error"]] + [f["why"] for f in out["failed"].values()]:
        for token in ("max_tokens", "max_uses", "Raise", "stop_reason"):
            assert token not in text, text


def test_a_crashed_audit_call_does_not_print_the_exception(monkeypatch):
    def boom(system, user, **kw):
        raise RuntimeError("SECRET-DSN psycopg2")
    monkeypatch.setattr(claude_websearch, "ask", boom)
    out = A.audit_famous([{"name": "Sibos", "famous": True}], PROFILE)
    assert "SECRET-DSN" not in out["error"]
