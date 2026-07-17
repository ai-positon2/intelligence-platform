# visitor_intelligence

The real de-anonymization engine behind the platform's **Anonymous Website
Visitors** surface. Replaces the old single-string IPinfo lookup (`_ip_company`)
with a fused, confidence-scored, gated resolution plus Apollo enrichment and
behavioural intent.

## What it does

```
raw visitor IP + on-site pages
        │
        ▼
 resolve_ip()  ── IPinfo (org/ASN/hostname/privacy)
        │        reverse DNS (PTR)
        │        RDAP (RIR netblock owner + size)
        ▼
 classify_connection()  ── HARD GATE
        │   isp / mobile / hosting / vpn  → not identifiable (return empty)
        │   business / education / government → proceed
        ▼
 confidence score  (noisy-OR over agreeing methods, netblock-size adjusted)
        │
        ▼
 enrich_company(domain)  ── Apollo firmographics (name, industry, size,
        │                    revenue, HQ, LinkedIn, tech) + buying committee
        ▼
 score_intent(pages)  ── 0-100 + stage (awareness→interest→consideration→decision)
        │
        ▼
 flat record for the surface
```

The gate is the whole game: residential, mobile, and cloud/VPN IPs name the
carrier, not a company, so they are marked not-identifiable rather than
mislabelled. This is why honest company-level match rates are ~20-40% of traffic,
and the engine is explicit about *why* each visitor did or didn't resolve
(`reasons` / `intent_reasons`).

## Public API

```python
from visitor_intelligence import resolve_visitor, resolve_ip, score_intent

rec = resolve_visitor(
    ip="140.82.112.3",
    pages=["/pricing", "/demo"],
    apollo_key=APOLLO_API_KEY,      # optional; omit for free resolution only
    ipinfo_token=IPINFO_TOKEN,      # optional; improves accuracy
    with_committee=True,            # pull the account's buying committee
)
# rec: {identifiable, company, domain, confidence, connection_type,
#       industry, employees, revenue, linkedin_url, technologies,
#       buying_committee[], intent_score, intent_stage, reasons[...]}
```

## Integration in app.py

- `_ip_company(ip)` now delegates to the engine (better resolution + gating),
  keeping its old string return for backward compatibility.
- `_ip_resolve(ip)` returns the full record (cached per IP).
- The Anonymous Traffic builder attaches `domain`, `confidence`,
  `connection_type`, `industry`, `employees`, `revenue`, `linkedin_url`,
  `intent_score`, and `intent_stage` to every visitor record.

### Cost control

Apollo firmographic enrichment costs 1 credit per company. To avoid a dashboard
load silently spending credits, paid enrichment is **off by default** and only
runs when the env var `VI_ENRICH_ON_VIEW=1` is set. Free resolution
(IP → domain, confidence, connection type, intent) always runs. Point a
scheduled/batch job at `resolve_visitor(..., apollo_key=KEY)` to enrich
deliberately.

## Config (env)

| Var | Purpose |
|---|---|
| `IPINFO_TOKEN` | IPinfo lookups (org/ASN/hostname; privacy+company on paid plans) |
| `APOLLO_API_KEY` | firmographic + buying-committee enrichment (reuses tracker/apollo_client) |
| `VI_ENRICH_ON_VIEW` | set to `1` to allow Apollo enrichment during a page view (spends credits) |

## Data sources & providers

- **IPinfo** — primary IP intelligence (org, ASN, hostname; `privacy`/`company`
  objects on paid tiers give VPN/proxy/hosting flags and direct IP→company).
- **Reverse DNS / RDAP** — free, authoritative corroboration (PTR domain, RIR
  registrant, netblock size). RDAP via the rdap.org bootstrap.
- **Apollo** — firmographics + people, via the existing `tracker/apollo_client.py`
  (same key/account, no second integration).

To upgrade accuracy further, add MaxMind GeoIP2 Company or IPinfo Company/Privacy
datasets (drop-in: they only change what `ipinfo_lookup` / the ASN source
returns) and, to recover remote-worker traffic, a learned IP↔cookie↔account graph
keyed on the existing `p2_vid`.

## Tests

```bash
python3 -m visitor_intelligence.tests    # 19 offline checks, no network/credits
```
