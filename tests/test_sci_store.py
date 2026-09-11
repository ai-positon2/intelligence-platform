"""tracker/sci_store.py, mirroring tests/test_linkedin_playbook_store.py's
approach: a fake connection/cursor that interprets the actual SQL text this
module issues (WHERE columns, INSERT/UPDATE targets), rather than
hand-simulating "correct" behavior independently of the query -- so removing
the "AND email = %s" clause from get_run's SQL would make the cross-user
isolation test below fail for real, not just because a hardcoded fake agreed
with the original code.
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_store as store  # noqa: E402

_FIXED_TS = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


def _unwrap(v):
    return v.adapted if hasattr(v, "adapted") else v


def _where_conditions(sql):
    m = re.search(r"WHERE (.+?)(?: ORDER BY| LIMIT|$)", sql)
    if not m:
        return []
    return [(c, op.upper()) for c, op in re.findall(r"(\w+)\s*(=|ILIKE)\s*%s", m.group(1))]


def _row_matches(row, conds, params):
    for (col, op), val in zip(conds, params):
        actual = row.get(col)
        if op == "ILIKE":
            if str(val).strip("%").lower() not in str(actual or "").lower():
                return False
        elif actual != val:
            return False
    return True


def _select_columns(sql, table):
    m = re.search(r"SELECT (.+?) FROM %s" % table, sql)
    # A DISTINCT ON (col) prefix is not part of the column list.
    cols = re.sub(r"^DISTINCT ON \(\w+\)\s*", "", m.group(1))
    return [c.strip() for c in cols.split(",")]


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        params = tuple(_unwrap(p) for p in params)

        if sql.startswith("SELECT GREATEST("):
            # run_last_activity's own query -- the max updated_at across this
            # run's own row and every sci_platform_runs/sci_posts row under
            # it. Checked FIRST, and separately from the generic "FROM
            # sci_runs" branch below, because this query's nested subqueries
            # contain "FROM sci_runs" as a substring too and would otherwise
            # be misrouted into that branch's column-parsing regex. Computed
            # here the same way Postgres's GREATEST(...) would, over the fake
            # tables, rather than hand-asserting an answer that never
            # actually depended on the SQL's own run_id placement.
            run_id = params[0]
            timestamps = [r["updated_at"] for r in self.db.runs if r["id"] == run_id]
            timestamps += [p["updated_at"] for p in self.db.platform_runs if p["run_id"] == run_id]
            timestamps += [p["updated_at"] for p in self.db.posts if p["run_id"] == run_id]
            self._result = [(max(timestamps),)] if timestamps else [(None,)]
            return

        if sql.startswith("INSERT INTO sci_runs"):
            row = {"id": self.db.next_run_id, "email": params[0], "company_name": params[1],
                  "company_url": params[2], "company_logo": params[3], "status": "running",
                  "error": None, "identify_result": None, "synthesis": None, "reddit_pulse": None,
                  "created_at": _FIXED_TS, "updated_at": _FIXED_TS}
            self.db.runs.append(row)
            self.db.next_run_id += 1
            self._result = [(row["id"],)]
            return

        if sql.startswith("UPDATE sci_runs SET status"):
            status, error, identify_result, synthesis, reddit_pulse, run_id = params
            for r in self.db.runs:
                if r["id"] == run_id:
                    r["status"] = status
                    r["error"] = error
                    if identify_result is not None:
                        r["identify_result"] = identify_result
                    if synthesis is not None:
                        r["synthesis"] = synthesis
                    if reddit_pulse is not None:
                        r["reddit_pulse"] = reddit_pulse
            self._result = []
            return

        if sql.startswith("SELECT") and "FROM sci_runs" in sql:
            cols = _select_columns(sql, "sci_runs")
            conds = _where_conditions(sql)
            matches = [r for r in self.db.runs if _row_matches(r, conds, params)]
            if "created_at DESC" in sql:
                matches = sorted(matches, key=lambda r: r["id"], reverse=True)
            distinct_on = re.search(r"DISTINCT ON \((\w+)\)", sql)
            if distinct_on:
                seen, deduped = set(), []
                for r in matches:
                    marker = r.get(distinct_on.group(1))
                    if marker in seen:
                        continue
                    seen.add(marker)
                    deduped.append(r)
                matches = deduped
            if "LIMIT %s" in sql:
                matches = matches[:params[len(conds)]]
            self._result = [tuple(r.get(c) for c in cols) for r in matches]
            return

        if sql.startswith("INSERT INTO sci_platform_runs"):
            cols_m = re.search(r"\(([^)]+)\)\s*VALUES", sql)
            cols = [c.strip() for c in cols_m.group(1).split(",")]
            values = dict(zip(cols, params))
            existing = next((p for p in self.db.platform_runs
                             if p["run_id"] == values["run_id"] and p["platform"] == values["platform"]), None)
            if existing:
                existing.update(values)
                new_id = existing["id"]
            else:
                row = {"id": self.db.next_pr_id, "handle": None, "handle_confidence": None,
                      "status": "pending", "status_detail": None, "post_count": 0,
                      "last_post_at": None, "window_start": None, "window_end": None,
                      "collected_at": None, "analyzed_at": None, "error": None,
                      "created_at": _FIXED_TS, "updated_at": _FIXED_TS}
                row.update(values)
                self.db.platform_runs.append(row)
                new_id = row["id"]
                self.db.next_pr_id += 1
            self._result = [(new_id,)]
            return

        if sql.startswith("SELECT") and "FROM sci_platform_runs" in sql:
            cols_m = re.search(r"SELECT (.+?) FROM sci_platform_runs", sql)
            cols = [c.strip() for c in cols_m.group(1).split(",")]
            conds = _where_conditions(sql)
            matches = [r for r in self.db.platform_runs if _row_matches(r, conds, params)]
            self._result = [tuple(r.get(c) for c in cols) for r in matches]
            return

        if sql.startswith("INSERT INTO sci_posts"):
            (run_id, platform, pid, post_url, post_type, caption, posted_at,
             media_urls, metrics, raw) = params
            existing = next((p for p in self.db.posts
                             if p["run_id"] == run_id and p["platform"] == platform
                             and p["platform_post_id"] == pid), None)
            if existing:
                existing.update({"post_url": post_url, "metrics": metrics, "raw": raw})
            else:
                self.db.posts.append({
                    "id": self.db.next_post_id, "run_id": run_id, "platform": platform,
                    "platform_post_id": pid, "post_url": post_url, "post_type": post_type,
                    "caption": caption, "posted_at": posted_at, "media_urls": media_urls,
                    "metrics": metrics, "raw": raw, "creative_analysis": None,
                    "creative_analysis_status": "pending", "creative_analysis_error": None,
                    "created_at": _FIXED_TS, "updated_at": _FIXED_TS,
                })
                self.db.next_post_id += 1
            self._result = []
            return

        if sql.startswith("UPDATE sci_posts SET creative_analysis"):
            analysis, status, error, post_id = params
            for p in self.db.posts:
                if p["id"] == post_id:
                    p["creative_analysis"] = analysis
                    p["creative_analysis_status"] = status
                    p["creative_analysis_error"] = error
            self._result = []
            return

        if sql.startswith("SELECT raw->'snippet'->>'channelId' FROM sci_posts"):
            # youtube_channel_id_from_posts' own query. Each condition is
            # applied only if the SQL TEXT actually carries it (a JSON path
            # expression doesn't fit _where_conditions' "col = %s" regex, so
            # "platform = 'youtube'" and the IS NOT NULL check are read
            # straight off the string instead) -- dropping any one of them
            # in the real code changes what this fake matches too, the same
            # way it would change what Postgres matches, rather than a fixed
            # assertion an exception handler upstream would just swallow.
            conds = _where_conditions(sql)
            scope_platform = "platform = 'youtube'" in sql
            require_channel_id = "IS NOT NULL" in sql
            matches = []
            for p in self.db.posts:
                if conds and not _row_matches(p, conds, params):
                    continue
                if scope_platform and p["platform"] != "youtube":
                    continue
                channel_id = (p.get("raw") or {}).get("snippet", {}).get("channelId")
                if require_channel_id and not channel_id:
                    continue
                matches.append((channel_id,))
            self._result = matches[:1] if "LIMIT 1" in sql else matches
            return

        if sql.startswith("SELECT") and "FROM sci_posts" in sql:
            cols_m = re.search(r"SELECT (.+?) FROM sci_posts", sql)
            cols = [c.strip() for c in cols_m.group(1).split(",")]
            conds = _where_conditions(sql)
            matches = [r for r in self.db.posts if _row_matches(r, conds, params)]
            self._result = [tuple(r.get(c) for c in cols) for r in matches]
            return

        if sql.startswith("INSERT INTO sci_spend_log"):
            self._result = []
            return

        raise AssertionError("FakeCursor doesn't know how to handle: %s" % sql)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class _FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _FakeDB:
    def __init__(self):
        self.runs = []
        self.platform_runs = []
        self.posts = []
        self.next_run_id = 1
        self.next_pr_id = 1
        self.next_post_id = 1


@pytest.fixture
def fake_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(store, "_pg_conn", lambda: _FakeConn(db))
    store._TABLES_READY = True
    return db


# ── Ownership scoping ────────────────────────────────────────────────────────

def test_a_run_is_invisible_to_a_different_email(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    assert store.get_run(run_id, "bob@position2.com") is None


def test_a_run_is_visible_to_its_own_owner(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    run = store.get_run(run_id, "alice@position2.com")
    assert run is not None
    assert run["company_name"] == "Acme Inc"


def test_list_runs_only_returns_that_emails_runs(fake_db):
    store.save_run("alice@position2.com", "Acme Inc")
    store.save_run("bob@position2.com", "Globex")
    runs = store.list_runs("alice@position2.com")
    assert len(runs) == 1
    assert runs[0]["company_name"] == "Acme Inc"


def test_company_logo_round_trips_through_get_and_list(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc", "acme.com", "https://cdn/acme.png")
    assert store.get_run(run_id, "alice@position2.com")["company_logo"] == "https://cdn/acme.png"
    assert store.list_runs("alice@position2.com")[0]["company_logo"] == "https://cdn/acme.png"


def test_company_logo_defaults_to_none_when_not_picked_from_a_suggestion(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc", "acme.com")
    assert store.get_run(run_id, "alice@position2.com")["company_logo"] is None


def test_known_companies_surfaces_the_stored_logo(fake_db):
    store.save_run("alice@position2.com", "Google", "google.com", "https://cdn/google.png")
    found = store.search_known_companies("alice@position2.com", "goo")
    assert found[0]["logo"] == "https://cdn/google.png"


def test_known_companies_match_by_partial_name(fake_db):
    store.save_run("alice@position2.com", "Google", "google.com")
    store.save_run("alice@position2.com", "Myntra", "myntra.com")
    found = store.search_known_companies("alice@position2.com", "goo")
    assert [c["name"] for c in found] == ["Google"]
    assert found[0]["website"] == "google.com"
    assert found[0]["from_history"] is True


def test_known_companies_are_scoped_to_the_asking_user(fake_db):
    """One user's analyzed-company list must never leak into another's
    search, the same ownership property every read in this module guarantees."""
    store.save_run("alice@position2.com", "Google", "google.com")
    assert store.search_known_companies("bob@position2.com", "goo") == []


def test_known_companies_collapse_repeat_analyses_of_one_company(fake_db):
    for _ in range(3):
        store.save_run("alice@position2.com", "Google", "google.com")
    found = store.search_known_companies("alice@position2.com", "google")
    assert len(found) == 1


def test_known_companies_is_case_insensitive(fake_db):
    store.save_run("alice@position2.com", "Google", "google.com")
    assert len(store.search_known_companies("alice@position2.com", "GOOGLE")) == 1


def test_known_companies_needs_a_query(fake_db):
    store.save_run("alice@position2.com", "Google", "google.com")
    assert store.search_known_companies("alice@position2.com", "  ") == []


def test_known_companies_honours_the_limit(fake_db):
    for i in range(5):
        store.save_run("alice@position2.com", "Acme %d" % i, None)
    assert len(store.search_known_companies("alice@position2.com", "acme", limit=2)) == 2


def test_known_companies_returns_empty_without_postgres(monkeypatch):
    monkeypatch.setattr(store, "_pg_conn", lambda: None)
    assert store.search_known_companies("alice@position2.com", "goo") == []


def test_update_run_status_is_not_ownership_scoped(fake_db):
    # By design -- the background worker knows its own run_id and never
    # accepts a caller-supplied one, so this call has no email to check.
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    assert store.update_run_status(run_id, "done") is True
    run = store.get_run(run_id, "alice@position2.com")
    assert run["status"] == "done"


# ── Platform runs ────────────────────────────────────────────────────────────

def test_upsert_platform_run_creates_then_updates_the_same_row(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "instagram", status="identifying", handle="acme")
    store.upsert_platform_run(run_id, "instagram", status="ok", post_count=12)
    rows = store.get_platform_runs(run_id)
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["post_count"] == 12
    assert rows[0]["handle"] == "acme"  # earlier field survives a later partial update


def test_source_vendor_round_trips_through_upsert_platform_run(fake_db):
    """Added alongside Unipile as a second collection vendor -- which vendor
    actually served a platform (unipile / apify / youtube_api) must persist
    and read back exactly like any other field."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "linkedin", status="ok", post_count=8, source_vendor="unipile")
    rows = store.get_platform_runs(run_id)
    assert rows[0]["source_vendor"] == "unipile"


def test_profile_url_round_trips_through_upsert_platform_run(fake_db):
    """The bug this guards against: sci_identify resolves a profile_url per
    platform, but the column/allowlist to actually persist it did not exist
    -- the account directory could never link to a platform no matter how
    successfully it was identified."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "instagram", status="identifying", handle="acme",
                              profile_url="https://instagram.com/acme")
    rows = store.get_platform_runs(run_id)
    assert rows[0]["profile_url"] == "https://instagram.com/acme"
    # A later partial update (collection finishing) must not wipe it out --
    # the same "earlier field survives" contract every other column gets.
    store.upsert_platform_run(run_id, "instagram", status="ok", post_count=12)
    rows = store.get_platform_runs(run_id)
    assert rows[0]["profile_url"] == "https://instagram.com/acme"


# ── canonical_profile_url ────────────────────────────────────────────────────
# The bug this whole function exists to fix: identify_handles() is a language
# model told to return a profile_url alongside each handle, and it does not
# reliably do so. A handle can resolve, collection can succeed with real
# posts, and profile_url still comes back empty -- "Every account, in one
# place" then shows LINK NOT CAPTURED next to a platform whose own post count
# proves the account was found. This builds the link instead from the handle
# that actually fetched those posts.

@pytest.mark.parametrize("platform,handle,expected", [
    ("instagram", "acme", "https://www.instagram.com/acme/"),
    ("instagram", "@acme", "https://www.instagram.com/acme/"),
    ("facebook", "Acme.Co", "https://www.facebook.com/Acme.Co/"),
    ("tiktok", "@acmeco", "https://www.tiktok.com/@acmeco"),
    ("x", "@acme", "https://x.com/acme"),
    ("reddit", "u/acmeco", "https://www.reddit.com/user/acmeco/"),
    ("reddit", "acmeco", "https://www.reddit.com/user/acmeco/"),
])
def test_canonical_profile_url_builds_the_right_shape_per_platform(platform, handle, expected):
    assert store.canonical_profile_url(platform, handle) == expected


def test_canonical_profile_url_passes_through_a_handle_that_is_already_a_url():
    """identify's language model is free to hand back a full URL as "handle"
    for any platform, not just LinkedIn (see company_slug's own docstring) --
    wrapping one in another platform's template would double it up into
    garbage like https://www.instagram.com/https://instagram.com/acme/."""
    url = "https://www.instagram.com/acme.official/"
    assert store.canonical_profile_url("instagram", url) == url


def test_canonical_profile_url_is_none_for_an_empty_handle():
    assert store.canonical_profile_url("instagram", "") is None
    assert store.canonical_profile_url("instagram", None) is None


def test_canonical_profile_url_is_none_for_an_unknown_platform():
    assert store.canonical_profile_url("mastodon", "acme") is None


def test_canonical_profile_url_linkedin_prefers_the_confirmed_slug_from_the_note():
    """note["public_identifier"] is LinkedIn's own API confirming which page
    was actually read -- more trustworthy than the raw handle, which
    identify's language model can hand back as "position2", "@position2",
    "company/position2", or a full URL."""
    url = store.canonical_profile_url(
        "linkedin", "some raw guess identify made", note={"public_identifier": "acme-corp"})
    assert url == "https://www.linkedin.com/company/acme-corp/"


def test_canonical_profile_url_linkedin_falls_back_to_the_handle_without_a_note():
    """No note (the Apify path, or a read-time backfill with nothing but the
    stored handle) still gets a link, normalized the same way
    sci_source_linkedin_unipile.company_slug normalizes it everywhere else."""
    assert (store.canonical_profile_url("linkedin", "company/acme-corp/")
            == "https://www.linkedin.com/company/acme-corp/")


def test_canonical_profile_url_youtube_needs_the_resolved_channel_id():
    """There is no offline way to turn identify's raw "handle" guess into a
    channel URL -- it can be a channel id, an @handle, a company-name guess,
    or a full URL, and disambiguating those is exactly what the YouTube API
    call already did during collection. Without that resolved id, this
    returns None rather than a guess that might point at the wrong channel."""
    assert store.canonical_profile_url("youtube", "@acmeco") is None
    assert (store.canonical_profile_url("youtube", "@acmeco", youtube_channel_id="UC12345")
            == "https://www.youtube.com/channel/UC12345")


# ── get_platform_runs' read-time fallback ────────────────────────────────────

def test_get_platform_runs_backfills_a_missing_link_for_a_pre_fix_row(fake_db):
    """The actual screenshot this guards against: a row from before
    run_platform_collection started storing profile_url itself, with real
    posts and a real handle, but no link -- must not keep reading as LINK NOT
    CAPTURED forever."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "instagram", status="ok", post_count=20, handle="acmeco")
    rows = store.get_platform_runs(run_id)
    assert rows[0]["profile_url"] == "https://www.instagram.com/acmeco/"


def test_get_platform_runs_never_overwrites_an_already_stored_link(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "instagram", status="ok", post_count=20, handle="acmeco",
                              profile_url="https://www.instagram.com/the.real.page/")
    rows = store.get_platform_runs(run_id)
    assert rows[0]["profile_url"] == "https://www.instagram.com/the.real.page/"


@pytest.mark.parametrize("status", ["identifying", "handle_not_found", "scrape_failed", "collecting"])
def test_get_platform_runs_does_not_backfill_a_status_that_never_confirmed_the_page(fake_db, status):
    """A handle can be stored long before anything about it is confirmed
    (identifying), or after collection outright failed (scrape_failed) -- in
    neither case has this run actually verified the handle points anywhere,
    so guessing a link would be worse than leaving it blank."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "instagram", status=status, handle="acmeco")
    rows = store.get_platform_runs(run_id)
    assert rows[0]["profile_url"] is None


def test_get_platform_runs_backfills_no_presence_too(fake_db):
    """no_presence means the page answered with nothing in it, not that
    nothing was ever confirmed -- the link is still worth showing."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "facebook", status="no_presence", post_count=0, handle="acmeco")
    rows = store.get_platform_runs(run_id)
    assert rows[0]["profile_url"] == "https://www.facebook.com/acmeco/"


def test_youtube_channel_id_from_posts_ignores_a_different_runs_video(fake_db):
    """Scoped to run_id, not just platform -- a channel id belonging to some
    OTHER company's run must never leak into this one's backfill."""
    other_run = store.save_run("alice@position2.com", "Some Other Co")
    store.upsert_posts(other_run, "youtube", [
        {"platform_post_id": "v1", "post_url": None, "post_type": "video", "caption": "",
         "posted_at": None, "media_urls": [], "metrics": {},
         "raw": {"snippet": {"channelId": "UC-WRONG"}}},
    ])
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    assert store.youtube_channel_id_from_posts(run_id) is None


def test_youtube_channel_id_from_posts_ignores_a_post_missing_the_field(fake_db):
    """Defensive against the field simply not being where it's expected --
    read as "nothing to recover" rather than raising."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_posts(run_id, "youtube", [
        {"platform_post_id": "v1", "post_url": None, "post_type": "video", "caption": "",
         "posted_at": None, "media_urls": [], "metrics": {}, "raw": {"snippet": {}}},
    ])
    assert store.youtube_channel_id_from_posts(run_id) is None


def test_youtube_channel_id_from_posts_skips_past_one_with_no_channel_id(fake_db):
    """LIMIT 1 with no ORDER BY means whichever row Postgres happens to
    return first -- if that one lacks a channelId, the query itself (not
    Python re-checking the result afterward) must be what skips it, or a run
    whose FIRST stored video happens to be missing the field would stay
    unbackfilled even though a later one has it."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_posts(run_id, "youtube", [
        {"platform_post_id": "v1", "post_url": None, "post_type": "video", "caption": "",
         "posted_at": None, "media_urls": [], "metrics": {}, "raw": {"snippet": {}}},
        {"platform_post_id": "v2", "post_url": None, "post_type": "video", "caption": "",
         "posted_at": None, "media_urls": [], "metrics": {},
         "raw": {"snippet": {"channelId": "UC12345"}}},
    ])
    assert store.youtube_channel_id_from_posts(run_id) == "UC12345"


def test_youtube_channel_id_from_posts_ignores_a_different_platforms_post(fake_db):
    """Scoped to platform, not just run_id -- a post recorded for some other
    platform in the SAME run must never be read as a YouTube channel id,
    however its raw JSON happens to be shaped."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_posts(run_id, "instagram", [
        {"platform_post_id": "p1", "post_url": None, "post_type": "image", "caption": "",
         "posted_at": None, "media_urls": [], "metrics": {},
         "raw": {"snippet": {"channelId": "UC-NOT-YOUTUBE"}}},
    ])
    assert store.youtube_channel_id_from_posts(run_id) is None


def test_youtube_channel_id_from_posts_returns_none_without_postgres(monkeypatch):
    monkeypatch.setattr(store, "_pg_conn", lambda: None)
    assert store.youtube_channel_id_from_posts(1) is None


def test_get_platform_runs_backfills_youtube_from_its_own_stored_videos(fake_db):
    """A pre-fix YouTube row has no resolved channel id stored on the
    platform_runs row itself, but every video this pipeline has ever
    collected carries playlistItems.snippet.channelId in its own `raw` --
    the channel whose uploads playlist was read, which is exactly the
    channel the handle resolved to. Reading that back off an already-stored
    post is what closes the one gap canonical_profile_url can't close from
    the bare handle alone, with no live API call and no re-analysis needed."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "youtube", status="ok", post_count=22, handle="@acmeco")
    store.upsert_posts(run_id, "youtube", [
        {"platform_post_id": "v1", "post_url": "https://www.youtube.com/watch?v=v1",
         "post_type": "video", "caption": "hi", "posted_at": None, "media_urls": [],
         "metrics": {}, "raw": {"snippet": {"channelId": "UC12345"}, "statistics": {}}},
    ])
    rows = store.get_platform_runs(run_id)
    assert rows[0]["profile_url"] == "https://www.youtube.com/channel/UC12345"


def test_get_platform_runs_leaves_youtube_unbackfilled_without_a_channel_id(fake_db):
    """The one gap this fallback still can't close: a pre-fix YouTube row
    with no stored videos at all (or none carrying a channelId) has nowhere
    offline to recover a channel id from, and the raw handle alone isn't
    enough to build a trustworthy link (see canonical_profile_url). Re-
    analyzing is what fixes a row like this; this fallback can't."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "youtube", status="ok", post_count=22, handle="@acmeco")
    rows = store.get_platform_runs(run_id)
    assert rows[0]["profile_url"] is None


def test_one_platform_failing_does_not_touch_another_platforms_row(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "instagram", status="ok", post_count=5)
    store.upsert_platform_run(run_id, "youtube", status="scrape_failed", error="blocked")
    rows = {r["platform"]: r for r in store.get_platform_runs(run_id)}
    assert rows["instagram"]["status"] == "ok"
    assert rows["youtube"]["status"] == "scrape_failed"


# ── Posts ─────────────────────────────────────────────────────────────────

def test_upsert_posts_then_update_creative_analysis_round_trips(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    written = store.upsert_posts(run_id, "instagram", [
        {"platform_post_id": "p1", "post_url": "https://instagram.com/p/p1",
         "post_type": "image", "caption": "hello", "posted_at": None,
         "media_urls": ["https://cdn/p1.jpg"], "metrics": {"likes": 10}, "raw": {}},
    ])
    assert written == 1
    posts = store.get_posts(run_id, "instagram")
    assert len(posts) == 1
    assert posts[0]["creative_analysis_status"] == "pending"

    store.update_post_creative_analysis(posts[0]["id"], {"subject": "a product shot"}, status="ok")
    posts = store.get_posts(run_id, "instagram")
    assert posts[0]["creative_analysis_status"] == "ok"
    assert posts[0]["creative_analysis"]["subject"] == "a product shot"


# ── Abandoned runs: a daemon thread a process restart killed mid-flight ────
# leaves sci_runs.status='running' forever with nothing left to ever finish
# it (see sci_store.resolve_stale_run's own docs). These prove the fix
# without ever touching real wall-clock time in the test itself.

def _touch(row, when):
    row["updated_at"] = when


def test_run_last_activity_reads_the_run_row_when_nothing_else_exists(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    assert store.run_last_activity(run_id) == _FIXED_TS


def test_run_last_activity_prefers_the_freshest_platform_touch(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "instagram", status="collecting")
    later = _FIXED_TS + timedelta(minutes=5)
    _touch(fake_db.platform_runs[0], later)
    assert store.run_last_activity(run_id) == later


def test_run_last_activity_prefers_the_freshest_post_touch(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_posts(run_id, "instagram", [
        {"platform_post_id": "p1", "post_url": None, "post_type": "image", "caption": "",
         "posted_at": None, "media_urls": ["https://cdn/p1.jpg"], "metrics": {}, "raw": {}},
    ])
    later = _FIXED_TS + timedelta(minutes=9)
    _touch(fake_db.posts[0], later)
    assert store.run_last_activity(run_id) == later


def test_run_last_activity_is_none_without_postgres(monkeypatch):
    monkeypatch.setattr(store, "_pg_conn", lambda: None)
    assert store.run_last_activity(1) is None


def test_resolve_stale_run_ignores_runs_that_are_not_running(fake_db):
    """Must not even ask the question for a run that has already finished --
    a done/errored run's own error text (if any) must survive untouched."""
    run = {"id": 1, "status": "done", "error": None}
    assert store.resolve_stale_run(run) == run
    run = {"id": 2, "status": "error", "error": "a real vendor failure"}
    assert store.resolve_stale_run(run) == run


def test_resolve_stale_run_ignores_a_done_run_even_with_a_stale_activity_signal(fake_db, monkeypatch):
    """A finished run naturally goes quiet forever -- that must never be
    read as abandonment. Pins the status check ahead of the time check,
    rather than the two coincidentally agreeing only because a finished
    run's last-activity lookup happens to come back empty in other tests."""
    calls = []
    monkeypatch.setattr(store, "run_last_activity", lambda rid: calls.append(rid) or
                        (datetime.now(timezone.utc) - timedelta(days=30)))
    called_update = []
    monkeypatch.setattr(store, "update_run_status", lambda *a, **k: called_update.append(a) or True)
    run = {"id": 1, "status": "done", "error": None}
    assert store.resolve_stale_run(run) == run
    assert not called_update


def test_resolve_stale_run_leaves_a_recently_active_run_alone(fake_db, monkeypatch):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    monkeypatch.setattr(store, "run_last_activity", lambda rid: datetime.now(timezone.utc))
    run = store.get_run(run_id, "alice@position2.com")
    resolved = store.resolve_stale_run(run)
    assert resolved["status"] == "running"
    # Not just the returned dict -- the stored row itself must be untouched.
    assert store.get_run(run_id, "alice@position2.com")["status"] == "running"


def test_resolve_stale_run_leaves_a_running_run_alone_with_no_activity_signal(fake_db, monkeypatch):
    """Defensive: an unreadable signal must never be treated as proof of
    abandonment -- that would flip every run to 'error' the instant Postgres
    itself has a bad moment."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    monkeypatch.setattr(store, "run_last_activity", lambda rid: None)
    run = store.get_run(run_id, "alice@position2.com")
    assert store.resolve_stale_run(run)["status"] == "running"


def test_resolve_stale_run_flips_a_long_silent_run_to_error(fake_db, monkeypatch):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=store.STALE_RUN_MINUTES + 1)
    monkeypatch.setattr(store, "run_last_activity", lambda rid: long_ago)
    run = store.get_run(run_id, "alice@position2.com")
    resolved = store.resolve_stale_run(run)
    assert resolved["status"] == "error"
    assert "interrupted" in resolved["error"]
    # And persisted, not just returned -- the whole point is that the NEXT
    # reader (History, a reopened tab) sees it fixed too, not just this one.
    assert store.get_run(run_id, "alice@position2.com")["status"] == "error"


def _freeze_now(monkeypatch, when):
    """Pins resolve_stale_run's own datetime.now(timezone.utc) to `when`, so
    a boundary test isn't at the mercy of however many microseconds elapse
    between the test computing its input and the function computing its own
    threshold from a second, later call to the real clock -- which would
    make ANY exact-boundary assertion pass or fail by accident of timing,
    not by the >= the code actually uses."""
    monkeypatch.setattr(store, "datetime",
                        type("_FrozenDatetime", (), {"now": staticmethod(lambda tz=None: when)}))


def test_resolve_stale_run_treats_exactly_the_threshold_as_still_fresh(fake_db, monkeypatch):
    """The boundary itself, at zero drift: last activity exactly
    STALE_RUN_MINUTES old has not yet gone quiet for MORE than that long, so
    it reads as fresh (>=) -- the conservative side of the line, favoring a
    real still-working run over a fast false positive."""
    frozen_now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    _freeze_now(monkeypatch, frozen_now)
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    exactly_at = frozen_now - timedelta(minutes=store.STALE_RUN_MINUTES)
    monkeypatch.setattr(store, "run_last_activity", lambda rid: exactly_at)
    run = store.get_run(run_id, "alice@position2.com")
    assert store.resolve_stale_run(run)["status"] == "running"


def test_resolve_stale_run_treats_one_second_past_the_threshold_as_stale(fake_db, monkeypatch):
    """The other side of the same boundary, at the same zero drift."""
    frozen_now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    _freeze_now(monkeypatch, frozen_now)
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    one_second_past = frozen_now - timedelta(minutes=store.STALE_RUN_MINUTES) - timedelta(seconds=1)
    monkeypatch.setattr(store, "run_last_activity", lambda rid: one_second_past)
    run = store.get_run(run_id, "alice@position2.com")
    assert store.resolve_stale_run(run)["status"] == "error"


def test_resolve_stale_run_uses_a_generous_margin_not_a_hair_trigger(fake_db, monkeypatch):
    """One minute short of the threshold must still read as active -- this
    is the boundary a real run's own worst-case legitimate gap sits inside."""
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    just_inside = datetime.now(timezone.utc) - timedelta(minutes=store.STALE_RUN_MINUTES - 1)
    monkeypatch.setattr(store, "run_last_activity", lambda rid: just_inside)
    run = store.get_run(run_id, "alice@position2.com")
    assert store.resolve_stale_run(run)["status"] == "running"
