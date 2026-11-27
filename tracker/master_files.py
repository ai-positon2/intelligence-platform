"""Master Agent chat: what happens to the files someone gives the agent.

Every upload is turned into the parts the model can use, by kind:

  table   CSV, TSV, Excel, JSON, XML      copied into the code sandbox (to analyse in Python) and previewed in the message
  text    plain text, Markdown, code, HTML  shown in the message in full (up to a limit) and copied into the sandbox
  image   PNG, JPEG, GIF, WebP             shown to the model as an image and copied into the sandbox
  pdf     PDF                              shown to the model as a document (it reads the pages, tables and charts)
  doc     Word, PowerPoint                 their text taken out here and shown in the message
  zip     a zip, or a folder              opened here, and each file inside handled as above

Nothing in a file is ever an instruction: the model is told so. Names are cleaned so a file cannot reach outside where it belongs.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import zipfile

MAX_FILES = 40                     # after a zip or folder is opened
MAX_TOTAL_BYTES = 20 * 1024 * 1024          # sent as base64 in JSON, under the app's 32 MB request limit
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ZIP_MEMBERS = 200
MAX_UNZIPPED_BYTES = 60 * 1024 * 1024
MAX_TEXT_CHARS = 60000
PREVIEW_ROWS = 15
IMAGE_MIMES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
TABLE_EXT = {"csv", "tsv", "xlsx", "xlsm", "xls", "json", "xml"}
TEXT_EXT = {"txt", "md", "markdown", "py", "js", "ts", "sql", "log", "html", "htm", "css", "yaml", "yml", "ini", "cfg", "rtf", "srt", "vtt"}
DOC_EXT = {"docx", "pptx"}
MIMES = {"csv": "text/csv", "tsv": "text/tab-separated-values", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12", "xls": "application/vnd.ms-excel", "json": "application/json", "xml": "application/xml",
         "pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "zip": "application/zip", "txt": "text/plain", "md": "text/markdown",
         "html": "text/html", "htm": "text/html"}


class FileProblem(ValueError):
    """A file that cannot be taken, in words for the person."""


def clean_name(name, fallback="file"):
    """A safe file name: no folders, no leading dots, only ordinary characters. A folder path becomes part of the name (a/b.csv -> a_b.csv)."""
    name = str(name or "").replace("\\", "/").strip("/")
    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
    joined = "_".join(parts) or fallback
    joined = re.sub(r"[^\w.\- ()]+", "_", joined).strip(" ._") or fallback
    if len(joined) > 120:
        stem, dot, ext = joined.rpartition(".")
        joined = (stem[:110] + "." + ext[:8]) if dot else joined[:120]
    return joined


def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def mime_of(name, given=""):
    e = ext_of(name)
    return IMAGE_MIMES.get(e) or MIMES.get(e) or (given if given and "/" in given else "application/octet-stream")


def kind_of(name):
    e = ext_of(name)
    if e in IMAGE_MIMES:
        return "image"
    if e in TABLE_EXT:
        return "table"
    if e in TEXT_EXT:
        return "text"
    if e == "pdf":
        return "pdf"
    if e in DOC_EXT:
        return "doc"
    if e == "zip":
        return "zip"
    return "other"


def _decode(data):
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", "replace")


def preview_table(name, data):
    """A short look at a table so the model knows its shape before it opens it: the sheets, the columns and the first rows."""
    e = ext_of(name)
    try:
        if e in ("csv", "tsv"):
            text = _decode(data)
            rows = list(csv.reader(io.StringIO(text), delimiter="\t" if e == "tsv" else ","))
            total = len(rows)
            shown = "\n".join(", ".join(c[:60] for c in r[:30]) for r in rows[:PREVIEW_ROWS + 1])
            return "%d rows including the header. First rows:\n%s" % (total, shown)
        if e in ("xlsx", "xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets[:12]:
                rows = []
                for i, r in enumerate(ws.iter_rows(values_only=True)):
                    if i > PREVIEW_ROWS:
                        break
                    rows.append(", ".join("" if v is None else str(v)[:60] for v in r[:30]))
                out.append("Sheet '%s' (about %s rows x %s columns):\n%s" % (ws.title, ws.max_row, ws.max_column, "\n".join(rows)))
            wb.close()
            return "\n\n".join(out) or "An empty workbook."
        if e == "json":
            obj = json.loads(_decode(data))
            if isinstance(obj, list):
                return "A JSON list of %d items. First: %s" % (len(obj), json.dumps(obj[:3], default=str)[:1500])
            if isinstance(obj, dict):
                return "A JSON object with keys: %s" % ", ".join(list(obj)[:40])
            return "JSON: %s" % str(obj)[:500]
        if e == "xml":
            return _decode(data)[:1500]
        if e == "xls":
            return "An old-style Excel file: open it in the sandbox with pandas (xlrd is installed there)."
    except Exception as ex:                                                  # noqa: BLE001 (a preview that fails is said, the file still goes to the sandbox)
        return "Could not preview it here (%s); open it in the sandbox." % type(ex).__name__
    return ""


def doc_text(name, data):
    """The text of a Word or PowerPoint file, tables included, in reading order."""
    e = ext_of(name)
    if e == "docx":
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(parts)
    if e == "pptx":
        from pptx import Presentation
        prs = Presentation(io.BytesIO(data))
        out = []
        for i, slide in enumerate(prs.slides, 1):
            lines = []
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                    lines.append(shape.text_frame.text.strip())
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        lines.append(" | ".join(c.text.strip() for c in row.cells))
            notes = ""
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                notes = slide.notes_slide.notes_text_frame.text.strip()
            out.append("Slide %d:\n%s%s" % (i, "\n".join(lines), ("\nNotes: " + notes) if notes else ""))
        return "\n\n".join(out)
    return ""


def unzip(name, data):
    """The files inside a zip, safely: no paths outside it, no hidden or system files, no zip inside a zip, and limits on count and size."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise FileProblem("%s is not a zip file that can be opened." % name)
    out, total = [], 0
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_ZIP_MEMBERS:
        raise FileProblem("%s holds %d files; the most that can be opened is %d." % (name, len(infos), MAX_ZIP_MEMBERS))
    for info in infos:
        inner = info.filename.replace("\\", "/")
        base = inner.rsplit("/", 1)[-1]
        if not base or base.startswith(".") or "__MACOSX" in inner or base.lower() in ("thumbs.db", "desktop.ini"):
            continue
        if ext_of(base) == "zip":
            continue
        total += info.file_size
        if total > MAX_UNZIPPED_BYTES or info.file_size > MAX_FILE_BYTES:
            raise FileProblem("%s opens to more than %d MB; send fewer or smaller files." % (name, MAX_UNZIPPED_BYTES // (1024 * 1024)))
        with zf.open(info) as fh:
            content = fh.read(MAX_FILE_BYTES + 1)
        out.append((clean_name(inner), content))
    return out


def prepare(uploads):
    """[(name, bytes, mime)] from the request -> [part], where a part is a dict the chat uses: name, mime, size, kind, data (bytes), text (what the model
    reads in the message), sandbox (copy into the code sandbox) and note (why something was left out). Raises FileProblem for what cannot be taken at all."""
    expanded = []
    total = 0
    for name, data, mime in uploads:
        name = clean_name(name)
        data = data or b""
        if len(data) > MAX_FILE_BYTES:
            raise FileProblem("%s is %.1f MB; the most for one file is %d MB." % (name, len(data) / 1048576.0, MAX_FILE_BYTES // 1048576))
        total += len(data)
        if kind_of(name) == "zip":
            for inner, content in unzip(name, data):
                expanded.append((inner, content, mime_of(inner)))
        else:
            expanded.append((name, data, mime_of(name, mime)))
    if total > MAX_TOTAL_BYTES:
        raise FileProblem("The files add up to %.1f MB; the most at once is %d MB." % (total / 1048576.0, MAX_TOTAL_BYTES // 1048576))
    if len(expanded) > MAX_FILES:
        raise FileProblem("That is %d files; the most at once is %d. Zip them by topic or send them over a few messages." % (len(expanded), MAX_FILES))
    seen, parts = set(), []
    for name, data, mime in expanded:
        stem, dot, ext = name.rpartition(".")
        n, unique = 2, name
        while unique.lower() in seen:
            unique = "%s (%d).%s" % (stem, n, ext) if dot else "%s (%d)" % (name, n)
            n += 1
        seen.add(unique.lower())
        parts.append(_part(unique, data, mime))
    return parts


def _part(name, data, mime):
    kind = kind_of(name)
    p = {"name": name, "mime": mime_of(name, mime), "size": len(data), "kind": kind, "data": data, "text": "", "sandbox": False, "note": ""}
    if kind == "table":
        p["sandbox"] = True
        p["text"] = preview_table(name, data)
    elif kind == "text":
        text = _decode(data)
        p["sandbox"] = True
        p["text"] = text[:MAX_TEXT_CHARS]
        if len(text) > MAX_TEXT_CHARS:
            p["note"] = "Only the first %d characters are shown in the message; the whole file is in the sandbox." % MAX_TEXT_CHARS
    elif kind == "image":
        p["sandbox"] = True
    elif kind == "pdf":
        if not data.startswith(b"%PDF"):
            p["kind"], p["note"] = "other", "It does not look like a PDF, so it was not read."
    elif kind == "doc":
        try:
            text = doc_text(name, data)
        except Exception as ex:                                              # noqa: BLE001 (a file that cannot be opened is said, not fatal)
            text, p["note"] = "", "Could not open it (%s)." % type(ex).__name__
        p["text"] = text[:MAX_TEXT_CHARS]
        if len(text) > MAX_TEXT_CHARS:
            p["note"] = "Only the first %d characters of its text are shown." % MAX_TEXT_CHARS
    else:
        p["note"] = "This kind of file (.%s) cannot be read. Send CSV, Excel, JSON, XML, text, PDF, Word, PowerPoint, images or a zip of them." % (ext_of(name) or "?")
    return p


def b64(data):
    return base64.standard_b64encode(data).decode("ascii")


def describe(part):
    """One line about a part, for the record kept with the turn (no bytes)."""
    return {"name": part["name"], "mime": part["mime"], "size": part["size"], "kind": part["kind"], "note": part.get("note") or ""}


def is_image_name(name):
    return ext_of(name) in IMAGE_MIMES


SAFE_DOWNLOAD = re.compile(r"^[\w.\- ()]{1,200}$")


def download_name(name):
    n = clean_name(os.path.basename(str(name or "")), "file")
    return n if SAFE_DOWNLOAD.match(n) else "file"
