"""Pages the first Cvent run (live run 29, 2026-10-01) refused.

IMEX America and IBTM World are the two flagships of the meetings industry,
and both were left unscored. Each admit is paired with its nearest refusal.
"""
import pytest

from tracker import event_intel_admission as A

FILLER = "Agenda, speakers and venue details for buyers. " * 20


def E(name, s, e, website="https://event.example/"):
    return dict(name=name, website=website, sources=[], starts_on=s, ends_on=e)


def support(event, text=FILLER, titles=(), rows=(), final=None):
    fetched = dict(status="ok", http_status=200, text=text, titles=list(titles),
                   structured_events=list(rows), final_url=final or event["website"])
    return A.inspect(event, lambda url: fetched)["support"]


IMEX = E("IMEX America", "2026-10-13", "2026-10-15", website="https://imexamerica.com/")
IMEX_TITLE = ["IMEX America 2026—13-15 October, 2026 | IMEX Homepage | Welcome"]


# ── the organizer's home page redirecting to its own other site ──

def test_a_home_page_redirect_to_the_organizers_other_site_is_followed():
    assert support(IMEX, titles=IMEX_TITLE, final="https://america.imexevents.com/") == \
        "organizer_title_name_and_dates"


@pytest.mark.parametrize("final", ["https://foreign.example/", "https://www.eventbrite.com/e/imex-1",
                                   "https://www.linkedin.com/company/imex"])
def test_a_redirect_to_an_unrelated_or_platform_site_is_refused(final):
    assert support(IMEX, titles=IMEX_TITLE, final=final) == "unverified", final


def test_a_platform_sharing_the_stem_is_still_a_platform():
    ev = dict(IMEX, website="https://eventbriteexpo.com/")
    assert support(dict(ev, name="IMEX America"), titles=IMEX_TITLE,
                   final="https://www.eventbrite.com/e/imex-1") == "unverified"


def test_only_the_home_page_may_redirect_away():
    ev = dict(IMEX, website="https://imexamerica.com/exhibitors")
    assert support(ev, titles=IMEX_TITLE, final="https://america.imexevents.com/") == "unverified"


# ── a structured date written with a space ──

IBTM = E("IBTM World", "2026-11-17", "2026-11-19")
IBTM_ROW = dict(name="IBTM World: Meetings & Events Industry Expo",
                startDate="2026-11-17 08:00", endDate="2026-11-19 17:00")


def test_a_node_named_exactly_as_the_page_is_titled_may_describe_itself():
    assert support(IBTM, rows=[IBTM_ROW], titles=[IBTM_ROW["name"]]) == \
        "organizer_structured_name_and_dates"


def test_the_same_tagline_on_a_page_titled_otherwise_is_still_another_event():
    assert support(IBTM, rows=[IBTM_ROW], titles=["Exhibit with us"]) == "unverified"


@pytest.mark.parametrize("name", ["IBTM World: at Fira Expo", "IBTM World: Virtual Edition"])
def test_a_tie_or_an_edition_in_the_tagline_still_refuses_even_as_the_title(name):
    row = dict(IBTM_ROW, name=name)
    assert support(IBTM, rows=[row], titles=[name]) == "unverified", name


def test_a_space_separated_structured_date_is_read():
    assert A._structured_date("2026-11-17 08:00").isoformat() == "2026-11-17"
    assert A._structured_local_dates("2026-11-17 23:00Z") >= {A.date(2026, 11, 17)}


# ── "Name YEAR - Place" then the dates ──

HITEC = E("HITEC", "2027-06-28", "2027-07-01")
HITEC_TEXT = ("Future Locations\nHITEC Paris 2026 - Paris\nNovember 2\n–5, 2026\n"
              "HITEC Tokyo 2026 - Tokyo\nDecember 2\n–4, 2026\n"
              "HITEC 2027 - Orlando\nJun 28\n–Jul 1, 2027\nHITEC 2027\nOrlando, Florida\n")


def test_the_name_this_year_and_a_place_then_the_dates():
    assert support(HITEC, HITEC_TEXT) == "literal_name_and_dates_only"


def test_a_spin_offs_heading_does_not_date_the_main_event():
    assert support(E("HITEC", "2026-12-02", "2026-12-04"), HITEC_TEXT) == "unverified"


def test_the_words_before_the_year_must_be_the_name_itself():
    assert support(HITEC, "HITEC Europe 2027 - Paris\nJun 28 - Jul 1, 2027\n") == "unverified"


@pytest.mark.parametrize("line", ["MWC 2027 - Shanghai", "MWC 2027 - Virtual", "MWC 2026 - Barcelona"])
def test_an_edition_place_or_another_year_still_refuses(line):
    assert support(E("MWC", "2027-03-01", "2027-03-04"), line + "\nMarch 1-4, 2027\n") == "unverified", line
