"""Creative & Asset Health: which creatives are wearing out and which assets are weak or missing (tracker/paid_creative_math.py). No network, no clock."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_creative_math as m  # noqa: E402


def wk(impr, ctr, cost=100.0, reach=0):
    return {"impr": impr, "clicks": round(impr * ctr), "cost": cost, "reach": reach}


def weeks(base_ctr=0.05, recent_ctr=0.05, impr=5000, **kw):
    """5 weeks: the first 3 at base_ctr, the last 2 at recent_ctr."""
    return [wk(impr, base_ctr, **kw) for _ in range(3)] + [wk(impr, recent_ctr, **kw) for _ in range(2)]


def cr(cid="a1", name="Ad one", w=None, **kw):
    return dict({"id": cid, "name": name, "kind": "Responsive search ad", "campaign": "Camp", "group": "Group", "weeks": w if w is not None else weeks(), "rank": None}, **kw)


def acct(creatives, platform="google", **kw):
    base = {"platform": platform, "currency": "USD", "creatives": creatives, "weak": [], "gaps": [], "weeksRead": True}
    base.update(kw)
    return base


def run(a, platform=None):
    return m.analyse(a, platform=platform)


def rules(r):
    return [f["rule"] for f in r["findings"]]


def check(r, key):
    return next(c for c in r["checks"] if c["key"] == key)


# ── Click-through rate ──────────────────────────────────────────────────────

def test_a_steady_creative_is_clean_and_counted_as_judged():
    r = run(acct([cr(), cr("a2", "Ad two")]))
    assert r["status"] == "clean" and r["findings"] == []
    assert r["headline"]["judged"] == 2 and check(r, "tired")["state"] == "ok" and "2 creatives judged" in check(r, "tired")["note"]


def test_a_big_fall_in_the_click_through_rate_is_high_and_says_the_numbers():
    r = run(acct([cr(w=weeks(0.05, 0.028)), cr("a2", "Steady")]))
    f = r["findings"][0]
    assert rules(r) == ["tired"] and f["level"] == "high" and f["key"] == "tired:a1" and r["status"] == "fix"
    assert "click-through rate down 44%" in f["title"] and "5.0%" in f["evidence"][0] and "2.8%" in f["evidence"][0]


def test_a_fall_between_a_quarter_and_two_fifths_is_medium():
    r = run(acct([cr(w=weeks(0.05, 0.034)), cr("a2", "Steady")]))
    assert [f["level"] for f in r["findings"]] == ["medium"]


def test_a_small_fall_is_not_flagged():
    assert run(acct([cr(w=weeks(0.05, 0.043))]))["findings"] == []


def test_a_fall_the_whole_account_shares_is_not_blamed_on_the_creative():
    both = [cr("a1", w=weeks(0.05, 0.03)), cr("a2", "Ad two", w=weeks(0.05, 0.03)), cr("a3", "Ad three", w=weeks(0.05, 0.03))]
    assert run(acct(both))["findings"] == []


def test_a_creative_that_fell_clearly_more_than_the_account_is_flagged():
    mixed = [cr("a1", w=weeks(0.05, 0.02)), cr("a2", "Ad two", w=weeks(0.05, 0.045)), cr("a3", "Ad three", w=weeks(0.05, 0.045))]
    assert [f["key"] for f in run(acct(mixed))["findings"]] == ["tired:a1"]


def test_noise_on_few_clicks_is_not_a_fall():
    few = cr(w=[wk(500, 0.04), wk(500, 0.04), wk(500, 0.04), wk(300, 0.03), wk(300, 0.03)])
    r = run(acct([few]))
    assert r["findings"] == [] and r["headline"]["notJudged"] == 1


def test_a_creative_with_too_few_impressions_is_counted_as_not_judged_with_the_reason():
    tiny = cr(w=weeks(0.05, 0.01, impr=100))
    r = run(acct([tiny]))
    assert r["findings"] == [] and r["headline"] == dict(r["headline"], judged=0, notJudged=1)
    c = check(r, "tired")
    assert c["state"] == "unknown" and "too few impressions" in c["note"]


def test_a_creative_with_too_few_weeks_is_not_judged():
    r = run(acct([cr(w=weeks()[-3:])]))
    assert r["headline"]["notJudged"] == 1 and check(r, "tired")["state"] == "unknown"


def test_the_weekly_numbers_not_read_says_so_and_judges_nothing():
    r = run(acct([cr(w=weeks(0.05, 0.01))], weeksRead=False))
    assert r["findings"] == [] and check(r, "tired")["state"] == "unknown" and "could not be read" in check(r, "tired")["note"]


# ── Meta: frequency and cost per thousand ───────────────────────────────────

def meta_weeks(freq_base=2.0, freq_last=2.0, cpm_base=10.0, cpm_recent=10.0, ctr=0.02, impr=5000):
    out = []
    for i in range(5):
        f = freq_last if i == 4 else freq_base
        cpm = cpm_recent if i >= 3 else cpm_base
        out.append({"impr": impr, "clicks": round(impr * ctr), "cost": impr * cpm / 1000, "reach": round(impr / f)})
    return out


def test_a_high_frequency_is_a_signal_on_meta_and_never_on_google():
    ad = cr("m1", "Meta ad", w=meta_weeks(freq_base=2.0, freq_last=4.4), kind="Image ad")
    r = run(acct([ad, cr("m2", "Other", w=meta_weeks())], platform="meta"))
    f = r["findings"][0]
    assert f["key"] == "tired:m1" and "frequency 4.4" in f["title"] and f["level"] == "medium" and "Frequency: each person reached 4.4 times" in f["evidence"][0]
    assert run(acct([ad], platform="google"))["findings"] == []


def test_a_frequency_of_three_needs_a_rise_to_count():
    flat = cr("m1", w=meta_weeks(freq_base=3.1, freq_last=3.2))
    rising = cr("m2", w=meta_weeks(freq_base=2.2, freq_last=3.2))
    r = run(acct([flat, rising], platform="meta"))
    assert [f["key"] for f in r["findings"]] == ["tired:m2"]


def test_a_cost_per_thousand_rising_with_the_account_is_not_blamed_on_the_ad():
    r = run(acct([cr("m1", w=meta_weeks(cpm_recent=14)), cr("m2", w=meta_weeks(cpm_recent=14))], platform="meta"))
    assert r["findings"] == []


def test_a_cost_per_thousand_rising_on_one_ad_is_a_signal():
    r = run(acct([cr("m1", w=meta_weeks(cpm_recent=14)), cr("m2", w=meta_weeks()), cr("m3", w=meta_weeks())], platform="meta"))
    assert [f["key"] for f in r["findings"]] == ["tired:m1"] and "cost per 1,000 views up 40%" in r["findings"][0]["title"]


def test_two_signals_make_it_high_and_the_meta_fix_is_meta_wording():
    ad = cr("m1", w=meta_weeks(freq_base=2.0, freq_last=4.5, cpm_recent=14))
    r = run(acct([ad, cr("m2", w=meta_weeks()), cr("m3", w=meta_weeks())], platform="meta"))
    f = r["findings"][0]
    assert f["level"] == "high" and len(f["evidence"]) == 2 and "audience" in f["fix"]


# ── Platform ratings ────────────────────────────────────────────────────────

def weak(cid="a1", field="HEADLINE", text="Best crowns"):
    return {"creativeId": cid, "name": "Ad one", "campaign": "Camp", "group": "Group", "field": field, "text": text, "label": "LOW"}


def test_google_low_rated_assets_become_one_finding_per_creative():
    r = run(acct([cr()], weak=[weak(), weak(field="DESCRIPTION", text="Call us"), weak("zz", text="x")]))
    low = [f for f in r["findings"] if f["rule"] == "low_rating"]
    assert len(low) == 2 and low[0]["level"] == "medium" and "2 assets rated Low by Google" in low[0]["title"]
    assert "HEADLINE: Best crowns" in low[0]["evidence"]
    assert next(f for f in low if f["key"] == "low:zz")["level"] == "low"        # a creative nobody sees is not urgent
    assert "3 assets rated Low in 2 creatives" in check(r, "ratings")["note"]


def test_google_ratings_not_read_say_so_and_none_is_clean():
    assert check(run(acct([cr()], weak=None)), "ratings")["state"] == "unknown"
    assert check(run(acct([cr()], weak=[])), "ratings")["state"] == "ok"


def ranked(q, e, c):
    return {"quality": q, "engagement": e, "conversion": c}


def test_meta_two_below_average_rankings_are_medium_and_three_are_high():
    ads = [cr("m1", rank=ranked("BELOW_AVERAGE_20", "BELOW_AVERAGE_35", "AVERAGE")), cr("m2", rank=ranked("BELOW_AVERAGE_20", "BELOW_AVERAGE_35", "BELOW_AVERAGE_35")),
           cr("m3", rank=ranked("BELOW_AVERAGE_35", "AVERAGE", "AVERAGE")), cr("m4", rank=ranked("ABOVE_AVERAGE", "AVERAGE", "AVERAGE"))]
    r = run(acct(ads, platform="meta"))
    got = {f["key"]: f["level"] for f in r["findings"] if f["rule"] == "low_rating"}
    assert got == {"low:m1": "medium", "low:m2": "high"} and "4 ads ranked" in check(r, "ratings")["note"]


def test_two_bottom_ten_rankings_are_high():
    r = run(acct([cr("m1", rank=ranked("BELOW_AVERAGE_10", "BELOW_AVERAGE_10", "AVERAGE"))], platform="meta"))
    assert [f["level"] for f in r["findings"] if f["rule"] == "low_rating"] == ["high"]


def test_meta_with_no_ranked_ad_says_unknown():
    r = run(acct([cr("m1"), cr("m2", rank=ranked(None, None, None))], platform="meta"))
    c = check(r, "ratings")
    assert c["state"] == "unknown" and "gave no ranking for any of the 2 ads" in c["note"] and "500 impressions" in c["note"]


# ── Missing assets ──────────────────────────────────────────────────────────

def gap(sev="high", key="SITELINK", campaign="Camp", cid="9", scope="Campaign", have=0, target=4):
    return {"key": key, "scope": scope, "campaign": campaign, "campaignId": cid, "label": "Sitelinks", "severity": sev, "have": have, "target": target, "add": target - have,
            "message": "No sitelinks, so the ad is smaller than it could be."}


def test_missing_assets_keep_the_coverage_rules_own_severity_and_are_one_finding_each():
    r = run(acct([cr()], gaps=[gap("high"), gap("low", key="CALLOUT"), gap("medium", scope="Asset group: Crowns", key="IMAGE")]))
    got = {f["key"]: f["level"] for f in r["findings"] if f["rule"] == "missing_asset"}
    assert got == {"gap:9:Campaign:SITELINK": "high", "gap:9:Campaign:CALLOUT": "low", "gap:9:Asset group: Crowns:IMAGE": "medium"}
    assert "place is short" not in check(r, "assets")["note"] and "2 places are short" in check(r, "assets")["note"]


def test_missing_assets_not_read_say_so_and_none_is_clean():
    assert check(run(acct([cr()], gaps=None)), "assets")["state"] == "unknown"
    r = run(acct([cr()], gaps=[]))
    assert check(r, "assets")["state"] == "ok" and r["status"] == "clean"


def test_a_low_gap_alone_is_check_not_fix():
    r = run(acct([cr()], gaps=[gap("low")]))
    assert r["status"] == "check" and r["counts"] == {"high": 0, "medium": 0, "low": 1}


# ── The whole account ───────────────────────────────────────────────────────

def test_findings_come_worst_first_then_by_rule_and_are_counted_by_rule():
    a = acct([cr(w=weeks(0.05, 0.02)), cr("a2", "Two")], weak=[weak("a2")], gaps=[gap("medium")])
    r = run(a)
    assert [f["level"] for f in r["findings"]] == ["high", "medium", "medium"] and rules(r) == ["tired", "low_rating", "missing_asset"]
    assert [x["rule"] for x in r["byRule"]] == ["tired", "low_rating", "missing_asset"] and r["findingCount"] == 3


def test_the_not_measured_list_is_never_empty_and_every_rule_has_a_fix_and_an_area():
    assert len(m.NOT_MEASURED) >= 4 and all("—" not in x for x in m.NOT_MEASURED)
    for k, v in m.RULES.items():
        assert v["fix"] and v["area"] and v["label"] and "—" not in v["fix"]
    assert sorted(m.ORDER) == sorted(m.RULES)


# ── Edges the thresholds stand for ──────────────────────────────────────────

def test_a_lone_creative_is_held_against_nothing_and_still_flagged():
    r = run(acct([cr(w=weeks(0.05, 0.028))]))
    assert [f["key"] for f in r["findings"]] == ["tired:a1"]


def test_a_fall_within_chance_is_not_flagged():
    chance = cr(w=[wk(700, 0.02), wk(700, 0.02), wk(700, 0.02), wk(500, 0.014), wk(500, 0.014)])
    r = run(acct([chance, cr("a2", w=weeks(0.05, 0.05))]))
    assert r["findings"] == [] and r["headline"]["judged"] == 2


def test_a_usual_rate_resting_on_few_clicks_is_not_judged_on_clicks():
    few = cr(w=[wk(700, 0.01), wk(700, 0.01), wk(700, 0.01), wk(500, 0.0), wk(500, 0.0)])
    assert run(acct([few, cr("a2")]))["findings"] == []


def test_a_creative_that_has_nearly_stopped_delivering_is_not_judged():
    gone = cr(w=[wk(5000, 0.05), wk(5000, 0.05), wk(5000, 0.05), wk(150, 0.01), wk(150, 0.01)])
    r = run(acct([gone, cr("a2")]))
    assert r["findings"] == [] and r["headline"]["notJudged"] == 1


def test_a_modest_frequency_that_doubled_is_not_a_signal():
    r = run(acct([cr("m1", w=meta_weeks(freq_base=1.2, freq_last=2.6)), cr("m2", w=meta_weeks())], platform="meta"))
    assert r["findings"] == []


def test_a_small_cost_per_thousand_rise_is_not_a_signal():
    r = run(acct([cr("m1", w=meta_weeks(cpm_recent=11.5)), cr("m2", w=meta_weeks()), cr("m3", w=meta_weeks())], platform="meta"))
    assert r["findings"] == []


def test_a_medium_finding_alone_makes_the_account_fix():
    r = run(acct([cr(w=weeks(0.05, 0.034)), cr("a2", "Steady")]))
    assert r["status"] == "fix" and r["counts"] == {"high": 0, "medium": 1, "low": 0}


def test_only_the_base_weeks_just_before_count_as_the_usual_rate():
    older = [wk(5000, 0.20), wk(5000, 0.20)] + weeks(0.05, 0.05)
    assert run(acct([cr(w=older), cr("a2")]))["findings"] == []


def test_a_rest_of_the_account_with_almost_no_delivery_is_not_a_yardstick():
    tiny_rest = cr("a2", w=[wk(100, 0.05)] * 3 + [wk(100, 0.01)] * 2)
    assert [f["key"] for f in run(acct([cr(w=weeks(0.05, 0.028)), tiny_rest]))["findings"]] == ["tired:a1"]


def test_a_meta_ad_without_a_ranking_is_not_called_too_new_to_rank():
    r = run(acct([cr("m1", rank=ranked("AVERAGE", None, None)), cr("m2", rank=ranked(None, None, None))], platform="meta"))
    note = check(r, "ratings")["note"]
    assert "1 ad ranked" in note and "1 not ranked by Meta" in note and "too new" not in note
