"""Ad Copy Checker: what is wrong with a piece of ad text (tracker/paid_copycheck_rules.py). No network, no model, and a stand-in dictionary so the result never depends on a package."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_copycheck_rules as r  # noqa: E402


class FakeDict:
    """The three calls Speller makes on pyspellchecker: unknown(words), candidates(word), correction(word)."""
    KNOWN = {"dentist", "affordable", "quality", "dental", "care", "implants", "call", "today", "best", "family", "near", "you", "your", "visit", "book", "now", "the", "and", "for", "smile",
             "clinic", "clinics", "teens", "city", "great", "easy", "free", "exam", "whitening", "offer", "tooth"}
    FIX = {"dentst": ["dentist"], "affordble": ["affordable"], "qualty": ["quality"], "clincs": ["clinics", "clinic"], "wxyzq": [], "tooht": ["tooth"], "teh": ["the"]}

    def unknown(self, words):
        return {w for w in words if w not in self.KNOWN}

    def candidates(self, w):
        return set(self.FIX.get(w, []))

    def correction(self, w):
        c = self.FIX.get(w) or []
        return c[0] if c else w


def ctx(**kw):
    kw.setdefault("speller", r.Speller(FakeDict()))
    return r.context(**kw)


def rules(text, slot="headline", **kw):
    return [f["rule"] for f in r.check_text(text, slot, ctx(**kw))]


def one(text, rule, slot="headline", **kw):
    got = [f for f in r.check_text(text, slot, ctx(**kw)) if f["rule"] == rule]
    assert len(got) == 1, (text, got)
    return got[0]


def test_every_rule_has_a_level_a_label_and_a_fix_and_the_order_lists_them_all():
    assert set(r.RULES) == set(r.ORDER) and len(r.ORDER) == len(set(r.ORDER))
    for k, v in r.RULES.items():
        assert v["level"] in ("low", "medium", "high") and v["label"] and v["fix"] and "—" not in v["label"] + v["fix"]


def test_an_unfilled_template_is_high_and_a_real_customizer_or_keyword_insertion_is_left_alone():
    f = one("{Brand} Implants", "placeholder")
    assert (f["start"], f["end"], f["excerpt"], f["level"]) == (0, 7, "{Brand}", "high")
    assert rules("Dentist in [City]") == ["placeholder"] and rules("Visit <name> today") == ["placeholder"] and rules("Lorem ipsum dolor sit") == ["placeholder"] and rules("TBD offer") == ["placeholder"]
    assert rules("{KeyWord:Dentist} Near You") == [] and rules("{=Offers.Price} for you") == [] and rules("{LOCATION(City)} Dentist") == [] and rules("{COUNTDOWN(2026/12/31 00:00:00)} left") == []
    assert [f["rule"] for f in r.check_text("{KeyWord:Dentst} Near You", "headline", ctx())] == ["spelling"]         # the default text of a keyword insertion is read; the markup is not


def test_the_positions_still_point_into_the_original_text_after_a_customizer_is_blanked():
    text = "{KeyWord:Dentst} Near You"
    f = r.check_text(text, "headline", ctx())[0]
    assert text[f["start"]:f["end"]] == "Dentst" and f["suggestion"] == "dentist"


def test_damaged_characters_are_found():
    assert one("Itâ€™s easy", "garbled", "description")["excerpt"] == "â€™" and rules("CafÃ© Dental") == ["garbled"] and rules("Smile � now") == ["garbled"]
    assert rules("It's easy, café open", "description") == []


def test_a_word_written_twice_in_a_row_is_found_but_a_real_double_is_not():
    f = one("Call the the clinic today", "repeat", "description")
    assert f["excerpt"] == "the the" and f["suggestion"] == "the"
    assert rules("Call us now now") == ["repeat"] and rules("Everything that that clinic does") == [] and rules("Walla Walla Dental", allowed=["walla"]) == []


def test_space_after_and_before_punctuation_urls_numbers_and_times_are_not_gaps():
    assert one("Call now,book today", "gap")["excerpt"] == "now,b" and one("Best dentist.Call now", "gap")["excerpt"] == "dentist.C"
    assert one("Call now , book today", "gap")["excerpt"] == " ," and one("Book today .", "gap", "description")["excerpt"] == " ."
    assert rules("Visit www.smile.com/book today") == [] and rules("Visit www.Smile.com today") == [] and rules("Open 9:30,Mon to Fri") == [] and rules("From $1,000 today") == [] and rules("Email info@smile.com now") == []


def test_doubled_punctuation_is_found_and_an_ellipsis_is_not():
    assert one("Book now!!", "punct")["excerpt"] == "!!" and one("Why wait??", "punct")["excerpt"] == "??" and one("Call now,, please", "punct", "description")["excerpt"] == ",,"
    assert rules("Book now...") == [] and one("Book now....", "punct")["excerpt"] == "...."


def test_a_line_in_capitals_is_one_finding_and_one_shouted_word_is_another_but_acronyms_and_brands_are_not():
    f = one("BEST DENTIST IN TOWN", "caps_line")
    assert (f["start"], f["end"]) == (0, 20) and rules("BEST DENTIST IN TOWN") == ["caps_line"]
    w = one("Call Now For FREEBIES today", "caps_word")
    assert w["excerpt"] == "FREEBIES" and w["suggestion"] == "Freebies"
    assert rules("Call HVAC Experts Today") == [] and rules("Visit SMILECO Today", brands=["SmileCo"]) == [] and rules("NEW Patients Welcome") == []
    assert "caps_line" not in rules("USA LLC") and rules("A B C") == []


def test_a_line_that_starts_with_a_small_letter_is_found_unless_it_is_a_brand_written_that_way():
    f = one("dentist near you", "lower_start")
    assert f["excerpt"] == "dentist" and f["suggestion"] == "Dentist"
    assert rules("iPhone repair near you") == [] and rules("eBay sellers welcome") == [] and rules("smileco dentists", brands=["smileco"]) == [] and rules("24 hour dental care") == []
    assert "lower_start" not in rules("dentist near you", "path") and "lower_start" not in rules("dentist near you", "snippet")


def test_a_capital_inside_a_word_is_found_but_a_name_that_is_written_so_is_not():
    assert one("Quality DenTist Care", "odd_caps")["excerpt"] == "DenTist"
    assert rules("McDonald Dental") == [] and rules("Visit SmileCo today", brands=["SmileCo"]) == [] and rules("Try YouTube ads") == [] and rules("Visit WeSmile today", allowed=["wesmile"]) == []


def test_spelling_names_the_word_the_fix_and_the_place_and_leaves_alone_what_it_cannot_help_with():
    got = r.check_text("Affordble Dentst for Your Family", "headline", ctx())
    assert [(f["rule"], f["excerpt"], f["suggestion"], f["level"]) for f in got] == [("spelling", "Affordble", "affordable", "medium"), ("spelling", "Dentst", "dentist", "medium")]
    assert "Did you mean \"affordable\"" in got[0]["message"]
    assert rules("Invisalign for Teens") == [] and rules("Wxyzq clinic") == []                                        # a word with no near word is a name, not a typo
    assert rules("Tooht exam", allowed=["tooht"]) == [] and rules("Visit Dr Tooht", brands=["Tooht"]) == []          # allowed and brand words are never doubted
    assert rules("Dentst") == ["spelling"] and "spelling" not in rules("Dnt now") and "spelling" not in rules("DENTST CARE", "path")
    assert rules("Teh clinic") == [] and rules("Tehr clinic") == []                                                   # under four letters is not looked up
    assert rules("Call 1234dentst now") == []                                                                         # next to a digit: a code, not a word


def test_the_accounts_own_words_decide_which_correction_is_offered():
    got = r.check_text("Clincs near you", "headline", ctx(own={"clinic": 5}))
    assert got[0]["suggestion"] == "clinic"
    assert r.check_text("Clincs near you", "headline", ctx())[0]["suggestion"] == "clinics"


def test_without_a_dictionary_spelling_is_skipped_and_everything_else_still_runs():
    c = r.context(speller=r.Speller.__new__(r.Speller))
    c["speller"].inner, c["speller"]._cache = None, {}
    assert not c["speller"].ready
    assert [f["rule"] for f in r.check_text("Dentst now!!", "headline", c)] == ["punct"]
    assert [f["rule"] for f in r.check_text("Dentst now!!", "headline", r.context())] in (["punct"], ["spelling", "punct"])


@pytest.mark.skipif(r.SpellChecker is None, reason="pyspellchecker is not installed")
def test_the_real_dictionary_finds_a_typo_and_does_not_doubt_common_words():
    c = r.context(speller=r.Speller())
    assert [f["suggestion"] for f in r.check_text("Qualty implants near you", "headline", c)] == ["quality"]
    assert r.check_text("Affordable family dentist near you", "headline", c) == []


def test_a_phone_number_is_checked_only_against_the_numbers_the_team_saved():
    f = one("Call 617-555-0199 today", "phone", "description", phones=["(617) 555-0100"])
    assert f["level"] == "high" and f["excerpt"] == "617-555-0199" and "not one of the numbers saved" in f["message"]
    for good in ("Call 617-555-0100", "Call (617) 555-0100", "Call 617.555.0100", "Call +1 617 555 0100", "Call 1-617-555-0100"):
        assert rules(good, "description", phones=["617-555-0100"]) == [], good
    assert rules("Call 617-555-0199 today", "description") == [] and rules("Since 2019-2024 we care", "description", phones=["617-555-0100"]) == []
    assert rules("From $6175550199 a month", "description", phones=["617-555-0100"]) == [] and rules("Call 1-800-DENTIST", "description", phones=["617-555-0100"]) == []


def test_phone_digits_are_ten_digits_or_nothing():
    assert r.phone_digits("(617) 555-0100") == "6175550100" and r.phone_digits("+1 617 555 0100") == "6175550100" and r.phone_digits("617-5550") == "" and r.phone_digits("22345678901") == "" and r.phone_digits("12345678901") == "2345678901"


def test_a_place_saved_for_another_account_is_found_but_this_accounts_own_place_is_not():
    other = {"Dover, NH": "Dover Dental", "Quincy, MA": "Quincy Smiles"}
    f = one("Dentist in Dover", "place", places=["Quincy, MA"], other_places=other)
    assert f["excerpt"] == "Dover" and "Dover Dental" in f["message"]
    assert rules("Dentist in Quincy", places=["Quincy, MA"], other_places=other) == []
    assert rules("Dentist in Dover", places=["Dover, NH"], other_places=other) == [] and rules("Dentist in Dover", other_places={}) == []
    assert rules("Ox pain care", other_places={"Ox, NH": "Ox Dental"}) == []                                          # a two-letter name is too short to match on
    assert rules("Redover dental", other_places=other) == []                                                         # a whole word only


def ad(*pairs):
    return {"texts": [{"slot": s, "text": t} for s, t in pairs]}


def test_headlines_that_end_with_a_full_stop_or_comma_are_found():
    got = [f for f in r.check_ad(ad(("headline", "Great Dental Care."), ("headline", "Book Today")), ctx()) if f["rule"] == "ending"]
    assert len(got) == 1 and got[0]["slot"] == "headline" and got[0]["text"] == "Great Dental Care." and got[0]["excerpt"] == "."
    assert [f for f in r.check_ad(ad(("headline", "Visit Dr."), ("headline", "Book Today")), ctx()) if f["rule"] == "ending"] == []


def test_descriptions_of_one_ad_that_do_not_agree_on_the_full_stop_name_the_odd_one_out():
    got = [f for f in r.check_ad(ad(("description", "We offer implants."), ("description", "Book your visit today."), ("description", "Call now")), ctx()) if f["rule"] == "ending"]
    assert [f["text"] for f in got] == ["Call now"] and "do not" not in got[0]["message"].split(";")[0] and "this one does not" in got[0]["message"]
    got = [f for f in r.check_ad(ad(("description", "We offer implants"), ("description", "Book your visit today"), ("description", "Call now.")), ctx()) if f["rule"] == "ending"]
    assert [f["text"] for f in got] == ["Call now."] and "this one does." in got[0]["message"]
    assert [f for f in r.check_ad(ad(("description", "We offer implants."), ("description", "Call now.")), ctx()) if f["rule"] == "ending"] == []
    assert [f for f in r.check_ad(ad(("primary", "We offer implants."), ("primary", "Call now")), ctx()) if f["rule"] == "ending"] == []              # a primary text is free-form
    tie = [f for f in r.check_ad(ad(("description", "We offer implants."), ("description", "Call now")), ctx()) if f["rule"] == "ending"]
    assert [f["text"] for f in tie] == ["We offer implants."]                                                                                       # one against one: the one with the full stop is named


def test_headlines_that_mix_title_case_and_sentence_case_name_the_minority():
    got = [f for f in r.check_ad(ad(("headline", "Quality Dental Care"), ("headline", "Family Dentist Near You"), ("headline", "Book your visit today")), ctx()) if f["rule"] == "style"]
    assert len(got) == 1 and got[0]["text"] == "Book your visit today" and "sentence case" in got[0]["message"]
    assert [f for f in r.check_ad(ad(("headline", "Quality Dental Care"), ("headline", "Family Dentist Near You"), ("headline", "Book Your Visit Today")), ctx()) if f["rule"] == "style"] == []
    assert [f for f in r.check_ad(ad(("headline", "Quality Dental Care"), ("headline", "Book your visit today")), ctx()) if f["rule"] == "style"] == []     # two headlines are too few to call a style
    assert [f for f in r.check_ad(ad(("headline", "Dentist In Boston"), ("headline", "Family care in Boston"), ("headline", "Book Your Visit")), ctx()) if f["rule"] == "style"][0]["text"] == "Family care in Boston"


def test_check_ad_carries_the_slot_and_the_text_of_every_finding():
    got = r.check_ad(ad(("headline", "Dentst Near You"), ("description", "Call now!!")), ctx())
    assert [(f["slot"], f["text"], f["rule"]) for f in got] == [("headline", "Dentst Near You", "spelling"), ("description", "Call now!!", "punct")]


def test_the_level_of_a_list_of_findings_is_the_highest():
    assert r.level_of([]) == "" and r.level_of([{"level": "low"}, {"level": "medium"}]) == "medium" and r.level_of([{"level": "low"}, {"level": "high"}, {"level": "medium"}]) == "high"
