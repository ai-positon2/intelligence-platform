# Alert Hub plan

Sheet item #38 ("Alert Hub", "New - not built"). The sheet says: "One alert layer with P1/P2/P3 severity, de-duplication and a single channel per client, used by every monitoring agent."
Section: Paid Marketing Agents (`/p2/paid-marketing/alert-hub`). Built 2026-10-27 at Krishna's request, the fifth of "the remaining 6".

## What was there before

Every monitor (`tracker/paid_monitor.py`, run by `tracker/paid_monitors.py`) kept its own issues and sent its own alert to one global Slack channel and one global list of emails (state key
`alerts`). Ten monitors, so a bad morning was up to ten messages, the same account named in several, and the same fact (a campaign that stopped converting) in two. Levels were high, medium,
low and info. There was one destination for everything: no client had a channel of its own.

## What it is

The hub is the layer every monitor's alert goes through (`tracker/paid_alerthub.py`). The monitors still find and keep their issues exactly as before. What changed is what happens to
`pending` (the issues that are new, worse or due a reminder) after a run: a run hands them to the hub, the hub decides, and sends one message per destination.

| Part | Rule |
|---|---|
| Severity | P1 is a high issue, P2 a medium one, P3 a low or information one. P1 and P2 are alerted. P3 stays on its page and in the Monday summary, as before (the engine never alerted low ones) |
| One message | A scheduled run sends one message per destination for all ten monitors, not one per monitor. A single monitor run by hand or the two-hourly Spend Anomaly run sends one message for that run |
| One channel per client | A client of the Master Agent can have its own Slack channel, extra emails and a severity floor (P1 only, or P1 and P2). An account in a client goes to that client's channel; an account in no client, or a client with no channel of its own, goes to the default channel (the one every monitor page already sets) |
| De-duplication | An issue is held back (not sent, not marked as alerted) while a more important issue on the same account already says it. Two rules, both written in `ROOT`, `SYMPTOMS` and `FAMILIES` |

### The two de-duplication rules

1. **Root cause.** When an account cannot serve at all (Account Watch: the account is suspended, closed or cancelled, `account_status` or `account_gone`), what follows from it is not a separate
   problem: KPI Monitoring (every finding), Keyword Visibility, Creative & Asset Health (every finding), Spend Anomaly (a drop), Tracking & Data Health (conversions stopped or dropped) and the
   Disapproved Ads Tracker (an ad group that cannot show).
2. **The same fact.** Two monitors that say the same thing about one account: "cannot show or deliver" (Disapproved Ads: a stopped ad group, then KPI Monitoring: not delivering, then Spend
   Anomaly: a drop) and "stopped converting" (Tracking & Data Health, then KPI Monitoring). The first in each list is the one that names the cause, so it is the one that is sent.

A held-back issue is never marked alerted. It stays open on its own page, and the alert that covers it says so ("also on KPI Monitoring"). If the covering issue is fixed while the held-back one is
still open, the held-back one is alerted at the next run, because nothing covers it any more. An issue is only held back by one of the same or a higher level, so a high issue is never hidden behind
a medium one.

## Sending and marking

* The hub sends first and marks after, per destination: an issue is marked alerted only when a channel took the message it was in, so a destination that refused is tried again at the next run
  (the engine's rule, unchanged).
* An issue below a destination's floor is marked alerted with the reason `below floor`, so it is not counted as new every day. If it gets worse it is alerted like any other.
* A destination with no Slack channel and no email at all is not sent to and nothing is marked: the issues wait for one to be set.
* Each run's record on its monitor's page says what became of its alerts (sent, held back, below a floor) and the page of the hub lists the last sends.

## The page

`/p2/paid-marketing/alert-hub`:

1. Tiles: open P1, open P2, P3, held back, destinations set up.
2. Where alerts go: the default destination (the same setting every monitor page shows) and one row per Master Agent client (Slack channel, emails, floor, a test send).
3. Open alerts across all ten monitors, by severity, with the client, the monitor, how long open and whether another issue covers it. Filter by severity, client and monitor; CSV.
4. What was sent: the last sends, where each went, how many issues, how many held back.
5. How it decides: the rules above, and what it does not do.

## Weekly summary

Mondays, each destination gets one summary for all monitors instead of ten: open by severity and by monitor, the worst, and what was fixed in the last seven days.

## Not built, on purpose

* **Quiet hours, snoozing and acknowledging** an alert: nothing in the sheet asks for it, and a snooze that hides a P1 is worse than a repeat.
* **P3 alerts:** the engine alerts medium and above, and a low issue is on the page and in the weekly summary. Making the floor P3 would also mean a change in every monitor's levels.
* **Flapping:** an issue that is fixed and comes back is alerted each time it comes back (the engine says how many times it has). The hub does not throttle it.
* **Paging a person** (SMS or a call) for a P1: needs a provider.
* **Per-account channels:** the unit is the client, as the sheet says.
* **Pacing Desk** has no scheduled run and no issues in the monitors' store, so it has nothing to send.
