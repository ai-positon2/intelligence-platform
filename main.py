"""Main orchestrator for the weekly company signal tracker.

HIGH signals (Funding Round, C-Suite Join/Exit, Acquisition/M&A, IPO, Subsidiary Change)
are sourced from user-maintained Google Sheets — one sheet per signal type.

LOW signals (News Mention, and M&A/IPO when not in a Sheet) continue to come from
the Google News RSS feed (no API key required).

Apollo is no longer called during enrichment. The apollo-accounts-export.csv is
still used as the company master list and for baseline metadata only.
"""

from __future__ import annotations

import os
import re
import logging
import time
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from tracker import change_detector, news_client, notifier_slack, sheets_client, snapshot_store
from tracker.change_detector import ChangeEvent
from tracker.csv_loader import load_companies
from tracker.snapshot_store import SnapshotStore

app = typer.Typer(add_completion=False)
console = Console()

_DEFAULT_CONFIG = Path(__file__).parent / "config.yaml"
_DEFAULT_DB = Path(__file__).parent / "data" / "tracker.db"
_REPORTS_DIR = Path(__file__).parent / "reports"


# ── Config ─────────────────────────────────────────────────────────────────────

def _load_config(path: Path = _DEFAULT_CONFIG) -> dict:
    if not path.exists():
        console.print(f"[red]config.yaml not found at {path}[/red]")
        raise typer.Exit(code=1)
    with path.open() as f:
        return yaml.safe_load(f)


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


# ── Baseline (first-run only) ──────────────────────────────────────────────────

def _save_csv_baseline(company: dict, store: SnapshotStore) -> None:
    """Persist one CSV row as the baseline snapshot. Zero API calls."""
    store.upsert_company({
        "apollo_id": company["apollo_id"],
        "name": company["name"],
        "domain": company.get("domain", ""),
        "industry": company.get("industry", ""),
        "city": company.get("city", ""),
        "state": company.get("state", ""),
    })
    store.save_snapshot(company["apollo_id"], company)




# ── Sheets-based HIGH signal detection ────────────────────────────────────────

def _detect_sheet_events(
    company: dict,
    company_signals: dict[str, list[dict]],
    config: dict,
) -> list[ChangeEvent]:
    """Convert Google Sheets rows for this company into ChangeEvents.

    One ChangeEvent is created per matching sheet row (subject to the 90-day
    age filter). The dedup window in SQLite prevents duplicate Slack alerts.
    """
    from tracker.change_detector import is_within_age_limit

    apollo_id = company["apollo_id"]
    name = company.get("name", "")
    domain = company.get("domain", "")
    max_age_days = int(config.get("signals", {}).get("max_signal_age_days", 90))
    events: list[ChangeEvent] = []

    def ev(signal_type, severity, headline, detail="", prev="", new_val="", source_url="", signal_date=""):
        return ChangeEvent(
            apollo_id=apollo_id,
            company_name=name,
            company_domain=domain,
            signal_type=signal_type,
            severity=severity,
            headline=headline,
            detail=detail or headline,
            previous_value=prev,
            new_value=new_val,
            source_url=source_url,
            signal_date=signal_date,
        )

    def _get(row: dict, *keys: str) -> str:
        """Try multiple column name variants, return first non-empty value."""
        for k in keys:
            v = row.get(k, "").strip()
            if v:
                return v
        return ""

    def _src(row: dict) -> str:
        """Return encoded source as 'Name||URL', 'URL', 'Name', or '' for dashboard.
        The dashboard JS decodes '||' to use name as label and URL as href."""
        url  = _get(row, "Source URL")
        name_ = _get(row, "Source Name")
        if url and name_:
            return f"{name_}||{url}"
        return url or name_

    # ── Signal Type routing map ────────────────────────────────────────────────
    # Maps the raw "Signal Type" column value (normalised) → (signal_type, severity)
    # Applies to ALL sheet types so any sheet can carry any HIGH signal.
    _SIGNAL_TYPE_MAP: dict[str, tuple[str, str]] = {
        # Funding
        "funding":          ("Funding Round",      "HIGH"),
        "funding_round":    ("Funding Round",      "HIGH"),
        "series_a":         ("Funding Round",      "HIGH"),
        "series_b":         ("Funding Round",      "HIGH"),
        "series_c":         ("Funding Round",      "HIGH"),
        "series_d":         ("Funding Round",      "HIGH"),
        "seed":             ("Funding Round",      "HIGH"),
        "grant":            ("Funding Round",      "HIGH"),
        "debt_financing":   ("Funding Round",      "HIGH"),
        "debt":             ("Funding Round",      "HIGH"),
        "credit_facility":  ("Funding Round",      "HIGH"),
        # IPO / Public offering
        "ipo":              ("IPO Signal",          "HIGH"),
        "ipo_signal":       ("IPO Signal",          "HIGH"),
        "going_public":     ("IPO Signal",          "HIGH"),
        "spac":             ("IPO Signal",          "HIGH"),
        # PUBLIC_OFFERING = shelf/ATM/buyback for already-public companies → Funding Round
        "public_offering":  ("Funding Round",       "HIGH"),
        # M&A
        "m_and_a":          ("Acquisition / M&A",  "HIGH"),
        "ma":               ("Acquisition / M&A",  "HIGH"),
        "acquisition":      ("Acquisition / M&A",  "HIGH"),
        "merger":           ("Acquisition / M&A",  "HIGH"),
        "take_private":     ("Acquisition / M&A",  "HIGH"),
        # C-Suite
        "c_suite_join":     ("C-Suite Join",        "HIGH"),
        "csuite_join":      ("C-Suite Join",        "HIGH"),
        "executive_hire":   ("C-Suite Join",        "HIGH"),
        "c_suite_exit":     ("C-Suite Exit",        "HIGH"),
        "csuite_exit":      ("C-Suite Exit",        "HIGH"),
        "executive_depart": ("C-Suite Exit",        "HIGH"),
        # Subsidiary
        "subsidiary":       ("Subsidiary Change",   "HIGH"),
        "subsidiary_change":("Subsidiary Change",   "HIGH"),
    }

    def _route_signal_type(raw: str, default_signal: str, default_sev: str) -> tuple[str, str]:
        """Normalise the Signal Type column value and look it up in the routing map."""
        key = raw.strip().lower().replace(" ", "_").replace("/", "_").replace("-", "_")
        return _SIGNAL_TYPE_MAP.get(key, (default_signal, default_sev))

    # ── Funding Round (with Signal Type routing) ───────────────────────────────
    for row in company_signals.get("funding", []):
        # Support both original column names and actual sheet columns
        date   = _get(row, "Scan Date", "Raised At", "Announcement Date", "Date")
        if not is_within_age_limit(date, max_age_days):
            continue
        # For display use the actual event date (Announcement Date / Raised At),
        # falling back to Scan Date only when no event date is available.
        event_date = _get(row, "Announcement Date", "Raised At", "Date") or date
        raw_signal_type = _get(row, "Signal Type")
        routed_type, routed_sev = _route_signal_type(
            raw_signal_type, "Funding Round", "HIGH"
        )
        ftype   = _get(row, "Funding Stage", "Funding Type") or raw_signal_type
        amount  = _get(row, "Amount", "Funding Amount (USD)")
        src     = _src(row)
        conf    = _get(row, "Confidence")
        summary = _get(row, "Summary", "Notes")
        lead    = _get(row, "Lead Investor")

        if routed_type == "Funding Round":
            headline = f"Funding: {ftype}" + (f" — {amount}" if amount else "")
        elif routed_type == "Acquisition / M&A":
            headline = f"M&A: {summary[:80]}" if summary else f"M&A activity at {name}"
        elif routed_type == "IPO Signal":
            headline = f"IPO: {summary[:80]}" if summary else f"IPO signal for {name}"
        else:
            headline = f"{routed_type}: {summary[:80]}" if summary else f"{routed_type} at {name}"

        detail = (
            f"Company: {name}. "
            + (f"Stage/Type: {ftype}. " if ftype else "")
            + (f"Amount: {amount}. " if amount else "")
            + (f"Lead: {lead}. " if lead else "")
            + f"Date: {date}. "
            + (f"Source: {src}. " if src else "")
            + (f"Confidence: {conf}. " if conf else "")
            + (f"Summary: {summary}" if summary else "")
        ).strip()

        events.append(ev(
            routed_type, routed_sev,
            headline, detail,
            new_val=f"{ftype} / {amount}",
            source_url=src,
            signal_date=event_date,
        ))

    # ── C-Suite Join / Exit ────────────────────────────────────────────────────
    for row in company_signals.get("csuite", []):
        date    = _get(row, "Scan Date", "Start Date", "Date")
        if not is_within_age_limit(date, max_age_days):
            continue
        event_date = _get(row, "Start Date", "Date") or date
        action   = _get(row, "Action", "Signal Type").lower().strip()
        person   = _get(row, "Person Name", "Executive Name", "Name")
        title    = _get(row, "Title", "Role", "Position")
        linkedin = _get(row, "LinkedIn URL", "LinkedIn")
        notes    = _get(row, "Notes", "Summary")

        # ── Classify action (covers "Appointed", "Promoted", "Role Change",
        #    "Join", "Hire" → Join; "Departed", "Exit", "Left" → Exit)
        _EXIT_WORDS = ("depart", "exit", "left", "resign", "terminat", "leav")
        is_exit  = any(w in action for w in _EXIT_WORDS)
        signal_type = "C-Suite Exit" if is_exit else "C-Suite Join"
        verb        = "exited" if is_exit else action.capitalize() if action else "Joined"

        # ── Extract source URL + name from Notes when no dedicated column
        # Notes format: "Source: Becker's Hospital Review, https://... / Confidence: High / ..."
        # URL regex: stop only at whitespace or common terminators — NOT at apostrophes
        _url_match = re.search(r'https?://[^\s)>"\]]+', notes)
        notes_url  = _url_match.group(0).rstrip(".,;|/'\"") if _url_match else ""
        # Source name: capture everything after "Source:" up to the comma + URL
        # Use .+? so apostrophes, hyphens, dots, etc. are included (e.g. "Becker's Hospital Review")
        _name_match = re.search(r"Source:\s*(.+?)\s*,\s*https?://", notes, re.IGNORECASE)
        if not _name_match:
            # Fallback: try pipe separator ("Source: Business Wire | https://...")
            _name_match = re.search(r"Source:\s*(.+?)\s*[|]\s*https?://", notes, re.IGNORECASE)
        notes_src_name = _name_match.group(1).strip() if _name_match else ""

        # Build encoded source: prefer LinkedIn, then sheet Source URL col, then Notes URL
        sheet_src = _src(row)   # encodes Source Name||Source URL cols if present
        if not sheet_src and notes_url:
            sheet_src = f"{notes_src_name}||{notes_url}" if notes_src_name else notes_url

        # Extract confidence + summary from notes for detail text
        _conf_match = re.search(r"Confidence:\s*([\w ]+?)(?:\s*[|/,.]|$)", notes, re.IGNORECASE)
        confidence  = _conf_match.group(1) if _conf_match else ""
        _summ_match = re.search(r"Summary:\s*(.+)", notes, re.IGNORECASE)
        summary     = _summ_match.group(1).strip()[:150] if _summ_match else notes[:150]

        headline = f"{person} — {verb} as {title}" if title else f"{person} — {verb}"
        detail   = (
            f"{person} ({title}) at {name}. "
            + (f"LinkedIn: {linkedin}. " if linkedin else "")
            + f"Date: {date}. "
            + (f"Confidence: {confidence}. " if confidence else "")
            + (f"Summary: {summary}" if summary else "")
        ).strip()
        events.append(ev(signal_type, "HIGH", headline, detail, new_val=f"{person} — {title}",
                         source_url=sheet_src or linkedin, signal_date=event_date))

    # ── Acquisition / M&A ─────────────────────────────────────────────────────
    for row in company_signals.get("ma", []):
        date    = _get(row, "Scan Date", "Announcement Date", "Date")
        if not is_within_age_limit(date, max_age_days):
            continue
        event_date = _get(row, "Announcement Date", "Date") or date
        summary = _get(row, "Summary", "Description", "Notes")
        src     = _src(row)
        conf    = _get(row, "Confidence")
        headline = f"M&A: {summary[:80]}" if summary else f"M&A activity at {name}"
        detail   = (
            (f"{summary} " if summary else "")
            + (f"Source: {src}. " if src else "")
            + f"Date: {date}."
            + (f" Confidence: {conf}" if conf else "")
        ).strip()
        events.append(ev("Acquisition / M&A", "HIGH", headline, detail, source_url=src, signal_date=event_date))

    # ── IPO Signal ────────────────────────────────────────────────────────────
    for row in company_signals.get("ipo", []):
        date    = _get(row, "Scan Date", "Announcement Date", "Date")
        if not is_within_age_limit(date, max_age_days):
            continue
        event_date = _get(row, "Announcement Date", "Date") or date
        summary = _get(row, "Summary", "Description", "Notes")
        src     = _src(row)
        conf    = _get(row, "Confidence")
        headline = f"IPO: {summary[:80]}" if summary else f"IPO signal for {name}"
        detail   = (
            (f"{summary} " if summary else "")
            + (f"Source: {src}. " if src else "")
            + f"Date: {date}."
            + (f" Confidence: {conf}" if conf else "")
        ).strip()
        events.append(ev("IPO Signal", "HIGH", headline, detail, source_url=src, signal_date=event_date))

    # ── Subsidiary Change ─────────────────────────────────────────────────────
    for row in company_signals.get("subsidiary", []):
        date       = _get(row, "Scan Date", "Date")
        if not is_within_age_limit(date, max_age_days):
            continue
        event_date = _get(row, "Date") or date
        old_parent = _get(row, "Old Parent") or "none"
        new_parent = _get(row, "New Parent") or "none"
        summary    = _get(row, "Summary", "Notes")
        src        = _src(row)
        headline   = f"Subsidiary changed: {old_parent} → {new_parent}"
        detail     = (
            f"Company: {name}. Old parent: {old_parent}. New parent: {new_parent}. "
            + f"Date: {date}. "
            + (f"Source: {src}. " if src else "")
            + (f"Summary: {summary}" if summary else "")
        ).strip()
        events.append(ev(
            "Subsidiary Change", "HIGH",
            headline, detail,
            prev=old_parent, new_val=new_parent,
            signal_date=event_date,
        ))

    return events


# ── Per-company processing (Sheets + News RSS) ─────────────────────────────────

def _process_company_sheets(
    company: dict,
    config: dict,
    store: SnapshotStore,
    all_sheet_data: dict[str, list[dict]],
    all_changes: list[ChangeEvent],
    dry_run: bool,
    skip_sheets: bool = False,
    skip_news: bool = False,
) -> None:
    """Detect signals from Google Sheets (HIGH) and Google News RSS (LOW), fire alerts."""
    apollo_id  = company["apollo_id"]
    domain     = company.get("domain", "")
    name       = company.get("name", "Unknown")
    creds      = config.get("credentials", {})
    behaviour  = config.get("behaviour", {})
    dedup_days = behaviour.get("dedup_window_days", 7)

    store.upsert_company({
        "apollo_id": apollo_id,
        "name":      name,
        "domain":    domain,
        "industry":  company.get("industry", ""),
        "city":      company.get("city", ""),
        "state":     company.get("state", ""),
    })

    old_snapshot = store.get_latest_snapshot(apollo_id)

    # Build new snapshot from CSV fields only (no Apollo enrichment)
    new_data = {**company}

    # ── HIGH signals from Google Sheets ───────────────────────────────────────
    if not skip_sheets:
        company_signals = sheets_client.get_company_signals(name, domain, all_sheet_data)
        events: list[ChangeEvent] = _detect_sheet_events(company, company_signals, config)
    else:
        events = []

    # ── LOW signals from Google News RSS ──────────────────────────────────────
    if not skip_news and behaviour.get("enrich_news", True):
        serpapi_key = creds.get("serpapi_key", "")
        _sig_cfg   = config.get("signals", {})
        _ai_filter = bool(_sig_cfg.get("news_ai_filter", False))
        _ai_key    = os.environ.get("OPENAI_API_KEY", "") if _ai_filter else ""
        _ai_model  = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        new_data["news_articles"] = news_client.get_news_articles(
            name, serpapi_key, ai_key=_ai_key, ai_filter=_ai_filter, ai_model=_ai_model,
        )
        news_events = change_detector.detect_news_signals(
            old_snapshot, new_data, config,
            company_name=name, company_domain=domain,
        )
        events.extend(news_events)

    # Persist snapshot (used for dedup + dashboard)
    store.save_snapshot(apollo_id, new_data)

    # ── Dedup, send, record ───────────────────────────────────────────────────
    for event in events:
        ev_source = getattr(event, 'source_url', '')
        if store.was_alert_sent_recently(apollo_id, event.signal_type, dedup_days,
                                          signal_detail=event.headline):
            # Already stored — backfill source_url if we now have a better value
            if ev_source:
                store.update_source_url_if_better(
                    apollo_id, event.signal_type, event.headline, ev_source
                )
            continue
        store.record_alert(apollo_id, event.signal_type, event.headline, event.severity, dry_run,
                             source_url=ev_source,
                             signal_date=getattr(event, 'signal_date', None) or None)
        all_changes.append(event)
        console.print(f"  [[bold]{event.severity}[/bold]] {name}: {event.headline}")


# ── Dashboard ──────────────────────────────────────────────────────────────────

def _generate_dashboard(store: SnapshotStore, companies_from_csv: list[dict], max_signal_age_days: int = 90) -> Path:
    try:
        from tracker.dashboard_builder import build_dashboard
        out = _REPORTS_DIR / "dashboard.html"
        return build_dashboard(companies_from_csv, store, out, max_signal_age_days)
    except Exception as exc:
        logging.error("Dashboard generation failed: %s", exc)
        return _REPORTS_DIR / "dashboard.html"


def _disable_force_csv_baseline(config_path: Path) -> None:
    """Rewrite the force_csv_baseline line in config.yaml to false."""
    text = config_path.read_text(encoding="utf-8")
    text = text.replace("force_csv_baseline: true", "force_csv_baseline: false")
    config_path.write_text(text, encoding="utf-8")


# ── CLI ────────────────────────────────────────────────────────────────────────

@app.command()
def run(
    dry_run: bool        = typer.Option(False, "--dry-run",        help="No Slack writes, print only"),
    sheets_only: bool    = typer.Option(False, "--sheets-only",    help="Refresh Google Sheets signals only — skips News RSS"),
    news_only: bool      = typer.Option(False, "--news-only",      help="Refresh Google News RSS only — skips Sheets"),
    enrich_sample: bool  = typer.Option(False, "--enrich-sample",  help="Process first 10 companies only"),
    batch: Optional[int] = typer.Option(None,  "--batch",          help="Process next N unprocessed companies"),
    company: Optional[str] = typer.Option(None, "--company",       help="Process single company by name"),
    dashboard_only: bool = typer.Option(False, "--dashboard-only", help="Rebuild dashboard from DB without API calls"),
    reset_alerts: bool   = typer.Option(False, "--reset-alerts",   help="Clear alert dedup history"),
    verbose: bool        = typer.Option(False, "--verbose", "-v",  help="Debug logging"),
    config_path: Path    = typer.Option(_DEFAULT_CONFIG, "--config", help="Path to config.yaml"),
) -> None:
    """Run the weekly company signal tracker (Sheets + News RSS, no Apollo)."""
    _setup_logging(verbose)
    config = _load_config(config_path)

    if sheets_only and news_only:
        console.print("[red]Cannot use both --sheets-only and --news-only at the same time.[/red]")
        raise typer.Exit(1)
    if sheets_only:
        console.print("[cyan]--sheets-only: Refreshing Google Sheets signals, skipping News RSS.[/cyan]")
    if news_only:
        console.print("[cyan]--news-only: Refreshing Google News RSS, skipping Sheets.[/cyan]")

    if config.get("behaviour", {}).get("dry_run") and not dry_run:
        dry_run = True

    if dry_run:
        console.print("[yellow]DRY RUN — no Slack messages will be sent.[/yellow]")

    store = SnapshotStore(_DEFAULT_DB)
    creds = config.get("credentials", {})

    if reset_alerts:
        store.reset_alerts()
        console.print("[green]Alert history cleared.[/green]")

    # Load company list from CSV
    csv_path = Path(__file__).parent / config.get("input", {}).get("csv_file", "apollo-accounts-export.csv")
    companies = load_companies(csv_path)
    total_loaded = len(companies)
    console.print(f"[cyan]Loaded {total_loaded} companies from CSV.[/cyan]")

    top_n = config.get("behaviour", {}).get("top_n")
    if top_n and isinstance(top_n, int) and top_n > 0 and not company:
        companies = companies[:top_n]
        console.print(
            f"[bold yellow][TOP N MODE][/bold yellow] Processing only top {top_n} of {total_loaded} companies"
        )

    max_signal_age_days: int = int(config.get("signals", {}).get("max_signal_age_days", 90))

    if dashboard_only:
        console.print("[cyan]Dashboard-only mode — regenerating from DB…[/cyan]")
        out = _generate_dashboard(store, companies, max_signal_age_days)
        console.print(f"[green]Dashboard: {out}[/green]")
        return

    if company:
        companies = [c for c in companies if company.lower() in c["name"].lower()]
        if not companies:
            console.print(f"[red]No company matching '{company}'[/red]")
            raise typer.Exit(1)
        console.print(f"[cyan]Single-company mode: {companies[0]['name']}[/cyan]")

    console.print(f"[bold cyan]Processing {len(companies):,} companies — Sheets + News RSS[/bold cyan]")

    if enrich_sample:
        companies = companies[:10]
        console.print(f"[yellow]Sample mode: {len(companies)} companies.[/yellow]")

    if batch is not None:
        processed = {r["apollo_id"] for r in store.get_all_companies()}
        unprocessed = [c for c in companies if c["apollo_id"] not in processed]
        companies = unprocessed[:batch]
        console.print(f"[cyan]Batch mode: processing {len(companies)} unprocessed companies.[/cyan]")

    # ── Fetch all Sheets data once before the company loop ────────────────────
    if news_only:
        all_sheet_data: dict[str, list[dict]] = {k: [] for k in ("funding","csuite","ma","ipo","subsidiary")}
        sheets_summary = {k: 0 for k in all_sheet_data}
        console.print("[dim]Skipping Sheets fetch (--news-only).[/dim]")
    else:
        console.print("[cyan]Fetching Google Sheets signal data…[/cyan]")
        all_sheet_data = sheets_client.fetch_all_signals(config)
        sheets_summary = {k: len(v) for k, v in all_sheet_data.items()}
        non_empty = {k: v for k, v in sheets_summary.items() if v > 0}
        if non_empty:
            console.print(
                "[green]Sheets loaded:[/green] "
                + ", ".join(f"{k}={v}" for k, v in non_empty.items())
            )
        else:
            console.print(
                "[yellow]No Sheets data loaded — HIGH signals will be skipped. "
                "Configure google_sheets IDs in config.yaml.[/yellow]"
            )

    start_time = time.time()
    all_changes: list[ChangeEvent] = []
    failed = 0
    first_run_count = 0

    behaviour = config.get("behaviour", {})
    is_global_first_run = (
        behaviour.get("force_csv_baseline", False)
        or not store.has_any_snapshots_at_all()
    )
    if is_global_first_run:
        console.print(
            f"[bold green][FIRST RUN][/bold green] Saving baseline for "
            f"[bold]{len(companies)}[/bold] companies — no alerts on first run."
        )

    # ── Main company loop ─────────────────────────────────────────────────────
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Processing companies…", total=len(companies))

        for comp in companies:
            name = comp.get("name", "Unknown")
            progress.update(task, description=f"  {name[:45]}…")
            try:
                if is_global_first_run:
                    _save_csv_baseline(comp, store)
                    first_run_count += 1
                    continue

                if not store.has_any_snapshot(comp["apollo_id"]):
                    _save_csv_baseline(comp, store)
                    first_run_count += 1
                else:
                    _process_company_sheets(
                        comp, config, store, all_sheet_data, all_changes, dry_run,
                        skip_sheets=news_only, skip_news=sheets_only,
                    )
            except Exception as exc:
                logging.error("Failed to process '%s': %s", name, exc)
                failed += 1
            finally:
                progress.advance(task)

    duration = time.time() - start_time

    if is_global_first_run and behaviour.get("force_csv_baseline", False):
        _disable_force_csv_baseline(config_path)
        console.print("[green]force_csv_baseline set to false in config.yaml — next run will process via Sheets.[/green]")

    # ── Dashboard ─────────────────────────────────────────────────────────────
    console.print("[cyan]Generating dashboard…[/cyan]")
    out = _generate_dashboard(store, companies, max_signal_age_days)
    console.print(f"[green]Dashboard: {out}[/green]")

    # ── Weekly summary to Slack ───────────────────────────────────────────────
    store.record_weekly_run(
        companies_checked=len(companies),
        signals_high=sum(1 for e in all_changes if e.severity == "HIGH"),
        signals_medium=sum(1 for e in all_changes if e.severity == "MEDIUM"),
        signals_low=sum(1 for e in all_changes if e.severity == "LOW"),
        duration_seconds=duration,
    )
    if not is_global_first_run:
        notifier_slack.send_weekly_summary(
            all_changes,
            len(companies),
            webhook_url=creds.get("slack_webhook_url", ""),
            dashboard_url=creds.get("dashboard_url", ""),
            dry_run=dry_run,
        )

    # ── Terminal summary ──────────────────────────────────────────────────────
    table = Table(title="Run Summary", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Companies processed", str(len(companies)))
    table.add_row("  First-run baselines saved", str(first_run_count))
    table.add_row("  Processed via Sheets+News", str(len(companies) - first_run_count))
    table.add_row("Signals detected", str(len(all_changes)))
    table.add_row("  HIGH", str(sum(1 for e in all_changes if e.severity == "HIGH")))
    table.add_row("  MEDIUM", str(sum(1 for e in all_changes if e.severity == "MEDIUM")))
    table.add_row("  LOW", str(sum(1 for e in all_changes if e.severity == "LOW")))
    table.add_row("Sheets rows loaded", str(sum(sheets_summary.values())))
    table.add_row("Failed", str(failed))
    table.add_row("Duration", f"{duration:.1f}s")
    table.add_row("Mode", "[yellow]DRY RUN[/yellow]" if dry_run else "LIVE")
    console.print(table)

    # ── Write weekly-stats.json for Railway /api/weekly-stats endpoint ───────
    import json as _json
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    import sqlite3 as _sqlite3
    _cutoff = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
    try:
        _conn = _sqlite3.connect(str(store.db_path))
        _rows = _conn.execute(
            "SELECT signal_type, COUNT(*) cnt FROM alerts_sent"
            " WHERE dry_run=0 AND sent_at >= ? GROUP BY signal_type ORDER BY cnt DESC",
            (_cutoff,),
        ).fetchall()
        _total_signals = _conn.execute(
            "SELECT COUNT(*) FROM alerts_sent WHERE dry_run=0 AND sent_at >= ?", (_cutoff,)
        ).fetchone()[0]
        _companies_count = _conn.execute(
            "SELECT COUNT(*) FROM companies WHERE is_active=1"
        ).fetchone()[0]
        _conn.close()
        _stats = {
            "counts": dict(_rows),
            "total": _total_signals,
            "companies": _companies_count,
            "generated_at": _dt.now(_tz.utc).isoformat(),
            "cutoff": _cutoff,
        }
        _stats_path = Path(__file__).parent / "data" / "weekly-stats.json"
        _stats_path.write_text(_json.dumps(_stats, indent=2))
        console.print(f"[green]Weekly stats JSON written ({_total_signals} signals)[/green]")
    except Exception as _e:
        console.print(f"[yellow]Could not write weekly-stats.json: {_e}[/yellow]")

    # ── Auto-push dashboard to GitHub → triggers Railway redeploy ────────────
    import subprocess as _sp
    _proj = Path(__file__).parent
    _sp.run(["git", "add", "reports/dashboard.html", "data/weekly-stats.json"], cwd=_proj)
    _sp.run(["git", "commit", "-m", "Dashboard refresh"], cwd=_proj)
    _sp.run(["git", "push"], cwd=_proj)


if __name__ == "__main__":
    app()
