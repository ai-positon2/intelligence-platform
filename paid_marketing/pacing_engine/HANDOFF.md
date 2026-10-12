# Budget Optimization Agent — handoff to Claude Code

**Purpose of this doc:** everything a fresh session needs to continue this project,
especially to rebuild the dashboard page properly. Written 2026-09-07 for the
Position2 PPC team (ppc@position2.com).

The Python engine is **done and validated**. The web page is the weak part and is
what you're being asked to rebuild.

---

## 1. What this is

A daily agent that reads a Google Ads export plus a budget sheet, and tells each
account manager **where to move budget today** so every account lands the month at
100% of its allocation.

Portfolio: **11 clients, 46 accounts, 728 campaigns, ~$322k/month.**

---

## 2. Run it

```bash
cd bo
python3 run.py --as-of 2026-09-07          # per-client Slack markdown + JSON -> out/
python3 validate.py 2026-09-07             # 12 self-checks; must print 0 issues
python3 export_dashboard.py 2026-09-07     # one JSON payload -> out/dashboard.json
```

Then the page is built by string-substituting the JSON into a template:

```bash
python3 -c "
import pathlib
t=pathlib.Path('dashboard_template.html').read_text()
d=pathlib.Path('out/dashboard.json').read_text().replace('</script>','<\\\\/script>')
pathlib.Path('out/pacing-desk.html').write_text(t.replace('__DATA__',d))"
```

**This build step is the thing to replace.** See §8.

Deps: `pandas`, `numpy`, `openpyxl`. Nothing else.

---

## 3. Inputs

### 3a. Google Ads export — the primary input
`Campaign Spends.xlsx`, a Google Ads UI export, one row per campaign per day.

Required columns:
`Day | Campaign status | Campaign | Budget | Budget name | Budget type | Account |
Campaign type | Bid strategy type | Impr. | Search impr. share | Cost | Conversions | Clicks`

Four columns matter more than they look:

| Column | Why |
|---|---|
| `Budget` | The campaign's **real daily budget**. Without it, everything is inferred from run-rate and the move amounts are guesses. |
| `Budget name` | Non-empty = a **shared budget pool**. Must be counted once, not per campaign, and two campaigns on one pool can never trade budget. |
| `Campaign status` | Separates paused from enabled-but-dark. |
| `Search impr. share` | Auction headroom. Parses `< 10%` → 0.05, `> 90%` → 0.95, `--` → None. |

### 3b. Budget Tracker (Google Sheet)
`1X_HjD0NUzp1br9SsLV7AICdS_SokdYuzbXc75ENVJ7A`, tab `DPR`.
Columns: `Client | Account | Allocated Budget | Target CPA/ROAS/CPM | Date |
Account Status | Days Ads are Running | AM | Channels`.

**Read Drive sheets as xlsx, never as text.** `read_file_content` flattens to markdown
and silently truncates (Riccobene's Data tab returned 248 of 3,540 rows). Use:

```
download_file_content(fileId, exportMimeType=
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
```

That returns the complete workbook, every tab, base64. `loaders.save_drive_payload()`
decodes it.

---

## 4. The domain rules (from the PPC team, not invented)

### Pacing
- Ideal pacing = `(today - 1) / days_in_month`. Sept 7 2026 → 6/30 = **20.00%**.
- Campaigns may run **1–2pp ahead** of ideal. Config: `UPPER_TOL_PP=2.0`, `LOWER_TOL_PP=1.0`.
- Recomputed monthly (30 vs 31 days).
- `Days Ads are Running` is parsed ("Monday - Friday", "Monday, Wednesday, Thursday").
  All remaining-day maths uses **active** days.

### Move scope (confirmed with the team)
Budget moves **only between categories inside the same location**. Never across
locations, never across accounts. Cross-account gaps are flagged for the AM, never
auto-moved. Every location's moves net to zero.

### OIA — how the team actually works it
> "I look at each campaign's performance Sept 1–6, check performance of Brand, PMax,
> and modalities like MRI, CT Scan, Ultrasound and others. Check if the overall CPLs
> for each defined category are under $10 and move money around."

So the unit is the **modality**, not the campaign. Campaign naming is
`P2_<Account>_<Category>_<MatchType>` (e.g. `P2_Heartland_CTScan_ET_B`), and OIA's
**shared budget pools are literally named MRI, CTScan, Ultrasound, Mammogram, PET** —
so the category is also the only level at which the budget *can* be changed.
Target CPL **$10**.

### GD & Affiliates — how the team actually works it
> "$40 prospect for each location, brand and pmax has most of the spends, the other
> campaign types are given $5 as allocated budget, but if the account is under
> spending at the end of the month their allocated budget increases from $5."

Campaign naming is `<Location>_<Type>` (e.g. `Arlington_Brand`). Encoded in
`config.CLIENT_RULES["gd & affiliates"]`: `target_cpl 40`, `major {Brand, PMax}`,
`minor_floor $5`, `floor_relief 4x` when underspending.

---

## 5. Engine design — the decisions that matter

### Forecast vs lever (the biggest one)
- **Forecast** = `spend_to_date + actual_spend_per_active_day × remaining_active_days`.
  Never from the budget cap. Most campaigns never spend their cap, so projecting off
  budget wildly overstates — an account at 38% utilisation looked like it was about to
  triple its target.
- **Lever** = the set daily budget. To raise SPEND by X you must raise BUDGET by
  `X / utilisation`. Where utilisation is already low, more budget does nothing — that
  is a demand/bid/targeting problem, and the agent says so instead of recommending a raise.

### Priority = dollars at risk, not percentage points
Pacing deviation alone is alarmist mid-month. Monkey Junction showed −5.1pp but
projected to 101%, so it correctly gets no action. Sort by `abs(projected - allocated)`.

### Category rollup
`categories.py` maps campaign → category per client. Campaigns roll into
`CategoryRow` objects per `(location, category)`; the allocator runs on those.
`CategoryRow` deliberately mirrors `CampaignRow`'s field names so `_build_moves`
treats both identically.

### Scoring (weights in `config.py`)
| Signal | Weight | Meaning |
|---|---|---|
| Efficiency | 40% | CPL vs target, clipped 0.25–2.5×, normalised within the account |
| Volume | 25% | share of conversions produced |
| Absorption | 25% | utilisation + steadiness + IS headroom — can it actually spend more? |
| Type | 10% | strategic preference |

### Allocation — water-fill, not clamp-then-rescale
`_water_fill()` scales targets to hit the required total while respecting `[lo, hi]`
elementwise, pushing residual only onto lines with headroom. The naive "clamp then
rescale" silently re-breached every cap. When bounds make the target unreachable, the
shortfall is recorded in `constrained_by` and reported — never silently breached.

### Guardrails
- Max 30% donated / 50% received per category per run, measured against the
  **pro-rata share of the new account total**, so an account-wide cut is never
  mistaken for a reshuffle.
- PMax / Demand Gen / Video capped at +30% (learning phase).
- Per-client type share ceilings (`type_max_share`). OIA Brand is capped at 85%, not
  the generic 35% — imaging centres convert on their own name and Brand legitimately
  carries most of the spend.
- Client policy floors (`minor_floor`), raised by `floor_relief` when underspending.
- A category with spend and **zero conversions never receives more** — but only where
  conversion tracking is reliable for that account (see below).
- Moves below $5/day or 10% are suppressed.
- Moves are **absolute** deltas (`new_daily - cur_daily`). Measuring against a pro-rata
  baseline produced the nonsense of "move $13/day from Brand" printed next to Brand's
  budget going *up*.

### Detections the engine makes (each was a real finding)
1. **Shared budget pools** — counted once; no moves proposed inside one.
2. **Daily figure in the monthly budget column** — allocation ≈ sum of daily budgets
   AND spend already past it. Pacing suppressed; budget-neutral moves still stand.
3. **Budget-capped accounts** — caps mathematically cannot reach the allocation.
   Only asserted when utilisation ≤ 105%, else it contradicts the forecast.
4. **Mid-month budget edits** — spend running >1.6× the current cap.
5. **ROAS/CPM in the CPA column** — `Target CPA/ROAS/CPM` is one column, three units.
   If blended CPA > 5× the stated target it is not a CPA; dropped, ranked relatively
   instead. Fired on BD Diesel (target 15, CPA $82) and Monster Transmission (14, $575).
   An **explicit** `CLIENT_RULES` target bypasses this check — it is policy, not a
   suspect number.
6. **Unreliable conversion tracking** — if implied CPL > 3× target, the client counts
   prospects somewhere Google cannot see (GD uses CallRail), so zero-conversion
   categories must not be read as failures or the whole account freezes.

---

## 6. Files

| File | Lines | Purpose |
|---|---|---|
| `config.py` | ~130 | Every tunable: tolerances, weights, caps, floors, `CLIENT_RULES`, paused accounts |
| `pacing.py` | ~120 | Calendar / schedule / base-plan ideal pacing; parses "Monday - Friday" |
| `categories.py` | ~110 | Campaign → category, per-client rules |
| `gads.py` | ~230 | Google Ads export loader + account/location resolver |
| `loaders.py` | ~200 | Budget Tracker; legacy Supermetrics sheet adapter |
| `engine.py` | ~800 | Scoring, water-fill allocation, move construction, notes |
| `slack_fmt.py` | ~130 | One Slack post per client |
| `run.py` | ~120 | Orchestrator |
| `validate.py` | ~110 | 12 self-checks |
| `export_dashboard.py` | ~110 | Single JSON payload for the page |
| `dashboard_template.html` | ~560 | The page. `__DATA__` placeholder gets the JSON. |

### Name resolution (fiddly, already solved — don't regress it)
- 35 of 43 Google Ads accounts match Budget Tracker rows directly on a normalised key.
- `ACCOUNT_ALIASES`: `"daniel i. shapiro, m.d., p.c."` → `"Shapiro Plastic Surgery"`.
- `SPLIT_ACCOUNTS = {"riccobene"}` — one Google Ads account holding 10 budgeted
  locations. Location is matched **by name**, never by splitting on a delimiter:
  `P2_Generic_PortersNeck` vs `P2_Pmax_Porters_Neck` broke positional splitting
  ("Neck"). `match_location()` does normalised substring, longest-match-wins, then a
  prefix fallback so `P2_Generic_Myrtle` finds `Myrtle Beach (de novo)`.
- `LOCATION_RULES` — Gentle Dental / Great Hill hold ~93 locations under 4 budget
  lines; the rule only fences moves, the budget stays at account level.
- Budgets are filtered to `Channel = Google`, since the export is Google-only. This is
  what makes Eventgroove's "duplicate" rows resolve (Google vs Bing vs MNTN).

---

## 7. Verification

`validate.py` runs 12 checks and passes clean on 2026-09-07, -09-06, -09-03:
moves net to zero · endpoints exist · never cross locations · never inside one shared
pool · above the noise floor · never fund a dark category · donor budget actually falls
and recipient's actually rises · client policy floors respected · zero-conv categories
not funded where tracking is reliable · categories and campaigns both reconcile to
account spend · campaigns partition cleanly into categories · pools not double-counted ·
forecast derived from run-rate not cap · client totals reconcile.

**Ground truth:** Riccobene reconciles exactly to its own September DPR —
**$3,288.96 across 10 locations, all 40 campaigns.** Keep this as a regression test.

Category keys must be `(location, category)` — "Brand" exists in all six Great Hill
locations and collides on name alone.

---

## 8. What to build — the page

Current page is `dashboard_template.html`: vanilla JS, `<details>` accordions, JSON
string-substituted in. It works but is at its ceiling. Live at
`claude.ai/code/artifact/578df6f1-53d0-4da0-be8c-db92f7728080`.

**Known weaknesses to fix in the rebuild:**
- The template/`__DATA__` substitution is fragile. Serve the JSON properly or bundle it.
- No build step, no types, no component structure. 560 lines of string concatenation.
- No charts. Spend-by-day vs plan per account would be genuinely useful.
- No sort on the tables; no CSV export.
- No persistence — an AM cannot tick off a move as applied.
- No month-over-month view; the JSON is one snapshot.

**Feedback already given by the user, all now implemented — preserve these:**
1. Client dropdown + account dropdown (they explicitly did **not** want a search box).
2. The stat strip must recompute for the current selection, not stay portfolio-wide.
3. Category table is the primary view; campaign detail collapses underneath.
4. Structured, not prose.

**The one instrument worth keeping:** the pacing bar — spend fill, dashed tick at ideal
pacing, solid line at 100% of budget, ghost extension to the projection, colour by state.
It lets you scan 46 accounts without reading a number.

**Design tokens in use** (light + dark, all three theme states handled):
ground `#F4F6F9`/`#0E1116` · surface `#FFFFFF`/`#161B23` · ink `#12161C`/`#E9ECF1` ·
accent `#22346B`/`#8AA3E0` · over `#A8500C` · under `#1B5B8C` · ontrack `#166B45` ·
unclear `#63548C`. Type: Archivo (display/labels), IBM Plex Sans (body), IBM Plex Mono
(figures, tabular-nums).

**Bugs already found and fixed — don't reintroduce:**
- `'Day '+D.elapsed+1` → string concat, printed "Day 61 of 30". Needs `(D.elapsed+1)`.
- `.kgrid div` matched nested label/value divs → each cell rendered as two boxes. Needs `>`.
- `CUR` used but never declared in page JS.
- Categories keyed by name alone collide across locations.

---

## 9. Open items

1. **Slack posting is blocked** — the connector is not a member of `#adcopyqc`
   (`C0B322RN6SG`); posting fails `not_in_channel`. Someone must `/invite` it.
   Formatter and message content are done and tested.
2. **Is the tracker's Allocated Budget monthly or daily for the ecommerce accounts?**
   Eight accounts (Monster, BD Diesel, Eventgroove ×4, Brian Tooley, D&J) show the
   allocation equal to the sum of daily budgets. Pacing is suppressed for them pending
   an answer.
3. **~$98,600 MTD spending with no budget row** — Workato Demand Generation alone is
   $84,624, the single largest spender in the export. Plus Tealium ×3, Soteri Skin.
4. **Beta Bionics** is paused — already suppressed via `config.PAUSED_ACCOUNTS`.
5. **11 Riccobene locations** spend ~$510 MTD with no September budget row.
6. **Five accounts cannot reach budget at current caps** — $8,973 at risk.
7. Data is Sept 1–6 only. A longer window would sharpen the absorption signal.
8. Supermetrics MCP is connected but exposes only campaign *write* tools, not
   `data_query` — so no direct Google Ads pull. The manual export stays the input.

---

## 10. Scheduling note

If you automate the daily run, use the Claude Code Remote MCP trigger tools
(`create_trigger` / `send_later`), **not** the local `CronCreate` tools — those run
in-process and are lost when the session ends.
