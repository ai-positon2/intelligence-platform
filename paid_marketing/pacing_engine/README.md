# Pacing engine (Pacing Desk)

The budget-pacing engine behind **Paid Marketing Agents › Pacing Desk**
(`/p2/paid-marketing/pacing-desk`). Brought over from Agent Holmes
(github.com/ai-positon2/Agent_Holmes), built by Divith K for the Position2 PPC
team. `HANDOFF.md` is the full design and the domain rules; `ENGINE_NOTES.md`
covers ranking weights, guardrails and adding a client.

Two ways a run reaches the page:

1. **The platform builds it itself** (`tracker/paid_refresh.py`), by the Refresh
   button or by the page on the first visit of each Pacific day. It reads every
   Google Ads account under the manager account through the API
   (`tracker/paid_pacing_read.py`: the month's campaign days, each campaign's
   real daily budget, shared pools, search impression share) and measures them
   against the month's **Allocations**, which the team keeps on the page
   (`tracker/paid_pacing_budgets.py`). No Google Sheet is read. It writes the
   two workbooks this engine's loaders expect into a throwaway folder, runs
   `validate.py` and `export_dashboard.py`, and publishes the result. Needs the
   `GOOGLE_ADS_*` variables the other Paid Marketing agents already use.
   `docs/pacing-desk-input.md` has the details.
2. **A push from the PPC team's machine**, below. The inputs in `raw/` hold
   client spend and are deliberately **not committed** (this repository is
   public).

```bash
pip install pandas numpy openpyxl
python3 run.py --as-of 2026-10-12
python3 validate.py 2026-10-12          # 12 self-checks, must print 0 issues
python3 export_dashboard.py 2026-10-12  # out/dashboard.json
SEED_SECRET=... python3 push.py         # makes it the run everyone sees
```

Holmes' own `dashboard/seed_live.js` works too:
`SEED_SECRET=... node dashboard/seed_live.js --url https://intelligence.position2.com/p2/paid-marketing/api/pacing/seed`.
