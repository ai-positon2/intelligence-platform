#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-command dashboard refresh for BOTH accounts, preserving all Kairo work.

For each account it:
  1. prunes irrelevant News Mention rows from the SQLite DB (relevance filter),
  2. rebuilds a *plain* dashboard from the DB into a temp file,
  3. splices that fresh `const DATA = {...}` blob into the committed Kairo
     dashboard (keeping every Insights / chat / header / perf customization),
  4. verifies the result (valid JSON, Kairo markers intact).

It does NOT fetch new signals (that needs Google credentials — run the fetch
first, or use the GitHub Action which does fetch + refresh + publish).
It does NOT commit (the caller / Action handles git).
"""
import io, os, re, json, sys, sqlite3, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from tracker.dashboard_builder import build_dashboard
from tracker.snapshot_store import SnapshotStore
from tracker.csv_loader import load_companies
from tracker.news_relevance import _RELEVANT_RE, _NOISE_RE, _norm
from tracker.news_relevance import classify_signal_type, is_important_news
import datetime as _dt

_PREFIX = re.compile(r"^\s*in the news:\s*", re.I)
_DATA_RE = re.compile(r'^const DATA = .*;$', re.M)

def _keep_news(detail):
    t = _norm(_PREFIX.sub("", detail or "").strip())
    return bool(t) and any(r.search(t) for r in _RELEVANT_RE) and not any(r.search(t) for r in _NOISE_RE)

MAX_AGE_DAYS = 90

def _parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s[:10])
    except Exception:
        for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return _dt.datetime.strptime(s[:19], f).date()
            except Exception:
                pass
    return None

def prune_old(db, max_age_days=MAX_AGE_DAYS):
    """Delete signals whose event date (signal_date, else sent_at) is older than
    max_age_days. Enforces the 'last 90 days only' retention policy."""
    cutoff = _dt.date.today() - _dt.timedelta(days=max_age_days)
    con = sqlite3.connect(db)
    rows = con.execute("SELECT id, signal_date, sent_at FROM alerts_sent").fetchall()
    drop = []
    for rid, sd, sent in rows:
        d = _parse_date(sd) or _parse_date(sent)
        if d is not None and d < cutoff:
            drop.append((rid,))
    if drop:
        con.executemany("DELETE FROM alerts_sent WHERE id=?", drop)
        con.commit(); con.execute("VACUUM"); con.commit()
    con.close()
    return len(drop), len(rows)

def reclassify(db):
    """Re-apply the Position2 relevance filter to Product Launch / Partnership
    rows; downgrade anything no longer relevant to a plain News Mention."""
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT id, signal_type, signal_detail FROM alerts_sent "
        "WHERE signal_type IN ('Product Launch','Partnership')").fetchall()
    changed = 0
    for rid, st, detail in rows:
        new_st, new_sev = classify_signal_type({"title": detail or "", "summary": ""})
        if new_st != st:
            con.execute("UPDATE alerts_sent SET signal_type=?, severity=? WHERE id=?",
                        (new_st, new_sev, rid))
            changed += 1
    if changed:
        con.commit()
    con.close()
    return changed

def prune_news(db):
    """Keep only important/relevant News Mentions (strict quality gate, no count
    cap — a company keeps every genuinely important item). Returns (dropped, total)."""
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT id, signal_detail FROM alerts_sent "
        "WHERE signal_type='News Mention' AND dry_run=0").fetchall()
    total = len(rows)
    drop = [(rid,) for rid, detail in rows if not is_important_news(detail or "")]
    if drop:
        con.executemany("DELETE FROM alerts_sent WHERE id=?", drop)
        con.commit(); con.execute("VACUUM"); con.commit()
    con.close()
    return len(drop), total

def fresh_data_line(plain_html):
    s = io.open(plain_html, encoding="utf-8").read()
    m = _DATA_RE.search(s)
    if not m:
        raise SystemExit("ERROR: no `const DATA` line in freshly built dashboard: " + plain_html)
    return m.group(0)

def splice(kairo_path, data_line):
    s = io.open(kairo_path, encoding="utf-8").read()
    for marker in ('INSIGHTS v10 JS', 'id="kairo-plat"'):
        if marker not in s:
            raise SystemExit("ERROR: Kairo marker missing (%s) in %s — refusing to splice" % (marker, kairo_path))
    if not _DATA_RE.search(s):
        raise SystemExit("ERROR: no `const DATA` line in " + kairo_path)
    s2 = _DATA_RE.sub(lambda _m: data_line, s, count=1)
    json.loads(_DATA_RE.search(s2).group(0)[len("const DATA = "):-1])  # validate JSON
    b = s2.encode("utf-8")
    with open(kairo_path, "wb") as f:
        for i in range(0, len(b), 262144):
            f.write(b[i:i+262144])

def build_healthcare(tmp):
    store = SnapshotStore(ROOT / "data" / "tracker.db")
    companies = load_companies(ROOT / "apollo-accounts-export.csv")
    build_dashboard(companies_from_csv=companies, store=store, output_path=tmp, max_signal_age_days=90)

def build_csg(tmp):
    import build_csg_dashboard as bcsg
    bcsg.OUT_PATH = Path(tmp)
    bcsg.build_csg()

ACCOUNTS = [
    ("healthcare", "data/tracker.db",        "reports/dashboard.html",     build_healthcare),
    ("csg",        "data/tracker_csg_v2.db",  "reports/dashboard_csg.html", build_csg),
]

def main():
    for name, db, kairo, builder in ACCOUNTS:
        pruned_old, _tot = prune_old(str(ROOT / db))
        reclassed = reclassify(str(ROOT / db))
        dropped, total = prune_news(str(ROOT / db))
        print("[refresh] %-11s pruned %d signals >90d, reclassified %d launch/partnership" % (name, pruned_old, reclassed))
        fd, tmp = tempfile.mkstemp(suffix=".html"); os.close(fd)
        try:
            builder(tmp)
            splice(str(ROOT / kairo), fresh_data_line(tmp))
        finally:
            try: os.remove(tmp)
            except OSError: pass
        # report signal count now embedded
        sigs = len(json.loads(_DATA_RE.search(io.open(ROOT/kairo,encoding="utf-8").read()).group(0)[len("const DATA = "):-1]).get("signals", []))
        print("[refresh] %-11s dashboard refreshed | %d signals embedded | pruned %d/%d news mentions"
              % (name, sigs, dropped, total))
    print("[refresh] done. Review, then commit data/ + reports/ and push (the GitHub Action does this automatically).")

if __name__ == "__main__":
    main()
