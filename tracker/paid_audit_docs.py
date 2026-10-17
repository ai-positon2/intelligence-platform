"""Paid Marketing Agents: Account Audit, the checklist as a document.

`markdown()` writes every check the audit runs (its id, severity, what it looks at, why it matters and what to do) straight from the catalog,
so docs/account-audit-checks.md cannot drift from the code: a test holds the file equal to this output. To refresh the file after changing a check:

    python -c "from tracker import paid_audit_docs as d; open('docs/account-audit-checks.md', 'w').write(d.markdown())"
"""
from __future__ import annotations

from . import paid_audit_google_checks, paid_audit_meta_checks  # noqa: F401  (importing them fills the catalog)
from . import paid_audit_model as model

PLATFORM_TITLE = {"google": "Google Ads", "meta": "Meta Ads"}


def markdown():
    out = ["# Account Audit: every check", "",
           "Generated from the catalog in `tracker/paid_audit_model.py` by `tracker/paid_audit_docs.py`; a test holds this file equal to it.",
           "A check whose input could not be read says \"could not check\" and is never passed or failed. Severity weights: critical 8, high 4, medium 2, low 1.", ""]
    total = {}
    for platform in model.PLATFORMS:
        sections = sorted(((k, v) for k, v in model.SECTIONS.items() if v["platform"] == platform), key=lambda kv: kv[1]["order"])
        checks = [c for c in model.CATALOG.values() if model.SECTIONS[c["section"]]["platform"] == platform]
        total[platform] = len(checks)
        out += ["## %s (%d checks)" % (PLATFORM_TITLE[platform], len(checks)), ""]
        for key, spec in sections:
            rows = sorted((c for c in checks if c["section"] == key), key=lambda c: c["id"])
            out += ["### %s (%d)" % (spec["label"], len(rows)), ""]
            for c in rows:
                out += ["**%s** %s (%s)" % (c["id"], c["title"], c["severity"]), "", "Why it matters: " + c["why"], "", "What to do: " + c["fix"], ""]
    return "\n".join(out).rstrip("\n") + "\n"
