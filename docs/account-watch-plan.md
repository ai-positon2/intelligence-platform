# Account Watch plan

Sheet item #34 ("Account Watch", Monitoring, "New - not built", owner Krishna, Google + Meta). The sheet says: "Alerts on payment failures, suspensions, restrictions, disconnected Pages, and
unexpected changes to budgets, bids, goals and user access." Section: Paid Marketing Agents (`/p2/paid-marketing/account-watch`). Built 2026-10-26 at Krishna's request, the second of "the remaining 6".

## What it is

For a Google Ads account, every account of an MCC or a Meta ad account it reads what shows that something happened to the account that nobody may have meant. A finding is a thing a person can open
and fix, with who did it and when where the platform says. It does not guess who is "unexpected": a person outside Position2's domain, a change large enough to matter, and a user, goal or Page that
was there and is not are flagged, and the team decides.

## What it can and cannot see

A declined card is not shown by either platform (it is in the billing page); a failed payment shows as an account status that is not active, which is flagged. Changes older than 3 days, edits the
platforms make on their own and tools that do not write to the change history are not seen. Written on the page (`NOT_MEASURED` in `tracker/paid_watch_math.py`).

## What is read

| Platform | Read | Used for |
|---|---|---|
| Google | `customer_client` of the manager account, every status (the platform's own account list only holds enabled accounts) | suspended, canceled or closed accounts, and accounts that vanished from the manager account |
| Google | `account_budget` (status, adjusted or approved limit, amount served, end date) | a budget order nearly used or about to end (invoiced accounts only) |
| Google | `change_event`, last 3 days (type, operation, changed fields, old and new resource, user, client) | budgets that moved a lot, bid strategy changes, people outside Position2 |
| Google | `conversion_action` (status, primary, included in conversions) | a conversion goal that was changed, held against yesterday |
| Google | `customer_user_access` (email, role) | who can open the account, held against yesterday |
| Meta | the ad account (`account_status`, `disable_reason`, `spend_cap`, `amount_spent`) | payment and status, the spend cap |
| Meta | `/{act}/promote_pages` (`is_published`; read again without it when Meta refuses it) | a Page that is gone or unpublished |
| Meta | `/{act}/activities`, last 3 days (event type, actor, extra data) | access, spend limit, payment, budget and bid changes, people outside Position2 |

Every read but the manager account's list is soft (a refusal leaves that check out and says so); an account all of whose reads were refused is raised. The new field names are checked by the admin
live query check (`/p2/paid-marketing/api/query-check?...&agents=watch`).

## Rules (`tracker/paid_watch_math.py`)

| Rule | Level | Meaning |
|---|---|---|
| `account_status` | high | Google SUSPENDED, CANCELED or CLOSED; Meta disabled, unsettled (a payment failed), in a grace period, under risk review, closed (settling a payment is medium) |
| `account_gone` | high | listed by the manager account at an earlier reading and not now; stays until it is listed again |
| `spend_limit` | medium, high from 95 percent (98 for a Meta cap) | an account budget order 85 percent used or ending within 3 days; a Meta spend cap 90 percent used |
| `budget_change` | medium, high when it doubled or fell under a third | a budget moved 30 percent or more (or was created or removed), from the change history or the activity log |
| `bid_change` | medium | a campaign's bid strategy or target (Google), an ad set's bid or optimisation goal (Meta) |
| `goal_change` | medium, high for a primary goal that was hidden, removed or stopped counting | a conversion action's status, primary flag or inclusion changed, or one was added or went |
| `access_change` | medium, high for an administrator | a user was added, removed or changed role |
| `outside_editor` | medium | someone whose address is not on position2.com changed the account (one finding per person) |
| `page_lost` | high | a Page that was connected is not now, or is not published |
| `activity` | medium, high for access added or an account status change | Meta activity that touches access, spend limits or payment |

A single change is named in the alert's title (what and by whom) because a Slack alert shows only the title. A new change is a new finding (its key carries the newest change's time), so it is alerted
once; one that is put right is resolved. Users, goals and Pages are judged from the second day.

## The monitor

Daily on the shared monitor layer (`tracker/paid_watch_monitor.py`, agent `watch`): one issue per high or medium finding, the low ones in one quiet issue, at most 100 per account. The manager
account's list is read once; each run saves who has access, the goals and the Pages per account, and the whole list under the key `mcc`. The first run, and the first read of any account, is a quiet
baseline. Alerts go to the Slack channel set for the monitors, once a day with the others.

## Not built, on purpose

Team-saved settings (the team's own domain list is fixed at position2.com, changeable in code), campaign status changes (pausing is routine), GA4 property access, Meta business-level user access
(needs more permission than the platform's token has), and a per-person "known editors" list.
