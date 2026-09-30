"""Discovery's find-and-confirm flow: the audited defects, one test each.

Model calls are stubbed; the stub picks its reply from the prompt it is
sent, never from call order, because categories run in parallel.
"""

import json
import re
from datetime import date, timedelta

import pytest

from tracker import claude_websearch
from tracker import event_intel_discover as D
from tracker import event_intel_rubric as R


PROFILE = {"client_name": "Northwind", "website": "https://northwind.example",
           "classification": R.CLASS_B2B_TO_MARKETING,
           "buyer_roles": "VP Marketing", "verticals": "fintech",
           "geo_scope": "North America", "window_months": 12}

_TARGET = re.compile(r"THE EVENT TO CONFIRM: (.+)")
FIND = "YOUR ONLY JOB IS TO NAME CANDIDATES"
REFORMAT = "You restate ONE research reply"


def _res(text, error=None, searches=3, budget=False, **over):
    out = {"text": text, "raw": text, "error": error, "stop_reason": "end_turn",
           "text_block_count": 1, "tool_version": "v", "tool_errors": [],
           "usage": {"input_tokens": 100, "output_tokens": 10},
           "search_count": searches, "budget_spent": budget}
    out.update(over)
    return out


def _find(cands, complete=True, note="n"):
    body = {"candidates": cands, "note": note}
    if complete is not None:
        body["search_complete"] = complete
    return json.dumps(body)


def _cand(name, site=None):
    return {"name": name, "why": "w",
            "website": site or "https://%s.example/" % re.sub(r"\W", "", name.lower())}


def _event(name, site=None, **over):
    site = site or "https://%s.example/" % re.sub(r"\W", "", name.lower())
    ev = {"name": name, "website": site, "sources": [site], "country": "USA",
          "starts_on": (date.today() + timedelta(days=30)).isoformat(),
          "ends_on": (date.today() + timedelta(days=31)).isoformat(),
          "confidence": "high", "category_fit": "fits"}
    ev.update(over)
    return ev


def _confirm(event=None, confirmed=True, reason=None):
    body = {"confirmed": confirmed, "event": event, "facts_complete": True}
    if reason:
        body["reject_reason"] = reason
    return json.dumps(body)


def _stub(monkeypatch, find, confirm=None):
    """`find(user) -> result dict`; `confirm(name) -> result dict`."""
    log = {"finds": [], "confirms": []}

    def fake(system, user, **kw):
        if FIND in system:
            log["finds"].append(user)
            return find(user)
        m = _TARGET.search(system)
        name = m.group(1).strip() if m else ""
        log["confirms"].append(name)
        if confirm:
            return confirm(name)
        return _res(_confirm(_event(name)))

    monkeypatch.setattr(claude_websearch, "ask", fake)
    monkeypatch.setattr(D, "FIND_RETRY_BACKOFF_SECONDS", 0)
    return log


def _limited(text):
    return _res(text, error={"kind": claude_websearch.ERR_SEARCH_LIMIT,
                             "detail": "too_many_requests after 3 searches"})


# ── 2. a rate-limited search keeps what it named ─────────────────────────

def test_a_rate_limited_find_that_named_events_keeps_them_without_a_retry(monkeypatch):
    log = _stub(monkeypatch, lambda u: _limited(_find([_cand("Alpha Summit"),
                                                       _cand("Beta Forum")],
                                                      complete=False))
                if "already found" not in u else _res(_find([], complete=True)))
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    first = [u for u in log["finds"] if "already found" not in u]
    assert len(first) == 1, "a reply that named events was retried and lost"
    assert sorted(e["name"] for e in r["events"]) == ["Alpha Summit", "Beta Forum"]
    assert r["status"] == D.STATUS_PARTIAL
    assert "part-way" in r["detail"]
    for word in ("search_limit", "too_many", "max_", "rate"):
        assert word not in r["detail"].lower()


def test_a_rate_limited_find_with_nothing_named_is_retried_once(monkeypatch):
    log = _stub(monkeypatch, lambda u: _limited(_find([], complete=False)))
    r = D.propose_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert len(log["finds"]) == 2
    assert r["status"] == D.STATUS_ERROR


def test_the_retry_does_not_claim_the_first_search_spent_its_budget():
    """A spent budget is never retried, so on the retry path that claim was
    always false."""
    narrowed = D._find_user(R.CAT_EMERGING, D.RETRY_PER_CATEGORY, narrowed=True)
    assert "whole budget" not in narrowed
    assert "did not finish" in narrowed


def test_the_retry_asks_for_fewer_names_than_the_first_attempt():
    first = D._find_user(R.CAT_EMERGING, D.PASS_WANT)
    narrowed = D._find_user(R.CAT_EMERGING, D.RETRY_PER_CATEGORY, narrowed=True)
    assert "up to %d more" % (D.FIND_ASK - D.PASS_WANT) in first
    assert "more candidates" not in narrowed and "and no others" in narrowed
    assert D.RETRY_PER_CATEGORY < D.FIND_ASK


# ── 3. malformed find replies are not an empty market ─────────────────────

def test_search_complete_written_as_a_string_is_not_a_finished_search(monkeypatch):
    _stub(monkeypatch, lambda u: _res(json.dumps(
        {"candidates": [], "note": "", "search_complete": "false"})))
    r = D.propose_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["status"] == D.STATUS_ERROR


def test_candidates_that_cannot_be_read_are_an_error_not_an_empty_market(monkeypatch):
    _stub(monkeypatch, lambda u: _res(json.dumps(
        {"candidates": ["Alpha Summit", "Beta Forum"], "note": "",
         "search_complete": True})))
    r = D.propose_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["status"] == D.STATUS_ERROR
    assert "could not be read" in r["detail"]


def test_a_real_true_with_nothing_found_is_still_empty(monkeypatch):
    _stub(monkeypatch, lambda u: _res(_find([], complete=True)))
    assert D.propose_category(R.CAT_VERTICAL_SUMMIT, PROFILE)["status"] == D.STATUS_EMPTY


# ── 4. a later pass that failed makes the category partial ────────────────

def test_rejected_candidates_and_a_failed_later_pass_are_partial_not_empty(monkeypatch):
    def find(u):
        if "already found" in u:
            return _res("", error={"kind": "transport", "detail": "reset"},
                        searches=0)
        return _res(_find([_cand("Alpha Summit"), _cand("Beta Forum")]))
    _stub(monkeypatch, find, confirm=lambda n: _res(_confirm(
        None, confirmed=False, reason="the 2025 edition was the last one")))
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["events"] == [] and len(r["rejected"]) == 2
    assert r["status"] == D.STATUS_PARTIAL
    assert "none qualified" not in r["detail"]
    assert "later search" in r["detail"]


def test_rejected_candidates_after_clean_passes_are_still_empty(monkeypatch):
    _stub(monkeypatch, lambda u: _res(_find([] if "already found" in u
                                            else [_cand("Alpha Summit")])),
          confirm=lambda n: _res(_confirm(None, confirmed=False,
                                          reason="discontinued")))
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["status"] == D.STATUS_EMPTY


# ── 6. later passes dedupe by event, not by exact key ─────────────────────

def test_a_regional_edition_found_in_a_later_pass_is_kept(monkeypatch):
    def find(u):
        if "already found" not in u:
            return _res(_find([_cand("Money20/20 USA", "https://us.money2020.example/")]))
        if "Money20/20 Europe" not in u:
            return _res(_find([_cand("Money20/20 Europe", "https://eu.money2020.example/")]))
        return _res(_find([_cand("Money20/20 Asia", "https://asia.money2020.example/")]))
    log = _stub(monkeypatch, find)
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert sorted(log["confirms"]) == ["Money20/20 Asia", "Money20/20 Europe",
                                       "Money20/20 USA"]
    assert len(log["finds"]) == 3, "the dropped edition ended the search early"
    assert len(r["events"]) == 3


def test_the_later_pass_prompt_allows_a_regional_edition():
    u = D._find_more_user(R.CAT_VERTICAL_SUMMIT, D.PASS_WANT, ["Money20/20 USA"])
    assert "regional edition" in u and "may be named" in u
    assert "regional version" not in u


def test_a_city_suffixed_repeat_is_not_confirmed_twice(monkeypatch):
    def find(u):
        if "already found" not in u:
            return _res(_find([_cand("Fintech Meetup", "https://fintechmeetup.example/")]))
        return _res(_find([_cand("Fintech Meetup Las Vegas",
                                 "https://www.fintechmeetup.example/vegas")]))
    log = _stub(monkeypatch, find)
    D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert log["confirms"] == ["Fintech Meetup"]


# ── 9 and 11. names beyond a pass are carried, not re-searched ────────────

def test_names_beyond_the_first_pass_fill_the_next_without_a_search(monkeypatch):
    five = [_cand(n) for n in ("Alpha Summit", "Beta Forum", "Gamma Expo",
                               "Delta Congress", "Epsilon Show")]
    log = _stub(monkeypatch, lambda u: _res(_find([] if "already found" in u
                                                  else five)))
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    # Pass 2 is filled from the carried names; pass 3 has one carried name,
    # so it searches once, finds nothing new, and confirms the carried one.
    assert len(log["finds"]) == 2
    assert sorted(log["confirms"]) == sorted(c["name"] for c in five)
    assert r["proposed"] == 5 <= D.PER_CATEGORY
    assert "Alpha Summit; Beta Forum; Gamma Expo; Delta Congress; Epsilon Show" \
        in log["finds"][1], "the later pass was not told what was already named"


def test_the_find_call_asks_for_more_names_than_a_pass_confirms():
    u = D._find_user(R.CAT_VERTICAL_SUMMIT, D.PASS_WANT)
    assert "%d STRONGEST candidate events" % D.PASS_WANT in u
    assert D.FIND_ASK > D.PASS_WANT
    assert "never spend a search only to lengthen the list" in u


def test_the_worst_case_run_stays_within_todays_ceiling(monkeypatch):
    """Every path at its maximum: the first find broken and retried, every
    later pass searching, every confirm unreadable and restated. The ceiling
    before this change was 96 calls and 360 searches per run."""
    seq = {"n": 0}
    calls = {"n": 0, "searches": 0}

    def fake(system, user, **kw):
        calls["n"] += 1
        calls["searches"] += kw.get("max_uses") or 0
        if REFORMAT in system:
            m = re.search(r"THE REPLY:\n(.+)", user)
            return _res(_confirm(_event(m.group(1).strip())), searches=0)
        if FIND in system:
            if "already found" not in user and "second attempt" not in user:
                return _res(_find([], complete=False))
            seq["n"] += 2
            return _res(_find([_cand("Alpha%d Summit" % seq["n"]),
                               _cand("Beta%d Forum" % seq["n"])]),
                        searches=D.FIND_MAX_USES)
        m = _TARGET.search(system)
        return _res(m.group(1).strip(), searches=D.CONFIRM_MAX_USES)

    monkeypatch.setattr(claude_websearch, "ask", fake)
    monkeypatch.setattr(D, "FIND_RETRY_BACKOFF_SECONDS", 0)
    out = D.discover(PROFILE)
    assert calls["n"] == 96 and calls["searches"] == 360, calls
    assert out["spend"]["calls"] == 96


# ── 7. a confirmation may rename, never swap ──────────────────────────────

def test_a_confirmation_that_swaps_the_event_is_not_kept(monkeypatch):
    _stub(monkeypatch, lambda u: _res(_find([] if "already found" in u else
                                            [_cand("FinTech Connect",
                                                   "https://fintechconnect.example/")])),
          confirm=lambda n: _res(_confirm(_event("Money20/20 Europe",
                                                 "https://money2020.example/"))))
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["events"] == []
    [u] = r["unverified"]
    assert u["name"] == "FinTech Connect"
    assert "identity changed during checking" in u["reason"]


def test_a_rename_on_the_proposals_own_host_is_kept(monkeypatch):
    _stub(monkeypatch, lambda u: _res(_find([] if "already found" in u else
                                            [_cand("NW Fin Day",
                                                   "https://nwfin.example/day")])),
          confirm=lambda n: _res(_confirm(_event("Northwest Finance Forum",
                                                 "https://nwfin.example/"))))
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert [e["name"] for e in r["events"]] == ["Northwest Finance Forum"]


# ── 8. a non-ISO start date still gets the organizer's dates ──────────────

def test_a_month_only_start_date_is_recovered(monkeypatch):
    seen = []
    monkeypatch.setattr(D, "_recover_dates",
                        lambda ev, today=None: seen.append(ev["starts_on"]))
    _stub(monkeypatch, lambda u: _res(_find([] if "already found" in u
                                            else [_cand("Alpha Summit")])),
          confirm=lambda n: _res(_confirm(_event(n, starts_on="March 2027",
                                                 ends_on=None))))
    D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert seen == ["March 2027"]


# ── 9. booleans are parsed strictly ───────────────────────────────────────

@pytest.mark.parametrize("value,expected", [(True, True), ("true", True),
                                            ("false", False), (False, False),
                                            ("yes", False), (1, False),
                                            (None, False)])
def test_organizer_run_and_famous_are_strict_booleans(value, expected):
    ev = D._clean_event(dict(_event("Alpha Summit"), organizer_run=value,
                             famous=value), R.CAT_VERTICAL_SUMMIT)
    assert ev["organizer_run"] is expected and ev["famous"] is expected


# ── 12. a citation must trace to what the searches returned ───────────────

def _one(monkeypatch, confirm):
    return _stub(monkeypatch, lambda u: _res(_find([] if "already found" in u
                                                   else [_cand("Alpha Summit")])),
                 confirm=confirm)


def test_a_confirmation_citing_only_pages_nobody_searched_is_not_kept(monkeypatch):
    _one(monkeypatch, lambda n: _res(_confirm(_event(n)),
                                     result_urls=["https://elsewhere.example/x"]))
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["events"] == []
    assert "searches returned" in r["unverified"][0]["reason"]


def test_a_citation_on_a_searched_host_is_kept(monkeypatch):
    _one(monkeypatch, lambda n: _res(_confirm(_event(n)),
                                     result_urls=["https://alphasummit.example/agenda"]))
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert [e["name"] for e in r["events"]] == ["Alpha Summit"]


def test_the_restatement_checks_urls_against_the_searches_not_the_prose(monkeypatch):
    reply = "Alpha Summit is real, see https://invented.example/p and more."
    restated = _confirm(_event("Alpha Summit", "https://invented.example/p"))
    monkeypatch.setattr(D, "_ask", lambda *a, **k: _res(restated, searches=0))
    parsed, _ = D._reformat_confirm(reply, ["https://alphasummit.example/"])
    assert parsed["event"]["sources"] == []
    assert parsed["event"]["website"] is None
    parsed, _ = D._reformat_confirm(reply)  # nothing recorded: prose fallback
    assert parsed["event"]["sources"] == ["https://invented.example/p"]


# ── 12. one date for the whole run ────────────────────────────────────────

def test_discover_states_one_date_in_every_prompt(monkeypatch):
    days = iter(["2026-09-30"] + ["2026-10-01"] * 500)
    monkeypatch.setattr(D, "_today", lambda: next(days))
    stated = set()

    def fake(system, user, **kw):
        stated.update(re.findall(r"TODAY IS (\d{4}-\d\d-\d\d)", system))
        if FIND in system:
            return _res(_find([] if "already found" in user else [_cand("Alpha Summit")]))
        return _res(_confirm(_event("Alpha Summit")))

    monkeypatch.setattr(claude_websearch, "ask", fake)
    D.discover(PROFILE)
    assert stated == {"2026-09-30"}, "a run crossing midnight changed its prompts"


def test_a_pinned_run_date_on_the_profile_is_honoured(monkeypatch):
    monkeypatch.setattr(D, "_today", lambda: "2026-10-05")
    assert D._run_date({"as_of": "2026-09-30"}) == "2026-09-30"
    assert D._run_date({"as_of": "not a date"}) == "2026-10-05"
    assert "TODAY IS 2026-09-30" in D.find_system(R.CAT_EMERGING,
                                                  dict(PROFILE, as_of="2026-09-30"))
