"""The daily to-do list: items, the message, a tick in Slack, the routes. No network."""
import datetime as dt
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

import app as app_module
from tracker import paid_dailytodo as t

NOW = dt.datetime(2026, 11, 18, 3, 30, tzinfo=dt.timezone.utc)


class Store:
    def __init__(self):
        self.d = {}

    def state_get(self, k):
        return self.d.get(k)

    def state_set(self, k, v):
        self.d[k] = v

    def claim(self, k, s):
        return True

    def release(self, k):
        pass


class Deliver:
    n = 0

    def __init__(self, fail=False):
        self.posted, self.updated, self.fail = [], [], fail

    def post_message(self, channel, text, blocks):
        if self.fail:
            raise RuntimeError("not_in_channel")
        self.posted.append((channel, text, blocks))
        Deliver.n += 1
        return "1700000000.%06d" % Deliver.n

    def update_message(self, channel, ts, text, blocks):
        self.updated.append((channel, ts, text, blocks))


GROUPS = {"Siddesh": ["Monster Transmission", "D&J"], "Divith": ["OIA"]}
PEOPLE = {"Siddesh": "U0SIDDESH1"}
TWO = '{"items": ["D&J: reply to the invoice", "Monster: add stock feed"]}'


def asker(answers):
    return lambda am, accounts, done: answers.get(am, '{"items": []}')


def tick(ts, values, block="todo_0"):
    return {"actions": [{"action_id": "todo_check", "block_id": block, "selected_options": [{"value": v} for v in values]}], "message": {"ts": ts}, "user": {"id": "U0SIDDESH1"}}


def test_items_are_read_from_the_agents_json_or_a_bullet_list():
    assert t.parse_items('Here you go: {"items": ["OIA: send the report", " ", 5]}') == ["OIA: send the report"]
    assert t.parse_items("- OIA: send the report\n2. D&J: call back") == ["OIA: send the report", "D&J: call back"]
    assert t.parse_items("nothing") == []


def test_the_managers_come_from_the_allocations_newest_row_first():
    rows = [{"account": "OIA", "am": "Divith"}, {"account": "oia", "am": "Old"}, {"account": "D&J", "am": " Siddesh "}, {"account": "", "am": "x"}]
    assert t.groups_from_rows(rows) == {"Divith": ["OIA"], "Siddesh": ["D&J"]}


def test_one_message_with_a_check_box_per_item_and_a_name_line_per_manager():
    s, d = Store(), Deliver()
    rec = t.run(s, d, groups=GROUPS, people=PEOPLE, ask=asker({"Siddesh": TWO}), now=NOW)
    assert rec["posted"] and rec["items"] == 2 and rec["errors"] == [] and len(d.posted) == 1
    channel, text, blocks = d.posted[0]
    assert channel == t.DEFAULT_CHANNEL == "C05SWCJHMDF" and text == "Today's to-do list: 2 items"
    assert blocks[2]["text"]["text"] == "<@U0SIDDESH1>" and blocks[3]["elements"][0]["type"] == "checkboxes" and len(blocks[3]["elements"][0]["options"]) == 2
    assert "initial_options" not in blocks[3]["elements"][0] and len(blocks) == 4          # Divith has nothing today, so no section for them


def test_a_ticked_item_is_done_redrawn_ticked_and_never_listed_again_while_an_open_one_is_carried_over():
    s, d = Store(), Deliver()
    t.run(s, d, groups=GROUPS, people=PEOPLE, ask=asker({"Siddesh": TWO}), now=NOW)
    ts = next(iter(s.state_get(t.KEY_MESSAGES)))
    opts = d.posted[0][2][3]["elements"][0]["options"]
    first = opts[0]["value"]
    assert t.handle_action(s, d, tick(ts, [first]), PEOPLE, now=NOW) == 1
    redrawn = d.updated[0][3][3]["elements"][0]
    assert [o["value"] for o in redrawn["initial_options"]] == [first]
    assert s.state_get(t.KEY_ITEMS)[first]["status"] == "done" and s.state_get(t.KEY_ITEMS)[first]["doneBy"] == "U0SIDDESH1"
    assert t.handle_action(s, d, tick(ts, [first]), PEOPLE, now=NOW) == 0                    # the same tick again changes nothing
    d2 = Deliver()
    again = '{"items": ["D&J: reply to the invoice", "D&J: send the weekly report"]}'
    t.run(s, d2, groups=GROUPS, people=PEOPLE, ask=asker({"Siddesh": again}), now=NOW + dt.timedelta(days=1))
    texts = [o["text"]["text"] for o in d2.posted[0][2][3]["elements"][0]["options"]]
    assert texts == ["Monster: add stock feed", "D&J: send the weekly report"]                # the done one is gone, the open one carried, the new one added
    notes = [o["description"]["text"] for o in d2.posted[0][2][3]["elements"][0]["options"]]
    assert notes == ["assigned 18 Nov (yesterday)", "assigned 19 Nov (today)"]                   # the carried item shows when it was first assigned
    assert t.handle_action(s, d, tick(ts, []), PEOPLE, now=NOW) == 1 and s.state_get(t.KEY_ITEMS)[first]["status"] == "open"   # unticking puts it back


def test_a_failed_post_is_not_remembered_and_one_managers_failure_does_not_stop_the_rest():
    s, d = Store(), Deliver(fail=True)
    rec = t.run(s, d, groups=GROUPS, people=PEOPLE, ask=asker({"Siddesh": '{"items": ["D&J: x"]}'}), now=NOW)
    assert not rec["posted"] and rec["errors"][0].startswith("Slack: not_in_channel") and s.state_get(t.KEY_ITEMS) is None

    def ask(am, accounts, done):
        if am == "Divith":
            raise RuntimeError("model down")
        return '{"items": ["D&J: x"]}'
    rec = t.run(Store(), Deliver(), groups=GROUPS, people=PEOPLE, ask=ask, now=NOW)
    assert rec["posted"] and rec["items"] == 1 and rec["errors"] == ["Divith: model down"]


def test_long_items_use_the_description_and_a_manager_gets_at_most_eight():
    s, d = Store(), Deliver()
    long = "OIA: " + "word " * 40
    many = json.dumps({"items": ["OIA: item %d" % i for i in range(12)]})
    t.run(s, d, groups={"Divith": ["OIA"]}, people={}, ask=asker({"Divith": many}), now=NOW)
    assert len(d.posted[0][2][3]["elements"][0]["options"]) == 8 and d.posted[0][2][2]["text"]["text"] == "*Divith*"
    items = {}
    ids = t.plan(items, "Divith", [t.clean_item(long)], "2026-11-18")
    o = t._option(items, ids[0])
    assert o["text"]["text"].endswith("word") and len(o["text"]["text"]) <= 75 and len(o["description"]["text"]) <= 75 and len(items[ids[0]]["text"]) <= t.MAX_ITEM
    words = ("McHale Landscaping: Add the new ad text from last week into the search campaigns before today's weekly client call, as promised")
    split = t._option({"x": {"text": words}}, "x")
    assert (split["text"]["text"] + " " + split["description"]["text"]).startswith(words[:len(split["text"]["text"])]) and len(split["description"]["text"]) <= 75


def test_slacks_signature_is_checked():
    body, ts = b"payload=%7B%7D", str(int(time.time()))
    sig = "v0=" + hmac.new(b"sek", ("v0:%s:" % ts).encode() + body, hashlib.sha256).hexdigest()
    t.verify_signature("sek", ts, body, sig)
    for args in (("sek", ts, body, "v0=bad"), ("", ts, body, sig), ("sek", str(int(ts) - 4000), body, sig), ("sek", "x", body, sig)):
        with pytest.raises(t.BadSignature):
            t.verify_signature(*args)


@pytest.fixture
def store(monkeypatch):
    s = Store()
    monkeypatch.setattr(app_module, "_mon_store", lambda: s)
    return s


def test_the_slack_address_refuses_an_unsigned_call_and_applies_a_signed_tick(monkeypatch, store):
    from tracker import market_radar_deliver as real
    d = Deliver()
    monkeypatch.setattr(real, "update_message", d.update_message)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "sek")
    monkeypatch.setattr(app_module, "_am_people", lambda: PEOPLE)
    c = app_module.app.test_client()
    assert c.post("/p2/paid-marketing/api/dailytodo/slack", data={"payload": "{}"}).status_code == 403
    t.run(store, Deliver(), groups=GROUPS, people=PEOPLE, ask=asker({"Siddesh": '{"items": ["D&J: reply"]}'}), now=NOW)
    k = next(iter(store.state_get(t.KEY_ITEMS)))
    body = urlencode({"payload": json.dumps(tick(next(iter(store.state_get(t.KEY_MESSAGES))), [k]))}).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(b"sek", ("v0:%s:" % ts).encode() + body, hashlib.sha256).hexdigest()
    r = c.post("/p2/paid-marketing/api/dailytodo/slack", data=body, content_type="application/x-www-form-urlencoded",
               headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})
    assert r.status_code == 200 and store.state_get(t.KEY_ITEMS)[k]["status"] == "done" and len(d.updated) == 1


def test_the_scheduled_address_needs_the_monitors_secret(monkeypatch, store):
    ran = []
    monkeypatch.setattr(app_module, "_mon_spawn", lambda fn: ran.append(fn))
    monkeypatch.setenv("MONITOR_ALERT_SECRET", "shh")
    c = app_module.app.test_client()
    assert c.post("/p2/paid-marketing/api/dailytodo/scheduled").status_code == 403 and ran == []
    assert c.post("/p2/paid-marketing/api/dailytodo/scheduled", headers={"X-Monitor-Secret": "shh"}).status_code == 202 and len(ran) == 1


def test_the_list_is_due_on_weekdays_from_nine_ist_until_noon_utc_and_only_once_a_day():
    s = Store()
    at = lambda d, h, m: dt.datetime(2026, 11, d, h, m, tzinfo=dt.timezone.utc)      # noqa: E731 (18 Nov 2026 is a Wednesday)
    assert t.due(s, at(18, 3, 30)) and t.due(s, at(18, 11, 59))
    assert not t.due(s, at(18, 3, 29)) and not t.due(s, at(18, 12, 0))               # too early, too late
    assert not t.due(s, at(21, 5, 0)) and not t.due(s, at(22, 5, 0))                 # Saturday, Sunday
    s.state_set(t.KEY_RUNS, [{"at": at(18, 3, 31).isoformat()}])
    assert not t.due(s, at(18, 5, 0)) and t.due(s, at(19, 3, 30))                    # sent today, due again tomorrow


def test_a_run_started_twice_in_a_day_sends_once_unless_forced(monkeypatch, store):
    ran = []
    monkeypatch.setattr(t, "due", lambda s, now: not ran)
    monkeypatch.setattr(app_module, "_am_rows", lambda: ([], "", ""))
    monkeypatch.setattr(t, "run", lambda *a, **k: ran.append(1))
    monkeypatch.setattr("tracker.paid_feedback.default_client", lambda: None)
    app_module._todo_run("platform timer")
    app_module._todo_run("platform timer")
    assert len(ran) == 1
    app_module._todo_run("schedule", True)
    assert len(ran) == 2


def test_nothing_about_riccobene_is_listed_and_an_open_item_from_before_is_retired():
    from tracker import paid_am
    s, d = Store(), Deliver()
    old = "Myrtle Beach (de novo): Check pacing"
    s.state_set(t.KEY_ITEMS, {t.item_id("Siddesh", old): {"am": "Siddesh", "text": old, "status": "open", "firstOn": "2026-11-18", "doneBy": "", "doneAt": ""}})
    skip = lambda x: paid_am.mentions_dropped(x, ["Benson Dental"])                  # noqa: E731
    ans = '{"items": ["D&J: reply to the invoice", "Riccobene: fix the budget", "Benson Dental: call", "Monster Transmission: add the feed"]}'
    t.run(s, d, groups=GROUPS, people=PEOPLE, ask=asker({"Siddesh": ans}), now=NOW, skip=skip)
    text = json.dumps(d.posted[0][2])
    assert "Myrtle" not in text and "Riccobene" not in text and "Benson" not in text and "D&amp;J" in text or "D&J" in text
    assert s.state_get(t.KEY_ITEMS)[t.item_id("Siddesh", old)]["status"] == "removed"
    assert paid_am.dropped_names([{"account": "Hampstead", "client": "Riccobene"}, {"account": "OIA", "client": "OIA"}]) == ["Hampstead", "Riccobene"]


def test_accounts_no_longer_managed_are_dropped_and_two_clients_go_to_ebin():
    from tracker import paid_am
    rows = [{"account": "Riccobene", "client": "Riccobene", "am": "Divith"}, {"account": "Great Lakes Dental", "client": "x", "am": "A"},
            {"account": "Daniel I. Shapiro, M.D.", "client": "y", "am": "A"}, {"account": "Dental Care Associates", "client": "z", "am": "A"},
            {"account": "Eventgroove 1", "client": "Eventgroove", "am": "Siddesh"}, {"account": "Burn Out Brands", "client": "Burn Out", "am": "Siddesh"},
            {"account": "OIA", "client": "OIA", "am": "Divith"}]
    assert t.groups_from_rows(paid_am.adjust(rows)) == {"Divith": ["OIA"], "Ebin V Edison": ["Burn Out Brands", "Eventgroove 1"]}
