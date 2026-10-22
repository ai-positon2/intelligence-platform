"""Ad Copy Checker: reading the text of Google and Meta ads, an MCC, the report, the cache and what the team saved (tracker/paid_copycheck.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tests.test_paid_copycheck_rules import FakeDict  # noqa: E402
from tracker import paid_copycheck as cc  # noqa: E402
from tracker import paid_copycheck_rules as rules  # noqa: E402
from tracker import paid_copycheck_settings as st  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_store  # noqa: E402

TODAY = dt.date(2026, 10, 22)
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}


def speller():
    return rules.Speller(FakeDict())


def rsa(ad="1", heads=("Quality Dental Care", "Book Today"), descs=("We offer implants.",), group="10", camp="Core Search", path=("dentist", "implants"), url="https://acme.example/a"):
    return {"campaign": {"id": "5", "name": camp}, "adGroup": {"id": group, "name": "Group " + group},
            "adGroupAd": {"ad": {"id": ad, "type": "RESPONSIVE_SEARCH_AD", "finalUrls": [url],
                                 "responsiveSearchAd": {"headlines": [{"text": h} for h in heads], "descriptions": [{"text": d} for d in descs], "path1": path[0], "path2": path[1]}}}}


def eta(ad="2", heads=("Dentist Near You", "Book Now", ""), descs=("We offer implants.", "Call us today.")):
    return {"campaign": {"id": "5", "name": "Old"}, "adGroup": {"id": "11", "name": "Group 11"},
            "adGroupAd": {"ad": {"id": ad, "type": "EXPANDED_TEXT_AD", "finalUrls": ["https://acme.example/b"],
                                 "expandedTextAd": {"headlinePart1": heads[0], "headlinePart2": heads[1], "headlinePart3": heads[2], "description": descs[0], "description2": descs[1], "path1": "a", "path2": "b"}}}}


def searcher(search=None, display=None, pmax=None, camp_assets=None, cust_assets=None, log=None, fail=(), refuse_eta=False, fail_kinds=()):
    search = search if search is not None else {ACCT["id"]: [rsa()]}

    def run(cid, q):
        if log is not None:
            log.append((cid, q))
        if cid in fail:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        if "RESPONSIVE_DISPLAY_AD" in q:
            kind = "display"
            rows = (display or {}).get(cid, [])
        elif "FROM asset_group_asset" in q:
            kind, rows = "pmax", (pmax or {}).get(cid, [])
        elif "FROM campaign_asset" in q:
            kind, rows = "campassets", (camp_assets or {}).get(cid, [])
        elif "FROM customer_asset" in q:
            kind, rows = "custassets", (cust_assets or {}).get(cid, [])
        else:
            if refuse_eta and "expanded_text_ad" in q:
                raise gads.GadsError("bad_request", "Unknown field expanded_text_ad")
            return search.get(cid, [])
        if kind in fail_kinds:
            raise gads.GadsError("api", "no %s" % kind)
        return rows
    return run


# ── Reading the answer ──────────────────────────────────────────────────────

def test_a_responsive_search_ad_is_read_with_its_headlines_descriptions_and_paths():
    a = cc.read_search_ads([rsa()])[0]
    assert a == {"id": "1", "platform": "google", "kind": "rsa", "campaignId": "5", "campaign": "Core Search", "adGroupId": "10", "adGroup": "Group 10", "url": "https://acme.example/a",
                 "texts": [{"slot": "headline", "text": "Quality Dental Care"}, {"slot": "headline", "text": "Book Today"}, {"slot": "description", "text": "We offer implants."},
                           {"slot": "path", "text": "dentist"}, {"slot": "path", "text": "implants"}]}


def test_an_old_expanded_text_ad_is_read_and_an_empty_part_and_a_repeated_text_are_not_kept():
    a = cc.read_search_ads([eta()])[0]
    assert a["kind"] == "eta" and [t["text"] for t in a["texts"] if t["slot"] == "headline"] == ["Dentist Near You", "Book Now"] and [t["slot"] for t in a["texts"]].count("path") == 2
    b = cc.read_search_ads([rsa(heads=("Same", "Same", "  Same  "), descs=("x  y",))])[0]
    assert [t["text"] for t in b["texts"]] == ["Same", "x y", "dentist", "implants"]                       # one copy, white space made single


def test_an_ad_without_an_id_or_without_any_text_is_dropped():
    blank = rsa(ad="3", heads=("", ""), descs=(), path=("", ""))
    noid = rsa(ad="")
    assert cc.read_search_ads([blank, noid]) == []


def test_the_search_query_asks_for_enabled_ads_and_the_plain_one_leaves_the_old_kind_out():
    q, p = cc.GAQL_SEARCH, cc.GAQL_SEARCH_PLAIN
    assert "ad_group_ad.status = ENABLED" in q and "campaign.status = ENABLED" in q and "ad_group.status = ENABLED" in q and "EXPANDED_TEXT_AD" in q and "expanded_text_ad.headline_part1" in q
    assert "expanded_text_ad" not in p and "RESPONSIVE_SEARCH_AD" in p and "responsive_search_ad.descriptions" in p


def test_display_ads_pmax_text_and_assets_are_read_into_the_same_shape():
    d = cc.read_display_ads([{"campaign": {"id": "6", "name": "Display"}, "adGroup": {"id": "20", "name": "G"}, "adGroupAd": {"ad": {"id": "7", "finalUrls": ["https://a.example"],
                             "responsiveDisplayAd": {"headlines": [{"text": "Short one"}], "longHeadline": {"text": "A longer headline here"}, "descriptions": [{"text": "Desc"}]}}}}])[0]
    assert d["kind"] == "display" and d["texts"] == [{"slot": "headline", "text": "Short one"}, {"slot": "longheadline", "text": "A longer headline here"}, {"slot": "description", "text": "Desc"}]
    rows = [{"campaign": {"id": "8", "name": "PMax"}, "assetGroup": {"id": "30", "name": "Implants"}, "assetGroupAsset": {"fieldType": f}, "asset": {"id": str(n), "textAsset": {"text": t}}}
            for n, (f, t) in enumerate([("HEADLINE", "Dentist"), ("DESCRIPTION", "We care"), ("HEADLINE", "Dentist"), ("MARKETING_IMAGE", "ignored")], 1)]
    p = cc.read_pmax_text(rows)
    assert len(p) == 1 and p[0]["id"] == "pmax:30" and p[0]["kind"] == "pmax" and p[0]["adGroup"] == "Implants" and p[0]["texts"] == [{"slot": "headline", "text": "Dentist"}, {"slot": "description", "text": "We care"}]
    assets = cc.read_assets([
        {"campaign": {"id": "5", "name": "Core"}, "asset": {"id": "90", "type": "SITELINK", "sitelinkAsset": {"linkText": "Implants", "description1": "From $99", "description2": "Book online"}}},
        {"campaign": {"id": "6", "name": "Other"}, "asset": {"id": "90", "type": "SITELINK", "sitelinkAsset": {"linkText": "Implants"}}},
        {"asset": {"id": "91", "type": "CALLOUT", "calloutAsset": {"calloutText": "Free exam"}}},
        {"asset": {"id": "92", "type": "STRUCTURED_SNIPPET", "structuredSnippetAsset": {"header": "Services", "values": ["Implants", "Whitening"]}}},
        {"asset": {"id": "93", "type": "SITELINK", "sitelinkAsset": {}}}])
    assert [(a["id"], a["kind"], a["campaign"]) for a in assets] == [("asset:90", "sitelink", "Core"), ("asset:91", "callout", "Whole account"), ("asset:92", "snippet", "Whole account")]
    assert assets[0]["texts"] == [{"slot": "sitelink", "text": "Implants"}, {"slot": "sitelinkdesc", "text": "From $99"}, {"slot": "sitelinkdesc", "text": "Book online"}]
    assert [t["slot"] for t in assets[2]["texts"]] == ["snippet", "snippet"]


def meta_ad(i="1", **creative):
    return {"id": i, "name": "Ad " + i, "effective_status": "ACTIVE", "adset": {"id": "55", "name": "Set"}, "campaign": {"id": "7", "name": "Camp"}, "creative": creative}


def test_a_meta_ad_is_read_from_every_place_meta_keeps_its_text():
    plain = cc.read_meta_ads([meta_ad(body="Primary text", title="Headline")])[0]
    assert plain["id"] == "meta:1" and plain["platform"] == "meta" and plain["kind"] == "meta" and plain["adGroup"] == "Set" and plain["campaign"] == "Camp"
    assert plain["texts"] == [{"slot": "primary", "text": "Primary text"}, {"slot": "title", "text": "Headline"}]
    link = cc.read_meta_ads([meta_ad(object_story_spec={"link_data": {"message": "M", "name": "N", "description": "D", "link": "https://x.example",
                                                                       "child_attachments": [{"name": "Card", "description": "Card text"}]}})])[0]
    assert link["texts"] == [{"slot": "primary", "text": "M"}, {"slot": "title", "text": "N"}, {"slot": "link", "text": "D"}, {"slot": "title", "text": "Card"}, {"slot": "link", "text": "Card text"}] and link["url"] == "https://x.example"
    vid = cc.read_meta_ads([meta_ad(object_story_spec={"video_data": {"message": "VM", "title": "VT", "link_description": "VD"}})])[0]
    assert [t["text"] for t in vid["texts"]] == ["VM", "VT", "VD"]
    photo = cc.read_meta_ads([meta_ad(object_story_spec={"photo_data": {"caption": "Cap"}})])[0]
    assert photo["texts"] == [{"slot": "primary", "text": "Cap"}]
    flex = cc.read_meta_ads([meta_ad(asset_feed_spec={"bodies": [{"text": "B1"}, {"text": "B2"}], "titles": [{"text": "T1"}], "descriptions": [{"text": "D1"}]})])[0]
    assert [(t["slot"], t["text"]) for t in flex["texts"]] == [("primary", "B1"), ("primary", "B2"), ("title", "T1"), ("link", "D1")]
    assert cc.read_meta_ads([meta_ad()]) == [] and cc.read_meta_ads([{"creative": {"body": "x"}}]) == []                                # no text, no id


def test_the_meta_request_asks_for_active_ads_and_the_creative_text():
    path, params = cc.meta_request("act_123")
    assert path == "/act_123/ads" and params["effective_status"] == '["ACTIVE"]' and "creative{" in params["fields"] and "object_story_spec" in params["fields"] and "asset_feed_spec" in params["fields"]


# ── One account, an MCC ─────────────────────────────────────────────────────

def test_an_account_is_read_for_search_ads_display_pmax_and_assets_in_one_list():
    s = searcher(display={ACCT["id"]: [{"campaign": {"id": "6", "name": "D"}, "adGroup": {"id": "2", "name": "G"}, "adGroupAd": {"ad": {"id": "9", "responsiveDisplayAd": {"headlines": [{"text": "H"}]}}}}]},
                 pmax={ACCT["id"]: [{"campaign": {"id": "8", "name": "P"}, "assetGroup": {"id": "30", "name": "AG"}, "assetGroupAsset": {"fieldType": "HEADLINE"}, "asset": {"id": "1", "textAsset": {"text": "Hi"}}}]},
                 camp_assets={ACCT["id"]: [{"campaign": {"id": "5", "name": "C"}, "asset": {"id": "90", "type": "CALLOUT", "calloutAsset": {"calloutText": "Open late"}}}]},
                 cust_assets={ACCT["id"]: [{"asset": {"id": "91", "type": "SITELINK", "sitelinkAsset": {"linkText": "About"}}}]})
    r = cc.read_account(ACCT["id"], s)
    assert [a["kind"] for a in r["ads"]] == ["rsa", "display", "pmax", "callout", "sitelink"] and r["warnings"] == []


def test_a_refusal_of_the_old_ad_fields_is_asked_again_without_them_but_a_spent_quota_is_raised():
    log = []
    r = cc.read_account(ACCT["id"], searcher(log=log, refuse_eta=True))
    assert [a["id"] for a in r["ads"]] == ["1"] and sum(1 for _c, q in log if "FROM ad_group_ad" in q and "RESPONSIVE_SEARCH_AD" in q and "RESPONSIVE_DISPLAY_AD" not in q) == 2

    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError, match="daily limit"):
        cc.read_account(ACCT["id"], spent)


def test_kinds_that_cannot_be_read_are_said_and_the_search_ads_still_come():
    r = cc.read_account(ACCT["id"], searcher(fail_kinds=("display", "pmax", "campassets", "custassets")))
    assert [a["id"] for a in r["ads"]] == ["1"] and len(r["warnings"]) == 4 and r["warnings"][0].startswith("The Display ads could not be read (no display)")
    assert any("Performance Max" in w for w in r["warnings"]) and any("of the account" in w for w in r["warnings"])


def test_an_mcc_reads_every_account_and_says_the_one_it_could_not_read_and_all_failing_is_raised():
    mcc = {"id": "mcc-1", "name": "Group", "members": [ACCT, ACCT2]}
    raw = cc.google_raw(mcc, search_fn=searcher(search={ACCT["id"]: [rsa()], ACCT2["id"]: [rsa()]}, fail=(ACCT2["id"],)))
    assert [a["id"] for a in raw["accounts"]] == [ACCT["id"]] and raw["warnings"] == ["Beta Clinic could not be read: Account 2222222222 cannot be read."] and raw["members"] == ["Acme Dental", "Beta Clinic"]
    with pytest.raises(gads.GadsError, match="cannot be read"):
        cc.google_raw(mcc, search_fn=searcher(fail=(ACCT["id"], ACCT2["id"])))


def test_a_meta_account_is_read_with_the_request_and_named():
    asked = []

    def pages(path, params):
        asked.append((path, params["effective_status"]))
        return [meta_ad("1", body="Hello")]
    raw = cc.meta_raw("act_123", pages_fn=pages, account_fn=lambda a: {"name": "Beta Meta", "currency": "EUR"})
    assert asked == [("/act_123/ads", '["ACTIVE"]')] and raw["accounts"][0]["name"] == "Beta Meta" and raw["accounts"][0]["platform"] == "meta" and raw["accounts"][0]["ads"][0]["id"] == "meta:1"


# ── The report and its cache ────────────────────────────────────────────────

@pytest.fixture
def cache(monkeypatch):
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")
    yield saved
    paid_geo.clear_cache()
    base.forget("")


def report(search, **kw):
    kw.setdefault("speller", speller())
    return cc.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=search, today=TODAY, **kw)


BAD = rsa(ad="1", heads=("Affordble Dentst", "Quality Dental Care"), descs=("Call 617-555-0199 now!!",))


def test_the_report_names_the_account_the_rules_and_what_it_found(cache):
    rep = report(searcher(search={ACCT["id"]: [BAD, rsa(ad="2")]}))
    a = rep["accounts"][0]
    assert rep["account"]["name"] == "Acme Dental" and rep["account"]["isGroup"] is False and a["id"] == "1234567890" and a["status"] == "fix" and a["ads"] == 2 and a["clean"] == 1
    assert [f["rule"] for f in a["items"][0]["findings"]] == ["spelling", "spelling", "punct"] and a["items"][0]["level"] == "medium"
    assert rep["summary"] == {"counts": {"fix": 1, "check": 0, "clean": 0}, "accounts": 1, "ads": 2, "clean": 1, "high": 0, "medium": 1, "low": 0, "findings": 3}
    assert [r["rule"] for r in rep["rules"]] == rules.ORDER and rep["readAt"] and rep["warnings"] == []


def test_a_second_ask_never_reads_google_again_and_refresh_does(cache):
    calls = []
    s = searcher(log=calls)
    report(s)
    n = len(calls)
    assert n >= 5
    report(s)
    assert len(calls) == n
    report(s, force=True)
    assert len(calls) > n


def test_what_the_team_saved_is_applied_to_the_saved_read_without_reading_google_again(cache):
    calls, mon = [], MemStore()
    s = searcher(search={ACCT["id"]: [BAD]}, log=calls)
    assert [f["rule"] for f in report(s, mon=mon)["accounts"][0]["items"][0]["findings"]] == ["spelling", "spelling", "punct"]
    n = len(calls)
    st.save_account(mon, ACCT["id"], "Acme Dental", {"phones": "617-555-0100", "allow": "affordble"}, "me@x.co")
    rep = report(s, mon=mon)
    got = [(f["rule"], f["level"]) for f in rep["accounts"][0]["items"][0]["findings"]]
    assert len(calls) == n and ("phone", "high") in got and ("spelling", "medium") in got and len([g for g in got if g[0] == "spelling"]) == 1
    assert rep["accounts"][0]["saved"] == {"phones": 1, "places": 0, "brand": 0, "allow": 1} and rep["accounts"][0]["items"][0]["level"] == "high"


def test_the_brand_spend_alerts_brand_words_are_read_as_the_accounts_brand(cache):
    from tracker import paid_brandspend_settings as bs
    mon = MemStore()
    bs.save_account(mon, ACCT["id"], {"brandTerms": ["smileco"]}, "me@x.co")
    ads = [rsa(ad="1", heads=("SmileCo Dental", "Visit SmileCo"), descs=("SmileCo cares.",)), rsa(ad="2", heads=("Smile Co Dental", "Quality Dental Care"), descs=("We care.",))]
    rep = report(searcher(search={ACCT["id"]: ads}), mon=mon)
    f = [f for i in rep["accounts"][0]["items"] for f in i["findings"] if f["rule"] == "brand"]
    assert len(f) == 1 and f[0]["excerpt"] == "Smile Co" and f[0]["level"] == "low"


def test_the_history_gives_an_open_problem_its_age_and_an_unreadable_one_is_said(cache):
    mon = MemStore()
    mon.issues_put([{"agent": "copycheck", "fingerprint": "copycheck:1234567890:1", "status": "open", "firstSeen": "2026-10-12T07:00:00+00:00", "lastSeen": "2026-10-21T07:00:00+00:00", "reopened": 1}])
    rep = report(searcher(search={ACCT["id"]: [BAD]}), mon=mon, asof=dt.datetime(2026, 10, 22, 8, tzinfo=dt.timezone.utc))
    it = rep["accounts"][0]["items"][0]
    assert rep["agesKnown"] is True and it["ageDays"] == 10 and it["reopened"] == 1

    class Broken(MemStore):
        def state_get(self, key, default=None):
            raise RuntimeError("db down")
    rep = report(searcher(), mon=Broken())
    assert rep["agesKnown"] is False and rep["warnings"][0].startswith("What the team saved") and "db down" in rep["warnings"][0] and rep["accounts"][0]["ads"] == 1


def test_a_server_without_a_dictionary_says_spelling_was_not_checked(cache):
    nodict = rules.Speller.__new__(rules.Speller)
    nodict.inner, nodict._cache = None, {}
    rep = report(searcher(search={ACCT["id"]: [BAD]}), speller=nodict)
    assert "spelling check could not run" in rep["warnings"][-1] and rep["accounts"][0]["spellingChecked"] is False
    assert [f["rule"] for f in rep["accounts"][0]["items"][0]["findings"]] == ["punct"]


def test_a_report_for_a_meta_account_reads_the_active_ads(cache):
    pages = lambda path, params: [meta_ad("1", body="Affordble care now!!", title="Dentst")]
    rep = cc.report_for("act_1234567", search_fn=searcher(), meta_pages_fn=pages, meta_account_fn=lambda act: {"name": "Beta Meta", "currency": "EUR"}, today=TODAY, speller=speller())
    a = rep["accounts"][0]
    assert rep["account"]["platform"] == "meta" and a["platform"] == "meta" and a["currency"] == "EUR" and a["items"][0]["platform"] == "meta"
    assert [(f["slot"], f["rule"]) for f in a["items"][0]["findings"]] == [("primary", "spelling"), ("title", "spelling"), ("primary", "punct")]


def test_an_mcc_report_puts_the_accounts_to_fix_first_and_names_the_account_in_a_warning(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    s = searcher(search={ACCT["id"]: [rsa()], ACCT2["id"]: [BAD]}, fail_kinds=("display",))
    rep = cc.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=s, today=TODAY, speller=speller())
    assert [a["name"] for a in rep["accounts"]] == ["Beta Clinic", "Acme Dental"] and rep["account"]["isGroup"] is True and rep["account"]["accountCount"] == 2
    assert [w.split(":")[0] for w in rep["warnings"][:2]] == ["Acme Dental", "Beta Clinic"] and all("The Display ads could not be read" in w for w in rep["warnings"][:2]) and rep["summary"]["counts"] == {"fix": 1, "check": 0, "clean": 1}


def test_a_read_that_lost_a_part_is_shown_but_not_saved_for_hours(cache):
    rep = report(searcher(search={ACCT["id"]: [rsa()]}, fail_kinds=("display",)))
    assert [w for w in rep["warnings"] if "Display ads" in w] == ["The Display ads could not be read (no display), so they were not checked."]      # shown once
    assert cache == {}                                                                  # the saved copy is for a whole read only: the next ask in a new process reads again
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.forget("")
    report(searcher(search={ACCT["id"]: [rsa()]}))
    assert len(cache) == 1                                                              # a whole read is saved


def test_a_spent_quota_with_nothing_saved_is_raised_and_an_account_that_is_not_position2s_is_refused(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError):
        report(spent)
    with pytest.raises(cc.BadRequest, match="not one of Position2"):
        cc.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY, speller=speller())


def test_other_places_are_the_places_saved_for_the_other_accounts():
    s = {"accounts": {"1": {"name": "Quincy Smiles", "places": ["Quincy, MA"]}, "2": {"name": "Dover Dental", "places": ["Dover, NH", "Quincy, MA"]}, "3": {"places": ["Salem, MA"]}}}
    assert cc.other_places(s, "2") == {"Quincy, MA": "Quincy Smiles", "Salem, MA": "3"} and cc.other_places(s, "9") == {"Quincy, MA": "Quincy Smiles", "Dover, NH": "Dover Dental", "Salem, MA": "3"}
