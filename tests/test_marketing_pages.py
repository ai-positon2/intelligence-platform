"""Tests for the public marketing pages: /security removal, and that the real
Position2 privacy policy and terms of use render without stray compliance
certification claims (HIPAA/SOC 2/ISO 27001) that don't apply to this product.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


@pytest.fixture
def client():
    return appmod.app.test_client()


def test_security_page_is_gone(client):
    assert client.get("/security").status_code == 404


def test_no_page_links_to_security(client):
    """The page used to be linked from the home teaser, the resources card and
    the footer nav; all three must be repointed, not just the route removed."""
    for path in ("/", "/resources", "/privacy", "/terms"):
        body = client.get(path).data.decode("utf-8")
        assert "/security" not in body, "%s still links to the removed page" % path


def test_privacy_page_has_no_compliance_certification_claims(client):
    """HIPAA/SOC 2/ISO 27001 are audited certifications this product does not
    hold; a flat claim of them is different from citing HIPAA by name as one of
    several statutes the CCPA's definition of personal information excludes,
    which is the one legitimate mention this page keeps."""
    body = client.get("/privacy").data.decode("utf-8")
    for term in ("SOC 2", "SOC2", "ISO 27001", "ISO27001"):
        assert term not in body
    # The one surviving HIPAA mention is a citation, not a claim; assert it
    # reads as a citation rather than "compliant"/"aware" language.
    assert "HIPAA" in body
    assert "HIPAA compliant" not in body and "HIPAA-compliant" not in body
    assert "HIPAA-aware" not in body and "HIPAA aware" not in body


def test_privacy_and_terms_reference_position2_inc(client):
    """These are meant to be Position2's real corporate policy and terms, not
    the placeholder plain-language summaries this page shipped with before."""
    for path, marker in (("/privacy", "Position2, Inc. takes your privacy seriously"),
                         ("/terms", "Position2, Inc.")):
        body = client.get(path).data.decode("utf-8")
        assert marker in body


def test_terms_links_to_our_own_privacy_page_not_a_different_product(client):
    """The source terms point at thearena.ai's own privacy policy; ours must
    point at this site's /privacy instead."""
    body = client.get("/terms").data.decode("utf-8")
    assert 'href="/privacy"' in body
    assert "thearena.ai" not in body


def test_no_agent_is_named_or_slugged_for_hipaa(client):
    """A product-name-level compliance claim (e.g. "HIPAA-Aware ... Auditor")
    is exactly the kind of hardcore claim that had to come off pre-login pages.
    That agent card lives on the healthcare industry page, not /agents."""
    for path in ("/agents", "/industries/healthcare"):
        body = client.get(path).data.decode("utf-8")
        assert "hipaa" not in body.lower(), "%s still names/slugs a HIPAA agent" % path


def test_legal_body_is_not_a_scroll_reveal_target(client):
    """The site's fade-in-on-scroll animation adds an 'in' class via an
    IntersectionObserver with threshold 0.12: the callback only fires once the
    element's visible share of itself reaches 12%. A short card can cross that
    easily, but the full policy text is one div many viewport-heights tall, so
    its own height keeps the achievable ratio under 0.12 forever, and it never
    fires. The div rendered at permanent opacity:0, which is what "the privacy
    and terms pages are blank" actually was. It must never carry that class."""
    for path in ("/privacy", "/terms"):
        body = client.get(path).data.decode("utf-8")
        assert 'class="legal-body"' in body
        assert 'class="legal-body reveal"' not in body
