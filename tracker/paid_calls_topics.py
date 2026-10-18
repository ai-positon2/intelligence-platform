"""Paid Marketing Agents: Call Transcript Analyzer, what the calls were about.

CallRail records and transcribes calls (Conversation Intelligence) and gives each one a summary, a lead score, a sentiment and
the practice's own tags (Qualified Lead, Booked Appointment, Not Booked, ...). This module reads a batch of such calls and
says, in plain rules anyone can read and a test can check, WHAT the callers wanted, whether the call ended in an appointment
and, when it did not, the likeliest reason.

No call content ever goes to a language model here, nothing in this module stores anything, and no sentence of a summary or a
transcript is ever returned: the accounts behind this are HIPAA accounts and a summary can hold a patient's name, date of birth
and even a Social Security number. The output is counts, plus a short list of calls that look like lost opportunities described
only by their company, time, source, campaign, length, topics and CallRail's own lead score and link, so a reader opens the call
in CallRail itself, where access is controlled and logged.

How a call is read, in order of trust:
  1. the practice's own tags (Booked Appointment, Schedule booked, Not Booked) settle the outcome;
  2. CallRail's summary, which is cleaner than a transcript;
  3. the transcript, with the greeting every call opens with ("this call may be recorded", "if this is a medical emergency
     hang up and call 911") cut out, because that boilerplate would otherwise make every call an emergency. What the CALLER
     said decides what they wanted; what the AGENT said decides whether an appointment was made.

The rules are keyword rules, and they are wrong sometimes. The page says so and always shows how many calls it could not
place ("Other") so a reader can see how much it is guessing.
"""
from __future__ import annotations

import re

# ── What a call was about: the first rule that matches wins ─────────────────

INTENTS = [
    ("not_a_patient", "Vendor, wrong number or job seeker"),
    ("emergency", "Pain or an emergency"),
    ("new_patient", "New patient"),
    ("appointment", "Existing appointment"),
    ("insurance", "Insurance or billing"),
    ("records", "Records, forms or referrals"),
    ("service", "Asking about a treatment"),
    ("no_conversation", "No real conversation"),
    ("other", "Other"),
]
INTENT_LABEL = dict(INTENTS)

_R = lambda p: re.compile(p, re.I)

NOT_A_PATIENT = _R(r"\b(marketing|seo\b|search engine|web ?site design|solicit|vendor|sales ?(rep|call|pitch)|on behalf of (a|an|the) (company|agency)|"
                   r"wrong (number|office|practice|location)|(job|position|employment) (opening|application|inquiry)|applying for|resume|hiring|"
                   r"looking for work|recruit|merchant services|business loan)")
EMERGENCY = _R(r"\b(pain(ful)?|toothache|tooth ?ache|hurts?|hurting|swollen|swelling|abscess|infect(ed|ion)|broke(n)?|chipped|cracked|knocked out|"
               r"bleeding|throbbing|emergenc(y|ies)|urgent)\b")          # closed with a word boundary: "painless sedation" is not pain
NEW_PATIENT = _R(r"\b(new patients?|become a patient|first (time|visit|appointment)|never (been|seen)|looking for a (new )?dentist|"
                 r"accepting new|taking new|consultation|second opinion|(just )?moved (to|here)|establish(ing)? care|referred (by|to))")
APPOINTMENT = _R(r"\b(re-?schedul\w*|cancel\w*|confirm\w*|running late|late for|my appointment|appointment (on|for|at|tomorrow|next|today)|"
                 r"(move|change) (my|the|an) appointment|appointment (time|reminder))")
INSURANCE = _R(r"\b(insurance|delta|metlife|cigna|aetna|guardian|humana|anthem|united ?healthcare|medicaid|medicare|masshealth|husky|"
               r"billing|balance|statement|invoice|payment|owe|refund|copay|co-pay|out of pocket)")
RECORDS = _R(r"\b(records?|x-?rays? (sent|to be sent|request)|referral|prescription|refill|forms?|paperwork|letter|fax|medical clearance|"
             r"transfer(red)? (my )?(file|chart))")
SERVICE = _R(r"\b(implants?|invisalign|aligners?|braces|orthodon\w*|whitening|veneers?|cleanings?|crowns?|root canal|extractions?|dentures?|"
             r"wisdom teeth|fillings?|bridges?|check-?ups?|exam)")

# ── What was talked about: every one that matches (a call can raise several) ─

TOPICS = [
    ("new_patient", "New patient", NEW_PATIENT),
    ("emergency", "Pain or an emergency", EMERGENCY),
    ("scheduling", "Scheduling", _R(r"\b(schedul\w*|appointments?|book(ed|ing)?|availab\w*|openings?|come in|next available|time slot)")),
    ("price", "Price or cost", _R(r"\b(price|prices|pricing|cost|costs|how much|estimate|quote|payment plan|financing|free|discount|special|offer)")),
    ("insurance", "Insurance", _R(r"\b(insurance|delta|metlife|cigna|aetna|guardian|humana|anthem|united ?healthcare|medicaid|medicare|masshealth|"
                                  r"husky|in[- ]network|out[- ]of[- ]network|copay|co-pay)")),
    ("implants", "Implants and dentures", _R(r"\b(implants?|dentures?|partials?|all-on|full mouth)")),
    ("ortho", "Braces and aligners", _R(r"\b(braces|invisalign|aligners?|orthodon\w*|retainers?)")),
    ("cosmetic", "Whitening and cosmetic", _R(r"\b(whitening|veneers?|cosmetic|smile makeover|bonding)")),
    ("general", "Cleanings and check-ups", _R(r"\b(cleanings?|check-?ups?|exam|x-?rays?|fillings?|cavit\w*|gum)")),
    ("kids", "Children", _R(r"\b(child|children|kids?|son|daughter|pediatric|my (boy|girl)|toddler|teen\w*)")),
    ("after_hours", "After hours or voicemail", _R(r"\b(after hours|after-hours|closed|voicemail|leave a message|left a message)")),
]

# ── How it ended ────────────────────────────────────────────────────────────

BOOKED_TAGS = {"booked appointment", "schedule booked"}
NOT_BOOKED_TAGS = {"not booked"}
BOOKED_TEXT = _R(r"((successfully )?(scheduled|booked|set up|rescheduled|confirmed|made)( (an|the|her|his|their|your|a|new))*( new)?( patient)? "
                 r"(appointment|visit|consultation|exam)|appointment (is|was|has been) (scheduled|booked|confirmed|set)|see you (then|soon|on)|"
                 r"you('| a)re (all )?(set|scheduled|booked)|has been (scheduled|booked))")
NOT_BOOKED_TEXT = _R(r"\b(call(ed)? (him|her|them|you|the caller) back|callback|call back|will call back|return(ed)? (his|her|their|the) call|"
                     r"left (a )?(voicemail|message)|voicemail|unable to|was not able to|could not|couldn't|no availability|fully booked|no openings|"
                     r"does not accept|do not accept|doesn't accept|not accept(ing)?|out of network|not in network|hung up|disconnected|"
                     r"requested (a )?call|asked (for )?(someone|a call)|will contact|someone will (call|contact|reach)|did not (schedule|book)|"
                     r"no appointment)")

# why it did not end in an appointment, the first that matches
REASONS = [
    ("no_availability", "No appointment time available", _R(r"\b(no availability|fully booked|no openings|booked (up|out)|no (open )?(slots|time)|next (available|opening) (is|was|would be)|"
                                                           r"not available until)")),
    ("insurance", "Insurance not accepted", _R(r"\b(does not accept|do not accept|doesn't accept|not accept(ing)?|out of network|not in network|no insurance)")),
    ("transfer", "Call transfer failed", _R(r"\b(unable to transfer|transfer(red)? (failed|unsuccessful)|could not transfer|couldn't transfer|"
                                            r"(transfer|transferred).{0,60}(failed|did not|didn't|disconnect|unsuccess|no answer|voicemail)|unable to (handle|schedule|book))")),
    ("callback", "Left to call back", _R(r"\b(callback|call back|call(ed)? (him|her|them|you) back|will call|someone will|requested (a )?call|left (a )?(voicemail|message)|"
                                         r"voicemail|will contact|reach out)")),
    ("price", "Asked about price", _R(r"\b(price|prices|pricing|cost|how much|estimate|quote|payment plan|financing)")),
]
REASON_LABEL = {k: v for k, v, _p in REASONS}
REASON_LABEL["other"] = "No clear reason in the call"

# the sentences every call opens with: they say nothing about the caller
BOILERPLATE = _R(r"(may be recorded|being recorded|quality and training|medical emergency|hang up and call 911|call 911|thank you for calling|"
                 r"your call is important|press \d|for (english|spanish)|para espa|this call (may|will) be)")

# The calls that are people looking for care, the ones advertising pays for: a new patient, someone in pain, someone asking about a
# treatment. A patient moving an appointment or asking about a bill is not a lead and is never counted as a lost one.
WANTS_APPOINTMENT = {"new_patient", "emergency", "service"}

PHONE = re.compile(r"(?<![\w])(\+?\d[\d\s().\-]{6,}\d)(?![\w])")
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(\.[\w\-]+)+")
TURN = re.compile(r"(?:^|\s)(Agent|Caller|Customer|Speaker \d+)\s*:\s*", re.I)


def scrub(text):
    """The text with phone numbers and email addresses taken out (a summary often repeats the caller's own number)."""
    t = EMAIL.sub("[email]", str(text or ""))
    return PHONE.sub("[number]", t)


def turns(transcript):
    """[(who, text)] from "Agent: ... Caller: ..." with who "agent" or "caller"."""
    s = str(transcript or "")
    parts = TURN.split(s)
    out = []
    for i in range(1, len(parts) - 1, 2):
        who = parts[i].lower()
        out.append(("agent" if who == "agent" else "caller", parts[i + 1].strip()))
    return out


def _strip_boilerplate(text):
    sentences = re.split(r"(?<=[.!?])\s+", str(text or ""))
    return " ".join(s for s in sentences if not BOILERPLATE.search(s))


NEGATION = _R(r"\b(no|not|never|without|unable|cannot|can't|couldn't|wasn't|weren't|isn't|hasn't|haven't|didn't|doesn't|don't|nor)\b|n't\b")


def _said_booked(text):
    """True when the text says an appointment was made. "No appointment was scheduled because the office was full" matches the booked wording and is the
    opposite of it, so a match with a negation just before it (or inside it) does not count."""
    text = text or ""
    for m in BOOKED_TEXT.finditer(text):
        clause = re.split(r"[.,;:!?]", text[max(0, m.start() - 30):m.start()])[-1]       # a "no" in an earlier clause ("Not a problem, ...") is not about it
        if not NEGATION.search(clause + m.group(0)):
            return True
    return False


def read_call(call):
    """What one call was about and how it ended.

    `call` is {summary, transcript, tags: [names], duration, answered}. Returns {intent, topics, outcome, reason, words}:
    outcome is "booked", "not_booked" or "unknown", and reason only says something for a call that wanted an appointment
    and did not get one."""
    summary = scrub(call.get("summary"))
    ts = turns(call.get("transcript"))
    caller = _strip_boilerplate(" ".join(t for w, t in ts if w == "caller")) if ts else ""
    agent = _strip_boilerplate(" ".join(t for w, t in ts if w == "agent")) if ts else ""
    if not ts and call.get("transcript"):
        caller = _strip_boilerplate(call.get("transcript"))
    said = (summary + " " + caller).strip()
    tags = {str(t).strip().lower() for t in (call.get("tags") or [])}
    words = len(caller.split()) if ts else len(said.split())
    dur = call.get("duration") or 0

    if not said or (words < 4 and not summary) or (dur and dur < 15 and not summary):
        intent = "no_conversation"
    elif NOT_A_PATIENT.search(said):
        intent = "not_a_patient"
    elif EMERGENCY.search(said):
        intent = "emergency"
    elif NEW_PATIENT.search(said):
        intent = "new_patient"
    elif APPOINTMENT.search(said):
        intent = "appointment"
    elif INSURANCE.search(said):
        intent = "insurance"
    elif RECORDS.search(said):
        intent = "records"
    elif SERVICE.search(said):
        intent = "service"
    else:
        intent = "other"

    topics = [k for k, _l, p in TOPICS if intent != "no_conversation" and p.search(said)]

    evidence = summary + " " + agent
    if tags & BOOKED_TAGS:
        outcome = "booked"
    elif tags & NOT_BOOKED_TAGS:
        outcome = "not_booked"
    elif _said_booked(evidence):
        outcome = "booked"
    elif NOT_BOOKED_TEXT.search(evidence):
        outcome = "not_booked"
    else:
        outcome = "unknown"

    reason = ""
    if intent in WANTS_APPOINTMENT and outcome != "booked":
        reason = next((k for k, _l, p in REASONS if p.search(evidence)), "other")
    return {"intent": intent, "topics": topics, "outcome": outcome, "reason": reason, "words": words}


# ── A batch of calls ────────────────────────────────────────────────────────

LEAD_BUCKETS = [("high", "scored 60 and over", 60), ("middle", "scored 30 to 59", 30), ("low", "scored under 30", 0)]
MAX_LOST = 40


def _score(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def analyze(calls):
    """Counts and the lost-opportunity list for calls of the shape paid_calls.parse_call gives:
    {id, start, company, source, campaign, duration, answered, summary, transcript, tags, sentiment, score, url}.

    Only calls with something to read (a summary or a transcript) are counted as read; the rest are counted in `sampled`."""
    read, intents, topics, tag_counts, sent = [], {}, {}, {}, {}
    by_source, reasons, lost, scores = {}, {}, [], []
    for c in calls:
        if not (c.get("summary") or c.get("transcript")):
            continue
        r = read_call(c)
        read.append(r)
        i = intents.setdefault(r["intent"], {"n": 0, "booked": 0, "notBooked": 0, "lost": 0})
        i["n"] += 1
        if r["outcome"] == "booked":
            i["booked"] += 1
        elif r["outcome"] == "not_booked":
            i["notBooked"] += 1
        for t in r["topics"]:
            topics[t] = topics.get(t, 0) + 1
        for t in c.get("tags") or []:
            tag_counts[t] = tag_counts.get(t, 0) + 1
        if c.get("sentiment"):
            sent[c["sentiment"]] = sent.get(c["sentiment"], 0) + 1
        s = _score(c.get("score"))
        if s is not None:
            scores.append(s)
        src = by_source.setdefault(c.get("source") or "Unknown", {"n": 0, "wantsAppt": 0, "booked": 0, "scoreSum": 0.0, "scored": 0})
        src["n"] += 1
        if r["intent"] in WANTS_APPOINTMENT:
            src["wantsAppt"] += 1
            if r["outcome"] == "booked":
                src["booked"] += 1
        if s is not None:
            src["scoreSum"] += s
            src["scored"] += 1
        if r["reason"]:
            i["lost"] += 1
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
            lost.append({"id": c.get("id"), "start": c.get("start"), "company": c.get("company"), "source": c.get("source"),
                         "campaign": c.get("campaign"), "duration": c.get("duration"), "intent": r["intent"], "reason": r["reason"],
                         "topics": r["topics"], "score": s, "sentiment": c.get("sentiment"), "url": c.get("url")})
    n = len(read)
    wants = sum(v["n"] for k, v in intents.items() if k in WANTS_APPOINTMENT)
    booked = sum(v["booked"] for k, v in intents.items() if k in WANTS_APPOINTMENT)
    lost.sort(key=lambda x: (-(x["score"] if x["score"] is not None else -1), x["start"] or ""))
    return {
        "sampled": len(calls), "read": n,
        "intents": [dict(key=k, label=l, **intents.get(k, {"n": 0, "booked": 0, "notBooked": 0, "lost": 0})) for k, l in INTENTS if k in intents],
        "topics": sorted(({"key": k, "label": l, "n": topics[k], "share": round(topics[k] / n, 4) if n else 0} for k, l, _p in TOPICS if k in topics),
                         key=lambda t: -t["n"]),
        "wantsAppointment": wants, "booked": booked, "bookRate": round(booked / wants, 4) if wants else None,
        "reasons": sorted(({"key": k, "label": REASON_LABEL[k], "n": v} for k, v in reasons.items()), key=lambda x: -x["n"]),
        "tags": sorted(({"name": k, "n": v} for k, v in tag_counts.items()), key=lambda x: -x["n"])[:12],
        "sentiment": [{"label": k, "n": v} for k, v in sorted(sent.items(), key=lambda kv: -kv[1])],
        "score": {"avg": round(sum(scores) / len(scores), 1) if scores else None, "scored": len(scores),
                  "buckets": [{"key": k, "label": l, "n": sum(1 for x in scores if _bucket(x) == k)} for k, l, _lo in LEAD_BUCKETS]},
        "bySource": sorted(({"source": k, "n": v["n"], "wantsAppt": v["wantsAppt"], "booked": v["booked"],
                             "bookRate": round(v["booked"] / v["wantsAppt"], 4) if v["wantsAppt"] else None,
                             "avgScore": round(v["scoreSum"] / v["scored"], 1) if v["scored"] else None} for k, v in by_source.items()),
                           key=lambda x: -x["n"]),
        "lost": lost[:MAX_LOST], "lostTotal": len(lost),
    }


def _bucket(score):
    for k, _l, lo in LEAD_BUCKETS:
        if score >= lo:
            return k
    return "low"
