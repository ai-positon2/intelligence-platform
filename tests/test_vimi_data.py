"""Vimi reads an agent's report through a digest and a drill, so a big result neither drowns the model nor loses the rows that answer the question."""
import json

import pytest

from tracker import vimi_data as vd


def report(n=60):
    camps = [{"id": str(1000 + i), "name": "Campaign %02d" % i, "channel": "SEARCH" if i % 3 else "PMAX", "cost": float(10 * i), "conversions": float(i % 7), "ctr": 0.01 * (i % 5), "status": "ENABLED" if i % 4 else "PAUSED"} for i in range(n)]
    return {"account": {"id": "1234567890", "name": "Beta Bionics", "currency": "USD"}, "range": ["2026-10-01", "2026-10-28"], "campaigns": camps,
            "summary": {"cost": sum(c["cost"] for c in camps), "notes": ["a", "b"]}, "findings": [{"severity": "high", "text": "x" * 500}, {"severity": "low", "text": "y"}]}


# ── the first look ────────────────────────────────────────────────────────────

def test_the_digest_names_every_table_with_its_row_count_columns_and_totals():
    text = vd.digest(report())
    assert "- campaigns: 60 rows" in text and "- findings: 2 rows" in text
    assert "cost(n)" in text and "name(t)" in text
    assert "cost sum 17700" in text                      # 10 * (0 + 1 + ... + 59)
    assert "ctr avg" in text and "ctr sum" not in text   # a sum of rates means nothing: the average is given


def test_the_digest_cuts_long_tables_and_says_how_many_rows_it_left_out():
    text = vd.digest(report())
    body = text.split("CONTENT:")[1]
    assert "... 54 more" in body and "Campaign 05" in body and "Campaign 06" not in body


def test_the_digest_stays_inside_its_budget_even_for_a_huge_result():
    huge = {"rows": [{"k%d" % j: "v" * 90 for j in range(30)} for _ in range(400)], "more": {str(i): {"a": [1] * 50} for i in range(300)}}
    assert len(vd.digest(huge, budget=6000)) <= 6200


def test_a_long_text_field_is_cut():
    assert "x" * 500 not in vd.digest(report())


def test_a_scalar_result_and_an_empty_one_do_not_break_the_digest():
    assert vd.digest({}).endswith("{}") and "42" in vd.digest(42) and vd.digest([]).endswith("[]")


# ── paths ─────────────────────────────────────────────────────────────────────

def test_a_path_walks_keys_and_indexes():
    assert vd.resolve(report(), "campaigns[2].name") == "Campaign 02"
    assert vd.resolve(report(), "range[1]") == "2026-10-28" and vd.resolve(report(), "") == report()


def test_a_wrong_path_says_what_is_there_instead_of_failing_silently():
    with pytest.raises(vd.PathError) as e:
        vd.resolve(report(), "campaign")
    assert "campaigns" in str(e.value)
    with pytest.raises(vd.PathError) as e:
        vd.resolve(report(), "range[9]")
    assert "2 items" in str(e.value)


# ── the drill ─────────────────────────────────────────────────────────────────

def test_sort_and_limit_pick_the_top_rows_by_a_column():
    out = vd.query(report(), "campaigns", sort_by="cost", limit=3)
    assert [r["name"] for r in out["rows"]] == ["Campaign 59", "Campaign 58", "Campaign 57"]
    assert out["matched"] == 60 and out["next_offset"] == 3


def test_ascending_order_and_paging_continue_where_the_last_page_stopped():
    first = vd.query(report(), "campaigns", sort_by="cost", order="asc", limit=2)
    second = vd.query(report(), "campaigns", sort_by="cost", order="asc", limit=2, offset=first["next_offset"])
    assert [r["name"] for r in first["rows"]] == ["Campaign 00", "Campaign 01"] and [r["name"] for r in second["rows"]] == ["Campaign 02", "Campaign 03"]


def test_filters_combine_and_numbers_compare_as_numbers_not_text():
    out = vd.query(report(), "campaigns", where=[{"field": "cost", "op": ">=", "value": 100}, {"field": "status", "op": "=", "value": "paused"}], limit=100)
    assert out["matched"] == sum(1 for i in range(60) if 10 * i >= 100 and i % 4 == 0)      # "100" must not sort as text before "20"
    assert all(r["status"] == "PAUSED" and r["cost"] >= 100 for r in out["rows"])


def test_contains_in_and_exists_filters():
    assert vd.query(report(), "campaigns", where=[{"field": "name", "op": "contains", "value": "campaign 1"}])["matched"] == 10
    assert vd.query(report(), "campaigns", where=[{"field": "channel", "op": "in", "value": ["pmax"]}])["matched"] == 20
    assert vd.query(report(), "campaigns", where=[{"field": "nope", "op": "exists"}])["matched"] == 0


def test_fields_project_only_the_columns_asked_for():
    out = vd.query(report(), "campaigns", fields=["name", "cost"], limit=1, sort_by="cost")
    assert out["rows"] == [{"name": "Campaign 59", "cost": 590.0}]


def test_group_by_sums_in_code_and_sorts_the_groups():
    out = vd.query(report(), "campaigns", group_by="channel", aggregate=[{"field": "cost", "fn": "sum", "as": "spend"}, {"fn": "count", "as": "n"}])
    by = {g["channel"]: g for g in out["groups"]}
    assert by["PMAX"]["n"] == 20 and by["SEARCH"]["n"] == 40
    assert by["PMAX"]["spend"] + by["SEARCH"]["spend"] == 17700
    assert out["groups"][0]["spend"] >= out["groups"][1]["spend"] and out["sorted_by"] == "spend"


def test_an_aggregate_with_no_grouping_answers_for_all_matched_rows():
    out = vd.query(report(), "campaigns", where=[{"field": "channel", "op": "=", "value": "PMAX"}], aggregate=[{"field": "cost", "fn": "sum"}, {"field": "cost", "fn": "max"}])
    assert out["aggregate"] == {"sum_cost": sum(10 * i for i in range(60) if i % 3 == 0), "max_cost": 570.0}


def test_a_path_at_an_object_returns_its_value_and_names_the_tables_inside_it():
    out = vd.query(report(), "")
    assert out["tables"] == ["campaigns", "findings"] and out["value"]["account"]["name"] == "Beta Bionics"
    assert vd.query(report(), "summary")["value"]["notes"] == ["a", "b"]


def test_a_plain_list_pages():
    assert vd.query({"words": list("abcdef")}, "words", limit=2, offset=1)["values"] == ["b", "c"]


def test_bad_filters_and_functions_are_refused_with_the_allowed_choices():
    with pytest.raises(ValueError, match="op 'like'"):
        vd.query(report(), "campaigns", where=[{"field": "name", "op": "like", "value": "x"}])
    with pytest.raises(ValueError, match="fn 'median'"):
        vd.query(report(), "campaigns", aggregate=[{"field": "cost", "fn": "median"}])
    with pytest.raises(ValueError, match="needs a field"):
        vd.query(report(), "campaigns", where=[{"op": "="}])


def test_a_nested_field_is_reached_with_a_dotted_name():
    data = {"rows": [{"n": "a", "m": {"cost": 5}}, {"n": "b", "m": {"cost": 9}}]}
    assert vd.query(data, "rows", sort_by="m.cost")["rows"][0]["n"] == "b"
    assert vd.query(data, "rows", aggregate=[{"field": "m.cost", "fn": "sum"}])["aggregate"] == {"sum_m.cost": 14}


def test_missing_values_sort_last_and_do_not_break_aggregates():
    data = {"rows": [{"n": "a", "v": None}, {"n": "b", "v": 3}, {"n": "c"}]}
    assert [r["n"] for r in vd.query(data, "rows", sort_by="v")["rows"]][0] == "b"
    assert vd.query(data, "rows", aggregate=[{"field": "v", "fn": "sum"}, {"field": "v", "fn": "count"}])["aggregate"] == {"sum_v": 3, "count_v": 1}


def test_render_cuts_a_long_answer_row_by_row_and_says_how_to_ask_for_less():
    rows = [{"name": "n" * 80, "i": i} for i in range(100)]
    text = vd.render(vd.query({"r": rows}, "r", limit=100), budget=3000)
    back = json.loads(text)
    assert len(text) <= 3000 and 0 < back["returned"] < 100 and "fields" in back["cut"] and back["next_offset"] == back["returned"]


# ── what one question holds ───────────────────────────────────────────────────

def test_results_are_kept_under_ids_and_an_unknown_id_lists_the_known_ones():
    r = vd.Results()
    a, b = r.add("KPI Monitoring: Beta", {"x": 1}), r.add("SQR: Beta", {"y": 2})
    assert (a, b) == ("r1", "r2") and r.get("R2")["data"] == {"y": 2}
    with pytest.raises(KeyError) as e:
        r.get("r9")
    assert "r1=KPI Monitoring: Beta" in str(e.value)


def test_results_drop_the_oldest_beyond_the_limit():
    r = vd.Results(keep=2)
    for i in range(3):
        r.add("l%d" % i, i)
    assert [k for k, _l in r.labels()] == ["r2", "r3"]
