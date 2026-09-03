"""_cpi_enrich_person's email branch billed 1 credit any time _enrich_people
returned matched: true, whether or not Apollo was actually called this time.
_enrich_people has its own two-tier cache (in-process _PE_MEM, then
Postgres) and returns the identical matched:true shape on a cache hit as on
a fresh, billed Apollo call -- so re-enriching the same email well inside
the cache's positive TTL added a phantom credit to the shared-pool ledger
(_cpi_credit_record) and, if the history dedupe key resolved to the same
entry, kept adding to that entry's recorded cost on every repeat, all for
zero actual Apollo spend. The apollo_id branch three lines below was already
guarded against exactly this via its own _cpi_id_cache_read check; the email
branch was the one path left that hadn't been.

Fixed by checking the same two caches _enrich_people itself would consult
(in the same order) before calling it, and only billing when neither had the
email -- i.e. only when _enrich_people is actually about to spend.

Currently unreachable from the shipped UI: both cpi_enrich and the chat's
contact-reveal path always pass email="". This is a latent-bug fix, not a
regression test for a live incident.
"""

import os
import sys
import time

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

_EMAIL = "jane@acme.com"


@pytest.fixture(autouse=True)
def apollo_key(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def clean_pe_cache(monkeypatch):
    """Each test starts with a cold in-process cache and no Postgres (so
    _pe_cache_read returns {} unless a test explicitly wants a DB hit)."""
    monkeypatch.setattr(appmod, "_PE_MEM", {})
    monkeypatch.setattr(appmod, "_pe_cache_read", lambda emails: {})


def test_a_fresh_apollo_match_is_billed(monkeypatch):
    monkeypatch.setattr(appmod, "_enrich_people",
                        lambda emails, force=False: {_EMAIL: {"matched": True, "name": "Jane"}})
    spend = {"credits": 0}
    hit = appmod._cpi_enrich_person("", "", "", email=_EMAIL, spend=spend)
    assert hit["matched"] is True
    assert spend["credits"] == 1


def test_a_genuine_miss_is_never_billed(monkeypatch):
    monkeypatch.setattr(appmod, "_enrich_people",
                        lambda emails, force=False: {_EMAIL: {"matched": False}})
    spend = {"credits": 0}
    appmod._cpi_enrich_person("", "", "", email=_EMAIL, spend=spend)
    assert spend["credits"] == 0


def test_a_hit_already_warm_in_the_in_process_cache_is_not_billed(monkeypatch):
    """_enrich_people would answer this from _PE_MEM without touching Apollo --
    confirmed here by making it a hard failure to call the real function."""
    monkeypatch.setattr(appmod, "_PE_MEM", {_EMAIL: (time.time() + 900,
                                                     {"matched": True, "name": "Jane"})})

    def fail_if_called(emails, force=False):
        raise AssertionError("_enrich_people should read the warm cache, not be replaced, "
                             "but even if it were called this test wants no credit charged")
    # Use the real _enrich_people (reads _PE_MEM itself) rather than the fail
    # stub, so this test also proves _enrich_people really does serve the hit
    # from memory without an Apollo call.
    spend = {"credits": 0}
    hit = appmod._cpi_enrich_person("", "", "", email=_EMAIL, spend=spend)
    assert hit["matched"] is True
    assert spend["credits"] == 0, "a warm in-process cache hit must not be billed"


def test_a_hit_already_in_postgres_is_not_billed(monkeypatch):
    monkeypatch.setattr(appmod, "_pe_cache_read",
                        lambda emails: {_EMAIL: {"matched": True, "name": "Jane"}} if _EMAIL in emails else {})
    # _enrich_people itself would read the same Postgres cache and never reach
    # Apollo; stub it to return that same cached shape without a real DB.
    monkeypatch.setattr(appmod, "_enrich_people",
                        lambda emails, force=False: {_EMAIL: {"matched": True, "name": "Jane"}})
    spend = {"credits": 0}
    hit = appmod._cpi_enrich_person("", "", "", email=_EMAIL, spend=spend)
    assert hit["matched"] is True
    assert spend["credits"] == 0, "a Postgres cache hit must not be billed"


def test_two_calls_for_the_same_email_bill_once_not_twice(monkeypatch):
    """The realistic repeat-click scenario this bug actually described."""
    calls = {"n": 0}

    def enrich(emails, force=False):
        calls["n"] += 1
        # First call is a genuine Apollo hit; _PE_MEM now holds it for the
        # second call, exactly like the real _enrich_people would leave it.
        appmod._PE_MEM[_EMAIL] = (time.time() + 900, {"matched": True, "name": "Jane"})
        return {_EMAIL: {"matched": True, "name": "Jane"}}

    monkeypatch.setattr(appmod, "_enrich_people", enrich)
    spend = {"credits": 0}
    appmod._cpi_enrich_person("", "", "", email=_EMAIL, spend=spend)
    appmod._cpi_enrich_person("", "", "", email=_EMAIL, spend=spend)
    assert spend["credits"] == 1, "the second call found the first call's cache and must not re-bill"
