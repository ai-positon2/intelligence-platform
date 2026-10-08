"""The report PDF holds everything the report shows, and nothing is cut.

Each report here is rendered by the page's real script (in node) the way the
export renders it, with every row's details open, then laid out as a PDF and
read back with pdfplumber. "Complete" is checked word by word against the
report's own visible text, so a section the layout drops, a cell that clips
or a glyph that prints as a box fails the build.
"""

import collections
import io
import os
import re
import sys

import pdfplumber
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_charts import (_cand, _lookup, _out, _part, _recommend, _render,  # noqa: E402
                                     _src, _workroom)
from test_event_intel_event_view import page_script  # noqa: E402,F401  (fixture)

from tracker import event_intel_pdf as P

LONG_URL = "https://example.com/" + "a-very-long-path-segment-without-any-break" * 6


def _exported(body):
    """What the page's export sends: details rows shown, folds open."""
    body = re.sub(r'(<tr class="evi-detail-row"[^>]*?) hidden', r"\1", body)
    return body


def _words(text):
    P._fonts()
    text = "".join(ch for ch in text if ch.isspace() or ord(ch) in P._CMAP or P._script_of(ch))
    return re.findall(r"\S+", text.lower())


def _pdf_text(pdf):
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return [pg for pg in doc.pages], "\n".join((pg.extract_text() or "") for pg in doc.pages)


def _missing(html, pdf):
    _pages, text = _pdf_text(pdf)
    have = collections.Counter(_words(text))
    flat = re.sub(r"\s+", "", text.lower())
    out = {}
    for w, n in collections.Counter(_words(P.visible_text(P.parse(html)))).items():
        if have[w] < n and flat.count(w) < n:
            out[w] = n - have[w]
    return out


def _recommend_run():
    kept = [_cand("Pharma Forum", 90, "P1", client_line="Planners pick the software here."),
            _cand("IBTM World", 88, "P1", website=LONG_URL),
            _cand("Société Générale Summit", 76, "P2"),
            _cand("Яндекс Конференция", 72, "P2")]
    return _recommend(kept, counts={"P1": 2, "P2": 2, "kept": 4}, selection={"kept": kept},
                      notes=[{"title": "This shortlist is provisional", "why": "One event was not scored."}])


def _lookup_run():
    parts = [_part(n, r, org_domain=d) for n, r, d in [
        ("Łódź Robotics", "exhibitor", "lodz.example"), ("株式会社リコー", "sponsor", None),
        ("삼성 넥스트", "exhibitor", None), ("Very Long Company Name That Keeps Going Incorporated "
                                            "International Holdings Group", "speaker", "verylong.example"),
        ("Acme", "partner", "acme.example")]]
    return _lookup(parts, [_src("https://e.example/exhibitors", "ok"),
                           _src(LONG_URL, "blocked", note="This page could not be read.")])


def _workroom_run():
    return _workroom([_out("Mastercard", 80, person_name="Dana Lee", opener="Hello Dana, about fleets."),
                      _out("Şahin Pay", 70, opener="Your payouts story caught my eye.")])


RUNS = {"recommend": _recommend_run, "lookup": _lookup_run, "workroom": _workroom_run}


@pytest.mark.parametrize("mode", sorted(RUNS))
def test_every_word_of_the_report_reaches_the_pdf(page_script, mode):
    html = _exported(_render(page_script, RUNS[mode]()))
    pdf = P.build(html, "Report", "subtitle")
    assert _missing(html, pdf) == {}


@pytest.mark.parametrize("mode", sorted(RUNS))
def test_nothing_is_drawn_past_the_page_edge(page_script, mode):
    pdf = P.build(_exported(_render(page_script, RUNS[mode]())), "Report")
    pages, _ = _pdf_text(pdf)
    for pg in pages:
        for ch in pg.chars:
            assert 0 <= ch["x0"] and ch["x1"] <= pg.width - 15, (ch["text"], ch["x1"], pg.width)
            assert 0 <= ch["top"] and ch["bottom"] <= pg.height, ch["text"]


def test_names_in_any_script_print_as_themselves(page_script):
    _pages, text = _pdf_text(P.build(_exported(_render(page_script, _lookup_run())), "Web Summit"))
    for name in ("Łódź Robotics", "株式会社リコー", "삼성 넥스트"):
        assert name in text, name
    _pages, text = _pdf_text(P.build(_exported(_render(page_script, _recommend_run())), "Cvent"))
    assert "Société Générale Summit" in text and "Яндекс Конференция" in text


def test_row_details_are_printed_open(page_script):
    body = _render(page_script, _lookup_run())
    assert 'class="evi-detail-row"' in body and " hidden" in body
    _pages, text = _pdf_text(P.build(_exported(body), "Web Summit"))
    assert "From the event" in text or "From Apollo" in text


def test_controls_are_left_out():
    html = ('<p>Pharma Forum</p><button type="button" class="dbtn">Decided to go</button>'
            '<button type="button" class="evi-row-more">Details</button>'
            '<button type="button" class="evi-chip">All 93</button>'
            '<div class="agent-fb"><span>Was this useful?</span></div>'
            '<span class="evi-av">PF</span><span class="evi-sr">screen reader only</span>'
            '<button type="button" class="at"><span class="an">IBTM World</span><span class="as">88</span></button>')
    _pages, text = _pdf_text(P.build(html, "T"))
    for gone in ("Decided to go", "Details", "All 93", "Was this useful", "PF", "screen reader"):
        assert gone not in text, gone
    # A card that happens to be a button is content.
    assert "IBTM World" in text and "88" in text


def test_a_row_taller_than_a_page_still_prints_in_full():
    cell = "".join("<p>Line %d of a very long detail.</p>" % i for i in range(220))
    html = "<table><tr><th>Name</th><th>Detail</th></tr><tr><td>Acme</td><td>%s</td></tr></table>" % cell
    pdf = P.build(html, "T")
    _pages, text = _pdf_text(pdf)
    assert "Line 0 of" in text and "Line 219 of" in text and "Acme" in text


def test_a_long_table_repeats_its_header_on_every_page():
    rows = "".join("<tr><td>Company %d</td><td>Exhibitor</td></tr>" % i for i in range(400))
    pages, text = _pdf_text(P.build("<table><thead><tr><th>Organisation</th><th>Listed as</th></tr></thead>"
                                    "<tbody>%s</tbody></table>" % rows, "T"))
    assert len(pages) > 3
    assert all("Organisation" in (pg.extract_text() or "") for pg in pages[1:])
    assert all("Company %d" % i in text for i in range(400))


def test_pieces_laid_side_by_side_are_spaced_but_fractions_are_not():
    _pages, text = _pdf_text(P.build(
        '<div class="row"><span class="a">1</span><span class="b">Not measured</span></div>'
        '<p><span class="as"><b>90</b><i>/110</i></span> and categories delivered<i>3 came up short</i></p>',
        "T"))
    assert "1 Not measured" in text and "90/110" in text and "delivered 3 came up short" in text


def test_links_are_kept_only_when_they_are_web_links():
    pdf = P.build('<p><a href="https://e.example/x">Open proof</a> '
                  '<a href="javascript:alert(1)">bad</a></p>', "T")
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        uris = [a.get("uri") for a in doc.pages[0].annots or []]
    assert uris == ["https://e.example/x"]


def test_footer_says_which_page_of_how_many():
    rows = "".join("<p>Paragraph %d</p>" % i for i in range(300))
    pages, _ = _pdf_text(P.build(rows, "Cvent"))
    n = len(pages)
    assert n > 1
    assert "Page 1 of %d" % n in pages[0].extract_text() and "Page %d of %d" % (n, n) in pages[-1].extract_text()


def test_markup_in_the_text_is_printed_not_obeyed():
    _pages, text = _pdf_text(P.build("<p>&lt;b&gt;not bold&lt;/b&gt; &amp; 5 &lt; 6</p>", "A <i> title"))
    assert "<b>not bold</b> & 5 < 6" in text and "A <i> title" in text


# ── what the page sends ─────────────────────────────────────────────────────

def _page_fn(name):
    page = open(os.path.join(os.path.dirname(__file__), "..", "templates",
                             "event_conference_intelligence.html")).read()
    start = page.index("    function %s(" % name)
    return page, page[start:page.index("\n    }\n", start)]


def test_the_export_renders_everything_then_puts_the_view_back():
    _page, fn = _page_fn("exportReportHtml")
    body = fn[fn.index("try {"):fn.index("} finally {")]
    restore = fn[fn.index("} finally {"):]
    # Everything showing: no role filter, every attendee, no format filter.
    assert "activeRoles = null; ATT_FILTER[run.id] = 'all'; FORMAT_FILTER = null;" in fn
    assert ".evi-detail-row[hidden]" in body and "removeAttribute('hidden')" in body
    assert "querySelectorAll('details')" in body and "setAttribute('open', '')" in body
    # And the reader's own view back afterwards, even if rendering threw.
    for put_back in ("activeRoles = saved.roles", "ATT_FILTER[run.id] = saved.att",
                     "FORMAT_FILTER = saved.fmt", "render(run)", "body.scrollTop = saved.scroll"):
        assert put_back in restore, put_back


def test_the_button_is_a_pdf_and_only_on_a_finished_report():
    page, render = _page_fn("render")
    assert re.search(r'<button type="button" id="eventExportLink"[^>]*onclick="downloadPdf\(this\)">Download PDF</button>', page)
    assert "exportLink.hidden = !run.id || run.status !== 'complete';" in render
    _page, dl = _page_fn("downloadPdf")
    assert "BASE + '/runs/' + run.id + '/pdf'" in dl and "exportReportHtml(run)" in dl
