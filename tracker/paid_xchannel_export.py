"""Paid Marketing Agents: Cross-Channel & Attribution Analysis, the downloads.

One report, three files, all drawn from `tables(report)` so they cannot drift apart:

  Excel    every table as a sheet
  CSV      every table as its own CSV in a zip
  PDF      the report as a document to read and to send: the summary, the platforms side by side, the findings, what moved the blended cost, how each platform counts, the reconciliation

A value that a spreadsheet would run as a formula (it starts with = + - @) is written as plain text in every format (tracker/paid_strategy_export.py).
"""
from __future__ import annotations

import io
import re
import zipfile

from .paid_strategy_check import text as clean
from .paid_strategy_export import CELL_CHARS, _csv_text, plain
from .paid_xchannel_findings import poss
from .paid_xchannel_math import PLATFORM_NAME

LEVEL_TEXT = {"high": "High", "medium": "Medium", "low": "Low", "info": "For information"}
READS_TEXT = {"together": "Move together", "opposite": "Move in opposite directions", "none": "No clear link"}
WHEN_TEXT = {0: "in the same week", 1: "a week later"}
PDF_TYPES = 30


def file_stem(report):
    acct = report.get("account") or {}
    base = re.sub(r"[^A-Za-z0-9]+", "-", acct.get("name") or acct.get("id") or "")[:60].strip("-") or "account"
    return "%s-cross-channel-and-attribution" % base


def _when(iso):
    return (iso or "")[:16].replace("T", " ")


def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


def _name(p):
    return PLATFORM_NAME.get(p, p)


def tables(report):
    """{sheet name: (header, rows)} in the order they are shown. Empty tables are left out. Numbers stay numbers."""
    cur = (report.get("account") or {}).get("currency") or ""
    rng, prev = report.get("range") or {}, report.get("previous") or {}
    ch = report.get("channels") or {}
    bl = report.get("blended") or {}
    out = {}
    ov = [[("Google Ads account" if a["platform"] == "google" else "Meta ad account"), "%s (%s)" % (a.get("name") or "", a.get("id") or "")] for a in report.get("accounts") or []]
    ov += [["Range", "%s, %s to %s (%s days)" % (rng.get("label"), rng.get("start"), rng.get("end"), rng.get("days"))], ["Compared with", "%s to %s" % (prev.get("start"), prev.get("end"))],
           ["Conversion measure", report.get("measureLabel")], ["Currency", cur], ["Read on", _when(report.get("readAt"))], ["Report made on", (report.get("generatedAt") or "")[:10]],
           ["Spend", (bl.get("cur") or {}).get("cost")], ["Conversions claimed by the platforms", (bl.get("cur") or {}).get("conv")], ["Blended cost per claimed conversion", _r((bl.get("cur") or {}).get("cpa"))],
           ["Findings", (report.get("summary") or {}).get("findings")], ["High level findings", (report.get("summary") or {}).get("high")]]
    out["Summary"] = (["Item", "Detail"], [r for r in ov if r[1] not in (None, "")])

    head = ["Platform", "Conversion measure", "Spend", "Impressions", "Clicks", "Conversions", "Conversion value", "Cost per conversion", "Return on ad spend", "Click rate %", "Cost per click",
            "Conversion rate %", "Share of spend %", "Share of conversions %", "Share of value %", "Previous spend", "Previous conversions", "Previous cost per conversion", "Spend change %",
            "Conversions change %", "Cost per conversion change %"]
    rows = []
    for p in report.get("platforms") or []:
        c = ch[p]
        a, b = c["cur"], c["prev"]
        rows.append([c["name"], "All conversions" if c["measure"] == "all" else "Conversions", a["cost"], a["impr"], a["clicks"], a["conv"], a["value"], _r(a["cpa"]), _r(a["roas"], 3), _r(a["ctr"]), _r(a["cpc"]),
                     _r(a["cvr"]), c["share"]["cost"], c["share"]["conv"], c["share"]["value"], b["cost"], b["conv"], _r(b["cpa"]), c["delta"]["cost"], c["delta"]["conv"], c["delta"]["cpa"]])
    if len(rows) > 1 and bl:
        a, b, d = bl["cur"], bl["prev"], bl["delta"]
        rows.append(["Blended (the platforms' claims added)", "", a["cost"], a["impr"], a["clicks"], a["conv"], a["value"], _r(a["cpa"]), _r(a["roas"], 3), _r(a["ctr"]), _r(a["cpc"]), _r(a["cvr"]),
                     100.0, 100.0 if a["conv"] else None, 100.0 if a["value"] else None, b["cost"], b["conv"], _r(b["cpa"]), d["cost"], d["conv"], d["cpa"]])
    out["Platforms"] = (head, rows)

    trows = [[ch[p]["name"], t["label"], t["roleLabel"], t["cost"], t["shareOfPlatform"], t["shareOfAll"], t["conv"], t["cpa"], t["value"], t["roas"]]
             for p in report.get("platforms") or [] for t in ch[p]["types"]]
    if trows:
        out["Campaign types"] = (["Platform", "Campaign type", "Role (rule of thumb)", "Spend", "Share of the platform's spend %", "Share of all spend %", "Conversions", "Cost per conversion",
                                  "Conversion value", "Return on ad spend"], trows)

    d = report.get("decomposition") or {}
    if d.get("ok"):
        rows = [["Blended cost per conversion, previous period", _r(d["from"])], ["Blended cost per conversion, this period", _r(d["to"])], ["Change", _r(d["change"])],
                ["Of which the mix of conversions between platforms", _r(d["mix"])]] + [["Of which %s own cost per conversion" % poss(p), _r(v)] for p, v in d["rate"].items()]
        rows += [["%s share of conversions, previous to this period %%" % _name(p), "%s to %s" % (_r(s["before"], 1), _r(s["after"], 1))] for p, s in d["mixShare"].items()]
        out["Cost change"] = (["Part", "Amount (%s)" % cur if cur else "Amount"], rows)

    weeks = report.get("weeks") or []
    if weeks:
        plats = report.get("platforms") or []
        header = ["Week starting"] + [x for p in plats for x in ("%s spend" % _name(p), "%s conversions" % _name(p))]
        out["Weekly"] = (header, [[w["start"]] + [v for p in plats for v in ((w[p] or {}).get("cost"), (w[p] or {}).get("conv"))] for w in weeks])
    lk = report.get("links") or {}
    if lk.get("ok"):
        out["Weekly links"] = (["Spend of", "Against conversions of", "When", "Weeks", "Correlation", "p value", "Reads as"],
                               [[_name(t["spend"]), _name(t["conversions"]), WHEN_TEXT.get(t["lag"], ""), t["n"], _r(t["r"]), _r(t["p"], 4), READS_TEXT.get(t["reads"], t["reads"])] for t in lk["tests"]])

    at = report.get("attribution") or {}
    arows = []
    g = at.get("google")
    for a in (g or {}).get("counted") or []:
        arows.append(["Google Ads", "Counted conversion action", a["name"], "%s, %s, click window %s days, view window %s days%s" % (
            a["category"], a["model"], a["clickWindow"], a["viewWindow"], "" if a["conversions"] is None else ", %s conversions in the range" % a["conversions"])])
    for a in (g or {}).get("secondary") or []:
        arows.append(["Google Ads", "Not in the Conversions column", a["name"], a["category"]])
    m = at.get("meta")
    for x in (m or {}).get("groups") or []:
        arows.append(["Meta Ads", "Attribution window", "%s day click, %s day view" % (x["click"], x["view"]),
                      "%s of spend in %s ad set%s%s" % (x["share"], x["adsets"], "" if x["adsets"] == 1 else "s", " (Meta's default, not stated by the ad set)" if x["assumed"] else "")])
    for n in at.get("notes") or []:
        arows.append(["Both", LEVEL_TEXT.get(n["level"], n["level"]), n["title"], n["text"]])
    if arows:
        out["Attribution"] = (["Platform", "Kind", "Item", "Detail"], arows)

    rec = report.get("reconciliation")
    if rec:
        rows = []
        c, v = rec.get("conversions"), rec.get("value")
        if c:
            rows += [["Conversions the platforms claim", c["claimed"]], ["Conversions your system recorded", c["actual"]], ["Claimed against recorded", _r(c["ratio"], 3)],
                     ["Spend per recorded conversion", _r(rec.get("blendedCpa"))]]
            rows += [["%s cost per conversion%s" % (_name(p), " scaled to what was recorded" if c["over"] else ""), _r(x.get("cpa"))] for p, x in c["perPlatform"].items()]
        if v:
            rows += [["Value the platforms claim", v["claimed"]], ["Revenue your system recorded", v["actual"]], ["Return on the revenue recorded", _r(rec.get("blendedRoas"), 3)]]
            rows += [["%s return%s" % (_name(p), " scaled to what was recorded" if v["over"] else ""), _r(x.get("roas"), 3)] for p, x in v["perPlatform"].items()]
        out["Reconciliation"] = (["Item", "Value"], rows)

    frows = [[LEVEL_TEXT.get(f["level"], f["level"]), f["title"], f["text"], "; ".join(f.get("evidence") or []), f.get("action") or "",
              "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate((f.get("plan") or {}).get("steps") or [])),
              " ".join(x for x in ((f.get("plan") or {}).get("worth"), (f.get("plan") or {}).get("note")) if x)] for f in report.get("findings") or []]
    if frows:
        out["Findings"] = (["Level", "Finding", "Detail", "Figures", "What to do", "How to do it", "What it is worth"], frows)

    notes = [["Note", n] for n in report.get("notes") or []] + [["Part that could not be read", w] for w in report.get("warnings") or []]
    if report.get("stale"):
        notes.append(["Saved read", "Part of this read is the one saved on %s because the platform would not answer now." % _when(report["stale"])])
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
    fills = {"High": "FFD9D6", "Medium": "FFF1CC", "Low": "E1ECFA", "For information": "ECEFF5", "Move together": "DDF3E2", "Move in opposite directions": "FFF1CC"}
    for name, (header, rows) in tables(report).items():
        ws = wb.create_sheet(name)
        ws.append([plain(h) for h in header])
        for c in ws[1]:
            c.fill, c.font, c.alignment = head_fill, head_font, wrap
        for r in rows:
            ws.append([plain(v) for v in r])
        tint_cols = [i for i, h in enumerate(header) if h in ("Level", "Reads as", "Kind")]
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
    tint = {"High": "#FFD9D6", "Medium": "#FFF1CC", "Low": "#E1ECFA", "For information": "#ECEFF5"}

    def P(t, st=base):
        return Paragraph(text_markup(str(t if t is not None else "")), st)

    def table(header, rows, shade_col=None, weights=None):
        n = len(header)
        weights = weights or [max(1.0, min(4.0, max([len(str(header[i]))] + [min(len(str(r[i])), 200) / 12.0 for r in rows if i < len(r)] or [1]) / 6.0)) for i in range(n)]
        cw = [width * w / sum(weights) for w in weights]
        data = [[Paragraph(text_markup(h), head) for h in header]] + [[P(str(plain(c))[:CELL_CHARS]) for c in r] for r in rows]
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
    rng = report.get("range") or {}
    story = [P("Cross-Channel & Attribution Analysis", h1), P(name, h2),
             P("%s, %s to %s | read on %s | report made on %s" % (rng.get("label"), rng.get("start"), rng.get("end"), _when(report.get("readAt")), (report.get("generatedAt") or "")[:10]), small), Spacer(1, 6)]
    h, rows = all_tables["Summary"]
    story += [P("Summary", h2), table(h, rows)]
    h, rows = all_tables["Platforms"]
    short = [[r[0], r[2], r[5], _r(r[7]), r[8], r[12], r[13], r[14], r[18], r[19], r[20]] for r in rows]
    story += [P("The platforms side by side", h2), table(["Platform", "Spend", "Conversions", "Cost per conversion", "Return on ad spend", "Share of spend %", "Share of conversions %", "Share of value %",
                                                        "Spend change %", "Conversions change %", "Cost per conversion change %"], short)]
    if "Findings" in all_tables:
        h, rows = all_tables["Findings"]
        story += [PageBreak(), P("Findings", h2), table(h, rows, shade_col=0, weights=[0.9, 2.2, 6.0, 2.2, 3.0])]
    if "Cost change" in all_tables:
        h, rows = all_tables["Cost change"]
        story += [P("What moved the blended cost per conversion", h2), table(h, rows, weights=[4.0, 1.5])]
    if "Reconciliation" in all_tables:
        h, rows = all_tables["Reconciliation"]
        story += [P("Against your own numbers", h2), table(h, rows, weights=[4.0, 1.5])]
    if "Campaign types" in all_tables:
        h, rows = all_tables["Campaign types"]
        story += [PageBreak(), P("Campaign types", h2), table(h, rows[:PDF_TYPES])]
        if len(rows) > PDF_TYPES:
            story += [P("The %d campaign types that spent most are listed here. The Excel file has every one." % PDF_TYPES, small)]
    if "Attribution" in all_tables:
        h, rows = all_tables["Attribution"]
        story += [PageBreak(), P("How each platform counts a conversion", h2), table(h, rows, weights=[1.0, 1.8, 2.4, 6.0])]
    if "Notes" in all_tables:
        h, rows = all_tables["Notes"]
        story += [P("Notes", h2), table(h, rows, weights=[1.5, 8.0])]
    out = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(BODY, 7)
        canvas.setFillColor(mute)
        canvas.drawString(14 * mm, 8 * mm, "Position2: %s. Cross-Channel & Attribution Analysis" % clean(name, 70))
        canvas.drawRightString(page[0] - 14 * mm, 8 * mm, "Page %d" % doc.page)
        canvas.restoreState()

    doc = SimpleDocTemplate(out, pagesize=page, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=14 * mm,
                            title="Cross-Channel & Attribution Analysis: %s" % name, author="Position2")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
