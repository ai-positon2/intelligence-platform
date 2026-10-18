"""Ad Copy & Creative Generator: what the writing is held to (tracker/paid_adcopy_check.py). The model is never called here: every case is a line the model might write and
what the rules do with it."""
import pytest

from tracker import paid_adcopy as pa, paid_adcopy_check as ck

FORM = {"name": "Acme Dental", "product": "dental implants and invisalign", "website": "acmedental.com", "keywords": "dental implants austin, invisalign cost",
        "facts": "Rated 4.9 stars from 312 reviews.\nFree consultation for new patients.", "avoid": "cheap, dentures", "competitors": "Smile Austin",
        "angles": ["benefit", "proof", "action"], "formats": ["google_rsa", "google_pmax", "meta_feed", "meta_stories_reels", "google_display"]}
INPUTS = pa.clean_inputs(FORM)
CTX = ck.context(INPUTS)
RSA_H = pa.FORMATS["google_rsa"]["fields"][0]
RSA_D = pa.FORMATS["google_rsa"]["fields"][1]
META_P = pa.FORMATS["meta_feed"]["fields"][0]


def research(text="Acme Dental offers dental implants in Austin. Rated 4.9 stars from 312 reviews. Free consultation for new patients."):
    return {"website": {"state": "ok", "data": {"pages": [{"url": "https://acmedental.com/", "title": "Acme Dental", "text": text}]}}}


def finder(text=None):
    return ck.claims_finder(*ck.evidence(research(text) if text else research(), INPUTS))


def one(raw, field=RSA_H, platform="google"):
    return ck.check_text(raw, field, platform, dict(CTX, platform=platform))


# ── one line ────────────────────────────────────────────────────────────────

def test_a_good_headline_is_kept_in_start_case():
    assert one("dental implants in austin") == ("Dental Implants in Austin", None)


def test_a_line_over_the_limit_is_dropped_not_cut():
    assert one("A" * 31) == (None, "long")
    assert one("Natural Looking Dental Implants Austin") == (None, "long")          # 38
    assert one("Dental Implants Austin")[0] == "Dental Implants Austin"
    assert one("x" * 90, RSA_D)[0] == "x" * 90 and one("x" * 91, RSA_D) == (None, "long")


def test_dashes_become_commas_and_never_reach_the_copy():
    assert one("Implants – Done Right")[0] == "Implants, Done Right"
    assert one("Implants — Done Right")[0] == "Implants, Done Right"


@pytest.mark.parametrize("line,why", [
    ("", "empty"), ("   ", "empty"),
    ("Book Today \U0001F600", "emoji"),
    ("Book Your Visit Today!", "exclaim"),
    ("Smile Brighter!!", "exclaim"),
    ("Great Value??", "punct"),
    ("Smiles... Restored", "punct"),
    ("SAVE ON IMPLANTS", "caps"),
    ("Dentist ACCEPTING Patients", "caps"),
    ("Call 512-555-0199", "phone"),
    ("Call (512) 555 0199 Now", "phone"),
    ("Visit acmedental.com", "phone"),
    ("See www.acme.example", "phone"),
    ("Cheap Dental Implants", "avoid"),
    ("Better Than Smile Austin", "rival"),
])
def test_google_refuses_these_lines(line, why):
    assert one(line) == (None, why)


def test_a_whole_line_in_capitals_is_refused_on_both_platforms():
    assert one("SAVE NOW") == (None, "caps")                                             # no single word is long enough to be shouting: the whole line is
    assert ck.check_text("BOOK YOUR VISIT TODAY", META_P, "meta", dict(CTX, platform="meta")) == (None, "caps")
    assert one("Q&A")[0] and one("24/7 Care")[0]                                         # a short acronym is not a line in capitals


def test_an_exclamation_mark_is_allowed_once_in_a_description_and_not_twice():
    assert one("Book your visit today!", RSA_D)[0] == "Book your visit today!"
    assert one("Book today! Smile again!", RSA_D) == (None, "exclaim")


def test_a_year_range_or_a_price_is_not_a_phone_number():
    assert one("Serving Austin 2019-2024")[0]
    assert one("Implants From $1,900")[0]


def test_an_acronym_the_client_uses_is_not_shouting():
    inputs = pa.clean_inputs(dict(FORM, facts="We are a HIPAA compliant practice."))
    ctx = dict(ck.context(inputs), platform="google")
    assert ck.check_text("HIPAA Compliant Care", RSA_H, "google", ctx)[0] == "HIPAA Compliant Care"
    assert ck.check_text("NASDAQ Compliant Care", RSA_H, "google", ctx) == (None, "caps")


def test_the_avoid_list_and_competitors_match_whole_words_only():
    assert one("Dentures Without Waiting") == (None, "avoid")
    assert one("Dentistry for Cheaper Visits")[0]                                      # "cheaper" is not "cheap"
    assert one("Smile Austin Alternatives") == (None, "rival")


def test_meta_allows_what_google_refuses_but_not_what_meta_refuses():
    assert ck.check_text("Book your visit today!", META_P, "meta", dict(CTX, platform="meta"))[0] == "Book your visit today!"
    assert ck.check_text("Smile \U0001F601 again", META_P, "meta", dict(CTX, platform="meta"))[0]
    assert ck.check_text("Are you diabetic? Ask us about implants", META_P, "meta", dict(CTX, platform="meta")) == (None, "attribute")
    assert ck.check_text("Your depression is not your fault", META_P, "meta", dict(CTX, platform="meta")) == (None, "attribute")


def test_a_sensitive_subject_without_a_claim_about_the_viewer_is_kept_but_flagged():
    got = ck.check_text("Dental care for people with diabetes", META_P, "meta", dict(CTX, platform="meta"))
    assert got[0] and ck.flags_of(got[0], "meta") and not ck.flags_of(got[0], "google")


# ── a field ─────────────────────────────────────────────────────────────────

def field(items, f=RSA_H, platform="google", find=None):
    drops = {}
    out = ck.clean_field(items, f, platform, dict(CTX, platform=platform), drops, find or (lambda t: []))
    return out, drops


def test_a_field_keeps_order_drops_repeats_and_counts_why():
    got, drops = field([{"text": "Dental Implants Austin", "angle": "benefit"}, {"text": "dental implants austin", "angle": "benefit"},
                        {"text": "Austin Dental Implants", "angle": "benefit"}, {"text": "X" * 40, "angle": "action"}, {"text": "Book Your Visit", "angle": "action"}])
    assert [g["text"] for g in got] == ["Dental Implants Austin", "Book Your Visit"]
    assert drops == {"repeat": 2, "long": 1}


def test_near_duplicates_are_the_same_words_or_all_but_one():
    assert ck.same_words("Natural Looking Dental Implants", "Dental Implants Natural Looking")
    assert ck.same_words("Natural Looking Dental Implants Austin", "Natural Looking Dental Implants Texas")
    assert not ck.same_words("Dental Implants", "Dental Crowns")
    assert not ck.same_words("", "x")


def test_a_field_holds_at_most_its_maximum():
    got, drops = field([{"text": "Unique %s Words %s" % (w, w[::-1]), "angle": "benefit"} for w in
                        ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima", "mike", "nova", "oscar", "papa", "quebec"]])
    assert len(got) == 15 and drops == {"extra": 2}


def test_an_angle_not_asked_for_is_other_and_the_chars_are_counted():
    got, _ = field([{"text": "Dental Implants", "angle": "benefit"}, {"text": "Book Now", "angle": "vibes"}, {"text": "Call Us"}, "Plain String Item"])
    assert [g["angle"] for g in got] == ["benefit", "other", "other", "other"]
    assert [g["chars"] for g in got] == [15, 8, 7, 17]


def test_claims_decide_ready_or_confirm_but_never_drop():
    find = finder()
    got, drops = field([{"text": "Rated 4.9 Stars", "angle": "proof"}, {"text": "Rated 5 Stars", "angle": "proof"}, {"text": "Free Consultation", "angle": "offer"},
                        {"text": "Free Whitening", "angle": "offer"}, {"text": "Best Dentist in Austin", "angle": "benefit"}, {"text": "Gentle Implant Care", "angle": "benefit"}], find=find)
    by = {g["text"]: g for g in got}
    assert by["Rated 4.9 Stars"]["status"] == "ready" and by["Free Consultation"]["status"] == "ready" and by["Gentle Implant Care"]["status"] == "ready"
    assert by["Rated 5 Stars"]["status"] == "confirm" and by["Rated 5 Stars"]["claims"]
    assert by["Free Whitening"]["status"] == "confirm" and by["Best Dentist in Austin"]["status"] == "confirm"
    assert drops == {}


def test_a_keyword_is_not_evidence_for_a_claim():
    inputs = pa.clean_inputs(dict(FORM, keywords="free dental implants", facts="", product="dental implants"))
    corpus, brief, raw = ck.evidence({}, inputs)
    got = ck.clean_field([{"text": "Free Dental Implants", "angle": "offer"}], RSA_H, "google", dict(ck.context(inputs), platform="google"), {}, ck.claims_finder(corpus, brief, raw))
    assert got[0]["status"] == "confirm"


def test_what_the_person_said_is_evidence():
    inputs = pa.clean_inputs(dict(FORM, facts="We have 25 years of experience."))
    find = ck.claims_finder(*ck.evidence({}, inputs))
    assert find("25 Years of Experience") == [] and find("40 Years of Experience")


# ── paths and buttons ───────────────────────────────────────────────────────

def test_paths_are_letters_digits_and_hyphens_within_fifteen():
    assert ck.clean_paths({"path1": "Dental Implants", "path2": "Austin, TX!"}, 15) == {"path1": "Dental-Implants", "path2": "Austin-TX"}
    assert ck.clean_paths({"path1": "a" * 30}, 15)["path1"] == "a" * 15
    assert ck.clean_paths(None, 15) == {"path1": "", "path2": ""}


def test_a_button_must_be_the_platforms():
    drops = {}
    assert ck.clean_cta("book now", "google", drops) == ("Book now", "") and drops == {}
    assert ck.clean_cta("Call now", "google", drops)[0] == "Learn more" and drops == {"cta": 1}
    assert ck.clean_cta("Call Now", "meta", {})[0] == "Call now"


# ── the brief ───────────────────────────────────────────────────────────────

def brief(raw, find=None):
    drops = {}
    return ck.clean_brief(raw, "meta", dict(CTX, platform="meta"), find or (lambda t: []), drops), drops


def test_a_brief_is_cleaned_and_its_words_are_checked_like_copy():
    raw = {"concept": "A patient meets the team — and smiles.", "hook": "Rated 5 stars by 900 patients", "visual": "Warm office light.",
           "script": [{"beat": "Opening shot", "onScreen": "Smile Again", "voiceover": "Meet Acme Dental."}, {"beat": "x", "onScreen": "Cheap implants", "voiceover": ""}],
           "assets": ["Team photo", ""], "dos": ["Show real staff"], "donts": ["No stock photos", 5]}
    out, drops = brief(raw, finder())
    assert out["concept"] == "A patient meets the team, and smiles."
    assert out["script"][1]["onScreen"] == "" and drops == {"brief": 1}                    # the avoid list reached the words on screen
    assert out["assets"] == ["Team photo"] and out["donts"] == ["No stock photos", "5"]
    assert [c["where"] for c in out["claims"]] == ["hook"] and out["claims"][0]["claims"]


def test_an_empty_brief_is_none():
    assert brief({})[0] is None and brief(None)[0] is None and brief({"assets": ["x"]})[0] is None


def test_a_script_holds_at_most_eight_beats():
    out, _ = brief({"concept": "c", "script": [{"beat": "beat %d" % i, "onScreen": "", "voiceover": ""} for i in range(12)]})
    assert len(out["script"]) == ck.SCRIPT_BEATS


# ── a whole format ──────────────────────────────────────────────────────────

def rsa_answer(n_h=15, n_d=4, long_h=0):
    heads = ["Dental Implants Austin", "Invisalign Cost Guide", "Rated 4.9 Stars", "Free Consultation", "Book Your Visit", "Gentle Implant Care", "Same Visit Planning",
             "Local Austin Dentist", "Clear Pricing Upfront", "Meet The Team", "Family Friendly Office", "Modern Dental Technology", "Easy Online Booking", "Dental Care Near You",
             "Smile With Confidence"]
    descs = ["Plan your dental implants with a team that explains every step. Book a visit today.", "See what invisalign costs and how treatment works. Free consultation for new patients.",
             "Rated 4.9 stars from 312 reviews. Gentle care for the whole family in Austin.", "Questions about implants? We answer them in plain words. Call or book online."]
    return {"headlines": [{"text": t, "angle": "benefit"} for t in heads[:n_h] + ["X" * 40] * long_h], "descriptions": [{"text": t, "angle": "proof"} for t in descs[:n_d]],
            "paths": {"path1": "Dental Implants", "path2": "Austin"}}


def test_a_full_rsa_is_assembled_ok():
    out = ck.assemble("google_rsa", rsa_answer(), INPUTS, finder())
    assert out["state"] == "ok" and out["shortfalls"] == [] and out["brief"] is None and out["cta"] == "" and out["specs"] == []
    h, d = out["fields"]
    assert (len(h["items"]), len(d["items"])) == (15, 4) and out["paths"] == {"path1": "Dental-Implants", "path2": "Austin"}
    assert all(i["chars"] <= 30 for i in h["items"]) and all(i["chars"] <= 90 for i in d["items"])
    assert out["dropped"] == {}


def test_too_long_lines_are_dropped_and_a_shortfall_is_said():
    out = ck.assemble("google_rsa", rsa_answer(n_h=2, long_h=5, n_d=1), INPUTS, finder())
    assert out["state"] == "short" and out["dropped"]["long"] == 5
    assert out["shortfalls"] == ["Headlines: 2 kept, at least 3 needed", "Descriptions: 1 kept, at least 2 needed"]
    assert "at least 3 are needed" in ck.problems_for(out)[0] and "longer than the limit" in ck.problems_for(out)[-1]


def test_a_missing_brief_is_a_shortfall_when_the_format_has_one():
    answer = {"primary_texts": [{"text": "Dental implants, explained simply.", "angle": "benefit"}, {"text": "Book a visit and meet the team.", "angle": "action"}],
              "headlines": [{"text": "Meet Acme Dental", "angle": "benefit"}, {"text": "Book Your Visit", "angle": "action"}], "descriptions": [], "cta": "Book now"}
    out = ck.assemble("meta_feed", answer, INPUTS, finder())
    assert out["shortfalls"] == ["The creative brief was not written"] and out["cta"] == "Book now" and out["specs"]
    out = ck.assemble("meta_feed", dict(answer, brief={"concept": "A day at the practice", "hook": "Meet Dr Lee"}), INPUTS, finder())
    assert out["shortfalls"] == [] and out["brief"]["hook"] == "Meet Dr Lee"
    without = pa.clean_inputs(dict(FORM, briefs=False))
    out = ck.assemble("meta_feed", answer, without, finder())
    assert out["brief"] is None and out["specs"] == [] and out["shortfalls"] == []


def test_an_optional_field_may_be_empty():
    answer = {"primary_texts": [{"text": "Dental implants, explained simply.", "angle": "benefit"}, {"text": "Book a visit and meet the team.", "angle": "action"}],
              "headlines": [{"text": "Meet Acme Dental", "angle": "benefit"}, {"text": "Book Your Visit", "angle": "action"}], "brief": {"concept": "c"}, "cta": "Learn more"}
    assert ck.assemble("meta_feed", answer, INPUTS, finder())["shortfalls"] == []


def test_pmax_keeps_its_own_field_limits():
    answer = {"headlines": [{"text": t, "angle": "benefit"} for t in ["Dental Implants Austin", "Invisalign Cost", "Book Your Visit"]],
              "long_headlines": [{"text": "Dental implants in Austin with a team that explains every step before you start", "angle": "benefit"}],
              "descriptions": [{"text": "Plan your dental implants with a team that explains every step.", "angle": "benefit"}, {"text": "Book a visit and meet the team in Austin today.", "angle": "action"}],
              "short_description": [{"text": "Implants planned in plain words. Book today in Austin, TX.", "angle": "action"}], "cta": "Book now"}
    out = ck.assemble("google_pmax", answer, INPUTS, finder())
    assert out["shortfalls"] == [] and out["cta"] == "Book now" and out["paths"] is None
    too_long = dict(answer, short_description=[{"text": "x" * 61, "angle": "action"}])
    assert ck.assemble("google_pmax", too_long, INPUTS, finder())["shortfalls"] == ["Short description: 0 kept, at least 1 needed"]


def test_merge_keeps_the_first_answer_and_adds_the_second():
    first, second = rsa_answer(n_h=2, long_h=5), {"headlines": [{"text": "Extra One Line", "angle": "benefit"}, {"text": "Another New Line", "angle": "benefit"}]}
    merged = ck.merge(first, second, "google_rsa", INPUTS, finder())
    assert [h["text"] for h in merged["headlines"]][:2] == ["Dental Implants Austin", "Invisalign Cost Guide"] and len(merged["headlines"]) == 9
    assert merged["paths"] == first["paths"] and ck.assemble("google_rsa", merged, INPUTS, finder())["state"] in ("ok", "short")
    assert ck.merge({}, {"cta": "Book now"}, "google_pmax", INPUTS, finder())["cta"] == "Book now"


# ── coverage and totals ─────────────────────────────────────────────────────

def test_keyword_coverage_counts_google_headlines_only_and_ignores_word_order():
    google = ck.assemble("google_rsa", rsa_answer(), INPUTS, finder())
    got = ck.keyword_coverage(["dental implants austin", "invisalign cost", "teeth whitening"], [google])
    assert got == {"covered": ["dental implants austin", "invisalign cost"], "missing": ["teeth whitening"], "checked": True}
    only_meta = ck.assemble("meta_feed", {"headlines": [{"text": "Teeth Whitening", "angle": "benefit"}]}, INPUTS, finder())
    assert ck.keyword_coverage(["teeth whitening"], [only_meta]) == {"covered": [], "missing": ["teeth whitening"], "checked": False}


def test_totals_and_the_claims_list():
    a = rsa_answer()
    a["headlines"][2] = {"text": "Rated 5 Stars", "angle": "proof"}
    out = ck.assemble("google_rsa", a, INPUTS, finder())
    t = ck.totals([out])
    assert t == {"ready": 18, "confirm": 1}
    listed = ck.claims_list([out])
    assert listed == [{"format": "Google Search", "field": "Headlines", "text": "Rated 5 Stars", "claims": listed[0]["claims"]}] and listed[0]["claims"]
