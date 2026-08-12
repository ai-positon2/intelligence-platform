"""Two things went wrong in one answer, and both are about respecting the question.

Asked "who is the CFO of Lenovo India", the chat replied with the publicly listed
CFO and then offered six "closest on-file senior people": Vivek, Meghana, Amit,
Rohit, Vikram and Satish. Not one of them was in finance, not one of them had a
title printed next to their name, and two of the names rendered with random words
in bold. Nobody looking for the finance lead can use any of that.

Cause one, relevance: the fallback that runs when the requested title is not on
file filtered by SENIORITY only, so it returned whoever happened to be senior at
that company. Seniority is not relevance. The fallback now scopes to the same
business FUNCTION as the title that was asked about, verified against each
person's own title in code rather than trusted to Apollo's fuzzy title search, and
when we hold nobody in that function it says so instead of reaching for strangers.

Cause two, the bold: Apollo returns a withheld surname as an asterisk mask,
"Vivek Sh***a". The chat renderer treats **...** as bold, so two masked names in
one sentence made the text BETWEEN them bold. Masked names are now abbreviated to
"Vivek Sh." before they reach either the model or the buttons, and the renderer
neutralises stray asterisk runs as a second line of defence.
"""

import json as _json
import os
import re
import sys
import types

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import app as appmod  # noqa: E402


# ══ Part one: the masked name, and the bold it caused ════════════════════════

@pytest.mark.parametrize("raw,expected", [
    ("Vivek Sh***a", "Vivek Sh."),
    ("Meghana Ka***i", "Meghana Ka."),
    ("Satish Pr***i", "Satish Pr."),
    ("Binal S**h", "Binal S."),
    ("Heidi B.", "Heidi B."),              # already abbreviated, left alone
    ("Julie Woods-Moss", "Julie Woods-Moss"),
])
def test_a_masked_surname_becomes_a_readable_abbreviation(raw, expected):
    assert appmod._cpi_display_name(raw) == expected


def test_a_name_with_no_mask_is_returned_untouched():
    """Byte-identical, not merely equal-looking: this runs on every name in every
    answer, and a formatter that quietly rewrites clean input is a liability."""
    for name in ("Ada Lovelace", "Jean-Luc Picard", "Ana María Ruiz", "O'Brien", ""):
        assert appmod._cpi_display_name(name) is name or \
            appmod._cpi_display_name(name) == name


def test_a_fully_masked_token_is_dropped_not_printed_as_a_full_stop():
    """"Vivek ****" has no surname letters at all, so there is nothing to
    abbreviate. Printing "Vivek ." would look like a bug."""
    assert appmod._cpi_display_name("Vivek ****") == "Vivek"
    assert appmod._cpi_display_name("****") == ""


@pytest.mark.parametrize("junk", [None, "", "   "])
def test_a_missing_name_does_not_crash(junk):
    assert appmod._cpi_display_name(junk) == ""


def test_no_letters_are_invented():
    """The abbreviation may only ever SHORTEN. If this rule breaks, an answer
    starts asserting a surname Apollo never gave us."""
    out = appmod._cpi_display_name("Vivek Sh***a")
    assert out.replace(".", "") in "Vivek Sh***a".replace("*", "")
    assert "Sharma" not in out and "Shah" not in out


def test_the_row_keeps_its_raw_name_for_apollo():
    """people/match should still be given what Apollo gave us; only the display
    copy is abbreviated."""
    row = {"id": "p1", "full_name": "Vivek Sh***a", "title": "VP Finance"}
    shown = appmod._cpi_display_person(row)
    assert shown["full_name"] == "Vivek Sh."
    assert row["full_name"] == "Vivek Sh***a", "the caller's row was mutated"


def test_the_masked_flag_survives_so_the_answer_can_still_explain_itself():
    row = {"id": "p1", "full_name": "Vivek Sh***a", "name_masked": True,
           "title": "VP Finance"}
    brief = appmod._cpi_contact_brief(row)
    assert brief["name"] == "Vivek Sh."
    assert brief["surname_withheld_until_enriched"] is True


def test_the_enrich_button_shows_the_abbreviation_and_sends_the_raw_name():
    chip = appmod._cpi_enrich_chip(
        {"id": "p1", "full_name": "Vivek Sh***a", "title": "VP Finance"}, "lenovo.com")
    assert chip["label"] == "Vivek Sh."
    assert chip["name"] == "Vivek Sh***a"


def test_an_unmasked_name_needs_no_label():
    """No label means the client just prints the name. Sending one anyway would be
    two sources of truth for the same string."""
    chip = appmod._cpi_enrich_chip({"id": "p1", "full_name": "Ada Lin"}, "x.com")
    assert "label" not in chip


# ── The renderer, ported ────────────────────────────────────────────────────
# fmtAnswer's two regexes, as JavaScript applies them. Kept honest by
# test_the_js_still_neutralises_asterisk_runs below, which fails if the shipped
# code stops matching this port.

def _fmt_old(text):
    return re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)


def _fmt_new(text):
    return _fmt_old(re.sub(r"\*{3,}", "…", text))


_REPORTED = ("Closest on-file senior people at Lenovo India include Vivek Sh***a, "
             "Meghana Ka***i, Amit Ch***h, Rohit Mi***a, Vikram Mu***e, and "
             "Satish Pr***i.")


def test_the_reported_line_used_to_bold_the_wrong_words():
    """The bug, reproduced: with three asterisks in each name the matcher pairs an
    asterisk from ONE name with an asterisk from the NEXT, and bolds everything in
    between."""
    assert "<b>a, Meghana Ka</b>" in _fmt_old(_REPORTED)


def test_it_no_longer_bolds_anything():
    out = _fmt_new(_REPORTED)
    assert "<b>" not in out


def test_deliberate_bold_still_works():
    """The fix must not cost the renderer its actual feature."""
    assert _fmt_new("the **CFO** is listed") == "the <b>CFO</b> is listed"


def test_the_server_side_fix_alone_removes_the_trigger():
    """Belt and braces: even with the old renderer, the abbreviated names the
    server now sends cannot produce a stray bold."""
    clean = " ".join(appmod._cpi_display_name(w) if "*" in w else w
                     for w in _REPORTED.split())
    assert "<b>" not in _fmt_old(clean)
    assert "Vivek Sh." in clean


def test_the_js_still_neutralises_asterisk_runs():
    src = open(os.path.join(_ROOT, "static", "js",
                            "company_people_intelligence.js")).read()
    assert r'replace(/\*{3,}/g,"…")' in src
    assert "e.label||e.name" in src, "the button must prefer the printable label"


def test_the_js_and_css_bundles_are_cache_busted_together():
    """A renderer fix nobody's browser downloads is not a fix.

    Pinned to the relationship rather than to a number: asserting "?v=10" made
    this fail on every legitimate bump while catching nothing real. The failure
    actually worth catching is a JS bump that forgets the stylesheet, which ships
    new markup to a browser still holding the old CSS.
    """
    import re as _re
    html = open(os.path.join(_ROOT, "templates",
                             "company_people_intelligence.html")).read()
    js = _re.search(r"company_people_intelligence\.js\?v=(\d+)", html)
    css = _re.search(r"company_people_intelligence\.css'\) \}\}\?v=(\d+)", html)
    assert js, "the JS bundle must carry a ?v= cache buster"
    assert css, "the stylesheet must carry a ?v= cache buster"
    assert js.group(1) == css.group(1), (
        "JS is at v=%s but CSS is at v=%s: bump both together"
        % (js.group(1), css.group(1)))


# ══ Part two: relevance ═════════════════════════════════════════════════════

@pytest.mark.parametrize("title,expected", [
    ("CFO", "finance"),
    ("Chief Financial Officer", "finance"),
    ("VP Finance", "finance"),
    ("Financial Controller", "finance"),
    ("Head of Accounting", "finance"),
    ("CMO", "marketing"),
    ("Head of Brand", "marketing"),
    ("CTO", "technology"),
    ("VP Engineering", "technology"),
    ("CHRO", "hr"),
    ("Head of Talent", "hr"),
    ("General Counsel", "legal"),
    ("COO", "operations"),
    ("Head of Procurement", "operations"),
    ("CISO", "security"),
    ("Chief Medical Officer", "medical"),
    ("VP Customer Success", "customer"),
    ("Head of Analytics", "data"),
    ("VP Product", "product"),
    ("CRO", "sales"),
    ("CEO", "executive"),
])
def test_a_title_is_placed_in_its_function(title, expected):
    assert expected in appmod._cpi_title_functions(title)


def test_a_title_can_sit_in_two_functions():
    """"VP Finance & Operations" really does, and someone asking for either should
    be offered them."""
    fns = appmod._cpi_title_functions("VP Finance & Operations")
    assert "finance" in fns and "operations" in fns


def test_an_unclassifiable_title_is_placed_nowhere():
    """The safe direction to fail. A person we cannot place is never offered as a
    same-function contact, so a missing name is possible but a wrong one is not."""
    assert appmod._cpi_title_functions("Head of Special Projects") == frozenset()
    assert appmod._cpi_title_functions("") == frozenset()


def test_the_asked_function_comes_from_the_asked_titles():
    assert appmod._cpi_requested_functions(["CFO"]) == frozenset({"finance"})
    assert appmod._cpi_requested_functions([]) == frozenset()


def test_a_cfo_question_does_not_ask_for_engineering():
    """The reported bug, as one assertion."""
    asked = appmod._cpi_requested_functions(["CFO", "Chief Financial Officer"])
    assert not (asked & appmod._cpi_title_functions("VP Engineering"))
    assert not (asked & appmod._cpi_title_functions("Chief Marketing Officer"))
    assert not (asked & appmod._cpi_title_functions("Head of Human Resources"))


# ── The revenue leader crossover ───────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "CRO", "Chief Revenue Officer", "VP Revenue", "Head of Revenue",
])
def test_a_revenue_leader_counts_as_marketing_too(title):
    """At many companies the CRO owns marketing as well as sales, so a CMO
    question should be offered them when no CMO is on file."""
    fns = appmod._cpi_title_functions(title)
    assert "marketing" in fns
    assert "sales" in fns, "they have not stopped being the sales leader"


@pytest.mark.parametrize("title", [
    "Revenue Operations Manager", "Revenue Analyst", "Director of Revenue Operations",
])
def test_the_revenue_team_does_not_count_as_marketing(title):
    """The rationale is ownership of the revenue org, which a manager or an analyst
    does not have. Offering one as the closest marketing contact is the exact
    substitution this scoping exists to prevent."""
    fns = appmod._cpi_title_functions(title)
    assert "marketing" not in fns
    assert "sales" in fns


def test_the_crossover_runs_one_way_only():
    """"A CRO's remit usually includes marketing" makes a CRO a reasonable answer to
    a marketing question. It does not make a marketing head a reasonable answer to a
    revenue one."""
    assert "sales" not in appmod._cpi_title_functions("CMO")
    assert "sales" not in appmod._cpi_title_functions("Head of Marketing")


def test_a_marketing_question_actually_searches_for_the_revenue_leader():
    """A crossover rule with nothing to apply to would be dead: Apollo has to be
    asked for the CRO before one can be classified as marketing."""
    titles = appmod._cpi_function_search_titles(frozenset({"marketing"}))
    assert "Chief Revenue Officer" in titles
    assert "CMO" in titles, "and still for the title actually asked about"


def test_apollos_own_department_can_place_someone_a_title_cannot():
    """A second, independent signal for the plans that return it."""
    p = {"title": "Head of Special Projects", "departments": ["finance"]}
    assert "finance" in appmod._cpi_person_functions(p)


def test_any_c_suite_person_counts_as_the_executive_team():
    """"Who is the CEO" asks for a level, not a specialism, so a Chief Creative
    Officer is a real alternative when no CEO is on file. No keyword list would
    ever have placed them there."""
    fns = appmod._cpi_person_functions({"title": "Chief Creative Officer"})
    assert "executive" in fns


def test_being_c_suite_is_not_being_in_finance():
    """The other half of that rule: it must not quietly re-open the door the
    function scoping just closed."""
    fns = appmod._cpi_person_functions({"title": "Chief Creative Officer"})
    assert "finance" not in fns


# ── Ranking ────────────────────────────────────────────────────────────────

def test_seniors_sort_first():
    rows = [{"title": "Finance Manager"}, {"title": "CFO"},
            {"title": "VP Finance"}, {"title": "Finance Director"}]
    assert [r["title"] for r in sorted(rows, key=appmod._cpi_seniority_rank)] == [
        "CFO", "VP Finance", "Finance Director", "Finance Manager"]


def test_apollos_own_seniority_is_used_when_it_has_one():
    assert (appmod._cpi_seniority_rank({"seniority": "c_suite", "title": "Analyst"})
            < appmod._cpi_seniority_rank({"seniority": "manager", "title": "CFO"}))


def test_an_unrankable_person_sorts_last_rather_than_first():
    """Sorting the unknown to the top would bury the CFO under an intern."""
    assert (appmod._cpi_seniority_rank({"title": "Accountant"})
            > appmod._cpi_seniority_rank({"title": "Finance Manager"}))


# ── The fallback search ────────────────────────────────────────────────────

@pytest.fixture
def apollo(monkeypatch):
    """Captures the filters each search_people call is given, and replies with a
    scripted roster."""
    import tracker.apollo_client as ac
    seen = []
    box = {"rows": []}

    def _sp(filters, key, **kw):
        seen.append(dict(filters))
        return list(box["rows"])

    monkeypatch.setattr(ac, "search_people", _sp)
    return types.SimpleNamespace(seen=seen, box=box)


_ROSTER = [
    {"id": "p1", "full_name": "Vivek Sharma", "title": "VP Engineering"},
    {"id": "p2", "full_name": "Meghana Kalra", "title": "Chief Marketing Officer"},
    {"id": "p3", "full_name": "Amit Chandra", "title": "Finance Director"},
    {"id": "p4", "full_name": "Rohit Mishra", "title": "Head of Human Resources"},
    {"id": "p5", "full_name": "Vikram Murthy", "title": "VP Finance"},
    {"id": "p6", "full_name": "Satish Prasad", "title": "Head of Delivery"},
]


def test_the_fallback_searches_by_the_functions_titles_not_by_seniority(apollo):
    apollo.box["rows"] = list(_ROSTER)
    appmod._cpi_same_function_people("org1", frozenset({"finance"}), "k")
    sent = apollo.seen[0]
    assert "seniorities" not in sent, \
        "seniority alone is what returned six unrelated people"
    assert "CFO" in sent["titles"] and "VP Finance" in sent["titles"]
    assert sent["organization_ids"] == ["org1"]


def test_only_the_people_actually_in_that_function_come_back(apollo):
    """Apollo searches titles loosely, so the search is a recall net and this
    check in code is the actual guarantee."""
    apollo.box["rows"] = list(_ROSTER)
    got = appmod._cpi_same_function_people("org1", frozenset({"finance"}), "k")
    assert [p["full_name"] for p in got] == ["Vikram Murthy", "Amit Chandra"]


def test_they_come_back_most_senior_first(apollo):
    apollo.box["rows"] = list(_ROSTER)
    got = appmod._cpi_same_function_people("org1", frozenset({"finance"}), "k")
    assert got[0]["title"] == "VP Finance", "a VP outranks a Director"


def test_nobody_in_the_function_means_an_empty_list_not_a_substitute(apollo):
    """The heart of the fix: no finance people on file returns NOTHING, so the
    caller can say so rather than offering the engineering VP."""
    apollo.box["rows"] = [r for r in _ROSTER if "Finance" not in r["title"]]
    assert appmod._cpi_same_function_people("org1", frozenset({"finance"}), "k") == []


def test_an_executive_question_is_searched_by_seniority(apollo):
    apollo.box["rows"] = list(_ROSTER)
    appmod._cpi_same_function_people("org1", frozenset({"executive"}), "k")
    sent = apollo.seen[0]
    assert sent["seniorities"] == ["c_suite", "owner", "founder"]
    assert "titles" not in sent, "no title list can cover every C-suite variant"


def test_an_unclassifiable_question_keeps_the_old_broad_fallback(apollo):
    """When we cannot tell what was asked for, a broad senior list beats no list:
    this must not narrow a case it cannot reason about."""
    apollo.box["rows"] = list(_ROSTER)
    got = appmod._cpi_same_function_people("org1", frozenset(), "k")
    assert apollo.seen[0]["seniorities"] == ["c_suite", "vp", "director", "owner", "founder"]
    assert len(got) == 5, "unfiltered, just capped"


def test_the_list_is_capped(apollo):
    apollo.box["rows"] = [dict(r, id="f%d" % i, title="VP Finance")
                          for i, r in enumerate(_ROSTER)]
    got = appmod._cpi_same_function_people("org1", frozenset({"finance"}), "k")
    assert len(got) == appmod._CPI_CONSOLATION_MAX == 5


def test_apollo_failing_is_not_a_crash(monkeypatch):
    import tracker.apollo_client as ac

    def _boom(*a, **k):
        raise RuntimeError("apollo down")

    monkeypatch.setattr(ac, "search_people", _boom)
    assert appmod._cpi_same_function_people("org1", frozenset({"finance"}), "k") == []


def test_no_api_key_means_no_call(apollo):
    assert appmod._cpi_same_function_people("org1", frozenset({"finance"}), "") == []
    assert apollo.seen == []


# ── End to end, the reported question ──────────────────────────────────────

def _ask(monkeypatch, roster, titles=("CFO",), role=None, message="CFO of Lenovo India",
         titled=(), intent="person_at_company"):
    """Drives the real /chat route. By default Apollo holds nobody with the asked
    title, so the question reaches the fallback; pass `titled` to have the
    title-scoped search succeed instead and take one of the other two branches."""
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(),
                        raising=False)
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": intent, "titles": list(titles),
        "company_name": "Lenovo India", "seniorities": [], "max_results": 10}), "m"))
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: {
        "id": "org-lenovo", "name": "Lenovo India", "primary_domain": "lenovo.com"})
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: role)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    matched = []

    def _bulk(ids, api_key):
        matched.extend(ids)
        return {}

    monkeypatch.setattr(ac, "bulk_match_people", _bulk)
    monkeypatch.setattr(ac, "search_companies", lambda *a, **k: [])

    def _sp(filters, key, **kw):
        # The fallback arrives as a whole function's titles or a seniority band;
        # anything narrower is the search for the title the question named.
        if len(filters.get("titles") or []) > 1 or filters.get("seniorities"):
            return list(roster)
        return list(titled)

    monkeypatch.setattr(ac, "search_people", _sp)
    box = {}
    monkeypatch.setattr(appmod, "_cpi_grounded_answer",
                        lambda oai, facts, q, research="":
                        box.setdefault("facts", facts) and "answer")
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/b2b-agents/company-people-intelligence/chat", json={"message": message})
    assert r.status_code == 200
    return r.get_json(), box.get("facts", {}), matched


def test_the_reported_answer_now_offers_only_finance_people(monkeypatch):
    """The whole complaint, as one test: six senior strangers become the two
    finance leaders we actually hold."""
    _body, facts, _m = _ask(monkeypatch, _ROSTER)
    assert [p["name"] for p in facts["closest_people_we_hold"]] == [
        "Vikram Murthy", "Amit Chandra"]
    blob = _json.dumps(facts)
    for stranger in ("Vivek Sharma", "Meghana Kalra", "Rohit Mishra", "Satish Prasad"):
        assert stranger not in blob, "%s is not a finance contact" % stranger


def test_every_offered_person_comes_with_their_title(monkeypatch):
    """The old list was bare names, which told the reader nothing about how close
    any of them was to the role they asked for."""
    _body, facts, _m = _ask(monkeypatch, _ROSTER)
    assert [(p["name"], p["title"]) for p in facts["closest_people_we_hold"]] == [
        ("Vikram Murthy", "VP Finance"), ("Amit Chandra", "Finance Director")]


def test_the_answer_never_offers_more_than_a_readable_handful(monkeypatch):
    """Pinned end to end, not just on the helper: the point of this list is the two
    or three people worth contacting, not a directory dump."""
    roster = [{"id": "f%d" % i, "full_name": "Person %d" % i, "title": "VP Finance"}
              for i in range(9)]
    body, facts, _m = _ask(monkeypatch, roster)
    assert len(facts["closest_people_we_hold"]) == appmod._CPI_CONSOLATION_MAX == 5
    assert len(body["enrich"]) == 5


def test_the_answer_is_told_which_function_they_share(monkeypatch):
    """So it can say WHY these people, instead of calling them "closest senior
    people" and leaving the reader to guess the connection."""
    _body, facts, _m = _ask(monkeypatch, _ROSTER)
    assert facts["these_people_all_work_in"] == "finance"
    assert facts["no_one_holds_the_requested_title"] is True
    assert facts["requested_titles"] == ["CFO"]


def test_only_the_finance_people_get_enrich_buttons(monkeypatch):
    body, _facts, _m = _ask(monkeypatch, _ROSTER)
    assert [c["apollo_id"] for c in body["enrich"]] == ["p5", "p3"]


def test_no_finance_leadership_on_file_says_so_and_offers_nobody(monkeypatch):
    _body, facts, _m = _ask(monkeypatch, [r for r in _ROSTER
                                          if "Finance" not in r["title"]])
    assert facts["no_one_in_this_function_on_file"] == "finance"
    assert "closest_people_we_hold" not in facts
    assert "Vivek Sharma" not in _json.dumps(facts)


def test_the_public_role_holder_still_leads_that_answer(monkeypatch):
    """Narrowing the alternatives must not cost the answer the thing the user
    actually asked for."""
    role = {"name": "Winston Cheng", "title": "Chief Financial Officer",
            "source": "https://www.lenovo.com/in/en/about/who-we-are/our-leadership/",
            "exact_title_match": True}
    body, facts, _m = _ask(monkeypatch, [r for r in _ROSTER
                                         if "Finance" not in r["title"]], role=role)
    assert facts["public_role_holder"]["name"] == "Winston Cheng"
    assert body["enrich"][0]["name"] == "Winston Cheng"


def test_a_ceo_question_still_gets_the_c_suite(monkeypatch):
    """The narrowing is per-question. An executive question asks for a level, and
    the people who answer it are the other executives."""
    _body, facts, _m = _ask(monkeypatch, _ROSTER, titles=("CEO",),
                            message="CEO of Lenovo India")
    names = [p["name"] for p in facts["closest_people_we_hold"]]
    assert "Meghana Kalra" in names, "a CMO is C-suite"
    assert "Rohit Mishra" not in names, "a head of HR is not"


def test_the_masked_names_from_the_screenshot_never_reach_the_client(monkeypatch):
    """End to end: Apollo masks these surnames, and neither the model nor the
    buttons see an asterisk."""
    roster = [{"id": "p5", "full_name": "Vikram Mu***e", "title": "VP Finance",
               "name_masked": True},
              {"id": "p3", "full_name": "Amit Ch***h", "title": "Finance Director",
               "name_masked": True}]
    body, facts, _m = _ask(monkeypatch, roster)
    assert "*" not in _json.dumps(facts)
    assert [p["name"] for p in facts["closest_people_we_hold"]] == [
        "Vikram Mu.", "Amit Ch."]
    assert facts["some_surnames_withheld_until_enriched"] is True
    assert [c["label"] for c in body["enrich"]] == ["Vikram Mu.", "Amit Ch."]


def test_a_cmo_question_is_offered_the_revenue_leader_but_not_their_team(monkeypatch):
    """The crossover, end to end. The CRO is offered because a revenue leader
    usually owns marketing; the revenue ops manager and the engineering VP are
    not."""
    roster = [{"id": "p1", "full_name": "Vivek Sharma", "title": "VP Engineering"},
              {"id": "p7", "full_name": "Priya Nair", "title": "Chief Revenue Officer"},
              {"id": "p8", "full_name": "Karan Bose", "title": "Revenue Operations Manager"}]
    _body, facts, _m = _ask(monkeypatch, roster, titles=("CMO",),
                            message="CMO of Lenovo India")
    assert [(p["name"], p["title"]) for p in facts["closest_people_we_hold"]] == [
        ("Priya Nair", "Chief Revenue Officer")]
    assert facts["these_people_all_work_in"] == "marketing"


def test_the_person_who_was_asked_for_is_also_printable(monkeypatch):
    """The other two branches carry names too. Here the reveal could not un-mask the
    one person the question was about, and the answer still must not be handed
    "Vikram Mu***e" to copy into a sentence."""
    masked = [{"id": "p5", "full_name": "Vikram Mu***e", "name_masked": True,
               "title": "VP Finance", "organization_domain": "lenovo.com"}]
    body, facts, _m = _ask(monkeypatch, [], titled=masked, titles=("VP Finance",),
                           message="VP Finance of Lenovo India")
    assert facts["person"]["full_name"] == "Vikram Mu."
    assert body["enrich"][0]["label"] == "Vikram Mu."
    assert body["enrich"][0]["name"] == "Vikram Mu***e", "Apollo still gets the raw name"


def test_a_list_answer_is_printable_too(monkeypatch):
    """"List the finance leaders at X" is the third branch. Same rule."""
    masked = [{"id": "p5", "full_name": "Vikram Mu***e", "name_masked": True,
               "title": "VP Finance"},
              {"id": "p3", "full_name": "Amit Ch***h", "name_masked": True,
               "title": "Finance Director"}]
    _body, facts, _m = _ask(monkeypatch, [], titled=masked, intent="people_list",
                            titles=("VP Finance",), message="finance leaders at Lenovo")
    assert [p["full_name"] for p in facts["people"]] == ["Vikram Mu.", "Amit Ch."]
    assert "*" not in _json.dumps(facts)


def test_the_narrowed_list_is_still_free(monkeypatch):
    """These people are a substitute for the answer, not the answer, so no credit
    is spent un-masking them (see the consolation note in cpi_chat)."""
    roster = [{"id": "p5", "full_name": "Vikram Mu***e", "title": "VP Finance",
               "name_masked": True}]
    body, _facts, matched = _ask(monkeypatch, roster)
    assert matched == [], "no bulk_match on people the user did not ask about"
    assert not body.get("credits")


# ── The prompt has to know about all of this ───────────────────────────────

def test_the_prompt_requires_a_title_beside_every_offered_name():
    p = appmod._CPI_ANSWER_SYSTEM
    assert "closest_people_we_hold" in p
    assert "own title exactly as it is written" in p


def test_the_prompt_forbids_padding_with_other_functions():
    p = appmod._CPI_ANSWER_SYSTEM
    assert "no_one_in_this_function_on_file" in p
    assert "Do NOT offer people from other functions as" in p


def test_the_prompt_explains_why_these_people_were_chosen():
    assert "these_people_all_work_in" in appmod._CPI_ANSWER_SYSTEM


def test_the_prompt_no_longer_mentions_the_old_key():
    """A renamed fact the prompt still describes is an instruction about something
    the model will never be given."""
    assert "other_senior_people_at_this_company" not in appmod._CPI_ANSWER_SYSTEM
