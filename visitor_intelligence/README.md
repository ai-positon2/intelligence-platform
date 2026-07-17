# visitor_intelligence

The real de-anonymization engine behind the platform's **Anonymous Website
Visitors** surface. Replaces the old single-string IPinfo lookup (`_ip_company`)
with a fused, confidence-scored, gated resolution, free-first firmographic
enrichment, an identity graph for person-level resolution, and behavioural
intent, all at zero ongoing cost unless a human explicitly asks to spend an
Apollo credit on a specific lead.

## What it does

```
raw visitor IP + on-site pages
        |
        v
 resolve_ip()  -- IPinfo (org/ASN/hostname/privacy)
        |        reverse DNS (PTR)
        |        RDAP (RIR netblock owner + size)
        v
 classify_connection()  -- HARD GATE
        |   isp / mobile / hosting / vpn  -> not identifiable (return empty)
        |   business / education / government -> proceed
        v
 confidence score  (noisy-OR over agreeing methods, netblock-size adjusted)
        |
        v
 enrich_company_free(domain)  -- FREE, always runs: the company's own
        |    schema.org/OpenGraph data, self-built tech-stack fingerprinting,
        |    SEC EDGAR for the minority that are US public filers
        v
 score_intent(pages)  -- 0-100 + stage (awareness->interest->consideration->decision)
        |
        v
 flat record for the surface
        |
        v  (only when a human explicitly asks -- see "Cost model" below)
 deepen_with_apollo(rec)  -- precise revenue/headcount + real buying committee
```

The connection-type gate is the whole game: residential, mobile, and cloud/VPN
IPs name the carrier, not a company, so they are marked not-identifiable rather
than mislabelled. This is why honest company-level match rates are ~20-40% of
traffic, and the engine is explicit about *why* each visitor did or didn't
resolve (`reasons` / `intent_reasons`).

## Cost model: free by default, paid only on request

Enrichment is two tiers, and they are two SEPARATE functions, not one function
with a flag:

- **`resolve_visitor()`** -- free, always safe to call on every visitor, every
  page view, in a loop over your whole traffic history. It never touches
  Apollo. It gets the company name/description/LinkedIn URL/tech stack from
  data the company already publishes about itself (schema.org, OpenGraph, its
  own HTML), plus SEC EDGAR when the company happens to be a US public filer.
  This is genuinely real data, not a mock, see the Data sources section.

- **`deepen_with_apollo(rec, apollo_key)`** -- costs ~1 Apollo credit per
  company (plus more for a buying committee pull). This is a SEPARATE function
  you call only when a human has decided a specific lead is worth it, e.g. a
  rep clicking "Enrich further" on one account. It is never called
  automatically by `resolve_visitor()`, by the anon-traffic builder, or by any
  page load. Nothing in `app.py` currently calls it automatically; wire it to
  an explicit UI action when you're ready.

Free enrichment cannot get everything: precise employee headcount and revenue
for a private company are usually not public anywhere, so those fields stay
empty until you explicitly deepen with Apollo (or another paid source). That's
an honest gap, not a bug, don't guess at numbers nobody publishes.

## Public API

```python
from visitor_intelligence import resolve_visitor, deepen_with_apollo

# Runs on every visitor, zero cost:
rec = resolve_visitor(ip="140.82.112.3", pages=["/pricing", "/demo"], check_sec=True)
# rec: {identifiable, company, domain, confidence, connection_type, description,
#       linkedin_url, technologies, social_links, industry (SEC only),
#       intent_score, intent_stage, reasons[...], enrichment_source: "free"}

# Only when a rep decides this lead is worth a credit:
rec = deepen_with_apollo(rec, apollo_key=APOLLO_API_KEY, with_committee=True)
# adds: employees, employee_range, revenue, apollo_org_id, buying_committee[]
```

## Integration in app.py

- `_ip_company(ip)` delegates to the engine (better resolution + gating),
  keeping its old string return for backward compatibility.
- `_ip_resolve(ip)` returns the full FREE record (cached per IP). This is what
  the Anonymous Traffic builder calls for every visitor -- zero Apollo cost.
- `_ip_deepen_with_apollo(ip)` is the explicit opt-in function. It is defined
  but not wired to any automatic path; connect it to a rep-facing "Enrich
  further" action when you want it.

## Config (env)

| Var | Purpose |
|---|---|
| `IPINFO_TOKEN` | IPinfo lookups (org/ASN/hostname; privacy+company on paid plans) |
| `APOLLO_API_KEY` | only read by `deepen_with_apollo()` / the explicit deepen path |
| `SEC_EDGAR_CONTACT` | contact email sent in the User-Agent to SEC EDGAR (required by their fair-access policy); defaults to `reporting@position2.com` |

## Data sources & providers

**Free, always on:**
- **IPinfo** -- primary IP intelligence (org, ASN, hostname; `privacy`/`company`
  objects on paid tiers give VPN/proxy/hosting flags and direct IP->company).
- **Reverse DNS / RDAP** -- free, authoritative corroboration (PTR domain, RIR
  registrant, netblock size). RDAP via the rdap.org bootstrap.
- **schema.org / OpenGraph** -- a company's own homepage frequently publishes
  its legal name, description, HQ, and social/LinkedIn links as structured
  data. Coverage varies by site (not every company publishes this); when
  present it is a real, first-party signal, not a guess.
- **Self-built tech-stack fingerprinting** -- the same technique BuiltWith/
  Wappalyzer use: regex signatures over the fetched HTML/headers for ~25 common
  analytics/CRM/framework/hosting tags. Hand-rolled here, so it costs nothing.
- **SEC EDGAR** -- free, authoritative revenue/industry/CIK data, but only for
  US public filers. Most B2B website visitors are private companies, so an
  empty result here is expected and honest, not a failure of the method.
- **Clearbit Logo API** -- free, no-auth, confirms a domain resolves to a known
  brand and gets a logo.

**Explicit opt-in only (spends credits):**
- **Apollo** -- firmographics + buying committee + person match, via the
  existing `tracker/apollo_client.py` (same key/account, no second
  integration). Only called from `deepen_with_apollo()`, never automatically.

**Deliberately not used:** LinkedIn scraping. The team-page lookup
(`fetch_team_page`) only reads a company's own published `/leadership` or
`/team` page, and requires the extracted text to contain a real job-title
keyword (CEO, VP, Director, ...) before returning anything, specifically so
generic marketing copy on that page ("500M+ API requests") never gets
mislabelled as a person's name.

To upgrade accuracy further, add MaxMind GeoIP2 Company or IPinfo Company/
Privacy datasets (drop-in: they only change what `ipinfo_lookup` / the ASN
source returns) and, to recover remote-worker traffic, a learned IP<->cookie
<->account graph keyed on the existing `p2_vid`.

## Identity graph (person-level)

See `identity_graph.py`: a persistent SQLite identity graph that retro-stitches
a visitor's prior anonymous sessions to a named person the moment they log in,
submit a form, or come through `/api/identify`, plus a pluggable
`IdentityProvider` slot (`CoopFileProvider`) for a licensed third-party
identity graph or your own co-op feed, if you ever contract one. It never
fabricates a person: no deterministic anchor and no provider hit means the
visitor stays anonymous, with the reason recorded.

## Tests

```bash
python3 -m visitor_intelligence.tests    # 36 offline checks, no network/credits
```
