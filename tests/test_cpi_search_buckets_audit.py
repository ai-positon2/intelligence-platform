"""Audit of the one search this tool exists for: everyone at one company.

Reported against "at company betabionics.com", which drew "No matches. Try
widening the filters." while Apollo held 355 people at that domain and this
team's own Apollo account held thirteen of them as saved contacts, including
the Chief Medical Officer.

Three separate defects, each of which alone can empty that page, and all three
of which report the same untrue sentence when they do:

  - Apollo answers a people search in TWO arrays, `people` and `contacts`, and
    only the first was read. `contacts` is where every person this team has
    already saved and paid to enrich lives, so the better half of the answer
    was invisible -- and the more the search was narrowed to one company, the
    larger the share of the truth that went missing. search_companies has
    merged the parallel `accounts` bucket all along; people search never did.

  - A contact's `id` is a contact id and its person id is a separate field.
    Merging the bucket without swapping them would hand people/bulk_match an id
    from the wrong namespace, which matches nothing while looking exactly like
    Apollo having no record of that person.

  - Apollo treats its employer-domain parameter as a relevance hint, not a
    rule, so search_people re-checks it in code. That removal was logged and
    never reported, so the one filter that fires on the most common search on
    this page was the only filter that could not explain its own empty result.

  - And when Apollo did not answer at all, the route returned an empty result
    list, which the grid drew as "No matches. Try widening the filters." --
    advice that cannot help, about a search that never ran. Nothing was found
    AND nothing was ruled out; the page asserted the first half of that.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import tracker.apollo_client as ac  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "js", "company_people_intelligence.js")

_SEARCH = "/p2/b2b-agents/company-people-intelligence/search"
_DOMAIN = "betabionics.com"


def _js():
    return open(_JS, encoding="utf-8").read()


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


@pytest.fixture(autouse=True)
def apollo_key(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "")


def _org(domain=_DOMAIN):
    return {"id": "57c4ab6ca6da98689038ddf4", "name": "Beta Bionics", "domain": domain}


def _person(pid, first, last, title, domain=_DOMAIN):
    return {"id": pid, "first_name": first, "last_name": last, "title": title,
            "organization": _org(domain)}


def _contact(cid, person_id, first, last, title):
    """Apollo's saved-contact shape: the row id is a CONTACT id and the person
    id it maps to is carried separately, exactly as returned live."""
    return {"id": cid, "person_id": person_id, "first_name": first,
            "last_name": last, "title": title, "email_status": "verified",
            "organization_id": "57c4ab6ca6da98689038ddf4",
            "organization_name": "Beta Bionics", "organization": _org()}


def _apollo(monkeypatch, payload):
    """Stub Apollo's HTTP layer, capturing the request that was actually sent."""
    sent = []

    def _post(endpoint, body, api_key, retries=3):
        sent.append({"endpoint": endpoint, "body": body})
        return payload

    monkeypatch.setattr(ac, "_post", _post)
    return sent


# ── The contacts bucket ─────────────────────────────────────────────────────

def test_saved_contacts_are_part_of_the_answer(monkeypatch):
    """The people this team already saved are people. Reading only `people`
    hid them, and a search narrow enough to return only saved contacts
    reported that the company had nobody."""
    _apollo(monkeypatch, {
        "people": [],
        "contacts": [_contact("69e89ce144279d0001959da3",
                              "68689d7d7481c30001ca1c0e",
                              "Rebecca", "Langer", "Associate Marketing Director")],
        "pagination": {"total_entries": 1, "total_pages": 1},
    })
    rows = ac.search_people({"company_domains": [_DOMAIN]}, "k")
    assert [r["full_name"] for r in rows] == ["Rebecca Langer"], (
        "a saved contact is missing from a search of their own employer")


def test_a_contact_carries_its_person_id_not_its_contact_id(monkeypatch):
    """people/bulk_match only understands the person id. Passing the contact id
    through would spend the call and match nothing, which is indistinguishable
    from Apollo having no record of that person."""
    _apollo(monkeypatch, {
        "people": [],
        "contacts": [_contact("69e89ce144279d0001959da3",
                              "68689d7d7481c30001ca1c0e",
                              "Rebecca", "Langer", "Associate Marketing Director")],
    })
    row = ac.search_people({"company_domains": [_DOMAIN]}, "k")[0]
    assert row["id"] == "68689d7d7481c30001ca1c0e", (
        "the contact id leaked into the person id field")
    assert row["is_saved_contact"] is True


def test_a_person_in_both_buckets_is_one_person(monkeypatch):
    """Apollo can list the same person in both arrays. Merging without a
    dedupe would show them twice and, on the Companies-style bulk paths, offer
    to pay for them twice."""
    _apollo(monkeypatch, {
        "people": [_person("68689d7d7481c30001ca1c0e", "Rebecca", "Langer", "Director")],
        "contacts": [_contact("69e89ce144279d0001959da3",
                              "68689d7d7481c30001ca1c0e",
                              "Rebecca", "Langer", "Director")],
    })
    rows = ac.search_people({"company_domains": [_DOMAIN]}, "k")
    assert len(rows) == 1, "the same person came back twice"
    assert rows[0]["id"] == "68689d7d7481c30001ca1c0e"


def test_a_net_new_person_is_not_labelled_as_already_saved(monkeypatch):
    """The flag has to mean something: a row from `people` has not been paid
    for, and marking it as saved would misprice the next click."""
    _apollo(monkeypatch, {
        "people": [_person("6119290ae2df410001ea1156", "Sean", "Saint",
                           "Chief Executive Officer")],
        "contacts": [],
    })
    assert ac.search_people({"company_domains": [_DOMAIN]}, "k")[0]["is_saved_contact"] is False


# ── The employer-domain filter reports itself ───────────────────────────────

def test_the_domain_filter_says_how_many_it_removed(monkeypatch):
    """Apollo's domain parameter is a relevance hint, so rows come back from
    other companies and are removed here. Removing them silently is what turned
    "Apollo sent 3 people, none of them work there" into "No matches"."""
    _apollo(monkeypatch, {
        "people": [_person("a", "A", "One", "VP", "example.com"),
                   _person("b", "B", "Two", "VP", "other.com"),
                   _person("c", "C", "Three", "VP", _DOMAIN)],
    })
    meta = {}
    rows = ac.search_people({"company_domains": [_DOMAIN]}, "k", meta=meta)
    assert len(rows) == 1
    assert meta["company_dropped"] == 2


def test_the_route_reports_the_domain_drop_like_every_other_filter(client, monkeypatch):
    """The grid already explains an empty page from `rejected`. This filter
    was the only one not wired into it."""
    _apollo(monkeypatch, {
        "people": [_person("a", "A", "One", "VP", "elsewhere.com"),
                   _person("b", "B", "Two", "VP", "elsewhere.com")],
    })
    r = client.post(_SEARCH, json={"entity": "people", "page": 1,
                                   "filters": {"company_domains": [_DOMAIN],
                                               "company_detail": False}})
    body = r.get_json()
    assert body["results"] == []
    assert body["rejected"]["company"] == 2
    assert body["rejected_labels"]["company"] == "working somewhere else"
    assert body["total"] is None, "Apollo's total counted rows we then removed"


def test_a_clean_page_claims_no_rejections(client, monkeypatch):
    """The mirror of the above: a filter that quietly does nothing must not
    appear as a reason that never fired."""
    _apollo(monkeypatch, {
        "people": [_person("a", "A", "One", "VP")],
        "pagination": {"total_entries": 1, "total_pages": 1},
    })
    body = client.post(_SEARCH, json={"entity": "people", "page": 1,
                                      "filters": {"company_domains": [_DOMAIN],
                                                  "company_detail": False}}).get_json()
    assert len(body["results"]) == 1
    assert "rejected" not in body


# ── A failed search is not an empty one ─────────────────────────────────────

def test_apollo_not_answering_is_not_an_empty_result(client, monkeypatch):
    """Nothing was found AND nothing was ruled out. The route used to assert
    only the first half, and the grid drew "try widening the filters" over a
    search that never reached Apollo."""
    def _boom(endpoint, body, api_key, retries=3):
        raise RuntimeError("apollo down")

    monkeypatch.setattr(ac, "_post", _boom)
    body = client.post(_SEARCH, json={"entity": "people", "page": 1,
                                      "filters": {"company_domains": [_DOMAIN],
                                                  "company_detail": False}}).get_json()
    assert body["search_failed"] is True
    assert body["results"] == []
    assert "widening" not in (body.get("error") or "")


def test_an_unconfigured_environment_says_so_the_same_way(client, monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "")
    body = client.post(_SEARCH, json={"entity": "people", "page": 1,
                                      "filters": {"company_domains": [_DOMAIN]}}).get_json()
    assert body["search_failed"] is True


# ── The grid must draw the failure, not the empty state ─────────────────────

def test_the_page_stops_on_a_failed_search(monkeypatch):
    """Executed rather than grepped: the handler has to RETURN on failure. It
    used to toast and fall through, so renderResults() painted "No matches"
    over the top and a reset wiped the rows already on screen."""
    js = _js()
    assert "d.search_failed" in js
    marker = js.index("if(d && (d.error || d.search_failed)){")
    ret = js.index("return;", marker)
    nxt = js.index("else if", marker)
    assert ret < nxt, "the failure branch falls through into the render path"


def test_removing_the_company_reason_clears_the_company_filter():
    """Every rejection reason is a button that drops the filter it blames. A
    reason with no filter behind it renders as dead text."""
    js = _js()
    assert 'company:"company_domains"' in js
    # ...and dropping it must release a pinned organization id too, or the next
    # search silently re-applies the filter the reader just removed.
    assert 'if(key==="company_domains"){ STATE.pinnedOrgId=null;' in js
