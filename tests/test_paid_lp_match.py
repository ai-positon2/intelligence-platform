"""Landing Page & Relevancy Check: do the keywords, ads and page say the same thing (tracker/paid_lp_match.py)."""
from tracker import paid_lp_match as m


def page(title="", h1=(), h2=(), h3=(), text="", words=None):
    return {"title": title, "h1": list(h1), "h2": list(h2), "h3": list(h3), "text": text, "wordCount": len(text.split()) if words is None else words}


# ── Words ───────────────────────────────────────────────────────────────────

def test_stems_let_plurals_and_endings_meet():
    for a, b in (("dentists", "dentist"), ("companies", "company"), ("licensed", "license"), ("licensing", "licensed"), ("heating", "heat"), ("services", "service"),
                 ("glasses", "glass"), ("repairs", "repair"), ("creating", "create")):
        assert m.stem(a) == m.stem(b), (a, b)


def test_stems_leave_words_that_only_look_plural_alone():
    for w in ("business", "bonus", "analysis", "glass", "gas", "this"):
        assert m.stem(w) == w, w


def test_words_that_mean_the_same_on_a_page_and_in_a_search_fold_to_one():
    assert len({m.stem(w) for w in ("price", "prices", "pricing", "cost", "costs", "fee", "fees", "rates")}) == 1
    assert m.stem("attorney") == m.stem("lawyer") == m.stem("lawyers")


def test_words_fold_case_accents_apostrophes_and_ampersands():
    assert m.words("Café Owner's  Plumbing & Heating") == [m.stem(w) for w in ("cafe", "owners", "plumbing", "and", "heating")]
    assert m.words("") == [] and m.words(None) == []


def test_content_drops_filler_and_how_people_search_and_repeats():
    assert m.content("best dentist near me") == ["dentist"]
    assert m.content("the cost of invisalign in boston") == ["price", "invisalign", "boston"]
    assert m.content("dentist dentists Dentist") == ["dentist"]
    assert m.content("near me") == [] and m.content("") == []


def test_content_keeps_online_because_it_can_be_what_is_sold():
    assert m.stem("online") in m.content("online pharmacy")


def test_a_pasted_sentence_is_cut_to_its_first_words():
    sentence = " ".join("word%d" % i for i in range(40))
    assert len(m.content(sentence)) == m.MAX_PHRASE_WORDS


# ── A keyword on the page ───────────────────────────────────────────────────

def vocabs(p):
    return m.page_vocabs(p)


def test_a_keyword_is_in_the_headline_on_the_page_partly_or_not_at_all():
    p = page(title="Invisalign Boston", h1=["Straighter teeth"], text="We offer implants and whitening. Our dentists are kind.")
    prom, full = vocabs(p)
    assert m.hit("invisalign boston", prom, full) == "headline"
    assert m.hit("Straighter teeth", prom, full) == "headline"
    assert m.hit("dental implants whitening", prom, full) == "partial"        # "dental" is not on the page, the other two are
    assert m.hit("whitening implants", prom, full) == "page"
    assert m.hit("root canal", prom, full) == "none"


def test_a_single_word_keyword_is_all_or_nothing():
    prom, full = vocabs(page(text="plumbing and heating"))
    assert m.hit("plumbers", prom, full) == "none"
    assert m.hit("plumbing", prom, full) == "page"


def test_a_keyword_with_no_content_words_is_not_judged():
    prom, full = vocabs(page(text="anything"))
    assert m.hit("near me", prom, full) is None and m.hit("", prom, full) is None


def test_the_headline_means_the_title_and_every_heading_level():
    p = page(title="Acme", h1=["One"], h2=["Two"], h3=["Three"], text="four")
    prom, full = vocabs(p)
    assert all(m.hit(w, prom, full) == "headline" for w in ("acme", "one", "two", "three"))
    assert m.hit("four", prom, full) == "page"


# ── The keyword list ────────────────────────────────────────────────────────

def test_phrase_stats_weigh_each_keyword_by_what_it_spent():
    p = page(title="Invisalign Boston", text="invisalign boston braces clear aligners")
    rows = [{"text": "invisalign boston", "cost": 80.0}, {"text": "braces", "cost": 15.0}, {"text": "root canal", "cost": 5.0}]
    s = m.phrase_stats(rows, p)
    assert s["judged"] == 3
    assert round(s["onPage"], 2) == 0.95 and round(s["inHeadline"], 2) == 0.80
    assert s["missing"] == ["root canal"] and s["partial"] == []


def test_phrase_stats_list_the_biggest_missing_first_and_separate_the_partial_ones():
    p = page(text="dental implants")
    rows = [{"text": "veneers", "cost": 5.0}, {"text": "crowns", "cost": 50.0}, {"text": "dental crowns", "cost": 10.0}]
    s = m.phrase_stats(rows, p)
    assert s["missing"] == ["crowns", "veneers"] and s["partial"] == ["dental crowns"]


def test_phrase_stats_skip_phrases_with_nothing_to_look_for_and_say_so():
    s = m.phrase_stats([{"text": "near me", "cost": 9.0}], page(text="x"))
    assert s["judged"] == 0 and s["onPage"] is None and s["inHeadline"] is None
    assert m.phrase_stats([], page())["judged"] == 0


def test_phrase_stats_weigh_a_list_that_spent_nothing_evenly():
    p = page(text="alpha beta")
    s = m.phrase_stats([{"text": "alpha"}, {"text": "gamma"}], p)
    assert s["onPage"] == 0.5


# ── Headlines ───────────────────────────────────────────────────────────────

def test_the_best_echo_is_the_headline_the_page_carries_most_of():
    prom = m.vocab("Emergency dentist Boston open today")
    share, which = m.best_echo(["Book a Visit", "Emergency Dentist in Boston", "Cheap Fillings"], prom)
    assert share == 1.0 and which == "Emergency Dentist in Boston"


def test_a_one_word_headline_counts_only_when_there_is_no_longer_one():
    prom = m.vocab("dentist")
    assert m.best_echo(["Dentist", "Teeth Whitening Deals"], prom)[0] == 0.0
    assert m.best_echo(["Dentist"], prom) == (1.0, "Dentist")


def test_a_headline_with_none_of_its_words_on_the_page_is_still_named():
    assert m.best_echo(["Teeth Whitening Deals", "Brighter Smile"], m.vocab("emergency dentist")) == (0.0, "Teeth Whitening Deals")


def test_no_usable_headline_is_no_echo():
    assert m.best_echo([], set()) == (0.0, "")
    assert m.best_echo(["near me", ""], {"x"}) == (0.0, "")


# ── What an ad promises ─────────────────────────────────────────────────────

def keys(offers):
    return [(o["kind"], o["key"]) for o in offers]


def test_the_promises_in_ad_copy_are_found_once_each():
    got = m.offers_in(["Cleaning from $99", "Free Consultation | 20% off", "Open 24/7", "Same-day crowns, no obligation", "Only $99 today"])
    assert keys(got) == [("price", "99"), ("percent", "20"), ("free", m.stem("consultation")), ("hours", "24"), ("sameday", "sameday"), ("nocost", "no obligation")]


def test_prices_with_commas_and_cents_are_one_number():
    assert keys(m.offers_in(["From $1,299.00", "Now $49.50"])) == [("price", "1299"), ("price", "49.5")]


def test_free_followed_by_filler_is_not_a_promise_and_a_word_that_only_contains_free_is_not_free():
    assert m.offers_in(["Free for all", "Carefree living", "Free and fair", "Stress-Free Dentistry", "Worry-free care"]) == []


def test_what_is_free_is_every_word_up_to_the_first_filler():
    got = m.offers_in(["Get a Free Dental Consultation", "Free consultation for new patients", "Free Teeth Whitening Kit Today"])
    assert [(o["text"], o["key"]) for o in got] == [("free dental consultation", "dental|consultation"), ("free consultation", "consultation"), ("free teeth whitening kit", "teeth|whiten|kit")]


def test_no_promise_in_plain_copy():
    assert m.offers_in(["Trusted dentist in Boston", "", None]) == []


def promised(text, kind, key="x"):
    return {"kind": kind, "key": key, "text": text}


def test_a_price_is_on_the_page_when_the_figure_follows_a_dollar_sign():
    p = page(text="Cleanings start at $ 99 for new patients. Whitening $1,299.00.")
    assert m.on_page(promised("$99", "price", "99"), p)
    assert m.on_page(promised("$1,299", "price", "1299"), p)
    assert not m.on_page(promised("$9", "price", "9"), p)             # the 9 inside 99 is not $9
    assert not m.on_page(promised("$199", "price", "199"), p)


def test_a_price_written_in_dollars_or_usd_counts_and_a_bare_number_does_not():
    assert m.on_page(promised("$79", "price", "79"), page(text="Only 79 dollars a month"))
    assert m.on_page(promised("$79", "price", "79"), page(text="79 USD per visit"))
    assert not m.on_page(promised("$79", "price", "79"), page(text="Call 79 Main Street"))


def test_a_price_with_cents_matches_the_page_written_with_a_trailing_zero():
    assert m.on_page(promised("$49.50", "price", "49.5"), page(text="Visit $49.50"))


def test_a_discount_is_on_the_page_when_the_same_percent_is():
    p = page(text="Save 20% on your first visit")
    assert m.on_page(promised("20%", "percent", "20"), p)
    assert not m.on_page(promised("30%", "percent", "30"), p)
    assert not m.on_page(promised("2%", "percent", "2"), p)
    assert m.on_page(promised("15%", "percent", "15"), page(text="take 15 percent off"))


def test_a_free_thing_is_on_the_page_when_free_sits_next_to_it():
    assert m.on_page(promised("free consultation", "free", m.stem("consultation")), page(text="Book your free initial consultation today"))
    assert not m.on_page(promised("free consultation", "free", m.stem("consultation")), page(text="Free parking. Consultation by appointment only."), )
    assert not m.on_page(promised("free consultation", "free", m.stem("consultation")), page(text="consultation is free"))   # a looser reading than the ad's; the check says what was missing


def test_a_free_promise_is_kept_when_any_of_its_words_follows_free_on_the_page():
    ad = m.offers_in(["Get a Free Dental Consultation"])[0]
    assert m.on_page(ad, page(text="Book your free consultation today"))
    assert m.on_page(ad, page(text="Free dental exam for new patients"))
    assert not m.on_page(ad, page(text="Free parking. Consultations by appointment."))
    assert not m.on_page(ad, page(text="Stress-free dentistry"))


def test_service_promises_are_found_in_their_other_wordings():
    assert m.on_page(promised("24/7", "hours", "24"), page(text="We are here around the clock"))
    assert m.on_page(promised("24/7", "hours", "24"), page(text="Open 24 hours"))
    assert not m.on_page(promised("24/7", "hours", "24"), page(text="Open 9 to 5"))
    assert m.on_page(promised("same day", "sameday", "sameday"), page(text="Same-day appointments"))
    assert m.on_page(promised("no obligation", "nocost", "no obligation"), page(text="Your quote is free"))
    assert not m.on_page(promised("no obligation", "nocost", "no obligation"), page(text="Pay upfront"))


def test_an_unknown_promise_kind_is_never_found():
    assert not m.on_page({"kind": "mystery", "key": "x", "text": "x"}, page(text="x"))


def test_offer_status_pairs_each_promise_with_whether_the_page_has_it():
    p = page(text="Cleanings $99")
    out = m.offer_status(m.offers_in(["$99 cleanings", "Free whitening"]), p)
    assert [(o["kind"], ok) for o, ok in out] == [("price", True), ("free", False)]


# ── Thin pages ──────────────────────────────────────────────────────────────

def test_a_page_with_few_words_is_too_thin_to_judge_a_match_on():
    assert m.too_thin(page(words=m.MIN_WORDS - 1)) and not m.too_thin(page(words=m.MIN_WORDS))
    assert m.too_thin({})
