"""Strategy & Campaign Book: what the page may ask for, the steps of a build, campaign names and money (tracker/paid_strategy.py)."""
import datetime as dt

import pytest

from tracker import paid_strategy as ps

TODAY = dt.date(2026, 10, 16)


def ok(**kw):
    base = {"name": "Acme Dental", "website": "acmedental.com"}
    base.update(kw)
    return ps.clean_inputs(base, today=TODAY)


# ── the inputs ──────────────────────────────────────────────────────────────

def test_a_name_and_a_website_are_enough():
    i = ok()
    assert i["name"] == "Acme Dental" and i["website"] == "https://acmedental.com/" and i["goal"] == "leads"
    assert i["launch"] == "2026-10-30"                         # two weeks out when none is given
    assert i["campaignTypes"] == [] and i["competitors"] == [] and i["budget"] is None


@pytest.mark.parametrize("raw,msg", [({"website": "x.com"}, "name"), ({"name": "A", "website": "x.com"}, "name"), ({"name": "Acme"}, "something to work from"),
                                      ({"name": "Acme", "website": "not a site"}, "web address"), ({"name": "Acme", "account": "123"}, "ten digits"),
                                      ({"name": "Acme", "website": "x.com", "goal": "fame"}, "goal"), ({"name": "Acme", "website": "x.com", "budget": "lots"}, "number"),
                                      ({"name": "Acme", "website": "x.com", "budget": "0"}, "more than 0"), ({"name": "Acme", "website": "x.com", "launch": "soon"}, "2026-11-30"),
                                      ({"name": "Acme", "website": "x.com", "launch": "2020-01-01"}, "from today"),
                                      ({"name": "Acme", "website": "x.com", "campaignTypes": ["tv"]}, "campaign types")])
def test_bad_inputs_say_what_is_wrong(raw, msg):
    with pytest.raises(ps.BadPayload) as e:
        ps.clean_inputs(raw, today=TODAY)
    assert msg in str(e.value)


def test_a_brief_or_an_account_alone_is_enough():
    assert ok(website="", brief="A family dental practice in Austin that wants more implant patients.")["brief"].startswith("A family")
    assert ok(website="", account="1234567890")["account"] == "1234567890"
    with pytest.raises(ps.BadPayload):
        ok(website="", brief="too short")


def test_websites_are_cleaned_and_only_public_looking_ones_pass():
    assert ps.clean_url("http://WWW.Example.com/a//b?x=1#y") == "https://www.example.com/a/b"
    for bad in ("localhost", "http://127.0.0.1/", "https://user:pw@example.com/", "ftp://example.com", "example.com:8080", "http://exa mple.com", "https://-bad.com"):
        with pytest.raises(ps.BadPayload):
            ps.clean_url(bad)
    assert ps.clean_url("") == ""
    assert ps.host_of("https://www.Example.com/x") == "example.com"


def test_competitors_may_be_addresses_or_names_and_repeats_go():
    i = ok(competitors="smileaustin.com\nAnother Dental, https://www.smileaustin.com/x ;another dental")
    assert i["competitors"] == [{"name": "smileaustin.com", "url": "https://smileaustin.com/"}, {"name": "Another Dental", "url": ""}]
    with pytest.raises(ps.BadPayload):
        ok(competitors=["c%d.com" % n for n in range(9)])
    assert ok(competitors="nonsense")["competitors"] == [{"name": "nonsense", "url": ""}]       # a bare word is a name
    with pytest.raises(ps.BadPayload):
        ok(competitors=5)


def test_a_research_card_that_cannot_work_is_turned_off():
    i = ok(website="", brief="x" * 60)
    assert i["research"]["website"] is False and i["research"]["history"] is False and i["research"]["seasonality"] is False
    assert i["research"]["competitors"] and i["research"]["social"] and i["research"]["keywords"] and i["research"]["market"]
    j = ok(account="1234567890", research={"social": False, "market": 0})
    assert j["research"]["history"] and j["research"]["seasonality"] and not j["research"]["social"] and not j["research"]["market"]


def test_the_launch_date_may_be_yesterday_but_not_before():
    assert ok(launch="2026-10-15")["launch"] == "2026-10-15"
    with pytest.raises(ps.BadPayload):
        ok(launch="2026-10-14")


def test_budget_launch_and_currency():
    i = ok(budget="6,000.50", currency="cad ", launch="2026-12-01")
    assert i["budget"] == 6000.5 and i["currency"] == "CAD" and i["launch"] == "2026-12-01"
    assert ok(launch="2026-10-16")["launch"] == "2026-10-16"       # today is allowed
    with pytest.raises(ps.BadPayload):
        ok(launch="2028-01-01")


def test_campaign_types_keep_the_order_of_the_catalogue():
    assert ok(campaignTypes=["video", "search", "search"])["campaignTypes"] == ["search", "video"]


def test_free_text_is_trimmed_and_capped():
    i = ok(brief="line one   with  spaces\n\n\n\nline two" + " x" * 9000, services="s" * 2000)
    assert "\n\n\n" not in i["brief"] and len(i["brief"]) <= ps.MAX_BRIEF and len(i["services"]) == 800


# ── the steps ───────────────────────────────────────────────────────────────

def test_steps_follow_the_cards_that_are_on():
    i = ok(account="1234567890", research={"social": False})
    keys = [s["key"] for s in ps.steps_for(i)]
    assert keys == ["r:website", "r:competitors", "r:history", "r:keywords", "r:seasonality", "r:market", "a:profile", "a:competitive", "a:strategy", "a:architecture", "f:finish"]
    assert {s["state"] for s in ps.steps_for(i)} == {"pending"}


def test_each_campaign_gets_a_build_step_before_the_finish():
    steps = ps.steps_for(ok())
    got = ps.with_builds(steps, [{"key": "a", "name": "P2_Search_A_1"}, {"key": "b", "name": "P2_PMax_B_1"}])
    keys = [s["key"] for s in got]
    assert keys[-3:] == ["b:a", "b:b", "f:finish"]
    again = ps.with_builds(got, [{"key": "z", "name": "Z"}])        # redesigned campaigns replace the old build steps
    assert [s["key"] for s in again if s["key"].startswith("b:")] == ["b:z"]


# ── names and money ─────────────────────────────────────────────────────────

def test_campaign_names_follow_the_teams_format_and_pass_the_qa_name_rule():
    from tracker.paid_qa_auto import NAME_RE
    assert ps.campaign_name("search", "Implants & Veneers", "2026-11-02") == "P2_Search_ImplantsVeneers_20261102"
    assert ps.campaign_name("pmax", "", "2026-11-02") == "P2_PMax_20261102"
    assert ps.campaign_name("demandgen", "Local", "2026-11-02") == "P2_DemandGen_Local_20261102"
    for kind in ("search", "pmax"):
        name = ps.campaign_name(kind, "Brand", "2026-11-02")
        m = NAME_RE.match(name)
        assert m and m.group(1).lower() == ("pmax" if kind == "pmax" else "search")


def test_slug_keeps_letters_and_digits_and_a_length():
    assert ps.slug("a b-c_d 12!") == "ABCD12" and len(ps.slug("x" * 80)) == 24 and ps.slug("***") == ""


def test_daily_budget_is_a_month_over_thirty_point_four_days():
    assert ps.daily_from_monthly(6000) == 197.37 and ps.daily_from_monthly(None) is None and ps.daily_from_monthly(0) is None


def test_the_split_adds_back_to_the_daily_budget_and_respects_the_floor():
    days = ps.split_budget(6000, [70, 30], floor=1)
    assert round(sum(days), 2) == 197.37 and days[0] > days[1]
    assert round(sum(ps.split_budget(1000, [1, 1, 1])), 2) == 32.89
    tiny = ps.split_budget(30, [1, 1000], floor=1)
    assert round(sum(tiny), 2) == 0.99 and tiny[0] >= 0.49                      # a floor never spends more than the budget has
    many = ps.split_budget(100, [1] * 8, floor=1)
    assert round(sum(many), 2) == 3.29 and min(many) > 0
    assert ps.split_budget(None, [1, 2]) == [None, None] and ps.split_budget(100, [0, 0]) == [None, None]


# ── what the page is told ───────────────────────────────────────────────────

def test_public_strips_research_page_text_and_brief_lists_a_row():
    rec = {"id": "x" * 32, "name": "Acme", "status": "done", "created_by": "a@position2.com", "created_at": "t", "updated_at": "t", "inputs": {"website": "https://a.com/", "goal": "leads"},
           "research": {"website": {"key": "website", "state": "ok", "note": "n", "data": {"pages": [{"url": "u", "text": "SECRET PAGE TEXT"}]}}},
           "plan": {"campaigns": [{}, {}]}, "cost": {"usd": 1.5}}
    pub = ps.public(rec)
    assert "SECRET PAGE TEXT" not in str(pub) and pub["research"]["website"]["data"]["pages"][0]["url"] == "u"
    row = ps.brief(rec)
    assert row["campaigns"] == 2 and row["cost"] == 1.5 and row["website"] == "https://a.com/"
    assert ps.public(None) is None


def test_a_port_that_is_not_a_number_or_a_host_that_never_closes_is_a_refusal_not_a_crash():
    for bad in ("acme.com:xx", "http://[bad", "https://acme.com:99999999999/", "http://[::1"):
        with pytest.raises(ps.BadPayload):
            ps.clean_url(bad)
        with pytest.raises(ps.BadPayload):
            ok(website=bad)


def test_scrub_takes_out_what_postgres_and_spreadsheets_cannot_hold():
    nul, bell = chr(0), chr(7)
    assert ps.scrub({"a" + nul: ["x" + nul + "y", {"b": bell + "c"}], "n": 5, "t": (1, "z" + chr(0x1f))}) == {"a": ["xy", {"b": "c"}], "n": 5, "t": [1, "z"]}
    assert ps.scrub("tab\tand\nnewline stay") == "tab\tand\nnewline stay" and ps.scrub(None) is None
