"""An in-memory stand-in for tracker/paid_domains_store.py, and a recording stand-in for the senders in tracker/market_radar_deliver.py."""
import copy


class MemStore:
    class StoreUnavailable(Exception):
        pass

    def __init__(self):
        self.known, self.found, self.state, self.locks, self._id = [], {}, {}, set(), 0

    def known_list(self):
        return sorted((dict(k) for k in self.known), key=lambda k: (k["domain"], k["scope"]))

    def known_add(self, domain, scope, note, by):
        for k in self.known:
            if k["domain"] == domain and k["scope"] == scope:
                return dict(k), False
        self._id += 1
        k = {"id": self._id, "domain": domain, "scope": scope, "note": note, "added_by": by, "added_at": "2026-10-19T00:00:00+00:00"}
        self.known.append(k)
        return dict(k), True

    def known_bulk(self, rows, by):
        added = 0
        for r in rows:
            added += self.known_add(r["domain"], r["scope"], r.get("note") or "", by)[1]
        return {"added": added, "existing": len(rows) - added}

    def known_remove(self, entry_id):
        n = len(self.known)
        self.known = [k for k in self.known if k["id"] != entry_id]
        return len(self.known) < n

    def findings_all(self):
        return [copy.deepcopy(f) for f in sorted(self.found.values(), key=lambda f: f["lastSeen"], reverse=True)]

    def finding_get(self, fp):
        return copy.deepcopy(self.found.get(fp))

    def findings_put(self, rows):
        for f in rows:
            self.found[f["fingerprint"]] = copy.deepcopy(f)

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

    def send_email(self, subject, text, html, to):
        if "email" in self.fail:
            raise RuntimeError("smtp down")
        self.emails.append({"subject": subject, "text": text, "html": html, "to": to})

    def send_slack(self, channel, text, blocks):
        if "slack" in self.fail:
            raise RuntimeError("not_in_channel")
        self.slacks.append({"channel": channel, "text": text, "blocks": blocks})
