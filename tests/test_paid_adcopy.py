"""Ad Copy & Creative Generator: what a build is and what it may be asked for (tracker/paid_adcopy.py). Plain functions, no network, no database."""
import pytest

from tracker import paid_adcopy as pa, paid_strategy as ps

FORM = {"name": "Acme Dental", "product": "dental implants and invisalign", "website": "acmedental.com"}


def test_the_formats_table_is_complete_and_consistent():
    assert set(pa.DEFAULT_FORMATS) <= set(pa.FORMATS)
    for key, f in pa.FORMATS.items():
        assert f["label"] and f["short"] and f["platform"] in ("google", "meta"), key
        assert f["fields"], key
        for fld in f["fields"]:
            assert fld["limit"] > 0 and 0 <= fld["min"] <= fld["max"], (key, fld["key"])
            assert fld["optional"] or fld["min"] >= 1, (key, fld["key"])
        assert f["cta"] in (None, "google", "meta"), key
        if f["brief"]:
            assert pa.SPECS.get(key), "%s needs image or video sizes in its brief" % key


def test_google_limits_are_googles():
    rsa = {f["key"]: f for f in pa.FORMATS["google_rsa"]["fields"]}
    assert (rsa["headlines"]["limit"], rsa["headlines"]["max"], rsa["descriptions"]["limit"], rsa["descriptions"]["max"]) == (30, 15, 90, 4)
    assert pa.FORMATS["google_rsa"]["paths"] == 15
    pmax = {f["key"]: f for f in pa.FORMATS["google_pmax"]["fields"]}
    assert (pmax["long_headlines"]["limit"], pmax["long_headlines"]["max"], pmax["short_description"]["limit"]) == (90, 5, 60)
    assert {f["key"]: f["limit"] for f in pa.FORMATS["google_demand_gen"]["fields"]} == {"headlines": 40, "descriptions": 90}


def test_meta_limits_are_the_constants():
    feed = {f["key"]: f["limit"] for f in pa.FORMATS["meta_feed"]["fields"]}
    assert feed == {"primary_texts": pa.META_PRIMARY, "headlines": pa.META_HEADLINE, "descriptions": pa.META_DESCRIPTION}
    assert (pa.META_PRIMARY, pa.META_HEADLINE, pa.META_DESCRIPTION) == (125, 40, 30)


def test_a_good_form_is_accepted_and_normalised():
    got = pa.clean_inputs(dict(FORM, account="123-456-7890", keywords="dental implants, invisalign, dental implants", formats=["google_rsa", "google_rsa", "meta_feed"]))
    assert got["kind"] == "adcopy" and got["website"] == "https://acmedental.com/" and got["account"] == "1234567890"
    assert got["keywords"] == ["dental implants", "invisalign"] and got["formats"] == ["google_rsa", "meta_feed"]
    assert got["angles"] == pa.DEFAULT_ANGLES and got["tone"] == "plain" and got["language"] == "English" and got["briefs"] is True
    assert got["services"] == got["product"] == "dental implants and invisalign"
    assert got["research"] == {"website": True, "account": True}


def test_defaults_when_formats_are_left_out():
    assert pa.clean_inputs(FORM)["formats"] == pa.DEFAULT_FORMATS


@pytest.mark.parametrize("change,word", [
    ({"name": "A"}, "name"),
    ({"product": ""}, "what the ads are for"),
    ({"product": "ab"}, "what the ads are for"),
    ({"website": "not a url at all"}, "web address"),
    ({"account": "12345"}, "ten digits"),
    ({"account": "act_1234567890"}, "Only a Google Ads account"),
    ({"account": "1234567890+act_1"}, "Only a Google Ads account"),
    ({"goal": "fame"}, "goal"),
    ({"tone": "angry"}, "tone"),
    ({"formats": ["tiktok"]}, "Unknown ad format"),
    ({"angles": ["vibes"]}, "Unknown angle"),
    ({"angles": ["benefit", "problem", "proof", "offer", "urgency", "question"]}, "at most 5"),
])
def test_a_bad_form_is_refused_with_a_sentence(change, word):
    with pytest.raises(ps.BadPayload) as e:
        pa.clean_inputs(dict(FORM, **change))
    assert word.lower() in str(e.value).lower()


def test_not_a_form():
    with pytest.raises(ps.BadPayload):
        pa.clean_inputs("hello")


def test_a_card_that_needs_something_missing_is_off():
    got = pa.clean_inputs({"name": "Acme", "product": "implants", "research": {"website": True, "account": True}})
    assert got["research"] == {"website": False, "account": False}
    on = pa.clean_inputs(dict(FORM, research={"website": False}))
    assert on["research"]["website"] is False


def test_competitors_become_names_not_to_write():
    assert pa.clean_inputs(dict(FORM, competitors="Smile Austin, Other Dental"))["competitors"] == [{"name": "Smile Austin", "url": ""}, {"name": "Other Dental", "url": ""}]
    assert pa.clean_inputs(dict(FORM, competitors=["Smile Austin"]))["competitors"] == [{"name": "Smile Austin", "url": ""}]


def test_steps_follow_the_form():
    inputs = pa.clean_inputs(dict(FORM, account="1234567890", formats=["google_rsa", "meta_stories_reels"]))
    assert [s["key"] for s in pa.steps_for(inputs)] == ["r:website", "r:account", "a:google_rsa", "a:meta_stories_reels", "f:finish"]
    bare = pa.clean_inputs({"name": "Acme", "product": "implants", "formats": ["meta_feed"]})
    assert [s["key"] for s in pa.steps_for(bare)] == ["a:meta_feed", "f:finish"]
    assert all(s["state"] == "pending" for s in pa.steps_for(inputs))


def test_a_brief_is_wanted_only_for_formats_with_one_and_when_asked():
    on = pa.clean_inputs(dict(FORM, formats=["google_rsa", "meta_feed"]))
    off = pa.clean_inputs(dict(FORM, formats=["google_rsa", "meta_feed"], briefs=False))
    assert not pa.wants_brief(on, "google_rsa") and pa.wants_brief(on, "meta_feed") and not pa.wants_brief(off, "meta_feed")


def test_the_list_row_and_the_kind_test():
    rec = {"id": "a" * 32, "name": "Acme", "status": "done", "created_by": "x@position2.com", "created_at": "t", "inputs": pa.clean_inputs(FORM), "cost": {"usd": 0.12}}
    row = pa.brief(rec)
    assert row["formats"] == ["Google Search", "Meta feed"] and row["cost"] == 0.12 and row["product"].startswith("dental")
    assert pa.is_adcopy(rec) and not pa.is_adcopy({"inputs": {"kind": "keywords"}}) and not pa.is_adcopy(None)


def test_phrases_and_lines():
    assert pa.phrases("a, b;a\nc") == ["a", "b", "c"]
    assert pa.lines_of("one, with comma; two\nthree") == ["one, with comma", "two", "three"]
