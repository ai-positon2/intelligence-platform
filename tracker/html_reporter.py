"""Generate static HTML dashboard after each run."""

from __future__ import annotations

import html
import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from tracker.change_detector import ChangeEvent

logger = logging.getLogger(__name__)

_REPORTS_DIR = Path(__file__).parent.parent / "reports"
_SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _esc(val) -> str:
    return html.escape(str(val)) if val else ""


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Company Signal Tracker &mdash; {run_date}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #f5f5f5; color: #222; }}
  header {{ background: #1a237e; color: #fff; padding: 1.5rem 2rem; }}
  header h1 {{ font-size: 1.5rem; font-weight: 700; }}
  header p {{ opacity: .75; font-size: .9rem; margin-top: .3rem; }}
  .stats {{ display: flex; gap: 1rem; padding: 1.5rem 2rem; flex-wrap: wrap; }}
  .stat-card {{ background: #fff; border-radius: 8px; padding: 1rem 1.5rem; flex: 1; min-width: 130px;
                box-shadow: 0 1px 4px rgba(0,0,0,.1); text-align: center; }}
  .stat-card .num {{ font-size: 2.2rem; font-weight: 700; line-height: 1; }}
  .stat-card .label {{ font-size: .78rem; color: #777; margin-top: .4rem; text-transform: uppercase; letter-spacing: .04em; }}
  .chart-section {{ padding: 0 2rem 1.5rem; }}
  .chart-box {{ background: #fff; border-radius: 8px; padding: 1.25rem 1.5rem;
               box-shadow: 0 1px 4px rgba(0,0,0,.1); max-width: 720px; }}
  .chart-box h3 {{ font-size: .9rem; color: #555; margin-bottom: .75rem; text-transform: uppercase; letter-spacing: .04em; }}
  section {{ padding: 0 2rem 2rem; }}
  section h2 {{ margin-bottom: .75rem; font-size: 1rem; padding: .5rem .85rem;
               border-radius: 4px; background: #e8eaf6; color: #1a237e;
               text-transform: uppercase; letter-spacing: .05em; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
           box-shadow: 0 1px 4px rgba(0,0,0,.1); border-radius: 8px; overflow: hidden; font-size: .82rem; }}
  th {{ background: #eeeeee; text-align: left; padding: .55rem .75rem; font-size: .78rem;
        cursor: pointer; user-select: none; white-space: nowrap; color: #333; }}
  th::after {{ content: " ↕"; opacity: .35; font-size: .7rem; }}
  th:hover {{ background: #e0e0e0; }}
  td {{ padding: .5rem .75rem; border-top: 1px solid #f0f0f0; vertical-align: top; }}
  tr.row-high > td {{ background: #ffebee; }}
  tr.row-medium > td {{ background: #fff8e1; }}
  tr:hover > td {{ filter: brightness(.97); }}
  .sev-HIGH {{ color: #c62828; font-weight: 700; }}
  .sev-MEDIUM {{ color: #e65100; font-weight: 700; }}
  .sev-LOW {{ color: #1565c0; }}
  code {{ background: #f5f5f5; padding: .1em .35em; border-radius: 3px; font-size: .85em; }}
  .no-data {{ color: #999; font-style: italic; padding: .75rem; }}
  footer {{ text-align: center; padding: 2rem; color: #aaa; font-size: .78rem; }}
</style>
</head>
<body>

<header>
  <h1>Company Signal Tracker</h1>
  <p>Run date: {run_date} UTC &nbsp;&bull;&nbsp; {company_count} companies tracked &nbsp;&bull;&nbsp; {total_signals} signals this run</p>
</header>

<div class="stats">
  <div class="stat-card"><div class="num" style="color:#c62828">{high_count}</div><div class="label">HIGH Signals</div></div>
  <div class="stat-card"><div class="num" style="color:#e65100">{medium_count}</div><div class="label">MEDIUM Signals</div></div>
  <div class="stat-card"><div class="num" style="color:#1565c0">{low_count}</div><div class="label">LOW Signals</div></div>
  <div class="stat-card"><div class="num">{company_count}</div><div class="label">Companies Tracked</div></div>
</div>

<div class="chart-section">
  <div class="chart-box">
    <h3>Signals per Week &mdash; Last 8 Weeks</h3>
    <canvas id="historyChart" height="110"></canvas>
  </div>
</div>

<section>
  <h2>Signals This Run ({total_signals} total)</h2>
  {signals_table}
</section>

<section>
  <h2>Company Roster ({company_count} companies)</h2>
  {company_table}
</section>

<footer>Generated {run_date} UTC &mdash; Company Signal Tracker</footer>

<script>
const histData = {history_chart_data};
new Chart(document.getElementById('historyChart'), {{
  type: 'bar',
  data: {{
    labels: histData.labels,
    datasets: [
      {{ label: 'HIGH',   data: histData.high,   backgroundColor: '#E53935' }},
      {{ label: 'MEDIUM', data: histData.medium, backgroundColor: '#FB8C00' }},
      {{ label: 'LOW',    data: histData.low,    backgroundColor: '#1E88E5' }},
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ position: 'top' }} }},
    scales: {{ x: {{ stacked: true }}, y: {{ stacked: true, beginAtZero: true, ticks: {{ precision: 0 }} }} }}
  }}
}});

document.querySelectorAll('th').forEach((th, _, ths) => {{
  th.addEventListener('click', () => {{
    const table = th.closest('table');
    const tbody = table.querySelector('tbody');
    const col = [...th.parentNode.children].indexOf(th);
    const asc = th.dataset.asc !== '1';
    ths.forEach(t => delete t.dataset.asc);
    th.dataset.asc = asc ? '1' : '0';
    const rows = [...tbody.querySelectorAll('tr')];
    rows.sort((a, b) => {{
      const av = a.cells[col]?.textContent.trim() ?? '';
      const bv = b.cells[col]?.textContent.trim() ?? '';
      return asc ? av.localeCompare(bv, undefined, {{numeric: true}})
                 : bv.localeCompare(av, undefined, {{numeric: true}});
    }});
    rows.forEach(r => tbody.appendChild(r));
  }});
}});
</script>
</body>
</html>
"""


def _signals_table_html(changes: list[ChangeEvent]) -> str:
    if not changes:
        return '<p class="no-data">No signals detected this run.</p>'

    sorted_changes = sorted(changes, key=lambda e: (_SEV_ORDER.get(e.severity, 3), (e.company_name or "").lower()))
    headers = ["Severity", "Company", "Domain", "Signal", "Headline", "Before", "After", "Detected"]
    ths = "".join(f"<th>{h}</th>" for h in headers)

    rows = []
    for e in sorted_changes:
        row_cls = f"row-{e.severity.lower()}" if e.severity in ("HIGH", "MEDIUM") else ""
        detected = _esc((e.detected_at or "")[:19].replace("T", " "))
        rows.append(
            f'<tr class="{row_cls}">'
            f'<td class="sev-{_esc(e.severity)}">{_esc(e.severity)}</td>'
            f"<td>{_esc(e.company_name)}</td>"
            f"<td>{_esc(e.company_domain)}</td>"
            f"<td><code>{_esc(e.signal_type)}</code></td>"
            f"<td>{_esc(e.headline)}</td>"
            f"<td>{_esc(e.previous_value)}</td>"
            f"<td>{_esc(e.new_value)}</td>"
            f"<td>{detected}</td>"
            f"</tr>"
        )
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _company_table_html(companies: list[dict], signal_map: dict) -> str:
    if not companies:
        return '<p class="no-data">No company data available.</p>'

    headers = ["Company Name", "Domain", "Location", "Employees", "Revenue Band", "Funding Stage", "Last Signal", "Last Checked"]
    ths = "".join(f"<th>{h}</th>" for h in headers)

    rows = []
    for c in sorted(companies, key=lambda x: (x.get("name") or "").lower()):
        domain = c.get("domain") or ""
        name   = c.get("name") or ""
        sig    = signal_map.get(domain) or signal_map.get(name)
        sev    = sig[0] if sig else ""
        last_signal = f"{sig[1]}: {sig[2][:60]}" if sig else ""
        row_cls = f"row-{sev.lower()}" if sev in ("HIGH", "MEDIUM") else ""
        last_checked = (c.get("last_enriched_at") or "")[:10]
        rows.append(
            f'<tr class="{row_cls}">'
            f"<td>{_esc(name)}</td>"
            f"<td>{_esc(domain)}</td>"
            f"<td>{_esc(c.get('hq_location') or '')}</td>"
            f"<td>{_esc(c.get('num_employees') or '')}</td>"
            f"<td>{_esc(c.get('revenue_band') or '')}</td>"
            f"<td>{_esc(c.get('funding_stage') or '')}</td>"
            f'<td class="sev-{_esc(sev)}">{_esc(last_signal)}</td>'
            f"<td>{_esc(last_checked)}</td>"
            f"</tr>"
        )
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _history_from_db(db_path: str | Path) -> dict:
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            """
            SELECT strftime('%Y-%W', sent_at) AS week, severity, COUNT(*) AS cnt
            FROM alerts_sent
            WHERE dry_run = 0
            GROUP BY week, severity
            ORDER BY week DESC
            LIMIT 48
            """
        ).fetchall()
        conn.close()
    except Exception:
        return {"labels": [], "high": [], "medium": [], "low": []}

    weeks: dict[str, dict[str, int]] = defaultdict(lambda: {"HIGH": 0, "MEDIUM": 0, "LOW": 0})
    for week, severity, cnt in rows:
        weeks[week][severity] = cnt

    sorted_weeks = sorted(weeks.keys())[-8:]
    return {
        "labels": sorted_weeks,
        "high":   [weeks[w]["HIGH"]   for w in sorted_weeks],
        "medium": [weeks[w]["MEDIUM"] for w in sorted_weeks],
        "low":    [weeks[w]["LOW"]    for w in sorted_weeks],
    }


def generate_dashboard(
    changes: list[ChangeEvent],
    companies: list[dict],
    db_path: str | Path,
) -> Path:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    high   = [e for e in changes if e.severity == "HIGH"]
    medium = [e for e in changes if e.severity == "MEDIUM"]
    low    = [e for e in changes if e.severity == "LOW"]

    # Build lookup: domain/name → (severity, signal_type, headline) for highest-severity signal
    signal_map: dict[str, tuple] = {}
    for e in sorted(changes, key=lambda e: _SEV_ORDER.get(e.severity, 3)):
        for key in (e.company_domain, e.company_name):
            if key and key not in signal_map:
                signal_map[key] = (e.severity, e.signal_type, e.headline)

    history_chart_data = json.dumps(_history_from_db(db_path))

    page = _HTML_TEMPLATE.format(
        run_date=run_date,
        company_count=len(companies),
        total_signals=len(changes),
        high_count=len(high),
        medium_count=len(medium),
        low_count=len(low),
        signals_table=_signals_table_html(changes),
        company_table=_company_table_html(companies, signal_map),
        history_chart_data=history_chart_data,
    )

    out_path = _REPORTS_DIR / "dashboard.html"
    out_path.write_text(page, encoding="utf-8")
    logger.info("Dashboard written to %s", out_path)
    return out_path
