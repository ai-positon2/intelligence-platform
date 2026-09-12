"""Performance audit: every Sheets client in this file is built via
build("sheets", "v4", credentials=creds, cache_discovery=False, ...). Passing
cache_discovery=False WITHOUT static_discovery=True (the state nine of the ten
call sites were in) forces googleapiclient to fetch the API discovery document
over the network on every single call -- an entire extra network round-trip
paid by every Sheets-backed page/agent on every cache miss, on top of the
actual data read(s). _va_sheets_service_st's own docstring already names this
("forces static discovery so building a fresh service does no network
round-trip"); this test guards that every build("sheets", ...) call site in the
file actually does so, so a new call site copy-pasted from an old one can't
silently reintroduce the extra round-trip.
"""

import os
import re

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def test_every_sheets_client_build_call_uses_static_discovery():
    src = open(_APP_PY, encoding="utf-8").read()
    calls = re.findall(r'build\(\s*"sheets"\s*,\s*"v4".*?\)', src, re.S)
    assert calls, "expected at least one Sheets client build() call in app.py"
    missing = [c for c in calls if "static_discovery=True" not in c]
    assert not missing, (
        "found build(\"sheets\", ...) call(s) without static_discovery=True -- "
        "each one pays an extra discovery-document network round-trip on every "
        "call: %r" % missing
    )
