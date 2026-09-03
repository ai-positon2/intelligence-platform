"""Two pages advertised how many companies the platform tracks, and they disagreed.

The hub's "by the numbers" band said 1200+; the ABM Signal Tracker card on the
dashboard page said 1,500+. Both were typed in by hand, months apart, and neither
was wrong on purpose: the healthcare universe was the only one that existed when
the first number was written, and nobody went back to the band when CSG and
NorthStar were added. A hardcoded count is a promise to update it, and that promise
gets broken every time a dashboard is rebuilt.

So neither page holds a number any more. Each Signal Tracker dashboard is a
self-contained HTML file whose build script stamps meta.total_companies into the
payload it embeds, which makes the file itself the source of truth for what it
tracks, and _tracked_company_total() adds those up.

What is pinned here: that the derived total actually matches the dashboards, that
the rounding can only ever understate it (so the "+" is honest), that an unreadable
or missing dashboard drops the claim rather than printing a wrong or zero one, and
that the two surfaces now quote the same figure.
"""

import json
import os
import re
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import app as appmod  # noqa: E402


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


@pytest.fixture(autouse=True)
def _clear_count_cache():
    """The count is cached on (mtime, size). Tests that fake a dashboard path must
    not inherit or leave an entry."""
    appmod._COMPANY_COUNT_CACHE.clear()
    yield
    appmod._COMPANY_COUNT_CACHE.clear()


def _companies_in_payload(path):
    """Independent ground truth: parse the embedded JSON and count the rows, rather
    than trusting the meta field the app reads. If the generator ever stamps a
    total that disagrees with the data it shipped, this is what catches it."""
    src = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"(?:const|var|window\.)\s*DATA\s*=\s*", src)
    data = json.JSONDecoder().raw_decode(src, m.end())[0]
    return len(data["companies"])


# ── The count is real ───────────────────────────────────────────────────────

@pytest.mark.parametrize("account_id", sorted(appmod.ACCOUNTS))
def test_each_dashboards_stamped_total_matches_the_rows_it_embeds(account_id):
    path = appmod.ACCOUNTS[account_id]["dashboard"]
    if not path.exists():
        pytest.skip("dashboard not generated in this checkout")
    assert appmod._company_count(path) == _companies_in_payload(path)


def test_the_total_is_the_sum_across_every_account_universe():
    expected = sum(appmod._company_count(cfg["dashboard"])
                   for cfg in appmod.ACCOUNTS.values())
    assert appmod._tracked_company_total() == expected
    assert expected > 0, "no dashboard could be read at all"


def test_the_client_only_dashboard_is_not_double_counted():
    """reports/dashboard_northstar_client.html is the same 35 companies again, served
    to the client portal. Deriving from ACCOUNTS is what keeps it out of the total;
    globbing reports/*.html would have counted NorthStar twice."""
    paths = [str(cfg["dashboard"]) for cfg in appmod.ACCOUNTS.values()]
    assert len(paths) == len(set(paths))
    assert not any("northstar_client" in p for p in paths)


# ── The accounts page still reads its own per-universe count ────────────────

def test_each_account_card_still_shows_that_universes_own_count(client):
    """/p2/abm-signal-tracker/accounts prints a count per card, and it shares the reading
    code with the new total. The refactor must not have turned those into dashes."""
    body = client.get("/p2/abm-signal-tracker/accounts").get_data(as_text=True)
    for cfg in appmod.ACCOUNTS.values():
        n = appmod._company_count(cfg["dashboard"])
        assert "<span>%d</span> companies" % n in body


def test_an_unreadable_dashboard_shows_a_dash_rather_than_zero_companies(tmp_path):
    """A card reading "0 companies" claims the universe is empty; a dash says the
    file could not be read, which is the truth."""
    assert appmod._read_company_count(tmp_path / "nope.html") == "—"


# ── The rounding only ever understates ──────────────────────────────────────

def test_the_floor_never_exceeds_the_real_total():
    """The whole point of the "+": the figure shown must be a number the platform
    has passed, not one it is approaching."""
    assert appmod._tracked_company_floor() <= appmod._tracked_company_total()


def test_the_floor_rounds_down_to_the_hundred():
    total = appmod._tracked_company_total()
    assert appmod._tracked_company_floor() == (total // 100) * 100
    assert appmod._tracked_company_total() - appmod._tracked_company_floor() < 100


def test_a_total_just_under_a_round_number_does_not_round_up(monkeypatch):
    monkeypatch.setattr(appmod, "_tracked_company_total", lambda: 1599)
    assert appmod._tracked_company_floor() == 1500, "1,600+ would be a false claim"


def test_a_total_below_the_step_yields_no_claim(monkeypatch):
    monkeypatch.setattr(appmod, "_tracked_company_total", lambda: 42)
    assert appmod._tracked_company_floor() == 0


# ── Nothing readable means no claim, not a wrong one ────────────────────────

def test_a_missing_dashboard_counts_as_zero_rather_than_crashing(tmp_path):
    assert appmod._company_count(tmp_path / "nope.html") == 0


def test_a_file_without_the_meta_field_counts_as_zero(tmp_path):
    p = tmp_path / "d.html"
    p.write_text("<html>no payload here</html>")
    assert appmod._company_count(p) == 0


def test_a_non_numeric_total_counts_as_zero(tmp_path):
    p = tmp_path / "d.html"
    p.write_text('DATA = {"meta": {"total_companies": null}}')
    assert appmod._company_count(p) == 0


def test_the_band_drops_the_stat_when_nothing_can_be_counted(client, monkeypatch):
    """Better to show three stats than to tell everyone the platform tracks 0
    companies because a deploy shipped without the reports directory."""
    monkeypatch.setattr(appmod, "_tracked_company_total", lambda: 0)
    body = client.get("/p2/hub").get_data(as_text=True)
    assert "companies tracked" not in body
    assert "dashboards" in body, "the rest of the band must still render"


def test_the_abm_card_stays_a_sentence_when_nothing_can_be_counted(client, monkeypatch):
    monkeypatch.setattr(appmod, "_tracked_company_total", lambda: 0)
    body = client.get("/p2/b2b-agents").get_data(as_text=True)
    assert "across every tracked company:" in body
    assert "+ companies" not in body


# ── The two surfaces agree ─────────────────────────────────────────────────

def _band_companies(body):
    band = body.split('class="lx-stats2"', 1)[1].split("</section>", 1)[0]
    m = re.search(r'data-lxn="(\d+)"[^>]*>0</b><span>companies tracked</span>', band)
    return int(m.group(1)) if m else None


def test_the_hub_band_quotes_the_derived_figure(client):
    body = client.get("/p2/hub").get_data(as_text=True)
    assert _band_companies(body) == appmod._tracked_company_floor()


def test_the_abm_card_quotes_the_same_figure(client):
    body = client.get("/p2/b2b-agents").get_data(as_text=True)
    shown = "{:,}".format(appmod._tracked_company_floor())
    assert "across %s+ companies" % shown in body


def test_neither_surface_still_carries_the_old_hardcoded_numbers(client):
    """The exact mismatch this fixed. Both figures came from a template literal;
    if either string reappears, someone has typed a count back in."""
    hub = client.get("/p2/hub").get_data(as_text=True)
    band = hub.split('class="lx-stats2"', 1)[1].split("</section>", 1)[0]
    assert 'data-lxn="1200"' not in band
    b2b = client.get("/p2/b2b-agents").get_data(as_text=True)
    assert "1,500+ companies" not in b2b or appmod._tracked_company_floor() == 1500


def test_the_two_surfaces_cannot_disagree(client, monkeypatch):
    """Move the underlying total and both pages move together. This is the property
    that was missing before: two independent literals could not track each other."""
    monkeypatch.setattr(appmod, "_tracked_company_total", lambda: 2750)
    hub = client.get("/p2/hub").get_data(as_text=True)
    b2b = client.get("/p2/b2b-agents").get_data(as_text=True)
    assert _band_companies(hub) == 2700
    assert "across 2,700+ companies" in b2b


# ── Reading it is cheap enough for the busiest page ────────────────────────

def test_the_payload_is_read_once_per_dashboard_not_once_per_render(monkeypatch):
    """The healthcare dashboard alone is ~5MB. The hub asks for all three on every
    render, so an uncached read would put ~6.5MB of file IO on the front door."""
    reads = []
    real = appmod.Path.read_text

    def counting_read_text(self, *a, **kw):
        reads.append(str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(appmod.Path, "read_text", counting_read_text)
    first = appmod._tracked_company_total()
    after_first = len(reads)
    assert appmod._tracked_company_total() == first
    assert len(reads) == after_first, "second call re-read the dashboards"


def test_a_rebuilt_dashboard_invalidates_its_own_entry(tmp_path):
    """A refresh is the event that used to make the number a lie, so the cache must
    not be the thing that keeps it one."""
    p = tmp_path / "d.html"
    p.write_text('DATA = {"meta": {"total_companies": 10}}')
    assert appmod._company_count(p) == 10
    p.write_text('DATA = {"meta": {"total_companies": 4000}}')
    os.utime(p, (0, 0))  # a rebuild can land with any mtime, including an older one
    assert appmod._company_count(p) == 4000
