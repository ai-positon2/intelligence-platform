"""All 22 signal checks comparing old vs new snapshots. Pure functions — no I/O."""

from __future__ import annotations

import email.utils
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass
class ChangeEvent:
    apollo_id: str
    company_name: str
    company_domain: str
    signal_type: str
    severity: str       # HIGH | MEDIUM | LOW
    headline: str
    detail: str
    previous_value: str = ""
    new_value: str = ""
    source_url: str = ""   # Direct link to source (LinkedIn, news article, sheet source URL)
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ev(
    apollo_id: str,
    company_name: str,
    company_domain: str,
    signal_type: str,
    severity: str,
    headline: str,
    detail: str = "",
    prev: str = "",
    new: str = "",
    source_url: str = "",
) -> ChangeEvent:
    return ChangeEvent(
        apollo_id=apollo_id,
        company_name=company_name,
        company_domain=company_domain,
        signal_type=signal_type,
        severity=severity,
        headline=headline,
        detail=detail or headline,
        previous_value=prev,
        new_value=new,
        source_url=source_url,
    )


def _str(val) -> str:
    return str(val or "").strip()


def _low(val) -> str:
    return _str(val).lower()


def _int(val, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _str_changed(old: dict, new: dict, key: str) -> bool:
    ov = _low(old.get(key))
    nv = _low(new.get(key))
    return bool(ov) and bool(nv) and ov != nv


def _parse_leadership(value) -> list[dict]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, list) else []
        except Exception:
            return []
    return []


def _parse_tech(value) -> set[str]:
    if isinstance(value, list):
        return {t.strip().lower() for t in value if t.strip()}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return {t.strip().lower() for t in parsed if t.strip()}
        except Exception:
            pass
        return {t.strip().lower() for t in value.split(",") if t.strip()}
    return set()


def _is_c_suite(title: str, c_suite_titles: list[str]) -> bool:
    tl = (title or "").lower()
    return any(kw.lower() in tl for kw in c_suite_titles)


def is_within_age_limit(date_str: str, max_age_days: int = 90) -> bool:
    """True if date_str is within max_age_days of today, OR if date_str is absent/unparseable.
    False only when a date is present and clearly older than max_age_days.
    Handles ISO 8601 (Apollo), RFC 2822 (RSS), and plain YYYY-MM-DD formats.
    """
    if not date_str:
        return True
    dt: datetime | None = None
    # ISO 8601 — fromisoformat handles YYYY-MM-DD, YYYY-MM-DDTHH:MM:SSZ, etc.
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # RFC 2822 (RSS feeds: "Mon, 12 May 2026 10:00:00 GMT")
    if dt is None:
        try:
            dt = email.utils.parsedate_to_datetime(date_str).astimezone(timezone.utc)
        except Exception:
            pass
    if dt is None:
        return True  # unparseable — allow rather than suppress
    return dt >= datetime.now(timezone.utc) - timedelta(days=max_age_days)


# ── Main detector ──────────────────────────────────────────────────────────────

def detect_changes(
    old_snapshot: dict | None,
    new_snapshot: dict,
    config: dict,
    company_name: str = "",
    company_domain: str = "",
) -> list[ChangeEvent]:
    """Run all 22 signal checks. Returns detected ChangeEvents."""
    if old_snapshot is None:
        return []

    apollo_id = new_snapshot.get("apollo_id") or old_snapshot.get("apollo_id", "")
    sig_cfg = config.get("signals", {})
    c_titles: list[str] = sig_cfg.get("c_suite_titles", [])
    max_age_days: int = int(sig_cfg.get("max_signal_age_days", 90))
    events: list[ChangeEvent] = []

    def e(stype, sev, headline, detail="", prev="", new="", source_url=""):
        return _ev(apollo_id, company_name, company_domain, stype, sev, headline, detail, prev, new, source_url)

    # ── TIER 1 — HIGH ─────────────────────────────────────────────────────────

    # 1. C-Suite Join
    old_leaders = {p["id"]: p for p in _parse_leadership(old_snapshot.get("leadership_json") or old_snapshot.get("leadership", [])) if p.get("id")}
    new_leaders = {p["id"]: p for p in _parse_leadership(new_snapshot.get("leadership") or []) if p.get("id")}

    _news_leaders_cache = None  # lazily populated if an unnamed C-suite person is found
    for pid, person in new_leaders.items():
        if pid not in old_leaders and _is_c_suite(person.get("title", ""), c_titles):
            if not is_within_age_limit(person.get("start_date", ""), max_age_days):
                logger.debug("Skipping stale C-Suite Join for %s (start_date=%s)", person.get("full_name") or person.get("name"), person.get("start_date"))
                continue
            name_ = (person.get("full_name") or person.get("name") or "").strip()
            title_ = person.get("title", "")
            source_tag = ""
            if not name_:
                if _news_leaders_cache is None:
                    try:
                        from tracker import news_client as _nc
                        _news_leaders_cache = _nc.get_leadership_from_news(company_name)
                    except Exception:
                        _news_leaders_cache = []
                for nl in _news_leaders_cache:
                    nl_title = (nl.get("title") or "").lower()
                    if nl_title and (nl_title in title_.lower() or title_.lower() in nl_title):
                        name_ = nl["name"]
                        source_tag = " (via Google News)"
                        break
            if not name_:
                name_ = "Unknown"
            events.append(e(
                "C-Suite Join", "HIGH",
                f"{name_} joined as {title_}{source_tag}",
                f"{name_} ({title_}) appears as a new executive at {company_name}. LinkedIn: {person.get('linkedin_url', 'N/A')}",
                "Not listed", f"{name_} — {title_}",
            ))

    # Deduplicate: collapse >3 C-Suite Joins into a single summary event
    _joins = [ev for ev in events if ev.signal_type == "C-Suite Join"]
    if len(_joins) > 3:
        events = [ev for ev in events if ev.signal_type != "C-Suite Join"]
        _exec_names = [ev.new_value.split(" — ")[0] for ev in _joins if ev.new_value]
        events.append(e(
            "C-Suite Join", "HIGH",
            f"Multiple leadership changes detected at {company_name} — {len(_joins)} new C-suite members",
            f"New executives: {', '.join(_exec_names) if _exec_names else f'{len(_joins)} executives'}",
        ))

    # 2. C-Suite Exit
    for pid, person in old_leaders.items():
        if pid not in new_leaders and _is_c_suite(person.get("title", ""), c_titles):
            name_ = (person.get("full_name") or person.get("name") or "Unknown")
            title_ = person.get("title", "")
            events.append(e(
                "C-Suite Exit", "HIGH",
                f"{name_} ({title_}) no longer listed",
                f"{name_}, previously {title_} at {company_name}, is no longer in the leadership roster.",
                f"{name_} — {title_}", "Not listed",
            ))

    # 3. Funding Round
    fund_keys = ("latest_funding_type", "latest_funding_amount", "last_raised_at")
    if any(_str_changed(old_snapshot, new_snapshot, k) for k in fund_keys):
        raised_at = _str(new_snapshot.get("last_raised_at"))
        if is_within_age_limit(raised_at, max_age_days):
            old_type = _str(old_snapshot.get("latest_funding_type"))
            new_type = _str(new_snapshot.get("latest_funding_type"))
            old_amt = old_snapshot.get("latest_funding_amount")
            new_amt = new_snapshot.get("latest_funding_amount")
            events.append(e(
                "Funding Round", "HIGH",
                f"Funding changed: {old_type or '(none)'} → {new_type or '(none)'}",
                f"Stage: {old_type} → {new_type}. Amount: {old_amt} → {new_amt}. Date: {raised_at}",
                f"{old_type} / {old_amt}", f"{new_type} / {new_amt}",
            ))

    # 4 & 5. Acquisition / IPO — from injected news_articles
    for article in new_snapshot.get("news_articles", []):
        if not is_within_age_limit(article.get("published", ""), max_age_days):
            continue
        art_url = article.get("url", "")
        text = (_str(article.get("title")) + " " + _str(article.get("summary"))).lower()
        acq_kws = ["acquired", "acquisition", "merger", "acquires"]
        if any(kw in text for kw in acq_kws) and not any(ev.signal_type == "Acquisition / M&A" for ev in events):
            events.append(e(
                "Acquisition / M&A", "HIGH",
                f"M&A news: {article.get('title', '')[:80]}",
                article.get("title", ""),
                source_url=art_url,
            ))
        ipo_kws = ["ipo", "going public", "s-1"]
        if any(kw in text for kw in ipo_kws) and not any(ev.signal_type == "IPO Signal" for ev in events):
            events.append(e(
                "IPO Signal", "HIGH",
                f"IPO news: {article.get('title', '')[:80]}",
                article.get("title", ""),
                source_url=art_url,
            ))

    # 6. Subsidiary Change
    old_sub = _low(old_snapshot.get("subsidiary_of"))
    new_sub = _low(new_snapshot.get("subsidiary_of"))
    if old_sub != new_sub and (old_sub or new_sub):
        events.append(e(
            "Subsidiary Change", "HIGH",
            f"Subsidiary changed: {_str(old_snapshot.get('subsidiary_of')) or 'none'} → {_str(new_snapshot.get('subsidiary_of')) or 'none'}",
            prev=old_sub, new=new_sub,
        ))

    # ── TIER 2 — MEDIUM ───────────────────────────────────────────────────────

    # 7. Headcount Growth
    old_emp = _int(old_snapshot.get("employees") or old_snapshot.get("num_employees"))
    new_emp = _int(new_snapshot.get("employees") or new_snapshot.get("num_employees"))
    growth_pct = sig_cfg.get("headcount_growth_pct", 15) / 100

    if old_emp > 0 and new_emp > old_emp * (1 + growth_pct):
        pct = round((new_emp - old_emp) / old_emp * 100, 1)
        events.append(e(
            "Headcount Growth", "MEDIUM",
            f"Headcount grew {pct}% ({old_emp:,} → {new_emp:,})",
            prev=str(old_emp), new=str(new_emp),
        ))

    # 8. Headcount Shrink
    shrink_pct = sig_cfg.get("headcount_shrink_pct", 10) / 100
    if old_emp > 0 and new_emp > 0 and new_emp < old_emp * (1 - shrink_pct):
        pct = round((old_emp - new_emp) / old_emp * 100, 1)
        events.append(e(
            "Headcount Shrink", "MEDIUM",
            f"Headcount dropped {pct}% ({old_emp:,} → {new_emp:,})",
            prev=str(old_emp), new=str(new_emp),
        ))

    # 9. Revenue Growth
    old_rev = _float(old_snapshot.get("annual_revenue"))
    new_rev = _float(new_snapshot.get("annual_revenue"))
    if old_rev and new_rev and new_rev > old_rev:
        events.append(e(
            "Revenue Growth", "MEDIUM",
            f"Revenue increased: ${old_rev:,.0f} → ${new_rev:,.0f}",
            prev=str(old_rev), new=str(new_rev),
        ))

    # 10. New Location
    old_city = _low(old_snapshot.get("hq_city") or old_snapshot.get("city"))
    new_city = _low(new_snapshot.get("city") or new_snapshot.get("hq_city"))
    old_state = _low(old_snapshot.get("hq_state") or old_snapshot.get("state"))
    new_state = _low(new_snapshot.get("state") or new_snapshot.get("hq_state"))
    if (old_city and new_city and old_city != new_city) or (old_state and new_state and old_state != new_state):
        events.append(e(
            "New Location", "MEDIUM",
            f"HQ changed: {old_city},{old_state} → {new_city},{new_state}",
            prev=f"{old_city},{old_state}", new=f"{new_city},{new_state}",
        ))

    # 11. Job Posting Spike
    old_jobs = _int(old_snapshot.get("open_job_count"))
    new_jobs = _int(new_snapshot.get("open_job_count"))
    spike_pct = sig_cfg.get("job_posting_spike_pct", 30) / 100
    if old_jobs > 0 and new_jobs > old_jobs * (1 + spike_pct):
        pct = round((new_jobs - old_jobs) / old_jobs * 100, 1)
        events.append(e(
            "Job Posting Spike", "MEDIUM",
            f"Open roles spiked {pct}% ({old_jobs} → {new_jobs})",
            prev=str(old_jobs), new=str(new_jobs),
        ))

    # 12. New Funding Stage
    if _str_changed(old_snapshot, new_snapshot, "latest_funding_type"):
        raised_at_fs = _str(new_snapshot.get("last_raised_at"))
        if is_within_age_limit(raised_at_fs, max_age_days):
            old_ft = _str(old_snapshot.get("latest_funding_type"))
            new_ft = _str(new_snapshot.get("latest_funding_type"))
            events.append(e(
                "New Funding Stage", "MEDIUM",
                f"Funding stage: {old_ft} → {new_ft}",
                prev=old_ft, new=new_ft,
            ))

    # 13 & 14. Tech Stack Addition / Removal
    old_tech = _parse_tech(old_snapshot.get("tech_stack"))
    new_tech = _parse_tech(new_snapshot.get("tech_stack"))
    added = new_tech - old_tech
    removed = old_tech - new_tech
    if added:
        top = ", ".join(sorted(added)[:5])
        events.append(e("Tech Stack Addition", "MEDIUM", f"Added tech: {top}", new=top))
    if removed:
        top = ", ".join(sorted(removed)[:5])
        events.append(e("Tech Stack Removal", "MEDIUM", f"Removed tech: {top}", prev=top))

    # 15. Intent Score Spike
    old_intent = _float(old_snapshot.get("intent_score_1"))
    new_intent = _float(new_snapshot.get("intent_score_1"))
    spike_pts = sig_cfg.get("intent_score_spike_points", 20)
    if old_intent is not None and new_intent is not None and (new_intent - old_intent) >= spike_pts:
        events.append(e(
            "Intent Score Spike", "MEDIUM",
            f"Intent score spiked: {old_intent:.0f} → {new_intent:.0f} (+{new_intent - old_intent:.0f} pts)",
            prev=str(old_intent), new=str(new_intent),
        ))

    # 16. Intent Topic Change
    if _str_changed(old_snapshot, new_snapshot, "intent_topic_1"):
        old_t = _str(old_snapshot.get("intent_topic_1"))
        new_t = _str(new_snapshot.get("intent_topic_1"))
        events.append(e("Intent Topic Change", "MEDIUM", f"Intent topic: {old_t} → {new_t}", prev=old_t, new=new_t))

    # ── TIER 3 — LOW ──────────────────────────────────────────────────────────

    # 17. Title Change
    for pid, person in new_leaders.items():
        if pid in old_leaders:
            old_title = _str(old_leaders[pid].get("title"))
            new_title = _str(person.get("title"))
            if old_title and new_title and old_title.lower() != new_title.lower():
                pname = (person.get("full_name") or person.get("name") or "")
                events.append(e(
                    "Title Change", "LOW",
                    f"{pname}: {old_title} → {new_title}",
                    prev=old_title, new=new_title,
                ))

    # 18. CRM Stage Change
    if _str_changed(old_snapshot, new_snapshot, "crm_stage"):
        old_s = _str(old_snapshot.get("crm_stage"))
        new_s = _str(new_snapshot.get("crm_stage"))
        events.append(e("CRM Stage Change", "LOW", f"Stage: {old_s} → {new_s}", prev=old_s, new=new_s))

    # 19. New Retail Location
    old_ret = _int(old_snapshot.get("retail_locations"))
    new_ret = _int(new_snapshot.get("retail_locations"))
    if new_ret > old_ret and new_ret > 0:
        events.append(e("New Retail Location", "LOW", f"Retail locations: {old_ret} → {new_ret}", prev=str(old_ret), new=str(new_ret)))

    # 20. Website Change
    if _str_changed(old_snapshot, new_snapshot, "domain"):
        events.append(e(
            "Website Change", "LOW",
            f"Domain changed: {old_snapshot.get('domain')} → {new_snapshot.get('domain')}",
            prev=_str(old_snapshot.get("domain")), new=_str(new_snapshot.get("domain")),
        ))

    # 21. Description Update
    old_desc = ""
    try:
        raw = old_snapshot.get("raw_json", "{}")
        if isinstance(raw, str):
            old_desc = json.loads(raw).get("description", "")
        elif isinstance(raw, dict):
            old_desc = raw.get("description", "")
    except Exception:
        pass
    new_desc = _str(new_snapshot.get("description"))
    if old_desc and new_desc and old_desc.strip() != new_desc.strip() and len(new_desc) > 20:
        events.append(e("Description Update", "LOW", "Short description was updated"))

    # 22. News Mention (MEDIUM keywords in news → LOW signal)
    medium_kws = sig_cfg.get("news_medium_keywords", [])
    already_high_news = any(ev.signal_type in ("Acquisition / M&A", "IPO Signal") for ev in events)
    fresh_articles = [
        a for a in new_snapshot.get("news_articles", [])
        if is_within_age_limit(a.get("published", ""), max_age_days)
    ]
    for article in fresh_articles:
        text = (_str(article.get("title")) + " " + _str(article.get("summary"))).lower()
        if any(kw.lower() in text for kw in medium_kws) and not any(ev.signal_type == "News Mention" for ev in events):
            events.append(e(
                "News Mention", "LOW",
                f"In the news: {article.get('title', '')[:80]}",
                source_url=article.get("url", ""),
            ))
            break

    if not already_high_news and fresh_articles and not any(ev.signal_type == "News Mention" for ev in events):
        art = fresh_articles[0]
        events.append(e(
            "News Mention", "LOW",
            f"In the news: {art.get('title', '')[:80]}",
            source_url=art.get("url", ""),
        ))

    return events


# ── News-only signal detection (no Apollo snapshot required) ───────────────────

def detect_news_signals(
    old_snapshot: dict | None,
    new_snapshot: dict,
    config: dict,
    company_name: str = "",
    company_domain: str = "",
) -> list[ChangeEvent]:
    """Detect signals from news_articles only — no Apollo enrichment needed.

    Checks for:
      - Acquisition / M&A  (HIGH) — keyword match in article title/summary
      - IPO Signal          (HIGH) — keyword match in article title/summary
      - News Mention        (LOW)  — any fresh article (medium keyword boost)

    Called by main.py when HIGH signals come from Google Sheets and only
    the news-based LOW path needs to run.
    """
    if old_snapshot is None:
        # No prior snapshot means first run for this company — skip news alerts
        # so we don't flood Slack on the very first pass.
        return []

    apollo_id = (
        new_snapshot.get("apollo_id")
        or (old_snapshot.get("apollo_id") if old_snapshot else "")
        or ""
    )
    sig_cfg = config.get("signals", {})
    max_age_days: int = int(sig_cfg.get("max_signal_age_days", 90))
    medium_kws: list[str] = sig_cfg.get("news_medium_keywords", [])
    events: list[ChangeEvent] = []

    def e(stype, sev, headline, detail="", prev="", new="", source_url=""):
        return _ev(apollo_id, company_name, company_domain, stype, sev, headline, detail, prev, new, source_url)

    fresh_articles = [
        a for a in new_snapshot.get("news_articles", [])
        if is_within_age_limit(a.get("published", ""), max_age_days)
    ]

    # Acquisition / M&A from news
    for article in fresh_articles:
        art_url = article.get("url", "")
        text = (_str(article.get("title")) + " " + _str(article.get("summary"))).lower()
        acq_kws = ["acquired", "acquisition", "merger", "acquires"]
        if any(kw in text for kw in acq_kws) and not any(
            ev.signal_type == "Acquisition / M&A" for ev in events
        ):
            events.append(e(
                "Acquisition / M&A", "HIGH",
                f"M&A news: {article.get('title', '')[:80]}",
                article.get("title", ""),
                source_url=art_url,
            ))
        # IPO from news
        ipo_kws = ["ipo", "going public", "s-1"]
        if any(kw in text for kw in ipo_kws) and not any(
            ev.signal_type == "IPO Signal" for ev in events
        ):
            events.append(e(
                "IPO Signal", "HIGH",
                f"IPO news: {article.get('title', '')[:80]}",
                article.get("title", ""),
                source_url=art_url,
            ))

    # News Mention (LOW) — medium keyword match first, then any fresh article
    for article in fresh_articles:
        text = (_str(article.get("title")) + " " + _str(article.get("summary"))).lower()
        if any(kw.lower() in text for kw in medium_kws) and not any(
            ev.signal_type == "News Mention" for ev in events
        ):
            events.append(e(
                "News Mention", "LOW",
                f"In the news: {article.get('title', '')[:80]}",
            ))
            break

    if fresh_articles and not any(ev.signal_type == "News Mention" for ev in events):
        art = fresh_articles[0]
        events.append(e(
            "News Mention", "LOW",
            f"In the news: {art.get('title', '')[:80]}",
            source_url=art.get("url", ""),
        ))

    return events
