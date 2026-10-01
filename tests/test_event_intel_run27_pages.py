"""Pages the second Gong run (live run 27, 2026-10-01) refused.

Each admit is paired with the nearest shape that must still be refused.
"""
import pytest

from tracker import event_intel_admission as A

FILLER = 'Agenda, speakers and venue details. ' * 20


def E(name, s, e, website='https://event.example/', city=''):
    return dict(name=name, website=website, sources=[], starts_on=s, ends_on=e, city=city)


def inspect(event, text='', titles=(), rows=()):
    fetched = dict(status='ok', http_status=200, text=text, titles=list(titles),
                   structured_events=list(rows))
    return A.inspect(event, lambda url: fetched)


def support(*a, **k):
    return inspect(*a, **k)['support']


# ── "members only after ..." is a word order, not a members-only event ──

DF = E('Dreamforce', '2027-09-21', '2027-09-23')
DF_TEXT = 'Dreamforce returns to San Francisco, CA, at Moscone Center, September 21-23, 2027.\n'


def test_members_only_after_a_payment_is_not_a_restriction():
    text = DF_TEXT + ("Please note: If paying by check or wire transfer, you'll be able to invite "
                      "your group members only after the payment has cleared.\n")
    assert support(DF, text) == 'literal_name_and_dates_only'


@pytest.mark.parametrize('line', [
    'Dreamforce is members only this year.',
    'Attendance is members-only. After registration you will hear from us.',
    'Members only: the main conference requires a membership.',
])
def test_a_members_only_event_is_still_held(line):
    assert support(DF, DF_TEXT + line + '\n') == 'unverified', line


# ── the host's own brand before the event name ──

FORRESTER = E('Forrester B2B Summit North America', '2027-05-02', '2027-05-04',
              website='https://www.forrester.com/event/b2b-summit-north-america/')
FR_TEXT = 'B2B Summit North America runs May 2-4, 2027 in Phoenix.'


def test_the_hosts_brand_may_be_left_off_the_name():
    assert support(FORRESTER, FR_TEXT) == 'literal_name_and_dates_only'


@pytest.mark.parametrize('event', [
    # Not the host's brand: the shorter name would be somebody else's event.
    E('Forrester B2B Summit North America', '2027-05-02', '2027-05-04',
      website='https://www.b2bsummit.example/'),
    # What is left is only the event's nouns.
    E('Forrester Summit Expo', '2027-05-02', '2027-05-04', website='https://www.forrester.com/'),
])
def test_the_brand_is_dropped_only_from_its_own_host_and_never_down_to_a_noun(event):
    text = FR_TEXT if 'North' in event['name'] else 'Summit Expo runs May 2-4, 2027 in Phoenix.'
    assert support(event, text) == 'unverified', event


def test_a_two_word_name_keeps_its_brand():
    assert A._names(dict(name='Gartner Symposium', website='https://www.gartner.com/'), 2027) == \
        ['gartner symposium']


# ── the candidate's own city is its place ──

TORONTO_ROW = [dict(name='Customer Success Summit | Toronto',
                    startDate='2026-11-12T05:00:00.000Z', endDate='2026-11-13T05:00:00.000Z')]


def test_a_node_naming_the_candidates_own_city_is_its_edition():
    ev = E('Customer Success Summit', '2026-11-12', '2026-11-13', city='Toronto, ON')
    assert support(ev, FILLER, rows=TORONTO_ROW) == 'organizer_structured_name_and_dates'


@pytest.mark.parametrize('city', ['', 'Boston, MA'])
def test_a_spin_off_city_is_still_another_edition(city):
    ev = E('Customer Success Summit', '2026-11-12', '2026-11-13', city=city)
    assert support(ev, FILLER, rows=TORONTO_ROW) == 'unverified', city


def test_the_own_city_does_not_outlive_its_inspection():
    inspect(E('Customer Success Summit', '2026-11-12', '2026-11-13', city='Toronto'), FILLER,
            rows=TORONTO_ROW)
    assert A._OWN_PLACE.get() == ''


# ── a title's dated segment may say where ──

PULSE = E('Gainsight Pulse', '2026-10-19', '2026-10-20')


@pytest.mark.parametrize('title', ['Gainsight Pulse | 19-20 October 2026 in London',
                                   'Gainsight Pulse | 19-20 October 2026 at The Brewery Hall',
                                   'Gainsight Pulse | 19-20 October 2026'])
def test_dates_and_a_place_in_the_second_segment(title):
    assert support(PULSE, FILLER, titles=[title]) == 'organizer_title_name_and_dates', title


@pytest.mark.parametrize('title', ['Gainsight Pulse | 19-20 October 2026 at SaaStr Annual',
                                   'Gainsight Pulse | 19-20 October 2026 in Shanghai',
                                   'Gainsight Pulse | 19-20 October 2026 with Foo Expo',
                                   'Gainsight Pulse | 19-20 October 2026 in London Tech Week'])
def test_anything_more_than_where_still_refuses(title):
    assert support(PULSE, FILLER, titles=[title]) == 'unverified', title
