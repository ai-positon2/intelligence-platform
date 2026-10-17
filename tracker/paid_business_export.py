"""Paid Marketing Agents: Business Research, the downloads.

One report, three files, all drawn from `tables(rec)` so they cannot drift apart:

  Excel   every table as a sheet
  CSV     every table as its own CSV in a zip (a single CSV is the questions for the client, the one table a person pastes into an email)
  PDF     the report as a document to read and to send

A value that a spreadsheet would run as a formula (it starts with = + - @) is written as plain text in every format (tracker/paid_strategy_export.py).
"""
from __future__ import annotations

import io
import re
import zipfile

from . import paid_strategy as ps
from .paid_strategy_check import text as clean
from .paid_strategy_export import CELL_CHARS, SHEET_NAME_BAD, _csv_text, _join, plain


def file_stem(rec):
    base = re.sub(r"[^A-Za-z0-9]+", "-", rec.get("name") or "client").strip("-")[:50] or "client"
    return "%s-business-research" % base


def _seen(ok):
    return "Yes" if ok else "Not found on a page"


def tables(rec):
    """{sheet name: (header, rows)} in the order they are shown. Text only; empty tables are left out."""
    plan = rec.get("plan") or {}
    inputs = rec.get("inputs") or {}
    research = rec.get("research") or {}
    core, aud, imp = plan.get("core") or {}, plan.get("audience") or {}, plan.get("implications") or {}
    out = {}

    ov = [["Client", inputs.get("name")], ["Website", inputs.get("website")], ["Industry", inputs.get("industry")], ["Goal of the advertising", ps.GOALS.get(inputs.get("goal"), inputs.get("goal"))],
          ["Places", inputs.get("locations")], ["Google Ads account", rec.get("account_name") or rec.get("account_id")], ["Built by", rec.get("created_by")],
          ["Built on", (plan.get("generatedAt") or "")[:10]]]
    ov += [["In short", s] for s in imp.get("summary") or []]
    out["Overview"] = (["Item", "Detail"], [r for r in ov if r[1]])

    pos = core.get("positioning") or {}
    rows = [["Summary", core.get("summary")]] + [[r["item"], r["detail"]] for r in core.get("snapshot") or []]
    rows += [["Promise", pos.get("promise")], ["Tone", pos.get("tone")], ["Words it uses", _join(pos.get("wordsUsed"))], ["Words to avoid", _join(pos.get("wordsToAvoid"))]]
    rows += [["Assumption", a] for a in core.get("assumptions") or []]
    out["Business"] = (["Item", "Detail"], [r for r in rows if r[1]])

    if core.get("offerings"):
        out["Offerings"] = (["Offering", "What it is", "Who it is for", "Price on the page", "Page", "Priority for paid search", "Why"],
                            [[o["name"], o["description"], o["audience"], o["pricing"], o["page"], o["priority"], o["why"]] for o in core["offerings"]])
    rows = [["USP", u["usp"], u["why_it_matters"], u["strength"], '"%s"' % u["evidence"] if u["evidence"] else "", u["source"],
             "" if not u["evidence"] else ("Yes" if u["verified"] else "Only in what the web search found" if u.get("viaSearch") else "Not found on a page")]
            for u in core.get("usps") or []]
    rows += [["Proof", p["point"], "", "", "", p["source"], ""] for p in core.get("proof") or []]
    if rows:
        out["USPs and proof"] = (["Kind", "Point", "Why it matters", "Strength", "Evidence quoted", "Where", "Quote found on a page"], rows)
    if aud.get("audiences"):
        out["Audiences"] = (["Audience", "Who", "Their situation", "What they want", "What worries them", "What they search", "Priority"],
                            [[a["name"], a["who"], a["situation"], a["goal"], a["worries"], a["searches"], a["priority"]] for a in aud["audiences"]])
    rows = [["Step", j["stage"], j["what_happens"], j["friction"], j["opportunity"]] for j in aud.get("journey") or []]
    rows += [["Trigger", t, "", "", ""] for t in aud.get("triggers") or []]
    rows += [["Objection", o["objection"], o["answer"], "", o["proof"]] for o in aud.get("objections") or []]
    if rows:
        out["Journey and objections"] = (["Kind", "Item", "What happens or the answer", "Friction", "Opportunity or proof"], rows)
    voice, geo = aud.get("voice") or {}, aud.get("geography") or {}
    rows = [["Writing style", voice.get("style")], ["Words customers use", _join(voice.get("vocabulary"))], ["Keep out", _join(voice.get("avoid"))], ["Where customers come from", geo.get("summary")]]
    rows += [["Area: " + a["area"], a["note"]] for a in geo.get("areas") or []] + [["Season: " + s["window"], s["why"]] for s in aud.get("seasonality") or []]
    if any(r[1] for r in rows):
        out["Voice, places and seasons"] = (["Item", "Detail"], [r for r in rows if r[1]])
    if core.get("compliance"):
        out["Rules that touch the ads"] = (["Topic", "Rule", "What it means here"], [[c["topic"], c["rule"], c["impact"]] for c in core["compliance"]])
    if core.get("facts"):
        out["Facts the ads may state"] = (["Fact", "Quoted from the page", "Page", "Quote found on a page"], [[f["fact"], '"%s"' % f["quote"], f["source"], _seen(f["verified"])] for f in core["facts"]])
    if core.get("siteGaps"):
        out["What the site gets wrong"] = (["Issue", "Why it matters", "Fix"], [[g["issue"], g["why"], g["fix"]] for g in core["siteGaps"]])

    rep = (research.get("reputation") or {}).get("data") or {}
    imp_rep = imp.get("reputation") or {}
    rows = [["Rating", "%s: %s stars from %s reviews%s" % (r["platform"], r["rating"] or "?", r["count"] or "?", "" if r.get("confirmed") else " (no page the search read confirms this)"), r["url"]]
            for r in rep.get("ratings") or []]
    rows += [["Customers praise", x, ""] for x in rep.get("praise") or []] + [["Customers complain about", x, ""] for x in rep.get("complaints") or []]
    rows += [["Customers ask", x, ""] for x in rep.get("questions") or []]
    rows += [["Press", "%s (%s, %s)" % (p["headline"], p["source"], p["when"]), ""] for p in rep.get("press") or []] + [["Award", x, ""] for x in rep.get("awards") or []]
    rows += [["Person", "%s, %s" % (p["name"], p["role"]), ""] for p in rep.get("people") or []] + [["Company: " + k, v, ""] for k, v in (rep.get("company") or {}).items()]
    rows += [["Read of it", imp_rep.get("summary"), ""]] + [["Lean on", x, ""] for x in imp_rep.get("strengths") or []] + [["Could hurt", x, ""] for x in imp_rep.get("risks") or []]
    rows += [["Do", x, ""] for x in imp_rep.get("actions") or []] + [["Source", u, u] for u in rep.get("sources") or []]
    rows = [r for r in rows if r[1]]
    if rows:
        out["Reputation"] = (["Kind", "Finding", "Page"], rows)

    tech = plan.get("tech") or {}
    rows = [["Seen on the site", t["name"], t["category"], ", ".join(t["ids"]), "%d page%s" % (t["pages"], "" if t["pages"] == 1 else "s")] for t in tech.get("tools") or []]
    rows += [["Not seen", g, "", "", ""] for g in tech.get("gaps") or []]
    rows += [["To check", t["item"], t["seen"], t["action"], ""] for t in imp.get("tracking") or []]
    if rows:
        out["Tracking and set-up"] = (["Kind", "Item", "Category or what was seen", "Ids or what to do", "Pages"], rows)

    rows = [["Advertise first", a["what"], a["why"], a["page"], a["evidence"]] for a in imp.get("advertiseFirst") or []]
    rows += [["Hold back", h["what"], h["why"], "", ""] for h in imp.get("holdBack") or []]
    rows += [["Angle to test", a["angle"], "For %s. Rests on: %s" % (a["audience"], a["proof"]), "", a["example"]] for a in imp.get("angles") or []]
    rows += [["Test", t["hypothesis"], t["how"], "", "Metric: %s" % t["metric"]] for t in imp.get("tests") or []]
    if rows:
        out["What it means for paid media"] = (["Kind", "What", "Why", "Page", "Evidence or example"], rows)

    comp = imp.get("competitive") or {}
    read = [c for c in ((research.get("competitors") or {}).get("data") or {}).get("competitors") or [] if c.get("ok")]
    rows = [["Read of it", comp.get("summary")]] + [["Where it stands apart", x] for x in comp.get("edge") or []] + [["Where it is exposed", x] for x in comp.get("exposed") or []]
    rows += [["Site read", "%s (%s)" % (c["name"], c["url"])] for c in read]
    rows = [r for r in rows if r[1]]
    if rows:
        out["Competitors"] = (["Kind", "Finding"], rows)
    if imp.get("social"):
        out["Social media"] = (["What the brand's own posts show", "How to use it in the ads"], [[s["insight"], s["use"]] for s in imp["social"]])

    if imp.get("questions"):
        out["Questions for the client"] = (["Area", "Question", "Why it matters", "When"], [[q["area"], q["question"], q["why"], q["urgency"]] for q in imp["questions"]])
    rows = [["Risk", r["risk"], r["mitigation"], ""] for r in imp.get("risks") or []] + [["Next step", s["step"], s["owner"], s["when"]] for s in imp.get("nextSteps") or []]
    if rows:
        out["Risks and next steps"] = (["Kind", "Item", "What to do or who", "When"], rows)
    if plan.get("claims"):
        out["Claims to confirm"] = (["Where", "Text", "Claim not found on the client's pages or in what the search found"], [[c["where"], c["text"], c["claim"]] for c in plan["claims"]])
    chk = plan.get("checks") or {}
    rows = [["Not available", s] for s in chk.get("missing") or []]
    if chk.get("unconfirmedFacts"):
        rows.append(["Facts not found on a page", str(chk["unconfirmedFacts"])])
    if chk.get("unconfirmedUsps"):
        rows.append(["Points of difference whose quote was not found on a page", str(chk["unconfirmedUsps"])])
    if rows:
        out["Checks"] = (["Check", "Detail"], rows)
    return out


def xlsx(rec):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    wb.remove(wb.active)
    head_fill, head_font, wrap = PatternFill("solid", fgColor="1F2A44"), Font(bold=True, color="FFFFFF"), Alignment(wrap_text=True, vertical="top")
    used = set()
    for name, (header, rows) in tables(rec).items():
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
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, str):
                    c.data_type = "s"
                c.alignment = wrap
        for i in range(1, len(header) + 1):
            longest = max([len(str(header[i - 1]))] + [len(str(r[i - 1])) for r in rows if i - 1 < len(r) and r[i - 1] is not None] or [10])
            ws.column_dimensions[get_column_letter(i)].width = min(60, max(12, longest + 2))
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def csv_questions(rec):
    """The questions for the client as one CSV, or None when there are none."""
    tab = tables(rec).get("Questions for the client")
    return _csv_text(*tab) if tab else None


def csv_zip(rec):
    out = io.BytesIO()
    used = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for sheet, (header, rows) in tables(rec).items():
            stem = re.sub(r"[^A-Za-z0-9]+", "-", sheet).strip("-").lower() or "table"
            cand, n = stem, 2
            while cand in used:
                cand, n = "%s-%d" % (stem, n), n + 1
            used.add(cand)
            z.writestr(cand + ".csv", _csv_text(header, rows))
    return out.getvalue()


def pdf(rec):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from .event_intel_pdf import BODY, BOLD, _fonts, text_markup
    _fonts()
    inputs = rec.get("inputs") or {}
    ink, mute, rule = colors.HexColor("#1B2333"), colors.HexColor("#5B6577"), colors.HexColor("#D7DDE8")
    base = ParagraphStyle("b", fontName=BODY, fontSize=8.6, leading=11.8, textColor=ink)
    small = ParagraphStyle("s", parent=base, fontSize=7.6, leading=10, textColor=mute)
    h1 = ParagraphStyle("h1", parent=base, fontName=BOLD, fontSize=17, leading=21, spaceBefore=4, spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=base, fontName=BOLD, fontSize=12, leading=15, spaceBefore=10, spaceAfter=4)
    head = ParagraphStyle("th", parent=base, fontName=BOLD, textColor=colors.white, fontSize=8)
    page = landscape(A4)
    width = page[0] - 28 * mm

    def P(t, st=base):
        return Paragraph(text_markup(str(t if t is not None else "")), st)

    def table(header, rows):
        n = len(header)
        weights = [max(1.0, min(4.0, max([len(str(header[i]))] + [min(len(str(r[i])), 200) / 12.0 for r in rows if i < len(r)] or [1]) / 6.0)) for i in range(n)]
        cw = [width * w / sum(weights) for w in weights]
        data = [[Paragraph(text_markup(h), head) for h in header]] + [[P(str(c)[:CELL_CHARS]) for c in r] for r in rows]
        t = Table(data, colWidths=cw, repeatRows=1, splitInRow=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2A44")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6FA")]), ("LINEBELOW", (0, 0), (-1, -1), 0.25, rule),
                               ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        return t

    tb = tables(rec)
    story = [P("Business Research", h1), P(inputs.get("name"), h2), P("%s | %s | goal of the advertising: %s" % (inputs.get("website") or "no website", inputs.get("industry") or "",
                                                                                                           ps.GOALS.get(inputs.get("goal"), "")), small), Spacer(1, 6)]
    for name, (header, rows) in tb.items():
        story += [PageBreak() if name in ("Business", "Audiences", "Reputation", "What it means for paid media", "Questions for the client") else Spacer(1, 4), P(name, h2), table(header, rows)]
    out = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(BODY, 7)
        canvas.setFillColor(mute)
        canvas.drawString(14 * mm, 8 * mm, "Position2: %s. Business Research" % clean(inputs.get("name"), 60))
        canvas.drawRightString(page[0] - 14 * mm, 8 * mm, "Page %d" % doc.page)
        canvas.restoreState()

    doc = SimpleDocTemplate(out, pagesize=page, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=14 * mm,
                            title="Business Research: %s" % inputs.get("name"), author="Position2")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
