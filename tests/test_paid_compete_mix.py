"""Competitor Analysis: the kinds of ads (tracker/paid_compete_mix.py, paid_compete_own.py), the reason a platform could not be compared, and the ideas Claude writes (paid_compete_brief.py). No network.

The Google rows below are the shape the Google Ads API really returned for the two new queries on 2026-10-28 (camelCase keys, read from two live accounts)."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from test_paid_compete import MA, keep, mad, own, picture_only  # noqa: E402,F401  (own is a fixture)
from tracker import paid_compete as pc  # noqa: E402
from tracker import paid_compete_brief as cb  # noqa: E402
from tracker import paid_compete_math as cm  # noqa: E402
from tracker import paid_compete_mix as mx  # noqa: E402
from tracker import paid_compete_own as co  # noqa: E402
from tracker import paid_compete_settings as cs  # noqa: E402

NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)
TODAY = NOW.date()


def grow(ad_type, channel="SEARCH"):
    return {"campaign": {"resourceName": "customers/1/campaigns/2", "advertisingChannelType": channel}, "adGroupAd": {"resourceName": "x", "ad": {"type": ad_type, "resourceName": "y"}}}


def prow(group, field):
    return {"assetGroup": {"resourceName": "customers/1/assetGroups/%s" % group, "id": str(group)}, "assetGroupAsset": {"resourceName": "z", "fieldType": field}}


def their(i, who, fmt, *, days=30, first="2026-09-01", cta="", networks=()):
    return {"id": "t%d" % i, "platform": "meta", "who": who, "format": fmt, "days": days, "firstShown": first, "ctaType": cta, "networks": list(networks), "headlines": ["Line %d" % i], "bodies": [],
            "media": [{"kind": "image", "url": "https://scontent.example.com/%d.jpg" % i}], "adUrl": "https://www.facebook.com/ads/library/?id=%d" % i}


def mine(fmt, *, cta="LEARN_MORE", networks=("facebook",), created="2026-06-01", automatic=False):
    return {"kind": "ad", "formats": [fmt], "ctaType": cta, "networks": list(networks), "created": created, "automatic": automatic}


# ── our ads, as the APIs give them ──────────────────────────────────────────

def test_our_google_ads_become_units_by_format_and_an_asset_group_shows_every_format_it_carries():
    rows = [grow("RESPONSIVE_SEARCH_AD"), grow("RESPONSIVE_SEARCH_AD"), grow("RESPONSIVE_DISPLAY_AD", "DISPLAY"), grow("VIDEO_RESPONSIVE_AD", "VIDEO"), grow("SOMETHING_NEW")]
    pmax = [prow(7, "HEADLINE"), prow(7, "MARKETING_IMAGE"), prow(7, "YOUTUBE_VIDEO"), prow(8, "SQUARE_MARKETING_IMAGE"), prow(9, "HEADLINE")]
    u = co.google_units(rows, pmax)
    assert [x["formats"] for x in u] == [["text"], ["text"], ["image"], ["video"], ["image", "text", "video"], ["image", "text"], ["text"]]
    assert [x["kind"] for x in u].count("pmax") == 3 and co.google_units([], []) == []


@pytest.mark.parametrize("creative,fmt,cta", [
    ({"object_story_spec": {"video_data": {"call_to_action": {"type": "SIGN_UP"}}}}, "video", "SIGN_UP"),
    ({"object_story_spec": {"link_data": {"child_attachments": [{}, {}], "call_to_action": {"type": "SHOP_NOW"}}}}, "carousel", "SHOP_NOW"),
    ({"asset_feed_spec": {"videos": [{}], "call_to_action_types": ["LEARN_MORE"]}}, "dynamic", "LEARN_MORE"),
    ({"object_story_spec": {"template_data": {"call_to_action": {"type": "shop_now"}}}}, "catalog", "SHOP_NOW"),
    ({"object_story_spec": {"link_data": {"picture": "p"}}, "call_to_action_type": "BOOK_TRAVEL"}, "image", "BOOK_TRAVEL"),
    ({"object_type": "VIDEO"}, "video", ""), ({}, "image", "")])
def test_our_meta_ad_has_the_ad_librarys_format_and_its_button(creative, fmt, cta):
    assert co.meta_format(creative) == fmt and co.meta_cta(creative) == cta


def test_our_meta_ads_keep_their_placements_and_automatic_placements_mean_everywhere():
    rows = [{"id": "1", "created_time": "2026-10-20T10:00:00+0000", "adset": {"targeting": {"publisher_platforms": ["instagram"]}}, "creative": {"object_type": "VIDEO"}},
            {"id": "2", "created_time": "2026-01-02T10:00:00+0000", "adset": {"targeting": {}}, "creative": {}}, {"no": "id"}, "junk"]
    u = co.meta_units(rows)
    assert [x["networks"] for x in u] == [["instagram"], list(mx.META_NETWORKS)] and [x["automatic"] for x in u] == [False, True] and [x["created"] for x in u] == ["2026-10-20", "2026-01-02"]


def test_a_refused_read_of_our_ads_is_said_and_never_read_as_running_nothing():
    from tracker import paid_gads_segments as gads

    def refuse(acct, q):
        raise gads.GadsError("permission", "No access to that account.")
    assert co.read_google("1", refuse)["units"] == [] and "No access" in co.read_google("1", refuse)["error"]

    def boom(path, params):
        raise RuntimeError("(#100) Tried accessing nonexisting field (targeting)")
    assert co.read_meta("act_1", boom) == {"units": [], "error": "(#100) Tried accessing nonexisting field (targeting)"}
    first = []

    def pmax_refused(acct, q):
        first.append(q)
        if q == co.GAQL_PMAX_MEDIA:
            raise gads.GadsError("bad_request", "nope")
        return [grow("RESPONSIVE_SEARCH_AD")]
    assert co.read_google("1", pmax_refused) == {"units": [{"kind": "ad", "formats": ["text"], "channel": "SEARCH"}], "error": ""}       # the asset groups are soft


def test_the_new_reads_are_in_the_live_query_check():
    from tracker import paid_gaql_check as qc
    labels = [(a, l) for a, l, _q in qc.google_queries(TODAY)]
    assert ("compete", "our ad types") in labels and ("compete", "our asset group pictures and videos") in labels
    meta = [(a, path, p) for a, _l, path, p in qc.meta_requests("act_1")]
    assert ("compete", "/act_1/ads", dict(co.meta_request("act_1")[1], limit=1)) in meta


# ── the mix ─────────────────────────────────────────────────────────────────

def pool(n_video=6, n_image=4, who=("A", "B")):
    out = [their(i, who[i % len(who)], "video", days=120) for i in range(n_video)]
    return out + [their(100 + i, who[i % len(who)], "image", days=20) for i in range(n_image)]


def test_a_format_they_use_clearly_more_than_we_do_is_a_gap():
    k = mx.compare_kind([mine("image")] * 4, pool(), "format", n_competitors=2)
    assert k["judged"] and [(g["value"], g["level"], g["their"], g["own"], g["ads"], g["who"]) for g in k["gaps"]] == [("video", "high", 60.0, 0.0, 6, 2)]
    assert [r["value"] for r in k["rows"]] == ["video", "image"] and [e["value"] for e in k["edges"]] == ["image"] and k["rows"][1]["own"] == 100.0


def test_a_gap_needs_two_competitors_when_two_were_read_and_enough_ads_on_each_side():
    one_side = pool(who=("A",)) + [their(200, "B", "image")]
    assert mx.compare_kind([mine("image")] * 4, one_side, "format", n_competitors=2)["gaps"] == []
    assert mx.compare_kind([mine("image")] * 4, pool(who=("A",)), "format", n_competitors=1)["gaps"][0]["value"] == "video"
    thin = mx.compare_kind([mine("image")] * 4, pool(3, 2), "format", n_competitors=2)
    assert not thin["judged"] and thin["why"] == "Only 5 ads of theirs say it, too few to compare (at least 8 are needed)."
    few = mx.compare_kind([mine("image")] * 2, pool(), "format", n_competitors=2)
    assert not few["judged"] and "Only 2 ads of ours" in few["why"]
    assert mx.compare_kind([], pool(), "format", n_competitors=2)["why"] == "None of our ads could be read for it."


def test_what_we_run_clearly_more_is_our_edge():
    k = mx.compare_kind([mine("carousel")] * 4, pool(), "format", n_competitors=2)
    assert [e["value"] for e in k["edges"]] == ["carousel"]


def test_buttons_and_placements_are_compared_on_meta_and_automatic_placements_count_everywhere():
    theirs = [their(i, "AB"[i % 2], "image", cta="SHOP_NOW", networks=("facebook", "instagram")) for i in range(10)]
    ours = [mine("image", cta="LEARN_MORE", networks=("facebook",)) for _ in range(4)]
    m = mx.analyse("meta", ours, theirs, n_competitors=2, today=TODAY)
    assert [g["label"] for g in m["kinds"]["cta"]["gaps"]] == ["Shop now"] and [g["label"] for g in m["kinds"]["network"]["gaps"]] == ["Instagram"]
    auto = mx.analyse("meta", [mine("image", networks=mx.META_NETWORKS, automatic=True)] * 4, theirs, n_competitors=2, today=TODAY)
    assert auto["kinds"]["network"]["gaps"] == [] and auto["automatic"] == 4
    assert list(mx.analyse("google", ours, theirs, n_competitors=2, today=TODAY)["kinds"]) == ["format"]


def test_their_formats_are_ranked_by_how_long_their_ads_run():
    d = mx.durability(pool() + [dict(their(300, "A", "carousel"), days=None)])
    assert [(r["format"], r["ads"], r["median"], r["longest"]) for r in d] == [("video", 6, 120, 120), ("image", 4, 20, 20)]


def test_freshness_compares_our_meta_start_days_and_says_google_gives_none():
    theirs = [dict(their(i, "AB"[i % 2], "image"), firstShown="2026-10-20" if i < 6 else "2026-01-01") for i in range(10)]
    f = mx.freshness([mine("image", created="2026-01-01")] * 4, theirs, today=TODAY, own_known=True)
    assert (f["their"], f["theirNew"], f["own"], f["ownNew"], f["gap"]) == (60.0, 6, 0.0, 0, "high")
    g = mx.freshness([], theirs, today=TODAY, own_known=False)
    assert g["own"] is None and g["gap"] is None and g["their"] == 60.0


def test_a_read_of_our_ads_that_failed_leaves_the_mix_unjudged_and_says_why():
    m = mx.analyse("meta", [], pool(), n_competitors=2, today=TODAY, own_error="token expired")
    assert all(not k["judged"] and "token expired" in k["why"] for k in m["kinds"].values()) and m["fresh"]["own"] is None


def test_the_mix_gaps_become_ideas_with_their_evidence():
    google = mx.analyse("google", [{"kind": "pmax", "formats": ["image", "text"]}] + [mine("text")] * 3, pool(), n_competitors=2, today=TODAY)
    ideas = mx.ideas_of({"google": google})
    assert [i["id"] for i in ideas] == ["google:format:video"] and ideas[0]["level"] == "high"
    assert ideas[0]["why"] == ("60% of their ads are video ads (6 ads, 2 competitors) against 0% of ours. Their video ads have run 120 days at the median. "
                               "1 Performance Max asset group of ours has no video of our own, so Google makes one from the pictures.")
    assert ideas[0]["tryThis"] == mx.TRY[("format", "video")] and ideas[0]["title"] == "Google: they run more video ads"


# ── why a platform's words could not be compared ────────────────────────────

def test_the_reason_names_each_competitor_and_what_stopped_it():
    entries = [{"name": "A", "ads": [], "pictures": [], "error": "", "skipped": "No Facebook page is set, so Meta's Ad Library was not searched."},
               {"name": "B", "ads": [], "pictures": [], "error": "", "skipped": "Not read yet: press Refresh competitor ads."},
               {"name": "C", "ads": [], "pictures": [], "error": "The Meta read failed: boom", "skipped": ""},
               {"name": "D", "ads": [], "pictures": [{"id": "1"}, {"id": "2"}], "error": "", "skipped": ""},
               {"name": "E", "ads": [], "pictures": [], "error": "", "skipped": ""}]
    assert cm.thin_reason(entries, "meta", 0) == ("No line of the competitors' copy was read, too few to say what they have in common (at least 8 are needed): A has no Facebook page set, so Meta was not "
                                                  "searched for it; B has not been read yet (press Refresh competitor ads); the Meta read of C failed; the 2 ads read of D are only pictures or videos with no "
                                                  "words (their formats are compared below); E shows no active ads on Meta.")
    two = [dict(entries[0], name="A"), dict(entries[0], name="B")]
    assert "A and B have no Facebook page set, so Meta was not searched for them" in cm.thin_reason(two, "meta", 3) and cm.thin_reason(two, "meta", 3).startswith("Only 3 lines")
    assert cm.thin_reason([], "google", 2).endswith("(at least 8 are needed).")


def test_the_report_says_why_and_still_compares_the_formats_of_picture_only_ads(own, monkeypatch):
    monkeypatch.setattr(co, "load", lambda platform, account_id, **kw: {"units": [mine("image")] * 4, "error": ""})
    s = MemStore()
    cs.save_account(s, MA, "Acme", {"competitors": "Tandem | tandemdiabetes.com | TandemDiabetes\nInsulet | omnipod.com | myomnipod"}, "ana", NOW)
    keep(s, MA, "Tandem", "meta", [], pictures=[picture_only(i, "Tandem Diabetes Care", "2026-10-01") for i in range(5)])
    keep(s, MA, "Insulet", "meta", [], pictures=[picture_only(10 + i, "Insulet Corporation", "2026-10-01") for i in range(5)])
    rep = pc.report_for("", MA, mon=s, now=NOW)
    why = rep["analysis"]["meta"]["compare"]["why"]
    assert why.startswith("No line of the competitors' copy was read") and "the 10 ads read of Tandem and Insulet are only pictures or videos with no words" in why
    assert rep["mix"]["meta"]["theirAds"] == 10 and rep["mix"]["meta"]["kinds"]["format"]["judged"] and rep["counts"]["ideas"] == len(rep["ideas"])


def test_the_report_merges_the_kind_ideas_after_the_word_ideas_of_the_same_level(own, monkeypatch):
    monkeypatch.setattr(co, "load", lambda platform, account_id, **kw: {"units": [mine("image", networks=("facebook",))] * 4, "error": ""})
    s = MemStore()
    cs.save_account(s, MA, "Acme", {"competitors": "Tandem | tandemdiabetes.com | TandemDiabetes\nInsulet | omnipod.com | myomnipod"}, "ana", NOW)
    keep(s, MA, "Tandem", "meta", [mad(i, "Tandem Diabetes Care", "Pump %d" % i, "2026-05-01", fmt="VIDEO") for i in range(5)])
    keep(s, MA, "Insulet", "meta", [mad(10 + i, "Insulet Corporation", "Pod %d" % i, "2026-05-01", fmt="VIDEO") for i in range(5)])
    rep = pc.report_for("", MA, mon=s, now=NOW)
    ids = [i["id"] for i in rep["ideas"]]
    assert "meta:format:video" in ids and rep["counts"]["kindGaps"] >= 1
    levels = [mx.ORDER[i["level"]] for i in rep["ideas"]]
    assert levels == sorted(levels)


def test_a_read_of_our_kinds_that_raises_is_said_not_fatal(own, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("store down")
    monkeypatch.setattr(co, "load", boom)
    s = MemStore()
    cs.save_account(s, MA, "Acme", {"competitors": "Tandem | tandemdiabetes.com | TandemDiabetes"}, "ana", NOW)
    rep = pc.report_for("", MA, mon=s, now=NOW)
    assert rep["mix"]["meta"]["ownError"] == "store down"


# ── the ideas Claude writes ─────────────────────────────────────────────────

def test_the_ads_shown_are_the_longest_runners_with_every_format_seen():
    ads = [their(i, "A", "image", days=300 - i) for i in range(12)] + [their(50, "A", "video", days=5), their(51, "A", "carousel", days=1)]
    picked = cb.pick_ads(ads)
    assert len(picked) == cb.MAX_ADS and {"video", "carousel"} <= {a["format"] for a in picked} and picked[0]["id"] == "t0"


def test_an_idea_that_names_no_ad_it_was_shown_is_dropped():
    ideas = [{"title": "Video", "platform": "meta", "format": "Video", "placement": "Reels", "hook": "h", "firstLine": "f", "why": "w", "basedOn": ["t1", "zzz"]},
             {"title": "Made up", "platform": "meta", "format": "x", "placement": "x", "hook": "x", "firstLine": "x", "why": "x", "basedOn": ["nope"]},
             {"title": "Bad platform", "platform": "tiktok", "format": "x", "placement": "x", "hook": "x", "firstLine": "x", "why": "x", "basedOn": ["t1"]}, "junk"]
    out = cb.clean(ideas, {"t1", "t2"})
    assert [i["title"] for i in out] == ["Video"] and out[0]["basedOn"] == ["t1"]
    assert len(cb.clean([dict(ideas[0], title=str(i)) for i in range(9)], {"t1"})) == cb.MAX_IDEAS


@pytest.fixture
def briefed(own, monkeypatch):
    monkeypatch.setattr(co, "load", lambda platform, account_id, **kw: {"units": [mine("image")] * 4, "error": ""})
    s = MemStore()
    cs.save_account(s, MA, "Acme", {"competitors": "Tandem | tandemdiabetes.com | TandemDiabetes"}, "ana", NOW)
    keep(s, MA, "Tandem", "meta", [mad(i, "Tandem Diabetes Care", "Pump %d" % i, "2026-05-01", fmt="VIDEO") for i in range(3)])
    return s


def test_ideas_are_written_from_the_evidence_and_kept_until_the_competitors_are_read_again(briefed):
    asked = []

    def ask(system, content):
        asked.append((system, content))
        return {"ideas": [{"title": "Short video", "platform": "meta", "format": "Video", "placement": "Reels", "hook": "A parent at breakfast", "firstLine": "[your offer] today",
                           "why": "Their videos run longest.", "basedOn": ["meta:0", "meta:9"]}], "model": "claude-opus-5-5"}
    assert pc.brief_for("", MA, mon=briefed, now=NOW)["status"] == "none"
    got = pc.brief_for("", MA, mon=briefed, now=NOW, write=True, ask_fn=ask, fetch_fn=lambda url: (b"\x89PNG", "image/png"))
    assert got["status"] == "ready" and got["ideas"][0]["basedOn"] == ["meta:0"] and got["ideas"][0]["ads"][0]["who"] == "Tandem Diabetes Care" and got["images"] == 3 and got["shown"] == 3
    system, content = asked[0]
    text = content[0]["text"]
    assert "Do not invent facts" in system and "id meta:0" in text and "Our own Meta copy" in text and sum(1 for b in content if b["type"] == "image") == 3
    assert pc.brief_for("", MA, mon=briefed, now=NOW)["status"] == "ready"
    keep(briefed, MA, "Tandem", "meta", [mad(1, "Tandem Diabetes Care", "Pump", "2026-05-01")], fetchedAt="2026-10-27T00:00:00+00:00")
    old = pc.brief_for("", MA, mon=briefed, now=NOW)
    assert old["status"] == "old" and old["ideas"][0]["title"] == "Short video"


def test_a_failed_ask_is_said_and_keeps_the_last_good_ideas(briefed):
    briefed.state_set(cb.STATE + "meta:act_1234567", {"sig": "x", "ideas": [{"title": "Kept"}], "at": "2026-10-20T00:00:00+00:00"})
    got = pc.brief_for("", MA, mon=briefed, now=NOW, write=True, ask_fn=lambda s, c: {"error": "Claude could not be asked (overloaded)."}, fetch_fn=lambda u: (None, ""))
    assert got == {"status": "failed", "error": "Claude could not be asked (overloaded)."} and briefed.state_get(cb.STATE + "meta:act_1234567")["ideas"] == [{"title": "Kept"}]
    none = pc.brief_for("", MA, mon=briefed, now=NOW, write=True, ask_fn=lambda s, c: {"ideas": [{"title": "x", "platform": "meta", "basedOn": ["nope"]}]}, fetch_fn=lambda u: (None, ""))
    assert none["status"] == "failed" and "no idea tied to a competitor ad" in none["error"]


def test_ideas_need_competitor_ads_and_one_ask_at_a_time(briefed, own):
    s = MemStore()
    cs.save_account(s, MA, "Acme", {"competitors": "Tandem | tandemdiabetes.com | TandemDiabetes"}, "ana", NOW)
    assert "Press Refresh competitor ads first" in pc.brief_for("", MA, mon=s, now=NOW, write=True, ask_fn=lambda s, c: {}, fetch_fn=lambda u: (None, ""))["error"]
    with pytest.raises(pc.BadRequest, match="Add the competitors first"):
        pc.brief_for("", MA, mon=MemStore(), now=NOW, write=True)
    briefed.claim("lock:compete:brief:meta:act_1234567", 180)
    with pytest.raises(pc.BadRequest, match="already writing"):
        pc.brief_for("", MA, mon=briefed, now=NOW, write=True, ask_fn=lambda s, c: {}, fetch_fn=lambda u: (None, ""))


def test_without_a_key_claude_is_not_asked(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cb.ask_claude("s", []) == {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
