"""Shared test fixtures for the whole tests/ directory.

Only one thing lives here on purpose: resetting Contact Finder's per-user
rate-limit state between tests. That state is real, in-process, and persists
for the life of the app module -- exactly like it would in a running server.
Many test files reuse the same fake session email across a full-suite run in
one pytest process, so without a reset, tests later in that run start
tripping the very limit meant for a real abusive external caller, not a test
suite. Scoped to just this one dict, not any other in-memory cache: several
others (company-name resolution, employer firmographics) are deliberately
exercised for cross-call persistence WITHIN a single test, and must not be
cleared out from under it.
"""

import os
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app as appmod


@pytest.fixture(autouse=True)
def _reset_cpi_rate_limits():
    appmod._CPI_RATE_STATE.clear()
    yield
    appmod._CPI_RATE_STATE.clear()
