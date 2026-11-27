"""Master Agent chat: check the figures of an answer against the data the answer was built from (no network, no model).

Every figure that matters in an answer (money, percentages, decimals, big counts) must be traceable: it must appear in something the agent was
given or computed (a tool result, the sandbox's printed output, the person's question or files, or an earlier answer of the conversation), to
the precision it is written with. A figure with no source is one the model worked out in its head, which is how a wrong total gets through.

`unverified(answer, evidence)` returns the figures of the answer that match nothing in the evidence. The loop sends those back to the model once,
to recompute them in code or drop them, and says so in the answer if one still cannot be traced.
"""
from __future__ import annotations

import bisect
import re

MAX_EVIDENCE_CHARS = 8_000_000
MAX_REPORTED = 12

# a figure as written: optional sign and currency, digits with thousands separators or a decimal part, an optional K/M/B/% suffix
NUM = re.compile(r"(?<![\w.])([-+]?)([$€£₹]?)(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(\s?(?:%|[KkMmBb]\b|x\b|×))?")
MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
DATEISH = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(r"\b%s\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b" % MONTHS, re.I),
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?%s(?:\s+\d{4})?\b" % MONTHS, re.I),
    re.compile(r"\b\d{1,2}\s+(?:to|-)\s+\d{1,2}\s+%s\b" % MONTHS, re.I),
    re.compile(r"\b(?:19|20)\d{2}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),
    re.compile(r"\bQ[1-4]\b"),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:days?|weeks?|months?|years?|hours?|minutes?|mins?|seconds?)\b", re.I),
    re.compile(r"\bLAST_\d+_DAYS\b"),
    re.compile(r"\b[A-Za-z_]+\d+[A-Za-z_\d]*\b"),            # names with digits: act_123, P2_Brand2, h1
    re.compile(r"\bv\d+\b", re.I),
]
REPAIR_MARK = "Before this goes out, check your figures."       # the loop's own message listing the untraced figures: never evidence for them
SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}


def _scrub(text):
    """The text with dates, times, ids and the like blanked out, so only figures are left."""
    t = text
    for rx in DATEISH:
        t = rx.sub(" ", t)
    return t


def _parse(m):
    """(value, decimals, is_percent, scale, has_unit) of one figure match."""
    sign, cur, digits, suffix = m.group(1), m.group(2), m.group(3), (m.group(4) or "").strip().lower()
    clean = digits.replace(",", "")
    decimals = len(clean.split(".")[1]) if "." in clean else 0
    value = float(clean) * (-1 if sign == "-" else 1)
    scale = SCALE.get(suffix, 1.0)
    return value, decimals, suffix == "%", scale, bool(cur or suffix)


def evidence_values(texts):
    """Every number found in the evidence texts, sorted, for matching."""
    vals, used = [], 0
    for t in texts:
        if not t:
            continue
        t = str(t)
        used += len(t)
        if used > MAX_EVIDENCE_CHARS:
            break
        for m in re.finditer(r"-?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?", t):
            raw = m.group(0).replace(",", "")
            try:
                vals.append(abs(float(raw)))
            except ValueError:
                continue
    vals.sort()
    return vals


def _has(vals, x, tol):
    i = bisect.bisect_left(vals, x - tol)
    return i < len(vals) and vals[i] <= x + tol


def figures(answer):
    """The figures of an answer worth checking: (as written, value, decimals, is_percent, scale)."""
    out = []
    for line in str(answer or "").split("\n"):
        line = re.sub(r"^\s*(?:[-*•]\s+|\d+[.)]\s+|#+\s+)", "", line)
        line = re.sub(r"https?://\S+", " ", line)
        line = re.sub(r"`[^`]*`", " ", line)                                   # code names
        for m in NUM.finditer(_scrub(line)):
            value, decimals, pct, scale, unit = _parse(m)
            digits = m.group(3)
            if not unit and decimals == 0 and "," not in digits and abs(value) < 100:
                continue                                                       # a count or a rank: "3 campaigns", "top 5"
            if not unit and decimals == 0 and len(digits) >= 7:
                continue                                                       # an id or a phone number
            out.append((m.group(0).strip(), abs(value), decimals, pct, scale))
    return out


def unverified(answer, evidence_texts):
    """The figures of the answer that no evidence supports (as written), at most MAX_REPORTED, without repeats."""
    figs = figures(answer)
    if not figs:
        return []
    vals = evidence_values(evidence_texts)
    if not vals:
        return []                                                              # nothing to check against (a question answered from the model's knowledge): not a verdict
    bad, seen = [], set()
    for raw, x, d, pct, scale in figs:
        if raw in seen:
            continue
        seen.add(raw)
        shown = x * scale
        tol = 0.5 * (10 ** -d) * scale + 1e-9                                  # the rounding of what was written
        ok = _has(vals, shown, tol)
        if not ok and pct:
            ok = _has(vals, x / 100.0, 0.5 * (10 ** -(d + 2)) + 1e-12) or _has(vals, x * 100.0, 0.5 * (10 ** -d) * 100.0)
        if not ok:
            bad.append(raw)
        if len(bad) >= MAX_REPORTED:
            break
    return bad


def evidence_from_messages(messages):
    """The texts the answer may be built from: what the person wrote, every tool result, the sandbox's printed output, and earlier answers.
    The model's own thinking, its working text in this turn and the code it wrote are not evidence."""
    texts = []

    def walk(o):
        if isinstance(o, str):
            texts.append(o)
        elif isinstance(o, dict):
            for k, v in o.items():
                if k in ("signature", "thinking", "data"):
                    continue
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    for m in messages or []:
        role, content = m.get("role"), m.get("content")
        if isinstance(content, str):
            if role in ("user", "assistant") and not content.startswith(REPAIR_MARK):
                texts.append(content)                                          # a question, or an earlier answer (checked when it was given)
            continue
        for b in content or []:
            d = b if isinstance(b, dict) else (b.model_dump() if hasattr(b, "model_dump") else getattr(b, "__dict__", {}))
            t = str(d.get("type") or "")
            if role == "user":
                if t == "text":
                    texts.append(d.get("text") or "")
                elif t.endswith("tool_result"):
                    walk(d.get("content"))
            elif role == "assistant" and t.endswith("tool_result") and t != "tool_result":
                walk(d.get("content"))                                         # the sandbox's own output (stdout), not the model's words
    return texts


def has_unreadable(messages):
    """True when the conversation holds a PDF or an image the model read itself: its figures are not in any text here, so they cannot be checked."""
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                d = b if isinstance(b, dict) else {}
                if d.get("type") in ("document", "image"):
                    return True
    return False
