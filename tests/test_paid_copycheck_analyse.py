"""Ad Copy Checker: what the rules say about every ad of one account (tracker/paid_copycheck_analyse.py). No network."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_paid_copycheck_rules import FakeDict  # noqa: E402
from tracker import paid_copycheck_analyse as an  # noqa: E402
from tracker import paid_copycheck_rules as rules  # noqa: E402

ACCT = {"id": "1234567890", "name": "Acme Dental"}
NOW = dt.datetime(2026, 10, 22, 8, tzinfo=dt.timezone.utc)


def sp():
    return rules.Speller(FakeDict())


def ad(i, *pairs, camp="Core", group="G", url="https://acme.example/a", kind="rsa", platform="google"):
    return {"id": str(i), "platform": platform, "kind": kind, "campaignId": "5", "campaign": camp, "adGroupId": "10", "adGroup": group, "url": url, "texts": [{"slot": s, "text": t} for s, t in pairs]}


GOOD = ("headline", "Quality Dental Care"), ("headline", "Book Today"), ("description", "We offer implants.")


def run(ads, **kw):
    kw.setdefault("speller", sp())
    return an.analyse(ads, account=ACCT, **kw)


def test_an_account_with_nothing_wrong_is_clean():
    r = run([ad(1, *GOOD), ad(2, *GOOD)])
    assert r["status"] == "clean" and r["ads"] == 2 and r["clean"] == 2 and r["counts"] == {"high": 0, "medium": 0, "low": 0} and r["items"] == [] and r["byRule"] == [] and r["findingCount"] == 0
    assert r["spellingChecked"] is True and r["truncated"] is False and r["vocab"] == [] and r["phones"] == []


def test_the_ads_with_findings_come_worst_first_with_their_level_and_fingerprint():
    r = run([ad(1, *GOOD), ad(2, ("headline", "Dentst near you!!"), ("description", "We offer implants.")), ad(3, ("headline", "{Brand} implants"), ("description", "Call now,book today")),
             ad(4, ("headline", "BEST DENTIST IN TOWN"), ("description", "We offer implants."))])
    assert [i["id"] for i in r["items"]] == ["3", "2", "4"] and [i["level"] for i in r["items"]] == ["high", "medium", "medium"] and r["status"] == "fix" and r["counts"] == {"high": 1, "medium": 2, "low": 0}
    assert r["items"][0]["fingerprint"] == "copycheck:1234567890:3" and r["clean"] == 1 and r["items"][0]["findings"][0]["rule"] == "placeholder"
    assert [[f["rule"] for f in i["findings"]] for i in r["items"]] == [["placeholder", "gap"], ["spelling", "punct"], ["caps_line"]]


def test_only_low_findings_make_the_account_check_and_not_fix():
    r = run([ad(1, ("headline", "Call Now For FREEBIES today"), ("description", "We offer implants."))])
    assert r["status"] == "check" and r["counts"] == {"high": 0, "medium": 0, "low": 1} and r["items"][0]["level"] == "low"


def test_the_rules_are_ranked_by_level_then_by_the_ads_they_touch_with_a_few_examples():
    ads = [ad(i, ("headline", "Dentst near you"), ("description", "We offer implants.")) for i in range(1, 3)] + [ad(3, ("headline", "Quality Care"), ("description", "Call now!! Book now!!"))] + \
          [ad(i, ("headline", "Quality Care"), ("description", "Book now"), ("description", "We offer implants.")) for i in range(4, 9)]
    r = run(ads)
    got = [(b["rule"], b["ads"], b["findings"]) for b in r["byRule"]]
    assert got == [("spelling", 2, 2), ("punct", 1, 2), ("ending", 5, 5)]
    punct = next(b for b in r["byRule"] if b["rule"] == "punct")
    assert punct["findings"] == 2 and punct["ads"] == 1 and punct["label"] == "Doubled punctuation" and punct["fix"] and len(punct["examples"]) == 2
    ending = next(b for b in r["byRule"] if b["rule"] == "ending")
    assert ending["ads"] == 5 and len(ending["examples"]) == an.SHOWN_EXAMPLES


def test_a_word_the_dictionary_does_not_know_that_many_ads_spell_the_same_way_is_the_accounts_own_word_and_is_only_listed():
    three = [ad(i, ("headline", "Dentst near you"), ("description", "We offer implants.")) for i in (1, 2, 3)]
    r = run(three)
    assert {f["rule"] for it in r["items"] for f in it["findings"]} == {"vocab"} and r["status"] == "check" and r["counts"] == {"high": 0, "medium": 0, "low": 3}
    assert r["vocab"] == [{"word": "dentst", "ads": 3}] and "spelt that way in 3 ads" in r["items"][0]["findings"][0]["message"]
    two = run(three[:2])
    assert {f["rule"] for it in two["items"] for f in it["findings"]} == {"spelling"} and two["vocab"] == [] and two["status"] == "fix"


def test_the_accounts_name_its_web_address_and_the_team_allowed_words_are_never_doubted():
    ads = [ad(1, ("headline", "Dentst near you"), ("description", "We offer implants."), url="https://dentst.example/a")]
    assert run(ads)["status"] == "clean"                                                                                    # the address holds the word
    assert run([ad(1, ("headline", "Dentst near you"), ("description", "We offer implants."))], config={"allow": ["Dentst"]})["status"] == "clean"
    assert an.analyse([ad(1, ("headline", "Dentst near you"), ("description", "We offer implants."))], account={"id": "1", "name": "Dentst Co"}, speller=sp())["status"] == "clean"
    assert run([ad(1, ("headline", "Dentst near you"), ("description", "We offer implants."))], allowed_extra=["dentst"])["status"] == "clean"


def test_domain_words_are_the_labels_of_the_address_without_the_usual_endings():
    assert an.domain_words(["https://www.smile-co.com/book", "smileco.health", ""]) == {"smile-co", "smile", "smileco"} and an.domain_words([]) == set()


def test_the_phones_the_ads_carry_are_counted_by_ad_and_flagged_only_against_a_saved_list():
    ads = [ad(1, ("headline", "Quality Care"), ("description", "Call 617-555-0100 today.")), ad(2, ("headline", "Quality Care"), ("description", "Call (617) 555-0100 today.")),
           ad(3, ("headline", "Quality Care"), ("description", "Call 617-555-0199 today."))]
    r = run(ads)
    assert r["phones"] == [{"number": "617-555-0100", "digits": "6175550100", "ads": 2}, {"number": "617-555-0199", "digits": "6175550199", "ads": 1}] and r["items"] == [] and r["phonesSaved"] == 0
    r = run(ads, config={"phones": ["617-555-0100"]})
    assert [i["id"] for i in r["items"]] == ["3"] and r["items"][0]["level"] == "high" and r["phonesSaved"] == 1 and r["counts"]["high"] == 1


def test_a_brand_written_a_way_the_team_did_not_save_is_a_medium_finding_on_each_such_place():
    ads = [ad(1, ("headline", "SmileCo Dental"), ("description", "Visit SmileCo today.")), ad(2, ("headline", "Smile Co Dental"), ("description", "Visit Smile-Co today."))]
    r = run(ads, config={"brand": ["SmileCo"]})
    got = [(i["id"], f["excerpt"], f["level"], f["rule"]) for i in r["items"] for f in i["findings"]]
    assert got == [("2", "Smile Co", "medium", "brand"), ("2", "Smile-Co", "medium", "brand")] and "saved for it is \"SmileCo\"" in r["items"][0]["findings"][0]["message"]
    assert r["brandsRead"] == [{"brand": "SmileCo", "forms": {"SmileCo": 2, "Smile Co": 1, "Smile-Co": 1}}]


def test_a_brand_from_the_brand_spend_alert_written_two_ways_names_the_less_used_one_quietly():
    ads = [ad(1, ("headline", "SmileCo Dental"), ("description", "Visit SmileCo today.")), ad(2, ("headline", "SmileCo Care"), ("description", "Visit Smile Co today."))]
    r = run(ads, brand_terms=["smileco"])
    got = [(f["excerpt"], f["level"]) for i in r["items"] for f in i["findings"]]
    assert got == [("Smile Co", "low")] and r["status"] == "check" and "in 3 of the other places" in r["items"][0]["findings"][0]["message"]
    one_way = run([ads[0]], brand_terms=["smileco"])
    assert one_way["items"] == []                                                                                              # one spelling: nothing to say
    unknown = run(ads)                                                                                                         # no brand known: the capital inside the word is all it can say
    assert {f["rule"] for i in unknown["items"] for f in i["findings"]} == {"odd_caps"} and unknown["brandsRead"] == []


def test_brand_forms_are_found_by_joining_up_to_three_words_without_case_or_separators():
    ads = [ad(1, ("headline", "Smile Co Dental"), ("description", "S-m-i-l-e C-o"), ("path", "smileco"))]
    f = an.brand_forms(ads, ["SmileCo", ""])
    assert [(x["surface"], x["slot"]) for x in f] == [("Smile Co", "headline"), ("smileco", "path")] and an.brand_forms(ads, []) == []


def test_a_place_saved_for_another_account_is_a_medium_finding_in_this_one():
    ads = [ad(1, ("headline", "Dentist in Dover"), ("description", "We offer implants."))]
    r = run(ads, config={"places": ["Quincy, MA"]}, other_places={"Dover, NH": "Dover Dental"})
    assert [f["rule"] for f in r["items"][0]["findings"]] == ["place"] and r["placesSaved"] == 1 and "Dover Dental" in r["items"][0]["findings"][0]["message"]


def test_an_open_problem_has_its_age_and_how_often_it_came_back():
    ads = [ad(1, ("headline", "Dentst near you!!"), ("description", "We offer implants."))]
    ages = {"copycheck:1234567890:1": {"firstSeen": "2026-10-12T07:00:00+00:00", "reopened": 2}}
    it = run(ads, ages=ages, now=NOW)["items"][0]
    assert it["ageDays"] == 10 and it["reopened"] == 2
    assert "ageDays" not in run(ads)["items"][0] and "ageDays" not in run(ads, ages={}, now=NOW)["items"][0]


def test_more_ads_than_the_cap_are_cut_and_said(monkeypatch):
    monkeypatch.setattr(an, "MAX_ADS", 2)
    r = run([ad(i, ("headline", "Dentst near you"), ("description", "We offer implants.")) for i in range(1, 6)])
    assert r["truncated"] is True and r["ads"] == 2


def test_without_a_dictionary_nothing_is_said_about_spelling_and_the_account_says_so():
    r = an.analyse([ad(1, ("headline", "Dentst near you"), ("description", "We offer implants."))], account=ACCT, speller=None)
    assert r["spellingChecked"] is False and r["items"] == []


def test_the_accounts_own_words_choose_the_correction():
    own = [ad(i, ("headline", "Clinic near you"), ("description", "We offer implants.")) for i in range(1, 4)] + [ad(9, ("headline", "Clincs near you"), ("description", "We offer implants."))]
    r = run(own)
    assert r["items"][0]["findings"][0]["suggestion"] == "clinic"


def test_an_ad_with_findings_is_sent_without_its_texts_but_with_how_many_lines_it_holds():
    r = run([ad(1, ("headline", "Dentst near you"), ("headline", "Quality Dental Care"), ("description", "We offer implants."), ("description", "Book now"))])
    it = r["items"][0]
    assert "texts" not in it and it["lineCount"] == 4 and {f["text"] for f in it["findings"]} == {"Dentst near you", "We offer implants."}
    assert all("slot" in f and "text" in f for f in it["findings"])
