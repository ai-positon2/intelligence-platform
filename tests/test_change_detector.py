"""Tests for change_detector.py — pure logic, no I/O."""

import pytest
from tracker.change_detector import detect_changes, ChangeEvent, is_within_age_limit

_BASE_CONFIG = {
    "signals": {
        "headcount_growth_pct": 15,
        "headcount_shrink_pct": 10,
        "job_posting_spike_pct": 30,
        "intent_score_spike_points": 20,
        "c_suite_titles": ["CEO", "CFO", "CTO", "Chief", "VP", "Vice President", "President"],
        "news_high_keywords": ["acquired", "acquisition", "merger", "IPO", "going public", "S-1"],
        "news_medium_keywords": ["funding", "raises", "expansion", "new office", "appoints", "hires"],
    }
}

_BASE_OLD = {
    "apollo_id": "abc123",
    "employees": 200,
    "annual_revenue": 10_000_000.0,
    "hq_city": "Austin",
    "hq_state": "TX",
    "latest_funding_type": "Series B",
    "latest_funding_amount": 20_000_000.0,
    "last_raised_at": "2023-01-01",
    "total_funding": 20_000_000.0,
    "open_job_count": 10,
    "tech_stack": ["Salesforce", "HubSpot"],
    "intent_score_1": 50.0,
    "intent_topic_1": "CRM Software",
    "crm_stage": "Cold",
    "retail_locations": 2,
    "subsidiary_of": "",
    "leadership_json": [
        {"id": "p1", "name": "Alice Smith", "title": "CEO", "linkedin_url": None},
        {"id": "p2", "name": "Bob Jones", "title": "VP of Sales", "linkedin_url": None},
    ],
}


def _new(overrides: dict) -> dict:
    base = dict(_BASE_OLD)
    base["leadership"] = list(_BASE_OLD["leadership_json"])
    base.pop("leadership_json", None)
    base.update(overrides)
    return base


def test_no_changes_returns_empty():
    new = _new({})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert events == []


def test_csuite_join_detected():
    new = _new({
        "leadership": [
            *_BASE_OLD["leadership_json"],
            {"id": "p3", "name": "Carol Lee", "title": "CFO", "linkedin_url": None},
        ]
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    joins = [e for e in events if e.signal_type == "C-Suite Join"]
    assert len(joins) == 1
    assert joins[0].severity == "HIGH"
    assert "Carol Lee" in joins[0].headline


def test_csuite_exit_detected():
    new = _new({
        "leadership": [
            {"id": "p2", "name": "Bob Jones", "title": "VP of Sales", "linkedin_url": None},
        ]
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    exits = [e for e in events if e.signal_type == "C-Suite Exit"]
    assert len(exits) == 1
    assert exits[0].severity == "HIGH"
    assert "Alice Smith" in exits[0].headline


def test_headcount_growth_detected():
    new = _new({"employees": 240})  # 20% growth > 15% threshold
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    grow = [e for e in events if e.signal_type == "Headcount Growth"]
    assert len(grow) == 1
    assert grow[0].severity == "MEDIUM"
    assert "200" in grow[0].previous_value
    assert "240" in grow[0].new_value


def test_headcount_growth_below_threshold_not_detected():
    new = _new({"employees": 210})  # 5% growth < 15% threshold
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert not any(e.signal_type == "Headcount Growth" for e in events)


def test_headcount_shrink_detected():
    new = _new({"employees": 170})  # 15% drop > 10% threshold
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type == "Headcount Shrink" for e in events)


def test_new_location_detected():
    new = _new({"city": "San Francisco", "state": "CA"})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    locs = [e for e in events if e.signal_type == "New Location"]
    assert len(locs) == 1
    assert locs[0].severity == "MEDIUM"


def test_same_location_no_event():
    new = _new({"city": "Austin", "state": "TX"})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert not any(e.signal_type == "New Location" for e in events)


def test_funding_round_detected():
    new = _new({"latest_funding_type": "Series C", "latest_funding_amount": 50_000_000.0, "last_raised_at": "2026-04-01"})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    funding = [e for e in events if e.signal_type == "Funding Round"]
    assert len(funding) == 1
    assert funding[0].severity == "HIGH"


def test_funding_stage_detected_separately():
    new = _new({"latest_funding_type": "Series C", "last_raised_at": "2026-04-01"})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    types = {e.signal_type for e in events}
    assert "Funding Round" in types or "New Funding Stage" in types


def test_job_posting_spike_detected():
    new = _new({"open_job_count": 14})  # 40% spike > 30% threshold
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type == "Job Posting Spike" for e in events)


def test_job_posting_below_threshold_not_detected():
    new = _new({"open_job_count": 12})  # 20% spike < 30% threshold
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert not any(e.signal_type == "Job Posting Spike" for e in events)


def test_intent_score_spike_detected():
    new = _new({"intent_score_1": 75.0})  # +25 pts > 20 threshold
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type == "Intent Score Spike" for e in events)


def test_intent_topic_change_detected():
    new = _new({"intent_topic_1": "Marketing Automation"})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type == "Intent Topic Change" for e in events)


def test_tech_stack_addition_detected():
    new = _new({"tech_stack": ["Salesforce", "HubSpot", "Marketo"]})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type == "Tech Stack Addition" for e in events)


def test_tech_stack_removal_detected():
    new = _new({"tech_stack": ["Salesforce"]})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type == "Tech Stack Removal" for e in events)


def test_crm_stage_change_detected():
    new = _new({"crm_stage": "Qualified"})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type == "CRM Stage Change" for e in events)


def test_no_previous_snapshot_returns_empty():
    events = detect_changes(None, _new({}), _BASE_CONFIG)
    assert events == []


def test_multiple_signals_in_one_diff():
    new = _new({
        "employees": 250,
        "open_job_count": 15,
        "latest_funding_type": "Series C",
        "latest_funding_amount": 60_000_000.0,
        "last_raised_at": "2026-04-01",
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    types = {e.signal_type for e in events}
    assert "Headcount Growth" in types
    assert "Job Posting Spike" in types
    assert "Funding Round" in types or "New Funding Stage" in types


def test_acquisition_from_news():
    new = _new({
        "news_articles": [{"title": "Acme Corp acquired by BigCo", "summary": "acquisition deal closed"}]
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type == "Acquisition / M&A" for e in events)


def test_csuite_join_dedup_more_than_3_collapses():
    """More than 3 C-suite joins in one run → single grouped event."""
    new = _new({
        "leadership": [
            *_BASE_OLD["leadership_json"],
            {"id": "p3", "full_name": "Carol Lee", "title": "CFO", "linkedin_url": None},
            {"id": "p4", "full_name": "Dave Kim", "title": "CTO", "linkedin_url": None},
            {"id": "p5", "full_name": "Eva Stone", "title": "Chief Marketing Officer", "linkedin_url": None},
            {"id": "p6", "full_name": "Frank Wu", "title": "Chief Revenue Officer", "linkedin_url": None},
        ]
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG, company_name="Acme Corp")
    joins = [e for e in events if e.signal_type == "C-Suite Join"]
    assert len(joins) == 1
    assert "4" in joins[0].headline
    assert "Acme Corp" in joins[0].headline


def test_csuite_join_dedup_exactly_3_stays_individual():
    """Exactly 3 C-suite joins should NOT be collapsed."""
    new = _new({
        "leadership": [
            *_BASE_OLD["leadership_json"],
            {"id": "p3", "full_name": "Carol Lee", "title": "CFO", "linkedin_url": None},
            {"id": "p4", "full_name": "Dave Kim", "title": "CTO", "linkedin_url": None},
            {"id": "p5", "full_name": "Eva Stone", "title": "Chief Marketing Officer", "linkedin_url": None},
        ]
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    joins = [e for e in events if e.signal_type == "C-Suite Join"]
    assert len(joins) == 3


def test_csuite_join_uses_full_name():
    """full_name field is preferred over name for C-suite join display."""
    new = _new({
        "leadership": [
            *_BASE_OLD["leadership_json"],
            {"id": "p3", "full_name": "Carol Lee", "name": "WRONG", "title": "CFO", "linkedin_url": None},
        ]
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    joins = [e for e in events if e.signal_type == "C-Suite Join"]
    assert len(joins) == 1
    assert "Carol Lee" in joins[0].headline
    assert "WRONG" not in joins[0].headline


# ── is_within_age_limit tests ───────────────────────────────────────────────

def test_is_within_age_limit_recent_iso():
    assert is_within_age_limit("2026-04-01", 90) is True


def test_is_within_age_limit_old_iso():
    assert is_within_age_limit("2023-01-01", 90) is False


def test_is_within_age_limit_empty_allows():
    """No date → cannot determine age → allow (do not suppress)."""
    assert is_within_age_limit("", 90) is True


def test_is_within_age_limit_none_allows():
    assert is_within_age_limit(None, 90) is True  # type: ignore[arg-type]


def test_is_within_age_limit_rss_format():
    """RFC 2822 date format used by Google News RSS."""
    assert is_within_age_limit("Mon, 05 May 2026 10:00:00 GMT", 90) is True
    assert is_within_age_limit("Mon, 01 Jan 2023 10:00:00 GMT", 90) is False


# ── 90-day signal gate tests ─────────────────────────────────────────────────

def test_csuite_join_with_old_start_date_suppressed():
    """C-suite join with a start_date older than 90 days must not fire."""
    new = _new({
        "leadership": [
            *_BASE_OLD["leadership_json"],
            {"id": "p3", "full_name": "Carol Lee", "title": "CFO", "linkedin_url": None, "start_date": "2022-01-01"},
        ]
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert not any(e.signal_type == "C-Suite Join" for e in events)


def test_csuite_join_with_recent_start_date_fires():
    """C-suite join with a start_date within 90 days must fire."""
    new = _new({
        "leadership": [
            *_BASE_OLD["leadership_json"],
            {"id": "p3", "full_name": "Carol Lee", "title": "CFO", "linkedin_url": None, "start_date": "2026-04-01"},
        ]
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type == "C-Suite Join" for e in events)


def test_funding_with_stale_raised_at_suppressed():
    """Funding stage change with last_raised_at older than 90 days must not fire."""
    new = _new({"latest_funding_type": "Series C", "last_raised_at": "2022-06-01"})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert not any(e.signal_type in ("Funding Round", "New Funding Stage") for e in events)


def test_funding_without_raised_at_fires():
    """Funding stage change with no last_raised_at must still fire (unknown date → allow)."""
    new = _new({"latest_funding_type": "Series C", "last_raised_at": ""})
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type in ("Funding Round", "New Funding Stage") for e in events)


def test_news_article_with_old_date_suppressed():
    """M&A news article with a published date older than 90 days must not fire."""
    new = _new({
        "news_articles": [{"title": "Acme Corp acquired by BigCo", "summary": "deal", "published": "2022-01-01"}]
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert not any(e.signal_type == "Acquisition / M&A" for e in events)


def test_news_article_with_recent_date_fires():
    """M&A news article with a recent published date must fire."""
    new = _new({
        "news_articles": [{"title": "Acme Corp acquired by BigCo", "summary": "deal", "published": "2026-04-01"}]
    })
    events = detect_changes(_BASE_OLD, new, _BASE_CONFIG)
    assert any(e.signal_type == "Acquisition / M&A" for e in events)
