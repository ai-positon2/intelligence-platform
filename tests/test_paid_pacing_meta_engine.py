"""Pacing Desk: the real engine run once per channel. A Meta run matches the month's spend against the Meta allocation rows, a Google run against the
Google ones, and neither sees the other's (the engine is run in a throwaway folder, as the platform runs it)."""
import datetime as dt

from tracker import paid_pacing_budgets as pb
from tracker import paid_pacing_read as rd
from tracker import paid_refresh as rf

AS_OF = dt.date(2026, 10, 16)


def spend(account, per_day, name="Camp", conv=2.0, days=15):
    return [{"id": "1", "name": name, "status": "ENABLED", "type": "Search", "bid": "Manual CPC", "budget": per_day, "pool": "", "date": "2026-10-%02d" % d, "impr": 100.0,
             "clicks": 10.0, "cost": per_day, "conv": conv, "value": 0.0, "sis": 0.5, "account": account, "accountId": "1"} for d in range(1, days + 1)]


def allocations(*rows):
    return pb.dpr_rows(pb.check_rows(list(rows)), "2026-10")


def row(account, allocated, channel):
    return {"client": "Acme", "account": account, "allocated": allocated, "channel": channel}


def accounts_of(body):
    return {a["name"]: a for c in body["clients"] for a in c["accounts"]}


def test_a_meta_run_paces_the_meta_spend_against_the_meta_allocation():
    rows = rd.export_rows(spend("Acme Social", 100.0))
    body, _ = rf.run_engine(rows, allocations(row("Acme Dental", 3100, "Google"), row("Acme Social", 3100, "Meta")), AS_OF, channel="Meta")
    a = accounts_of(body)["Acme Social"]
    assert a["spend"] == 1500.0 and a["allocated"] == 3100.0 and a["landing"] == "ON TARGET"          # 15 days of $100 against $3,100 on the 16th
    assert "Acme Dental" not in accounts_of(body)                                                       # the Google allocation is not this run's


def test_the_google_run_never_matches_google_spend_against_a_meta_allocation():
    rows = rd.export_rows(spend("Acme Dental", 100.0))
    body, _ = rf.run_engine(rows, allocations(row("Acme Dental", 3100, "Google"), row("Acme Dental", 900, "Meta")), AS_OF)
    a = accounts_of(body)["Acme Dental"]
    assert a["allocated"] == 3100.0                                                                     # not 4,000: the Meta row is the other run's


def test_spend_on_a_meta_account_with_no_meta_allocation_is_not_paced_against_anything():
    """The Meta run pairs spend with Meta allocation rows only: an account that has none is left out, never matched to a Google row."""
    rows = rd.export_rows(spend("Acme Social", 100.0) + spend("Acme Other", 50.0))
    body, _ = rf.run_engine(rows, allocations(row("Acme Dental", 3100, "Google"), row("Acme Social", 3100, "Meta")), AS_OF, channel="Meta")
    assert set(accounts_of(body)) == {"Acme Social"} and accounts_of(body)["Acme Social"]["spend"] == 1500.0
