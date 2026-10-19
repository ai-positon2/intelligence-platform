"""Paid Marketing Agents: Unknown Domains Alert, the logic.

An ad that sends people to a domain nobody approved is either an ad launched under the wrong account or the first sign of an ad account that has
been taken over. This is the part that decides, with no database, no network and no clock (so it is all tested):

  * what a web address's host is (and when it has none to judge: a ValueTrack placeholder, a Facebook or Instagram address),
  * what a person means by a domain they type, and whether a host is covered by an entry ("example.com" covers every subdomain of it),
  * who an entry is for (one account, a client, which is an MCC group, or everyone),
  * whether an address is fine, unknown (in nobody's list), or in the list but for other accounts only ("wrong account"),
  * whether an unknown domain is a look-alike of an approved one (how phishing domains are named),
  * which findings are new and how many in one account at once mean a takeover,
  * the words of the alert, as an email and as a Slack message.

A finding is one address in one place in one account (an ad, an asset group, a sitelink, a tracking template). Its fingerprint includes the host,
so a page change on an approved domain is nothing and a change of domain is a new finding.
"""
from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import urlsplit

EVERYONE = "*"
BURST = 3                         # this many distinct new unknown domains in one account in one scan is read as a takeover
MAX_ENTRY = 253

# Where people land that is not the advertiser's own site: never a finding.
IGNORED = ("facebook.com", "fb.com", "fb.me", "fb.watch", "m.me", "messenger.com", "instagram.com", "wa.me", "whatsapp.com",
           "play.google.com", "itunes.apple.com", "apps.apple.com")

# Two-part public suffixes, so "example.co.uk" is one domain and not "co.uk". An unlisted one falls back to the last two labels.
SUFFIX2 = frozenset("""co.uk org.uk ac.uk gov.uk me.uk ltd.uk plc.uk net.uk com.au net.au org.au edu.au gov.au co.nz org.nz net.nz co.in net.in org.in
firm.in gen.in ind.in ac.in edu.in gov.in co.za org.za com.br com.mx com.ar com.co com.sg com.my com.hk com.cn com.tr com.sa co.jp ne.jp or.jp co.kr
com.ph com.pk com.ng co.ke com.eg com.ua co.il co.id com.vn com.tw co.th com.pe com.ve com.bd com.np com.lk""".split())

_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_GLYPHS = (("rn", "m"), ("vv", "w"), ("cl", "d"), ("0", "o"), ("1", "l"), ("3", "e"), ("5", "s"), ("4", "a"))


# ── Addresses ───────────────────────────────────────────────────────────────

def host_of(url):
    """The lower-case host of an address, or None when there is none to judge: nothing in it, a ValueTrack placeholder where the host would be
    ("{lpurl}?x=1"), or a host that is not a domain name."""
    s = str(url or "").strip()
    if not s or s.startswith("{") or s.lower().startswith(("tel:", "mailto:", "sms:")):
        return None
    if "://" not in s:
        s = "http://" + s.lstrip("/")
    try:
        host = (urlsplit(s).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host or "{" in host or "." not in host or re.fullmatch(r"[\d.]+", host) or ":" in host:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return host if all(_LABEL.match(l) for l in host.split(".")) else None


def host_of_template(template):
    """The host a tracking template sends people to *before* the ad's own address, or None when it starts with the address ("{lpurl}?gclid=...")."""
    t = str(template or "").strip()
    if not t or t.startswith("{"):
        return None
    return host_of(_PLACEHOLDER.sub("x", t.split("?")[0]) if "://" in t else None)


def is_ignored(host):
    return any(host == h or host.endswith("." + h) for h in IGNORED)


def registrable(host):
    """The domain a person would call the site: "shop.example.co.uk" is "example.co.uk"."""
    labels = str(host or "").split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in SUFFIX2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else str(host or "")


def clean_domain(text):
    """What a person typed (a domain, a host or a whole address) as an entry, or None when it is not one. A leading "www." is dropped: an entry for
    example.com already covers it."""
    s = str(text or "").strip().lower()
    if not s or len(s) > MAX_ENTRY or " " in s:
        return None
    host = host_of(s)
    if host is None:
        return None
    return host[4:] if host.startswith("www.") and host.count(".") >= 2 else host


def covers(entry, host):
    return host == entry or host.endswith("." + entry)


# ── Who an entry is for ─────────────────────────────────────────────────────

def clean_scope(scope, accounts=(), groups=()):
    """The scope of an entry as stored ("*", an MCC group id, a Google customer id or a Meta account id), or None when it is none of these or is not one
    the platform knows."""
    s = str(scope or "").strip()
    if s in ("", EVERYONE, "everyone", "all"):
        return EVERYONE
    ids = {a["id"] for a in accounts} | {g["id"] for g in groups}
    digits = re.sub(r"\D", "", s)
    if s.lower().startswith("act_"):
        s = "act_" + digits
    elif re.fullmatch(r"[\d-]{10,12}", s):
        s = digits
    return s if s in ids else None


def applies(scope, account):
    """True when an entry with this scope is for this account (`account` has id, platform and group)."""
    return scope == EVERYONE or scope == account["id"] or (account.get("group") and scope == account["group"])


def owner_names(scopes, accounts, groups):
    """Who a list of scopes names, in words."""
    by = {a["id"]: a["name"] for a in accounts}
    by.update({g["id"]: g["name"] for g in groups})
    names = []
    for s in scopes:
        n = "everyone" if s == EVERYONE else by.get(s) or s
        if n not in names:
            names.append(n)
    return names


# ── Look-alikes ─────────────────────────────────────────────────────────────

def _distance(a, b, limit=2):
    """The edit distance of two short strings, or limit + 1 when it is more than limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _fold(s):
    for a, b in _GLYPHS:
        s = s.replace(a, b)
    return s


def lookalike(host, known):
    """The approved domain this host is dressed up as (a letter or two off, or look-alike characters swapped in), or None. Short names are not
    compared: two letters off on a six-letter name is a different company far more often than a fake."""
    mine = registrable(host)
    name = mine.split(".")[0]
    best = None
    for k in {registrable(x) for x in known}:
        if k == mine:
            continue
        kn = k.split(".")[0]
        if len(kn) < 6 or len(name) < 6:
            continue
        same_tld_family = k.split(".")[-1] == mine.split(".")[-1] or kn == name
        d = _distance(name, kn)
        if (d <= 2 and same_tld_family) or (name != kn and _fold(name) == _fold(kn)) or (kn == name):
            if best is None or d < best[0]:
                best = (d, k)
    return best[1] if best else None


# ── Judging an address ──────────────────────────────────────────────────────

def judge(host, account, entries):
    """('ok', None) when an entry for this account covers the host; ('wrong_account', [scopes]) when entries cover it but none is for this account;
    ('unknown', None) when no entry covers it at all. `entries` is a list of {domain, scope}."""
    mine, others = False, []
    for e in entries:
        if covers(e["domain"], host):
            if applies(e["scope"], account):
                mine = True
            elif e["scope"] not in others:
                others.append(e["scope"])
    if mine:
        return "ok", None
    return ("wrong_account", others) if others else ("unknown", None)


def fingerprint(s):
    key = "|".join([s["platform"], s["accountId"], s["where"], str(s.get("objectId") or ""), s["host"]])
    return hashlib.sha1(key.encode()).hexdigest()[:24]


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2}


def assess(sightings, entries, accounts, groups=()):
    """The findings in a list of sightings. A sighting is {platform, accountId, accountName, where, objectId, objectName, campaign, adGroup, url, host,
    redirect}; `accounts` is [{id, name, platform, group}]. Returns one finding per fingerprint, worst first."""
    by_id = {a["id"]: a for a in accounts}
    known = [e["domain"] for e in entries]
    out = {}
    for s in sightings:
        host = s.get("host")
        account = by_id.get(s["accountId"])
        if not host or not account or is_ignored(host):
            continue
        verdict, owners = judge(host, account, entries)
        if verdict == "ok":
            continue
        like = lookalike(host, known) if verdict == "unknown" else None
        redirect = bool(s.get("redirect"))
        severity = "critical" if verdict == "unknown" and (like or redirect) else "high" if verdict == "unknown" else "medium"
        names = owner_names(owners or [], accounts, groups)
        if verdict == "unknown":
            why = "Not in the domain database for any account."
            if like:
                why += " It looks like %s, which is." % like
        else:
            why = "In the domain database, but for %s only, not for this account." % ", ".join(names)
        if redirect:
            why = "The tracking template sends people through this host before the ad's own address. " + why
        f = {"fingerprint": fingerprint(s), "kind": verdict, "severity": severity, "platform": s["platform"], "accountId": s["accountId"],
             "accountName": s["accountName"], "where": s["where"], "objectId": str(s.get("objectId") or ""), "objectName": s.get("objectName") or "",
             "campaign": s.get("campaign") or "", "adGroup": s.get("adGroup") or "", "url": s["url"], "host": host, "domain": registrable(host),
             "redirect": redirect, "lookalike": like, "owners": names, "why": why}
        out.setdefault(f["fingerprint"], f)
    return sorted(out.values(), key=lambda f: (SEVERITY_ORDER[f["severity"]], f["accountName"].lower(), f["domain"], f["objectName"]))


def takeovers(new_findings):
    """The accounts where this many distinct unknown domains turned up in one scan: [{accountId, accountName, platform, domains}]."""
    by = {}
    for f in new_findings:
        if f["kind"] == "unknown":
            a = by.setdefault(f["accountId"], {"accountId": f["accountId"], "accountName": f["accountName"], "platform": f["platform"], "domains": []})
            if f["domain"] not in a["domains"]:
                a["domains"].append(f["domain"])
    return sorted((a for a in by.values() if len(a["domains"]) >= BURST), key=lambda a: -len(a["domains"]))


# ── The baseline ────────────────────────────────────────────────────────────

def in_use(sightings):
    """{account id: {domain: count}} of the domains the ads of each account point at today, for the baseline."""
    out = {}
    for s in sightings:
        h = s.get("host")
        if h and not is_ignored(h):
            d = out.setdefault(s["accountId"], {})
            d[registrable(h)] = d.get(registrable(h), 0) + 1
    return out


# ── The alert ───────────────────────────────────────────────────────────────

PLATFORM = {"google": "Google Ads", "meta": "Meta Ads"}
SHOWN = 12


def _line(f):
    where = "%s%s" % (f["where"], (": " + f["objectName"]) if f["objectName"] else "")
    place = " / ".join(x for x in (f["campaign"], f["adGroup"]) if x)
    return "%s (%s) · %s%s · %s · %s" % (f["accountName"], PLATFORM.get(f["platform"], f["platform"]), where, (" · " + place) if place else "", f["url"], f["why"])


def digest(findings, link, takeover=()):
    """{subject, text, html, slack_text, slack_blocks} for the new findings of a scan. `link` is the page's address."""
    n = len(findings)
    accounts = len({f["accountId"] for f in findings})
    unknown = sum(1 for f in findings if f["kind"] == "unknown")
    lead = ("POSSIBLE ACCOUNT TAKEOVER: " if takeover else "")
    subject = "%s%d ad%s point%s to a domain that is not approved (%d account%s)" % (
        lead, n, "" if n == 1 else "s", "s" if n == 1 else "", accounts, "" if accounts == 1 else "s")
    shown = findings[:SHOWN]
    more = n - len(shown)
    notes = []
    for t in takeover:
        notes.append("%s has %d new unknown domains at once (%s). That pattern is how a taken-over account looks. Check who has access and what was launched, and consider pausing the account's ads." % (
            t["accountName"], len(t["domains"]), ", ".join(t["domains"][:5])))
    text = "\n".join(notes + ([""] if notes else []) + ["%d new finding%s: %d unknown domain%s, %d for another account." % (
        n, "" if n == 1 else "s", unknown, "" if unknown == 1 else "s", n - unknown), ""] + ["- " + _line(f) for f in shown]
        + (["...and %d more on the page." % more] if more else []) + ["", "Review, approve or dismiss them: " + link])
    e = html.escape
    rows = "".join("<li><b>%s</b> (%s)<br>%s%s<br><code>%s</code><br>%s</li>" % (
        e(f["accountName"]), e(PLATFORM.get(f["platform"], f["platform"])), e(f["where"] + ((": " + f["objectName"]) if f["objectName"] else "")),
        e((" · " + " / ".join(x for x in (f["campaign"], f["adGroup"]) if x)) if (f["campaign"] or f["adGroup"]) else ""), e(f["url"]), e(f["why"])) for f in shown)
    html_body = ("<div style=\"font-family:Arial,sans-serif;font-size:14px;line-height:1.5\">%s<p>%d new finding%s.</p><ul>%s</ul>%s<p><a href=\"%s\">Review, approve or dismiss them</a></p></div>"
                 % ("".join("<p style=\"color:#b91c1c\"><b>%s</b></p>" % e(x) for x in notes), n, "" if n == 1 else "s", rows,
                    ("<p>...and %d more on the page.</p>" % more) if more else "", e(link)))
    slack_lines = ["*%s*" % subject] + ["> " + x for x in notes]
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": subject[:150]}}]
    for x in notes:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": ":rotating_light: " + x}})
    for f in shown:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*%s* (%s)\n%s%s\n`%s`\n%s" % (
            f["accountName"], PLATFORM.get(f["platform"], f["platform"]), f["where"] + ((": " + f["objectName"]) if f["objectName"] else ""),
            (" · " + " / ".join(x for x in (f["campaign"], f["adGroup"]) if x)) if (f["campaign"] or f["adGroup"]) else "", f["url"], f["why"])}})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": ("...and %d more. " % more if more else "") + "<%s|Review, approve or dismiss them>" % link}})
    return {"subject": subject, "text": text, "html": html_body, "slack_text": "\n".join(slack_lines), "slack_blocks": blocks[:49]}
