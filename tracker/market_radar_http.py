"""Market Radar: the one HTTP read every detector uses. No model here.

Every read goes through event_intel_http's address checks (no private or
reserved destinations, every redirect checked again) with the TLS settings
and user agent the site reader already proved on Railway. It never raises:
a detector needs to know HOW a read failed (refused, missing, timed out)
to say so in its coverage line, because "nothing found" and "could not
look" must never read the same.
"""
from __future__ import annotations

import gzip
import json

import requests

from .event_intel_http import public_get, public_post
from .market_radar_site import TIMEOUT, UA, tls_context

MAX_BODY = 8_000_000


def get(url, *, limit=MAX_BODY, accept="*/*", post=None, headers=None, timeout=TIMEOUT):
    """{"status", "http", "body", "note", "final_url", "truncated"}.

    status is ok (HTTP 200), not_found (404 or 410), blocked (401, 403, 429
    or 5xx), or error (anything else, including no answer). `post` is a JSON
    body to send instead of a GET; a redirected POST is an error."""
    out = {"status": "error", "http": None, "body": "", "note": "", "final_url": url,
           "truncated": False}
    hdrs = dict({"User-Agent": UA, "Accept": accept, "Accept-Encoding": "gzip, deflate"},
                **(headers or {}))
    try:
        if post is None:
            r = public_get(url, timeout=timeout, stream=True, headers=hdrs,
                           ssl_context=tls_context())
        else:
            hdrs["Content-Type"] = "application/json"
            r = public_post(url, json.dumps(post).encode("utf-8"), timeout=timeout,
                            headers=hdrs, ssl_context=tls_context())
    except requests.Timeout:
        out["note"] = "timed out after %ss" % timeout
        return out
    except ValueError as e:
        out["note"] = "address refused (%s)" % str(e)[:80]
        return out
    except Exception as e:
        out["note"] = "could not be reached (%s)" % type(e).__name__
        return out
    try:
        out["http"] = r.status_code
        out["final_url"] = str(getattr(r, "url", "") or url)
        if r.status_code in (404, 410):
            out["status"], out["note"] = "not_found", "not found (HTTP %s)" % r.status_code
            return out
        if r.status_code in (401, 403, 429) or r.status_code >= 500:
            out["status"], out["note"] = "blocked", "refused (HTTP %s)" % r.status_code
            return out
        if r.status_code != 200:
            out["note"] = "unexpected HTTP %s" % r.status_code
            return out
        chunks, total = [], 0
        for chunk in r.iter_content(65536):
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                out["truncated"] = True
                break
        raw = b"".join(chunks)
        if raw[:2] == b"\x1f\x8b":           # a .xml.gz sitemap served as bytes
            try:
                raw = gzip.decompress(raw)
            except Exception:
                out["note"] = "a compressed file that would not open"
                return out
        out["body"] = raw.decode("utf-8", errors="replace")
        out["status"] = "ok"
    except Exception as e:
        out["note"] = "reading the answer failed (%s)" % type(e).__name__
    finally:
        r.close()
    return out


def get_json(url, **kw):
    """(data, read). data is None when the read failed or the body is not
    JSON; read["note"] then says which."""
    read = get(url, accept="application/json", **kw)
    if read["status"] != "ok":
        return None, read
    try:
        return json.loads(read["body"]), read
    except ValueError:
        read["status"], read["note"] = "error", "the answer was not JSON"
        return None, read
