"""Market Radar, Phase 8: sending the weekly update, by email and Slack.

Email goes through the Gmail API with the platform's service account
(GOOGLE_SA_JSON, impersonating GMAIL_SENDER), the same path the
platform's access-request emails use: Railway blocks outbound SMTP, which
is the fallback only where SMTP_HOST/USER/PASS are set. Slack goes through
the platform's bot (SLACK_BOT_TOKEN, chat.postMessage); the bot has to be
a member of the channel.

Each send returns nothing or raises. attempt() turns that into the record
stored with the update: sent, not_configured (a variable is unset), or
failed with the reason, so "nothing arrived" always has an answer.
"""
from __future__ import annotations

import base64
import json
import os
import socket
from contextlib import contextmanager
from email.message import EmailMessage

import requests


class NotConfigured(RuntimeError):
    pass


def attempt(fn):
    try:
        fn()
        return {"status": "sent"}
    except NotConfigured as e:
        return {"status": "not_configured", "error": str(e)[:300]}
    except Exception as e:
        return {"status": "failed", "error": ("%s: %s" % (type(e).__name__, e))[:300]}


def ready():
    """Which senders are set up on this deployment, without sending."""
    return {"email": bool(os.environ.get("GMAIL_SENDER") and os.environ.get("GOOGLE_SA_JSON"))
            or bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
                    and os.environ.get("SMTP_PASS")),
            "slack": bool(os.environ.get("SLACK_BOT_TOKEN"))}


def _message(subject, text, html_body, to, sender):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    return msg


def _gmail_reason(e):
    """The Gmail API's refusal, in words someone can act on."""
    text = str(e)
    if "unauthorized_client" in text:
        return ("Google refused the service account: it is not allowed to send mail as %s. A "
                "Google Workspace admin has to grant its client id the scope "
                "https://www.googleapis.com/auth/gmail.send under domain-wide delegation "
                "(Admin console, Security, API controls)." % os.environ.get("GMAIL_SENDER", "the sender"))
    return "%s: %s" % (type(e).__name__, text[:200])


def send_email(subject, text, html_body, to):
    """Gmail API first; SMTP when that is not set up or refuses (as the
    platform's own access-request email does). Raises with both reasons
    when neither sends."""
    sender = os.environ.get("GMAIL_SENDER", "")
    sa = os.environ.get("GOOGLE_SA_JSON", "")
    gmail_error = None
    if sender and sa:
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            creds = service_account.Credentials.from_service_account_info(
                json.loads(sa), scopes=["https://www.googleapis.com/auth/gmail.send"]).with_subject(sender)
            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
            raw = base64.urlsafe_b64encode(_message(subject, text, html_body, to, sender).as_bytes()).decode()
            svc.users().messages().send(userId="me", body={"raw": raw}).execute()
            return
        except Exception as e:
            gmail_error = _gmail_reason(e)
    host, user, pwd = (os.environ.get(k, "") for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))
    if not (host and user and pwd):
        if gmail_error:
            raise RuntimeError(gmail_error + " SMTP is not set up as a fallback.")
        raise NotConfigured("no email sender is set up (GMAIL_SENDER with GOOGLE_SA_JSON, "
                            "or SMTP_HOST/SMTP_USER/SMTP_PASS)")
    try:
        _smtp(subject, text, html_body, to, host, user, pwd)
    except Exception as e:
        raise RuntimeError(((gmail_error + " ") if gmail_error else "") +
                           "SMTP failed too: %s: %s" % (type(e).__name__, str(e)[:150]))


@contextmanager
def _ipv4():
    """Railway containers have no IPv6 route: SMTP to a host with an IPv6
    address fails with "Network is unreachable" (as app._force_ipv4 says)."""
    orig = socket.getaddrinfo

    def v4(host, port, family=0, type=0, proto=0, flags=0):
        return orig(host, port, socket.AF_INET, type, proto, flags) or \
            orig(host, port, family, type, proto, flags)
    socket.getaddrinfo = v4
    try:
        yield
    finally:
        socket.getaddrinfo = orig


def _smtp(subject, text, html_body, to, host, user, pwd):
    with _ipv4():
        _smtp_send(subject, text, html_body, to, host, user, pwd)


def _smtp_send(subject, text, html_body, to, host, user, pwd):
    import smtplib
    import ssl
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    msg = _message(subject, text, html_body, to, os.environ.get("SMTP_FROM", "") or user)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15, context=ctx) as srv:
            srv.login(user, pwd)
            srv.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as srv:
            srv.starttls(context=ctx)
            srv.login(user, pwd)
            srv.send_message(msg)


def send_slack(channel, text, blocks):
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise NotConfigured("SLACK_BOT_TOKEN is not set")
    r = requests.post("https://slack.com/api/chat.postMessage",
                      headers={"Authorization": "Bearer " + token},
                      json={"channel": channel, "text": text, "blocks": blocks,
                            "unfurl_links": False}, timeout=15)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok or not body.get("ok"):
        # "not_in_channel": the bot has to be invited to the channel first.
        raise RuntimeError("Slack refused: %s" % (body.get("error") or "HTTP %s" % r.status_code))
