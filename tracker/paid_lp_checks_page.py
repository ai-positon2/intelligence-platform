"""Paid Marketing Agents: Landing Page & Relevancy Check, the checks about the page itself.

Five of the six sections: the page opens, can a visitor act, works on a phone, loads fast, tracking and trust. (Does it match the ad is
tracker/paid_lp_checks_match.py.) A check function takes a `Ctx` (the page and what was read of it) and returns one finding, or None when it has
nothing to say. It decides a result and says what it saw with its numbers; the catalog below holds the wording that never changes.

What a check could not see it says so: a page that refused us, a page that did not open or one built by scripts leaves the checks that need
the HTML as "could not check", never as a pass or a failure. The server time, page weight and script counts are measured from our servers on the
page's HTML alone; the optional lab test (tracker/paid_lp_speed.py) and Google's own speed score are shown beside them, never replaced by them.
"""
from __future__ import annotations

import re

from . import paid_lp_model as m
from .paid_lp_model import make, not_applicable, plural, unknown
from .paid_lp_page import ATF_CHARS, MAX_BYTES, TIMEOUT_S
from .paid_strategy_research import registrable

# ── Thresholds ──────────────────────────────────────────────────────────────

SERVER_OK_MS, SERVER_SLOW_MS = 1500, 3000          # time for the server to start answering, counted from the request, so DNS, the secure connection and any redirect are in it
HTML_OK_KB, HTML_HEAVY_KB = 300, 600
COMPRESSED = ("gzip", "br", "deflate", "zstd")
TINY_BYTES = 5000                                  # a page this small is not worth compressing
BLOCKING_OK, BLOCKING_MANY = 2, 5                  # scripts in the head that stop the first paint
SCRIPTS_OK, SCRIPTS_MANY = 20, 40
FORM_OK, FORM_LONG = 5, 8                          # visible fields
LINKS_OK, LINKS_MANY = 40, 150
SPEED_GOOD, SPEED_FAIR = 7, 5                      # Google's mobile speed score, one to ten
LAB_GOOD, LAB_FAIR = 90, 50                        # the lab test's performance score
GAP_MIN_CLICKS = 150                               # clicks on each device before the two are compared
GAP_MIN_MOBILE_SHARE = 25                          # per cent of clicks that are mobile, from which a gap matters
GAP_OK, GAP_WIDE = 0.7, 0.4                        # mobile conversion rate as a share of desktop's
GOOGLE_TAGS = ("Google Tag Manager", "Google Analytics 4", "Google Ads tag")

m.register([
    {"id": "reach_status", "section": "reach", "title": "The page opens", "severity": "critical",
     "why": "An ad that sends people to a page that does not open pays for clicks that can never convert, and Google marks the ad's landing page experience down.",
     "fix": "Open the address from the ad. If it fails, correct the final URL in the ad or restore the page."},
    {"id": "reach_redirect", "section": "reach", "title": "The ad's address does not bounce", "severity": "medium",
     "why": "Each redirect adds a wait on a phone, and a redirect that drops people on the home page throws away the work the ad did to promise something specific.",
     "fix": "Put the address people should land on straight into the ad's final URL, so there is no redirect."},
    {"id": "reach_params", "section": "reach", "title": "The click id survives the redirect", "severity": "high",
     "why": "When a redirect drops the click id (gclid) and the UTM tags, Google Ads and Analytics lose track of which click became a customer.",
     "fix": "Have the redirect keep the whole query string, or send the ad straight to the final address."},
    {"id": "reach_params_meta", "section": "reach", "title": "Meta's click id survives the redirect", "severity": "high",
     "why": "When a redirect drops the click id (fbclid) and the UTM tags, the Meta pixel cannot match the visit to the ad click, so Meta and Analytics lose track of which click became a customer.",
     "fix": "Have the redirect keep the whole query string, or send the ad straight to the final address."},
    {"id": "reach_https", "section": "reach", "title": "The page is served over https", "severity": "high",
     "why": "Browsers warn visitors away from pages that are not secure and Google may refuse the ad, especially where a form asks for contact details.",
     "fix": "Serve the page over https and use the https address in the ad."},
    {"id": "reach_staging", "section": "reach", "title": "The address is not a test site", "severity": "critical",
     "why": "A staging or test address in a live ad shows the client's budget to a page that is unfinished and often hidden from search engines.",
     "fix": "Replace the final URL with the live site's address."},
    {"id": "reach_adsbot", "section": "reach", "title": "Google's ad crawler may read the page", "severity": "critical",
     "why": "AdsBot judges the landing page. When robots.txt names it and keeps it out, the page cannot be checked and the ad can stop running.",
     "fix": "Remove the rule that blocks AdsBot-Google from the page in robots.txt."},

    {"id": "path_action", "section": "path", "title": "The page gives a way to act", "severity": "critical",
     "why": "A visitor who cannot call, book, buy or send a form leaves, however well the ad matched the page.",
     "fix": "Add the one thing the ad is for: a short form, a tappable phone number, a booking tool or a buy button."},
    {"id": "path_cta_top", "section": "path", "title": "A call to action sits near the top", "severity": "high",
     "why": "Most visitors decide in the first screen. A page that asks for the action only far down loses the ones who never scroll.",
     "fix": "Put a clear button or phone link, in words like Book now or Get a quote, in the first screen."},
    {"id": "path_form_length", "section": "path", "title": "The form is short", "severity": "medium",
     "why": "Every extra field lowers the share of visitors who finish a form, and most of the loss is on a phone.",
     "fix": "Keep the fields the sales team needs to follow up (name, phone or email, one question) and ask for the rest later."},
    {"id": "path_focus", "section": "path", "title": "The page is not a maze of links", "severity": "low",
     "why": "Every other link on a landing page is a way to leave without acting.",
     "fix": "Use a landing page template with a short header and no full site menu for ad traffic."},

    {"id": "mobile_viewport", "section": "mobile", "title": "The page is built for a phone", "severity": "high",
     "why": "Most ad clicks come from phones. A page with no mobile viewport shows as a shrunken desktop page that people must pinch and drag.",
     "fix": "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> and a responsive layout."},
    {"id": "mobile_zoom", "section": "mobile", "title": "Visitors can zoom", "severity": "low",
     "why": "Blocking pinch zoom makes small text unreadable for people who need it and fails accessibility rules.",
     "fix": "Remove user-scalable=no and maximum-scale=1 from the viewport tag."},
    {"id": "mobile_tap_to_call", "section": "mobile", "title": "Phone numbers can be tapped", "severity": "medium",
     "why": "A phone number shown as plain text must be remembered and typed. A tel: link starts the call with one tap.",
     "fix": "Wrap each phone number in a link that begins tel: followed by the number."},
    {"id": "mobile_conv_gap", "section": "mobile", "title": "Phones convert nearly as well as desktops", "severity": "medium",
     "why": "When phone visitors convert at far less than desktop visitors on the same page, the page is probably costing mobile clicks.",
     "fix": "Open the page on a phone, find where it gets hard (the form, a menu, a slow image), and fix that before raising mobile bids."},

    {"id": "speed_google", "section": "speed", "title": "Google's mobile speed score is good", "severity": "medium",
     "why": "Google scores each landing page's mobile speed from one to ten and slow pages lose clicks before they load.",
     "fix": "Compress images, remove scripts the page does not need and ask the developer for the biggest delays in a speed test."},
    {"id": "speed_server", "section": "speed", "title": "The server answers quickly", "severity": "medium",
     "why": "Nothing appears until the server starts to answer, so a slow server delays everything after it.",
     "fix": "Turn on page caching or a content network, or move to faster hosting."},
    {"id": "speed_html", "section": "speed", "title": "The page's HTML is light", "severity": "low",
     "why": "A very large page takes longer to download and to read, most of all on a mobile connection.",
     "fix": "Remove inline scripts, styles and repeated blocks from the page, or load them from files that can be cached."},
    {"id": "speed_compression", "section": "speed", "title": "The page is compressed", "severity": "medium",
     "why": "Without compression the same page is several times larger to download.",
     "fix": "Turn on gzip or brotli compression for HTML on the web server."},
    {"id": "speed_blocking", "section": "speed", "title": "Few scripts hold up the first paint", "severity": "medium",
     "why": "A script in the head that is not async or deferred stops the page from showing until it has downloaded and run.",
     "fix": "Add async or defer to scripts in the head, or move them to the end of the page."},
    {"id": "speed_scripts", "section": "speed", "title": "The page loads a sensible number of scripts", "severity": "medium",
     "why": "Each script is a download and some work for the phone; a page with dozens of them is slow to become usable.",
     "fix": "Remove tags and widgets nobody uses, and load the rest through one tag manager."},
    {"id": "speed_images", "section": "speed", "title": "Images do not shift or weigh the page down", "severity": "low",
     "why": "Images without a set size make the page jump as they load, and images far down the page need not load until someone scrolls there.",
     "fix": "Give every image width and height, and add loading=\"lazy\" to those below the first screen."},
    {"id": "speed_lab", "section": "speed", "title": "The lab speed test is good", "severity": "medium",
     "why": "A lab test loads the page like a mid-range phone on a mobile connection and times what a visitor sees and when it becomes usable.",
     "fix": "Work through the test's own list of biggest delays, starting with images and scripts."},

    {"id": "trust_tracking", "section": "trust", "title": "A Google tag or tag manager is on the page", "severity": "high",
     "why": "Without a tag Google Ads and Analytics cannot count the visit or the sale that follows it.",
     "fix": "Install the Google tag or Tag Manager on the page and check that the conversion fires on the thank-you step."},
    {"id": "trust_meta_pixel", "section": "trust", "title": "The Meta pixel is on the page", "severity": "high",
     "why": "Without the pixel Meta cannot count the visit or the lead or sale that follows it, so it cannot find more people like the ones who act and the ad set learns from clicks alone.",
     "fix": "Install the Meta pixel on the page (directly or through Tag Manager) and check in Events Manager that the conversion fires on the thank-you step."},
    {"id": "trust_privacy", "section": "trust", "title": "There is a privacy policy link", "severity": "medium",
     "why": "Google's rules and privacy laws expect a policy to be reachable wherever a page collects contact details.",
     "fix": "Link the privacy policy in the page's footer and next to the form."},
    {"id": "trust_contact", "section": "trust", "title": "The business can be contacted from the page", "severity": "medium",
     "why": "A page with no phone number, address or email looks like it could be anyone, and visitors who want to ask first have nowhere to go.",
     "fix": "Show the phone number, the address and an email address in the header or footer."},
    {"id": "trust_proof", "section": "trust", "title": "The page shows proof", "severity": "low",
     "why": "Reviews, ratings, awards and years in business answer the visitor's doubt before they fill in a form.",
     "fix": "Add review ratings, a testimonial or two and any awards or credentials near the call to action."},
])


# A page that answers 200 but says, in its title or main headline, that the page is gone. Narrow on purpose: it is a warning on what the page itself says.
SOFT_404 = re.compile(r"\b404\b|\bpage (?:was |is )?not found\b|^\s*not found\s*(?:$|[|:!.\-\u2013\u2014])|\bno longer (?:available|exists?)\b|\b(?:does not|doesn['\u2019]?t) exist\b|"
                      r"\bcan['\u2019]?t (?:find|be found)\b|\bpage (?:is )?unavailable\b", re.I)


# ── What a check is given ───────────────────────────────────────────────────

class Ctx:
    """A page and what was read of it: `page` is the record the report assembles (spend, devices, speed, the fetch record, the ad groups, the lab test)."""

    def __init__(self, page):
        self.page = page
        self.fetch = page.get("fetch") or None
        self.facts = (self.fetch or {}).get("facts")
        self.tech = (self.fetch or {}).get("tech") or {}
        self.groups = page.get("groups") or []

    @property
    def platforms(self):
        """The platforms whose ads send people to this page: google, meta or both. A page record without the list is a Google page."""
        return set(self.page.get("platforms") or ["google"])

    def why_unread(self):
        """Why the page's HTML could not be judged, as a sentence fragment; '' when it was read."""
        f = self.fetch
        if not f or f.get("skipped"):
            return "the page was not read in time"
        if f.get("templated"):
            who = "Google" if self.platforms == {"google"} else "the ad platform"
            return "the ad's address has a placeholder in its host or path that %s fills in when someone clicks, so the page people reach is not known here" % who
        if f.get("blocked"):
            return "the page refuses automated requests (HTTP %d), so it could not be verified" % f["status"]
        if not f.get("status"):
            return "the page could not be reached (%s)" % (f.get("error") or "no answer")
        if f["status"] >= 400:
            return "the page answered HTTP %d" % f["status"]
        if not f.get("isHtml") or self.facts is None:
            return "the address did not return a web page"
        return ""

    @property
    def script_built(self):
        return bool(self.facts and self.facts.get("scriptBuilt"))

    def not_seen(self, check_id, what):
        """The finding for a check that found NOTHING of `what` on a page that was cut at the reading limit (it may be further down): could not check.
        None when the page was read in full."""
        if self.fetch and self.fetch.get("truncated"):
            return unknown(check_id, "%s was not found in the first %d KB of the page, which is longer, so it may be further down" % (what, MAX_BYTES // 1000))
        return None

    def page_unread(self):
        """Why a check that needs the page's own HTML and its forms and buttons cannot run: the page was not read, or it is built by scripts."""
        why = self.why_unread()
        if why:
            return why
        if self.script_built:
            return "the page is built by scripts, so its forms, buttons and words are not in the HTML a plain read sees"
        return ""


def _kb(n):
    return round(n / 1024)


def _ms(n):
    return "%.1f seconds" % (n / 1000.0) if n >= 1000 else "%d ms" % n


# ── The page opens ──────────────────────────────────────────────────────────

def check_status(c):
    f = c.fetch
    if not f or f.get("skipped"):
        return unknown("reach_status", "the page was not read in time")
    if f.get("templated"):
        return unknown("reach_status", c.why_unread())
    if f.get("blocked"):
        return unknown("reach_status", "the page refuses automated requests (HTTP %d); Google's crawler and real visitors are normally let in, so it is not called broken" % f["status"])
    status = f.get("status") or 0
    if 200 <= status < 400:
        gone = next((t for t in [(c.facts or {}).get("title") or ""] + list((c.facts or {}).get("h1") or [])[:1] if SOFT_404.search(t)), "")
        if gone:
            return make("reach_status", "warn", "The page answered with HTTP %d but its %s reads like an error page (\"%s\"), so the ad may be sending people to a page that no longer exists."
                        % (status, "title" if gone == (c.facts or {}).get("title") else "headline", gone[:90]), severity="high")
        return make("reach_status", "pass", "The page answered with HTTP %d in %s." % (status, _ms(f.get("ms") or 0)))
    if status >= 400:
        return make("reach_status", "fail", "The page answered HTTP %d, so the ads' clicks reach an error page." % status, caps="a landing page does not open")
    err = f.get("error") or ""
    if err == "the address does not resolve":
        return make("reach_status", "fail", "The address does not resolve to a website.", caps="a landing page does not open")
    if err == "timed out":
        return make("reach_status", "warn", "The page did not answer within %d seconds, twice. It may be slow or down." % TIMEOUT_S, severity="high")
    return unknown("reach_status", "the page could not be reached (%s)" % (err or "no answer"))


def check_redirect(c):
    f = c.fetch
    why = c.why_unread()
    if why:
        return unknown("reach_redirect", why)
    if not f.get("redirected"):
        return make("reach_redirect", "pass", "The ad's address opens the page directly.")
    where = f.get("finalUrl") or ""
    if f.get("collapsed"):
        return make("reach_redirect", "fail", "The ad's address redirects to the home page (%s), so the ad's promise lands on a general page." % where, severity="high")
    return make("reach_redirect", "warn", "The ad's address redirects to %s, which adds a wait before the page starts to load." % where)


def check_params(c):
    f = c.fetch
    why = c.why_unread()
    if why:
        return unknown("reach_params", why)
    if not f.get("redirected"):
        return not_applicable("reach_params", "The address does not redirect, so nothing is lost on the way.")
    s = f.get("paramsSurvive")
    if s is True:
        return make("reach_params", "pass", "A test click id and UTM tag were still on the address after the redirect.")
    if s is False:
        return make("reach_params", "fail", "A test click id (gclid) and UTM tag were dropped by the redirect, so conversions from this page cannot be tied to the click.")
    return unknown("reach_params", "the second request that tests the click id did not complete")


def check_params_meta(c):
    f = c.fetch
    why = c.why_unread()
    if why:
        return unknown("reach_params_meta", why)
    if not f.get("redirected"):
        return not_applicable("reach_params_meta", "The address does not redirect, so nothing is lost on the way.")
    kept = f.get("keptIds")
    if not kept:
        return unknown("reach_params_meta", "the second request that tests the click id did not complete")
    if kept.get("fbclid") and kept.get("utm"):
        return make("reach_params_meta", "pass", "A test click id (fbclid) and UTM tag were still on the address after the redirect.")
    lost = " and ".join(x for x, ok in (("the click id (fbclid)", kept.get("fbclid")), ("the UTM tag", kept.get("utm"))) if not ok)
    return make("reach_params_meta", "fail", "A test click carrying a Meta click id (fbclid) and a UTM tag lost %s in the redirect, so conversions from this page cannot be tied to the ad click." % lost)


def check_https(c):
    f = c.fetch
    why = c.why_unread()
    if why:
        return unknown("reach_https", why)
    if (c.tech or {}).get("https"):
        if (f.get("url") or "").lower().startswith("http://"):
            return make("reach_https", "warn", "The ad's address uses plain http (%s). The site sends every click on to https, but each visitor waits for that redirect first." % f["url"])
        return make("reach_https", "pass", "The page is served over https.")
    return make("reach_https", "fail", "The page is served over plain http: %s" % (f.get("finalUrl") or f.get("url")))


_PRIVATE_END = (".localhost", ".local", ".test", ".invalid", ".internal")
_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_SURE = re.compile(r"(?:^|-)(?:staging|uat|sandbox)(?:-|\d*$)")      # words that only ever mean a test copy, in any part of a name
_UNSURE = re.compile(r"^(?:dev|test|qa|preview|stage)\d*$")         # words a real business name can hold ("test-prep.com"), so only a whole subdomain counts


def is_test_site(host):
    """True when a host name is a test or staging copy of a site: a private or numeric address, a subdomain that is dev, test, qa, preview or stage ("dev.acme.com"), or a name
    with staging, uat or sandbox in it ("acme-staging.com"). A business whose own name holds one of those words ("test-prep.com", "qa-solutions.com") is not one."""
    host = (host or "").lower().strip(".")
    if not host:
        return False
    if host == "localhost" or host.endswith(_PRIVATE_END) or _IPV4.match(host):
        return True
    reg = registrable(host)
    brand = reg.split(".")[0]
    subs = host[:-len(reg)].strip(".").split(".") if host != reg and host.endswith(reg) else []
    return any(_UNSURE.match(x) or _SURE.search(x) for x in subs) or bool(_SURE.search(brand))


def check_staging(c):
    f = c.fetch
    from urllib.parse import urlsplit
    hosts = [urlsplit(c.page["url"]).hostname or ""]
    if f and f.get("finalUrl"):
        hosts.append(urlsplit(f["finalUrl"]).hostname or "")
    hit = next((h for h in hosts if is_test_site(h)), "")
    if hit:
        return make("reach_staging", "fail", "The address %s looks like a test or staging site." % hit, caps="the ad points at a test site")
    return make("reach_staging", "pass", "The address does not look like a test or staging site.")


def check_adsbot(c):
    f = c.fetch
    if not f or f.get("skipped") or not f.get("status"):
        return unknown("reach_adsbot", "the site's robots.txt was not read")
    a = f.get("adsbot")
    if a is True:
        return make("reach_adsbot", "pass", "robots.txt does not keep Google's ad crawler (AdsBot-Google) out of this page.")
    if a is False:
        return make("reach_adsbot", "fail", "robots.txt has a rule that keeps AdsBot-Google out of this page.", caps="Google's ad crawler is blocked from the page")
    return unknown("reach_adsbot", "the site's robots.txt could not be read")


# ── Can a visitor act ───────────────────────────────────────────────────────

def _forms(c):
    """The forms that count: not a search box, and with a field to fill in."""
    return [f for f in c.facts["forms"] if not f["search"] and f["visible"] >= 1]


def _ways(c):
    """What the page offers to act with, by name."""
    f = c.facts
    ways = []
    if _forms(c):
        ways.append("a form")
    if f["embeds"]:
        ways.append("an embedded form or booking widget")
    if f["booking"]:
        ways.append("an online booking tool")
    if f["tel"]:
        ways.append("a phone link")
    if f["commerce"]:
        ways.append("a buy button")
    if any(t["category"] == "chat" for t in c.tech.get("tools", [])):
        ways.append("a live chat widget")
    return ways


def _script_forms(c):
    """The tools the page loads whose forms are added by script after load, so a plain read does not see them."""
    return [t["name"] for t in c.tech.get("tools", []) if t["category"] == "CRM and forms"]


def check_action(c):
    why = c.page_unread()
    if why:
        return unknown("path_action", why)
    f = c.facts
    ways = _ways(c)
    if ways:
        return make("path_action", "pass", "The page offers %s." % ", ".join(ways))
    weak = []
    if f["phones"]:
        weak.append("a phone number shown as text")
    if f["mailto"] or f["emails"]:
        weak.append("an email address")
    strong = [x for x in f["ctas"] if x["kind"] == "strong"]
    cut = c.not_seen("path_action", "a form, phone link, booking tool or buy button")
    if cut:
        return cut
    if strong:
        weak.append("%s that %s" % (plural(len(strong), "button or link"), "asks" if len(strong) == 1 else "ask"))
    later = _script_forms(c)
    if later:
        weak.append("%s, whose forms are added by script after the page loads and are not seen here" % later[0])
    if weak:
        return make("path_action", "warn", "The page has no form, phone link, booking tool or buy button in its HTML, only %s." % " and ".join(weak), severity="high")
    return make("path_action", "fail", "No form, phone link, booking tool, buy button or call to action was found on the page.", caps="the page gives no way to act")


def check_cta_top(c):
    why = c.page_unread()
    if why:
        return unknown("path_cta_top", why)
    strong = [x for x in c.facts["ctas"] if x["kind"] == "strong"]
    if not strong:
        cut = c.not_seen("path_cta_top", "a call to action")
        if cut:
            return cut
        if _forms(c) or c.facts["embeds"]:
            return make("path_cta_top", "warn", "No button or link asks the visitor to act; the form is the only prompt.", severity="medium")
        return make("path_cta_top", "fail", "No call to action (such as Book now, Call now or Get a quote) was found on the page.")
    first = min(strong, key=lambda x: x["at"])
    if first["at"] <= ATF_CHARS:
        return make("path_cta_top", "pass", "\"%s\" is within the first screen of the page." % first["text"])
    return make("path_cta_top", "warn", "The first call to action (\"%s\") comes after about %d words of text, below the first screen." % (first["text"], first["at"] // 6))


def check_form_length(c):
    why = c.page_unread()
    if why:
        return unknown("path_form_length", why)
    forms = _forms(c)
    if not forms:
        if c.facts["embeds"]:
            return unknown("path_form_length", "the form is an embedded widget (%s) that a plain read cannot look inside" % c.facts["embeds"][0])
        return not_applicable("path_form_length", "There is no form on the page.")
    main = max(forms, key=lambda f: f["visible"])
    n = main["visible"]
    names = [x["label"] or x["name"] or x["kind"] for x in main["fields"] if x["kind"] != "checkbox"]
    if n <= FORM_OK:
        return make("path_form_length", "pass", "The main form asks for %s." % plural(n, "field"), items=names)
    if n <= FORM_LONG:
        return make("path_form_length", "warn", "The main form asks for %d fields (%d of them required)." % (n, main["required"]), items=names)
    return make("path_form_length", "fail", "The main form asks for %d fields (%d of them required)." % (n, main["required"]), items=names)


def check_focus(c):
    why = c.page_unread()
    if why:
        return unknown("path_focus", why)
    total = c.facts["links"]["total"]
    nav = c.facts["links"]["nav"]
    if total <= LINKS_OK:
        return make("path_focus", "pass", "The page has %s (%d in the header or menu)." % (plural(total, "link"), nav))
    if total <= LINKS_MANY:
        return make("path_focus", "warn", "The page has %d links (%d in the header or menu), each a way to leave without acting." % (total, nav))
    return make("path_focus", "fail", "The page has %d links (%d in the header or menu); this is a whole website's menu, not a landing page." % (total, nav))


# ── Works on a phone ────────────────────────────────────────────────────────

def check_viewport(c):
    why = c.why_unread()
    if why:
        return unknown("mobile_viewport", why)
    if (c.tech or {}).get("mobile"):
        return make("mobile_viewport", "pass", "The page declares a mobile viewport (width=device-width).")
    vp = c.facts.get("viewport") or ""
    return make("mobile_viewport", "fail", ("The page's viewport tag is \"%s\", not width=device-width." % vp[:80]) if vp else "The page has no viewport tag, so phones show it as a shrunken desktop page.")


def check_zoom(c):
    why = c.why_unread()
    if why:
        return unknown("mobile_zoom", why)
    vp = (c.facts.get("viewport") or "").lower().replace(" ", "")
    if not vp:
        return not_applicable("mobile_zoom", "The page has no viewport tag.")
    if "user-scalable=no" in vp or "user-scalable=0" in vp or re.search(r"maximum-scale=1(\.0)?(?![\d.])", vp):
        return make("mobile_zoom", "warn", "The viewport tag blocks pinch zoom (%s)." % c.facts["viewport"][:80])
    return make("mobile_zoom", "pass", "The viewport tag lets visitors zoom.")


def check_tap_to_call(c):
    why = c.page_unread()
    if why:
        return unknown("mobile_tap_to_call", why)
    f = c.facts
    if f["tel"]:
        return make("mobile_tap_to_call", "pass", "The page has a tappable phone link (%s)." % f["tel"][0])
    if f["phones"]:
        cut = c.not_seen("mobile_tap_to_call", "a phone link")
        if cut:
            return cut
        return make("mobile_tap_to_call", "warn", "The page shows %s as text but has no tap-to-call link." % ("the number " + f["phones"][0] if len(f["phones"]) == 1 else "%d phone numbers" % len(f["phones"])),
                    items=f["phones"])
    return not_applicable("mobile_tap_to_call", "No phone number was found on the page.")


def check_conv_gap(c):
    d = c.page.get("devices") or {}
    mob, desk = d.get("mobile") or {}, d.get("desktop") or {}
    total = sum(x.get("clicks", 0) for x in d.values())
    if mob.get("clicks", 0) < GAP_MIN_CLICKS or desk.get("clicks", 0) < GAP_MIN_CLICKS:
        return not_applicable("mobile_conv_gap", "Too few clicks on both phones and desktops (at least %d each) to compare them." % GAP_MIN_CLICKS)
    if m.share(mob["clicks"], total) < GAP_MIN_MOBILE_SHARE:
        return not_applicable("mobile_conv_gap", "Under %d%% of this page's clicks are from phones, so the gap would not matter." % GAP_MIN_MOBILE_SHARE)
    rm, rd = mob["conversions"] / mob["clicks"], desk["conversions"] / desk["clicks"]
    if rd <= 0:
        return not_applicable("mobile_conv_gap", "Desktop visitors did not convert on this page, so there is nothing to compare phones with.")
    ratio = round(rm / rd, 4)
    said = "Phones convert at %.1f%% of clicks and desktops at %.1f%% (%d%% of desktop's rate)." % (100 * rm, 100 * rd, round(100 * ratio))
    if ratio >= GAP_OK:
        return make("mobile_conv_gap", "pass", said)
    if ratio >= GAP_WIDE:
        return make("mobile_conv_gap", "warn", said)
    return make("mobile_conv_gap", "fail", said)


# ── Loads fast ──────────────────────────────────────────────────────────────

def check_google_speed(c):
    s = c.page.get("speed")
    if s is None:
        return unknown("speed_google", "Google gave no mobile speed score for this page (it shows one only for pages with enough mobile traffic)")
    said = "Google's mobile speed score for the page is %d out of 10." % s
    if s >= SPEED_GOOD:
        return make("speed_google", "pass", said)
    if s >= SPEED_FAIR:
        return make("speed_google", "warn", said)
    return make("speed_google", "fail", said)


def check_server(c):
    why = c.why_unread()
    if why:
        return unknown("speed_server", why)
    ms = c.fetch.get("responseMs") or 0
    said = "The server started to answer after %s (measured from our servers; connecting and any redirect are in it)." % _ms(ms)
    if ms <= SERVER_OK_MS:
        return make("speed_server", "pass", said)
    if ms <= SERVER_SLOW_MS:
        return make("speed_server", "warn", said)
    return make("speed_server", "fail", said)


def check_html(c):
    why = c.why_unread()
    if why:
        return unknown("speed_html", why)
    f = c.fetch
    kb = _kb(f.get("bytes") or 0)
    if f.get("truncated"):
        return make("speed_html", "fail", "The page's HTML is more than %d KB (reading stopped there)." % _kb(f["bytes"]))
    said = "The page's HTML is %d KB." % kb
    if kb <= HTML_OK_KB:
        return make("speed_html", "pass", said)
    if kb <= HTML_HEAVY_KB:
        return make("speed_html", "warn", said)
    return make("speed_html", "fail", said)


def check_compression(c):
    why = c.why_unread()
    if why:
        return unknown("speed_compression", why)
    f = c.fetch
    enc = (f.get("encoding") or "").lower()
    if enc in COMPRESSED:
        return make("speed_compression", "pass", "The page is sent compressed (%s)." % enc)
    if (f.get("bytes") or 0) < TINY_BYTES:
        return not_applicable("speed_compression", "The page is under %d KB, so compression would save little." % _kb(TINY_BYTES))
    return make("speed_compression", "fail", "The page's %d KB of HTML was sent without compression, though gzip was accepted." % _kb(f["bytes"]))


def check_blocking(c):
    why = c.why_unread()
    if why:
        return unknown("speed_blocking", why)
    n = c.facts["head"]["blockingScripts"]
    css = c.facts["head"]["stylesheets"]
    said = "%s in the head %s up the first paint, and %s %s there." % (plural(n, "script"), m.verb(n, "hold"), plural(css, "stylesheet"), m.verb(css, "load"))
    if n <= BLOCKING_OK:
        return make("speed_blocking", "pass", said)
    if n <= BLOCKING_MANY:
        return make("speed_blocking", "warn", said)
    return make("speed_blocking", "fail", said)


def check_scripts(c):
    why = c.why_unread()
    if why:
        return unknown("speed_scripts", why)
    h = c.facts["head"]
    n, third = h["scripts"], len(h["thirdParty"])
    said = "The page loads %s, %d from other sites." % (plural(n, "script"), third)
    if n <= SCRIPTS_OK:
        return make("speed_scripts", "pass", said)
    if n <= SCRIPTS_MANY:
        return make("speed_scripts", "warn", said, items=h["thirdParty"])
    return make("speed_scripts", "fail", said, items=h["thirdParty"])


def check_images(c):
    why = c.why_unread()
    if why:
        return unknown("speed_images", why)
    im = c.facts["images"]
    n = im["count"]
    if not n:
        return not_applicable("speed_images", "The page has no images in its HTML.")
    problems = []
    if im["noSize"] >= 5 and m.share(im["noSize"], n) >= 50:
        problems.append("%d of %d images have no width and height set, so the page shifts as they load" % (im["noSize"], n))
    if n > 40 and m.share(im["lazy"], n) < 30:
        problems.append("only %d of %d images load lazily" % (im["lazy"], n))
    if problems:
        said = "; ".join(problems)
        return make("speed_images", "warn", said[0].upper() + said[1:] + ".")
    return make("speed_images", "pass", "The page has %s; %d without a set size, %d lazy-loaded." % (plural(n, "image"), im["noSize"], im["lazy"]))


def check_lab(c):
    lab = c.page.get("lab")
    if not lab or lab.get("score") is None:
        return not_applicable("speed_lab", "The lab speed test has not been run for this page.")
    s = lab["score"]
    bits = ["performance score %d out of 100" % s]
    for key, label in (("lcpMs", "largest content shown after"), ("tbtMs", "blocked for"), ("cls", "layout shift")):
        if lab.get(key) is not None:
            bits.append("%s %s" % (label, ("%.2f" % lab[key]) if key == "cls" else _ms(lab[key])))
    said = "The lab test on a mobile profile: %s." % ", ".join(bits)
    if s >= LAB_GOOD:
        return make("speed_lab", "pass", said)
    if s >= LAB_FAIR:
        return make("speed_lab", "warn", said)
    return make("speed_lab", "fail", said)


# ── Tracking and trust ──────────────────────────────────────────────────────

def check_tracking(c):
    why = c.why_unread()
    if why:
        return unknown("trust_tracking", why)
    names = [t["name"] for t in c.tech.get("tools", []) if t["name"] in GOOGLE_TAGS]
    if names:
        return make("trust_tracking", "pass", "The page loads %s." % " and ".join(names))
    cut = c.not_seen("trust_tracking", "a Google tag or Tag Manager")
    if cut:
        return cut
    later = " A cookie banner is on the page, so the tag may load only after a choice is made." if c.facts.get("consent") else ""
    return make("trust_tracking", "warn", "No Google tag or Tag Manager was seen in the page's HTML." + later + " A tag that loads later is not seen by a plain read.")


def check_meta_pixel(c):
    why = c.why_unread()
    if why:
        return unknown("trust_meta_pixel", why)
    pixel = next((t for t in c.tech.get("tools", []) if t["name"] == "Meta Pixel"), None)
    if pixel:
        ids = pixel.get("ids") or []
        return make("trust_meta_pixel", "pass", "The page loads the Meta pixel%s." % ((" (%s)" % ", ".join(str(i) for i in ids[:3])) if ids else ""))
    cut = c.not_seen("trust_meta_pixel", "the Meta pixel")
    if cut:
        return cut
    gtm = any(t["name"] == "Google Tag Manager" for t in c.tech.get("tools", []))
    later = " A cookie banner is on the page, so the pixel may load only after a choice is made." if c.facts.get("consent") else ""
    via = " Google Tag Manager is on the page and may load the pixel, which a plain read does not see." if gtm else ""
    return make("trust_meta_pixel", "warn", "The Meta pixel was not seen in the page's HTML." + via + later + " A pixel that loads later is not seen by a plain read, and the Conversions API cannot be seen from a page at all.")


def check_privacy(c):
    why = c.page_unread()
    if why:
        return unknown("trust_privacy", why)
    if c.facts["privacyLink"]:
        return make("trust_privacy", "pass", "The page links to a privacy policy.")
    cut = c.not_seen("trust_privacy", "a link to a privacy policy")
    if cut:
        return cut
    if _forms(c) or c.facts["embeds"]:
        return make("trust_privacy", "fail", "The page collects details in a form but has no link to a privacy policy.")
    return make("trust_privacy", "warn", "The page has no link to a privacy policy.", severity="low")


def check_contact(c):
    why = c.page_unread()
    if why:
        return unknown("trust_contact", why)
    f = c.facts
    have = []
    if f["tel"] or f["phones"]:
        have.append("a phone number")
    if f["address"]:
        have.append("an address")
    if f["emails"] or f["mailto"]:
        have.append("an email address")
    if have:
        return make("trust_contact", "pass", "The page shows %s." % ", ".join(have))
    cut = c.not_seen("trust_contact", "a phone number, address or email address")
    if cut:
        return cut
    return make("trust_contact", "fail", "No phone number, address or email address was found on the page.")


def check_proof(c):
    why = c.page_unread()
    if why:
        return unknown("trust_proof", why)
    p = c.facts["proof"]
    if p:
        return make("trust_proof", "pass", "The page shows proof: %s." % ", ".join(p))
    cut = c.not_seen("trust_proof", "reviews, ratings, awards or credentials")
    if cut:
        return cut
    return make("trust_proof", "warn", "No reviews, ratings, awards, credentials or years in business were found on the page.")


CHECKS = [
    ("reach_status", check_status), ("reach_redirect", check_redirect), ("reach_params", check_params), ("reach_params_meta", check_params_meta), ("reach_https", check_https),
    ("reach_staging", check_staging), ("reach_adsbot", check_adsbot),
    ("path_action", check_action), ("path_cta_top", check_cta_top), ("path_form_length", check_form_length), ("path_focus", check_focus),
    ("mobile_viewport", check_viewport), ("mobile_zoom", check_zoom), ("mobile_tap_to_call", check_tap_to_call), ("mobile_conv_gap", check_conv_gap),
    ("speed_google", check_google_speed), ("speed_server", check_server), ("speed_html", check_html), ("speed_compression", check_compression),
    ("speed_blocking", check_blocking), ("speed_scripts", check_scripts), ("speed_images", check_images), ("speed_lab", check_lab),
    ("trust_tracking", check_tracking), ("trust_meta_pixel", check_meta_pixel), ("trust_privacy", check_privacy), ("trust_contact", check_contact), ("trust_proof", check_proof),
]
