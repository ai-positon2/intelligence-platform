"""Keyword Research: what of the model's answers is kept (tracker/paid_keywords_check.py)."""
from tests import keyword_fakes as K
from tracker import paid_keywords_check as kck, paid_keywords_data as kd

CAMPS = ["P2_Search_Implants_20250101", "P2_Search_Invisalign_20250101"]


_DEFAULT = object()


def themes(raw=_DEFAULT, camps=CAMPS):
    return kck.themes(K.THEMES if raw is _DEFAULT else raw, camps)


# ── themes ──────────────────────────────────────────────────────────────────

def test_the_themes_are_kept_with_their_words_checked():
    out = themes()
    t = out["themes"]
    assert [x["key"] for x in t] == ["implants", "invisalign", "emergency", "family", "brand"] and out["assumptions"] == ["Implants earn the most per patient"]
    assert t[0] == {"key": "implants", "name": "Dental Implants", "intent": "transactional", "priority": "high", "why": "People ready to replace missing teeth, the client's highest value service.",
                    "campaign": "P2_Search_Implants_20250101", "existing": True}


def test_an_existing_campaign_must_be_one_the_account_has_and_is_written_as_the_account_writes_it():
    t = {x["key"]: x for x in themes()["themes"]}
    assert t["invisalign"]["campaign"] == "P2_Search_Invisalign_20250101" and t["invisalign"]["existing"] is True          # given in lower case, kept as the account has it
    assert t["family"]["campaign"] == "" and t["family"]["existing"] is False                                           # one the model made up
    assert themes(camps=[])["themes"][0]["campaign"] == ""


def test_a_theme_with_no_name_a_repeated_key_or_the_key_other_is_dropped_and_the_number_is_capped():
    raw = {"themes": [{"key": "a", "name": "A", "intent": "local", "why": "", "existingCampaign": "", "priority": "high"}, {"key": "a", "name": "A again", "intent": "", "why": "", "existingCampaign": "", "priority": ""},
                      {"key": "x", "name": "", "intent": "", "why": "", "existingCampaign": "", "priority": ""}, {"key": "other", "name": "Other", "intent": "", "why": "", "existingCampaign": "", "priority": ""},
                      {"key": "", "name": "Teeth Whitening", "intent": "", "why": "", "existingCampaign": "", "priority": ""}]}
    assert [x["key"] for x in themes(raw)["themes"]] == ["a", "teeth-whitening"]
    many = {"themes": [{"key": "k%d" % i, "name": "Theme %d" % i, "intent": "", "why": "", "existingCampaign": "", "priority": ""} for i in range(30)]}
    assert len(themes(many)["themes"]) == kck.MAX_THEMES


def test_a_theme_word_from_a_list_is_one_of_the_list_or_its_default():
    raw = {"themes": [{"key": "a", "name": "A", "intent": "Transactional.", "why": "w", "existingCampaign": "", "priority": "high priority"}, {"key": "b", "name": "B", "intent": "whatever", "why": "w", "existingCampaign": "", "priority": "urgent"}]}
    t = themes(raw)["themes"]
    assert (t[0]["intent"], t[0]["priority"]) == ("transactional", "high") and (t[1]["intent"], t[1]["priority"]) == ("commercial", "medium")


def test_themes_tolerate_an_answer_of_the_wrong_shape():
    for raw in (None, {}, [], "x", {"themes": "none"}, {"themes": [None, 3, "x"]}):
        assert themes(raw) == {"themes": [], "assumptions": []}


def test_theme_text_has_no_markup_and_no_dashes():
    raw = {"themes": [{"key": "a", "name": "<b>Implants</b> — cost", "intent": "", "why": "Big – and <i>clear</i>", "existingCampaign": "", "priority": ""}], "assumptions": ["x — y"]}
    out = themes(raw)
    blob = str(out)
    assert "<" not in blob and "—" not in blob and "–" not in blob


def test_a_judged_keyword_names_its_theme_by_key_name_or_slug_and_anything_else_is_other():
    t = themes()["themes"]
    assert [kck.theme_key(v, t) for v in ("implants", "Dental Implants", "dental-implants", "DENTAL IMPLANTS", "Implants", "nothing", "", None)] == ["implants", "implants", "implants", "implants", "implants", "other", "other", "other"]


# ── the judgement of each keyword ───────────────────────────────────────────

def row(kw="dental implants austin", **kw_):
    base = {"kw": kw, "fit": "yes", "intent": "transactional", "theme": "implants", "match": "exact", "priority": "high", "note": "Core"}
    base.update(kw_)
    return base


def classify(rows, wanted=None):
    return kck.classify({"rows": rows}, wanted if wanted is not None else {r["kw"].lower() for r in rows}, themes()["themes"])


def test_a_judgement_is_kept_for_a_keyword_that_was_asked_about():
    judged, dropped = classify([row()])
    assert judged == {"dental implants austin": {"fit": "yes", "intent": "transactional", "theme": "implants", "match": "exact", "priority": "high", "note": "Core"}} and dropped == 0


def test_an_answer_about_a_keyword_that_was_not_asked_or_a_second_answer_is_dropped():
    judged, dropped = kck.classify({"rows": [row("dental implants austin"), row("invented keyword"), row("dental implants austin", fit="no"), row("  DENTAL   implants austin ")]},
                                   {"dental implants austin"}, themes()["themes"])
    assert list(judged) == ["dental implants austin"] and judged["dental implants austin"]["fit"] == "yes" and dropped == 3


def test_the_keyword_is_matched_however_the_model_wrote_its_case_and_spaces():
    judged, _ = kck.classify({"rows": [row("  Dental   Implants Austin ")]}, {"dental implants austin"}, themes()["themes"])
    assert list(judged) == ["dental implants austin"]


def test_each_word_of_a_judgement_is_one_of_its_list_or_its_default():
    judged, _ = classify([row(fit="Yes.", intent="Local search", theme="Dental Implants", match="Exact match", priority="HIGH")])
    j = judged["dental implants austin"]
    assert (j["fit"], j["intent"], j["theme"], j["match"], j["priority"]) == ("yes", "local", "implants", "exact", "high")
    judged, _ = classify([row(fit="great", intent="???", theme="zzz", match="fuzzy", priority="urgent")])
    j = judged["dental implants austin"]
    assert (j["fit"], j["intent"], j["theme"], j["match"], j["priority"]) == ("maybe", "other", "other", "phrase", "medium")


def test_a_note_is_cut_and_has_no_markup_or_dashes():
    judged, _ = classify([row(note="<script>x</script> " + "long " * 80 + "— end")])
    n = judged["dental implants austin"]["note"]
    assert len(n) <= 160 and "<" not in n and "—" not in n


def test_a_reply_of_the_wrong_shape_judges_nothing():
    for raw in (None, {}, [], "x", {"rows": "no"}, {"rows": [None, 1]}):
        assert kck.classify(raw, {"a"}, themes()["themes"]) == ({}, 0)


# ── negatives and the plan ──────────────────────────────────────────────────

def rows_fit(*kws):
    return [{"kw": k, "fit": "yes"} for k in kws]


ACCOUNT = {"keywords": K.ACCOUNT_KEYWORDS, "negatives": K.ACCOUNT_NEGATIVES, "lists": K.ACCOUNT_LISTS}


def negs(items, keywords=None, account=ACCOUNT):
    return kck.negatives({"negatives": items}, keywords if keywords is not None else rows_fit("dental implants austin", "invisalign cost"), account)


def neg(kw, match="phrase", why="Not a buyer"):
    return {"kw": kw, "match": match, "why": why}


def test_a_negative_is_kept_with_the_smallest_word_it_was_given_and_its_match_type():
    kept, removed = negs([neg("Dental Assistant"), neg("how long", "Phrase"), neg("gem", "exact", "Not dental")])
    assert [(n["kw"], n["match"]) for n in kept] == [("dental assistant", "phrase"), ("how long", "phrase"), ("gem", "exact")] and removed == []


def test_a_negative_that_would_block_a_keyword_the_research_recommends_is_dropped_and_says_which():
    kept, removed = negs([neg("dental implants"), neg("invisalign", "broad"), neg("cost", "phrase")])
    assert kept == [] and [r["kw"] for r in removed] == ["dental implants", "invisalign", "cost"]
    assert removed[0]["reason"] == "It would block the keyword \"dental implants austin\", which the account bids on or this research recommends."


def test_a_negative_that_would_block_a_keyword_the_account_already_bids_on_is_dropped():
    kept, removed = negs([neg("emergency dentist")], keywords=[])
    assert kept == [] and "emergency dentist austin" in removed[0]["reason"]
    kept, removed = negs([neg("acme")], keywords=[])
    assert kept == [] and removed


def test_an_exact_negative_blocks_only_that_exact_keyword():
    kept, _ = negs([neg("dental implants", "exact")], keywords=rows_fit("dental implants austin"), account={"keywords": [], "negatives": [], "lists": []})
    assert [n["kw"] for n in kept] == ["dental implants"]
    kept, removed = negs([neg("dental implants austin", "exact")], keywords=rows_fit("dental implants austin"), account={"keywords": [], "negatives": [], "lists": []})
    assert kept == [] and len(removed) == 1


def test_a_keyword_judged_not_a_fit_does_not_stop_a_negative():
    kept, _ = negs([neg("dallas")], keywords=[{"kw": "dentist dallas", "fit": "no"}], account={"keywords": [], "negatives": [], "lists": []})
    assert [n["kw"] for n in kept] == ["dallas"]


def test_a_negative_the_account_already_has_in_every_campaign_is_dropped_and_one_it_has_in_some_is_kept_with_a_note():
    kept, removed = negs([neg("jobs"), neg("dogs"), neg("dallas")], keywords=[])
    assert [r["kw"] for r in removed] == ["jobs", "dogs"] and removed[0]["reason"] == "The account already blocks it in every campaign it bids in (with \"jobs\")."
    assert kept == [{"kw": "dallas", "match": "phrase", "why": "Not a buyer", "note": "Some campaigns already block it; add it where it is missing."}]


def test_negatives_are_not_repeated_and_are_capped_and_cleaned():
    kept, _ = negs([neg("gem"), neg("GEM"), neg("gem", "exact"), neg("  "), neg("!!!"), {"kw": "x" * 200, "match": "phrase", "why": ""}], keywords=[], account={"keywords": [], "negatives": [], "lists": []})
    assert [(n["kw"], n["match"]) for n in kept] == [("gem", "phrase"), ("gem", "exact")]
    many = [neg("word%d" % i) for i in range(80)]
    kept, _ = negs(many, keywords=[], account={"keywords": [], "negatives": [], "lists": []})
    assert len(kept) == kck.MAX_NEGATIVES


def test_a_negative_match_type_the_model_made_up_is_phrase():
    kept, _ = negs([neg("gem", "fuzzy")], keywords=[], account={"keywords": [], "negatives": [], "lists": []})
    assert kept[0]["match"] == "phrase"


def test_the_plan_keeps_each_part_as_words_and_puts_the_questions_right():
    rows = [{"kw": "dental implants austin", "fit": "yes"}]
    p = kck.plan(K.PLAN, rows, ACCOUNT)
    assert p["summary"] == K.PLAN["summary"] and p["quickWins"][0]["what"].startswith("Add the converting search term") and p["tests"][0]["metric"] == "Cost per lead"
    assert [n["kw"] for n in p["negatives"]] == ["dental assistant", "how long", "gem"]
    assert [r["kw"] for r in p["removedNegatives"]] == ["free", "dental implants", "jobs", "dogs"]
    q = p["questions"]
    assert q[0]["urgency"] == "Before launch" and q[1]["area"] == "General" and q[1]["urgency"] == "First month"                  # "Before launch." is before launch; no area is still a question
    assert p["risks"][0]["risk"] == "Implant terms are broad" and p["seasonal"][0]["window"] == "April to June"


def test_a_plan_of_the_wrong_shape_is_empty_and_not_an_error():
    for raw in (None, {}, [], "x", {"summary": 5, "questions": "no", "negatives": {"a": 1}}):
        p = kck.plan(raw, [], {})
        assert p["summary"] == [] and p["quickWins"] == [] and p["negatives"] == [] and p["removedNegatives"] == [] and p["questions"] == [] and p["tests"] == [] and p["risks"] == [] and p["seasonal"] == []


def test_the_plan_text_has_no_markup_and_no_dashes():
    raw = dict(K.PLAN, summary=["A — b <b>bold</b>"], quickWins=[{"what": "x – y", "why": "<i>z</i>"}])
    blob = str(kck.plan(raw, [], {}))
    assert "<" not in blob and "—" not in blob and "–" not in blob


# ── a keyword line echoed whole, existing negatives that are not as broad, and numbers the model was not given ──

def test_a_model_that_echoes_a_whole_line_it_was_given_is_still_understood():
    judged, dropped = kck.classify({"rows": [row("dental implants austin | 1,900 searches a month | competition Low")]}, {"dental implants austin"}, themes()["themes"])
    assert list(judged) == ["dental implants austin"] and dropped == 0


def test_an_existing_negative_covers_a_proposed_one_only_if_it_blocks_everything_it_would():
    account = {"keywords": [{"t": "x one", "m": "EXACT", "c": "A", "g": "G"}, {"t": "y two", "m": "EXACT", "c": "B", "g": "G"}], "negatives": [{"t": "jobs", "m": "EXACT", "c": ["A", "B"]}, {"t": "diy", "m": "EXACT", "c": ["A", "B"]}, {"t": "free dental", "m": "PHRASE", "c": ["A", "B"]}],
               "lists": []}
    kept, removed = negs([neg("jobs", "phrase"), neg("free dental", "broad"), neg("diy", "broad"), neg("dental jobs", "exact")], keywords=[], account=account)
    assert [n["kw"] for n in kept] == ["jobs", "free dental", "diy", "dental jobs"] and removed == []                          # [jobs], [diy] and "free dental" block less than these would
    kept, removed = negs([neg("free dental clinic", "phrase"), neg("free dental", "phrase"), neg("free dental", "exact")], keywords=[], account=account)
    assert [n["kw"] for n in kept] == [] and [r["kw"] for r in removed] == ["free dental clinic", "free dental", "free dental"]   # "free dental" already blocks every search holding those words together


def test_the_numbers_the_model_was_given_are_the_only_figures_it_may_write():
    allowed = kck.numbers_in("coverage 38, 263,610 searches, bid 3.71 to 17.94, 12 000 more")
    assert allowed == {"38", "263610", "3.71", "17.94", "12", "0"}
    items = [("In short", "The account covers 38% of the market"), ("In short", "That is 263,610 searches a month"), ("In short", "Expect 450 leads a month"), ("First move", "Start 3 themes in 2026"),
             ("Test", "Raise the bid by 15%"), ("Risk", "It could cost $4,500")]
    assert kck.unbacked_numbers(items, allowed) == [{"where": "In short", "text": "Expect 450 leads a month", "number": "450"}, {"where": "Test", "text": "Raise the bid by 15%", "number": "15%"},
                                                    {"where": "Risk", "text": "It could cost $4,500", "number": "4,500"}]
    assert kck.unbacked_numbers([("In short", "12 percent")], set()) == [] and kck.unbacked_numbers([("In short", "13 leads")], set())[0]["number"] == "13" and kck.unbacked_numbers([("x", "12%")], set())[0]["number"] == "12%"
    assert kck.unbacked_numbers([("x", "in 1999")], set())[0]["number"] == "1999" and kck.unbacked_numbers([("x", "in 2101")], set())[0]["number"] == "2101" and kck.unbacked_numbers([("x", "in 2000 and 2100")], set()) == []


def test_the_plan_lists_the_numbers_it_was_not_given_and_skips_the_check_when_there_is_no_evidence_to_check_against():
    raw = dict(K.PLAN, summary=["The market is worth 9,999 searches", "It is big"], quickWins=[{"what": "Add 3 keywords", "why": "They bring 77 leads"}])
    p = kck.plan(raw, [], {}, allowed={"3"})
    assert [(n["where"], n["number"]) for n in p["numbers"]] == [("In short", "9,999"), ("First move", "77")]
    assert kck.plan(raw, [], {})["numbers"] == []


# ── what the second round of mutation tests found missing ──────────────────

def test_a_text_with_two_figures_it_was_not_given_is_listed_once_and_the_seasons_are_checked_too():
    assert kck.unbacked_numbers([("In short", "450 leads and 900 calls")], set()) == [{"where": "In short", "text": "450 leads and 900 calls", "number": "450"}]
    raw = dict(K.PLAN, seasonal=[{"window": "May", "action": "Raise the budget by 30%"}])
    p = kck.plan(raw, [], {}, allowed=set())
    assert [(n["where"], n["number"]) for n in p["numbers"] if n["where"] == "Season"] == [("Season", "30%")]
