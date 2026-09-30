from unittest.mock import Mock

import pytest

from tracker import event_intel_admission as A

EVENT = dict(name='CMO Summit', website='https://event.example/cmo',
             sources=['https://event.example/cmo'], starts_on='2027-05-11', ends_on='2027-05-12')


def page(text, **over):
    return dict(status='ok', text=text, **over)


@pytest.mark.parametrize('dates', ['May 11–12, 2027', '11-12 May 2027',
                                 '2027-05-11 to 2027-05-12',
                                 'May 11, 2027 through May 12, 2027'])
def test_named_edition_has_literal_support(dates):
    result = A.inspect(EVENT, lambda url: page('CMO Summit: '+dates))
    assert result['reasons'] == []
    assert result['support'] == 'literal_name_and_dates_only'
    assert result['checks'][0]['snapshot']['text_sha256']


@pytest.mark.parametrize('text', [
    # These connector phrases and this date format were previously rejected
    # on an otherwise perfectly readable, correctly-dated page: _owns_date's
    # allowed-opener list didn't cover them, and _date_patterns had no
    # numeric branch at all.
    'CMO Summit, taking place May 11-12, 2027 in Austin.',
    'CMO Summit will be held May 11-12, 2027 in Austin.',
    'CMO Summit convenes May 11-12, 2027 in Austin.',
    'CMO Summit takes place 5/11/2027 - 5/12/2027 in Austin. Format MM/DD/YYYY.',
    'CMO Summit takes place 05/11/2027 - 05/12/2027 in Austin. Format MM/DD/YYYY.',
])
def test_realistic_connector_phrasing_and_numeric_dates_are_recognised(text):
    result = A.inspect(EVENT, lambda url: page(text))
    assert result['reasons'] == []
    assert result['support'] == 'literal_name_and_dates_only'


@pytest.mark.parametrize('fetched', [
    page('Parent Conference May 11–12, 2027'),
    page('CMO Summit May 14, 2026'),
    page('CMO Summit 2027. Date to be announced.'),
    page('CMO Summit May 11–12, 2027. JavaScript is disabled.',spa='react'),
    page('CMO Summit May 11–12, 2027',truncated=True),
    page('CMO Summit May 11–12, 2027',final_url='https://foreign.example/cmo'),
    {'status':'blocked'},
])
def test_unknown_or_unreadable_editions_are_held_for_review(fetched):
    assert A.inspect(EVENT, lambda url: fetched)['reasons']


@pytest.mark.parametrize('term', ['Invite-only', 'By invitation', 'Members only',
                                 'Application-only', 'Sold out', 'Waitlist'])
def test_date_support_does_not_override_restricted_access(term):
    result = A.inspect(EVENT, lambda url: page('CMO Summit May 11–12, 2027. '+term))
    assert 'access' in result['reasons'][0]
    assert result['support'] == 'unverified'


def test_bound_and_no_third_party_fetch():
    event = dict(EVENT, sources=['https://foreign.example/cmo'] +
                 ['https://event.example/'+str(i) for i in range(5)])
    fetch = Mock(return_value=page('No dated event announcement.'))
    result = A.inspect(event,fetch)
    assert fetch.call_count == A.MAX_PAGES
    assert all('foreign.example' not in call.args[0] for call in fetch.call_args_list)
    assert result['not_read'] == 4


def test_fetch_failure_is_reported_not_raised():
    assert A.inspect(EVENT,Mock(side_effect=RuntimeError('unavailable')))['reasons']


def test_framework_marker_alone_does_not_reject_readable_page():
    result = A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027',spa='wp-json/wp/v2'))
    assert result['reasons'] == []


@pytest.mark.parametrize('copy', ['Available until sold out', 'If sold out, join our list',
                                  'Not invite-only', 'No longer sold out'])
def test_conditional_or_negated_restriction_is_not_a_closure(copy):
    assert A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027. '+copy))['reasons'] == []


def stub_readable_sources(monkeypatch):
    """Synthetic organizer pages for unrelated pipeline/store wiring tests."""
    def inspect_all(events):
        return [A.inspect(e, lambda url, e=e: page(
            e['name']+' '+e['starts_on']+' to '+e['ends_on'])) for e in events]
    monkeypatch.setattr(A,'inspect_all',inspect_all)


def test_pipeline_keeps_unsupported_event_out_of_scoring_and_reports_why(monkeypatch):
    from tests.test_event_intel_recommend import _FakeStore, _wire, _cand, PROFILE
    from tracker import event_intel_pipeline as P, event_intel_harvest as H
    fake = _FakeStore()
    original = A.inspect_all
    _wire(monkeypatch,fake)
    monkeypatch.setattr(A,'inspect_all',original)
    monkeypatch.setattr(H,'fetch_page',lambda url: page('CMO Summit: 2026 agenda coming soon.'))
    monkeypatch.setattr(P.event_intel_discover,'discover',lambda p: {
        'candidates':[_cand('CMO Summit')], 'by_category':{}, 'statuses':{},
        'shortfall':[], 'categories_searched':6,'categories_failed':0,'found':1})
    monkeypatch.setattr(P.event_intel_audit,'audit_famous',lambda c,p: {
        'checked':0,'error':None,'cut':[],'kept':[],'verdicts':{}})
    monkeypatch.setattr(P.event_intel_audit,'promote_alternatives',lambda *a,**k: {
        'promoted':[], 'unconfirmed':[], 'not_attempted':[]})
    score = Mock(return_value={'scored':[],'unscored':[],'errors':[],'batches':0})
    monkeypatch.setattr(P.event_intel_scorer,'score_all',score)
    P._run_recommend(1,'me@p2.example',PROFILE)
    assert score.call_args.args[0] == []
    assert fake.rows == []
    summary = fake.runs[1]['summary']
    assert summary['source_admission'][0]['reasons']
    assert 'parent-event dates' in str(summary)
    assert summary['completion_state'] != 'complete'


@pytest.mark.parametrize('name,text', [
    ('CMO Summit 2027','CMO Summit: May 11–12, 2027'),
    ('CMO Summit (CMOS)','CMO Summit: May 11–12, 2027'),
    ('Growth & Marketing Summit','Growth and Marketing Summit: May 11–12, 2027'),
    ('CMO Summit','CMO-Summit: May 11–12, 2027'),
])
def test_safe_name_variations_keep_literal_identity(name,text):
    assert A.inspect(dict(EVENT,name=name),lambda url: page(text))['reasons'] == []


@pytest.mark.parametrize('text', [
    'Parent Conference May 11–12, 2027. CMO Summit dates to be announced.',
    'CMO Summit Europe May 11–12, 2027',
    'Old CMO Summit [https://event.example/CMO-Summit-May-11-12-2027]',
    'CMO Summit 2026. Parent Conference May 11–12, 2027.',
])
def test_neighbor_or_different_edition_cannot_supply_dates(text):
    assert A.inspect(EVENT,lambda url: page(text))['reasons']


def test_shared_year_cross_month_date_range():
    event = dict(EVENT,starts_on='2027-05-31',ends_on='2027-06-02')
    assert A.inspect(event,lambda url: page('CMO Summit May 31–June 2, 2027'))['reasons'] == []


@pytest.mark.parametrize('copy', [
    'Hotel room block is sold out. Event registration is open.',
    'VIP passes are sold out. General admission is open.',
    'VIP dinner is invite-only. General admission is open.',
    # Real organizer copy phrases the same "not the whole event" restriction
    # in ways the original list didn't cover.
    'The VIP suite is sold out. General admission is open.',
    'The VIP box is sold out. General admission is open.',
    'The exhibit booth is sold out. General admission is open.',
    'The sponsor table is sold out. General admission is open.',
])
def test_explicit_other_inventory_does_not_close_general_event(copy):
    r=A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027. '+copy))
    assert r['reasons'] == []
    assert r['checks'][0]['access_observations']


def test_unknown_tier_access_remains_unresolved():
    assert A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027. VIP passes sold out.'))['reasons']


def test_duplicate_fragment_does_not_use_second_source_slot():
    event=dict(EVENT,sources=[EVENT['website']+'#agenda','https://event.example/details'])
    fetch=Mock(side_effect=lambda url: page('CMO Summit May 11–12, 2027') if url.endswith('details') else page('No dated announcement'))
    assert A.inspect(event,fetch)['reasons'] == []
    assert fetch.call_args_list[-1].args[0] == 'https://event.example/details'


def test_dynamic_fallback_records_evidence_without_admitting_it():
    r=A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027. Please enable JavaScript.',spa='root'))
    assert r['reasons']
    assert r['checks'][0]['read_mode'] == 'javascript_fallback'
    assert r['checks'][0]['snapshot']['text_sha256']


@pytest.mark.parametrize('copy',['Application required','Subject to approval'])
def test_application_condition_is_not_confirmed_client_access(copy):
    assert A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027. '+copy))['reasons']


def test_other_inventory_exception_cannot_hide_event_closure():
    text='CMO Summit May 11–12, 2027. Hotel room block sold out. Event registration is open. CMO Summit is cancelled.'
    assert A.inspect(EVENT,lambda url: page(text))['reasons']


def test_intervening_sentence_cannot_transfer_parent_dates():
    text='CMO Summit is planned. Parent Conference May 11–12, 2027.'
    assert A.inspect(EVENT,lambda url: page(text))['reasons']


def test_conflicting_year_in_candidate_name_is_not_normalized_away():
    event=dict(EVENT,name='CMO Summit 2026')
    assert A.inspect(event,lambda url: page('CMO Summit 2026 May 11–12, 2027'))['reasons']


def test_registration_call_to_action_keeps_nearby_named_event():
    text='CMO Summit 2027. Choose your pass to join us May 11–12, 2027.'
    assert A.inspect(EVENT,lambda url: page(text))['reasons'] == []


def test_parent_general_admission_does_not_unlock_named_vip_dinner():
    event=dict(EVENT,name='VIP Dinner')
    text='VIP Dinner May 11–12, 2027. VIP dinner is invite-only. General admission is open.'
    assert A.inspect(event,lambda url: page(text))['reasons']


def test_parent_general_admission_does_not_unlock_named_vip_suite():
    event=dict(EVENT,name='VIP Suite Experience')
    text='VIP Suite Experience May 11–12, 2027. VIP suite is invite-only. General admission is open.'
    assert A.inspect(event,lambda url: page(text))['reasons']
@pytest.mark.parametrize('row,accepted', [
    ({'name':'CMO Summit','startDate':'2027-05-11T09:00:00-07:00','endDate':'2027-05-12T17:00:00-07:00'},True),
    ({'name':'Parent Conference','startDate':'2027-05-11','endDate':'2027-05-12'},False),
    ({'name':'CMO Summit','startDate':'2026-05-11','endDate':'2026-05-12'},False),
    ({'name':'CMO Summit','startDate':'2027-05-11'},False),
    ({'name':'CMO Summit Europe','startDate':'2027-05-11','endDate':'2027-05-12'},False),
    ({'name':'CMO Summit','startDate':'2027-05-11','endDate':'2027-05-12','eventStatus':'https://schema.org/EventCancelled'},False),
    ({'name':'CMO Summit','startDate':'2027-05-11','endDate':'2027-05-12','offers':{'availability':'https://schema.org/SoldOut'}},False),
])
def test_structured_organizer_edition_does_not_borrow_parent_or_old_dates(row, accepted):
    fetched = dict(status='blocked',http_status=200,text='Please enable JavaScript',spa='react',structured_events=[row])
    result = A.inspect(EVENT, lambda url: fetched)
    assert (not result['reasons']) == accepted
    if accepted:
        assert result['support'] == 'organizer_structured_name_and_dates'
        assert result['checks'][0]['structured_snapshot']['text_sha256']
        assert result['checks'][0]['read_mode'] == 'javascript_fallback'


@pytest.mark.parametrize('override', [{'truncated':True},{'http_status':403},{'final_url':'https://foreign.example/'}])
def test_structured_evidence_does_not_override_fetch_boundaries(override):
    fetched = dict(status='blocked',http_status=200,text='',structured_events=[
        {'name':'CMO Summit','startDate':'2027-05-11','endDate':'2027-05-12'}])
    fetched.update(override)
    assert A.inspect(EVENT,lambda url:fetched)['reasons']


def test_extract_only_typed_event_jsonld_and_ignore_broken_scripts():
    from tracker.event_intel_structured import events
    markup = '<script>window.event={"name":"Invented"}</script>'
    markup += '<script type="application/ld+json">not json</script>'
    markup += '<script type="application/ld+json">{"@graph":[{"@type":"WebPage","name":"Parent"},{"@type":"Event","name":"CMO Summit","startDate":"2027-05-11"}]}</script>'
    assert events(markup) == [{'name':'CMO Summit','startDate':'2027-05-11'}]


@pytest.mark.parametrize("start,end", [
    ("2027-05-11junk", "2027-05-12"),
    ("2027-05-11T99:00:00", "2027-05-12"),
    ("2027-05-11", None),
])
def test_structured_dates_require_complete_valid_values(start, end):
    fetched = dict(status='blocked', http_status=200, text='', structured_events=[
        {'name':'CMO Summit','startDate':start,'endDate':end}])
    assert A.inspect(EVENT, lambda _: fetched)['reasons']


def test_structured_metadata_cannot_clear_visible_access_restriction():
    fetched = dict(status='blocked', http_status=200,
        text='CMO Summit is invite-only.',
        structured_events=[{'name':'CMO Summit','startDate':'2027-05-11','endDate':'2027-05-12'}])
    assert A.inspect(EVENT, lambda _: fetched)['reasons']


# ── the organizer's page title ────────────────────────────────────────────
#
# Money20/20 USA, 2026-09-29: the body says "fintech's #1 event in Las Vegas
# October 18-21, 2026" without the name; the title names both.

M2020 = dict(name='Money20/20 USA', website='https://us.money2020.com/',
             sources=['https://us.money2020.com/'], starts_on='2026-10-18',
             ends_on='2026-10-21')
M2020_BODY = "Join 1 in 3 C-Suite leaders at fintech's #1 event in Las Vegas October 18-21, 2026."


def test_the_money2020_body_alone_is_still_refused():
    assert A.inspect(M2020, lambda url: page(M2020_BODY))['reasons']


@pytest.mark.parametrize('title', [
    'Money20/20 USA in Las Vegas | October 18-21, 2026',
    'Money20/20 USA | October 18-21, 2026',
    'Money20/20 USA 2026 | Oct 18-21, 2026',
    'Money20/20 USA, October 18-21, 2026, Las Vegas',
    'Attend Money20/20 USA - October 18-21, 2026',
])
def test_a_title_led_by_the_event_with_its_dates_admits_it(title):
    result = A.inspect(M2020, lambda url: page(M2020_BODY, titles=[title]))
    assert result['reasons'] == []
    assert result['support'] == 'organizer_title_name_and_dates'
    assert result['checks'][0]['title_excerpt'] == title
    assert result['checks'][0]['title_snapshot']['text_sha256']


@pytest.mark.parametrize('title', [
    # The parent/summit trap, in every shape a title can take it.
    'CMO Summit at Money20/20 USA | October 18-21, 2026',
    'Money20/20 USA | CMO Summit | October 18-21, 2026',
    'CMO Summit | Money20/20 USA | October 18-21, 2026',
    'Money20/20 USA at SaaStr Annual | October 18-21, 2026',
    'Money20/20 USA Women’s Forum | October 18-21, 2026',
    'Money20/20 USA and Money20/20 Europe | October 18-21, 2026',
    'Money20/20 USA in Las Vegas with SaaStr Annual October 18-21, 2026',
    'Money20/20 USA returns alongside Foo Expo | October 18-21, 2026',
    # Wrong or incomplete dates.
    'Money20/20 USA | October 18-20, 2026',
    'Money20/20 USA | October 18-21, 2025',
    'Money20/20 USA 2025 | October 18-21, 2026',
    'Money20/20 USA',
    # Dates that share a segment with something else.
    'Money20/20 USA | Europe October 18-21, 2026',
    'Money20/20 Global | October 18-21, 2026',
])
def test_a_title_that_could_be_another_events_dates_is_refused(title):
    result = A.inspect(M2020, lambda url: page(M2020_BODY, titles=[title]))
    assert result['reasons'], title
    assert result['support'] == 'unverified'


def test_a_title_cannot_rescue_an_unreadable_page():
    for over in (dict(spa='react', text='Money20/20 USA. JavaScript is disabled.'),
                 dict(truncated=True), dict(final_url='https://foreign.example/')):
        fetched = page(over.pop('text', M2020_BODY),
                       titles=['Money20/20 USA | October 18-21, 2026'], **over)
        assert A.inspect(M2020, lambda url: fetched)['reasons'], over


def test_a_title_does_not_clear_a_restricted_access_page():
    text = M2020_BODY + ' Registration is closed.'
    result = A.inspect(M2020, lambda url: page(
        text, titles=['Money20/20 USA | October 18-21, 2026']))
    assert result['support'] == 'unverified' and result['reasons']


def test_body_evidence_outranks_title_evidence_in_the_label():
    body = 'Money20/20 USA takes place October 18-21, 2026 in Las Vegas.'
    result = A.inspect(M2020, lambda url: page(
        body, titles=['Money20/20 USA | October 18-21, 2026']))
    assert result['support'] == 'literal_name_and_dates_only'


def test_the_fetcher_reads_titles_from_markup():
    from tracker.event_intel_structured import titles
    markup = ('<html><head><title>Money20/20 USA in Las Vegas | October 18-21, 2026</title>'
              '<meta property="og:title" content="Attend Money20/20 in Las Vegas October 18-21, 2026">'
              '<meta name="twitter:title" content="Money20/20 USA in Las Vegas | October 18-21, 2026">'
              '</head><body><h1>USA&#39;s #1 Fintech Event</h1></body></html>')
    assert titles(markup) == ['Money20/20 USA in Las Vegas | October 18-21, 2026',
                              'Attend Money20/20 in Las Vegas October 18-21, 2026']


def test_a_spaced_day_range_in_a_title_is_a_date_not_a_separator():
    europe = dict(M2020, name='Money20/20 Europe', website='https://europe.money2020.com/',
                  sources=['https://europe.money2020.com/'], starts_on='2027-06-08', ends_on='2027-06-10')
    result = A.inspect(europe, lambda url: page('See who attended in 2026.',
                       titles=['Money20/20 Europe in Amsterdam | 8 - 10 June 2027']))
    assert result['support'] == 'organizer_title_name_and_dates'


# ── the eight held back on 2026-09-29 (run 13) ────────────────────────────

SFF = dict(name='Singapore FinTech Festival', website='https://www.fintechfestival.sg',
           sources=['https://www.fintechfestival.sg'], starts_on='2026-11-18', ends_on='2026-11-20')
SFF_ROW = {'name': 'Singapore FinTech Festival 2026', 'startDate': '2026-11-18', 'endDate': '2026-11-20'}


@pytest.mark.parametrize('copy', [
    'The year leads into the invitation-only Insights2040 Annual Meetings, held under Chatham House rules.',
    'Calendar Tuesday 17th November time 19:00 - 22:00 Marker Jiak Kim House *By invite-only',
    'Tuesday 17th November 7:00 PM - 10:00 PM Jiak Kim House *By invite-only',
    # As the real page lays it out: time, venue and note on separate lines.
    'Tuesday 17th November\n7:00 PM - 10:00 PM\nJiak Kim House\n*By invite-only\nCheck Out the Full Agenda',
])
def test_a_restriction_on_a_side_programme_does_not_close_the_festival(copy):
    result = A.inspect(SFF, lambda url: page(copy, http_status=200, structured_events=[SFF_ROW]))
    assert result['reasons'] == [], result['reasons']
    assert result['support'] == 'organizer_structured_name_and_dates'
    obs = result['checks'][0]['access_observations']
    assert obs and obs[0]['scope'] in ('named_other_programme', 'timed_session')


@pytest.mark.parametrize('copy', [
    'Singapore FinTech Festival is an invitation-only Executive Summit.',
    'Singapore FinTech Festival is invitation-only this year.',
    'The invitation-only Singapore FinTech Festival returns.',
    'Admission is invitation-only.',
    'This is an invitation-only gathering of regulators.',
    'Tuesday 17th November 7:00 PM - 10:00 PM. Registration closed.',
    'Tuesday 17th November 7:00 PM - 10:00 PM Sold out.',
    'The festival is sold out.',
    'Doors open 9:00 - 18:00. Singapore FinTech Festival is invite-only.',
    'Singapore FinTech Festival 9:00 - 18:00\n*By invite-only',
    'Registration\n*By invite-only\nCheck Out the Full Agenda',
    # A pass is the festival's own inventory, not another programme.
    'Entry is by the invitation-only Delegate Pass.',
    # The next LINE is a different item, not what the restriction modifies.
    'Registration\n*By invite-only\nFounders Dinner',
])
def test_a_restriction_that_could_be_the_festivals_own_still_blocks(copy):
    result = A.inspect(SFF, lambda url: page(copy, http_status=200, structured_events=[SFF_ROW]))
    assert result['reasons'], copy


@pytest.mark.parametrize('row_name', [
    'Fintech Meetup | Leading Fintech Event | Networking & Innovation',
    'Fintech Meetup, Las Vegas',
    'Fintech Meetup 2027',
])
def test_structured_data_named_with_a_tagline_or_city_is_this_event(row_name):
    ev = dict(name='Fintech Meetup', website='https://www.fintechmeetup.com/',
              sources=['https://www.fintechmeetup.com/'], starts_on='2027-02-22', ends_on='2027-02-24')
    row = {'name': row_name, 'startDate': '2027-02-22', 'endDate': '2027-02-24'}
    result = A.inspect(ev, lambda url: page('Home', http_status=200, structured_events=[row]))
    assert result['support'] == 'organizer_structured_name_and_dates', row_name


@pytest.mark.parametrize('row_name', [
    'Fintech Meetup | CMO Summit',
    'Fintech Meetup: Founders Dinner',
    'Fintech Meetup at Money20/20',
    'Fintech Meetup, powered by Foo',
    'Fintech Meetup | 2026 recap',
    'Welcome to Fintech Meetup | Leading events',
    'CMO Summit | Fintech Meetup',
])
def test_structured_data_naming_another_gathering_is_not_this_event(row_name):
    ev = dict(name='Fintech Meetup', website='https://www.fintechmeetup.com/',
              sources=['https://www.fintechmeetup.com/'], starts_on='2027-02-22', ends_on='2027-02-24')
    row = {'name': row_name, 'startDate': '2027-02-22', 'endDate': '2027-02-24'}
    result = A.inspect(ev, lambda url: page('Home', http_status=200, structured_events=[row]))
    assert result['support'] == 'unverified', row_name


NRF = dict(name="NRF 2027: Retail's Big Show", website='https://nrfbigshow.nrf.com',
           sources=['https://nrfbigshow.nrf.com'], starts_on='2027-01-10', ends_on='2027-01-12')


@pytest.mark.parametrize('text', [
    "Join your retail peers at NRF 2027: Retail’s Big Show in New York City, January 10 – 12, 2027.",
    "NRF 2027: Retail's Big Show in New York, January 10-12, 2027.",
])
def test_a_short_place_between_the_name_and_the_dates_is_allowed(text):
    result = A.inspect(NRF, lambda url: page(text))
    assert result['support'] == 'literal_name_and_dates_only', text


@pytest.mark.parametrize('text', [
    "NRF 2027: Retail's Big Show in partnership with Foo Expo, January 10-12, 2027.",
    "NRF 2027: Retail's Big Show in New York at Foo Week, January 10-12, 2027.",
    "NRF 2027: Retail's Big Show in the Innovation Conference hall, January 10-12, 2027.",
    "NRF 2027: Retail's Big Show in a very long list of many words, January 10-12, 2027.",
])
def test_anything_more_than_a_place_between_name_and_dates_is_refused(text):
    assert A.inspect(NRF, lambda url: page(text))['support'] == 'unverified', text


# ── title names the event, body calls it "the event" (Nacha, 2026-09-30) ──

SFP = dict(name='Smarter Faster Payments', website='https://payments.nacha.org/',
           sources=['https://payments.nacha.org/'], starts_on='2027-04-11', ends_on='2027-04-14')
SFP_TITLE = ['Smarter Faster Payments Conference | Payments 2027']
SFP_BODY = ('Payments 2027 is where the industry meets. Our in-person event takes place at the '
            'Gaylord National Harbor Resort & Convention Center, minutes from Washington, D.C., '
            'April 11-14, 2027. Consider Remote Connect, our virtual event, June 7-9, 2027.')


def test_a_title_named_event_dated_by_its_own_self_reference_is_admitted():
    result = A.inspect(SFP, lambda url: page(SFP_BODY, titles=SFP_TITLE))
    assert result['reasons'] == []
    assert result['support'] == 'organizer_title_name_and_self_referenced_dates'
    assert 'April 11-14, 2027' in result['checks'][0]['date_excerpt']


@pytest.mark.parametrize('body,titles,dates', [
    # The virtual sister event's dates on the same page.
    ('The virtual event takes place June 7-9, 2027.', SFP_TITLE, ('2027-06-07', '2027-06-09')),
    (SFP_BODY, SFP_TITLE, ('2027-06-07', '2027-06-09')),
    # Dates that belong to something the sentence ties the event to.
    ('The event takes place during Fintech Week, April 11-14, 2027.', SFP_TITLE, None),
    ('The event takes place alongside Money20/20, April 11-14, 2027.', SFP_TITLE, None),
    ('The event takes place at the Payments Summit, April 11-14, 2027.', SFP_TITLE, None),
    # A title that is not led by this event, names a sub-event, or another year.
    (SFP_BODY, ['Payments 2027 | Smarter Faster Payments Conference'], None),
    (SFP_BODY, ['Smarter Faster Payments Awards Dinner | Payments 2027'], None),
    (SFP_BODY, ['Smarter Faster Payments Conference | Payments 2026'], None),
    (SFP_BODY, [], None),
    # No self-reference at all, or the wrong dates.
    ('Registration opens soon. April 11-14, 2027.', SFP_TITLE, None),
    (SFP_BODY, SFP_TITLE, ('2027-04-11', '2027-04-13')),
])
def test_anything_short_of_that_shape_stays_unverified(body, titles, dates):
    event = dict(SFP, starts_on=dates[0], ends_on=dates[1]) if dates else SFP
    result = A.inspect(event, lambda url: page(body, titles=titles))
    assert result['support'] == 'unverified', (body, titles, dates)


def test_a_sentence_break_is_not_hidden_by_an_abbreviation():
    body = 'The event takes place in Washington. D.C. Payments Forum runs April 11-14, 2027.'
    assert A.inspect(SFP, lambda url: page(body, titles=SFP_TITLE))['support'] == 'unverified'


def test_a_date_in_the_next_sentence_is_not_the_events():
    body = 'The event takes place in Boston. Early pricing ends April 11-14, 2027.'
    assert A.inspect(SFP, lambda url: page(body, titles=SFP_TITLE))['support'] == 'unverified'


# ── the event's own noun, and a year in the title (MRC, 2026-09-30) ──

MRC = dict(name='MRC Vegas', website='https://merchantriskcouncil.org/events/2027/mrc-vegas-2027',
           sources=[], starts_on='2027-03-15', ends_on='2027-03-18')


@pytest.mark.parametrize('text', [
    'Vegas Skyline Photo MRC Vegas 2027 Conference 15 - 18 Mar, 2027 ARIA Resort & Casino',
    'MRC Vegas Summit March 15-18, 2027.',
])
def test_the_events_own_noun_between_name_and_dates_is_allowed(text):
    result = A.inspect(MRC, lambda url: page(text))
    assert result['support'] == 'literal_name_and_dates_only', text


@pytest.mark.parametrize('text', [
    'MRC Vegas Payments Summit 15 - 18 Mar, 2027.',
    'MRC Vegas Awards Dinner 15 - 18 Mar, 2027.',
    'MRC Vegas Workshop 15 - 18 Mar, 2027.',
    'MRC Vegas 2026 Conference 15 - 18 Mar, 2027.',
    'MRC Vegas Conference Expo 15 - 18 Mar, 2027.',
])
def test_more_than_the_events_own_noun_is_refused(text):
    assert A.inspect(MRC, lambda url: page(text))['support'] == 'unverified', text


@pytest.mark.parametrize('titles,support', [
    (['MRC Vegas 2027'], 'organizer_title_name_and_self_referenced_dates'),
    (['MRC Vegas 2027 | MRC Conferences'], 'organizer_title_name_and_self_referenced_dates'),
    (['MRC Vegas 2026'], 'unverified'),
    (['MRC Vegas 2027 Awards'], 'unverified'),
    (['MRC Vegas Awards'], 'unverified'),
    (['MRC Vegas 3'], 'unverified'),  # a sequel number is not this year
])
def test_a_title_of_the_name_and_this_year_identifies_the_event(titles, support):
    body = 'The conference takes place at ARIA Resort, March 15-18, 2027.'
    assert A.inspect(MRC, lambda url: page(body, titles=titles))['support'] == support, titles
