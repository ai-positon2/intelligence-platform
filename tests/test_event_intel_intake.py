"""Drafting a client profile from a name and a URL.

The recommendation play opens on thirteen fields, only two of which are
required, and the person filling them in is usually answering questions about
someone else's business. This module reads the client's own site and fills the
form in for them.

Everything under test here is one idea: a draft is a PROPOSAL, and a proposal
that cannot be told apart from a confirmed fact is worse than a blank form.
So a field with nothing behind it must arrive empty and say so, a reply that
read no page must be thrown away, and the one answer that decides which side
of the trade-show floor gets scored must come back as an argument for a person
to accept rather than as a decision already taken.
"""

import json

import pytest

from tracker import claude_websearch
from tracker import event_intel_intake as I
from tracker import event_intel_rubric as R


SITE = "https://northwind.example"


def _body(**over):
    b = {"wrong_company": None,
         "what_they_sell": "Analytics for insurance claims teams.",
         "classification": R.CLASS_B2B_OTHER_FUNCTION,
         "classification_why": "They sell to claims operations, not marketing.",
         "classification_confidence": "high",
         "buyer_roles": "VP Claims, Head of Claims Ops",
         "verticals": "insurance, insurtech",
         "acv_band": None, "sales_cycle": None,
         "geo_scope": "North America",
         "evidence": {"buyer_roles": "Their customers page names claims leaders.",
                      "verticals": "Every case study is an insurer.",
                      "geo_scope": "Offices listed in Boston and Toronto only."},
         "unknown": ["acv_band", "sales_cycle"],
         "sources": ["https://northwind.example/customers"],
         "note": ""}
    b.update(over)
    return b


def _stub(monkeypatch, body=None, error=None, text=None, searches=3):
    def fake_ask(system, user, **kw):
        return {"text": text if text is not None else json.dumps(body or _body()),
                "raw": "", "error": error, "stop_reason": "end_turn",
                "text_block_count": 1, "tool_version": "v",
                "search_count": searches, "tool_errors": [], "usage": {}}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)


# ── the prompt ────────────────────────────────────────────────────────────

def test_the_prompt_offers_every_classification_the_rubric_knows():
    """A menu missing an option is a menu that pushes the model onto a wrong
    one. The rubric owns the list, so the prompt has to read it from there."""
    menu = I._classification_menu()
    for k in R.CLASSIFICATIONS:
        assert k in menu
        assert R.CLASSIFICATION_LABELS[k] in menu


def test_the_prompt_tells_the_model_a_blank_field_is_a_good_answer():
    """The single rule this module lives or dies by. Without it the model
    fills a deal size it cannot possibly have read."""
    p = I._SYSTEM.format(client_name="X", website=SITE,
                         classification_menu=I._classification_menu())
    assert "MUST BE null" in p
    assert "Leaving a field blank is a good answer" in p
    assert "usually NOT published" in p


def test_the_prompt_makes_it_check_it_read_the_right_company():
    """Two firms sharing a name is common and the site is the only tell. This
    agent has already shipped a fix for exactly that trap elsewhere."""
    p = I._SYSTEM.format(client_name="X", website=SITE,
                         classification_menu=I._classification_menu())
    assert "MAKE SURE IT IS THE RIGHT COMPANY" in p
    assert "wrong_company" in p


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_draft_without_a_website_is_refused_before_any_call(monkeypatch):
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("a call was made without a site")

    monkeypatch.setattr(claude_websearch, "ask", boom)
    out = I.draft_profile("Northwind", "northwind.example")
    assert out["error"]["kind"] == "bad_request"
    assert "tells two companies with the same name apart" in out["error"]["detail"]
    assert called["n"] == 0


def test_a_draft_that_ran_no_search_is_discarded(monkeypatch):
    """Recall about a company with this name, not a reading of the site. That
    is precisely how the wrong firm's buyers get filled into the form."""
    _stub(monkeypatch, searches=0)
    out = I.draft_profile("Northwind", SITE)
    assert out["error"]["kind"] == "ungrounded"
    assert out["draft"] == {}


def test_a_draft_that_cites_no_page_is_discarded(monkeypatch):
    _stub(monkeypatch, _body(sources=[]))
    out = I.draft_profile("Northwind", SITE)
    assert out["error"]["kind"] == "ungrounded"
    assert "nothing here anyone could check it against" in out["error"]["detail"]


def test_a_non_http_source_does_not_count_as_a_citation(monkeypatch):
    _stub(monkeypatch, _body(sources=["javascript:alert(1)", 7]))
    out = I.draft_profile("Northwind", SITE)
    assert out["error"]["kind"] == "ungrounded"


def test_the_wrong_company_is_reported_rather_than_described(monkeypatch):
    """The failure that would otherwise be invisible: a complete, confident,
    well-sourced profile of a different business with the same name."""
    _stub(monkeypatch, _body(
        wrong_company="northwind.example sells garden furniture; the name you "
                      "gave is an analytics firm.",
        buyer_roles="Head of Retail"))
    out = I.draft_profile("Northwind Analytics", SITE)
    assert out["error"]["kind"] == "wrong_company"
    assert "garden furniture" in out["error"]["detail"]
    assert out["draft"] == {}, "a wrong-company draft was still handed back"
    assert out["sources"], "the reader cannot check the claim without the page"


def test_an_unreadable_reply_fills_nothing(monkeypatch):
    _stub(monkeypatch, text="Sure! Here is what I found about Northwind.")
    out = I.draft_profile("Northwind", SITE)
    assert out["error"]["kind"] == "unparsable"
    assert out["draft"] == {}


# ── what survives, and what does not ──────────────────────────────────────

def test_a_field_with_no_evidence_is_emptied_rather_than_shown_as_read(monkeypatch):
    """The prompt asks for evidence per field. Asking is not getting, and a
    filled field nobody can trace is exactly the guess this module exists to
    keep out of the form."""
    _stub(monkeypatch, _body(acv_band="$50k to $150k", unknown=[]))
    out = I.draft_profile("Northwind", SITE)
    assert out["draft"]["acv_band"] is None
    assert "acv_band" in out["unknown"]


def test_a_field_with_evidence_survives_with_it(monkeypatch):
    """The control. If evidence were never read, every field would empty and
    the module would return a blank form very expensively."""
    _stub(monkeypatch, _body(
        acv_band="$40k to $120k",
        evidence=dict(_body()["evidence"],
                      acv_band="Their pricing page lists Growth at $40k a year."),
        unknown=[]))
    out = I.draft_profile("Northwind", SITE)
    assert out["draft"]["acv_band"] == "$40k to $120k"
    assert "pricing page" in out["evidence"]["acv_band"]
    assert "acv_band" not in out["unknown"]


def test_a_field_left_blank_without_being_declared_is_still_reported_unknown(monkeypatch):
    """An empty box on a form reads as something the person forgot. Naming it
    is what turns it into "their site does not say"."""
    _stub(monkeypatch, _body(sales_cycle=None, unknown=[]))
    out = I.draft_profile("Northwind", SITE)
    assert "sales_cycle" in out["unknown"]


def test_evidence_is_not_kept_for_a_field_that_ended_up_empty(monkeypatch):
    """A sentence about a value that is not on the form has nowhere honest to
    be shown, and shown anyway it implies the field was filled."""
    _stub(monkeypatch, _body(
        sales_cycle=None,
        evidence=dict(_body()["evidence"], sales_cycle="Roughly two quarters.")))
    out = I.draft_profile("Northwind", SITE)
    assert "sales_cycle" not in out["evidence"]


def test_every_drafted_field_is_one_the_profile_form_actually_has(monkeypatch):
    """A draft key the form cannot show is a value that silently never reaches
    the profile."""
    from tracker import event_intel_store as S
    _stub(monkeypatch)
    out = I.draft_profile("Northwind", SITE)
    for f in out["draft"]:
        assert f in S._PROFILE_TEXT_FIELDS, f


def test_unknown_only_ever_names_fields_the_form_has(monkeypatch):
    """A model that answers `unknown: ["their mood"]` must not put that on a
    page that renders each entry as a field nobody could fill."""
    _stub(monkeypatch, _body(unknown=["acv_band", "their mood", "revenue"]))
    out = I.draft_profile("Northwind", SITE)
    assert "their mood" not in out["unknown"]
    assert "revenue" not in out["unknown"]
    assert "acv_band" in out["unknown"]


# ── the classification ────────────────────────────────────────────────────

def test_the_classification_comes_back_with_its_reasoning(monkeypatch):
    _stub(monkeypatch)
    out = I.draft_profile("Northwind", SITE)
    assert out["classification"] == R.CLASS_B2B_OTHER_FUNCTION
    assert "claims operations" in out["classification_why"]
    assert out["classification_confidence"] == "high"


def test_a_classification_the_rubric_does_not_know_becomes_no_proposal(monkeypatch):
    """`orientation_for` raises on an unknown value rather than defaulting,
    because a default silently scores the opposite side of the floor. A draft
    must not be able to smuggle one past it either."""
    _stub(monkeypatch, _body(classification="b2b_probably"))
    out = I.draft_profile("Northwind", SITE)
    assert out["classification"] is None


@pytest.mark.parametrize("k", list(R.CLASSIFICATIONS))
def test_every_real_classification_is_accepted(k, monkeypatch):
    _stub(monkeypatch, _body(classification=k))
    out = I.draft_profile("Northwind", SITE)
    assert out["classification"] == k


def test_this_module_never_writes_a_profile():
    """The rule that keeps `normalise_profile` the single validator, and keeps
    a draft from becoming a second, quieter way to create a profile.

    Read off the source rather than the behaviour, because the failure this
    guards against is somebody adding the save later for convenience.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(I))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
    assert not any("store" in m for m in imported), (
        "the intake module imported the store; drafting must not write. "
        "Imported: %s" % sorted(imported))

    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for forbidden in ("save_profile", "update_profile", "save_run", "update_run"):
        assert forbidden not in called, forbidden
