"""Stand-ins for Google Ads and Meta that answer Tracking Data Health's reads in the platforms' own row shapes (REST camelCase for Google, Graph lists for Meta). The days are made
from the date of each call, so a test can break "yesterday" on any run. Every account here is made up."""
import datetime as dt

from tracker import paid_gads_segments as gads


class Google:
    """Accounts by id, each {clicks, conv, over: {n: (clicks, conv)}, actions: [{id, name, primary, perDay, stopFrom}], tagging, lists: [{id, name, type, size}], fail: set of parts}.
    n is days from yesterday (0 yesterday, -1 the day before); stopFrom is the n from which an action records nothing."""

    def __init__(self, accounts, now_fn):
        self.accounts, self.now_fn, self.calls = accounts, now_fn, []

    def _days(self):
        today = self.now_fn().date()
        y = today - dt.timedelta(days=1)
        return [(n, y + dt.timedelta(days=n)) for n in range(-36, 1)]

    def __call__(self, cid, q):
        self.calls.append((cid, q))
        a = self.accounts[cid]
        if a.get("down"):
            raise gads.GadsError(a["down"], "refused")
        part = ("actions" if "conversion_action" in q else "tagging" if "auto_tagging" in q else "lists" if "user_list" in q else "days")
        if part in a.get("fail", ()):
            raise gads.GadsError("permission", "not allowed")
        if part == "tagging":
            return [{"customer": {"resourceName": "customers/%s" % cid, "autoTaggingEnabled": a.get("tagging", True)}}]
        if part == "lists":
            return [{"userList": {"resourceName": "customers/%s/userLists/%s" % (cid, l["id"]), "id": l["id"], "name": l["name"], "type": l.get("type", "REMARKETING"),
                                  "membershipStatus": "OPEN", "sizeForSearch": str(l["size"])}} for l in a.get("lists", [])]
        if q.startswith("SELECT conversion_action."):
            return [{"conversionAction": {"resourceName": "customers/%s/conversionActions/%s" % (cid, x["id"]), "id": x["id"], "name": x["name"], "status": "ENABLED",
                                          "category": "SUBMIT_LEAD_FORM", "type": "WEBPAGE", "primaryForGoal": x.get("primary", True)}} for x in a.get("actions", [])]
        if part == "actions":
            rows = []
            for x in a.get("actions", []):
                for n, day in self._days():
                    if x.get("stopFrom") is not None and n >= x["stopFrom"]:
                        continue
                    rows.append({"customer": {"resourceName": "customers/%s" % cid}, "metrics": {"conversions": x["perDay"]},
                                 "segments": {"date": day.isoformat(), "conversionAction": "customers/%s/conversionActions/%s" % (cid, x["id"]), "conversionActionName": x["name"]}})
            return rows
        rows = []
        for n, day in self._days():
            c, v = a.get("over", {}).get(n, (a.get("clicks", 100), a.get("conv", 4)))
            rows.append({"customer": {"resourceName": "customers/%s" % cid, "timeZone": "UTC"}, "metrics": {"clicks": str(c), "costMicros": str(int(c * 1_500_000)), "conversions": v},
                         "segments": {"date": day.isoformat()}})
        return rows


class Meta:
    """One Meta ad account: {clicks, conv, over, pixels: [{id, name, hoursAgo}], audiences: [{id, name, subtype, size}], fail: set of parts}."""

    def __init__(self, account, now_fn):
        self.a, self.now_fn, self.calls = account, now_fn, []

    def __call__(self, path, params):
        self.calls.append(path)
        part = path.rsplit("/", 1)[-1]
        if part in self.a.get("fail", ()):
            raise RuntimeError("(#200) Permissions error")
        now = self.now_fn()
        if part == "adspixels":
            return [{"id": p["id"], "name": p["name"], "last_fired_time": (now - dt.timedelta(hours=p["hoursAgo"])).strftime("%Y-%m-%dT%H:%M:%S+0000")} for p in self.a.get("pixels", [])]
        if part == "customaudiences":
            return [{"id": x["id"], "name": x["name"], "subtype": x.get("subtype", "WEBSITE"), "approximate_count_lower_bound": x["size"]} for x in self.a.get("audiences", [])]
        y = now.date() - dt.timedelta(days=1)
        rows = []
        for n in range(-36, 1):
            c, v = self.a.get("over", {}).get(n, (self.a.get("clicks", 100), self.a.get("conv", 4)))
            rows.append({"campaign_id": "77", "campaign_name": "Leads", "objective": "OUTCOME_LEADS", "date_start": (y + dt.timedelta(days=n)).isoformat(), "spend": str(c * 1.2),
                         "impressions": str(c * 40), "inline_link_clicks": str(c), "actions": [{"action_type": "lead", "value": str(v)}] if v else []})
        return rows
