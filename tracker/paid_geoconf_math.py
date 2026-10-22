"""Paid Marketing Agents: Location Conflicts, what the settings, the names and the words say about where a campaign shows (no network, no clock).

An account is passed in as plain lists (see tracker/paid_geoconf.py for how they are read):

  campaigns  [{id, name, channel, positive, negative}]       positive and negative are the geo target types, PRESENCE_OR_INTEREST or PRESENCE
  locations  [{campaignId, negative, kind, name, canonical, targetType, city}]      kind is "location" or "proximity"; canonical is "Quincy,Massachusetts,United States"
  schedules  [{campaignId, day, start, end}]                  hours as numbers (9.5 is 09:30)
  devices    [{campaignId, device, modifier}]                 modifier 1.0 is no change, 0 excludes the device
  ads        [{id, campaignId, campaign, adGroupId, adGroup, kind, texts: [{slot, text}]}]
  keywords   [{campaignId, adGroupId, adGroup, text}]
  groups     [{id, campaignId, name}]                         ad groups, so one with no live ad is still checked by its name

and the account's answer comes back as campaigns each with its findings, the rules ranked by how many things they touch, and the account's status. A place here is a name the account itself
uses (the places its campaigns target or exclude, with their state and country, the places the team saved, the places saved for other accounts, the fifty states): a campaign called after a
city nobody targets and nobody saved is not seen.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

LEVEL_RANK = {"high": 3, "medium": 2, "low": 1}
MAX_EVIDENCE = 3
MIN_PEERS = 3                      # campaigns of one type before a schedule or device can differ from "most of them"
PEER_SHARE = 0.6
MAX_KEYWORD_EXAMPLES = 3

STATES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas",
          "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
          "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
          "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming"]
SHORT_PLACE = 4                    # a place name shorter than this is not looked for in text (Ada, Rye, Rio)

RULES = {
    "tgt_conflict": {"level": "high", "label": "Targeted and excluded", "fix": "Remove the place from the excluded locations, or from the targeted ones. An exclusion always wins, so the targeted place never shows."},
    "name_place": {"level": "medium", "label": "Name says a place the campaign does not target", "fix": "Rename it to match where it shows, or add the place to the campaign's locations."},
    "group_copy": {"level": "high", "label": "Ad group and its ad name different places", "fix": "Make the ad say the place of its ad group, or move the ad to the ad group it belongs to."},
    "copy_place": {"level": "medium", "label": "Ad names a place the campaign does not target", "fix": "Change the ad to the place the campaign shows in, or add the place to the campaign's locations."},
    "kw_place": {"level": "medium", "label": "Keywords name a place the campaign does not target", "fix": "Move the keywords to the campaign that targets the place, or add the place to this campaign's locations."},
    "setting": {"level": "medium", "label": "Shows to people interested in the place", "fix": "Change the location option to Presence: people in or regularly in the targeted locations. Leave it if showing to people interested is wanted, and say so below."},
    "no_target": {"level": "medium", "label": "No location set", "fix": "Add the locations the campaign should reach. With none it shows in every country and territory."},
    "overlap": {"level": "low", "label": "Same place in two campaigns", "fix": "Check the two campaigns do not bid on the same searches. If they are for different services, it is fine."},
    "schedule": {"level": "low", "label": "Schedule differs from the other campaigns", "fix": "Check the ad schedule is on purpose. If the others are right, copy their schedule."},
    "device": {"level": "low", "label": "Devices differ from the other campaigns", "fix": "Check the device adjustments are on purpose. A device set to minus 100 percent never shows there."},
}
ORDER = ["tgt_conflict", "group_copy", "name_place", "copy_place", "kw_place", "no_target", "setting", "overlap", "device", "schedule"]
COUNTRY_TYPES = {"Country"}
DEVICE_WORD = {"MOBILE": "mobile", "DESKTOP": "desktop", "TABLET": "tablet"}
DAY_WORD = {"MONDAY": "Mon", "TUESDAY": "Tue", "WEDNESDAY": "Wed", "THURSDAY": "Thu", "FRIDAY": "Fri", "SATURDAY": "Sat", "SUNDAY": "Sun"}


def norm(s):
    """Lower case letters and digits with single spaces, so Dover_Emergency, Dover's and "dover," all hold the word dover."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split())


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def find_places(text, vocab):
    """[(norm place, start, end)] for every place of the vocabulary the text names, longest names first so York is not found inside New York. Positions are in the normalised text."""
    t = " " + norm(text) + " "
    taken, out = [], []
    for p in sorted(vocab, key=lambda x: (-len(x), x)):
        for m in re.finditer(r"(?<= )" + re.escape(p) + r"(?= )", t):
            span = (m.start(), m.end())
            if any(span[0] < b and a < span[1] for a, b in taken):
                continue
            taken.append(span)
            out.append((p, span[0] - 1, span[1] - 1))
    return out


def places_in(text, vocab):
    return sorted({p for p, _a, _b in find_places(text, vocab)})


def parts_of(canonical):
    """['quincy', 'massachusetts', 'united states'] from 'Quincy,Massachusetts,United States'."""
    return [norm(p) for p in str(canonical or "").split(",") if norm(p)]


def inside(canon_a, canon_b):
    """True when place A is B or lies inside B: Quincy,Massachusetts,United States inside Massachusetts,United States."""
    a, b = parts_of(canon_a), parts_of(canon_b)
    return bool(a) and bool(b) and len(a) >= len(b) and a[len(a) - len(b):] == b


def _display(vocab, p):
    return vocab.get(p) or p.title()


def build_vocab(locations, *, areas_by_campaign=None, saved=(), other_places=None, ignore=()):
    """{norm place: shown name} for everything this account names as a place: the names and parts of every targeted or excluded location, the saved places, the places of other accounts and the
    states. A place the team said to ignore, or shorter than SHORT_PLACE letters, is left out."""
    out = {}
    for s in STATES:
        out[norm(s)] = s
    for loc in locations:
        shown = [x.strip() for x in str(loc.get("canonical") or "").split(",") if x.strip()]
        if not shown and loc.get("name"):
            shown = [str(loc["name"])]
        if loc.get("city"):
            shown = [str(loc["city"])] + shown
        for s in shown:
            out.setdefault(norm(s), s)
    for s in saved:
        for piece in str(s).split(","):
            if norm(piece):
                out.setdefault(norm(piece), piece.strip())
    for s in (other_places or {}):
        for piece in str(s).split(","):
            if norm(piece):
                out.setdefault(norm(piece), piece.strip())
    skip = {norm(i) for i in ignore} | {"united states", "usa", "america"}
    return {k: v for k, v in out.items() if k and len(k) >= SHORT_PLACE and k not in skip}


def _campaign_places(campaign_id, locations):
    """(allowed, excluded, targets, shown): the norm places a campaign may name (what it targets, with the state and country of each), the ones it excludes, and the lists the page draws."""
    allowed, excluded, targets, banned = set(), set(), [], []
    for loc in locations:
        if str(loc.get("campaignId")) != str(campaign_id):
            continue
        names = parts_of(loc.get("canonical")) or ([norm(loc.get("name"))] if norm(loc.get("name")) else [])
        if loc.get("city"):
            names = [norm(loc["city"])] + names
        label = str(loc.get("name") or loc.get("city") or loc.get("canonical") or "")
        if loc.get("kind") == "proximity":
            label = "%s (%s)" % (label or "A radius", loc.get("radiusText") or "radius")
        if loc.get("negative"):
            excluded |= set(names)
            banned.append(label)
        else:
            allowed |= set(names)
            targets.append(label)
    return allowed, excluded, targets, banned


def _finding(rule, scope, key, title, detail, *, level=None, campaign=None, group=None, ad=None, places=(), evidence=(), extra=None):
    f = {"rule": rule, "level": level or RULES[rule]["level"], "scope": scope, "key": str(key), "title": title, "detail": detail, "campaignId": str((campaign or {}).get("id") or ""),
         "campaign": (campaign or {}).get("name") or "", "adGroupId": str((group or {}).get("id") or ""), "adGroup": (group or {}).get("name") or "", "adId": str((ad or {}).get("id") or ""),
         "places": list(places), "evidence": list(evidence)[:MAX_EVIDENCE], "fix": RULES[rule]["fix"]}
    if extra:
        f.update(extra)
    return f


def _hours_of(sched):
    return round(sum(max(0.0, float(s.get("end") or 0) - float(s.get("start") or 0)) for s in sched), 2)


def _window_text(sched):
    if not sched:
        return "all day, every day"
    return "%d %s, %g hours a week" % (len(sched), "window" if len(sched) == 1 else "windows", _hours_of(sched))


def _schedule_sig(sched):
    return tuple(sorted((str(s.get("day")), float(s.get("start") or 0), float(s.get("end") or 0)) for s in sched))


def _device_sig(devs):
    return tuple(sorted((str(d.get("device")), round(float(d.get("modifier") if d.get("modifier") is not None else 1.0), 2)) for d in devs if abs(float(d.get("modifier") if d.get("modifier") is not None else 1.0) - 1.0) > 1e-9))


def _device_text(sig):
    if not sig:
        return "no device adjustments"
    out = []
    for dev, mod in sig:
        word = DEVICE_WORD.get(dev, dev.lower())
        out.append("%s off" % word if mod <= 0 else "%s %+d%%" % (word, round((mod - 1) * 100)))
    return ", ".join(out)


def _drift(campaigns, schedules, devices):
    """schedule and device findings: a campaign whose signature is not the one at least PEER_SHARE of its peers (enabled campaigns of the same type, at least MIN_PEERS of them) share."""
    out = []
    by_channel = defaultdict(list)
    for c in campaigns:
        by_channel[c.get("channel") or ""].append(c)
    for channel, group in by_channel.items():
        if len(group) < MIN_PEERS:
            continue
        for rule, sig_of in (("schedule", lambda c: _schedule_sig([s for s in schedules if str(s.get("campaignId")) == str(c["id"])])),
                             ("device", lambda c: _device_sig([d for d in devices if str(d.get("campaignId")) == str(c["id"])]))):
            sigs = {str(c["id"]): sig_of(c) for c in group}
            top, n = Counter(sigs.values()).most_common(1)[0]
            if n < 2 or n / len(group) < PEER_SHARE:
                continue
            for c in group:
                sig = sigs[str(c["id"])]
                if sig == top:
                    continue
                if rule == "schedule":
                    mine = [s for s in schedules if str(s.get("campaignId")) == str(c["id"])]
                    peer_ids = [k for k, v in sigs.items() if v == top]
                    peers = [s for s in schedules if str(s.get("campaignId")) == peer_ids[0]]
                    my_h = _hours_of(mine) if mine else 168.0
                    top_h = _hours_of(peers) if peers else 168.0
                    level = "medium" if my_h < top_h * 0.5 else "low"
                    detail = "%s runs %s; %d of the other %d %s campaigns run %s." % (c["name"], _window_text(mine), n, len(group) - 1, _channel_word(channel), _window_text(peers))
                    title = "%s runs a different schedule" % c["name"]
                else:
                    off_mine = {d for d, m in sig if m <= 0}
                    off_top = {d for d, m in top if m <= 0}
                    level = "medium" if off_mine - off_top else "low"
                    detail = "%s has %s; %d of %d %s campaigns have %s." % (c["name"], _device_text(sig), n, len(group), _channel_word(channel), _device_text(top))
                    title = "%s has different device adjustments" % c["name"]
                out.append(_finding(rule, "campaign", "%s:%s" % (rule, c["id"]), title, detail, level=level, campaign=c))
    return out


def _channel_word(channel):
    return {"SEARCH": "Search", "PERFORMANCE_MAX": "Performance Max", "DISPLAY": "Display", "SHOPPING": "Shopping", "DEMAND_GEN": "Demand Gen", "VIDEO": "Video"}.get(channel, "other")


def analyse(account, *, config=None, other_places=None, platform="google"):
    """The account's answer from what was read. `config` is what the team saved ({areas, ignore, acceptPresence}); `other_places` {place: account name} from the other accounts' lists."""
    config = config or {}
    campaigns = [c for c in account.get("campaigns") or [] if c.get("id")]
    locations = account.get("locations") or []
    ads, keywords, groups = account.get("ads") or [], account.get("keywords") or [], account.get("groups") or []
    areas = list(config.get("areas") or [])
    vocab = build_vocab(locations, saved=areas, other_places=other_places, ignore=config.get("ignore") or [])
    area_norms = {norm(p) for a in areas for p in str(a).split(",") if norm(p)}
    cmap = {str(c["id"]): c for c in campaigns}
    per = {}
    for c in campaigns:
        allowed, excluded, targets, banned = _campaign_places(c["id"], locations)
        per[str(c["id"])] = {"allowed": allowed | area_norms, "excluded": excluded, "targets": targets, "banned": banned}
    targeted_elsewhere = defaultdict(set)                       # place -> ids of the campaigns that target it
    for cid, p in per.items():
        for n in p["allowed"] - area_norms:
            targeted_elsewhere[n].add(cid)
    findings = []

    for c in campaigns:
        cid, p = str(c["id"]), per[str(c["id"])]
        mine = [x for x in locations if str(x.get("campaignId")) == cid]
        inc = [x for x in mine if not x.get("negative")]
        exc = [x for x in mine if x.get("negative")]
        if not inc and platform == "google":
            findings.append(_finding("no_target", "campaign", "no_target:%s" % cid, "%s has no location set" % c["name"],
                                     "%s targets no location, so it shows in every country and territory." % c["name"], campaign=c))
        clash = []
        for a in inc:
            for b in exc:
                if a.get("kind") == "proximity" or b.get("kind") == "proximity":
                    continue
                if inside(a.get("canonical") or a.get("name"), b.get("canonical") or b.get("name")):
                    clash.append((a, b))
        if clash:
            a, b = clash[0]
            same = norm(a.get("canonical") or a.get("name")) == norm(b.get("canonical") or b.get("name"))
            findings.append(_finding("tgt_conflict", "campaign", "tgt_conflict:%s" % cid, "%s targets a place it also excludes" % c["name"],
                                     "%s is %s %s, which this campaign excludes%s." % (a.get("name"), "the same place as" if same else "inside", b.get("name"), (" (and %d more)" % (len(clash) - 1)) if len(clash) > 1 else ""),
                                     campaign=c, places=[a.get("name") or "", b.get("name") or ""]))
        positive = c.get("positive") or ""
        if positive == "PRESENCE_OR_INTEREST":
            narrow = [x for x in inc if x.get("kind") == "proximity" or x.get("targetType") not in COUNTRY_TYPES]
            if config.get("acceptPresence"):
                level = "low"
            else:
                level = "medium" if narrow else "low"
            if narrow or inc:
                where = "the places it targets" if narrow else "its country"
                findings.append(_finding("setting", "campaign", "setting:%s" % cid, "%s shows to people interested in %s" % (c["name"], where),
                                         "The location option is Presence or interest, so people who only search about %s see the ads too, wherever they are.%s" % (
                                             where, " The team accepted this for the account." if config.get("acceptPresence") else ""), level=level, campaign=c))
        if c.get("negative") == "PRESENCE" and exc:
            findings.append(_finding("setting", "campaign", "setting_excl:%s" % cid, "%s excludes only people in the excluded places" % c["name"],
                                     "The exclusion option is Presence, so people interested in an excluded place can still see the ads.", level="low", campaign=c))
        for n in find_places(c.get("name"), vocab):
            place = n[0]
            if place in p["allowed"]:
                continue
            level = "high" if (place in p["excluded"] or targeted_elsewhere.get(place)) else "medium"
            why = "it excludes it" if place in p["excluded"] else ("it is targeted by %s" % _names([cmap[i]["name"] for i in sorted(targeted_elsewhere[place])]) if targeted_elsewhere.get(place) else "it targets %s" % _short(p["targets"]))
            findings.append(_finding("name_place", "campaign", "name_place:%s:%s" % (cid, place), "%s is named for %s but does not target it" % (c["name"], _display(vocab, place)),
                                     "The campaign's name says %s, and %s." % (_display(vocab, place), why), level=level, campaign=c, places=[_display(vocab, place)]))

    for g in groups:
        c = cmap.get(str(g.get("campaignId")))
        if not c:
            continue
        p = per[str(c["id"])]
        for place, _a, _b in find_places(g.get("name"), vocab):
            if place in p["allowed"]:
                continue
            level = "high" if (place in p["excluded"] or targeted_elsewhere.get(place)) else "medium"
            where = "does not target it" if place not in p["excluded"] else "excludes it"
            findings.append(_finding("name_place", "adgroup", "name_place:%s:%s" % (g["id"], place), "%s is named for %s but %s %s" % (g.get("name"), _display(vocab, place), c["name"], where),
                                     "The ad group is called %s, and its campaign %s %s." % (g.get("name"), c["name"], "excludes it" if place in p["excluded"] else "targets %s" % _short(p["targets"])),
                                     level=level, campaign=c, group=g, places=[_display(vocab, place)]))

    for ad in ads:
        c = cmap.get(str(ad.get("campaignId")))
        if not c:
            continue
        p = per[str(c["id"])]
        group = {"id": ad.get("adGroupId"), "name": ad.get("adGroup")}
        named = {}                                                # place -> [evidence]
        for t in ad.get("texts") or []:
            for place in places_in(t["text"], vocab):
                named.setdefault(place, []).append({"slot": t["slot"], "text": t["text"]})
        in_group = set(places_in(ad.get("adGroup"), vocab))
        if in_group and named and not (in_group & set(named)):
            ev = [e for v in named.values() for e in v]
            findings.append(_finding("group_copy", "ad", "group_copy:%s" % ad["id"], "%s says %s but its ad says %s" % (ad.get("adGroup"), _names([_display(vocab, x) for x in sorted(in_group)]),
                                                                                                                      _names([_display(vocab, x) for x in sorted(named)])),
                                     "The ad group is called %s and the ad names %s." % (ad.get("adGroup"), _names([_display(vocab, x) for x in sorted(named)])),
                                     campaign=c, group=group, ad=ad, places=[_display(vocab, x) for x in sorted(in_group | set(named))], evidence=ev))
            continue
        bad = {pl: ev for pl, ev in named.items() if pl not in p["allowed"]}
        if bad:
            gone = [pl for pl in bad if pl in p["excluded"]]
            other = [pl for pl in bad if targeted_elsewhere.get(pl)]
            level = "high" if gone else "medium"
            ev = [e for v in bad.values() for e in v]
            findings.append(_finding("copy_place", "ad", "copy_place:%s" % ad["id"], "An ad of %s names %s" % (c["name"], _names([_display(vocab, x) for x in sorted(bad)])),
                                     "%s does not target %s%s." % (c["name"], _names([_display(vocab, x) for x in sorted(bad)]),
                                                                   ", and it is excluded" if gone else (" (another campaign does)" if other else "")),
                                     level=level, campaign=c, group=group, ad=ad, places=[_display(vocab, x) for x in sorted(bad)], evidence=ev))

    by_group = defaultdict(list)
    for k in keywords:
        c = cmap.get(str(k.get("campaignId")))
        if not c:
            continue
        p = per[str(c["id"])]
        bad = [pl for pl in places_in(k.get("text"), vocab) if pl not in p["allowed"]]
        if bad:
            by_group[(str(c["id"]), str(k.get("adGroupId")), k.get("adGroup"))].append((k.get("text"), bad))
    for (cid, gid, gname), rows in by_group.items():
        c = cmap[cid]
        places = sorted({_display(vocab, x) for _t, b in rows for x in b})
        findings.append(_finding("kw_place", "adgroup", "kw_place:%s" % gid, "%s of %s name %s the campaign does not target" % (plural(len(rows), "keyword"), gname or c["name"], _names(places)),
                                 "%s name %s, and %s targets %s." % (plural(len(rows), "keyword"), _names(places), c["name"], _short(per[cid]["targets"])), campaign=c, group={"id": gid, "name": gname},
                                 places=places, evidence=[{"slot": "keyword", "text": t} for t, _b in rows], extra={"count": len(rows)}))

    search = [c for c in campaigns if c.get("channel") == "SEARCH"]
    shared = defaultdict(list)
    for c in search:
        for loc in locations:
            if str(loc.get("campaignId")) == str(c["id"]) and not loc.get("negative") and loc.get("kind") != "proximity" and loc.get("targetType") not in COUNTRY_TYPES and norm(loc.get("name")):
                shared[norm(loc.get("name"))].append(c)
    for place, cs in shared.items():
        uniq = {str(c["id"]): c for c in cs}
        if len(uniq) > 1:
            names = [c["name"] for c in uniq.values()]
            first = sorted(uniq.values(), key=lambda c: c["name"])[0]
            findings.append(_finding("overlap", "account", "overlap:%s" % place, "%s is targeted by %d Search campaigns" % (_display(vocab, place), len(uniq)),
                                     "%s all target %s." % (_names(sorted(names)), _display(vocab, place)), campaign=first, places=[_display(vocab, place)], extra={"campaigns": sorted(names)}))

    findings += _drift(campaigns, account.get("schedules") or [], account.get("devices") or [])

    items = []
    by_c = defaultdict(list)
    for f in findings:
        by_c[f["campaignId"]].append(f)
    for c in campaigns:
        fs = sorted(by_c.get(str(c["id"]), []), key=lambda f: (-LEVEL_RANK[f["level"]], ORDER.index(f["rule"]), f["key"]))
        p = per[str(c["id"])]
        items.append({"id": str(c["id"]), "name": c["name"], "channel": c.get("channel") or "", "positive": c.get("positive") or "", "negative": c.get("negative") or "",
                      "targets": p["targets"], "excluded": p["banned"], "level": fs[0]["level"] if fs else "", "findings": fs})
    items.sort(key=lambda i: (-LEVEL_RANK.get(i["level"], 0), -len(i["findings"]), i["name"].lower()))
    counts = Counter(f["level"] for f in findings)
    rules_seen = Counter(f["rule"] for f in findings)
    by_rule = [{"rule": r, "label": RULES[r]["label"], "level": max((f["level"] for f in findings if f["rule"] == r), key=lambda x: LEVEL_RANK[x]), "findings": rules_seen[r], "fix": RULES[r]["fix"]}
               for r in ORDER if rules_seen[r]]
    status = "fix" if counts["high"] or counts["medium"] else ("check" if counts["low"] else "clean")
    return {"status": status, "campaigns": len(campaigns), "ads": len(ads), "keywords": len(keywords), "places": len(vocab),
            "counts": {k: counts.get(k, 0) for k in ("high", "medium", "low")}, "findingCount": len(findings), "byRule": by_rule, "items": items}


def _names(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _short(targets):
    t = [x for x in targets if x]
    if not t:
        return "no place"
    return _names(t[:3]) + (" and %d more" % (len(t) - 3) if len(t) > 3 else "")
