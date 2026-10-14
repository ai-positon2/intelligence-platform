"""An in-memory stand-in for tracker/paid_store's Google Ads functions, shared by the tests of the agents that read
Google Ads. Entries remember when they were saved, so a test can age one (`s.at[key] -= 7200`, `s.dayat[(cid, day)] -= ...`)
and see it go stale."""
import datetime as dt
import time


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat()


class FakeStore:
    def __init__(self, calls=0):
        self.calls, self.cache, self.old, self.block = calls, {}, {}, 0.0
        self.at = {}                    # key -> epoch seconds it was saved
        self.days, self.dayat = {}, {}  # (customer, day) -> data, epoch seconds
        self.reads = 0                  # how many times a saved copy was asked for (cache or days)

    # whole results
    def get_gads_cache_any(self, key):
        if key in self.cache:
            return self.cache[key], iso(self.at.get(key, time.time()))
        if key in self.old:
            return self.old[key], "2026-10-10T08:00:00+00:00"
        return None

    def get_gads_entry(self, key, ttl):
        self.reads += 1
        if key in self.cache and time.time() - self.at.get(key, time.time()) <= ttl:
            return self.cache[key], iso(self.at.get(key, time.time()))
        return None

    def get_gads_cache(self, key, ttl):
        hit = self.get_gads_entry(key, ttl)
        return hit[0] if hit else None

    def put_gads_cache(self, key, value):
        self.cache[key] = value
        self.at[key] = time.time()

    # days
    def get_gads_days(self, customer_id, start, end):
        self.reads += 1
        return {d: (v, iso(self.dayat[(c, d)])) for (c, d), v in self.days.items() if c == customer_id and start <= d <= end}

    def put_gads_days(self, customer_id, days):
        for d, v in days.items():
            self.days[(customer_id, d)] = v
            self.dayat[(customer_id, d)] = time.time()

    # the block and the tally
    def get_gads_block(self):
        return self.block

    def set_gads_block(self, until):
        self.block = until

    def bump_gads_calls(self, day):
        self.calls += 1
        return self.calls

    def gads_calls(self, day):
        return self.calls
