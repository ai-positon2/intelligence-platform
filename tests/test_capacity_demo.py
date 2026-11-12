"""Dental Capacity Demo: the anonymized copy of the Slot Checker.

The whole point of the page is that it can be shown without naming the client,
so the main check is that none of the real practice, brand or city names from
the Slot Checker's own snapshot appear anywhere in what the page serves.
"""
import json
import os
import re
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import app as appmod  # noqa: E402

_DIR = os.path.join(_ROOT, "data", "capacity_demo")
_PATHS = ["/p2/paid-marketing/capacity-demo",
          "/p2/paid-marketing/capacity-demo/data",
          "/p2/paid-marketing/capacity-demo/insights"]


def _client(email=None):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.mark.parametrize("path", _PATHS)
def test_signed_out_visitors_are_not_served(path):
    r = _client().get(path)
    assert r.status_code in (302, 401, 403)


def test_a_position2_user_gets_the_page_and_its_data():
    c = _client("reporting@position2.com")
    page = c.get(_PATHS[0])
    assert page.status_code == 200 and b"Dental Group Capacity Dashboard" in page.data
    data = c.get(_PATHS[1]).get_json()
    assert len(data["practices"]) == 82 and data["totals"]
    assert c.get(_PATHS[2]).get_json() == {"configured": False}


def _real_names():
    with open(os.path.join(_ROOT, "data", "slot_checker_snapshot.json"), encoding="utf-8") as fh:
        snap = json.load(fh)
    names = {"Gentle Dental", "gentledental", "Great Hill", "Dynamic Dental",
             "Southern Connecticut", "Dental Care Associates", "42 North", "42-north",
             "Jarvis", "jarvisanalytics", "Slot Checker"}
    for loc in snap["locations"]:
        names.update(v for k in ("brand", "account", "name", "city") for v in [loc.get(k)] if v)
    return names


def test_no_real_name_appears_in_anything_the_page_serves():
    blob = ""
    for f in os.listdir(_DIR):
        with open(os.path.join(_DIR, f), encoding="utf-8") as fh:
            blob += fh.read()
    found = sorted(n for n in _real_names()
                   if re.search(r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])", blob,
                                0 if n.isupper() else re.I))
    assert not found, "real names in the demo: %s" % found
