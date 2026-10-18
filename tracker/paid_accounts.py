"""Paid Marketing Agents: naming the account an agent reads when it can read a Google Ads account, a Meta ad account or one of each.

The one string the pages send is "<10-digit Google Ads id>", "act_<digits>" or "<google id>+act_<digits>" (the platform switch's combined account). `parse_account` says which
platforms it names, `account_key` is the string a saved read is filed under, and `targets` finds each account among what the connections can read. Used by the Landing Page &
Relevancy Check and the Test & Experiment Tracker; the Account Audit names its accounts the same way (tracker/paid_audit.py).
"""
from __future__ import annotations

import re

from . import paid_segments as seg

BadRequest = seg.BadRequest

ID_RE = re.compile(r"^\d{10}$")
COMBO_RE = re.compile(r"^(\d{10})\+(act_\d{4,20})$")
NAMES = {"google": "Google Ads", "meta": "Meta Ads"}


def parse_account(raw):
    """{google: id or None, meta: act id or None} from "<google id>", "act_..." or "<google id>+<act_...>"."""
    raw = str(raw or "").strip()
    m = COMBO_RE.match(raw)
    if m:
        return {"google": m.group(1), "meta": m.group(2)}
    if ID_RE.match(raw):
        return {"google": raw, "meta": None}
    from . import paid_meta
    if paid_meta.is_meta(raw):
        return {"google": None, "meta": raw}
    raise BadRequest("account should be a 10-digit Google Ads customer id, a Meta ad account id (act_...) or both joined by +")


def account_key(ids):
    """The one string a read of this account is filed under: "<google id>", "act_..." or "<google id>+<act_...>"."""
    return "+".join(x for x in (ids["google"], ids["meta"]) if x)


def targets(account, *, store, accounts_fn=None):
    """(account key, [{platform, id, name, currency}]) for the account named: its Google Ads account and its Meta ad account, resolved against what the connections can read. Raises
    BadRequest for one that cannot be read or for a pair that bills in two currencies (their spend would be added)."""
    ids = parse_account(account)
    found = []
    if ids["google"]:
        g = seg.resolve_target(ids["google"], accounts_fn=accounts_fn, groups_fn=lambda: [])
        found.append({"platform": "google", "id": g["id"], "name": g["name"], "currency": g.get("currency") or ""})
    if ids["meta"]:
        m = seg._meta_target(ids["meta"], store)
        found.append({"platform": "meta", "id": m["id"], "name": m["name"], "currency": m.get("currency") or ""})
    if len(found) == 2 and found[0]["currency"] and found[1]["currency"] and found[0]["currency"] != found[1]["currency"]:
        raise BadRequest("A Google Ads account and a Meta ad account are read together only when they bill in the same currency (%s and %s here), because their spend is added." % (
            found[0]["currency"], found[1]["currency"]))
    return account_key(ids), found
