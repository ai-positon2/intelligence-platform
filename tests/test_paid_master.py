"""The Master Agent: what the team saved once per client and how the agents read it (tracker/paid_master.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tracker import paid_master as m  # noqa: E402

NOW = dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.timezone.utc)
G, MA = "1234567890", "act_1234567"


def data(name="Acme Dental", **kw):
    d = {"name": name, "accounts": [{"id": G, "budget": {"monthly": "12,000", "weeklyMin": 2000, "weeklyMax": "3500"}, "targets": {"cpa": "85", "roas": "", "cvr": "4", "ctr": "3.5"}, "tolerance": "25"},
                                    {"id": MA, "targets": {"cpa": "120"}}],
         "shared": {"phones": "(617) 555-0100; 617-555-0111", "locations": "Quincy, MA\nBoston, MA", "areas": "Greater Boston", "brandTerms": "Acme Dental, acme",
                    "writtenAs": "AcmeDental", "allowWords": "invisalign", "domains": "https://www.acmedental.com/book, acmedental.net", "offers": [{"text": "Free whitening with a new patient exam", "ends": "2026-12-31"}],
                    "notes": "Closed on Sundays."}}
    d.update(kw)
    return d


def saved(store=None, **kw):
    s = store or MemStore()
    m.save_client(s, "", data(**kw), "ana@position2.com", NOW)
    return s


# ── Cleaning ────────────────────────────────────────────────────────────────

def test_a_client_is_cleaned_into_the_shapes_the_agents_read():
    c = m.clean_client(data())
    assert c["name"] == "Acme Dental" and list(c["accounts"]) == [G, MA]
    assert c["accounts"][G] == {"budget": {"monthly": 12000.0, "weeklyMin": 2000.0, "weeklyMax": 3500.0}, "targets": {"cpa": 85.0, "cvr": 4.0, "ctr": 3.5}, "tolerance": 25.0}
    assert c["accounts"][MA] == {"budget": {}, "targets": {"cpa": 120.0}, "tolerance": None}
    sh = c["shared"]
    assert sh["phones"] == ["617-555-0100", "617-555-0111"] and sh["locations"] == ["Quincy, MA", "Boston, MA"] and sh["areas"] == ["Greater Boston"]
    assert sh["brandTerms"] == ["acme dental", "acme"] and sh["writtenAs"] == ["AcmeDental"] and sh["allowWords"] == ["invisalign"] and sh["domains"] == ["acmedental.com", "acmedental.net"]
    assert sh["offers"] == [{"text": "Free whitening with a new patient exam", "ends": "2026-12-31"}] and sh["notes"] == "Closed on Sundays."


@pytest.mark.parametrize("change, message", [
    ({"name": "  "}, "Give the client a name"), ({"name": "x" * 81}, "at most 80"),
    ({"accounts": [{"id": "12345"}]}, "is not a Google Ads customer id or a Meta ad account"), ({"accounts": [{"id": G}, {"id": "123-456-7890"}]}, "listed twice"),
    ({"accounts": [{"id": G, "budget": {"weeklyMin": 5000, "weeklyMax": 100}}]}, "weekly minimum cannot be above"), ({"accounts": [{"id": G, "targets": {"cpa": "abc"}}]}, "must be a number"),
    ({"accounts": [{"id": G, "tolerance": 500}]}, "between"),
    ({"shared": {"phones": "12345"}}, "not a ten digit US phone number"), ({"shared": {"locations": "12345"}}, "not a place name"), ({"shared": {"domains": "not a domain"}}, "not a domain"),
    ({"shared": {"domains": "localhost"}}, "not a domain"), ({"shared": {"offers": [{"text": "Hi"}]}}, "3 to 200"), ({"shared": {"offers": [{"text": "Free exam", "ends": "soon"}]}}, "must be a date"),
    ({"shared": {"allowWords": "not-a word!"}}, "not a word")])
def test_what_is_wrong_is_said_in_words(change, message):
    d = data()
    for k, v in change.items():
        d[k] = v if k != "shared" else dict(d["shared"], **v)
    with pytest.raises(m.BadRequest, match=message):
        m.clean_client(d)


def test_the_limits_hold():
    with pytest.raises(m.BadRequest, match="At most 30 accounts"):
        m.clean_client(data(accounts=[{"id": "%010d" % i} for i in range(31)]))
    with pytest.raises(m.BadRequest, match="At most 100 domains"):
        m.clean_client(data(shared={"domains": ["d%d.com" % i for i in range(101)]}))
    with pytest.raises(m.BadRequest, match="At most 20 offers"):
        m.clean_client(data(shared={"offers": [{"text": "Offer number %d" % i} for i in range(21)]}))


def test_a_domain_listed_twice_in_any_form_is_kept_once():
    assert m.clean_client(data(shared={"domains": "acme.com, https://www.acme.com/x; ACME.com"}))["shared"]["domains"] == ["acme.com"]


def test_an_empty_client_with_only_a_name_is_allowed():
    c = m.clean_client({"name": "Beta Clinic"})
    assert c["accounts"] == {} and c["shared"]["phones"] == [] and c["shared"]["offers"] == []


def test_account_ids_are_written_one_way():
    assert m.account_id("123-456-7890") == "1234567890" and m.account_id(" ACT_1234567 ") == "act_1234567"


# ── Saving ──────────────────────────────────────────────────────────────────

def test_a_client_is_saved_with_who_and_when_and_gets_a_stable_id():
    s = saved()
    v = m.view(s)
    assert [c["id"] for c in v["clients"]] == ["c-acme-dental"] and v["changes"][0] == {"at": "2026-10-27T09:00:00+00:00", "by": "ana@position2.com", "what": "saved the client Acme Dental (2 accounts)"}
    again = m.save_client(s, "c-acme-dental", data(name="Acme Dental Group"), "ben@position2.com", NOW)
    assert again["id"] == "c-acme-dental" and [c["name"] for c in m.view(s)["clients"]] == ["Acme Dental Group"]
    other = m.save_client(s, "", {"name": "Acme Dental!"}, "ana@position2.com", NOW)
    assert other["id"] == "c-acme-dental-2"


def test_an_account_belongs_to_one_client_and_a_name_to_one_client():
    s = saved()
    with pytest.raises(m.BadRequest, match="Acme Dental Smiles is already in the client Acme Dental"):
        m.save_client(s, "", {"name": "Other", "accounts": [{"id": G}]}, "x", NOW, names={G: "Acme Dental Smiles"})
    with pytest.raises(m.BadRequest, match="A client called Acme Dental already exists"):
        m.save_client(s, "", {"name": "acme dental"}, "x", NOW)
    with pytest.raises(m.BadRequest, match="does not exist"):
        m.save_client(s, "c-nope", {"name": "Nope"}, "x", NOW)
    m.save_client(s, "c-acme-dental", data(), "x", NOW)                                       # saving a client again with its own accounts is fine
    assert len(m.view(s)["clients"]) == 1


def test_a_failed_save_changes_nothing():
    s = saved()
    before = s.state_get(m.KEY)
    with pytest.raises(m.BadRequest):
        m.save_client(s, "c-acme-dental", data(shared={"phones": "nope"}), "x", NOW)
    assert s.state_get(m.KEY) == before


def test_a_client_can_be_removed_and_its_accounts_are_free_again():
    s = saved()
    assert m.delete_client(s, "c-acme-dental", "ana@position2.com", NOW) == {"id": "c-acme-dental", "name": "Acme Dental"}
    assert m.view(s)["clients"] == [] and m.view(s)["changes"][0]["what"] == "removed the client Acme Dental"
    m.save_client(s, "", {"name": "New owner", "accounts": [{"id": G}]}, "x", NOW)
    with pytest.raises(m.BadRequest):
        m.delete_client(s, "c-acme-dental", "x", NOW)


# ── What an account is held to ──────────────────────────────────────────────

def test_an_account_gets_its_own_budget_and_targets_and_the_clients_shared_lists():
    master = m.load(saved())
    g, ma = m.effective(master, G), m.effective(master, MA)
    assert g["client"] == {"id": "c-acme-dental", "name": "Acme Dental"} and g["budget"]["monthly"] == 12000.0 and g["targets"] == {"cpa": 85.0, "cvr": 4.0, "ctr": 3.5} and g["tolerance"] == 25.0
    assert ma["budget"] == {} and ma["targets"] == {"cpa": 120.0} and ma["phones"] == g["phones"] and ma["domains"] == ["acmedental.com", "acmedental.net"]
    assert m.effective(master, "9999999999") is None and m.client_of(master, "9999999999") is None


def test_offers_that_have_not_ended_are_the_active_ones():
    offers = [{"text": "a", "ends": "2026-10-26"}, {"text": "b", "ends": "2026-10-27"}, {"text": "c", "ends": ""}]
    assert [o["text"] for o in m.active_offers(offers, dt.date(2026, 10, 27))] == ["b", "c"]


# ── What the agents read ────────────────────────────────────────────────────

def test_brand_spend_gets_the_masters_budget_and_brand_words_only_where_it_has_none_and_never_for_a_meta_account():
    master = m.load(saved())
    own = {"limit": 30, "marks": {}, "accounts": {G: {"weeklyMin": 999.0, "weeklyMax": None, "monthlyBudget": None, "brandTerms": []}}, "changes": []}
    out = m.overlay_brandspend(own, master)
    assert out["accounts"][G] == {"weeklyMin": 999.0, "weeklyMax": 3500.0, "monthlyBudget": 12000.0, "brandTerms": ["acme dental", "acme"]}      # its own minimum wins, the rest is filled
    assert MA not in out["accounts"] and own["accounts"][G]["weeklyMax"] is None and out["limit"] == 30                                            # the original is untouched
    assert m.overlay_brandspend({"accounts": {}}, {"clients": {}}) == {"accounts": {}}


def test_kpi_gets_each_missing_target_and_the_tolerance_and_an_account_keeps_the_figures_it_has():
    master = m.load(saved())
    own = {"accounts": {G: {"targets": {"cpa": 70.0}, "tolerance": None, "campaigns": {"1": {"cpa": 5}}}}, "changes": []}
    out = m.overlay_kpi(own, master)
    assert out["accounts"][G]["targets"] == {"cpa": 70.0, "cvr": 4.0, "ctr": 3.5} and out["accounts"][G]["tolerance"] == 25.0 and out["accounts"][G]["campaigns"] == {"1": {"cpa": 5}}
    assert out["accounts"][MA] == {"targets": {"cpa": 120.0}} and own["accounts"][G]["targets"] == {"cpa": 70.0}
    kept = m.overlay_kpi({"accounts": {G: {"targets": {}, "tolerance": 30.0}}}, master)
    assert kept["accounts"][G]["tolerance"] == 30.0 and kept["accounts"][G]["targets"] == {"cpa": 85.0, "cvr": 4.0, "ctr": 3.5}


def test_the_ad_copy_checker_gets_each_missing_list_on_its_own():
    master = m.load(saved())
    own = {"accounts": {G: {"phones": ["800-555-0000"], "places": [], "brand": [], "allow": [], "name": "Acme"}}, "allow": ["x"], "changes": []}
    a = m.overlay_copycheck(own, master)["accounts"][G]
    assert a == {"phones": ["800-555-0000"], "places": ["Quincy, MA", "Boston, MA"], "brand": ["AcmeDental"], "allow": ["invisalign"], "name": "Acme"}
    assert m.overlay_copycheck(own, master)["allow"] == ["x"]


def test_location_conflicts_gets_the_areas_where_it_has_none():
    master = m.load(saved())
    out = m.overlay_geoconf({"accounts": {G: {"areas": [], "ignore": ["orange"], "acceptPresence": True}}}, master)
    assert out["accounts"][G] == {"areas": ["Greater Boston"], "ignore": ["orange"], "acceptPresence": True}
    assert m.overlay_geoconf({"accounts": {G: {"areas": ["Mine"]}}}, master)["accounts"][G]["areas"] == ["Mine"]


def test_the_allowed_domains_become_one_entry_per_account_of_the_client():
    entries = m.domain_entries(m.load(saved()))
    assert sorted((e["domain"], e["scope"]) for e in entries) == [("acmedental.com", G), ("acmedental.com", MA), ("acmedental.net", G), ("acmedental.net", MA)]
    assert all(e["fromMaster"] and "Acme Dental" in e["note"] for e in entries) and m.domain_entries({"clients": {}}) == []


def test_where_an_agents_own_value_is_not_the_masters_is_listed_and_agreement_is_not():
    master = m.load(saved())
    out = m.drift(master, brandspend={"accounts": {G: {"weeklyMin": 1500.0, "weeklyMax": 3500.0, "brandTerms": ["ACME", "acme dental"]}}},
                  kpi={"accounts": {G: {"targets": {"cpa": 70.0, "cvr": 4.0}, "tolerance": 30}}}, copycheck={"accounts": {G: {"phones": ["617-555-0100"], "places": ["quincy, ma", "boston, ma"]}}},
                  geoconf={"accounts": {G: {"areas": ["Greater Boston"]}}})
    got = {(d["agent"], d["field"]): (d["master"], d["saved"]) for d in out}
    assert got == {("Brand Spend Alert", "budget.weeklyMin"): (2000.0, 1500.0), ("KPI Monitoring", "targets.cpa"): (85.0, 70.0), ("KPI Monitoring", "tolerance"): (25.0, 30.0),
                   ("Ad Copy Checker", "phones"): (["617-555-0100", "617-555-0111"], ["617-555-0100"])}
    assert all(d["account"] == G and d["client"] == "Acme Dental" for d in out) and m.drift(master) == []


def test_every_field_names_the_agents_that_read_it_and_nothing_uses_an_em_dash():
    assert set(m.USED_BY) >= {"budget.monthly", "targets", "phones", "locations", "areas", "brandTerms", "writtenAs", "allowWords", "domains", "offers", "notes"}
    assert m.USED_BY["domains"] == ["Unknown Domains"] and m.USED_BY["offers"] == []
    assert "—" not in open(os.path.join(ROOT, "tracker", "paid_master.py"), encoding="utf-8").read()
