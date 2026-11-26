# Liine Lead Analysis (Paid Marketing agent, 2026-11-26)

What happens to the new patient leads Liine (call tracking for medical practices) tracks for the Inspire Aesthetics practices.
Page: `/p2/paid-marketing/liine-lead-analysis`. Report: `/p2/paid-marketing/api/liine/report` (GET, staff only, `?refresh=1` reads the folder again).

## Why it is built this way

Liine has no API key we can use. Its MCP connector is OAuth only: it signs in as a person, and only Claude can use it. So the platform
cannot call Liine. The data goes through a Drive folder instead:

1. Claude pulls the data through its own Liine connector (read only) following the written recipe (kept outside the repo, see below).
2. Claude saves it as Google Sheets (csv converted) in ONE Drive folder shared, read only, with the platform's service account
   (`GOOGLE_SA_JSON`). Files are titled `Liine Data YYYY-MM-DD HHMM` and, when a pull is too big for one file, `... part N`.
3. The page reads the newest whole pull (`tracker/paid_liine.py`) and does every sum (`tracker/paid_liine_math.py`). No model is asked
   to find anything. Nothing is written to Drive or Sheets by the platform.

The Google Sheets connector and the Ryze connector cannot write to a file Claude creates (permission denied / subscription cancelled), and a
Drive file's content cannot be replaced by the Drive connector, so every pull is a NEW set of files in the folder; the platform takes the newest.

## The csv layout (one header, eleven columns)

`table,practice_id,practice,period,key,leads,booked,spend,clicks,value,note`

| table | one row is | notes |
|---|---|---|
| meta | one fact | `pulled_at` (value), `last90_start/_end`, `prev90_start/_end` (period), `date_basis`, `liine_user` (note), `recipe_version` |
| practices | one practice | period = first date of data, key = organization, note = `ppc=1;formflow=1;mroi=0;ppc_start=...` |
| totals | practice x period | period `last90` / `prev90`, leads, booked (never hidden by Liine) |
| monthly | practice x month | period `YYYY-MM` |
| channels | practice x period x marketing channel | key = channel |
| notbooked | practice x period x reason | key = reason (`Booked` is included), leads |
| missed | practice x period x metric | key = metric name, value |
| ppc | practice x period x campaign | spend, clicks, value = impressions, note = `platform=...;cpl=...;cpb=...`; `__total__` is the practice's root row |
| forms | practice x period x metric | only practices with FormFlow; period `now` = the dashboard counters |

An empty cell is a number Liine hid (it hides counts under 10) or could not give. It is never read as 0.

## What it shows

Key numbers (leads, booked, booking rate against the 90 days before, share never called, paid spend and cost per booked paid lead, voicemail
rate), findings with the numbers behind them, a practice table, leads and bookings by month, why leads did not book (grouped by who can fix it),
channels, and paid campaigns. A practice picker narrows everything.

Findings (thresholds in `paid_liine_math.py`, repeated on the page and in Vimi): never called (25% of leads and 30 leads, high from 50%), booking
rate down 8 points (high from 15; both periods need 100 leads), paid campaigns that spent $500 or more and booked nobody (shown when they add up
to $3,000, high from 40% of the practice's paid spend), voicemail 15% or more of inbound calls (30 voicemails, high from 25%), leads lost in how the
call was handled (10% and 30 leads), a channel with 100 leads booking at under half the practice's rate.

## Limits worth knowing

- Leads are counted by when their outcome was decided (the Liine user's own setting), so the latest weeks fill in later.
- Liine hides counts under 10: channel and reason tables can add up to a little less than a practice's total.
- Cost is only what Liine imports (Google Ads, Microsoft Ads and ChatGPT Ads where linked); it starts 2026-01-01.
- A practice with a short history (a new one) has a shorter earlier period; the page says so.
- The connector is signed in as one person, so the data is that person's accessible practices.
- Freshness: the page shows when the data was pulled and warns after 72 hours. A pull only happens when someone asks Claude to pull it.
- The service account needs the Drive API enabled in its Google Cloud project to list the folder (`drive.readonly` scope).

## Refreshing

In a Claude session that has the Liine and Google Drive connectors: "Pull the Liine data again" (the recipe is in the user's own files, not in
this repository, because it names the practices and holds real figures for its sanity checks).

## Files

`tracker/paid_liine.py` (read), `tracker/paid_liine_math.py` (arithmetic), `templates/liine_analysis.html`, `static/js/liine.js`,
`static/css/liine.css`, `tests/test_paid_liine.py` (invented data only), Vimi entry in `tracker/vimi_catalog.py` and a paragraph in `app.py`.
