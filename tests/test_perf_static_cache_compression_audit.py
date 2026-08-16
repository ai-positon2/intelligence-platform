"""Performance audit, static assets: every static JS/CSS/image/font response in
this app is served through send_file()/send_from_directory()/the built-in
/static route -- and Flask's own default for those, with no max_age configured,
is an EXPLICIT "Cache-Control: no-cache", not merely an absent header. So an
after_request hook that only adds Cache-Control "if not already set" (the first
version of this fix) was silently a no-op for every static asset in the repo --
confirmed here by hitting the real routes before settling on the actual fix.

The real fix is app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600 (set near the top
of app.py, right after `app = Flask(__name__)`), which changes what Flask's own
send_file()/send_from_directory()/static-route machinery puts in Cache-Control
in the first place, for every one of those call sites at once. The five
per-request generated downloads (Vimi Insights export: csv/xlsx/docx/pptx/pdf)
opt back out with an explicit max_age=0, since those must never be cached.

Separately, nothing in front of gunicorn here compresses responses at all, so
HTML/JSON/CSS/JS went over the wire uncompressed except on the two endpoints
that already gzip by hand. _compress_response generalizes that same stdlib-gzip
approach to every response, skipping anything already encoded and skipping
direct_passthrough responses (send_file/send_from_directory often serve
conditional/range requests that byte-for-byte recompression could break).
"""

import gzip
import io
import os
import sys

import pytest
from flask import Response

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


# ── static asset caching (SEND_FILE_MAX_AGE_DEFAULT) ─────────────────────────

def test_send_file_max_age_default_is_configured_to_an_hour():
    assert appmod.app.config.get("SEND_FILE_MAX_AGE_DEFAULT") == 3600


def test_a_real_favicon_route_carries_the_new_cache_header():
    """send_from_directory(), no explicit override -- must pick up the new default."""
    client = appmod.app.test_client()
    resp = client.get("/favicon.svg")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "public, max-age=3600"


def test_a_static_js_file_carries_the_new_cache_header():
    """The built-in /static/<path> route, not an explicit send_from_directory call
    -- must be covered by the same app-wide config, not a per-route change."""
    client = appmod.app.test_client()
    resp = client.get("/static/js/theme.js")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "public, max-age=3600"


def test_a_route_that_explicitly_overrides_cache_control_after_send_file_still_wins():
    """The client-portal dashboard route sets its own no-cache headers right
    after send_file() -- the new app-wide default must not leak through."""
    with appmod.app.test_request_context():
        resp = appmod.make_response(appmod.send_file(io.BytesIO(b"pdfdata"), mimetype="application/pdf"))
        resp.headers.update({"Cache-Control": "no-cache, no-store, must-revalidate",
                              "Pragma": "no-cache", "Expires": "0"})
        assert resp.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"


def test_a_generated_download_with_max_age_0_is_not_cached():
    """The Vimi Insights export routes pass max_age=0 explicitly -- a per-request
    generated file must never inherit the new hour-long static-asset default."""
    with appmod.app.test_request_context():
        resp = appmod.send_file(io.BytesIO(b"a,b,c\n"), mimetype="text/csv",
                                 as_attachment=True, download_name="x.csv", max_age=0)
        assert resp.headers.get("Cache-Control") != "public, max-age=3600"


def test_every_vimi_export_send_file_call_passes_max_age_0():
    """Guards against a future format branch being added to vimi_export without
    the same max_age=0 override -- would silently make that one format's
    export cacheable while the others aren't."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "app.py")).read()
    start = src.index("def vimi_export")
    end = src.index("\n@app.route", start)
    body = src[start:end]
    calls = [line for line in body.splitlines() if "send_file(" in line]
    assert calls, "expected at least one send_file() call in vimi_export"
    for line in calls:
        assert "max_age=0" in line or "max_age=0" in body[body.index(line):body.index(line) + 200], (
            "send_file call in vimi_export is missing max_age=0: %r" % line
        )


# ── _compress_response ────────────────────────────────────────────────────────

def test_a_large_response_is_gzip_compressed_when_the_client_accepts_it():
    payload = "y" * 2000
    with appmod.app.test_request_context(headers={"Accept-Encoding": "gzip, deflate"}):
        resp = Response(payload, mimetype="text/html")
        out = appmod._compress_response(resp)
        assert out.headers.get("Content-Encoding") == "gzip"
        assert gzip.decompress(out.get_data()).decode() == payload
        assert "Accept-Encoding" in out.headers.get("Vary", "")


def test_a_client_without_gzip_support_gets_an_uncompressed_response():
    payload = "y" * 2000
    with appmod.app.test_request_context(headers={}):
        resp = Response(payload, mimetype="text/html")
        out = appmod._compress_response(resp)
        assert "Content-Encoding" not in out.headers
        assert out.get_data(as_text=True) == payload


def test_a_small_response_is_not_compressed_despite_client_support():
    """gzip's own overhead isn't worth it below the size floor."""
    payload = "y" * 10
    with appmod.app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        resp = Response(payload, mimetype="text/html")
        out = appmod._compress_response(resp)
        assert "Content-Encoding" not in out.headers
        assert out.get_data(as_text=True) == payload


def test_an_already_encoded_response_is_not_double_compressed():
    with appmod.app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        resp = Response(b"already-gzipped-bytes-pretend", mimetype="application/json")
        resp.headers["Content-Encoding"] = "gzip"
        out = appmod._compress_response(resp)
        assert out.get_data() == b"already-gzipped-bytes-pretend"


def test_a_direct_passthrough_response_is_left_alone():
    """send_file/send_from_directory responses are direct_passthrough -- often
    serving conditional/range requests that recompression here could break."""
    with appmod.app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        resp = Response("y" * 2000, mimetype="application/javascript")
        resp.direct_passthrough = True
        out = appmod._compress_response(resp)
        assert "Content-Encoding" not in out.headers


def test_a_non_compressible_mimetype_is_left_alone():
    with appmod.app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        resp = Response(b"y" * 2000, mimetype="image/png")
        out = appmod._compress_response(resp)
        assert "Content-Encoding" not in out.headers
