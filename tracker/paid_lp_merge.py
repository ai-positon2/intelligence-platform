"""Paid Marketing Agents: Landing Page & Relevancy Check, one read from the platforms read.

A Google Ads read (tracker/paid_lp_read.py) and a Meta read (tracker/paid_lp_meta_read.py) have the same shape. This puts them together: a page both platforms
send people to is one page (the same host and path), with the two platforms' spend, clicks, results and device split added and the ad groups and ad sets of
both listed under it, so it is fetched once and held to the checks of every platform that sends people to it. Either read may be None (one platform).

What the merged read says about each platform: `platforms` lists the ones that were read; Meta's `ok` flags are named "meta:<part>" and its warnings begin "Meta: " so
nothing of Google's is mistaken for Meta's; `off` is what Meta's ads that reach no web page of the client's took.
"""
from __future__ import annotations

from . import paid_lp_read as lp


def _add_devices(into, other):
    for dev, d in other.items():
        t = into.setdefault(dev, {"clicks": 0, "cost": 0.0, "conversions": 0.0})
        t["clicks"] += d["clicks"]
        t["cost"] = round(t["cost"] + d["cost"], 2)
        t["conversions"] = round(t["conversions"] + d["conversions"], 4)


def merge(google, meta, keep=lp.KEEP_PAGES):
    """{pages, pageCount, pageCost, adGroups, ok, warnings, platforms[, off]} from a Google read and a Meta read, either of which may be None. The busiest `keep` pages
    of the two together are kept, with the ad groups and ad sets that reach them."""
    ok_meta = {"meta:" + k: v for k, v in (meta or {}).get("ok", {}).items()}
    warn_meta = ["Meta: " + w for w in (meta or {}).get("warnings", [])]
    if meta is None:
        return dict(google, platforms=["google"])
    if google is None:
        return {"pages": meta["pages"], "pageCount": meta["pageCount"], "pageCost": meta["pageCost"], "adGroups": meta["adGroups"], "ok": ok_meta,
                "warnings": warn_meta, "platforms": ["meta"], "off": meta["off"]}
    groups = dict(google["adGroups"])
    groups.update(meta["adGroups"])
    pages = {p["id"]: dict(p, platforms=["google"], adGroups=list(p["adGroups"]), devices={k: dict(v) for k, v in p["devices"].items()}) for p in google["pages"]}
    for m in meta["pages"]:
        g = pages.get(m["id"])
        if g is None:
            pages[m["id"]] = dict(m, platforms=["meta"], adGroups=list(m["adGroups"]), devices={k: dict(v) for k, v in m["devices"].items()})
            continue
        g["platforms"] = ["google", "meta"]
        g["impressions"] += m["impressions"]
        g["clicks"] += m["clicks"]
        if m["cost"] > g["cost"]:                                  # the address shown is the one the bigger spender sends people to
            g["url"], g["name"] = m["url"], m["name"]
        g["cost"] = round(g["cost"] + m["cost"], 2)
        g["conversions"] = round(g["conversions"] + m["conversions"], 4)
        g["value"] = round(g["value"] + m["value"], 2)
        g["templated"] = g["templated"] or m["templated"]
        g["adGroups"] += m["adGroups"]
        _add_devices(g["devices"], m["devices"])
    rows = sorted(pages.values(), key=lambda p: (-p["cost"], p["id"]))
    kept = rows[:keep]
    keep_ids = {p["id"] for p in kept}
    used = {}
    for p in kept:
        p["adGroups"].sort(key=lambda gid: (-groups[gid]["pages"].get(p["id"], 0.0), -groups[gid]["cost"], gid))
        for gid in p["adGroups"]:
            used[gid] = groups[gid]
    for g in used.values():
        g["pages"] = {pid: c for pid, c in g["pages"].items() if pid in keep_ids}
    unseen = (google["pageCount"] - len(google["pages"])) + (meta["pageCount"] - len(meta["pages"]))        # pages each platform read but did not keep
    return {"pages": kept, "pageCount": len(rows) + unseen, "pageCost": round(google["pageCost"] + meta["pageCost"], 2), "adGroups": used,
            "ok": dict(google["ok"], **ok_meta), "warnings": list(google["warnings"]) + warn_meta, "platforms": ["google", "meta"], "off": meta["off"]}
