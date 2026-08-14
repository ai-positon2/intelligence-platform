"""_cpi_resolve_company_name can spend 2 credits in one request (a first
lookup by name, then a web-assisted retry) yet both toasts that report that
spend on the results page hard-coded the singular "Apollo credit", unlike
every other credit toast in this file, which already pluralizes. "2 Apollo
credit" reached the screen on the one path that can legitimately cost more
than one.
"""

import os
import re

_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "static", "js", "company_people_intelligence.js")


def _js():
    return open(_JS, encoding="utf-8").read()


def test_the_company_choice_toast_pluralizes_credits():
    body = _js()
    line = next(l for l in body.splitlines() if "Looked up " in l and "Apollo credit" in l)
    assert re.search(r'credit"\+\(d\.credits===1\?"":"s"\)', line), line


def test_the_resolved_company_toast_pluralizes_credits():
    body = _js()
    line = next(l for l in body.splitlines() if 'note += " ("+d.credits' in l)
    assert re.search(r'credit"\+\(d\.credits===1\?"":"s"\)', line), line
