"""Paid Marketing Agents: Competitor Analysis, the arithmetic (no network, no clock but the one passed in).

What goes in: our own ads' text (the Ad Copy Checker's read) and each competitor's ads as the Meta Ad Library and Google's Ads Transparency Center show them, already turned into one shape by
the normalisers below (`meta_ad`, `google_ad`, `own_ad`). What comes out is what the sheet asks for: how the competitors' ads differ from ours, and ideas to improve ours.

An ad here is {id, platform, who, format, headlines, bodies, cta, url, firstShown, lastShown, days, active, copies}. The messages in it are found by plain rules (THEMES: a price, "free",
urgency, proof, trust, a guarantee, convenience, insurance, local, a question, something new, a figure, a comparison), so every number can be traced to the words that made it.

Rules it keeps:
  * the comparison is by LINE of copy (a headline or a description, each counted once), not by ad: one responsive search ad holds up to fifteen headlines and a competitor's creative one, so by ad
    ours would always look richer;
  * a theme is a gap only when the competitors' ads use it clearly more than ours (GAP_POINTS), in at least MIN_ADS_WITH of their ads, in two competitors when two were read, from at least
    MIN_POOL lines of theirs, and against at least MIN_OWN lines of ours. Short of any of these it says what it could not judge instead of a finding;
  * identical ads of one competitor (Meta repeats an ad per placement) are counted once;
  * how long an ad has run is the best public sign that it works, so the longest runners are listed to study;
  * what could not be read (a competitor with no Facebook page, a read that failed, the numbers Meta and Google do not publish) is listed, never counted as nothing.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from . import paid_domains as dom

MIN_POOL = 8                      # lines of the competitors' copy needed to judge a message
MIN_OWN = 6                       # lines of ours
MIN_ADS_WITH = 3
GAP_POINTS = 25
HIGH_POINTS = 45
EDGE_POINTS = 25
NEW_DAYS = 30
STALE_DAYS = 14                   # a read older than this is said to be old
TOP_N = 5
MAX_LINE = 300
MAX_MEDIA = 6                     # pictures and videos kept per ad (a dynamic ad can carry dozens of variants); the ad says how many there were
MAX_COPY = 900                    # the full text of an ad, kept for the gallery

THEMES = {
    "price": {"label": "A price or a saving", "rx": r"\$\s?\d|\d+\s?%\s?off|\bsave\b|\bsavings?\b|\bdiscounts?\b|starting (at|from)|\baffordable\b|\blow[- ]cost\b|\bprices?\b|\bpricing\b|\bcheap",
              "idea": "Put the price or the saving in a headline, or the first line of the primary text, and test it against the message you lead with now."},
    "free": {"label": "Free or no cost", "rx": r"(?<!-)\bfree\b|\$\s?0\b|\bno cost\b|\b0 out of pocket\b|\bcomplimentary\b|\bno charge\b",
             "idea": "If you offer a free consultation, check or first visit, say it in the first line of the ad, not on the landing page."},
    "urgency": {"label": "A reason to act now", "rx": r"\btoday\b|\bnow\b|limited time|\bends? (soon|\w+ \d+)|\bhurry\b|last chance|don'?t miss|this week|while (supplies|it lasts)|\bact fast\b",
                "idea": "Give one ad per group a reason to act now (an end date, a limited number of spots) and compare it with the same ad without."},
    "proof": {"label": "Proof: ratings, awards, numbers of customers", "rx": r"#\s?1\b|\bbest\b|top[- ]rated|\b\d(\.\d)?\s?(stars?|/\s?5)\b|\breviews?\b|\btrusted\b|\baward|\btestimonial|\b\d[\d,]*\+?\s(patients|customers|users|people|families|clients)\b|\brated\b",
              "idea": "Add proof to the ad: a rating, a count of patients or customers, an award. Use the real figure from your own reviews."},
    "trust": {"label": "Credentials and approvals", "rx": r"\bFDA\b|\bcertified\b|\baccredited\b|\blicensed\b|\bclinically\b|\bdoctors?\b|\bphysicians?\b|\bexperts?\b|\byears of experience\b|\bsince \d{4}\b|\bHIPAA\b|\bspecialists?\b",
              "idea": "Say who stands behind it: credentials, certifications, approvals, years in practice."},
    "guarantee": {"label": "A guarantee or no risk", "rx": r"guarantee|risk[- ]free|money[- ]back|no obligation|cancel anytime|\bwarranty\b|\bno commitment\b",
                  "idea": "State your guarantee, trial or cancellation terms in the ad. It removes the reason not to click."},
    "convenience": {"label": "Speed and convenience", "rx": r"same[- ]day|\beasy\b|\bsimple\b|\bfast\b|\bquick(ly)?\b|\bno wait\b|\bdelivered\b|to your door|\bonline\b|book (online|now|today)|\bschedule\b|24/7|walk-?ins?\b|open (late|weekends|sundays)|\bat home\b",
                    "idea": "Say how easy it is: how fast, how to book, whether it comes to the customer. Lead with the step that is easier than the competitor's."},
    "insurance": {"label": "Insurance, coverage or financing", "rx": r"\binsurance\b|\bmedicare\b|\bmedicaid\b|\bcovered\b|\bcoverage\b|\bfinancing\b|payment plans?|0%\s?(apr|interest)|\bcopay\b|\bbenefits?\b",
                  "idea": "Say what coverage or financing you accept, in the ad. People who cannot tell whether they can afford you do not click."},
    "local": {"label": "Local: near you, your area", "rx": r"near (you|me)|\blocal\b|\bnearby\b|in your area|\b[A-Z][a-z]+, [A-Z]{2}\b|your (neighborhood|community|city)",
              "idea": "Name the area, or say 'near you', in a headline. Local intent clicks on the ad that names its place."},
    "question": {"label": "Opens with a question", "rx": r"\?", "idea": "Open one ad with the question your customer asks before they search, and answer it in the next line."},
    "newness": {"label": "Something new", "rx": r"\bnew\b|\bintroducing\b|\blatest\b|now available|just launched|next[- ]generation|\binnovative\b|\bnewest\b",
                "idea": "If you have something new (a service, a model, an offer), lead with it for a few weeks and say it is new."},
    "numbers": {"label": "A figure: years, percent, count", "rx": r"\b\d[\d,.]*\+?\s?(%|x\b|years?|days?|minutes?|mins?|hours?|hrs?)", "idea": "Use one specific figure in a headline (a number of years, a percent, a time). A figure reads as a fact."},
    "comparison": {"label": "Compared with others", "rx": r"\bvs\.?\b|compared (to|with)|switch(ing)? (from|to)|\balternative\b|why choose|\bunlike\b|\bbetter than\b",
                   "idea": "Say how you differ from the alternative the customer is weighing, in a line of its own."},
}
_RX = {k: re.compile(v["rx"], re.I) for k, v in THEMES.items()}
_RX["local"] = re.compile(THEMES["local"]["rx"])                    # the town-and-state pattern needs capitals; the other words are lower case in practice
_LOCAL_LOWER = re.compile(r"near (you|me)|\blocal\b|\bnearby\b|in your area|your (neighborhood|community|city)", re.I)
_OFFER = re.compile(r"(\$\s?\d[\d,.]*\s?(off|per|a|/)?[^.!?\n]{0,40}|\d+\s?%\s?off[^.!?\n]{0,40}|\bfree\b[^.!?\n]{0,40}|\$\s?0\b[^.!?\n]{0,40}|\b0%\s?(apr|interest)[^.!?\n]{0,30})", re.I)

NOT_MEASURED = [
    "How the competitors' ads perform. Clicks, conversions and spend are not public; only how long an ad has run is, and a long run is the best sign that it works.",
    "How fresh our own Google ads are: Google gives no start day for an ad, so only our Meta ads' start days are compared.",
    "What is in the pictures and videos: the rules count formats, buttons and placements. Only the written ideas look at their pictures, and those are a model's reading.",
    "Competitors' ads that have stopped. Meta's Ad Library lists the ads running now, and Google's Transparency Center the ads it still shows.",
    "Which searches a competitor's Google ads answer. The Transparency Center names the advertiser and the creative, not the keywords.",
    "LinkedIn, TikTok and Microsoft ads of the competitors: only Google and Meta are read.",
]
OCR_NOTE = "Google shows a text ad as a picture, so its description is read from the picture and can have mistakes; the headline is exact."


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


# ── Dates ───────────────────────────────────────────────────────────────────

def to_date(v):
    """A day from an ISO time, a day, or a number of seconds since 1970; None when it is none of those."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v, timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            return None


def _day(d):
    return d.isoformat() if d else ""


def _days_between(a, b):
    return max(0, (b - a).days) if a and b else None


# ── Text ────────────────────────────────────────────────────────────────────

_SHORT_OK = {"a", "an", "to", "of", "in", "on", "up", "or", "is", "it", "we", "us", "no", "do", "at", "as", "by", "if", "my", "so", "go", "be", "he", "me", "i", "vs", "tv", "ai", "uk", "us", "pc"}


_OCR_LEAD = re.compile(r"^(\S{1,3}\s+)?(https?://)?(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}/\S*\s*", re.I)


def clean_ocr(text):
    """Text read from the picture of a Google text ad, tidied: the address the picture opens with, stray one- and two-letter fragments and the trailing dash are dropped. What is left is still
    approximate."""
    t = _OCR_LEAD.sub("", " ".join(str(text or "").split()), count=1)
    words = [w for w in t.split(" ") if not (len(w) <= 2 and w.isalpha() and w.lower() not in _SHORT_OK) and w not in ("\u2014", "-", "|")]
    return " ".join(words).strip(" -\u2014|")[:MAX_LINE]


def _first_paragraph(text):
    t = str(text or "").strip()
    return " ".join(re.split(r"\n\s*\n", t)[0].split())[:MAX_LINE] if t else ""


def _clean_line(text):
    t = " ".join(str(text or "").split())
    return "" if (not t or "{{" in t) else t[:MAX_LINE]


def norm(text):
    return re.sub(r"[^a-z0-9$%]+", " ", str(text or "").lower()).strip()


def _uniq(lines):
    seen, out = set(), []
    for l in lines:
        l = _clean_line(l)
        k = norm(l)
        if l and k not in seen:
            seen.add(k)
            out.append(l)
    return out


def themes_of(text):
    """The ids of the messages a line of copy carries."""
    out = set()
    for k, rx in _RX.items():
        if rx.search(text) or (k == "local" and _LOCAL_LOWER.search(text)):
            out.add(k)
    return out


def path_of(url):
    """The host and the first two steps of the path of a landing page, or ''."""
    host = dom.host_of(url)
    if not host:
        return ""
    t = str(url).strip()
    parts = [x for x in urlsplit(t if "://" in t else "http://" + t.lstrip("/")).path.split("/") if x][:2]
    return (host[4:] if host.startswith("www.") else host) + (("/" + "/".join(parts)) if parts else "")


# ── Turning what a platform gave into one shape ─────────────────────────────

FORMATS = {"IMAGE": "image", "VIDEO": "video", "CAROUSEL": "carousel", "DCO": "dynamic", "DPA": "catalog", "TEXT": "text", "MULTI_IMAGES": "carousel", "MULTI_VIDEOS": "carousel", "DAI": "dynamic"}


def https(url):
    """The address when it is an https one with no spaces, else '': a picture or a file a scraper hands over is never trusted further than that (it is drawn on our page)."""
    u = url.strip() if isinstance(url, str) else ""
    return u if u.startswith("https://") and " " not in u and len(u) <= 1500 else ""


def web(url):
    """A landing page address: http or https, else ''."""
    u = url.strip() if isinstance(url, str) else ""
    return u if (u[:8].lower() == "https://" or u[:7].lower() == "http://") and " " not in u and len(u) <= 1500 else ""


def _copy(text):
    """The full text of an ad, paragraph by paragraph with the whitespace tidied; '' for a catalog template."""
    t = str(text or "")
    if "{{" in t:
        return ""
    return "\n".join(p for p in (" ".join(x.split()) for x in re.split(r"\n\s*\n", t.strip())) if p)[:MAX_COPY]


def _meta_piece(kind, url, poster="", **text):
    out = {"kind": kind, "url": url}
    if poster:
        out["poster"] = poster
    out.update({k: v for k, v in text.items() if v})
    return out


def _meta_media(snap, cards):
    """The pictures and videos of a Meta ad, as [{kind, url, poster?, title?, body?, cta?, link?}] in the order the ad shows them: its videos, its pictures, then each card (a card is one
    variant of a carousel or a dynamic ad, with its own words and link). A picture is the 600 pixel one when there is one."""
    out = []
    for v in snap.get("videos") or []:
        if isinstance(v, dict):
            file, poster = https(v.get("videoHdUrl")) or https(v.get("videoSdUrl")), https(v.get("videoPreviewImageUrl"))
            if file or poster:
                out.append(_meta_piece("video", file or poster, poster if file else ""))
    for i in snap.get("images") or []:
        url = https(i.get("resizedImageUrl")) or https(i.get("originalImageUrl")) if isinstance(i, dict) else ""
        if url:
            out.append(_meta_piece("image", url))
    for c in cards:
        file, poster = https(c.get("videoHdUrl")) or https(c.get("videoSdUrl")), https(c.get("videoPreviewImageUrl"))
        pic = https(c.get("resizedImageUrl")) or https(c.get("originalImageUrl"))
        words = {"title": _clean_line(c.get("title")), "body": _first_paragraph(c.get("body")) if "{{" not in str(c.get("body") or "") else "", "cta": str(c.get("ctaText") or ""), "link": web(c.get("linkUrl"))}
        if file:
            out.append(_meta_piece("video", file, poster or pic, **words))
        elif pic or poster:
            out.append(_meta_piece("image", pic or poster, **words))
    return out


def meta_ad(item, *, asof, textless=False):
    """One ad of the Meta Ad Library as the shape above, or None for a row that has no ad text. `asof` is the day it was read. With `textless` an ad that is only a picture or a video is returned too
    (no lines of copy, its media kept): the gallery shows it, the analysis, which counts words, does not take it."""
    snap = item.get("snapshot") or {}
    cards = [c for c in snap.get("cards") or [] if isinstance(c, dict)]
    body = snap.get("body")
    body = body.get("text") if isinstance(body, dict) else body
    headlines = _uniq([snap.get("title")] + [c.get("title") for c in cards])
    bodies = _uniq([_first_paragraph(body)] + [_first_paragraph(c.get("body")) for c in cards] + [snap.get("linkDescription")] + [c.get("linkDescription") for c in cards])
    media = _meta_media(snap, cards)
    if not headlines and not bodies and not (textless and media):
        return None
    start, end = to_date(item.get("startDateFormatted") or item.get("startDate")), to_date(item.get("endDateFormatted") or item.get("endDate"))
    active = item.get("isActive")
    last = asof if active else (end or asof)
    cta = snap.get("ctaText") or (cards[0].get("ctaText") if cards else "") or ""
    ident = str(item.get("adArchiveID") or item.get("adArchiveId") or "")
    return {"id": "meta:" + ident, "platform": "meta", "who": snap.get("pageName") or item.get("pageName") or "", "format": FORMATS.get(str(snap.get("displayFormat") or "").upper(), str(snap.get("displayFormat") or "other").lower()),
            "headlines": headlines, "bodies": bodies, "cta": str(cta), "url": web(snap.get("linkUrl")) or (web(cards[0].get("linkUrl")) if cards else ""), "firstShown": _day(start), "lastShown": _day(last),
            "days": _days_between(start, last), "active": bool(active) if active is not None else None, "copies": max(1, int(item.get("collationCount") or 1)),
            "networks": [str(p).lower() for p in item.get("publisherPlatform") or []],
            "text": _copy(body), "caption": str(snap.get("caption") or "")[:120], "ctaType": str(snap.get("ctaType") or ""), "media": media[:MAX_MEDIA], "mediaCount": len(media),
            "adUrl": ("https://www.facebook.com/ads/library/?id=" + ident) if ident.isdigit() else ""}


def google_ad(item, *, asof, textless=False):
    """One creative of Google's Ads Transparency Center as the shape above, or None for a row with no text in any variation (with `textless`, one that has a picture is kept). The descriptions were
    read from the picture of the ad; the picture of each variation is kept, with the words read from it and the link it opens. A video creative's file is not given, only its picture, so
    its media stay pictures and the format says "video"."""
    heads, bodies, url, media = [], [], "", []
    for v in item.get("variations") or []:
        if not isinstance(v, dict):
            continue
        heads.append(v.get("headline"))
        desc = clean_ocr(v.get("description"))
        bodies.append(desc)
        url = url or web(v.get("clickUrl"))
        pic = https(v.get("imageUrl"))
        if pic:
            media.append({"kind": "image", "url": pic, "title": _clean_line(v.get("headline")), "body": desc, "link": web(v.get("clickUrl"))})
    if not media and https(item.get("previewUrl")):
        first = next((v for v in item.get("variations") or [] if isinstance(v, dict)), {})
        media.append({"kind": "image", "url": https(item.get("previewUrl")), "title": _clean_line(first.get("headline")), "body": clean_ocr(first.get("description")), "link": web(first.get("clickUrl"))})
    heads, bodies = _uniq(heads), _uniq(bodies)
    if not heads and not bodies and not (textless and media):
        return None
    first, last = to_date(item.get("firstShown")), to_date(item.get("lastShown"))
    served = item.get("numServedDays")
    lib = https(item.get("adLibraryUrl"))
    return {"id": "google:" + str(item.get("creativeId") or ""), "platform": "google", "who": item.get("advertiserName") or "", "format": FORMATS.get(str(item.get("format") or "").upper(), str(item.get("format") or "other").lower()),
            "headlines": heads, "bodies": bodies, "cta": "", "url": url, "firstShown": _day(first), "lastShown": _day(last), "days": int(served) if isinstance(served, (int, float)) else _days_between(first, last),
            "active": (_days_between(last, asof) or 0) <= 3 if last else None, "copies": 1, "networks": [], "creativeId": str(item.get("creativeId") or ""),
            "text": "", "caption": "", "ctaType": "", "media": media[:MAX_MEDIA], "mediaCount": len(media), "adUrl": lib if urlsplit(lib).hostname == "adstransparency.google.com" else ""}


_OWN_HEAD = {"headline", "longheadline", "title"}
_OWN_BODY = {"description", "primary", "link"}


def own_ad(ad):
    """One of our own ads (the Ad Copy Checker's read: {id, platform, kind, campaign, adGroup, url, texts: [{slot, text}]}) as the shape above, or None for an ad with no copy."""
    if ad.get("kind") == "assets":
        return None
    heads = _uniq([t["text"] for t in ad.get("texts") or [] if t.get("slot") in _OWN_HEAD])
    bodies = _uniq([_first_paragraph(t["text"]) for t in ad.get("texts") or [] if t.get("slot") in _OWN_BODY])
    if not heads and not bodies:
        return None
    return {"id": str(ad.get("id") or ""), "platform": ad.get("platform") or "google", "who": ad.get("campaign") or "", "format": "", "headlines": heads, "bodies": bodies, "cta": "", "url": ad.get("url") or "",
            "firstShown": "", "lastShown": "", "days": None, "active": True, "copies": 1, "networks": []}


def lines_of(ad):
    return list(ad["headlines"]) + list(ad["bodies"])


def dedupe(ads):
    """The same ad of one competitor shown once (Meta repeats an ad per placement and audience): the one that has run longest, with how many copies there were."""
    best = {}
    for a in ads:
        key = (a["who"].lower(), norm((a["headlines"] or [""])[0]), norm((a["bodies"] or [""])[0])[:100])
        cur = best.get(key)
        if cur is None:
            best[key] = dict(a)
        else:
            keep = a if (a.get("days") or 0) > (cur.get("days") or 0) else cur
            best[key] = dict(keep, copies=cur["copies"] + a["copies"])
    return list(best.values())


# ── Figures ─────────────────────────────────────────────────────────────────

def _median(xs):
    xs = sorted(xs)
    return None if not xs else (xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2)


def line_shares(ads):
    """{theme: {lines, share, ads, who}} over the distinct lines of copy of `ads`: how many lines carry it, their share of all lines, how many ads and which advertisers use it."""
    seen, total = {}, 0
    ad_hits, who_hits = {k: set() for k in THEMES}, {k: set() for k in THEMES}
    for a in ads:
        for line in lines_of(a):
            k = norm(line)
            if k in seen:
                continue
            seen[k] = themes_of(line)
            total += 1
        for line in lines_of(a):
            for t in themes_of(line):
                ad_hits[t].add(a["id"])
                who_hits[t].add(a["who"].lower())
    counts = {k: sum(1 for ts in seen.values() if k in ts) for k in THEMES}
    return {"lines": total, "themes": {k: {"lines": counts[k], "share": round(100.0 * counts[k] / total, 1) if total else 0.0, "ads": len(ad_hits[k]), "who": len(who_hits[k])} for k in THEMES}}


def offers_of(ads):
    """[{text, n, who, maxDays}]: the offers the competitors' copy states (a price, a percent off, something free), the commonest first."""
    found = {}
    for a in ads:
        for line in lines_of(a):
            for m in _OFFER.finditer(line):
                t = " ".join(m.group(0).split()).strip(" ,;:-*")
                if len(t) < 4:
                    continue
                e = found.setdefault(norm(t), {"text": t[:70], "n": 0, "who": set(), "maxDays": 0})
                e["n"] += 1
                e["who"].add(a["who"])
                e["maxDays"] = max(e["maxDays"], a.get("days") or 0)
    out = [dict(e, who=sorted(e["who"])) for e in found.values()]
    out.sort(key=lambda e: (-e["n"], -e["maxDays"], e["text"]))
    return out[:TOP_N]


def _brief(a):
    return {"id": a["id"], "who": a["who"], "headline": (a["headlines"] or [""])[0], "body": (a["bodies"] or [""])[0][:160], "format": a["format"], "cta": a["cta"], "days": a["days"], "firstShown": a["firstShown"],
            "url": a["url"], "copies": a["copies"]}


def competitor_stats(name, ads, *, fetched_at, now, error=None, skipped=None, limit=None, capped=False, partial="", items=0):
    read = len(ads)
    ads = dedupe(ads)
    day0 = now.date()
    cut = day0 - timedelta(days=NEW_DAYS)
    days = [a["days"] for a in ads if a.get("days") is not None]
    formats, ctas = {}, {}
    for a in ads:
        formats[a["format"] or "other"] = formats.get(a["format"] or "other", 0) + 1
        if a["cta"]:
            ctas[a["cta"]] = ctas.get(a["cta"], 0) + 1
    new = [a for a in ads if (to_date(a["firstShown"]) or date.min) >= cut]
    paths = {}
    for a in ads:
        p = path_of(a["url"])
        if p:
            paths[p] = paths.get(p, 0) + 1
    fetched = to_date(fetched_at)
    return {"name": name, "count": len(ads), "read": read, "noText": max(0, (items or 0) - read), "limit": limit, "capped": bool(capped), "partial": partial or "", "active": sum(1 for a in ads if a.get("active")), "new": len(new), "medianDays": _median(days), "maxDays": max(days) if days else None, "formats": formats,
            "ctas": dict(sorted(ctas.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]), "landing": [{"path": p, "ads": n} for p, n in sorted(paths.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]],
            "longest": [_brief(a) for a in sorted(ads, key=lambda a: -(a.get("days") or 0))[:3]], "newest": [_brief(a) for a in sorted(new, key=lambda a: a["firstShown"], reverse=True)[:3]],
            "fetchedAt": fetched_at or "", "ageDays": _days_between(fetched, day0) if fetched else None, "error": error or "", "skipped": skipped or ""}


# ── Comparing ───────────────────────────────────────────────────────────────

def compare(own_ads, comp_ads, *, n_competitors):
    """({gaps, edges, judged, why}, pool figures) for one platform. `comp_ads` are every competitor's ads (deduplicated here per competitor)."""
    pool = dedupe(comp_ads)
    theirs, ours = line_shares(pool), line_shares(own_ads)
    out = {"gaps": [], "edges": [], "judged": False, "why": "", "ownLines": ours["lines"], "theirLines": theirs["lines"]}
    if theirs["lines"] < MIN_POOL:
        out["why"] = "Only %s of the competitors' copy were read, too few to say what they have in common (at least %d are needed)." % (plural(theirs["lines"], "line"), MIN_POOL)
        return out, pool, theirs, ours
    if ours["lines"] < MIN_OWN:
        out["why"] = "Only %s of our own copy were read, too few to compare (at least %d are needed)." % (plural(ours["lines"], "line"), MIN_OWN)
        return out, pool, theirs, ours
    out["judged"] = True
    need_who = min(2, n_competitors)
    for k, th in THEMES.items():
        t, o = theirs["themes"][k], ours["themes"][k]
        diff = t["share"] - o["share"]
        if diff >= GAP_POINTS and t["ads"] >= MIN_ADS_WITH and t["who"] >= need_who:
            out["gaps"].append({"theme": k, "label": th["label"], "theirShare": t["share"], "ownShare": o["share"], "points": round(diff, 1), "ads": t["ads"], "who": t["who"], "level": "high" if diff >= HIGH_POINTS and t["share"] >= 50 else "medium"})
        elif -diff >= EDGE_POINTS:
            out["edges"].append({"theme": k, "label": th["label"], "theirShare": t["share"], "ownShare": o["share"], "points": round(-diff, 1)})
    out["gaps"].sort(key=lambda g: (-g["points"], g["theme"]))
    out["edges"].sort(key=lambda g: (-g["points"], g["theme"]))
    return out, pool, theirs, ours


def thin_reason(entries, platform, lines):
    """Why the competitors' copy on a platform was too thin to compare, competitor by competitor: no page or website to look up, not read yet, a failed read, ads with no words, or no ads at all."""
    where, what = ("Meta", "Facebook page") if platform == "meta" else ("Google", "website")
    groups = {"unset": [], "unread": [], "failed": [], "pictures": [], "empty": []}
    pics = 0
    for c in entries:
        if c.get("ads"):
            continue
        if c.get("error"):
            groups["failed"].append(c["name"])
        elif str(c.get("skipped") or "").startswith("Not read yet"):
            groups["unread"].append(c["name"])
        elif c.get("skipped"):
            groups["unset"].append(c["name"])
        elif c.get("pictures"):
            groups["pictures"].append(c["name"])
            pics += len(c["pictures"])
        else:
            groups["empty"].append(c["name"])
    names = lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]    # noqa: E731
    parts = []
    if groups["unset"]:
        parts.append("%s %s no %s set, so %s was not searched for %s" % (names(groups["unset"]), "has" if len(groups["unset"]) == 1 else "have", what, where, "it" if len(groups["unset"]) == 1 else "them"))
    if groups["unread"]:
        parts.append("%s %s not been read yet (press Refresh competitor ads)" % (names(groups["unread"]), "has" if len(groups["unread"]) == 1 else "have"))
    if groups["failed"]:
        parts.append("the %s read of %s failed" % (where, names(groups["failed"])))
    if groups["pictures"]:
        parts.append("the %s read of %s %s only pictures or videos with no words (their formats are compared below)" % (plural(pics, "ad"), names(groups["pictures"]), "is" if pics == 1 else "are"))
    if groups["empty"]:
        parts.append("%s %s no active ads on %s" % (names(groups["empty"]), "shows" if len(groups["empty"]) == 1 else "show", where))
    head = ("No line of the competitors' copy was read" if not lines else "Only %s of the competitors' copy were read" % plural(lines, "line")) + ", too few to say what they have in common (at least %d are needed)" % MIN_POOL
    return head + (": " + "; ".join(parts) + "." if parts else ".")


def example_for(theme, pool):
    """A competitor line that carries the theme: the one from the ad that has run longest."""
    best = None
    for a in sorted(pool, key=lambda a: -(a.get("days") or 0)):
        for line in lines_of(a):
            if theme in themes_of(line):
                best = {"line": line, "who": a["who"], "days": a["days"]}
                return best
    return best


def ideas_of(platforms):
    """[{id, level, title, why, tryThis, example, platform}] from the gaps, the offers and the longest runners, worst first."""
    out = []
    names = {"google": "Google Ads", "meta": "Meta Ads"}
    for p, r in platforms.items():
        for g in r["compare"]["gaps"]:
            ex = r["examples"].get(g["theme"])
            out.append({"id": "%s:%s" % (p, g["theme"]), "level": g["level"], "platform": p, "title": "%s: %s" % (names[p], g["label"].lower()),
                        "why": "%d%% of the competitors' lines say it (%s, %s) against %d%% of ours." % (round(g["theirShare"]), plural(g["ads"], "ad"), plural(g["who"], "competitor"), round(g["ownShare"])),
                        "tryThis": THEMES[g["theme"]]["idea"], "example": ex})
        if r["offers"]:
            top = r["offers"][0]
            out.append({"id": "%s:offers" % p, "level": "medium" if top["n"] >= 3 else "low", "platform": p, "title": "%s: the competitors' offers" % names[p],
                        "why": "They state %s; the commonest, \"%s\", is in %s." % (plural(len(r["offers"]), "offer"), top["text"], plural(top["n"], "line")),
                        "tryThis": "Decide whether you match, beat or avoid these offers, and say your answer in the first line of an ad: a visitor compares before they click.", "example": None})
        if r["winners"]:
            w = r["winners"][0]
            out.append({"id": "%s:winners" % p, "level": "medium" if (w["days"] or 0) >= 180 else "low", "platform": p, "title": "%s: the ads that have run longest" % names[p],
                        "why": "%s has run %s: nobody keeps paying for an ad that does not work." % (w["who"] or "A competitor", plural(w["days"] or 0, "day")),
                        "tryThis": "Read the longest runners below. Write your own version of the angle (not the words) and test it against your current best ad.", "example": {"line": w["headline"] or w["body"], "who": w["who"], "days": w["days"]}})
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda i: (order[i["level"]], i["id"]))
    return out


def analyse(own, competitors, *, now, region="US"):
    """The report. `own` is {platform: [own_ad...]} (a platform not asked for is absent); `competitors` is [{name, platform, ads: [...], fetchedAt, error, skipped}], one entry per competitor and platform.
    Returns {status, platforms, ideas, notMeasured, headline, counts, caveats}."""
    platforms, caveats = {}, []
    for p, own_ads in own.items():
        entries = [c for c in competitors if c["platform"] == p]
        stats = [competitor_stats(c["name"], c.get("ads") or [], fetched_at=c.get("fetchedAt"), now=now, error=c.get("error"), skipped=c.get("skipped"),
                                  limit=c.get("limit"), capped=c.get("capped"), partial=c.get("partial"), items=c.get("items")) for c in entries]
        with_data = [c for c in entries if c.get("ads")]
        cmp_, pool, theirs, ours = compare(own_ads, [a for c in with_data for a in c["ads"]], n_competitors=len(with_data))
        if not cmp_["judged"] and theirs["lines"] < MIN_POOL:
            cmp_["why"] = thin_reason(entries, p, theirs["lines"])
        platforms[p] = {"own": {"ads": len(own_ads), "lines": ours["lines"], "themes": {k: v["share"] for k, v in ours["themes"].items()}}, "competitors": stats, "compare": cmp_,
                        "pool": {"ads": len(pool), "lines": theirs["lines"], "themes": {k: dict(v) for k, v in theirs["themes"].items()}}, "offers": offers_of(pool),
                        "winners": [_brief(a) for a in sorted(pool, key=lambda a: -(a.get("days") or 0))[:TOP_N] if (a.get("days") or 0) > 0],
                        "examples": {g["theme"]: example_for(g["theme"], pool) for g in cmp_["gaps"]}}
        where = "Google" if p == "google" else "Meta"
        for s in stats:
            if s["error"]:
                caveats.append("%s on %s: %s" % (s["name"], where, s["error"]))
            elif s["skipped"]:
                caveats.append("%s on %s: %s" % (s["name"], where, s["skipped"]))
            else:
                if s["capped"]:
                    caveats.append("%s on %s: the limit of %d ads was reached, so they run more ads than were read. Their longest-running and newest ads may be missing." % (s["name"], where, s["limit"] or s["read"]))
                if s["partial"]:
                    caveats.append("%s on %s: %s" % (s["name"], where, s["partial"]))
                if s["ageDays"] is not None and s["ageDays"] > STALE_DAYS:
                    caveats.append("%s on %s was last read %s ago." % (s["name"], where, plural(s["ageDays"], "day")))
        if p == "google" and any(c.get("ads") for c in entries):
            caveats.append(OCR_NOTE)
        if not cmp_["judged"] and cmp_["why"]:
            caveats.append("%s: %s" % ("Google" if p == "google" else "Meta", cmp_["why"]))
    ideas = ideas_of(platforms)
    read = sum(1 for c in competitors if c.get("ads"))
    total_ads = sum(len(c.get("ads") or []) for c in competitors)
    status = "ok" if read else "no_competitor_data"
    gaps = sum(len(r["compare"]["gaps"]) for r in platforms.values())
    headline = ("%s read from %s; %s." % (plural(total_ads, "competitor ad"), plural(read, "competitor read", "competitor reads"), (plural(gaps, "message gap") + " against our ads") if gaps else "no clear message gap against our ads")
                ) if read else "No competitor ads have been read yet."
    return {"status": status, "region": region, "platforms": platforms, "ideas": ideas, "notMeasured": list(NOT_MEASURED), "caveats": list(dict.fromkeys(caveats)), "headline": headline,
            "counts": {"competitorAds": total_ads, "reads": read, "gaps": gaps, "ideas": len(ideas)}}
