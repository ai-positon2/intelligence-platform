"""Paid Marketing Agents: QA Agent, the campaign book.

Some Campaign Book checks have no fixed answer: the bid strategy, devices, genders,
household income, locations, age and ad schedule "depend on the campaign book", the
client's plan for that campaign. The team adds that book to the QC as an Excel file
(.xlsx) or a Google Sheet link, and the AI checks the live campaign against it.

This reads the book into plain rows (Field | Value | Other things to check, the same
layout as the Campaign Book template) plus a text dump of everything on its sheets,
because a client's book will not always keep that layout. Nothing here trusts the file:
size and decompressed size are capped, only the cell text is read (no macros, links or
formulas are followed), and a Google Sheet is fetched only from docs.google.com and only
over the public-address guard in tracker/event_intel_http.py.
"""
from __future__ import annotations

import base64
import csv
import io
import re
import zipfile

MAX_BYTES = 4_000_000
MAX_UNZIPPED = 40_000_000
MAX_SHEETS = 6
MAX_ROWS = 400
MAX_COLS = 12
MAX_CELL = 600
MAX_TEXT = 14_000
SHEET_ID = re.compile(r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})(?:[/?#]|$)")

# Template phrases that mean "nothing was filled in for this field".
PLACEHOLDERS = ("depends on the campaign book", "depends on the client", "check the age", "from the campaign book", "can be asked back")


class BookError(ValueError):
    """What is wrong with the file or link, in words the page can show."""


def _norm(s):
    s = re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()
    return s.replace("shceduling", "scheduling").replace("aaccount", "account")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return " ".join(str(v).split())[:MAX_CELL]


def _build(filename, source, sheets):
    """sheets: [(name, [[cell, ...], ...])] -> the book."""
    rows, lines = [], []
    for name, grid in sheets[:MAX_SHEETS]:
        header = [_norm(c) for c in (grid[0] if grid else [])]
        has_header = header[:2] == ["field", "value"]
        for r in grid[1 if has_header else 0:MAX_ROWS]:
            cells = [_cell(c) for c in r[:MAX_COLS]]
            if not any(cells):
                continue
            lines.append("%s | %s" % (name, " | ".join(c for c in cells if c)))
            label = cells[0] if cells else ""
            rest = [c for c in cells[1:] if c]
            if label and rest:
                rows.append({"field": label, "value": rest[0], "other": " ".join(rest[1:])[:MAX_CELL]})
    if not lines:
        raise BookError("That file has no text in it.")
    return {"filename": filename[:120], "source": source, "sheets": [n for n, _ in sheets[:MAX_SHEETS]], "rows": rows,
            "text": "\n".join(lines)[:MAX_TEXT]}


def parse_xlsx(data, filename="campaign book.xlsx", source="upload"):
    if len(data) > MAX_BYTES:
        raise BookError("That file is over %d MB." % (MAX_BYTES // 1_000_000))
    if not data[:2] == b"PK":
        raise BookError("That is not an .xlsx file. Save it as Excel Workbook (.xlsx) or paste the Google Sheet link.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
                raise BookError("That workbook is too large to read.")
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        sheets = []
        for ws in wb.worksheets[:MAX_SHEETS]:
            sheets.append((ws.title, [list(r)[:MAX_COLS] for r in ws.iter_rows(max_row=MAX_ROWS, values_only=True)]))
        wb.close()
    except BookError:
        raise
    except Exception as e:                                  # noqa: BLE001 (a corrupt file is the user's to fix)
        raise BookError("That workbook could not be read (%s)." % type(e).__name__)
    return _build(filename, source, sheets)


def parse_csv(data, filename="campaign book.csv"):
    if len(data) > MAX_BYTES:
        raise BookError("That file is over %d MB." % (MAX_BYTES // 1_000_000))
    text = data.decode("utf-8-sig", errors="replace")
    grid = list(csv.reader(io.StringIO(text)))[:MAX_ROWS]
    return _build(filename, "upload", [("Sheet1", grid)])


def from_upload(filename, b64):
    """The book from a browser upload: the file name and its base64 content."""
    name = re.sub(r"[^\w .()-]", "", str(filename or "campaign book"))[:120] or "campaign book"
    try:
        data = base64.b64decode(re.sub(r"^data:[^,]*,", "", str(b64 or "")), validate=False)
    except Exception:                                       # noqa: BLE001
        raise BookError("That file could not be read.")
    if not data:
        raise BookError("That file is empty.")
    if name.lower().endswith(".csv"):
        return parse_csv(data, name)
    if name.lower().endswith(".xls"):
        raise BookError("That is an old .xls file. Save it as .xlsx (Excel Workbook) and add it again.")
    return parse_xlsx(data, name)


def sheet_export_url(link):
    m = SHEET_ID.match(str(link or "").strip())
    if not m:
        raise BookError("That is not a Google Sheets link. It should start with https://docs.google.com/spreadsheets/d/")
    return "https://docs.google.com/spreadsheets/d/%s/export?format=xlsx" % m.group(1)


def from_google_sheet(link, fetcher=None):
    """The book from a Google Sheet shared as 'anyone with the link can view'. A private sheet cannot be read:
    Google answers with a sign-in page, which is said in words rather than parsed as a workbook."""
    url = sheet_export_url(link)
    if fetcher is None:
        from .event_intel_http import public_get as fetcher
    try:
        resp = fetcher(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (compatible; Position2-LaunchQA/1.0)"})
        try:
            data = resp.raw.read(MAX_BYTES + 1, decode_content=True) if hasattr(resp.raw, "read") else resp.content
            status = resp.status_code
        finally:
            resp.close()
    except Exception as e:                                  # noqa: BLE001
        raise BookError("The sheet could not be fetched (%s)." % type(e).__name__)
    if status in (401, 403, 404) or data[:2] != b"PK":
        raise BookError("That sheet is private, so it cannot be read. Share it as 'Anyone with the link can view', or download it as .xlsx and add the file.")
    return parse_xlsx(data, "Google Sheet", "google_sheet")


def public(book):
    """What the page is told about a stored book (not the whole text)."""
    if not book:
        return None
    return {"filename": book.get("filename"), "source": book.get("source"), "fields": len(book.get("rows") or []),
            "sheets": book.get("sheets") or [], "uploadedBy": book.get("uploadedBy"), "uploadedAt": book.get("uploadedAt")}


def filled(value):
    """True when a value is a real answer, not the template's placeholder."""
    v = _norm(value)
    return bool(v) and not any(p in v for p in PLACEHOLDERS)


def lookup(book, *names):
    """The first filled-in value of the first row whose field is one of `names` (matched on the normalised label)."""
    if not book:
        return None
    wanted = [_norm(n) for n in names]
    for row in book.get("rows") or []:
        f = _norm(row.get("field"))
        if any(f == w or f.startswith(w + " ") for w in wanted) and filled(row.get("value")):
            return row["value"]
    return None
