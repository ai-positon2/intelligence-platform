"""Answers shaped like Meta's Graph API (from its documented fields), for the Account Audit's Meta tests."""
import json

from tracker import paid_audit_meta_read as m
from tracker import paid_meta as meta

ACT = "act_1234567890"
NOW = "2026-10-17T12:00:00+00:00"


def account(**kw):
    a = {"name": "Acme Dental", "account_status": 1, "disable_reason": 0, "spend_cap": "0", "amount_spent": "250000", "currency": "USD",
         "timezone_name": "America/New_York", "id": ACT}
    a.update(kw)
    return a


def account_extra(**kw):
    a = {"is_prepay_account": False, "offsite_pixels_tos_accepted": True, "business": {"id": "55", "name": "Acme Holdings"}, "id": ACT}
    a.update(kw)
    return a


def pixel(pid="111", name="Acme pixel", last="2026-10-17T10:30:00+0000", unavailable=False):
    return {"id": pid, "name": name, "last_fired_time": last, "is_unavailable": unavailable, "creation_time": "2024-02-01T10:00:00+0000"}


def pixel_extra(pid="111", matching=True, cookie="FIRST_PARTY_COOKIE_ENABLED"):
    return {"id": pid, "enable_automatic_matching": matching, "first_party_cookie_status": cookie}


def stats(counts):
    """Pixel stats rows: counts is {value: count}, spread over two hourly rows."""
    half = {k: v // 2 for k, v in counts.items()}
    rest = {k: v - v // 2 for k, v in counts.items()}
    row = lambda d: {"start_time": "2026-10-16T07:00:00+0000", "aggregation": "event", "data": [{"value": k, "count": v} for k, v in d.items()]}
    return [row(half), row(rest)]


def campaign(cid="1", name="Leads | Implants", objective="OUTCOME_LEADS", status="ACTIVE", issues=(), special=(), bid="LOWEST_COST_WITHOUT_CAP"):
    return {"id": cid, "name": name, "objective": objective, "effective_status": status, "buying_type": "AUCTION", "special_ad_categories": list(special),
            "bid_strategy": bid, "issues_info": [{"level": "CAMPAIGN", "error_summary": i} for i in issues]}


def insight(cid="1", name="Leads | Implants", objective="OUTCOME_LEADS", spend=500.0, impressions=40000, clicks=800, leads=25, frequency=1.8, reach=22000):
    return {"campaign_id": cid, "campaign_name": name, "objective": objective, "spend": str(spend), "impressions": str(impressions), "inline_link_clicks": str(clicks),
            "reach": str(reach), "frequency": str(frequency), "actions": [{"action_type": "lead", "value": str(leads)}] if leads is not None else [],
            "date_start": "2026-09-17", "date_stop": "2026-10-16"}


def adset(sid="10", name="Women 30-55", cid="1", goal="LEAD_GENERATION", learning="SUCCESS", issues=(), conversions=60):
    return {"id": sid, "name": name, "campaign_id": cid, "effective_status": "ACTIVE", "optimization_goal": goal, "billing_event": "IMPRESSIONS",
            "learning_stage_info": {"status": learning, "conversions": conversions}, "destination_type": "WEBSITE",
            "issues_info": [{"level": "AD_SET", "error_summary": i} for i in issues]}


def ad(aid="100", name="Ad A", sid="10", cid="1", status="ACTIVE", feedback=None, issues=()):
    a = {"id": aid, "name": name, "adset_id": sid, "campaign_id": cid, "effective_status": status,
         "issues_info": [{"level": "AD", "error_summary": i} for i in issues]}
    if feedback:
        a["ad_review_feedback"] = {"global": feedback}
    return a


class Graph:
    """A Graph API double: `get` and `pages` answer by path and requested fields; a part named in `fail` raises meta.AdsError, one in
    `quota` raises meta.OverQuota. `.asked` records (path, fields) for every request."""

    def __init__(self, answers=None, fail=(), quota=()):
        self.answers = {
            "account": account(), "accountExtra": {k: v for k, v in account_extra().items() if k != "business"},
            "accountBusiness": {"business": {"id": "55", "name": "Acme Holdings"}, "id": ACT}, "pixels": [pixel()], "pixelExtra": [pixel_extra()],
            "stats": stats({"PageView": 5000, "Lead": 40}), "sources": stats({"pixel": 5000, "server": 30}),
            "campaigns": [campaign()], "insights": [insight()], "adSets": [adset()], "ads": [ad("100", "Ad A"), ad("101", "Ad B")]}
        self.answers.update(answers or {})
        self.fail, self.quota, self.asked = set(fail), set(quota), []

    def key(self, path, params):
        fields = (params or {}).get("fields", "")
        if path.endswith("/stats"):
            return "sources" if params.get("aggregation") == "event_detection_method" else "stats"
        if path.endswith("/adspixels"):
            return "pixelExtra" if fields == m.PIXEL_EXTRA else "pixels"
        if path.endswith("/campaigns"):
            return "campaigns"
        if path.endswith("/adsets"):
            return "adSets"
        if path.endswith("/ads"):
            return "ads"
        if path.endswith("/insights"):
            return "insights"
        return {m.ACCOUNT_EXTRA: "accountExtra", m.ACCOUNT_BUSINESS: "accountBusiness"}.get(fields, "account")

    def call(self, path, params):
        k = self.key(path, params)
        self.asked.append((path, k))
        if k in self.quota:
            raise meta.OverQuota("Meta is rate limiting this app right now. Try again in a minute.")
        if k in self.fail:
            raise meta.AdsError("Meta answered 400: (#100) nonexisting field")
        return self.answers[k]

    def get(self, path, params):
        return self.call(path, params)

    def pages(self, path, params):
        return self.call(path, params)


def a_read(answers=None, fail=(), quota=(), **over):
    """A healthy Meta read, built through the real parser; `answers` replaces what a part returns, `fail` names parts that raise."""
    g = Graph(answers, fail, quota)
    out = m.read_account(ACT, get=g.get, pages=g.pages, now=m.dt.datetime.fromisoformat(NOW))
    out.update(over)
    return out
