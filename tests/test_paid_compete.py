"""Competitor Analysis: reading the competitors' ads, the refresh and the report (tracker/paid_compete_fetch.py, paid_compete.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from test_paid_compete_math import GOOGLE_REAL, META_REAL, own_without_price, pool_with_price  # noqa: E402
from tracker import paid_compete as pc  # noqa: E402
from tracker import paid_compete_fetch as cf  # noqa: E402
from tracker import paid_compete_math as cm  # noqa: E402
from tracker import paid_compete_settings as cs  # noqa: E402

NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)
G, MA = "1234567890", "act_1234567"
TANDEM = {"name": "Tandem", "domain": "tandemdiabetes.com", "facebook": "https://www.facebook.com/TandemDiabetes", "googleAdvertiser": ""}
OMNI = {"name": "Insulet", "domain": "omnipod.com", "facebook": "", "googleAdvertiser": "AR111111111111"}
BARE = {"name": "Bare", "domain": "", "facebook": "https://www.facebook.com/Bare", "googleAdvertiser": ""}


class Runner:
    """A stand-in for the Apify transport: answers per actor and records what it was asked."""

    def __init__(self, meta=(META_REAL,), google=(GOOGLE_REAL,), fail=()):
        self.calls, self.meta, self.google, self.fail = [], list(meta), list(google), set(fail)

    def __call__(self, actor, run_input, token, timeout):
        self.calls.append({"actor": actor, "input": run_input, "token": token, "timeout": timeout})
        if actor in self.fail or run_input["startUrls"][0]["url"] in self.fail:
            raise RuntimeError("Apify actor %s run r1 ended with status FAILED" % actor)
        return self.meta if actor == cf.actor_id("meta") else self.google


# ── what is asked ───────────────────────────────────────────────────────────

def test_each_platform_is_asked_with_the_address_and_limits_the_actors_were_tried_with():
    assert cf.start_url("meta", TANDEM, "US") == "https://www.facebook.com/TandemDiabetes" and cf.start_url("meta", OMNI, "US") is None
    assert cf.start_url("google", TANDEM, "US") == "https://adstransparency.google.com/?region=US&domain=tandemdiabetes.com"
    assert cf.start_url("google", OMNI, "DE") == "https://adstransparency.google.com/advertiser/AR111111111111?region=DE"          # the advertiser id wins over the website
    assert cf.start_url("google", BARE, "US") is None
    assert cf.actor_input("meta", "u") == {"startUrls": [{"url": "u"}], "resultsLimit": 400, "activeStatus": "active"}
    assert cf.actor_input("google", "u") == {"startUrls": [{"url": "u"}], "resultsLimit": 150, "ocr": True}


def test_the_actors_are_the_ones_tried_and_an_environment_setting_replaces_them(monkeypatch):
    assert cf.actor_id("meta") == "apify/facebook-ads-scraper" and cf.actor_id("google") == "silva95gustavo/google-ads-scraper"
    monkeypatch.setenv("CA_APIFY_META_ACTOR_ID", " someone/other-meta ")
    assert cf.actor_id("meta") == "someone/other-meta" and cf.actor_id("google") == "silva95gustavo/google-ads-scraper"


def test_a_competitor_with_nothing_to_look_up_on_a_platform_says_so():
    assert "No Facebook page" in cf.skipped_reason("meta", OMNI, "US") and "No website or advertiser id" in cf.skipped_reason("google", BARE, "US")
    assert cf.skipped_reason("meta", TANDEM, "US") == "" and cf.skipped_reason("google", OMNI, "US") == ""


def test_the_cache_keys_keep_platforms_countries_and_competitors_apart():
    assert cf.cache_key("meta", TANDEM, "US") == "compete:ads:meta:https://www.facebook.com/tandemdiabetes" and cf.cache_key("meta", TANDEM, "DE") == cf.cache_key("meta", TANDEM, "US")
    assert cf.cache_key("google", TANDEM, "US") == "compete:ads:google:US:tandemdiabetes.com" and cf.cache_key("google", TANDEM, "DE") != cf.cache_key("google", TANDEM, "US")
    assert cf.cache_key("google", OMNI, "US") == "compete:ads:google:US:ar111111111111"


def test_the_cost_of_a_refresh_counts_only_what_can_be_looked_up():
    assert cf.estimate([TANDEM, OMNI, BARE], ["meta", "google"], "US") == {"reads": 4, "ads": 1100, "usd": round(2 * 400 * 0.005 + 2 * 150 * 0.0016, 2), "minutes": 28}
    assert cf.estimate([TANDEM], ["google"], "US") == {"reads": 1, "ads": 150, "usd": 0.24, "minutes": 28} and cf.estimate([], ["meta"], "US") == {"reads": 0, "ads": 0, "usd": 0.0, "minutes": 0}


# ── one read ────────────────────────────────────────────────────────────────

def test_a_read_asks_the_actor_and_turns_its_rows_into_ads():
    r = Runner()
    res = cf.read_one("meta", TANDEM, "US", run_fn=r, token="tok", now=NOW)
    assert r.calls == [{"actor": "apify/facebook-ads-scraper", "input": cf.actor_input("meta", "https://www.facebook.com/TandemDiabetes"), "token": "tok", "timeout": cf.timeout_for("meta")}]
    assert res["error"] == "" and res["skipped"] == "" and res["items"] == 1 and res["fetchedAt"] == "2026-10-27T12:00:00+00:00" and res["ads"][0]["who"] == "Tandem Diabetes Care" and res["ads"][0]["days"] == 34
    g = cf.read_one("google", TANDEM, "US", run_fn=r, token="tok", now=NOW)
    assert r.calls[1]["actor"] == "silva95gustavo/google-ads-scraper" and g["ads"][0]["id"] == "google:CR03842488522816618497"


def test_a_failed_read_is_said_with_its_reason_and_a_read_with_no_rows_is_an_empty_read():
    res = cf.read_one("meta", TANDEM, "US", run_fn=Runner(fail=["apify/facebook-ads-scraper"]), token="t", now=NOW)
    assert res["ads"] == [] and res["error"].startswith("The Meta read failed: Apify actor apify/facebook-ads-scraper run r1 ended with status FAILED") and res["items"] == 0
    empty = cf.read_one("meta", TANDEM, "US", run_fn=Runner(meta=[]), token="t", now=NOW)
    assert empty["ads"] == [] and empty["error"] == "" and empty["items"] == 0
    junk = cf.read_one("meta", TANDEM, "US", run_fn=Runner(meta=["x", {"adArchiveID": "1", "snapshot": {}}, META_REAL]), token="t", now=NOW)
    assert len(junk["ads"]) == 1 and junk["items"] == 3
    def wordy(*a):
        raise RuntimeError("x" * 500)
    long = cf.read_one("google", TANDEM, "US", run_fn=wordy, token="t", now=NOW)
    assert long["error"] == "The Google read failed: " + "x" * 200


def test_a_competitor_with_nothing_to_look_up_is_not_asked():
    r = Runner()
    res = cf.read_one("meta", OMNI, "US", run_fn=r, token="t", now=NOW)
    assert r.calls == [] and res["ads"] == [] and "No Facebook page" in res["skipped"] and res["error"] == ""


def test_the_limits_are_far_above_what_a_large_advertiser_runs_and_the_time_allowed_covers_the_slow_google_read():
    """Measured 2026-10-27 on a real advertiser: Meta showed 94 active ads (read in 96 s), Google 126 creatives (23 minutes, because the text of each is read from its picture)."""
    assert cf.LIMIT["meta"] >= 307 and cf.LIMIT["google"] >= 126                                              # Meta: a second advertiser showed 307
    assert cf.LIMIT["google"] * cf.SECONDS_PER_AD["google"] <= cf.timeout_for("google") and cf.LIMIT["meta"] * cf.SECONDS_PER_AD["meta"] <= cf.timeout_for("meta")
    assert cf.LOCK_TTL_S > cf.timeout_for("google")                                                          # a lock must outlive the longest read


def collected(items, status="SUCCEEDED", error=None):
    def run(actor, run_input, token, timeout):
        return {"items": items, "status": status, "error": error}
    return run


def test_a_read_that_reaches_its_limit_says_the_competitor_has_more_ads():
    rows = [META_REAL] * cf.LIMIT["meta"]
    res = cf.read_one("meta", TANDEM, "US", run_fn=collected(rows), token="t", now=NOW)
    assert res["capped"] is True and res["items"] == cf.LIMIT["meta"] and res["limit"] == 400 and res["partial"] == ""
    one_short = cf.read_one("meta", TANDEM, "US", run_fn=collected(rows[:-1]), token="t", now=NOW)
    assert one_short["capped"] is False
    assert cf.read_one("meta", TANDEM, "US", run_fn=Runner(meta=rows), token="t", now=NOW)["capped"] is True      # a plain list of rows is judged the same way


def test_a_run_stopped_before_it_finished_keeps_the_ads_it_saved_as_a_partial_read():
    res = cf.read_one("google", TANDEM, "US", run_fn=collected([GOOGLE_REAL], status="ABORTED", error="Apify run r1 did not finish within 1500s"), token="t", now=NOW)
    assert len(res["ads"]) == 1 and res["error"] == "" and "stopped before it finished" in res["partial"] and "did not finish within 1500s" in res["partial"] and res["capped"] is False
    assert not cf.fresh(dict(res, fetchedAt="2026-10-27T11:00:00+00:00"), NOW)                              # read again at the next refresh


def test_a_run_that_failed_with_nothing_saved_is_a_failed_read_and_one_that_found_nothing_is_empty():
    res = cf.read_one("google", TANDEM, "US", run_fn=collected([], status="FAILED"), token="t", now=NOW)
    assert res["ads"] == [] and res["error"] == "The Google read failed: the run ended with status FAILED"
    res = cf.read_one("google", TANDEM, "US", run_fn=collected([], status=None, error="start failed: 402"), token="t", now=NOW)
    assert res["error"] == "The Google read failed: start failed: 402"
    res = cf.read_one("google", TANDEM, "US", run_fn=collected([]), token="t", now=NOW)
    assert res["error"] == "" and res["partial"] == "" and res["ads"] == [] and res["items"] == 0


def test_a_partial_read_does_not_replace_a_fuller_one_but_replaces_a_poorer_one():
    s = MemStore()
    rows = [dict(GOOGLE_REAL, creativeId="CR%d" % i) for i in range(3)]
    refresh(s, runner=lambda *a: collected(rows)(*a), platforms=("google",), competitors=(TANDEM,))
    good = s.state_get(cf.cache_key("google", TANDEM, "US"))
    assert len(good["ads"]) == 3
    refresh(s, runner=lambda *a: collected(rows[:1], status="ABORTED", error="out of time")(*a), platforms=("google",), competitors=(TANDEM,), force=True)
    assert s.state_get(cf.cache_key("google", TANDEM, "US")) == good
    s2 = MemStore()
    refresh(s2, runner=lambda *a: collected(rows[:1], status="ABORTED", error="out of time")(*a), platforms=("google",), competitors=(TANDEM,))
    kept = s2.state_get(cf.cache_key("google", TANDEM, "US"))
    assert len(kept["ads"]) == 1 and kept["partial"]
    job, _ = refresh(s2, runner=lambda *a: collected(rows)(*a), platforms=("google",), competitors=(TANDEM,))
    assert job["steps"][0]["ads"] == 3 and len(s2.state_get(cf.cache_key("google", TANDEM, "US"))["ads"]) == 3        # a partial read is read again, and a fuller one replaces it


def test_a_read_that_stopped_at_a_lower_limit_than_todays_is_read_again_but_one_at_todays_limit_is_not():
    s = MemStore()
    refresh(s, runner=Runner(meta=[META_REAL] * 400), platforms=("meta",), competitors=(TANDEM,))
    job, r = refresh(s, runner=Runner(meta=[META_REAL] * 400), platforms=("meta",), competitors=(TANDEM,))
    assert r.calls == [] and job["skippedFresh"] == 1                                                         # capped at today's limit, read lately: kept
    old = s.state_get(cf.cache_key("meta", TANDEM, "US"))
    s.state_set(cf.cache_key("meta", TANDEM, "US"), dict(old, limit=150))
    job, r = refresh(s, runner=Runner(meta=[META_REAL] * 3), platforms=("meta",), competitors=(TANDEM,))
    assert len(r.calls) == 1 and job["skippedFresh"] == 0
    assert cf.outgrown(dict(old, limit=150), "meta") is True and cf.outgrown(dict(old, capped=False, limit=150), "meta") is False and cf.outgrown(None, "meta") is False and cf.outgrown(old, "meta") is False


def test_the_job_steps_say_when_a_read_hit_its_limit_or_stopped_early():
    s = MemStore()
    job, _ = refresh(s, runner=Runner(meta=[META_REAL] * 400), platforms=("meta",), competitors=(TANDEM,))
    assert job["steps"][0]["capped"] is True and job["steps"][0]["limit"] == 400 and job["steps"][0]["partial"] == ""


def test_the_entries_for_the_analysis_carry_the_limit_and_the_partial_note():
    s = MemStore()
    refresh(s, runner=Runner(meta=[META_REAL] * 400), platforms=("meta",), competitors=(TANDEM,))
    e = cf.entries_for(s, [TANDEM, OMNI], ["meta"], "US")
    assert e[0]["capped"] is True and e[0]["items"] == 400 and e[0]["limit"] == 400 and e[0]["partial"] == ""
    assert e[1]["capped"] is False and e[1]["limit"] == 400 and e[1]["items"] == 0
    s.state_set(cf.cache_key("meta", TANDEM, "US"), dict(s.state_get(cf.cache_key("meta", TANDEM, "US")), partial="The Meta read stopped before it finished (x), so only part of the ads were read."))
    assert cf.entries_for(s, [TANDEM], ["meta"], "US")[0]["partial"].startswith("The Meta read stopped before it finished")


# ── a refresh ───────────────────────────────────────────────────────────────

def refresh(store, runner=None, competitors=(TANDEM, OMNI), platforms=("meta", "google"), **kw):
    runner = runner or Runner()
    kw.setdefault("now_fn", lambda: NOW)
    return cf.refresh(store, "google:%s" % G, list(competitors), list(platforms), "US", run_fn=runner, token="tok", who="ana@position2.com", **kw), runner


def test_a_refresh_reads_every_competitor_on_every_platform_and_keeps_what_it_read():
    s = MemStore()
    job, r = refresh(s)
    assert job["status"] == "done" and job["total"] == 3 and job["skippedFresh"] == 0 and job["by"] == "ana@position2.com" and len(r.calls) == 3          # Insulet has no Facebook page: three reads, not four
    assert {(x["name"], x["platform"]): (x["status"], x["ads"]) for x in job["steps"]} == {("Tandem", "meta"): ("done", 1), ("Tandem", "google"): ("done", 1), ("Insulet", "google"): ("done", 1)}
    assert s.state_get("compete:job:google:%s" % G)["status"] == "done" and s.state_get(cf.cache_key("meta", TANDEM, "US"))["ads"][0]["who"] == "Tandem Diabetes Care"
    assert s.state_get(cf.cache_key("google", OMNI, "US"))["name"] == "Insulet"
    assert s.state_get(cf.cache_key("meta", OMNI, "US")) is None                                              # a competitor with nothing to look up is not a read
    assert not s.locks


def test_a_competitor_read_lately_is_not_read_again_unless_forced():
    s = MemStore()
    refresh(s)
    job, r = refresh(s)
    assert job["total"] == 0 and job["skippedFresh"] == 3 and r.calls == [] and job["status"] == "done"
    job, r = refresh(s, force=True)
    assert job["total"] == 3 and len(r.calls) == 3
    later = NOW + dt.timedelta(days=cf.CACHE_DAYS + 1)
    job, r = refresh(s, now_fn=lambda: later)
    assert job["total"] == 3


def test_a_refresh_can_be_limited_to_the_competitors_named():
    s = MemStore()
    job, r = refresh(s, only=["Tandem"], platforms=("google",))
    assert job["total"] == 1 and len(r.calls) == 1 and r.calls[0]["input"]["startUrls"][0]["url"].endswith("domain=tandemdiabetes.com")


def test_a_failed_read_does_not_replace_the_last_good_one_and_says_why_in_the_job():
    s = MemStore()
    refresh(s, platforms=("meta",), competitors=(TANDEM,))
    good = s.state_get(cf.cache_key("meta", TANDEM, "US"))
    job, _ = refresh(s, runner=Runner(fail=["apify/facebook-ads-scraper"]), platforms=("meta",), competitors=(TANDEM,), force=True)
    step = job["steps"][0]
    assert step["status"] == "failed" and "The Meta read failed" in step["error"] and step["ads"] == 0 and s.state_get(cf.cache_key("meta", TANDEM, "US")) == good
    fresh_s = MemStore()
    job, _ = refresh(fresh_s, runner=Runner(fail=["apify/facebook-ads-scraper"]), platforms=("meta",), competitors=(TANDEM,))
    kept = fresh_s.state_get(cf.cache_key("meta", TANDEM, "US"))
    assert kept["ads"] == [] and "The Meta read failed" in kept["error"] and not cf.fresh(kept, NOW)                  # a first failure is kept as a failure, and is read again next time


def test_two_refreshes_of_one_account_never_run_at_once():
    s = MemStore()
    s.locks.add("lock:compete:google:%s" % G)
    job, r = refresh(s)
    assert job == {"status": "busy"} and r.calls == [] and s.state_get("compete:job:google:%s" % G) is None


def test_a_crash_in_a_refresh_is_recorded_and_the_lock_is_released(monkeypatch):
    s = MemStore()

    def boom(*a, **k):
        raise RuntimeError("the store broke")
    monkeypatch.setattr(cf, "read_one", boom)
    job, _ = refresh(s)
    assert job["status"] == "failed" and "RuntimeError: the store broke" in job["error"] and not s.locks and s.state_get("compete:job:google:%s" % G)["status"] == "failed"


def test_the_entries_for_the_analysis_say_what_was_read_failed_skipped_or_not_read_yet():
    s = MemStore()
    refresh(s, platforms=("meta",), competitors=(TANDEM,))
    s.state_set(cf.cache_key("google", TANDEM, "US"), {"ads": [], "error": "The Google read failed: x", "skipped": "", "fetchedAt": "2026-10-20T00:00:00+00:00"})
    e = {(x["name"], x["platform"]): x for x in cf.entries_for(s, [TANDEM, OMNI, BARE], ["meta", "google"], "US")}
    assert len(e[("Tandem", "meta")]["ads"]) == 1 and e[("Tandem", "meta")]["error"] == "" and e[("Tandem", "meta")]["fetchedAt"].startswith("2026-10-27")
    assert e[("Tandem", "google")]["error"] == "The Google read failed: x" and e[("Tandem", "google")]["ads"] == []
    assert "No Facebook page" in e[("Insulet", "meta")]["skipped"] and e[("Insulet", "google")]["skipped"] == "Not read yet: press Refresh competitor ads."
    assert e[("Bare", "meta")]["skipped"] == "Not read yet: press Refresh competitor ads." and "No website or advertiser id" in e[("Bare", "google")]["skipped"]


def test_a_read_is_fresh_for_a_week_unless_it_failed():
    ok = {"fetchedAt": "2026-10-25T00:00:00+00:00", "error": ""}
    assert cf.fresh(ok, NOW) and not cf.fresh(dict(ok, error="x"), NOW) and not cf.fresh(None, NOW) and not cf.fresh({"fetchedAt": "2026-10-01T00:00:00+00:00"}, NOW) and not cf.fresh({"fetchedAt": "nope"}, NOW) and not cf.fresh({}, NOW)


# ── the report ──────────────────────────────────────────────────────────────

def raw_of(platform, ads, name="Acme"):
    return {"accounts": [{"id": "x", "name": name, "platform": platform, "ads": ads}], "warnings": [], "members": []}


def own_raw_ads(platform):
    return [{"id": "o%d" % i, "platform": platform, "kind": "rsa" if platform == "google" else "meta", "campaign": "C", "url": "https://acme.com",
             "texts": [{"slot": "headline" if platform == "google" else "title", "text": "Friendly team number %d" % i}, {"slot": "description" if platform == "google" else "primary", "text": "We care about you %d" % i}]} for i in range(4)]


@pytest.fixture
def own(monkeypatch):
    from tracker import paid_copycheck
    seen = []

    def load_raw(account_id, **kw):
        seen.append(account_id)
        platform = "meta" if str(account_id).startswith("act_") else "google"
        if account_id == "9999999999":
            return dict(raw_of("google", []), members=["a", "b"]), False, {"id": account_id, "name": "Everything", "members": [{"id": "1"}, {"id": "2"}]}
        return raw_of(platform, own_raw_ads(platform)), False, {"id": account_id, "name": "Acme " + platform}
    monkeypatch.setattr(paid_copycheck, "load_raw", load_raw)
    return seen


def test_the_scope_is_one_google_account_and_or_one_meta_account():
    assert pc.scope_of(G, MA) == ({"google": G, "meta": MA}, "google:1234567890+meta:act_1234567") and pc.scope_of(G, "") == ({"google": G}, "google:1234567890") and pc.scope_of("", "1234567")[0] == {"meta": "act_1234567"}
    for g, m, msg in (("", "", "Pick a Google Ads account"), ("abc", "", "Pick one Google Ads account"), ("", "act_x", "Pick one Meta ad account")):
        with pytest.raises(pc.BadRequest, match=msg):
            pc.scope_of(g, m)


def test_the_competitors_of_two_accounts_are_named_once_and_the_first_region_set_is_used():
    st = {"accounts": {G: {"competitors": [TANDEM], "region": "GB"}, MA: {"competitors": [dict(TANDEM, name="Tandem again"), OMNI], "region": "US"}}}
    comps, region = pc.competitors_of(st, {"google": G, "meta": MA})
    assert [c["name"] for c in comps] == ["Tandem", "Insulet"] and region == "GB"
    assert pc.competitors_of({}, {"google": G}) == ([], "US")


def test_with_no_competitor_saved_the_report_says_so_and_still_reads_our_ads(own):
    s = MemStore()
    rep = pc.report_for(G, "", mon=s, now=NOW)
    assert rep["status"] == "no_competitors" and rep["competitors"] == [] and rep["ideas"] == [] and rep["own"] == {"google": {"ads": 4}} and rep["estimate"]["reads"] == 0 and rep["notMeasured"]
    assert own == [G] and rep["platforms"] == ["google"] and rep["names"] == {"google": "Acme google"} and rep["key"] == "google:%s" % G and rep["job"] is None


def test_the_report_compares_the_cached_competitor_ads_with_ours_and_gives_ideas(own):
    s = MemStore()
    cs.save_account(s, MA, "Acme", {"competitors": "A | a.com | AFacebook\nB | b.com | BFacebook"}, "ana", NOW)
    for who, fb in (("A", "https://www.facebook.com/AFacebook"), ("B", "https://www.facebook.com/BFacebook")):
        s.state_set(cf.cache_key("meta", {"facebook": fb}, "US"), {"ads": pool_with_price(who=(who,)), "error": "", "skipped": "", "fetchedAt": "2026-10-26T00:00:00+00:00", "items": 4})
    rep = pc.report_for("", MA, mon=s, now=NOW)
    assert rep["status"] == "ok" and rep["counts"]["gaps"] >= 1 and rep["ideas"][0]["platform"] == "meta" and rep["analysis"]["meta"]["pool"]["ads"] == 8 and rep["estimate"] == {"reads": 2, "ads": 800, "usd": 4.0, "minutes": 8}
    assert rep["region"] == "US" and [c["name"] for c in rep["competitors"]] == ["A", "B"] and rep["limits"] == {"competitors": 8, "ads": {"meta": 400, "google": 150}} and rep["cacheDays"] == 7


def test_the_report_carries_the_refresh_in_progress(own):
    s = MemStore()
    cs.save_account(s, G, "", {"competitors": "A | a.com"}, "ana", NOW)
    s.state_set("compete:job:google:%s" % G, {"status": "running", "total": 1, "steps": []})
    assert pc.report_for(G, "", mon=s, now=NOW)["job"]["status"] == "running"


def test_a_group_of_accounts_is_refused_because_each_client_has_its_own_competitors(own):
    with pytest.raises(pc.BadRequest, match="Everything is a group of accounts"):
        pc.report_for("9999999999", "", mon=MemStore(), now=NOW)


def test_our_ads_that_cannot_be_read_stop_the_report_in_words(monkeypatch):
    from tracker import paid_copycheck

    def down(*a, **k):
        raise RuntimeError("Google said no")
    monkeypatch.setattr(paid_copycheck, "load_raw", down)
    with pytest.raises(pc.BadRequest, match=r"The ads of 1234567890 could not be read \(Google said no\)"):
        pc.report_for(G, "", mon=MemStore(), now=NOW)


def test_our_ads_read_twice_through_the_real_saved_read_with_a_datetime_clock(monkeypatch):
    """The report's own clock is a datetime but the saved read counts in epoch seconds: the second ask of an account used to subtract one datetime from another and fail on a timedelta."""
    from gads_fakes import FakeStore
    from tracker import paid_geo
    paid_geo.clear_cache()
    reads = dict(store=FakeStore(), today=NOW.date(), meta_pages_fn=lambda path, params: [], meta_account_fn=lambda act: {"name": "Meta Co"})
    first = pc.own_ads("meta", MA, now=NOW, **reads)
    second = pc.own_ads("meta", MA, now=NOW, **reads)
    third = pc.own_ads("meta", MA, now=NOW, force=False, **reads)
    paid_geo.clear_cache()
    assert first == second == third == ([], [], "Meta Co")


def test_only_the_ads_of_the_platform_asked_for_are_ours_and_assets_are_left_out(monkeypatch):
    from tracker import paid_copycheck
    mixed = own_raw_ads("google") + [{"id": "a1", "platform": "google", "kind": "assets", "texts": [{"slot": "headline", "text": "Sitelink"}]}, dict(own_raw_ads("meta")[0], id="m")]
    monkeypatch.setattr(paid_copycheck, "load_raw", lambda account_id, **kw: (raw_of("google", mixed), False, {"id": account_id, "name": "Acme"}))
    ads, warnings, name = pc.own_ads("google", G, now=NOW)
    assert len(ads) == 4 and warnings == [] and name == "Acme" and all(a["platform"] == "google" for a in ads)
