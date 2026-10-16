"""Asset Coverage Audit: how one asset is drawn, run for real in Node.

The server sends each asset as a small object (tests/test_paid_assets_detail.py pins that side); the page turns it into a card. Here the card
script runs on objects built by the server's own code, and the HTML it writes is read back: an image shows the image, a sitelink its words
and lines, a video its thumbnail, a problem says why, every word from Google is escaped, a link or picture from anywhere unexpected is not
drawn, and the page and the server agree on which picture hosts are Google's.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TILES = os.path.join(ROOT, "static", "js", "asset_tiles.js")

from tracker import paid_assets_detail as D  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const ctx = { window: {}, Math, Number, String, Object, Array, RegExp, JSON };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const T = ctx.window.ACTiles, job = JSON.parse(process.argv[2]);
const out = job.map(([fn, ...args]) => T[fn].apply(null, args));
process.stdout.write(JSON.stringify(out));
"""

GOOGLE_IMG = "https://tpc.googlesyndication.com/simgad/2833249052621513497"


def call(*jobs):
    out = subprocess.run(["node", "-e", HARNESS, TILES, json.dumps(list(jobs))], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def one(fn, *args):
    return call([fn, *args])[0]


def asset(raw, **extra):
    """An asset as the server would send it: its content, then its state and where it is linked."""
    t = dict(D.content(raw), st="live", lv="campaign", id="1")
    t.update(extra)
    return t


IMG = {"type": "IMAGE", "id": "9", "name": "Hero.png", "imageAsset": {"fullSize": {"url": GOOGLE_IMG, "widthPixels": "896", "heightPixels": "640"}, "fileSize": "107975"}}
SITELINK = {"type": "SITELINK", "finalUrls": ["https://www.example.com/offers/new-patient?utm_source=x"],
            "sitelinkAsset": {"linkText": "New patient offer", "description1": "Exam and x-rays", "description2": "Book now"}}


def test_an_image_card_shows_the_picture_its_name_size_and_a_way_to_enlarge_it():
    h = one("tile", asset(IMG))
    assert '<img src="%s"' % GOOGLE_IMG in h and 'loading="lazy"' in h and 'referrerpolicy="no-referrer"' in h
    assert 'data-zoom="%s"' % GOOGLE_IMG in h and "Hero.png" in h and "896 × 640 px" in h and "105 KB" in h and "t-image" in h and "has-m" in h
    for size, words in ((500, "500 B"), (1023, "1023 B"), (1024, "1 KB"), (1048575, "1024 KB"), (3145728, "3.0 MB"), (0, "")):
        t = dict(asset(IMG), b=size)
        assert (words in one("tile", t)) if words else ("KB" not in one("tile", t) and " B<" not in one("tile", t) and "MB" not in one("tile", t)), size


def test_a_picture_from_anywhere_but_googles_own_hosts_is_never_drawn():
    for url in ("https://evil.example.com/x.png", "http://tpc.googlesyndication.com/x", "https://googlesyndication.com.evil.com/x", "javascript:alert(1)", ""):
        t = dict(asset(IMG), img=url)
        h = one("tile", t)
        assert "<img" not in h and "No preview from Google" in h, url
        assert "<img" not in one("chip", t), url


def test_the_page_and_the_server_agree_on_which_picture_hosts_are_googles():
    urls = ["https://tpc.googlesyndication.com/simgad/1", "https://lh3.googleusercontent.com/abc", "https://googlesyndication.com/x", "https://x.ggpht.com/y", "http://tpc.googlesyndication.com/x",
            "https://evil.example.com/x", "https://googlesyndication.com.evil.com/x", "https://evilgooglesyndication.com/x", "https://tpc.googlesyndication.com:8443/x",
            "https://user@tpc.googlesyndication.com/x", "https://evil.example.com\\@tpc.googlesyndication.com/x", "https://tpc.googlesyndication.com/a b", "https://TPC.GoogleSyndication.com/x",
            "javascript:alert(1)", "", "https://i.ytimg.com/vi/x/hqdefault.jpg", "https://tpc.googlesyndication.com"]
    in_page = call(*[["safeImg", u] for u in urls])
    on_server = [D.safe_image(u) for u in urls]
    assert in_page == on_server, list(zip(urls, in_page, on_server))
    assert [bool(p) for p in in_page] == [True, True, True, True, False, False, False, False, False, False, False, False, True, False, False, False, True], in_page      # any case; no port, name, backslash or space


def test_a_sitelink_card_shows_its_words_both_lines_and_its_link_and_how_it_did():
    h = one("tile", asset(SITELINK, i=12809, k=872))
    assert "New patient offer" in h and "Exam and x-rays" in h and "Book now" in h
    assert 'href="https://www.example.com/offers/new-patient?utm_source=x"' in h and 'target="_blank"' in h and 'rel="noopener noreferrer"' in h
    assert ">www.example.com/offers/new-patient<" in h                                                    # the link as words: no scheme, no tracking tail
    assert "<b>12,809</b> impressions" in h and "<b>872</b> clicks" in h and "last 30 days" in h and "This campaign" in h


def test_a_link_that_is_not_plain_web_is_words_not_a_link():
    for url in ("javascript:alert(1)", "data:text/html,hi", "ftp://x.com/a"):
        t = dict(asset(SITELINK), u=url)
        assert "href" not in one("tile", t), url
    assert one("safeHttp", "https://x.com/a") == "https://x.com/a" and one("safeHttp", "javascript:1") == "" and one("safeHttp", "https://x.com/a b") == ""


def test_a_problem_is_marked_and_says_why():
    t = asset(SITELINK, st="expired", why=["Ended 2026-05-31 but still switched on"])
    t["e"] = "2026-05-31"
    h = one("tile", t)
    assert "ac-tile bad" in h and "Past end date" in h and "Ended 2026-05-31 but still switched on" in h and "Ends 2026-05-31" in h
    h = one("tile", asset(SITELINK, st="disapproved", why=["Disapproved by Google"]))
    assert "ac-tile bad" in h and ">Disapproved<" in h and "Disapproved by Google" in h
    assert "ac-tile muted" in one("tile", asset(SITELINK, st="paused", why=["Paused"])) and "Under review" in one("tile", asset(SITELINK, st="pending", why=["Under review"]))
    live = one("tile", asset(SITELINK))
    assert "ac-tile live" in live and "Live" not in live and "ac-why" not in live                         # a live asset carries no warning


def test_where_it_is_linked_and_who_added_it_are_said():
    assert "Account-wide" in one("tile", asset(SITELINK, lv="account")) and "This asset group" in one("tile", asset(SITELINK, lv="asset group")) and "This ad" in one("tile", asset(SITELINK, lv="ad"))
    assert "Added by Google" in one("tile", asset(SITELINK, auto=1))
    assert "Account-wide" not in one("tile", asset(SITELINK, lv="account"), {"library": True})            # a library says it in its own "used by" line


def test_the_other_kinds_each_show_what_they_are():
    snippet = one("tile", asset({"type": "STRUCTURED_SNIPPET", "structuredSnippetAsset": {"header": "Types", "values": ["Exam", "Cleaning"]}}))
    assert "Types" in snippet and ">Exam<" in snippet and ">Cleaning<" in snippet
    assert "(781) 318-8040" in one("tile", asset({"type": "CALL", "callAsset": {"phoneNumber": "(781) 318-8040", "countryCode": "US"}})) and ">US<" in one("tile", asset({"type": "CALL", "callAsset": {"phoneNumber": "1", "countryCode": "US"}}))
    promo = one("tile", asset({"type": "PROMOTION", "promotionAsset": {"promotionTarget": "Whitening", "percentOff": "10000000", "promotionCode": "SAVE10", "occasion": "BLACK_FRIDAY"}}))
    assert "Whitening" in promo and "10% off" in promo and "Code SAVE10" in promo and "Black friday" in promo
    price = one("tile", asset({"type": "PRICE", "priceAsset": {"type": "SERVICES", "priceOfferings": [{"header": "Crown", "description": "Per tooth", "price": {"currencyCode": "USD", "amountMicros": "900000000"}}]}}))
    assert "Services" in price and "<b>Crown</b>" in price and "USD 900" in price and "Per tooth" in price
    form = one("tile", asset({"type": "LEAD_FORM", "leadFormAsset": {"businessName": "Acme", "headline": "Book a visit", "description": "Tell us more.", "callToActionType": "BOOK_NOW"}}))
    assert "Book a visit" in form and "Tell us more." in form and ">Acme<" in form and ">Book now<" in form
    text = one("tile", asset({"type": "TEXT", "textAsset": {"text": "Gentle Dental Hudson"}}))
    assert "Gentle Dental Hudson" in text and "20 characters" in text and "has-m" not in text
    assert "Learn more" in one("tile", asset({"type": "CALL_TO_ACTION", "callToActionAsset": {"callToAction": "LEARN_MORE"}}))
    assert "Main Street" in one("tile", {"t": "SET", "x": "Main Street", "n": "Location sync", "st": "live", "lv": "campaign"}) and "Location sync" in one("tile", {"t": "SET", "x": "Main Street", "n": "Location sync", "st": "live"})
    assert "bundle.zip" in one("tile", asset({"type": "MEDIA_BUNDLE", "name": "bundle.zip"}))


def test_a_video_card_shows_its_thumbnail_and_opens_on_youtube_and_a_bad_id_is_not_trusted():
    v = asset({"type": "YOUTUBE_VIDEO", "youtubeVideoAsset": {"youtubeVideoId": "DceKeCNqo-g", "youtubeVideoTitle": "New Patient Deal"}})
    h = one("tile", v)
    assert 'src="https://i.ytimg.com/vi/DceKeCNqo-g/hqdefault.jpg"' in h and 'href="https://www.youtube.com/watch?v=DceKeCNqo-g"' in h and "New Patient Deal" in h and 'rel="noopener noreferrer"' in h
    for bad in ('x"><script>alert(1)</script>', "a b", "ab", "", "x" * 40):
        t = dict(v, vid=bad)
        h = one("tile", t)
        assert "ytimg" not in h and "youtube.com/watch" not in h and "<script" not in h, bad


def test_every_word_from_google_is_escaped():
    evil = "<script>alert(1)</script>"
    t = asset({"type": "SITELINK", "finalUrls": ['https://x.com/"><b>'], "sitelinkAsset": {"linkText": evil, "description1": evil, "description2": "<img src=x onerror=alert(2)>"}},
              why=[evil], camps=[{"key": "1:2", "name": evil}], account=evil, nc=1)
    h = one("tile", t, {"library": True})
    assert "<script" not in h and "<img" not in h and "&lt;script&gt;" in h and "&lt;img src=x" in h
    snippet = asset({"type": "STRUCTURED_SNIPPET", "structuredSnippetAsset": {"header": evil, "values": [evil]}})
    assert "<script" not in one("tile", snippet) and "<script" not in one("chip", snippet)
    name = dict(asset(IMG), n=evil)
    assert "<script" not in one("tile", name) and "<script" not in one("chip", name) and 'alt="&lt;script&gt;' in one("tile", name)
    assert one("esc", '<a href="x">&\'') == "&lt;a href=&quot;x&quot;&gt;&amp;&#39;" and one("esc", None) == "" and one("esc", 0) == "0"


def test_a_library_card_says_who_uses_it_and_how_many_copies():
    t = asset(SITELINK, nc=14, ng=2, acct=1, copies=3, account="Acme Dental", camps=[{"key": "1:11", "name": "Alpha"}, {"key": "1:12", "name": "Beta"}])
    h = one("tile", t, {"library": True})
    assert "Used by Acme Dental · 3 identical copies · 14 campaigns · 2 asset groups · account-wide" in h
    assert 'data-camp="1:11"' in h and ">Alpha<" in h and ">Beta<" in h and "and 12 more" in h
    one_camp = one("tile", asset(SITELINK, nc=1, camps=[{"key": "1:11", "name": "Alpha"}]), {"library": True})
    assert "identical copies" not in one("tile", asset(SITELINK, nc=1, copies=1), {"library": True})
    assert "Used by 1 campaign<" in one_camp and "and " not in one_camp.split("Used by")[1]
    assert "ac-use" not in one("tile", asset(SITELINK), {"library": True})                                  # unused: nothing to say
    assert "ac-use" not in one("tile", t)                                                                    # only a library says it


def test_a_chip_is_the_first_words_of_an_asset_and_a_picture_is_a_thumbnail():
    assert one("chip", asset(SITELINK)).count("New patient offer") == 2                                      # the words, and again as the hover title
    assert 'class="ac-chip live img"' in one("chip", asset(IMG)) and '<img src="%s"' % GOOGLE_IMG in one("chip", asset(IMG))
    long = one("chip", asset({"type": "CALLOUT", "calloutAsset": {"calloutText": "x" * 100}}))
    assert "x" * 45 + "…" in long and "x" * 46 not in long
    assert "Types: Exam, Cleaning, Whitening +1" in one("chip", asset({"type": "STRUCTURED_SNIPPET", "structuredSnippetAsset": {"header": "Types", "values": ["Exam", "Cleaning", "Whitening", "X-rays"]}}))
    assert "Video: Tour" in one("chip", asset({"type": "YOUTUBE_VIDEO", "youtubeVideoAsset": {"youtubeVideoId": "abcdefghijk", "youtubeVideoTitle": "Tour"}}))
    assert "Price (2)" in one("chip", asset({"type": "PRICE", "priceAsset": {"priceOfferings": [{"header": "a"}, {"header": "b"}]}}))
    assert "ac-chip bad" in one("chip", asset(SITELINK, st="disapproved")) and "ac-chip bad" not in one("chip", asset(SITELINK))
    assert 'title="New patient offer (past end date)"' in one("chip", asset(SITELINK, st="expired")) and 'title="New patient offer"' in one("chip", asset(SITELINK))


def test_a_strip_shows_the_first_few_and_counts_the_rest():
    items = [asset({"type": "CALLOUT", "calloutAsset": {"calloutText": "Callout %d" % i}}) for i in range(11)]
    h = one("preview", items, 8)
    assert h.count('class="ac-chip live"') == 8 and "Callout 7" in h and "Callout 8" not in h and "+3 more" in h
    assert "more" not in one("preview", items[:8], 8) and one("preview", [], 8) == ""
    assert one("preview", items).count("ac-chip live") == 8                                                  # eight is the default


def test_a_grid_of_nothing_is_nothing():
    assert one("tiles", []) == "" and one("tiles", [asset(SITELINK)]).startswith('<div class="ac-tgrid"><article')
    assert one("tiles", [asset(SITELINK), asset(IMG)]).count("<article") == 2
