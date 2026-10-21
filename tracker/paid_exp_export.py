"""Paid Marketing Agents: Test & Experiment Tracker, the downloads.

One report, three files, all drawn from `tables(report)` so they cannot drift apart:

  Excel    every table as a sheet
  CSV      every table as its own CSV in a zip
  PDF      the report as a document to read and to send: the summary, what needs a decision, every experiment and every ad test with its verdict and next step

A value that a spreadsheet would run as a formula (it starts with = + - @) is written as plain text in every format (tracker/paid_strategy_export.py).
"""
from __future__ import annotations

import io
import re
import zipfile

from .paid_strategy_check import text as clean
from .paid_strategy_export import CELL_CHARS, _csv_text, plain

TRIAL_TEXT = {"winner": "Trial is ahead", "behind": "Trial is behind", "no_difference": "No difference", "too_early": "Too early", "no_data": "No data", "not_measured": "Not measured"}
AD_TEXT = {"winner": "Clear leader", "partial": "Leads some", "behind": "Behind", "no_difference": "No difference", "too_early": "Too early", "no_data": "Not shown"}
KIND_TEXT = {"decide": "Decide now", "apply": "Apply it", "end": "End it", "pause": "Pause the losers", "stuck": "Not getting traffic", "check": "Check it"}
STATE_TEXT = {"running": "Running", "setting up": "Setting up", "ended": "Ended", "applied": "Applied"}
METRIC_TEXT = {"convRate": "Conversion rate", "ctr": "Click rate"}
ACCOUNT_LABEL = {"google": "Google Ads account", "meta": "Meta ad account"}
PLATFORM_TEXT = {"google": "Google Ads", "meta": "Meta Ads"}
PDF_AD_TESTS = 40


def file_stem(report):
    acct = report.get("account") or {}
    base = re.sub(r"[^A-Za-z0-9]+", "-", acct.get("name") or acct.get("id") or "")[:60].strip("-") or "account"
    return "%s-test-and-experiment-tracker" % base


def _rate(n):
    return None if n is None else round(float(n), 2)


def _when(iso):
    return (iso or "")[:16].replace("T", " ")


def _how(plan):
    """The plan's steps as numbered lines for a cell, or ""."""
    return "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate((plan or {}).get("steps") or []))


def _worth(plan):
    """What acting is worth, and the note on how long a real answer would take, for a cell, or ""."""
    return " ".join(x for x in ((plan or {}).get("worth"), (plan or {}).get("note")) if x)


def tables(report):
    """{sheet name: (header, rows)} in the order they are shown. Empty tables are left out. Numbers stay numbers."""
    summary = report.get("summary") or {}
    acct = report.get("account") or {}
    cur = acct.get("currency") or ""
    exps = report.get("experiments") or []
    tests = report.get("adTests") or []
    out = {}
    shown = [[ACCOUNT_LABEL.get(a["platform"], "Account"), "%s (%s)" % (a.get("name") or "", a.get("id") or "")] for a in report.get("accounts") or []]
    ov = shown + [["Ad tests cover", report.get("window") or ""], ["Read on", _when(report.get("readAt"))],
          ["Report made on", (report.get("generatedAt") or "")[:10]], ["Tests that need a decision", summary.get("needsAction")],
          ["Experiments", summary.get("experiments")], ["Experiments running", summary.get("running")], ["Experiments ended", summary.get("ended")], ["Experiments applied", summary.get("applied")],
          ["Running and the trial is ahead", summary.get("winners")], ["Running and the trial is behind", summary.get("behind")], ["Running with no difference", summary.get("noDifference")],
          ["Running and too early to call", summary.get("tooEarly")], ["Running past their end date", summary.get("overdue")], ["Ending within 7 days", summary.get("endsSoon")],
          ["Ad groups with two or more live ads", summary.get("adTests")], ["Ad tests shown", summary.get("adTestsShown")], ["Ad tests with a clear leader", summary.get("adConclusive")],
          ["Ad tests with no difference", summary.get("adNoDifference")], ["Ad tests too early to call", summary.get("adTooEarly")],
          ["Spend in experiments (%s)" % cur, summary.get("experimentSpend")], ["Spend in the ad tests shown (%s)" % cur, summary.get("adSpend")]]
    out["Summary"] = (["Item", "Detail"], [r for r in ov if r[1] not in (None, "")])

    needs = report.get("needs") or []
    if needs:
        out["Needs a decision"] = (["Order", "What to do", "Kind of test", "Test", "Detail", "Spend (%s)" % cur, "How to do it", "What it is worth"],
                                   [[i + 1, n.get("label") or KIND_TEXT.get(n["kind"], n["kind"]), "Ad test" if n["ref"]["type"] == "adTest" else "Experiment", n["title"], n["text"], n["spend"],
                                     _how(n.get("plan")), _worth(n.get("plan"))] for i, n in enumerate(needs)])

    if exps:
        rows, days = [], []
        for e in exps:
            c = e.get("comparison") or {}
            ctl = next((a for a in e["arms"] if a["control"]), None) or {}
            tri = next((a for a in e["arms"] if not a["control"]), None) or {}
            ct, tt = ctl.get("total") or {}, tri.get("total") or {}
            rows.append([e["name"], STATE_TEXT.get(e["state"], e["state"]), e.get("typeLabel") or "", e.get("start") or "", e.get("end") or "", e.get("daysRead"),
                         TRIAL_TEXT.get(e["verdict"], e["verdict"]), METRIC_TEXT.get(c.get("metric"), ""), _rate(c.get("control")), _rate(c.get("trial")), c.get("relative"), c.get("confidence"),
                         c.get("p"), ct.get("impressions"), ct.get("clicks"), ct.get("conversions"), ct.get("cost"), tt.get("impressions"), tt.get("clicks"), tt.get("conversions"), tt.get("cost"),
                         e["action"]["text"], e.get("splitNote") or "", PLATFORM_TEXT.get(e.get("platform"), ""), e.get("reason") or "",
                         _how(e["action"].get("plan")), _worth(e["action"].get("plan"))])
            for a in e["arms"]:
                for d in a.get("days") or []:
                    days.append([e["name"], a["name"], "Control" if a["control"] else "Trial", d[0], d[1], d[2], d[3], d[4], d[5]])
        out["Experiments"] = (["Experiment", "Status", "Type", "Start", "End", "Days of results", "Verdict", "Compared on", "Control rate %", "Trial rate %", "Trial change %", "Confidence %",
                               "p value", "Control impressions", "Control clicks", "Control conversions", "Control spend", "Trial impressions", "Trial clicks", "Trial conversions", "Trial spend",
                               "What to do", "Traffic split warning", "Platform", "Why there is no result", "How to do it", "What it is worth"], rows)
        if days:
            out["Experiment days"] = (["Experiment", "Arm", "Role", "Date", "Impressions", "Clicks", "Spend", "Conversions", "Conversion value"], days)

    if tests:
        trows, arows = [], []
        for t in tests:
            trows.append([t["name"], t["campaign"], t["adCount"], t["spend"], AD_TEXT.get(t["verdict"], t["verdict"]), METRIC_TEXT.get(t.get("metric"), ""), t.get("eligible"),
                          t["action"]["text"], t.get("note") or "", PLATFORM_TEXT.get(t.get("platform"), ""), _how(t["action"].get("plan")), _worth(t["action"].get("plan"))])
            for a in t["ads"]:
                n = a["numbers"]
                arows.append([t["name"], t["campaign"], a["label"], " | ".join(a.get("descriptions") or []), a.get("url") or "", a.get("strength") or "", n["impressions"], n["clicks"], n["ctr"],
                              n["conversions"], n["convRate"], n["cost"], n["cpa"], AD_TEXT.get(a["verdict"], a["verdict"]), a.get("rate"), a.get("relative"), a.get("confidence"),
                              "Yes" if a["id"] == t.get("leader") else ""])
        out["Ad tests"] = (["Ad group or ad set", "Campaign", "Live ads", "Spend, last 30 days", "Verdict", "Compared on", "Ads compared", "What to do", "Note", "Platform", "How to do it", "What it is worth"], trows)
        out["Ads"] = (["Ad group or ad set", "Campaign", "Ad (headlines)", "Descriptions", "Final URL", "Ad strength", "Impressions", "Clicks", "Click rate %", "Conversions", "Conversion rate %", "Spend",
                       "Cost per conversion", "Verdict", "Rate compared %", "Against the leader %", "Confidence %", "Leader"], arows)

    notes = [["Note", n] for n in report.get("notes") or []] + [["Part that could not be read", w] for w in report.get("warnings") or []]
    for st in report.get("stale") or []:
        notes.append(["Saved read", "The read is the one saved on %s because %s would not answer now." % (_when(st.get("savedAt")), PLATFORM_TEXT.get(st.get("platform"), "the platform"))])
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
    fills = {"Trial is ahead": "DDF3E2", "Clear leader": "DDF3E2", "Leads some": "DDF3E2", "Trial is behind": "FFD9D6", "Behind": "FFD9D6", "Too early": "E1ECFA",
             "No difference": "ECEFF5", "Decide now": "FFD9D6", "Apply it": "DDF3E2", "End it": "FFF1CC", "Pause the losers": "FFF1CC", "Not getting traffic": "FFF1CC"}
    for name, (header, rows) in tables(report).items():
        ws = wb.create_sheet(name)
        ws.append([plain(h) for h in header])
        for c in ws[1]:
            c.fill, c.font, c.alignment = head_fill, head_font, wrap
        for r in rows:
            ws.append([plain(v) for v in r])
        tint_cols = [i for i, h in enumerate(header) if h in ("Verdict", "What to do")]
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, str):
                    c.data_type = "s"
                c.alignment = wrap
            for i in tint_cols:
                if fills.get(row[i].value):
                    row[i].fill = PatternFill("solid", fgColor=fills[row[i].value])
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
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for sheet, (header, rows) in tables(report).items():
            z.writestr(re.sub(r"[^A-Za-z0-9]+", "-", sheet).strip("-").lower() + ".csv", _csv_text(header, rows))
    return out.getvalue()


def pdf_ad_rows(report):
    """([Ad group (campaign), live ads, spend, verdict, what to do], how many ad tests there were): the `PDF_AD_TESTS` that spent most, for the PDF."""
    tests = report.get("adTests") or []
    return [[t["name"] + " (" + t["campaign"] + ")", t["adCount"], t["spend"], AD_TEXT.get(t["verdict"], t["verdict"]), t["action"]["text"]] for t in tests[:PDF_AD_TESTS]], len(tests)


def pdf_ad_note(report):
    """The line under the ad tests in the PDF when it lists fewer than the report holds, or an empty string."""
    rows, total = pdf_ad_rows(report)
    return "The %d ad tests that spent most are listed here. The Excel file has every ad test that was read." % len(rows) if total > len(rows) else ""


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
    tint = {"Trial is ahead": "#DDF3E2", "Clear leader": "#DDF3E2", "Leads some": "#DDF3E2", "Trial is behind": "#FFD9D6", "Behind": "#FFD9D6", "Too early": "#E1ECFA", "No difference": "#ECEFF5",
            "Decide now": "#FFD9D6", "Apply it": "#DDF3E2", "End it": "#FFF1CC", "Pause the losers": "#FFF1CC", "Not getting traffic": "#FFF1CC"}

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
    acct = report.get("account") or {}
    name = acct.get("name") or acct.get("id") or ""
    story = [P("Test & Experiment Tracker", h1), P("%s (%s)" % (name, acct.get("id") or ""), h2),
             P("Read on %s | report made on %s | ad tests cover %s" % (_when(report.get("readAt")), (report.get("generatedAt") or "")[:10], report.get("window") or ""), small), Spacer(1, 6)]
    head_t, ov_rows = all_tables["Summary"]
    story += [P("Summary", h2), table(head_t, ov_rows)]
    if "Needs a decision" in all_tables:
        h, rows = all_tables["Needs a decision"]
        story += [PageBreak(), P("Needs a decision", h2), table(["No.", "What to do", "Kind", "Test", "Detail", "Spend"], rows, shade_col=1, weights=[0.5, 1.3, 1.0, 2.4, 6.0, 0.9])]
    exps = report.get("experiments") or []
    if exps:
        rows = []
        for e in exps:
            c = e.get("comparison") or {}
            both = "%s%% against %s%% (%s)" % (c["control"], c["trial"], METRIC_TEXT.get(c.get("metric"), "")) if c.get("control") is not None and c.get("trial") is not None else ""
            rows.append([e["name"], STATE_TEXT.get(e["state"], e["state"]), TRIAL_TEXT.get(e["verdict"], e["verdict"]), both, e["action"]["text"]])
        story += [PageBreak(), P("Experiments", h2), table(["Experiment", "Status", "Verdict", "Control against trial", "What to do"], rows, shade_col=2, weights=[2.4, 0.9, 1.2, 2.2, 6.0])]
    ad_rows, _total = pdf_ad_rows(report)
    if ad_rows:
        story += [PageBreak(), P("Ad copy tests", h2), table(["Ad group", "Live ads", "Spend", "Verdict", "What to do"], ad_rows, shade_col=3, weights=[2.6, 0.7, 0.9, 1.1, 6.0])]
        if pdf_ad_note(report):
            story += [P(pdf_ad_note(report), small)]
    if "Notes" in all_tables:
        h, rows = all_tables["Notes"]
        story += [PageBreak(), P("Notes", h2), table(h, rows)]
    out = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(BODY, 7)
        canvas.setFillColor(mute)
        canvas.drawString(14 * mm, 8 * mm, "Position2: %s. Test & Experiment Tracker" % clean(name, 70))
        canvas.drawRightString(page[0] - 14 * mm, 8 * mm, "Page %d" % doc.page)
        canvas.restoreState()

    doc = SimpleDocTemplate(out, pagesize=page, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=14 * mm,
                            title="Test & Experiment Tracker: %s" % name, author="Position2")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
