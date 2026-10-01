"""The client line under the report's answer ends in exactly one full stop."""
from tracker import event_intel_report as R


def test_a_field_that_ends_in_a_full_stop_is_not_doubled():
    # Live run 24 (Stripe, 2026-10-01) printed "...and other regions..".
    line = R._profile_sentence({"client_name": "Stripe", "verticals": "SaaS",
                                "geo_scope": "Global, with case studies in other regions."})
    assert line == "Stripe, selling into SaaS, across Global, with case studies in other regions."


def test_a_plain_profile_still_gets_its_full_stop():
    assert R._profile_sentence({"client_name": "Acme", "geo_scope": "US"}) == "Acme, across US."
    assert R._profile_sentence({}) == "The client."
