"""Identity findings from the 2026-09-30 admission audit.

Each refused case below was admitted at 5f61153 with ANOTHER event's dates:
a sub-event "at" or "part of" its parent, a call to action for a different
event, a self-reference whose noun is the parent's, a regional edition
read as a place, a sub-part read as the event's own noun, and a title
whose third segment ties the event to another.
"""
import pytest

from tracker import event_intel_admission as A


def E(name, s='2026-09-09', e='2026-09-11'):
    return dict(name=name, website='https://event.example/', sources=[], starts_on=s, ends_on=e)


def support(event, text, titles=(), **over):
    fetched = dict(status='ok', text=text, titles=list(titles), **over)
    return A.inspect(event, lambda url: fetched)['support']


CMO, SAASTR = E('CMO Summit'), E('SaaStr Annual')
DATES = 'September 9-11, 2026.'


# ── H1: the whole stretch between name and date is read ──

@pytest.mark.parametrize('between', [
    'at SaaStr Annual,', 'is part of SaaStr Annual,', 'from the SaaStr Annual team,',
    'hosted by SaaStr,', 'during SaaStr Annual,', 'alongside Foo Expo,', 'powered by Acme,',
    'returns to SaaStr Annual,', 'is back at SaaStr Annual,', 'in partnership with Foo,',
    'at the SaaStr Annual Expo Hall,', 'is coming to Web Summit Rio,', 'returns to Europe,',
    # No event word at all: only a venue noun makes "at" a place.
    'at Dreamforce,', 'at SaaStr,',
])
def test_a_sub_event_tied_to_another_does_not_take_its_dates(between):
    assert support(CMO, 'CMO Summit %s %s' % (between, DATES)) == 'unverified', between


@pytest.mark.parametrize('between', [
    'takes place on', 'will be held', 'returns to Las Vegas', 'is coming to London',
    'at Moscone Center,', 'at the Venetian Convention Center,', 'runs from',
    'is back in Austin,', 'returns this year on', 'will take place on',
])
def test_plain_connecting_copy_and_a_place_still_admit(between):
    assert support(CMO, 'CMO Summit %s %s' % (between, DATES)) == 'literal_name_and_dates_only', between


# ── H2: a call to action carries the name only when it names nothing else ──

@pytest.mark.parametrize('text', [
    'Welcome to SaaStr Annual. Join us for the Women in SaaS Breakfast on ' + DATES,
    'Welcome to SaaStr Annual. Register for SaaStr Europa, ' + DATES,
    'Welcome to SaaStr Annual. Book your spot at the CMO Dinner on ' + DATES,
    'SaaStr Annual. Get tickets for the Founders Retreat ' + DATES,
    # A call to action that adds a place is not a bare "join us": on a page
    # welcoming visitors to the flagship it is as often a regional date.
    'Welcome to SaaStr Annual. Join us in London on ' + DATES,
])
def test_a_call_to_action_for_another_event_does_not_carry_the_name(text):
    assert support(SAASTR, text) == 'unverified', text


@pytest.mark.parametrize('text', [
    'SaaStr Annual 2026. Join us ' + DATES,
    'SaaStr Annual 2026. Choose your pass to join us ' + DATES,
    'Welcome to SaaStr Annual. Get your tickets now: ' + DATES,
    'SaaStr Annual. Register now ' + DATES,
])
def test_a_plain_call_to_action_still_carries_the_name(text):
    assert support(SAASTR, text) == 'literal_name_and_dates_only', text


# ── H3: "the event" must be this event ──

@pytest.mark.parametrize('event,body,titles', [
    (CMO, 'The CMO Summit is held on day two of SaaStr Annual. The conference takes place ' + DATES, ['CMO Summit | SaaStr']),
    (CMO, 'The summit takes place at SaaStr Annual, ' + DATES, ['CMO Summit']),
    (SAASTR, 'Join us for the CMO Summit. The summit takes place ' + DATES, ['SaaStr Annual 2026']),
    (SAASTR, 'Do not miss the Women in SaaS Breakfast. The event takes place ' + DATES, ['SaaStr Annual 2026']),
    (CMO, 'The summit takes place by invitation of SaaStr Annual, ' + DATES, ['CMO Summit']),
    (CMO, 'The summit takes place within SaaStr Annual, ' + DATES, ['CMO Summit']),
    (CMO, 'The event takes place online, ' + DATES, ['CMO Summit']),
    (CMO, 'The event takes place in the virtual lobby, ' + DATES, ['CMO Summit']),
    (CMO, 'The summit takes place at SaaStr Annual Foo Summit, ' + DATES, ['CMO Summit']),
])
def test_a_self_reference_that_may_be_another_event_is_refused(event, body, titles):
    assert support(event, body, titles) == 'unverified', body


@pytest.mark.parametrize('body,titles', [
    ('The summit takes place at Moscone Center, ' + DATES, ['CMO Summit']),
    ('The event takes place ' + DATES, ['CMO Summit']),
    ('Save the date. Our flagship event takes place ' + DATES, ['CMO Summit']),
    ('The summit takes place in San Francisco, ' + DATES, ['CMO Summit 2026']),
])
def test_a_self_reference_that_agrees_with_the_title_is_admitted(body, titles):
    assert support(CMO, body, titles) == 'organizer_title_name_and_self_referenced_dates', body


def test_a_long_navigation_run_before_the_sentence_is_not_its_subject():
    # Nacha's real page: a menu with no full stop, its "15 Under 40 Awards"
    # item far back from the sentence that dates "our in-person event".
    sfp = E('Smarter Faster Payments', '2027-04-11', '2027-04-14')
    body = ('Menu 15 Under 40 Awards Podcasts ' + 'Look Who Came in 2026 Register ' * 8 +
            'Save the date. Our in-person event takes place at the Gaylord National Harbor '
            'Resort & Convention Center, minutes from Washington, D.C., April 11-14, 2027.')
    assert support(sfp, body, ['Smarter Faster Payments Conference | Payments 2027']) == \
        'organizer_title_name_and_self_referenced_dates'


# ── M1: a bare "City, ST" is the event's place only on the event's own page ──

@pytest.mark.parametrize('event,text,titles', [
    (E('MWC', '2026-06-01', '2026-06-05'), 'MWC Shanghai, China June 1-5, 2026', []),
    (E('MWC', '2026-06-01', '2026-06-05'), 'MWC Shanghai, China June 1-5, 2026', ['MWC Shanghai 2026']),
    (E('SXSW', '2026-06-01', '2026-06-05'), 'SXSW London, UK June 1-5, 2026', []),
    (E('SXSW', '2026-06-01', '2026-06-05'), 'SXSW London, UK June 1-5, 2026', ['SXSW London']),
    (E('SXSW', '2026-06-01', '2026-06-05'), 'SXSW Rio, Brazil June 1-5, 2026', ['SXSW']),
    (SAASTR, 'SaaStr Annual San Mateo, CA ' + DATES, []),
])
def test_a_regional_edition_is_not_read_as_a_place(event, text, titles):
    assert support(event, text, titles) == 'unverified', (text, titles)


@pytest.mark.parametrize('event,text,titles', [
    (E('fintech_devcon', '2027-08-02', '2027-08-04'), 'fintech_devcon Boulder, CO August 2-4, 2027', ['fintech_devcon']),
    # The place is read from right after the name, not from the leftmost
    # capitalised run ("Annual San Mateo").
    (SAASTR, 'SaaStr Annual San Mateo, CA ' + DATES, ['SaaStr Annual 2026']),
    (SAASTR, 'SaaStr Annual 2026 San Mateo, CA ' + DATES, ['SaaStr Annual | The #1 B2B Event']),
])
def test_a_bare_place_on_the_events_own_page_is_admitted(event, text, titles):
    assert support(event, text, titles) == 'literal_name_and_dates_only', (text, titles)


# ── M2: sister events and sub-parts ──

@pytest.mark.parametrize('event,text', [
    (E('Web Summit'), 'Web Summit in Rio, ' + DATES),
    (E('Web Summit'), 'Web Summit in Vancouver, ' + DATES),
    (E('Web Summit'), 'Web Summit in Europe, ' + DATES),
    (E('RSAC'), 'RSAC Expo ' + DATES),
    (E('RSAC'), 'RSAC Forum ' + DATES),
    (E('RSAC'), 'RSAC Show ' + DATES),
])
def test_a_sister_edition_or_a_sub_part_does_not_date_the_event(event, text):
    assert support(event, text) == 'unverified', text


@pytest.mark.parametrize('event,text', [
    (E('Web Summit'), 'Web Summit in Lisbon, ' + DATES),
    (E('Web Summit Rio'), 'Web Summit Rio in Rio, ' + DATES),
    (E('RSAC'), 'RSAC Conference ' + DATES),
])
def test_the_events_own_place_and_noun_still_admit(event, text):
    assert support(event, text) == 'literal_name_and_dates_only', text


def structured(event, row_name):
    row = {'name': row_name, 'startDate': event['starts_on'], 'endDate': event['ends_on']}
    return support(event, 'Home', http_status=200, structured_events=[row])


@pytest.mark.parametrize('event,row_name', [
    (E('Web Summit'), 'Web Summit | Vancouver'),
    (E('Web Summit'), 'Web Summit, Rio'),
    (E('Money20/20'), 'Money20/20, Europe'),
    (E('SaaStr Annual'), 'SaaStr Annual: Virtual Edition'),
    (E('SaaStr Annual'), 'SaaStr Annual | Online'),
])
def test_a_structured_node_naming_an_edition_is_not_this_event(event, row_name):
    assert structured(event, row_name) == 'unverified', row_name


@pytest.mark.parametrize('event,row_name', [
    (E('Web Summit'), 'Web Summit, Lisbon'),          # the real websummit.com node
    (E('Web Summit Vancouver'), 'Web Summit | Vancouver'),
    (E('Money20/20 Europe'), 'Money20/20 Europe, Amsterdam'),
])
def test_a_structured_node_with_the_events_own_place_is_still_admitted(event, row_name):
    assert structured(event, row_name) == 'organizer_structured_name_and_dates', row_name


@pytest.mark.parametrize('titles', [
    ['SaaStr Annual: Virtual Edition'], ['SaaStr Annual | Online'], ['SaaStr Annual Expo'],
    ['SaaStr Annual | Powered by Foo'],
])
def test_a_title_naming_an_edition_or_a_part_does_not_identify_the_event(titles):
    assert support(SAASTR, 'The event takes place ' + DATES, titles) == 'unverified', titles


# ── M3: every title segment is read ──

@pytest.mark.parametrize('title', [
    'CMO Summit | September 9-11, 2026 | at SaaStr Annual',
    'CMO Summit returns to SaaStr Annual | September 9-11, 2026',
    'CMO Summit | September 9-11, 2026 | Powered by SaaStr',
    'CMO Summit | September 9-11, 2026 | Part of SaaStr Annual',
    'CMO Summit, September 9-11, 2026 at SaaStr Annual',
    'CMO Summit | September 9-11, 2026 | Virtual Edition',
    'Web Summit in Rio | September 9-11, 2026',
])
def test_a_title_tied_to_another_event_anywhere_is_refused(title):
    assert support(CMO if title.startswith('CMO') else E('Web Summit'), 'Home', [title]) == 'unverified', title


@pytest.mark.parametrize('title', [
    'CMO Summit | September 9-11, 2026 | San Francisco',
    'CMO Summit returns to Las Vegas | September 9-11, 2026',
    'CMO Summit, September 9-11, 2026 at Moscone Center',
    'CMO Summit | September 9-11, 2026 | The best marketing conference on the planet',
])
def test_a_title_with_only_a_place_or_a_tagline_is_admitted(title):
    assert support(CMO, 'Home', [title]) == 'organizer_title_name_and_dates', title
