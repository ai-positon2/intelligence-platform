"""Keyword Research: how to act on the result and which negative is in the way (tracker/paid_keywords_plans.py).

The worth of adding keywords is the size of the market (the searches and the top-of-page bid Keyword Planner gives) and never a forecast; these tests pin the arithmetic against hand
figures and that no sentence promises a result. A negative in the way is named with the keywords it stops and what to do about it, by its match type: an exact negative is simply removed,
a broad or phrase one is narrowed, never removed without a look at what it was stopping.
"""
import json

import pytest

from tests.test_keyword_research_js import rec  # noqa: F401  (a finished build, made by the real job on fakes)
from tracker import paid_keywords_plans as pl

EM, EN = chr(0x2014), chr(0x2013)


def row(kw, avg=1000, lo=2.0, hi=4.0, include=True, fit="yes", neg=None):
    return {"kw": kw, "avg": avg, "lo": lo, "hi": hi, "include": include, "fit": fit, "neg": neg}


def blocked(kw, by, avg=1000, fit="yes"):
    return row(kw, avg=avg, include=False, fit=fit, neg={"scope": "some", "by": by, "campaigns": sorted({c for b in by for c in b["campaigns"]})})


def hit(kw, match="Phrase", campaigns=("A", "B")):
    return {"kw": kw, "match": match, "campaigns": list(campaigns)}


def text(p):
    return " ".join(p["steps"])


# ── Adding ────────────────────────────────────────────────────────────────

def test_adding_is_worth_the_size_of_the_market_at_the_plannners_bids_weighted_by_searches():
    p = pl.add_plan([row("a", 1000, 2.0, 4.0), row("b", 3000, 6.0, 8.0), row("c", 500, include=False)], {}, "USD")
    # 1000 and 3000 searches: the bid average is weighted by them, (2*1000+6*3000)/4000 = 5 and (4*1000+8*3000)/4000 = 7
    assert p["worth"] == ("The 2 keywords to add are searched about 4,000 times a month in all, and Keyword Planner puts the top-of-page bid for them at about $5.00 to $7.00 a click. "
                          "That is the size of the market, not a forecast of what the account would win: how much of it you reach depends on bids, quality and budget.")
    t = text(p)
    assert 'Download "Keywords to add, for Google Ads Editor (CSV)"' in p["steps"][0] and "Account, then Import, then From file" in t and "arrive Paused" in t and "responsive search ad" in t and "keywords with no ad serve nothing" in t


def test_the_bid_range_is_left_out_when_planner_gave_none_and_the_currency_follows_the_account():
    p = pl.add_plan([row("a", 1000, None, None)], {}, "USD")
    assert "top-of-page bid" not in p["worth"] and p["worth"].startswith("The 1 keyword to add is searched about 1,000 times a month in all.")
    eu = pl.add_plan([row("a", 1000, 1.5, 2.5)], {}, "EUR")["worth"]
    assert "€1.50 to €2.50" in eu and "1.50 CHF to 2.50 CHF" in pl.add_plan([row("a", 1000, 1.5, 2.5)], {}, "CHF")["worth"]


def test_nothing_is_added_when_none_are_marked_or_the_account_was_not_read():
    assert pl.add_plan([row("a", include=False)], {}) is None and pl.add_plan([], {}) is None
    assert pl.add_plan([row("a")], {}, "USD", unread=True) is None


# ── Negatives to add ──────────────────────────────────────────────────────

def test_negatives_say_where_to_put_them_and_what_was_already_checked():
    p = pl.negatives_plan([{"kw": "free", "match": "phrase"}])
    t = text(p)
    assert "Copy as Google Ads negatives" in p["steps"][0] and "[exact] or \"phrase\"" in p["steps"][0] and "account level" in p["steps"][1] and "Account settings" in p["steps"][1]
    assert "shared list (Tools, then Shared library, then Negative keyword lists)" in p["steps"][2] and "left out any negative that would block a keyword" in p["steps"][3] and p["worth"] is None
    assert pl.negatives_plan([]) is None and pl.negatives_plan(None) is None


# ── The negative in the way ───────────────────────────────────────────────

def test_the_negative_that_stops_the_most_searches_comes_first_with_the_keywords_it_stops():
    rows = [blocked("free dental implants", [hit("free")], 1000), blocked("free whitening", [hit("free")], 500), blocked("cheap braces", [hit("cheap", "Broad", ["A"])], 800),
            blocked("maybe thing", [hit("free")], 9000, fit="maybe"), row("open keyword"), row("dangling", neg={"scope": "", "by": [], "campaigns": []})]
    out = pl.unblock_plans(rows)
    assert [(u["negative"], u["count"], u["searches"]) for u in out] == [("free", 2, 1500), ("cheap", 1, 800)]          # a keyword judged only maybe is not one anyone wants unblocked
    assert out[0]["blocks"] == ["free dental implants", "free whitening"] and out[0]["campaigns"] == ["A", "B"]
    assert out[0]["worth"] == 'It stops 2 keywords judged a fit, searched about 1,500 times a month in all ("free dental implants", "free whitening").' and out[1]["worth"].startswith("It stops 1 keyword judged")


def test_an_exact_negative_is_removed_and_a_broad_or_phrase_one_is_narrowed_never_just_removed():
    exact = pl.unblock_plans([blocked("dental implants", [hit("dental implants", "Exact")])])[0]
    assert "stops only this one search, so if the search is wanted, remove the negative" in text(exact) and "Narrow it instead" not in text(exact)
    phrase = pl.unblock_plans([blocked("a free x", [hit("free", "Phrase")])])[0]
    broad = pl.unblock_plans([blocked("a free x", [hit("free", "Broad")])])[0]
    assert "stops every search holding its words together" in text(phrase) and "stops every search holding all of its words" in text(broad)
    assert "Narrow it instead: remove it, then add an exact negative" in text(phrase) and "Search terms report" in text(phrase)
    assert 'find "free" (phrase match)' in phrase["steps"][0] and "Shared library" in phrase["steps"][0]


def test_the_campaigns_are_named_and_an_account_wide_negative_is_said_to_be_one():
    named = pl.unblock_plans([blocked("a free x", [hit("free", "Phrase", ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"])])])[0]
    assert 'the campaigns "Alpha", "Beta", "Delta" and 2 more' in named["steps"][0]
    none = pl.unblock_plans([blocked("a free x", [hit("free", "Phrase", [])])])[0]
    assert "an account-level negative or a shared list" in none["steps"][0]


def test_one_keyword_blocked_by_two_negatives_is_counted_under_each_and_the_list_is_capped():
    both = pl.unblock_plans([blocked("free cheap x", [hit("free"), hit("cheap")], 700)])
    assert sorted(u["negative"] for u in both) == ["cheap", "free"] and all(u["searches"] == 700 for u in both)
    many = pl.unblock_plans([blocked("kw %d" % i, [hit("neg%d" % i)], 100 + i) for i in range(15)])
    assert len(many) == pl.UNBLOCK_SHOWN and many[0]["negative"] == "neg14"
    big = pl.unblock_plans([blocked("kw %d" % i, [hit("free")], 100) for i in range(8)])[0]
    assert len(big["blocks"]) == pl.BLOCKS_SHOWN and big["count"] == 8 and 'and 3 more' in big["worth"]


# ── Through the build ─────────────────────────────────────────────────────

def test_a_finished_build_carries_its_plans(rec):
    plans = rec["plan"]["plans"]
    assert set(plans) == {"add", "negatives", "unblock"}
    assert plans["add"]["worth"].startswith("The ") and "Account, then Import, then From file" in text(plans["add"]) and plans["negatives"]["steps"]
    assert isinstance(plans["unblock"], list)


def test_a_build_with_the_account_unread_recommends_no_keyword_and_so_has_no_how_to_add_them():
    from tests import keyword_fakes as K  # noqa: F401
    assert pl.build([row("a")], {}, {"negatives": []}, "USD", True)["add"] is None
    assert pl.build([row("a")], {}, {"negatives": [{"kw": "x", "match": "exact"}]}, "USD", False)["negatives"] is not None


def test_no_plan_uses_an_em_or_en_dash_or_leaves_a_placeholder():
    rows = [row("a"), blocked("free x", [hit("free", "Phrase")]), blocked("cheap y", [hit("cheap", "Exact", [])]), blocked("z", [hit("w", "Broad", ["A"])])]
    plans = pl.build(rows, {}, {"negatives": [{"kw": "free", "match": "phrase"}]}, "USD", False)
    blob = json.dumps(plans, ensure_ascii=False)
    assert EM not in blob and EN not in blob and "None" not in blob and "%s" not in blob and "{" not in blob.replace('{"', "").replace("{}", "")
