"""Cross-Channel & Attribution Analysis: how each platform counts a conversion, and what makes two platforms' numbers incomparable."""
import pytest

from tracker import paid_xchannel_settings as st


def action(i, name, *, category="PURCHASE", primary=True, included=True, status="ENABLED", click=30, view=1, model="GOOGLE_SEARCH_ATTRIBUTION_DATA_DRIVEN", conv=10.0):
    return {"id": str(i), "name": name, "status": status, "type": "WEBPAGE", "category": category, "primary": primary, "counting": "ONE_PER_CLICK", "attribution": model,
            "defaultValue": 0.0, "alwaysDefault": False, "clickWindow": click, "viewWindow": view, "origin": "WEBSITE", "included": included, "mine": True,
            "conversions": conv, "allConversions": conv, "value": 0.0}


def raw_action(i, name, **kw):
    """An action as Google's answer carries it, for read_google."""
    a = action(i, name, **kw)
    return {"conversionAction": {"id": a["id"], "name": a["name"], "status": a["status"], "type": a["type"], "category": a["category"], "primaryForGoal": a["primary"],
                                 "countingType": a["counting"], "attributionModelSettings": {"attributionModel": a["attribution"]},
                                 "clickThroughLookbackWindowDays": a["clickWindow"], "viewThroughLookbackWindowDays": a["viewWindow"], "origin": a["origin"],
                                 "includeInConversionsMetric": a["included"]}}


# ── Google ──────────────────────────────────────────────────────────────────

def test_read_google_asks_for_the_actions_and_their_volume_and_parses_both():
    asked = []

    def search(gaql):
        asked.append(gaql)
        if "FROM conversion_action" in gaql:
            return [raw_action(1, "Booked")]
        return [{"segments": {"conversionAction": "customers/1/conversionActions/1"}, "metrics": {"conversions": 12, "allConversions": 14, "conversionsValue": 0, "allConversionsValue": 0}}]
    got = st.read_google(search, "2026-09-01", "2026-09-30")
    assert got[0]["name"] == "Booked" and got[0]["conversions"] == 12.0 and got[0]["clickWindow"] == 30
    assert "BETWEEN '2026-09-01' AND '2026-09-30'" in asked[1]


def test_read_google_survives_volumes_that_cannot_be_read_and_says_none_not_zero():
    def search(gaql):
        if "FROM conversion_action" in gaql:
            return [raw_action(1, "Booked")]
        raise RuntimeError("quota")
    got = st.read_google(search, "2026-09-01", "2026-09-30")
    assert got[0]["conversions"] is None


def test_read_google_raises_when_the_actions_cannot_be_read():
    def search(gaql):
        raise RuntimeError("no")
    with pytest.raises(RuntimeError):
        st.read_google(search, "2026-09-01", "2026-09-30")


def test_only_enabled_primary_actions_in_the_conversions_column_are_counted():
    acts = [action(1, "Purchase"), action(2, "Page view", category="PAGE_VIEW", primary=False), action(3, "Old", status="REMOVED"),
            action(4, "Hidden", included=False), action(5, "Paused", status="PAUSED")]
    s = st.summarise_google(acts)
    assert [a["name"] for a in s["counted"]] == ["Purchase"]
    assert {a["name"] for a in s["secondary"]} == {"Page view", "Hidden"}
    assert s["enabled"] == 3


def test_the_dominant_window_is_the_one_most_conversions_came_through():
    acts = [action(1, "A", click=30, conv=10), action(2, "B", click=7, conv=90), action(3, "C", click=7, conv=5)]
    s = st.summarise_google(acts)
    assert s["dominantClick"] == 7
    assert s["clickWindows"] == [{"days": 7, "actions": 2, "conversions": 95.0}, {"days": 30, "actions": 1, "conversions": 10.0}]


def test_without_volumes_every_action_counts_once_and_the_numbers_are_none():
    acts = [action(1, "A", click=30, conv=None), action(2, "B", click=7, conv=None), action(3, "C", click=7, conv=None)]
    s = st.summarise_google(acts)
    assert s["volumeKnown"] is False and s["dominantClick"] == 7
    assert all(w["conversions"] is None for w in s["clickWindows"])


def test_models_and_categories_are_grouped_and_named():
    acts = [action(1, "A", model="GOOGLE_ADS_LAST_CLICK", category="SUBMIT_LEAD_FORM", conv=4), action(2, "B", model="EXTERNAL", category="PURCHASE", conv=1)]
    s = st.summarise_google(acts)
    assert {m["label"] for m in s["models"]} == {"Last click", "Set outside Google Ads (imported)"}
    assert s["dominantModel"] == "GOOGLE_ADS_LAST_CLICK"
    assert s["kinds"] == {"leads": 4.0, "sales": 1.0}
    assert {c["category"]: c["kind"] for c in s["categories"]} == {"SUBMIT_LEAD_FORM": "leads", "PURCHASE": "sales"}


def test_an_unknown_model_gets_a_readable_name():
    s = st.summarise_google([action(1, "A", model="")])
    assert s["counted"][0]["model"] == "Not stated" and s["dominantModel"] == "UNKNOWN"


def test_nothing_counted_summarises_to_nothing():
    s = st.summarise_google([action(1, "Page view", primary=False)])
    assert s["counted"] == [] and s["dominantClick"] is None and s["dominantModel"] is None and s["volumeKnown"] is False


def test_category_kinds():
    assert st.category_kind("purchase") == "sales" and st.category_kind("BOOK_APPOINTMENT") == "leads" and st.category_kind("PAGE_VIEW") == "other" and st.category_kind(None) == "other"


# ── Meta ────────────────────────────────────────────────────────────────────

def adset(i, spec="default", name=None):
    a = {"id": str(i), "name": name or "Set %s" % i}
    if spec != "default":
        a["attribution_spec"] = spec
    return a


CLICK7_VIEW1 = [{"event_type": "CLICK_THROUGH", "window_days": 7}, {"event_type": "VIEW_THROUGH", "window_days": 1}]
CLICK1 = [{"event_type": "CLICK_THROUGH", "window_days": 1}]


def test_windows_come_from_each_ad_sets_attribution_spec_and_are_weighted_by_spend():
    sets = [adset(1, CLICK7_VIEW1), adset(2, CLICK1)]
    spend = [{"adset_id": "1", "spend": "300"}, {"adset_id": "2", "spend": "100"}]
    m = st.parse_meta(sets, spend)
    assert m["spend"] == 400.0 and m["adsetCount"] == 2 and m["unmatched"] == 0
    assert m["groups"][0] == {"click": 7, "view": 1, "assumed": False, "spend": 300.0, "adsets": 1, "share": 75.0}
    assert m["groups"][1]["click"] == 1 and m["groups"][1]["view"] == 0
    assert m["viewShare"] == 75.0 and m["dominant"] == {"click": 7, "view": 1, "assumed": False}


def test_an_ad_set_with_no_stated_window_is_counted_at_the_default_and_marked_assumed():
    m = st.parse_meta([adset(1)], [{"adset_id": "1", "spend": "50"}])
    assert m["groups"][0]["click"] == 7 and m["groups"][0]["view"] == 1 and m["groups"][0]["assumed"] is True
    assert m["dominant"]["assumed"] is True


def test_an_empty_spec_list_is_the_same_as_none():
    m = st.parse_meta([adset(1, [])], [{"adset_id": "1", "spend": "50"}])
    assert m["groups"][0]["assumed"] is True


def test_a_click_only_spec_means_no_view_window_not_the_default_one():
    m = st.parse_meta([adset(1, CLICK1)], [{"adset_id": "1", "spend": "50"}])
    assert m["groups"][0]["view"] == 0 and m["groups"][0]["assumed"] is False and m["viewShare"] == 0.0


def test_an_ad_set_that_spent_nothing_is_left_out_and_one_not_listed_is_counted_as_unmatched():
    m = st.parse_meta([adset(1, CLICK7_VIEW1)], [{"adset_id": "1", "spend": "0"}, {"adset_id": "9", "spend": "40"}])
    assert m["adsetCount"] == 1 and m["unmatched"] == 1 and m["adsets"][0]["id"] == "9" and m["adsets"][0]["assumed"] is True


def test_spend_rows_for_the_same_ad_set_are_added():
    m = st.parse_meta([adset(1, CLICK7_VIEW1)], [{"adset_id": "1", "spend": "10"}, {"adset_id": "1", "spend": "15.5"}])
    assert m["spend"] == 25.5 and m["adsetCount"] == 1


def test_nothing_spent_means_no_dominant_window_and_no_share():
    m = st.parse_meta([], [])
    assert m["dominant"] is None and m["viewShare"] is None and m["groups"] == []


def test_read_meta_asks_for_the_ad_sets_and_their_spend_in_the_range():
    calls = []

    def pages(path, params):
        calls.append((path, params))
        if path == "adsets":
            return [adset(1, CLICK7_VIEW1)]
        return [{"adset_id": "1", "spend": "20"}]
    m = st.read_meta(pages, "2026-09-01", "2026-09-30")
    assert m["spend"] == 20.0
    assert calls[0][1]["fields"].find("attribution_spec") >= 0
    assert calls[1][1]["level"] == "adset" and '"since": "2026-09-01"' in calls[1][1]["time_range"]


# ── Side by side ────────────────────────────────────────────────────────────

def google_summary(**kw):
    return st.summarise_google([action(1, "Booked", **kw)])


def meta_summary(click=7, view=1, spend=100.0):
    spec = [{"event_type": "CLICK_THROUGH", "window_days": click}] + ([{"event_type": "VIEW_THROUGH", "window_days": view}] if view else [])
    return st.parse_meta([adset(1, spec)], [{"adset_id": "1", "spend": str(spend)}])


def ids(notes):
    return [n["id"] for n in notes]


def test_different_click_windows_are_flagged_with_the_direction_of_the_bias():
    notes = st.compare(google_summary(click=30), meta_summary(click=7), meta_kinds=None)
    n = next(n for n in notes if n["id"] == "click-window")
    assert n["level"] == "high" and "30 days" in n["text"] and "7 days" in n["text"] and "Google's longer window" in n["text"]


def test_the_longer_window_can_be_metas():
    notes = st.compare(google_summary(click=7), meta_summary(click=28), meta_kinds=None)
    assert "Meta's longer window" in next(n for n in notes if n["id"] == "click-window")["text"]


def test_the_same_window_is_not_flagged():
    assert "click-window" not in ids(st.compare(google_summary(click=7), meta_summary(click=7)))


def test_meta_counting_views_is_flagged_and_google_view_through_is_quoted():
    notes = st.compare(google_summary(), meta_summary(view=1), google_view=42)
    n = next(n for n in notes if n["id"] == "view-through")
    assert "100% of Meta's spend" in n["text"] and "1 day" in n["text"] and "(42)" in n["text"] and "clicks only" in n["text"]


def test_no_view_window_means_no_view_through_note():
    assert "view-through" not in ids(st.compare(google_summary(), meta_summary(view=0)))


def test_the_models_note_says_both_platforms_claim_the_same_person():
    n = next(n for n in st.compare(google_summary(model="GOOGLE_ADS_LAST_CLICK"), meta_summary()) if n["id"] == "models")
    assert "last click" in n["text"] and "counted by both" in n["text"] and "counts that person twice" in n["text"] and "at the top of the page" in n["text"]


def test_different_goals_are_flagged_only_when_meta_mostly_spends_on_the_other_goal():
    g = google_summary(category="SUBMIT_LEAD_FORM")
    notes = st.compare(g, meta_summary(), meta_kinds={"sales": 80.0, "leads": 20.0})
    n = next(n for n in notes if n["id"] == "goal-mismatch")
    assert "leads" in n["text"] and "80% of Meta's spend is on sales" in n["text"]
    assert "goal-mismatch" not in ids(st.compare(g, meta_summary(), meta_kinds={"sales": 45.0, "leads": 55.0}))
    assert "goal-mismatch" not in ids(st.compare(google_summary(category="PURCHASE"), meta_summary(), meta_kinds={"sales": 80.0, "leads": 20.0}))


def test_a_google_goal_that_is_neither_leads_nor_sales_is_not_called_a_mismatch():
    g = google_summary(category="PAGE_VIEW")
    assert "goal-mismatch" not in ids(st.compare(g, meta_summary(), meta_kinds={"sales": 100.0}))


def test_secondary_actions_are_named_and_pluralised():
    acts = [action(1, "Booked"), action(2, "Scroll", primary=False, category="ENGAGEMENT"), action(3, "Call", primary=False, category="PHONE_CALL_LEAD")]
    n = next(n for n in st.compare(st.summarise_google(acts), meta_summary()) if n["id"] == "secondary")
    assert n["level"] == "info" and "2 enabled actions count under" in n["text"] and "Scroll" in n["text"] and "includes them" in n["text"]
    one = st.summarise_google([action(1, "Booked"), action(2, "Scroll", primary=False)])
    assert "1 enabled action counts under" in next(n for n in st.compare(one, meta_summary()) if n["id"] == "secondary")["text"]


def test_the_most_important_notes_come_first():
    acts = [action(1, "Booked", click=30), action(2, "Scroll", primary=False)]
    notes = st.compare(st.summarise_google(acts), meta_summary(click=7, view=1), meta_kinds=None)
    levels = [n["level"] for n in notes]
    assert levels == sorted(levels, key=lambda l: {"high": 0, "medium": 1, "info": 2}[l])


def test_nothing_is_compared_when_either_side_is_missing():
    assert st.compare(None, meta_summary()) == [] and st.compare(google_summary(), None) == []
    assert st.compare(st.summarise_google([]), meta_summary()) == []
    assert st.compare(google_summary(), st.parse_meta([], [])) == []


def test_a_google_window_that_is_not_stated_is_not_compared_with_metas():
    assert "click-window" not in ids(st.compare(google_summary(click=0), meta_summary(click=7)))


def test_a_spec_that_lists_only_a_view_window_has_no_click_window_not_the_default():
    m = st.parse_meta([adset(1, [{"event_type": "VIEW_THROUGH", "window_days": 1}])], [{"adset_id": "1", "spend": "50"}])
    assert m["groups"][0]["click"] == 0 and m["groups"][0]["view"] == 1 and m["groups"][0]["assumed"] is False


def test_the_goals_note_needs_half_of_metas_spend_on_the_other_goal():
    g = google_summary(category="SUBMIT_LEAD_FORM")
    assert "goal-mismatch" not in ids(st.compare(g, meta_summary(), meta_kinds={"sales": 45.0, "leads": 10.0, "other": 45.0}))
    assert "goal-mismatch" in ids(st.compare(g, meta_summary(), meta_kinds={"sales": 50.0, "leads": 10.0, "other": 40.0}))


def test_only_the_fifty_biggest_ad_sets_are_listed_but_all_are_counted():
    sets = [adset(i) for i in range(1, 61)]
    spend = [{"adset_id": str(i), "spend": str(i)} for i in range(1, 61)]
    m = st.parse_meta(sets, spend)
    assert m["adsetCount"] == 60 and len(m["adsets"]) == 50 and m["adsets"][0]["id"] == "60" and m["spend"] == sum(range(1, 61))
