"""Paid Marketing Agents: Call Transcript Analyzer, how a call is read (tracker/paid_calls_topics.py).

What is pinned here: the greeting every call opens with does not make it an emergency; what the CALLER said decides what they
wanted and what the AGENT said decides whether an appointment was made; the practice's own tags outrank the text; a patient moving
an appointment is never a lost lead; the reason an appointment was not made; the counts add up; and that no sentence of a summary or
transcript, and no phone number or email, ever leaves the module.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_calls_topics as T  # noqa: E402

GREETING = ("Agent: Thank you for calling Great Hill Dental. This call may be recorded for quality and training purposes. "
            "If this is a medical emergency, please hang up and call 911. How can I help you? ")


def call(caller="", agent="", summary="", tags=(), duration=90, **k):
    transcript = ""
    if caller or agent:
        transcript = GREETING + "Caller: " + caller + " Agent: " + agent
    c = {"id": k.pop("id", "CAL1"), "start": "2026-10-10T09:00:00", "company": "Great Hill", "source": "Google Ads", "campaign": "brand",
         "duration": duration, "summary": summary, "transcript": transcript, "tags": list(tags), "sentiment": "Neutral", "score": 50, "url": "https://app.callrail.com/x"}
    c.update(k)
    return c


# ── Reading the transcript ─────────────────────────────────────────────────

def test_the_turns_of_a_transcript_are_split_by_who_spoke():
    ts = T.turns("Agent: Hello there. Caller: Hi, I need an appointment. Agent: Sure thing. Customer: Thanks")
    assert [w for w, _t in ts] == ["agent", "caller", "agent", "caller"]
    assert ts[1][1] == "Hi, I need an appointment."
    assert T.turns("") == [] and T.turns("no speakers here") == []


def test_the_greeting_every_call_opens_with_does_not_make_it_an_emergency():
    r = T.read_call(call(caller="Hi, I would like to book a cleaning please for next week.", agent="Sure, I can help you with that."))
    assert r["intent"] != "emergency" and "emergency" not in r["topics"]
    r2 = T.read_call(call(caller="I have a terrible toothache and my face is swollen.", agent="I am sorry, let me find you a time today."))
    assert r2["intent"] == "emergency" and "emergency" in r2["topics"]


def test_what_the_agent_said_does_not_decide_what_the_caller_wanted():
    r = T.read_call(call(caller="Hello, do you do cleanings?", agent="Yes. Are you a new patient? We have a new patient special on whitening."))
    assert r["intent"] == "service" and "new_patient" not in r["topics"]


def test_phone_numbers_and_emails_are_taken_out_of_text():
    t = T.scrub("Call him back on (857) 310-9334 or 8573-1093-3485 or jane.doe@example.com today")
    assert "857" not in t and "9334" not in t and "jane" not in t and "[number]" in t and "[email]" in t
    assert T.scrub(None) == "" and T.scrub("Seen on the 7th at 5:45 PM") == "Seen on the 7th at 5:45 PM"


# ── What a call was about ──────────────────────────────────────────────────

@pytest.mark.parametrize("caller,summary,expected", [
    ("", "A sales rep called to sell the practice marketing and SEO services.", "not_a_patient"),
    ("", "The caller had the wrong number and was looking for a different office.", "not_a_patient"),
    ("", "A caller asked about a job opening and wanted to send a resume.", "not_a_patient"),
    ("My son broke his front tooth playing football and it is bleeding.", "", "emergency"),
    ("", "Customer called about severe tooth pain and swelling.", "emergency"),
    ("Hi, I am looking for a new dentist, are you accepting new patients?", "", "new_patient"),
    ("", "Customer called to ask if the practice is accepting new patients and takes Delta Dental.", "new_patient"),
    ("I need to reschedule my appointment on Friday please.", "", "appointment"),
    ("", "Customer called to cancel their appointment and to say they were running late.", "appointment"),
    ("I have a question about the balance on my last statement.", "", "insurance"),
    ("Can you send my x-rays to my new orthodontist? I need my records.", "", "records"),
    ("How much do you charge for Invisalign?", "", "service"),
    ("Hello? Anyone there?", "", "no_conversation"),
])
def test_the_intent_of_a_call(caller, summary, expected):
    c = call(caller=caller, agent="Okay.", summary=summary) if caller else call(summary=summary)
    assert T.read_call(c)["intent"] == expected


def test_pain_outranks_a_new_patient_and_a_vendor_outranks_everything():
    assert T.read_call(call(summary="A new patient called with a toothache wanting to be seen today."))["intent"] == "emergency"
    assert T.read_call(call(summary="Marketing company called about SEO for new patients and pain points."))["intent"] == "not_a_patient"


def test_a_very_short_call_with_nothing_to_read_is_no_conversation():
    assert T.read_call(call(duration=8))["intent"] == "no_conversation"
    assert T.read_call(call(caller="Hello?", agent="Hello?", duration=40))["intent"] == "no_conversation"
    assert T.read_call(call(duration=300))["intent"] == "no_conversation"            # nothing at all to read


def test_topics_are_every_one_that_came_up_not_just_the_first():
    r = T.read_call(call(summary="Customer asked how much a new patient exam costs and whether Delta Dental insurance is accepted for their daughter."))
    assert {"new_patient", "price", "insurance", "kids", "general"} <= set(r["topics"])
    assert T.read_call(call(duration=8))["topics"] == []


# ── How it ended ───────────────────────────────────────────────────────────

def test_the_practices_own_tags_settle_the_outcome_over_the_text():
    base = dict(summary="Customer called to ask about becoming a new patient. The agent scheduled an appointment for Tuesday.")
    assert T.read_call(call(tags=["Not Booked"], **base))["outcome"] == "not_booked"
    assert T.read_call(call(tags=["Booked Appointment"], summary="Customer called to become a new patient. The agent left a voicemail."))["outcome"] == "booked"
    assert T.read_call(call(tags=["Schedule booked"], summary="new patient"))["outcome"] == "booked"
    assert T.read_call(call(tags=["Qualified Lead"], summary="new patient inquiry"))["outcome"] == "unknown"        # not a booking tag


@pytest.mark.parametrize("text,outcome", [
    ("The agent scheduled a new patient appointment for Monday at 3.", "booked"),
    ("Appointment was confirmed for Thursday. See you then!", "booked"),
    ("The agent successfully rescheduled the appointment.", "booked"),
    ("Agent offered a callback because the office was closed.", "not_booked"),
    ("The agent was unable to transfer the call and left a voicemail.", "not_booked"),
    ("The practice does not accept her insurance.", "not_booked"),
    ("Customer asked about new patient hours.", "unknown"),
])
def test_how_a_call_ended_from_its_text(text, outcome):
    assert T.read_call(call(summary="Customer called as a new patient. " + text))["outcome"] == outcome


@pytest.mark.parametrize("text,reason", [
    ("There was no availability this week, the office is fully booked.", "no_availability"),
    ("The practice does not accept her insurance plan.", "insurance"),
    ("The agent was unable to transfer the call to the front desk.", "transfer"),
    ("The agent said someone will call her back tomorrow.", "callback"),
    ("The caller asked how much the exam costs and hung up.", "price"),
    ("The caller said thanks and ended the call.", "other"),
])
def test_why_a_new_patient_did_not_book(text, reason):
    r = T.read_call(call(summary="A new patient called to get seen. " + text))
    assert r["outcome"] != "booked" and r["reason"] == reason


def test_a_booked_call_has_no_reason_and_a_call_that_is_not_a_lead_never_has_one():
    assert T.read_call(call(summary="A new patient called and the agent scheduled an appointment for Friday."))["reason"] == ""
    r = T.read_call(call(summary="Customer called to cancel their appointment. Left a voicemail afterwards."))
    assert r["intent"] == "appointment" and r["reason"] == ""
    assert T.read_call(call(summary="A vendor called about marketing and the agent could not help."))["reason"] == ""


# ── A batch of calls ───────────────────────────────────────────────────────

def batch():
    return [
        call(id="A", summary="A new patient called wanting to be seen. The agent scheduled an appointment for Monday.", tags=["Booked Appointment"], score=72, source="Google Ads"),
        call(id="B", summary="A new patient called about new patient exams. The practice does not accept her insurance.", score=82, source="Google Ads"),
        call(id="C", summary="Customer in pain called for an emergency visit. Someone will call back.", score=62, source="Directories"),
        call(id="D", summary="Customer called to reschedule their appointment.", score=8, source="Google Ads"),
        call(id="E", summary="", duration=5),                       # nothing to read
        call(id="F", summary="Customer asked about Invisalign pricing and hung up.", score=None, source="Directories", sentiment="Slightly positive"),
    ]


def test_the_counts_of_a_batch_add_up():
    out = T.analyze(batch())
    assert out["sampled"] == 6 and out["read"] == 5
    by = {i["key"]: i for i in out["intents"]}
    assert by["new_patient"]["n"] == 2 and by["emergency"]["n"] == 1 and by["appointment"]["n"] == 1 and by["service"]["n"] == 1
    assert out["wantsAppointment"] == 4 and out["booked"] == 1 and out["bookRate"] == 0.25
    assert out["lostTotal"] == 3 and by["new_patient"]["lost"] == 1 and by["appointment"]["lost"] == 0
    assert sum(r["n"] for r in out["reasons"]) == 3
    assert out["score"]["scored"] == 4 and out["score"]["avg"] == 56.0 and [b["n"] for b in out["score"]["buckets"]] == [3, 0, 1]
    assert {s["label"]: s["n"] for s in out["sentiment"]} == {"Neutral": 4, "Slightly positive": 1}


def test_book_rate_by_source_only_counts_calls_from_people_looking_for_care():
    src = {s["source"]: s for s in T.analyze(batch())["bySource"]}
    assert src["Google Ads"]["n"] == 3 and src["Google Ads"]["wantsAppt"] == 2 and src["Google Ads"]["booked"] == 1 and src["Google Ads"]["bookRate"] == 0.5
    assert src["Directories"]["wantsAppt"] == 2 and src["Directories"]["booked"] == 0 and src["Directories"]["bookRate"] == 0.0
    assert src["Directories"]["avgScore"] == 62.0                       # the unscored call is not averaged in as 0


def test_the_lost_list_puts_the_hottest_leads_first_and_is_capped():
    lost = T.analyze(batch())["lost"]
    assert [x["id"] for x in lost] == ["B", "C", "F"] and lost[0]["score"] == 82.0
    many = [call(id="X%d" % i, summary="A new patient called wanting to be seen. Someone will call back.", score=i) for i in range(70)]
    out = T.analyze(many)
    assert len(out["lost"]) == T.MAX_LOST and out["lostTotal"] == 70 and out["lost"][0]["score"] == 69.0


def test_a_batch_with_nothing_to_read_is_empty_and_does_not_divide_by_zero():
    out = T.analyze([call(duration=5), call(duration=9)])
    assert out["read"] == 0 and out["bookRate"] is None and out["intents"] == [] and out["topics"] == [] and out["score"]["avg"] is None
    assert T.analyze([])["sampled"] == 0


def test_nothing_a_caller_said_or_the_summary_says_comes_back_out():
    secret = "Heather Wright 857-310-9334 heather@example.com Social Security 123-45-6789"
    c = call(id="S", summary="A new patient called. Callback requested. " + secret, tags=["Not Booked"], score=70)
    c["transcript"] = GREETING + "Caller: My name is Heather Wright and my number is 857 310 9334. Agent: Thanks Heather."
    out = json.dumps(T.analyze([c]))
    for leak in ("Heather", "Wright", "857", "9334", "heather@", "Social Security", "123-45-6789", "Thanks"):
        assert leak not in out, leak
    assert "summary" not in T.analyze([c])["lost"][0] and "transcript" not in T.analyze([c])["lost"][0]


def test_every_lost_call_carries_where_when_and_how_to_open_it():
    x = T.analyze(batch())["lost"][0]
    assert set(x) == {"id", "start", "company", "source", "campaign", "duration", "intent", "reason", "topics", "score", "sentiment", "url"}
    assert x["url"] == "https://app.callrail.com/x" and x["company"] == "Great Hill"


# ── Cases a first pass of mutation testing found untested ───────────────────

def test_a_transcript_without_speaker_labels_still_has_its_greeting_cut_out():
    text = ("Thank you for calling Great Hill Dental. This call may be recorded for quality and training purposes. If this is a medical emergency, "
            "please hang up and call 911. Hi I would like to book a cleaning for next week please.")
    c = call(duration=90)
    c["transcript"] = text
    r = T.read_call(c)
    assert r["intent"] == "service" and "emergency" not in r["topics"]


def test_what_the_agent_said_alone_decides_whether_an_appointment_was_made():
    booked = T.read_call(call(caller="Hi, I am a new patient and want to be seen.", agent="Great, I have scheduled your appointment for Tuesday at three."))
    assert booked["outcome"] == "booked" and booked["reason"] == ""
    lost = T.read_call(call(caller="Hi, I am a new patient and want to be seen.", agent="I am so sorry, I was unable to transfer you to the office."))
    assert lost["outcome"] == "not_booked" and lost["reason"] == "transfer"


def test_a_call_with_no_real_conversation_raises_no_topics_even_if_a_word_matches():
    c = call(duration=10)
    c["transcript"] = "Caller: I want to book a new patient appointment for my son."
    r = T.read_call(c)
    assert r["intent"] == "no_conversation" and r["topics"] == []


def test_an_automated_greeting_heard_as_the_caller_is_not_what_the_caller_wanted():
    c = call(duration=60)
    c["transcript"] = ("Agent: Hello, Great Hill Dental. Caller: You have reached a voicemail. This call may be recorded. If this is a medical emergency, "
                       "hang up and call 911, otherwise please leave a message after the tone about your account.")
    r = T.read_call(c)
    assert r["intent"] != "emergency" and "emergency" not in r["topics"]


def test_an_appointment_that_was_not_made_is_not_counted_as_booked():
    """"No appointment was scheduled" matched the booked wording, so a lost new patient was counted as a booking and left out of the lost list."""
    for said in ("New patient called for a cleaning exam but no appointment was scheduled because the office was fully booked.",
                 "The caller wanted a visit. No appointment has been scheduled.", "An appointment was not scheduled; they will call back."):
        r = T.read_call(call(summary=said))
        assert r["outcome"] == "not_booked", said
    assert T.read_call(call(summary="New patient called for a cleaning exam but no appointment was scheduled because the office was fully booked."))["reason"] == "no_availability"
    assert T.read_call(call(summary="She scheduled an appointment for Tuesday at 9."))["outcome"] == "booked"
    assert T.read_call(call(summary="The appointment was scheduled for Friday, no insurance needed."))["outcome"] == "booked"      # a far "no" is not about it
    assert T.read_call(call(summary="Not a problem, an appointment was booked for next week."))["outcome"] == "booked"


def test_painless_is_not_pain_but_painful_still_is():
    assert T.read_call(call(caller="Do you offer painless sedation dentistry for implants?", agent="Yes we do."))["intent"] != "emergency"
    assert T.read_call(call(caller="My tooth is painful and I need to come in.", agent="Come at two."))["intent"] == "emergency"
    assert T.read_call(call(caller="I have pain when I chew.", agent="Let me find you a time."))["intent"] == "emergency"
