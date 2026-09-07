"""Who gets emailed when someone submits "Request access" on the marketing
site, and whether the admin email diagnostic reports the same list.

This is a notification list, not access control: being on it grants nothing,
and it deliberately does not track ADMIN_EMAILS. It lived as two independent
copies of one hardcoded string -- the real send in _demo_request_to_email and
/p2/admin/email-test's diagnostic -- which is the shape of bug this repo has
been bitten by more than once: someone adds a recipient, the diagnostic keeps
reporting the old list, and the page that exists to tell you who gets the
mail is the thing lying about it. Both now resolve through
_demo_notify_recipients(), and this file holds them to that.
"""

import os
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

_EXPECTED = (
    "krishna.ladha@position2.com",
    "abhilash.dg@position2.com",
    "sudheer.d@position2.com",
    "sparikh@position2.com",
    "pushpendra.k@position2.com",
    "nikhil.ashok@position2.com",
)


def _parsed(csv):
    return [a.strip() for a in csv.split(",") if a.strip()]


def test_the_default_recipient_list_is_exactly_the_intended_team(monkeypatch):
    monkeypatch.delenv("DEMO_NOTIFY_EMAIL", raising=False)
    assert _parsed(appmod._demo_notify_recipients()) == list(_EXPECTED)


def test_every_default_recipient_is_a_lowercase_position2_address(monkeypatch):
    monkeypatch.delenv("DEMO_NOTIFY_EMAIL", raising=False)
    for addr in _parsed(appmod._demo_notify_recipients()):
        assert addr == addr.lower(), addr
        assert addr.endswith("@position2.com"), addr


def test_no_duplicate_recipients_so_nobody_gets_the_same_mail_twice(monkeypatch):
    monkeypatch.delenv("DEMO_NOTIFY_EMAIL", raising=False)
    addrs = _parsed(appmod._demo_notify_recipients())
    assert len(addrs) == len(set(addrs)), addrs


def test_the_env_var_overrides_the_hardcoded_list_entirely(monkeypatch):
    """The override is total, not additive. It is the reason adding a name to
    the literal does not necessarily reach that person in production, so the
    behaviour is pinned rather than assumed."""
    monkeypatch.setenv("DEMO_NOTIFY_EMAIL", "someone.else@position2.com")
    assert appmod._demo_notify_recipients() == "someone.else@position2.com"
    for addr in _EXPECTED:
        assert addr not in appmod._demo_notify_recipients()


def test_an_empty_env_var_falls_back_instead_of_emailing_nobody(monkeypatch):
    """An unset variable and a variable set to "" must behave the same way: a
    blank recipient list would make the notification silently reach no one."""
    monkeypatch.setenv("DEMO_NOTIFY_EMAIL", "")
    assert _parsed(appmod._demo_notify_recipients()) == list(_EXPECTED)


def test_the_real_send_and_the_admin_diagnostic_read_one_shared_source():
    """The whole point of the shared resolver: /p2/admin/email-test exists to
    report who the notification actually goes to, so it must not carry its own
    copy of the list. Asserted against the source, since proving it by
    behaviour would mean really sending mail.
    """
    src = open(appmod.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    # The literal itself appears exactly once: in the fallback constant.
    assert src.count("krishna.ladha@position2.com, abhilash.dg@position2.com") == 1
    # And both readers go through the resolver rather than os.environ directly.
    assert src.count("_demo_notify_recipients()") >= 3   # 1 def + 2 call sites
    assert 'os.environ.get("DEMO_NOTIFY_EMAIL"' in src
    assert src.count('os.environ.get("DEMO_NOTIFY_EMAIL"') == 1
