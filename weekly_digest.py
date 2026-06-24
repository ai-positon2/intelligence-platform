"""
weekly_digest.py
================
Builds a ranked "Top Opportunities" brief per account from the scored signals
(last 90 days) and emits:
  - reports/opportunities_<account>.csv   (always — sales-ready ranked list)
  - reports/weekly_brief_<account>.md     (if OPENAI_API_KEY — short AI brief)
  - a Slack post                          (if SLACK_WEBHOOK_URL — top opportunities)

Safe & free: CSV always; AI + Slack only when their env vars are set. No outreach
is ever sent automatically. Runs in the weekly Action after the dashboard refresh.

Usage: python weekly_digest.py            # both accounts
       python weekly_digest.py --account csg
"""
from __future__ import annotations
import argparse, csv, json, os, sqlite3, urllib.request
from pathlib import Path
ROOT = Path(__file__).parent
import sys; sys.path.insert(0, str(ROOT))
from tracker.signal_score import score_company_signals

ACCOUNTS = {"healthcare": "data/tracker.db", "csg": "data/tracker_csg_v2.db"}
TOP_N = 25

def _load_scored(db):
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT c.name,c.domain,c.industry,a.signal_type,a.signal_detail,a.severity,a.signal_date "
        "FROM alerts_sent a JOIN companies c ON a.apollo_id=c.apollo_id "
        "WHERE a.dry_run=0 AND a.signal_date >= date('now','-90 days')").fetchall()
    con.close()
    sigs = [dict(r) for r in rows]
    score_company_signals(sigs)
    by_co = {}
    for s in sigs:
        co = by_co.setdefault(s["name"], {"domain": s.get("domain",""), "industry": s.get("industry",""),
                                          "score": 0.0, "types": set(), "top": None})
        co["score"] += s["_score"]; co["types"].add(s["signal_type"])
        if not co["top"] or s["_score"] > co["top"]["_score"]:
            co["top"] = s
    ranked = sorted(by_co.items(), key=lambda x: -x[1]["score"])
    return ranked

def _write_csv(account, ranked):
    out = ROOT / "reports" / f"opportunities_{account}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank","company","domain","industry","total_score","signal_types","top_signal","top_signal_date"])
        for i,(co,info) in enumerate(ranked[:200], 1):
            t = info["top"] or {}
            w.writerow([i, co, info["domain"], info["industry"], round(info["score"],1),
                        " + ".join(sorted(info["types"])), (t.get("signal_detail") or t.get("signal_type") or "")[:120],
                        t.get("signal_date","")])
    return out

def _ai_brief(account, ranked):
    key = os.environ.get("OPENAI_API_KEY","")
    if not key: return None
    try:
        from openai import OpenAI
        top = [{"company":co,"score":round(i["score"],1),"types":sorted(i["types"]),
                "top_signal":(i["top"] or {}).get("signal_detail","")[:140]} for co,i in ranked[:15]]
        oai = OpenAI(api_key=key, timeout=60)
        r = oai.chat.completions.create(model=os.environ.get("OPENAI_MODEL","gpt-4o-mini"),
            messages=[{"role":"system","content":"You are Vimi, Position2's revenue-intelligence AI. Write a crisp weekly brief (markdown): a 1-line headline, 2-sentence summary, then the top 5 opportunities as bullets — each naming the company, why-now from its signal, and the single best Position2 service to pitch (SEO/PPC/Content/Brand/RevOps). No dollar figures, no fluff."},
                      {"role":"user","content":f"Account: {account}\nTop scored opportunities:\n{json.dumps(top,indent=1)}"}],
            max_completion_tokens=700)
        md = r.choices[0].message.content.strip()
        (ROOT/"reports"/f"weekly_brief_{account}.md").write_text(md, encoding="utf-8")
        return md
    except Exception as e:
        print(f"  [ai] brief skipped: {e}"); return None

def _slack(account, ranked, brief):
    url = os.environ.get("SLACK_WEBHOOK_URL","")
    if not url: return
    lines = [f"*Weekly Opportunity Brief — {account.upper()}*"]
    if brief: lines.append(brief.split("\n\n")[0][:300])
    for i,(co,info) in enumerate(ranked[:5],1):
        lines.append(f"{i}. *{co}* (score {round(info['score'],1)}) — {' + '.join(sorted(info['types']))}")
    try:
        req = urllib.request.Request(url, data=json.dumps({"text":"\n".join(lines)}).encode(),
                                     headers={"Content-Type":"application/json"})
        urllib.request.urlopen(req, timeout=15)
        print("  [slack] posted")
    except Exception as e:
        print(f"  [slack] skipped: {e}")

def run(account):
    db = ROOT / ACCOUNTS[account]
    if not db.exists(): print(f"  {account}: DB missing"); return
    ranked = _load_scored(str(db))
    csv_out = _write_csv(account, ranked)
    brief = _ai_brief(account, ranked)
    _slack(account, ranked, brief)
    print(f"[digest] {account}: {len(ranked)} companies ranked -> {csv_out.name}"
          f"{' + AI brief' if brief else ''}")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--account", choices=list(ACCOUNTS))
    a = ap.parse_args()
    for acct in ([a.account] if a.account else ACCOUNTS):
        run(acct)

if __name__ == "__main__":
    main()
