"""
dashboard_builder.py — Builds a self-contained HTML dashboard from SnapshotStore data.
All data is embedded as JSON; no server required.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_money(value) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v == 0:
        return "—"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _severity_order(s: str) -> int:
    return {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(str(s).upper(), 3)


def _safe_json(obj) -> str:
    raw = json.dumps(obj, default=str, ensure_ascii=False)
    return raw.replace("</script>", "<\\/script>").replace("<!--", "<\\!--")


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_dashboard(
    companies_from_csv: list[dict],
    store,
    output_path: str | Path,
    max_signal_age_days: int = 90,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    store_companies: list[dict] = store.get_all_companies()
    recent_alerts: list[dict] = store.get_recent_alerts(limit=10_000, max_age_days=max_signal_age_days)
    weekly_runs: list[dict] = store.get_weekly_runs(limit=8)

    csv_by_id: dict[str, dict] = {}
    for row in companies_from_csv:
        aid = row.get("apollo_id") or row.get("id")
        if aid:
            csv_by_id[str(aid)] = row

    alerts_by_company: dict[str, list[dict]] = {}
    for alert in recent_alerts:
        aid = str(alert.get("apollo_id", ""))
        alerts_by_company.setdefault(aid, []).append(alert)

    companies_data: list[dict] = []
    for sc in store_companies:
        aid = str(sc.get("apollo_id", ""))
        snap = {}
        try:
            snap = store.get_latest_snapshot(aid) or {}
        except Exception:
            pass
        csv_row = csv_by_id.get(aid, {})

        company_alerts = sorted(
            alerts_by_company.get(aid, []),
            key=lambda a: _severity_order(a.get("severity", "LOW")),
        )
        signal_count = len(company_alerts)
        max_sev = company_alerts[0].get("severity", "LOW") if company_alerts else "NONE"
        last_sig = company_alerts[0].get("signal_type", "") if company_alerts else ""

        leadership = []
        try:
            raw_lead = snap.get("leadership_json") or "[]"
            if isinstance(raw_lead, str):
                leadership = json.loads(raw_lead)
            elif isinstance(raw_lead, list):
                leadership = raw_lead
        except Exception:
            leadership = []

        tech_stack = snap.get("tech_stack") or csv_row.get("tech_stack") or []
        if isinstance(tech_stack, str):
            try:
                tech_stack = json.loads(tech_stack)
            except Exception:
                tech_stack = [t.strip() for t in tech_stack.split(",") if t.strip()]

        companies_data.append({
            "apollo_id": aid,
            "name": sc.get("name") or csv_row.get("name") or "",
            "domain": sc.get("domain") or csv_row.get("domain") or "",
            "tier": int(csv_row.get("tier") or 2),
            "industry": sc.get("industry") or csv_row.get("industry") or "",
            "city": sc.get("city") or snap.get("hq_city") or csv_row.get("city") or "",
            "state": sc.get("state") or snap.get("hq_state") or csv_row.get("state") or "",
            "logo_url": csv_row.get("logo_url") or "",
            "linkedin_url": csv_row.get("linkedin_url") or "",
            "twitter_url": csv_row.get("twitter_url") or "",
            "facebook_url": csv_row.get("facebook_url") or "",
            "description": csv_row.get("description") or "",
            "keywords": csv_row.get("keywords") or "",
            "founded_year": csv_row.get("founded_year") or "",
            "employees": snap.get("employees") or csv_row.get("employees") or "",
            "annual_revenue": snap.get("annual_revenue") or csv_row.get("annual_revenue") or "",
            "annual_revenue_fmt": _fmt_money(snap.get("annual_revenue") or csv_row.get("annual_revenue")),
            "total_funding": snap.get("total_funding") or csv_row.get("total_funding") or "",
            "total_funding_fmt": _fmt_money(snap.get("total_funding") or csv_row.get("total_funding")),
            "latest_funding_type": snap.get("latest_funding_type") or csv_row.get("latest_funding_type") or "",
            "latest_funding_amount": snap.get("latest_funding_amount") or csv_row.get("latest_funding_amount") or "",
            "latest_funding_amount_fmt": _fmt_money(snap.get("latest_funding_amount") or csv_row.get("latest_funding_amount")),
            "last_raised_at": snap.get("last_raised_at") or csv_row.get("last_raised_at") or "",
            "tech_stack": tech_stack,
            "leadership": leadership,
            "open_job_count": snap.get("open_job_count") or 0,
            "intent_score_1": snap.get("intent_score_1") or csv_row.get("intent_score_1") or 0,
            "intent_topic_1": snap.get("intent_topic_1") or csv_row.get("intent_topic_1") or "",
            "intent_score_2": snap.get("intent_score_2") or csv_row.get("intent_score_2") or 0,
            "intent_topic_2": snap.get("intent_topic_2") or csv_row.get("intent_topic_2") or "",
            "crm_stage": snap.get("crm_stage") or csv_row.get("crm_stage") or "",
            "retail_locations": snap.get("retail_locations") or "",
            "subsidiary_of": snap.get("subsidiary_of") or "",
            "signal_count": signal_count,
            "max_severity": max_sev,
            "last_signal_type": last_sig,
            "alerts": [
                {
                    "signal_type": a.get("signal_type", ""),
                    "signal_detail": a.get("signal_detail", ""),
                    "severity": a.get("severity", "LOW"),
                    "sent_at": str(a.get("sent_at", "")),
                    "source_url": a.get("source_url", ""),
                }
                for a in company_alerts
            ],
        })

    total_companies = len(companies_data)
    high_alerts = sum(1 for c in companies_data if c["max_severity"] == "HIGH")
    medium_alerts = sum(1 for c in companies_data if c["max_severity"] == "MEDIUM")
    low_alerts = sum(1 for c in companies_data if c["max_severity"] == "LOW")
    signals_this_week = len(recent_alerts)
    csuite_changes = sum(1 for a in recent_alerts if a.get("signal_type") in ("C-Suite Join", "C-Suite Exit"))
    funding_signals = sum(1 for a in recent_alerts if a.get("signal_type") == "Funding Round")
    ma_signals      = sum(1 for a in recent_alerts if a.get("signal_type") == "Acquisition / M&A")
    ipo_signals     = sum(1 for a in recent_alerts if a.get("signal_type") == "IPO Signal")
    news_signals    = sum(1 for a in recent_alerts if a.get("signal_type") == "News Mention")
    _age_cutoff = datetime.now(timezone.utc) - timedelta(days=max_signal_age_days)

    def _raised_recently(c: dict) -> bool:
        raw = c.get("last_raised_at") or ""
        if not raw:
            return False
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= _age_cutoff
        except Exception:
            return False

    funding_activity = sum(1 for c in companies_data if _raised_recently(c))
    tier1_count = sum(1 for c in companies_data if c.get("tier") == 1)
    tier2_count = total_companies - tier1_count

    industry_counts: dict[str, int] = {}
    for c in companies_data:
        ind = c.get("industry") or "Unknown"
        industry_counts[ind] = industry_counts.get(ind, 0) + 1

    signals_sorted = sorted(
        [
            {
                "apollo_id": str(a.get("apollo_id", "")),
                "signal_type": a.get("signal_type", ""),
                "signal_detail": a.get("signal_detail", ""),
                "severity": a.get("severity", "LOW"),
                "sent_at": str(a.get("sent_at", "")),
                "company_name": a.get("company_name", ""),
                "domain": a.get("domain", ""),
                "industry": a.get("industry", ""),
                "previous_value": a.get("previous_value", ""),
                "new_value": a.get("new_value", ""),
                "source_url": a.get("source_url", ""),
            }
            for a in recent_alerts
        ],
        key=lambda x: _severity_order(x["severity"]),
    )

    weekly_runs_clean = [
        {
            "run_date": str(r.get("run_date", "")),
            "companies_checked": r.get("companies_checked") or 0,
            "signals_high": r.get("signals_high") or 0,
            "signals_medium": r.get("signals_medium") or 0,
            "signals_low": r.get("signals_low") or 0,
            "duration_seconds": r.get("duration_seconds") or 0,
        }
        for r in (weekly_runs or [])
    ]
    while len(weekly_runs_clean) < 8:
        weekly_runs_clean.insert(
            0,
            {"run_date": "", "companies_checked": 0, "signals_high": 0,
             "signals_medium": 0, "signals_low": 0, "duration_seconds": 0},
        )
    weekly_runs_clean = weekly_runs_clean[-8:]

    IST = timezone(timedelta(hours=5, minutes=30))
    generated_at = datetime.now(IST).strftime("%d-%m-%Y %H:%M IST")

    data_obj = {
        "meta": {"generated_at": generated_at, "total_companies": total_companies, "max_signal_age_days": max_signal_age_days},
        "companies": companies_data,
        "signals": signals_sorted,
        "weekly_runs": weekly_runs_clean,
        "kpis": {
            "total_companies": total_companies,
            "signals_this_week": signals_this_week,
            "high_alerts": high_alerts,
            "medium_alerts": medium_alerts,
            "low_alerts": low_alerts,
            "csuite_changes":  csuite_changes,
            "funding_signals": funding_signals,
            "ma_signals":      ma_signals,
            "ipo_signals":     ipo_signals,
            "news_signals":    news_signals,
            "funding_activity": funding_activity,
            "max_signal_age_days": max_signal_age_days,
            "industry_counts": industry_counts,
            "tier1_count": tier1_count,
            "tier2_count": tier2_count,
        },
    }

    html = _render_html(data_obj)
    output_path.write_text(html, encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def _render_html(data_obj: dict) -> str:
    data_json = _safe_json(data_obj)
    return _HTML_TEMPLATE.replace("__DATA_JSON__", data_json)


# ---------------------------------------------------------------------------
# HTML template (single-file, no truncation)
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Signal Tracker Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet" />
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0e1a;--card:#111827;--border:#1f2937;--blue:#3b82f6;--purple:#8b5cf6;
  --high:#ef4444;--medium:#f59e0b;--low:#6b7280;--text:#f9fafb;--text2:#9ca3af;
  --green:#10b981;--sidebar:240px;
}
html{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);font-size:14px;height:100%}
body{display:flex;flex-direction:column;min-height:100vh}
body.modal-open{overflow:hidden}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:#374151}

/* ── Layout ── */
#app{display:flex;flex:1;min-height:0}
#sidebar{
  width:var(--sidebar);min-width:var(--sidebar);background:var(--card);
  border-right:1px solid var(--border);display:flex;flex-direction:column;
  position:fixed;top:0;left:0;height:100vh;z-index:100;overflow-y:auto;
  transition:transform .25s
}
#main{margin-left:var(--sidebar);flex:1;display:flex;flex-direction:column;min-width:0}

/* ── Top nav ── */
#topnav{
  background:var(--card);border-bottom:1px solid var(--border);
  padding:0 20px;height:56px;display:flex;align-items:center;gap:12px;
  position:sticky;top:0;z-index:90;flex-wrap:nowrap
}
.nav-title{font-size:17px;font-weight:700;color:var(--text);display:flex;align-items:center;gap:8px;white-space:nowrap}
.pulse-dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse-anim 2s infinite;flex-shrink:0}
@keyframes pulse-anim{0%,100%{box-shadow:0 0 0 0 rgba(16,185,129,.4)}50%{box-shadow:0 0 0 6px rgba(16,185,129,0)}}
.nav-updated{color:var(--text2);font-size:11px;white-space:nowrap;display:flex;align-items:center;gap:6px}
.nav-updated-ts{color:var(--text2);font-size:11px}
.nav-age-badge{border-radius:999px;padding:1px 8px;font-size:10px;font-weight:600;white-space:nowrap}
.nav-age-badge.fresh{background:rgba(16,185,129,.15);color:var(--green)}
.nav-age-badge.stale{background:rgba(239,68,68,.15);color:#ef4444}
.badge{background:var(--blue);color:#fff;border-radius:999px;padding:2px 10px;font-size:11px;font-weight:600;white-space:nowrap;flex-shrink:0}
.badge-count{background:rgba(59,130,246,.2);color:var(--blue);border-radius:999px;padding:1px 7px;font-size:10px;font-weight:600;margin-left:6px}
.nav-spacer{flex:1}
.refresh-btn{
  background:#10b981;color:#fff;border:none;border-radius:8px;
  padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer;
  font-family:inherit;display:flex;align-items:center;gap:6px;
  white-space:nowrap;flex-shrink:0;transition:background .15s
}
.refresh-btn:hover{background:#059669}
.refresh-btn .spin{display:inline-block;animation:spin-anim .8s linear infinite}
@keyframes spin-anim{to{transform:rotate(360deg)}}
#refresh-overlay{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9998
}
#refresh-modal{
  display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
  z-index:9999;background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:24px;width:440px;max-width:92vw;box-shadow:0 8px 40px rgba(0,0,0,.35)
}
#refresh-modal-close{
  position:absolute;top:12px;right:14px;background:none;border:none;
  cursor:pointer;color:var(--text2);font-size:16px;line-height:1
}
.refresh-opt{
  cursor:pointer;border:2px solid var(--border);border-radius:10px;
  padding:14px 16px;margin-bottom:10px;transition:border-color .15s,background .15s
}
.refresh-opt:hover{border-color:var(--blue);background:rgba(59,130,246,.05)}
.refresh-opt-title{display:flex;align-items:center;gap:10px;margin-bottom:5px}
.refresh-opt-title span{font-weight:700;color:var(--text);font-size:13px}
.refresh-opt-desc{font-size:11px;color:var(--text2);margin-bottom:10px;line-height:1.5}
.refresh-opt code{
  display:block;background:var(--bg);border-radius:6px;padding:7px 10px;
  font-size:12px;color:var(--blue);font-family:monospace;border:1px solid var(--border)
}
.copy-cmd-btn{
  margin-top:8px;background:var(--blue);color:#fff;border:none;border-radius:6px;
  padding:5px 12px;font-size:11px;font-weight:600;cursor:pointer;font-family:inherit;
  transition:background .15s
}
.copy-cmd-btn:hover{background:#2563eb}

/* ── Search bar ── */
#search-wrap{position:relative;flex-shrink:0}
#nav-search{
  background:var(--bg);border:1px solid var(--border);color:var(--text);
  border-radius:8px;padding:6px 32px 6px 12px;font-size:13px;width:240px;
  outline:none;font-family:inherit;transition:border-color .15s
}
#nav-search:focus{border-color:var(--blue)}
#nav-search::placeholder{color:var(--text2)}
#search-clear{
  position:absolute;right:8px;top:50%;transform:translateY(-50%);
  background:none;border:none;color:var(--text2);cursor:pointer;
  font-size:14px;line-height:1;display:none;padding:2px
}
#search-result-count{
  position:absolute;right:30px;top:50%;transform:translateY(-50%);
  font-size:10px;color:var(--text2);pointer-events:none;white-space:nowrap
}
.filters-btn{
  background:var(--bg);border:1px solid var(--border);color:var(--text2);
  border-radius:8px;padding:6px 14px;font-size:12px;cursor:pointer;
  font-family:inherit;font-weight:500;transition:all .15s;
  display:flex;align-items:center;gap:6px;white-space:nowrap;flex-shrink:0
}
.filters-btn:hover,.filters-btn.active{border-color:var(--blue);color:var(--blue)}
.filter-badge{
  background:var(--blue);color:#fff;border-radius:999px;
  padding:0 6px;font-size:10px;font-weight:700;display:none
}
.filter-badge.show{display:inline-block}

/* ── Sidebar ── */
.sidebar-logo{padding:18px 16px 10px;font-size:15px;font-weight:700;color:var(--text);display:flex;align-items:center;gap:8px}
.sidebar-logo span{color:var(--blue)}
.sidebar-nav{display:flex;flex-direction:column;gap:2px;padding:0 8px}
.nav-item{
  display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;
  cursor:pointer;color:var(--text2);font-size:13px;font-weight:500;
  transition:all .15s;border:none;background:none;width:100%;text-align:left
}
.nav-item:hover,.nav-item.active{background:rgba(59,130,246,.12);color:var(--blue)}
.nav-item .icon{font-size:15px;width:20px;text-align:center;flex-shrink:0}
.sidebar-footer{margin-top:auto;padding:12px;border-top:1px solid var(--border)}
.sidebar-footer p{color:var(--text2);font-size:11px;text-align:center}

/* ── Content ── */
#content{padding:20px 24px;flex:1}
.section{display:none}
.section.active{display:block}
.section-title{font-size:18px;font-weight:700;margin-bottom:20px;display:flex;align-items:center;gap:10px}

/* ── KPI cards ── */
.kpi-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:22px;overflow:visible}
.kpi-card{
  background:var(--card);border:1px solid var(--border);border-radius:12px;
  padding:16px;position:relative;overflow:visible;cursor:default;
  transition:border-color .2s,box-shadow .2s;
  display:flex;flex-direction:column;
  min-width:0;
}
.kpi-card:hover{border-color:var(--blue);box-shadow:0 0 20px rgba(59,130,246,.1)}
.kpi-card.high-card{border-color:rgba(239,68,68,.3);background:linear-gradient(135deg,#111827,rgba(239,68,68,.05))}
.kpi-icon{font-size:20px;margin-bottom:8px}
.kpi-number{font-size:30px;font-weight:700;color:var(--text);line-height:1;margin-bottom:4px}
.kpi-label{font-size:11px;color:var(--text2);font-weight:500;margin-bottom:6px}
.kpi-chip{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--text2)}
.chip-h{color:var(--high)}.chip-m{color:var(--medium)}.chip-l{color:var(--low)}
.kpi-sparkline{margin-top:auto;padding-top:10px;height:50px;width:100%}
.kpi-tooltip{
  display:none;position:absolute;top:calc(100% + 6px);left:0;z-index:200;
  background:#1e2533;border:1px solid var(--border);border-radius:8px;
  padding:10px 14px;min-width:180px;box-shadow:0 8px 32px rgba(0,0,0,.5);
  font-size:12px;color:var(--text2);pointer-events:none
}
.kpi-card:hover .kpi-tooltip{display:block}
.kpi-tooltip p{margin-bottom:3px;display:flex;justify-content:space-between;gap:8px}
.kpi-tooltip p span{color:var(--text)}

/* ── Two-column layout ── */
.two-col{display:grid;grid-template-columns:38% 1fr;gap:18px;margin-bottom:22px}

/* ── Panel / Signal feed ── */
.panel{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.panel-header{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.panel-header h3{font-size:13px;font-weight:600}
.panel-body{overflow-y:auto;flex:1}
.signal-feed-list{max-height:460px;overflow-y:auto}

.signal-item{
  padding:10px 14px;border-bottom:1px solid var(--border);
  display:flex;align-items:flex-start;gap:10px;cursor:pointer;
  transition:background .12s;border-left:2px solid transparent
}
.signal-item:last-child{border-bottom:none}
.signal-item:hover{background:rgba(255,255,255,.025)}
.signal-item.sev-high:hover{border-left-color:var(--high);background:rgba(239,68,68,.05)}
.signal-item.sev-medium:hover{border-left-color:var(--medium);background:rgba(245,158,11,.05)}
.signal-item.sev-low:hover{border-left-color:var(--low);background:rgba(107,114,128,.04)}
.signal-item.expanded{border-left-color:var(--blue)!important;background:rgba(59,130,246,.04)!important}
.signal-feed-expand{border-top:1px solid var(--border);margin-top:6px;padding-top:8px}

.sev-badge{
  border-radius:4px;padding:1px 5px;font-size:9px;font-weight:700;
  letter-spacing:.05em;white-space:nowrap;flex-shrink:0;margin-top:1px
}
.sev-HIGH{background:rgba(239,68,68,.15);color:var(--high)}
.sev-MEDIUM{background:rgba(245,158,11,.15);color:var(--medium)}
.sev-LOW{background:rgba(107,114,128,.15);color:var(--low)}
.sev-NONE{background:rgba(107,114,128,.15);color:var(--low)}

.company-avatar{
  width:32px;height:32px;border-radius:8px;
  background:linear-gradient(135deg,var(--blue),var(--purple));
  display:flex;align-items:center;justify-content:center;
  font-size:12px;font-weight:700;flex-shrink:0;overflow:hidden
}
.company-avatar img{width:100%;height:100%;object-fit:cover;border-radius:8px}
.signal-body{flex:1;min-width:0}
.signal-company{font-size:12px;font-weight:600;color:var(--text)}
.signal-type{font-size:10px;color:var(--blue);font-weight:500}
.signal-detail{font-size:11px;color:var(--text2);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.signal-meta{display:flex;align-items:center;gap:8px;margin-top:3px}
.signal-time{font-size:10px;color:var(--text2)}
.signal-links{display:flex;gap:5px}
.signal-link{font-size:10px;color:var(--blue);text-decoration:none;padding:1px 5px;border:1px solid rgba(59,130,246,.3);border-radius:4px;transition:background .12s}
.signal-link:hover{background:rgba(59,130,246,.12)}

/* ── Charts ── */
.charts-panel{display:flex;flex-direction:column;gap:14px}
.chart-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px}
.chart-card h3{font-size:12px;font-weight:600;margin-bottom:12px;color:var(--text)}
.chart-wrap{position:relative}
.chart-clickable{cursor:pointer}

/* ── Filter Panel (drawer) ── */
#filter-panel{
  position:fixed;top:0;left:0;height:100vh;width:300px;
  background:var(--card);border-right:2px solid var(--border);
  z-index:150;transform:translateX(-100%);
  transition:transform .25s cubic-bezier(.4,0,.2,1);
  display:flex;flex-direction:column;overflow:hidden
}
#filter-panel.open{transform:translateX(0)}
.fp-header{padding:16px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.fp-header h3{font-size:14px;font-weight:600}
.fp-body{flex:1;overflow-y:auto;padding:18px}
.fp-section{margin-bottom:20px}
.fp-label{font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}
.fp-footer{padding:14px 18px;border-top:1px solid var(--border);display:flex;gap:10px;flex-shrink:0}
.fp-apply{flex:1;background:var(--blue);border:none;color:#fff;border-radius:8px;padding:9px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;transition:opacity .15s}
.fp-apply:hover{opacity:.85}
.fp-reset{background:var(--bg);border:1px solid var(--border);color:var(--text2);border-radius:8px;padding:9px 16px;font-size:13px;cursor:pointer;font-family:inherit;transition:all .15s}
.fp-reset:hover{border-color:var(--blue);color:var(--text)}
.multi-select{display:flex;flex-direction:column;gap:5px;max-height:150px;overflow-y:auto;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px}
.ms-item{display:flex;align-items:center;gap:8px;padding:3px 4px;border-radius:5px;cursor:pointer;transition:background .1s}
.ms-item:hover{background:rgba(255,255,255,.04)}
.ms-item input[type=checkbox]{accent-color:var(--blue);width:13px;height:13px;cursor:pointer}
.ms-item label{font-size:12px;color:var(--text2);cursor:pointer;flex:1}
.ms-item label.checked{color:var(--text)}
.range-row{display:flex;align-items:center;gap:8px;margin-top:6px}
.range-row input[type=range]{flex:1;accent-color:var(--blue)}
.range-val{font-size:11px;color:var(--text2);white-space:nowrap;min-width:36px;text-align:right}
.toggle-row{display:flex;align-items:center;justify-content:space-between}
.toggle-row label{font-size:12px;color:var(--text2)}
.toggle{position:relative;display:inline-block;width:38px;height:20px}
.toggle input{opacity:0;width:0;height:0}
.toggle-slider{position:absolute;inset:0;background:#374151;border-radius:10px;transition:.2s}
.toggle-slider:before{content:'';position:absolute;width:14px;height:14px;left:3px;top:3px;background:#9ca3af;border-radius:50%;transition:.2s}
.toggle input:checked + .toggle-slider{background:var(--blue)}
.toggle input:checked + .toggle-slider:before{transform:translateX(18px);background:#fff}
.fp-select{background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:7px 10px;font-size:12px;font-family:inherit;outline:none;width:100%}
.fp-select option{background:var(--card)}

/* Filter panel overlay */
#fp-overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:140;display:none}
#fp-overlay.open{display:block}

/* ── Table section ── */
.section-toolbar{
  display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap
}
.search-inline-wrap{position:relative}
#tbl-search{
  background:var(--bg);border:1px solid var(--border);color:var(--text);
  border-radius:8px;padding:6px 30px 6px 12px;font-size:12px;
  font-family:inherit;outline:none;width:200px;transition:border-color .15s
}
#tbl-search:focus{border-color:var(--blue)}
#tbl-search::placeholder{color:var(--text2)}
#tbl-search-count{position:absolute;right:8px;top:50%;transform:translateY(-50%);font-size:10px;color:var(--text2);pointer-events:none}
.export-btn{
  margin-left:auto;background:rgba(59,130,246,.12);border:1px solid rgba(59,130,246,.3);
  color:var(--blue);border-radius:8px;padding:6px 14px;font-size:12px;cursor:pointer;
  font-family:inherit;font-weight:500;transition:all .15s
}
.export-btn:hover{background:rgba(59,130,246,.22)}
.result-count{font-size:12px;color:var(--text2)}

/* ── Data table ── */
.table-wrap{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.data-table{width:100%;border-collapse:collapse;font-size:12px}
.data-table thead th{
  background:#0d1321;color:var(--text2);font-weight:600;padding:10px 12px;
  text-align:left;position:sticky;top:0;z-index:10;cursor:pointer;
  border-bottom:1px solid var(--border);white-space:nowrap;user-select:none
}
.data-table thead th:hover{color:var(--text)}
.data-table thead th .sort-arrow{opacity:.3;margin-left:3px}
.data-table thead th.sorted-asc .sort-arrow::after{content:'↑';opacity:1}
.data-table thead th.sorted-desc .sort-arrow::after{content:'↓';opacity:1}
.data-table tbody tr{border-bottom:1px solid var(--border);transition:background .1s;cursor:pointer}
.data-table tbody tr:last-child{border-bottom:none}
.data-table tbody tr:hover{background:rgba(255,255,255,.03)}
.data-table tbody tr.row-high{background:rgba(239,68,68,.05)}
.data-table tbody tr.row-high:hover{background:rgba(239,68,68,.09)}
.data-table tbody tr.row-medium{background:rgba(245,158,11,.03)}
.data-table tbody tr.row-medium:hover{background:rgba(245,158,11,.07)}
.data-table tbody td{padding:9px 12px;vertical-align:middle}
.data-table tbody td.num{text-align:right}
mark{background:rgba(59,130,246,.2);color:var(--text);border-radius:2px;padding:0 1px}

/* ── Expanded row ── */
.expand-row td{padding:0}
.expand-inner{
  background:#0d1321;border-top:1px solid var(--border);padding:14px 16px;
  display:grid;grid-template-columns:1fr 1fr;gap:18px
}
.expand-section{margin-bottom:10px}
.expand-section h4{font-size:10px;color:var(--text2);font-weight:600;margin-bottom:7px;text-transform:uppercase;letter-spacing:.05em}
.expand-alerts{max-height:180px;overflow-y:auto}
.mini-alert{display:flex;align-items:center;gap:7px;padding:6px 8px;margin:2px 0;border-radius:7px;border:1px solid transparent;font-size:11px;cursor:pointer;transition:background .12s,border-color .12s}
.mini-alert:hover{background:rgba(59,130,246,.07);border-color:rgba(59,130,246,.2)}
.mini-alert:hover .mini-alert-arrow{opacity:1}
.mini-alert-type{color:var(--blue);font-weight:600;white-space:nowrap}
.mini-alert-detail{color:var(--text2);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mini-alert-time{color:var(--text2);white-space:nowrap;flex-shrink:0;font-size:10px}
.mini-alert-arrow{color:var(--blue);opacity:0;transition:opacity .12s;flex-shrink:0;font-size:10px}
.emp-bar-wrap{display:flex;align-items:center;gap:6px}
.emp-bar-svg{flex-shrink:0}
.pill-group{display:flex;flex-wrap:wrap;gap:5px}
.pill{border-radius:999px;padding:2px 8px;font-size:10px;font-weight:500;border:1px solid transparent}
.pill-crm{background:rgba(59,130,246,.1);color:var(--blue);border-color:rgba(59,130,246,.2)}
.pill-marketing{background:rgba(139,92,246,.1);color:var(--purple);border-color:rgba(139,92,246,.2)}
.pill-analytics{background:rgba(16,185,129,.1);color:var(--green);border-color:rgba(16,185,129,.2)}
.pill-infrastructure{background:rgba(245,158,11,.1);color:var(--medium);border-color:rgba(245,158,11,.2)}
.pill-security{background:rgba(239,68,68,.1);color:var(--high);border-color:rgba(239,68,68,.2)}
.pill-other{background:rgba(107,114,128,.1);color:var(--low);border-color:rgba(107,114,128,.2)}
.tag{background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:1px 5px;font-size:10px;color:var(--text2)}

/* ── Pagination ── */
.pagination{display:flex;align-items:center;justify-content:space-between;padding:11px 16px;border-top:1px solid var(--border)}
.pagination span{font-size:12px;color:var(--text2)}
.pag-controls{display:flex;align-items:center;gap:8px}
.pag-btn{background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:4px 12px;font-size:12px;cursor:pointer;font-family:inherit;transition:all .15s}
.pag-btn:hover:not(:disabled){border-color:var(--blue);color:var(--blue)}
.pag-btn:disabled{opacity:.35;cursor:default}
.pag-info{font-size:12px;color:var(--text2)}

/* ── Active filter tags ── */
.active-filter-tags{display:flex;flex-wrap:wrap;gap:6px;padding:6px 0 2px}
.filter-tag{
  background:rgba(59,130,246,.15);color:var(--blue);border-radius:999px;
  padding:3px 8px 3px 12px;font-size:11px;font-weight:500;
  display:inline-flex;align-items:center;gap:5px;cursor:pointer;
  border:1px solid rgba(59,130,246,.3);transition:background .15s
}
.filter-tag:hover{background:rgba(59,130,246,.25)}
.filter-tag-x{font-size:13px;line-height:1;opacity:.7}
.filter-tag-x:hover{opacity:1}

/* ── KPI modal ── */

#sig-detail-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9500;align-items:center;justify-content:center}
#sig-detail-overlay.open{display:flex}
#sig-detail-box{background:var(--card);border:1px solid var(--border);border-radius:16px;width:540px;max-width:94vw;max-height:88vh;overflow-y:auto;box-shadow:0 16px 60px rgba(0,0,0,.45);display:flex;flex-direction:column;position:relative}
.sdm-header{display:flex;align-items:flex-start;gap:12px;padding:22px 22px 14px;border-bottom:1px solid var(--border)}
.sdm-avatar{width:44px;height:44px;border-radius:10px;background:var(--border);display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:700;color:var(--text);flex-shrink:0;overflow:hidden}
.sdm-avatar img{width:100%;height:100%;object-fit:contain}
.sdm-company{font-size:15px;font-weight:700;color:var(--text);margin-bottom:4px}
.sdm-badges{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.sdm-body{padding:18px 22px;display:flex;flex-direction:column;gap:14px}
.sdm-section-label{font-size:10px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.sdm-detail-text{font-size:13px;color:var(--text);line-height:1.65;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.sdm-meta-row{display:flex;gap:24px;flex-wrap:wrap}
.sdm-meta-item{display:flex;flex-direction:column;gap:2px}
.sdm-meta-val{font-size:13px;color:var(--text);font-weight:500}
.sdm-prev-new{font-size:12px;color:var(--text2);background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px 14px;line-height:1.6}
.sdm-footer{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:14px 22px 18px;border-top:1px solid var(--border)}
.sdm-close{position:absolute;top:14px;right:16px;background:none;border:none;color:var(--text2);font-size:18px;cursor:pointer;line-height:1}

#kpi-overlay{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:600;
  align-items:center;justify-content:center;padding:20px;animation:fadeIn .18s
}
#kpi-overlay.open{display:flex}
#kpi-modal-box{
  background:var(--card);border:1px solid var(--border);border-radius:16px;
  width:100%;max-width:760px;max-height:88vh;overflow:hidden;
  display:flex;flex-direction:column;box-shadow:0 24px 64px rgba(0,0,0,.6);
  animation:slideUp .2s
}
.kpi-modal-header{
  padding:14px 20px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;flex-shrink:0
}
.kpi-modal-header h2{font-size:15px;font-weight:600}
.kpi-modal-search{padding:12px 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.kpi-modal-search input{
  width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);
  border-radius:8px;padding:7px 12px;font-size:13px;font-family:inherit;outline:none
}
.kpi-modal-search input:focus{border-color:var(--blue)}
#kpi-modal-body{overflow-y:auto;flex:1;padding:0}
.kpi-modal-row{
  display:flex;align-items:center;gap:12px;padding:10px 20px;
  border-bottom:1px solid var(--border);cursor:pointer;transition:background .15s
}
.kpi-modal-row:hover{background:rgba(255,255,255,.03)}
.kpi-modal-row .kpi-row-name{font-weight:500;font-size:13px;flex:1}
.kpi-modal-row .kpi-row-meta{font-size:11px;color:var(--text2);flex-shrink:0}
.kpi-modal-empty{padding:40px 20px;text-align:center;color:var(--text2)}
.kpi-modal-section-hdr{
  padding:7px 20px;font-size:10px;font-weight:600;letter-spacing:.07em;
  text-transform:uppercase;background:var(--bg);border-bottom:1px solid var(--border);
  position:sticky;top:0;z-index:1;display:flex;align-items:center;gap:8px
}
.kpi-modal-section-hdr.recent{color:#10b981}
.kpi-modal-section-hdr.historical{color:var(--text2)}
.kpi-modal-row.dim{opacity:.55}

/* ── Modal ── */
#modal-overlay{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:500;
  align-items:center;justify-content:center;padding:20px;
  animation:fadeIn .18s
}
#modal-overlay.open{display:flex}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
#modal-box{
  background:var(--card);border:1px solid var(--border);border-radius:16px;
  width:100%;max-width:940px;max-height:90vh;overflow:hidden;
  display:flex;flex-direction:column;
  box-shadow:0 24px 64px rgba(0,0,0,.6);
  animation:slideUp .2s
}
@keyframes slideUp{from{transform:translateY(24px);opacity:0}to{transform:translateY(0);opacity:1}}
#modal-header{
  padding:14px 20px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;flex-shrink:0
}
#modal-header h2{font-size:15px;font-weight:600}
.modal-close{background:none;border:none;color:var(--text2);cursor:pointer;font-size:18px;line-height:1;padding:4px 8px;border-radius:6px;transition:all .15s}
.modal-close:hover{background:rgba(255,255,255,.07);color:var(--text)}
#modal-body{display:flex;flex:1;overflow:hidden}
#modal-left{width:240px;min-width:240px;border-right:1px solid var(--border);padding:18px;overflow-y:auto;flex-shrink:0}
#modal-right{flex:1;display:flex;flex-direction:column;overflow:hidden}
.modal-logo{width:72px;height:72px;border-radius:12px;margin-bottom:12px;overflow:hidden;background:linear-gradient(135deg,var(--blue),var(--purple));display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700}
.modal-logo img{width:100%;height:100%;object-fit:cover}
#modal-name{font-size:17px;font-weight:700;margin-bottom:8px}
.modal-btns{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}
.modal-btn{background:rgba(59,130,246,.1);border:1px solid rgba(59,130,246,.3);color:var(--blue);border-radius:6px;padding:5px 11px;font-size:11px;cursor:pointer;text-decoration:none;font-family:inherit;display:inline-flex;align-items:center;gap:4px;font-weight:500;transition:all .15s}
.modal-btn:hover{background:rgba(59,130,246,.2)}
.modal-meta{font-size:12px;color:var(--text2);line-height:1.9}
.modal-meta strong{color:var(--text)}
.modal-desc{margin-top:12px;font-size:12px;color:var(--text2);line-height:1.6;border-top:1px solid var(--border);padding-top:12px}
.modal-keywords{margin-top:10px;display:flex;flex-wrap:wrap;gap:5px}

/* ── Modal tabs ── */
.tabs{display:flex;gap:0;border-bottom:1px solid var(--border);padding:0 14px;flex-shrink:0}
.tab-btn{background:none;border:none;border-bottom:2px solid transparent;color:var(--text2);padding:10px 13px;cursor:pointer;font-size:12px;font-family:inherit;font-weight:500;transition:all .15s;margin-bottom:-1px}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--blue);border-bottom-color:var(--blue)}
.tab-content{flex:1;overflow-y:auto;padding:14px}
.tab-pane{display:none}
.tab-pane.active{display:block}

/* ── Modal tab content ── */
.overview-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}
.ov-stat{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px 12px}
.ov-stat-label{font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px}
.ov-stat-value{font-size:15px;font-weight:700;color:var(--text)}
.intent-bar-wrap{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.intent-bar-label{font-size:12px;color:var(--text2);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.intent-bar-track{flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden}
.intent-bar-fill{height:100%;border-radius:3px}
.intent-score-num{font-size:11px;color:var(--text2);width:26px;text-align:right}
.lead-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:9px}
.lead-card{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px}
.lead-name{font-size:12px;font-weight:600;color:var(--text);margin-bottom:2px}
.lead-title{font-size:11px;color:var(--text2);margin-bottom:6px}
.lead-li{font-size:11px;color:var(--blue);text-decoration:none}
.lead-li:hover{text-decoration:underline}
.tech-section{margin-bottom:14px}
.tech-section h4{font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-bottom:7px}
.sig-item{padding:9px 0;border-bottom:1px solid var(--border);font-size:12px}
.sig-item:last-child{border-bottom:none}
.sig-row1{display:flex;align-items:center;gap:8px;margin-bottom:3px}
.sig-detail{color:var(--text2);line-height:1.5}
.sig-time{font-size:11px;color:var(--text2);margin-left:auto}
.modal-export-btn{margin:12px 0 4px;background:rgba(59,130,246,.12);border:1px solid rgba(59,130,246,.3);color:var(--blue);border-radius:8px;padding:6px 14px;font-size:11px;cursor:pointer;font-family:inherit;font-weight:500;transition:all .15s}
.modal-export-btn:hover{background:rgba(59,130,246,.22)}

/* ── Signals full-page ── */
.signals-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.sig-filter-select{background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:6px 10px;font-size:12px;font-family:inherit;outline:none}
.sig-filter-select option{background:var(--card)}

/* ── Trends ── */
.trends-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}

/* ── Empty state ── */
.empty{text-align:center;padding:36px;color:var(--text2)}
.empty p{font-size:13px}

/* ── Misc ── */
.row-num{color:var(--text2);font-size:11px}
.placeholder-tab{padding:36px;text-align:center;color:var(--text2)}
.placeholder-tab p{font-size:13px;line-height:1.6}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.section-header h2{font-size:16px;font-weight:700}

/* ── Mobile ── */
@media(max-width:768px){
  #sidebar{transform:translateX(-100%)}
  #sidebar.mobile-open{transform:translateX(0)}
  #main{margin-left:0}
  .kpi-row{grid-template-columns:1fr 1fr;gap:8px}
  .two-col{grid-template-columns:1fr}
  .trends-grid{grid-template-columns:1fr}
  #modal-box{max-width:100%;max-height:95vh;border-radius:12px 12px 0 0;position:fixed;bottom:0;left:0;right:0;margin:0}
  #modal-left{width:100%;min-width:0;border-right:none;border-bottom:1px solid var(--border)}
  #modal-body{flex-direction:column}
  #nav-search{width:160px}
  #bottom-tabs{display:flex}
  #filter-panel{width:100%}
}
#bottom-tabs{
  display:none;position:fixed;bottom:0;left:0;right:0;
  background:var(--card);border-top:1px solid var(--border);
  z-index:110;height:56px
}
.bt-item{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;background:none;border:none;color:var(--text2);font-size:9px;font-weight:500;cursor:pointer;font-family:inherit;transition:color .15s;padding:0}
.bt-item.active,.bt-item:hover{color:var(--blue)}
.bt-item .bt-icon{font-size:18px}
@media(max-width:768px){body{padding-bottom:56px}}
</style>
</head>
<body>
<div id="app">

<!-- Filter Panel Overlay -->
<div id="fp-overlay" onclick="closeFilterPanel()"></div>

<!-- Filter Panel -->
<div id="filter-panel">
  <div class="fp-header">
    <h3>Filters</h3>
    <button class="modal-close" onclick="closeFilterPanel()">✕</button>
  </div>
  <div class="fp-body">
    <div class="fp-section">
      <div class="fp-label">Industry</div>
      <div class="multi-select" id="fp-industry"></div>
    </div>
    <div class="fp-section">
      <div class="fp-label">State</div>
      <div class="multi-select" id="fp-state"></div>
    </div>
    <div class="fp-section">
      <div class="fp-label">Severity</div>
      <select class="fp-select" id="fp-severity">
        <option value="">Any severity</option>
        <option value="HIGH">Has HIGH</option>
        <option value="MEDIUM">Has MEDIUM</option>
        <option value="LOW">LOW only</option>
      </select>
    </div>
    <div class="fp-section">
      <div class="fp-label">Min Employees</div>
      <div class="range-row">
        <input type="range" id="fp-emp-min" min="0" max="10000" step="100" value="0" oninput="document.getElementById('fp-emp-min-val').textContent=this.value==='0'?'Any':Number(this.value).toLocaleString()" />
        <span class="range-val" id="fp-emp-min-val">Any</span>
      </div>
      <div class="fp-label" style="margin-top:10px">Max Employees</div>
      <div class="range-row">
        <input type="range" id="fp-emp-max" min="0" max="100000" step="500" value="100000" oninput="document.getElementById('fp-emp-max-val').textContent=Number(this.value)>=100000?'Any':Number(this.value).toLocaleString()" />
        <span class="range-val" id="fp-emp-max-val">Any</span>
      </div>
    </div>
    <div class="fp-section">
      <div class="fp-label">Funding Stage</div>
      <select class="fp-select" id="fp-funding">
        <option value="">Any stage</option>
      </select>
    </div>
    <div class="fp-section">
      <div class="fp-label">Has Signals Only</div>
      <div class="toggle-row">
        <label>Show only companies with signals</label>
        <label class="toggle"><input type="checkbox" id="fp-has-signals" /><span class="toggle-slider"></span></label>
      </div>
    </div>
    <div class="fp-section" style="background:rgba(59,130,246,.07);border-radius:8px;padding:10px 12px;margin-top:4px">
      <div class="fp-label" style="color:var(--blue)">Date Range</div>
      <div style="font-size:12px;color:var(--text2);margin-top:4px">Last <strong id="fp-age-days">90</strong> days (fixed)</div>
      <div style="font-size:11px;color:var(--text2);margin-top:3px;opacity:.8">Signals older than this window are automatically hidden.</div>
    </div>
  </div>
  <div class="fp-footer">
    <button class="fp-reset" onclick="resetFilters()">Reset</button>
    <button class="fp-apply" onclick="applyPanelFilters()">Apply Filters</button>
  </div>
</div>

<!-- Sidebar -->
<nav id="sidebar">
  <div class="sidebar-logo"><span>◈</span> Signal Tracker</div>
  <div class="sidebar-nav">
    <button class="nav-item active" id="nav-overview" onclick="showSection('overview',this)">
      <span class="icon">📊</span> Overview <span class="badge-count" id="snav-overview"></span>
    </button>
    <button class="nav-item" id="nav-companies" onclick="showSection('companies',this)">
      <span class="icon">🏢</span> Companies <span class="badge-count" id="snav-companies"></span>
    </button>
    <button class="nav-item" id="nav-signals" onclick="showSection('signals',this)">
      <span class="icon">⚡</span> Signals <span class="badge-count" id="snav-signals"></span>
    </button>
    <button class="nav-item" id="nav-trends" onclick="showSection('trends',this)">
      <span class="icon">📈</span> Trends <span class="badge-count" id="snav-trends"></span>
    </button>
  </div>
  <div class="sidebar-footer">
    <p id="sidebar-gen"></p>
  </div>
</nav>

<!-- Main -->
<div id="main">
  <header id="topnav">
    <div class="nav-title">
      <div class="pulse-dot"></div>
      Signal Tracker
    </div>
    <div class="nav-updated" id="nav-updated">
      <span class="nav-updated-ts" id="nav-updated-ts"></span>
      <span class="nav-age-badge fresh" id="nav-age-badge"></span>
    </div>
    <div class="nav-spacer"></div>
    <button class="refresh-btn" id="refresh-btn" onclick="showRefreshModal()">
      <span id="refresh-icon">⟳</span> Refresh Dashboard
    </button>
    <button class="filters-btn" id="filters-btn" onclick="toggleFilterPanel()">
      ⚙ Filters <span class="filter-badge" id="filter-active-count">0</span>
    </button>
    <div id="search-wrap">
      <input type="text" id="nav-search" placeholder="Search companies…" autocomplete="off"
             oninput="onNavSearch(this.value)" onkeydown="if(event.key==='Escape'){clearSearch()}" />
      <span id="search-result-count"></span>
      <button id="search-clear" onclick="clearSearch()" title="Clear search">✕</button>
    </div>
  </header>

  <!-- Refresh modal overlay -->
  <div id="refresh-overlay" onclick="hideRefreshModal()"></div>

  <!-- Refresh modal -->
  <div id="refresh-modal">
    <button id="refresh-modal-close" onclick="hideRefreshModal()" title="Close">✕</button>
    <div style="font-size:15px;font-weight:700;color:var(--text);margin-bottom:4px">⟳ Refresh Dashboard</div>
    <div style="font-size:12px;color:var(--text2);margin-bottom:18px">Choose what to refresh, then run the command in your terminal</div>

    <!-- Option 1: Sheets only -->
    <div class="refresh-opt" onclick="highlightOpt('opt-sheets')">
      <div class="refresh-opt-title" id="opt-sheets">
        <span style="font-size:18px">📊</span>
        <span>Refresh Sheets</span>
      </div>
      <div class="refresh-opt-desc">
        Re-reads all Google Sheets signal data and updates HIGH signals only.<br>
        Fast — no internet news fetch required.
      </div>
      <code id="cmd-sheets">python main.py --sheets-only</code>
      <button class="copy-cmd-btn" onclick="copyCmd('cmd-sheets', event)">Copy command</button>
    </div>

    <!-- Option 2: News only -->
    <div class="refresh-opt" onclick="highlightOpt('opt-news')">
      <div class="refresh-opt-title" id="opt-news">
        <span style="font-size:18px">📰</span>
        <span>Refresh Google News</span>
      </div>
      <div class="refresh-opt-desc">
        Fetches the latest Google News RSS for all companies and updates LOW signals.<br>
        Takes ~20 minutes for 1,251 companies.
      </div>
      <code id="cmd-news">python main.py --news-only</code>
      <button class="copy-cmd-btn" onclick="copyCmd('cmd-news', event)">Copy command</button>
    </div>

    <!-- Option 3: Publish to Railway -->
    <div class="refresh-opt" onclick="highlightOpt('opt-publish')" style="margin-bottom:0">
      <div class="refresh-opt-title" id="opt-publish">
        <span style="font-size:18px">🚀</span>
        <span>Publish to Live Site</span>
      </div>
      <div class="refresh-opt-desc">
        After running either command above, push the updated dashboard to Railway.<br>
        Run this from <strong>C:\Users\krishna.l\company-signal-tracker\</strong>
      </div>
      <code id="cmd-publish">git add reports/dashboard.html ; git commit -m "Update dashboard" ; git push</code>
      <button class="copy-cmd-btn" onclick="copyCmd('cmd-publish', event)">Copy command</button>
    </div>
  </div>

  <div id="content">

    <!-- OVERVIEW -->
    <div id="section-overview" class="section active">
      <div class="kpi-row" id="kpi-row"></div>
      <div class="two-col">
        <div class="panel">
          <div class="panel-header">
            <h3>⚡ Signal Feed</h3>
            <span class="badge" id="feed-count"></span>
          </div>
          <div style="font-size:11px;color:var(--blue);background:rgba(59,130,246,.08);border-radius:6px;padding:5px 10px;margin-bottom:8px;display:flex;align-items:center;gap:6px">
            <span>🕐</span> Showing signals from last <strong id="feed-age-label">90</strong> days only
          </div>
          <div class="signal-feed-list" id="signal-feed"></div>
        </div>
        <div class="charts-panel">
          <div class="chart-card">
            <h3>Weekly Signal Trend <span style="font-size:10px;color:var(--text2);font-weight:400">(click point to filter feed)</span></h3>
            <div class="chart-wrap chart-clickable" style="height:150px"><canvas id="chart-trend"></canvas></div>
          </div>
          <div class="chart-card">
            <h3>Signal Type Breakdown <span style="font-size:10px;color:var(--text2);font-weight:400">(click to filter feed)</span></h3>
            <div class="chart-wrap chart-clickable" style="height:150px"><canvas id="chart-donut"></canvas></div>
          </div>
          <div class="chart-card">
            <h3>Top Industries by Signal Count <span style="font-size:10px;color:var(--text2);font-weight:400">(click to filter table)</span></h3>
            <div class="chart-wrap chart-clickable" style="height:150px"><canvas id="chart-industry"></canvas></div>
          </div>
        </div>
      </div>
    </div>

    <!-- COMPANIES -->
    <div id="section-companies" class="section">
      <div class="section-toolbar">
        <div class="search-inline-wrap">
          <input type="text" id="tbl-search" placeholder="Search name, domain, industry…"
                 oninput="onTableSearch(this.value)" onkeydown="if(event.key==='Escape'){clearTableSearch()}" autocomplete="off" />
          <span id="tbl-search-count"></span>
        </div>
        <span class="result-count" id="tbl-result-count"></span>
        <button class="export-btn" onclick="exportCSV()">⬇ Export CSV</button>
      </div>
      <div id="active-filter-tags" class="active-filter-tags"></div>
      <div class="table-wrap">
        <table class="data-table">
          <thead id="table-head"></thead>
          <tbody id="table-body"></tbody>
        </table>
        <div class="pagination">
          <span id="pag-summary"></span>
          <div class="pag-controls">
            <button class="pag-btn" id="pag-prev" onclick="changePage(-1)">← Prev</button>
            <span class="pag-info" id="pag-info"></span>
            <button class="pag-btn" id="pag-next" onclick="changePage(1)">Next →</button>
          </div>
        </div>
      </div>
    </div>

    <!-- SIGNALS -->
    <div id="section-signals" class="section">
      <div class="signals-toolbar">
        <div class="section-header" style="margin-bottom:0;flex:1">
          <h2>All Signals <span class="badge-count" id="all-signals-count"></span></h2>
        </div>
        <select class="sig-filter-select" id="sig-sev-filter" onchange="renderAllSignals()">
          <option value="">All severities</option>
          <option value="HIGH">HIGH</option>
          <option value="MEDIUM">MEDIUM</option>
          <option value="LOW">LOW</option>
        </select>
        <select class="sig-filter-select" id="sig-type-filter" onchange="renderAllSignals()">
          <option value="">All types</option>
        </select>
        <button class="export-btn" onclick="exportSignalsCSV()">⬇ Export CSV</button>
      </div>
      <div class="panel">
        <div id="all-signals-list" style="max-height:72vh;overflow-y:auto"></div>
      </div>
    </div>

    <!-- TRENDS -->
    <div id="section-trends" class="section">
      <div class="section-header">
        <h2>Trends</h2>
      </div>
      <div class="chart-card">
        <h3>Weekly Signal Trend (8 Weeks)</h3>
        <div class="chart-wrap" style="height:260px"><canvas id="chart-trend-full"></canvas></div>
      </div>
      <div class="trends-grid">
        <div class="chart-card">
          <h3>Signals by Category <span style="font-size:10px;color:var(--text2);font-weight:400">(click bar to filter feed)</span></h3>
          <div class="chart-wrap" style="height:260px"><canvas id="chart-by-category"></canvas></div>
        </div>
        <div class="chart-card">
          <h3>Top 10 Companies by Signal Count</h3>
          <div class="chart-wrap" style="height:260px"><canvas id="chart-top-companies"></canvas></div>
        </div>
      </div>
      <div class="trends-grid">
        <div class="chart-card">
          <h3>Signal Type Breakdown</h3>
          <div class="chart-wrap" style="height:260px"><canvas id="chart-donut-full"></canvas></div>
        </div>
        <div class="chart-card">
          <h3>Top Industries by Signal Count</h3>
          <div class="chart-wrap" style="height:260px"><canvas id="chart-industry-full"></canvas></div>
        </div>
      </div>
    </div>

  </div><!-- /content -->
</div><!-- /main -->
</div><!-- /app -->

<!-- Bottom tab bar (mobile) -->
<div id="bottom-tabs">
  <button class="bt-item active" id="bt-overview" onclick="showSection('overview',document.getElementById('nav-overview'));setActiveBt(this)">
    <span class="bt-icon">📊</span>Overview
  </button>
  <button class="bt-item" id="bt-companies" onclick="showSection('companies',document.getElementById('nav-companies'));setActiveBt(this)">
    <span class="bt-icon">🏢</span>Companies
  </button>
  <button class="bt-item" id="bt-signals" onclick="showSection('signals',document.getElementById('nav-signals'));setActiveBt(this)">
    <span class="bt-icon">⚡</span>Signals
  </button>
  <button class="bt-item" id="bt-trends" onclick="showSection('trends',document.getElementById('nav-trends'));setActiveBt(this)">
    <span class="bt-icon">📈</span>Trends
  </button>
</div>

<!-- KPI Modal -->
<div id="sig-detail-overlay" onclick="maybeSigDetailClose(event)"><div id="sig-detail-box"><button class="sdm-close" onclick="closeSigDetail()">&#x2715;</button><div id="sig-detail-content"></div></div></div>
<div id="kpi-overlay" onclick="maybeCloseKpiModal(event)">
  <div id="kpi-modal-box">
    <div class="kpi-modal-header">
      <h2 id="kpi-modal-title"></h2>
      <button class="modal-close" onclick="closeKpiModal()">✕</button>
    </div>
    <div class="kpi-modal-search">
      <input type="text" id="kpi-modal-search" placeholder="Search…" oninput="filterKpiModal(this.value)" autocomplete="off" />
    </div>
    <div id="kpi-modal-body"></div>
  </div>
</div>

<!-- Modal -->
<div id="modal-overlay" onclick="maybeCloseModal(event)">
  <div id="modal-box">
    <div id="modal-header">
      <h2 id="modal-title">Company Detail</h2>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>
    <div id="modal-body">
      <div id="modal-left"></div>
      <div id="modal-right">
        <div class="tabs" id="modal-tabs"></div>
        <div class="tab-content" id="modal-tab-content"></div>
      </div>
    </div>
  </div>
</div>

<script>
const DATA = __DATA_JSON__;

// ── State ──────────────────────────────────────────────────────────────────
let tableData = [];
let currentPage = 1;
const PAGE_SIZE = 25;
let sortCol = 'signal_count';
let sortAsc = false;
let activeSearch = '';
let activeFilters = {
  industries: [],
  states: [],
  severity: '',
  empMin: 0,
  empMax: Infinity,
  funding: '',
  hasSignals: false,
  industry_from_chart: '',
  tier: '',
};
let feedFilter = { week: null, type: null };
// Chart instances for click interactivity
let chartTrend = null, chartDonut = null, chartIndustry = null;
let chartTrendFull = null, chartDonutFull = null, chartIndustryFull = null;
// overlay stack: 'modal' | 'filters'
let overlayStack = [];

const COL_DEFS = [
  {key:'_idx',     label:'#',           sortable:false},
  {key:'_logo',    label:'',            sortable:false},
  {key:'name',     label:'Company',     sortable:true},
  {key:'industry', label:'Industry',    sortable:true},
  {key:'_location',label:'Location',    sortable:true},
  {key:'employees',label:'Employees',   sortable:true},
  {key:'annual_revenue_fmt', label:'Revenue', sortable:true},
  {key:'latest_funding_type', label:'Funding Stage', sortable:true},
  {key:'last_signal_type', label:'Last Signal', sortable:true},
  {key:'signal_count', label:'Signals', sortable:true},
  {key:'_actions', label:'',            sortable:false},
];

const MAX_EMPLOYEES = Math.max(1, ...DATA.companies.map(c => Number(c.employees)||0));

// ── Helpers ──────────────────────────────────────────────────────────────
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function highlight(text, query) {
  if (!query || !text) return esc(text);
  const escaped = esc(text);
  if (!query) return escaped;
  const re = new RegExp('(' + query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&') + ')', 'gi');
  return escaped.replace(re, '<mark>$1</mark>');
}

function relTime(dt) {
  if (!dt) return '';
  const d = new Date(dt);
  if (isNaN(d)) return dt;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  if (diff < 604800) return Math.floor(diff/86400) + 'd ago';
  return Math.floor(diff/604800) + 'w ago';
}

function initials(name) {
  return (name||'?').split(/\s+/).slice(0,2).map(w=>w[0]).join('').toUpperCase();
}

function avatarHtml(company, size=32) {
  const sz = `width:${size}px;height:${size}px`;
  if (company.logo_url) {
    return `<div class="company-avatar" style="${sz}"><img src="${esc(company.logo_url)}" loading="lazy" onerror="this.parentElement.innerHTML='<span style=\\'font-size:${Math.round(size*.38)}px\\'>${initials(company.name)}</span>'" /></div>`;
  }
  return `<div class="company-avatar" style="${sz};font-size:${Math.round(size*.38)}px"><span>${initials(company.name)}</span></div>`;
}

function formatMoney(n) {
  if (!n && n !== 0) return '—';
  const v = parseFloat(n);
  if (isNaN(v) || v === 0) return '—';
  if (v >= 1e9) return '$' + (v/1e9).toFixed(1) + 'B';
  if (v >= 1e6) return '$' + (v/1e6).toFixed(1) + 'M';
  if (v >= 1e3) return '$' + Math.round(v/1e3) + 'K';
  return '$' + v.toFixed(0);
}

function techCategory(name) {
  const n = (name||'').toLowerCase();
  if (/salesforce|hubspot|dynamics|zoho|pipedrive|sugarcrm/.test(n)) return 'crm';
  if (/marketo|pardot|mailchimp|activecampaign|klaviyo|eloqua/.test(n)) return 'marketing';
  if (/google analytics|mixpanel|segment|amplitude|tableau|looker/.test(n)) return 'analytics';
  if (/aws|azure|gcp|cloudflare|akamai|fastly/.test(n)) return 'infrastructure';
  if (/okta|crowdstrike|splunk|palo alto|zscaler/.test(n)) return 'security';
  return 'other';
}

function empBar(employees) {
  const v = Number(employees) || 0;
  if (!v) return '<span style="color:var(--text2)">—</span>';
  const w = Math.max(4, Math.round((v / MAX_EMPLOYEES) * 80));
  return `<div class="emp-bar-wrap">
    <svg class="emp-bar-svg" width="${w}" height="16" viewBox="0 0 ${w} 16">
      <rect x="0" y="4" width="${w}" height="8" rx="3" fill="#3b82f6" opacity="0.75"/>
    </svg>
    <span style="font-size:11px;color:var(--text2)">${v.toLocaleString()}</span>
  </div>`;
}

function tierBadge(tier) {
  if (Number(tier) === 1)
    return '<span title="Tier 1 — full enrichment" style="color:#f59e0b;font-size:13px;line-height:1;flex-shrink:0;margin-right:3px" aria-label="Tier 1">★</span>';
  return '<span title="Tier 2 — news only" style="color:#4b5563;font-size:11px;line-height:1;flex-shrink:0;margin-right:3px" aria-label="Tier 2">●</span>';
}

function safeUrl(domain) {
  if (!domain) return '';
  const d = String(domain).trim();
  if (d.startsWith('http://') || d.startsWith('https://')) return esc(d);
  return 'https://' + esc(d);
}

function formatFullDate(dt) {
  if (!dt) return '—';
  const d = new Date(dt);
  if (isNaN(d)) return String(dt);
  return d.toLocaleString('en-US', {month:'long',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true});
}

// ── KPI sparkline shared Chart.js config factory ────────────────────────────
function _sparkConfig(trend, hex, bgRgba) {
  return {
    type: 'line',
    data: {
      labels: Array(8).fill(''),
      datasets: [{
        data: trend,
        borderColor: hex,
        borderWidth: 2,
        tension: 0.4,
        fill: true,
        backgroundColor: bgRgba,
        pointRadius: 0,
        pointHoverRadius: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false, grid: { display: false } },
        y: { display: false, grid: { display: false }, min: 0, beginAtZero: true },
      },
      animation: false,
    },
  };
}

// Pad or trim to exactly 8 entries (prepend 0s).
// If only 1 or fewer non-zero values exist, repeat the last value across all 8
// so a single data point shows a flat line rather than a spike from 0.
function _normSpark(arr) {
  let a = arr.slice();
  while (a.length < 8) a.unshift(0);
  a = a.slice(-8);
  const nonZero = a.filter(v => v > 0);
  if (nonZero.length <= 1) {
    const val = a[a.length - 1] || 0;
    return Array(8).fill(val);
  }
  return a;
}

function countUp(el, target, duration=1200) {
  const start = performance.now();
  const step = ts => {
    const p = Math.min((ts-start)/duration, 1);
    const ease = 1 - Math.pow(1-p, 3);
    el.textContent = Math.round(ease * target).toLocaleString();
    if (p < 1) requestAnimationFrame(step);
    else el.textContent = target.toLocaleString();
  };
  requestAnimationFrame(step);
}

// ── Refresh modal ────────────────────────────────────────────────────────────
function showRefreshModal() {
  document.getElementById('refresh-overlay').style.display = 'block';
  document.getElementById('refresh-modal').style.display = 'block';
}
function hideRefreshModal() {
  document.getElementById('refresh-overlay').style.display = 'none';
  document.getElementById('refresh-modal').style.display = 'none';
  // Reset highlights
  ['opt-sheets','opt-news'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.closest('.refresh-opt').style.borderColor = '';
  });
}
function highlightOpt(id) {
  ['opt-sheets','opt-news'].forEach(oid => {
    const el = document.getElementById(oid);
    if (!el) return;
    el.closest('.refresh-opt').style.borderColor = oid === id ? 'var(--blue)' : 'var(--border)';
    el.closest('.refresh-opt').style.background   = oid === id ? 'rgba(59,130,246,.07)' : '';
  });
}
function copyCmd(id, event) {
  event.stopPropagation();
  const text = document.getElementById(id).textContent.trim();
  navigator.clipboard.writeText(text).then(() => {
    const btn = event.target;
    const orig = btn.textContent;
    btn.textContent = '✓ Copied!';
    btn.style.background = '#10b981';
    setTimeout(() => { btn.textContent = orig; btn.style.background = ''; }, 1600);
  }).catch(() => {
    const btn = event.target;
    btn.textContent = 'Select & copy manually';
    setTimeout(() => { btn.textContent = 'Copy command'; }, 2000);
  });
}

// ── Updated-ago badge ───────────────────────────────────────────────────────
function renderUpdatedBadge() {
  const ts    = document.getElementById('nav-updated-ts');
  const badge = document.getElementById('nav-age-badge');
  const raw   = DATA.meta.generated_at;   // e.g. "14-05-2026 08:30 IST"

  // Display the timestamp exactly as generated (DD-MM-YYYY HH:MM IST)
  ts.textContent = 'Updated: ' + raw;

  // Parse DD-MM-YYYY HH:MM IST → JS Date (IST = UTC+05:30)
  const match = raw.match(/^(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2}) IST$/);
  if (!match) { badge.style.display = 'none'; return; }
  const [, dd, mm, yyyy, hh, min] = match;
  const genDate = new Date(`${yyyy}-${mm}-${dd}T${hh}:${min}:00+05:30`);

  const diffSec = (Date.now() - genDate.getTime()) / 1000;
  const diffMin = diffSec / 60;
  const diffH   = diffMin / 60;
  const diffD   = diffH   / 24;

  let label;
  if      (diffSec < 60)  label = 'Updated just now';
  else if (diffMin < 60)  label = `Updated ${Math.floor(diffMin)}m ago`;
  else if (diffH   < 24)  label = `Updated ${Math.floor(diffH)}h ${Math.floor(diffMin % 60)}m ago`;
  else if (diffD   < 7)   label = `Updated ${Math.floor(diffD)}d ago`;
  else                    label = `Updated ${Math.floor(diffD / 7)}w ago`;

  badge.textContent = label;
  badge.className = 'nav-age-badge ' + (diffD > 7 ? 'stale' : 'fresh');
}
// Refresh the badge every minute so it stays accurate as the page sits open
setInterval(renderUpdatedBadge, 60_000);

// ── Bootstrap ──────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  renderUpdatedBadge();
  document.getElementById('sidebar-gen').textContent = DATA.meta.generated_at;
  const ageDays = DATA.meta.max_signal_age_days || 90;
  document.getElementById('feed-age-label').textContent = ageDays;
  document.getElementById('fp-age-days').textContent = ageDays;

  // Sidebar badges
  document.getElementById('snav-overview').textContent = DATA.kpis.total_companies;
  document.getElementById('snav-companies').textContent = DATA.kpis.total_companies;
  document.getElementById('snav-signals').textContent = DATA.kpis.signals_this_week;
  document.getElementById('snav-trends').textContent = DATA.weekly_runs.length;

  renderKPIs();
  renderSignalFeed('signal-feed', DATA.signals.slice(0,50));
  document.getElementById('feed-count').textContent = DATA.signals.length;
  renderCharts();
  buildFilterPanel();
  buildTableHead();
  applyFilters();
  renderAllSignals();
  buildSignalTypeFilter();

  document.addEventListener('keydown', handleEscape);
});

// ── Escape key handling ─────────────────────────────────────────────────────
function handleEscape(e) {
  if (e.key !== 'Escape') return;
  if (overlayStack.length === 0) return;
  const top = overlayStack[overlayStack.length - 1];
  if (top === 'modal') closeModal();
  else if (top === 'filters') closeFilterPanel();
}

// ── Section nav ──────────────────────────────────────────────────────────────
function showSection(name, btn) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.getElementById('section-' + name).classList.add('active');
}

function setActiveBt(btn) {
  document.querySelectorAll('.bt-item').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

// ── KPI cards ────────────────────────────────────────────────────────────────
function renderKPIs() {
  const k  = DATA.kpis;
  const wr = DATA.weekly_runs;   // always 8 entries padded by Python

  // Trend arrays from weekly_runs history (overview cards)
  const compTrend = _normSpark(wr.map(r => r.companies_checked));
  const sigTrend  = _normSpark(wr.map(r => r.signals_high + r.signals_medium + r.signals_low));
  const highTrend = _normSpark(wr.map(r => r.signals_high));

  // Per-type weekly trend computed from actual signal timestamps (last 8 weeks)
  function _weeklySignalTrend(types) {
    const typeSet = types ? new Set(Array.isArray(types) ? types : [types]) : null;
    const now     = Date.now();
    const weekMs  = 7 * 24 * 3600 * 1000;
    const counts  = Array(8).fill(0);
    DATA.signals.forEach(s => {
      if (typeSet && !typeSet.has(s.signal_type)) return;
      const t = new Date(s.sent_at).getTime();
      const weeksAgo = Math.floor((now - t) / weekMs);
      if (weeksAgo >= 0 && weeksAgo < 8) counts[7 - weeksAgo]++;
    });
    return _normSpark(counts);
  }

  const indLines = Object.entries(k.industry_counts)
    .sort((a,b)=>b[1]-a[1]).slice(0,6)
    .map(([ind,cnt]) => `<p>${esc(ind)} <span>${cnt}</span></p>`).join('');

  // ── Row 1: overview cards ─────────────────────────────────────────────────
  // ── Row 2: signal-type cards ──────────────────────────────────────────────
  const cards = [
    // ── Row 1 ──
    { id:'ksp0', icon:'🏢', num:k.total_companies,   label:'Companies Tracked',
      hex:'#3b82f6', bg:'rgba(59,130,246,0.15)', trend:compTrend, action:'companies', cls:'',
      chip:'',
      tooltip:`<div class="kpi-tooltip">${indLines}</div>` },
    { id:'ksp1', icon:'⚡', num:k.signals_this_week,  label:'Total Signals',
      hex:'#a78bfa', bg:'rgba(167,139,250,0.15)', trend:sigTrend, action:'signals', cls:'',
      chip:`<span class="kpi-chip"><span class="chip-h">H:${k.high_alerts}</span> <span class="chip-m">M:${k.medium_alerts}</span> <span class="chip-l">L:${k.low_alerts}</span></span>`,
      tooltip:`<div class="kpi-tooltip"><p>HIGH <span>${k.high_alerts}</span></p><p>MEDIUM <span>${k.medium_alerts}</span></p><p>LOW <span>${k.low_alerts}</span></p></div>` },
    { id:'ksp2', icon:'🔥', num:k.high_alerts,        label:'HIGH Alerts',
      hex:'#ef4444', bg:'rgba(239,68,68,0.15)',  trend:highTrend, action:'high', cls:'',
      chip:'',
      tooltip:`<div class="kpi-tooltip"><p>Companies with HIGH severity signals <span>${k.high_alerts}</span></p></div>` },
    { id:'ksp3', icon:'👔', num:k.csuite_changes,     label:'C-Suite Changes',
      hex:'#8b5cf6', bg:'rgba(139,92,246,0.15)',
      trend:_weeklySignalTrend(['C-Suite Join','C-Suite Exit']),
      action:'csuite', cls:'', chip:'',
      tooltip:`<div class="kpi-tooltip"><p>C-Suite Join / Exit in last 90d <span>${k.csuite_changes}</span></p></div>` },
    // ── Row 2 ──
    { id:'ksp4', icon:'💰', num:k.funding_signals,    label:'Funding',
      hex:'#f59e0b', bg:'rgba(245,158,11,0.15)',
      trend:_weeklySignalTrend('Funding Round'),
      action:'kfunding', cls:'', chip:'',
      tooltip:`<div class="kpi-tooltip"><p>Funding Round signals (last 90d) <span>${k.funding_signals}</span></p></div>` },
    { id:'ksp5', icon:'🤝', num:k.ma_signals,         label:'Mergers & Acquisitions',
      hex:'#10b981', bg:'rgba(16,185,129,0.15)',
      trend:_weeklySignalTrend('Acquisition / M&A'),
      action:'kma', cls:'', chip:'',
      tooltip:`<div class="kpi-tooltip"><p>Acquisition / M&A signals (last 90d) <span>${k.ma_signals}</span></p></div>` },
    { id:'ksp6', icon:'📈', num:k.ipo_signals,        label:'IPO',
      hex:'#06b6d4', bg:'rgba(6,182,212,0.15)',
      trend:_weeklySignalTrend('IPO Signal'),
      action:'kipo', cls:'', chip:'',
      tooltip:`<div class="kpi-tooltip"><p>IPO Signal detections (last 90d) <span>${k.ipo_signals}</span></p></div>` },
    { id:'ksp7', icon:'📰', num:k.news_signals,       label:'News',
      hex:'#6b7280', bg:'rgba(107,114,128,0.15)',
      trend:_weeklySignalTrend('News Mention'),
      action:'knews', cls:'', chip:'',
      tooltip:`<div class="kpi-tooltip"><p>News Mention signals (last 90d) <span>${k.news_signals}</span></p></div>` },
  ];

  const row = document.getElementById('kpi-row');
  row.innerHTML = cards.map(c => `
    <div class="kpi-card ${c.cls}" style="cursor:pointer" onclick="openKpiModal('${c.action}')">
      <div class="kpi-icon">${c.icon}</div>
      <div class="kpi-number" data-target="${c.num}">0</div>
      <div class="kpi-label">${c.label}</div>
      <div style="min-height:17px">${c.chip}</div>
      ${c.tooltip}
      <div class="kpi-sparkline"><canvas id="${c.id}"></canvas></div>
    </div>`).join('');

  cards.forEach(c => {
    const canvas = document.getElementById(c.id);
    if (canvas) new Chart(canvas, _sparkConfig(c.trend, c.hex, c.bg));
  });
  row.querySelectorAll('.kpi-number').forEach(el => countUp(el, parseInt(el.dataset.target)));
}

// ── Signal feed ──────────────────────────────────────────────────────────────
// ── Source URL helpers (global — used by renderSignalFeed, renderSignalsTab, openSigDetail) ──
function _parseStoredSource(raw) {
  if (!raw) return { name: null, url: null };
  const sep = raw.indexOf('||');
  if (sep !== -1) return { name: raw.slice(0, sep).trim(), url: raw.slice(sep + 2).trim() };
  if (raw.startsWith('http')) return { name: null, url: raw };
  return { name: raw, url: null };
}
function _signalSourceUrl(raw, stype, cname, domain) {
  const parsed = _parseStoredSource(raw);
  const q = encodeURIComponent(cname);
  const st = (stype || '').toLowerCase();
  if (parsed.url) {
    let label = parsed.name || 'Source';
    if (!parsed.name) {
      const u = parsed.url.toLowerCase();
      if (u.includes('linkedin'))         label = 'LinkedIn';
      else if (u.includes('crunchbase'))  label = 'Crunchbase';
      else if (u.includes('news.google')) label = 'Google News';
      else if (st.includes('news') || st.includes('m&a') || st.includes('ipo')) label = 'Read Article';
    }
    return { href: parsed.url, label };
  }
  const fallbackLabel = parsed.name || null;
  if (st.includes('news') || st.includes('m&a') || st.includes('ipo'))
    return { href: `https://news.google.com/search?q=${q}`, label: fallbackLabel || 'Google News' };
  if (st.includes('c-suite') || st.includes('csuite'))
    return { href: `https://www.linkedin.com/search/results/people/?keywords=${q}`, label: fallbackLabel || 'LinkedIn' };
  if (st.includes('funding'))
    return { href: `https://www.crunchbase.com/textsearch?q=${q}`, label: fallbackLabel || 'Crunchbase' };
  return { href: `https://news.google.com/search?q=${encodeURIComponent(cname + ' ' + stype)}`, label: fallbackLabel || 'Google News' };
}

function renderSignalFeed(containerId, signals) {
  const el = document.getElementById(containerId);
  if (!signals || signals.length === 0) {
    el.innerHTML = '<div class="empty"><p>No signals found</p></div>';
    return;
  }
  const compMap = {};
  DATA.companies.forEach(c => { compMap[c.apollo_id] = c; });

  el.innerHTML = signals.map(s => {
    const c = compMap[s.apollo_id] || {};
    const detail = (s.signal_detail||'').substring(0,80) + ((s.signal_detail||'').length > 80 ? '…' : '');
    const domain = s.domain || c.domain || '';
    const sev = (s.severity||'LOW');
    const apolloId = s.apollo_id || c.apollo_id || '';
    const isNews = (s.signal_type||'').toLowerCase().includes('news');
    const isCsuite = (s.signal_type||'').toLowerCase().includes('c-suite');
    const companyName = s.company_name || c.name || '';
    const rawSourceUrl = (s.source_url || '').trim();
    const _src = _signalSourceUrl(rawSourceUrl, s.signal_type, companyName, s.domain || c.domain || '');
    const sourceLink = `<a class="signal-link" href="${_src.href}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${_src.label}</a>`;
    const prevNewHtml = (s.previous_value || s.new_value)
      ? `<div style="font-size:11px;color:var(--text2);margin:4px 0">Previously: <span style="color:var(--text)">${esc(s.previous_value||'—')}</span> → Now: <span style="color:var(--text)">${esc(s.new_value||'—')}</span></div>` : '';
    return `<div class="signal-item sev-${sev.toLowerCase()}" onclick="toggleFeedSignalExpand(event,this)">
      <span class="sev-badge sev-${esc(sev)}">${esc(sev)}</span>
      <div onclick="event.stopPropagation();openModal('${esc(apolloId)}')" style="flex-shrink:0;cursor:pointer">${avatarHtml(c,32)}</div>
      <div class="signal-body">
        <div class="signal-company" onclick="event.stopPropagation();openModal('${esc(apolloId)}')" style="cursor:pointer">${esc(s.company_name||c.name||'Unknown')}</div>
        <div class="signal-type">${esc(s.signal_type)}</div>
        <div class="signal-detail" title="${esc(s.signal_detail)}">${esc(detail)}</div>
        <div class="signal-meta">
          <span class="signal-time">${relTime(s.sent_at)}</span>
          <div class="signal-links">
            ${c.linkedin_url ? `<a class="signal-link" href="${esc(c.linkedin_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">LinkedIn</a>` : ''}
            ${domain ? `<a class="signal-link" href="${safeUrl(domain)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Website</a>` : ''}
          </div>
        </div>
        <div class="signal-feed-expand" style="display:none">
          <div style="font-size:12px;color:var(--text);line-height:1.5;margin-bottom:6px">${esc(s.signal_detail||'')}</div>
          ${prevNewHtml}
          <div style="font-size:11px;color:var(--text2);margin-bottom:6px">Detected: <span style="color:var(--text)">${formatFullDate(s.sent_at)}</span></div>
          <div class="signal-links" style="margin-top:4px">${sourceLink}</div>
        </div>
      </div>
    </div>`;
  }).join('');
}

function toggleFeedSignalExpand(event, el) {
  if (event.target.closest('a') || event.target.closest('.signal-link')) return;
  const expand = el.querySelector('.signal-feed-expand');
  if (!expand) return;
  const isOpen = expand.style.display !== 'none';
  document.querySelectorAll('.signal-feed-expand').forEach(e => {
    e.style.display = 'none';
    const item = e.closest('.signal-item');
    if (item) item.classList.remove('expanded');
  });
  if (!isOpen) {
    expand.style.display = 'block';
    el.classList.add('expanded');
  }
}

// Populated by renderCharts() so filterFeedByWeek can reference real week boundaries
let _weekBuckets = [];

function filterFeedByWeek(weekIndex) {
  const b = _weekBuckets[weekIndex];
  if (!b) return;
  const filtered = DATA.signals.filter(s => {
    const t = new Date(s.sent_at).getTime();
    return !isNaN(t) && t >= b.start && t < b.end;
  });
  renderSignalFeed('signal-feed', filtered.length ? filtered : DATA.signals.slice(0, 50));
  document.getElementById('feed-count').textContent = filtered.length
    ? `${filtered.length} (week of ${b.label})`
    : DATA.signals.length;
}

function filterFeedByType(type) {
  const filtered = DATA.signals.filter(s => s.signal_type === type);
  renderSignalFeed('signal-feed', filtered.length ? filtered : DATA.signals.slice(0,50));
  document.getElementById('feed-count').textContent = filtered.length ? `${filtered.length} (${type})` : DATA.signals.length;
}

function filterTableByIndustry(industry) {
  activeFilters.industry_from_chart = industry;
  showSection('companies', document.getElementById('nav-companies'));
  applyFilters();
}

// ── Charts ──────────────────────────────────────────────────────────────────
function fmtChartDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d)) return dateStr.substring(0,10);
  return d.toLocaleDateString('en-US', {month:'short', day:'numeric'});
}

function renderCharts() {
  const palette = ['#3b82f6','#8b5cf6','#ef4444','#f59e0b','#10b981','#06b6d4','#f97316','#ec4899','#a3e635','#fbbf24'];

  // ── Build 8 weekly buckets from actual signal timestamps ──────────────────
  // Each bucket spans Mon 00:00 → Sun 23:59 for the 8 most recent calendar weeks.
  function _buildWeekBuckets(numWeeks) {
    const now = new Date();
    const dayOfWeek = (now.getDay() + 6) % 7; // 0=Mon … 6=Sun
    const thisMonday = new Date(now);
    thisMonday.setHours(0, 0, 0, 0);
    thisMonday.setDate(thisMonday.getDate() - dayOfWeek);

    const buckets = [];
    for (let i = numWeeks - 1; i >= 0; i--) {
      const start = new Date(thisMonday);
      start.setDate(start.getDate() - i * 7);
      const end = new Date(start);
      end.setDate(end.getDate() + 7);
      buckets.push({
        label : start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
        start : start.getTime(),
        end   : end.getTime(),
        high  : 0,
        medium: 0,
        low   : 0,
        total : 0,
      });
    }

    DATA.signals.forEach(s => {
      const t = new Date(s.sent_at).getTime();
      if (isNaN(t)) return;
      for (const b of buckets) {
        if (t >= b.start && t < b.end) {
          const sev = (s.severity || '').toUpperCase();
          if      (sev === 'HIGH')   b.high++;
          else if (sev === 'MEDIUM') b.medium++;
          else                       b.low++;
          b.total++;
          break;
        }
      }
    });
    return buckets;
  }

  const weekBuckets = _buildWeekBuckets(8);
  _weekBuckets = weekBuckets;                       // expose to filterFeedByWeek
  const wkLabels  = weekBuckets.map(b => b.label);
  const wkHigh    = weekBuckets.map(b => b.high);
  const wkMedium  = weekBuckets.map(b => b.medium);
  const wkLow     = weekBuckets.map(b => b.low);

  // ── Trend (overview mini-chart) ───────────────────────────────────────────
  const tCtx = document.getElementById('chart-trend');
  if (tCtx) {
    chartTrend = new Chart(tCtx, {
      type: 'bar',
      data: {
        labels: wkLabels,
        datasets: [
          { label: 'HIGH',   data: wkHigh,   backgroundColor: '#ef4444', stack: 'sev', borderRadius: 3 },
          { label: 'MEDIUM', data: wkMedium, backgroundColor: '#f59e0b', stack: 'sev', borderRadius: 3 },
          { label: 'LOW',    data: wkLow,    backgroundColor: '#6b7280', stack: 'sev', borderRadius: 3 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        onClick: (evt, elements) => {
          if (elements.length > 0) filterFeedByWeek(elements[0].index);
        },
        plugins: {
          legend: { labels: { color: '#9ca3af', boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              afterTitle: (items) => {
                const b = weekBuckets[items[0].dataIndex];
                return b ? `Total: ${b.total}` : '';
              },
            },
          },
        },
        scales: {
          x: { ticks: { color: '#9ca3af', font: { size: 10 } }, grid: { color: '#1f2937' }, stacked: true },
          y: { ticks: { color: '#9ca3af', font: { size: 10 }, precision: 0 }, grid: { color: '#1f2937' }, beginAtZero: true, stacked: true },
        },
      },
    });
  }

  // Build type counts
  const typeCounts = {};
  DATA.signals.forEach(s => { const t=s.signal_type||'Unknown'; typeCounts[t]=(typeCounts[t]||0)+1; });
  const donutLabels = Object.keys(typeCounts);
  const donutData   = Object.values(typeCounts);
  const total = donutData.reduce((a,b)=>a+b,0)||1;

  // ── Donut (overview) ──
  const dCtx = document.getElementById('chart-donut');
  if (dCtx) {
    chartDonut = new Chart(dCtx, {
      type:'doughnut',
      data:{labels:donutLabels, datasets:[{data:donutData, backgroundColor:donutLabels.map((_,i)=>palette[i%palette.length]), borderWidth:0, hoverOffset:6}]},
      options:{
        responsive:true, maintainAspectRatio:false, cutout:'65%',
        onClick:(evt, elements) => {
          if (elements.length > 0) filterFeedByType(donutLabels[elements[0].index]);
        },
        plugins:{
          legend:{position:'right', labels:{color:'#9ca3af',boxWidth:10,font:{size:11},padding:8}},
          tooltip:{callbacks:{label:ctx=>` ${ctx.label}: ${ctx.parsed} (${((ctx.parsed/total)*100).toFixed(1)}%)`}},
        },
      },
    });
  }

  // Build industry signal counts
  const indSig = {};
  DATA.signals.forEach(s => { const ind=s.industry||'Unknown'; indSig[ind]=(indSig[ind]||0)+1; });
  const sorted = Object.entries(indSig).sort((a,b)=>b[1]-a[1]).slice(0,10);

  // ── Industry bar (overview) ──
  const iCtx = document.getElementById('chart-industry');
  if (iCtx) {
    chartIndustry = new Chart(iCtx, {
      type:'bar',
      data:{
        labels:sorted.map(e=>e[0]),
        datasets:[{label:'Signals', data:sorted.map(e=>e[1]),
          backgroundColor:sorted.map((_,i)=>palette[i%palette.length]), borderRadius:4}],
      },
      options:{
        indexAxis:'y', responsive:true, maintainAspectRatio:false,
        onClick:(evt, elements) => {
          if (elements.length > 0) filterTableByIndustry(sorted[elements[0].index][0]);
        },
        plugins:{legend:{display:false}},
        scales:{
          x:{ticks:{color:'#9ca3af',font:{size:10}},grid:{color:'#1f2937'},beginAtZero:true},
          y:{ticks:{color:'#9ca3af',font:{size:10}},grid:{display:false}},
        },
      },
    });
  }

  // ── Signals by Category bar chart ──
  const catLabels = ['Funding','M&A','IPO','C-Suite Join','C-Suite Exit','News Mention','Other'];
  const catMap    = {
    'Funding':       'Funding Round',
    'M&A':           'Acquisition / M&A',
    'IPO':           'IPO Signal',
    'C-Suite Join':  'C-Suite Join',
    'C-Suite Exit':  'C-Suite Exit',
    'News Mention':  'News Mention',
  };
  const catColors = ['#f59e0b','#10b981','#06b6d4','#8b5cf6','#a78bfa','#6b7280','#374151'];
  const catCounts = catLabels.map(lbl => {
    if (lbl === 'Other') {
      const known = new Set(Object.values(catMap));
      return DATA.signals.filter(s => !known.has(s.signal_type)).length;
    }
    return DATA.signals.filter(s => s.signal_type === catMap[lbl]).length;
  });
  const catCtx = document.getElementById('chart-by-category');
  if (catCtx) {
    new Chart(catCtx, {
      type: 'bar',
      data: {
        labels: catLabels,
        datasets: [{ label: 'Signals', data: catCounts,
          backgroundColor: catColors, borderRadius: 5 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        onClick: (evt, elements) => {
          if (!elements.length) return;
          const lbl = catLabels[elements[0].index];
          filterFeedByType(lbl === 'Other' ? null : (catMap[lbl] || lbl));
        },
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#9ca3af', font: { size: 11 } }, grid: { color: '#1f2937' } },
          y: { ticks: { color: '#9ca3af', font: { size: 11 } }, grid: { color: '#1f2937' }, beginAtZero: true },
        },
      },
    });
  }

  // ── Top 10 Companies by Signal Count ──
  const compCounts = {};
  DATA.signals.forEach(s => {
    const n = s.company_name || '—';
    compCounts[n] = (compCounts[n] || 0) + 1;
  });
  const topComps = Object.entries(compCounts).sort((a,b)=>b[1]-a[1]).slice(0,10);
  const tcCtx = document.getElementById('chart-top-companies');
  if (tcCtx) {
    new Chart(tcCtx, {
      type: 'bar',
      data: {
        labels: topComps.map(e => e[0].length > 22 ? e[0].substring(0,20)+'…' : e[0]),
        datasets: [{ label: 'Signals', data: topComps.map(e => e[1]),
          backgroundColor: '#3b82f6', borderRadius: 5 }],
      },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#9ca3af', font: { size: 10 } }, grid: { color: '#1f2937' }, beginAtZero: true },
          y: { ticks: { color: '#9ca3af', font: { size: 10 } }, grid: { display: false } },
        },
      },
    });
  }

  // ── Full trend (8-week stacked bar — Trends section) ─────────────────────
  const tfCtx = document.getElementById('chart-trend-full');
  if (tfCtx) {
    new Chart(tfCtx, {
      type: 'bar',
      data: {
        labels: wkLabels,
        datasets: [
          {
            label: 'HIGH', data: wkHigh,
            backgroundColor: 'rgba(239,68,68,0.85)', stack: 'sev',
            borderRadius: { topLeft: 0, topRight: 0, bottomLeft: 4, bottomRight: 4 },
          },
          {
            label: 'MEDIUM', data: wkMedium,
            backgroundColor: 'rgba(245,158,11,0.85)', stack: 'sev',
            borderRadius: 0,
          },
          {
            label: 'LOW', data: wkLow,
            backgroundColor: 'rgba(107,114,128,0.85)', stack: 'sev',
            borderRadius: { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 },
          },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#9ca3af', boxWidth: 12, font: { size: 12 } } },
          tooltip: {
            mode: 'index', intersect: false,
            callbacks: {
              footer: (items) => {
                const total = items.reduce((s, i) => s + i.parsed.y, 0);
                return `Total: ${total}`;
              },
            },
          },
        },
        scales: {
          x: {
            stacked: true,
            ticks: { color: '#9ca3af', font: { size: 11 } },
            grid: { color: '#1f2937' },
          },
          y: {
            stacked: true,
            beginAtZero: true,
            ticks: { color: '#9ca3af', font: { size: 11 }, precision: 0 },
            grid: { color: '#1f2937' },
          },
        },
      },
    });
  }

  // ── Full donut ──
  const dfCtx = document.getElementById('chart-donut-full');
  if (dfCtx) {
    new Chart(dfCtx, {
      type:'doughnut',
      data:{labels:donutLabels, datasets:[{data:donutData, backgroundColor:donutLabels.map((_,i)=>palette[i%palette.length]), borderWidth:0, hoverOffset:8}]},
      options:{
        responsive:true, maintainAspectRatio:false, cutout:'60%',
        plugins:{
          legend:{position:'right', labels:{color:'#9ca3af',boxWidth:12,font:{size:12},padding:10}},
          tooltip:{callbacks:{label:ctx=>` ${ctx.label}: ${ctx.parsed} (${((ctx.parsed/total)*100).toFixed(1)}%)`}},
        },
      },
    });
  }

  // ── Full industry bar ──
  const ifCtx = document.getElementById('chart-industry-full');
  if (ifCtx) {
    new Chart(ifCtx, {
      type:'bar',
      data:{
        labels:sorted.map(e=>e[0]),
        datasets:[{label:'Signals', data:sorted.map(e=>e[1]),
          backgroundColor:sorted.map((_,i)=>palette[i%palette.length]), borderRadius:5}],
      },
      options:{
        indexAxis:'y', responsive:true, maintainAspectRatio:false,
        plugins:{legend:{display:false}},
        scales:{
          x:{ticks:{color:'#9ca3af',font:{size:11}},grid:{color:'#1f2937'},beginAtZero:true},
          y:{ticks:{color:'#9ca3af',font:{size:11}},grid:{display:false}},
        },
      },
    });
  }
}

// ── Filter Panel ──────────────────────────────────────────────────────────────
function buildFilterPanel() {
  const indSet = new Set(), stateSet = new Set(), fundSet = new Set();
  DATA.companies.forEach(c => {
    if (c.industry) indSet.add(c.industry);
    if (c.state) stateSet.add(c.state);
    if (c.latest_funding_type) fundSet.add(c.latest_funding_type);
  });

  // Industry checkboxes
  const indEl = document.getElementById('fp-industry');
  [...indSet].sort().forEach(ind => {
    indEl.insertAdjacentHTML('beforeend', `<div class="ms-item"><input type="checkbox" id="fp-ind-${esc(ind)}" value="${esc(ind)}" onchange="updateFilterBadge()"><label for="fp-ind-${esc(ind)}">${esc(ind)}</label></div>`);
  });

  // State checkboxes
  const stEl = document.getElementById('fp-state');
  [...stateSet].sort().forEach(st => {
    stEl.insertAdjacentHTML('beforeend', `<div class="ms-item"><input type="checkbox" id="fp-st-${esc(st)}" value="${esc(st)}" onchange="updateFilterBadge()"><label for="fp-st-${esc(st)}">${esc(st)}</label></div>`);
  });

  // Funding stage
  const fundEl = document.getElementById('fp-funding');
  [...fundSet].sort().forEach(f => {
    fundEl.insertAdjacentHTML('beforeend', `<option value="${esc(f)}">${esc(f)}</option>`);
  });
}

function toggleFilterPanel() {
  const panel = document.getElementById('filter-panel');
  const overlay = document.getElementById('fp-overlay');
  if (panel.classList.contains('open')) {
    closeFilterPanel();
  } else {
    panel.classList.add('open');
    overlay.classList.add('open');
    document.getElementById('filters-btn').classList.add('active');
    overlayStack.push('filters');
  }
}

function closeFilterPanel() {
  document.getElementById('filter-panel').classList.remove('open');
  document.getElementById('fp-overlay').classList.remove('open');
  document.getElementById('filters-btn').classList.remove('active');
  overlayStack = overlayStack.filter(x => x !== 'filters');
}

function updateFilterBadge() {
  const count = document.querySelectorAll('#fp-industry input:checked, #fp-state input:checked').length
    + (document.getElementById('fp-severity').value ? 1 : 0)
    + (document.getElementById('fp-funding').value ? 1 : 0)
    + (document.getElementById('fp-has-signals').checked ? 1 : 0)
    + (parseInt(document.getElementById('fp-emp-min').value) > 0 ? 1 : 0)
    + (parseInt(document.getElementById('fp-emp-max').value) < 100000 ? 1 : 0);
  const badge = document.getElementById('filter-active-count');
  badge.textContent = count;
  count > 0 ? badge.classList.add('show') : badge.classList.remove('show');
}

function renderFilterTags() {
  const container = document.getElementById('active-filter-tags');
  if (!container) return;
  const tags = [];
  activeFilters.industries.forEach(v => tags.push({label:'Industry: ' + v, clear:() => { activeFilters.industries = activeFilters.industries.filter(x=>x!==v); syncPanelToFilters(); applyFilters(); renderFilterTags(); updateFilterBadge(); }}));
  activeFilters.states.forEach(v => tags.push({label:'State: ' + v, clear:() => { activeFilters.states = activeFilters.states.filter(x=>x!==v); syncPanelToFilters(); applyFilters(); renderFilterTags(); updateFilterBadge(); }}));
  if (activeFilters.severity) tags.push({label:'Severity: ' + activeFilters.severity, clear:() => { activeFilters.severity=''; document.getElementById('fp-severity').value=''; applyFilters(); renderFilterTags(); updateFilterBadge(); }});
  if (activeFilters.funding) tags.push({label:'Funding: ' + activeFilters.funding, clear:() => { activeFilters.funding=''; document.getElementById('fp-funding').value=''; applyFilters(); renderFilterTags(); updateFilterBadge(); }});
  if (activeFilters.hasSignals) tags.push({label:'Has Signals', clear:() => { activeFilters.hasSignals=false; document.getElementById('fp-has-signals').checked=false; applyFilters(); renderFilterTags(); updateFilterBadge(); }});
  if (activeFilters.empMin > 0) tags.push({label:'Min Emp: ' + activeFilters.empMin, clear:() => { activeFilters.empMin=0; document.getElementById('fp-emp-min').value=0; document.getElementById('fp-emp-min-val').textContent='Any'; applyFilters(); renderFilterTags(); updateFilterBadge(); }});
  if (activeFilters.empMax < Infinity) tags.push({label:'Max Emp: ' + activeFilters.empMax, clear:() => { activeFilters.empMax=Infinity; document.getElementById('fp-emp-max').value=100000; document.getElementById('fp-emp-max-val').textContent='Any'; applyFilters(); renderFilterTags(); updateFilterBadge(); }});
  if (activeFilters.industry_from_chart) tags.push({label:'Chart: ' + activeFilters.industry_from_chart, clear:() => { activeFilters.industry_from_chart=''; applyFilters(); renderFilterTags(); updateFilterBadge(); }});
  container.innerHTML = tags.map((t,i) => `<span class="filter-tag" onclick="_clearFilterTag(${i})"><span>${esc(t.label)}</span><span class="filter-tag-x">×</span></span>`).join('');
  window._filterTagClearFns = tags.map(t => t.clear);
}

function _clearFilterTag(i) { if (window._filterTagClearFns && window._filterTagClearFns[i]) window._filterTagClearFns[i](); }

function syncPanelToFilters() {
  document.querySelectorAll('#fp-industry input').forEach(el => { el.checked = activeFilters.industries.includes(el.value); });
  document.querySelectorAll('#fp-state input').forEach(el => { el.checked = activeFilters.states.includes(el.value); });
}

function applyPanelFilters() {
  activeFilters.industries = [...document.querySelectorAll('#fp-industry input:checked')].map(el => el.value);
  activeFilters.states     = [...document.querySelectorAll('#fp-state input:checked')].map(el => el.value);
  activeFilters.severity   = document.getElementById('fp-severity').value;
  activeFilters.funding    = document.getElementById('fp-funding').value;
  activeFilters.hasSignals = document.getElementById('fp-has-signals').checked;
  const empMin = parseInt(document.getElementById('fp-emp-min').value) || 0;
  const empMax = parseInt(document.getElementById('fp-emp-max').value) || 100000;
  activeFilters.empMin = empMin;
  activeFilters.empMax = empMax >= 100000 ? Infinity : empMax;
  updateFilterBadge();
  renderFilterTags();
  closeFilterPanel();
  // Switch to Companies tab so filtered results are visible
  showSection('companies', document.getElementById('nav-companies'));
  applyFilters();
}

function resetFilters() {
  document.querySelectorAll('#fp-industry input, #fp-state input').forEach(el => el.checked = false);
  document.getElementById('fp-severity').value = '';
  document.getElementById('fp-funding').value = '';
  document.getElementById('fp-has-signals').checked = false;
  document.getElementById('fp-emp-min').value = 0;
  document.getElementById('fp-emp-max').value = 100000;
  document.getElementById('fp-emp-min-val').textContent = 'Any';
  document.getElementById('fp-emp-max-val').textContent = 'Any';
  activeFilters = {industries:[], states:[], severity:'', empMin:0, empMax:Infinity, funding:'', hasSignals:false, industry_from_chart:''};
  updateFilterBadge();
  renderFilterTags();
  applyFilters();
}

// ── Search ────────────────────────────────────────────────────────────────────
function onNavSearch(val) {
  activeSearch = val;
  const clearBtn = document.getElementById('search-clear');
  clearBtn.style.display = val ? 'block' : 'none';
  // Switch to Companies section so results are visible
  if (val) showSection('companies', document.getElementById('nav-companies'));
  applyFilters();
  updateSearchCount();
}

function clearSearch() {
  document.getElementById('nav-search').value = '';
  activeSearch = '';
  document.getElementById('search-clear').style.display = 'none';
  document.getElementById('search-result-count').textContent = '';
  applyFilters();
}

function updateSearchCount() {
  const el = document.getElementById('search-result-count');
  if (activeSearch) {
    el.textContent = tableData.length + ' found';
    el.style.right = '30px';
  } else {
    el.textContent = '';
  }
}

function onTableSearch(val) {
  activeSearch = val;
  applyFilters();
  const el = document.getElementById('tbl-search-count');
  el.innerHTML = val ? `${tableData.length} results for "<strong>${esc(val)}</strong>"` : '';
}

function clearTableSearch() {
  document.getElementById('tbl-search').value = '';
  activeSearch = '';
  document.getElementById('tbl-search-count').textContent = '';
  applyFilters();
}

// ── Table ─────────────────────────────────────────────────────────────────────
function matchesSearch(c, q) {
  if (!q) return true;
  const ql = q.toLowerCase();
  return [c.name, c.domain, c.industry, c.city, c.state, c.keywords,
          (c.tech_stack||[]).join(' '), c.description]
    .some(f => (f||'').toLowerCase().includes(ql));
}

function getFilteredSorted() {
  let rows = DATA.companies.filter(c => {
    if (activeSearch && !matchesSearch(c, activeSearch)) return false;
    if (activeFilters.industries.length && !activeFilters.industries.includes(c.industry)) return false;
    if (activeFilters.states.length && !activeFilters.states.includes(c.state)) return false;
    if (activeFilters.severity) {
      const sev = activeFilters.severity;
      if (sev === 'HIGH' && c.max_severity !== 'HIGH') return false;
      if (sev === 'MEDIUM' && !['HIGH','MEDIUM'].includes(c.max_severity)) return false;
      if (sev === 'LOW' && c.max_severity !== 'LOW') return false;
    }
    if (activeFilters.industry_from_chart && c.industry !== activeFilters.industry_from_chart) return false;
    if (activeFilters.funding && c.latest_funding_type !== activeFilters.funding) return false;
    if (activeFilters.hasSignals && c.signal_count === 0) return false;
    const emp = Number(c.employees) || 0;
    if (activeFilters.empMin > 0 && emp < activeFilters.empMin) return false;
    if (activeFilters.empMax < Infinity && emp > activeFilters.empMax) return false;
    return true;
  });

  rows.sort((a, b) => {
    let av = a[sortCol], bv = b[sortCol];
    if (sortCol === '_location') { av = (a.city||'') + (a.state||''); bv = (b.city||'') + (b.state||''); }
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    if (av == null || av === '') return 1;
    if (bv == null || bv === '') return -1;
    return sortAsc ? (av < bv ? -1 : av > bv ? 1 : 0) : (av > bv ? -1 : av < bv ? 1 : 0);
  });
  return rows;
}

function applyFilters() {
  tableData = getFilteredSorted();
  currentPage = 1;
  renderTable();
  document.getElementById('tbl-result-count').textContent =
    tableData.length + ' of ' + DATA.companies.length + ' companies';
}

function changePage(delta) {
  const totalPages = Math.ceil(tableData.length / PAGE_SIZE);
  currentPage = Math.max(1, Math.min(totalPages, currentPage + delta));
  renderTable();
}

function buildTableHead() {
  const head = document.getElementById('table-head');
  head.innerHTML = '<tr>' + COL_DEFS.map(c => {
    const sc = c.key === sortCol ? (sortAsc ? 'sorted-asc' : 'sorted-desc') : '';
    return `<th class="${sc}" onclick="${c.sortable ? `sortTable('${c.key}')` : ''}" style="${c.sortable?'':'cursor:default'}">${esc(c.label)}${c.sortable ? '<span class="sort-arrow"></span>' : ''}</th>`;
  }).join('') + '</tr>';
}

function sortTable(col) {
  if (sortCol === col) sortAsc = !sortAsc;
  else { sortCol = col; sortAsc = false; }
  buildTableHead();
  applyFilters();
}

function renderTable() {
  const totalPages = Math.max(1, Math.ceil(tableData.length / PAGE_SIZE));
  const start = (currentPage - 1) * PAGE_SIZE;
  const pageRows = tableData.slice(start, start + PAGE_SIZE);
  const q = activeSearch;

  document.getElementById('pag-summary').textContent =
    `Showing ${start+1}–${Math.min(start+PAGE_SIZE, tableData.length)} of ${tableData.length}`;
  document.getElementById('pag-info').textContent = `Page ${currentPage} / ${totalPages}`;
  document.getElementById('pag-prev').disabled = currentPage === 1;
  document.getElementById('pag-next').disabled = currentPage === totalPages;

  const tbody = document.getElementById('table-body');
  // Close any expanded row
  const expRow = tbody.querySelector('.expand-row');
  if (expRow) expRow.remove();

  tbody.innerHTML = '';
  pageRows.forEach((c, idx) => {
    const rowClass = c.max_severity === 'HIGH' ? 'row-high' : c.max_severity === 'MEDIUM' ? 'row-medium' : '';
    const tr = document.createElement('tr');
    tr.className = rowClass;
    tr.dataset.id = c.apollo_id;
    tr.onclick = () => toggleExpandRow(c.apollo_id, tr);

    const loc = [c.city, c.state].filter(Boolean).join(', ') || '—';

    tr.innerHTML = `
      <td class="row-num">${start+idx+1}</td>
      <td>${avatarHtml(c,28)}</td>
      <td><span onclick="event.stopPropagation();openModal('${esc(c.apollo_id)}')" style="cursor:pointer;color:var(--blue);font-weight:500;display:inline-flex;align-items:center;gap:2px">${tierBadge(c.tier)}${highlight(c.name, q)}</span></td>
      <td>${highlight(c.industry||'—', q)}</td>
      <td>${highlight(loc, q)}</td>
      <td>${empBar(c.employees)}</td>
      <td class="num">${esc(c.annual_revenue_fmt||'—')}</td>
      <td><span class="sev-badge" style="background:rgba(59,130,246,.1);color:var(--blue)">${esc(c.latest_funding_type||'—')}</span></td>
      <td>${esc(c.last_signal_type||'—')}</td>
      <td class="num"><span class="sev-badge sev-${esc(c.max_severity)}">${c.signal_count}</span></td>
      <td>
        ${c.linkedin_url ? `<a href="${esc(c.linkedin_url)}" target="_blank" rel="noopener" class="signal-link" onclick="event.stopPropagation()">LI</a>` : ''}
        ${c.domain ? `<a href="${safeUrl(c.domain)}" target="_blank" rel="noopener" class="signal-link" onclick="event.stopPropagation()">Web</a>` : ''}
        ${c.name ? `<a href="https://app.apollo.io/#/companies?q=${encodeURIComponent(c.name)}" target="_blank" rel="noopener" class="signal-link" onclick="event.stopPropagation()">Search Apollo</a>` : ''}
      </td>`;
    tbody.appendChild(tr);
  });
}

function toggleExpandRow(apolloId, tr) {
  const existing = tr.parentNode.querySelector('.expand-row');
  if (existing) {
    const prevId = existing.dataset.for;
    existing.remove();
    if (prevId === apolloId) return;
  }
  const c = DATA.companies.find(x => x.apollo_id === apolloId);
  if (!c) return;

  const expTr = document.createElement('tr');
  expTr.className = 'expand-row';
  expTr.dataset.for = apolloId;
  const expTd = document.createElement('td');
  expTd.colSpan = COL_DEFS.length;

  const alertsHtml = c.alerts.length === 0
    ? '<p style="color:var(--text2);font-size:12px">No signals</p>'
    : c.alerts.map((a, i) => `<div class="mini-alert" onclick="openSigDetailFromExpand('${esc(apolloId)}',${i})">
        <span class="sev-badge sev-${esc(a.severity)}">${esc(a.severity)}</span>
        <span class="mini-alert-type">${esc(a.signal_type)}</span>
        <span class="mini-alert-detail" title="${esc(a.signal_detail)}">${esc((a.signal_detail||'').substring(0,70))}</span>
        <span class="mini-alert-time">${relTime(a.sent_at)}</span>
        <span class="mini-alert-arrow">&#8599;</span>
      </div>`).join('');

  const techHtml = (c.tech_stack||[]).length === 0
    ? '<span style="color:var(--text2);font-size:12px">—</span>'
    : c.tech_stack.map(t=>`<span class="pill pill-${techCategory(t)}">${esc(t)}</span>`).join('');

  const kwHtml = (c.keywords||'').split(',').filter(Boolean)
    .map(k=>`<span class="tag">${esc(k.trim())}</span>`).join('');

  expTd.innerHTML = `<div class="expand-inner">
    <div>
      <div class="expand-section"><h4>Signal History</h4><div class="expand-alerts">${alertsHtml}</div></div>
    </div>
    <div>
      <div class="expand-section" style="margin-bottom:12px"><h4>Tech Stack</h4><div class="pill-group">${techHtml}</div></div>
      <div class="expand-section"><h4>Keywords</h4><div class="pill-group">${kwHtml}</div></div>
      ${(c.intent_score_1||0) > 0 ? `<div class="expand-section" style="margin-top:10px"><h4>Intent</h4><span style="color:var(--text2);font-size:12px">${esc(c.intent_topic_1)} (${c.intent_score_1})</span></div>` : ''}
    </div>
  </div>`;
  expTr.appendChild(expTd);
  tr.after(expTr);
}

// ── Export ─────────────────────────────────────────────────────────────────
function exportCSV() {
  const rows = getFilteredSorted();
  const cols = [
    'name','domain','industry','city','state','employees',
    'annual_revenue','total_funding','latest_funding_type',
    'signal_count','max_severity','last_signal_type',
  ];
  const headers = [
    'Company','Domain','Industry','City','State','Employees',
    'Annual Revenue','Total Funding','Latest Funding Type',
    'Signal Count','Max Severity','Last Signal Type',
  ];
  const lines = rows.map(r =>
    cols.map(c => '"' + String(r[c] == null ? '' : r[c]).replace(/"/g,'""') + '"').join(',')
  );
  dlCSV([headers.join(','), ...lines].join('\n'), 'companies_export.csv');
}

function exportSignalsCSV() {
  const sev = document.getElementById('sig-sev-filter').value;
  const typ = document.getElementById('sig-type-filter').value;
  const rows = DATA.signals.filter(s =>
    (!sev || s.severity === sev) && (!typ || s.signal_type === typ)
  );
  const cols    = ['company_name','domain','industry','signal_type','signal_detail','severity','sent_at'];
  const headers = ['Company','Domain','Industry','Signal Type','Detail','Severity','Detected At'];
  const lines   = rows.map(r =>
    cols.map(c => '"' + String(r[c] == null ? '' : r[c]).replace(/"/g,'""') + '"').join(',')
  );
  dlCSV([headers.join(','), ...lines].join('\n'), 'signals_export.csv');
}

function exportModalSignalsCSV(apolloId) {
  const c = DATA.companies.find(x => x.apollo_id === apolloId);
  if (!c) return;
  const cols    = ['signal_type','signal_detail','severity','sent_at'];
  const headers = ['Signal Type','Detail','Severity','Detected At'];
  const lines   = (c.alerts || []).map(a =>
    cols.map(col => '"' + String(a[col] == null ? '' : a[col]).replace(/"/g,'""') + '"').join(',')
  );
  dlCSV([headers.join(','), ...lines].join('\n'), (c.name||'company').replace(/\s+/g,'_') + '_signals.csv');
}

function dlCSV(csv, filename) {
  const bom  = '﻿';   // UTF-8 BOM so Excel opens correctly
  const blob = new Blob([bom + csv], { type: 'text/csv;charset=utf-8;' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 500);
}

// ── All signals section ────────────────────────────────────────────────────
function buildSignalTypeFilter() {
  // Fixed canonical list — always shown regardless of whether data exists yet
  const SIGNAL_TYPES = [
    { value: 'C-Suite Join',       label: 'C-Suite Join' },
    { value: 'C-Suite Exit',       label: 'C-Suite Exit' },
    { value: 'Funding Round',      label: 'Funding' },
    { value: 'Acquisition / M&A',  label: 'Mergers & Acquisitions' },
    { value: 'IPO Signal',         label: 'IPO' },
    { value: 'News Mention',       label: 'News' },
    { value: 'Subsidiary Change',  label: 'Subsidiary Change' },
  ];
  // Also add any signal types present in data but not in the canonical list above
  const canonical = new Set(SIGNAL_TYPES.map(t => t.value));
  const extra = [...new Set(DATA.signals.map(s => s.signal_type).filter(t => t && !canonical.has(t)))].sort();

  const sel = document.getElementById('sig-type-filter');
  [...SIGNAL_TYPES, ...extra.map(t => ({ value: t, label: t }))].forEach(({ value, label }) => {
    const o = document.createElement('option');
    o.value = value;
    o.textContent = label;
    sel.appendChild(o);
  });
}

function renderAllSignals() {
  const sev = document.getElementById('sig-sev-filter').value;
  const typ = document.getElementById('sig-type-filter').value;
  const filtered = DATA.signals.filter(s =>
    (!sev || s.severity === sev) && (!typ || s.signal_type === typ));
  document.getElementById('all-signals-count').textContent = filtered.length;
  renderSignalFeed('all-signals-list', filtered);
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal(apolloId) {
  const c = DATA.companies.find(x => x.apollo_id === apolloId);
  if (!c) return;
  document.getElementById('modal-title').textContent = c.name;

  // Left panel
  const logoHtml = c.logo_url
    ? `<div class="modal-logo"><img src="${esc(c.logo_url)}" onerror="this.parentElement.innerHTML='<span>${initials(c.name)}</span>'" /></div>`
    : `<div class="modal-logo"><span>${initials(c.name)}</span></div>`;

  const btns = [
    c.domain ? `<a class="modal-btn" href="${safeUrl(c.domain)}" target="_blank" rel="noopener">🌐 Website</a>` : '',
    c.linkedin_url ? `<a class="modal-btn" href="${esc(c.linkedin_url)}" target="_blank" rel="noopener">in LinkedIn</a>` : '',
    c.twitter_url ? `<a class="modal-btn" href="${esc(c.twitter_url)}" target="_blank" rel="noopener">𝕏 Twitter</a>` : '',
    c.facebook_url ? `<a class="modal-btn" href="${esc(c.facebook_url)}" target="_blank" rel="noopener">f Facebook</a>` : '',
  ].filter(Boolean).join('');

  const kwTags = (c.keywords||'').split(',').filter(Boolean)
    .map(k=>`<span class="tag">${esc(k.trim())}</span>`).join('');

  document.getElementById('modal-left').innerHTML = `
    ${logoHtml}
    <h2 id="modal-name" style="display:flex;align-items:center;gap:5px">${tierBadge(c.tier)}${esc(c.name)}</h2>
    <div class="modal-btns">${btns}</div>
    <div class="modal-meta">
      ${c.founded_year ? `<div><strong>Founded</strong> ${esc(String(c.founded_year))}</div>` : ''}
      ${(c.city||c.state) ? `<div><strong>HQ</strong> ${esc([c.city,c.state].filter(Boolean).join(', '))}</div>` : ''}
      ${c.industry ? `<div><strong>Industry</strong> ${esc(c.industry)}</div>` : ''}
      ${c.crm_stage ? `<div><strong>CRM Stage</strong> ${esc(c.crm_stage)}</div>` : ''}
      ${c.subsidiary_of ? `<div><strong>Parent</strong> ${esc(c.subsidiary_of)}</div>` : ''}
    </div>
    ${c.description ? `<div class="modal-desc">${esc(c.description)}</div>` : ''}
    ${kwTags ? `<div class="modal-keywords">${kwTags}</div>` : ''}
  `;

  // Tabs
  const tabDefs = ['Overview','Signals','Leadership','Tech Stack','News'];
  document.getElementById('modal-tabs').innerHTML = tabDefs.map((t,i) =>
    `<button class="tab-btn${i===0?' active':''}" onclick="switchTab(${i},this)">${t}</button>`).join('');
  document.getElementById('modal-tab-content').innerHTML = tabDefs.map((t,i) =>
    `<div class="tab-pane${i===0?' active':''}" id="mtab-${i}"></div>`).join('');

  for (let i = 0; i < tabDefs.length; i++) renderModalTab(c, i);

  document.getElementById('modal-overlay').classList.add('open');
  document.body.classList.add('modal-open');
  if (!overlayStack.includes('modal')) overlayStack.push('modal');
}

function switchTab(idx, btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('mtab-' + idx).classList.add('active');
}

function renderModalTab(c, idx) {
  const el = document.getElementById('mtab-' + idx);
  if (!el) return;
  if (idx === 0) el.innerHTML = renderOverviewTab(c);
  else if (idx === 1) el.innerHTML = renderSignalsTab(c);
  else if (idx === 2) el.innerHTML = renderLeadershipTab(c);
  else if (idx === 3) el.innerHTML = renderTechTab(c);
  else el.innerHTML = `<div class="placeholder-tab"><p style="font-size:24px;margin-bottom:8px">📰</p><p>News is fetched during the weekly run.</p></div>`;
}

function renderOverviewTab(c) {
  const stats = [
    ['Employees', c.employees ? Number(c.employees).toLocaleString() : '—'],
    ['Annual Revenue', c.annual_revenue_fmt || '—'],
    ['Total Funding', c.total_funding_fmt || '—'],
    ['Funding Stage', c.latest_funding_type || '—'],
    ['Last Raised', c.last_raised_at ? String(c.last_raised_at).substring(0,10) : '—'],
    ['Open Jobs', c.open_job_count || 0],
    ['Signal Count', c.signal_count],
    ['Max Severity', c.max_severity || 'NONE'],
  ];
  const statsHtml = `<div class="overview-grid">${stats.map(([l,v]) =>
    `<div class="ov-stat"><div class="ov-stat-label">${esc(l)}</div><div class="ov-stat-value">${esc(String(v))}</div></div>`
  ).join('')}</div>`;

  const intentHtml = (c.intent_score_1 > 0 || c.intent_score_2 > 0) ? `
    <h4 style="font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin:12px 0 8px">Intent Signals</h4>
    ${c.intent_score_1 > 0 ? `
    <div class="intent-bar-wrap">
      <span class="intent-bar-label">${esc(c.intent_topic_1||'Topic 1')}</span>
      <div class="intent-bar-track"><div class="intent-bar-fill" style="width:${Math.min(c.intent_score_1,100)}%;background:linear-gradient(90deg,var(--blue),var(--purple))"></div></div>
      <span class="intent-score-num">${c.intent_score_1}</span>
    </div>` : ''}
    ${c.intent_score_2 > 0 ? `
    <div class="intent-bar-wrap">
      <span class="intent-bar-label">${esc(c.intent_topic_2||'Topic 2')}</span>
      <div class="intent-bar-track"><div class="intent-bar-fill" style="width:${Math.min(c.intent_score_2,100)}%;background:linear-gradient(90deg,var(--purple),var(--blue))"></div></div>
      <span class="intent-score-num">${c.intent_score_2}</span>
    </div>` : ''}
  ` : '';

  const techHtml = (c.tech_stack||[]).length === 0 ? '' :
    `<h4 style="font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin:14px 0 8px">Tech Stack Summary</h4>
    <div class="pill-group">${c.tech_stack.slice(0,12).map(t=>`<span class="pill pill-${techCategory(t)}">${esc(t)}</span>`).join('')}</div>`;

  return statsHtml + intentHtml + techHtml;
}

function renderSignalsTab(c) {
  if (!c.alerts || c.alerts.length === 0)
    return '<div class="empty"><p>No signals recorded for this company.</p></div>';
  const exportBtn = `<button class="modal-export-btn" onclick="exportModalSignalsCSV('${esc(c.apollo_id)}')">⬇ Export CSV</button>`;
  const items = c.alerts.map((a, i) => {
    const isNews = (a.signal_type||'').toLowerCase().includes('news');
    const rawSrcM = (a.source_url || '').trim();
    // Always produce a source link for modal alerts too
    const _srcM = _signalSourceUrl(rawSrcM, a.signal_type, c.name || '', c.domain || '');
    const sourceLink = `<a class="modal-btn" style="font-size:10px" href="${_srcM.href}" target="_blank" rel="noopener">${_srcM.label}</a>`;
    const verified = (a.severity === 'HIGH' || a.severity === 'MEDIUM');
    const verBadge = `<span style="background:${verified?'rgba(16,185,129,.15)':'rgba(107,114,128,.15)'};color:${verified?'var(--green)':'var(--text2)'};border-radius:4px;padding:2px 8px;font-size:10px;font-weight:600">${verified?'✓ Verified':'Unverified'}</span>`;
    const prevNewHtml = (a.previous_value || a.new_value)
      ? `<div style="font-size:11px;color:var(--text2);margin:5px 0">Previously: <span style="color:var(--text)">${esc(a.previous_value||'—')}</span> → Now: <span style="color:var(--text)">${esc(a.new_value||'—')}</span></div>` : '';
    const uid = `msig-${esc(c.apollo_id)}-${i}`.replace(/[^a-zA-Z0-9-]/g,'_');
    return `
    <div class="sig-item">
      <div class="sig-row1">
        <span class="sev-badge sev-${esc(a.severity)}">${esc(a.severity)}</span>
        <strong style="color:var(--blue);font-size:12px">${esc(a.signal_type)}</strong>
        <span class="sig-time">${relTime(a.sent_at)}</span>
        <button id="btn-${uid}" onclick="toggleModalSigExpand('${uid}')" style="margin-left:auto;background:none;border:1px solid var(--border);color:var(--text2);border-radius:5px;padding:2px 8px;font-size:10px;cursor:pointer;font-family:inherit;white-space:nowrap">▶ View Details</button>
      </div>
      <div class="sig-detail">${esc((a.signal_detail||'').substring(0,120))}${(a.signal_detail||'').length>120?'…':''}</div>
      <div id="exp-${uid}" style="display:none;margin-top:8px;padding:10px 12px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
        <div style="font-size:12px;color:var(--text);line-height:1.6;margin-bottom:6px">${esc(a.signal_detail||'')}</div>
        ${prevNewHtml}
        <div style="font-size:11px;color:var(--text2);margin-bottom:8px">Detected: <span style="color:var(--text)">${formatFullDate(a.sent_at)}</span></div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${sourceLink}${verBadge}</div>
      </div>
    </div>`;
  }).join('');
  return exportBtn + items;
}

function toggleModalSigExpand(uid) {
  const content = document.getElementById('exp-' + uid);
  const btn = document.getElementById('btn-' + uid);
  if (!content) return;
  const isOpen = content.style.display !== 'none';
  content.style.display = isOpen ? 'none' : 'block';
  if (btn) btn.textContent = isOpen ? '▶ View Details' : '▼ Hide Details';
}

function renderLeadershipTab(c) {
  const leads = c.leadership || [];
  if (leads.length === 0)
    return '<div class="empty"><p>No leadership data available.</p></div>';
  return `<div class="lead-grid">${leads.map(p => `
    <div class="lead-card">
      <div class="lead-name">${esc(p.name||p.full_name||'Unknown')}</div>
      <div class="lead-title">${esc(p.title||p.headline||'')}</div>
      ${(p.linkedin_url||p.linkedin) ? `<a class="lead-li" href="${esc(p.linkedin_url||p.linkedin)}" target="_blank" rel="noopener">LinkedIn →</a>` : ''}
    </div>`).join('')}</div>`;
}

function renderTechTab(c) {
  const tech = c.tech_stack || [];
  if (tech.length === 0)
    return '<div class="empty"><p>No tech stack data available.</p></div>';
  const cats = {crm:'CRM',marketing:'Marketing',analytics:'Analytics',infrastructure:'Infrastructure',security:'Security',other:'Other'};
  const grouped = {};
  tech.forEach(t => { const cat=techCategory(t); (grouped[cat]=grouped[cat]||[]).push(t); });
  return Object.entries(cats).filter(([k])=>grouped[k]?.length).map(([k,label]) =>
    `<div class="tech-section">
      <h4>${label}</h4>
      <div class="pill-group">${grouped[k].map(t=>`<span class="pill pill-${k}">${esc(t)}</span>`).join('')}</div>
    </div>`).join('');
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  document.body.classList.remove('modal-open');
  overlayStack = overlayStack.filter(x => x !== 'modal');
}

function maybeCloseModal(e) {
  if (e.target === document.getElementById('modal-overlay')) closeModal();
}

// ── KPI Modals ────────────────────────────────────────────────────────────────
let _kpiRows = [];

function openKpiModal(type) {
  const overlay = document.getElementById('kpi-overlay');
  const titleEl = document.getElementById('kpi-modal-title');
  const searchEl = document.getElementById('kpi-modal-search');
  searchEl.value = '';

  if (type === 'companies') {
    titleEl.textContent = 'All Companies (' + DATA.companies.length + ')';
    _kpiRows = DATA.companies.map(c => ({
      id: c.apollo_id,
      name: c.name,
      meta: [c.industry, c.state, c.employees ? c.employees + ' emp' : ''].filter(Boolean).join(' · '),
      badge: c.max_severity !== 'NONE' ? `<span class="sev-badge sev-${esc(c.max_severity)}" style="font-size:10px">${esc(c.max_severity)}</span>` : '',
      action: () => { closeKpiModal(); openModal(c.apollo_id); },
    }));
  } else if (type === 'signals') {
    titleEl.textContent = 'All Signals (' + DATA.signals.length + ')';
    _kpiRows = DATA.signals.map(s => ({
      id: s.apollo_id,
      name: s.company_name || '—',
      meta: s.signal_type + (s.signal_detail ? ' · ' + s.signal_detail.substring(0,60) : ''),
      badge: `<span class="sev-badge sev-${esc(s.severity)}" style="font-size:10px">${esc(s.severity)}</span>`,
      action: () => { closeKpiModal(); openSigDetail(s); },
    }));
  } else if (type === 'high') {
    const rows = DATA.signals.filter(s => s.severity === 'HIGH');
    titleEl.textContent = 'HIGH Alerts (' + rows.length + ')';
    _kpiRows = rows.map(s => ({
      id: s.apollo_id,
      name: s.company_name || '—',
      meta: s.signal_type + (s.signal_detail ? ' · ' + s.signal_detail.substring(0,60) : ''),
      badge: `<span class="sev-badge sev-HIGH" style="font-size:10px">HIGH</span>`,
      action: () => { closeKpiModal(); openSigDetail(s); },
    }));
  } else if (type === 'csuite') {
    const rows = DATA.signals.filter(s =>
      s.signal_type === 'C-Suite Join' || s.signal_type === 'C-Suite Exit'
    );
    titleEl.textContent = 'C-Suite Changes (' + rows.length + ')';
    _kpiRows = rows.length ? rows.map(s => ({
      id: s.apollo_id,
      name: s.company_name || '—',
      meta: s.signal_type + (s.signal_detail ? ' · ' + s.signal_detail.substring(0, 70) : '') + (s.sent_at ? ' · ' + relTime(s.sent_at) : ''),
      badge: `<span class="sev-badge sev-HIGH" style="font-size:10px">${s.signal_type === 'C-Suite Join' ? '➕ Join' : '➖ Exit'}</span>`,
      action: () => { closeKpiModal(); openSigDetail(s); },
    })) : [{ _empty: true, _msg: 'No C-Suite signals detected yet. Add rows to your C-Suite sheet to start tracking.' }];
  } else if (type === 'kfunding') {
    const rows = DATA.signals.filter(s => s.signal_type === 'Funding Round');
    titleEl.textContent = 'Funding Rounds (' + rows.length + ')';
    _kpiRows = rows.length ? rows.map(s => ({
      id: s.apollo_id, name: s.company_name || '—',
      meta: (s.signal_detail || '').substring(0, 80) + (s.sent_at ? ' · ' + relTime(s.sent_at) : ''),
      badge: `<span class="sev-badge sev-HIGH" style="font-size:10px">💰 Funding</span>`,
      action: () => { closeKpiModal(); openSigDetail(s); },
    })) : [{ _empty: true, _msg: 'No funding signals detected yet. Add rows to your Funding sheet to start tracking.' }];
  } else if (type === 'kma') {
    const rows = DATA.signals.filter(s => s.signal_type === 'Acquisition / M&A');
    titleEl.textContent = 'Mergers & Acquisitions (' + rows.length + ')';
    _kpiRows = rows.length ? rows.map(s => ({
      id: s.apollo_id, name: s.company_name || '—',
      meta: (s.signal_detail || '').substring(0, 80) + (s.sent_at ? ' · ' + relTime(s.sent_at) : ''),
      badge: `<span class="sev-badge sev-HIGH" style="font-size:10px">🤝 M&A</span>`,
      action: () => { closeKpiModal(); openSigDetail(s); },
    })) : [{ _empty: true, _msg: 'No M&A signals detected yet. Add rows to your M&A sheet to start tracking.' }];
  } else if (type === 'kipo') {
    const rows = DATA.signals.filter(s => s.signal_type === 'IPO Signal');
    titleEl.textContent = 'IPO Signals (' + rows.length + ')';
    _kpiRows = rows.length ? rows.map(s => ({
      id: s.apollo_id, name: s.company_name || '—',
      meta: (s.signal_detail || '').substring(0, 80) + (s.sent_at ? ' · ' + relTime(s.sent_at) : ''),
      badge: `<span class="sev-badge sev-HIGH" style="font-size:10px">📈 IPO</span>`,
      action: () => { closeKpiModal(); openSigDetail(s); },
    })) : [{ _empty: true, _msg: 'No IPO signals detected yet. Add rows to your IPO sheet to start tracking.' }];
  } else if (type === 'knews') {
    const rows = DATA.signals.filter(s => s.signal_type === 'News Mention');
    titleEl.textContent = 'News Mentions (' + rows.length + ')';
    _kpiRows = rows.length ? rows.map(s => ({
      id: s.apollo_id, name: s.company_name || '—',
      meta: (s.signal_detail || '').substring(0, 80) + (s.sent_at ? ' · ' + relTime(s.sent_at) : ''),
      badge: `<span class="sev-badge sev-LOW" style="font-size:10px">📰 News</span>`,
      action: () => { closeKpiModal(); openSigDetail(s); },
    })) : [{ _empty: true, _msg: 'No news signals detected yet.' }];
  } else if (type === 'funding') {
    const ageDays = (DATA.kpis && DATA.kpis.max_signal_age_days) || 90;
    const cutoff  = Date.now() - ageDays * 86400000;
    const _isRecent = c => {
      if (!c.last_raised_at) return false;
      try { return new Date(c.last_raised_at).getTime() >= cutoff; } catch(e) { return false; }
    };
    const allFunded = DATA.companies.filter(c =>
      c.latest_funding_type || (+c.total_funding||0) > 0 || (+c.latest_funding_amount||0) > 0
    );
    const recent     = allFunded.filter(c =>  _isRecent(c));
    const historical = allFunded.filter(c => !_isRecent(c));
    titleEl.textContent = 'Funding Activity — ' + recent.length + ' recent · ' + historical.length + ' historical';
    const _mkRow = (c, isRecent) => ({
      id: c.apollo_id, name: c.name,
      meta: [c.latest_funding_type, c.latest_funding_amount_fmt, c.last_raised_at ? c.last_raised_at.substring(0,10) : ''].filter(Boolean).join(' · '),
      badge: isRecent ? '<span style="display:inline-flex;align-items:center;font-size:10px;color:#10b981;background:rgba(16,185,129,.12);border-radius:4px;padding:2px 6px;flex-shrink:0;margin-right:4px">Recent</span>' : '',
      _dim: !isRecent,
      action: () => { closeKpiModal(); openModal(c.apollo_id); },
    });
    _kpiRows = [
      { _header: true, _label: `Recent Funding (Last ${ageDays} Days)`, _cls: 'recent' },
      ...(recent.length ? recent.map(c => _mkRow(c, true)) : [{ _empty: true, _msg: 'No companies with funding events in this period' }]),
      { _header: true, _label: 'All Companies With Funding History', _note: 'Historical data from CSV', _cls: 'historical' },
      ...(historical.length ? historical.map(c => _mkRow(c, false)) : [{ _empty: true, _msg: 'None' }]),
    ];
  }

  renderKpiModalRows('');
  overlay.classList.add('open');
  document.body.classList.add('modal-open');
  searchEl.focus();
}

function renderKpiModalRows(q) {
  const body = document.getElementById('kpi-modal-body');
  const ql = q.toLowerCase();

  // Section-aware rendering (used by funding modal)
  if (_kpiRows.some(r => r._header)) {
    // Split flat list into sections: [{header, items:[]}]
    const sections = [];
    let cur = null;
    for (const r of _kpiRows) {
      if (r._header) { cur = { header: r, items: [] }; sections.push(cur); }
      else if (cur) cur.items.push(r);
    }
    // Filter company rows within each section; always keep _empty placeholders
    const filteredSections = sections.map(s => ({
      header: s.header,
      items: ql
        ? s.items.filter(r => r._empty || (r.name && (r.name.toLowerCase().includes(ql) || (r.meta||'').toLowerCase().includes(ql))))
        : s.items,
    }));
    // Build click index only over real (non-_empty) rows
    window._kpiFilteredRows = filteredSections.flatMap(s => s.items.filter(r => !r._empty && r.action));
    let clickIdx = 0;
    let html = '';
    for (const s of filteredSections) {
      if (ql && s.items.filter(r => !r._empty).length === 0) continue; // hide empty sections while searching
      const noteHtml = s.header._note ? `<span style="font-size:10px;font-weight:400;text-transform:none;opacity:.7;letter-spacing:0"> — ${esc(s.header._note)}</span>` : '';
      html += `<div class="kpi-modal-section-hdr ${s.header._cls||''}">${esc(s.header._label)}${noteHtml}</div>`;
      for (const r of s.items) {
        if (r._empty) {
          html += `<div style="padding:10px 20px;font-size:12px;color:var(--text2)">${esc(r._msg)}</div>`;
        } else {
          const dimCls = r._dim ? ' dim' : '';
          const idx = clickIdx++;
          html += `<div class="kpi-modal-row${dimCls}" onclick="_kpiRowClick(${idx})">
            <div class="kpi-row-name">${esc(r.name)}</div>
            ${r.badge}
            <div class="kpi-row-meta">${esc(r.meta)}</div>
          </div>`;
        }
      }
    }
    body.innerHTML = html || '<div class="kpi-modal-empty">No results</div>';
    return;
  }

  // Standard (flat) rendering
  // Handle empty-state sentinel rows
  if (_kpiRows.length === 1 && _kpiRows[0]._empty) {
    body.innerHTML = `<div class="kpi-modal-empty" style="padding:40px 24px;font-size:13px;color:var(--text2);line-height:1.6">${esc(_kpiRows[0]._msg)}</div>`;
    window._kpiFilteredRows = [];
    return;
  }
  const filtered = ql
    ? _kpiRows.filter(r => !r._empty && (r.name.toLowerCase().includes(ql) || (r.meta||'').toLowerCase().includes(ql)))
    : _kpiRows.filter(r => !r._empty);
  if (filtered.length === 0) {
    body.innerHTML = '<div class="kpi-modal-empty">No results</div>';
    return;
  }
  body.innerHTML = filtered.map((r,i) => `
    <div class="kpi-modal-row" onclick="_kpiRowClick(${i})">
      <div class="kpi-row-name">${esc(r.name)}</div>
      ${r.badge}
      <div class="kpi-row-meta">${esc(r.meta)}</div>
    </div>`).join('');
  window._kpiFilteredRows = filtered;
}



function openSigDetailFromExpand(apolloId, idx) {
  const c = DATA.companies.find(x => x.apollo_id === apolloId);
  if (!c || !c.alerts || !c.alerts[idx]) return;
  const a = c.alerts[idx];
  openSigDetail({
    apollo_id:    apolloId,
    company_name: c.name,
    domain:       c.domain,
    industry:     c.industry,
    logo_url:     c.logo_url,
    signal_type:  a.signal_type,
    signal_detail:a.signal_detail,
    severity:     a.severity,
    sent_at:      a.sent_at,
    source_url:   a.source_url || '',
    previous_value: a.previous_value || '',
    new_value:    a.new_value || '',
  });
}

function openSigDetail(s) {
  const compMap = {};
  DATA.companies.forEach(co => { compMap[co.apollo_id] = co; });
  const comp = compMap[s.apollo_id] || {};
  const sev  = (s.severity || 'LOW').toUpperCase();
  const logoUrl = s.logo_url || comp.logo_url || '';
  const cname   = s.company_name || comp.name || '—';
  const avatarHtml = logoUrl
    ? `<div class="sdm-avatar"><img src="${esc(logoUrl)}" onerror="this.parentElement.innerHTML='<span>${esc(initials(cname))}</span>'" /></div>`
    : `<div class="sdm-avatar"><span>${esc(initials(cname))}</span></div>`;
  const rawSrc = (s.source_url || '').trim();
  const srcObj = _signalSourceUrl(rawSrc, s.signal_type, cname, s.domain||comp.domain||'');
  const srcBtn = `<a class="modal-btn" href="${esc(srcObj.href)}" target="_blank" rel="noopener" style="font-size:12px">&#128279; ${esc(srcObj.label)}</a>`;
  const prevNewHtml = (s.previous_value || s.new_value)
    ? `<div class="sdm-prev-new"><strong style="color:var(--text2)">Change:</strong> <span style="color:var(--text)">${esc(s.previous_value||'—')}</span> <span style="color:var(--text2)">→</span> <span style="color:var(--text)">${esc(s.new_value||'—')}</span></div>` : '';
  const industry = s.industry || comp.industry || '';
  const domain   = s.domain   || comp.domain   || '';
  const profileBtn = s.apollo_id
    ? `<button class="modal-btn" onclick="closeSigDetail();setTimeout(()=>openModal('${esc(s.apollo_id)}'),50)" style="font-size:12px">&#127970; View Company Profile</button>` : '';
  document.getElementById('sig-detail-content').innerHTML = `
  <div class="sdm-header">
    ${avatarHtml}
    <div style="flex:1;min-width:0">
      <div class="sdm-company">${esc(cname)}</div>
      <div class="sdm-badges">
        <span class="sev-badge sev-${esc(sev)}">${esc(sev)}</span>
        <span style="background:rgba(59,130,246,.12);color:var(--blue);border-radius:5px;padding:2px 8px;font-size:11px;font-weight:600">${esc(s.signal_type||'Signal')}</span>
        ${industry ? `<span style="color:var(--text2);font-size:11px">${esc(industry)}</span>` : ''}
      </div>
    </div>
  </div>
  <div class="sdm-body">
    <div>
      <div class="sdm-section-label">Signal Detail</div>
      <div class="sdm-detail-text">${esc(s.signal_detail || s.signal_type || '—')}</div>
    </div>
    ${prevNewHtml}
    <div class="sdm-meta-row">
      <div class="sdm-meta-item">
        <div class="sdm-section-label">Detected</div>
        <div class="sdm-meta-val">${formatFullDate(s.sent_at)}</div>
      </div>
      ${domain ? `<div class="sdm-meta-item"><div class="sdm-section-label">Domain</div><div class="sdm-meta-val"><a href="${safeUrl(domain)}" target="_blank" rel="noopener" style="color:var(--blue)">${esc(domain)}</a></div></div>` : ''}
    </div>
  </div>
  <div class="sdm-footer">${srcBtn}${profileBtn}</div>`;
  document.getElementById('sig-detail-overlay').classList.add('open');
  document.body.classList.add('modal-open');
}
function closeSigDetail() {
  document.getElementById('sig-detail-overlay').classList.remove('open');
  document.body.classList.remove('modal-open');
}
function maybeSigDetailClose(e) {
  if (e.target === document.getElementById('sig-detail-overlay')) closeSigDetail();
}

function _kpiRowClick(i) { if (window._kpiFilteredRows && window._kpiFilteredRows[i]) window._kpiFilteredRows[i].action(); }

function filterKpiModal(val) { renderKpiModalRows(val); }

function closeKpiModal() {
  document.getElementById('kpi-overlay').classList.remove('open');
  document.body.classList.remove('modal-open');
}

function maybeCloseKpiModal(e) {
  if (e.target === document.getElementById('kpi-overlay')) closeKpiModal();
}
</script>
</body>
</html>"""