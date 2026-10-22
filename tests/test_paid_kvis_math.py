"""Keyword Visibility Tracker: the rules (tracker/paid_kvis_math.py): weeks, enough data, each rule and its levels, the competitors."""
import datetime as dt

from tracker import paid_kvis_math as m

TODAY = dt.date(2026, 10, 28)                                   # a Wednesday: the last whole week is Monday 19 to Sunday 25 October
MONDAYS = [a.isoformat() for a, _b in m.week_list(TODAY)]       # four weeks before, then the last whole week
RECENT, OLDEST = MONDAYS[-1], MONDAYS[0]


def wk(week, top=0.6, share=0.8, lost=0.1, impressions=1000, cost=50.0):
    return {"week": week, "impressions": impressions, "clicks": 50, "cost": cost, "conv": 2.0, "share": share, "top": top, "absTop": 0.2, "lostRank": lost}


def rows(recent=None, prior=None, prior_weeks=4, **kw):
    out = [wk(RECENT, **(recent or {}))]
    for w in MONDAYS[:-1][-prior_weeks:] if prior_weeks else []:
        out.append(wk(w, **(prior or {})))
    return out


def kw(i="1", text="dentist quincy", cost=400.0, qs=7, qs_weeks=None, rows_=None, camp="Search Core", match="EXACT"):
    return {"id": i, "campaignId": "5", "campaign": camp, "adGroupId": "9", "adGroup": "G", "text": text, "match": match, "qs": qs, "cost": cost, "qsWeeks": qs_weeks or {},
            "rows": rows_ if rows_ is not None else rows()}


def run(keywords, config=None, auction=None, brand_ids=None):
    return m.analyse({"keywords": keywords, "auction": auction or [], "currency": "USD", "brandIds": brand_ids or []}, config=config or {}, today=TODAY)


def finds(keywords, config=None, rule=None, **kw_):
    got = run(keywords, config, **kw_)
    out = [f for i in got["items"] for f in i["findings"]] + got["competitorFindings"]
    return [f for f in out if rule is None or f["rule"] == rule]


# ── weeks and enough data ───────────────────────────────────────────────────

def test_the_weeks_are_the_last_whole_week_and_the_four_before():
    assert len(MONDAYS) == 5 and RECENT == "2026-10-19" and OLDEST == "2026-09-21"
    recent, prior, week = m.split_weeks(rows(), TODAY)
    assert week == RECENT and recent["impressions"] == 1000 and prior["impressions"] == 4000
    outside = rows() + [wk("2026-10-26", top=0.0), wk("2026-09-14", top=0.0)]                        # this week and a week too old are in neither
    r2, p2, _w = m.split_weeks(outside, TODAY)
    assert r2["impressions"] == 1000 and p2["impressions"] == 4000


def test_a_keyword_is_judged_only_with_enough_searches_it_was_eligible_for():
    thin = rows(recent={"impressions": 79, "share": 0.8})                                              # 79 / 0.8 = 98.75 eligible
    got = run([kw(rows_=thin)])["items"][0]
    assert got["judged"] is False and got["why"] == "only 99 searches it was eligible for last week (at least 100 are needed)" and got["findings"] == []
    edge = rows(recent={"impressions": 80, "share": 0.8, "top": 0.1})                                  # exactly 100 eligible
    assert run([kw(rows_=edge)])["items"][0]["judged"] is True
    assert run([kw(rows_=edge)])["judged"] == 1 and run([kw(rows_=thin)])["notJudged"] == 1


def test_a_keyword_whose_share_google_did_not_give_is_not_judged():
    none = rows(recent={"share": None, "top": None})
    got = run([kw(rows_=none)])["items"][0]
    assert got["judged"] is False and got["findings"] == []


# ── top of the page ─────────────────────────────────────────────────────────

def test_a_keyword_under_the_lowest_top_share_is_low_and_twenty_points_under_is_medium():
    (low,) = finds([kw(rows_=rows(recent={"top": 0.45}, prior={"top": 0.45}))], rule="top_is_low")
    assert low["level"] == "low" and low["title"] == '"dentist quincy" (exact) is at the top of the page 45% of the time'
    assert low["evidence"][0] == "Top of the page: 45% of the searches it was eligible for (the lowest wanted is 50%)" and low["evidence"][1] == "Lost to rank: 10%"
    (mid,) = finds([kw(rows_=rows(recent={"top": 0.30}, prior={"top": 0.30}))], rule="top_is_low")
    assert mid["level"] == "medium"
    (less,) = finds([kw(rows_=rows(recent={"top": 0.31}, prior={"top": 0.31}))], rule="top_is_low")
    assert less["level"] == "low"
    assert finds([kw(rows_=rows(recent={"top": 0.50}, prior={"top": 0.50}))], rule="top_is_low") == []                # on the target is not under it


def test_the_lowest_wanted_top_share_can_be_saved():
    cfg = {"topIs": 30}
    assert finds([kw(rows_=rows(recent={"top": 0.35}, prior={"top": 0.35}))], cfg, rule="top_is_low") == []
    assert len(finds([kw(rows_=rows(recent={"top": 0.25}, prior={"top": 0.25}))], cfg, rule="top_is_low")) == 1


# ── a fall in top-of-page share ─────────────────────────────────────────────

def test_a_fall_of_fifteen_points_is_medium_and_thirty_is_high_against_the_four_weeks_before():
    (a,) = finds([kw(rows_=rows(recent={"top": 0.60}, prior={"top": 0.75}))], rule="is_drop")
    assert a["level"] == "medium" and a["title"] == '"dentist quincy" (exact) lost 15 points of top-of-page share'
    assert a["detail"] == '"dentist quincy" (exact) in Search Core reached the top of the page 60% of the time last week against 75% on average in the four weeks before.'
    (b,) = finds([kw(rows_=rows(recent={"top": 0.60}, prior={"top": 0.90}))], rule="is_drop")
    assert b["level"] == "high"
    assert finds([kw(rows_=rows(recent={"top": 0.61}, prior={"top": 0.75}))], rule="is_drop") == []                   # 14 points
    assert finds([kw(rows_=rows(recent={"top": 0.60}, prior=None, prior_weeks=0))], rule="is_drop") == []             # no weeks before to compare with


def test_a_fall_is_not_called_when_the_weeks_before_are_too_thin():
    thin = rows(recent={"top": 0.5}, prior={"top": 0.9, "impressions": 10})
    assert finds([kw(rows_=thin)], rule="is_drop") == []


# ── lost to rank ────────────────────────────────────────────────────────────

def test_the_share_lost_to_rank_is_low_from_forty_percent_and_medium_from_sixty():
    assert finds([kw(rows_=rows(recent={"lost": 0.39}))], rule="lost_rank") == []
    (low,) = finds([kw(rows_=rows(recent={"lost": 0.40}))], rule="lost_rank")
    assert low["level"] == "low" and low["title"] == '"dentist quincy" (exact) loses 40% of its searches to rank'
    assert "was eligible for about 1,250 searches" in low["detail"]
    (mid,) = finds([kw(rows_=rows(recent={"lost": 0.60}))], rule="lost_rank")
    assert mid["level"] == "medium"


# ── Quality Score ───────────────────────────────────────────────────────────

def test_a_low_quality_score_is_medium_at_four_and_low_at_five_when_five_is_wanted_higher():
    (a,) = finds([kw(qs=4)], rule="qs_low")
    assert a["level"] == "medium" and a["title"] == '"dentist quincy" (exact) has a Quality Score of 4' and a["detail"].endswith("has a Quality Score of 4; the lowest wanted is 5.")
    assert finds([kw(qs=5)], rule="qs_low") == [] and finds([kw(qs=None)], rule="qs_low") == []
    (b,) = finds([kw(qs=5)], {"qsMin": 7}, rule="qs_low")
    assert b["level"] == "low"
    (c,) = finds([kw(qs=3)], {"qsMin": 7}, rule="qs_low")
    assert c["level"] == "medium"


def test_the_quality_score_of_the_last_week_is_the_one_kept_for_it_and_the_current_one_stands_in():
    got = run([kw(qs=9, qs_weeks={RECENT: 3})])["items"][0]
    assert got["qs"] == 3
    assert run([kw(qs=9)])["items"][0]["qs"] == 9


def test_a_fall_of_two_points_in_four_weeks_is_medium_and_high_when_it_is_now_four_or_less():
    (a,) = finds([kw(qs=6, qs_weeks={OLDEST: 8, RECENT: 6})], rule="qs_drop")
    assert a["level"] == "medium" and a["title"] == '"dentist quincy" (exact) Quality Score fell from 8 to 6'
    (b,) = finds([kw(qs=4, qs_weeks={OLDEST: 6, RECENT: 4})], rule="qs_drop")
    assert b["level"] == "high"
    assert finds([kw(qs=7, qs_weeks={OLDEST: 8, RECENT: 7})], rule="qs_drop") == []                                   # one point
    assert finds([kw(qs=6, qs_weeks={RECENT: 6})], rule="qs_drop") == []                                              # nothing kept four weeks ago


# ── the account ─────────────────────────────────────────────────────────────

def test_keywords_are_ranked_by_spend_and_the_worst_come_first():
    a = kw("1", "cheap", cost=900.0, rows_=rows(recent={"top": 0.55}, prior={"top": 0.55}))
    b = kw("2", "costly", cost=100.0, qs=3, rows_=rows(recent={"top": 0.55}, prior={"top": 0.55}))
    got = run([a, b])
    assert [i["keyword"] for i in got["items"]] == ["costly", "cheap"]
    assert {i["keyword"]: i["rank"] for i in got["items"]} == {"cheap": 1, "costly": 2}
    assert got["status"] == "fix" and got["counts"] == {"high": 0, "medium": 1, "low": 0} and got["keywords"] == 2 and got["withQs"] == 2


def test_the_account_is_clean_or_worth_a_look_by_its_findings():
    assert run([kw(rows_=rows(recent={"top": 0.55}, prior={"top": 0.55}))])["status"] == "clean"
    assert run([kw(rows_=rows(recent={"top": 0.45}, prior={"top": 0.45}))])["status"] == "check"


def test_findings_are_counted_by_rule_worst_level_first():
    k = kw(qs=3, rows_=rows(recent={"top": 0.20, "lost": 0.65}, prior={"top": 0.60}))
    got = run([k])
    assert [(b["rule"], b["level"], b["findings"]) for b in got["byRule"]] == [("is_drop", "high", 1), ("top_is_low", "medium", 1), ("qs_low", "medium", 1), ("lost_rank", "medium", 1)]


# ── competitors ─────────────────────────────────────────────────────────────

def au(domain="rival.com", week=RECENT, overlap=0.3, camp="5", name="Search Core"):
    return {"campaignId": camp, "campaign": name, "domain": domain, "week": week, "overlap": overlap, "outranking": 0.4, "above": 0.3, "topPct": 0.5, "share": 0.2}


def history(domain, now, before, **kw_):
    return [au(domain, RECENT, now, **kw_)] + [au(domain, w, before, **kw_) for w in MONDAYS[:-1]]


def test_a_new_competitor_is_medium_and_one_that_was_there_before_is_not_new():
    (f,) = finds([], auction=history("rival.com", 0.10, 0.0), rule="comp_new")
    assert f["level"] == "medium" and f["key"] == "comp_new:rival.com" and f["title"] == "rival.com is a new competitor"
    assert f["detail"] == "rival.com showed in 10% of your auctions last week and in almost none before."
    assert f["evidence"] == ["Overlap with your ads last week: 10%", "Average of the four weeks before: 0%", "In: Search Core"] and f["campaigns"] == ["Search Core"]
    assert finds([], auction=history("rival.com", 0.09, 0.0), rule="comp_new") == []                              # under 10 percent
    assert finds([], auction=history("rival.com", 0.30, 0.02), rule="comp_new") == []                             # 2 percent before is not "almost none"
    assert [x["rule"] for x in finds([], auction=history("rival.com", 0.30, 0.0))] == ["comp_new"]


def test_a_new_week_one_competitor_seen_in_only_one_of_the_weeks_before_is_not_new():
    a = [au(week=RECENT, overlap=0.3)] + [au(week=MONDAYS[1], overlap=0.05)]
    assert finds([], auction=a, rule="comp_new") == []


def test_a_competitor_overlapping_much_more_is_low():
    (f,) = finds([], auction=history("rival.com", 0.40, 0.20), rule="comp_up")
    assert f["level"] == "low" and f["title"] == "rival.com overlaps 40% now" and f["detail"] == "rival.com showed in 40% of your auctions last week, up from 20% on average."
    assert finds([], auction=history("rival.com", 0.40, 0.26), rule="comp_up") == []                                 # up 14 points
    assert finds([], auction=history("rival.com", 0.19, 0.03), rule="comp_up") == []                                 # under 20 percent
    assert len(finds([], auction=history("rival.com", 0.40, 0.25), rule="comp_up")) == 1                             # up exactly 15 points
    assert len(finds([], auction=history("rival.com", 0.20, 0.03), rule="comp_up")) == 1                             # at exactly 20 percent


def test_a_competitor_on_a_brand_campaign_is_a_possible_brand_hijack():
    a = history("rival.com", 0.05, 0.0, camp="7", name="P2 Brand")
    (f,) = finds([], auction=a, brand_ids=["7"])
    assert (f["rule"], f["level"], f["key"]) == ("brand_hijack", "high", "brand_hijack:rival.com") and f["title"] == "rival.com is new in your brand auctions"
    assert f["detail"] == "rival.com showed in 5% of your brand auctions last week and had not before."
    assert finds([], auction=history("rival.com", 0.049, 0.0, camp="7", name="P2 Brand"), brand_ids=["7"]) == []
    (rise,) = finds([], auction=history("rival.com", 0.40, 0.10, camp="7", name="P2 Brand"), brand_ids=["7"])
    assert (rise["rule"], rise["level"]) == ("brand_hijack", "medium") and rise["title"] == "rival.com is overlapping more in your brand auctions" and rise["detail"].endswith(", up from 10%.")
    assert finds([], auction=a, brand_ids=[]) == []                                                                  # 5 percent is new only on a brand campaign


def test_the_same_domain_on_a_brand_and_a_generic_campaign_is_two_findings():
    a = history("rival.com", 0.30, 0.0, camp="7", name="P2 Brand") + history("rival.com", 0.30, 0.0, camp="8", name="Generic")
    assert sorted(f["rule"] for f in finds([], auction=a, brand_ids=["7"])) == ["brand_hijack", "comp_new"]


def test_domains_the_team_ignores_and_the_accounts_own_are_left_out():
    a = history("rival.com", 0.30, 0.0) + history("Partner.com", 0.30, 0.0) + history("You", 0.30, 0.0)
    got = run([], {"ignore": ["partner.COM"]}, auction=a)
    assert [f["key"] for f in got["competitorFindings"]] == ["comp_new:rival.com"] and [c["domain"] for c in got["competitors"]] == ["rival.com"]


def test_the_competitors_shown_are_by_last_weeks_overlap_with_what_it_was_before():
    a = history("a.com", 0.2, 0.1) + history("b.com", 0.5, 0.3) + history("c.com", 0.0, 0.4)
    got = run([], auction=a)["competitors"]
    assert [(c["domain"], c["overlap"], c["before"], c["campaigns"]) for c in got] == [("b.com", 0.5, 0.3, 1), ("a.com", 0.2, 0.1, 1)]
    assert len(m.competitors([au("d%d.com" % i) for i in range(15)], {}, TODAY)) == 10


def test_weeks_outside_the_five_and_rows_without_an_overlap_are_ignored():
    a = [au(week="2026-09-14", overlap=0.9), dict(au(), overlap=None), au(week=RECENT, overlap=0.0)]
    assert run([], auction=a)["competitors"] == [] and run([], auction=a)["competitorFindings"] == []


def test_an_account_with_no_auction_read_says_so():
    assert run([kw()])["auctionRead"] is False and run([kw()], auction=history("x.com", 0.3, 0.0))["auctionRead"] is True
