# Master Agent plan

Sheet item #37 ("Master Agent", "New - not built"). The sheet says: "One source of truth per client (budgets, KPI targets, locations, phone numbers, brand terms, allowed domains, offers) that every
agent reads from." Section: Paid Marketing Agents (`/p2/paid-marketing/master-agent`). Built 2026-10-27 at Krishna's request, the fourth of "the remaining 6". It is the "Client Master Config" that
the Brand Spend, KPI Monitoring, Ad Copy Checker and Location Conflicts modules each said they were waiting for.

## What it is

A client is written down once in the monitors' store (`settings:master`, `tracker/paid_master.py`):

| Part | Holds |
|---|---|
| client | a name and the accounts it owns (Google customer ids and Meta ad accounts); an account is in one client only |
| per account | budget (monthly, weekly floor and ceiling), KPI targets (cost per conversion, return on ad spend, lowest conversion rate and click-through rate) and the tolerance: what differs between a client's own accounts |
| shared | phone numbers, locations, regions people say, brand terms, the way the name is written, allowed words, allowed domains, offers (text and the day each ends), notes |

Every change says who made it and when. The page cleans nothing itself: the server's cleaners are the agents' own (phone numbers, places, targets, money, domains), so a value the master accepts is one
the agent would have accepted.

## How the agents read it

The master FILLS what an agent has not saved for itself; what an agent saved on its own page still wins (the team set it there on purpose). Nothing is written into an agent's own settings, and the
agents' settings pages show only what the team saved on them.

| Agent | Reads from the master |
|---|---|
| Brand Spend Alert | monthly budget, weekly floor and ceiling, brand terms (Google accounts) |
| KPI Monitoring | each KPI target (one by one) and the tolerance |
| Ad Copy Checker | phone numbers, places (the master's locations), the written name, allowed words (each list on its own) |
| Location Conflicts | regions |
| Unknown Domains | allowed domains, as entries for each account of the client |

The reads go through `load_for_read` in each settings module (the settings pages and every save use `load`), and a test scans the reports and monitors for a read left on plain `load`. A master that
cannot be read never stops an agent: the agent's own settings are used as they are.

## Where they disagree

The page lists every figure an agent saved that is not the master's (the agent's wins until one is changed). Offers and notes are kept and no agent reads them yet; the page says so beside each field.

## Not built, on purpose

Reading the master from Pacing Desk (its allocations are per channel and a monthly budget does not say how it is split), checking ad text against the offers' end days (a new check of the Ad Copy
Checker), a per-person permission (every signed-in Position2 user can edit, like the agents' own settings), and importing clients from a sheet.
