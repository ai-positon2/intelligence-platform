"""Paid Marketing Agents: Landing Page & Relevancy Check, the checklist as a document.

`markdown()` writes every check (its id, severity, why it matters and what to do) straight from the catalog, so
docs/landing-page-check-checks.md cannot drift from the code: a test holds the file equal to this output. To refresh the file after changing a check:

    python -c "from tracker import paid_lp_docs as d; open('docs/landing-page-check-checks.md', 'w').write(d.markdown())"
"""
from __future__ import annotations

from . import paid_lp_checks as checks  # (importing it fills the catalog)
from . import paid_lp_model as model


def markdown():
    out = ["# Landing Page & Relevancy Check: every check", "",
           "Generated from the catalog in `tracker/paid_lp_model.py` by `tracker/paid_lp_docs.py`; a test holds this file equal to it.",
           "Every check is run on each landing page, except the ones marked for one platform, which run only for a page that platform's ads send people to. "
           "A check whose input could not be read says \"could not check\" and is never passed or failed. "
           "Severity weights: critical 8, high 4, medium 2, low 1.", ""]
    out += ["%d checks in %d sections." % (len(model.CATALOG), len(model.SECTIONS)), ""]
    for key, spec in sorted(model.SECTIONS.items(), key=lambda kv: kv[1]["order"]):
        rows = [c for c in model.CATALOG.values() if c["section"] == key]
        out += ["## %s (%d)" % (spec["label"], len(rows)), ""]
        for c in rows:
            only = "Runs for pages Google's ads reach only." if c["id"] in checks.GOOGLE_ONLY else "Runs for pages Meta's ads reach only." if c["id"] in checks.META_ONLY else ""
            out += ["**%s** %s (%s)" % (c["id"], c["title"], c["severity"]), ""] + ([only, ""] if only else []) + ["Why it matters: " + c["why"], "", "What to do: " + c["fix"], ""]
    return "\n".join(out).rstrip("\n") + "\n"
