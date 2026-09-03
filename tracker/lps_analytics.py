"""Derived intelligence for LinkedIn Strategy Researcher runs.

The vendor's analysis workflow returns up to 100 full raw posts per run
(`getcompanypost.items`) and then leaves its OWN engagement fields empty:
`contentcreativeagent.engagement.{activity,best,stats,worst}` came back as
empty arrays on every real production run this was built against (Google,
Myntra, Boat). So the single richest source of signal in the response was
being rendered as 24 post cards and otherwise thrown away, while the tab that
should have held posting cadence, engagement rates and format performance sat
empty.

This module closes that gap by computing all of it locally: cadence over time,
engagement distribution, which post format actually earns reactions, voice
mix, hashtag usage, hiring/poll/article signals, and a handful of
deterministic grounded observations. Every number here is arithmetic over
fields that are actually present in the response -- nothing is modelled,
estimated or inferred, so a section is either backed by real posts or absent.

Two design rules:

1. `augment()` is called on READ (see app.py's run route), not at save time.
   Recomputing per request is a few milliseconds of pure arithmetic over at
   most 100 posts, and it means every improvement to this file applies
   retroactively to runs that were already saved -- no migration, no re-run of
   a multi-minute vendor workflow to gain a new metric.
2. It never raises and never mutates its input. Any failure returns the
   original output untouched, so a bug in a derived metric can't take down a
   report whose vendor data is perfectly fine.
"""

from __future__ import annotations

import copy
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_POSTS_KEY = "getcompanypost.items"

# Vendor attachment `type` values seen in real responses. "file" is how a
# document/PDF carousel post arrives; there is no separate "document" type.
_MEDIA_IMAGE = "img"
_MEDIA_VIDEO = "video"
_MEDIA_FILE = "file"

_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,40})")
_URL_RE = re.compile(r"https?://", re.I)
_QUESTION_RE = re.compile(r"\?")
# Emoji-ish: the pictographic ranges that actually show up in LinkedIn copy.
# Only meaningful AFTER _repair_text below runs -- in the raw vendor response
# every emoji arrives mojibake-encoded, so this matches nothing until repaired.
_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff⬀-⯿]"
)

# ── Mojibake repair ──────────────────────────────────────────────────────────
# The vendor double-decodes text before sending it: UTF-8 bytes read back as
# Latin-1, so an apostrophe arrives as "a-hat-euro-TM" and every emoji as four
# mangled characters. This is not a rare edge case -- on the real production
# runs this was built against it affected 122 strings (Google) and 78 (Myntra)
# per run, including 97 of Google's 100 post bodies, the company description,
# hook examples, CTAs, persona rationales and launch titles. The report was
# faithfully displaying all of it verbatim.
#
# The repair is the standard round trip (encode Latin-1, decode UTF-8), applied
# only to strings that provably contain a mojibake sequence: a Latin-1 lead
# byte followed by a continuation byte. That guard matters -- a genuine name
# like "Angela" spelled with an A-circumflex has a lead byte but no
# continuation byte after it, and must be left alone.
#
# Regex character classes are built from chr() rather than written as literal
# bytes so this file stays plain ASCII and readable in any editor.
_MOJI_LEAD = "".join(chr(c) for c in (0xC2, 0xC3, 0xE2, 0xF0))
_MOJI_CONT = chr(0x80) + "-" + chr(0xBF)
_MOJI_SEQ_RE = re.compile("[" + _MOJI_LEAD + "][" + _MOJI_CONT + "]")
# A lead byte with no continuation after it can't be the start of anything: it
# is the surviving half of a C2 A0 (non-breaking space) whose tail the vendor
# already flattened to a plain space. Dropping it also unblocks any real
# sequence sitting next to it from decoding.
_MOJI_ORPHAN_RE = re.compile("[" + chr(0xC2) + chr(0xC3) + "](?![" + _MOJI_CONT + "])")
# Splits on ASCII whitespace only: \s+ would also match U+00A0 and orphan the
# lead byte of a non-breaking space from its own tail.
_ASCII_WS_RE = re.compile(r"([ \t\r\n]+)")


def _latin1_utf8(text: str) -> str | None:
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None


def _repair_text(text: str) -> str:
    """Undo one round of UTF-8-read-as-Latin-1 on a single string.

    Tries the whole string first, then falls back to repairing token by token,
    because one truncated multi-byte sequence (the vendor also truncates long
    posts mid-character) would otherwise block the entire string from
    decoding. Anything still unrepairable is returned as-is rather than
    replaced with question marks.
    """
    if not isinstance(text, str) or not _MOJI_SEQ_RE.search(text):
        return text
    cleaned = _MOJI_ORPHAN_RE.sub("", text)
    whole = _latin1_utf8(cleaned)
    if whole is not None:
        return whole
    parts = []
    for token in _ASCII_WS_RE.split(cleaned):
        if _MOJI_SEQ_RE.search(token):
            repaired = _latin1_utf8(token)
            if repaired is not None:
                token = repaired
        parts.append(token)
    return "".join(parts)


def repair_strings(value: Any) -> Any:
    """_repair_text applied to every string anywhere in a nested structure.

    Applied to the whole run output rather than just post bodies, since the
    same corruption shows up in agent prose, company descriptions, hook
    examples and launch titles. Dict keys are namespace identifiers and are
    never touched.
    """
    if isinstance(value, str):
        return _repair_text(value)
    if isinstance(value, dict):
        return {k: repair_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [repair_strings(v) for v in value]
    return value

_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ── small helpers ────────────────────────────────────────────────────────────

def _is_dict(v: Any) -> bool:
    return isinstance(v, dict)


def _num(v: Any) -> float:
    """A count field as a number. The vendor sends these as ints, but a
    missing/None/garbage value must read as 0 rather than raising."""
    try:
        if isinstance(v, bool):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _int(v: Any) -> int:
    return int(round(_num(v)))


def _round(v: float, places: int = 1) -> float:
    """Round for display. Returns an int-valued float as an int so the UI
    shows "3" rather than "3.0"."""
    r = round(float(v), places)
    return int(r) if r == int(r) else r


def _parse_dt(v: Any) -> datetime | None:
    """`parsed_datetime` is ISO-8601 with a trailing Z in every real response.
    Anything unparseable is dropped rather than defaulted to "now", which
    would silently invent activity on a date the company never posted."""
    if not isinstance(v, str) or not v.strip():
        return None
    text = v.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _pct(part: float, whole: float) -> float:
    return _round(part / whole * 100.0, 1) if whole else 0.0


def _clean_posts(output: dict) -> list[dict]:
    items = output.get(_POSTS_KEY)
    if not isinstance(items, list):
        return []
    return [p for p in items if _is_dict(p)]


def _post_text(post: dict) -> str:
    text = post.get("text")
    if isinstance(text, str) and text.strip():
        return text
    reposted = post.get("repost_content")
    if _is_dict(reposted) and isinstance(reposted.get("text"), str):
        return reposted["text"]
    return ""


def _attachment_types(post: dict) -> set[str]:
    out: set[str] = set()
    for a in post.get("attachments") or []:
        if _is_dict(a) and isinstance(a.get("type"), str):
            out.add(a["type"])
    return out


def _post_format(post: dict) -> str:
    """One format label per post, most-specific first: a post with both a
    video and images is a video post, and a poll or job posting is that even
    when it also carries an image."""
    if _is_dict(post.get("poll")):
        return "Poll"
    if _is_dict(post.get("job_posting")):
        return "Job posting"
    if _is_dict(post.get("article")):
        return "Article share"
    types = _attachment_types(post)
    if _MEDIA_VIDEO in types:
        return "Video"
    if _MEDIA_FILE in types:
        return "Document"
    image_count = sum(
        1 for a in post.get("attachments") or []
        if _is_dict(a) and a.get("type") == _MEDIA_IMAGE
    )
    if image_count > 1:
        return "Multi-image"
    if image_count == 1:
        return "Image"
    if _URL_RE.search(_post_text(post)):
        return "Text + link"
    return "Text only"


def _engagement(post: dict) -> float:
    return (_num(post.get("reaction_counter"))
            + _num(post.get("comment_counter"))
            + _num(post.get("repost_counter")))


def _excerpt(text: str, limit: int = 220) -> str:
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def _lv(pairs) -> list[dict]:
    """The {l,v} shape the report's chart renderers already consume."""
    return [{"l": str(label), "v": value} for label, value in pairs]


# ── metric blocks ────────────────────────────────────────────────────────────

def _activity(posts: list[dict]) -> dict:
    """Posting cadence: a weekly time series, day-of-week and hour-of-day
    profiles, and the real posting rate over the window the posts actually
    cover (never a made-up "per month" figure extrapolated from 3 days)."""
    stamps = sorted(d for d in (_parse_dt(p.get("parsed_datetime")) for p in posts) if d)
    if not stamps:
        return {}

    first, last = stamps[0], stamps[-1]
    span_days = max((last - first).days, 0)
    # Inclusive window: 100 posts on a single day is a 1-day window, not 0.
    window_days = span_days + 1
    per_week = _round(len(stamps) / (window_days / 7.0), 1) if window_days else 0

    # Weekly buckets across the whole window, including the zero weeks -- a
    # cadence chart that silently drops the weeks with no posts would show a
    # flat line for a company that went dark for a month.
    weeks: Counter = Counter()
    for dt in stamps:
        iso = dt.isocalendar()
        weeks[(iso[0], iso[1])] += 1
    ordered_weeks = sorted(weeks)
    series: list[dict] = []
    if ordered_weeks:
        cursor = datetime.fromisocalendar(ordered_weeks[0][0], ordered_weeks[0][1], 1)
        end = datetime.fromisocalendar(ordered_weeks[-1][0], ordered_weeks[-1][1], 1)
        guard = 0
        while cursor <= end and guard < 120:
            iso = cursor.isocalendar()
            # Built by hand rather than strftime("%-d %b"): the no-pad flag is
            # a glibc extension, absent on some platforms.
            label = f"{cursor.day} {_MONTHS[cursor.month - 1]}"
            series.append({"l": label, "v": weeks.get((iso[0], iso[1]), 0)})
            cursor = datetime.fromordinal(cursor.toordinal() + 7)
            guard += 1

    days = Counter(dt.weekday() for dt in stamps)
    hours = Counter(dt.hour for dt in stamps)

    # Longest silent stretch between consecutive posts -- a consistency signal
    # the per-week average hides entirely.
    gaps = [(stamps[i + 1] - stamps[i]).days for i in range(len(stamps) - 1)]

    return {
        "postsAnalyzed": len(posts),
        "windowStart": first.date().isoformat(),
        "windowEnd": last.date().isoformat(),
        "windowDays": window_days,
        "postsPerWeek": per_week,
        "activeWeeks": sum(1 for b in series if b["v"] > 0),
        "totalWeeks": len(series),
        "longestGapDays": max(gaps) if gaps else 0,
        "cadence": series,
        "dayOfWeek": _lv((_DAY_LABELS[i], days.get(i, 0)) for i in range(7)),
        "hourOfDay": _lv((f"{h:02d}", hours.get(h, 0)) for h in range(24)),
    }


def _engagement_block(posts: list[dict], followers: float) -> dict:
    reactions = [_num(p.get("reaction_counter")) for p in posts]
    comments = [_num(p.get("comment_counter")) for p in posts]
    reposts = [_num(p.get("repost_counter")) for p in posts]
    totals = [_engagement(p) for p in posts]
    if not posts:
        return {}

    avg_total = sum(totals) / len(totals)
    block = {
        "totalReactions": _int(sum(reactions)),
        "totalComments": _int(sum(comments)),
        "totalReposts": _int(sum(reposts)),
        "avgReactions": _round(sum(reactions) / len(reactions), 1),
        "medianReactions": _round(_median(reactions), 1),
        "avgComments": _round(sum(comments) / len(comments), 1),
        "avgTotal": _round(avg_total, 1),
        "bestPostEngagement": _int(max(totals)) if totals else 0,
        "zeroEngagementPosts": sum(1 for t in totals if t <= 0),
        "commentsPerReaction": _round(
            sum(comments) / sum(reactions), 3) if sum(reactions) else 0,
    }
    # Engagement rate is only meaningful against a real follower count -- with
    # no followers field it is omitted rather than divided by a guessed base.
    if followers > 0:
        block["engagementRatePct"] = _round(avg_total / followers * 100.0, 4)
        block["followers"] = _int(followers)
    return block


def _by_group(posts: list[dict], key_fn) -> list[dict]:
    """Average engagement per group, plus the group's share of posts. Sorted
    by average engagement so the best-performing format reads first."""
    buckets: dict[str, list[float]] = {}
    for p in posts:
        buckets.setdefault(str(key_fn(p)), []).append(_engagement(p))
    rows = [{
        "label": label,
        "posts": len(vals),
        "avgEngagement": _round(sum(vals) / len(vals), 1),
        "totalEngagement": _int(sum(vals)),
    } for label, vals in buckets.items() if vals]
    rows.sort(key=lambda r: r["avgEngagement"], reverse=True)
    return rows


def _length_bucket(post: dict) -> str:
    words = len(_post_text(post).split())
    if words < 40:
        return "Short (<40 words)"
    if words < 120:
        return "Medium (40-120)"
    return "Long (120+ words)"


def _content_block(posts: list[dict]) -> dict:
    if not posts:
        return {}
    texts = [_post_text(p) for p in posts]
    words = [len(t.split()) for t in texts]
    tags: Counter = Counter()
    for t in texts:
        for tag in _HASHTAG_RE.findall(t):
            tags[tag.lower()] += 1

    with_tag = sum(1 for t in texts if _HASHTAG_RE.search(t))
    with_link = sum(1 for t in texts if _URL_RE.search(t))
    with_q = sum(1 for t in texts if _QUESTION_RE.search(t))
    with_emoji = sum(1 for t in texts if _EMOJI_RE.search(t))
    # Mention COUNTS only, never the mentioned names: the vendor's
    # mentions[].start/length offsets drift against the text on posts
    # containing emoji, so slicing them yields mangled fragments
    # ("| Niket", "\x94 Susan Cred") in real data. A garbled name list is
    # worse than no name list, so only the volume is reported.
    with_mention = sum(1 for p in posts if (p.get("mentions") or []))
    total = len(posts)

    return {
        "avgWords": _round(sum(words) / len(words), 0),
        "medianWords": _round(_median([float(w) for w in words]), 0),
        "longestPostWords": max(words) if words else 0,
        "signals": _lv([
            ("Uses hashtags", _pct(with_tag, total)),
            ("Has media", _pct(sum(1 for p in posts if _attachment_types(p)), total)),
            ("Tags people or pages", _pct(with_mention, total)),
            ("Includes a link", _pct(with_link, total)),
            ("Asks a question", _pct(with_q, total)),
            ("Uses emoji", _pct(with_emoji, total)),
        ]),
        "hashtags": [[tag, count] for tag, count in tags.most_common(24)],
        "hashtagsUsed": len(tags),
    }


def _voice_block(posts: list[dict]) -> dict:
    """Page voice vs. people voice. `author.is_company` is false when a post
    on the company feed came from a person, which is the employee-advocacy
    signal a B2B strategist actually wants."""
    if not posts:
        return {}
    company, people = [], []
    authors: Counter = Counter()
    author_engagement: dict[str, float] = {}
    for p in posts:
        author = p.get("author") if _is_dict(p.get("author")) else {}
        if author.get("is_company"):
            company.append(p)
        else:
            people.append(p)
            name = author.get("name")
            if isinstance(name, str) and name.strip():
                authors[name.strip()] += 1
                author_engagement[name.strip()] = (
                    author_engagement.get(name.strip(), 0.0) + _engagement(p))
    originals = sum(1 for p in posts if not p.get("is_repost"))
    block = {
        "mix": _lv([("Company page", len(company)), ("People", len(people))]),
        "originalVsRepost": _lv([
            ("Original", originals), ("Reposted", len(posts) - originals)]),
        "repostSharePct": _pct(len(posts) - originals, len(posts)),
    }
    if authors:
        block["topPeople"] = [{
            "name": name,
            "posts": count,
            "engagement": _int(author_engagement.get(name, 0)),
        } for name, count in authors.most_common(8)]
    return block


def _top_posts(posts: list[dict], limit: int = 6) -> list[dict]:
    ranked = sorted(posts, key=_engagement, reverse=True)[:limit]
    out = []
    for p in ranked:
        dt = _parse_dt(p.get("parsed_datetime"))
        row = {
            "excerpt": _excerpt(_post_text(p)),
            "format": _post_format(p),
            "reactions": _int(p.get("reaction_counter")),
            "comments": _int(p.get("comment_counter")),
            "reposts": _int(p.get("repost_counter")),
            "engagement": _int(_engagement(p)),
        }
        if dt:
            row["date"] = dt.date().isoformat()
        if isinstance(p.get("share_url"), str) and p["share_url"].strip():
            row["url"] = p["share_url"].strip()
        out.append(row)
    return out


def _signals_block(posts: list[dict]) -> dict:
    """Discrete GTM signals buried in the post feed that no vendor field
    surfaces: open roles, poll results, and shared articles."""
    hiring, polls, articles = [], [], []
    for p in posts:
        job = p.get("job_posting")
        if _is_dict(job):
            row = {k: job.get(k) for k in ("title", "location") if isinstance(job.get(k), str)}
            company = job.get("company")
            if _is_dict(company) and isinstance(company.get("name"), str):
                row["company"] = company["name"]
            if row:
                hiring.append(row)
        poll = p.get("poll")
        if _is_dict(poll) and isinstance(poll.get("question"), str):
            options = [{
                "text": o.get("text"),
                "votes": _int(o.get("votes_count")),
            } for o in (poll.get("options") or []) if _is_dict(o) and isinstance(o.get("text"), str)]
            polls.append({
                "question": poll["question"],
                "totalVotes": _int(poll.get("total_votes_count")),
                "options": options,
                "isOpen": bool(poll.get("is_open")),
            })
        article = p.get("article")
        if _is_dict(article) and isinstance(article.get("title"), str):
            row = {"title": article["title"]}
            for k in ("url", "author"):
                if isinstance(article.get(k), str) and article[k].strip():
                    row[k] = article[k].strip()
            articles.append(row)
    block = {}
    if hiring:
        block["hiring"] = hiring[:12]
    if polls:
        block["polls"] = polls[:6]
    if articles:
        block["articles"] = articles[:12]
    return block


def _footprint(output: dict) -> dict:
    """Company-shape facts the profile blob carries but the report never
    showed: office footprint (Google's real run has 48 locations), and
    followers per employee as an audience-reach-vs-headcount ratio."""
    profile = output.get("getcompanyprofile.profile")
    profile = profile if _is_dict(profile) else {}
    followers = _num(output.get("getcompanyprofile.followers_count"))
    employees = _num(output.get("getcompanyprofile.employee_count"))

    block: dict = {}
    locations = [l for l in (profile.get("locations") or []) if _is_dict(l)]
    if locations:
        countries: Counter = Counter()
        for l in locations:
            country = l.get("country")
            if isinstance(country, str) and country.strip():
                countries[country.strip().upper()] += 1
        hq = next((l for l in locations if l.get("is_headquarter")), None)
        block["officeCount"] = len(locations)
        if countries:
            block["countryCount"] = len(countries)
            block["topCountries"] = _lv(countries.most_common(8))
        if hq:
            city = hq.get("city") if isinstance(hq.get("city"), str) else None
            country = hq.get("country") if isinstance(hq.get("country"), str) else None
            label = ", ".join([p for p in (city, country) if p])
            if label:
                block["headquarters"] = label
    if followers > 0 and employees > 0:
        block["followersPerEmployee"] = _round(followers / employees, 1)
    if isinstance(profile.get("foundation_date"), str) and profile["foundation_date"].strip():
        block["founded"] = profile["foundation_date"].strip().split("/")[-1]
    industry = profile.get("industry")
    if isinstance(industry, list) and industry and isinstance(industry[0], str):
        block["industry"] = industry[0]
    elif isinstance(industry, str) and industry.strip():
        block["industry"] = industry.strip()
    if isinstance(profile.get("tagline"), str) and profile["tagline"].strip():
        block["tagline"] = profile["tagline"].strip()
    return block


def _insights(activity: dict, engagement: dict, formats: list[dict],
              lengths: list[dict], voice: dict, content: dict) -> list[str]:
    """Deterministic, fully grounded observations -- each one is arithmetic
    over the blocks above, phrased as the finding a strategist would write.
    Unlike the Claude synthesis tab these need no API key and cannot
    hallucinate: if the numbers supporting a line aren't there, the line
    isn't generated."""
    out: list[str] = []

    per_week = activity.get("postsPerWeek")
    if per_week:
        window = activity.get("windowDays") or 0
        out.append(
            f"Publishes {per_week} posts per week across the {window}-day window "
            f"analyzed ({activity.get('postsAnalyzed')} posts)."
        )
    gap = activity.get("longestGapDays") or 0
    if gap >= 7:
        out.append(f"Longest silent stretch in the window was {gap} days, so cadence is uneven.")

    days = activity.get("dayOfWeek") or []
    if days:
        busiest = max(days, key=lambda d: _num(d.get("v")))
        quiet_weekend = sum(_num(d["v"]) for d in days if d["l"] in ("Sat", "Sun"))
        if _num(busiest.get("v")) > 0:
            out.append(f"{busiest['l']} is the heaviest posting day.")
        if quiet_weekend == 0 and len(days) == 7:
            out.append("Nothing at all is published on weekends.")

    # Format performance only reads as a finding when the winner has enough
    # posts behind it to not be a single lucky outlier.
    solid = [f for f in formats if f.get("posts", 0) >= 3]
    if len(solid) >= 2:
        best, worst = solid[0], solid[-1]
        avg = engagement.get("avgTotal") or 0
        if avg and best["avgEngagement"] > avg:
            ratio = _round(best["avgEngagement"] / avg, 1)
            out.append(
                f"{best['label']} posts earn {ratio}x the average engagement "
                f"({best['avgEngagement']} vs {avg}) across {best['posts']} posts."
            )
        if worst["avgEngagement"] < avg:
            out.append(
                f"{worst['label']} posts underperform at {worst['avgEngagement']} "
                f"average engagement against an overall {avg}."
            )

    long_rows = [l for l in lengths if l.get("posts", 0) >= 3]
    if len(long_rows) >= 2:
        out.append(
            f"{long_rows[0]['label']} copy performs best at "
            f"{long_rows[0]['avgEngagement']} average engagement."
        )

    rate = engagement.get("engagementRatePct")
    if rate is not None:
        out.append(
            f"Average engagement is {engagement.get('avgTotal')} per post against "
            f"{engagement.get('followers'):,} followers, a {rate}% engagement rate."
        )
    zero = engagement.get("zeroEngagementPosts") or 0
    if zero:
        noun = "post" if zero == 1 else "posts"
        out.append(f"{zero} {noun} in the window drew no engagement at all.")

    repost = voice.get("repostSharePct")
    if repost is not None and repost >= 20:
        out.append(f"{repost}% of the feed is reposted rather than original content.")
    people = next((m for m in (voice.get("mix") or []) if m.get("l") == "People"), None)
    if people is not None and _num(people.get("v")) == 0:
        out.append("Every post comes from the company page, with no employee voices in the feed.")

    signals = {s["l"]: _num(s["v"]) for s in (content.get("signals") or [])}
    if signals.get("Uses hashtags") is not None and signals.get("Uses hashtags", 0) < 20:
        out.append("Hashtags are barely used, so posts rely entirely on follower reach.")
    if signals.get("Asks a question", 0) < 15:
        out.append("Few posts ask a question, which is the cheapest lever on comment volume.")
    if content.get("avgWords"):
        out.append(f"Average post runs {content['avgWords']} words.")

    return out


# ── public API ───────────────────────────────────────────────────────────────

def backfill_nested_fields(output: dict) -> dict:
    """Hoist `messagingagent.summary.{messaging,stats}` to top level.

    Real runs are inconsistent about where these two live: the Myntra and Boat
    runs returned `messagingagent.messaging` and `messagingagent.stats` as
    their own top-level keys, while the Google run omitted both and nested the
    identical objects inside `messagingagent.summary`. The report renders by
    top-level key, so on a Google-shaped run the Messaging tab lost its
    keyword cloud, pains, benefits, value props and stat tiles entirely --
    the data was in the response the whole time, one level down.

    The nested copy is REMOVED once hoisted. The report renders one section
    per top-level key and also renders `summary` itself, so leaving the copy
    in place made the whole keyword cloud, pains, benefits and stat tiles
    appear twice on the Messaging tab, once nested under "Summary" and once as
    their own sections.

    Mutates and returns `output` (callers pass a copy).
    """
    summary = output.get("messagingagent.summary")
    if not _is_dict(summary):
        return output
    for field in ("messaging", "stats"):
        key = f"messagingagent.{field}"
        nested = summary.get(field)
        if nested in (None, [], {}, ""):
            continue
        current = output.get(key)
        if current in (None, [], {}, ""):
            output[key] = nested
            summary.pop(field, None)
        elif current == nested:
            summary.pop(field, None)
    return output


def compute(output: dict) -> dict:
    """The `derived.*` block for one run's output. {} when there is nothing to
    derive from (no posts and no profile), so callers can skip the section
    rather than render an empty shell."""
    posts = _clean_posts(output)
    followers = _num(output.get("getcompanyprofile.followers_count"))
    footprint = _footprint(output)

    if not posts:
        # No post feed: the footprint block is still real and worth showing.
        return {"derived.footprint": footprint} if footprint else {}

    activity = _activity(posts)
    engagement = _engagement_block(posts, followers)
    formats = _by_group(posts, _post_format)
    lengths = _by_group(posts, _length_bucket)
    voice = _voice_block(posts)
    content = _content_block(posts)
    signals = _signals_block(posts)

    derived: dict = {
        "derived.activity": activity,
        "derived.engagement": engagement,
        "derived.formatPerformance": formats,
        "derived.lengthPerformance": lengths,
        "derived.voice": voice,
        "derived.content": content,
        "derived.topPosts": _top_posts(posts),
        "derived.insights": _insights(activity, engagement, formats, lengths, voice, content),
        "derived.formatMix": _lv((f["label"], f["posts"]) for f in
                                 sorted(formats, key=lambda r: r["posts"], reverse=True)),
    }
    if footprint:
        derived["derived.footprint"] = footprint
    for key, value in signals.items():
        derived[f"derived.{key}"] = value
    return {k: v for k, v in derived.items() if v not in (None, {}, [])}


def augment(output: dict) -> dict:
    """A read-time view of one run: text repaired, nested fields backfilled,
    every `derived.*` block added.

    Order matters. Mojibake repair runs first so that every later step, and
    every consumer, sees real text: the emoji-usage metric can only be
    computed on repaired copy, and the excerpts in `derived.topPosts` would
    otherwise carry the corruption forward into the report and the Claude
    prompt.

    Best effort: on any failure the original output is returned unchanged,
    because a broken derived metric must never cost a reader the vendor data
    that was fine.
    """
    if not _is_dict(output):
        return output
    try:
        merged = repair_strings(copy.deepcopy(output))
        backfill_nested_fields(merged)
        merged.update(compute(merged))
        return merged
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("lps_analytics: augment failed: %s", e)
        return output


# Vendor fields that are enormous and add nothing to a synthesis prompt: the
# raw post feed is up to 100 full post bodies with attachment URLs and
# permission flags (320KB on the real Google run), and the profile blob
# carries ~72 viewer-permission booleans.
_LLM_DROP_KEYS = {"_sseDebug", _POSTS_KEY, "getcompanyprofile.profile"}


def compact_for_llm(output: dict, max_top_posts: int = 8) -> dict:
    """A token-efficient view of a run for the Claude synthesis pass.

    The naive approach (hand Claude the whole output) sent ~80k tokens of raw
    post JSON per run, most of it attachment URLs and permission flags, and
    still left the model to do arithmetic on 100 posts to notice that video
    outperforms. This sends the computed metrics plus a handful of top-post
    excerpts instead: an order of magnitude fewer tokens AND strictly more
    useful signal, since cadence and format performance arrive as numbers
    rather than something the model has to derive.
    """
    if not _is_dict(output):
        return {}
    compact = {k: v for k, v in output.items() if k not in _LLM_DROP_KEYS}
    posts = _clean_posts(output)
    if posts:
        compact["postFeedSummary"] = {
            "postsAvailable": len(posts),
            "topPosts": _top_posts(posts, max_top_posts),
        }
    profile = output.get("getcompanyprofile.profile")
    if _is_dict(profile):
        keep = ("tagline", "foundation_date", "industry", "activities")
        trimmed = {k: profile[k] for k in keep if profile.get(k) not in (None, [], "")}
        if trimmed:
            compact["getcompanyprofile.profileHighlights"] = trimmed
    return compact
