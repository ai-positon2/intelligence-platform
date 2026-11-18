"""Paid Marketing Agents: the daily to-do list for the account managers, posted to one Slack channel with a check box on every item.

Each weekday morning (.github/workflows/paid-daily-todo.yml) the Client Feedback Agent is asked, per account manager, what needs doing today on
their accounts. The answers go into ONE message in the channel: a section per manager, a check box per item. Ticking a box (Slack's
interactivity, the address in app.py) marks the item done; a done item is never put in a later list. An item nobody has ticked is carried into
the next day's list, so nothing is dropped by being ignored. Unticking a box puts the item back.

  KEY_ITEMS     {item id: {am, text, status "open"|"done", firstOn, doneBy, doneAt}}  (the id is made from the manager and the item's words)
  KEY_MESSAGES  {message ts: {channel, date, sections: [{am, ids}]}}                    (what each posted message shows, so a tick can rebuild it)

No network here: the agent's answers, the Slack calls and the clock are passed in.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

DEFAULT_CHANNEL = "C05SWCJHMDF"
KEY_ITEMS = "dailytodo:items"
KEY_MESSAGES = "dailytodo:messages"
KEY_RUNS = "dailytodo:runs"
MAX_PER_AM = 8                     # Slack allows ten options in one check box group
MAX_ITEM = 150                     # an option shows 75 characters as its text and 75 more as its description
MESSAGES_KEPT = 60
RUNS_KEPT = 30
WORKERS = 4
SIGNATURE_AGE = 300


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def item_id(am, text):
    return hashlib.sha1((norm(am) + "|" + norm(text)).encode("utf-8")).hexdigest()[:16]


def clean_item(text):
    t = re.sub(r"\s+", " ", str(text or "")).strip().lstrip("-*• ").strip()
    return (t[:MAX_ITEM - 1].rstrip() + "…") if len(t) > MAX_ITEM else t


def parse_items(answer):
    """The items in the agent's answer: a JSON object {"items": [...]} (found anywhere in the text), or a bulleted list as a fallback."""
    text = str(answer or "")
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            raw = json.loads(m.group(0)).get("items") or []
            return [t for t in (clean_item(x) for x in raw if isinstance(x, str)) if t]
        except (ValueError, AttributeError):
            pass
    return [t for t in (clean_item(x) for x in re.findall(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", text, re.M)) if t]


def groups_from_rows(rows):
    """{account manager: [account names]} from the allocation rows (newest first; an account belongs to the first manager listed for it)."""
    seen, out = set(), {}
    for r in rows:
        am = " ".join(str(r.get("am") or "").split())
        acct = " ".join(str(r.get("account") or "").split())
        if not am or not acct or norm(acct) in seen:
            continue
        seen.add(norm(acct))
        out.setdefault(am, []).append(acct)
    return {am: sorted(a, key=str.lower) for am, a in sorted(out.items(), key=lambda kv: kv[0].lower())}


def prompt_for(am, accounts, done_texts, today):
    done = ("\n\nThese were already done, do not list them again or anything meaning the same:\n- " + "\n- ".join(done_texts[:40])) if done_texts else ""
    return (
        "Today is %s. You are preparing the daily to-do list for the account manager %s, who manages these accounts: %s.\n\n"
        "Search Slack, the meeting notes, email and the budget snapshot for the last three days and list the things this person needs to DO today "
        "on those accounts: follow-ups promised on calls, client requests waiting for an answer, changes agreed but not made yet, budget or pacing "
        "problems that need action. Only include something a source actually says; never invent or pad. Fewer than %d items is fine, and an empty "
        "list is right when nothing needs doing.\n\n"
        "Each item is one short sentence that starts with the account name and says what to do. Reply with ONLY this JSON and nothing else: "
        '{"items": ["...", "..."]}%s' % (today, am, "; ".join(accounts), MAX_PER_AM + 1, done)
    )


# ── State ───────────────────────────────────────────────────────────────────

def _items(store):
    return dict(store.state_get(KEY_ITEMS) or {})


def done_texts(items, am):
    return [v["text"] for v in items.values() if v["am"] == am and v["status"] == "done"][-40:]


def plan(items, am, fresh, today):
    """The ids that go in `am`'s list today: items still open (oldest first), then the new ones that were never done or listed. Adds the new ones
    to `items`. Returns the ids, at most MAX_PER_AM."""
    keep = [k for k, v in items.items() if v["am"] == am and v["status"] == "open"]
    for text in fresh:
        k = item_id(am, text)
        if k in items:
            continue                                                                # done already, or already open (and in `keep`)
        items[k] = {"am": am, "text": text, "status": "open", "firstOn": today, "doneBy": "", "doneAt": ""}
        keep.append(k)
    keep.sort(key=lambda k: items[k]["firstOn"])
    return keep[:MAX_PER_AM]


# ── The message ─────────────────────────────────────────────────────────────

def _option(items, k):
    t = items[k]["text"]
    o = {"text": {"type": "mrkdwn", "text": t[:75]}, "value": k}
    if len(t) > 75:
        o["description"] = {"type": "mrkdwn", "text": t[75:150]}
    return o


def _mention(am, people):
    sid = people.get(am) or next((v for k, v in people.items() if k.lower() == am.lower()), "")
    return "<@%s>" % sid if sid and re.match(r"^[UW][A-Z0-9]{6,20}$", sid) else "*%s*" % am


def build_blocks(sections, items, people, date_label):
    """Slack blocks for a message's sections ([{am, ids}]): a header, then per manager a name line and a check box group (done items ticked)."""
    n = sum(len(s["ids"]) for s in sections)
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Today's to-do list"}},
              {"type": "context", "elements": [{"type": "mrkdwn", "text": "%s · %d item%s · tick an item when it is done and it will not be listed again" % (
                  date_label, n, "" if n == 1 else "s")}]}]
    for i, s in enumerate(sections):
        ids = [k for k in s["ids"] if k in items]
        if not ids:
            continue
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _mention(s["am"], people)}})
        box = {"type": "checkboxes", "action_id": "todo_check", "options": [_option(items, k) for k in ids]}
        ticked = [_option(items, k) for k in ids if items[k]["status"] == "done"]
        if ticked:
            box["initial_options"] = ticked
        blocks.append({"type": "actions", "block_id": "todo_%d" % i, "elements": [box]})
    return blocks


def summary_text(sections):
    return "Today's to-do list: %d item%s" % (sum(len(s["ids"]) for s in sections), "" if sum(len(s["ids"]) for s in sections) == 1 else "s")


# ── The daily run ───────────────────────────────────────────────────────────

def run(store, deliver, *, groups, people, ask, channel=DEFAULT_CHANNEL, now=None):
    """Ask for each manager's items, post one message. `ask(am, accounts, done_texts)` returns the agent's answer text. Returns the run record."""
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    items = _items(store)
    record = {"at": now.isoformat(), "managers": len(groups), "items": 0, "posted": False, "errors": []}

    def one(am):
        try:
            return am, parse_items(ask(am, groups[am], done_texts(items, am))), None
        except Exception as e:                                                      # noqa: BLE001 (one manager's failure never stops the rest)
            return am, [], "%s: %s" % (am, str(e)[:160])
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        answers = list(ex.map(one, list(groups)))
    sections = []
    for am, fresh, err in answers:
        if err:
            record["errors"].append(err)
        ids = plan(items, am, fresh, today)
        if ids:
            sections.append({"am": am, "ids": ids})
    record["items"] = sum(len(s["ids"]) for s in sections)
    if sections:
        label = now.strftime("%a %d %b %Y")
        try:
            ts = deliver.post_message(channel, summary_text(sections), build_blocks(sections, items, people, label))
        except Exception as e:                                                      # noqa: BLE001 (nothing is remembered, so the next run lists them again)
            record["errors"].append("Slack: %s" % str(e)[:200])
        else:
            record["posted"] = True
            store.state_set(KEY_ITEMS, items)
            msgs = dict(store.state_get(KEY_MESSAGES) or {})
            msgs[ts] = {"channel": channel, "date": today, "label": label, "sections": sections}
            store.state_set(KEY_MESSAGES, dict(sorted(msgs.items())[-MESSAGES_KEPT:]))
    runs = [record] + list(store.state_get(KEY_RUNS) or [])
    store.state_set(KEY_RUNS, runs[:RUNS_KEPT])
    return record


# ── A tick in Slack ─────────────────────────────────────────────────────────

class BadSignature(ValueError):
    pass


def verify_signature(secret, timestamp, body, signature, now=None):
    """Slack's request signature (v0), or BadSignature. `body` is the raw request body."""
    if not secret:
        raise BadSignature("SLACK_SIGNING_SECRET is not set.")
    try:
        age = abs((now if now is not None else time.time()) - int(timestamp))
    except (TypeError, ValueError):
        raise BadSignature("No timestamp.")
    if age > SIGNATURE_AGE:
        raise BadSignature("Stale request.")
    want = "v0=" + hmac.new(secret.encode("utf-8"), ("v0:%s:" % timestamp).encode("utf-8") + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, str(signature or "")):
        raise BadSignature("Bad signature.")


def handle_action(store, deliver, payload, people, now=None):
    """Apply a check box tick or untick from Slack's interactivity payload and redraw the message. Returns the number of items changed."""
    now = now or dt.datetime.now(dt.timezone.utc)
    act = next((a for a in payload.get("actions") or [] if a.get("action_id") == "todo_check"), None)
    ts = (payload.get("message") or {}).get("ts") or (payload.get("container") or {}).get("message_ts")
    msgs = store.state_get(KEY_MESSAGES) or {}
    msg = msgs.get(ts)
    if not act or not msg:
        return 0
    try:
        index = int(str(act.get("block_id") or "").split("_")[-1])
        shown = msg["sections"][index]["ids"]
    except (ValueError, IndexError):
        return 0
    ticked = {o.get("value") for o in act.get("selected_options") or []}
    items = _items(store)
    who = (payload.get("user") or {}).get("id") or ""
    changed = 0
    for k in shown:
        if k not in items:
            continue
        want = "done" if k in ticked else "open"
        if items[k]["status"] != want:
            items[k].update(status=want, doneBy=who if want == "done" else "", doneAt=now.isoformat() if want == "done" else "")
            changed += 1
    if changed:
        store.state_set(KEY_ITEMS, items)
        deliver.update_message(msg["channel"], ts, summary_text(msg["sections"]), build_blocks(msg["sections"], items, people, msg["label"]))
    return changed
