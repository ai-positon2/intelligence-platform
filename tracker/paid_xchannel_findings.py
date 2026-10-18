"""Paid Marketing Agents: Cross-Channel & Attribution Analysis, what the numbers say.

`findings(r)` reads the assembled report (tracker/paid_xchannel.py) and returns the things worth doing something about, each with a level (high, medium, low, info), a title, the
words, and the figures it rests on. Every rule is arithmetic against a bar written down here; a platform with too little data is said to have too little, never ranked. No model is asked.
"""
from __future__ import annotations

from . import paid_xchannel_math as m

BLENDED_MOVE = 10.0                              # percent the blended cost per conversion must move before it is called a change
CREATE_SHARE = 25.0                              # percent of a platform's spend on demand creating campaigns with no conversions before the page says so
CREATE_OVERALL = 30.0
OVERLAP_BAR = 1.10                               # the platforms claim at least this many times what the person's system recorded before it is called overlap
LEVELS = {"high": 0, "medium": 1, "low": 2, "info": 3}
SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "CAD": "CA$", "AUD": "A$"}


def money(v, currency=""):
    if v is None:
        return "n/a"
    s = SYMBOL.get(currency)
    body = "{:,.2f}".format(v) if abs(v) < 1000 else "{:,.0f}".format(v)
    return (s + body) if s else (body + (" " + currency if currency else ""))


def num(v):
    return "{:,.0f}".format(v) if abs(v - round(v)) < 0.05 or abs(v) >= 100 else "{:,.1f}".format(v)


def pct(v):
    return "%.0f%%" % v


def name(p):
    return m.PLATFORM_NAME.get(p, p)


def poss(p):
    """The possessive of a platform's name: both names end in s, so an apostrophe alone ("Meta Ads' budget")."""
    n = name(p)
    return n + ("'" if n.endswith("s") else "'s")


def _item(id_, level, title, text, evidence=None, action=None):
    out = {"id": id_, "level": level, "title": title, "text": text, "evidence": evidence or []}
    if action:
        out["action"] = action
    return out


def findings(r):
    """The findings of a report. `r` carries: currency, platforms (the ones read), channels {platform: {"cur", "prev", "share", "types"}}, blended {"cur", "prev"}, decomposition, links,
    notes (attribution comparison notes) and reconcile (or None)."""
    out = []
    cur = r.get("currency") or ""
    ch = r["channels"]
    names = list(ch)
    if len(names) < 2:
        only = name(names[0]) if names else "No platform"
        out.append(_item("one-platform", "info", "Only one platform was read", "%s was read on its own, so there is no cross-channel comparison. Pick the Google Ads and the Meta account together to compare them." % only))
    for n in names:
        c = ch[n]["cur"]
        if c["cost"] > 0 and c["conv"] < m.MIN_CONV:
            out.append(_item("thin-" + n, "medium", "%s recorded too few conversions to judge" % name(n),
                             "%s recorded %s conversions on %s of spend in this range. Under %d the cost per conversion moves a lot on one or two conversions, so it is not ranked against anything. "
                             "Choose a longer range." % (name(n), num(c["conv"]), money(c["cost"], cur), m.MIN_CONV),
                             ["%s conversions" % num(c["conv"]), "%s spend" % money(c["cost"], cur)]))
    both = [n for n in names if ch[n]["cur"]["conv"] >= m.MIN_CONV and ch[n]["cur"]["cpa"]]
    if len(both) == 2:
        a, b = sorted(both, key=lambda n: ch[n]["cur"]["cpa"])
        cheap, dear = ch[a]["cur"], ch[b]["cur"]
        ratio = dear["cpa"] / cheap["cpa"]
        if ratio >= m.CPA_GAP:
            text = ("%s costs %.1f times as much per conversion as %s (%s against %s). These are averages: the cheaper platform may not take more budget at the same price, so treat this as a "
                    "place to test, not a reason to move everything." % (name(b), ratio, name(a), money(dear["cpa"], cur), money(cheap["cpa"], cur)))
            if any(n["id"] == "click-window" or n["id"] == "view-through" for n in r.get("notes") or []):
                text += " Part of the gap is the two platforms' different attribution windows (see below)."
            create = _create_share(ch[b])
            if create is not None and create >= 50:
                text += " %s of %s spend is on campaigns that create demand, which are not judged fairly on cost per conversion alone." % (pct(create), poss(b))
            out.append(_item("cpa-gap", "high", "%s costs %.1f times as much per conversion as %s" % (name(b), ratio, name(a)), text,
                             ["%s: %s per conversion, %s conversions" % (name(n), money(ch[n]["cur"]["cpa"], cur), num(ch[n]["cur"]["conv"])) for n in (a, b)],
                             "Test moving 10 to 20 percent of %s budget to %s for two to four weeks, and judge it on the conversions your own system records, not on what the platforms claim." % (poss(b), name(a))))
    bl = r["blended"]
    if bl["cur"]["conv"] >= m.MIN_CONV and bl["prev"]["conv"] >= m.MIN_CONV and bl["cur"]["cpa"] and bl["prev"]["cpa"]:
        move = m.change(bl["cur"]["cpa"], bl["prev"]["cpa"])
        if move is not None and abs(move) >= BLENDED_MOVE:
            d = r.get("decomposition") or {}
            up = move > 0
            text = "The blended cost per conversion %s %s, from %s to %s." % ("rose" if up else "fell", pct(abs(move)), money(bl["prev"]["cpa"], cur), money(bl["cur"]["cpa"], cur))
            ev = []
            if d.get("ok"):
                text += " Of the %s change, %s is the platforms' own costs changing and %s is the mix of where the conversions came from." % (
                    money(abs(d["change"]), cur), money(abs(d["rateTotal"]), cur), money(abs(d["mix"]), cur))
                for n, v in sorted(d["rate"].items(), key=lambda kv: -abs(kv[1])):
                    ev.append("%s own cost: %s%s" % (poss(n), "+" if v >= 0 else "-", money(abs(v), cur)))
                ev.append("Mix: %s%s" % ("+" if d["mix"] >= 0 else "-", money(abs(d["mix"]), cur)))
            out.append(_item("blended-move", "medium" if up else "info", "Blended cost per conversion %s %s" % ("rose" if up else "fell", pct(abs(move))), text, ev))
    for n in names:
        idle = _idle_share(ch[n])
        if idle is not None and idle >= CREATE_SHARE:
            out.append(_item("idle-" + n, "medium", "%s of %s spend recorded no conversions" % (pct(idle), poss(n)),
                             "%s of %s spend is on campaign types that create demand (awareness, traffic, video and the like) and recorded no conversions in this range. %s cost per conversion divides all of its "
                             "spend by conversions only some of its campaigns are built to produce, so it reads worse than the campaigns that convert are doing." % (pct(idle), poss(n), poss(n))))
    allcost = sum(ch[n]["cur"]["cost"] for n in names)
    create_cost = sum(t["cost"] for n in names for t in ch[n]["types"] if t["role"] == "create")
    if allcost > 0 and create_cost / allcost * 100 >= CREATE_OVERALL:
        out.append(_item("create", "info", "%s of the spend is on campaigns that create demand" % pct(create_cost / allcost * 100),
                         "Campaigns that create demand (Meta awareness and traffic, Display, Video, Demand Gen) are answered later by people who search or click something else. A last-click cost per conversion "
                         "undercounts them and flatters the campaigns that capture the demand."))
    for n in r.get("notes") or []:
        out.append(_item("attr-" + n["id"], {"high": "high", "medium": "medium"}.get(n["level"], "low"), n["title"], n["text"]))
    rec = r.get("reconcile")
    if rec:
        out.extend(_reconcile_findings(rec, cur))
    lk = r.get("links") or {}
    for t in lk.get("tests") or []:
        if t["reads"] == "none":
            continue
        when = "in the same week" if t["lag"] == 0 else "a week later"
        out.append(_item("link-%s-%s-%d" % (t["spend"], t["conversions"], t["lag"]), "low", "%s spend and %s conversions move %s" % (name(t["spend"]), name(t["conversions"]), "together" if t["reads"] == "together" else "in opposite directions"),
                         "Weeks when %s spend rose had %s %s conversions %s (correlation %.2f over %d weeks, p %.3f). It is a pattern in the numbers, not proof that one causes the other." % (
                             name(t["spend"]), "more" if t["reads"] == "together" else "fewer", name(t["conversions"]), when, t["r"], t["n"], t["p"])))
    out.sort(key=lambda f: LEVELS[f["level"]])
    return out


def _reconcile_findings(rec, cur):
    out = []
    c = rec.get("conversions")
    if c:
        if c["ratio"] and c["ratio"] >= OVERLAP_BAR and c["over"]:
            out.append(_item("overlap", "high", "The platforms claim %s conversions that your system did not record" % num(c["excess"]),
                             "Google and Meta between them claim %s conversions; your own system recorded %s. At least %s are counted by both platforms, or by one of them and not by your system. Spend per recorded conversion is %s; "
                             "the platforms' own costs scaled down by the same factor are %s." % (
                                 num(c["claimed"]), num(c["actual"]), num(c["excess"]), money(rec.get("blendedCpa"), cur),
                                 " and ".join("%s %s" % (name(n), money(v.get("cpa"), cur)) for n, v in c["perPlatform"].items())),
                             ["Claimed: %s" % num(c["claimed"]), "Recorded: %s" % num(c["actual"])],
                             "Report the blended cost per recorded conversion to the client, and set bids and budgets by the scaled costs, which assume each platform over-claims by the same share."))
        elif not c["over"]:
            share = c["claimed"] / c["actual"] * 100
            out.append(_item("claim-share", "info", "The platforms claim %s of the conversions your system recorded" % pct(share),
                             "Google and Meta between them claim %s of the %s conversions your system recorded. The rest came from other channels, direct visits, offline or sources neither platform sees. "
                             "Overlap between the two platforms cannot be seen from this, so nothing is scaled down. Spend per recorded conversion is %s." % (pct(share), num(c["actual"]), money(rec.get("blendedCpa"), cur)),
                             ["Claimed: %s" % num(c["claimed"]), "Recorded: %s" % num(c["actual"])]))
    v = rec.get("value")
    if v and v["ratio"] and v["ratio"] >= OVERLAP_BAR and v["over"]:
        out.append(_item("overlap-value", "high", "The platforms claim %s more revenue than your system recorded" % money(v["excess"], cur),
                         "Google and Meta between them claim %s of conversion value; your own system recorded %s. Return on the revenue you recorded is %.2f against the %.2f the platforms' claims add up to." % (
                             money(v["claimed"], cur), money(v["actual"], cur), rec.get("blendedRoas") or 0, (v["claimed"] / rec["spend"]) if rec["spend"] else 0)))
    return out


def _create_share(ch):
    cost = ch["cur"]["cost"]
    if cost <= 0:
        return None
    return sum(t["cost"] for t in ch["types"] if t["role"] == "create") / cost * 100


def _idle_share(ch):
    cost = ch["cur"]["cost"]
    if cost <= 0:
        return None
    return sum(t["cost"] for t in ch["types"] if t["role"] == "create" and t["conv"] <= 0) / cost * 100
