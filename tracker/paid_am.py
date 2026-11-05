"""Paid Marketing Agents: tag the account manager in the Slack alerts (no network; the app reads the rows and sends).

Who manages an account comes from the Pacing Desk's allocations (client | account | AM, pasted from the team's Budget Tracker), the newest month
that has rows. Who that person is on Slack is saved once per AM name on the Alert Hub page (a Slack member id; an email is turned into one when
saved). The alerts are not changed where they are written: just before a message goes to Slack, the accounts it names in bold are looked up and
one line is added, "Account managers: @Divith (OIA, Riccobene) · @Siddesh (Monster Transmission)", so the people are notified.

  owners      {normalised account name: AM name} from allocation rows
  am_of       the AM of an account name as an alert writes it ("Gentle Dental", "Gentle Dental (GD & Affiliates)", "D&amp;J")
  clean_people / people_list   the saved AM -> Slack member id table
  tag         a message's Slack text and blocks with the account managers' line added (unchanged when nobody is found)
"""
from __future__ import annotations

import html
import re

KEY = "settings:am-slack"
MEMBER_RE = re.compile(r"^[UW][A-Z0-9]{6,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_PEOPLE = 60
_BOLD = re.compile(r"\*([^*\n]{2,160})\*")


class BadRequest(ValueError):
    pass


def norm(name):
    return re.sub(r"[^a-z0-9]+", "", html.unescape(str(name or "")).lower().replace("&", "and"))


SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})|^([A-Za-z0-9_-]{30,})$")


def sheet_id(link):
    """The spreadsheet id in a Google Sheets link (or a bare id), or BadRequest."""
    m = SHEET_ID_RE.search(str(link or "").strip())
    if not m:
        raise BadRequest("Paste the Budget Tracker's link (https://docs.google.com/spreadsheets/d/...).")
    return m.group(1) or m.group(2)


def parse_dpr(values):
    """Rows {client, account, am} from the Budget Tracker's DPR tab (its first row is the header: Client, Account, ..., Date, ..., AM). The newest
    month's row of an account wins, so a manager who changed is the one tagged."""
    if not values:
        return []
    head = [str(h or "").strip().lower() for h in values[0]]
    col = {k: head.index(k) for k in ("client", "account", "am", "date") if k in head}
    if "account" not in col or "am" not in col:
        raise BadRequest("The DPR tab has no Account and AM columns in its first row.")
    get = lambda r, k: str(r[col[k]]).strip() if k in col and col[k] < len(r) else ""  # noqa: E731
    rows = [{"client": get(r, "client"), "account": get(r, "account"), "am": get(r, "am"), "date": get(r, "date")} for r in values[1:]]
    return sorted((r for r in rows if r["account"] and r["am"]), key=lambda r: r["date"], reverse=True)


MAX_PASTED = 2000


def parse_paste(text):
    """Rows {client, account, am} from cells copied out of the DPR tab (tab-separated, the header row included), or BadRequest."""
    lines = [l for l in str(text or "").replace("\r\n", "\n").split("\n") if l.strip()]
    if len(lines) > MAX_PASTED:
        raise BadRequest("At most %d rows can be pasted." % MAX_PASTED)
    rows = parse_dpr([l.split("\t") for l in lines])
    if not rows:
        raise BadRequest("No row with both an Account and an AM was found. Copy the DPR tab's cells with the header row.")
    return [{"client": r["client"], "account": r["account"], "am": r["am"], "date": r["date"]} for r in rows]


def owners(rows):
    """{normalised account name: AM} and {normalised client name: AM when the client has one AM} from allocation rows."""
    acc, by_client = {}, {}
    for r in rows or []:
        am = " ".join(str(r.get("am") or "").split())
        if not am:
            continue
        if norm(r.get("account")):
            acc.setdefault(norm(r["account"]), am)
        if norm(r.get("client")):
            by_client.setdefault(norm(r["client"]), set()).add(am)
    clients = {c: next(iter(s)) for c, s in by_client.items() if len(s) == 1}
    return {"accounts": acc, "clients": clients}


def am_of(name, own):
    """The AM of an account as an alert names it, or ''. Exact name first, then the name without a trailing "(client)", then the client in that
    bracket, then one allocation name that contains the other (a Meta account "Soteri Skin" and the row "Soteri Skin Facebook")."""
    raw = html.unescape(str(name or "")).strip()
    tries = [raw]
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", raw)
    if m:
        tries.append(m.group(1))
    for t in tries:
        n = norm(t)
        if n and n in own["accounts"]:
            return own["accounts"][n]
        if n and n in own["clients"]:
            return own["clients"][n]
    if m and norm(m.group(2)) in own["clients"]:
        return own["clients"][norm(m.group(2))]
    n = norm(tries[-1])
    if len(n) >= 4:
        near = {am for k, am in own["accounts"].items() if len(k) >= 4 and (n in k or k in n)}
        if len(near) == 1:
            return near.pop()
    return ""


def clean_people(data):
    """{AM name: Slack member id or email} from what the page sent ({people: [{name, slack}]}), or BadRequest. An empty slack drops the name."""
    out = {}
    for p in (data or {}).get("people") or []:
        name = " ".join(str((p or {}).get("name") or "").split())[:60]
        slack = str((p or {}).get("slack") or "").strip()
        if not name or not slack:
            continue
        if slack.startswith("<@") and slack.endswith(">"):
            slack = slack[2:-1].split("|")[0]
        if not (MEMBER_RE.match(slack.upper()) or EMAIL_RE.match(slack)):
            raise BadRequest("%s: give a Slack member id (it starts with U, from their Slack profile, More, Copy member ID) or their email." % name)
        out[name] = slack.upper() if MEMBER_RE.match(slack.upper()) else slack.lower()
    if len(out) > MAX_PEOPLE:
        raise BadRequest("At most %d account managers." % MAX_PEOPLE)
    return out


def people_list(saved, names):
    """Rows for the page: every AM named in the allocations, then any saved name no longer in them."""
    seen = []
    for n in list(names) + sorted(saved):
        if n and n not in seen:
            seen.append(n)
    return [{"name": n, "slack": saved.get(n, ""), "inAllocations": n in names} for n in seen]


def _mention(am, people):
    sid = people.get(am) or next((v for k, v in people.items() if k.lower() == am.lower()), "")
    return "<@%s>" % sid if sid and MEMBER_RE.match(sid) else am


def _named(blocks):
    """The account names a message's blocks show in bold, in order."""
    out = []
    for b in blocks or []:
        t = ((b or {}).get("text") or {}).get("text") if (b or {}).get("type") == "section" else None
        for m in _BOLD.finditer(t or ""):
            if m.group(1) not in out:
                out.append(m.group(1))
    return out


def tag(text, blocks, own, people):
    """(text, blocks) with a line naming the account managers of the accounts in the message. Unchanged when no account has an AM."""
    by_am = {}
    for name in _named(blocks):
        am = am_of(name, own)
        if am:
            label = re.sub(r"\s*\([^()]*\)\s*$", "", html.unescape(name)).strip()
            if label not in by_am.setdefault(am, []):
                by_am[am].append(label)
    if not by_am:
        return text, blocks
    parts = ["%s (%s)" % (_mention(am, people), ", ".join(names[:6]) + (" +%d" % (len(names) - 6) if len(names) > 6 else "")) for am, names in sorted(by_am.items())]
    line = "Account managers: " + "  ·  ".join(parts)
    block = {"type": "context", "elements": [{"type": "mrkdwn", "text": line[:2900]}]}
    blocks = list(blocks or [])
    at = next((i for i in range(len(blocks) - 1, -1, -1) if blocks[i].get("type") == "actions"), len(blocks))   # above the button
    blocks.insert(at, block)
    mentions = " ".join(_mention(am, people) for am in sorted(by_am) if _mention(am, people).startswith("<@"))
    return ((text or "") + ("  " + mentions if mentions else "")), blocks[:50]
