"""Pages live run 26 (Gong, 2026-10-01) could not use.

sales30conf.com labels a plain HTML body "Content-Encoding: gzip,deflate";
revenueoperationsalliance.com names its schema.org node "Home | Revenue
Operations Summit | Boston" and writes the Amsterdam summit's start as a UTC
timestamp that is midnight local time on the NEXT day.
"""
import gzip

import pytest
import requests

from tracker import event_intel_admission as A
from tracker import event_intel_discover as D
from tracker import event_intel_harvest as H

PAGE = ("<html><head><title>Acme Summit | May 5-6, 2027</title></head><body>"
        + "<p>Acme Summit runs May 5-6, 2027 in Austin.</p>" * 40 + "</body></html>").encode()


class _Raw:
    def __init__(self, body):
        self.body = body

    def read(self, n=None, decode_content=True):
        assert decode_content is False
        return self.body


class _Resp:
    def __init__(self, body, decodes):
        self.status_code, self.url, self.encoding = 200, "https://acme.example/", "utf-8"
        self.headers = {"Content-Type": "text/html", "Content-Encoding": "gzip,deflate"}
        self.raw, self._decodes, self._body, self.closed = _Raw(body), decodes, body, False

    def iter_content(self, n):
        if not self._decodes:
            raise requests.exceptions.ContentDecodingError("incorrect header check")
        yield self._body

    def close(self):
        self.closed = True


def _serve(monkeypatch, *responses):
    queue = list(responses)
    calls = []

    def get(url, **kw):
        calls.append(kw.get("headers"))
        return queue.pop(0)
    monkeypatch.setattr(H, "public_get", get)
    return calls


# ── a mislabelled body is read as sent ──

def test_a_plain_body_labelled_gzip_is_read(monkeypatch):
    calls = _serve(monkeypatch, _Resp(PAGE, False), _Resp(PAGE, False))
    out = H.fetch_page("https://acme.example/")
    assert out["status"] == "ok" and "Acme Summit runs May 5-6, 2027" in out["text"]
    assert out["titles"] == ["Acme Summit | May 5-6, 2027"]
    assert len(calls) == 2


def test_a_real_gzip_body_on_the_re_read_is_decompressed(monkeypatch):
    _serve(monkeypatch, _Resp(PAGE, False), _Resp(gzip.compress(PAGE), False))
    assert "Acme Summit runs" in H.fetch_page("https://acme.example/")["text"]


def test_a_body_that_is_neither_is_an_error_not_an_exception(monkeypatch):
    _serve(monkeypatch, _Resp(PAGE, False), _Resp(b"\x1f\x8bnot gzip at all", False))
    out = H.fetch_page("https://acme.example/")
    assert out["status"] == "error" and out["text"] == ""
    assert out["note"] == "The page's compressed response could not be decoded."


def test_a_page_that_decodes_is_read_once(monkeypatch):
    calls = _serve(monkeypatch, _Resp(PAGE, True))
    assert H.fetch_page("https://acme.example/")["status"] == "ok"
    assert len(calls) == 1


# ── a page label leading a structured name ──

def E(name, s, e):
    return dict(name=name, website='https://event.example/', sources=[], starts_on=s, ends_on=e)


def support(event, rows):
    fetched = dict(status='ok', http_status=200, text='Agenda, speakers and venue details. ' * 20,
                   titles=[], structured_events=rows)
    return A.inspect(event, lambda url: fetched)['support']


REVOPS = E('Revenue Operations Summit', '2026-10-27', '2026-10-27')


@pytest.mark.parametrize('name', ['Home | Revenue Operations Summit | Boston',
                                  'Register | Revenue Operations Summit',
                                  'Home | Revenue Operations Summit'])
def test_a_page_label_before_the_structured_name_is_skipped(name):
    rows = [dict(name=name, startDate='2026-10-27T04:00:00.000Z', endDate='2026-10-27T04:00:00.000Z')]
    assert support(REVOPS, rows) == 'organizer_structured_name_and_dates', name


@pytest.mark.parametrize('name', ['Home | CMO Summit | Revenue Operations Summit',
                                  'Home | Revenue Operations Summit | Virtual Edition',
                                  'Home | Revenue Operations Summit | Shanghai',
                                  'Home'])
def test_a_page_label_does_not_open_the_structured_name_to_anything_else(name):
    rows = [dict(name=name, startDate='2026-10-27', endDate='2026-10-27')]
    assert support(REVOPS, rows) == 'unverified', name


# ── a UTC timestamp is read as the organizer's local day ──

CRO = E('Chief Revenue Officer Summit', '2027-05-25', '2027-05-25')


def test_a_utc_time_late_in_the_day_can_be_the_next_local_day():
    rows = [dict(name='Chief Revenue Officer Summit | Amsterdam',
                 startDate='2027-05-24T22:00:00.000Z', endDate='2027-05-24T22:00:00.000Z')]
    assert support(CRO, rows) == 'organizer_structured_name_and_dates'
    # And it is still the UTC day as well: London's midnight is 00:00 UTC.
    assert support(E('Chief Revenue Officer Summit', '2027-05-24', '2027-05-24'), rows) == \
        'organizer_structured_name_and_dates'


@pytest.mark.parametrize('start', [
    '2027-05-24T04:00:00.000Z',      # early UTC: the local day is that day
    '2027-05-24T22:00:00+00:00x',    # not a timestamp at all
    '2027-05-24T22:00:00-04:00',     # its own offset: exactly its own date
    '2027-05-24T22:00:00',           # no zone: exactly its own date
    '2027-05-24',
])
def test_any_other_timestamp_means_exactly_its_own_date(start):
    rows = [dict(name='Chief Revenue Officer Summit', startDate=start, endDate='2027-05-25')]
    assert support(CRO, rows) == 'unverified', start


def test_two_days_off_is_never_accepted():
    rows = [dict(name='Chief Revenue Officer Summit',
                 startDate='2027-05-23T22:00:00Z', endDate='2027-05-23T22:00:00Z')]
    assert support(CRO, rows) == 'unverified'


def test_recovery_never_writes_an_ambiguous_utc_day(monkeypatch):
    rows = [dict(name='Chief Revenue Officer Summit', startDate='2027-05-24T22:00:00Z',
                 endDate='2027-05-24T22:00:00Z')]
    page = dict(status='ok', http_status=200, truncated=False, final_url='https://event.example/',
                text='', structured_events=rows)
    monkeypatch.setattr(H, 'fetch_page', lambda url: page)
    ev = dict(name='Chief Revenue Officer Summit', website='https://event.example/', sources=[])
    D._recover_dates(ev, '2026-10-01')
    assert 'starts_on' not in ev
    rows[0].update(startDate='2027-05-25', endDate='2027-05-25')
    D._recover_dates(ev, '2026-10-01')
    assert (ev['starts_on'], ev['ends_on']) == ('2027-05-25', '2027-05-25')


def test_a_late_utc_time_never_means_the_day_before():
    # 22:00 UTC on 25 May is 25 May or 26 May somewhere; never 24 May.
    rows = [dict(name='Chief Revenue Officer Summit',
                 startDate='2027-05-25T22:00:00Z', endDate='2027-05-25T22:00:00Z')]
    assert support(E('Chief Revenue Officer Summit', '2027-05-24', '2027-05-24'), rows) == 'unverified'
