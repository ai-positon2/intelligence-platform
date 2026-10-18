"""Shared by the tests that look at the Paid Marketing listings.

Five agents are withdrawn from the listings (PAID_HIDDEN_AGENTS in app.py) and
come back when that dict is emptied, so a test that counts or looks for a card
has to follow the switch rather than assume either state.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "FLASK_SECRET_KEY"):
    os.environ.setdefault(_k, "test")
import app as appmod  # noqa: E402

_BLOCK = re.compile(r"\{% if '([a-z\-]+)' not in paid_hidden %\}(.*?)\{% endif %\}", re.S)


def hidden():
    return set(appmod.PAID_HIDDEN_SLUGS)


def visible(raw_template):
    """The template with every block of a hidden agent dropped and the
    wrappers of the shown ones removed: what a signed-in person's page holds."""
    return _BLOCK.sub(lambda m: "" if m.group(1) in hidden() else m.group(2), raw_template)


def rendered(path, email="ana@position2.com"):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c.get(path).get_data(as_text=True)


def stats(html_block):
    return dict((label, int(n)) for n, label in
                re.findall(r'class="card-stat"><span>(\d+)</span>\s*(\w+)', html_block))
