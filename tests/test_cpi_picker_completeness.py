"""Every filter vocabulary is reachable, and every closed list is complete.

Reported: the location picker "only shows locations till Czech Republic and not
the entire list", and other filters get stuck the same way.

Two separate defects were behind that.

1. THE CAP. suggest() returned 40 entries and every vocabulary here is larger
   than 40, so with nothing typed the picker was a hard alphabetical stop rather
   than a list. Czech Republic is the 40th location; 164 of the other 203 could
   not be browsed to. Industry lost 107 of 147, NAICS 81 of 121, SIC 95 of 135,
   technology 128 of 168. Typing narrowed the list, so a value could still be
   reached by guessing its spelling, but the picker exists precisely so that
   nobody has to guess Apollo's spelling.

2. A SHORT CLOSED LIST. The seniority filter offered nine of the eleven values
   Apollo accepts. "partner" and "head" were missing from the template, even
   though app.py's own _CPI_SENIORITY_ORDER has ranked all eleven since the page
   shipped, so the chat could reason about a Head of Marketing that the search
   panel could not ask for.

The other closed lists were checked at the same time and are complete, so they
are pinned here against the source they were checked against rather than left to
drift: departments against Apollo's fourteen documented department keys, email
status against the two values a previous audit found Apollo actually honours on
this account, and industries against the LinkedIn taxonomy Apollo classifies by.
"""

import os
import re
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from tracker import apollo_taxonomy as at  # noqa: E402
from tracker import apollo_vocab as av  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TPL = os.path.join(_ROOT, "templates", "company_people_intelligence.html")
_VOCAB = "/p2/b2b-agents/company-people-intelligence/vocab"
_INDUSTRIES = "/p2/b2b-agents/company-people-intelligence/industries"

# Bigger than any vocabulary here, so "what would an uncapped call return" is
# expressible without hardcoding a size that will change as seeds are added.
_NO_CAP = 10 ** 6


def _tpl():
    return open(_TPL, encoding="utf-8").read()


@pytest.fixture
def client(monkeypatch):
    """A picker reading the seed vocabulary alone, so the counts below are the
    seed's and not the run order's.

    Postgres is stubbed out AND the two process-level learned-value caches are
    emptied. Both are needed: _cpi_vocab_seen answers from its process cache
    before it ever looks for a connection, so a test module that ran earlier and
    recorded values (test_cpi_vocab_pickers does exactly that) would otherwise
    leak five extra locations into this one's totals. The merge itself is correct
    behaviour and is covered separately; what it must not do is make a
    completeness assertion depend on what ran first.
    """
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    monkeypatch.setattr(appmod, "_CPI_VOCAB_SEEN", {})
    monkeypatch.setattr(appmod, "_CPI_INDUSTRY_SEEN", set())
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


# ── The cap ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", sorted(av.kinds()))
def test_browsing_a_vocabulary_reaches_all_of_it(kind):
    """Nothing typed: the picker must offer the whole written-down vocabulary,
    because this is the state the reported bug was seen in."""
    assert len(av.suggest(kind, "")) == len(av.suggest(kind, "", limit=_NO_CAP))


def test_browsing_the_industry_list_reaches_all_of_it():
    assert len(at.suggest("")) == len(at.suggest("", limit=_NO_CAP))


def test_the_location_list_no_longer_stops_at_czech_republic():
    """The exact reported symptom, pinned to the value it stopped at."""
    values = [e["value"] for e in av.suggest("location", "")]
    assert "Czech Republic" in values
    after = values[values.index("Czech Republic") + 1:]
    assert after, "Czech Republic was the last entry, which is the reported bug"
    assert "Dallas, Texas" in after


@pytest.mark.parametrize("kind,query", [
    ("location", "a"), ("location", "s"), ("naics", "a"), ("sic", "e"),
    ("technology", "a"),
])
def test_a_broad_query_is_not_silently_cut(kind, query):
    """A one-letter query matches most of a vocabulary. That is the other half of
    the same bug: the list was cut to 40 with nothing saying so."""
    m = {}
    got = av.suggest(kind, query, meta=m)
    assert len(got) == m["total"]
    assert m["truncated"] is False


def test_the_cap_is_above_every_seed_vocabulary():
    """The guarantee behind the tests above. If a seed list ever grows past the
    cap this fails here rather than silently shortening a picker again."""
    for kind in av.kinds():
        assert len(av._SEEDS[kind]) <= av.PICKER_LIMIT, kind
    assert len(at.SEED_INDUSTRIES) + len(at.FAMILIES) <= at.PICKER_LIMIT


def test_both_vocabulary_modules_cap_alike():
    """One widget renders both, so two different caps would make one picker stop
    somewhere the other did not."""
    assert av.PICKER_LIMIT == at.PICKER_LIMIT


# ── Saying so when the cap is genuinely hit ──────────────────────────────────

def test_a_capped_vocabulary_reports_its_real_total():
    m = {}
    got = av.suggest("location", "", limit=5, meta=m)
    assert len(got) == 5
    assert m["total"] > 5
    assert m["truncated"] is True


def test_a_capped_industry_list_reports_its_real_total():
    m = {}
    got = at.suggest("", limit=5, meta=m)
    assert len(got) == 5
    assert m["total"] > 5
    assert m["truncated"] is True


def test_meta_is_optional():
    """Every existing caller passes no meta and must keep working."""
    assert av.suggest("location", "")
    assert at.suggest("")


# ── What the endpoints hand the picker ───────────────────────────────────────

def test_the_vocab_endpoint_reports_the_total(client):
    d = client.get(_VOCAB + "?kind=location&q=").get_json()
    assert d["total"] == len(d["entries"])
    assert d["truncated"] is False
    assert len(d["entries"]) == len(av.suggest("location", "", limit=_NO_CAP))


def test_the_industries_endpoint_reports_the_total(client):
    d = client.get(_INDUSTRIES + "?q=").get_json()
    assert d["total"] == len(d["entries"])
    assert d["truncated"] is False


def test_the_vocab_endpoint_serves_every_kind(client):
    for kind in av.kinds():
        d = client.get(_VOCAB + "?kind=%s&q=" % kind).get_json()
        assert d["entries"], kind
        assert d["total"] == len(d["entries"]), kind


# ── Seniority: the short closed list ─────────────────────────────────────────

def _ui_seniorities():
    m = re.search(r'id="fpSeniority".*?\{% for s in \[(.*?)\] %\}', _tpl(), re.S)
    assert m, "the seniority chip group moved; this test needs updating"
    return re.findall(r'\("([a-z_]+)"\s*,', m.group(1))


def test_every_apollo_seniority_is_offered():
    """Apollo accepts eleven. Nine were offered, so partners and heads of
    function could not be searched for at all."""
    assert _ui_seniorities() == list(appmod._CPI_SENIORITY_ORDER)


def test_partner_and_head_specifically_are_offered():
    """Named rather than only counted, so this still fails for the right reason
    if the order tuple itself is ever shortened."""
    ui = _ui_seniorities()
    assert "partner" in ui
    assert "head" in ui


def test_the_seniority_chips_are_in_seniority_order():
    """Owner outranks intern on screen because the tuple is ranked, not sorted."""
    ui = _ui_seniorities()
    assert ui.index("c_suite") < ui.index("vp") < ui.index("director")
    assert ui.index("partner") < ui.index("vp")
    assert ui.index("head") < ui.index("director")


# ── The other closed lists, pinned where they were verified ──────────────────

def test_every_department_apollo_accepts_is_offered():
    """Apollo's fourteen documented department-headcount keys."""
    m = re.search(r'id="fpDeptName".*?\{% for d in \[(.*?)\] %\}', _tpl(), re.S)
    assert m
    ui = set(re.findall(r'\("([a-z_]+)"\s*,', m.group(1)))
    assert ui == {
        "c_suite", "product_management", "master_engineering_technical", "design",
        "education", "master_finance", "master_human_resources",
        "master_information_technology", "master_legal", "master_marketing",
        "medical_health", "master_operations", "master_sales", "consulting",
    }


def test_the_email_status_filter_stays_deliberately_short():
    """Two values, not Apollo's four: a previous audit measured that this account
    ignores 'unverified' and 'likely to engage', and a chip that changes nothing
    is the mismatch this page exists not to have. Pinned so a later completeness
    pass does not "fix" it back."""
    m = re.search(r'id="fpEmailStatus".*?\{% for s in \[(.*?)\] %\}', _tpl(), re.S)
    assert m
    assert re.findall(r'\("([a-z_]+)"\s*,', m.group(1)) == ["verified", "unavailable"]


def test_the_industry_seed_is_the_full_linkedin_taxonomy():
    """Apollo classifies by the LinkedIn industry taxonomy, whose original list
    is 147 values. Pinned so a shortened seed is caught: the learned-value merge
    hides a short seed in production, because Apollo's own returned values fill
    the gap silently."""
    assert len(at.SEED_INDUSTRIES) == 147
    for expected in ("hospital & health care", "computer software",
                     "marketing & advertising", "writing & editing"):
        assert expected in at.SEED_INDUSTRIES
