"""Pacing Desk: a campaign's recommended daily budget follows its category's move (the Change column of the campaign table)."""
from tests.test_paid_pacing_meta_engine import AS_OF, accounts_of, allocations, rd, rf, row, spend


def run(extra_rows=(), budget_a=100.0, budget_b=50.0):
    rows = rd.export_rows(spend("Acme Dental", budget_a, name="P2_Brand", conv=0.5) +
                          [dict(r, id="2", name="P2_Implants", cost=r["cost"] * 0.5, conv=6.0, budget=budget_b, sis=0.4) for r in spend("Acme Dental", budget_a)] + list(extra_rows))
    body, _ = rf.run_engine(rows, allocations(row("Acme Dental", 6200, "Google")), AS_OF)
    return accounts_of(body)["Acme Dental"]


def test_no_campaign_reads_a_recommended_budget_of_zero_and_each_follows_its_categorys_direction():
    a = run()
    camps = {c["name"]: c for c in a["campaigns"]}
    cats = {c["name"]: c for c in a["categories"]}
    assert all(c["newDaily"] > 0 for c in camps.values())
    brand, implants = camps["P2_Brand"], camps["P2_Implants"]
    assert brand["newDaily"] < brand["budget"] and implants["newDaily"] > implants["budget"]             # the weak category is cut and the strong one raised
    assert brand["newDaily"] == round(brand["budget"] * cats["Brand"]["newDaily"] / cats["Brand"]["budget"], 2)
    assert implants["newDaily"] == round(implants["budget"] * cats["Search"]["newDaily"] / cats["Search"]["budget"], 2)


def test_a_campaign_that_is_not_delivering_keeps_its_budget():
    old = [dict(r, id="9", name="P2_Old", status="PAUSED", cost=0.0, conv=0.0, impr=0.0, clicks=0.0, budget=30.0) for r in spend("Acme Dental", 30.0)]
    a = run(old)
    camps = {c["name"]: c for c in a["campaigns"]}
    assert "P2_Old" in camps and camps["P2_Old"]["active"] is False
    assert camps["P2_Old"]["newDaily"] == camps["P2_Old"]["budget"] == 30.0


def test_a_move_carries_the_steps_to_make_it_with_each_campaigns_budget_before_and_after():
    a = run()
    mv = a["moves"][0]
    camps = {c["name"]: c for c in a["campaigns"]}
    how = mv["how"]
    assert how[0].startswith("In Google Ads open Campaigns and click the amount in the Budget column")
    low = next(h for h in how if h.startswith("Lower Brand:"))
    assert "P2_Brand from $100 to $%d a day" % round(camps["P2_Brand"]["newDaily"]) in low
    up = next(h for h in how if h.startswith("Raise Search:"))
    assert "P2_Implants from $50 to $%d a day" % round(camps["P2_Implants"]["newDaily"]) in up
    assert how[-1] == "Look at the pacing here again tomorrow: a raised budget takes a day or two to be spent, and Google can spend up to twice a campaign's budget on one day."


def test_a_shared_budget_is_named_in_the_steps_and_a_meta_move_points_at_ads_manager(monkeypatch):
    rows = rd.export_rows(spend("Acme Dental", 100.0, name="P2_Brand", conv=0.5) +
                          [dict(r, id="2", name="P2_Implants", cost=r["cost"] * 0.5, conv=6.0, budget=50.0, sis=0.4, pool="Implants pool") for r in spend("Acme Dental", 100.0)])
    body, _ = rf.run_engine(rows, allocations(row("Acme Dental", 6200, "Google")), AS_OF)
    how = accounts_of(body)["Acme Dental"]["moves"][0]["how"]
    assert any("Implants pool is a shared budget: change the shared budget itself" in h for h in how)
    meta_rows = rd.export_rows(spend("Acme Social", 100.0, name="P2_Brand", conv=0.5) + [dict(r, id="2", name="P2_Implants", cost=r["cost"] * 0.5, conv=6.0, budget=50.0) for r in spend("Acme Social", 100.0)])
    body, _ = rf.run_engine(meta_rows, allocations(row("Acme Social", 6200, "Meta")), AS_OF, channel="Meta")
    meta = accounts_of(body)["Acme Social"]["moves"]
    assert meta and meta[0]["how"][0].startswith("In Meta Ads Manager open each campaign below") and meta[0]["how"][-1].endswith("Meta can need a few days to settle after a budget change.")


# ── On the page ─────────────────────────────────────────────────────────────

import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "js", "pacing_desk.js")


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_moves_table_lists_the_steps_of_each_move_escaped_and_nothing_for_a_move_without_any():
    src = open(_JS, encoding="utf-8").read()
    a = src.index("function movesTable(a){")
    b = src.index("var CAMP_GET=")
    acct = {"name": "Acme", "moves": [{"loc": "Acme", "from": "Brand", "to": "Search", "amt": 14, "why": "w", "how": ["Open <b>x</b>", "Then y"]},
                                      {"loc": "Acme", "from": "A", "to": "B", "amt": 9, "why": "w2"}]}
    code = ("const D={asOf:'2026-10-16'}, moveId=(d,l,f,t)=>f+t, movesApplied={}, moveSort={dir:1}, m0=v=>'$'+v, arw=()=>'';"
            "const esc=s=>String(s==null?'':s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
            "%s;console.log(JSON.stringify(movesTable(%s)));") % (src[a:b], json.dumps(acct))
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    html = json.loads(r.stdout)
    assert '<details class="pd-how"><summary>Steps</summary><ol><li>Open &lt;b&gt;x&lt;/b&gt;</li><li>Then y</li></ol></details>' in html
    assert html.count("<details") == 1                                                             # the move with no steps (a saved run from before) has none


def test_a_campaign_whose_budget_would_change_by_under_a_dollar_is_left_out_of_the_steps():
    tiny = [dict(r, id="3", name="P2_Brand_Small", budget=1.0, cost=1.0, conv=0.0) for r in spend("Acme Dental", 1.0)]
    a = run(tiny)
    camps = {c["name"]: c for c in a["campaigns"]}
    assert abs(camps["P2_Brand_Small"]["newDaily"] - camps["P2_Brand_Small"]["budget"]) < 1
    assert not any("P2_Brand_Small" in h for h in a["moves"][0]["how"]) and any("P2_Brand from" in h for h in a["moves"][0]["how"])
