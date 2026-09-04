"""The two checks that stop a shortlist being the list everybody gets."""

import json

import re

import pytest

from tracker import claude_websearch
from tracker import event_intel_audit as A
from tracker import event_intel_rubric as R


def _soon(days: int = 90) -> str:
    import datetime
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()

PROFILE = {"client_name": "Northwind", "classification": R.CLASS_B2B_TO_MARKETING,
           "buyer_roles": "VP Marketing", "verticals": "fintech",
           "window_months": 12}


def _c(name, famous=False, **kw):
    d = {"name": name, "famous": famous, "category": R.CAT_INDUSTRY_FLAGSHIP}
    d.update(kw)
    return d


def _stub(monkeypatch, payload=None, error=None, text=None, search_count=4):
    """A reply that ran searches, unless a test says otherwise.

    `search_count` is not decoration. The audit refuses a reply that ran no
    search at all, on the grounds that its verdicts were recalled rather than
    checked, and a stub that omits the count is stubbing a reply the wrapper
    does not produce.
    """
    def fake_ask(system, user, **kw):
        if error:
            return {"text": "", "error": error}
        return {"text": text if text is not None else json.dumps(payload),
                "error": None, "search_count": search_count}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)


# ── Step 3: the famous-event audit ────────────────────────────────────────

def test_the_audit_is_one_call_per_famous_event(monkeypatch):
    """The shape, not the budget.

    This used to be a single call over every famous event at max_uses=10, and
    a live run measured what that costs: 152,820 input tokens and 241 seconds,
    against 50,829 to 58,918 for the six-search calls beside it. Input grows
    with the SQUARE of one call's search count, because each search round
    re-sends the whole accumulated conversation, so splitting the call reduces
    the bill rather than merely spreading it.

    Locked in as a test because the arithmetic is not obvious enough to
    survive somebody "simplifying" this back into one call.
    """
    seen = []

    def fake_ask(system, user, **kw):
        seen.append((user, kw.get("max_uses")))
        return {"text": json.dumps({"verdict": "kept", "alternative": "Alt",
                                    "why": "w"}),
                "error": None, "search_count": 2}

    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    famous = [_c("Dreamforce", True), _c("CES", True), _c("Web Summit", True)]
    out = A.audit_famous(famous, PROFILE)

    assert len(seen) == 3, (
        "3 famous events took %d calls; the audit is meant to make one per "
        "event" % len(seen))
    # Each call is about ONE event, so the whole shortlist must not be pasted
    # into every prompt.
    for user, _ in seen:
        assert sum(1 for c in famous if c["name"] in user) == 1, user
    assert {u for u, _ in seen} == {
        "Audit this famous event:\n- %s" % c["name"] for c in famous}
    # A per-call budget bigger than the old whole-audit budget would undo the
    # squaring saving the split exists to get.
    assert all(m == A.AUDIT_MAX_USES for _, m in seen)
    assert A.AUDIT_MAX_USES <= 5, (
        "%d searches per event is back in the territory the single call was "
        "abandoned for" % A.AUDIT_MAX_USES)
    assert len(out["verdicts"]) == 3


def _by_event(monkeypatch, replies: dict, calls=None):
    """Answer each per-event audit call by the event its prompt names."""
    def fake_ask(system, user, **kw):
        for name, reply in sorted(replies.items(), key=lambda kv: -len(kv[0])):
            if name in user:
                if calls is not None:
                    calls.append(name)
                return reply
        raise AssertionError("unstubbed audit call: %r" % user)
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)


def _reply(payload, search_count=2, usage=None):
    return {"text": json.dumps(payload), "error": None,
            "search_count": search_count, "usage": usage or {}}


def test_the_verdict_is_labelled_from_the_candidate_not_the_reply(monkeypatch):
    """The city-suffix bug class, closed at its source.

    The audit is shown "Name (City) - audience note", so a reply that echoes
    the name it was given comes back carrying the city, and `name_key` then
    produces a different key: "adobe las vegas" against "adobe". That has cost
    real events twice, once in the verdict lookup and once in the category
    lookup a promotion inherits.

    With one call per known event there is nothing to match up, so the name is
    taken from the candidate and the mismatch cannot arise. The loose matching
    in `_verdict_for` stays for stored runs written by the old shape.
    """
    _by_event(monkeypatch, {"Adobe Summit": _reply({
        # Exactly what the live run produced.
        "name": "Adobe Summit (Las Vegas)", "verdict": "cut",
        "alternative": "MAICON", "why": "Adobe's own product conference."})})
    cand = _c("Adobe Summit", True, city="Las Vegas")
    out = A.audit_famous([cand], PROFILE)

    assert list(out["verdicts"]) == [A.name_key("Adobe Summit")], (
        "the verdict was keyed off the model's echo: %s" % list(out["verdicts"]))
    assert out["cut"][0]["name"] == "Adobe Summit"
    # The whole point: the promotion can find the row it stands in for.
    wanted = A.alternatives_to_promote(out, [])
    assert wanted[0]["replaces"] == "Adobe Summit"


def test_one_broken_audit_costs_only_its_own_event(monkeypatch):
    """The correctness half of the split. Five calls are five chances to lose
    one, and apply_audit CUTS a famous event it has no verdict for, so without
    per-event failure records the split would quietly delete real
    recommendations that the single call always kept."""
    _by_event(monkeypatch, {
        "Dreamforce": _reply({"verdict": "kept", "alternative": "Alt A",
                              "why": "dense"}),
        "CES": {"text": "", "error": {"kind": "transport", "detail": "HTTP 503"}},
        "Web Summit": _reply({"verdict": "cut", "alternative": "Alt B",
                              "why": "too broad"}),
    })
    famous = [_c("Dreamforce", True), _c("CES", True), _c("Web Summit", True)]
    out = A.audit_famous(famous, PROFILE)

    assert out["error"] is None
    assert [v["name"] for v in out["kept"]] == ["Dreamforce"]
    assert [v["name"] for v in out["cut"]] == ["Web Summit"]
    assert list(out["failed"]) == [A.name_key("CES")]

    survivors = [c["name"] for c in A.apply_audit(famous, out)]
    assert survivors == ["Dreamforce", "CES"], (
        "the broken call took an event with it, or spared a cut one")


def test_the_cut_and_kept_lists_read_in_shortlist_order(monkeypatch):
    """The calls finish in whatever order the network returns them, and a
    report whose cut list is ordered by network latency is not reproducible."""
    _by_event(monkeypatch, {
        n: _reply({"verdict": "cut", "alternative": "Alt", "why": "w"})
        for n in ("Alpha Expo", "Beta Expo", "Gamma Expo", "Delta Expo")})
    famous = [_c(n, True) for n in
              ("Alpha Expo", "Beta Expo", "Gamma Expo", "Delta Expo")]
    out = A.audit_famous(famous, PROFILE)
    assert [v["name"] for v in out["cut"]] == [c["name"] for c in famous]


def test_the_whole_audit_failing_is_one_error_and_cuts_nobody(monkeypatch):
    """The contract the single call had, preserved. `error` now means "not one
    marquee event could be audited", which is the only case in which the check
    as a whole can be said not to have run."""
    _by_event(monkeypatch, {
        n: {"text": "", "error": {"kind": "transport", "detail": "HTTP 503"}}
        for n in ("Dreamforce", "CES")})
    famous = [_c("Dreamforce", True), _c("CES", True)]
    out = A.audit_famous(famous, PROFILE)
    assert out["error"] and "503" in out["error"]
    assert out["verdicts"] == {} and len(out["failed"]) == 2
    assert [c["name"] for c in A.apply_audit(famous, out)] == ["Dreamforce", "CES"]


def test_the_split_counts_what_every_call_cost(monkeypatch):
    """One spend record per event, summed. The single call had one reply to
    read a cost off; this stage now has one per event, and a stage that
    reports only the last one understates the bill by however many marquee
    events the client had."""
    _by_event(monkeypatch, {
        n: _reply({"verdict": "cut", "alternative": "Alt", "why": "w"},
                  search_count=3,
                  usage={"input_tokens": 20000, "output_tokens": 2000})
        for n in ("Dreamforce", "CES", "Web Summit")})
    out = A.audit_famous([_c(n, True) for n in
                          ("Dreamforce", "CES", "Web Summit")], PROFILE)
    assert out["spend"]["calls"] == 3
    assert out["spend"]["input_tokens"] == 60000
    assert out["spend"]["searches"] == 9


def test_a_refused_audit_is_still_billed(monkeypatch):
    """A reply that ran searches and then could not be read has already been
    paid for. Counting only the usable ones is how a stage looks cheaper than
    it is, and the unusable calls are the ones worth seeing."""
    _by_event(monkeypatch, {"Dreamforce": {
        "text": "no json here", "error": None, "search_count": 3,
        "usage": {"input_tokens": 21000, "output_tokens": 900}}})
    out = A.audit_famous([_c("Dreamforce", True)], PROFILE)
    assert out["failed"], "an unreadable reply was accepted"
    assert out["spend"]["input_tokens"] == 21000, (
        "a refused audit was billed to nobody")


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
    # The name is available to whoever runs the analysis and is deliberately
    # NOT in the advice string, because the advice string is what reaches the
    # deliverable and the deliverable leaves the building. Naming Acme here
    # would put one client's engagement into another client's report.
    assert "Acme" not in g["advice"]
    assert "a different client on this account" in g["advice"]
    assert "75%" in g["advice"]


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


# ── the alternative the audit names has to actually get scored ───────────
#
# The gap, live on 2026-09-02: the audit cut MarTech Conference for being
# fully online with no exhibit floor, named INBOUND as the in-person
# alternative with a real expo floor, wrote a paragraph on why it was the
# better fit, and then nothing ever looked INBOUND up. That run ended with an
# empty list while the system had already worked out the answer.

def _cut(name, alternative=None, website=None, note=None):
    return {"name": name, "verdict": A.VERDICT_CUT, "alternative": alternative,
            "alternative_website": website, "alternative_note": note,
            "why": "why"}


def _resolved(name, **kw):
    ev = {"name": name, "edition": "2026", "website": "https://%s.com" % name.lower().replace(" ", ""),
          "organizer": "Org", "starts_on": "2026-09-08", "ends_on": "2026-09-11",
          "location": "Boston, MA", "venue": None, "format": "in_person",
          "stated_size": "12,000", "audience_note": "Marketers and sales leaders.",
          "confidence": "high", "reasoning": "Confirmed on the official site."}
    ev.update(kw)
    return {"ok": True, "confidence": ev["confidence"], "reasoning": ev["reasoning"],
            "event": ev, "pages": [{"url": "https://inbound.com/agenda",
                                    "kind": "agenda", "note": ""}], "error": None}


def _resolver_for(mapping):
    """A stub resolver. Anything not in the mapping fails to resolve."""
    def _r(name):
        got = mapping.get(name)
        if got is None:
            return {"ok": False, "confidence": "low", "event": None,
                    "reasoning": "No single edition could be pinned down."}
        return got
    return _r


def test_the_alternative_to_a_cut_event_is_confirmed_and_added(monkeypatch):
    """The regression. Cutting a marquee event and naming a replacement is
    only half an answer; the client needs the replacement on the list."""
    audit = {"cut": [_cut("MarTech Conference", "INBOUND",
                          "https://www.inbound.com", "It has a real expo floor.")],
             "checked": 1, "error": None}
    cands = [_c("MarTech Conference", famous=True)]
    out = A.promote_alternatives(audit, cands,
                                 resolver=_resolver_for({"INBOUND": _resolved("INBOUND")}))
    assert [c["name"] for c in out["promoted"]] == ["INBOUND"]
    got = out["promoted"][0]
    assert got["starts_on"] == "2026-09-08", "promoted without confirmed dates"
    assert got["sources"], "promoted with nothing to check it against"


def test_a_promoted_event_says_it_did_not_come_from_a_category_search(monkeypatch):
    """It sits among that category's results. Without provenance the reader
    believes a search found it, and cannot tell that a different check
    confirmed it."""
    audit = {"cut": [_cut("MarTech Conference", "INBOUND")], "checked": 1, "error": None}
    out = A.promote_alternatives(audit, [_c("MarTech Conference", famous=True)],
                                 resolver=_resolver_for({"INBOUND": _resolved("INBOUND")}))
    got = out["promoted"][0]
    assert got["audit_verdict"] == A.VERDICT_PROMOTED
    assert "MarTech Conference" in got["audit_note"]
    assert "not found by a category search" in got["audit_note"]


def test_a_promoted_event_is_never_marked_famous(monkeypatch):
    """`famous` is what selects events for auditing. A promoted event that
    carried it would be audited, could name its own alternative, and the step
    would recurse. It has also already won the comparison."""
    audit = {"cut": [_cut("MarTech Conference", "INBOUND")], "checked": 1, "error": None}
    out = A.promote_alternatives(audit, [_c("MarTech Conference", famous=True)],
                                 resolver=_resolver_for(
                                     {"INBOUND": _resolved("INBOUND", famous=True)}))
    assert out["promoted"][0]["famous"] is False


def test_a_promoted_event_inherits_the_category_it_stands_in_for(monkeypatch):
    audit = {"cut": [_cut("MarTech Conference", "INBOUND")], "checked": 1, "error": None}
    cands = [_c("MarTech Conference", famous=True, category=R.CAT_VERTICAL_SUMMIT)]
    out = A.promote_alternatives(audit, cands,
                                 resolver=_resolver_for({"INBOUND": _resolved("INBOUND")}))
    assert out["promoted"][0]["category"] == R.CAT_VERTICAL_SUMMIT


def test_an_alternative_that_cannot_be_confirmed_is_reported_not_injected():
    """The audit naming an event does not establish that it exists. Putting an
    unconfirmed name on a client's travel calendar is the failure this whole
    module exists to prevent, and staying silent about it hides the same gap
    the promotion step was built to close."""
    audit = {"cut": [_cut("MarTech Conference", "Some Event That Is Not Real")],
             "checked": 1, "error": None}
    out = A.promote_alternatives(audit, [_c("MarTech Conference", famous=True)],
                                 resolver=_resolver_for({}))
    assert out["promoted"] == []
    assert len(out["unconfirmed"]) == 1
    assert out["unconfirmed"][0]["name"] == "Some Event That Is Not Real"
    assert out["unconfirmed"][0]["replaces"] == "MarTech Conference"
    assert out["unconfirmed"][0]["why"]


def test_the_alternative_on_a_kept_verdict_is_not_promoted():
    """A kept verdict means the marquee event justified its place AGAINST the
    alternative, so the alternative lost. Promoting it would add an event the
    audit had just implicitly rejected."""
    audit = {"cut": [], "kept": [{"name": "INBOUND", "verdict": A.VERDICT_KEPT,
                                  "alternative": "Some Smaller Summit"}],
             "checked": 1, "error": None}
    out = A.promote_alternatives(audit, [_c("INBOUND", famous=True)],
                                 resolver=_resolver_for(
                                     {"Some Smaller Summit": _resolved("Some Smaller Summit")}))
    assert out["promoted"] == [] and out["considered"] == 0


def test_an_alternative_already_on_the_list_is_not_added_twice():
    """A category search may well have found the same event. A duplicate is
    scored twice and can take two slots under the cap."""
    audit = {"cut": [_cut("MarTech Conference", "INBOUND")], "checked": 1, "error": None}
    cands = [_c("MarTech Conference", famous=True), _c("INBOUND")]
    out = A.promote_alternatives(audit, cands,
                                 resolver=_resolver_for({"INBOUND": _resolved("INBOUND")}))
    assert out["promoted"] == [] and out["considered"] == 0


def test_two_cut_events_naming_the_same_replacement_promote_it_once():
    audit = {"cut": [_cut("MarTech Conference", "INBOUND"),
                     _cut("Some Other Show", "INBOUND")],
             "checked": 2, "error": None}
    cands = [_c("MarTech Conference", famous=True), _c("Some Other Show", famous=True)]
    out = A.promote_alternatives(audit, cands,
                                 resolver=_resolver_for({"INBOUND": _resolved("INBOUND")}))
    assert [c["name"] for c in out["promoted"]] == ["INBOUND"]


def test_the_number_of_lookups_is_capped_and_the_rest_are_named():
    """Each promotion is a live search. Trimming silently would leave the
    reader thinking every cut event got a replacement."""
    marquee = ["Dreamforce", "Web Summit", "CES", "SXSW", "Cannes Lions"]
    alts = ["Pavilion GTM", "SaaStr Annual", "MOps-Apalooza", "Exit Five Drive",
            "Demandbase Summit"]
    audit = {"cut": [_cut(m, a) for m, a in zip(marquee, alts)],
             "checked": 5, "error": None}
    cands = [_c(m, famous=True) for m in marquee]
    mapping = {a: _resolved(a) for a in alts}
    out = A.promote_alternatives(audit, cands, resolver=_resolver_for(mapping), cap=2)
    assert len(out["promoted"]) == 2, "the cost ceiling was not applied"
    assert [c["name"] for c in out["not_attempted"]] == alts[2:], (
        "alternatives dropped by the cap were not named")
    assert out["considered"] == 5


def test_a_failed_lookup_does_not_use_up_a_promotion_slot():
    """Measured on a live run. The audit named four alternatives, the cap
    allowed three lookups, and the first two were refused, so the run promoted
    one event out of four named and never examined the fourth: lookups spent
    on events that never reached the list had been counted against it.

    The cap is on what reaches the list. The lookup budget is what it costs.
    They were one number, and one number cannot do both jobs.
    """
    marquee = ["Adobe Summit", "Content Marketing World",
               "MarTech Conference", "UNBOUND"]
    alts = ["MAICON", "B2BMX", "Gartner Marketing Symposium", "SaaStr Annual"]
    audit = {"cut": [_cut(m, a) for m, a in zip(marquee, alts)],
             "checked": 4, "error": None}
    cands = [_c(m, famous=True) for m in marquee]
    # The first two named cannot be confirmed; the last two can.
    mapping = {a: _resolved(a) for a in alts[2:]}
    out = A.promote_alternatives(audit, cands,
                                 resolver=_resolver_for(mapping), cap=2)
    assert [c["name"] for c in out["promoted"]] == alts[2:], (
        "two refused lookups consumed the promotion budget: promoted %s"
        % [c["name"] for c in out["promoted"]])
    assert len(out["unconfirmed"]) == 2
    assert out["not_attempted"] == [], (
        "an alternative was left unexamined while the list still had room")


def test_the_lookup_budget_still_bounds_what_the_step_can_spend():
    """The other side of splitting the number in two. Nothing confirms here,
    so the promotion goal is never reached and the lookup ceiling is the only
    thing that can stop the step walking a long list at $0.50 a time."""
    alts = ["A%d Summit" % i for i in range(8)]
    marquee = ["M%d Expo" % i for i in range(8)]
    audit = {"cut": [_cut(m, a) for m, a in zip(marquee, alts)],
             "checked": 8, "error": None}
    calls = []

    def _r(name):
        calls.append(name)
        return {"ok": False, "confidence": "low", "event": None,
                "reasoning": "No single edition could be pinned down."}

    out = A.promote_alternatives(audit, [_c(m, famous=True) for m in marquee],
                                 resolver=_r, cap=3, lookups=4)
    assert len(calls) == 4, "the lookup ceiling did not hold: %d calls" % len(calls)
    assert out["promoted"] == []
    assert [c["name"] for c in out["not_attempted"]] == alts[4:], (
        "the alternatives the budget never reached were not named")


def test_the_promotion_goal_and_its_cost_ceiling_are_separate_numbers():
    """A lookup budget no larger than the promotion cap collapses the two
    back into one, which is the bug above with extra steps."""
    assert A.MAX_PROMOTION_LOOKUPS > A.MAX_PROMOTED, (
        "%d lookups for %d promotions leaves no room for a refused lookup"
        % (A.MAX_PROMOTION_LOOKUPS, A.MAX_PROMOTED))


def test_a_resolver_that_raises_is_reported_rather_than_killing_the_run():
    def _boom(name):
        raise RuntimeError("HTTP 503")
    audit = {"cut": [_cut("MarTech Conference", "INBOUND")], "checked": 1, "error": None}
    out = A.promote_alternatives(audit, [_c("MarTech Conference", famous=True)],
                                 resolver=_boom)
    assert out["promoted"] == []
    assert "503" in out["unconfirmed"][0]["why"]


def test_a_cut_with_no_named_alternative_promotes_nothing():
    """The no-verdict cuts carry alternative=None, and so does a cut that
    simply had nothing better to offer."""
    audit = {"cut": [{"name": "X", "verdict": A.VERDICT_CUT, "alternative": None,
                      "no_verdict": True, "why": "no verdict"}],
             "checked": 1, "error": None}
    out = A.promote_alternatives(audit, [_c("X", famous=True)],
                                 resolver=_resolver_for({}))
    assert out["considered"] == 0 and out["promoted"] == [] and out["unconfirmed"] == []


def test_the_audit_prose_is_not_reused_as_matchmaking_evidence():
    """The +10 bonus needs evidence the rubric has read. A sentence about why
    one event beats another is not that, and letting it through here would
    walk straight past the matchmaking gate."""
    audit = {"cut": [_cut("MarTech Conference", "INBOUND", None,
                          "It runs a hosted-buyer programme with pre-scheduled 1:1s.")],
             "checked": 1, "error": None}
    out = A.promote_alternatives(audit, [_c("MarTech Conference", famous=True)],
                                 resolver=_resolver_for({"INBOUND": _resolved("INBOUND")}))
    assert out["promoted"][0]["matchmaking_evidence"] is None


# ── the reader has to be told where a promoted event came from ───────────

def test_the_assumptions_name_the_promoted_event_and_what_it_replaced():
    from tracker import event_intel_report as RPT
    lines = RPT.assumptions(
        shortfall=[], audit={"checked": 1, "error": None,
                             "cut": [_cut("MarTech Conference", "INBOUND")]},
        generic={"measured": False, "why_not_measured": "x"}, candidates=[],
        scoring_errors=[], interchangeable=[], banned=[], thin=[], unscored=[],
        promoted={"promoted": [{"name": "INBOUND"}], "unconfirmed": [],
                  "not_attempted": [], "considered": 1})
    joined = " ".join(lines)
    assert "INBOUND" in joined
    assert "confirmed separately" in joined


def test_the_assumptions_say_when_a_replacement_could_not_be_confirmed():
    """The worst silent outcome: a marquee event is cut, its replacement
    cannot be confirmed, and the reader sees a shorter list with no
    explanation of what went missing."""
    from tracker import event_intel_report as RPT
    lines = RPT.assumptions(
        shortfall=[], audit={"checked": 1, "error": None,
                             "cut": [_cut("MarTech Conference", "Ghost Summit")]},
        generic={"measured": False, "why_not_measured": "x"}, candidates=[],
        scoring_errors=[], interchangeable=[], banned=[], thin=[], unscored=[],
        promoted={"promoted": [], "unconfirmed": [
            {"name": "Ghost Summit", "replaces": "MarTech Conference",
             "why": "No single edition could be pinned down."}],
            "not_attempted": [], "considered": 1})
    joined = " ".join(lines)
    assert "Ghost Summit" in joined and "could not be confirmed" in joined


def test_no_replacement_activity_adds_no_bullet():
    """A run that cut nothing must not grow an empty section."""
    from tracker import event_intel_report as RPT
    lines = RPT.assumptions(
        shortfall=[], audit={"checked": 0, "error": None, "cut": []},
        generic={"measured": False, "why_not_measured": "x"}, candidates=[],
        scoring_errors=[], interchangeable=[], banned=[], thin=[], unscored=[],
        promoted={"promoted": [], "unconfirmed": [], "not_attempted": [],
                  "considered": 0})
    assert not [l for l in lines if l.startswith("Replacements for cut")]


def test_the_pipeline_actually_calls_the_promotion_step(monkeypatch):
    """Wiring, not just capability. promote_alternatives working while the
    pipeline never calls it is the exact shape of bug that hid the Apollo
    grouping failure and the broken candidate INSERT."""
    from tracker import event_intel_pipeline as P
    seen = {}

    def spy(audit, survivors, *a, **kw):
        seen["called"] = True
        seen["survivors"] = [c.get("name") for c in survivors]
        return {"promoted": [], "unconfirmed": [], "not_attempted": [],
                "considered": 0}

    monkeypatch.setattr(P.event_intel_audit, "promote_alternatives", spy)
    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [_c("MarTech Conference", famous=True)],
        "shortfall": [], "statuses": {}, "categories_failed": 0, "found": 1,
        "by_category": {}, "categories_searched": 6})
    monkeypatch.setattr(P.event_intel_audit, "audit_famous", lambda c, p: {
        "verdicts": {}, "cut": [], "kept": [], "checked": 1,
        "error": "audit did not run"})
    monkeypatch.setattr(P.event_intel_scorer, "score_all", lambda s, p: {
        "scored": [], "unscored": [], "errors": []})
    monkeypatch.setattr(P.store, "update_run", lambda *a, **k: None)
    monkeypatch.setattr(P.store, "save_candidates", lambda *a, **k: 0)
    monkeypatch.setattr(P.store, "get_candidates", lambda *a, **k: [])
    monkeypatch.setattr(P.store, "prior_candidate_names", lambda *a, **k: [])
    monkeypatch.setattr(P.store, "get_outcomes", lambda *a, **k: {
        "counts": {}, "ruled_on": 0, "note": "", "by_name": {}})

    P._run_recommend(1, "e@x.com", dict(PROFILE))
    assert seen.get("called"), "the pipeline never asked for replacements"


# ── a replacement has to be one the client can actually attend ───────────

def test_a_replacement_whose_only_edition_is_over_is_refused():
    """Found live on 2026-09-02: promoting INBOUND resolved the 2025 edition,
    which had already finished. The lookup is allowed to fall back to the most
    recent past edition when no future one is announced, which is correct for
    reading a roster and wrong for offering a replacement. A line the client
    cannot act on, presented where a recommendation should be, is worse than
    an empty slot."""
    audit = {"cut": [_cut("MarTech Conference", "INBOUND")], "checked": 1,
             "error": None}
    past = _resolved("INBOUND", starts_on="2019-09-03", ends_on="2019-09-05")
    out = A.promote_alternatives(audit, [_c("MarTech Conference", famous=True)],
                                 resolver=_resolver_for({"INBOUND": past}))
    assert out["promoted"] == [], "an event that has already happened was recommended"
    assert out["unconfirmed"][0]["finished"] is True
    assert "already finished" in out["unconfirmed"][0]["why"]
    assert "2019-09-03" in out["unconfirmed"][0]["why"], (
        "the reader is not told which edition was found")


def test_an_undated_replacement_is_not_treated_as_finished():
    """No date is not the same as a date in the past. Refusing an undated
    event here would silently drop replacements for every event that has not
    published its dates yet."""
    audit = {"cut": [_cut("MarTech Conference", "INBOUND")], "checked": 1,
             "error": None}
    undated = _resolved("INBOUND", starts_on=None, ends_on=None)
    out = A.promote_alternatives(audit, [_c("MarTech Conference", famous=True)],
                                 resolver=_resolver_for({"INBOUND": undated}))
    assert [c["name"] for c in out["promoted"]] == ["INBOUND"]


def test_the_event_lookup_tells_the_model_what_day_it_is():
    """The prompt already asked for the next upcoming edition, but with no
    date in the conversation the model cannot tell upcoming from past. It
    returned the 2025 edition of INBOUND on a run dated 2026-09-02."""
    import datetime
    from tracker import event_intel_resolve as RES
    seen = {}

    def fake_ask(system, user, **kw):
        seen["user"] = user
        return {"text": "{}", "raw": "", "error": None, "stop_reason": "end_turn",
                "text_block_count": 1, "tool_version": "v", "search_count": 1,
                "tool_errors": [], "usage": {}}

    RES.claude_websearch.ask, real = fake_ask, RES.claude_websearch.ask
    try:
        RES.resolve_event("INBOUND")
    finally:
        RES.claude_websearch.ask = real
    assert datetime.date.today().isoformat() in seen["user"], (
        "the lookup never told the model what day it is")
    assert "on or after" in seen["user"]


# ── the verdict has to reach the event it is about ────────────────────────
#
# Found by the live run of 2026-09-03, not by this suite. The audit is shown
# each marquee event as "Name (City) - audience note" because the city is
# what separates two editions, so a reply that repeats the name it was given
# comes back carrying the city. Keyed strictly, that reply misses the
# candidate it is about, and the event is cut with the reason "the audit
# returned no verdict" while the real verdict sits unread beside it.

def test_a_verdict_that_echoes_the_city_back_still_lands_on_its_event():
    """The exact shape of the live failure: INBOUND was audited, weighed
    against a named alternative and cut on the merits, then cut a second time
    for never having been audited. The reader saw the false reason."""
    audit = {"error": None, "verdicts": {}}
    audit["verdicts"][A.name_key("INBOUND (rebranding to UNBOUND) (Boston, MA)")] = {
        "verdict": A.VERDICT_CUT, "alternative": "B2B Marketing Exchange",
        "alternative_website": None, "alternative_note": None,
        "why": "The room is split across functions.",
        "name": "INBOUND (rebranding to UNBOUND) (Boston, MA)"}
    out = A.apply_audit([_c("INBOUND (rebranding to UNBOUND)", True)], audit)
    assert out == [] or out[0].get("audit_verdict") == A.VERDICT_CUT
    entry = [e for e in audit.get("cut") or []] if audit.get("cut") else []
    # The event is still cut, but for the reason the audit actually gave.
    assert not any(e.get("no_verdict") for e in entry), (
        "the event was recorded as never audited")


def test_the_alternative_survives_a_verdict_that_echoed_the_city():
    """The consequence of the miss, and the reason it mattered more than a
    wrong sentence: an unmatched verdict takes its named alternative with it,
    so the better event the audit had already identified is never promoted."""
    audit = {"error": None, "cut": [], "verdicts": {}}
    audit["verdicts"][A.name_key("Dreamforce (San Francisco, CA)")] = {
        "verdict": A.VERDICT_CUT, "alternative": "Tiny Vertical Summit",
        "alternative_website": "https://tiny.example",
        "alternative_note": None, "why": "Too broad.",
        "name": "Dreamforce (San Francisco, CA)"}
    A.apply_audit([_c("Dreamforce", True)], audit)
    wanted = A.alternatives_to_promote(
        {"cut": [dict(audit["verdicts"][A.name_key("Dreamforce (San Francisco, CA)")],
                      name="Dreamforce (San Francisco, CA)")]},
        [_c("Dreamforce", True)])
    assert [w["name"] for w in wanted] == ["Tiny Vertical Summit"]


def test_a_kept_verdict_that_echoes_the_city_keeps_its_event():
    audit = {"error": None, "verdicts": {}}
    audit["verdicts"][A.name_key("CES (Las Vegas, NV)")] = {
        "verdict": A.VERDICT_KEPT, "alternative": "A smaller show",
        "alternative_website": None, "alternative_note": None,
        "why": "The floor is genuinely the buyer.", "name": "CES (Las Vegas, NV)"}
    out = A.apply_audit([_c("CES", True)], audit)
    assert [c["name"] for c in out] == ["CES"], (
        "a kept marquee event was dropped because its verdict came back with "
        "the city attached")
    assert out[0]["audit_verdict"] == A.VERDICT_KEPT


def test_an_ambiguous_loose_match_is_refused_rather_than_guessed():
    """Two marquee events whose names contain one another are exactly where a
    loose match would staple one event's verdict onto the other. A wrong
    verdict is worse than the missing one this fallback exists to fix."""
    audit = {"error": None, "verdicts": {}}
    for nm in ("MarTech Summit (Boston, MA)", "MarTech Summit Europe (Berlin)"):
        audit["verdicts"][A.name_key(nm)] = {
            "verdict": A.VERDICT_KEPT, "alternative": "Something",
            "alternative_website": None, "alternative_note": None,
            "why": "w", "name": nm}
    out = A.apply_audit([_c("MarTech Summit", True)], audit)
    # Exactly one of the two verdicts is a real key match for this name, and
    # neither is, so it is cut as unaudited rather than given the wrong one.
    assert out == [], "an ambiguous verdict was applied anyway"


def test_an_exact_key_still_wins_over_a_loose_one():
    audit = {"error": None, "verdicts": {}}
    audit["verdicts"][A.name_key("Big Show")] = {
        "verdict": A.VERDICT_KEPT, "alternative": "Alt",
        "alternative_website": None, "alternative_note": None,
        "why": "exact", "name": "Big Show"}
    audit["verdicts"][A.name_key("Big Show Europe (Berlin)")] = {
        "verdict": A.VERDICT_CUT, "alternative": None,
        "alternative_website": None, "alternative_note": None,
        "why": "loose", "name": "Big Show Europe (Berlin)"}
    out = A.apply_audit([_c("Big Show", True)], audit)
    assert [c["name"] for c in out] == ["Big Show"]
    assert "exact" in out[0]["audit_note"], out[0]["audit_note"]


def test_a_cut_entry_still_names_the_event_it_cut(monkeypatch):
    """The name on a cut entry is not decoration. `alternatives_to_promote`
    reads it as `replaces`, and `promote_alternatives` then looks the replaced
    event up by that name to inherit its category, so a promoted alternative
    with no name to replace lands in the wrong slot on the report."""
    _stub(monkeypatch, {"audits": [
        {"name": "Dreamforce (San Francisco, CA)", "verdict": "cut",
         "why": "Too broad for this ICP.",
         "alternative": "Tiny Vertical Summit",
         "alternative_website": "https://tiny.example"}]})
    out = A.audit_famous([_c("Dreamforce", True, city="San Francisco, CA")], PROFILE)
    assert out["cut"], "nothing was recorded as cut"
    assert out["cut"][0].get("name"), "the cut entry does not say what was cut"
    wanted = A.alternatives_to_promote(out, [_c("Dreamforce", True)])
    assert wanted and wanted[0]["replaces"], (
        "the promotion does not know which event it is standing in for")


def test_a_verdict_for_a_different_edition_never_lands_on_this_one():
    """The loose match must not merge two editions of the same series. A
    verdict written about the Singapore edition, applied to the European one,
    would cut a real event on the strength of a judgement about a different
    room in a different market."""
    audit = {"error": None, "verdicts": {}}
    audit["verdicts"][A.name_key("MarTech Summit APAC (Singapore)")] = {
        "verdict": A.VERDICT_CUT, "alternative": None,
        "alternative_website": None, "alternative_note": None,
        "why": "Wrong market for this client.",
        "name": "MarTech Summit APAC (Singapore)"}
    out = A.apply_audit([_c("MarTech Summit Europe", True)], audit)
    assert out == [], (
        "the European edition survived, so it took the APAC verdict")
    entry = (audit.get("cut") or [])[-1] if audit.get("cut") else {}
    assert entry.get("no_verdict"), (
        "the event was cut on another edition's verdict rather than reported "
        "as unaudited: %r" % entry.get("why"))


# ── the city suffix, second occurrence ──────────────────────────────────
#
# The audit is SHOWN each marquee event as "Name (City) - audience note", so
# a reply echoing the name it was given comes back carrying the city.
# `_verdict_for` was taught to cope with that (see its docstring). The
# CATEGORY lookup in promote_alternatives was not, and it is the same root
# cause one step later:
#
#   name_key("Adobe Summit (Las Vegas)") -> "adobe las vegas"
#   name_key("Adobe Summit")             -> "adobe"
#
# The replaced event's category could not be found, so the promotion was
# refused for having no slot on the list. Measured in the live run of
# 2026-09-04: two real, upcoming, high-confidence replacements lost, both
# reported to the client as unconfirmable.

_LIVE_NAMES = [
    ("Adobe Summit (Las Vegas)", "Adobe Summit", "MAICON"),
    ("Content Marketing World (Denver, CO)", "Content Marketing World",
     "B2B Marketing Exchange"),
    ("UNBOUND (formerly INBOUND, by HubSpot) (Boston, MA)", "UNBOUND",
     "SaaStr Annual"),
]


@pytest.mark.parametrize("echoed,real,_alt", _LIVE_NAMES)
def test_a_city_suffixed_name_still_finds_the_row_it_names(echoed, real, _alt):
    from tracker.event_intel_discover import name_key
    pool = [{"name": real, "category": R.CAT_INDUSTRY_FLAGSHIP}]
    by = {name_key(c["name"]): c for c in pool}
    assert name_key(echoed) != name_key(real), (
        "%r no longer reproduces the mismatch this guards" % echoed)
    row = A._row_for(echoed, by)
    assert row is not None, (
        "the audit echoed back the name it was given and the lookup missed it")
    assert row["category"] == R.CAT_INDUSTRY_FLAGSHIP


def test_an_unrelated_name_is_not_loosely_matched_to_something():
    from tracker.event_intel_discover import name_key
    by = {name_key("Adobe Summit"): {"name": "Adobe Summit",
                                     "category": R.CAT_INDUSTRY_FLAGSHIP}}
    assert A._row_for("Totally Unrelated Expo", by) is None
    assert A._row_for("", by) is None


def test_an_ambiguous_loose_match_is_refused_rather_than_guessed():
    """Stapling one event's category onto another is worse than the missing
    promotion this repairs.

    "Adobe Experience Summit (Boston)" loosely matches BOTH "Adobe Summit"
    and "Adobe Experience Summit", which sit in different categories.
    Picking either would give a promoted event a category it was never in,
    and the store would accept it, so the wrongness would be invisible.

    The first fixture written for this used a pair whose `name_key` values
    COLLIDE ("MarTech Summit" / "MarTech Summit Europe" both key to
    "martech", because name_key strips region words). A colliding pair
    cannot be ambiguous in a name_key-keyed dict: it is one entry.
    """
    from tracker.event_intel_discover import name_key, names_match
    rows = [{"name": "Adobe Summit", "category": R.CAT_INDUSTRY_FLAGSHIP},
            {"name": "Adobe Experience Summit",
             "category": R.CAT_VERTICAL_SUMMIT}]
    by = {name_key(r["name"]): r for r in rows}
    q = "Adobe Experience Summit (Boston)"
    # The fixture has to be genuinely ambiguous or this tests nothing: two
    # DISTINCT keys, both loosely matched by the query.
    assert len(by) == 2, "the fixture names collide to one key"
    assert sum(1 for r in rows if names_match(q, r["name"])
               and name_key(q) != name_key(r["name"])) == 2
    assert A._row_for(q, by) is None, (
        "an ambiguous name was resolved by guessing")


def test_a_promotion_inherits_the_category_through_a_city_suffixed_name():
    """End to end through promote_alternatives, with the names exactly as the
    live audit produced them."""
    audit = {"cut": [{"name": "Adobe Summit (Las Vegas)",
                      "alternative": "MAICON",
                      "alternative_website": "https://maicon.example",
                      "alternative_note": "Tighter room.",
                      "why": "Adobe's own product conference."}]}
    pre_audit = [{"name": "Adobe Summit", "category": R.CAT_INDUSTRY_FLAGSHIP,
                  "famous": True}]

    def resolver(name, year_hint=None):
        return {"ok": True, "confidence": "high",
                "pages": [{"url": "https://maicon.example/agenda"}],
                "event": {"name": "MAICON", "website": "https://maicon.example",
                          "starts_on": _soon(180), "ends_on": _soon(182),
                          "city": "Cleveland", "format": "in_person",
                          "confidence": "high"}}

    out = A.promote_alternatives(audit, [], resolver=resolver,
                                 replaced_from=pre_audit)
    assert [c["name"] for c in out["promoted"]] == ["MAICON"], out["unconfirmed"]
    assert out["promoted"][0]["category"] == R.CAT_INDUSTRY_FLAGSHIP
    assert not out["unconfirmed"], (
        "a confirmed replacement was still refused: %s" % out["unconfirmed"])
