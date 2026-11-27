"""The Master Agent's figure check (tracker/master_verify.py): a figure with no source in the data is found. No network."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import master_verify as V  # noqa: E402

DATA = json.dumps({"rows": [{"metrics.cost": 32329.33, "metrics.conversions": 362.01, "metrics.ctr": 0.0344, "metrics.clicks": 2198, "metrics.cpa": 89.3131}]})


def bad(answer, *evidence):
    return V.unverified(answer, list(evidence))


def test_figures_that_are_in_the_data_to_the_precision_written_pass():
    ans = "Spend was **$32,329.33** for **362** conversions at **$89.31** each, a CTR of 3.4%, from 2,198 clicks. About $32.3K in all."
    assert bad(ans, DATA) == []


def test_a_figure_worked_out_in_the_head_is_found():
    assert bad("Spend was $32,329.33 and the total with Meta is $41,000.", DATA) == ["$41,000"]
    assert bad("Conversions were 362 and the cost per conversion was $91.50.", DATA) == ["$91.50"]
    assert bad("CTR was 4.4%.", DATA) == ["4.4%"]
    assert bad("About $33K.", DATA) == ["$33K"]


def test_dates_ids_counts_ranks_and_list_numbers_are_not_figures():
    ans = ("1. First.\n2. Second.\nFrom Nov 1 to 27, 2026 (2026-11-20 to 2026-11-26) the top 5 of 13 accounts, P2_Brand2 and account 9296113127, "
           "over the last 7 days at 09:30, in Q4. Spend was $32,329.33.")
    assert bad(ans, DATA) == []


def test_a_percent_may_be_a_fraction_in_the_data_and_the_reverse():
    assert bad("CTR was 3.4%.", json.dumps({"ctr": 0.0344})) == []
    assert bad("Conversion rate 12.5%.", json.dumps({"rate": 12.5})) == []
    assert bad("Cost fell 25.0%.", json.dumps({"delta": 0.5})) == ["25.0%"]


def test_no_evidence_is_no_verdict_and_nothing_to_check_is_clean():
    assert bad("Spend was $5,000.") == [] and bad("Spend was $5,000.", "") == []
    assert bad("Nothing numeric here, only 3 things.", DATA) == []


def test_the_same_figure_is_reported_once_and_the_list_is_capped():
    assert bad("$41,000 and again $41,000.", DATA) == ["$41,000"]
    many = " ".join("$%d,000" % (50 + i) for i in range(40))
    assert len(bad(many, DATA)) == V.MAX_REPORTED


def test_the_evidence_is_what_was_given_or_printed_not_what_the_model_wrote_or_thought():
    msgs = [
        {"role": "user", "content": "q from before $1,111.11"},
        {"role": "assistant", "content": "earlier answer $2,222.22"},
        {"role": "user", "content": [{"type": "text", "text": "new question $3,333.33"}]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "so $9,999.99", "signature": "s"}, {"type": "text", "text": "working $8,888.88"},
                                          {"type": "server_tool_use", "name": "code_execution", "input": {"code": "x = 7,777.77"}},
                                          {"type": "code_execution_tool_result", "content": {"stdout": "printed 4,444.44"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "tool 5,555.55"}]},
    ]
    ev = " ".join(V.evidence_from_messages(msgs))
    for ok in ("1,111.11", "2,222.22", "3,333.33", "4,444.44", "5,555.55"):
        assert ok in ev, ok
    for no in ("9,999.99", "8,888.88", "7,777.77"):
        assert no not in ev, no


def test_a_pdf_or_image_the_model_read_itself_switches_the_check_off():
    assert V.has_unreadable([{"role": "user", "content": [{"type": "document", "source": {}}]}]) is True
    assert V.has_unreadable([{"role": "user", "content": [{"type": "text", "text": "x"}]}, {"role": "assistant", "content": "y"}]) is False


def test_a_huge_early_data_pull_never_crowds_out_the_code_output_that_comes_after_it(monkeypatch):
    monkeypatch.setattr(V, "MAX_EVIDENCE_CHARS", 1000)
    texts = ["9.5 " * 5000, "acct CPA 72.54"]
    assert V.unverified("The cost per lead was $72.54.", texts) == []
    assert 72.54 in V.evidence_values(texts)
