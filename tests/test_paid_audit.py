"""Account Audit: the report (tracker/paid_audit.py): account names, the shared cache, joining the two platforms."""
import datetime as dt

import pytest

from tests import audit_fakes as A
from tests import audit_meta_fakes as F
from tests.gads_fakes import FakeStore
from tracker import paid_audit as audit
from tracker import paid_audit_google_read as gread
from tracker import paid_audit_pages as pages
from tracker import paid_geo, paid_gads
from tracker import paid_gads_segments as gads
from tracker import paid_meta

TODAY = dt.date(2026, 10, 17)
NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc)
GOOGLE_ID = "1112223334"
ACCOUNT = {"id": GOOGLE_ID, "name": "Acme Dental", "currency": "USD", "timeZone": "America/New_York"}


def fresh():
    """Forget every in-memory copy (the shared cache keeps one for a minute in front of the store), so a second run in one test is a new read."""
    paid_geo.clear_cache()
    paid_gads.forget()


@pytest.fixture(autouse=True)
def fresh_caches():
    fresh()
    yield
    fresh()


class Fetcher:
    """A page fetcher double; counts calls."""

    def __init__(self, status=200):
        self.status, self.calls = status, []

    def __call__(self, url, **kw):
        self.calls.append(url)

        class R:
            status_code = self.status
            def __init__(s, u): s.url = u
            def close(s): pass
        return R(url)


def google_search(**fail_kw):
    """A search function over the healthy fixture answers; every call is recorded in `.asked`."""
    ans = {gread.CUSTOMER_GAQL: A.customer(optimizationScore=0.72), gread.CONVERSIONS_GAQL: [A.action("1", "Submit lead form")], gread.CONVERSION_VOLUME_GAQL: [A.volume("1", 40.0, value=0.0)],
           gread.LISTS_GAQL: [A.user_list(search=5000, display=5000)], gread.CAMPAIGNS_GAQL: [A.campaign("10", "Search | Implants")],
           gread.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 5000, 400, 1000.0, 40.0, share=0.6)],
           gread.CAMPAIGN_CRITERIA_GAQL: [A.criterion("10", "LOCATION"), A.criterion("10", "LANGUAGE")], gread.CAMPAIGN_NEGATIVES_GAQL: [A.negative("10")],
           gread.CAMPAIGN_LISTS_GAQL: [A.attached_list("10")], gread.NETWORK_GAQL: [A.network_row("10", "SEARCH", 1000.0, 40.0)],
           gread.AD_GROUPS_GAQL: [A.ad_group("100", "10", "Implants")], gread.KEYWORDS_GAQL: [A.keyword(qs=8, crit=str(i), text="kw %d" % i) for i in range(5)],
           gread.KEYWORD_METRICS_GAQL: [A.keyword_metrics(crit=str(i), impressions=1000, clicks=80, cost=200.0, conversions=8.0) for i in range(5)],
           gread.ADS_GAQL: [A.ad(aid="1"), A.ad(aid="2")], gread.TERMS_GAQL: [A.term("implants near me", 600.0, 60, 6.0)]}
    return A.fake_search(ans, **fail_kw)


def run(account=GOOGLE_ID, **kw):
    kw.setdefault("store", FakeStore())
    kw.setdefault("accounts_fn", lambda: [ACCOUNT])
    kw.setdefault("search_fn", google_search())
    kw.setdefault("fetcher", Fetcher())
    kw.setdefault("now", NOW)
    return audit.report_for(account, **kw)


# ── Naming the account ──────────────────────────────────────────────────────

def test_an_account_is_a_google_id_a_meta_id_or_both():
    assert audit.parse_account("1112223334") == {"google": "1112223334", "meta": None}
    assert audit.parse_account(" 1112223334 ") == {"google": "1112223334", "meta": None}
    assert audit.parse_account("act_1234567890") == {"google": None, "meta": "act_1234567890"}
    assert audit.parse_account("1234567890+act_1234567890") == {"google": "1234567890", "meta": "act_1234567890"}
    for bad in ("", None, "123", "mcc-1234567890", "act_1", "1112223334+", "+act_1234567890", "1112223334+1112223334", "1112223334 act_1234567890", "abc"):
        with pytest.raises(audit.BadRequest):
            audit.parse_account(bad)


# ── A Google audit ──────────────────────────────────────────────────────────

def test_a_google_audit_has_sections_a_score_and_no_failure_on_a_healthy_account():
    out = run()
    assert out["accounts"] == [{"platform": "google", "id": GOOGLE_ID, "name": "Acme Dental", "currency": "USD"}]
    assert [s["key"] for s in out["sections"]] == ["settings", "conversions", "links", "structure", "keywords", "ads"]
    assert out["summary"]["score"] is not None and out["summary"]["tally"]["fail"] == 0 and out["summary"]["critical"] == 0
    assert out["summary"]["platforms"].keys() == {"google"}
    assert out["warnings"] == [] and out["unread"] == [] and "stale" not in out
    assert out["readAt"] == NOW.isoformat() and out["generatedAt"] == NOW.isoformat()
    assert len(out["notes"]) == 2 and "account-level negative" in out["notes"][0]
    assert all(f["platform"] == "google" for s in out["sections"] for f in s["findings"])


def test_the_landing_pages_the_ads_use_are_checked_and_their_failures_show():
    f404 = Fetcher(404)
    out = run(fetcher=f404)
    ads = next(s for s in out["sections"] if s["key"] == "ads")
    page = next(f for f in ads["findings"] if f["id"] == "G-ADS-09")
    assert page["result"] == "fail" and f404.calls == ["https://acme.test/implants"]
    fresh()
    ok = next(f for f in next(s for s in run()["sections"] if s["key"] == "ads")["findings"] if f["id"] == "G-ADS-09")
    assert ok["result"] == "pass"


def test_a_failing_page_check_never_costs_the_audit():
    def boom(url, **kw):
        raise RuntimeError("network down")
    out = run(fetcher=boom)
    page = next(f for s in out["sections"] for f in s["findings"] if f["id"] == "G-ADS-09")
    assert page["result"] == "fail" and "RuntimeError" in page["items"][0]                  # each page's error is its own result
    fresh()

    def explode(urls, fetcher=None):
        raise RuntimeError("checker broke")
    orig = pages.check
    pages.check = explode
    try:
        out = run(store=FakeStore())
    finally:
        pages.check = orig
    page = next(f for s in out["sections"] for f in s["findings"] if f["id"] == "G-ADS-09")
    assert page["result"] == "unknown" and out["summary"]["score"] is not None


def test_page_checking_can_be_left_out_and_an_account_with_no_final_urls_has_nothing_to_check():
    out = run(check_pages=False)
    page = next(f for s in out["sections"] for f in s["findings"] if f["id"] == "G-ADS-09")
    assert page["result"] == "unknown"
    fresh()
    no_ads = A.fake_search({gread.CUSTOMER_GAQL: A.customer(), gread.ADS_GAQL: []})
    out = run(search_fn=no_ads)
    page = next(f for s in out["sections"] for f in s["findings"] if f["id"] == "G-ADS-09")
    assert page["result"] == "na"


def test_a_part_that_could_not_be_read_is_a_warning_and_its_checks_say_unknown():
    out = run(search_fn=google_search(fail={gread.KEYWORDS_GAQL}))
    assert out["warnings"] == ["The keywords could not be read (Google Ads answered 500)."] and out["unread"] == ["Google Ads: keywords"]
    kw = next(s for s in out["sections"] if s["key"] == "keywords")
    assert {f["id"]: f["result"] for f in kw["findings"]}["G-KWD-02"] == "unknown"
    assert out["summary"]["unknown"] >= 5 and out["summary"]["unknownShare"] > 0


def test_an_unreadable_account_record_is_an_error():
    with pytest.raises(gads.GadsError):
        run(search_fn=google_search(fail={gread.CUSTOMER_GAQL}))


def test_an_unknown_google_account_or_an_mcc_is_refused():
    with pytest.raises(audit.BadRequest):
        run("9999999999")
    with pytest.raises(audit.BadRequest):
        run("mcc-1112223334")


# ── The shared cache ────────────────────────────────────────────────────────

def test_the_second_report_comes_from_the_saved_read():
    store, search, fetcher = FakeStore(), google_search(), Fetcher()
    first = run(store=store, search_fn=search, fetcher=fetcher)
    asked, fetched = len(search.asked), len(fetcher.calls)
    second = run(store=store, search_fn=search, fetcher=fetcher)
    assert len(search.asked) == asked and len(fetcher.calls) == fetched
    assert second["summary"] == first["summary"] and second["readAt"] == first["readAt"]


def test_refresh_reads_again_and_is_limited_per_account():
    store, search = FakeStore(), google_search()
    run(store=store, search_fn=search)
    asked = len(search.asked)
    run(store=store, search_fn=search, force=True)
    assert len(search.asked) == 2 * asked
    with pytest.raises(paid_gads.RefreshTooSoon):
        run(store=store, search_fn=search, force=True)


def test_a_spent_quota_serves_the_last_saved_read_with_its_date():
    store, search = FakeStore(), google_search()
    run(store=store, search_fn=search)
    fresh()

    def quota(cid, gaql):
        raise gads.GadsError("quota", "Google Ads daily limit reached")
    store.cache = {k: v for k, v in store.cache.items()}
    store.old = dict(store.cache)
    store.at = {k: 0 for k in store.at}                                  # the saved copies are long out of date
    out = run(store=store, search_fn=quota, force=False)
    assert out["stale"] and out["stale"][0]["platform"] == "google" and out["accounts"][0]["id"] == GOOGLE_ID


# ── A Meta audit and both together ──────────────────────────────────────────

META_ACCOUNT = {"id": F.ACT, "name": "Acme Meta", "currency": "USD", "tz": "America/New_York", "level": 1, "platform": "meta", "status": "ACTIVE"}


@pytest.fixture
def meta_connected(monkeypatch):
    monkeypatch.setattr(paid_meta, "config_problem", lambda: "")
    monkeypatch.setattr(paid_meta, "account_by_id", lambda store, act, http=None: META_ACCOUNT if act == F.ACT else None)


def test_a_meta_audit_has_the_meta_sections(meta_connected):
    g = F.Graph()
    out = run(F.ACT, meta_get=g.get, meta_pages=g.pages)
    assert out["accounts"] == [{"platform": "meta", "id": F.ACT, "name": "Acme Meta", "currency": "USD"}]
    assert [s["key"] for s in out["sections"]] == ["m_account", "m_tracking", "m_structure", "m_ads"]
    assert out["summary"]["platforms"].keys() == {"meta"} and out["summary"]["tally"]["fail"] == 0
    assert len(out["notes"]) == 1 and "Conversions API" in out["notes"][0]


def test_both_platforms_together_are_one_report_with_a_score_for_each(meta_connected):
    g = F.Graph()
    out = run(GOOGLE_ID + "+" + F.ACT, meta_get=g.get, meta_pages=g.pages)
    assert [a["platform"] for a in out["accounts"]] == ["google", "meta"]
    assert [s["key"] for s in out["sections"]] == ["settings", "conversions", "links", "structure", "keywords", "ads", "m_account", "m_tracking", "m_structure", "m_ads"]
    assert out["summary"]["platforms"].keys() == {"google", "meta"} and len(out["notes"]) == 3
    assert out["summary"]["tally"]["total"] == sum(len(s["findings"]) for s in out["sections"])


def test_a_meta_account_the_token_cannot_read_is_refused(meta_connected):
    with pytest.raises(audit.BadRequest):
        run("act_9999999999")


def test_meta_asking_to_wait_is_an_error_the_page_can_show(meta_connected):
    g = F.Graph(quota={"campaigns"})
    with pytest.raises(gads.GadsError) as e:
        run(F.ACT, meta_get=g.get, meta_pages=g.pages)
    assert e.value.kind == "quota"


def test_meta_not_connected_is_reported_as_not_configured(monkeypatch):
    monkeypatch.setattr(paid_meta, "config_problem", lambda: "Meta Ads is not connected on this server.")
    with pytest.raises(gads.GadsError) as e:
        run(F.ACT)
    assert e.value.kind == "not_configured"


# ── Assembling ──────────────────────────────────────────────────────────────

def test_assemble_with_nothing_is_an_empty_report():
    out = audit.assemble(None, None, [], now=NOW)
    assert out["sections"] == [] and out["summary"]["score"] is None and out["readAt"] is None and out["fixFirst"] == [] and out["notes"] == []


def test_the_fix_first_list_is_the_worst_findings():
    read = A.a_read(answers={gread.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 5000, 400, 1000.0, 0.0)]})
    read["pages"] = None
    out = audit.assemble(read, None, [], now=NOW)
    first = out["fixFirst"][0]
    assert first["id"] == "G-CNV-02" and first["result"] == "fail" and first["severity"] == "critical"
    assert out["summary"]["critical"] >= 1 and out["summary"]["grade"] in ("C", "D", "F")


def test_the_parts_of_a_meta_read_that_failed_are_listed_as_not_read(meta_connected):
    g = F.Graph(fail={"campaigns", "ads"})
    out = run(F.ACT, meta_get=g.get, meta_pages=g.pages)
    assert out["unread"] == ["Meta: campaigns", "Meta: ads"] and len(out["warnings"]) == 2 and all("could not be read" in w for w in out["warnings"])
    fresh()
    both = run(GOOGLE_ID + "+" + F.ACT, meta_get=F.Graph(fail={"adSets"}).get, meta_pages=F.Graph(fail={"adSets"}).pages, search_fn=google_search(fail={gread.TERMS_GAQL}))
    assert both["unread"] == ["Google Ads: terms", "Meta: adSets"]


def test_the_key_a_saved_audit_is_filed_under_names_the_accounts_in_order():
    assert audit.account_key("1112223334") == "1112223334" and audit.account_key(" act_1234567890 ") == "act_1234567890"
    assert audit.account_key("1112223334+act_1234567890") == "1112223334+act_1234567890"
    with pytest.raises(audit.BadRequest):
        audit.account_key("nonsense")


# ── The audit's own cache: partial reads, any-age fallback, versioning ─────

def asked(search):
    return len(search.asked)


def test_a_read_with_a_part_missing_is_kept_for_five_minutes_not_an_hour():
    store, search = FakeStore(), google_search(fail={gread.TERMS_GAQL})
    first = run(store=store, search_fn=search)
    assert first["unread"] == ["Google Ads: terms"]
    n = asked(search)
    run(store=store, search_fn=search)
    assert asked(search) == n                                                    # asked again at once: the saved partial read answers
    key = audit.cache_key("audit-google", GOOGLE_ID)
    store.at[key] = store.at[key] - audit.PARTIAL_TTL_S - 5
    fresh()                                                                      # (the minute of in-memory copies is not what is being tested)
    healed = google_search()
    again = run(store=store, search_fn=healed)
    assert asked(healed) == len(gread.READS) and again["unread"] == [] and again["warnings"] == []          # the platform recovered: the next look reads again and shows it


def test_a_complete_read_stays_fresh_for_the_hour():
    store, search = FakeStore(), google_search()
    run(store=store, search_fn=search)
    key = audit.cache_key("audit-google", GOOGLE_ID)
    store.at[key] = store.at[key] - audit.PARTIAL_TTL_S - 5
    fresh()
    n = asked(search)
    run(store=store, search_fn=search)
    assert asked(search) == n
    store.at[key] = store.at[key] - audit.TTL_S
    fresh()
    run(store=store, search_fn=search)
    assert asked(search) == 2 * n                                                # past the hour it is read again


def test_a_spent_quota_serves_the_last_saved_read_even_from_another_day():
    store, search = FakeStore(), google_search()
    run(store=store, search_fn=search)
    key = audit.cache_key("audit-google", GOOGLE_ID)
    store.at[key] = store.at[key] - 3 * 86400                                     # saved three days ago
    fresh()

    def quota(cid, gaql):
        raise gads.GadsError("quota", "Google Ads daily limit reached")
    out = run(store=store, search_fn=quota)
    assert out["stale"] == [{"platform": "google", "savedAt": out["stale"][0]["savedAt"]}] and out["stale"][0]["savedAt"]
    with pytest.raises(gads.GadsError):
        run(store=FakeStore(), search_fn=quota)                                    # nothing was ever saved for this store: the error is the answer


def test_an_error_that_is_not_a_spent_quota_is_not_hidden_by_an_old_copy():
    store, search = FakeStore(), google_search()
    run(store=store, search_fn=search)
    key = audit.cache_key("audit-google", GOOGLE_ID)
    store.at[key] = store.at[key] - 3 * 86400
    fresh()

    def broken(cid, gaql):
        raise gads.GadsError("api", "Google Ads answered 500")
    with pytest.raises(gads.GadsError):
        run(store=store, search_fn=broken)


def test_a_copy_saved_under_another_version_is_never_read_as_this_one():
    store, search = FakeStore(), google_search()
    store.cache[paid_gads.CACHE_V + "audit0:audit-google:" + GOOGLE_ID] = {"customer": {}, "warnings": []}          # an older shape
    store.at[paid_gads.CACHE_V + "audit0:audit-google:" + GOOGLE_ID] = __import__("time").time()
    out = run(store=store, search_fn=search)
    assert asked(search) == len(gread.READS) and out["accounts"][0]["id"] == GOOGLE_ID and audit.cache_key("audit-google", GOOGLE_ID).startswith(paid_gads.CACHE_V + "audit" + audit.CACHE_VERSION + ":")


def test_a_google_and_a_meta_read_never_share_a_saved_copy(meta_connected):
    g = F.Graph()
    store = FakeStore()
    run(GOOGLE_ID + "+" + F.ACT, store=store, meta_get=g.get, meta_pages=g.pages)
    keys = sorted(k for k in store.cache if "audit" in k)
    assert len(keys) == 2 and keys[0] != keys[1] and any("audit-google" in k for k in keys) and any("audit-meta" in k for k in keys)
