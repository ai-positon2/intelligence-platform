"""Paid Marketing Agents: Ad Copy Checker, what the rules say about every ad of one account (no network, no clock but the one passed in).

An ad here is whatever carries text people read: a Search ad (responsive or the old expanded kind), a Display ad, the text assets of a Performance Max asset group, a sitelink, a callout, a
structured snippet, or a Meta ad. All of them are the same shape:

  {id, platform, kind, campaignId, campaign, adGroupId, adGroup, url, texts: [{slot, text}]}

An ad that has findings is returned without its texts (every finding carries the slot and the text it is about, so the page can draw the lines that matter) and with the number of lines it holds:
an account with thousands of ads must not send every line of every ad to the browser.

This turns them into the account's answer: each ad with its findings, the rules ranked by how many ads they touch, the phone numbers the ads carry, the brand written two ways, and the
words the dictionary does not know that the account uses over and over (listed quietly, never alerted). The rules themselves are tracker/paid_copycheck_rules.py; what the team told us
(its phone numbers, places, brand spellings and words never to doubt) is passed in.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from urllib.parse import urlparse

from . import paid_copycheck_rules as rules

MAX_ADS = 6000                    # ads checked in one account; a read past it says so
SHOWN_EXAMPLES = 3
MAX_VOCAB = 25


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def fingerprint(account_id, ad_id):
    return "copycheck:%s:%s" % (account_id, ad_id)


def domain_words(urls):
    """The words of the web addresses the ads point at (smileco.com gives 'smileco'): the brand is spelt that way there and is never doubted."""
    out = set()
    for u in urls:
        host = urlparse(u if "://" in str(u) else "http://" + str(u)).netloc.lower().split(":")[0]
        for label in host.split("."):
            if label and label not in ("www", "com", "net", "org", "co", "us", "io", "health", "dental", "care"):
                out.add(label)
                out |= {p for p in re.split(r"[-_]", label) if len(p) >= 3}
    return out


def _own_words(ads):
    """{word: number of ads it is in}, over every word of every text: what the account says, used to choose between two corrections."""
    own = Counter()
    for ad in ads:
        seen = set()
        for t in ad["texts"]:
            seen |= {w.lower() for w, _a, _b in rules.words_of(t["text"]) if len(w) >= 3}
        own.update(seen)
    return own


def brand_forms(ads, brands):
    """[{brand, surface, ad, slot, text, start, end}] for every place a text writes one of the brands (with any spacing, hyphen or case), found by joining one to three words."""
    out = []
    keyed = [(b, rules.norm_key(b)) for b in brands if rules.norm_key(b)]
    if not keyed:
        return out
    for ad in ads:
        for t in ad["texts"]:
            ws = rules.words_of(t["text"])
            for i in range(len(ws)):
                for n in (1, 2, 3):
                    if i + n > len(ws):
                        break
                    joined = "".join(w for w, _a, _b in ws[i:i + n]).lower()
                    for b, key in keyed:
                        if joined == key:
                            a, z = ws[i][1], ws[i + n - 1][2]
                            out.append({"brand": b, "surface": t["text"][a:z], "ad": ad["id"], "slot": t["slot"], "text": t["text"], "start": a, "end": z})
    return out


def _brand_findings(forms, saved):
    """{ad id: [finding]} for the brand written in a way the team did not save, or (with nothing saved) written two ways in one account."""
    out = defaultdict(list)
    by = defaultdict(Counter)
    for f in forms:
        by[f["brand"]][f["surface"]] += 1
    for f in forms:
        b = f["brand"]
        if b in saved:
            if f["surface"] != b:
                out[f["ad"]].append(dict(rules._f("brand", f["text"], f["start"], f["end"], "The brand is written \"%s\" here; the way saved for it is \"%s\"." % (f["surface"], b), b), slot=f["slot"], text=f["text"]))
        else:
            forms_of = by[b]
            if len(forms_of) >= 2:
                top = forms_of.most_common(1)[0][0]
                if f["surface"] != top:
                    out[f["ad"]].append(dict(rules._f("brand", f["text"], f["start"], f["end"], "The brand is written \"%s\" here and \"%s\" in %s of the other places." % (f["surface"], top, forms_of[top]), top, level="low"),
                                             slot=f["slot"], text=f["text"]))
    return out


def phone_summary(ads):
    """[{number, ads}] the phone numbers in the texts, written the way they first appear, the most used first."""
    seen, shown = defaultdict(set), {}
    for ad in ads:
        for t in ad["texts"]:
            for m in rules._PHONE.finditer(rules.mask(t["text"])):
                d = rules.phone_digits(m.group(0))
                if d:
                    seen[d].add(ad["id"])
                    shown.setdefault(d, m.group(0).strip())
    out = [{"number": shown[d], "digits": d, "ads": len(ids)} for d, ids in seen.items()]
    out.sort(key=lambda p: (-p["ads"], p["digits"]))
    return out


def _level_key(level):
    return {"high": 0, "medium": 1, "low": 2, "": 3}[level]


def analyse(ads, *, account, config=None, brand_terms=(), speller=None, ages=None, now=None, other_places=None, allowed_extra=()):
    """The account's answer. `config` is what the team saved for it ({phones, places, brand, allow}); `brand_terms` are the words the Brand Spend Alert saved for it."""
    config = config or {}
    truncated = len(ads) > MAX_ADS
    ads = ads[:MAX_ADS]
    brands = list(dict.fromkeys([b for b in (config.get("brand") or []) if str(b).strip()] + [b for b in brand_terms if str(b).strip()]))
    saved_brands = set(config.get("brand") or [])
    urls = [ad.get("url") for ad in ads if ad.get("url")]
    account_words = {w.lower() for w in re.findall(r"[A-Za-z']{3,}", str(account.get("name") or ""))} | domain_words(urls)
    ctx = rules.context(allowed=list(config.get("allow") or []) + list(allowed_extra), brands=brands, phones=config.get("phones") or [], places=config.get("places") or [],
                        other_places=other_places, speller=speller, own=_own_words(ads), account_words=account_words)
    per_ad = {ad["id"]: rules.check_ad(ad, ctx) for ad in ads}
    forms = brand_forms(ads, brands)
    for ad_id, fs in _brand_findings(forms, saved_brands).items():
        per_ad[ad_id] = per_ad[ad_id] + fs
    # a word the dictionary does not know that the account spells the same way in many ads is its own word: say so quietly instead of calling it a typo
    spread = defaultdict(set)
    for ad_id, fs in per_ad.items():
        for f in fs:
            if f["rule"] == "spelling":
                spread[f["excerpt"].lower()].add(ad_id)
    vocab = {w: ids for w, ids in spread.items() if len(ids) >= rules.VOCAB_ADS}
    for ad_id, fs in per_ad.items():
        for f in fs:
            if f["rule"] == "spelling" and f["excerpt"].lower() in vocab:
                n = len(vocab[f["excerpt"].lower()])
                f["rule"], f["level"] = "vocab", "low"
                f["message"] = "\"%s\" is not in the dictionary and is spelt that way in %d ads: if it is right, add it to the allowed words." % (f["excerpt"], n)
    items, rule_ads, rule_hits, examples = [], defaultdict(set), Counter(), defaultdict(list)
    for ad in ads:
        fs = per_ad[ad["id"]]
        if not fs:
            continue
        fs = sorted(fs, key=lambda f: (_level_key(f["level"]), rules.ORDER.index(f["rule"]), f["start"]))
        item = {"id": ad["id"], "platform": ad.get("platform") or "google", "kind": ad.get("kind") or "ad", "campaignId": ad.get("campaignId") or "", "campaign": ad.get("campaign") or "",
                "adGroupId": ad.get("adGroupId") or "", "adGroup": ad.get("adGroup") or "", "url": ad.get("url") or "", "level": rules.level_of(fs), "lineCount": len(ad["texts"]), "findings": fs,
                "fingerprint": fingerprint(account["id"], ad["id"])}
        a = (ages or {}).get(item["fingerprint"])
        if a and now is not None:
            from . import paid_monitor as mon
            item["ageDays"], item["reopened"] = mon.age_days(a["firstSeen"], now), a.get("reopened") or 0
        items.append(item)
        for f in fs:
            rule_ads[f["rule"]].add(ad["id"])
            rule_hits[f["rule"]] += 1
            if len(examples[f["rule"]]) < SHOWN_EXAMPLES:
                examples[f["rule"]].append({"ad": ad["id"], "text": f["text"], "excerpt": f["excerpt"], "message": f["message"]})
    items.sort(key=lambda i: (_level_key(i["level"]), -len(i["findings"]), i["campaign"].lower(), i["adGroup"].lower(), i["id"]))
    by_rule = [{"rule": k, "label": rules.RULES[k]["label"], "level": rules.RULES[k]["level"], "fix": rules.RULES[k]["fix"], "findings": rule_hits[k], "ads": len(rule_ads[k]), "examples": examples[k]}
               for k in rules.ORDER if rule_hits[k]]
    by_rule.sort(key=lambda r: (_level_key(r["level"]), -r["ads"]))
    phones = phone_summary(ads)
    counts = Counter(i["level"] for i in items)
    status = "fix" if counts["high"] or counts["medium"] else "check" if counts["low"] else "clean"
    return {"ads": len(ads), "clean": len(ads) - len(items), "counts": {lv: counts[lv] for lv in ("high", "medium", "low")}, "findingCount": sum(rule_hits.values()), "status": status,
            "byRule": by_rule, "items": items, "phones": phones, "phonesSaved": len(ctx["phones"]), "placesSaved": len(config.get("places") or []), "brandsRead": [{"brand": b, "forms": dict(Counter(f["surface"] for f in forms if f["brand"] == b))}
                                                                                                                                                for b in brands if any(f["brand"] == b for f in forms)],
            "vocab": sorted(({"word": w, "ads": len(ids)} for w, ids in vocab.items()), key=lambda v: (-v["ads"], v["word"]))[:MAX_VOCAB], "spellingChecked": bool(speller and speller.ready),
            "truncated": truncated}
