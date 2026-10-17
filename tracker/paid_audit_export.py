"""Paid Marketing Agents: Account Audit, the downloads.

One report, three files, all drawn from `tables(report)` so they cannot drift apart:

  Excel    every table as a sheet
  CSV      every table as its own CSV in a zip
  PDF      the audit as a document to read and to send: the summary, the checks to put right first, then every section's findings

A value that a spreadsheet would run as a formula (it starts with = + - @) is written as plain text in every format (tracker/paid_strategy_export.py).
"""
from __future__ import annotations

import io
import re
import zipfile

from . import paid_audit_model as model
from .paid_strategy_check import text as clean
from .paid_strategy_export import CELL_CHARS, SHEET_NAME_BAD, _csv_text, _join, plain

RESULT_TEXT = {"pass": "Pass", "warn": "Warning", "fail": "Fail", "na": "Not applicable", "unknown": "Could not check"}
PLATFORM_TEXT = {"google": "Google Ads", "meta": "Meta Ads"}
SEVERITY_TEXT = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}
ALL_HEADER = ["Platform", "Section", "Check", "Severity", "Result", "What was found", "Affected", "Why it matters", "What to do"]
FIX_HEADER = ["Order", "Platform", "Section", "Check", "Severity", "Result", "What was found", "Affected", "What to do"]


def file_stem(report):
    names = [a.get("name") or a.get("id") or "" for a in report.get("accounts") or []]
    base = re.sub(r"[^A-Za-z0-9]+", "-", "-".join(n for n in names if n))[:60].strip("-") or "account"
    return "%s-account-audit" % base


PDF_AFFECTED = 5                       # affected things named per finding in the PDF (the workbook names every one the read kept)


def _affected(f, limit=None):
    shown = f.get("items") or []
    if not shown:
        return ""
    if limit is not None:
        shown = shown[:limit]
    more = f.get("count", len(f.get("items") or [])) - len(shown)
    return "; ".join(str(i) for i in shown) + (" (and %d more)" % more if more > 0 else "")


def _row(f, labels):
    return [PLATFORM_TEXT.get(f["platform"], f["platform"]), labels.get(f["section"], f["section"]), "%s %s" % (f["id"], f["title"]), SEVERITY_TEXT.get(f["severity"], f["severity"]),
            RESULT_TEXT.get(f["result"], f["result"]), f["found"], _affected(f), f["why"], f["fix"]]


def tables(report):
    """{sheet name: (header, rows)} in the order they are shown. Empty tables are left out. Numbers stay numbers."""
    sections = report.get("sections") or []
    labels = {s["key"]: s["label"] for s in sections}
    summary = report.get("summary") or {}
    out = {}
    ov = []
    for a in report.get("accounts") or []:
        ov.append([PLATFORM_TEXT.get(a["platform"], a["platform"]) + " account", "%s (%s)" % (a.get("name") or "", a.get("id") or "")])
    ov += [["Read on", (report.get("readAt") or "")[:16].replace("T", " ")], ["Audit made on", (report.get("generatedAt") or "")[:10]],
           ["Overall score", summary.get("score") if summary.get("score") is not None else "Nothing could be judged"], ["Overall grade", summary.get("grade") or ""]]
    for p, v in (summary.get("platforms") or {}).items():
        ov.append(["%s score" % PLATFORM_TEXT.get(p, p), "%s (%s)" % (v.get("score"), v.get("grade")) if v.get("score") is not None else "Nothing could be judged"])
    t = summary.get("tally") or {}
    ov += [["Checks run", t.get("total")], ["Passed", t.get("pass")], ["Warnings", t.get("warn")], ["Failed", t.get("fail")], ["Failed critical checks", summary.get("critical")],
           ["Not applicable", t.get("na")], ["Could not check", t.get("unknown")], ["Share of the checks that could not be judged", "%d%%" % summary.get("unknownShare", 0)]]
    for s in sections:
        ov.append(["%s: %s" % (PLATFORM_TEXT.get(s["platform"], s["platform"]), s["label"]), "%s (%s)" % (s["score"], s["grade"]) if s["score"] is not None else "Nothing could be judged"])
    out["Summary"] = (["Item", "Detail"], [r for r in ov if r[1] not in (None, "")])

    fix = report.get("fixFirst") or []
    if fix:
        out["Fix first"] = (FIX_HEADER, [[i + 1] + [r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[8]] for i, r in enumerate(_row(f, labels) for f in fix)])
    findings = [f for s in sections for f in s["findings"]]
    if findings:
        out["All checks"] = (ALL_HEADER, [_row(f, labels) for f in findings])
    unknown = [f for f in findings if f["result"] == "unknown"]
    if unknown:
        out["Could not check"] = (["Platform", "Check", "Why not"], [[PLATFORM_TEXT.get(f["platform"], f["platform"]), "%s %s" % (f["id"], f["title"]), f["found"]] for f in unknown])
    notes = [["Note", n] for n in report.get("notes") or []] + [["Part that could not be read", w] for w in report.get("warnings") or []]
    for st in report.get("stale") or []:
        notes.append(["Saved read", "%s was read on %s because the platform would not answer now." % (PLATFORM_TEXT.get(st["platform"], st["platform"]), str(st.get("savedAt") or "")[:16].replace("T", " "))])
    if notes:
        out["Notes"] = (["Kind", "Detail"], notes)
    return out


def xlsx(report):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    wb.remove(wb.active)
    head_fill, head_font, wrap = PatternFill("solid", fgColor="1F2A44"), Font(bold=True, color="FFFFFF"), Alignment(wrap_text=True, vertical="top")
    fills = {"Fail": "FFD9D6", "Warning": "FFF1CC", "Pass": "DDF3E2", "Could not check": "E4E8F0"}
    used = set()
    for name, (header, rows) in tables(report).items():
        name = SHEET_NAME_BAD.sub("", name)[:31] or "Sheet"
        base, n = name, 2
        while name in used:
            name, n = base[:28] + "-%d" % n, n + 1
        used.add(name)
        ws = wb.create_sheet(name)
        ws.append([plain(h) for h in header])
        for c in ws[1]:
            c.fill, c.font, c.alignment = head_fill, head_font, wrap
        for r in rows:
            ws.append([plain(v) for v in r])
        result_col = header.index("Result") + 1 if "Result" in header else None
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, str):
                    c.data_type = "s"
                c.alignment = wrap
            if result_col and fills.get(row[result_col - 1].value):
                row[result_col - 1].fill = PatternFill("solid", fgColor=fills[row[result_col - 1].value])
        for i in range(1, len(header) + 1):
            longest = max([len(str(header[i - 1]))] + [min(len(str(r[i - 1])), 70) for r in rows if i - 1 < len(r) and r[i - 1] is not None] or [10])
            ws.column_dimensions[get_column_letter(i)].width = min(60, max(10, longest + 2))
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def csv_zip(report):
    out = io.BytesIO()
    used = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for sheet, (header, rows) in tables(report).items():
            stem = re.sub(r"[^A-Za-z0-9]+", "-", sheet).strip("-").lower() or "table"
            cand, n = stem, 2
            while cand in used:
                cand, n = "%s-%d" % (stem, n), n + 1
            used.add(cand)
            z.writestr(cand + ".csv", _csv_text(header, rows))
    return out.getvalue()


def pdf_sections(report):
    """[(section label with its platform, score text, header, rows)] for the PDF: each section's findings with the result first. Passing and
    not applicable checks are short lines, so the document is about what to look at."""
    out = []
    for s in report.get("sections") or []:
        rows = []
        for f in s["findings"]:
            rows.append([RESULT_TEXT.get(f["result"], f["result"]), SEVERITY_TEXT.get(f["severity"], f["severity"]), "%s %s" % (f["id"], f["title"]),
                         f["found"] + ((" Affected: " + _affected(f, PDF_AFFECTED)) if f["result"] in ("fail", "warn") and _affected(f) else ""), f["fix"] if f["result"] in ("fail", "warn") else ""])
        score = "%s (%s)" % (s["score"], s["grade"]) if s["score"] is not None else "nothing could be judged"
        out.append(("%s: %s" % (PLATFORM_TEXT.get(s["platform"], s["platform"]), s["label"]), score, ["Result", "Severity", "Check", "What was found", "What to do"], rows))
    return out


def pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from .event_intel_pdf import BODY, BOLD, _fonts, text_markup
    _fonts()
    ink, mute, rule = colors.HexColor("#1B2333"), colors.HexColor("#5B6577"), colors.HexColor("#D7DDE8")
    base = ParagraphStyle("b", fontName=BODY, fontSize=8.4, leading=11.4, textColor=ink)
    small = ParagraphStyle("s", parent=base, fontSize=7.6, leading=10, textColor=mute)
    h1 = ParagraphStyle("h1", parent=base, fontName=BOLD, fontSize=17, leading=21, spaceBefore=4, spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=base, fontName=BOLD, fontSize=12, leading=15, spaceBefore=10, spaceAfter=4, keepWithNext=1)
    head = ParagraphStyle("th", parent=base, fontName=BOLD, textColor=colors.white, fontSize=7.8)
    page = landscape(A4)
    width = page[0] - 28 * mm
    tint = {"Fail": "#FFD9D6", "Warning": "#FFF1CC", "Pass": "#EAF6ED", "Could not check": "#ECEFF5"}

    def P(t, st=base):
        return Paragraph(text_markup(str(t if t is not None else "")), st)

    def table(header, rows, shade_col=None, weights=None):
        n = len(header)
        weights = weights or [max(1.0, min(4.0, max([len(str(header[i]))] + [min(len(str(r[i])), 200) / 12.0 for r in rows if i < len(r)] or [1]) / 6.0)) for i in range(n)]
        cw = [width * w / sum(weights) for w in weights]
        data = [[Paragraph(text_markup(h), head) for h in header]] + [[P(str(c)[:CELL_CHARS]) for c in r] for r in rows]
        t = Table(data, colWidths=cw, repeatRows=1, splitInRow=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2A44")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, rule),
                 ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if shade_col is None:
            style.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6FA")]))
        else:
            for i, r in enumerate(rows, start=1):
                if tint.get(r[shade_col]):
                    style.append(("BACKGROUND", (shade_col, i), (shade_col, i), colors.HexColor(tint[r[shade_col]])))
        t.setStyle(TableStyle(style))
        return t

    all_tables = tables(report)
    names = ", ".join("%s (%s)" % (a.get("name") or a.get("id"), PLATFORM_TEXT.get(a["platform"], a["platform"])) for a in report.get("accounts") or [])
    story = [P("Account Audit", h1), P(names, h2), P("Read on %s | audit made on %s" % ((report.get("readAt") or "")[:16].replace("T", " "), (report.get("generatedAt") or "")[:10]), small), Spacer(1, 6)]
    head_t, ov_rows = all_tables["Summary"]
    story += [P("Summary", h2), table(head_t, ov_rows)]
    if "Fix first" in all_tables:
        fix_rows = [[i + 1, "%s %s" % (f["id"], f["title"]), SEVERITY_TEXT.get(f["severity"], f["severity"]), RESULT_TEXT.get(f["result"], f["result"]),
                     f["found"] + ((" Affected: " + _affected(f, PDF_AFFECTED)) if _affected(f) else ""), f["fix"]] for i, f in enumerate(report.get("fixFirst") or [])]
        story += [PageBreak(), P("Fix first", h2), table(["No.", "Check", "Severity", "Result", "What was found", "What to do"], fix_rows, shade_col=3, weights=[0.6, 2.0, 0.9, 0.9, 5.0, 3.2])]
    for label, score, header, rows in pdf_sections(report):
        story += [PageBreak(), P("%s (score %s)" % (label, score), h2), table(header, rows, shade_col=0, weights=[0.9, 0.8, 2.2, 5.0, 3.2])]
    if "Notes" in all_tables:
        h, rows = all_tables["Notes"]
        story += [PageBreak(), P("Notes", h2), table(h, rows)]
    out = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(BODY, 7)
        canvas.setFillColor(mute)
        canvas.drawString(14 * mm, 8 * mm, "Position2: %s. Account Audit" % clean(names, 70))
        canvas.drawRightString(page[0] - 14 * mm, 8 * mm, "Page %d" % doc.page)
        canvas.restoreState()

    doc = SimpleDocTemplate(out, pagesize=page, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=14 * mm,
                            title="Account Audit: %s" % names, author="Position2")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
