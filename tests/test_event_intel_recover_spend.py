"""Search recovery: what it costs, and what its ledger note says.

Recovery was the one model-calling stage with no spend accounting, and its
failure note carried "kind: detail" (an error class and the provider's own
message) onto a source ledger a reader sees.
"""

import json

from tracker import claude_websearch
from tracker import event_intel_recover as R


def _stub(monkeypatch, **res):
    def fake(system, user, **kw):
        out = {"text": json.dumps({"rows": [
                   {"org_name": "Acme", "role": "exhibitor",
                    "found_at": "https://news.example/list"}], "note": ""}),
               "error": None, "search_count": 4,
               "usage": {"input_tokens": 9000, "output_tokens": 800}}
        out.update(res)
        return out
    monkeypatch.setattr(claude_websearch, "ask", fake)


def test_a_recovery_reports_what_it_cost(monkeypatch):
    _stub(monkeypatch)
    got = R.recover_page("https://ev.example/exh", "exhibitors", "Ev")
    assert got["rows"]
    assert got["spend"] == {"calls": 1, "input_tokens": 9000,
                            "output_tokens": 800, "cache_read_tokens": 0,
                            "searches": 4}


def test_every_early_return_carries_its_cost_too(monkeypatch):
    for over in ({"search_count": 0}, {"text": "not json"},
                 {"error": {"kind": "transport", "detail": "HTTP 529 overloaded"},
                  "text": ""}):
        _stub(monkeypatch, **over)
        got = R.recover_page("https://ev.example/exh", "exhibitors", "Ev")
        assert got["spend"]["calls"] == 1, over


def test_a_refused_recovery_costs_no_call(monkeypatch):
    _stub(monkeypatch, error={"kind": claude_websearch.ERR_ACCOUNT_BUDGET,
                              "detail": "The account event research budget has been reached"},
          text="", usage={})
    assert R.recover_page("https://ev.example/exh", "exhibitors", "Ev")["spend"]["calls"] == 0


def test_a_failed_recovery_note_is_reader_english(monkeypatch):
    _stub(monkeypatch, error={"kind": "transport", "detail": "HTTP 529 overloaded"},
          text="")
    note = R.recover_page("https://ev.example/exh", "exhibitors", "Ev")["source"]["note"]
    assert note.startswith("Recovery by search failed: ")
    assert "529" not in note and "transport" not in note and "overloaded" not in note
    assert "connection to the search service failed" in note
