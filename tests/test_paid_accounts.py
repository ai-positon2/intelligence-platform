"""Naming the account an agent reads (tracker/paid_accounts.py): a Google Ads account, a Meta ad account or one of each."""
import pytest

from tracker import paid_accounts as acc
from tracker import paid_meta

ACT = "act_1234567890"


def test_an_account_is_a_google_ads_id_a_meta_ad_account_or_both_joined_by_a_plus():
    assert acc.parse_account(" 1112223334 ") == {"google": "1112223334", "meta": None}
    assert acc.parse_account("act_1234567890") == {"google": None, "meta": "act_1234567890"}
    assert acc.parse_account("1112223334+act_1234567890") == {"google": "1112223334", "meta": "act_1234567890"}
    for bad in ("", None, "123", "abc", "11122233345", "act_12", "1112223334+", "+act_1234567890", "1112223334+1112223334", "act_1234567890+1112223334", "1112223334 + act_1234567890",
                "x1112223334+act_1234567890", "1112223334+act_1234567890x", "1112223334+act_1234567890+1112223334", "ACT_1234567890"):
        with pytest.raises(acc.BadRequest):
            acc.parse_account(bad)


def test_the_account_key_is_the_one_string_a_read_is_filed_under():
    assert acc.account_key({"google": "1112223334", "meta": None}) == "1112223334"
    assert acc.account_key({"google": None, "meta": "act_1234567890"}) == "act_1234567890"
    assert acc.account_key({"google": "1112223334", "meta": "act_1234567890"}) == "1112223334+act_1234567890"


GOOGLE = {"id": "1112223334", "name": "Acme Dental", "currency": "USD", "timeZone": "America/New_York"}
META = {"id": ACT, "name": "Acme Dental Meta", "currency": "USD", "tz": "America/New_York", "platform": "meta"}


@pytest.fixture(autouse=True)
def meta_account(monkeypatch):
    monkeypatch.setattr(paid_meta, "config_problem", lambda: "")
    monkeypatch.setattr(paid_meta, "account_by_id", lambda store, act, http=None: META if act == ACT else None)


def test_each_account_named_is_found_among_what_the_connections_can_read():
    key, found = acc.targets("1112223334+" + ACT, store=None, accounts_fn=lambda: [GOOGLE])
    assert key == "1112223334+" + ACT
    assert found == [{"platform": "google", "id": "1112223334", "name": "Acme Dental", "currency": "USD"}, {"platform": "meta", "id": ACT, "name": "Acme Dental Meta", "currency": "USD"}]
    key, found = acc.targets(ACT, store=None)
    assert key == ACT and [t["platform"] for t in found] == ["meta"]
    key, found = acc.targets("1112223334", store=None, accounts_fn=lambda: [GOOGLE])
    assert key == "1112223334" and [t["platform"] for t in found] == ["google"]


def test_an_account_that_cannot_be_read_is_a_bad_request_for_either_platform():
    with pytest.raises(acc.BadRequest):
        acc.targets("9998887776", store=None, accounts_fn=lambda: [GOOGLE])
    with pytest.raises(acc.BadRequest):
        acc.targets("act_9999999999", store=None)


def test_a_pair_that_bills_in_two_currencies_is_refused_and_one_that_does_not_say_is_not():
    with pytest.raises(acc.BadRequest) as e:
        acc.targets("1112223334+" + ACT, store=None, accounts_fn=lambda: [dict(GOOGLE, currency="EUR")])
    assert "same currency (EUR and USD here)" in str(e.value)
    assert acc.targets("1112223334+" + ACT, store=None, accounts_fn=lambda: [dict(GOOGLE, currency="")])[1][0]["currency"] == ""


def test_the_platforms_are_named_in_the_words_the_pages_use():
    assert acc.NAMES == {"google": "Google Ads", "meta": "Meta Ads"}
