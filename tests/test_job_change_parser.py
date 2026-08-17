"""tracker/job_change_parser.py turns Apollo's fixed "Job_change_alert_apollo_database"
Slack notification into a flat event dict. Two things make this fiddly enough to
pin down with tests: the bold/colon placement is inconsistent across fields
("*Name:*" vs "*LinkedIn*:" vs "*Company description: *" with a trailing space
before the closing star), and "Company description" is the only field that can
wrap across several lines -- get either wrong and either every field silently
shifts by one, or a wrapped paragraph gets truncated to its first line.

Fixtures use synthetic people/companies, not the real prospects this parser
was validated against during backfill.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.job_change_parser import parse_job_change_message  # noqa: E402

_TS = "1786771530.195749"


def _message(body_lines):
    header = "We found *someone* who recently changed their job: *<https://app.chooseapollo.io/#/workflows/abc|Job_change_alert_apollo_database>*"
    return header + "\n\n" + "\n".join(body_lines) + "\n\n\n\nSee contact button"


def test_a_normal_message_parses_every_field():
    text = _message([
        ">*Name:* <https://app.chooseapollo.io/#/contacts/aaa111|Jane> <https://app.chooseapollo.io/#/contacts/aaa111|Doe>",
        ">*Title:* VP of Engineering",
        ">*Company:* <https://app.chooseapollo.io/#/accounts/bbb222|Acme Health>",
        ">*Company industry:* hospital & health care",
        ">*Company description: *Acme Health builds things for hospitals.",
        ">*City:* Austin",
        ">*# Employees:* 500",
        ">*Revenue:* $50M",
        ">*LinkedIn*: <http://www.linkedin.com/in/janedoe>",
        ">*Current job start date:* Jun 01, 2026",
    ])
    event = parse_job_change_message(text, _TS, "https://x/p1786771530195749")
    assert event == {
        "apollo_contact_id": "aaa111",
        "person_name": "Jane Doe",
        "linkedin_url": "http://www.linkedin.com/in/janedoe",
        "new_title": "VP of Engineering",
        "new_company_name": "Acme Health",
        "apollo_account_id": "bbb222",
        "company_industry": "hospital & health care",
        "company_description": "Acme Health builds things for hospitals.",
        "city": "Austin",
        "employees": "500",
        "revenue": "$50M",
        "job_start_date": "Jun 01, 2026",
        "detected_at": "2026-08-15T05:25:30.195749+00:00",
        "slack_message_ts": _TS,
        "slack_permalink": "https://x/p1786771530195749",
    }


def test_multiline_company_description_is_not_truncated_to_its_first_line():
    text = _message([
        ">*Name:* <https://x/#/contacts/ccc333|Sam> <https://x/#/contacts/ccc333|Lee>",
        ">*Title:* CTO",
        ">*Company:* <https://x/#/accounts/ddd444|Widget Co>",
        ">*Company industry:* software",
        ">*Company description: *First paragraph of the description.",
        "Second paragraph, still part of the same field.",
        ">*City:* Denver",
        ">*# Employees:* 40",
        ">*Revenue:* [Unavailable]",
        ">*LinkedIn*: <http://www.linkedin.com/in/samlee>",
        ">*Current job start date:* [Unavailable]",
    ])
    event = parse_job_change_message(text, _TS)
    assert event["company_description"] == (
        "First paragraph of the description.\nSecond paragraph, still part of the same field."
    )
    # Fields after the multiline one were not swallowed into it.
    assert event["city"] == "Denver"
    assert event["employees"] == "40"


def test_unavailable_fields_normalize_to_none_not_the_literal_string():
    text = _message([
        ">*Name:* <https://x/#/contacts/eee555|Kent> <https://x/#/contacts/eee555|Newman>",
        ">*Title:* Growth Lead",
        ">*Company:* <|[Unavailable]>",
        ">*Company industry:* [Unavailable]",
        ">*Company description: *[Unavailable]",
        ">*City:* [Unavailable]",
        ">*# Employees:* [Unavailable]",
        ">*Revenue:* [Unavailable]",
        ">*LinkedIn*: <http://www.linkedin.com/in/kentnewman>",
        ">*Current job start date:* Apr 01, 2024",
    ])
    event = parse_job_change_message(text, _TS)
    assert event["new_company_name"] is None
    assert event["apollo_account_id"] is None
    assert event["company_industry"] is None
    assert event["company_description"] is None
    assert event["city"] is None
    assert event["employees"] is None
    assert event["revenue"] is None
    # Fields that WERE available are untouched by the neighboring blanks.
    assert event["person_name"] == "Kent Newman"
    assert event["job_start_date"] == "Apr 01, 2024"


def test_trailing_see_contact_button_footer_is_not_folded_into_the_description():
    text = _message([
        ">*Name:* <https://x/#/contacts/fff666|Alex> <https://x/#/contacts/fff666|Kim>",
        ">*Title:* Head of Sales",
        ">*Company:* <https://x/#/accounts/ggg777|Beta Corp>",
        ">*Company industry:* retail",
        ">*Company description: *A short description.",
        ">*City:* Miami",
        ">*# Employees:* 90",
        ">*Revenue:* $9M",
        ">*LinkedIn*: <http://www.linkedin.com/in/alexkim>",
        ">*Current job start date:* Mar 01, 2026",
    ])
    event = parse_job_change_message(text, _TS)
    assert "see contact button" not in event["company_description"].lower()


def test_slack_mrkdwn_is_stripped_from_the_company_description():
    text = _message([
        ">*Name:* <https://x/#/contacts/hhh888|Priya> <https://x/#/contacts/hhh888|Rao>",
        ">*Title:* VP Partnerships",
        ">*Company:* <https://x/#/accounts/iii999|Gamma Health>",
        ">*Company industry:* hospital & health care",
        ">*Company description: *Think Growth :globe_with_meridians: <http://gamma.com|gamma.com> | <http://gamma.ai|gamma.ai>",
        ">*City:* Boston",
        ">*# Employees:* 300",
        ">*Revenue:* $40M",
        ">*LinkedIn*: <http://www.linkedin.com/in/priyarao>",
        ">*Current job start date:* May 01, 2026",
    ])
    event = parse_job_change_message(text, _TS)
    assert event["company_description"] == "Think Growth gamma.com | gamma.ai"


def test_a_non_job_change_message_returns_none():
    assert parse_job_change_message("Just a regular reply from a teammate.", _TS) is None
    assert parse_job_change_message("<@U123|Ebin V Edison> has joined the channel", _TS) is None


def test_empty_text_returns_none():
    assert parse_job_change_message("", _TS) is None
    assert parse_job_change_message(None, _TS) is None
