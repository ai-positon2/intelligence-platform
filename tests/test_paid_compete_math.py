"""Competitor Analysis: the arithmetic (tracker/paid_compete_math.py). No network. The two real rows below were read from the Meta Ad Library and Google's Ads Transparency Center for
Tandem Diabetes on 2026-10-27 (shortened), so the normalisers are held to what the two actors really return."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_compete_math as cm  # noqa: E402

NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)
ASOF = NOW.date()

META_REAL = {"adArchiveID": "1746216413314264", "pageName": "Tandem Diabetes Care", "pageId": "184956728255249", "isActive": True, "startDate": 1790146800, "endDate": 1793084400,
             "startDateFormatted": "2026-09-23T07:00:00.000Z", "endDateFormatted": "2026-10-27T07:00:00.000Z", "collationCount": 3, "publisherPlatform": ["FACEBOOK", "INSTAGRAM"],
             "snapshot": {"displayFormat": "DCO", "title": "$0 Insulin Pump Delivered to Your Door",
                          "body": {"text": "The best time to get a new insulin pump may be right NOW. You may be eligible to get a Tandem pump for as little as $0 out of pocket.*  \n\n*$0 pump claim based on evaluation of pharmacy coverage. Important Safety Information: RX ONLY."},
                          "linkDescription": "Explore your options.", "linkUrl": "https://www.tandemdiabetes.com/landing-pages/tandem-pump-rx?utm=1", "caption": "tandemdiabetes.com", "ctaText": "See details", "cards": [], "pageName": "Tandem Diabetes Care"}}
GOOGLE_REAL = {"advertiserId": "AR17804925876349960193", "advertiserName": "Tandem Diabetes Care, Inc.", "creativeId": "CR03842488522816618497", "firstShown": "2025-02-19T21:32:34.607Z", "format": "TEXT",
               "lastShown": "2026-10-27T10:48:42.330Z", "numServedDays": 497,
               "variations": [{"imageUrl": "x", "clickUrl": "https://tandemdiabetes.com", "headline": "Connected with Dexcom - Get Started Today", "description": "el www.tandemdiabetes.com/ Say goodbye to fingersticks when you use t:slim X2 insulin pump with Dexcom G6 or G7 CGM."},
                              {"imageUrl": "y", "clickUrl": "https://tandemdiabetes.com", "headline": "Tandem Diabetes Care - t:slim X2 Insulin Pump", "description": "el www.tandemdiabetes.com/ Tandem and Dexcom are creating life-changing diabetes management solutions."},
                              {"imageUrl": "z", "clickUrl": "https://tandemdiabetes.com", "headline": "Connected with Dexcom - Get Started Today", "description": ""}]}


def ad(who, head, body="", *, id=None, days=10, first="2026-10-01", platform="meta", fmt="image", cta="", url="", active=True, copies=1):
    return {"id": id or "%s:%s" % (who, head), "platform": platform, "who": who, "format": fmt, "headlines": [head] if head else [], "bodies": [body] if body else [], "cta": cta, "url": url,
            "firstShown": first, "lastShown": "2026-10-27", "days": days, "active": active, "copies": copies, "networks": []}


# ── dates and text ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("v,want", [(None, None), ("", None), ("2026-09-23T07:00:00.000Z", dt.date(2026, 9, 23)), ("2026-09-23", dt.date(2026, 9, 23)), (1790146800, dt.date(2026, 9, 23)), ("soon", None), (10 ** 20, None)])
def test_a_day_is_read_from_an_iso_time_a_day_or_seconds(v, want):
    assert cm.to_date(v) == want


def test_the_text_read_from_the_picture_of_a_google_ad_is_tidied():
    assert cm.clean_ocr("el www.tandemdiabetes.com/ Say goodbye to fingersticks when you use t:slim X2 insulin pump.") == "Say goodbye to fingersticks when you use t:slim X2 insulin pump."
    assert cm.clean_ocr("Tandem Diabetes Meet the new standard of care: i Tandem Mobi system with Control- il 9 IQ+ technology. Tandem AID predicts og 5 and helps prevent highs. \u2014") == \
        "Tandem Diabetes Meet the new standard of care: i Tandem Mobi system with Control- 9 IQ+ technology. Tandem AID predicts 5 and helps prevent highs."
    assert cm.clean_ocr("Visit example.com today and save") == "Visit example.com today and save" and cm.clean_ocr(None) == "" and len(cm.clean_ocr("word " * 200)) <= cm.MAX_LINE
    assert cm.clean_ocr("We do it at 5 to 6") == "We do it at 5 to 6"                                                 # short real words and numbers stay


def test_a_landing_page_is_its_host_and_the_first_two_steps():
    assert cm.path_of("https://www.tandemdiabetes.com/landing-pages/tandem-pump-rx?x=1") == "tandemdiabetes.com/landing-pages/tandem-pump-rx" and cm.path_of("tandemdiabetes.com") == "tandemdiabetes.com"
    assert cm.path_of("https://a.com/x/y/z/w") == "a.com/x/y" and cm.path_of("{lpurl}") == "" and cm.path_of("") == ""


@pytest.mark.parametrize("text,themes", [("$0 Insulin Pump Delivered to Your Door", {"price", "free", "convenience"}), ("Save 20% off today", {"price", "urgency", "numbers"}), ("Rated 4.8 stars by 12,000 patients", {"proof"}),
                                         ("FDA approved, board certified doctors", {"trust"}), ("Risk-free, cancel anytime", {"guarantee"}), ("Free whitening, sugar-free gum", {"free"}), ("Book online, same day", {"convenience"}),
                                         ("Insurance accepted. 0% APR financing", {"insurance", "numbers"}), ("Dentist near you in Quincy, MA", {"local"}), ("Tired of pain?", {"question"}), ("Introducing the new pump", {"newness"}),
                                         ("20 years of experience", {"trust", "numbers"}), ("Omnipod vs Tandem", {"comparison"}), ("Our team cares", set())])
def test_a_line_of_copy_carries_the_messages_the_rules_find(text, themes):
    assert cm.themes_of(text) == themes


def test_a_theme_pattern_does_not_fire_inside_other_words():
    assert cm.themes_of("Freestyle Libre sensors") == set() and cm.themes_of("a knowledge base") == set() and "price" not in cm.themes_of("surprise") and "urgency" not in cm.themes_of("knowledge")


# ── what the platforms gave ─────────────────────────────────────────────────

def test_a_real_meta_ad_becomes_one_shape():
    a = cm.meta_ad(META_REAL, asof=ASOF)
    assert a["id"] == "meta:1746216413314264" and a["platform"] == "meta" and a["who"] == "Tandem Diabetes Care" and a["format"] == "dynamic" and a["cta"] == "See details" and a["copies"] == 3
    assert a["headlines"] == ["$0 Insulin Pump Delivered to Your Door"] and a["bodies"][0].startswith("The best time to get a new insulin pump may be right NOW.") and "Safety" not in a["bodies"][0] and a["bodies"][1] == "Explore your options."
    assert a["firstShown"] == "2026-09-23" and a["lastShown"] == "2026-10-27" and a["days"] == 34 and a["active"] is True and a["networks"] == ["facebook", "instagram"] and a["url"].endswith("tandem-pump-rx?utm=1")


def test_a_meta_ad_that_has_stopped_ends_on_its_end_day_and_one_with_no_text_is_dropped():
    gone = cm.meta_ad(dict(META_REAL, isActive=False, endDateFormatted="2026-10-01T07:00:00.000Z"), asof=ASOF)
    assert gone["active"] is False and gone["lastShown"] == "2026-10-01" and gone["days"] == 8
    assert cm.meta_ad({"adArchiveID": "1", "snapshot": {"title": None, "body": None, "cards": []}}, asof=ASOF) is None
    dyn = cm.meta_ad({"adArchiveID": "2", "snapshot": {"title": "{{product.name}}", "body": {"text": "{{product.description}}"}, "cards": []}}, asof=ASOF)
    assert dyn is None                                                                                       # a catalog template is not copy


def test_a_meta_carousel_keeps_the_text_of_its_cards():
    a = cm.meta_ad({"adArchiveID": "3", "isActive": True, "startDateFormatted": "2026-10-20T07:00:00.000Z", "snapshot": {"displayFormat": "CAROUSEL", "title": None, "body": None, "ctaText": None,
                                                                                                                       "cards": [{"title": "Whitening", "body": "Brighter in a day", "ctaText": "Book now", "linkUrl": "https://a.com/w"}, {"title": "Implants", "body": "", "linkDescription": "Ask us"}]}}, asof=ASOF)
    assert a["format"] == "carousel" and a["headlines"] == ["Whitening", "Implants"] and a["bodies"] == ["Brighter in a day", "Ask us"] and a["cta"] == "Book now" and a["url"] == "https://a.com/w"


def test_a_real_google_creative_becomes_one_shape():
    a = cm.google_ad(GOOGLE_REAL, asof=ASOF)
    assert a["id"] == "google:CR03842488522816618497" and a["who"] == "Tandem Diabetes Care, Inc." and a["format"] == "text" and a["days"] == 497 and a["active"] is True
    assert a["headlines"] == ["Connected with Dexcom - Get Started Today", "Tandem Diabetes Care - t:slim X2 Insulin Pump"]
    assert a["bodies"] == ["Say goodbye to fingersticks when you use t:slim X2 insulin pump with Dexcom G6 or G7 CGM.", "Tandem and Dexcom are creating life-changing diabetes management solutions."]
    assert a["url"] == "https://tandemdiabetes.com" and a["firstShown"] == "2025-02-19" and a["lastShown"] == "2026-10-27"
    old = cm.google_ad(dict(GOOGLE_REAL, lastShown="2026-10-01T00:00:00.000Z", numServedDays=None), asof=ASOF)
    assert old["active"] is False and old["days"] == (dt.date(2026, 10, 1) - dt.date(2025, 2, 19)).days
    assert cm.google_ad({"creativeId": "1", "format": "IMAGE", "variations": [{"imageUrl": "x"}]}, asof=ASOF) is None


# Shapes of the rows the two actors returned on 2026-10-28 (urls shortened): a Meta IMAGE ad has snapshot.images, a DCO ad has one card per variant, a Google TEXT creative has one picture of the ad per
# variation. A Meta video's fields (snapshot.videos, a card's videoHdUrl / videoSdUrl / videoPreviewImageUrl) are the ones the actor lists for its cards; no video row was in the sample.
FB = "https://scontent-hou1-1.xx.fbcdn.net/v/t39.35426-6/"
META_IMAGE = {"adArchiveID": "1646739963541817", "pageName": "Tandem Diabetes Care", "isActive": True, "startDateFormatted": "2026-09-24T07:00:00.000Z", "publisherPlatform": ["FACEBOOK"],
              "snapshot": {"displayFormat": "IMAGE", "title": "The All-In-One t:slim X2 Pump", "caption": "tandemdiabetes.com", "ctaText": "See details", "ctaType": "SEE_DETAILS", "linkDescription": "Let us check your benefits today.",
                           "linkUrl": "https://www.tandemdiabetes.com/landing-pages/tandem-pump-rx", "cards": [], "videos": [],
                           "body": {"text": "With a sleek touchscreen, the t:slim X2 is designed to match your lifestyle.  \n\n$0 pump claim based on evaluation of pharmacy coverage.  \n\nImportant Safety Information: RX ONLY."},
                           "images": [{"originalImageUrl": FB + "orig1_n.jpg?oe=1", "resizedImageUrl": FB + "small1_n.jpg?stp=s600x600&oe=1", "watermarkedResizedImageUrl": ""}]}}


def card(n, **kw):
    return dict({"title": "Variant %d" % n, "body": "Body of variant %d.\n\nMore." % n, "ctaText": "See Details", "linkUrl": "https://www.tandemdiabetes.com/landing-pages/v%d" % n, "linkDescription": "", "caption": "",
                 "originalImageUrl": FB + "c%d_orig.jpg" % n, "resizedImageUrl": FB + "c%d_small.jpg" % n, "videoHdUrl": None, "videoSdUrl": None, "videoPreviewImageUrl": None}, **kw)


def test_a_meta_image_ad_keeps_its_picture_its_full_text_its_library_page_and_its_display_address():
    a = cm.meta_ad(META_IMAGE, asof=ASOF)
    assert a["media"] == [{"kind": "image", "url": FB + "small1_n.jpg?stp=s600x600&oe=1"}] and a["mediaCount"] == 1                    # the 600 pixel picture, not the original
    assert a["adUrl"] == "https://www.facebook.com/ads/library/?id=1646739963541817" and a["caption"] == "tandemdiabetes.com" and a["ctaType"] == "SEE_DETAILS"
    assert a["text"].startswith("With a sleek touchscreen") and "Important Safety Information" in a["text"] and a["text"].count("\n") == 2         # every paragraph, whitespace tidied
    assert cm.meta_ad(dict(META_IMAGE, snapshot=dict(META_IMAGE["snapshot"], images=[{"originalImageUrl": FB + "o.jpg", "resizedImageUrl": ""}])), asof=ASOF)["media"][0]["url"] == FB + "o.jpg"


def test_a_dynamic_meta_ad_keeps_each_cards_picture_and_words_up_to_a_limit_and_says_how_many_there_were():
    snap = dict(META_IMAGE["snapshot"], displayFormat="DCO", images=[], cards=[card(i) for i in range(1, 9)])
    a = cm.meta_ad(dict(META_IMAGE, snapshot=snap), asof=ASOF)
    assert a["mediaCount"] == 8 and len(a["media"]) == cm.MAX_MEDIA == 6
    assert a["media"][0] == {"kind": "image", "url": FB + "c1_small.jpg", "title": "Variant 1", "body": "Body of variant 1.", "cta": "See Details", "link": "https://www.tandemdiabetes.com/landing-pages/v1"}


def test_a_meta_video_keeps_its_file_and_its_poster_and_the_sd_file_when_there_is_no_hd_one():
    vids = [{"videoHdUrl": "https://video.xx.fbcdn.net/hd.mp4", "videoSdUrl": "https://video.xx.fbcdn.net/sd.mp4", "videoPreviewImageUrl": FB + "poster.jpg"}]
    a = cm.meta_ad(dict(META_IMAGE, snapshot=dict(META_IMAGE["snapshot"], displayFormat="VIDEO", images=[], videos=vids)), asof=ASOF)
    assert a["media"] == [{"kind": "video", "url": "https://video.xx.fbcdn.net/hd.mp4", "poster": FB + "poster.jpg"}] and a["format"] == "video"
    sd = cm.meta_ad(dict(META_IMAGE, snapshot=dict(META_IMAGE["snapshot"], images=[], videos=[dict(vids[0], videoHdUrl=None)])), asof=ASOF)
    assert sd["media"][0]["url"] == "https://video.xx.fbcdn.net/sd.mp4"
    in_card = cm.meta_ad(dict(META_IMAGE, snapshot=dict(META_IMAGE["snapshot"], images=[], cards=[card(1, videoHdUrl="https://video.xx.fbcdn.net/c.mp4", videoPreviewImageUrl=FB + "cp.jpg")])), asof=ASOF)
    assert in_card["media"][0]["kind"] == "video" and in_card["media"][0]["url"] == "https://video.xx.fbcdn.net/c.mp4" and in_card["media"][0]["poster"] == FB + "cp.jpg" and in_card["media"][0]["title"] == "Variant 1"


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "http://insecure.example/a.jpg", "data:image/png;base64,AAAA", "//x.example/a.jpg", "https://has space.example/a b.jpg", "", None, 5])
def test_only_https_pictures_and_files_are_kept(bad):
    a = cm.meta_ad(dict(META_IMAGE, snapshot=dict(META_IMAGE["snapshot"], images=[{"originalImageUrl": bad, "resizedImageUrl": bad}], linkUrl=bad)), asof=ASOF)
    assert a["media"] == [] and a["mediaCount"] == 0 and a["url"] == (bad if str(bad).startswith("http://insecure") else "")                 # a plain http landing page is still a page


def test_a_google_creative_keeps_one_picture_per_variation_with_its_words_and_its_transparency_page():
    item = dict(GOOGLE_REAL, adLibraryUrl="https://adstransparency.google.com/advertiser/AR1/creative/CR03842488522816618497", previewUrl="https://tpc.googlesyndication.com/archive/simgad/1")
    item["variations"] = [dict(v, imageUrl="https://tpc.googlesyndication.com/archive/simgad/%d" % i) for i, v in enumerate(GOOGLE_REAL["variations"], 1)]
    a = cm.google_ad(item, asof=ASOF)
    assert a["adUrl"] == "https://adstransparency.google.com/advertiser/AR1/creative/CR03842488522816618497" and a["mediaCount"] == 3
    assert a["media"][0] == {"kind": "image", "url": "https://tpc.googlesyndication.com/archive/simgad/1", "title": "Connected with Dexcom - Get Started Today",
                             "body": "Say goodbye to fingersticks when you use t:slim X2 insulin pump with Dexcom G6 or G7 CGM.", "link": "https://tandemdiabetes.com"}
    assert a["media"][2]["body"] == "" and cm.google_ad(dict(item, adLibraryUrl="https://evil.example/creative/1"), asof=ASOF)["adUrl"] == ""
    vid = cm.google_ad(dict(item, format="VIDEO"), asof=ASOF)
    assert vid["format"] == "video" and {m["kind"] for m in vid["media"]} == {"image"}                                  # Google gives the picture of a video creative, not the file: it must not be played
    nopic = cm.google_ad(dict(item, variations=[{"headline": "H", "description": "", "clickUrl": ""}]), asof=ASOF)
    assert nopic["media"] == [{"kind": "image", "url": "https://tpc.googlesyndication.com/archive/simgad/1", "title": "H", "body": "", "link": ""}]         # the creative's own preview stands in


def test_an_ad_that_is_only_a_picture_is_kept_for_the_gallery_when_asked_and_is_not_text_for_the_analysis():
    pic = dict(META_IMAGE, snapshot=dict(META_IMAGE["snapshot"], title=None, body=None, linkDescription=None))
    assert cm.meta_ad(pic, asof=ASOF) is None
    kept = cm.meta_ad(pic, asof=ASOF, textless=True)
    assert kept["headlines"] == [] and kept["bodies"] == [] and kept["mediaCount"] == 1 and kept["adUrl"].endswith("1646739963541817")
    assert cm.meta_ad({"adArchiveID": "1", "snapshot": {"title": None, "body": None, "cards": []}}, asof=ASOF, textless=True) is None            # no text and no picture: nothing to show
    g = {"creativeId": "CR1", "advertiserName": "A", "format": "IMAGE", "firstShown": "2026-10-01T00:00:00Z", "lastShown": "2026-10-27T00:00:00Z", "variations": [{"imageUrl": "https://tpc.googlesyndication.com/archive/simgad/9"}]}
    assert cm.google_ad(g, asof=ASOF) is None and cm.google_ad(g, asof=ASOF, textless=True)["media"][0]["url"].endswith("/9")


def test_our_own_ads_become_the_same_shape_without_their_paths_and_assets():
    own = {"id": "9", "platform": "google", "kind": "rsa", "campaign": "Brand", "url": "https://acme.com", "texts": [{"slot": "headline", "text": "Acme Dental"}, {"slot": "headline", "text": "Acme Dental"},
                                                                                                                        {"slot": "description", "text": "Call us today"}, {"slot": "path", "text": "dental"}]}
    a = cm.own_ad(own)
    assert a["headlines"] == ["Acme Dental"] and a["bodies"] == ["Call us today"] and a["platform"] == "google" and a["who"] == "Brand" and a["url"] == "https://acme.com"
    assert cm.own_ad(dict(own, kind="assets")) is None and cm.own_ad({"id": "1", "platform": "meta", "texts": [{"slot": "path", "text": "x"}]}) is None
    m = cm.own_ad({"id": "meta:1", "platform": "meta", "kind": "meta", "texts": [{"slot": "primary", "text": "First line.\n\nSecond paragraph."}, {"slot": "title", "text": "Whitening"}, {"slot": "link", "text": "Book"}]})
    assert m["headlines"] == ["Whitening"] and m["bodies"] == ["First line.", "Book"] and m["platform"] == "meta"


# ── figures ─────────────────────────────────────────────────────────────────

def test_the_same_ad_of_one_competitor_is_counted_once_with_its_copies():
    a = ad("Tandem", "Free pump", "Check now", id="m1", days=10)
    b = ad("Tandem", "Free pump", "Check now", id="m2", days=40, copies=2)
    other = ad("Omnipod", "Free pump", "Check now", id="m3")
    out = cm.dedupe([a, b, other])
    assert len(out) == 2 and [x for x in out if x["who"] == "Tandem"][0]["days"] == 40 and [x for x in out if x["who"] == "Tandem"][0]["copies"] == 3 and [x for x in out if x["who"] == "Tandem"][0]["id"] == "m2"


def test_the_share_of_lines_is_counted_over_distinct_lines_and_names_how_many_ads_and_advertisers():
    ads = [ad("A", "Free whitening", "Book online today", id="1"), ad("B", "Free whitening", "Our team cares", id="2"), ad("B", "Save 20% off", "Call us", id="3")]
    r = cm.line_shares(ads)
    assert r["lines"] == 5                                                                                   # "Free whitening" twice counts once
    f, p = r["themes"]["free"], r["themes"]["price"]
    assert f == {"lines": 1, "share": 20.0, "ads": 2, "who": 2} and p["lines"] == 1 and p["ads"] == 1 and p["who"] == 1 and r["themes"]["urgency"]["lines"] == 1 and cm.line_shares([])["lines"] == 0


def test_the_offers_the_competitors_state_are_listed_commonest_first():
    ads = [ad("A", "$0 Insulin Pump Delivered", id="1", days=30), ad("B", "$0 Insulin Pump Delivered", "Free benefits check today", id="2", days=90), ad("B", "Save 20% off your first order", id="3")]
    offers = cm.offers_of(ads)
    assert offers[0]["text"] == "$0 Insulin Pump Delivered" and offers[0]["n"] == 2 and offers[0]["who"] == ["A", "B"] and offers[0]["maxDays"] == 90
    assert any(o["text"].startswith("Free benefits check") for o in offers) and any(o["text"].startswith("20% off") for o in offers) and cm.offers_of([ad("A", "Our team cares")]) == []


def test_a_competitors_figures_count_new_old_formats_calls_to_action_and_landing_pages():
    ads = [ad("T", "A", id="1", days=400, first="2025-09-01", fmt="video", cta="Learn more", url="https://t.com/lp/a?x=1"), ad("T", "B", id="2", days=10, first="2026-10-20", cta="Learn more", url="https://t.com/lp/a"),
           ad("T", "C", id="3", days=3, first="2026-10-24", fmt="video", cta="Shop now", url="https://t.com/"), ad("T", "D", id="4", days=None, first="", active=False)]
    s = cm.competitor_stats("T", ads, fetched_at="2026-10-20T00:00:00+00:00", now=NOW)
    assert s["count"] == 4 and s["active"] == 3 and s["new"] == 2 and s["medianDays"] == 10 and s["maxDays"] == 400 and s["formats"] == {"video": 2, "image": 2}
    assert s["ctas"] == {"Learn more": 2, "Shop now": 1} and s["landing"][0] == {"path": "t.com/lp/a", "ads": 2} and s["ageDays"] == 7 and [x["id"] for x in s["longest"]][0] == "1" and [x["id"] for x in s["newest"]] == ["3", "2"]
    assert s["error"] == "" and s["skipped"] == ""
    e = cm.competitor_stats("T", [], fetched_at="", now=NOW, error="boom")
    assert e["count"] == 0 and e["medianDays"] is None and e["maxDays"] is None and e["ageDays"] is None and e["error"] == "boom"


# ── comparing ───────────────────────────────────────────────────────────────

def pool_with_price(n_each=4, who=("A", "B")):
    """Competitor ads where most lines carry a price."""
    out = []
    for w in who:
        for i in range(n_each):
            out.append(ad(w, "Save $%d today at %s" % (10 + i, w), "Plans from $%d a month at %s" % (20 + i, w), id="%s%d" % (w, i), days=100 - i))
    return out


def own_without_price(n=4):
    return [ad("Us", "Friendly team number %d" % i, "We care about you %d" % i, id="u%d" % i, platform="google") for i in range(n)]


def test_a_message_the_competitors_use_and_we_do_not_is_a_gap_with_its_proof():
    out, pool, theirs, ours = cm.compare(own_without_price(), pool_with_price(), n_competitors=2)
    assert out["judged"] is True and out["why"] == "" and out["ownLines"] == 8 and out["theirLines"] == 16
    price = [g for g in out["gaps"] if g["theme"] == "price"][0]
    assert price["theirShare"] == 100.0 and price["ownShare"] == 0.0 and price["points"] == 100.0 and price["ads"] == 8 and price["who"] == 2 and price["level"] == "high"
    assert out["gaps"][0]["points"] == 100.0 and {g["theme"] for g in out["gaps"]} == {"price", "urgency"}


def test_too_few_lines_on_either_side_is_said_and_nothing_is_judged():
    out, *_ = cm.compare(own_without_price(), pool_with_price(n_each=1), n_competitors=2)
    assert out["judged"] is False and out["gaps"] == [] and out["why"] == "Only 4 lines of the competitors' copy were read, too few to say what they have in common (at least 8 are needed)."
    out, *_ = cm.compare(own_without_price(n=2), pool_with_price(), n_competitors=2)
    assert out["judged"] is False and out["why"] == "Only 4 lines of our own copy were read, too few to compare (at least 6 are needed)."


def test_a_message_only_one_competitor_uses_is_not_a_gap_when_two_were_read_but_is_when_only_one_was():
    only_a = pool_with_price(n_each=4, who=("A",)) + [ad("B", "Plain %d" % i, "Words %d" % i, id="b%d" % i) for i in range(4)]
    out, *_ = cm.compare(own_without_price(), only_a, n_competitors=2)
    assert "price" not in [g["theme"] for g in out["gaps"]]
    out, *_ = cm.compare(own_without_price(), pool_with_price(n_each=4, who=("A",)), n_competitors=1)
    assert "price" in [g["theme"] for g in out["gaps"]]


def test_a_message_needs_enough_ads_and_is_not_a_gap_when_we_say_it_too():
    two_ads = [ad("A", "Save $10 today", "Plain one", id="1"), ad("B", "Save $12 today", "Plain two", id="2")] + [ad("A", "Plain %d" % i, "Words %d" % i, id="x%d" % i) for i in range(6)]
    out, *_ = cm.compare(own_without_price(), two_ads, n_competitors=2)
    assert "price" not in [g["theme"] for g in out["gaps"]]                                                  # two ads, not three
    own_price = [ad("Us", "Save $%d today" % i, "Plans from $%d" % i, id="p%d" % i, platform="google") for i in range(4)]
    out, *_ = cm.compare(own_price, pool_with_price(), n_competitors=2)
    assert "price" not in [g["theme"] for g in out["gaps"]]
    half = own_price[:2] + own_without_price(2)
    out, *_ = cm.compare(half, pool_with_price(), n_competitors=2)
    g = [x for x in out["gaps"] if x["theme"] == "price"][0]
    assert g["ownShare"] == 50.0 and g["points"] == 50.0                                                     # 50% of ours against 100% of theirs is still a gap


def test_the_gap_is_medium_below_forty_five_points_and_high_from_there():
    ads = pool_with_price()
    mild_own = [ad("Us", "Save $%d now" % i, "We care %d" % i, id="m%d" % i, platform="google") for i in range(2)] + [ad("Us", "Words %d" % i, "Plain %d" % i, id="n%d" % i, platform="google") for i in range(3)]
    out, *_ = cm.compare(mild_own, ads, n_competitors=2)
    g = [x for x in out["gaps"] if x["theme"] == "price"][0]
    assert g["ownShare"] == 20.0 and g["points"] == 80.0 and g["level"] == "high"
    own_more = [ad("Us", "Save $%d" % i, "Plan from $%d" % i, id="q%d" % i, platform="google") for i in range(2)] + [ad("Us", "Words %d" % i, "Plain %d" % i, id="r%d" % i, platform="google") for i in range(2)]
    out, *_ = cm.compare(own_more, ads, n_competitors=2)
    g = [x for x in out["gaps"] if x["theme"] == "price"][0]
    assert g["ownShare"] == 50.0 and g["points"] == 50.0 and g["level"] == "high"
    own_most = [ad("Us", "Save $%d" % i, "Plan from $%d" % i, id="s%d" % i, platform="google") for i in range(3)] + [ad("Us", "Words", "Plain", id="t", platform="google")]
    out, *_ = cm.compare(own_most, ads, n_competitors=2)
    g = [x for x in out["gaps"] if x["theme"] == "price"][0]
    assert g["ownShare"] == 75.0 and g["points"] == 25.0 and g["level"] == "medium"


def test_what_we_say_far_more_than_they_do_is_our_edge():
    own = [ad("Us", "Money-back guarantee %d" % i, "Risk-free trial %d" % i, id="e%d" % i, platform="google") for i in range(4)]
    out, *_ = cm.compare(own, pool_with_price(), n_competitors=2)
    assert [e["theme"] for e in out["edges"]] == ["guarantee"] and out["edges"][0]["ownShare"] == 100.0 and out["edges"][0]["theirShare"] == 0.0 and out["edges"][0]["points"] == 100.0


def test_an_example_is_a_competitors_line_from_the_ad_that_has_run_longest():
    pool = [ad("A", "Plain", "Words", id="1", days=5), ad("B", "Save $5 today", "x", id="2", days=300), ad("C", "Cheap prices here", "y", id="3", days=20)]
    assert cm.example_for("price", pool) == {"line": "Save $5 today", "who": "B", "days": 300} and cm.example_for("guarantee", pool) is None


# ── the report ──────────────────────────────────────────────────────────────

def entry(name, ads, platform="meta", **kw):
    return dict({"name": name, "platform": platform, "ads": ads, "fetchedAt": "2026-10-26T00:00:00+00:00", "error": "", "skipped": ""}, **kw)


def test_the_report_names_the_gaps_gives_ideas_and_lists_what_it_could_not_measure():
    comp = [entry("A", [a for a in pool_with_price(who=("A",))]), entry("B", [a for a in pool_with_price(who=("B",))])]
    rep = cm.analyse({"meta": own_without_price()}, comp, now=NOW)
    assert rep["status"] == "ok" and rep["counts"] == {"competitorAds": 8, "reads": 2, "gaps": len(rep["platforms"]["meta"]["compare"]["gaps"]), "ideas": len(rep["ideas"])} and rep["counts"]["gaps"] >= 1
    assert rep["headline"].startswith("8 competitor ads read from 2 competitor reads; ") and rep["notMeasured"] == cm.NOT_MEASURED and rep["region"] == "US"
    first = rep["ideas"][0]
    assert first["level"] == "high" and first["platform"] == "meta" and first["title"].startswith("Meta Ads: ") and "of the competitors' lines say it" in first["why"] and first["tryThis"] and first["example"]["who"] in ("A", "B")
    levels = [i["level"] for i in rep["ideas"]]
    assert levels == sorted(levels, key=["high", "medium", "low"].index)
    m = rep["platforms"]["meta"]
    assert m["own"]["ads"] == 4 and m["pool"]["ads"] == 8 and [c["name"] for c in m["competitors"]] == ["A", "B"] and m["offers"] and m["winners"][0]["days"] == 100 and m["examples"]["price"]["line"]


def test_the_report_says_what_it_could_not_read_and_what_is_old():
    comp = [entry("A", [], error="The Meta read failed: boom"), entry("B", [], skipped="No Facebook page is set, so Meta's Ad Library was not searched."),
            entry("C", pool_with_price(who=("C",)), fetchedAt="2026-09-01T00:00:00+00:00"), entry("G", [ad("G", "Hello", platform="google")], platform="google")]
    rep = cm.analyse({"meta": own_without_price(), "google": own_without_price()}, comp, now=NOW)
    c = rep["caveats"]
    assert "A on Meta: The Meta read failed: boom" in c and "B on Meta: No Facebook page is set, so Meta's Ad Library was not searched." in c and "C on Meta was last read 56 days ago." in c and cm.OCR_NOTE in c
    assert any(x.startswith("Google: Only 1 line of the competitors' copy") for x in c) and rep["status"] == "ok" and len(c) == len(set(c))


def test_with_nothing_read_the_report_says_so_instead_of_finding_nothing():
    rep = cm.analyse({"meta": own_without_price()}, [entry("A", [], error="x"), entry("B", [], skipped="y")], now=NOW)
    assert rep["status"] == "no_competitor_data" and rep["headline"] == "No competitor ads have been read yet." and rep["ideas"] == [] and rep["counts"]["reads"] == 0
    assert cm.analyse({}, [], now=NOW)["platforms"] == {}


def test_a_report_with_no_clear_gap_says_so_and_a_long_runner_is_still_an_idea():
    plain = [ad("A", "Plain %d" % i, "Words %d" % i, id="a%d" % i, days=300 if i == 0 else 5) for i in range(5)]
    rep = cm.analyse({"meta": own_without_price()}, [entry("A", plain), entry("B", [ad("B", "Other %d" % i, "Text %d" % i, id="b%d" % i) for i in range(5)])], now=NOW)
    assert rep["counts"]["gaps"] == 0 and rep["headline"].endswith("no clear message gap against our ads.") and [i["id"] for i in rep["ideas"]] == ["meta:winners"]
    w = rep["ideas"][0]
    assert w["level"] == "medium" and w["why"].startswith("A has run 300 days") and w["example"] == {"line": "Plain 0", "who": "A", "days": 300}
    short = cm.analyse({"meta": own_without_price()}, [entry("A", [ad("A", "Plain %d" % i, "Words %d" % i, id="a%d" % i, days=20) for i in range(5)]), entry("B", [ad("B", "O %d" % i, "T %d" % i, id="b%d" % i) for i in range(5)])], now=NOW)
    assert short["ideas"][0]["level"] == "low"


def test_the_competitors_offers_are_an_idea_and_a_commoner_one_is_medium():
    ads = [ad("A", "$0 Insulin Pump Delivered", "Plain %d" % i, id="a%d" % i) for i in range(2)] + [ad("B", "$0 Insulin Pump Delivered", "Other", id="b1")] + [ad("B", "Word %d" % i, "Text %d" % i, id="b%d" % (i + 2)) for i in range(4)]
    rep = cm.analyse({"meta": own_without_price()}, [entry("A", ads[:2]), entry("B", ads[2:])], now=NOW)
    o = [i for i in rep["ideas"] if i["id"] == "meta:offers"][0]
    assert o["level"] == "medium" and o["why"] == "They state 1 offer; the commonest, \"$0 Insulin Pump Delivered\", is in 3 lines." and "match, beat or avoid" in o["tryThis"]
    ads = [ad("A", "$0 Insulin Pump Delivered", "Plain %d" % i, id="a%d" % i) for i in range(2)] + [ad("B", "Word %d" % i, "Text %d" % i, id="b%d" % i) for i in range(5)]
    rep = cm.analyse({"meta": own_without_price()}, [entry("A", ads[:2]), entry("B", ads[2:])], now=NOW)
    assert [i for i in rep["ideas"] if i["id"] == "meta:offers"][0]["level"] == "low"


def multi(who, heads, bodies=(), id=None, days=10):
    a = ad(who, "", id=id, days=days)
    a["headlines"], a["bodies"] = list(heads), list(bodies)
    return a


def test_a_message_in_many_lines_of_only_two_ads_is_not_enough_ads():
    pool = [multi("A", ["Save $10 today", "Save $20 today", "Save $30 today"], ["Words one"], id="1"), multi("B", ["Save $40 today"], ["Words two"], id="2")] + [ad("A", "Plain %d" % i, "Text %d" % i, id="x%d" % i) for i in range(4)]
    out, *_ = cm.compare(own_without_price(), pool, n_competitors=2)
    p = cm.line_shares(pool)["themes"]["price"]
    assert p["ads"] == 2 and p["share"] >= 25 and "price" not in [g["theme"] for g in out["gaps"]]
    pool.append(multi("B", ["Save $50 today"], ["Words three"], id="3"))
    out, *_ = cm.compare(own_without_price(), pool, n_competitors=2)
    assert "price" in [g["theme"] for g in out["gaps"]]


def test_a_difference_of_twenty_points_is_not_a_gap_and_twenty_five_is():
    ads = pool_with_price()
    own20 = [ad("Us", "Save $%d now" % i, "Plan from $%d" % i, id="a%d" % i, platform="google") for i in range(4)] + [ad("Us", "Words", "Plain", id="z", platform="google")]
    out, *_ = cm.compare(own20, ads, n_competitors=2)
    assert "price" not in [g["theme"] for g in out["gaps"]]
    own25 = [ad("Us", "Save $%d now" % i, "Plan from $%d" % i, id="b%d" % i, platform="google") for i in range(3)] + [ad("Us", "Words", "Plain", id="y", platform="google")]
    out, *_ = cm.compare(own25, ads, n_competitors=2)
    assert "price" in [g["theme"] for g in out["gaps"]]


def test_our_edge_needs_twenty_five_points():
    pool = pool_with_price()
    ours = [ad("Us", "Money-back guarantee", "Plain %d" % i, id="g%d" % i, platform="google") for i in range(1)] + [ad("Us", "Words %d" % i, "Text %d" % i, id="w%d" % i, platform="google") for i in range(4)]
    out, *_ = cm.compare(ours, pool, n_competitors=2)
    assert out["edges"] == []                                                                                # one line in ten: not an edge
    two = [ad("Us", "Money-back guarantee", "Risk-free %d" % 1, id="g1", platform="google"), ad("Us", "Another guarantee", "Plain", id="g2", platform="google")] + [ad("Us", "Words %d" % i, "Text %d" % i, id="v%d" % i, platform="google") for i in range(3)]
    out, *_ = cm.compare(two, pool, n_competitors=2)
    assert [e["theme"] for e in out["edges"]] == ["guarantee"]                                               # three of ten lines against none: 30 points
    near = [ad("Us", "Money-back guarantee", "Plain", id="n1", platform="google"), ad("Us", "Another guarantee", "Plain two", id="n2", platform="google")] + [ad("Us", "Words %d" % i, "Text %d" % i, id="u%d" % i, platform="google") for i in range(4)]
    out, *_ = cm.compare(near, pool, n_competitors=2)
    assert out["edges"] == []                                                                                # two of twelve: 16.7 points


def test_a_gap_is_high_only_when_they_use_it_in_half_their_lines_or_more():
    pool = [ad("A", "Save $%d today at A" % i, "Plain a %d" % i, id="a%d" % i) for i in range(4)] + [ad("B", "Save $%d today at B" % i, "Plain b %d" % i, id="b%d" % i) for i in range(4)] + [ad("B", "Only words", id="c")]
    shares = cm.line_shares(pool)["themes"]["price"]
    assert 45 <= shares["share"] < 50
    out, *_ = cm.compare(own_without_price(), pool, n_competitors=2)
    g = [x for x in out["gaps"] if x["theme"] == "price"][0]
    assert g["points"] >= cm.HIGH_POINTS and g["level"] == "medium"


def test_an_ad_with_no_known_run_is_not_listed_among_the_longest_and_ideas_are_sorted_by_level():
    pool = [ad("A", "Plain %d" % i, "Words %d" % i, id="a%d" % i, days=None) for i in range(5)] + [ad("B", "Other %d" % i, "Text %d" % i, id="b%d" % i, days=0) for i in range(5)]
    rep = cm.analyse({"meta": own_without_price()}, [entry("A", pool[:5]), entry("B", pool[5:])], now=NOW)
    assert rep["platforms"]["meta"]["winners"] == [] and rep["ideas"] == []
    ads = [ad("A", "$0 Insulin Pump Delivered", "Plain %d" % i, id="a%d" % i, days=300 if i == 0 else 5) for i in range(2)] + [ad("B", "Word %d" % i, "Text %d" % i, id="b%d" % i, days=5) for i in range(5)]
    rep = cm.analyse({"meta": own_without_price()}, [entry("A", ads[:2]), entry("B", ads[2:])], now=NOW)
    assert [(i["id"], i["level"]) for i in rep["ideas"]] == [("meta:winners", "medium"), ("meta:offers", "low")]


# ── a read that hit its limit or stopped early is said, never passed off as everything ──────

def test_a_competitor_read_to_its_limit_or_stopped_early_is_said_in_its_figures_and_in_the_caveats():
    ads = pool_with_price(who=("A",))
    capped = dict(entry("A", ads), limit=150, capped=True)
    stopped = dict(entry("B", pool_with_price(who=("B",))), limit=150, partial="The Meta read stopped before it finished (out of time), so only part of the ads were read.")
    rep = cm.analyse({"meta": own_without_price()}, [capped, stopped], now=NOW)
    a, b = rep["platforms"]["meta"]["competitors"]
    assert a["capped"] is True and a["limit"] == 150 and a["read"] == 4 and a["partial"] == "" and b["capped"] is False and "stopped before it finished" in b["partial"]
    assert "A on Meta: the limit of 150 ads was reached, so they run more ads than were read. Their longest-running and newest ads may be missing." in rep["caveats"]
    assert "B on Meta: The Meta read stopped before it finished (out of time), so only part of the ads were read." in rep["caveats"]


def test_a_complete_read_has_no_limit_caveat_and_a_failed_one_has_only_its_error():
    rep = cm.analyse({"meta": own_without_price()}, [dict(entry("A", pool_with_price(who=("A",))), limit=150), dict(entry("B", [], error="x"), capped=True, limit=150)], now=NOW)
    assert [c for c in rep["caveats"] if "limit" in c] == [] and "B on Meta: x" in rep["caveats"]
    a = rep["platforms"]["meta"]["competitors"][0]
    assert a["capped"] is False and a["partial"] == ""


def test_ads_with_no_text_are_counted_so_a_picture_or_video_only_read_is_not_taken_for_a_small_advertiser():
    ads = pool_with_price(who=("A",))
    rep = cm.analyse({"meta": own_without_price()}, [dict(entry("A", ads), items=13), dict(entry("B", ads), items=4), dict(entry("C", ads))], now=NOW)
    a, b, c = rep["platforms"]["meta"]["competitors"]
    assert (a["noText"], b["noText"], c["noText"]) == (9, 0, 0) and a["read"] == 4
    assert cm.competitor_stats("X", [], fetched_at="", now=NOW, items=5)["noText"] == 5 and cm.competitor_stats("X", [], fetched_at="", now=NOW)["noText"] == 0
