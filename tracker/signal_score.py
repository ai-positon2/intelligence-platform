"""Shared signal-importance scoring — single source of truth for the dashboard
Insights tab and the weekly digest. Points reflect Position2 sales value."""
from __future__ import annotations
import datetime as _dt

SIGNAL_WEIGHTS = {
    "Funding Round": 10, "C-Suite Join": 10, "Acquisition / M&A": 9, "IPO Signal": 9,
    "Partnership": 7, "Product Launch": 7, "Creative Hiring": 6, "Subsidiary Change": 6,
    "C-Suite Exit": 5, "News Mention": 3,
}
SEVERITY_MULT = {"HIGH": 1.5, "MEDIUM": 1.0, "LOW": 0.6}
MULTI_INTENT_BONUS = 3.0

def signal_importance(signal_type, severity, signal_date):
    w = SIGNAL_WEIGHTS.get(signal_type, 4)
    sev = SEVERITY_MULT.get((severity or "").upper(), 1.0)
    try:
        age = (_dt.date.today() - _dt.date.fromisoformat(str(signal_date)[:10])).days
    except Exception:
        age = 9999
    rec = 2.0 if age <= 7 else 1.5 if age <= 30 else 1.0 if age <= 90 else 0.4
    return round(w * sev * rec, 1)

def score_company_signals(signals):
    """signals: list of dicts with signal_type/severity/signal_date/name.
    Returns same list with _score, applying a multi-intent bonus per company."""
    types_by_co = {}
    for s in signals:
        types_by_co.setdefault(s.get("name"), set()).add(s.get("signal_type"))
    for s in signals:
        base = signal_importance(s.get("signal_type"), s.get("severity"), s.get("signal_date"))
        if len(types_by_co.get(s.get("name"), ())) >= 2:
            base += MULTI_INTENT_BONUS
        s["_score"] = round(base, 1)
    return signals
