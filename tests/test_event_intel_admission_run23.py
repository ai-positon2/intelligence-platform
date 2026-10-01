"""Real organizer pages live run 23 (2026-10-01) refused.

Each was a genuine page naming the event and its full date range. Every
case that is admitted here is paired with the nearest shape that must
still be refused, so the fix cannot widen into the false admits the
2026-09-30 audit closed.
"""
import pytest

from tracker import event_intel_admission as A


def E(name, s, e):
    return dict(name=name, website='https://event.example/', sources=[], starts_on=s, ends_on=e)


def support(event, text='', titles=()):
    fetched = dict(status='ok', text=text, titles=list(titles))
    return A.inspect(event, lambda url: fetched)['support']


ASIA = E('Money20/20 Asia', '2027-04-27', '2027-04-29')
NTC = E('Nonprofit Technology Conference', '2027-03-23', '2027-03-26')
MWC = E('MWC', '2027-06-01', '2027-06-03')


# ── a leading page label is not the title's subject ──

@pytest.mark.parametrize('title', [
    'Attend | Money20/20 Asia in Bangkok | 27 - 29 April 2027',
    'FAQ | Money20/20 Asia | 27 - 29 April 2027',
    'Plan Your Trip | Money20/20 Asia | 27 - 29 April 2027',
    'Money20/20 Asia in Bangkok | 27 - 29 April 2027',
])
def test_a_page_label_before_the_name_is_skipped(title):
    assert support(ASIA, titles=[title]) == 'organizer_title_name_and_dates', title


@pytest.mark.parametrize('title', [
    # A leading segment that is another event stays the subject.
    'CMO Summit | Money20/20 Asia | 27 - 29 April 2027',
    # The label is dropped, the rest is still read: another year.
    'Attend | Money20/20 Asia 2026 | 27 - 29 April 2027',
    # And a later segment tying it to another event still refuses.
    'Attend | Money20/20 Asia | 27 - 29 April 2027 | part of Finance Week',
])
def test_a_page_label_does_not_open_the_title_to_anything_else(title):
    assert support(ASIA, titles=[title]) == 'unverified', title


# ── a region in the name makes the city its venue ──

def test_a_city_after_a_regional_name_is_its_venue():
    assert support(ASIA, 'Money20/20 Asia in Bangkok, 27 - 29 April 2027.') == 'literal_name_and_dates_only'


@pytest.mark.parametrize('text', [
    # No region in the name: the city is a spin-off edition, as before.
    'MWC Shanghai, June 1-3, 2027.',
    'MWC in Shanghai, June 1-3, 2027.',
])
def test_a_city_after_a_name_without_a_region_is_still_an_edition(text):
    assert support(MWC, text) == 'unverified', text


def test_a_regional_name_does_not_excuse_another_region():
    # The region words are still checked against the name.
    assert support(ASIA, 'Money20/20 Asia returns to Europe, 27 - 29 April 2027.') == 'unverified'


# ── a hybrid edition is one edition ──

@pytest.mark.parametrize('text', [
    '2027 Nonprofit Technology Conference Join us in Portland, OR and virtually from March 23–26, 2027 at the annual gathering.',
    'Nonprofit Technology Conference takes place in Portland and online on March 23-26, 2027.',
])
def test_in_person_and_virtually_is_the_same_edition(text):
    assert support(NTC, text) == 'literal_name_and_dates_only', text


@pytest.mark.parametrize('text', [
    # Online alone is the virtual edition.
    'Nonprofit Technology Conference takes place online on March 23-26, 2027.',
    'Nonprofit Technology Conference and virtually from March 23-26, 2027.',
    # "and" still joins another event when what follows is not "virtually".
    'Nonprofit Technology Conference in Portland and Foo Expo from March 23-26, 2027.',
    # Two places are two editions until a page says otherwise.
    'Nonprofit Technology Conference in Portland and Seattle from March 23-26, 2027.',
])
def test_virtual_without_a_place_is_still_refused(text):
    assert support(NTC, text) == 'unverified', text
