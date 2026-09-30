"""Date and name formats from the 2026-09-30 admission audit.

The audit found real organizer date formats held as "not found" (the US
"Sept", two-day "21 & 22 April", cross-month and cross-year ranges, figure
dash and minus sign separators, weekdays) and a one-day candidate admitted
from inside a three-day range. Every existing wrong-date refusal must keep
refusing, so each accepted shape here has a near miss beside it.
"""
import pytest

from tracker import event_intel_admission as A

FIGURE_DASH, MINUS, HYPHEN, EN_DASH = chr(0x2012), chr(0x2212), chr(0x2010), chr(0x2013)


def E(name, s, e):
    return dict(name=name, website='https://event.example/', sources=[], starts_on=s, ends_on=e)


def support(event, text, titles=(), **over):
    fetched = dict(status='ok', text=text, titles=list(titles), **over)
    return A.inspect(event, lambda url: fetched)['support']


SEPT = E('CMO Summit', '2026-09-09', '2026-09-11')
TWO_DAY = E('CMO Summit', '2027-04-21', '2027-04-22')
CROSS_MONTH = E('CMO Summit', '2026-10-30', '2026-11-02')
CROSS_YEAR = E('CMO Summit', '2026-12-30', '2027-01-02')
WEEK = E('CMO Summit', '2026-11-02', '2026-11-06')


@pytest.mark.parametrize('event,dates', [
    (SEPT, 'Sept 9-11, 2026'), (SEPT, 'Sept. 9-11, 2026'), (SEPT, 'Sep. 9-11, 2026'),
    (SEPT, 'September 9 %s 11, 2026' % FIGURE_DASH), (SEPT, 'September 9%s11, 2026' % MINUS),
    (SEPT, 'September 9%s11, 2026' % HYPHEN), (SEPT, '9 %s 11 Sept 2026' % EN_DASH),
    (SEPT, 'Sept 9 through 11, 2026'),
    (TWO_DAY, '21 & 22 April 2027'), (TWO_DAY, '21 and 22 April 2027'), (TWO_DAY, 'April 21 & 22, 2027'),
    (TWO_DAY, 'Wednesday, 21 and Thursday, 22 April 2027'),
    (CROSS_MONTH, '30 October - 2 November 2026'), (CROSS_MONTH, 'October 30 - November 2, 2026'),
    (CROSS_MONTH, 'Oct 30 %s Nov 2, 2026' % FIGURE_DASH),
    (CROSS_YEAR, 'December 30 - January 2, 2027'), (CROSS_YEAR, '30 December - 2 January 2027'),
    (CROSS_YEAR, 'December 30, 2026 - January 2, 2027'),
    (WEEK, 'Monday 2 November - Friday 6 November 2026'), (WEEK, 'November 2 - November 6, 2026'),
    (WEEK, 'Monday, November 2 - Friday, November 6, 2026'),
])
def test_real_organizer_date_formats_are_read(event, dates):
    assert support(event, 'CMO Summit ' + dates) == 'literal_name_and_dates_only', dates


@pytest.mark.parametrize('event,dates', [
    (SEPT, 'Sept 9-12, 2026'), (SEPT, 'Sept 10-11, 2026'), (SEPT, 'Sept 9-11, 2027'),
    (SEPT, 'September 9 %s 11, 2025' % FIGURE_DASH),
    # A joining word is the whole range only when the days touch.
    (E('CMO Summit', '2027-04-21', '2027-04-23'), '21 & 23 April 2027'),
    (E('CMO Summit', '2027-04-21', '2027-04-23'), '21 and 23 April 2027'),
    # The weekday must be that date's.
    (TWO_DAY, 'Tuesday, 21 and Thursday, 22 April 2027'),
    (WEEK, 'Tuesday 2 November - Friday 6 November 2026'),
    (CROSS_MONTH, '30 October - 2 November 2027'), (CROSS_MONTH, '30 October - 3 November 2026'),
    # The one written year is the END's, and must be the year after.
    (CROSS_YEAR, 'December 30 - January 2, 2026'), (CROSS_YEAR, 'December 30 - January 2, 2028'),
    (E('CMO Summit', '2025-12-30', '2027-01-02'), 'December 30 - January 2, 2027'),
])
def test_near_miss_dates_are_still_refused(event, dates):
    assert support(event, 'CMO Summit ' + dates) == 'unverified', dates


# ── M4: a single day inside a range is not a one-day event ──

ONE_DAY = E('TechConf', '2026-09-11', '2026-09-11')


@pytest.mark.parametrize('text', [
    'TechConf 9-11 September 2026', 'TechConf 9 %s 11 September 2026' % EN_DASH,
    'TechConf September 9 - September 11, 2026', 'TechConf 10 & 11 September 2026',
    'TechConf 11 September 2026 - 13 September 2026', 'TechConf 9 to 11 September 2026',
    'TechConf September 11, 2026 - September 13, 2026',
])
def test_a_one_day_candidate_is_not_admitted_from_a_range(text):
    assert support(ONE_DAY, text) == 'unverified', text


@pytest.mark.parametrize('text', [
    'TechConf 11 September 2026', 'TechConf - 11 September 2026',
    'TechConf, Friday 11 September 2026', 'TechConf Sept. 11, 2026', 'TechConf 2026-09-11',
    'TechConf 11 September 2026. Doors 9-5.',
])
def test_a_one_day_candidate_with_its_own_date_is_admitted(text):
    assert support(ONE_DAY, text) == 'literal_name_and_dates_only', text


def test_a_one_day_title_is_not_admitted_from_a_range_either():
    assert support(ONE_DAY, 'Home', ['TechConf | 9-11 September 2026']) == 'unverified'
    assert support(ONE_DAY, 'Home', ['TechConf | 11 September 2026']) == 'organizer_title_name_and_dates'


# ── a spaced dash inside a date is not a title separator ──

@pytest.mark.parametrize('event,title', [
    (CROSS_MONTH, 'CMO Summit | 30 October - 2 November 2026'),
    (CROSS_YEAR, 'CMO Summit | December 30 - January 2, 2027'),
    (SEPT, 'CMO Summit | Sept. 9-11, 2026'),
])
def test_a_title_range_across_months_or_years_is_read(event, title):
    assert support(event, 'Home', [title]) == 'organizer_title_name_and_dates', title


def test_a_year_before_a_dash_is_still_a_separator():
    event = E('The Phocuswright Conference', '2026-11-17', '2026-11-19')
    assert A._TitleSplit.split('The Phocuswright Conference 2026 - November 17-19, 2026')[0] == \
        'The Phocuswright Conference 2026'
    assert support(event, 'Home', ['The Phocuswright Conference 2026 - November 17-19, 2026']) == \
        'organizer_title_name_and_dates'


# ── names: accents and spacing ──

@pytest.mark.parametrize('name,text', [
    ('Salón Summit', 'Salon Summit September 9-11, 2026'),
    ('Salon Summit', 'Salón Summit September 9-11, 2026'),
    ('Money 20/20', 'Money20/20 September 9-11, 2026'),
    ('Money20/20', 'Money 20/20 September 9-11, 2026'),
    ('HIMSS27', 'HIMSS 27 September 9-11, 2026'),
])
def test_accent_and_spacing_variants_are_one_name(name, text):
    assert support(E(name, '2026-09-09', '2026-09-11'), text) == 'literal_name_and_dates_only', text


@pytest.mark.parametrize('name,text', [
    ('Money 20/20', 'Money20/20 Europe September 9-11, 2026'),
    ('Money 20/20', 'Money20 September 9-11, 2026'),
    ('HIMSS27', 'HIMSS 2027 September 9-11, 2026'),
    ('Salon Summit', 'Salons Summit September 9-11, 2026'),
])
def test_spacing_tolerance_does_not_match_another_name(name, text):
    assert support(E(name, '2026-09-09', '2026-09-11'), text) == 'unverified', text


@pytest.mark.parametrize('name,row', [('Money 20/20', 'Money20/20'), ('Money20/20', 'Money 20/20 | Las Vegas')])
def test_a_structured_node_with_other_spacing_is_this_event(name, row):
    event = E(name, '2026-09-09', '2026-09-11')
    node = {'name': row, 'startDate': '2026-09-09', 'endDate': '2026-09-11'}
    assert support(event, 'Home', http_status=200, structured_events=[node]) == 'organizer_structured_name_and_dates'
