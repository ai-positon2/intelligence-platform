"""Paid Marketing Agents: Change History, a Slack line for every Google Ads change made by someone who is not one of the account managers.

Every run reads each Google Ads account's change history (Google's change_event: who changed what, when and through which tool) for the last
two days and keeps only what it has not seen before. A change made by an address on the allowed list (the account managers, saved on the page,
never in this repository) is left alone; any other change is flagged: a person not on the list, a Google Ads script or automated rule run by
someone else, a recommendation Google applied on its own, a change with no address at all. Each account with flagged changes gets one line per
person in a single Slack message: "jane@agency.com (Google Ads Editor): paused 3 keywords, raised the P2_Brand budget from $50 to $70 a day".

  read_changes   Google's rows -> [change]
  summarize      [change] of one person -> the one-line summary
  new_since      which changes were not seen at the last run, and what to remember now
  run            one run over every Google account (the reads and the sender are handed in)

Google's change history has no rows for users given or losing access, manager (MCC) links or conversion actions, so each run also reads those
as they are now and flags what differs from the last run's snapshot (access_changes); Google does not say who made those changes.

Rules it keeps:
  * the first run sets the baseline: what it finds is marked seen and nothing is sent, and the same for an account read for the first time;
  * what was seen is remembered only once Slack took the message, so a message Slack refused is sent again at the next run;
  * an account that could not be read is named on the run and remembered as it was.

Read only: nothing is changed in an ad account.
"""
from __future__ import annotations

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor

AGENT = "changes"
LABEL = "Change History"
PAGE = "/p2/paid-marketing/change-history"
KEY = "settings:change-history"
SEEN_KEY = "changes:seen"
RECENT_KEY = "changes:recent"
RUNS_KEY = "runs:changes"
LOCK_KEY = "lock:changes"
ACCESS_KEY = "changes:access"      # the last snapshot of each account's access, manager links and conversion actions
DEFAULT_CHANNEL = "C0B322RN6SG"      # #adcopyqc
READ_DAYS = 2                        # more than the time between two runs, and Google's time zones either way
LAG_HOURS = 6                        # Google can list a change a little after its time: a change this much older than the newest one seen is still looked at
LIMIT = 1000
RECENT_KEPT = 300
RUNS_KEPT = 30
MAX_ALLOW = 200
SECTIONS_PER_MESSAGE = 40
PHRASES = 6
WORKERS = 4
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_CHANNEL = re.compile(r"^[#@]?[A-Za-z0-9._-]{1,80}$")
_DOMAIN = re.compile(r"^@[^@\s,;<>]+\.[^@\s,;<>]+$")

_FIELDS = ("change_event.resource_name, change_event.change_date_time, change_event.user_email, change_event.client_type, change_event.change_resource_type, "
           "change_event.resource_change_operation, change_event.changed_fields, change_event.old_resource, change_event.new_resource, change_event.campaign")
# Only change_event's own fields: selecting campaign.name or ad_group.name joins on them, and Google then leaves out every change that has no
# campaign or no ad group (campaign settings, asset groups, account assets...). The campaigns' names are read apart (campaign_names).
GAQL = ("SELECT " + _FIELDS + " FROM change_event WHERE change_event.change_date_time >= '%s' AND change_event.change_date_time <= '%s' "
        "ORDER BY change_event.change_date_time DESC LIMIT " + str(LIMIT))
GAQL_CAMPAIGNS = "SELECT campaign.resource_name, campaign.name FROM campaign WHERE campaign.resource_name IN (%s)"

CLIENTS = {"GOOGLE_ADS_WEB_CLIENT": "Google Ads", "GOOGLE_ADS_AUTOMATED_RULE": "an automated rule", "GOOGLE_ADS_SCRIPTS": "a script", "GOOGLE_ADS_BULK_UPLOAD": "a bulk upload",
           "GOOGLE_ADS_EDITOR": "Google Ads Editor", "GOOGLE_ADS_MOBILE_APP": "the Google Ads app", "GOOGLE_ADS_RECOMMENDATIONS": "a recommendation applied",
           "GOOGLE_ADS_API": "the API", "GOOGLE_ADS_RECOMMENDATIONS_SUBSCRIPTION": "a recommendation Google applied automatically", "SEARCH_ADS_360_SYNC": "Search Ads 360",
           "SEARCH_ADS_360_POST": "Search Ads 360", "INTERNAL_TOOL": "a Google tool", "OTHER": "another tool",
           "SNAPSHOT": "someone (Google does not say who)"}

# Google's resource type -> (one, many, the key of the resource in old_resource / new_resource)
NOUNS = {"AD": ("ad", "ads", "ad"), "AD_GROUP_AD": ("ad", "ads", "adGroupAd"), "AD_GROUP": ("ad group", "ad groups", "adGroup"),
         "AD_GROUP_CRITERION": ("keyword", "keywords", "adGroupCriterion"), "CAMPAIGN": ("campaign", "campaigns", "campaign"),
         "CAMPAIGN_BUDGET": ("budget", "budgets", "campaignBudget"), "AD_GROUP_BID_MODIFIER": ("bid adjustment", "bid adjustments", "adGroupBidModifier"),
         "CAMPAIGN_CRITERION": ("campaign targeting setting", "campaign targeting settings", "campaignCriterion"), "FEED": ("feed", "feeds", "feed"),
         "FEED_ITEM": ("feed item", "feed items", "feedItem"), "CAMPAIGN_FEED": ("campaign feed", "campaign feeds", "campaignFeed"),
         "AD_GROUP_FEED": ("ad group feed", "ad group feeds", "adGroupFeed"), "ASSET": ("asset", "assets", "asset"),
         "CUSTOMER_ASSET": ("account asset", "account assets", "customerAsset"), "CAMPAIGN_ASSET": ("campaign asset", "campaign assets", "campaignAsset"),
         "AD_GROUP_ASSET": ("ad group asset", "ad group assets", "adGroupAsset"), "ASSET_SET": ("asset set", "asset sets", "assetSet"),
         "ASSET_SET_ASSET": ("asset set item", "asset set items", "assetSetAsset"), "CAMPAIGN_ASSET_SET": ("campaign asset set", "campaign asset sets", "campaignAssetSet")}
_CRIT_NOUNS = {"KEYWORD": ("keyword", "keywords"), "LOCATION": ("location", "locations"), "PROXIMITY": ("location radius", "location radii"),
               "AD_SCHEDULE": ("ad schedule", "ad schedules"), "DEVICE": ("device setting", "device settings"), "LANGUAGE": ("language", "languages"),
               "AGE_RANGE": ("age range", "age ranges"), "GENDER": ("gender", "genders"), "USER_LIST": ("audience", "audiences"), "PLACEMENT": ("placement", "placements"),
               "KEYWORD_THEME": ("keyword theme", "keyword themes"), "WEBPAGE": ("page target", "page targets"), "USER_INTEREST": ("audience", "audiences"),
               "AUDIENCE": ("audience", "audiences"), "TOPIC": ("topic", "topics"), "INCOME_RANGE": ("income range", "income ranges"), "PARENTAL_STATUS": ("parental status", "parental statuses")}
_STATUS_VERB = {"ENABLED": "enabled", "PAUSED": "paused", "REMOVED": "removed"}
_BID_FIELDS = {"bidding_strategy_type", "target_cpa", "target_roas", "maximize_conversions", "maximize_conversion_value", "manual_cpc", "target_spend", "bidding_strategy",
               "target_impression_share", "manual_cpm", "percent_cpc"}
_URL_FIELDS = {"final_urls", "final_mobile_urls", "tracking_url_template", "final_url_suffix", "url_custom_parameters"}


class BadRequest(ValueError):
    pass


def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _fields(raw):
    """The changed fields as snake_case leaves ("campaign.status" -> "status", "keyword.text" -> "keyword.text")."""
    out = set()
    for f in str(raw or "").replace(",", " ").split():
        f = ".".join(_snake(p) for p in f.strip().split("."))
        if f:
            out.add(f)
            out.add(f.split(".")[-1])
            out.add(f.split(".")[0])
    return out


def _time(v):
    try:
        return dt.datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


# ── Settings (who is allowed, where it goes) ─────────────────────────────────

def clean_settings(data):
    """{allow: [email or @domain], channel} from what the page sent, or BadRequest."""
    if not isinstance(data, dict):
        raise BadRequest("Send the allowed addresses and the channel.")
    raw = data.get("allow")
    if isinstance(raw, str):
        raw = re.split(r"[,;\s]+", raw)
    allow = []
    for e in raw or []:
        e = str(e).strip().lower()
        if not e:
            continue
        if not (_EMAIL.match(e) or _DOMAIN.match(e)):
            raise BadRequest("%s is not an email address (or a whole domain written as @domain.com)." % e[:80])
        if e not in allow:
            allow.append(e)
    if len(allow) > MAX_ALLOW:
        raise BadRequest("At most %d allowed addresses." % MAX_ALLOW)
    channel = str(data.get("channel") or "").strip() or DEFAULT_CHANNEL
    if not _CHANNEL.match(channel):
        raise BadRequest("The Slack channel must be a channel id (C0...) or a #name.")
    return {"allow": allow, "channel": channel}


def settings(store):
    saved = store.state_get(KEY) or {}
    return {"allow": list(saved.get("allow") or []), "channel": saved.get("channel") or DEFAULT_CHANNEL, "savedBy": saved.get("savedBy") or "", "savedAt": saved.get("savedAt") or ""}


def save_settings(store, data, who, now=None):
    clean = clean_settings(data)
    clean.update(savedBy=str(who or ""), savedAt=(now or dt.datetime.now(dt.timezone.utc)).isoformat())
    store.state_set(KEY, clean)
    return settings(store)


def allowed(email, allow):
    e = str(email or "").strip().lower()
    if not e:
        return False
    return any(e == a or (a.startswith("@") and e.endswith(a)) for a in allow)


# ── Google's rows ────────────────────────────────────────────────────────────

def read_changes(rows, names=None):
    """[{id, at, user, client, type, op, fields, campaign, adGroup, old, new}] from Google's change_event rows (newest first, as Google gives them)."""
    out = []
    for r in rows or []:
        e = r.get("changeEvent") or {}
        at = str(e.get("changeDateTime") or "")[:26]
        rid = str(e.get("resourceName") or "")
        if not at:
            continue
        out.append({"id": rid or at, "at": at, "user": str(e.get("userEmail") or "").strip().lower(), "client": str(e.get("clientType") or ""),
                    "type": str(e.get("changeResourceType") or ""), "op": str(e.get("resourceChangeOperation") or ""), "fields": sorted(_fields(e.get("changedFields"))),
                    "campaign": str((names or {}).get(e.get("campaign")) or _get(r, "campaign", "name", default="") or ""), "adGroup": str(_get(r, "adGroup", "name", default="") or ""),
                    "old": e.get("oldResource") or {}, "new": e.get("newResource") or {}})
    return out


def who(change):
    """Who made a change, as the line names them: the address, else the tool."""
    return change["user"] or CLIENTS.get(change["client"]) or "an unknown source"


def via(change):
    """The tool a person used, when it says something ("" for the Google Ads website)."""
    if not change["user"] or change["client"] in ("GOOGLE_ADS_WEB_CLIENT", "SNAPSHOT", ""):
        return ""
    return CLIENTS.get(change["client"], "")


def _noun(change):
    one, many, key = NOUNS.get(change["type"], (change["type"].lower().replace("_", " ") or "setting", (change["type"].lower().replace("_", " ") or "setting") + "s", ""))
    res = _get(change["new"], key) or _get(change["old"], key) or {}
    if change["type"] in ("AD_GROUP_CRITERION", "CAMPAIGN_CRITERION"):
        kind = str(res.get("type") or "")
        if not kind:
            kind = _snake(next((k for k in ("keyword", "location", "proximity", "adSchedule", "device", "language", "ageRange", "gender", "userList", "placement") if k in res), "")).upper()
        if kind in _CRIT_NOUNS:
            one, many = _CRIT_NOUNS[kind]
        elif kind and change["type"] == "AD_GROUP_CRITERION":
            one, many = "ad group targeting setting", "ad group targeting settings"
        if res.get("negative"):
            one, many = "negative " + one, "negative " + many
    return one, many, res


def _verb(change, res):
    """What was done: added, removed, enabled, paused, or a word for what was edited."""
    op, f = change["op"], set(change["fields"])
    if op == "CREATE":
        return "added"
    if op == "REMOVE":
        return "removed"
    status = str(res.get("status") or "")
    if "status" in f and status in _STATUS_VERB:
        return _STATUS_VERB[status]
    if change["type"] == "CAMPAIGN" and f & _BID_FIELDS:
        return "changed the bidding of"
    if f & {"cpc_bid_micros", "cpm_bid_micros", "cpv_bid_micros", "bid_modifier", "target_cpa_micros", "target_roas"}:
        return "changed the bids of"
    if f & _URL_FIELDS:
        return "changed the URLs of"
    if change["type"] == "CAMPAIGN" and f & {"start_date", "end_date"}:
        return "changed the dates of"
    if f & {"name"} and len(f - {"name", "campaign", "ad_group", "ad_group_ad", "resource_name"}) <= 1:
        return "renamed"
    return "edited"


def _budget_phrase(change, currency, money):
    was = _num(_get(change["old"], "campaignBudget", "amountMicros"))
    now = _num(_get(change["new"], "campaignBudget", "amountMicros"))
    if change["op"] != "UPDATE" or was is None or now is None or was == now:
        return None
    name = change["campaign"] or str(_get(change["new"], "campaignBudget", "name", default="") or "")
    return "%s %sbudget from %s to %s a day" % ("raised the" if now > was else "lowered the", (name + " ") if name else "", money(was / 1e6, currency), money(now / 1e6, currency))


def _plain_money(v, currency):
    return ("$%s" if currency == "USD" else "%s " % currency + "%s") % ("{:,.0f}".format(v) if v == int(v) else "{:,.2f}".format(v))


def summarize(changes, currency="USD", money=None):
    """The one-line summary of what one person did in one account: the actions counted ("paused 3 keywords"), a budget with its amounts, the
    campaigns named when few, the oldest to the newest, at most PHRASES actions and "and N more"."""
    money = money or _plain_money
    counts, order, extra, camps = {}, [], [], []
    for c in sorted(changes, key=lambda c: c["at"]):
        if c.get("phrase"):
            if c["phrase"] not in extra:
                extra.append(c["phrase"])
            continue
        if c["campaign"] and c["campaign"] not in camps:
            camps.append(c["campaign"])
        if c["type"] == "CAMPAIGN_BUDGET":
            b = _budget_phrase(c, currency, money)
            if b:
                if b not in extra:
                    extra.append(b)
                continue
        one, many, res = _noun(c)
        verb = _verb(c, res)
        if c["type"] == "CAMPAIGN" and c["campaign"] and verb in ("enabled", "paused", "removed", "added", "changed the bidding of", "changed the dates of", "renamed"):
            p = "%s the campaign %s" % (verb, c["campaign"]) if verb != "renamed" else "renamed a campaign to %s" % c["campaign"]
            if p not in extra:
                extra.append(p)
            continue
        k = (verb, one, many)
        if k not in counts:
            order.append(k)
            counts[k] = {"n": 0, "text": set()}
        counts[k]["n"] += 1
        text = _get(res, "keyword", "text")
        if text:
            counts[k]["text"].add(str(text))
    phrases = list(extra)
    for k in order:
        verb, one, many = k
        n, texts = counts[k]["n"], counts[k]["text"]
        if n == 1 and len(texts) == 1:
            phrases.append('%s the %s "%s"' % (verb, one, next(iter(texts))))
        else:
            phrases.append("%s %s" % (verb, ("a" + ("n" if one[:1] in "aeiou" else "") + " " + one) if n == 1 else "%d %s" % (n, many)))
    more = len(phrases) - PHRASES
    line = ", ".join(phrases[:PHRASES]) + (", and %d more" % more if more > 0 else "")
    rest = [c for c in camps if c not in line]
    if rest:
        line += " (in %s)" % (", ".join(rest[:3]) + (" +%d" % (len(rest) - 3) if len(rest) > 3 else ""))
    total = len(changes)
    line = line[:1].upper() + line[1:]
    return "%s. %s." % (line, "1 change" if total == 1 else "%d changes" % total) if line else ("%d changes" % total)


# ── What is new since the last run ───────────────────────────────────────────

def new_since(changes, seen):
    """(the changes not seen before, what to remember now). `seen` is {last, ids} from the last run, or None for an account read the first time
    (then nothing is new). Remembered: the newest change's time and the ids of the changes within LAG_HOURS of it."""
    newest = max((c["at"] for c in changes), default=(seen or {}).get("last") or "")
    t = _time(newest)
    floor = (t - dt.timedelta(hours=LAG_HOURS)).isoformat(sep=" ") if t else ""
    keep = {"last": newest, "ids": sorted({c["id"] for c in changes if c["at"] >= floor})[:3000]}
    if seen is None:
        return [], keep
    old_ids = set(seen.get("ids") or [])
    last = _time(seen.get("last"))
    cut = (last - dt.timedelta(hours=LAG_HOURS)).isoformat(sep=" ") if last else ""
    fresh = [c for c in changes if c["id"] not in old_ids and c["at"] >= cut]
    return fresh, keep


def flagged(changes, allow):
    return [c for c in changes if not allowed(c["user"], allow)]


def by_person(changes):
    """[(who, via, [change])] the people (or tools) in the order of their first change."""
    groups, order = {}, []
    for c in sorted(changes, key=lambda c: c["at"]):
        k = (who(c), via(c))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(c)
    return [(k[0], k[1], groups[k]) for k in order]


# ── The Slack message ────────────────────────────────────────────────────────

def _mk(v):
    return str(v or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("*", "∗")


def lines_for(account, changes, currency="USD", money=None):
    """[{who, via, line, count, first, last}] for one account's flagged changes, one per person."""
    out = []
    for name, tool, cs in by_person(changes):
        out.append({"who": name, "via": tool, "line": summarize(cs, currency, money), "count": len(cs), "first": cs[0]["at"][:16], "last": cs[-1]["at"][:16],
                    "accountId": account["id"], "accountName": account["name"]})
    return out


def messages(found, link, now):
    """[(text, blocks)] for the accounts with flagged changes ([{account, lines}]): one section per account, its bold name first (the account
    manager's line is added on the way to Slack), split into several messages when there are many."""
    total = sum(x["count"] for f in found for x in f["lines"])
    out = []
    chunks = [found[i:i + SECTIONS_PER_MESSAGE] for i in range(0, len(found), SECTIONS_PER_MESSAGE)] or []
    for n, chunk in enumerate(chunks):
        blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Change History" + (" (%d of %d)" % (n + 1, len(chunks)) if len(chunks) > 1 else "")}},
                  {"type": "context", "elements": [{"type": "mrkdwn", "text": "Google Ads changes by someone other than the account managers: %d in %d account%s, read %s UTC" % (
                      total, len(found), "" if len(found) == 1 else "s", now.strftime("%d %b %H:%M"))}]}]
        for f in chunk:
            rows = ["•  %s%s: %s" % (_mk(x["who"]), (" (%s)" % _mk(x["via"])) if x["via"] else "", _mk(x["line"])) for x in f["lines"]]
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": ("*%s*\n" % _mk(f["account"]["name"]) + "\n".join(rows))[:2900]}})
        blocks.append({"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Open Change History"}, "url": link}]})
        text = "Change History: %d change%s by someone other than the account managers in %s" % (
            total, "" if total == 1 else "s", ", ".join(f["account"]["name"] for f in chunk[:5]) + (" +%d" % (len(chunk) - 5) if len(chunk) > 5 else ""))
        out.append((text, blocks))
    return out


# ── What change history leaves out: access, manager links, conversion actions ──
# Google's change_event has no row for a user given or losing access, a manager (MCC) account linked or unlinked, or a conversion action
# added or edited. Each run reads these as they are now and compares them with the last run's snapshot; what differs is a change. Google does
# not say who made it (except the inviter of a new user), so it is flagged unless the inviter is on the allowed list.

GAQL_MANAGERS = "SELECT customer_manager_link.manager_customer, customer_manager_link.status FROM customer_manager_link"
GAQL_USERS = ("SELECT customer_user_access.email_address, customer_user_access.access_role, customer_user_access.inviter_user_email_address "
              "FROM customer_user_access")
GAQL_CONVERSIONS = ("SELECT conversion_action.id, conversion_action.name, conversion_action.status, conversion_action.primary_for_goal, "
                    "conversion_action.counting_type FROM conversion_action")
_LINK_WORD = {"ACTIVE": "linked", "PENDING": "invited", "INACTIVE": "unlinked", "REFUSED": "refused", "CANCELED": "canceled"}
_ROLE_WORD = {"ADMIN": "admin", "STANDARD": "standard", "READ_ONLY": "read-only", "EMAIL_ONLY": "email-only"}
_COUNT_WORD = {"ONE_PER_CLICK": "one per click", "MANY_PER_CLICK": "every conversion"}
_CONV_STATUS = {"ENABLED": "enabled it", "HIDDEN": "hid it"}


def _cid(resource):
    d = re.sub(r"\D", "", str(resource or "").rsplit("/", 1)[-1])
    return "%s-%s-%s" % (d[:3], d[3:6], d[6:]) if len(d) == 10 else d


def _word(table, v):
    return table.get(v) or str(v or "").lower().replace("_", " ")


def read_access(account_id, search):
    """{managers: {id: status}, users: {email: {role, inviter}}, conversions: {id: {name, status, primary, counting}}} as they are now. A part
    Google refuses (no permission, say) is None and is not compared; a quota refusal stops the read."""
    def part(query, fn):
        try:
            return fn(search(account_id, query) or [])
        except Exception as e:                                                      # noqa: BLE001
            if getattr(e, "kind", "") == "quota":
                raise
            return None

    def managers(rows):
        return {_cid(_get(r, "customerManagerLink", "managerCustomer")): str(_get(r, "customerManagerLink", "status", default=""))
                for r in rows if _get(r, "customerManagerLink", "managerCustomer")}

    def users(rows):
        return {str(_get(r, "customerUserAccess", "emailAddress")).strip().lower(): {
            "role": str(_get(r, "customerUserAccess", "accessRole", default="")),
            "inviter": str(_get(r, "customerUserAccess", "inviterUserEmailAddress", default="")).strip().lower()}
            for r in rows if _get(r, "customerUserAccess", "emailAddress")}

    def conversions(rows):
        return {str(_get(r, "conversionAction", "id")): {
            "name": str(_get(r, "conversionAction", "name", default="")), "status": str(_get(r, "conversionAction", "status", default="")),
            "primary": bool(_get(r, "conversionAction", "primaryForGoal", default=False)),
            "counting": str(_get(r, "conversionAction", "countingType", default=""))}
            for r in rows if _get(r, "conversionAction", "id")}
    return {"managers": part(GAQL_MANAGERS, managers), "users": part(GAQL_USERS, users), "conversions": part(GAQL_CONVERSIONS, conversions)}


def _snap_change(at, key, phrase, user=""):
    return {"id": "snapshot:" + key, "at": at, "user": user, "client": "SNAPSHOT", "type": "SNAPSHOT", "op": "", "fields": [], "campaign": "", "adGroup": "",
            "old": {}, "new": {}, "phrase": phrase}


def access_changes(old, new, at):
    """[change] for what differs between two snapshots (read_access). A part missing from either one is not compared."""
    old, new, out = old or {}, new or {}, []
    a, b = old.get("managers"), new.get("managers")
    if a is not None and b is not None:
        for m in sorted(set(a) | set(b)):
            was, now = a.get(m), b.get(m)
            if was == now:
                continue
            if was is None:
                p = "%s the manager account %s" % ({"PENDING": "invited", "ACTIVE": "linked"}[now], m) if now in ("PENDING", "ACTIVE") \
                    else "added a link to the manager account %s (%s)" % (m, _word(_LINK_WORD, now))
            elif now is None:
                p = "removed the link to the manager account %s" % m
            else:
                p = "changed the link to the manager account %s from %s to %s" % (m, _word(_LINK_WORD, was), _word(_LINK_WORD, now))
            out.append(_snap_change(at, "manager:%s:%s:%s" % (m, was, now), p))
    a, b = old.get("users"), new.get("users")
    if a is not None and b is not None:
        for e in sorted(set(a) | set(b)):
            was, now = a.get(e), b.get(e)
            if was is None:
                out.append(_snap_change(at, "user:%s:+%s" % (e, now["role"]), "gave %s %s access" % (e, _word(_ROLE_WORD, now["role"])), now.get("inviter", "")))
            elif now is None:
                out.append(_snap_change(at, "user:%s:-" % e, "removed %s's access" % e))
            elif was["role"] != now["role"]:
                out.append(_snap_change(at, "user:%s:%s>%s" % (e, was["role"], now["role"]), "changed %s's access from %s to %s" % (
                    e, _word(_ROLE_WORD, was["role"]), _word(_ROLE_WORD, now["role"]))))
    a, b = old.get("conversions"), new.get("conversions")
    if a is not None and b is not None:
        for cid in sorted(set(a) | set(b)):
            was, now = a.get(cid), b.get(cid)
            if was == now:
                continue
            name = '"%s"' % (now or was)["name"]
            if was is None:
                p = "added the conversion action %s" % name
            elif now is None or (now["status"] == "REMOVED" and was["status"] != "REMOVED"):
                p = "removed the conversion action %s" % name
            else:
                bits = []
                if was["name"] != now["name"]:
                    bits.append('renamed it from "%s"' % was["name"])
                if was["status"] != now["status"]:
                    bits.append(_CONV_STATUS.get(now["status"], "set its status to " + _word({}, now["status"])))
                if was["primary"] != now["primary"]:
                    bits.append("made it %s" % ("primary" if now["primary"] else "secondary"))
                if was["counting"] != now["counting"]:
                    bits.append("set it to count %s" % _word(_COUNT_WORD, now["counting"]))
                if not bits:
                    continue
                p = "changed the conversion action %s (%s)" % (name, ", ".join(bits))
            out.append(_snap_change(at, "conversion:%s:%s" % (cid, p), p))
    return out


def _merge_access(old, new):
    """The snapshot to keep: each part as read now, or as it was when it could not be read."""
    old, new = old or {}, new or {}
    return {k: (new.get(k) if new.get(k) is not None else old.get(k)) for k in ("managers", "users", "conversions")}


# ── One run ──────────────────────────────────────────────────────────────────

def campaign_names(account_id, search, rows):
    """{campaign resource name: name} for the campaigns Google's rows name; {} when Google refuses (the lines then go without them)."""
    ids = sorted({str(_get(r, "changeEvent", "campaign", default="")) for r in rows or []} - {""})
    out = {}
    for k in range(0, len(ids), 200):
        try:
            got = search(account_id, GAQL_CAMPAIGNS % ", ".join("'%s'" % x for x in ids[k:k + 200] if "'" not in x))
        except Exception as e:                                                      # noqa: BLE001
            if getattr(e, "kind", "") == "quota":
                raise
            return out
        for r in got or []:
            c = r.get("campaign") or {}
            if c.get("resourceName"):
                out[c["resourceName"]] = str(c.get("name") or "")
    return out


def read_between(account_id, search, since, until):
    """[change] of one account between two times (the account's time zone, 'YYYY-MM-DD HH:MM:SS'), with the campaigns named."""
    rows = search(account_id, GAQL % (since, until)) or []
    return read_changes(rows, campaign_names(account_id, search, rows))


def read_account(account_id, search, now):
    """[change] of one account for the last READ_DAYS days."""
    since = (now - dt.timedelta(days=READ_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    until = (now + dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    return read_between(account_id, search, since, until)


def run(store, *, accounts, deliver, search_fn, link, trigger, now=None, currency_of=None, money=None, workers=WORKERS):
    """One run over the Google accounts in `accounts` ([{id, name, platform}]). Returns the run record (kept under RUNS_KEY)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    conf = settings(store)
    seen_all = store.state_get(SEEN_KEY) or {}
    access_all = store.state_get(ACCESS_KEY) or {}
    baseline = not seen_all
    google = [a for a in accounts if (a.get("platform") or "google") == "google"]
    currency_of = currency_of or (lambda aid: "USD")

    def one(a):
        try:
            return a, read_account(a["id"], search_fn, now), read_access(a["id"], search_fn), None
        except Exception as e:                                                      # noqa: BLE001 (one account failing never stops the rest)
            return a, None, None, ("%s" % e)[:240] or type(e).__name__
    unread, found, keep, access_keep = [], [], {}, {}
    at = now.strftime("%Y-%m-%d %H:%M:%S")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        done = list(pool.map(one, google))
    for a, changes, access, err in done:
        if changes is None:
            unread.append({"accountId": a["id"], "name": a["name"], "error": err})
            continue
        fresh, keep[a["id"]] = new_since(changes, None if baseline else seen_all.get(a["id"]))
        before = access_all.get(a["id"])
        if before is not None and not baseline:                                     # an account's first snapshot is its baseline
            fresh = fresh + access_changes(before, access, at)
        access_keep[a["id"]] = _merge_access(before, access)
        bad = flagged(fresh, conf["allow"])
        if bad:
            found.append({"account": a, "lines": lines_for(a, bad, currency_of(a["id"]), money)})
    found.sort(key=lambda f: f["account"]["name"].lower())
    record = {"at": now.isoformat(), "trigger": trigger, "accounts": len(google), "read": len(google) - len(unread), "unread": unread[:30], "baseline": baseline,
              "flagged": sum(x["count"] for f in found for x in f["lines"]), "accountsFlagged": len(found), "sent": 0, "error": ""}
    remember = True
    if baseline:
        record["flagged"] = record["accountsFlagged"] = 0                          # the baseline: everything found is marked seen, nothing is sent
    elif found and not conf["allow"]:
        record["error"] = "No allowed addresses are saved yet, so nothing was sent (every change would be flagged). Save the account managers' addresses first."
        remember = False                                                            # kept unseen, so they are judged once the list is saved
    elif found:
        for text, blocks in messages(found, link, now):
            try:
                deliver.send_slack(conf["channel"], text, blocks)
                record["sent"] += 1
            except Exception as e:                                                  # noqa: BLE001 (nothing is remembered, so the next run sends it again)
                remember = False
                record["error"] = ("Slack: %s" % e)[:300]
                break
    if remember:
        merged = dict(seen_all)
        merged.update(keep)
        store.state_set(SEEN_KEY, merged)
        snaps = dict(access_all)
        snaps.update(access_keep)
        store.state_set(ACCESS_KEY, snaps)
        if found and not baseline:
            recent = [dict(x, at=now.isoformat()) for f in found for x in f["lines"]] + list(store.state_get(RECENT_KEY) or [])
            store.state_set(RECENT_KEY, recent[:RECENT_KEPT])
    runs = [record] + list(store.state_get(RUNS_KEY) or [])
    store.state_set(RUNS_KEY, runs[:RUNS_KEPT])
    return record


HISTORY_DAYS = 29                    # Google keeps 30 days of change history


def clean_day(day, today):
    """The date asked for (a date), or BadRequest: one of the last HISTORY_DAYS days, today included."""
    try:
        d = dt.date.fromisoformat(str(day or "").strip())
    except ValueError:
        raise BadRequest("Pick a date (YYYY-MM-DD).")
    if d > today or d < today - dt.timedelta(days=HISTORY_DAYS):
        raise BadRequest("Pick a date in the last %d days: Google keeps 30 days of change history." % (HISTORY_DAYS + 1))
    return d


def day_report(store, *, accounts, search_fn, day, account=None, everyone=False, currency_of=None, money=None, workers=WORKERS):
    """What a run would flag for one day ({date, accounts: [{id, name, total, allowed, lines}], unread, read}), read for the page only: nothing is sent,
    nothing is remembered. `account` keeps one account id; `everyone` lists the allowed people's changes too (each line says whether it is allowed)."""
    allow = settings(store)["allow"]
    currency_of = currency_of or (lambda aid: "USD")
    google = [a for a in accounts if (a.get("platform") or "google") == "google" and (not account or a["id"] == account)]
    if account and not google:
        raise BadRequest("That is not one of the Google Ads accounts the platform reads.")
    since, until = "%s 00:00:00" % day.isoformat(), "%s 23:59:59" % day.isoformat()

    def one(a):
        try:
            return a, read_between(a["id"], search_fn, since, until), None
        except Exception as e:                                                      # noqa: BLE001
            return a, None, ("%s" % e)[:240] or type(e).__name__
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        done = list(pool.map(one, google))
    out, unread = [], []
    for a, changes, err in done:
        if changes is None:
            unread.append({"accountId": a["id"], "name": a["name"], "error": err})
            continue
        bad = flagged(changes, allow)
        shown = changes if everyone else bad
        lines = lines_for(a, shown, currency_of(a["id"]), money)
        for x in lines:
            x["allowed"] = allowed(x["who"], allow)
        if lines or account:
            out.append({"id": a["id"], "name": a["name"], "total": len(changes), "allowed": len(changes) - len(bad), "flagged": len(bad), "lines": lines})
    out.sort(key=lambda r: (-r["flagged"], r["name"].lower()))
    return {"date": day.isoformat(), "accounts": out, "unread": unread, "read": len(google) - len(unread), "of": len(google), "noAllowList": not allow,
            "flagged": sum(r["flagged"] for r in out)}


def state_for(store):
    """What the page shows: the settings, the last runs and the changes flagged lately."""
    return {"settings": settings(store), "runs": (store.state_get(RUNS_KEY) or [])[:10], "recent": (store.state_get(RECENT_KEY) or [])[:100],
            "baseline": bool(store.state_get(SEEN_KEY)), "running": bool((store.state_get(LOCK_KEY) or {}).get("locked")), "channel": DEFAULT_CHANNEL}
