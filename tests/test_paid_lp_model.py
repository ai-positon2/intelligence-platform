"""Landing Page & Relevancy Check: what a finding is and how a page and the account are scored (tracker/paid_lp_model.py)."""
import pytest

from tracker import paid_lp_model as m


@pytest.fixture(autouse=True)
def clean_catalog():
    cat, sec = dict(m.CATALOG), dict(m.SECTIONS)
    m.CATALOG.clear()
    m.SECTIONS.clear()
    yield
    m.CATALOG.clear()
    m.CATALOG.update(cat)
    m.SECTIONS.clear()
    m.SECTIONS.update(sec)


def entry(cid="T-1", section="s1", severity="high", **kw):
    e = {"id": cid, "section": section, "title": "Title " + cid, "severity": severity, "why": "Because.", "fix": "Do this."}
    e.update(kw)
    return e


def setup(*entries):
    m.section("s1", "First", 1)
    m.section("s2", "Second", 2)
    m.register(list(entries))


# ── The catalog ─────────────────────────────────────────────────────────────

def test_register_rejects_missing_text_and_unknown_values():
    m.section("s1", "First", 1)
    for bad in (entry(why=""), entry(fix="  "), entry(title=""), entry(severity="urgent"), entry(section="nope")):
        with pytest.raises(m.CatalogError):
            m.register([bad])
    with pytest.raises(m.CatalogError):
        m.register([{"id": "T-2", "section": "s1", "title": "t", "severity": "low", "why": "w"}])


def test_a_check_or_section_declared_twice_must_agree():
    setup(entry())
    m.register([entry()])
    m.section("s1", "First", 1)
    with pytest.raises(m.CatalogError):
        m.register([entry(title="Other")])
    with pytest.raises(m.CatalogError):
        m.section("s1", "Renamed", 1)


def test_make_joins_the_catalog_and_what_was_found():
    setup(entry())
    f = m.make("T-1", "fail", "  12 of 20 keywords are missing.  ", items=["a", "b"], count=12)
    assert (f["id"], f["section"], f["title"], f["severity"], f["result"]) == ("T-1", "s1", "Title T-1", "high", "fail")
    assert f["found"] == "12 of 20 keywords are missing." and f["why"] == "Because." and f["fix"] == "Do this."
    assert f["items"] == ["a", "b"] and f["count"] == 12
    assert "detail" not in f and "caps" not in f


def test_make_caps_the_items_shown_and_keeps_the_real_count():
    setup(entry())
    f = m.make("T-1", "warn", "x", items=[str(i) for i in range(30)])
    assert len(f["items"]) == m.MAX_ITEMS and f["count"] == 30
    assert m.make("T-1", "warn", "x", items=["a", "b", "c"], count=2)["count"] == 3          # a count smaller than the list never shrinks it


def test_make_can_raise_the_severity_and_carry_a_cap_and_a_detail():
    setup(entry(severity="low"))
    f = m.make("T-1", "fail", "x", severity="critical", caps="the page is down", detail={"k": 1})
    assert f["severity"] == "critical" and f["caps"] == "the page is down" and f["detail"] == {"k": 1}


def test_make_refuses_an_unknown_check_result_or_severity():
    setup(entry())
    for args, kw in ((("nope", "pass", "x"), {}), (("T-1", "maybe", "x"), {}), (("T-1", "pass", "x"), {"severity": "huge"})):
        with pytest.raises(m.CatalogError):
            m.make(*args, **kw)


def test_unknown_and_not_applicable_say_why_and_are_never_judged():
    setup(entry())
    u = m.unknown("T-1", "the page refused us.")
    assert u["result"] == "unknown" and u["found"] == "Could not be checked: the page refused us"
    n = m.not_applicable("T-1", "Only Search ads have keywords.")
    assert n["result"] == "na" and n["found"] == "Only Search ads have keywords."


# ── A page ──────────────────────────────────────────────────────────────────

def test_sections_are_grouped_in_order_each_with_a_score_and_failures_first():
    setup(entry("A", "s2", "low"), entry("B", "s1", "high"), entry("C", "s1", "critical"), entry("D", "s1", "medium"))
    fs = [m.make("A", "pass", "x"), m.make("B", "pass", "x"), m.make("C", "fail", "x"), m.make("D", "warn", "x")]
    secs = m.sections_of(fs)
    assert [s["key"] for s in secs] == ["s1", "s2"]
    first = secs[0]
    assert [f["id"] for f in first["findings"]] == ["C", "D", "B"]
    assert first["score"] == round(100 * (4 + 1) / (8 + 4 + 2)) and first["tally"]["fail"] == 1 and first["tally"]["warn"] == 1
    assert secs[1]["score"] == 100


def test_a_section_with_no_check_run_is_left_out():
    setup(entry("A", "s1"))
    assert [s["key"] for s in m.sections_of([m.make("A", "pass", "x")])] == ["s1"]


def test_page_summary_counts_what_could_not_be_read_and_the_failures():
    setup(entry("A", severity="critical"), entry("B", severity="high"), entry("C", severity="low"), entry("D", severity="low"))
    s = m.page_summary([m.make("A", "fail", "x"), m.make("B", "fail", "x"), m.make("C", "pass", "x"), m.make("D", "unknown", "x")])
    assert (s["critical"], s["high"], s["judged"], s["unknown"]) == (1, 1, 3, 1)
    assert s["score"] == round(100 * 1 / (8 + 4 + 1))
    assert s["tally"]["total"] == 4


def test_a_page_where_nothing_could_be_judged_has_no_score_or_grade():
    setup(entry("A"))
    s = m.page_summary([m.unknown("A", "refused"), m.not_applicable("A", "n/a")])
    assert s["score"] is None and s["grade"] is None


def test_the_grade_is_held_at_c_when_a_critical_check_failed_or_much_was_unread():
    setup(entry("A", severity="critical"), entry("B", severity="high"), entry("C", severity="high"), entry("D", severity="high"),
          entry("E", severity="high"), entry("F", severity="high"), entry("G", severity="high"), entry("H", severity="high"))
    fine = [m.make(c, "pass", "x") for c in "BCDEFGH"]
    assert m.page_summary(fine + [m.make("A", "pass", "x")])["grade"] == "A"
    down = m.page_summary(fine + [m.make("A", "fail", "x")])
    assert down["grade"] == "C" and "a critical check failed" in down["capReasons"]
    unread = m.page_summary([m.make("B", "pass", "x")] * 2 + [m.unknown("C", "r")] * 2)
    assert unread["grade"] == "C" and any("could not be read" in r for r in unread["capReasons"])


def test_a_finding_can_hold_the_grade_down_with_its_own_reason():
    setup(entry("A", severity="low"), entry("B", severity="low"))
    s = m.page_summary([m.make("A", "pass", "x"), m.make("B", "fail", "x", caps="closed to AdsBot")])
    assert "closed to AdsBot" in s["capReasons"]


# ── The account ─────────────────────────────────────────────────────────────

def test_the_account_score_weights_each_pages_score_by_its_spend():
    pages = [{"score": 100, "spend": 900.0}, {"score": 0, "spend": 100.0}]
    assert m.weighted_score(pages) == 90


def test_pages_with_no_score_are_left_out_and_no_spend_counts_each_page_once():
    assert m.weighted_score([{"score": None, "spend": 500.0}, {"score": 80, "spend": 10.0}]) == 80
    assert m.weighted_score([{"score": 60, "spend": 0.0}, {"score": 80, "spend": 0.0}]) == 70
    assert m.weighted_score([{"score": None, "spend": 5.0}]) is None
    assert m.weighted_score([]) is None


def test_a_negative_spend_is_not_a_negative_weight():
    assert m.weighted_score([{"score": 100, "spend": -50.0}, {"score": 50, "spend": 50.0}]) == 50


def test_fix_first_is_one_row_per_check_ordered_by_severity_then_failure_then_spend():
    setup(entry("A", severity="critical"), entry("B", severity="high"), entry("C", severity="high"), entry("D", severity="low"))
    pages = [
        {"url": "u1", "name": "one", "spend": 100.0, "findings": [m.make("B", "warn", "b1"), m.make("D", "fail", "d1"), m.make("A", "pass", "x")]},
        {"url": "u2", "name": "two", "spend": 900.0, "findings": [m.make("B", "warn", "b2"), m.make("C", "fail", "c2"), m.make("A", "fail", "a2")]},
    ]
    got = m.fix_first(pages)
    assert [r["id"] for r in got] == ["A", "C", "B", "D"]
    b = next(r for r in got if r["id"] == "B")
    assert b["result"] == "warn" and b["spend"] == 1000.0 and b["warned"] == 2 and b["failed"] == 0 and [x["url"] for x in b["pages"]] == ["u2", "u1"] and b["found"] == "b2"
    assert m.fix_first(pages, limit=2) == got[:2]


def test_a_check_that_fails_on_some_pages_and_warns_on_others_is_a_failure_led_by_the_failing_pages():
    setup(entry("A", severity="high"))
    pages = [{"url": "big-warn", "name": "bw", "spend": 900.0, "findings": [m.make("A", "warn", "warned")]},
             {"url": "small-fail", "name": "sf", "spend": 10.0, "findings": [m.make("A", "fail", "failed")]}]
    row = m.fix_first(pages)[0]
    assert row["result"] == "fail" and row["failed"] == 1 and row["warned"] == 1 and row["found"] == "failed" and [x["url"] for x in row["pages"]] == ["small-fail", "big-warn"]
    assert row["spend"] == 910.0


def test_a_findings_own_severity_is_what_counts_and_the_row_takes_the_most_severe():
    setup(entry("A", severity="low"))
    pages = [{"url": "u1", "name": "n1", "spend": 1.0, "findings": [m.make("A", "fail", "x")]}, {"url": "u2", "name": "n2", "spend": 1.0, "findings": [m.make("A", "fail", "x", severity="critical")]}]
    assert m.fix_first(pages)[0]["severity"] == "critical"


def test_a_row_names_only_the_first_few_pages_but_counts_them_all():
    setup(entry("A", severity="high"))
    pages = [{"url": "u%d" % i, "name": "n%d" % i, "spend": float(100 - i), "findings": [m.make("A", "fail", "x")]} for i in range(20)]
    row = m.fix_first(pages)[0]
    assert len(row["pages"]) == m.FIX_PAGES and row["failed"] == 20 and row["spend"] == sum(100.0 - i for i in range(20)) and row["pages"][0]["url"] == "u0"


def test_fix_first_leaves_out_passes_and_what_could_not_be_checked():
    setup(entry("A"))
    pages = [{"url": "u", "findings": [m.make("A", "pass", "x"), m.unknown("A", "r"), m.not_applicable("A", "n")]}]
    assert m.fix_first(pages) == []
