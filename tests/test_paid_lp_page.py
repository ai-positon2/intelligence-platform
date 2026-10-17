"""Landing Page & Relevancy Check: a landing page as it first arrives (tracker/paid_lp_page.py)."""
import io
import threading
import time

from tracker import paid_lp_page as p

PAGE = """<!doctype html><html lang="en"><head><title>Emergency Dentist Boston | Acme Dental</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Same day care in Boston.">
<meta name="robots" content="index,follow">
<link rel="stylesheet" href="/a.css"><link rel="stylesheet" href="/b.css">
<script src="/blocking.js"></script><script src="/deferred.js" defer></script><script src="https://cdn.other.com/x.js" async></script>
<script src="//www.googletagmanager.com/gtm.js?id=GTM-ABC1234"></script>
<script>window.dataLayer=1;</script><style>.x{color:red}</style></head>
<body><header><nav><a href="/">Home</a><a href="/about">About</a></nav><button class="menu-toggle">Menu</button></header>
<h1>Emergency Dentist in <a href="/boston">Boston</a></h1><h2>Why choose us</h2><h3>Open today</h3>
<p>Call (617) 555-0123 or visit 12 Main Street. We are 5-star rated, licensed and insured, in business since 1998.</p>
<a href="tel:+16175550123">Call now</a> <a class="btn" href="/book">Reserve a seat</a> <a href="/more">Learn more</a> <a href="#top">Top</a>
<a href="mailto:hi@acme.test">Email us</a> <a href="https://www.facebook.com/acme">Facebook</a> <a href="https://partner.example.org/x">Partner</a>
<a href="/privacy-policy">Privacy policy</a> <a href="javascript:void(0)">Do nothing</a>
<form action="/send" method="post"><input type="text" name="fullname" required placeholder="Your name"><input type="email" name="email" required>
<input type="tel" name="phone"><input type="hidden" name="csrf" value="1"><input type="text" name="website" style="display:none">
<input type="checkbox" name="consent"><input type="radio" name="when" value="am"><input type="radio" name="when" value="pm">
<select name="svc"><option>Cleaning</option><option>Whitening</option></select><textarea name="msg"></textarea><input type="submit" value="Get a free quote"></form>
<form role="search" action="/search"><input type="text" name="q"></form>
<iframe src="https://forms.hubspot.com/embed/1"></iframe><iframe src="https://www.youtube.com/embed/x"></iframe>
<img src="a.jpg" width="10" height="10" alt="x"><img src="b.jpg"><img src="c.jpg" loading="lazy" alt=""><img data-src="d.jpg" alt="y">
<footer>We use cookies. <a href="/terms">Terms</a></footer><script>var a = 1; fbq('init','123456789012345');</script></body></html>"""


def parsed(html=PAGE, url="https://acme.test/lp"):
    return p.parse(html, url)


def test_the_title_headings_and_description_are_read_and_the_first_heading_survives_a_link_inside_it():
    f = parsed()["facts"]
    assert f["title"] == "Emergency Dentist Boston | Acme Dental" and f["metaDescription"] == "Same day care in Boston." and f["lang"] == "en"
    assert f["h1"] == ["Emergency Dentist in Boston"] and f["h1Count"] == 1 and f["h2"] == ["Why choose us"] and f["h3"] == ["Open today"]


def test_the_visible_text_leaves_out_scripts_styles_options_and_the_title():
    f = parsed()["facts"]
    assert "dataLayer" not in f["text"] and "color:red" not in f["text"] and "Cleaning" not in f["text"] and "Whitening" not in f["text"]
    assert "Acme Dental" not in f["text"] and "Why choose us" in f["text"] and "Reserve a seat" in f["text"]
    assert f["wordCount"] == len(f["text"].split())


def test_the_text_kept_is_capped():
    big = "<html><body>" + "<p>word " * 8000 + "</body></html>"
    f = parsed(big)["facts"]
    assert len(f["text"]) <= p.TEXT_CHARS and f["wordCount"] == 8000


def test_viewport_robots_and_https_are_read():
    r = parsed()
    assert r["facts"]["viewport"] == "width=device-width, initial-scale=1" and r["facts"]["noindex"] is False and r["tech"]["mobile"] is True
    assert parsed(PAGE.replace("index,follow", "noindex,nofollow"))["facts"]["noindex"] is True
    assert parsed(PAGE.replace('<meta name="viewport" content="width=device-width, initial-scale=1">', ""))["tech"]["mobile"] is False


def test_the_form_has_its_visible_fields_counted_and_hidden_and_trap_fields_left_out():
    forms = parsed()["facts"]["forms"]
    main = forms[0]
    assert main["visible"] == 5                          # name, email, phone, the two radios as one, the message box; hidden and display:none left out, the select is not an input
    assert [f["kind"] for f in main["fields"]].count("hidden") == 0 and "website" not in [f["name"] for f in main["fields"]]
    assert main["required"] == 2 and main["checks"] == 1 and main["phone"] is True and main["email"] is True and main["search"] is False
    assert main["method"] == "post" and main["action"] == "/send"


def test_a_search_box_is_marked_as_a_search_form():
    forms = parsed()["facts"]["forms"]
    assert len(forms) == 2 and forms[1]["search"] is True and forms[1]["visible"] == 1


def test_a_lone_field_named_like_a_search_is_a_search_even_in_an_unmarked_form():
    f = parsed('<html><body><form><input name="s"></form><form><input name="email" type="email"></form></body></html>')["facts"]["forms"]
    assert [x["search"] for x in f] == [True, False]


def test_inputs_outside_any_form_are_one_form_when_there_are_two_or_more():
    html = "<html><body><input name='a'><input name='b' required><button>Send</button></body></html>"
    f = parsed(html)["facts"]["forms"]
    assert len(f) == 1 and f[0]["loose"] is True and f[0]["visible"] == 2 and f[0]["required"] == 1
    assert parsed("<html><body><input name='a'></body></html>")["facts"]["forms"] == []


def test_an_embedded_form_or_booking_frame_is_named_by_its_host_and_other_frames_are_not():
    assert parsed()["facts"]["embeds"] == ["forms.hubspot.com"]


def test_calls_to_action_are_found_with_how_far_down_they_are_and_a_menu_button_is_not_one():
    ctas = parsed()["facts"]["ctas"]
    by = {c["text"]: c for c in ctas}
    assert by["Call now"]["kind"] == "strong" and by["Reserve a seat"]["kind"] == "strong" and by["Learn more"]["kind"] == "weak"
    assert by["Get a free quote"]["kind"] == "strong"            # a form's submit button
    assert "Menu" not in by and "Home" not in by and "Facebook" not in by
    assert by["Call now"]["at"] < by["Get a free quote"]["at"]


def test_a_submit_button_inside_a_form_is_a_call_to_action_whatever_it_says():
    html = "<html><body><form><input name='a'><button>Go</button></form><button>Slide</button></body></html>"
    texts = [c["text"] for c in parsed(html)["facts"]["ctas"]]
    assert texts == ["Go"]


def test_a_phone_link_is_a_call_to_action_whatever_its_label_says():
    ctas = parsed('<html><body><p>Hello there</p><a href="tel:+16175550123">(617) 555-0123</a></body></html>')["facts"]["ctas"]
    assert [(c["text"], c["kind"]) for c in ctas] == [("(617) 555-0123", "strong")]


def test_a_page_built_by_scripts_is_recognised_by_its_few_words_and_its_app_shell_or_many_scripts():
    shell = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    assert parsed(shell)["facts"]["scriptBuilt"] is True and parsed(shell)["facts"]["appRoot"] is True
    many = "<html><head>" + '<script src="/s%d.js"></script>' % 1 * 1 + "".join('<script src="/s%d.js"></script>' % i for i in range(7)) + "</head><body><p>Loading</p></body></html>"
    assert parsed(many)["facts"]["scriptBuilt"] is True
    assert parsed("<html><body><p>Just a few words</p></body></html>")["facts"]["scriptBuilt"] is False                  # short, but nothing says scripts build it
    full = '<html><body><div id="root">' + "<p>word</p>" * 120 + "</div></body></html>"
    assert parsed(full)["facts"]["scriptBuilt"] is False                                                                # an app shell that has its words in it


def test_a_skip_link_is_not_a_call_to_action():
    assert parsed('<html><body><a class="button" href="#c">Skip to content</a></body></html>')["facts"]["ctas"] == []


def test_a_styled_button_link_is_a_weak_call_to_action_when_its_words_are_not_a_known_verb():
    ctas = parsed('<html><body><a class="btn-primary" href="/x">Our Services</a></body></html>')["facts"]["ctas"]
    assert [(c["text"], c["kind"]) for c in ctas] == [("Our Services", "weak")]


def test_a_very_long_link_is_not_a_call_to_action():
    long_label = "Book " + "x" * 60
    assert parsed('<html><body><a href="/x">%s</a></body></html>' % long_label)["facts"]["ctas"] == []


def test_phone_links_email_links_numbers_in_the_text_and_an_address_are_read():
    f = parsed()["facts"]
    assert f["tel"] == ["+16175550123"] and f["mailto"] == ["hi@acme.test"] and f["phones"] == ["(617) 555-0123"] and f["address"] is True


def test_links_are_counted_with_the_nav_ones_and_the_ones_that_leave_the_site():
    links = parsed()["facts"]["links"]
    assert links == {"total": 9, "nav": 2, "outbound": 1}                    # facebook is not counted as leaving; tel, mailto, # and javascript: are not links


def test_a_privacy_link_is_found_by_its_address_or_its_words():
    assert parsed()["facts"]["privacyLink"] is True
    assert parsed('<html><body><a href="/legal">Our Privacy Notice</a></body></html>')["facts"]["privacyLink"] is True
    assert parsed('<html><body><a href="/terms">Terms</a></body></html>')["facts"]["privacyLink"] is False


def test_images_are_counted_with_the_ones_without_a_size_lazy_ones_and_missing_alt():
    assert parsed()["facts"]["images"] == {"count": 4, "noSize": 3, "lazy": 2, "noAlt": 1}


def test_scripts_that_block_the_first_paint_are_counted_in_the_head_only():
    head = parsed()["facts"]["head"]
    assert head["blockingScripts"] == 2 and head["stylesheets"] == 2                # blocking.js and the tag manager; deferred and async are not
    assert head["scripts"] == 4 and head["thirdParty"] == ["cdn.other.com", "www.googletagmanager.com"]
    body_script = parsed('<html><head></head><body><script src="/late.js"></script></body></html>')["facts"]["head"]
    assert body_script["blockingScripts"] == 0 and body_script["scripts"] == 1


def test_proof_the_cookie_banner_and_tags_are_found():
    r = parsed()
    assert set(r["facts"]["proof"]) >= {"star rating", "credentials", "experience"} and r["facts"]["consent"] is True
    names = [t["name"] for t in r["tech"]["tools"]]
    assert "Google Tag Manager" in names and "Meta Pixel" in names


def test_rating_markup_in_the_page_counts_as_proof():
    html = '<html><body><script type="application/ld+json">{"@type":"AggregateRating","ratingValue":"4.9"}</script>hello</body></html>'
    assert "rating markup" in parsed(html)["facts"]["proof"]


def test_a_booking_widget_and_an_add_to_cart_button_are_noticed():
    r = parsed('<html><body><script src="https://calendly.com/assets/external/widget.js"></script><form><input name="q2" required><button>Add to cart</button></form></body></html>')
    assert r["facts"]["booking"] is True and r["facts"]["commerce"] is True
    assert parsed()["facts"]["booking"] is False and parsed()["facts"]["commerce"] is False


def test_unclosed_and_broken_markup_does_not_stop_the_read():
    f = parsed("<html><body><h1>Hello <b>world<p>text <a href='/x'>Book now<div>more <span")["facts"]
    assert f["h1"] == ["Hello world text Book nowmore"] or f["wordCount"] >= 4
    assert f["wordCount"] >= 4


def test_an_unclosed_link_does_not_swallow_the_page_into_one_label():
    html = "<html><body><a href='/x'>Book now" + "<p>filler text here</p>" * 200 + "</body></html>"
    f = parsed(html)["facts"]
    assert f["wordCount"] > 100 and all(len(c["text"]) <= 45 for c in f["ctas"])


def test_an_empty_page_has_no_facts_but_does_not_fail():
    f = parsed("")["facts"]
    assert f["wordCount"] == 0 and f["forms"] == [] and f["ctas"] == [] and f["title"] == ""


# ── Fetching ────────────────────────────────────────────────────────────────

class Raw:
    def __init__(self, body):
        self.io = io.BytesIO(body)

    def read(self, n=-1, decode_content=False):
        return self.io.read(n)


class Resp:
    def __init__(self, status=200, url="", body=b"", headers=None, closed=None):
        self.status_code, self.url, self.raw, self.headers, self._closed = status, url, Raw(body), dict(headers or {}), closed

    def close(self):
        if self._closed is not None:
            self._closed.append(self.url)


def html_bytes(text="<html><head><title>T</title></head><body><h1>Hello</h1>" + "<p>word</p>" * 5 + "</body></html>"):
    return text.encode()


def fetcher(answers, calls=None, closed=None):
    """answers: url -> (status, final url, body, headers) | exception | callable; anything else is a 200 page."""
    def get(url, **kw):
        if calls is not None:
            calls.append((url, kw))
        a = answers.get(url)
        if callable(a):
            a = a(url)
        if isinstance(a, Exception):
            raise a
        if a is None:
            return Resp(200, url, html_bytes(), {"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip"}, closed)
        status, final, body, headers = a
        return Resp(status, final or url, body, headers, closed)
    return get


def test_a_page_that_answers_is_recorded_with_its_timings_size_compression_and_facts():
    closed, calls = [], []
    r = p.fetch("https://a.test/x", fetcher({}, calls, closed))
    assert r["status"] == 200 and r["error"] == "" and r["isHtml"] is True and r["blocked"] is False and r["redirected"] is False
    assert r["encoding"] == "gzip" and r["bytes"] > 0 and r["truncated"] is False and r["finalUrl"] == "https://a.test/x"
    assert r["responseMs"] >= 0 and r["ms"] >= r["responseMs"]
    assert r["facts"]["h1"] == ["Hello"] and r["tech"]["https"] is True and closed == ["https://a.test/x"]
    url, kw = calls[0]
    assert kw["timeout"] == p.TIMEOUT_S and "Position2LandingPageCheck" in kw["headers"]["User-Agent"] and kw["headers"]["Accept-Encoding"] == "gzip, deflate"


def test_an_http_page_is_recorded_as_not_https():
    r = p.fetch("http://a.test/x", fetcher({}))
    assert r["tech"]["https"] is False


def test_a_page_that_refuses_automated_requests_is_blocked_not_broken():
    for status in (401, 403, 429):
        r = p.fetch("https://a.test/x", fetcher({"https://a.test/x": (status, "", b"no", {"Content-Type": "text/html"})}))
        assert r["status"] == status and r["blocked"] is True and r["facts"] is None and r["isHtml"] is False


def test_a_missing_page_is_recorded_without_facts():
    r = p.fetch("https://a.test/x", fetcher({"https://a.test/x": (404, "", b"<html>gone</html>", {"Content-Type": "text/html"})}))
    assert r["status"] == 404 and r["blocked"] is False and r["facts"] is None


def test_a_page_that_cannot_be_reached_says_why_and_a_timeout_is_tried_once_more():
    calls = []
    r = p.fetch("https://a.test/x", fetcher({"https://a.test/x": TimeoutError("timed out")}, calls))
    assert r["status"] == 0 and r["error"] == "timed out" and r["retried"] is True and len(calls) == 2
    calls2 = []
    r = p.fetch("https://a.test/x", fetcher({"https://a.test/x": ValueError("Private or reserved page destinations are not allowed.")}, calls2))
    assert r["error"].startswith("refused") and r["retried"] is False and len(calls2) == 1


def test_a_page_that_times_out_once_and_then_answers_is_recorded_as_answered():
    state = {"n": 0}

    def flaky(url):
        state["n"] += 1
        if state["n"] == 1:
            return TimeoutError("timed out")
        return (200, url, html_bytes(), {"Content-Type": "text/html"})
    r = p.fetch("https://a.test/x", fetcher({"https://a.test/x": flaky}))
    assert r["status"] == 200 and r["retried"] is True and r["error"] == "" and r["facts"] is not None


def test_a_file_that_is_not_html_is_not_read_as_a_page():
    r = p.fetch("https://a.test/x.pdf", fetcher({"https://a.test/x.pdf": (200, "", b"%PDF-1.4", {"Content-Type": "application/pdf"})}))
    assert r["status"] == 200 and r["isHtml"] is False and r["facts"] is None and r["contentType"] == "application/pdf"


def test_a_page_with_no_content_type_is_read_as_html():
    r = p.fetch("https://a.test/x", fetcher({"https://a.test/x": (200, "", html_bytes(), {})}))
    assert r["isHtml"] is True


def test_a_very_large_page_is_cut_and_says_so():
    big = ("<html><body>" + "<p>x</p>" * 200_000 + "</body></html>").encode()
    r = p.fetch("https://a.test/x", fetcher({"https://a.test/x": (200, "", big, {"Content-Type": "text/html"})}))
    assert r["truncated"] is True and r["bytes"] == p.MAX_BYTES


def test_a_body_that_fails_midway_keeps_the_status_and_names_the_error():
    class Broken(Resp):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)

            class R:
                def read(self, n=-1, decode_content=False):
                    raise ConnectionResetError("reset")
            self.raw = R()
    r = p.fetch("https://a.test/x", lambda url, **kw: Broken(200, url, b"", {"Content-Type": "text/html"}))
    assert r["status"] == 200 and r["facts"] is None and r["error"] == "ConnectionResetError"


def test_a_redirect_is_noticed_and_a_deep_link_sent_to_the_home_page_is_called_that():
    r = p.fetch("https://a.test/deal/x", fetcher({"https://a.test/deal/x": (200, "https://a.test/", html_bytes(), {"Content-Type": "text/html"})}))
    assert r["redirected"] is True and r["collapsed"] is True and r["finalUrl"] == "https://a.test/"
    r2 = p.fetch("https://a.test/deal/x", fetcher({"https://a.test/deal/x": (200, "https://a.test/offers/x", html_bytes(), {"Content-Type": "text/html"})}))
    assert r2["redirected"] is True and r2["collapsed"] is False
    r3 = p.fetch("https://a.test/x?a=1", fetcher({"https://a.test/x?a=1": (200, "https://a.test/x/?b=2", html_bytes(), {"Content-Type": "text/html"})}))
    assert r3["redirected"] is False                                     # the same page with other parameters is not a redirect
    r4 = p.fetch("https://a.test/", fetcher({"https://a.test/": (200, "https://a.test/home", html_bytes(), {"Content-Type": "text/html"})}))
    assert r4["redirected"] is True and r4["collapsed"] is False


def test_the_click_id_is_checked_only_after_a_redirect_and_says_whether_it_survived():
    def keeps(url):
        if "gclid" in url:
            return (200, "https://a.test/offers?gclid=LPcheck123&utm_source=lp_check", html_bytes(), {"Content-Type": "text/html"})
        return (200, "https://a.test/offers", html_bytes(), {"Content-Type": "text/html"})

    def drops(url):
        return (200, "https://a.test/offers", html_bytes(), {"Content-Type": "text/html"})
    assert p.fetch("https://a.test/x", fetcher({"https://a.test/x": keeps, "https://a.test/x?" + p.TEST_PARAMS: keeps}))["paramsSurvive"] is True
    assert p.fetch("https://a.test/x", fetcher({"https://a.test/x": drops, "https://a.test/x?" + p.TEST_PARAMS: drops}))["paramsSurvive"] is False
    calls = []
    assert p.fetch("https://a.test/x", fetcher({}, calls))["paramsSurvive"] is None and len(calls) == 1          # no redirect, no second request


def test_the_click_id_test_adds_its_parameters_to_an_address_that_already_has_a_query():
    calls = []
    p.fetch("https://a.test/x?a=1", fetcher({"https://a.test/x?a=1": (200, "https://a.test/y", html_bytes(), {"Content-Type": "text/html"})}, calls))
    assert calls[1][0] == "https://a.test/x?a=1&" + p.TEST_PARAMS


def test_a_failed_click_id_test_is_not_taken_as_the_click_id_being_lost():
    ans = {"https://a.test/x": (200, "https://a.test/y", html_bytes(), {"Content-Type": "text/html"}), "https://a.test/x?" + p.TEST_PARAMS: OSError("boom")}
    assert p.fetch("https://a.test/x", fetcher(ans))["paramsSurvive"] is None


# ── robots.txt and AdsBot ───────────────────────────────────────────────────

def robots_fetcher(body, status=200, calls=None):
    def get(url, **kw):
        if calls is not None:
            calls.append(url)
        if url.endswith("/robots.txt"):
            if body is None:
                raise OSError("down")
            return Resp(status, url, body.encode(), {"Content-Type": "text/plain"})
        return Resp(200, url, html_bytes(), {"Content-Type": "text/html"})
    return get


def test_a_site_with_no_rule_naming_adsbot_is_open_to_it_whatever_the_star_rules_say():
    allowed = p.make_robots(robots_fetcher("User-agent: *\nDisallow: /\n"))
    assert allowed("https://a.test/x") is True


def test_a_rule_naming_adsbot_is_obeyed():
    allowed = p.make_robots(robots_fetcher("User-agent: AdsBot-Google\nDisallow: /private\n\nUser-agent: *\nAllow: /\n"))
    assert allowed("https://a.test/private/x") is False and allowed("https://a.test/open") is True


def test_no_robots_file_means_nothing_is_blocked_and_an_unreadable_one_means_unknown():
    assert p.make_robots(robots_fetcher("gone", status=404))("https://a.test/x") is True
    assert p.make_robots(robots_fetcher(None))("https://a.test/x") is None
    assert p.make_robots(robots_fetcher("<html><body>a page</body></html>"))("https://a.test/x") is None


def test_a_robots_request_that_fails_is_made_once_more_and_a_second_failure_is_unknown_not_blocked():
    state = {"n": 0}

    def flaky(url, **kw):
        if url.endswith("/robots.txt"):
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("reset")
            return Resp(200, url, b"User-agent: AdsBot-Google\nDisallow: /private\n", {"Content-Type": "text/plain"})
        return Resp(200, url, html_bytes(), {"Content-Type": "text/html"})
    allowed = p.make_robots(flaky)
    assert allowed("https://a.test/private/x") is False and state["n"] == 2
    always = {"n": 0}

    def down(url, **kw):
        always["n"] += 1
        raise OSError("down")
    allowed2 = p.make_robots(down)
    assert allowed2("https://a.test/x") is None and allowed2("https://a.test/y") is None and always["n"] == 2       # tried twice, then remembered for the run


def test_robots_txt_is_read_once_per_site():
    calls = []
    allowed = p.make_robots(robots_fetcher("User-agent: *\nAllow: /\n", calls=calls))
    for path in ("/a", "/b", "/c"):
        allowed("https://a.test" + path)
    allowed("https://b.test/a")
    assert calls == ["https://a.test/robots.txt", "https://b.test/robots.txt"]


def test_fetch_records_whether_adsbot_may_read_the_page_and_a_failing_check_costs_nothing():
    r = p.fetch("https://a.test/x", fetcher({}), robots=lambda url: False)
    assert r["adsbot"] is False

    def boom(url):
        raise RuntimeError("x")
    r = p.fetch("https://a.test/x", fetcher({}), robots=boom)
    assert r["adsbot"] is None and r["status"] == 200


# ── A list of pages ─────────────────────────────────────────────────────────

def test_many_pages_come_back_in_order_each_with_adsbot_asked_once_per_site():
    urls = ["https://a.test/%d" % i for i in range(5)] + ["https://b.test/z"]
    calls = []
    out = p.fetch_many(urls, robots_fetcher("User-agent: *\nAllow: /\n", calls=calls))
    assert [r["url"] for r in out] == urls and all(r["status"] == 200 and r["adsbot"] is True for r in out)
    assert sorted(c for c in calls if c.endswith("robots.txt")) == ["https://a.test/robots.txt", "https://b.test/robots.txt"]
    assert p.fetch_many([]) == []


def test_a_page_not_read_inside_the_time_budget_is_reported_not_waited_for():
    gate = threading.Event()

    def slow(url, **kw):
        if "slow" in url:
            gate.wait(5)
        return Resp(200, url, html_bytes(), {"Content-Type": "text/html"})
    t0 = time.monotonic()
    out = p.fetch_many(["https://a.test/fast", "https://a.test/slow"], slow, workers=2, budget_s=0.3)
    gate.set()
    assert time.monotonic() - t0 < 3
    assert out[0]["status"] == 200 and out[1]["skipped"] is True and out[1]["error"] == "not read in time" and out[1]["facts"] is None


def test_one_page_that_raises_does_not_take_the_others_down():
    def get(url, **kw):
        if "bad" in url:
            raise RuntimeError("x")
        return Resp(200, url, html_bytes(), {"Content-Type": "text/html"})
    out = p.fetch_many(["https://a.test/ok", "https://a.test/bad"], get)
    assert out[0]["status"] == 200 and out[1]["status"] == 0 and out[1]["error"] == "RuntimeError"
