"""Meta (Facebook and Instagram) Ads in the Paid Marketing agents, read-only.

Pinned here: how a Meta insights row becomes the page's columns (link clicks, the objective's results, no double
counting of one event under two names); the token goes in a header, never in a URL, and is never returned; only GET
requests are ever sent; Meta's errors become answers the page can show (bad token, no access, rate limit with a wait
that stops further requests, a hiccup retried once); days are stored one at a time and a range costs nothing when its
days are stored; the account list, reach and frequency, device, placement and hour splits; the routes (Meta is
dispatched on the `act_` id, a missing token is a clear 409, the admin check never shows the token); Segment Analysis
for a Meta account and for a Google and a Meta one together; and Product Analysis from Meta's product_id breakdown.
"""
import datetime as dt
import json
import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from tests.gads_fakes import FakeStore  # noqa: E402
from tracker import paid_gads as g  # noqa: E402
from tracker import paid_meta as mt  # noqa: E402
from tracker import paid_meta_products as mp  # noqa: E402
from tracker import paid_meta_segments as ms  # noqa: E402
from tracker import paid_products as pp  # noqa: E402
from tracker import paid_segments as seg  # noqa: E402
from tracker import paid_store  # noqa: E402

TOKEN = "EAAB-secret-token-123"
ACT = "act_1000000001"
TODAY = dt.date.today()


def d(offset):
    return (TODAY - dt.timedelta(days=offset)).isoformat()


def days_between(a, b):
    x, y = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return [(x + dt.timedelta(days=i)).isoformat() for i in range((y - x).days + 1)]


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body if body is not None else {}, headers or {}

    def json(self):
        return self._b


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("META_ADS_ACCESS_TOKEN", TOKEN)
    monkeypatch.delenv("META_BUSINESS_ID", raising=False)
    monkeypatch.delenv("META_API_VERSION", raising=False)
    mt.reset_state()
    g.reset_state()
    seg.clear_cache()
    monkeypatch.setattr(mt.time, "sleep", lambda s: None)


# ── The fake Meta ───────────────────────────────────────────────────────────

def insight(cid, day, objective="OUTCOME_SALES", spend="10.00", impr="1000", link="50", actions=None, values=None, **extra):
    r = {"campaign_id": cid, "campaign_name": "Camp " + cid, "objective": objective, "date_start": day, "date_stop": day,
         "spend": spend, "impressions": impr, "inline_link_clicks": link, "inline_post_engagement": "7",
         "video_play_actions": [{"action_type": "video_view", "value": "20"}]}
    r["actions"] = actions if actions is not None else [{"action_type": "omni_purchase", "value": "2"}, {"action_type": "purchase", "value": "2"},
                                                         {"action_type": "link_click", "value": "50"}]
    r["action_values"] = values if values is not None else [{"action_type": "omni_purchase", "value": "150.5"}, {"action_type": "purchase", "value": "150.5"}]
    r.update(extra)
    return r


def meta_http(log, campaigns=("11", "12"), quiet=(), fail=None, accounts=None, pages=None, headers_of=None):
    """A Meta that answers the account list, the campaigns edge and insights (one row per campaign per day)."""
    def http(url, headers=None, params=None, timeout=None):
        log.append({"url": url, "headers": headers, "params": params})
        if fail:
            return fail(len(log)) if callable(fail) else fail
        path = url.split("/", 4)[-1] if url.startswith(mt.GRAPH) else url
        if path.endswith("me/adaccounts") or "adaccounts" in path:
            return Resp(200, {"data": accounts or []})
        if path.endswith("/campaigns"):
            return Resp(200, {"data": [{"id": c, "effective_status": "ACTIVE" if c == "11" else "PAUSED", "daily_budget": "7500"} for c in campaigns]})
        p = params or {}
        if p.get("level") == "account":
            return Resp(200, {"data": [{"reach": "5000", "frequency": "1.75"}]})
        rng = json.loads(p["time_range"]) if p.get("time_range") else None
        if pages and "page2" in url:
            return Resp(200, {"data": pages[1]})
        days = days_between(rng["since"], rng["until"]) if rng else []
        rows = [insight(c, day) for day in days if day not in quiet for c in campaigns]
        if p.get("breakdowns"):
            rows = pages_for_breakdown(p["breakdowns"], campaigns)
        out = {"data": rows}
        if pages:
            out = {"data": pages[0], "paging": {"next": mt.GRAPH + "v23.0/" + ACT + "/insights?page2=1"}}
        return Resp(200, out, headers_of(len(log)) if headers_of else None)
    return http


def pages_for_breakdown(breakdown, campaigns):
    values = {"device_platform": ["mobile_app", "mobile_web", "desktop"], "publisher_platform": ["facebook", "instagram", "weird_new"],
              "hourly_stats_aggregated_by_advertiser_time_zone": ["09:00:00 - 09:59:59", "21:00:00 - 21:59:59"],
              "age": ["18-24", "25-34", "Unknown"], "gender": ["female", "male", "unknown"], "region": ["Texas", "Ohio"],
              "product_id": ["SKU-1", "sku-2"]}[breakdown]
    out = []
    for v in values:
        for c in campaigns:
            r = insight(c, "2026-10-01")
            r[breakdown] = v
            out.append(r)
    return out


# ── How a row becomes the page's columns ────────────────────────────────────

def test_clicks_are_link_clicks_and_cost_is_spend():
    n = mt.numbers(insight("1", d(2), impr="1234", link="77", spend="12.3456"))
    assert n[0] == 1234 and n[1] == 77 and n[2] == 12.3456 and len(n) == g.NB


def test_a_sales_campaigns_result_is_purchases_counted_once_not_under_both_names():
    n = mt.numbers(insight("1", d(2)))          # omni_purchase 2 AND purchase 2 for the same events
    assert n[3] == 2 and n[4] == 150.5
    only_plain = mt.numbers(insight("1", d(2), actions=[{"action_type": "purchase", "value": "3"}],
                                    values=[{"action_type": "purchase", "value": "99"}]))
    assert only_plain[3] == 3 and only_plain[4] == 99


def test_a_lead_gen_campaign_optimised_for_a_custom_pixel_event_counts_that_event_and_has_no_value():
    # real shape: no "lead" at all, only the custom event, whose "value" is just its count
    n = mt.numbers(insight("1", d(2), objective="OUTCOME_LEADS", actions=[{"action_type": "offsite_conversion.fb_pixel_custom", "value": "12"}],
                           values=[{"action_type": "offsite_conversion.fb_pixel_custom", "value": "12"}]))
    assert n[3] == 12 and n[4] == 0 and n[5] == 12 and n[6] == 0          # a result and a conversion event, but no revenue
    both = mt.numbers(insight("1", d(2), objective="OUTCOME_LEADS", actions=[{"action_type": "lead", "value": "5"}, {"action_type": "offsite_conversion.fb_pixel_custom", "value": "12"}], values=[]))
    assert both[3] == 5 and both[5] == 17                                   # the standard lead is the result; a different custom event still counts as a conversion


def test_a_pixel_purchase_with_no_omni_name_still_counts():
    n = mt.numbers(insight("1", d(2), actions=[{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "4"}],
                           values=[{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "320"}]))
    assert n[3] == 4 and n[4] == 320


def test_a_leads_campaigns_result_is_leads_and_has_no_value():
    n = mt.numbers(insight("1", d(2), objective="OUTCOME_LEADS", actions=[{"action_type": "lead", "value": "9"}, {"action_type": "onsite_conversion.lead_grouped", "value": "9"}], values=[]))
    assert n[3] == 9 and n[4] == 0


def test_traffic_awareness_and_engagement_have_no_conversions_but_all_conversions_still_counts_events():
    for obj in ("OUTCOME_TRAFFIC", "OUTCOME_AWARENESS", "OUTCOME_ENGAGEMENT", "LINK_CLICKS", "REACH"):
        n = mt.numbers(insight("1", d(2), objective=obj))
        assert n[3] == 0 and n[4] == 0
        assert n[5] == 2 and n[6] == 150.5          # a purchase that happened under a traffic campaign is still a conversion event, with its revenue


def test_an_objective_nobody_has_heard_of_falls_back_to_purchases_then_leads():
    assert mt.numbers(insight("1", d(2), objective="SOMETHING_NEW"))[3] == 2
    lead = insight("1", d(2), objective="SOMETHING_NEW", actions=[{"action_type": "lead", "value": "4"}], values=[])
    assert mt.numbers(lead)[3] == 4


@pytest.mark.parametrize("objective, kind", [("OUTCOME_SALES", "SALES"), ("PRODUCT_CATALOG_SALES", "SALES"), ("CONVERSIONS", "SALES"),
                                              ("OUTCOME_LEADS", "LEADS"), ("LEAD_GENERATION", "LEADS"), ("OUTCOME_APP_PROMOTION", "APP"),
                                              ("OUTCOME_ENGAGEMENT", "ENGAGEMENT"), ("nonsense", "OTHER"), (None, "OTHER")])
def test_objectives_become_campaign_types(objective, kind):
    assert mt.objective_kind(objective) == kind and mt.campaign_type(objective) == "META_" + kind


def test_columns_meta_does_not_have_are_marked_missing_and_are_zero():
    n = mt.numbers(insight("1", d(2)))
    for i in mt.MISSING:
        assert n[i] == 0
    assert set(mt.MISSING) == {7, 9, 11, 12, 13, 14, 15, 16}            # view-through, engagements, cross-device, invalid, calls, viewability
    assert n[8] == 7 and n[10] == 20                                     # post engagement, video plays


def test_the_page_columns_line_up_with_googles():
    assert len(mt.numbers(insight("1", d(1)))) == g.NB
    names = [f for f, _ in g.BASE]
    for i in mt.MISSING:
        assert names[i] in ("view_through_conversions", "engagements", "cross_device_conversions", "invalid_clicks", "phone_calls",
                            "phone_impressions", "active_view_impressions", "active_view_measurable_impressions")


# ── Accounts ────────────────────────────────────────────────────────────────

def test_accounts_are_normalised_deduplicated_and_say_when_they_are_not_active():
    rows = [{"account_id": "222", "name": "Zed", "currency": "GBP", "timezone_name": "Europe/London", "account_status": 1},
            {"account_id": "act_1000000001", "name": "Alpha", "currency": "USD", "account_status": 1},
            {"id": "act_1000000001", "name": "Alpha again", "currency": "USD", "account_status": 1},
            {"account_id": "1000000003", "name": "Gone", "currency": "USD", "account_status": 2},
            {"account_id": "bad", "name": "Skip me"}]
    out = mt.parse_accounts(rows)
    assert [a["id"] for a in out] == ["act_1000000001", "act_1000000003"] or [a["id"] for a in out] == ["act_1000000003", "act_1000000001"]
    by = {a["id"]: a for a in out}
    assert by["act_1000000003"]["name"] == "Gone (disabled)" and by["act_1000000003"]["status"] == "DISABLED"
    assert by["act_1000000001"]["platform"] == "meta" and by["act_1000000001"]["currency"] == "USD"
    assert all(a["id"].startswith("act_") for a in out)


def test_account_ids_are_taken_in_any_spelling_and_nothing_else():
    assert mt.norm_id("1000000001") == ACT == mt.norm_id(" ACT_1000000001 ")
    assert mt.norm_id("1234567890123456") == "act_1234567890123456"
    for bad in ("", "act_", "act_12", "abc", "1112223334; drop", None):
        assert mt.norm_id(bad) == "" or bad == "1112223334; drop" and mt.norm_id(bad) == ""
    assert mt.is_meta(ACT) and not mt.is_meta("1112223334") and not mt.is_meta("1112223334+" + ACT)


def test_the_account_list_reads_me_adaccounts_and_the_business_when_one_is_set(monkeypatch):
    log, s = [], FakeStore()
    accounts = [{"account_id": "1000000001", "name": "Alpha", "currency": "USD", "account_status": 1}]
    http = meta_http(log, accounts=accounts)
    assert [a["id"] for a in mt.list_accounts(s, http=http)] == [ACT]
    assert len(log) == 1 and log[0]["url"].endswith("/me/adaccounts")
    monkeypatch.setenv("META_BUSINESS_ID", "555")
    log.clear()
    mt.list_accounts(s, http=http, force=True)
    assert [x["url"].split("/")[-2:] for x in log] == [["v23.0", "me/adaccounts"][-2:] and ["v23.0", "adaccounts"], ["555", "owned_ad_accounts"], ["555", "client_ad_accounts"]] or len(log) == 3


def test_a_business_edge_that_fails_does_not_hide_the_accounts_that_were_read(monkeypatch):
    monkeypatch.setenv("META_BUSINESS_ID", "555")
    log, s = [], FakeStore()
    accounts = [{"account_id": "1000000001", "name": "Alpha", "currency": "USD", "account_status": 1}]

    def http(url, headers=None, params=None, timeout=None):
        log.append(url)
        if "555" in url:
            return Resp(400, {"error": {"code": 100, "message": "Unsupported get request"}})
        return Resp(200, {"data": accounts})
    assert [a["id"] for a in mt.list_accounts(s, http=http)] == [ACT]


# ── The request: token, method, errors ──────────────────────────────────────

def test_the_token_is_a_header_never_in_the_url_or_the_parameters_and_only_get_is_used():
    log, s = [], FakeStore()
    mt.campaign_days(s, ACT, d(5), d(3), http=meta_http(log))
    assert log
    for call in log:
        assert call["headers"]["Authorization"] == "Bearer " + TOKEN
        assert TOKEN not in call["url"] and TOKEN not in json.dumps(call["params"] or {})
        assert call["url"].startswith("https://graph.facebook.com/v23.0/")


def test_nothing_in_the_module_sends_anything_but_a_get():
    src = open(os.path.join(ROOT, "tracker", "paid_meta.py")).read() + open(os.path.join(ROOT, "tracker", "paid_meta_segments.py")).read() \
        + open(os.path.join(ROOT, "tracker", "paid_meta_products.py")).read()
    for bad in ("requests.post", "requests.put", "requests.delete", "requests.patch", "method=\"POST\"", ".post(", "data=json.dumps"):
        assert bad not in src, bad


def test_the_api_version_can_be_set_and_a_bad_one_is_ignored(monkeypatch):
    monkeypatch.setenv("META_API_VERSION", "v24.0")
    assert mt.version() == "v24.0"
    monkeypatch.setenv("META_API_VERSION", "latest; rm -rf")
    assert mt.version() == mt.DEFAULT_VERSION


def test_without_a_token_the_problem_is_named():
    os.environ.pop("META_ADS_ACCESS_TOKEN")
    try:
        assert "META_ADS_ACCESS_TOKEN" in mt.config_problem()
    finally:
        os.environ["META_ADS_ACCESS_TOKEN"] = TOKEN
    assert mt.config_problem() is None


def error(code, message="Bad", status=400, headers=None):
    return Resp(status, {"error": {"code": code, "message": message}}, headers)


def test_a_rejected_token_says_so_and_never_repeats_the_token():
    log, s = [], FakeStore()
    with pytest.raises(g.AdsError) as e:
        mt.campaign_days(s, ACT, d(5), d(3), http=meta_http(log, fail=error(190, "Error validating access token", 401)))
    assert "META_ADS_ACCESS_TOKEN" in str(e.value) and TOKEN not in str(e.value) and e.value.status == 401


def test_an_account_the_token_cannot_read_says_to_assign_it():
    s = FakeStore()
    with pytest.raises(g.AdsError) as e:
        mt.campaign_days(s, ACT, d(5), d(3), http=meta_http([], fail=error(200, "Permissions error", 403)))
    assert "Business Settings" in str(e.value)


def test_a_long_rate_limit_is_remembered_and_no_further_request_is_sent():
    s, log = FakeStore(), []
    usage = json.dumps({"1000000001": [{"type": "ads_insights", "estimated_time_to_regain_access": 30}]})
    with pytest.raises(g.OverQuota) as e:
        mt.campaign_days(s, ACT, d(5), d(3), http=meta_http(log, fail=error(17, "User request limit reached", 400, {"X-Business-Use-Case-Usage": usage})))
    assert e.value.until and e.value.until > __import__("time").time() + 1500
    n = len(log)
    with pytest.raises(g.OverQuota):
        mt.campaign_days(s, ACT, d(5), d(3), http=meta_http(log))
    assert len(log) == n                                   # not one more request while Meta said to wait
    assert mt.blocked_until(s) > 0


def test_a_short_rate_limit_is_retried_once():
    s, log = FakeStore(), []
    calls = {"n": 0}

    def fail(n):
        calls["n"] += 1
        return error(17, "slow", 400) if calls["n"] == 1 else None
    http = meta_http(log)

    def wrapped(url, headers=None, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return error(17, "slow", 400)
        return http(url, headers=headers, params=params, timeout=timeout)
    out = mt.campaign_days(s, ACT, d(5), d(4), http=wrapped)
    assert len(out["days"]) == 2


def test_a_hiccup_is_retried_once_and_then_reported():
    s, log = FakeStore(), []
    seen = {"n": 0}

    def http(url, headers=None, params=None, timeout=None):
        seen["n"] += 1
        return error(2, "Service temporarily unavailable", 500)
    with pytest.raises(g.AdsError) as e:
        mt.campaign_days(s, ACT, d(5), d(4), http=http)
    assert seen["n"] == 2 and "Service temporarily unavailable" in str(e.value)


def test_an_unreachable_meta_is_an_ads_error_not_a_crash():
    def http(*a, **k):
        raise ConnectionError("dns")
    with pytest.raises(g.AdsError) as e:
        mt.campaign_days(FakeStore(), ACT, d(5), d(4), http=http)
    assert "Could not reach Meta" in str(e.value)


# ── Days ───────────────────────────────────────────────────────────────────

def test_a_range_is_stored_one_day_at_a_time_and_built_from_the_stored_days():
    s, log = FakeStore(), []
    out = mt.campaign_days(s, ACT, d(10), d(4), http=meta_http(log))
    inserts = [c for c in log if c["url"].endswith("/insights")]
    assert len(inserts) == 1 and json.loads(inserts[0]["params"]["time_range"]) == {"since": d(10), "until": d(4)}
    assert len(out["days"]) == 7 and len(out["rows"]) == 14
    assert sorted(day for (_, day) in s.days) == days_between(d(10), d(4))
    assert {c for (c, _) in s.days} == {mt.day_key(ACT)}
    assert out["missing"] == mt.MISSING
    log.clear()
    again = mt.campaign_days(s, ACT, d(8), d(5), http=lambda *a, **k: pytest.fail("a stored range went to Meta"))
    assert len(again["days"]) == 4 and log == []


def test_a_long_run_is_asked_for_in_chunks_of_a_month():
    s, log = FakeStore(), []
    mt.campaign_days(s, ACT, d(100), d(1), http=meta_http(log))
    spans = [json.loads(c["params"]["time_range"]) for c in log if c["url"].endswith("/insights")]
    assert len(spans) == 4 and spans[0]["since"] == d(100) and spans[-1]["until"] == d(1)
    assert all((dt.date.fromisoformat(x["until"]) - dt.date.fromisoformat(x["since"])).days < mt.CHUNK_DAYS for x in spans)


def test_a_wider_range_asks_only_for_the_days_it_does_not_have():
    s, log = FakeStore(), []
    mt.campaign_days(s, ACT, d(20), d(10), http=meta_http(log))
    log.clear()
    mt.campaign_days(s, ACT, d(30), d(5), http=meta_http(log))
    got = [(json.loads(c["params"]["time_range"])["since"], json.loads(c["params"]["time_range"])["until"]) for c in log if c["url"].endswith("/insights")]
    assert got == [(d(30), d(21)), (d(9), d(5))]


def test_a_quiet_day_is_stored_as_quiet_and_not_asked_for_again():
    s, log = FakeStore(), []
    out = mt.campaign_days(s, ACT, d(12), d(10), http=meta_http(log, quiet={d(11)}))
    assert s.days[(mt.day_key(ACT), d(11))]["r"] == [] and len(out["rows"]) == 4
    log.clear()
    mt.campaign_days(s, ACT, d(12), d(10), http=meta_http(log))
    assert log == []


def test_recent_days_go_stale_within_the_hour_and_old_days_do_not():
    s, log = FakeStore(), []
    mt.campaign_days(s, ACT, d(40), d(0), http=meta_http(log))
    for key in list(s.dayat):
        s.dayat[key] -= 2 * 3600
    log.clear()
    mt.campaign_days(s, ACT, d(40), d(0), http=meta_http(log))
    asked = [json.loads(c["params"]["time_range"]) for c in log if c["url"].endswith("/insights")]
    assert asked and asked[0]["since"] >= d(31)                      # only the days that change by the hour were read again
    assert all(x["until"] <= d(0) for x in asked)


def test_force_reads_every_day_again():
    s, log = FakeStore(), []
    mt.campaign_days(s, ACT, d(10), d(4), http=meta_http(log))
    log.clear()
    mt.campaign_days(s, ACT, d(10), d(4), force=True, http=meta_http(log))
    assert any(c["url"].endswith("/insights") for c in log)


def test_status_and_budget_come_from_the_campaigns_edge_and_a_failure_there_does_not_stop_the_numbers():
    s, log = FakeStore(), []
    out = mt.campaign_days(s, ACT, d(5), d(4), http=meta_http(log))
    by = {c["id"]: c for c in out["campaigns"]}
    assert by["11"]["status"] == "ENABLED" and by["12"]["status"] == "PAUSED" and by["11"]["budget"] == 75.0
    assert by["11"]["type"] == "META_SALES"

    s2, http = FakeStore(), meta_http([])
    g.forget()                                              # the minute held in memory belongs to the first call

    def partly(url, headers=None, params=None, timeout=None):
        if url.endswith("/campaigns"):
            return error(100, "nope", 400)
        return http(url, headers=headers, params=params, timeout=timeout)
    out2 = mt.campaign_days(s2, ACT, d(5), d(4), http=partly)
    assert len(out2["rows"]) == 4 and all(c["status"] == "ENABLED" for c in out2["campaigns"])


def test_a_whole_number_currency_budget_is_not_divided():
    assert mt._budget({"daily_budget": "7500"}, "USD") == 75.0
    assert mt._budget({"daily_budget": "7500"}, "JPY") == 7500.0
    assert mt._budget({"lifetime_budget": "10000"}, "USD") == 100.0
    assert mt._budget({}, "USD") == 0.0


def test_paging_is_followed():
    rows1 = [insight("11", d(5))]
    rows2 = [insight("12", d(5))]
    s, log = FakeStore(), []
    out = mt.campaign_days(s, ACT, d(5), d(5), http=meta_http(log, pages=[rows1, rows2]))
    assert len(out["rows"]) == 2


def test_stored_days_stand_in_when_meta_cannot_answer_and_say_so():
    s, log = FakeStore(), []
    mt.campaign_days(s, ACT, d(10), d(8), http=meta_http(log))
    for key in list(s.dayat):
        s.dayat[key] -= 3 * 86400 * 10
    notes = []
    usage = json.dumps({"1": [{"estimated_time_to_regain_access": 60}]})
    out = mt.campaign_days(s, ACT, d(10), d(8), notes=notes, http=meta_http([], fail=error(17, "limit", 400, {"X-Business-Use-Case-Usage": usage})))
    assert len(out["rows"]) == 6 and notes


def test_a_rejected_token_is_never_hidden_behind_stored_days():
    s, log = FakeStore(), []
    mt.campaign_days(s, ACT, d(10), d(8), http=meta_http(log))
    for key in list(s.dayat):
        s.dayat[key] -= 3 * 86400 * 10                       # stale, so they would stand in for a limit
    with pytest.raises(g.AdsError) as e:
        mt.campaign_days(s, ACT, d(10), d(8), http=meta_http([], fail=error(190, "Error validating access token", 401)))
    assert "META_ADS_ACCESS_TOKEN" in str(e.value)


# ── Performance, reach, splits ──────────────────────────────────────────────

def test_performance_has_the_pages_shape_and_account_wide_reach():
    s, log = FakeStore(), []
    out = mt.performance(s, ACT, d(6), d(5), http=meta_http(log))
    assert set(out) >= {"start", "end", "days", "campaigns", "rows", "missing", "sis", "shares"}
    assert out["shares"] == {"reach": 5000, "frequency": 1.75} and out["sis"] == {"account": None, "campaigns": {}}


def test_performance_pair_reads_the_comparison_range_too():
    s, log = FakeStore(), []
    cur, cmp, notes = mt.performance_pair(s, ACT, (d(6), d(5)), (d(8), d(7)), http=meta_http(log))
    assert cur["start"] == d(6) and cmp["start"] == d(8) and notes == []
    assert mt.performance_pair(FakeStore(), ACT, (d(6), d(5)), None, http=meta_http([]))[1] is None


def test_reach_that_cannot_be_read_leaves_the_numbers_standing():
    s = FakeStore()
    http = meta_http([])

    def partly(url, headers=None, params=None, timeout=None):
        if (params or {}).get("level") == "account":
            return error(100, "no", 400)
        return http(url, headers=headers, params=params, timeout=timeout)
    out = mt.performance(s, ACT, d(6), d(5), http=partly)
    assert out["shares"] == {"reach": None, "frequency": None} and out["rows"]


def test_device_placement_and_hour_splits_use_the_pages_names():
    s, log = FakeStore(), []
    out = mt.extras(s, ACT, d(6), d(5), http=meta_http(log))
    assert {r[0] for r in out["device"]} == {"MOBILE", "DESKTOP"}                      # app and web both mobile
    assert {r[0] for r in out["network"]} == {"FACEBOOK", "INSTAGRAM", "OTHER"}
    assert [r[0] for r in out["hour"]] == [9, 21] and out["dowHour"] == []             # Meta has the hour but not the weekday
    assert out["missing"] == mt.MISSING
    mobile = next(r for r in out["device"] if r[0] == "MOBILE")
    one = mt.numbers(insight("11", "2026-10-01"))
    assert mobile[1] == 4 * one[0]                                                      # 2 devices x 2 campaigns


# ── The admin check ─────────────────────────────────────────────────────────

def test_the_status_check_says_what_it_read_and_never_shows_the_token():
    s, log = FakeStore(), []
    accounts = [{"account_id": "1000000001", "name": "Alpha", "currency": "USD", "account_status": 1},
                {"account_id": "1000000002", "name": "Old", "currency": "EUR", "account_status": 2}]
    out = mt.status(s, http=meta_http(log, accounts=accounts))
    assert out["configured"] and out["accounts"] == 2 and out["byStatus"] == {"ACTIVE": 1, "DISABLED": 1}
    assert out["currencies"] == ["EUR", "USD"] and out["read"]["account"] == "Alpha" and out["read"]["spend"] > 0
    assert TOKEN not in json.dumps(out)


def test_the_status_check_with_no_token_says_what_is_missing():
    os.environ.pop("META_ADS_ACCESS_TOKEN")
    try:
        out = mt.status(FakeStore())
    finally:
        os.environ["META_ADS_ACCESS_TOKEN"] = TOKEN
    assert out["configured"] is False and "META_ADS_ACCESS_TOKEN" in out["problem"]


# ── Routes ─────────────────────────────────────────────────────────────────

STAFF = "someone@position2.com"
ADMIN = "krishna.ladha@position2.com"
GOOGLE_ACCTS = [{"id": "1234567890", "name": "Acme", "currency": "USD", "tz": "", "level": 1}]
META_ACCTS = [{"id": ACT, "name": "Acme Meta", "currency": "USD", "tz": "", "level": 1, "platform": "meta", "status": "ACTIVE"}]


def client_as(email):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def routes(monkeypatch):
    fs = FakeStore()
    for name in ("get_gads_entry", "put_gads_cache", "get_gads_cache", "gads_calls", "get_gads_block"):
        monkeypatch.setattr(paid_store, name, getattr(fs, name))
    for k, v in (("GOOGLE_ADS_CLIENT_ID", "a"), ("GOOGLE_ADS_CLIENT_SECRET", "b"), ("GOOGLE_ADS_DEVELOPER_TOKEN", "c"), ("GOOGLE_ADS_REFRESH_TOKEN", "d")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(g, "list_accounts", lambda st, **k: GOOGLE_ACCTS)
    monkeypatch.setattr(mt, "list_accounts", lambda st, **k: META_ACCTS)
    seen = {"google": [], "meta": []}

    def gpair(st, cid, cur, cmp, with_sis=True, with_shares=True, force=False, stamps=None, **k):
        seen["google"].append(cid)
        return {"start": cur[0], "days": [], "campaigns": [], "rows": []}, None, []

    def mpair(st, act, cur, cmp, currency="USD", with_shares=True, force=False, stamps=None, **k):
        seen["meta"].append((act, currency, force))
        if stamps is not None:
            stamps.append("2026-10-14T08:00:00+00:00")
        return {"start": cur[0], "days": [], "campaigns": [], "rows": []}, None, []
    monkeypatch.setattr(g, "performance_pair", gpair)
    monkeypatch.setattr(mt, "performance_pair", mpair)
    return seen


Q = "&start=2026-10-01&end=2026-10-07"


def test_a_meta_account_is_read_from_meta_and_a_google_one_from_google(routes):
    c = client_as(STAFF)
    j = c.get("/p2/paid-marketing/api/gads/performance?account=" + ACT + Q).get_json()
    assert routes == {"google": [], "meta": [(ACT, "USD", False)]} and j["account"]["platform"] == "meta"
    j2 = c.get("/p2/paid-marketing/api/gads/performance?account=1234567890" + Q).get_json()
    assert routes["google"] == ["1234567890"] and j2["account"]["platform"] == "google"


def test_an_account_the_token_cannot_read_is_a_400_not_a_crash(routes):
    r = client_as(STAFF).get("/p2/paid-marketing/api/gads/performance?account=act_999999999" + Q)
    assert r.status_code == 400 and "Meta" in r.get_json()["error"]
    assert client_as(STAFF).get("/p2/paid-marketing/api/gads/performance?account=1112223334%2Bact_1" + Q).status_code == 400


def test_meta_has_its_own_refresh_cooldown_and_it_is_not_googles(routes):
    c = client_as(STAFF)
    url = "/p2/paid-marketing/api/gads/performance?account=%s%s&refresh=1"
    assert c.get(url % (ACT, Q)).status_code == 200 and routes["meta"][-1][2] is True
    r = c.get(url % (ACT, Q))
    assert r.status_code == 429 and r.get_json()["cooldown"] is True
    assert c.get(url % ("1234567890", Q)).status_code == 200            # another account, another platform: not held back


def test_a_missing_token_is_a_409_that_names_it_and_google_still_works(routes, monkeypatch):
    monkeypatch.delenv("META_ADS_ACCESS_TOKEN")
    c = client_as(STAFF)
    r = c.get("/p2/paid-marketing/api/gads/performance?account=" + ACT + Q)
    assert r.status_code == 409 and "META_ADS_ACCESS_TOKEN" in r.get_json()["error"]
    assert c.get("/p2/paid-marketing/api/gads/performance?account=1234567890" + Q).status_code == 200


def test_the_account_list_by_platform(routes, monkeypatch):
    c = client_as(STAFF)
    only_g = c.get("/p2/paid-marketing/api/gads/accounts").get_json()
    assert [a["id"] for a in only_g["accounts"]] == ["1234567890"] and only_g["accounts"][0]["platform"] == "google"
    only_m = c.get("/p2/paid-marketing/api/gads/accounts?platform=meta").get_json()
    assert [a["id"] for a in only_m["accounts"]] == [ACT]
    both = c.get("/p2/paid-marketing/api/gads/accounts?platform=both").get_json()
    assert [a["id"] for a in both["accounts"]] == ["1234567890", ACT] and both["metaProblem"] is None and both["googleProblem"] is None
    assert c.get("/p2/paid-marketing/api/gads/accounts?platform=tiktok").status_code == 400


def test_both_keeps_the_google_accounts_when_meta_is_not_connected_and_the_reverse(routes, monkeypatch):
    c = client_as(STAFF)
    monkeypatch.delenv("META_ADS_ACCESS_TOKEN")
    both = c.get("/p2/paid-marketing/api/gads/accounts?platform=both").get_json()
    assert [a["id"] for a in both["accounts"]] == ["1234567890"] and "META_ADS_ACCESS_TOKEN" in both["metaProblem"]
    assert c.get("/p2/paid-marketing/api/gads/accounts?platform=meta").status_code == 409
    monkeypatch.setenv("META_ADS_ACCESS_TOKEN", TOKEN)
    monkeypatch.delenv("GOOGLE_ADS_REFRESH_TOKEN")
    both2 = c.get("/p2/paid-marketing/api/gads/accounts?platform=both").get_json()
    assert [a["id"] for a in both2["accounts"]] == [ACT] and "GOOGLE_ADS" in both2["googleProblem"]
    monkeypatch.delenv("META_ADS_ACCESS_TOKEN")
    assert c.get("/p2/paid-marketing/api/gads/accounts?platform=both").status_code == 409


def test_a_meta_failure_in_the_list_is_reported_in_both_not_fatal(routes, monkeypatch):
    def boom(st, **k):
        raise g.AdsError("Meta answered 500.")
    monkeypatch.setattr(mt, "list_accounts", boom)
    both = client_as(STAFF).get("/p2/paid-marketing/api/gads/accounts?platform=both").get_json()
    assert [a["id"] for a in both["accounts"]] == ["1234567890"] and "500" in both["metaProblem"]
    assert client_as(STAFF).get("/p2/paid-marketing/api/gads/accounts?platform=meta").status_code == 502


def test_the_wait_a_meta_account_reports_is_meta_s_not_googles(routes, monkeypatch):
    until = __import__("time").time() + 1800
    mt._BLOCK.update(until=until, checked=0.0)
    j = client_as(STAFF).get("/p2/paid-marketing/api/gads/performance?account=" + ACT + Q).get_json()
    assert j["blockedUntil"] == until
    g1 = client_as(STAFF).get("/p2/paid-marketing/api/gads/performance?account=1234567890" + Q).get_json()
    assert g1["blockedUntil"] is None


def test_the_status_route_is_for_admins_and_never_shows_the_token(routes, monkeypatch):
    monkeypatch.setattr(mt, "status", lambda store, **k: {"configured": True, "accounts": 3, "version": "v23.0"})
    assert client_as(STAFF).get("/p2/paid-marketing/api/meta/status").status_code == 403
    r = client_as(ADMIN).get("/p2/paid-marketing/api/meta/status")
    assert r.status_code == 200 and r.get_json()["accounts"] == 3 and TOKEN not in r.get_data(as_text=True)
    assert appmod.app.test_client().get("/p2/paid-marketing/api/meta/status").status_code == 302


def test_the_meta_products_are_read_from_meta(routes, monkeypatch):
    seen = []

    def analysis(st, act, cur, cmp, currency="USD", force=False, stamps=None, **k):
        seen.append((act, currency))
        return {"products": []}, None, []
    monkeypatch.setattr(mp, "analysis", analysis)
    r = client_as(STAFF).get("/p2/paid-marketing/api/gads/products?account=" + ACT + Q)
    assert r.status_code == 200 and seen == [(ACT, "USD")]


# ── Segment Analysis ────────────────────────────────────────────────────────

def fetch_for(rows_by_breakdown):
    def fetch(act, dim, params):
        key = dim["breakdown"] or "day"
        return rows_by_breakdown.get(key, [])
    return fetch


def seg_rows():
    sales = lambda day, **k: insight("11", day, spend="30.00", impr="3000", link="60", **k)           # 2 purchases
    leads = lambda day, **k: insight("12", day, objective="OUTCOME_LEADS", spend="20.00", impr="2000", link="40",
                                      actions=[{"action_type": "lead", "value": "5"}], values=[], **k)
    return {
        "day": [sales("2026-10-05"), leads("2026-10-05"), sales("2026-10-06")],                    # Mon, Tue (Oct 5 2026 is a Monday)
        "hourly_stats_aggregated_by_advertiser_time_zone": [sales("2026-10-05", hourly_stats_aggregated_by_advertiser_time_zone="09:00:00 - 09:59:59"),
                                                             leads("2026-10-05", hourly_stats_aggregated_by_advertiser_time_zone="21:00:00 - 21:59:59")],
        "age": [sales("2026-10-05", age="25-34"), leads("2026-10-05", age="Unknown")],
        "gender": [sales("2026-10-05", gender="female"), leads("2026-10-05", gender="unknown")],
        "region": [sales("2026-10-05", region="Texas"), leads("2026-10-05", region="")],
        "device_platform": [sales("2026-10-05", device_platform="desktop"), leads("2026-10-05", device_platform="mobile_app")],
        "publisher_platform": [sales("2026-10-05", publisher_platform="facebook"), leads("2026-10-05", publisher_platform="instagram")],
    }


ACCOUNT = {"id": ACT, "name": "Acme Meta", "currency": "USD", "tz": "America/Los_Angeles"}
START, END = dt.date(2026, 10, 5), dt.date(2026, 10, 11)


def test_a_meta_report_has_googles_seven_tables_and_the_campaign_types():
    rep = ms.build_report(ACCOUNT, START, END, fetch=fetch_for(seg_rows()))
    assert [s["key"] for s in rep["segments"]] == ["day", "hour", "age", "gender", "city", "device", "network"]
    assert rep["types"] == ["Meta Sales", "Meta Leads"] and rep["account"]["platform"] == "meta"
    day = rep["segments"][0]["views"]["All"]
    assert [r["label"] for r in day["rows"]] == ["Monday", "Tuesday"] or [r["label"] for r in day["rows"]] == ["Monday", "Tuesday".title()]
    assert day["coverage"] == 1.0 and rep["total"]["cost"] == 80.0
    assert rep["total"]["conversions"] == 4 + 5                       # 2 purchases x 2 sales rows, 5 leads


def test_age_gender_device_and_placement_use_the_labels_google_uses_so_they_add_up():
    rep = ms.build_report(ACCOUNT, START, END, fetch=fetch_for(seg_rows()))
    by = {s["key"]: [r["label"] for r in s["views"]["All"]["rows"]] for s in rep["segments"]}
    assert by["age"] == ["25-34", "Undetermined"] and by["gender"] == ["Female", "Undetermined"]
    assert "Computers" in by["device"] and "Mobile app" in by["device"]
    assert by["hour"] == ["9 AM", "9 PM"] and set(by["network"]) == {"Facebook", "Instagram"}
    assert by["city"] == ["Texas", "Not region-level"] and rep["segments"][4]["label"] == "Location (region)"


def test_a_table_that_fails_is_reported_and_the_rest_still_come_back_but_the_day_table_is_required():
    def fetch(act, dim, params):
        if dim["key"] == "age":
            raise g.AdsError("Meta answered 400: age not available")
        return seg_rows().get(dim["breakdown"] or "day", [])
    rep = ms.build_report(ACCOUNT, START, END, fetch=fetch)
    age = next(s for s in rep["segments"] if s["key"] == "age")
    assert "age not available" in age["error"] and age["views"] == {}
    assert next(s for s in rep["segments"] if s["key"] == "gender")["views"]

    def nothing(act, dim, params):
        raise g.AdsError("Meta answered 500")
    with pytest.raises(g.AdsError):
        ms.build_report(ACCOUNT, START, END, fetch=nothing)


def test_the_insights_rules_read_a_meta_report():
    from tracker import paid_segment_insights as ins
    rep = ms.build_report(ACCOUNT, START, END, fetch=fetch_for(seg_rows()))
    assert isinstance(ins.build(rep), list)


def google_report():
    row = lambda day, label, cost, conv, **k: None
    def mk(sort, label, imp, clk, cost, conv):
        return {"sort": sort, "label": label, "sub": "", "all": {"imp": imp, "clk": clk, "cost": cost, "conv": conv},
                "types": {"Brand": {"imp": imp, "clk": clk, "cost": cost, "conv": conv}}}
    dims = {d["key"]: d for d in seg.DIMENSIONS}
    acct = {"imp": 5000.0, "clk": 100.0, "cost": 100.0, "conv": 10.0}
    segs = {"day": {"Monday": mk(0, "Monday", 5000, 100, 100.0, 10)},
            "hour": {"9 AM": mk(9, "9 AM", 5000, 100, 100.0, 10)},
            "age": {"25-34": mk(1, "25-34", 5000, 100, 100.0, 10)},
            "gender": {"Female": mk(0, "Female", 5000, 100, 100.0, 10)},
            "city": {"geo/1": mk("geo/1", "Austin", 5000, 100, 100.0, 10)},
            "device": {"Computers": mk("DESKTOP", "Computers", 5000, 100, 100.0, 10)},
            "network": {"Search Network": mk("SEARCH", "Search Network", 5000, 100, 100.0, 10)}}
    entries = []
    for dim in seg.DIMENSIONS:
        e = {"key": dim["key"], "label": dim["label"], "error": None, "views": {}}
        for t in [seg.ALL, "Brand"]:
            e["views"][t] = seg._view(segs[dim["key"]], dim, t, acct["cost"])
        entries.append(e)
    return {"account": {"id": "1234567890", "name": "Acme", "currency": "USD", "isGroup": False, "accountCount": 1, "accounts": []},
            "range": {"from": "2026-10-05", "to": "2026-10-11", "days": 7}, "types": ["Brand"], "total": seg.derive(acct, 100.0),
            "segments": entries, "warnings": [], "timeZones": ["America/New_York"], "generatedAt": "2026-10-14T08:00:00+00:00"}


def test_a_google_and_a_meta_report_are_added_row_by_row_where_the_labels_match():
    g_rep = google_report()
    m_rep = ms.build_report(ACCOUNT, START, END, fetch=fetch_for(seg_rows()))
    both = ms.merge_reports(g_rep, m_rep, {"id": "1234567890+" + ACT, "name": "Acme + Acme Meta", "currency": "USD"})
    assert both["total"]["cost"] == 180.0 and both["types"][0] in ("Brand", "Meta Sales") and set(both["types"]) == {"Brand", "Meta Sales", "Meta Leads"}
    by = {s["key"]: s for s in both["segments"]}
    gender = {r["label"]: r for r in by["gender"]["views"]["All"]["rows"]}
    assert gender["Female"]["cost"] == 130.0 and gender["Female"]["conversions"] == 12     # Google's 100 + Meta's sales row 30
    assert set(gender) == {"Female", "Undetermined"}
    device = {r["label"]: r for r in by["device"]["views"]["All"]["rows"]}
    assert device["Computers"]["cost"] == 130.0 and "Mobile app" in device                 # desktop adds up, the rest stay their own rows
    assert by["city"]["note"] and "regions" in by["city"]["note"]
    total = by["day"]["views"]["All"]["total"]
    assert total["cost"] == 180.0 and by["day"]["views"]["All"]["coverage"] == 1.0
    assert both["account"]["platform"] == "both" and both["generatedAt"] == "2026-10-14T08:00:00+00:00"


def test_a_campaign_type_table_is_that_platforms_alone():
    both = ms.merge_reports(google_report(), ms.build_report(ACCOUNT, START, END, fetch=fetch_for(seg_rows())), {"id": "x", "name": "n", "currency": "USD"})
    brand = next(s for s in both["segments"] if s["key"] == "age")["views"]["Brand"]
    assert brand["total"]["cost"] == 100.0 and brand["coverage"] == 1.0
    sales = next(s for s in both["segments"] if s["key"] == "age")["views"]["Meta Sales"]
    assert sales["total"]["cost"] == 30.0


def test_cost_ordered_tables_list_the_biggest_spender_first_after_a_merge():
    both = ms.merge_reports(google_report(), ms.build_report(ACCOUNT, START, END, fetch=fetch_for(seg_rows())), {"id": "x", "name": "n", "currency": "USD"})
    device = [r["label"] for r in next(s for s in both["segments"] if s["key"] == "device")["views"]["All"]["rows"]]
    costs = [r["cost"] for r in next(s for s in both["segments"] if s["key"] == "device")["views"]["All"]["rows"]]
    assert costs == sorted(costs, reverse=True) and device[0] == "Computers"


def test_hours_sort_as_numbers_not_as_text_after_a_merge():
    g_rep = google_report()
    rows = seg_rows()
    rows["hourly_stats_aggregated_by_advertiser_time_zone"].append(
        insight("11", "2026-10-05", spend="1.00", hourly_stats_aggregated_by_advertiser_time_zone="12:00:00 - 12:59:59"))
    both = ms.merge_reports(g_rep, ms.build_report(ACCOUNT, START, END, fetch=fetch_for(rows)), {"id": "x", "name": "n", "currency": "USD"})
    hours = [r["label"] for r in next(s for s in both["segments"] if s["key"] == "hour")["views"]["All"]["rows"]]
    assert hours == ["9 AM", "12 PM", "9 PM"]                  # as text "12 PM" would come first


def test_rows_stay_in_their_natural_order_after_a_merge():
    both = ms.merge_reports(google_report(), ms.build_report(ACCOUNT, START, END, fetch=fetch_for(seg_rows())), {"id": "x", "name": "n", "currency": "USD"})
    hours = [r["label"] for r in next(s for s in both["segments"] if s["key"] == "hour")["views"]["All"]["rows"]]
    assert hours == ["9 AM", "9 PM"]
    ages = [r["label"] for r in next(s for s in both["segments"] if s["key"] == "age")["views"]["All"]["rows"]]
    assert ages == ["25-34", "Undetermined"]


def test_report_for_a_meta_account_is_saved_and_refresh_is_held_to_the_cooldown(monkeypatch):
    fs, calls = FakeStore(), []
    monkeypatch.setattr(mt, "list_accounts", lambda st, **k: META_ACCTS)

    def fetch(act, dim, params):
        calls.append(dim["key"])
        return seg_rows().get(dim["breakdown"] or "day", [])
    kw = dict(store=fs, meta_fetch=fetch)
    a = seg.report_for(ACT, "2026-09-01", "2026-09-30", **kw)
    n = len(calls)
    assert n == 7 and a["account"]["platform"] == "meta"
    seg.clear_cache()
    seg.report_for(ACT, "2026-09-01", "2026-09-30", **kw)
    assert len(calls) == n                                                   # from Postgres, not Meta
    seg.report_for(ACT, "2026-09-01", "2026-09-30", force=True, **kw)
    assert len(calls) == 2 * n
    with pytest.raises(g.RefreshTooSoon):
        seg.report_for(ACT, "2026-09-01", "2026-09-30", force=True, **kw)


def test_an_unknown_or_unconnected_meta_account_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(mt, "list_accounts", lambda st, **k: META_ACCTS)
    with pytest.raises(seg.BadRequest):
        seg.report_for("act_999999999", "2026-09-01", "2026-09-30", store=FakeStore())
    os.environ.pop("META_ADS_ACCESS_TOKEN")
    try:
        with pytest.raises(seg.gads.GadsError) as e:
            seg.report_for(ACT, "2026-09-01", "2026-09-30", store=FakeStore())
    finally:
        os.environ["META_ADS_ACCESS_TOKEN"] = TOKEN
    assert e.value.kind == "not_configured"


def test_a_meta_quota_serves_the_saved_report_with_its_date(monkeypatch):
    fs = FakeStore()
    monkeypatch.setattr(mt, "list_accounts", lambda st, **k: META_ACCTS)
    seg.report_for(ACT, "2026-09-01", "2026-09-30", store=fs, meta_fetch=lambda a, dim, p: seg_rows().get(dim["breakdown"] or "day", []))
    seg.clear_cache()
    g.forget()
    for k in list(fs.at):
        fs.at[k] -= 10 * 86400

    def limited(a, dim, p):
        raise g.OverQuota("Meta is limiting reads")
    out = seg.report_for(ACT, "2026-09-01", "2026-09-30", store=fs, meta_fetch=limited)
    assert out["stale"] and out["total"]["cost"] == 80.0


def test_a_combined_account_reads_each_half_and_adds_them(monkeypatch):
    monkeypatch.setattr(mt, "list_accounts", lambda st, **k: META_ACCTS)
    fs = FakeStore()
    accounts = [{"id": "1234567890", "name": "Acme", "currency": "USD", "timeZone": "America/New_York"}]
    calls = {"g": 0}

    def gsearch(cid, q):
        calls["g"] += 1
        return []
    monkeypatch.setattr(seg, "build_report", lambda account, s, e, **k: google_report())
    rep = seg.report_for("1234567890+" + ACT, "2026-09-01", "2026-09-30", store=fs, accounts_fn=lambda: accounts,
                         meta_fetch=lambda a, dim, p: seg_rows().get(dim["breakdown"] or "day", []))
    assert rep["account"]["platform"] == "both" and rep["total"]["cost"] == 180.0 and rep["account"]["name"] == "Acme + Acme Meta"


def test_refreshing_a_combined_account_whose_one_half_was_just_refreshed_still_answers(monkeypatch):
    monkeypatch.setattr(mt, "list_accounts", lambda st, **k: META_ACCTS)
    accounts = [{"id": "1234567890", "name": "Acme", "currency": "USD", "timeZone": ""}]
    monkeypatch.setattr(seg, "build_report", lambda account, s, e, **k: google_report())
    fs, fetch = FakeStore(), (lambda a, dim, p: seg_rows().get(dim["breakdown"] or "day", []))
    seg.report_for(ACT, "2026-09-01", "2026-09-30", force=True, store=fs, meta_fetch=fetch)      # Meta half refreshed a moment ago
    rep = seg.report_for("1234567890+" + ACT, "2026-09-01", "2026-09-30", force=True, store=fs, accounts_fn=lambda: accounts, meta_fetch=fetch)
    assert rep["account"]["platform"] == "both" and rep["total"]["cost"] == 180.0


def test_a_combined_account_with_one_half_down_shows_the_other_and_says_so(monkeypatch):
    monkeypatch.setattr(mt, "list_accounts", lambda st, **k: META_ACCTS)
    accounts = [{"id": "1234567890", "name": "Acme", "currency": "USD", "timeZone": ""}]
    monkeypatch.setattr(seg, "build_report", lambda account, s, e, **k: google_report())

    def down(a, dim, p):
        raise g.AdsError("Meta answered 500")
    rep = seg.report_for("1234567890+" + ACT, "2026-09-01", "2026-09-30", store=FakeStore(), accounts_fn=lambda: accounts, meta_fetch=down)
    assert rep["account"]["id"] == "1234567890" and any("Meta Ads could not be read" in w for w in rep["warnings"])


def test_a_combined_account_cannot_add_two_currencies(monkeypatch):
    monkeypatch.setattr(mt, "list_accounts", lambda st, **k: [dict(META_ACCTS[0], currency="GBP")])
    accounts = [{"id": "1234567890", "name": "Acme", "currency": "USD", "timeZone": ""}]
    monkeypatch.setattr(seg, "build_report", lambda account, s, e, **k: google_report())
    with pytest.raises(seg.BadRequest) as e:
        seg.report_for("1234567890+" + ACT, "2026-09-01", "2026-09-30", store=FakeStore(), accounts_fn=lambda: accounts,
                       meta_fetch=lambda a, dim, p: seg_rows().get(dim["breakdown"] or "day", [])
                       if False else [insight("11", "2026-10-05", spend="5.00")])
    assert "currenc" in str(e.value)
    for bad in ("1234567890+1234567890", "act_1+act_2", "1234567890+", "+" + ACT, "1234567890+act_1+act_2"):
        with pytest.raises(seg.BadRequest):
            seg.report_for(bad, "2026-09-01", "2026-09-30", store=FakeStore())


# ── Product Analysis ────────────────────────────────────────────────────────

def product_http(log):
    def http(url, headers=None, params=None, timeout=None):
        log.append(params or {})
        p = params or {}
        if url.endswith("/campaigns"):
            return Resp(200, {"data": [{"id": "11", "effective_status": "ACTIVE", "daily_budget": "5000"}]})
        if p.get("breakdowns") == "product_id":
            # the real shape: "<id>, <title>", and only the on-site purchase names carry a number (omni_purchase is 0)
            return Resp(200, {"data": [
                dict(insight("11", d(3), impressions="900", spend="9.00", link="30",
                             actions=[{"action_type": "omni_purchase", "value": "0"}, {"action_type": "onsite_web_purchase", "value": "2"}],
                             values=[{"action_type": "omni_purchase", "value": "0"}, {"action_type": "onsite_web_purchase", "value": "150"}]),
                     product_id="SKU-1, Blue Widget, Large"),
                dict(insight("11", d(3), impressions="100", spend="1.00", link="3", actions=[], values=[]), product_id="sku-2")]})
        rng = json.loads(p["time_range"])
        return Resp(200, {"data": [insight("11", day) for day in days_between(rng["since"], rng["until"])]})
    return http


def test_the_product_id_meta_sends_is_split_into_an_id_and_a_title():
    assert mp.split_id("50585477906741, chevy 350 engine & 4l60e") == ("50585477906741", "chevy 350 engine & 4l60e")
    assert mp.split_id("SKU-1, Blue Widget, Large") == ("SKU-1", "Blue Widget, Large")        # only the first comma splits
    assert mp.split_id("sku-2") == ("sku-2", "") and mp.split_id("") == ("", "") and mp.split_id(None) == ("", "")
    assert mp.split_id(", no id") == (", no id", "")


def test_a_product_row_is_the_google_row_the_parser_already_reads():
    row = mp.to_google_row(dict(insight("11", d(3)), product_id="SKU-9, Nine"))
    assert row["segments"]["productItemId"] == "SKU-9" and row["segments"]["productTitle"] == "Nine"
    assert row["metrics"]["costMicros"] == "10000000" and row["metrics"]["clicks"] == "50"
    assert row["metrics"]["conversions"] == 2 and row["metrics"]["conversionsValue"] == 150.5


def test_a_purchase_is_the_largest_of_its_names_because_meta_zeroes_omni_purchase_by_product():
    row = mp.to_google_row(dict(insight("11", d(3), actions=[{"action_type": "omni_purchase", "value": "0"}, {"action_type": "onsite_web_purchase", "value": "2"}],
                                        values=[{"action_type": "omni_purchase", "value": "0"}, {"action_type": "onsite_web_purchase", "value": "150"}]),
                                product_id="a, A"))
    assert row["metrics"]["conversions"] == 2 and row["metrics"]["conversionsValue"] == 150
    assert mt.purchases([{"action_type": "omni_purchase", "value": "5"}, {"action_type": "purchase", "value": "5"}], [])[0] == 5          # the same event is not added up
    assert mt.purchases([], []) == (0.0, 0.0) and mt.purchases(None, None) == (0.0, 0.0)


def test_meta_products_are_listed_and_say_what_meta_does_not_give():
    s, log = FakeStore(), []
    out = mp.fetch(s, ACT, d(5), d(1), http=product_http(log))
    ids = [p[0] for p in out["products"]]
    assert ids == ["sku-1", "sku-2"] and out["products"][0][1] == "Blue Widget, Large" and out["products"][1][1] == "sku-2"
    assert out["missing"] == [8, 9, 10] and out["outOfStock"] is None and out["series"] is None
    assert out["total"][2] == 10.0 and out["total"][3] == 2                          # spend, purchases (the second product had none)
    assert "only in part" in out["note"]
    assert out["mix"]["campaigns"] == [] and out["mix"]["by"]["sku-1"] == [0, 0, 0, 0, out["products"][0][8]]      # all of a Meta product's spend is Meta's, and bid advice is not Google's


def test_a_meta_product_with_no_spend_has_no_mix():
    rows = [["a", "A", 0, 0, 0, 0, 5, 1, 0.0], ["b", "B", 0, 0, 0, 0, 5, 1, 3.456]]
    assert mp.mix_of(rows) == {"campaigns": [], "by": {"b": [0, 0, 0, 0, 3.46]}, "top": {}}


def test_the_trend_is_the_accounts_sales_campaigns_by_day():
    s, log = FakeStore(), []
    out = mp.fetch(s, ACT, d(5), d(1), http=product_http(log))
    assert len(out["daily"]["days"]) == 5 and out["daily"]["rows"][0][2] == 10.0 and out["daily"]["rows"][0][3] == 2


def test_no_per_product_days_are_asked_for_so_no_call_is_wasted():
    s, log = FakeStore(), []
    mp.fetch(s, ACT, d(5), d(1), http=product_http(log))
    assert not any(p.get("filtering") or p.get("time_increment") and p.get("breakdowns") for p in log)


def test_products_are_saved_and_a_second_look_costs_nothing():
    s, log = FakeStore(), []
    mp.fetch(s, ACT, d(5), d(1), http=product_http(log))
    log.clear()
    mp.fetch(s, ACT, d(5), d(1), http=lambda *a, **k: pytest.fail("a saved range went to Meta"))
    assert log == []


def test_the_comparison_range_is_read_too():
    s, log = FakeStore(), []
    cur, cmp, notes = mp.analysis(s, ACT, (d(5), d(1)), (d(15), d(10)), http=product_http(log))
    assert cur["products"] and cmp["products"] and notes == [] and cur["note"] and cmp["note"]


# ── The probe: which action types an account really reports ─────────────────

def test_all_conversions_count_a_purchase_once_whatever_its_names_and_the_other_events_besides():
    n = mt.numbers(insight("1", d(2), actions=[{"action_type": "omni_purchase", "value": "2"}, {"action_type": "onsite_web_purchase", "value": "2"},
                                                {"action_type": "lead", "value": "3"}, {"action_type": "offsite_conversion.fb_pixel_custom", "value": "4"}],
                           values=[{"action_type": "omni_purchase", "value": "150"}, {"action_type": "onsite_web_purchase", "value": "150"}]))
    assert n[5] == 2 + 3 + 4 and n[6] == 150


def test_an_unknown_objective_counts_a_purchase_under_any_of_its_names():
    n = mt.numbers(insight("1", d(2), objective="SOMETHING_NEW", actions=[{"action_type": "omni_purchase", "value": "0"}, {"action_type": "onsite_web_purchase", "value": "3"}],
                           values=[{"action_type": "onsite_web_purchase", "value": "90"}]))
    assert n[3] == 3 and n[4] == 90


def test_the_probe_sums_every_action_type_per_objective():
    s, log = FakeStore(), []
    rows = [insight("11", d(2), actions=[{"action_type": "lead", "value": "3"}, {"action_type": "offsite_conversion.fb_pixel_custom", "value": "10"}], values=[]),
            insight("12", d(3), actions=[{"action_type": "lead", "value": "4"}, {"action_type": "offsite_conversion.fb_pixel_custom", "value": "5"}], values=[]),
            insight("13", d(3), objective="OUTCOME_SALES")]
    http = lambda url, headers=None, params=None, timeout=None: Resp(200, {"data": rows})
    out = mt.probe(s, "1000000001", http=http)
    sales = out["objectives"]["OUTCOME_SALES"]
    assert out["account"] == ACT and set(out["objectives"]) == {"OUTCOME_SALES"}
    assert sales["actions"]["lead"] == 7 and sales["actions"]["offsite_conversion.fb_pixel_custom"] == 15 and sales["actions"]["omni_purchase"] == 2
    assert list(sales["actions"])[:2] == ["offsite_conversion.fb_pixel_custom", "lead"] and "link_click" not in sales["actions"]      # biggest first, engagement noise left out
    assert sales["spend"] == 30.0 and len(sales["campaigns"]) == 3


def test_the_probe_refuses_anything_that_is_not_an_account_id():
    with pytest.raises(ValueError):
        mt.probe(FakeStore(), "1112223334; drop table")


def test_the_probe_route_is_admin_only_and_a_bad_id_is_a_400(routes, monkeypatch):
    monkeypatch.setattr(mt, "probe", lambda store, act, days=30, breakdown="", **k: {"account": act, "days": days, "breakdown": breakdown})
    assert client_as(STAFF).get("/p2/paid-marketing/api/meta/status?probe=" + ACT).status_code == 403
    r = client_as(ADMIN).get("/p2/paid-marketing/api/meta/status?probe=%s&days=500" % ACT)
    assert r.status_code == 200 and r.get_json() == {"account": ACT, "days": 90, "breakdown": ""}                 # capped
    monkeypatch.undo()
    assert client_as(ADMIN).get("/p2/paid-marketing/api/meta/status?probe=nonsense").status_code == 400


def test_days_and_copies_saved_under_an_older_mapping_are_never_read():
    s, log = FakeStore(), []
    old = "%s@%d" % (ACT, int(mt.MAPPING) - 1)
    s.days[(old, d(5))] = {"m": [], "r": [["11", "Camp 11", "ENABLED", "META_LEADS", 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]}
    s.dayat[(old, d(5))] = __import__("time").time()
    out = mt.campaign_days(s, ACT, d(5), d(5), http=meta_http(log))
    assert any(c["url"].endswith("/insights") for c in log)                # the old day was not trusted: Meta was asked
    assert {c["type"] for c in out["campaigns"]} == {"META_SALES"} and len(out["rows"]) == 2        # the fresh read, not the old day's META_LEADS row
    assert mt.KEY.endswith("meta%s:" % mt.MAPPING) and mt.KEY.startswith(g.CACHE_V)


def test_a_saved_segment_report_carries_the_mapping_version(monkeypatch):
    fs = FakeStore()
    monkeypatch.setattr(mt, "list_accounts", lambda st, **k: META_ACCTS)
    seg.report_for(ACT, "2026-09-01", "2026-09-30", store=fs, meta_fetch=lambda a, dim, p: seg_rows().get(dim["breakdown"] or "day", []))
    assert any(k.startswith(g.CACHE_V + "seg:" + mt.day_key(ACT)) for k in fs.cache)


def test_the_probe_can_show_a_breakdown_and_a_few_raw_rows_and_refuses_an_unknown_one():
    rows = [dict(insight("11", d(2)), product_id="123, Blue Widget"), dict(insight("11", d(2)), product_id="456, Red Widget")]
    seen = []

    def http(url, headers=None, params=None, timeout=None):
        seen.append(params)
        return Resp(200, {"data": rows})
    out = mt.probe(FakeStore(), ACT, breakdown="product_id", http=http)
    assert seen[0]["breakdowns"] == "product_id" and out["rowCount"] == 2 and out["sample"][0]["product_id"] == "123, Blue Widget"
    assert "campaign_name" not in out["sample"][0]
    with pytest.raises(ValueError):
        mt.probe(FakeStore(), ACT, breakdown="anything; drop")
    assert "sample" not in mt.probe(FakeStore(), ACT, http=http)
