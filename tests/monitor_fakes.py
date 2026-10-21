"""An in-memory stand-in for tracker/paid_monitor_store.py (the monitor tests run on it, and tests/test_paid_monitor_store.py holds it to the same contract as the real store)
and a recording stand-in for the senders in tracker/market_radar_deliver.py."""
import copy
from datetime import date, timedelta


class MemStore:
    class StoreUnavailable(Exception):
        pass

    def __init__(self):
        self.state, self.issues, self.snaps, self.locks = {}, {}, {}, set()

    def state_get(self, key, default=None):
        return copy.deepcopy(self.state.get(key, default))

    def state_set(self, key, value):
        self.state[key] = copy.deepcopy(value)

    def claim(self, key, ttl_s):
        if key in self.locks:
            return False
        self.locks.add(key)
        return True

    def release(self, key):
        self.locks.discard(key)

    def issues_all(self, agent):
        return [copy.deepcopy(v) for (a, _fp), v in sorted(self.issues.items(), key=lambda kv: kv[1]["lastSeen"], reverse=True) if a == agent]

    def issues_put(self, rows):
        for f in rows:
            self.issues[(f["agent"], f["fingerprint"])] = copy.deepcopy(f)

    def snaps_put(self, agent, day, rows):
        for aid, data in rows.items():
            self.snaps[(agent, aid, day)] = copy.deepcopy(data)

    def snaps_get(self, agent, account_id, since):
        return [dict(copy.deepcopy(d), day=day) for (a, aid, day), d in sorted(self.snaps.items(), key=lambda kv: kv[0][2]) if a == agent and aid == account_id and day >= since]

    def snaps_prune(self, agent, before):
        self.snaps = {k: v for k, v in self.snaps.items() if not (k[0] == agent and k[2] < before)}


class Deliver:
    """Records what would have been sent. `fail` makes a channel refuse."""

    def __init__(self, fail=()):
        self.emails, self.slacks, self.fail = [], [], set(fail)

    def ready(self):
        return {"email": True, "slack": True}

    def attempt(self, fn):
        try:
            fn()
            return {"status": "sent"}
        except Exception as e:  # noqa: BLE001
            return {"status": "failed", "error": str(e)}

    def send_email(self, subject, text, html, to, attachments=None):
        if "email" in self.fail:
            raise RuntimeError("smtp down")
        self.emails.append({"subject": subject, "text": text, "html": html, "to": to, "attachments": attachments or []})

    def send_slack(self, channel, text, blocks):
        if "slack" in self.fail:
            raise RuntimeError("not_in_channel")
        self.slacks.append({"channel": channel, "text": text, "blocks": blocks})
