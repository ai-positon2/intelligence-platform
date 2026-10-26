# Tracking Data Health: draft spec

Status: approved as drafted on 2026-10-26 and built on branch `tracking-health`. Rebuilt from the chat export of the first attempt (the original spec file and the half-built code were lost). The sections below are the approved spec; "Built, and what changed from the draft" at the end says where the build differs and what was and was not verified.

## What it is

A Paid Marketing agent that checks every live Google Ads and Meta account once a day and alerts by email and Slack when conversion tracking looks broken: conversions stop, drop sharply while traffic holds, or the audience lists that depend on tracking shrink. The point is to catch a broken tag or pixel within a day, not at month end.

It is a monitor on the shared engine (`tracker/paid_monitor.py`), like KPI Monitoring and the Spend Anomaly Alert, so baseline, alert-once, reminders, resolved issues, the lock and unreadable accounts all work the same way.

How it differs from the monitors already on `main`:
- **KPI Monitoring** judges results against the client's targets. This agent judges whether the numbers can be trusted at all.
- **Spend Anomaly Alert** watches spend. This one watches conversions and lists while spend and clicks are normal.

## Page and filters

`/p2/paid-marketing/tracking-health`, with the same account drop-down (MCC groups included) and Google | Meta | Both switch as the other agents.

- **Health strip:** one row per account with a health score of 0 to 100, its worst open finding, and when it was last read.
- **Account detail:** the 35-day daily chart of clicks against conversions, the findings, and the conversion actions or pixel events behind them.
- **Alert panel:** the shared one (recipients, Slack channel, baseline, recent runs).
- **Download:** a JSON export of the current state.

## What it reads

**Google Ads,** per account, for the last 35 days:
- Daily clicks, cost and conversions, overall and per conversion action (`segments.conversion_action`).
- Conversion actions: status, whether each is primary, and its category.
- Customer settings: auto-tagging. **(proposed)**
- Remarketing user lists: size, read daily so the trend can be kept.

**Meta,** per ad account, for the last 35 days:
- Daily clicks, spend and results by action type.
- The pixel's last fired time. **(proposed)**
- Custom audience sizes.

The parts that aren't essential (conversion action details, lists, the pixel) are read softly. If one is refused, it shows as "not run" for that account instead of failing the account.

## Findings and levels

The baseline is the 28 days before yesterday. Yesterday is judged against it.

| Finding | Rule (proposed thresholds) | Level |
|---|---|---|
| Conversions stopped | 0 conversions yesterday when the baseline averages at least 2 a day and clicks are at least 50% of normal | **critical** |
| Conversions stopped (low volume) | Same, but the baseline is under 2 a day: needs 3 days at zero in a row. The finding says what is "usually" seen | medium |
| Conversion drop | Conversions down at least 60% against the baseline while clicks are at least 70% of normal | high |
| Conversion rate drop | Conversion rate down at least 40% across the last 3 days, with at least 10 expected conversions | medium |
| Conversion action stopped | A primary Google conversion action that had conversions in the baseline has none for 3 days | high |
| Auto-tagging off | Google account with auto-tagging disabled | high |
| Pixel silent | Meta pixel not fired in 24 hours on an account with spend | critical |
| List below serving size | A remarketing list that held at least 1,000 members drops below 1,000 | medium |
| List shrinking | A list down at least 25% over 14 days | low (shown, not alerted) |
| Account unreadable | Named on the run; never counted as healthy | shown |

- **The "critical" level** is new to the shared engine. It is added as a separate top-level count, so the existing `low`/`medium`/`high` counts and Krishna's monitors don't change. Critical alerts carry a "CRITICAL:" prefix. A finding downgraded to low never shows that prefix.
- **Folding:** findings with one cause in one account go into one alert. For example, conversions stopping plus a primary action stopping on the same day are one alert.
- **Health score:** starts at 100 and deducts per open finding: critical 40, high 25, medium 10, low 3, with a floor of 0. **(proposed)**

## Settings, per account

- **Owner:** a name shown on the alert.
- **Expected-drop windows:** a date range (for example, a site migration or a paused promotion) when drops are not alerted.
- **Muted items:** a conversion action or list that is never judged.
- **Acknowledgement:** "known, being fixed". It stops reminders but keeps the finding open.

## Schedule and alerts

- **Daily run:** once a day through the shared scheduled run (`paid_monitors.run_scheduled`) at 06:00 UTC **(proposed)**. Run now is on the page.
- **Delivery:** email and Slack through the shared engine. Nothing is sent until a baseline is set, and an issue is alerted once. A high or critical issue still open after a week is alerted again.
- **Carried-over issues:** issues still open from earlier runs are listed under "still open" in each digest.

## Not in this version

- Checking that tags fire on the landing page itself (the URL Checker and QA Agent cover page loads).
- Offline or CRM conversion uploads.
- GA4 property checks.
- Changing anything in the ad accounts. It is read only.

## Phases

1. This spec, approved.
2. The shared engine: add the critical level, register the agent, and add `snaps_latest` to the store.
3. Pure maths (baselines, rules, list trends, score) with tests on constructed days.
4. Reads (Google, Meta, soft parts) with fakes that answer the way the real APIs shape their rows, rebuilt relative to each run's date.
5. Monitor, settings, page state and JSON export, with tests.
6. Routes, page, CSS, hub card, palettes, counts, assistant text and `PAID_AGENT_TOTAL`.
7. Browser check against live Google Ads (read only) with recorded senders. Then targeted tests, commit, PR. No merge until you say so.

## Questions for you

1. Are the thresholds in the table about right, especially the 60% drop and the 2-a-day floor for "conversions stopped"?
2. Is 06:00 UTC daily right, or should "conversions stopped" run more often, like the Spend Anomaly Alert's two-hourly run?
3. Should the alert recipients default to the Unknown Domains Alert's ones (Slack `C0B322RN6SG`)?
4. Is anything missing from what it should check, for example GA4 or call tracking?

## Built, and what changed from the draft

**Code.** `tracker/paid_tracking_math.py` (rules, score), `paid_tracking.py` (reads, report), `paid_tracking_settings.py`, `paid_tracking_monitor.py`; the page `templates/tracking_health.html`, `static/js/tracking_data.js`, `static/js/tracking.js`; routes under `/p2/paid-marketing/api/tracking/*`; registered as monitor `tracking` in `tracker/paid_monitors.py`.

**Changes from the draft, each for a reason:**
- **Schedule.** It runs in the shared daily monitor run (`.github/workflows/paid-monitors.yml`, 07:30 UTC), not a new 06:00 workflow, so it needs no new workflow or secret.
- **Conversion action stopped.** "None for 3 days" flagged two actions on a live account that record about 5 a month, where 3 quiet days are normal. An action is now judged over the days it takes to expect about 3 conversions at its own rate (at least 3, at most 14); one too slow to expect 3 in 14 days is shown as "too few to judge".
- **3-day rules.** The low-volume stop and the conversion-rate rule hold the last 3 days against the 28 days before those 3, so a stop does not lower its own usual.
- **Acknowledgement.** It lowers the finding to low for 14 days (kept open, not alerted or reminded), then counts again.
- **List below 1,000.** Google lists only (Search's minimum); Meta audiences get the shrinking rule only. Customer match lists are not followed (no tag feeds them).
- **Health strip.** Built from the report of the account or MCC picked (the accounts of an MCC listed worst first), not from a new `snaps_latest` read of every account.
- **Critical level.** Added to the shared engine: `LEVEL_RANK` has `critical`, it reminds like high, puts CRITICAL in the subject and a siren in Slack, and the panel state carries it as its own `critical` key so the three existing counts and every other monitor are unchanged.
- **Meta conversion actions.** Not read per action in this version; Meta is judged on its daily results, pixels and audiences.

**Verified.** 87 new tests (rules on constructed days, reads on the platforms' row shapes, the monitor through the real engine, routes, the page script in Node), plus the existing monitor, hub and page tests. The five Google reads were run live, read only, on one account; the page was run in a browser against live Google Ads (one account, and an MCC of three) with an in-memory store and the senders recorded, including saving an owner, and at 375px wide with no sideways scroll.

**Not verified.** Meta against a live account (no Meta token on the machine it was built on); the Postgres store (the agent uses the existing `pm_mon_*` tables, no new ones); a real email or Slack send; the daily run on Railway. List trends need a few days of the daily check before they show.
