# Unknown Domains Alert plan

Branch: `unknown-domains-alert` (from `main`; the history of `Divith` was restored on 2026-10-18, so this starts clean). Section: Paid Marketing Agents
(`/p2/paid-marketing/unknown-domains`).
Status: **Built (phases 1 to 7), awaiting review.** Results and what is not verified are at the end of this file.

## What it is

Watches every ad account for an ad that sends people to a web address the team has not approved, and alerts by **email and Slack** when it finds
one. Two things go wrong that it catches:

1. **An ad launched under the wrong account**: a client's ad (or a client's landing page) turns up in another client's account. The address is a
   good one, just not for this account.
2. **An account that has been taken over**: someone who is not the team puts ads live that point at a domain nobody has heard of (a phishing page,
   a look-alike of a real domain, a redirect through a tracking address). That is the usual first sign of a hacked ad account, and the damage is
   spend, so the sooner it is seen the better.

## The domain database

It lives in the platform's Postgres and is managed on the page. An entry is **a domain, and who may send people to it**:

- one ad account (Google or Meta),
- a client (an MCC group, so every account of that client), or
- everyone (the team's own site, a landing page builder they all use, a tracking service).

An entry for `example.com` covers `www.example.com` and every other subdomain of it; an entry for `shop.example.com` covers only that host and
below. Entries can be added one at a time, imported from a CSV file or a Google Sheet link (shared as anyone with the link can view), exported as CSV, and
removed. Each shows who added it and when. Nothing about the database is hard-coded: if the team already keeps a list of client domains in a sheet,
importing it is how it gets in (tell me the sheet and I will wire a standing sync).

## What is read (read only, enabled ads only)

- **Google Ads**, every account under Position2's manager account: the final URLs and mobile final URLs of every enabled ad, the tracking
  templates (ad, ad group, campaign, account) because a template that sends people through another host is a hijack, the final URLs of every
  Performance Max asset group, and the final URLs of the sitelinks and other assets attached to the campaigns and the account. About five reads per account.
- **Meta Ads**, every ad account the platform's token sees: the web address of every active ad's creative (link ads, video buttons, carousel cards,
  dynamic creative websites), read the way the Landing Page Check already reads them.

An address on Facebook, Instagram, WhatsApp or Messenger is not a landing page and is never a finding. A URL with only a ValueTrack placeholder
(`{lpurl}`, `{unescapedlpurl}`) has no host to judge.

## What it finds

For every address, its domain is held against the database for the account it was found in:

- **Not in the database at all** → *Unknown domain*. The one to worry about. If it looks nearly the same as a domain that is in the database (one or two
  letters off, or a look-alike character), the finding says so ("looks like example.com"), because that is how phishing domains are named.
- **In the database, but only for other accounts or clients** → *Wrong account*. The finding names who it belongs to.
- **A tracking template that sends people through a host that is not approved** → the same two kinds, and worded as a redirect, which is the more serious.

A finding is the unit: one address in one place in one account (an ad, an asset group, a sitelink, a template). It has a first-seen and last-seen time,
and a status: **open**, **approved** (the domain was added to the database for that account, client or everyone), **dismissed** (a person looked and
this one is fine; it stays quiet until its address changes), or **gone** (a later scan did not find it, so it was fixed in the account).

**A burst is the hack signal.** Three or more new unknown domains in one account in one scan (or an unknown domain on an account that has had none
before, with spend) is flagged "possible account takeover" and goes out first, in the subject line of the email and at the top of the Slack message.

## The baseline

Until the database has an entry, the first scan would find every address in every account. So **nothing is alerted until the team has set a
baseline**: the page shows every domain each account uses today and one click ("Trust what the accounts use today") adds them all, scoped to the
account that uses them; or approve them one by one. After that, anything new alerts. (Trusting everything today also trusts a hijack that has already
happened, so the page says to look through the list first.)

## The alerts

After a scan that finds *new* open findings (not alerted before), one **email** and one **Slack message** go out as a digest: what, which account,
which campaign and ad, the address, why it is flagged, and a link to the page. Recipients and the Slack channel are set on the page (staff only can
change them); nothing is sent until they are. The email goes through the platform's Gmail sender and Slack through its bot, which has to be a member of the
channel (the same two senders Market Radar uses). A send that fails is recorded on the scan and shown, never silent. Each finding is alerted once;
an approved, dismissed or gone finding is not alerted again. A **test alert** button checks the wiring.

## When it scans

- **Every 4 hours** by a GitHub Actions workflow that calls a secret-guarded address on the platform (the same pattern as the Pacing seed). It needs
  two things from you: a `DOMAIN_ALERT_SECRET` set both on Railway and as a GitHub Actions secret, and the platform's base URL as a GitHub variable.
  Every 4 hours is six scans a day, about 250 Google Ads reads each (roughly 1,500 a day) of the roughly 2,900 the platform may use, shared with every other agent; say if you want it less often.
- **Scan now** on the page (staff), which alerts too.

A scan runs in the background; two never run at once; an account that cannot be read is named on the scan, not skipped silently.

## Decisions to confirm (defaults chosen, tell me to change any)

1. The database is **managed on the page** (not an external sheet), with import and export.
2. **Enabled ads only.** A paused ad pointing somewhere strange is not read in this version.
3. **Alerts once per finding**, as a digest per scan, not one message each.
4. **Nothing is alerted before a baseline** exists.
5. **Read only.** It never pauses an ad or touches an account. A suspected takeover is for a person to act on.

## Not in this version (said out loud)

- Who changed what and when (Google's change history shows the user's email of each ad change for 30 days; an ad created by an address that is not
  the team's would be the strongest hack signal there is). A natural next step.
- New users added to an account, payment-method or billing changes, conversion-action changes.
- Paused ads, ads that were created and removed between two scans, and ads in accounts the platform's logins cannot see.
- Checking that the domain itself is safe or what the page says (the Landing Page Check does pages).

## Phases

1. Plan (this file).
2. The domain logic (`tracker/paid_domains.py`): normalising, matching, look-alikes, classifying, the burst rule, the alert text. Pure and tested.
3. Reading the ad accounts (`tracker/paid_domains_read.py`), proven live on a real Google account.
4. Storage (`tracker/paid_domains_store.py`) and the scan (`tracker/paid_domains_scan.py`): findings, baseline, runs, the lock, alerts.
5. Routes, the page, the scheduled workflow.
6. The hub card, palette and counts.
7. Tests, a real browser run, review, PR.

## Built

- Logic `tracker/paid_domains.py`, reading `tracker/paid_domains_read.py`, storage `tracker/paid_domains_store.py` (tables `pm_dom_known`, `pm_dom_findings`,
  `pm_dom_state`), scan and actions `tracker/paid_domains_scan.py`, routes in `app.py` (`/p2/paid-marketing/api/domains/...`), page `templates/unknown_domains.html`
  with `static/js/unknown_domains.js`, workflow `.github/workflows/unknown-domains-scan.yml`, hub card, command palette entry, counts (22 agents) and the assistant's text.
- Adding a domain (or importing a list, or setting the baseline) approves the open findings it now covers. A finding whose domain was added between scans is approved,
  not called gone.
- Import takes a client or account by name as well as by id.

## What to set up

1. `DOMAIN_ALERT_SECRET`: the same random value on Railway and as a GitHub Actions secret of the same name. Without it the scheduled address refuses every call.
2. Optional GitHub variable `PLATFORM_BASE_URL` (defaults to https://intelligence.position2.com).
3. On the page: the alert email addresses and the Slack channel (the Slack bot must be invited to the channel), then **Send a test alert**.
4. On the page: look through "what each account points at today", then Trust what the accounts use today. Alerts start after that.

## Verified

- 69 tests on the logic, the reading (fake Google and Meta answers), the scan, the routes and the hub counts; no network.
- A real browser run on a local server: real Google Ads reads of one test account (read only) plus one made-up account, in-memory store, senders replaced by a
  recorder so nothing was sent. Seen working: the first scan alerts nothing before a baseline; trusting an account approves its findings; after the baseline a
  new wrong-account ad, a look-alike domain and two unknown domains produced one digest by email and Slack with "POSSIBLE ACCOUNT TAKEOVER" in the subject; approve for
  everyone, dismiss, reopen, add domain (covered findings approved), filter buttons clear when clicked again, settings saved, scan history shown.

## Not verified

- The Postgres store against a real Postgres (no database here; the SQL is plain upserts and a lock claim). The tables are created on first use.
- Meta Ads reading against a live account (no Meta token on the development machine); it reuses the Landing Page Check's reader.
- Real email and Slack sends, and the GitHub Actions run (they need the secret and a deploy).
