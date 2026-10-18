"""Paid Marketing Agents: Test & Experiment Tracker, one read from the platforms read.

A Google Ads read (tracker/paid_exp_read.py) and a Meta read (tracker/paid_exp_meta_read.py) have the same shape. This puts them together: the experiments of both, each marked with its
platform, the ad groups and ad sets that run two or more live ads (the `MAX_GROUPS` that spent most of the two together), and the count of such tests. Either read may be None.

What the merged read says about each platform: `platforms` lists the ones that were read; Meta's `ok` flags are named "meta:<part>" and its warnings begin "Meta: " so nothing of
Google's is mistaken for Meta's.
"""
from __future__ import annotations

from . import paid_exp_read as rd


def merge(google, meta, keep=rd.MAX_GROUPS):
    """{experiments, adGroups, adTests, ok, warnings, today, platforms} from a Google read and a Meta read, either of which may be None."""
    if meta is None:
        return dict(google, platforms=["google"])
    ok_meta = {"meta:" + k: v for k, v in meta["ok"].items()}
    warn_meta = ["Meta: " + w for w in meta["warnings"]]
    if google is None:
        return {"experiments": [dict(e, platform="meta") for e in meta["experiments"]], "adGroups": [dict(g, platform="meta") for g in meta["adGroups"]], "adTests": meta["adTests"], "ok": ok_meta, "warnings": warn_meta, "today": meta["today"], "platforms": ["meta"]}
    exps = [dict(e, platform="google") for e in google["experiments"]] + [dict(e, platform="meta") for e in meta["experiments"]]
    exps.sort(key=lambda e: (e["start"] or "", e["name"]), reverse=True)
    groups = [dict(g, platform="google") for g in google["adGroups"]] + [dict(g, platform="meta") for g in meta["adGroups"]]
    groups.sort(key=lambda g: (-g["cost"], g["id"]))
    return {"experiments": exps, "adGroups": groups[:keep], "adTests": google["adTests"] + meta["adTests"], "ok": dict(google["ok"], **ok_meta), "warnings": list(google["warnings"]) + warn_meta,
            "today": google["today"], "platforms": ["google", "meta"]}
