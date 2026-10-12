# Pacing engine (Pacing Desk)

The budget-pacing engine behind **Paid Marketing Agents › Pacing Desk**
(`/p2/paid-marketing/pacing-desk`). Brought over from Agent Holmes
(github.com/ai-positon2/Agent_Holmes), built by Divith K for the Position2 PPC
team. `HANDOFF.md` is the full design and the domain rules; `ENGINE_NOTES.md`
covers ranking weights, guardrails and adding a client.

Two ways a run reaches the page:

1. **The Refresh button** on the Pacing Desk. The platform reads the
   "All Accounts Spend Data" sheet and the Budget Tracker with its service
   account, runs this engine in a throwaway folder, and publishes the result
   (`tracker/paid_refresh.py`). Needs `PACING_SPEND_SHEET_ID` set on Railway
   and both sheets shared (Viewer) with the service account in
   `GOOGLE_SA_JSON`. `PACING_BUDGET_SHEET_ID` overrides the Budget Tracker.
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
