"""tracker/sci_name_match.py -- the one check that stops an unrelated
account being attached to a company's report.

Extracted from sci_youtube_client when Reddit became the second platform
resolving a company against a vendor's fuzzy search index. The live example
in test_rejects_an_unrelated_account is real: YouTube's search genuinely
answers "Harborview Compliance Systems" with an Australian awnings channel.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_name_match as nm  # noqa: E402


def test_corporate_boilerplate_is_not_what_matches():
    """Otherwise "Acme Systems" matches "Fairview Systems" on "systems"."""
    assert nm.plausible_match("Acme Systems", "Fairview Systems") is False


def test_rejects_an_unrelated_account():
    assert nm.plausible_match("Harborview Compliance Systems",
                              "Outdoor Blinds and Awnings Australia (OBA)") is False


def test_accepts_an_exact_name():
    assert nm.plausible_match("Position2", "Position2") is True


def test_accepts_a_name_that_differs_only_in_spacing():
    assert nm.plausible_match("Gentle Dental", "GentleDental") is True


def test_accepts_a_branded_suffix():
    assert nm.plausible_match("Northstar Anesthesia", "Northstar Anesthesia Official") is True


def test_a_name_that_is_only_boilerplate_still_has_tokens():
    """An empty significant-token set would otherwise match everything."""
    assert nm.name_tokens("The Co") == {"the", "co"}


def test_an_empty_side_never_matches():
    assert nm.plausible_match("Acme", "") is False
    assert nm.plausible_match("", "Acme") is False


def test_the_youtube_client_still_exposes_the_matcher():
    """sci_youtube_client aliases these under its original private names;
    the aliases are what its own tests and callers reach for."""
    from tracker import sci_youtube_client as yt
    assert yt._plausible_channel_match is nm.plausible_match
    assert yt._name_tokens is nm.name_tokens
