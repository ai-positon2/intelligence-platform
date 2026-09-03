"""Drafting a client profile from a name and a URL.

The recommendation play opens on thirteen fields, only two of which are
required, and the person filling them in is usually answering questions about
someone else's business. This module fetches the client's own pages and fills
the form in from them.

Two ideas are under test.

A draft is a PROPOSAL, and a proposal that cannot be told apart from a
confirmed fact is worse than a blank form. So a field with nothing behind it
must arrive empty and say so, and the one answer that decides which side of
the trade-show floor gets scored must come back as an argument for a person to
accept rather than as a decision already taken.

And the model reads what WE fetched. It is given no search tool, so the only
alternative to the supplied pages is its own memory of a company with this
name, which is exactly the thing that must never reach a form field. The first
version of this module let the model do its own searching; it took between 29
seconds and over ten minutes, and once came back starved.
"""

import json

import pytest

from tracker import claude_websearch
from tracker import event_intel_harvest as H
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
         "note": ""}
    b.update(over)
    return b


def _page(url, status=None, text="Northwind Analytics. " * 60, note=""):
    return {"url": url, "status": status or H.SOURCE_OK, "http_status": 200,
            "text": text, "note": note, "truncated": False, "spa": None}


# The offsite entries deliberately carry labels and paths that WOULD match a
# hint, AND paths that do not collide with the on-site links above them.
#
# Two versions of this got it wrong. The first pointed them at /start and
# /northwind, which match nothing, so they were excluded for being
# uninteresting. The second reused /pricing and /customers, which the
# dedupe-by-path already had. Both times the same-host guard could be deleted
# without a single test noticing.
HOME_TEXT = ("Northwind Analytics\n"
             "Pricing [https://northwind.example/pricing]\n"
             "Customers [https://northwind.example/customers]\n"
             "About us [https://northwind.example/company/about]\n"
             "Pricing [https://docs.northwind.example/pricing-plans]\n"
             "Our customers [https://twitter.com/case-studies]\n"
             "Home [https://northwind.example/]\n") + "Claims analytics. " * 60


def _stub(monkeypatch, body=None, error=None, text=None, pages=None):
    """Stub both halves: the fetcher and the tool-free model call."""
    fetched = {}

    def fake_fetch(url):
        if pages is not None and url in pages:
            return pages[url]
        if url.rstrip("/") == SITE.rstrip("/"):
            return _page(url, text=HOME_TEXT)
        return _page(url)

    def fake_ask(system, user, **kw):
        fetched["kw"] = kw
        fetched["user"] = user
        fetched["system"] = system
        return {"text": text if text is not None else json.dumps(body or _body()),
                "raw": "", "error": error, "stop_reason": "end_turn",
                "text_block_count": 1, "tool_version": None,
                "search_count": 0, "tool_errors": [], "usage": {}}

    monkeypatch.setattr(H, "fetch_page", fake_fetch)
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    return fetched


# ── the model is not allowed to go looking ────────────────────────────────

def test_the_model_is_given_no_search_tool(monkeypatch):
    """The design, in one number. `claude_websearch.ask` omits the tool
    entirely at max_uses=0, so the model cannot complete a partial picture
    from somewhere the reader cannot see."""
    seen = _stub(monkeypatch)
    I.draft_profile("Northwind", SITE)
    assert seen["kw"]["max_uses"] == 0
    assert I.MAX_USES == 0


def test_the_pages_we_fetched_are_what_the_model_is_given(monkeypatch):
    seen = _stub(monkeypatch)
    I.draft_profile("Northwind", SITE)
    assert "=== https://northwind.example ===" in seen["user"]
    assert "=== https://northwind.example/pricing ===" in seen["user"]


def test_the_prompt_says_these_pages_are_everything(monkeypatch):
    """Without it, a model with no tool still answers from recall and never
    mentions that it did."""
    seen = _stub(monkeypatch)
    I.draft_profile("Northwind", SITE)
    assert "THE PAGES BELOW ARE EVERYTHING YOU HAVE" in seen["system"]
    assert "no search tool" in seen["system"]


def test_the_prompt_offers_every_classification_the_rubric_knows():
    """A menu missing an option is a menu that pushes the model onto a wrong
    one. The rubric owns the list, so the prompt has to read it from there."""
    menu = I._classification_menu()
    for k in R.CLASSIFICATIONS:
        assert k in menu
        assert R.CLASSIFICATION_LABELS[k] in menu


def test_the_prompt_tells_the_model_a_blank_field_is_a_good_answer():
    p = I._SYSTEM.format(client_name="X", website=SITE,
                         classification_menu=I._classification_menu())
    assert "MUST BE null" in p
    assert "Leaving a field blank is a good answer" in p
    assert "usually NOT published" in p


def test_the_prompt_makes_it_check_it_read_the_right_company():
    p = I._SYSTEM.format(client_name="X", website=SITE,
                         classification_menu=I._classification_menu())
    assert "MAKE SURE IT IS THE RIGHT COMPANY" in p
    assert "wrong_company" in p


def test_the_budget_never_reaches_the_prompt(monkeypatch):
    """A model that knows the client has $40k describes a $60k sponsorship
    differently, and this module never has a reason to know it."""
    seen = _stub(monkeypatch)
    I.draft_profile("Northwind", SITE)
    assert "budget" not in seen["system"].lower()


# ── which pages get read ──────────────────────────────────────────────────

def test_the_links_worth_following_are_picked_off_the_homepage():
    got = I.pick_links(HOME_TEXT, SITE)
    assert "https://northwind.example/pricing" in got
    assert "https://northwind.example/customers" in got
    assert "https://northwind.example/company/about" in got


def test_another_subdomain_is_not_this_company_s_site():
    """A docs site or a status page is a different property. Following one is
    how a profile ends up describing something the company merely also owns."""
    got = I.pick_links(HOME_TEXT, SITE)
    assert not any("docs.northwind.example" in u for u in got)


def test_an_offsite_link_is_never_followed():
    got = I.pick_links(HOME_TEXT, SITE)
    assert not any("twitter.com" in u for u in got)


def test_the_homepage_is_not_followed_back_to_itself():
    got = I.pick_links(HOME_TEXT, SITE)
    assert not any(u.rstrip("/") == SITE.rstrip("/") for u in got)


def test_pricing_is_read_before_the_about_page():
    """Deal size is the field hardest to fill and the one most often left
    blank, so the page most likely to answer it goes first when the budget of
    pages runs out."""
    got = I.pick_links(HOME_TEXT, SITE, limit=1)
    assert got == ["https://northwind.example/pricing"]


def test_no_more_pages_are_read_than_the_cap_allows(monkeypatch):
    _stub(monkeypatch)
    out = I.draft_profile("Northwind", SITE)
    assert len(out["pages"]) <= I.MAX_PAGES


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_draft_without_a_website_is_refused_before_anything_is_fetched(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(H, "fetch_page",
                        lambda u: called.__setitem__("n", called["n"] + 1))
    out = I.draft_profile("Northwind", "northwind.example")
    assert out["error"]["kind"] == "bad_request"
    assert "tells two companies with the same name apart" in out["error"]["detail"]
    assert called["n"] == 0


def test_a_site_that_cannot_be_read_is_reported_rather_than_guessed_at(monkeypatch):
    """Plenty of sites render their text in the browser. Saying so is a worse
    feature and a better answer than a profile assembled from whatever the
    model happens to remember about a company with this name."""
    _stub(monkeypatch, pages={SITE: _page(
        SITE, status=H.SOURCE_BLOCKED, text="",
        note="Returned only 12 characters of readable text, so its list is "
             "rendered by JavaScript after load.")})
    out = I.draft_profile("Northwind", SITE)
    assert out["error"]["kind"] == "unreadable"
    assert "rendered by JavaScript" in out["error"]["detail"]
    assert out["draft"] == {}


def test_an_upstream_failure_keeps_the_pages_it_did_read(monkeypatch):
    """The reader is owed the difference between "we never got there" and "we
    read it and the answer was unusable"."""
    _stub(monkeypatch, error={"kind": "transport", "detail": "HTTP 503"})
    out = I.draft_profile("Northwind", SITE)
    assert out["error"]["kind"] == "transport"
    assert [p["url"] for p in out["pages"]][0] == SITE


def test_the_wrong_company_is_reported_rather_than_described(monkeypatch):
    """The failure that would otherwise be invisible: a complete, confident,
    well-sourced profile of a different business with the same name."""
    _stub(monkeypatch, _body(
        wrong_company="These pages sell garden furniture; the name you gave "
                      "is an analytics firm.",
        buyer_roles="Head of Retail"))
    out = I.draft_profile("Northwind Analytics", SITE)
    assert out["error"]["kind"] == "wrong_company"
    assert "garden furniture" in out["error"]["detail"]
    assert out["draft"] == {}, "a wrong-company draft was still handed back"
    assert out["sources"], "the reader cannot check the claim without the pages"


def test_an_unreadable_reply_fills_nothing(monkeypatch):
    _stub(monkeypatch, text="Sure! Here is what I found about Northwind.")
    out = I.draft_profile("Northwind", SITE)
    assert out["error"]["kind"] == "unparsable"
    assert out["draft"] == {}


# ── what survives, and what does not ──────────────────────────────────────

def test_the_sources_are_the_pages_we_read_not_what_the_model_says(monkeypatch):
    """The honesty upgrade the rework bought. The model was handed a fixed set
    of pages and had no way to open another, so what was read is a fact this
    module owns rather than a claim it takes on trust."""
    _stub(monkeypatch, _body(sources=["https://somewhere.else/invented"]))
    out = I.draft_profile("Northwind", SITE)
    assert "https://somewhere.else/invented" not in out["sources"]
    assert SITE in out["sources"]


def test_a_page_that_refused_us_is_still_listed_with_its_reason(monkeypatch):
    """"We tried the pricing page and it refused us" is the difference between
    a blank deal size and an unexplained one."""
    _stub(monkeypatch, pages={
        "https://northwind.example/pricing": _page(
            "https://northwind.example/pricing", status=H.SOURCE_BLOCKED,
            text="", note="Server refused the request (HTTP 403).")})
    out = I.draft_profile("Northwind", SITE)
    tried = {p["url"]: p for p in out["pages"]}
    assert "https://northwind.example/pricing" in tried
    assert "403" in tried["https://northwind.example/pricing"]["note"]
    assert "https://northwind.example/pricing" not in out["sources"], (
        "a page we could not read was reported as one we read")


def test_a_field_with_no_evidence_is_emptied_rather_than_shown_as_read(monkeypatch):
    """With no search tool the only place an unsupported value can have come
    from is the model's own memory of a company with this name."""
    _stub(monkeypatch, _body(acv_band="$50k to $150k", unknown=[]))
    out = I.draft_profile("Northwind", SITE)
    assert out["draft"]["acv_band"] is None
    assert "acv_band" in out["unknown"]


def test_a_field_with_evidence_survives_with_it(monkeypatch):
    """The control. If evidence were never read, every field would empty and
    the module would return a blank form very efficiently."""
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
    _stub(monkeypatch, _body(
        sales_cycle=None,
        evidence=dict(_body()["evidence"], sales_cycle="Roughly two quarters.")))
    out = I.draft_profile("Northwind", SITE)
    assert "sales_cycle" not in out["evidence"]


def test_every_drafted_field_is_one_the_profile_form_actually_has(monkeypatch):
    from tracker import event_intel_store as S
    _stub(monkeypatch)
    out = I.draft_profile("Northwind", SITE)
    for f in out["draft"]:
        assert f in S._PROFILE_TEXT_FIELDS, f


def test_unknown_only_ever_names_fields_the_form_has(monkeypatch):
    """A model answering `unknown: ["their mood"]` must not put that on a page
    that renders each entry as a field nobody could fill."""
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


# ── budgets and boundaries ────────────────────────────────────────────────

def test_the_call_is_given_room_to_answer_and_time_to_finish(monkeypatch):
    """Both of these were guessed first and both guesses were wrong against
    the live API while every stubbed test stayed green: 2,500 output tokens
    died on max_tokens, and ask()'s own 280 second default would have cut off
    a 450 second call."""
    seen = _stub(monkeypatch)
    I.draft_profile("Northwind", SITE)
    assert seen["kw"]["max_tokens"] >= 12000
    assert seen["kw"]["timeout"] >= 120


def test_this_module_never_writes_a_profile():
    """The rule that keeps `normalise_profile` the single validator, and keeps
    a draft from becoming a second, quieter way to create a profile.

    Read off the source, because the failure this guards against is somebody
    adding the save later for convenience.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(I))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported |= {a.name for a in node.names}
    assert not any("store" in m for m in imported), (
        "the intake module imported the store; drafting must not write. "
        "Imported: %s" % sorted(imported))

    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for forbidden in ("save_profile", "update_profile", "save_run", "update_run"):
        assert forbidden not in called, forbidden


# ── the site's chrome is not the site's content ───────────────────────────
#
# The first live draft of a real company filled ONE of five fields and said
# exactly why: "the fetched pages are mostly repeated global navigation rather
# than full page bodies". Six pages of a marketing site share a header, a
# mega-menu and a footer, so every page was spending its character budget on
# the same menu and none of them reached its own body.

def _p(url, lines):
    return _page(url, text="\n".join(lines))


NAV = ["Home", "Pricing", "Customers", "About", "Careers", "Legal"]


def test_the_shared_menu_is_dropped_from_every_page_but_the_first():
    pages = [_p("https://a.example", NAV + ["We sell claims analytics."]),
             _p("https://a.example/pricing", NAV + ["Growth is $40k a year."]),
             _p("https://a.example/customers", NAV + ["Used by four insurers."])]
    bodies = I.strip_shared_lines(pages)
    assert "Growth is $40k a year." in bodies["https://a.example/pricing"]
    assert "Pricing" not in bodies["https://a.example/pricing"]
    assert "Used by four insurers." in bodies["https://a.example/customers"]


def test_the_homepage_keeps_its_navigation_because_that_is_its_content():
    """The menu is where a company lists the industries it sells into. The
    only filled field in that first live draft came out of exactly that, so
    the nav is kept once, on the page where it is the content."""
    pages = [_p("https://a.example", NAV + ["We sell claims analytics."]),
             _p("https://a.example/pricing", NAV + ["Growth is $40k a year."]),
             _p("https://a.example/customers", NAV + ["Used by four insurers."])]
    bodies = I.strip_shared_lines(pages)
    assert "Customers" in bodies["https://a.example"]
    assert "Careers" in bodies["https://a.example"]


def test_two_pages_are_too_few_to_tell_chrome_from_content():
    """A line shared by two pages is as likely to be the thing they have in
    common because it matters."""
    pages = [_p("https://a.example", ["Shared", "Home body"]),
             _p("https://a.example/x", ["Shared", "X body"])]
    bodies = I.strip_shared_lines(pages)
    assert "Shared" in bodies["https://a.example/x"]


def test_a_page_that_is_nothing_but_chrome_is_not_silently_dropped():
    """Empty would remove it from the corpus with no trace. "We read this and
    it said nothing new" is worth the few hundred characters."""
    pages = [_p("https://a.example", NAV + ["Body"]),
             _p("https://a.example/x", NAV),
             _p("https://a.example/y", NAV + ["Y body"])]
    bodies = I.strip_shared_lines(pages)
    assert bodies["https://a.example/x"].strip(), (
        "an all-chrome page came back empty and would vanish from the corpus")


def test_a_page_body_survives_into_the_corpus_the_model_is_given(monkeypatch):
    """The end of the chain, not the helper. A stripper nothing calls is worth
    nothing."""
    # The homepage carries the real linked menu, because that is what
    # `pick_links` reads to decide where to go next. The inner pages repeat it
    # as plain text, which is what a fetched page's chrome looks like.
    nav_links = [
        "Pricing [https://northwind.example/pricing]",
        "Customers [https://northwind.example/customers]",
        "About us [https://northwind.example/company/about]",
    ]
    pages = {
        SITE: _p(SITE, nav_links + ["We sell claims analytics."]),
        "https://northwind.example/pricing":
            _p("https://northwind.example/pricing",
               nav_links + ["Careers", "Growth is $40k a year."]),
        "https://northwind.example/customers":
            _p("https://northwind.example/customers",
               nav_links + ["Careers", "Used by four insurers."]),
        "https://northwind.example/company/about":
            _p("https://northwind.example/company/about",
               nav_links + ["Careers", "Founded 2015."]),
    }
    seen = _stub(monkeypatch, pages=pages)
    I.draft_profile("Northwind", SITE)
    assert "Growth is $40k a year." in seen["user"]
    assert "Used by four insurers." in seen["user"]
    # The menu reaches the model once. It is content on the homepage, which
    # does not carry the "Careers" line, and chrome everywhere else.
    assert "Careers" not in seen["user"], (
        "shared chrome reached the model instead of the pages' own bodies")
    assert seen["user"].count("Pricing [https://northwind.example/pricing]") == 1


def test_a_link_is_scored_on_its_own_label_not_its_neighbours():
    """A real run followed /support/ while looking for the page that says who
    they sell to, because a fixed window backwards from the URL swept in the
    neighbouring menu items."""
    # `/resources` is on nobody's skip list and matches no hint of its own, so
    # the ONLY thing that can keep it out is reading its own label. The first
    # version of this test used /support, which the skip list excludes anyway,
    # so it passed with the label fix reverted.
    text = ("Who we serve [https://a.example/industries]\n"
            "Resources [https://a.example/resources]\n")
    got = I.pick_links(text, "https://a.example")
    assert "https://a.example/industries" in got
    assert "https://a.example/resources" not in got, (
        "a link was picked on the words belonging to the link before it")


@pytest.mark.parametrize("path", [
    "/terms-of-services", "/privacy-policy", "/legal", "/cookie-settings",
    "/blog/why-we-rebranded", "/careers", "/support", "/docs/getting-started",
])
def test_a_page_that_never_says_who_they_sell_to_is_not_read(path):
    """Page slots are scarce. A live draft spent one of five on
    /terms-of-services, which matched the "services" hint and is a page about
    arbitration venues."""
    text = "Our services [https://a.example%s]\n" % path
    assert I.pick_links(text, "https://a.example") == []


@pytest.mark.parametrize("path", [
    "/pricing", "/customers", "/solutions/healthcare", "/industries",
    "/product/apm", "/about", "/web-master-services",
])
def test_the_pages_that_do_say_are_still_read(path):
    """The control. An exclusion list with no counterweight quietly starves
    the draft of every page worth reading."""
    text = "Link [https://a.example%s]\n" % path
    assert I.pick_links(text, "https://a.example") == ["https://a.example" + path]
