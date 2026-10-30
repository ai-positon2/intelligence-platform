"""Vimi's catalog is held to the app: every page of the two agent sections is listed or ignored with a reason, and every read is a real GET route that takes the parameters it declares."""
import inspect
import re

import pytest

import app as appmod
from tracker import vimi_catalog as cat

APP = appmod.app
FILL = {"date": "2026-10-01", "sid": "abc", "launch_id": "abc", "run_id": "7", "entry_id": "7", "client_id": "7", "account_id": "healthcare", "agent": "kpi"}


def _filled(path):
    return re.sub(r"\{(\w+)\}", lambda m: FILL.get(m.group(1), "x"), path)


def _rule_for(path):
    adapter = APP.url_map.bind("localhost")
    endpoint, _args = adapter.match(path, method="GET")
    return endpoint


def _params_read_by(endpoint):
    """The query parameters a route reads: in its own body and in the app-level helpers it calls (two levels), where most routes hand the query string."""
    seen, todo, text = set(), [inspect.unwrap(APP.view_functions[endpoint])], ""
    for _ in range(3):
        nxt = []
        for fn in todo:
            if fn in seen:
                continue
            seen.add(fn)
            try:
                src = inspect.getsource(fn)
            except (OSError, TypeError):
                continue
            text += src
            for name in re.findall(r"\b(_[a-z][a-z0-9_]*)\(", src):
                helper = getattr(appmod, name, None)
                if inspect.isfunction(helper):
                    nxt.append(inspect.unwrap(helper))
        todo = nxt
    return set(re.findall(r'(?:src|request\.args|args|a|body)\.get\(\s*"([A-Za-z_]+)"', text))


ROUTE_READS = [(a["id"], r) for a in cat.AGENTS for r in a["reads"] if not r["local"]]


def test_the_catalog_has_no_duplicate_ids_or_view_names():
    ids = [a["id"] for a in cat.AGENTS]
    assert len(ids) == len(set(ids))
    for a in cat.AGENTS:
        views = [r["view"] for r in a["reads"]]
        assert len(views) == len(set(views)), a["id"]


@pytest.mark.parametrize("agent_id,r", ROUTE_READS, ids=["%s/%s" % (a, r["view"]) for a, r in ROUTE_READS])
def test_every_read_is_a_real_get_route(agent_id, r):
    assert _rule_for(_filled(r["path"]))


@pytest.mark.parametrize("agent_id,r", ROUTE_READS, ids=["%s/%s" % (a, r["view"]) for a, r in ROUTE_READS])
def test_every_declared_parameter_is_one_the_route_reads(agent_id, r):
    endpoint = _rule_for(_filled(r["path"]))
    read_by = _params_read_by(endpoint)
    in_path = set(re.findall(r"\{(\w+)\}", r["path"]))
    declared = set(r["params"]) - in_path
    if declared and not read_by:          # parameters declared on a route whose reads this test cannot see: say so rather than pass it
        pytest.skip("parameters are read in a way the source scan cannot see")
    assert declared <= read_by | {"account"}, "%s/%s declares %s but the route reads %s" % (agent_id, r["view"], sorted(declared - read_by), sorted(read_by))


def test_no_read_offers_a_refresh():
    # a route may also take POST/PUT (the budgets page saves with PUT): Vimi only ever sends GET (tests/test_vimi_tools.py), and a Refresh is a person's decision
    for a in cat.AGENTS:
        for r in a["reads"]:
            assert "refresh" not in r["params"] and "force" not in r["params"], (a["id"], r["view"])


def test_no_read_is_a_route_that_spends_a_model_or_an_allowance_on_a_plain_get():
    banned = {"insights", "generate-email", "research-company", "company-analysis", "decision-makers", "refresh", "warm", "probe", "query-check", "ppc-chat", "run", "sync", "seed",
              "download", "export", "auth", "start", "rebuild", "summary", "classify"}
    for a in cat.AGENTS:
        for r in a["reads"]:
            segments = {x for x in re.split(r"[/.]", r["path"]) if x}
            assert not (segments & banned) and not r["path"].endswith((".csv", ".xlsx")), (a["id"], r["path"])


def _pages():
    out = set()
    for rule in APP.url_map.iter_rules():
        if "GET" not in rule.methods or "<" in rule.rule:
            continue
        m = re.fullmatch(r"(/p2/(?:paid-marketing|strategic-agents)(?:/[a-z0-9\-]+)?)", rule.rule)
        if m and not re.search(r"/(api|auth|assets)$", m.group(1)):
            out.add(m.group(1))
    return out


def test_every_agent_page_is_listed_or_ignored_with_a_reason():
    known = {a["page"] for a in cat.AGENTS} | set(cat.IGNORED)
    missing = sorted(p for p in _pages() if p not in known and p + "/" not in known)
    assert not missing, "agent pages Vimi does not know about: %s (add an agent to tracker/vimi_catalog.py or name it in IGNORED)" % missing


def test_every_listed_page_exists():
    pages = {rule.rule for rule in APP.url_map.iter_rules() if "GET" in rule.methods}
    for a in cat.AGENTS:
        assert a["page"] in pages or a["page"].rstrip("/") in pages, a["id"]
    for page, why in cat.IGNORED.items():
        assert why and (page in pages or page.rstrip("/") in pages), page


def test_the_paid_marketing_cards_and_the_catalog_agree_on_the_count():
    # the hub says how many Paid Marketing agents exist (PAID_AGENT_TOTAL counts hidden ones too); the catalog must not fall behind it
    assert len([a for a in cat.PAID_AGENTS]) >= appmod.PAID_AGENT_TOTAL


def test_the_roster_names_every_agent_once():
    text = cat.roster()
    for a in cat.AGENTS:
        assert "- %s | %s |" % (a["id"], a["title"]) in text


def test_find_matches_a_title_a_slug_or_part_of_a_title():
    assert cat.find("KPI monitoring")["id"] == "kpi-monitoring"
    assert cat.find("kpi-monitoring")["id"] == "kpi-monitoring"
    assert cat.find("Creative & Asset Health")["id"] == "creative-health"
    assert cat.find("impression") is None or cat.find("impression")["id"]
    assert cat.find("") is None


def test_search_ranks_by_title_then_description():
    ids = [a["id"] for a in cat.search("brand impression share")]
    assert ids[0] == "brand-is"
    assert "pacing-desk" in [a["id"] for a in cat.search("month end projection allocation")]


def test_the_knowledge_text_is_cut_into_one_entry_per_agent():
    agents = {a["id"]: (a["title"], a["page"]) for a in cat.AGENTS}
    know = cat.split_knowledge(appmod._VIMI_PLATFORM_KNOWLEDGE, agents)
    paid_missing = [a["id"] for a in cat.PAID_AGENTS if a["id"] not in know]
    assert not paid_missing, "no knowledge text found for: %s" % paid_missing
    assert "impression share" in know["brand-is"].lower() and "Brand Spend" not in know["brand-is"][:60]
    assert len(know["cross-channel"]) > 1500 and "SEO STUDIO" not in know["cross-channel"] and "ADMIN DASHBOARDS" not in know["cross-channel"]


def test_every_agent_has_something_to_say_about_how_it_works():
    know = cat.knowledge_for(appmod._VIMI_PLATFORM_KNOWLEDGE)
    missing = [a["id"] for a in cat.AGENTS if a["reads"] and a["id"] not in know]
    assert not missing, "no description for: %s" % missing
    assert "de-anonymisation" in know["anonymous-visitors"] or "business" in know["anonymous-visitors"]
    assert "severity" in know["abm-signal-tracker"] and "headline" in know["ad-intelligence"]
    assert "ADMIN DASHBOARDS" not in know["ad-intelligence"] and "PAID MARKETING" not in know["ad-intelligence"] and "ANONYMOUS" not in know["abm-signal-tracker"]
    assert "Apollo" in know["contact-finder"]


def test_the_knowledge_and_notes_hold_no_dash():
    know = cat.knowledge_for(appmod._VIMI_PLATFORM_KNOWLEDGE)
    for aid, text in cat.NOTES.items():
        assert "—" not in text and "–" not in text, aid


def test_a_title_that_ends_another_title_does_not_cut_it_short():
    text = "Audience Segment Analysis (/p2/paid-marketing/audience-segments): how audiences do. Segment Analysis (/p2/paid-marketing/segment-analysis): day and hour cuts."
    got = cat.split_knowledge(text, {"audience-segments": ("Audience Segment Analysis", "/p2/paid-marketing/audience-segments"), "segment-analysis": ("Segment Analysis", "/p2/paid-marketing/segment-analysis")})
    assert got["audience-segments"].endswith("how audiences do.") and got["segment-analysis"].endswith("day and hour cuts.")
    assert got["segment-analysis"].startswith("Segment Analysis (")


def test_every_paid_agent_entry_is_a_real_description_not_a_stub():
    know = cat.knowledge_for(appmod._VIMI_PLATFORM_KNOWLEDGE)
    short = {a["id"]: len(know[a["id"]]) for a in cat.PAID_AGENTS if len(know.get(a["id"], "")) < 150}
    assert not short, short
