"""Account Audit: what a finding is and how results are scored (tracker/paid_audit_model.py)."""
import pytest

from tracker import paid_audit_model as m


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


def setup(*entries, sections=(("s1", "First", "google", 1), ("s2", "Second", "google", 2), ("m1", "Meta one", "meta", 1))):
    for s in sections:
        m.section(*s)
    m.register(list(entries))


# ── The catalog ─────────────────────────────────────────────────────────────

def test_register_rejects_missing_text_and_unknown_values():
    m.section("s1", "First", "google", 1)
    for bad in (entry(why=""), entry(fix="  "), entry(title=""), entry(severity="urgent"), entry(section="nope")):
        with pytest.raises(m.CatalogError):
            m.register([bad])
    with pytest.raises(m.CatalogError):
        m.register([{"id": "T-2", "section": "s1", "title": "t", "severity": "low", "why": "w"}])       # no fix


def test_register_allows_the_same_entry_twice_but_not_a_different_one():
    m.section("s1", "First", "google", 1)
    m.register([entry()])
    m.register([entry()])
    with pytest.raises(m.CatalogError):
        m.register([entry(title="Another title")])


def test_section_rules():
    m.section("s1", "First", "google", 1)
    m.section("s1", "First", "google", 1)
    with pytest.raises(m.CatalogError):
        m.section("s1", "Renamed", "google", 1)
    with pytest.raises(m.CatalogError):
        m.section("s9", "Nine", "tiktok", 1)


# ── A finding ───────────────────────────────────────────────────────────────

def test_make_joins_the_catalog_with_what_was_found():
    setup(entry())
    f = m.make("T-1", "fail", "  3 of 4 campaigns have no location.  ", items=["A", "B", "C"])
    assert f["id"] == "T-1" and f["section"] == "s1" and f["platform"] == "google"
    assert f["title"] == "Title T-1" and f["severity"] == "high" and f["result"] == "fail"
    assert f["found"] == "3 of 4 campaigns have no location."
    assert f["why"] == "Because." and f["fix"] == "Do this."
    assert f["items"] == ["A", "B", "C"] and f["count"] == 3
    assert "detail" not in f
    assert m.make("T-1", "pass", "ok", detail={"x": 1})["detail"] == {"x": 1}
    assert "detail" not in m.make("T-1", "pass", "ok", detail={})


def test_make_caps_the_items_but_keeps_the_true_count():
    setup(entry())
    f = m.make("T-1", "fail", "Many.", items=["i%d" % n for n in range(30)])
    assert len(f["items"]) == m.MAX_ITEMS == 12 and f["count"] == 30
    g = m.make("T-1", "fail", "Many.", items=["a", "b"], count=500)
    assert g["items"] == ["a", "b"] and g["count"] == 500
    h = m.make("T-1", "fail", "Many.", items=["a", "b", "c"], count=1)
    assert h["count"] == 3                                    # a count below the items named is a slip, never shown


def test_make_can_raise_the_severity_for_one_finding():
    setup(entry(severity="medium"))
    assert m.make("T-1", "fail", "x")["severity"] == "medium"
    assert m.make("T-1", "fail", "x", severity="critical")["severity"] == "critical"
    with pytest.raises(m.CatalogError):
        m.make("T-1", "fail", "x", severity="huge")


def test_make_refuses_unknown_checks_and_results():
    setup(entry())
    with pytest.raises(m.CatalogError):
        m.make("T-9", "pass", "x")
    with pytest.raises(m.CatalogError):
        m.make("T-1", "maybe", "x")


def test_unknown_and_not_applicable_helpers():
    setup(entry())
    u = m.unknown("T-1", "the conversions could not be read.")
    assert u["result"] == "unknown" and u["found"] == "Could not be checked: the conversions could not be read"
    n = m.not_applicable("T-1", "No shopping campaigns.")
    assert n["result"] == "na" and n["found"] == "No shopping campaigns."


# ── Scoring ─────────────────────────────────────────────────────────────────

def f(cid, result, severity="high", section="s1", **kw):
    setup_needed = cid not in m.CATALOG
    if setup_needed:
        if section not in m.SECTIONS:
            m.section(section, section.upper(), "meta" if section.startswith("m") else "google", 1)
        m.register([entry(cid, section, severity)])
    return m.make(cid, result, "found " + cid, severity=severity, **kw)


def test_score_weights_by_severity_and_halves_a_warning():
    fs = [f("A", "pass", "critical"), f("B", "fail", "low")]
    assert m._score(fs) == round(100 * 8 / 9)
    fs = [f("C", "warn", "high"), f("D", "pass", "high")]
    assert m._score(fs) == round(100 * (2 + 4) / 8)           # a warning earns half of 4
    assert m._score([f("E", "fail", "critical"), f("F", "pass", "low")]) == round(100 * 1 / 9)


def test_unknown_and_na_are_left_out_of_the_score_and_counted():
    fs = [f("A", "pass", "high"), f("B", "unknown", "critical"), f("C", "na", "critical")]
    assert m._score(fs) == 100
    t = m.tally(fs)
    assert t == {"pass": 1, "warn": 0, "fail": 0, "na": 1, "unknown": 1, "total": 3}
    assert m._score([f("D", "unknown"), f("E", "na")]) is None
    assert m.grade_of(None) is None


def test_grades_and_the_cap_for_a_failed_critical_check():
    assert [m.grade_of(s) for s in (100, 90, 89, 75, 74, 60, 59, 40, 39, 0)] == ["A", "A", "B", "B", "C", "C", "D", "D", "F", "F"]
    crit = [f("A", "fail", "critical")]
    assert m.grade_of(95, crit) == "C" and m.grade_of(80, crit) == "C"
    assert m.grade_of(55, crit) == "D"                        # the cap only lowers, never raises
    assert m.grade_of(95, [f("B", "fail", "high")]) == "A"
    assert m.grade_of(95, [f("C", "warn", "critical")]) == "A"   # a warning is not a failed check
    assert m.grade_of(95, [f("D", "unknown", "critical")] + [f("Q%d" % i, "pass", "low") for i in range(9)]) == "A"       # one unread check in ten is not a reason


def test_sections_are_grouped_in_order_with_their_own_scores():
    m.section("s2", "Second", "google", 2)             # declared out of order on purpose: the order value decides, not the declaration
    m.section("m1", "Meta one", "meta", 1)
    m.section("s1", "First", "google", 1)
    m.register([entry("A", "s1", "high"), entry("B", "s2", "high"), entry("C", "m1", "low"), entry("D", "s1", "low")])
    fs = [m.make("C", "pass", "c"), m.make("B", "fail", "b"), m.make("D", "fail", "d"), m.make("A", "pass", "a")]
    secs = m.sections_of(fs)
    assert [s["key"] for s in secs] == ["s1", "s2", "m1"]            # Google first, then Meta, each in its own order
    assert secs[0]["score"] == round(100 * 4 / 5) and secs[0]["grade"] == "B"
    assert [x["id"] for x in secs[0]["findings"]] == ["D", "A"]       # the failure first
    assert secs[1]["score"] == 0 and secs[1]["grade"] == "F"
    assert secs[2]["score"] == 100 and secs[2]["tally"]["pass"] == 1


def test_a_section_with_no_findings_is_left_out():
    m.section("s1", "First", "google", 1)
    m.section("s2", "Second", "google", 2)
    m.register([entry("A", "s1")])
    assert [s["key"] for s in m.sections_of([m.make("A", "pass", "a")])] == ["s1"]
    assert m.sections_of([]) == []


def test_findings_in_a_section_order_by_result_severity_then_how_many_things_they_touch():
    m.section("s1", "First", "google", 1)
    m.register([entry(c, "s1", s) for c, s in (("P", "low"), ("U", "low"), ("W", "high"), ("F1", "medium"), ("F2", "critical"), ("F3", "medium"), ("N", "low"))])
    fs = [m.make("P", "pass", "x"), m.make("U", "unknown", "x"), m.make("W", "warn", "x"), m.make("F1", "fail", "x", items=["a"]),
          m.make("F2", "fail", "x"), m.make("F3", "fail", "x", items=["a", "b", "c"]), m.make("N", "na", "x")]
    assert [x["id"] for x in m.sections_of(fs)[0]["findings"]] == ["F2", "F3", "F1", "W", "U", "P", "N"]


def test_fix_first_is_the_failures_and_warnings_by_severity_then_failure_before_warning():
    fs = [f("A", "fail", "low"), f("B", "warn", "critical"), f("C", "fail", "critical"), f("D", "pass", "critical"), f("E", "unknown", "critical"),
          f("F", "fail", "high", items=["a", "b"]), f("G", "fail", "high"), f("H", "warn", "high")]
    ids = [x["id"] for x in m.fix_first(fs)]
    assert ids == ["C", "B", "F", "G", "H", "A"]
    assert len(m.fix_first([f("X%d" % i, "fail") for i in range(15)])) == 10
    assert len(m.fix_first([f("Y%d" % i, "fail") for i in range(15)], limit=3)) == 3
    assert m.fix_first([f("Z", "pass")]) == []


def test_summarise_counts_what_could_not_be_checked_and_splits_by_platform():
    m.section("s1", "First", "google", 1)
    m.section("m1", "Meta one", "meta", 1)
    m.register([entry("A", "s1", "critical"), entry("B", "s1", "high"), entry("C", "s1", "high"), entry("D", "m1", "low"), entry("E", "s1", "critical")])
    fs = [m.make("A", "fail", "a"), m.make("B", "unknown", "b"), m.make("C", "pass", "c"), m.make("D", "pass", "d")]
    fs.append(m.make("E", "pass", "e"))                 # a critical check that passed is not a critical problem
    fs[1:1] = []
    s = m.summarise(fs)
    assert s["score"] == round(100 * 13 / 21) and s["grade"] == "C"
    assert s["judged"] == 4 and s["unknown"] == 1 and s["unknownShare"] == 20
    assert s["critical"] == 1 and s["high"] == 0
    assert s["platforms"]["google"]["score"] == round(100 * 12 / 20) and s["platforms"]["meta"]["score"] == 100
    assert s["tally"]["total"] == 5
    only = m.summarise([m.make("D", "pass", "d")])
    assert list(only["platforms"]) == ["meta"] and only["unknownShare"] == 0
    empty = m.summarise([])
    assert empty["score"] is None and empty["grade"] is None and empty["unknownShare"] == 0 and "platforms" not in empty


def test_the_grade_cap_shows_in_the_summary_and_the_section():
    m.section("s1", "First", "google", 1)
    m.register([entry("A", "s1", "critical")] + [entry("P%d" % i, "s1", "low") for i in range(40)])
    fs = [m.make("A", "fail", "a")] + [m.make("P%d" % i, "pass", "p") for i in range(40)]
    assert m.summarise(fs)["score"] == 83 and m.summarise(fs)["grade"] == "C"
    assert m.sections_of(fs)[0]["grade"] == "C"


# ── Wording and arithmetic ──────────────────────────────────────────────────

def test_share_is_exact_and_pct_is_the_rounded_figure_shown():
    assert m.share(1, 4) == 25.0 and m.share(0, 5) == 0.0 and m.share(5, 0) == 0.0
    assert m.share(249, 1000) == 24.9 and m.pct(249, 1000) == 25 and m.pct(1, 3) == 33 and m.pct(2, 3) == 67 and m.pct(1, 0) == 0


def test_plural_and_verb_agree_with_the_count():
    assert [m.plural(n, "action") for n in (0, 1, 2)] == ["0 actions", "1 action", "2 actions"]
    assert [m.verb(n, "count") for n in (0, 1, 2)] == ["count", "counts", "count"]


def test_names_takes_a_key_from_dicts_and_text_from_anything_else():
    assert m.names([{"name": "A"}, {"name": "B"}]) == ["A", "B"] and m.names(["x", 3]) == ["x", "3"]
    assert m.names([{"title": "T"}], key="title") == ["T"] and m.names([]) == []


# ── What holds a grade down ─────────────────────────────────────────────────

def many(passed, unread, **kw):
    return [f("P%d" % i, "pass", "low") for i in range(passed)] + [f("U%d" % i, "unknown", "low") for i in range(unread)]


def test_a_grade_is_held_at_c_when_thirty_percent_or_more_of_the_checks_could_not_be_read():
    assert m.cap_reasons(many(7, 3)) == ["30% of the checks could not be read"] and m.grade_of(100, many(7, 3)) == "C"
    assert m.cap_reasons(many(8, 2)) == [] and m.grade_of(100, many(8, 2)) == "A"                      # 20 percent is not
    assert m.cap_reasons(many(69, 31)) == ["31% of the checks could not be read"] and m.cap_reasons(many(71, 29)) == []
    assert m.cap_reasons(many(1, 9)) == ["90% of the checks could not be read"]
    assert m.grade_of(100, many(3, 7)) == "C" and m.grade_of(50, many(3, 7)) == "D"                     # a cap only lowers
    assert m.cap_reasons([]) == [] and m.cap_reasons(many(5, 0)) == []


def test_a_finding_can_hold_the_grade_with_a_reason_and_the_reason_is_said_once():
    setup = [entry("N-1", "s1", "high"), entry("N-2", "s1", "high")]
    m.section("s1", "First", "google", 1)
    m.register(setup)
    a = m.make("N-1", "warn", "No campaign is enabled.", caps="the account is not advertising")
    b = m.make("N-2", "warn", "No campaign is live.", caps="the account is not advertising")
    assert a["caps"] == "the account is not advertising" and "caps" not in m.make("N-1", "warn", "x")
    p = many(40, 0)
    assert m.cap_reasons(p + [a, b]) == ["the account is not advertising"] and m.grade_of(95, p + [a, b]) == "C"
    assert m.grade_of(95, p + [m.make("N-1", "warn", "x")]) == "A"
    both = m.cap_reasons([f("C1", "fail", "critical")] + many(4, 6) + [a])
    assert both == ["a critical check failed", "50% of the checks could not be read", "the account is not advertising"]


def test_the_summary_and_each_section_say_why_the_grade_is_held():
    m.section("s1", "First", "google", 1)
    m.section("s2", "Second", "google", 2)
    m.register([entry("A", "s1", "low"), entry("B", "s2", "low"), entry("C", "s2", "low"), entry("D", "s2", "low")])
    fs = [m.make("A", "pass", "a")] + [m.make("B", "pass", "b"), m.make("C", "unknown", "c"), m.make("D", "unknown", "d")]
    s = m.summarise(fs)
    assert s["capReasons"] == ["50% of the checks could not be read"] and s["grade"] == "C" and s["score"] == 100
    secs = {x["key"]: x for x in m.sections_of(fs)}
    assert secs["s1"]["grade"] == "A" and secs["s2"]["grade"] == "C"                                    # the section that was mostly unread is held, the other is not
    assert m.summarise([m.make("A", "pass", "a")])["capReasons"] == []
