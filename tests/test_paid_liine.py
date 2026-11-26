"""Paid Marketing Agents: Liine Lead Analysis.

Pinned here: a cell Liine hid (empty) is unknown and never 0; every rule fires at its stated threshold and not just under it; a
month with a hidden practice is not a total and the running month is marked; the reader joins the parts of the newest run, falls
back to an older whole run, and never shows half a run; the routes are staff only; and the new page is wired into the hub, the
palette, Vimi and the stylesheet version. All data here is invented: no real client figure is kept in the repo.
"""
import datetime as dt
import os
import re
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from tracker import paid_liine as L  # noqa: E402
from tracker import paid_liine_math as M  # noqa: E402

NOW = dt.datetime(2026, 11, 26, 13, 0, 0)


def row(table, pid="", practice="", period="", key="", leads="", booked="", spend="", clicks="", value="", note=""):
    return {"table": table, "practice_id": str(pid), "practice": practice, "period": period, "key": key, "leads": str(leads),
            "booked": str(booked), "spend": str(spend), "clicks": str(clicks), "value": str(value), "note": note}


def base_rows(pulled="2026-11-26T12:00:00Z", end="2026-11-25"):
    return [
        row("meta", key="pulled_at", value=pulled),
        row("meta", key="last90_start", period="2026-08-27"), row("meta", key="last90_end", period=end),
        row("meta", key="prev90_start", period="2026-05-29"), row("meta", key="prev90_end", period="2026-08-26"),
        row("meta", key="date_basis", note="outcome"), row("meta", key="liine_user", note="x@example.com"),
        row("practices", 1, "Alpha", "2025-08-01", "Org", note="ppc=1;formflow=1;mroi=0"),
        row("practices", 2, "Beta", "2025-08-01", "Org", note="ppc=1;formflow=0;mroi=0"),
        row("totals", 1, "Alpha", "last90", "new_patient_leads", 1000, 500), row("totals", 1, "Alpha", "prev90", "new_patient_leads", 1000, 500),
        row("totals", 2, "Beta", "last90", "new_patient_leads", 400, 200), row("totals", 2, "Beta", "prev90", "new_patient_leads", 400, 200),
        row("monthly", 1, "Alpha", "2026-10", "", 300, 150), row("monthly", 1, "Alpha", "2026-11", "", 100, 50),
        row("monthly", 2, "Beta", "2026-10", "", 120, 60), row("monthly", 2, "Beta", "2026-11", "", "", ""),
        row("channels", 1, "Alpha", "last90", "Google Ads", 200, 100),
        row("notbooked", 1, "Alpha", "last90", "Booked", 500),
        row("missed", 1, "Alpha", "last90", "voicemail_interactions", value=10),
        row("ppc", 1, "Alpha", "last90", "__total__", 50, 20, 4000, 900, 90000, "platform=all"),
    ]


def build(rows):
    return M.analyse(M.parse(rows), now=NOW)


def with_(rows, *extra, drop=None):
    out = [r for r in rows if not (drop and drop(r))]
    return out + list(extra)


def find(a, fid, pid=None):
    return [f for f in a["findings"] if f["id"] == fid and (pid is None or f["practiceId"] == str(pid))]


# ── parsing ────────────────────────────────────────────────────────────────

def test_empty_cells_are_unknown_not_zero():
    assert M.num("") is None and M.num("  ") is None and M.num(None) is None and M.num("x") is None
    assert M.num("0") == 0.0 and M.num("12.5") == 12.5
    ds = M.parse(base_rows())
    assert ds["monthly"]["2"]["2026-11"] == (None, None)


def test_rate_needs_both_numbers_and_a_denominator():
    assert M.rate(None, 5) is None and M.rate(3, 0) is None and M.rate(3, None) is None
    assert M.rate(0, 5) == 0.0


def test_missing_tables_names_what_is_absent():
    rows = [r for r in base_rows() if r["table"] not in ("ppc", "monthly")]
    assert M.missing_tables(M.parse(rows)) == ["monthly", "ppc"]
    assert M.missing_tables(M.parse(base_rows())) == []


def test_reason_groups():
    assert M.reason_group("Booked") == "booked" and M.reason_group("Not Called") == "not_called"
    assert M.reason_group("Transferred Call Not Answered") == "handling"
    assert M.reason_group("Treatment Not Offered") == "fit"
    assert M.reason_group("Caller Procrastination") == "nurture"
    assert M.reason_group("Something New") == "other"


# ── headline and practice figures ──────────────────────────────────────────

def test_headline_sums_and_blank_when_a_practice_has_no_total():
    a = build(base_rows())
    assert a["headline"]["leads"] == 1400 and a["headline"]["booked"] == 700 and abs(a["headline"]["rate"] - 0.5) < 1e-9
    assert a["headline"]["prevLeads"] == 1400
    rows = with_(base_rows(), drop=lambda r: r["table"] == "totals" and r["practice_id"] == "2" and r["period"] == "last90")
    a = build(rows)
    assert a["headline"]["leads"] is None and a["headline"]["rate"] is None
    assert any("no total" in n["text"] for n in a["notes"])


def test_cost_per_booked_is_spend_over_paid_booked():
    a = build(base_rows())
    p = [x for x in a["practices"] if x["id"] == "1"][0]
    assert p["spend"] == 4000 and p["paidBooked"] == 20 and p["costPerBooked"] == 200
    assert a["headline"]["costPerBooked"] == 200


def test_cost_per_booked_is_blank_without_spend_or_without_a_booked_paid_lead():
    no_spend = with_(base_rows(), drop=lambda r: r["table"] == "ppc")
    p = [x for x in build(no_spend)["practices"] if x["id"] == "1"][0]
    assert p["spend"] is None and p["costPerBooked"] is None
    zero_booked = with_(base_rows(), row("ppc", 1, "Alpha", "last90", "__total__", 50, 0, 4000, 900, 0, "platform=all"), drop=lambda r: r["table"] == "ppc")
    p = [x for x in build(zero_booked)["practices"] if x["id"] == "1"][0]
    assert p["costPerBooked"] is None
    zero_spend = with_(base_rows(), row("ppc", 1, "Alpha", "last90", "__total__", 50, 5, 0, 900, 0, "platform=all"), drop=lambda r: r["table"] == "ppc")
    assert [x for x in build(zero_spend)["practices"] if x["id"] == "1"][0]["costPerBooked"] is None


def test_stale_pull_adds_a_note_only_after_72_hours():
    fresh = build(base_rows(pulled="2026-11-23T14:00:01Z"))
    assert not any("days ago" in n["text"] for n in fresh["notes"])
    old = build(base_rows(pulled="2026-11-23T12:59:59Z"))
    assert any("3 days ago" in n["text"] for n in old["notes"])


# ── findings: each at its threshold ────────────────────────────────────────

def nc_rows(leads, nc, pid=1):
    return [row("totals", pid, "P", "last90", "new_patient_leads", leads, 100), row("notbooked", pid, "P", "last90", "Not Called", nc)]


def run_nc(leads, nc):
    rows = with_(base_rows(), *nc_rows(leads, nc), drop=lambda r: r["table"] in ("totals", "notbooked") and r["practice_id"] == "1" and r["period"] == "last90")
    return find(build(rows), "notcalled", 1)


def test_not_called_fires_at_a_quarter_and_not_below():
    assert run_nc(120, 30)                      # 25%, 30 leads
    assert not run_nc(121, 30)                  # just under 25%
    assert not run_nc(200, 29)                  # under 30 leads
    assert run_nc(100, 50)[0]["severity"] == "high"
    assert run_nc(120, 30)[0]["severity"] == "medium"
    assert run_nc(1000, 499)[0]["severity"] == "medium" and run_nc(1000, 500)[0]["severity"] == "high"


def test_not_called_illustration_uses_ten_and_twenty_percent_and_form_figures():
    rows = with_(base_rows(), *nc_rows(1000, 600), drop=lambda r: r["table"] in ("totals", "notbooked") and r["practice_id"] == "1" and r["period"] == "last90")
    rows += [row("forms", 1, "Alpha", "last90", "total_leads", value=300), row("forms", 1, "Alpha", "last90", "perc_forms_called_or_texted", value=0.25),
             row("forms", 1, "Alpha", "now", "no_outreach_active_leads_count", value=77)]
    f = find(build(rows), "notcalled", 1)[0]
    text = " ".join(f["evidence"])
    assert "about 60 more patients" in text and "about 120" in text
    assert "only 25% were called or texted (75 of 300)" in text and "77 active form leads" in text


def rate_rows(l_now, b_now, l_prev, b_prev):
    return [row("totals", 1, "Alpha", "last90", "new_patient_leads", l_now, b_now), row("totals", 1, "Alpha", "prev90", "new_patient_leads", l_prev, b_prev)]


def run_rate(l_now, b_now, l_prev, b_prev):
    rows = with_(base_rows(), *rate_rows(l_now, b_now, l_prev, b_prev), drop=lambda r: r["table"] == "totals" and r["practice_id"] == "1")
    return find(build(rows), "ratefall", 1)


def test_rate_fall_needs_eight_points_and_a_hundred_leads_each_side():
    assert run_rate(100, 42, 100, 50)           # exactly 8 points
    assert not run_rate(100, 43, 100, 50)       # 7 points
    assert not run_rate(99, 10, 100, 50)        # too few leads now
    assert not run_rate(100, 10, 99, 50)        # too few leads before
    assert run_rate(100, 35, 100, 50)[0]["severity"] == "high"      # 15 points
    assert run_rate(100, 36, 100, 50)[0]["severity"] == "medium"    # 14 points


def test_rate_fall_says_when_the_earlier_period_is_short():
    rows = with_(base_rows(), *rate_rows(200, 40, 200, 100), drop=lambda r: (r["table"] == "totals" and r["practice_id"] == "1") or (r["table"] == "practices" and r["practice_id"] == "1"))
    rows.append(row("practices", 1, "Alpha", "2026-07-19", "Org", note="ppc=1"))
    assert "starts on 2026-07-19" in " ".join(run_rate_rows(rows)[0]["evidence"])


def run_rate_rows(rows):
    return find(build(rows), "ratefall", 1)


def waste_rows(*camps, total=4000):
    out = [row("ppc", 1, "Alpha", "last90", "__total__", 50, 20, total, 900, 0, "platform=all")]
    for name, spend, leads, booked in camps:
        out.append(row("ppc", 1, "Alpha", "last90", name, leads, booked, spend, 10, 0, "platform=Google Ads"))
    return out


def run_waste(*camps, total=4000):
    rows = with_(base_rows(), *waste_rows(*camps, total=total), drop=lambda r: r["table"] == "ppc")
    return find(build(rows), "paidwaste", 1)


def test_paid_waste_counts_only_campaigns_at_500_with_no_booked_patient():
    assert run_waste(("A", 3000, 5, 0), total=4000)
    assert not run_waste(("A", 499.99, 5, 0), ("B", 2999, 5, 0), total=4000)     # one under 500, other under 3000 on its own
    assert run_waste(("A", 500, 5, 0), ("B", 2500, 5, 0), total=4000)            # 3000 together
    assert not run_waste(("A", 3000, 5, 1), total=4000)                           # booked someone
    f = run_waste(("A", 3000, 1, 0), total=4000)[0]
    assert "$3,000 of $4,000" in f["evidence"][0] and "1 lead," in f["evidence"][1] and "75%" in f["evidence"][0]
    assert f["severity"] == "high"
    assert run_waste(("A", 3000, 1, 0), total=10000)[0]["severity"] == "medium"
    assert run_waste(("A", 3999, 1, 0), total=10000)[0]["severity"] == "medium"
    assert run_waste(("A", 4000, 1, 0), total=10000)[0]["severity"] == "high"    # 40%


def test_paid_waste_leaves_out_not_matched_and_total_rows():
    rows = with_(base_rows(), *waste_rows(("Not Matched", 9000, 3, 0)), drop=lambda r: r["table"] == "ppc")
    assert not find(build(rows), "paidwaste", 1)


def missed_rows(vm, calls, rate_=None, recovered=None):
    out = [row("missed", 1, "Alpha", "last90", "voicemail_interactions", value=vm), row("missed", 1, "Alpha", "last90", "inbound_analyzed_call_interactions", value=calls)]
    if rate_ is not None:
        out.append(row("missed", 1, "Alpha", "last90", "missed_call_rate", value=rate_))
    if recovered is not None:
        out.append(row("missed", 1, "Alpha", "last90", "within_1_day_recovered_rate", value=recovered))
    return out


def run_missed(vm, calls, **k):
    rows = with_(base_rows(), *missed_rows(vm, calls, **k), drop=lambda r: r["table"] == "missed")
    return find(build(rows), "missed", 1)


def test_missed_calls_fire_at_15_percent_with_30_voicemails():
    assert run_missed(30, 200)                   # 15%
    assert not run_missed(29, 100)               # under 30 voicemails
    assert not run_missed(30, 201)               # 14.9%
    assert run_missed(50, 200)[0]["severity"] == "high" and run_missed(30, 200)[0]["severity"] == "medium"
    assert run_missed(49, 200)[0]["severity"] == "medium"   # 24.5%
    assert "within a day" in " ".join(run_missed(60, 200, recovered=0.3)[0]["evidence"])


def test_missed_call_uses_liines_own_rate_when_given():
    assert not run_missed(60, 200, rate_=0.05)


def run_handling(leads, **reasons):
    rows = with_(base_rows(), row("totals", 1, "Alpha", "last90", "new_patient_leads", leads, 100),
                 *[row("notbooked", 1, "Alpha", "last90", k.replace("_", " "), v) for k, v in reasons.items()],
                 drop=lambda r: (r["table"] == "notbooked") or (r["table"] == "totals" and r["practice_id"] == "1" and r["period"] == "last90"))
    return find(build(rows), "handling", 1)


def test_handling_loss_needs_ten_percent_and_thirty_leads():
    assert run_handling(300, **{"Not_Reached": 30})
    assert not run_handling(301, **{"Not_Reached": 30})
    assert not run_handling(100, **{"Not_Reached": 29})
    f = run_handling(300, **{"Not_Reached": 20, "Dropped_Call_/_Hang_Up": 10})[0]
    assert "30 leads (10%)" in f["evidence"][0] and "Not Reached 20" in f["evidence"][0]


def run_channel(ch_leads, ch_booked, key="Google Ads", overall=(1000, 500)):
    rows = with_(base_rows(), row("channels", 1, "Alpha", "last90", key, ch_leads, ch_booked),
                 row("totals", 1, "Alpha", "last90", "new_patient_leads", *overall),
                 drop=lambda r: r["table"] == "channels" or (r["table"] == "totals" and r["practice_id"] == "1" and r["period"] == "last90"))
    return find(build(rows), "channel", 1)


def test_channel_with_volume_and_half_the_rate():
    assert run_channel(100, 25)                 # 25% against 50%: exactly half
    assert not run_channel(100, 26)
    assert not run_channel(99, 1)
    assert not run_channel(100, 1, key="Not attributed")
    assert not run_channel(100, "")             # booked hidden: no rate, no finding


def test_findings_are_ordered_high_first():
    rows = with_(base_rows(), *nc_rows(100, 60), drop=lambda r: r["table"] in ("totals", "notbooked") and r["practice_id"] == "1" and r["period"] == "last90")
    rows += missed_rows(30, 200)
    a = build(rows)
    sev = [f["severity"] for f in a["findings"]]
    assert sev == sorted(sev, key=lambda s: {"high": 0, "medium": 1}[s])


# ── tables ─────────────────────────────────────────────────────────────────

def test_channel_table_counts_hidden_cells_and_rates_only_known_pairs():
    rows = [row("practices", 1, "A", "2025-01-01"), row("practices", 2, "B", "2025-01-01"),
            row("channels", 1, "A", "last90", "Direct", 100, 40), row("channels", 2, "B", "last90", "Direct", 50, ""), row("channels", 2, "B", "last90", "Other", "", "")]
    t = {r["channel"]: r for r in M.channel_table(M.parse(rows), "last90")}
    assert t["Direct"]["leads"] == 150 and t["Direct"]["booked"] == 40
    assert t["Direct"]["rate"] == 0.4 and t["Direct"]["rateLeads"] == 100        # B's pair is not known, so it is left out of the rate
    assert t["Other"]["hidden"] == 1 and t["Other"]["leads"] == 0 and t["Other"]["rate"] is None


def test_reason_table_counts_hidden_and_sorts():
    rows = [row("practices", 1, "A", "2025-01-01"), row("notbooked", 1, "A", "last90", "Not Called", 8), row("notbooked", 1, "A", "last90", "Booked", 20),
            row("notbooked", 1, "A", "last90", "Scheduling Issue", "")]
    t, hidden = M.reason_table(M.parse(rows), "last90")
    assert [r["reason"] for r in t] == ["Booked", "Not Called"] and hidden == 1


def test_monthly_marks_hidden_and_running_month():
    a = build(base_rows())
    allm = {p["month"]: p for p in a["months"]["all"]}
    assert allm["2026-10"]["leads"] == 420 and allm["2026-10"]["hidden"] == 0 and not allm["2026-10"]["partial"]
    assert allm["2026-11"]["leads"] == 100 and allm["2026-11"]["hidden"] == 1 and allm["2026-11"]["partial"]


def test_a_practice_series_marks_the_running_month_too():
    pts = {p["month"]: p for p in build(base_rows())["months"]["byPractice"]["1"]}
    assert pts["2026-11"]["partial"] and not pts["2026-10"]["partial"]
    assert pts["2026-10"]["rate"] == 0.5


def test_monthly_skips_months_before_a_practice_started_and_marks_a_finished_month_whole():
    rows = with_(base_rows(end="2026-11-30"), row("practices", 3, "Late", "2026-11-01", "Org"), row("monthly", 3, "Late", "2026-11", "", 5, 1),
                 drop=lambda r: r["table"] == "monthly" and r["practice_id"] == "2")
    a = build(rows)
    assert [p["month"] for p in a["months"]["byPractice"]["3"]] == ["2026-11"]
    assert not {p["month"]: p for p in a["months"]["all"]}["2026-11"]["partial"]    # the last day of the month: it is complete


def test_campaign_table_sorted_by_spend_without_totals():
    rows = with_(base_rows(), *waste_rows(("Small", 100, 1, 1), ("Big", 900, 1, 1)), drop=lambda r: r["table"] == "ppc")
    t = M.campaign_table(M.parse(rows), "last90")
    assert [r["campaign"] for r in t] == ["Big", "Small"] and t[0]["costPerBooked"] == 900


def test_detail_is_there_for_every_practice():
    a = build(base_rows())
    assert set(a["detail"]) == {"1", "2"} and a["detail"]["1"]["channels"][0]["channel"] == "Google Ads"


# ── the reader ─────────────────────────────────────────────────────────────

CSV_HEAD = ["table", "practice_id", "practice", "period", "key", "leads", "booked", "spend", "clicks", "value", "note"]


def values(rows):
    return [CSV_HEAD] + [[r[h] for h in CSV_HEAD] for r in rows]


class FakeDrive:
    def __init__(self, files, fail=False):
        self.files, self.fail, self.calls = files, fail, 0

    def files_(self):
        return self

    def list(self, **kw):
        self.calls += 1
        self.q = kw.get("q")
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("403 not shared")
        return {"files": self.files}


class _Drive:
    def __init__(self, d):
        self.d = d

    def files(self):
        return self.d.files_()


class FakeSheets:
    def __init__(self, data, broken=()):
        self.data, self.broken, self.reads = data, set(broken), 0

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range):
        self.cur = spreadsheetId
        return self

    def execute(self):
        self.reads += 1
        if self.cur in self.broken:
            raise RuntimeError("500 boom")
        return {"values": self.data[self.cur]}


def sheet(name, fid, mt="application/vnd.google-apps.spreadsheet"):
    return {"id": fid, "name": name, "mimeType": mt}


def split(rows):
    """Cut a run into two files by table, like a pull that was too big for one."""
    a = [r for r in rows if r["table"] in ("meta", "practices", "totals", "monthly")]
    b = [r for r in rows if r["table"] not in ("meta", "practices", "totals", "monthly")]
    return a, b


@pytest.fixture(autouse=True)
def clean_cache():
    L._cache["value"], L._cache["at"] = None, 0.0
    yield
    L._cache["value"], L._cache["at"] = None, 0.0


def services(files, data, broken=(), drive_fail=False):
    fd = FakeDrive(files, fail=drive_fail)
    fs = FakeSheets(data, broken)
    return (lambda: (_Drive(fd), fs)), fd, fs


def test_runs_group_parts_newest_first_and_ignore_other_files():
    got = L.runs([sheet("Liine Data 2026-11-20 0900", "o"), sheet("Liine Data 2026-11-26 1216 part 2", "b"), sheet("Liine Data 2026-11-26 1216 part 1", "a"),
                  sheet("Liine Data 2026-11-26 1216", "c"), sheet("Notes", "n"), sheet("Liine Data 2026-11-25 1000", "x", mt="text/csv")])
    assert [s for s, _ in got] == ["2026-11-26 1216", "2026-11-20 0900"]
    assert [p[1] for p in got[0][1]] == ["a", "c", "b"] or [p[1] for p in got[0][1]] == ["c", "a", "b"]
    assert [p[0] for p in got[0][1]] == sorted(p[0] for p in got[0][1])


def test_rows_of_pads_short_rows_and_rejects_a_foreign_file():
    r = L.rows_of([CSV_HEAD, ["totals", "1", "A", "last90"]])
    assert r[0]["leads"] == "" and r[0]["key"] == ""
    assert L.rows_of([]) == [] and L.rows_of([CSV_HEAD, ["", "", ""]]) == []
    with pytest.raises(L.LiineError):
        L.rows_of([["a", "b"], ["1", "2"]])


def test_load_joins_the_parts_of_the_newest_run():
    a, b = split(base_rows())
    svc, fd, fs = services([sheet("Liine Data 2026-11-26 1216 part 2", "B"), sheet("Liine Data 2026-11-26 1216 part 1", "A")], {"A": values(a), "B": values(b)})
    out = L.load(services=svc)
    assert out["run"]["stamp"] == "2026-11-26 1216" and out["run"]["parts"] == 2 and out["headline"]["leads"] == 1400
    assert "'%s' in parents" % L.FOLDER_ID in fd.q and "trashed = false" in fd.q


def test_load_falls_back_to_an_older_whole_run_and_says_so():
    a, b = split(base_rows())
    old = base_rows(pulled="2026-11-20T09:00:00Z")
    svc, _, _ = services([sheet("Liine Data 2026-11-26 1216 part 1", "A"), sheet("Liine Data 2026-11-20 0900", "O")], {"A": values(a), "O": values(old)})
    out = L.load(services=svc)       # the newest run has no part 2: the tables ppc, channels... are missing
    assert out["run"]["stamp"] == "2026-11-20 0900" and not out["run"]["newest"]
    assert out["notes"][0]["level"] == "warn" and "2026-11-26 1216 is missing" in out["notes"][0]["text"] and "missing channels" in out["notes"][0]["text"]


def test_a_part_that_cannot_be_read_is_never_shown_as_a_whole_run():
    a, b = split(base_rows())
    svc, _, _ = services([sheet("Liine Data 2026-11-26 1216 part 1", "A"), sheet("Liine Data 2026-11-26 1216 part 2", "B")], {"A": values(a), "B": values(b)}, broken=("B",))
    with pytest.raises(L.LiineError) as e:
        L.load(services=svc)
    assert "Could not read" in str(e.value) and "part 2" in str(e.value)


def test_load_says_what_is_wrong_when_the_folder_is_empty_or_not_shared():
    svc, _, _ = services([sheet("Other", "z")], {})
    with pytest.raises(L.LiineError) as e:
        L.load(services=svc)
    assert "no Liine data" in str(e.value)
    svc, _, _ = services([], {}, drive_fail=True)
    with pytest.raises(L.LiineError) as e:
        L.load(services=svc)
    assert "shared" in str(e.value)


def test_load_is_cached_for_ten_minutes_and_force_reads_again(monkeypatch):
    svc, fd, fs = services([sheet("Liine Data 2026-11-26 1216", "A")], {"A": values(base_rows())})
    L.load(services=svc)
    reads = fs.reads
    L.load(services=svc)
    assert fs.reads == reads and fd.calls == 1
    clock = [L.time.time() + L.CACHE_SECONDS + 1]
    monkeypatch.setattr(L.time, "time", lambda: clock[0])
    L.load(services=svc)
    assert fd.calls == 2
    L.load(force=True, services=svc)
    assert fd.calls == 3


def test_services_without_a_key_say_so(monkeypatch):
    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    with pytest.raises(L.LiineError) as e:
        L._services()
    assert "GOOGLE_SA_JSON" in str(e.value)


# ── the routes ─────────────────────────────────────────────────────────────

def client_as(email):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


def test_page_and_report_are_for_staff_only():
    for path in ("/p2/paid-marketing/liine-lead-analysis", "/p2/paid-marketing/api/liine/report"):
        out = client_as("someone@gmail.com").get(path)
        assert out.status_code in (301, 302, 403)
        staff = client_as("someone@position2.com").get(path)
        assert staff.status_code not in (301, 302, 403)
        if not path.endswith("report"):
            assert staff.status_code == 200


def test_report_returns_the_analysis_or_a_plain_error(monkeypatch):
    seen = {}

    def fake(force=False, services=None):
        seen["force"] = force
        return {"headline": {"leads": 1}}
    monkeypatch.setattr(L, "load", fake)
    c = client_as("someone@position2.com")
    assert c.get("/p2/paid-marketing/api/liine/report").get_json() == {"headline": {"leads": 1}} and seen["force"] is False
    c.get("/p2/paid-marketing/api/liine/report?refresh=1")
    assert seen["force"] is True

    def boom(force=False, services=None):
        raise L.LiineError("There is no Liine data in the folder yet.")
    monkeypatch.setattr(L, "load", boom)
    out = c.get("/p2/paid-marketing/api/liine/report")
    assert out.status_code == 409 and "no Liine data" in out.get_json()["error"]

    def boom2(force=False, services=None):
        raise RuntimeError("secret internals")
    monkeypatch.setattr(L, "load", boom2)
    out = c.get("/p2/paid-marketing/api/liine/report")
    assert out.status_code == 502 and "secret" not in out.get_data(as_text=True)


# ── wiring ─────────────────────────────────────────────────────────────────

def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def test_the_page_loads_its_script_and_style_and_nothing_is_missing():
    html = client_as("someone@position2.com").get("/p2/paid-marketing/liine-lead-analysis").get_data(as_text=True)
    assert "js/liine.js" in html and "css/liine.css" in html and 'id="kpis"' in html and 'id="findings"' in html
    assert os.path.exists(os.path.join(ROOT, "static/js/liine.js")) and os.path.exists(os.path.join(ROOT, "static/css/liine.css"))


def test_the_agent_is_on_the_hub_the_palette_vimi_and_the_stylesheet_is_bumped_in_both_templates():
    assert "/p2/paid-marketing/liine-lead-analysis" in read("templates/paid_marketing.html")
    assert "/p2/paid-marketing/liine-lead-analysis" in read("templates/_paid_kpal.html")
    assert 'class="dash-card active c-liine"' in read("templates/paid_marketing.html") and ".c-liine" in read("static/css/paid_marketing.css")
    from tracker import vimi_catalog
    ids = [a["id"] for a in vimi_catalog.PAID_AGENTS]
    assert "liine-lead-analysis" in ids
    v1 = re.search(r"paid_marketing\.css'\) }}\?v=(\d+)", read("templates/paid_marketing.html")).group(1)
    v2 = re.search(r"paid_marketing\.css'\) }}\?v=(\d+)", read("templates/_paid_base.html")).group(1)
    assert v1 == v2


def test_no_em_dash_in_what_the_user_sees():
    for path in ("templates/liine_analysis.html", "static/js/liine.js", "static/css/liine.css", "tracker/paid_liine_math.py", "tracker/paid_liine.py"):
        assert "—" not in read(path), path


def test_every_figure_the_page_draws_is_escaped():
    js = read("static/js/liine.js")
    for needle in ("esc(f.title)", "esc(f.why)", "esc(e)", "esc(f.action)", "esc(r.campaign)", "esc(r.channel)", "esc(p.name)", "esc(x.text)"):
        assert needle in js, needle
    assert "innerHTML=" in js and "eval(" not in js
