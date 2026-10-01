"""extract_json reads a reply whose only defect is an invalid escape.

Live run 24 (2026-10-01): two of nine famous-event audits came back as
"its answer could not be read". Both replies were complete JSON apart from
a backslash before a closing curly quote, which is not a JSON escape.
"""
from tracker.claude_websearch import extract_json

# The shape of the Money20/20 Asia audit reply, shortened.
REPLY = ('{"verdict": "kept", "alternative": null, "why": "the organizer states '
         'attendees include \u201c\\"banks, payments, fintechs and retailers come '
         'together to make breakthroughs\\\u201d, which maps to Stripe."}')


def test_a_backslash_before_a_curly_quote_no_longer_loses_the_reply():
    got = extract_json(REPLY, require="verdict")
    assert got["verdict"] == "kept"
    assert got["why"].endswith('breakthroughs\u201d, which maps to Stripe.')
    assert '\u201c"banks' in got["why"]


def test_valid_escapes_are_untouched():
    raw = '{"a": "tab\\tnl\\nq\\"u\\u00e9 slash\\/ back\\\\ end"}'
    assert extract_json(raw) == {"a": 'tab\tnl\nq"u\u00e9 slash/ back\\ end'}


def test_an_escaped_backslash_before_a_curly_quote_stays_a_backslash():
    # "\\" then U+201D is a valid escaped backslash and a plain quote mark.
    assert extract_json('{"a": "C:\\\\\u201d"}') == {"a": 'C:\\\u201d'}


def test_the_repair_is_used_only_when_the_text_does_not_decode():
    assert extract_json('{"a": "x \\q y", "b": 1}') == {"a": "x q y", "b": 1}
    assert extract_json('{"a": broken \\q}') is None


def test_the_envelope_is_still_required_after_a_repair():
    assert extract_json('{"row": "x\\\u201d"}', require="verdict") is None


def test_a_repair_keeps_an_escaped_backslash_beside_a_bad_escape():
    # The repair runs here (the second backslash breaks decoding); a scan
    # that looked one character ahead instead of consuming escape pairs would
    # turn the first, valid, "\\" before U+201D into an invalid one.
    raw = '{"a": "C:\\\\\u201d and x\\\u201d y"}'
    assert extract_json(raw) == {"a": 'C:\\\u201d and x\u201d y'}
