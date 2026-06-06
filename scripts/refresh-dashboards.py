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

_PREFIX = re.compile(r"^\s*in the news:\s*", re.I)
_DATA_RE = re.compile(r'^const DATA = .*;$', re.M)

def _keep_news(detail):
    t = _norm(_PREFIX.sub("", detail or "").strip())
    return bool(t) and any(r.search(t) for r in _RELEVANT_RE) and not any(r.search(t) for r in _NOISE_RE)

def prune_news(db):
    con = sqlite3.connect(db)
    rows = con.execute("SELECT id, signal_detail FROM alerts_sent WHERE signal_type='News Mention'").fetchall()
    drop = [(i,) for i, d in rows if not _keep_news(d)]
    if drop:
        con.executemany("DELETE FROM alerts_sent WHERE id=?", drop)
        con.commit(); con.execute("VACUUM"); con.commit()
    con.close()
    return len(drop), len(rows)

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
        dropped, total = prune_news(str(ROOT / db))
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
