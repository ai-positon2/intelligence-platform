"""Landing Page & Relevancy Check: every check on one page (tracker/paid_lp_checks.py) and the checklist document."""
import os

import pytest

from tests.lp_fakes import blocked_fetch, page
from tracker import paid_lp_checks as checks
from tracker import paid_lp_docs as docs
from tracker import paid_lp_model as model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "landing-page-check-checks.md")


def test_every_catalog_entry_is_run_exactly_once():
    ids = [cid for cid, _fn in checks.CHECKS]
    assert len(ids) == len(set(ids)) == len(model.CATALOG) == 39 and set(ids) == set(model.CATALOG)
    assert checks.GOOGLE_ONLY | checks.META_ONLY <= set(ids) and not checks.GOOGLE_ONLY & checks.META_ONLY


def test_a_good_page_scores_a_hundred_and_every_section_is_there_in_order():
    found = checks.run(page())
    assert len(found) == 35
    secs = model.sections_of(found)
    assert [s["key"] for s in secs] == ["reach", "match", "path", "mobile", "speed", "trust"]
    s = model.page_summary(found)
    assert s["score"] == 100 and s["grade"] == "A" and s["unknown"] == 0


def test_findings_come_in_section_order():
    order = {k: v["order"] for k, v in model.SECTIONS.items()}
    keys = [order[f["section"]] for f in checks.run(page())]
    assert keys == sorted(keys)


def test_a_page_that_refused_us_keeps_what_google_said_and_marks_the_rest_could_not_check():
    found = checks.run(page(fetch=blocked_fetch()))
    by = {f["id"]: f for f in found}
    assert by["reach_status"]["result"] == "unknown" and by["trust_privacy"]["result"] == "unknown"
    assert by["mobile_conv_gap"]["result"] == "pass" and by["match_lp_experience"]["result"] == "pass" and by["speed_google"]["result"] == "pass"
    s = model.page_summary(found)
    assert s["grade"] == "C" and any("could not be read" in r for r in s["capReasons"]), s           # most of the page could not be seen, so no A


def test_a_check_that_raises_says_it_could_not_run_and_the_others_still_run(monkeypatch):
    def boom(ctx):
        raise RuntimeError("x")
    monkeypatch.setattr(checks, "CHECKS", [("reach_https", boom)] + [x for x in checks.CHECKS if x[0] != "reach_https"])
    found = checks.run(page())
    by = {f["id"]: f for f in found}
    assert by["reach_https"]["result"] == "unknown" and "could not run" in by["reach_https"]["found"] and by["reach_status"]["result"] == "pass"
    assert len(found) == 35


def test_no_check_text_has_an_em_or_en_dash():
    for spec in model.CATALOG.values():
        for key in ("title", "why", "fix"):
            assert "—" not in spec[key] and "–" not in spec[key], spec["id"]
    for f in checks.run(page()):
        assert "—" not in f["found"] and "–" not in f["found"]


# ── The document ────────────────────────────────────────────────────────────

def test_the_document_is_what_the_catalog_says():
    assert open(DOC, encoding="utf-8").read() == docs.markdown(), "run the command in tracker/paid_lp_docs.py to refresh docs/landing-page-check-checks.md"


def test_every_check_is_in_the_document_once_with_its_severity_why_and_fix():
    text = docs.markdown()
    for cid, spec in model.CATALOG.items():
        assert text.count("**%s** " % cid) == 1
        assert "**%s** %s (%s)" % (cid, spec["title"], spec["severity"]) in text and "Why it matters: " + spec["why"] in text and "What to do: " + spec["fix"] in text


def test_the_headings_count_the_checks_of_each_section():
    text = docs.markdown()
    for key, spec in model.SECTIONS.items():
        n = sum(1 for c in model.CATALOG.values() if c["section"] == key)
        assert "## %s (%d)" % (spec["label"], n) in text
    assert "39 checks in 6 sections." in text
