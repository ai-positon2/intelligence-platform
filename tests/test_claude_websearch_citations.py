"""Citation markup never reaches a caller of claude_websearch.

With web_search on, the model marks every span it lifted off a page with an
inline cite tag carrying the index of the result it came from. That is
presentation markup for a chat client. Every caller of this module hands the
reply to json.loads and then stores the strings, so the tag travelled into
the database and out onto a rendered page, where readers saw it printed in
the middle of a sentence.

Deleting it is not enough either: what it wraps is a direct quote from
someone else's page and the sentence around it reads as one, so dropping the
marks turns a quotation into our own claim about their content.
"""

from tracker import claude_websearch as CW

# Built rather than written out. This is the one string the file turns on,
# and a literal is what an editor or a paste is most likely to soften.
_O = "<" + "cite"
_C = "<" + "/cite>"


def _tag(idx="1-2"):
    return _O + ' index="%s">' % idx


def test_a_cite_pair_becomes_the_quotation_it_was_wrapping():
    out = CW.strip_citation_markup(
        "attendees came from " + _tag() + "47 states" + _C + " last year.")
    assert out == "attendees came from \u201c47 states\u201d last year."


def test_quote_marks_the_model_already_wrote_are_not_doubled():
    """The model usually puts its own quotes inside the tag. Adding a second
    pair produces the tell-tale doubled mark that says a machine did this."""
    out = CW.strip_citation_markup(_tag() + '"47 states"' + _C)
    assert out == "\u201c47 states\u201d"


def test_a_smart_quote_inside_the_tag_is_not_doubled_either():
    out = CW.strip_citation_markup(_tag() + "\u201c47 states\u201d" + _C)
    assert out == "\u201c47 states\u201d"


def test_an_empty_tag_leaves_nothing_behind():
    assert CW.strip_citation_markup("before " + _tag() + _C + " after") == "before  after"


def test_an_unpaired_opener_is_removed_rather_than_shown():
    """There is no quotation to recover, but there is still markup the reader
    must never see."""
    out = CW.strip_citation_markup("it gathers " + _tag() + "them.")
    assert "cite" not in out.lower()
    assert out == "it gathers them."


def test_a_stray_closer_is_removed():
    out = CW.strip_citation_markup("them" + _C + " gather.")
    assert "cite" not in out.lower()


def test_two_tags_in_one_sentence_both_survive():
    out = CW.strip_citation_markup(
        _tag("1-2") + "47 states" + _C + " and " + _tag("1-3") + "9 countries" + _C)
    assert out == "\u201c47 states\u201d and \u201c9 countries\u201d"


def test_text_with_no_markup_is_returned_untouched():
    s = 'A note with "quotes" and <angle> brackets and the word citation in it.'
    assert CW.strip_citation_markup(s) == s


def test_nothing_and_none_are_safe():
    assert CW.strip_citation_markup("") == ""
    assert CW.strip_citation_markup(None) == ""


def test_the_stripper_survives_the_escaped_form_a_json_reply_carries():
    """The reply is JSON before it is parsed, so the tag arrives with its
    attribute quotes backslash-escaped. Stripping BEFORE json.loads is
    deliberate: those inner quotes are also the thing most likely to break
    the parse if the model forgets to escape one."""
    raw = '{"why": "came from ' + _O + ' index=\\"1-2\\">47 states' + _C + '"}'
    out = CW.strip_citation_markup(raw)
    assert "cite" not in out.lower()
    import json
    assert json.loads(out)["why"] == "came from \u201c47 states\u201d"
