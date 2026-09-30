"""Whose page is it: shared ticketing hosts and the document title.

Audit, 2026-09-30: an event whose website is an Eventbrite or Luma listing
counted any other organizer's listing on that platform as its own page,
and an SVG icon's <title> was read as the page title when the head's was
empty.
"""
from unittest.mock import Mock

import pytest

from tracker import event_intel_admission as A
from tracker.event_intel_access import organizer_url
from tracker.event_intel_structured import titles

EB = 'https://www.eventbrite.com/e/cmo-summit-tickets-123'
LUMA = 'https://lu.ma/cmo-summit'


@pytest.mark.parametrize('url,website,ok', [
    (EB, EB, True),
    (EB + '?aff=home', EB, True),
    (EB + '/', EB, True),
    ('https://www.eventbrite.com/e/cmo-summit-tickets-123/checkout', EB, True),
    ('https://www.eventbrite.com/e/other-dinner-tickets-999', EB, False),
    ('https://www.eventbrite.com/e/cmo-summit-tickets-1234', EB, False),   # a prefix is not the path
    ('https://www.eventbrite.com/', EB, False),
    ('https://acme.eventbrite.com/', EB, False),                          # someone's organizer subdomain
    ('https://acme.eventbrite.com/e/cmo-summit-tickets-123', EB, False),  # even at the same path
    ('https://www.eventbrite.com/o/acme-456/events', 'https://www.eventbrite.com/o/acme-456', True),
    ('https://www.eventbrite.com/o/other-789', 'https://www.eventbrite.com/o/acme-456', False),
    (LUMA, LUMA, True),
    ('https://luma.com/cmo-summit', LUMA, True),                          # lu.ma redirects to luma.com
    ('https://lu.ma/other-meetup', LUMA, False),
    ('https://lu.ma/cmo-summit', 'https://lu.ma/', False),                # the platform root is nobody's
    ('https://web.cvent.com/event/abc/register', 'https://web.cvent.com/event/abc', True),
    ('https://web.cvent.com/event/xyz/register', 'https://web.cvent.com/event/abc', False),
    ('https://www.linkedin.com/events/999', 'https://www.linkedin.com/events/123', False),
])
def test_a_shared_platform_page_is_the_organizers_only_under_its_own_listing(url, website, ok):
    from urllib.parse import urlsplit
    assert organizer_url(url, urlsplit(website).hostname, website) is ok, (url, website)
    # A full website URL in place of the host means the same thing.
    assert organizer_url(url, website) is ok, (url, website)


def test_a_bare_platform_hostname_proves_nothing():
    assert organizer_url(EB, 'www.eventbrite.com') is False
    assert organizer_url(LUMA, 'lu.ma') is False


@pytest.mark.parametrize('url,host,ok', [
    ('https://event.example/cmo', 'event.example', True),
    ('https://www.event.example/cmo', 'event.example', True),
    ('https://tickets.event.example/cmo', 'event.example', True),
    ('https://event.example.evil.example/', 'event.example', False),
    ('https://user:pass@event.example/', 'event.example', False),
    ('ftp://event.example/', 'event.example', False),
    # A per-organizer subdomain on a platform is that organizer's own host.
    ('https://acme.bizzabo.com/agenda', 'acme.bizzabo.com', True),
    ('https://myevent.sched.com/list', 'myevent.sched.com', True),
])
def test_an_ordinary_host_is_unchanged(url, host, ok):
    assert organizer_url(url, host) is ok


def test_admission_does_not_read_another_listing_on_the_same_platform():
    event = dict(name='CMO Summit', website=EB, starts_on='2027-05-11', ends_on='2027-05-12',
                 sources=['https://www.eventbrite.com/e/other-dinner-tickets-999', EB + '#details'])
    fetch = Mock(return_value=dict(status='ok', text='CMO Summit May 11-12, 2027'))
    result = A.inspect(event, fetch)
    assert [c.args[0] for c in fetch.call_args_list] == [EB]
    assert result['support'] == 'literal_name_and_dates_only'


def test_a_redirect_to_another_listing_is_outside_the_event():
    event = dict(name='CMO Summit', website=EB, sources=[], starts_on='2027-05-11', ends_on='2027-05-12')
    fetched = dict(status='ok', text='CMO Summit May 11-12, 2027',
                   final_url='https://www.eventbrite.com/e/other-dinner-tickets-999')
    result = A.inspect(event, lambda url: fetched)
    assert result['support'] == 'unverified'
    assert 'redirected' in result['checks'][0]['reason']


# ── the document's own title ──

@pytest.mark.parametrize('markup,expected', [
    ('<html><head><title></title></head><body><svg><title>Icon</title></svg></body></html>', []),
    ('<html><head><title> </title></head><body><svg><title>Close</title></svg></body></html>', []),
    ('<html><head></head><body><title>Stray</title></body></html>', []),
    ('<html><head><svg><title>Logo</title></svg><title>CMO Summit | May 11-12, 2027</title></head></html>',
     ['CMO Summit | May 11-12, 2027']),
    ('<html><head><title>CMO Summit</title></head><body><svg><title>Icon</title></svg></body></html>',
     ['CMO Summit']),
    ('<html><head><title></title><meta property="og:title" content="CMO Summit 2027"></head>'
     '<body><svg><title>Icon</title></svg></body></html>', ['CMO Summit 2027']),
    # No explicit <head>: a title before the body is still the document's.
    ('<title>CMO Summit</title><body><svg/><p>x</p></body>', ['CMO Summit']),
])
def test_only_the_head_title_is_the_page_title(markup, expected):
    assert titles(markup) == expected
