"""Tests for _cpi_growth_pair, the one place growth6/growth12 are read off an
Apollo org record before every renderer (pmGrowth in the JS, _cpi_export_percent
here) multiplies them by 100 on the belief that Apollo sends a fraction (0.19 =
19% growth), not a whole percent.

That belief was settled from repo evidence -- fixtures recording 0.19 and 0.08,
and the older External Usage export multiplying the same field by 100 since
before this page existed -- not a live probe: the free Apollo endpoint this page
runs on strips org firmographics down to id/name/domain, and this repo has no
production key, so it could not be checked live from the sandbox (see the
CONTEXT doc's open items). _cpi_growth_pair does not change that belief or the
values it passes through; it adds the one thing a repo-evidence-only convention
was missing: a way to notice, out in production with a real key, if the belief
was ever wrong. It does that with a log line, not a runtime change, because
correcting the number itself would require knowing WHICH way the convention is
wrong for that call, and it never trusts a guess about that.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

import app as appmod  # noqa: E402


def _org(g6=None, g12=None, name="Tealium"):
    return {"name": name,
            "organization_headcount_six_month_growth": g6,
            "organization_headcount_twelve_month_growth": g12}


# ── The values pass through untouched ────────────────────────────────────────

def test_ordinary_fractions_pass_through_unchanged():
    assert appmod._cpi_growth_pair(_org(0.08, 0.19)) == (0.08, 0.19)


def test_missing_values_pass_through_as_none():
    assert appmod._cpi_growth_pair(_org(None, None)) == (None, None)


def test_a_none_org_does_not_crash():
    assert appmod._cpi_growth_pair(None) == (None, None)


def test_a_negative_fraction_passes_through_unchanged():
    """Shrinking headcount is a real, expected shape for this field."""
    assert appmod._cpi_growth_pair(_org(-0.12, -0.05)) == (-0.12, -0.05)


def test_a_fraction_at_the_edge_of_the_warning_threshold_is_not_flagged(caplog):
    """1.0 is a real fraction (100% growth, headcount doubled), not a sign the
    convention has broken -- the check must not cry wolf on ordinary hyper-growth."""
    with caplog.at_level(logging.WARNING):
        appmod._cpi_growth_pair(_org(1.0, 0.5))
    assert not caplog.records


# ── The self-check the convention was missing ────────────────────────────────

def test_a_whole_looking_percent_is_flagged(caplog):
    """A round number of magnitude 2+ looks like Apollo sent 45 meaning 45%,
    not 0.45 -- the one shape a fraction-only convention cannot self-correct
    for, so it must not pass silently."""
    with caplog.at_level(logging.WARNING):
        got = appmod._cpi_growth_pair(_org(45, 0.19))
    assert got == (45, 0.19), "flagging is a side effect, never a correction"
    assert any("growth" in r.message.lower() for r in caplog.records)
    assert any("tealium" in r.message.lower() for r in caplog.records)


def test_the_other_field_is_checked_independently(caplog):
    with caplog.at_level(logging.WARNING):
        appmod._cpi_growth_pair(_org(0.08, -12))
    assert len(caplog.records) == 1
    assert "twelve_month" in caplog.records[0].message


def test_both_fields_can_be_flagged_at_once(caplog):
    with caplog.at_level(logging.WARNING):
        appmod._cpi_growth_pair(_org(30, 45))
    assert len(caplog.records) == 2


def test_an_unparseable_value_is_left_alone_and_not_flagged(caplog):
    with caplog.at_level(logging.WARNING):
        got = appmod._cpi_growth_pair(_org("n/a", 0.19))
    assert got == ("n/a", 0.19)
    assert not caplog.records


# ── Wired into every place growth6/growth12 reach a renderer ────────────────

def test_the_org_card_shape_uses_the_shared_helper():
    row = appmod._apollo_org_normalize(_org(0.08, 0.19))
    assert (row["growth6"], row["growth12"]) == (0.08, 0.19)


def test_the_company_search_row_uses_the_shared_helper():
    row = appmod._cpi_company_row(_org(0.08, 0.19))
    assert (row["growth6"], row["growth12"]) == (0.08, 0.19)


def test_the_employer_facts_merged_onto_a_person_use_the_shared_helper():
    facts = appmod._cpi_employer_facts(_org(0.08, 0.19))
    assert (facts["organization_growth6"], facts["organization_growth12"]) == (0.08, 0.19)
