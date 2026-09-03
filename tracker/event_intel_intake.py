"""Step 0.5: read a company's own site and propose the intake it implies.

The recommendation play opens on thirteen fields. Only two of them are
actually required, and nothing on the form says so, but the deeper problem is
who is filling it in: an agency planner answering questions about someone
else's business. "Deal size" and "sales cycle" are not facts they carry
around, so the form stalls on things that are not theirs to know.

Most of it is on the client's own website. This module goes and reads it, and
hands back a filled draft the planner corrects instead of authors.

WHAT THIS MODULE IS NOT ALLOWED TO DO
-------------------------------------

1. **It proposes. It never decides.** Everything here is a draft that a person
   reviews before anything is saved, and nothing it returns is written to a
   profile by this module. The same rule Contact Finder's "Fill filters"
   established: it fills the controls, the user sees every value it set, and
   it stays an accelerator rather than a black box.

2. **The classification is a proposal that must be confirmed by a human, and
   the caller has to enforce that.** `event_intel_rubric.orientation_for`
   raises rather than defaulting, because that one answer decides which side
   of the trade-show floor every later score measures, and a wrong one
   produces a confident, fully-reasoned report about the wrong crowd. That
   rule is about the SYSTEM never picking silently. A model that argues for an
   answer a person then clicks does not break it; a draft that arrives
   pre-accepted does. So the draft carries a reason and a confidence, and the
   page keeps the run button dark until someone chooses.

3. **A field it could not find stays empty and says so.** Pricing and sales
   cycle are usually not published, and a plausible guess at a deal size is
   indistinguishable from a read one once it is sitting in a form field. Every
   field it could not answer is named in `unknown`, which is what lets the
   page show "we could not find this" instead of a blank that looks like an
   oversight.

4. **An ungrounded draft is discarded.** A reply that ran no search, or cites
   no page, is recall about a company rather than a reading of its site. Two
   firms sharing a name is common and the website is the only tell, so a
   recalled draft is exactly how the wrong company's ICP gets filled in. Same
   refusal `event_intel_discover` makes about a whole conference.
"""

from __future__ import annotations

import logging

from . import claude_websearch
from . import event_intel_rubric as rubric

logger = logging.getLogger(__name__)

# One page-read, and the searches around it. Small on purpose: this is one
# company's own site, not a survey, and it runs while somebody watches a
# spinner on a form.
MAX_USES = 5
MAX_TOKENS = 2500

# The fields this module may propose. `client_name` and `website` are what the
# user typed and are never re-drafted: correcting the name someone just typed
# is the one thing they did not ask for.
DRAFT_FIELDS = ("buyer_roles", "verticals", "acv_band", "sales_cycle",
                "geo_scope")

# Caps mirror event_intel_store._PROFILE_TEXT_FIELDS, so a draft cannot arrive
# in a shape the save path would silently truncate differently.
_CAP = 400


def _classification_menu() -> str:
    return "\n".join(
        "  %s = %s. Their buyers stand: %s"
        % (k, rubric.CLASSIFICATION_LABELS[k],
           rubric.CLASSIFICATION_BUYER_PLACE[k])
        for k in rubric.CLASSIFICATIONS)


_SYSTEM = """You read one company's own website and describe who it sells to, \
so that a person can check your reading and correct it. You are filling in a \
form on their behalf. You are not making a decision.

THE COMPANY: {client_name}
THEIR SITE: {website}

FIRST, MAKE SURE IT IS THE RIGHT COMPANY. Two businesses sharing a name is \
common and the website is the only thing that tells them apart. Read the site \
that was given to you. If the name and the site plainly describe different \
companies, say so in `wrong_company` and stop rather than describing whichever \
one you found.

RULES.
1. Use web search and read their own pages: homepage, product, pricing, \
customers, about. Do not answer from memory. A company profile written from \
recall is how the wrong firm's buyers end up on somebody's form.
2. ANY FIELD YOU CANNOT FIND ON A PAGE MUST BE null, and its name must appear \
in `unknown`. This is the most important rule here. A guessed deal size is \
indistinguishable from a read one once it is sitting in a form field, and the \
person checking your work cannot tell which fields you actually confirmed \
unless you say. Leaving a field blank is a good answer.
3. `acv_band` and `sales_cycle` are usually NOT published. Fill them only from \
something concrete you actually read: a pricing page, a published plan, a case \
study naming a contract value or an evaluation length. "Enterprise software so \
probably six figures" is a guess. Leave it null and put it in `unknown`.
4. `buyer_roles` are the job titles that sign or champion the purchase, as the \
site describes them, comma separated. `verticals` are the industries they sell \
INTO, comma separated, not the industry they are in.
5. `geo_scope` is where they actually sell, as evidenced by offices, case \
studies, currencies or stated coverage. It is not where they might like to \
sell.
6. Every field you fill needs a matching entry in `evidence` saying, in one \
short sentence, what you read that told you. A field with no evidence is a \
guess wearing a fact's clothes.
7. `sources` must be URLs you actually opened. At least one.

THE CLASSIFICATION. A person will confirm this before anything runs, so give \
them your best reading and your real reasoning, not a hedge. It decides which \
side of a trade-show floor gets measured, and the four options are:

{classification_menu}

The distinction people get wrong: a B2B company selling to marketing, growth \
or sales teams finds its buyers WORKING the exhibitor booths, because at most \
B2B events every booth is staffed by exactly those people. A B2B company \
selling to any other function finds its buyers in the audience and the session \
tracks instead. Say which of those two the company is, and why, in \
`classification_why`.

Respond with ONLY a JSON object, no prose before or after:
{{"wrong_company": str|null, "what_they_sell": str, \
"classification": str|null, "classification_why": str, \
"classification_confidence": "high"|"medium"|"low", \
"buyer_roles": str|null, "verticals": str|null, "acv_band": str|null, \
"sales_cycle": str|null, "geo_scope": str|null, \
"evidence": {{"field name": "what you read"}}, "unknown": [str], \
"sources": [str], "note": str}}

`what_they_sell` is one plain sentence a reader can check at a glance. It is \
the fastest way for them to notice you read the wrong company, so write it for \
that purpose rather than as a summary.

`note` is anything the person filling this form should know that does not fit \
a field: something ambiguous on the site, two plausible readings, a business \
that has clearly changed shape recently."""


def _err(kind: str, detail: str) -> dict:
    return {"draft": {}, "evidence": {}, "unknown": [], "sources": [],
            "note": "", "what_they_sell": "", "classification": None,
            "classification_why": "", "classification_confidence": None,
            "error": {"kind": kind, "detail": detail[:500]}}


def _text(raw, cap: int = _CAP) -> str | None:
    v = str(raw or "").strip()
    return v[:cap] or None


def draft_profile(client_name: str, website: str) -> dict:
    """Propose an intake for one company. Never raises.

    Returns a dict with `draft` (the fields to put in the form), `evidence`
    (per field, what was read), `unknown` (fields deliberately left empty),
    `classification` plus its reasoning, `sources`, and `error`.

    `classification` is a PROPOSAL. Nothing in this module or its route saves
    it. See the module docstring for why that distinction is the whole point.
    """
    name = str(client_name or "").strip()
    site = str(website or "").strip()
    if not name:
        return _err("bad_request", "A company name is required.")
    if not site.lower().startswith(("http://", "https://")):
        # Not a nicety. The site is the only thing that separates two firms
        # with one name, so a draft without one is guessing which company it
        # is before it starts guessing anything else.
        return _err("bad_request",
                    "A website starting with http:// or https:// is required. "
                    "It is the only thing that tells two companies with the "
                    "same name apart.")

    system = _SYSTEM.format(client_name=name[:200], website=site[:400],
                            classification_menu=_classification_menu())
    user = ("Read %s at %s and fill in the form. Leave blank anything their "
            "own pages do not tell you." % (name[:200], site[:400]))

    res = claude_websearch.ask(system, user, max_uses=MAX_USES,
                              max_tokens=MAX_TOKENS)
    if res.get("error"):
        e = res["error"]
        return _err(e["kind"], e["detail"])

    if not res.get("search_count"):
        return _err("ungrounded",
                    "The model answered without opening a single page, so this "
                    "would be a description of a company with this name rather "
                    "than a reading of the site you gave it.")

    parsed = claude_websearch.extract_json(res.get("text") or "",
                                           require="what_they_sell")
    if not isinstance(parsed, dict):
        logger.warning("event_intel_intake: unparsable draft for %r "
                       "(blocks=%s, stop=%s)", name[:80],
                       res.get("text_block_count"), res.get("stop_reason"))
        return _err("unparsable", "The site was read but the answer could not "
                                  "be understood, so nothing was filled in.")

    sources = [u for u in (parsed.get("sources") or [])
               if isinstance(u, str)
               and u.lower().startswith(("http://", "https://"))][:8]
    if not sources:
        return _err("ungrounded",
                    "The draft cited no page at all, so there is nothing here "
                    "anyone could check it against.")

    wrong = _text(parsed.get("wrong_company"), 600)
    if wrong:
        out = _err("wrong_company", wrong)
        out["sources"] = sources
        return out

    draft = {f: _text(parsed.get(f)) for f in DRAFT_FIELDS}

    # Whatever the model listed, plus anything it left blank without saying so.
    # Both halves matter: the list is what the page shows as "we could not find
    # this", and a field that is empty and unlisted would otherwise read as an
    # oversight by the person filling the form rather than as a real absence.
    unknown = {str(u).strip() for u in (parsed.get("unknown") or [])
               if str(u).strip() in DRAFT_FIELDS}
    unknown |= {f for f in DRAFT_FIELDS if draft[f] is None}

    # Evidence is per field and is only kept for fields that were actually
    # filled. Evidence attached to a blank field is a sentence about something
    # that is not on the form, and the page would have nowhere honest to put it.
    ev_raw = parsed.get("evidence")
    evidence = {}
    if isinstance(ev_raw, dict):
        for f in DRAFT_FIELDS:
            if draft[f] is not None:
                t = _text(ev_raw.get(f), 300)
                if t:
                    evidence[f] = t

    # A filled field with no evidence is a guess wearing a fact's clothes. The
    # prompt asks for one; asking is not the same as getting, so a field that
    # arrives unsupported is emptied rather than shown as read.
    for f in DRAFT_FIELDS:
        if draft[f] is not None and f not in evidence:
            draft[f] = None
            unknown.add(f)

    classification = str(parsed.get("classification") or "").strip()
    if classification not in rubric.CLASSIFICATIONS:
        classification = None

    return {
        "draft": draft,
        "evidence": evidence,
        "unknown": sorted(unknown),
        "what_they_sell": _text(parsed.get("what_they_sell"), 400) or "",
        "classification": classification,
        "classification_why": _text(parsed.get("classification_why"), 600) or "",
        "classification_confidence": (
            str(parsed.get("classification_confidence") or "").strip().lower()[:10]
            or None),
        "sources": sources,
        "note": _text(parsed.get("note"), 600) or "",
        "error": None,
    }
