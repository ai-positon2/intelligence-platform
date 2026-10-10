"""The report as a PDF: the drawer's own HTML, laid out by reportlab.

The page builds every report in the browser, from the run, with several
thousand lines of rendering code. A second, Python copy of that code would
drift from it the first time a section changed, and the PDF would quietly
miss whatever it had not caught up with. So the page renders the report once
with everything showing (every row's details, every folded reason, no filter)
and sends that HTML here; this module turns it into flowing, paginated text.

What it promises, and a test holds it to: every piece of text a reader sees in
that report reaches the PDF. Nothing is drawn at a fixed size, so nothing can
be cut: text wraps, long words and links break, tables split across pages and
repeat their header, and a block too tall for one page simply continues on
the next.

What it leaves out on purpose, all of them controls rather than content: the
filter chips, the decision and action buttons, the per-section feedback
thumbs, the row "Details" toggles (their details are printed open), the
calendar's rank markers (each event is listed with its dates elsewhere), the
initials avatars beside names, the "i" and "!" badges on callouts, and
screen-reader-only text.
"""

from __future__ import annotations

import html as _html
import io
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Flowable, HRFlowable, KeepTogether, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)
from reportlab.platypus.doctemplate import LayoutError

MAX_HTML = 8 * 1024 * 1024

# ── fonts ─────────────────────────────────────────────────────────────────
# DejaVu Sans covers Latin with every accent, Greek and Cyrillic. Helvetica,
# the default, covers Latin-1 only, so "Łukasz", "Şahin" and "Яндекс" would
# have printed as empty boxes. Chinese, Japanese and Korean use reportlab's
# built-in CID fonts, which need no font file.
_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
_FONTS_READY = False
_CMAP: set = set()
BODY, BOLD, ITAL, BOLDITAL = "EviSans", "EviSans-Bold", "EviSans-Oblique", "EviSans-BoldOblique"
_CJK = {"zh": "STSong-Light", "ja": "HeiseiKakuGo-W5", "ko": "HYGothic-Medium"}


def _fonts():
    global _FONTS_READY, _CMAP
    if _FONTS_READY:
        return
    files = {BODY: "DejaVuSans.ttf", BOLD: "DejaVuSans-Bold.ttf",
             ITAL: "DejaVuSans-Oblique.ttf", BOLDITAL: "DejaVuSans-BoldOblique.ttf"}
    for name, fn in files.items():
        font = TTFont(name, os.path.join(_FONT_DIR, fn))
        pdfmetrics.registerFont(font)
        if name == BODY:
            _CMAP = set(font.face.charToGlyph.keys())
    pdfmetrics.registerFontFamily(BODY, normal=BODY, bold=BOLD, italic=ITAL, boldItalic=BOLDITAL)
    for cid in _CJK.values():
        pdfmetrics.registerFont(UnicodeCIDFont(cid))
    _FONTS_READY = True


def _script_of(ch: str) -> str | None:
    o = ord(ch)
    if 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF:
        return "ja"
    if 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
        return "ko"
    if (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF
            or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF):
        return "zh"
    return None


def text_markup(text: str) -> str:
    """Plain text as Paragraph markup: escaped, CJK runs in a CJK font, and
    characters no font here can draw (emoji, mostly) dropped rather than
    printed as boxes."""
    out, run, run_script = [], [], None

    def flush():
        if run:
            s = _html.escape("".join(run), quote=False)
            out.append('<font name="%s">%s</font>' % (_CJK[run_script], s) if run_script else s)
            run.clear()

    for ch in text:
        sc = _script_of(ch)
        if sc is None and ord(ch) not in _CMAP and not ch.isspace():
            continue
        if sc != run_script:
            flush()
            run_script = sc
        run.append(ch)
    flush()
    return "".join(out)


# ── a small DOM ───────────────────────────────────────────────────────────
VOID = {"br", "img", "hr", "input", "meta", "link", "wbr", "area", "base", "col",
        "embed", "source", "track"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs or {}), [], parent

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, [(k, v if v is not None else "") for k, v in attrs], self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, [(k, v if v is not None else "") for k, v in attrs], self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(markup: str) -> Node:
    p = _Parser()
    p.feed(markup)
    p.close()
    return p.root


# ── what is content ───────────────────────────────────────────────────────
DROP_TAGS = {"script", "style", "svg", "input", "select", "textarea", "template",
             "noscript", "img", "canvas", "video", "audio", "iframe", "object"}
# Controls, never content. Kept in one place, and each is named in the
# module docstring.
SKIP_CLASSES = {"evi-row-more", "dbtn", "evi-sr", "evi-av", "mk", "cx", "thinking-orb-slot",
                # The round "i" and "!" badges on callouts: a glyph, not a word.
                "ic"}
SKIP_BUTTON_CLASSES = {"evi-chip", "evi-btn", "evi-x"}


def skipped(node: Node) -> bool:
    if node.tag in DROP_TAGS:
        return True
    a = node.attrs
    if "hidden" in a or re.search(r"display\s*:\s*none", a.get("style", ""), re.I):
        return True
    if a.get("aria-hidden") == "true" and not node.text().strip():
        return True
    cls = node.classes
    if cls & SKIP_CLASSES or any(c.startswith("agent-fb") for c in cls):
        return True
    if node.tag == "button" and cls & SKIP_BUTTON_CLASSES:
        return True
    return False


def visible_text(node: Node) -> str:
    """Every word a reader of the exported report sees, by the same rules the
    layout uses. The completeness test compares the PDF against this."""
    if isinstance(node, str):
        return node
    if node is not None and node.tag != "root" and skipped(node):
        return " "
    # Every element boundary separates words here. That can only split a
    # word, never join two, so a check built on it can miss nothing.
    return " " + "".join(visible_text(c) for c in node.children) + " "


BLOCK = {"div", "p", "section", "article", "header", "footer", "main", "aside", "nav",
         "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead",
         "tbody", "tfoot", "tr", "td", "th", "details", "summary", "blockquote", "dl",
         "dt", "dd", "figure", "figcaption", "hr", "form", "fieldset", "pre", "button",
         "caption"}
LAYOUT_TAGS = {"span", "a", "label", "b", "strong", "i", "em", "small"}


def _has_loose_text(node: Node) -> bool:
    return any(isinstance(c, str) and c.strip() for c in node.children)


def _is_stat(p: Node, kids: list) -> bool:
    """A figure and its label ("10" + "Found across the 6 categories"): one
    line, not three."""
    if not 2 <= len(kids) <= 4:
        return False
    texts = [visible_text(k).strip() for k in kids]
    flat = all(not any(isinstance(g, Node) and g.tag in BLOCK and not skipped(g) for g in k.children)
               for k in kids)
    return (bool(re.match(r"^[\d$\u20ac\u00a3+~<>]", texts[0])) and len(texts[0]) <= 14
            and all(len(t) <= 60 for t in texts) and flat
            and not any(k.tag in ("table", "ul", "ol", "dl", "h1", "h2", "h3", "h4", "h5", "h6", "p")
                        for k in kids))


def is_block(node: Node) -> bool:
    if node.tag in BLOCK:
        return True
    # A "layout span": one of several classed pieces a card is built from,
    # laid out as lines by CSS. Run together they read "1Pharma Forum90/110".
    p = node.parent
    if node.tag in ("span", "a", "label") and node.attrs.get("class") and p is not None:
        kids = [c for c in p.children if isinstance(c, Node) and not skipped(c)]
        if len(kids) >= 2 and not _has_loose_text(p) and not _is_stat(p, kids) and all(
                (k.tag in BLOCK) or (k.tag in LAYOUT_TAGS and k.attrs.get("class")) for k in kids):
            return True
    if node.tag in ("span", "a", "label"):
        return any(isinstance(c, Node) and not skipped(c) and is_block(c) for c in node.children)
    return False


# ── styles ────────────────────────────────────────────────────────────────
INK, INK2, INK3 = colors.HexColor("#10162b"), colors.HexColor("#3c4770"), colors.HexColor("#5d6789")
ACCENT, LINE = colors.HexColor("#4f46e5"), colors.HexColor("#d9deef")


def _styles():
    base = ParagraphStyle("body", fontName=BODY, fontSize=9.4, leading=13.2, textColor=INK,
                          alignment=TA_LEFT, spaceAfter=3, splitLongWords=1)
    return {
        "body": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8.2, leading=11.4, textColor=INK2),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.2, leading=11, spaceAfter=1),
        "head": ParagraphStyle("head", parent=base, fontName=BOLD, fontSize=8.2, leading=11,
                               textColor=INK2, spaceAfter=0),
        "h1": ParagraphStyle("h1", parent=base, fontName=BOLD, fontSize=21, leading=25, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base, fontName=BOLD, fontSize=14.5, leading=18,
                             spaceBefore=12, spaceAfter=6, textColor=INK),
        "h3": ParagraphStyle("h3", parent=base, fontName=BOLD, fontSize=12, leading=15.5,
                             spaceBefore=14, spaceAfter=6, textColor=ACCENT),
        "h4": ParagraphStyle("h4", parent=base, fontName=BOLD, fontSize=10.4, leading=14,
                             spaceBefore=6, spaceAfter=3),
        "quote": ParagraphStyle("quote", parent=base, fontName=ITAL, leftIndent=10,
                                textColor=INK2),
        "li": ParagraphStyle("li", parent=base, leftIndent=12, bulletIndent=2),
        "sub": ParagraphStyle("sub", parent=base, fontSize=10.5, leading=14.5, textColor=INK2,
                              spaceAfter=2),
        "meta": ParagraphStyle("meta", parent=base, fontSize=8, leading=11, textColor=INK3),
    }


CARD_CLASSES = {"evi-cand", "evi-out", "evi-ev", "asc", "evi-hero-ev", "evi-exec", "evi-answer",
                "evi-coverage", "evi-err", "at", "evi-psum", "evi-anat", "evi-att-spot",
                "evi-att-mix", "evi-window", "evi-classplay"}


# ── inline markup ─────────────────────────────────────────────────────────
def _safe_href(href: str) -> str | None:
    href = (href or "").strip()
    return href if re.match(r"^(https?://|mailto:)", href, re.I) and len(href) < 2000 else None


def inline(node, out: list):
    """Paragraph markup for an inline node, appended to `out`."""
    if isinstance(node, str):
        out.append(text_markup(re.sub(r"\s+", " ", node)))
        return
    if skipped(node):
        return
    tag = node.tag
    if tag == "br":
        out.append("<br/>")
        return
    open_, close = "", ""
    if tag in ("b", "strong"):
        open_, close = "<b>", "</b>"
    elif tag in ("i", "em", "q", "cite"):
        open_, close = "<i>", "</i>"
    elif tag == "small":
        open_, close = '<font size="7.6">', "</font>"
    elif tag == "a":
        href = _safe_href(node.attrs.get("href"))
        if href:
            open_, close = '<a href="%s" color="#3554b5">' % _html.escape(href, quote=True), "</a>"
    out.append(open_)
    for i, c in enumerate(node.children):
        if i and needs_space(out, c) and (isinstance(c, Node) or isinstance(node.children[i - 1], Node)):
            out.append(" ")
        inline(c, out)
    out.append(close)


def _plain_tail(parts: list) -> str:
    return re.sub(r"<[^>]+>", "", "".join(parts[-4:]))[-1:]


def needs_space(parts: list, node) -> bool:
    """Two pieces drawn side by side by CSS ("1" and "Not measured") need a
    space once they are one line of text; "90" and "/110" do not."""
    tail = _plain_tail(parts)
    head = (visible_text(node) if isinstance(node, Node) else node).lstrip()[:1]
    return bool(tail) and not tail.isspace() and bool(head) and (head.isalnum() or head in "(\u00b7")


def _para(parts: list, style) -> Paragraph | None:
    markup = re.sub(r"\s+", " ", "".join(parts)).strip()
    # A run of only tags, or only <br/>, is not a line.
    if not re.sub(r"<[^>]+>", "", markup).strip():
        return None
    markup = re.sub(r"^(<br/>\s*)+|(\s*<br/>)+$", "", markup)
    return Paragraph(markup, style)


# ── blocks ────────────────────────────────────────────────────────────────
class Converter:
    def __init__(self, width):
        self.width = width
        self.st = _styles()

    def style_for(self, node: Node, inherited):
        tag, cls = node.tag, node.classes
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if inherited in (self.st["cell"], self.st["head"]):
                return self.st["head"]
            return self.st[{"h1": "h2", "h2": "h2", "h3": "h3"}.get(tag, "h4")]
        if tag == "blockquote" or cls & {"ass", "oo"} and tag == "p":
            return self.st["quote"]
        if tag == "summary":
            return self.st["h4"]
        if tag in ("th",):
            return self.st["head"]
        if cls & {"hint", "sub", "evi-dom", "muted", "note", "fn", "xfoot", "pg-note"}:
            return self.st["small"] if inherited is not self.st["cell"] else inherited
        return inherited

    def blocks(self, node: Node, style) -> list:
        """Flowables for a node's children."""
        flow, run = [], []

        def flush():
            p = _para(run, style)
            if p is not None:
                flow.append(p)
            run.clear()

        prev_el = False
        for c in node.children:
            if isinstance(c, str):
                if prev_el and run and needs_space(run, c):
                    run.append(" ")
                run.append(text_markup(re.sub(r"\s+", " ", c)))
                prev_el = False
                continue
            prev_el = True
            if skipped(c):
                continue
            if is_block(c):
                flush()
                flow.extend(self.block(c, style))
            else:
                if run and needs_space(run, c):
                    run.append(" ")
                inline(c, run)
        flush()
        return flow

    def block(self, node: Node, inherited) -> list:
        tag, cls = node.tag, node.classes
        style = self.style_for(node, inherited)
        if tag == "hr":
            return [HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=4, spaceAfter=4)]
        if tag == "table":
            return self.table(node)
        if tag == "dl":
            # "Country: Germany" on one line, not a label line and a value line.
            out, label = [], None
            items = [c for c in node.children if isinstance(c, Node) and not skipped(c)]
            for it in items:
                if it.tag == "dt":
                    label = []
                    inline(it, label)
                elif it.tag == "dd":
                    parts = ["<b>%s:</b> " % "".join(label).strip()] if label else []
                    for c in it.children:
                        if isinstance(c, Node) and is_block(c):
                            parts.append(" ")
                            inline(c, parts)
                        else:
                            if isinstance(c, Node) and needs_space(parts, c):
                                parts.append(" ")
                            inline(c, parts)
                    p = _para(parts, style)
                    if p is not None:
                        out.append(p)
                    label = None
                elif it.tag == "div":
                    out.extend(self.block(it, style))
            if label:
                p = _para(label, style)
                if p is not None:
                    out.append(p)
            return out
        if tag in ("ul", "ol"):
            out = []
            for i, li in enumerate(c for c in node.children if isinstance(c, Node) and not skipped(c)):
                bullet = "%d." % (i + 1) if tag == "ol" else "•"
                inner = self.blocks(li, self.st["li"])
                if inner and isinstance(inner[0], Paragraph):
                    inner[0] = Paragraph(inner[0].text, self.st["li"], bulletText=bullet)
                out.extend(inner)
            return out
        kids = [c for c in node.children if isinstance(c, Node) and not skipped(c)]
        if tag in ("div", "span", "a", "button") and kids and not _has_loose_text(node) and _is_stat(node, kids):
            parts = []
            for k in kids:
                if parts:
                    parts.append(" ")
                inline(k, parts)
            p = _para(parts, style)
            return [p] if p is not None else []
        inner = self.blocks(node, style)
        if not inner:
            return []
        if cls & CARD_CLASSES:
            card = [HRFlowable(width="100%", thickness=0.8, color=LINE, spaceBefore=6, spaceAfter=5)] + inner
            # Only a short card is kept on one page; a long one moved whole
            # left most of a page blank.
            return [KeepTogether(card)] if len(inner) <= 6 else card
        return inner

    def table(self, node: Node) -> list:
        rows = []
        def collect(n):
            for c in n.children:
                if isinstance(c, Node) and not skipped(c):
                    if c.tag == "tr":
                        rows.append(c)
                    elif c.tag in ("thead", "tbody", "tfoot"):
                        collect(c)
        collect(node)
        if not rows:
            return []
        cells = [[c for c in r.children if isinstance(c, Node) and c.tag in ("td", "th") and not skipped(c)]
                 for r in rows]
        ncols = max((sum(int(c.attrs.get("colspan") or 1) for c in r) for r in cells), default=1)
        if ncols < 1:
            return []
        # Column widths from what is in them, so a long name gets the room
        # and a two-letter role does not.
        weight = [6.0] * ncols
        for r in cells:
            ci = 0
            for c in r:
                span = int(c.attrs.get("colspan") or 1)
                if span == 1 and ci < ncols:
                    weight[ci] = max(weight[ci], min(len(visible_text(c).strip()), 70))
                ci += span
        total = sum(weight)
        widths = [max(self.width * w / total, 34) for w in weight]
        scale = self.width / sum(widths)
        widths = [w * scale for w in widths]

        data, spans, header_rows = [], [], 0
        for ri, r in enumerate(cells):
            row, ci = [], 0
            for c in r:
                span = int(c.attrs.get("colspan") or 1)
                st = self.st["head"] if c.tag == "th" else self.st["cell"]
                inner = self.blocks(c, st) or [Paragraph("", st)]
                row.append(inner)
                for _ in range(span - 1):
                    row.append("")
                if span > 1:
                    spans.append(("SPAN", (ci, ri), (min(ci + span - 1, ncols - 1), ri)))
                ci += span
            row += [""] * (ncols - len(row))
            data.append(row[:ncols])
            if ri == header_rows and all(c.tag == "th" for c in r):
                header_rows += 1
        t = Table(data, colWidths=widths, repeatRows=header_rows, splitByRow=1)
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ] + ([("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#eef1fb"))] if header_rows else [])
          + spans))
        return [Spacer(1, 4), t, Spacer(1, 6)]


class _Numbered:
    """Page footer: what this is and which page of how many."""

    def __init__(self, title, product=None):
        self.title = title
        self.product = product or PRODUCT

    def make(self):
        title, product = self.title, self.product

        from reportlab.pdfgen.canvas import Canvas

        class NumberedCanvas(Canvas):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self._pages = []

            def showPage(self):
                self._pages.append(dict(self.__dict__))
                self._startPage()

            def save(self):
                n = len(self._pages)
                for state in self._pages:
                    self.__dict__.update(state)
                    self.setFont(BODY, 7.5)
                    self.setFillColor(INK3)
                    w, _h = A4
                    self.drawString(16 * mm, 9 * mm, (product + " · " + title)[:110])
                    self.drawRightString(w - 16 * mm, 9 * mm, "Page %d of %d" % (self._pageNumber, n))
                    super().showPage()
                super().save()

        return NumberedCanvas


PRODUCT = "Event & Conference Intelligence"


def build(report_html: str, title: str, subtitle: str = "", generated: datetime | None = None,
          _tables_as_lists: bool = False, product: str = PRODUCT) -> bytes:
    """The PDF for one report. Raises ValueError on input it will not take."""
    if not isinstance(report_html, str) or not report_html.strip():
        raise ValueError("The report was empty.")
    if len(report_html) > MAX_HTML:
        raise ValueError("The report is too large to export.")
    _fonts()
    margin = 16 * mm
    width = A4[0] - 2 * margin
    conv = Converter(width)
    root = parse(report_html)
    if _tables_as_lists:
        for n in _walk(root):
            if n.tag in ("table", "thead", "tbody", "tfoot", "tr"):
                n.tag = "div"
            elif n.tag in ("td", "th"):
                n.tag = "p"
    when = (generated or datetime.now(timezone.utc)).strftime("%d %b %Y, %H:%M UTC")
    story = [Paragraph(text_markup(title or "Report"), conv.st["h1"])]
    if subtitle:
        story.append(Paragraph(text_markup(subtitle), conv.st["sub"]))
    story.append(Paragraph(text_markup(product + " · exported " + when), conv.st["meta"]))
    story.append(HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceBefore=6, spaceAfter=8))
    story.extend(conv.blocks(root, conv.st["body"]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin,
                            topMargin=15 * mm, bottomMargin=16 * mm,
                            title=title or "Report", author=product)
    try:
        doc.build(story, canvasmaker=_Numbered(title or "Report", product).make())
    except LayoutError:
        # A single table row taller than a page cannot be split by reportlab.
        # Rather than lose it, lay every table out as plain stacked text.
        if _tables_as_lists:
            raise
        return build(report_html, title, subtitle, generated, _tables_as_lists=True,
                     product=product)
    return buf.getvalue()


def _walk(node):
    yield node
    for c in node.children:
        if isinstance(c, Node):
            yield from _walk(c)
