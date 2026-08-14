"""_cpi_verify_rows checks HQ against `company_locations` for a people search
and `locations` for a company search -- two different filter keys for the
same "hq" rejection reason (see app.py). The JS side's REJECT_FILTER table
mapped "hq" to the single fixed key "company_locations", so on the Companies
tab, clicking "Remove that filter" on an HQ rejection called
cpiDropFilter("company_locations") -- a key nothing on that tab's panel is
ever stored under (fcLocation writes to "locations", not "company_locations",
per COMBO_SPECS) -- cleared nothing, and re-ran the identical search, which
reproduced the identical rejection while telling the user "Removed that
filter. Searching again."

Fixed by resolving "hq" against STATE.entity instead of a fixed string, via a
shared rejectFilterKey() used by both rejectedActions() (decides whether a
reason renders as a clickable button at all) and cpiRelax() (acts on it) --
so the two cannot disagree about which reasons are clickable, which is what
broke the first time an entity-specific reason existed and only one of the
two functions was updated for it.
"""

import os

_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "static", "js", "company_people_intelligence.js")


def _js():
    return open(_JS, encoding="utf-8").read()


def _js_function(name):
    """Handles both `function name(){...}` and `window.name = function(){...}`,
    since this file declares some functions the second way (anything the inline
    onclick= HTML needs to reach)."""
    body = _js()
    for needle in ("function %s(" % name, "window.%s = function(" % name):
        idx = body.find(needle)
        if idx != -1:
            start = idx
            break
    else:
        raise AssertionError("function %s not found" % name)
    open_brace = body.index("{", start)
    depth = 0
    for i in range(open_brace, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                return body[open_brace:i + 1]
    raise AssertionError("unbalanced braces in function %s" % name)


def test_hq_is_no_longer_a_fixed_entry_in_reject_filter():
    """A fixed entry here is wrong on one tab no matter which of the two real
    keys it names -- it must be resolved dynamically instead."""
    body = _js()
    table_start = body.index("var REJECT_FILTER = {")
    table_end = body.index("};", table_start)
    table = body[table_start:table_end]
    assert '"hq"' not in table and "hq:" not in table


def test_reject_filter_key_resolves_hq_by_entity():
    body = _js_function("rejectFilterKey")
    assert '"hq"' in body
    assert "STATE.entity" in body
    assert '"locations"' in body and '"company_locations"' in body


def test_rejected_actions_uses_the_shared_resolver_not_the_raw_table():
    body = _js_function("rejectedActions")
    assert "rejectFilterKey(k)" in body
    assert "REJECT_FILTER[k]" not in body, (
        "must not read the raw table directly -- that is exactly what made "
        "an hq button render on the Companies tab as if company_locations "
        "were still the filter to clear"
    )


def test_cpi_relax_uses_the_shared_resolver():
    body = _js_function("cpiRelax")
    assert "rejectFilterKey(reasonKey)" in body


def test_the_new_company_side_reasons_are_still_wired_to_the_right_keys():
    """domain and excluded_keyword were added to REJECT_FILTER alongside this
    fix -- confirm they didn't silently regress while hq was being pulled out
    of the same table."""
    body = _js()
    table_start = body.index("var REJECT_FILTER = {")
    table_end = body.index("};", table_start)
    table = body[table_start:table_end]
    assert 'domain:"domains"' in table
    assert 'excluded_keyword:"exclude_keywords"' in table
