"""Account Audit: the checklist document (docs/account-audit-checks.md) is the catalog, word for word."""
import os

from tracker import paid_audit_docs as d
from tracker import paid_audit_google_checks as g
from tracker import paid_audit_meta_checks as m
from tracker import paid_audit_model as model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "account-audit-checks.md")


def test_the_document_is_what_the_catalog_says():
    assert open(DOC, encoding="utf-8").read() == d.markdown(), "run the command in tracker/paid_audit_docs.py to refresh docs/account-audit-checks.md"


def test_every_check_is_in_the_document_once_with_its_severity_why_and_fix():
    text = d.markdown()
    ids = [cid for cid, _fn in g.CHECKS] + [cid for cid, _fn in m.CHECKS]
    assert len(ids) == len(set(ids)) == len(model.CATALOG)
    for cid in ids:
        spec = model.CATALOG[cid]
        assert text.count("**%s** " % cid) == 1
        assert "**%s** %s (%s)" % (cid, spec["title"], spec["severity"]) in text and "Why it matters: " + spec["why"] in text and "What to do: " + spec["fix"] in text


def test_the_headings_count_the_checks_of_each_platform_and_section():
    text = d.markdown()
    assert "## Google Ads (%d checks)" % len(g.CHECKS) in text and "## Meta Ads (%d checks)" % len(m.CHECKS) in text
    for key, spec in model.SECTIONS.items():
        n = sum(1 for c in model.CATALOG.values() if c["section"] == key)
        assert "### %s (%d)" % (spec["label"], n) in text


def test_there_is_no_em_or_en_dash_in_the_document():
    text = open(DOC, encoding="utf-8").read()
    assert "—" not in text and "–" not in text
