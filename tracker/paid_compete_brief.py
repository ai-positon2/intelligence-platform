"""Paid Marketing Agents: Competitor Analysis, ideas written by Claude from the competitors' real ads (the call is passed in, so all of it is tested without a network).

The rule-based ideas (tracker/paid_compete_math.py, tracker/paid_compete_mix.py) say WHAT differs. These say what to MAKE: a format and a placement, a hook, a first line, and why, each tied to the
competitor ads it learned from. Claude is shown the gaps the rules found, our own ads (their kinds and a sample of their words) and their longest-running ads, with the pictures of up to MAX_IMAGES of
them (a video by its cover picture).

What keeps it honest:
  * every idea must name at least one of the competitor ads it was shown, by its id; an idea that names none, or only ads it was not shown, is dropped;
  * it is told not to invent facts about our business (a price, an offer, a figure): it writes a placeholder such as [your offer] instead;
  * the page says these are written by a model.

It is never automatic (it costs a few cents): the page asks for it, and the answer is kept until the competitors are read again or our ads change (SIGNATURE).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import os

from . import paid_compete_math as cm
from . import paid_compete_mix as mx

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
MAX_IDEAS = 6
MAX_ADS = 10                      # their ads shown per platform
PER_FORMAT = 2                    # the longest runners of each format first, so every format they use is seen
MAX_IMAGES = 8
MAX_OWN_LINES = 25
STATE = "compete:brief:"

SYSTEM = (
    "You are a senior paid media creative strategist. You are shown a client's competitors' live ads on Google and Meta (their words, format, button, placements and how many days each has run; "
    "a long run is the best public sign that an ad works), the client's own ads, and the gaps a set of rules found between the two. Pictures of some competitor ads follow, each labelled with its id.\n\n"
    "Write between 3 and %d ideas for new ads or creatives the client should test. Each idea is specific enough to brief a designer: the platform, the format, the placement, the hook (what the "
    "picture or the first seconds of the video show), the first line of copy, and why, naming the competitor ads it learned from by their ids in basedOn. Learn the angle from their ads, never copy "
    "their words or claims. Prefer ideas that close the gaps and that follow the formats and angles that have run longest.\n\n"
    "Do not invent facts about the client (a price, an offer, a number of customers, a rating, an award): where an idea needs one, write a placeholder in square brackets, such as [your offer]. "
    "Write plainly, in short sentences. Do not use em dashes." % MAX_IDEAS
)
SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"}, "platform": {"type": "string", "enum": ["google", "meta"]}, "format": {"type": "string"}, "placement": {"type": "string"},
                    "hook": {"type": "string"}, "firstLine": {"type": "string"}, "why": {"type": "string"}, "basedOn": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "platform", "format", "placement", "hook", "firstLine", "why", "basedOn"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["ideas"],
    "additionalProperties": False,
}


def _picture(a):
    for m in a.get("media") or []:
        u = m.get("poster") if m.get("kind") == "video" else m.get("url")
        if cm.https(u or ""):
            return u
    return ""


def pick_ads(theirs):
    """Their ads to show Claude, the longest-running first: the PER_FORMAT longest of each format, then the next longest, MAX_ADS in all."""
    ranked = sorted(theirs, key=lambda a: (-(a.get("days") or 0), a["id"]))
    out, per = [], {}
    for a in ranked:
        if per.get(a.get("format"), 0) < PER_FORMAT:
            per[a.get("format")] = per.get(a.get("format"), 0) + 1
            out.append(a)
    out += [a for a in ranked if a not in out]
    return out[:MAX_ADS]


def _ad_line(a):
    words = " | ".join(x for x in [(a.get("headlines") or [""])[0], (a.get("bodies") or [""])[0], (a.get("text") or "")[:300]] if x)
    return "- id %s · %s · %s · %s%s%s · %s · %s" % (a["id"], a.get("who") or "?", a["platform"], mx.label_of("format", a.get("format") or "other"),
                                                  (" · button " + (a.get("cta") or a.get("ctaType"))) if (a.get("cta") or a.get("ctaType")) else "",
                                                  (" · on " + ", ".join(a["networks"])) if a.get("networks") else "",
                                                  "running %s" % cm.plural(a["days"], "day") if a.get("days") is not None else "running for an unknown time", words or "(no words: a picture or a video)")


def evidence(rep, theirs_by, own_lines_by):
    """(text, shown ads): what Claude is told. `theirs_by` is {platform: their ads}, `own_lines_by` {platform: our lines of copy}."""
    parts, shown = [], []
    for p in rep["platforms"]:
        name = "Google" if p == "google" else "Meta"
        a = (rep.get("analysis") or {}).get(p) or {}
        m = (rep.get("mix") or {}).get(p) or {}
        parts.append("## %s" % name)
        gaps = [g for g in (a.get("compare") or {}).get("gaps") or []]
        if gaps:
            parts.append("Messages they use clearly more than we do: " + "; ".join("%s (%d%% of their lines, %d%% of ours)" % (g["label"], round(g["theirShare"]), round(g["ownShare"])) for g in gaps))
        for kind, k in (m.get("kinds") or {}).items():
            if k.get("rows"):
                parts.append("%s mix, theirs against ours: %s" % (kind.capitalize(), "; ".join("%s %d%% / %d%%" % (r["label"], round(r["their"]), round(r["own"])) for r in k["rows"][:8])))
        if a.get("offers"):
            parts.append("Offers they state: " + "; ".join(o["text"] for o in a["offers"][:6]))
        lines = own_lines_by.get(p) or []
        parts.append("Our own %s copy (a sample): %s" % (name, " | ".join(lines[:MAX_OWN_LINES]) if lines else "none read"))
        ads = pick_ads(theirs_by.get(p) or [])
        shown += ads
        parts.append("Their ads, the longest-running first:\n" + "\n".join(_ad_line(x) for x in ads) if ads else "No competitor ads were read on %s." % name)
    return "\n".join(parts), shown


def signature(rep, entries):
    """What the ideas depend on: the competitors' reads and our ads. When it changes, the saved ideas are old."""
    reads = sorted("%s|%s|%s" % (e["name"], e["platform"], e.get("fetchedAt") or "") for e in entries)
    own = json.dumps(rep.get("own") or {}, sort_keys=True)
    return hashlib.sha256(("\n".join(reads) + own).encode("utf-8")).hexdigest()[:16]


def clean(ideas, shown_ids):
    """The ideas Claude wrote that name at least one ad it was shown (the others are dropped), MAX_IDEAS at most."""
    out = []
    for i in ideas or []:
        if not isinstance(i, dict):
            continue
        based = [str(x) for x in i.get("basedOn") or [] if str(x) in shown_ids]
        if not based or i.get("platform") not in ("google", "meta"):
            continue
        out.append({k: " ".join(str(i.get(k) or "").split())[:400] for k in ("title", "platform", "format", "placement", "hook", "firstLine", "why")} | {"basedOn": list(dict.fromkeys(based))[:4]})
    return out[:MAX_IDEAS]


def _image_blocks(ads, fetch):
    blocks = []
    for a in ads:
        if len(blocks) >= MAX_IMAGES * 2:
            break
        url = _picture(a)
        if not url:
            continue
        data, media_type = fetch(url)
        if not data:
            continue
        blocks += [{"type": "text", "text": "Picture of ad %s (%s, %s):" % (a["id"], a.get("who") or "?", mx.label_of("format", a.get("format") or "other"))},
                   {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode("ascii")}}]
    return blocks


def ask_claude(system, content):
    """The parsed answer {ideas: [...]}, or {error}. Never raises."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return {"error": "ANTHROPIC_API_KEY is not configured on this deployment."}
    import anthropic
    client = anthropic.Anthropic(api_key=key, timeout=100.0, max_retries=1)
    model = os.environ.get("COMPETE_BRIEF_MODEL", MODEL)
    args = dict(model=model, max_tokens=16000, system=system, messages=[{"role": "user", "content": content}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}})
    try:
        try:                                                                         # on a policy decline the API runs the same request on a fallback model
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **args)
        except anthropic.BadRequestError:
            resp = client.messages.create(**args)
    except anthropic.APIError as e:
        return {"error": "Claude could not be asked (%s)." % str(e)[:160]}
    if resp.stop_reason == "refusal":
        return {"error": "Claude declined to write ideas for these ads."}
    if resp.stop_reason == "max_tokens":
        return {"error": "Claude's answer was cut off. Try again."}
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        got = json.loads(text)
    except ValueError:
        return {"error": "Claude's answer could not be read."}
    return dict(got if isinstance(got, dict) else {}, model=getattr(resp, "model", model))


def saved(mon, key, sig):
    """The saved ideas for this scope: {status: "ready"|"old"|"none", ...}."""
    s = mon.state_get(STATE + key) or {}
    if not s:
        return {"status": "none"}
    return dict(s, status="ready" if s.get("sig") == sig else "old")


def write(mon, key, sig, rep, theirs_by, own_lines_by, *, ask_fn=None, fetch_fn=None, now=None):
    """Ask Claude for ideas and keep them: {status, ideas, at, model, images, error}. A failed ask is answered, not kept over the last good ideas."""
    if fetch_fn is None:
        from .sci_vision import fetch_image_bytes as fetch_fn
    text, shown = evidence(rep, theirs_by, own_lines_by)
    if not shown:
        return {"status": "none", "error": "No competitor ads have been read yet, so there is nothing to learn from. Press Refresh competitor ads first."}
    images = _image_blocks(shown, fetch_fn)
    got = (ask_fn or ask_claude)(SYSTEM, [{"type": "text", "text": text}] + images + [{"type": "text", "text": "Write the ideas now."}])
    if got.get("error"):
        return {"status": "failed", "error": got["error"]}
    ideas = clean(got.get("ideas"), {a["id"] for a in shown})
    if not ideas:
        return {"status": "failed", "error": "Claude wrote no idea tied to a competitor ad it was shown, so none is listed. Try again."}
    by_id = {a["id"]: a for a in shown}
    for i in ideas:
        i["ads"] = [{"id": x, "who": by_id[x].get("who") or "", "format": by_id[x].get("format") or "", "days": by_id[x].get("days"), "adUrl": by_id[x].get("adUrl") or "",
                     "headline": (by_id[x].get("headlines") or [""])[0]} for x in i["basedOn"]]
    out = {"sig": sig, "at": (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"), "ideas": ideas, "model": got.get("model") or MODEL, "images": len(images) // 2, "shown": len(shown)}
    mon.state_set(STATE + key, out)
    return dict(out, status="ready")
