"""Access findings from the 2026-09-30 admission audit.

Every case here was admitted clean at 5f61153: an event-wide closure read
as a pass category, a hero block read as a session, an event describing
itself read as another programme, everyday closure wording not recognised
at all, a link URL blocking a page, and a JSON-LD page admitted with its
text never read.
"""
import time

import pytest

from tracker import event_intel_admission as A

EV = dict(name='CMO Summit', website='https://event.example/', sources=[],
          starts_on='2026-09-09', ends_on='2026-09-11')
OWN = 'CMO Summit September 9-11, 2026. '
ROW = {'name': 'CMO Summit', 'startDate': '2026-09-09', 'endDate': '2026-09-11'}


def run(text, event=EV, **over):
    fetched = dict(status='ok', text=text, titles=[], **over)
    return A.inspect(event, lambda url: fetched)


def scopes(result):
    return [o['scope'] for c in result['checks'] for o in c.get('access_observations', [])]


def blocked(result):
    return any('restricted' in r for r in result['reasons'])


# ── H7: the wording a real organizer uses to close an event ──

@pytest.mark.parametrize('copy', [
    'Registration is now closed.', 'Registrations are closed.', 'Registration has closed.',
    'Registration closed.', 'Registrations have officially closed.',
    'SOLD-OUT.', 'Sold out.', 'Soldout.', 'Fully booked.', 'Fully-booked.',
    'The summit has been postponed.', 'Apply to attend.', 'Request an invitation.',
    'Request an invite.', 'At capacity.', 'Join the wait-list.', 'You will be waitlisted.',
])
def test_everyday_closure_wording_blocks(copy):
    result = run(OWN + copy)
    assert blocked(result), copy
    assert result['support'] == 'unverified'


@pytest.mark.parametrize('copy', [
    'Apply to sponsor.', 'Apply to exhibit.',            # a stand, not a seat
    'Registration closes on 1 September.',               # a deadline still ahead
    'Tickets available until fully booked.',             # conditional
    'The summit is not postponed.',                      # negated
    'What happens if the event is postponed? We refund in full.',
])
def test_near_misses_of_the_new_wording_do_not_block(copy):
    result = run(OWN + copy)
    assert result['reasons'] == [], copy


def test_a_hypothetical_postponement_is_scoped_but_a_plain_question_is_not():
    assert 'hypothetical_question' in scopes(run(OWN + 'What happens if the event is postponed? Refunds.'))
    assert blocked(run(OWN + 'Is the event postponed? Yes.'))


# ── H4: a concession pass must be what the restriction is said of ──

@pytest.mark.parametrize('copy', [
    'Apart from media passes, attendance is by invitation only.',
    'Media passes sold separately, and all attendee registration is subject to approval.',
    'Student passes available; admission is subject to approval.',
    'Press passes and all tickets are subject to approval.',
])
def test_an_event_wide_restriction_near_a_concession_pass_still_blocks(copy):
    result = run(OWN + copy)
    assert blocked(result), copy
    assert 'concession_pass' not in scopes(result)


@pytest.mark.parametrize('copy', [
    'Press passes are subject to approval.',
    'Government and Media Passes: complimentary and subject to approval.',
    'Student passes will be subject to approval.',
])
def test_a_concession_pass_that_is_the_subject_is_still_scoped(copy):
    result = run(OWN + copy)
    assert result['reasons'] == [], copy
    assert scopes(result) == ['concession_pass']


# ── H5: the event's own hours are not a session ──

HERO = '\n9 September 2026\n09:00 - 17:30\nMoscone West\nInvitation only'


@pytest.mark.parametrize('copy', [
    HERO,
    '\n9 September 2026\n9:00 AM - 5:30 PM\nMoscone West\nBy invitation',
    # An agenda, but this entry is the whole day and names no gathering.
    '\n08:00 - 09:00 Registration\n9 September 2026\n09:00 - 17:30\nMoscone West\nInvitation only',
    # A short entry on a page with no other timed entry.
    '\n10 September 2026\n19:00 - 22:00\nMoscone West\n*By invite-only',
])
def test_a_timed_block_that_may_be_the_event_itself_blocks(copy):
    result = run(OWN + copy)
    assert blocked(result), copy
    assert 'timed_session' not in scopes(result)


@pytest.mark.parametrize('copy', [
    # Outside the event's own dates: the night before (the real SFF shape).
    '\nTuesday 8th September\n7:00 PM - 10:00 PM\nJiak Kim House\n*By invite-only',
    # One of several timed entries, short.
    '\n10 September\n09:00 - 10:00 Keynote\n12:00 - 13:30 Lunch\nRoom 3\nInvitation only',
    # One of several timed entries, a named gathering.
    '\n09:00 - 17:30 Main stage\n10 September\n18:00 - 23:30 Founders Dinner\n*By invite-only',
])
def test_a_session_among_several_or_outside_the_dates_is_scoped(copy):
    result = run(OWN + copy)
    assert result['reasons'] == [], copy
    assert scopes(result) == ['timed_session']


# ── H6: the event describing itself ──

SFF = dict(name='Singapore FinTech Festival (SFF)', website='https://www.fintechfestival.sg',
           sources=[], starts_on='2026-11-18', ends_on='2026-11-20')
SFF_OWN = 'Singapore FinTech Festival November 18-20, 2026. '


@pytest.mark.parametrize('event,copy', [
    (EV, OWN + 'Our flagship gathering is an invite-only Executive Summit.'),
    (EV, OWN + 'This year the programme is an invitation-only Leadership Forum.'),
    (SFF, SFF_OWN + 'Singapore FinTech Festival is an invite-only Leadership Forum.'),
    (SFF, SFF_OWN + 'SFF is an invite-only Leadership Forum.'),
    (SFF, SFF_OWN + 'The festival remains an invitation-only Leadership Summit.'),
    # No copula, but the clause names the festival by the acronym _names strips.
    (SFF, SFF_OWN + 'Admission to SFF, the invite-only Leadership Forum.'),
])
def test_a_copula_before_the_modifier_is_the_event_itself(event, copy):
    result = run(copy, event=event)
    assert blocked(result), copy
    assert 'named_other_programme' not in scopes(result)


def test_the_real_sff_side_programme_is_still_scoped_with_the_acronym_in_the_name():
    copy = SFF_OWN + 'The year leads into the invitation-only Insights2040 Annual Meetings, held under Chatham House rules.'
    result = run(copy, event=SFF)
    assert result['reasons'] == []
    assert scopes(result) == ['named_other_programme']


# ── M5: the access scan is linear in the page ──

@pytest.mark.parametrize('unit', [
    'sold out ',                          # every match blocks
    'invite-only Founders Dinner ',       # every match is scoped
    'what happens if it is sold out ',    # every match asks a question, never ends one
    'Founders Dinner 19:00 - 22:00 invite-only ',
])
def test_hostile_text_is_read_in_linear_time(unit):
    text = unit * (200000 // len(unit))
    began = time.perf_counter()
    A._access(text, 'CMO Summit', (A.date(2026, 9, 9), A.date(2026, 9, 11)))
    assert time.perf_counter() - began < 2.0, unit


def test_the_bounded_look_ahead_still_sees_a_near_question_mark():
    copy = 'What happens if the event is postponed' + ' for a reason' * 10 + '? We refund.'
    assert 'hypothetical_question' in scopes(run(OWN + copy))


def test_observations_are_capped_but_a_late_closure_is_still_found():
    text = OWN + 'The invite-only Founders Dinner. ' * 200 + 'Registration is closed.'
    result = run(text)
    obs = result['checks'][0]['access_observations']
    assert len(obs) == A.MAX_OBSERVATIONS
    assert blocked(result)


# ── L2 and M6: the JSON-LD path ──

def test_a_link_destination_does_not_block_a_structured_page():
    result = run('Join us [https://event.example/waitlist]', http_status=200, structured_events=[ROW])
    assert result['reasons'] == []
    assert result['support'] == 'organizer_structured_name_and_dates'


def test_visible_waitlist_copy_still_blocks_a_structured_page():
    result = run('Join the waitlist [https://event.example/register]', http_status=200, structured_events=[ROW])
    assert blocked(result)


@pytest.mark.parametrize('text', ['', '   ', '[https://event.example/register]'])
def test_a_structured_page_whose_text_was_not_read_is_not_admitted_clean(text):
    fetched = dict(status='blocked', http_status=200, text=text, structured_events=[ROW])
    result = A.inspect(EV, lambda url: fetched)
    assert result['support'] == 'unverified'
    assert any('could not be verified' in r for r in result['reasons'])
    # The dates were still found in the metadata.
    assert result['checks'][0]['support'] == 'organizer_structured_name_and_dates'
