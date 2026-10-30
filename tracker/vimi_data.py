"""Vimi: reading what an agent returned (no network, no model).

An agent's report can be hundreds of campaigns by dozens of columns. Put whole into a prompt it
drowns the model; cut blindly it loses the rows that answer the question. So a result is kept
here, whole, under an id, and the model is handed:

  digest(obj)     a bounded first look: the scalar fields, every table (its path, row count,
                  columns, each numeric column's total) and the first rows of each, with a
                  marker wherever something was cut and the path to ask for the rest
  query(obj, ...) the drill: pick a path, filter rows, sort, project columns, or group and
                  aggregate. The model asks for "cost by campaign, highest first" and gets the
                  sums computed by code, never added up in its head

Everything is plain dicts and lists (what jsonify already returned), so the same functions read
any agent's report without a per-agent adapter.
"""
from __future__ import annotations

import json
import math
import re
from collections import OrderedDict

DIGEST_CHARS = 9000                  # one first look
QUERY_CHARS = 12000                  # one drill
MAX_ROWS = 100
DEFAULT_ROWS = 25
KEEP_RESULTS = 40                    # results one question may hold
_PATH_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
# columns whose total means nothing (a sum of rates), so the digest gives their average instead
_RATIO_KEY = re.compile(r"(rate|ratio|share|pct|percent|avg|average|score|cpc|cpa|cpm|roas|ctr|cvr|position|rank|index|confidence|id)$", re.I)
OPS = ("=", "!=", ">", ">=", "<", "<=", "contains", "not_contains", "in", "startswith", "exists", "missing")
FUNCS = ("sum", "avg", "min", "max", "count", "count_distinct")


class PathError(ValueError):
    """The path does not lead anywhere in this result; the message says what is there."""


# ── paths ─────────────────────────────────────────────────────────────────────

def _parts(path):
    out = []
    for m in _PATH_PART.finditer(str(path or "")):
        out.append(int(m.group(2)) if m.group(2) is not None else m.group(1))
    return out


def resolve(obj, path):
    """The value at "a.b[0].c" ("" is the whole result)."""
    cur, walked = obj, ""
    for p in _parts(path):
        if isinstance(p, int):
            if not isinstance(cur, list) or p >= len(cur):
                raise PathError("%s has %s" % (walked or "the result", ("%d items" % len(cur)) if isinstance(cur, list) else "no list to index"))
            cur = cur[p]
            walked += "[%d]" % p
        else:
            if not isinstance(cur, dict) or p not in cur:
                have = ", ".join(list(cur)[:25]) if isinstance(cur, dict) else type(cur).__name__
                raise PathError("%s has no '%s' (it has: %s)" % (walked or "the result", p, have))
            cur = cur[p]
            walked += ("." if walked else "") + p
    return cur


def _join(path, rel):
    """The path of `rel` found inside the value at `path`."""
    if not rel:
        return path
    if not path:
        return rel
    return path + rel if rel.startswith("[") else "%s.%s" % (path, rel)


def _is_rows(v):
    return isinstance(v, list) and len(v) > 0 and sum(1 for x in v if isinstance(x, dict)) >= max(1, len(v) // 2)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def _get(row, field):
    """row["a.b"] reads row["a"]["b"] when the key itself is not there."""
    if not isinstance(row, dict):
        return None
    if field in row:
        return row[field]
    cur = row
    for p in str(field).split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _clean(v):
    return round(v, 6) if isinstance(v, float) else v


# ── tables ────────────────────────────────────────────────────────────────────

def find_tables(obj, *, max_depth=5, limit=40):
    """[(path, rows)] for every list of records in the result, shallowest first."""
    found, queue = [], [("", obj, 0)]
    while queue and len(found) < limit:
        path, cur, depth = queue.pop(0)
        if _is_rows(cur):
            found.append((path, cur))
            # records can hold tables of their own (a campaign's ad groups): look in the first one only, so a path is offered once
            if depth < max_depth and isinstance(cur[0], dict):
                for k, v in cur[0].items():
                    if isinstance(v, (list, dict)):
                        queue.append(("%s[0].%s" % (path, k), v, depth + 1))
        elif isinstance(cur, dict) and depth < max_depth:
            for k, v in cur.items():
                if isinstance(v, (list, dict)):
                    queue.append((("%s.%s" % (path, k)) if path else str(k), v, depth + 1))
    return found


def columns(rows, sample=200):
    """{column: kind} over the first rows: number, text, list, object or flag (the first kind a column shows)."""
    cols = OrderedDict()
    for r in rows[:sample]:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            if k in cols or v is None:
                continue
            cols[k] = ("flag" if isinstance(v, bool) else "number" if _num(v) else "list" if isinstance(v, list)
                       else "object" if isinstance(v, dict) else "text")
    return cols


def totals(rows, cols=None):
    """{column: {sum or avg, min, max, n}} for each numeric column."""
    out = OrderedDict()
    for k, kind in (cols or columns(rows)).items():
        if kind != "number":
            continue
        vals = [r[k] for r in rows if isinstance(r, dict) and _num(r.get(k))]
        if not vals:
            continue
        agg = {"n": len(vals), "min": _clean(min(vals)), "max": _clean(max(vals))}
        if _RATIO_KEY.search(k):
            agg["avg"] = _clean(sum(vals) / len(vals))
        else:
            agg["sum"] = _clean(sum(vals))
        out[k] = agg
    return out


# ── the first look ────────────────────────────────────────────────────────────

def _prune(v, *, rows, text, depth, keys):
    """A copy of v cut to the limits, with a marker wherever it was cut."""
    if isinstance(v, str):
        return v if len(v) <= text else v[:text] + "..."
    if isinstance(v, float):
        return _clean(v)
    if not isinstance(v, (dict, list)):
        return v
    if depth <= 0:
        return "{...}" if isinstance(v, dict) else "[%d items]" % len(v)
    if isinstance(v, list):
        out = [_prune(x, rows=rows, text=text, depth=depth - 1, keys=keys) for x in v[:rows]]
        if len(v) > rows:
            out.append("... %d more" % (len(v) - rows))
        return out
    out, n = OrderedDict(), 0
    for k, x in v.items():
        if n >= keys:
            out["..."] = "%d more fields" % (len(v) - keys)
            break
        out[k] = _prune(x, rows=rows, text=text, depth=depth - 1, keys=keys)
        n += 1
    return out


def _tables_text(obj, budget):
    lines = []
    for path, rows in find_tables(obj):
        cols = columns(rows)
        tot = totals(rows, cols)
        line = "- %s: %d rows. columns: %s" % (path or "(the result itself)", len(rows), ", ".join("%s(%s)" % (k, kind[0]) for k, kind in list(cols.items())[:40]))
        if tot:
            line += "\n    totals: " + "; ".join("%s %s" % (k, ("sum %s" % a["sum"]) if "sum" in a else ("avg %s" % a["avg"])) + " [min %s, max %s]" % (a["min"], a["max"]) for k, a in list(tot.items())[:14])
        lines.append(line)
        if sum(len(x) for x in lines) > budget:
            lines.append("- ... more tables: ask for a path to see them")
            break
    return "\n".join(lines)


def digest(obj, budget=DIGEST_CHARS):
    """A bounded first look at a result: its shape and totals, then the content cut to fit."""
    tables = _tables_text(obj, budget // 3)
    head = ("TABLES (path: rows, columns n=number t=text l=list o=object f=flag; use inspect_result with a path to filter, sort, group or page them):\n" + tables + "\n\n") if tables else ""
    room = max(budget - len(head), 1500)
    for rows, text, depth, keys in ((6, 220, 6, 40), (4, 160, 5, 30), (3, 110, 4, 24), (2, 80, 4, 16), (1, 60, 3, 10)):
        body = json.dumps(_prune(obj, rows=rows, text=text, depth=depth, keys=keys), separators=(",", ":"), ensure_ascii=False, default=str)
        if len(body) <= room:
            return head + "CONTENT:\n" + body
    return head + "CONTENT (cut):\n" + body[:room] + "..."


# ── the drill ─────────────────────────────────────────────────────────────────

def _cmp_key(v):
    return (0, v) if _num(v) else (1, str(v).lower())


def _sorted(items, value_of, desc):
    """items ordered by value_of, a missing value last whichever way it runs (reverse=True alone would put it first)."""
    present = [i for i in items if value_of(i) not in (None, "")]
    missing = [i for i in items if value_of(i) in (None, "")]
    return sorted(present, key=lambda i: _cmp_key(value_of(i)), reverse=desc) + missing


def _match(row, cond):
    field, op, want = cond.get("field"), cond.get("op", "="), cond.get("value")
    have = _get(row, field)
    if op == "exists":
        return have not in (None, "")
    if op == "missing":
        return have in (None, "")
    if have is None:
        return op in ("!=", "not_contains")
    if op in ("in",):
        pool = want if isinstance(want, list) else [want]
        return any(_eq(have, w) for w in pool)
    if op in ("=", "!="):
        return _eq(have, want) == (op == "=")
    if op in ("contains", "not_contains", "startswith"):
        a, b = str(have).lower(), str(want).lower()
        return {"contains": b in a, "not_contains": b not in a, "startswith": a.startswith(b)}[op]
    try:
        a, b = float(have), float(want)
    except (TypeError, ValueError):
        a, b = str(have).lower(), str(want).lower()
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]


def _eq(a, b):
    if _num(a):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False
    return str(a).strip().lower() == str(b).strip().lower()


def _check_conds(where):
    out = []
    for c in where or []:
        if not isinstance(c, dict) or not c.get("field"):
            raise ValueError("each filter needs a field, an op and a value")
        op = c.get("op", "=")
        if op not in OPS:
            raise ValueError("op '%s' is not one of: %s" % (op, ", ".join(OPS)))
        out.append(dict(c, op=op))
    return out


def _aggregate(rows, specs):
    out = OrderedDict()
    for s in specs:
        fn, field = s.get("fn", "sum"), s.get("field")
        if fn not in FUNCS:
            raise ValueError("fn '%s' is not one of: %s" % (fn, ", ".join(FUNCS)))
        name = s.get("as") or ("%s_%s" % (fn, field) if field else fn)
        if fn == "count":
            out[name] = len(rows) if not field else sum(1 for r in rows if _get(r, field) not in (None, ""))
            continue
        vals = [_get(r, field) for r in rows]
        if fn == "count_distinct":
            out[name] = len({str(v).lower() for v in vals if v not in (None, "")})
            continue
        nums = [v for v in vals if _num(v)]
        if not nums:
            out[name] = None
            continue
        out[name] = _clean({"sum": sum(nums), "avg": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}[fn])
    return out


def query(obj, path="", *, where=None, sort_by=None, order="desc", limit=DEFAULT_ROWS, offset=0, fields=None, group_by=None, aggregate=None):
    """Rows (or groups) from the table at `path`, as a dict ready to serialise. Raises PathError / ValueError with a message the model can act on."""
    node = resolve(obj, path)
    if isinstance(node, dict):
        out = {"path": path, "value": json.loads(json.dumps(_prune(node, rows=10, text=400, depth=5, keys=60), default=str))}
        inner = [_join(path, p) for p, _r in find_tables(node)]
        if inner:
            out["tables"] = inner[:12]
        return out
    if not isinstance(node, list):
        return {"path": path, "value": node}
    rows = [r for r in node if isinstance(r, dict)] if _is_rows(node) else node
    if not _is_rows(node):                       # a plain list (strings, numbers): page it
        lim = max(1, min(int(limit or DEFAULT_ROWS), 300))
        return {"path": path, "total": len(rows), "offset": int(offset or 0), "values": rows[int(offset or 0):int(offset or 0) + lim]}
    conds = _check_conds(where)
    if conds:
        rows = [r for r in rows if all(_match(r, c) for c in conds)]
    total = len(rows)
    if group_by:
        keys = [group_by] if isinstance(group_by, str) else list(group_by)
        specs = aggregate or [{"fn": "count"}]
        buckets = OrderedDict()
        for r in rows:
            k = tuple(("" if _get(r, g) is None else _get(r, g)) for g in keys)
            buckets.setdefault(k, []).append(r)
        groups = []
        for k, members in buckets.items():
            g = OrderedDict(zip(keys, k))
            g["rows"] = len(members)
            g.update(_aggregate(members, specs))
            groups.append(g)
        by = sort_by or (next((n for n in groups[0] if n not in keys and n != "rows"), "rows") if groups else "rows")
        groups = _sorted(groups, lambda g: g.get(by), order != "asc")
        lim = max(1, min(int(limit or DEFAULT_ROWS), MAX_ROWS))
        return {"path": path, "matched": total, "groups_total": len(groups), "sorted_by": by, "groups": groups[:lim]}
    if aggregate:
        return {"path": path, "matched": total, "aggregate": _aggregate(rows, aggregate)}
    if sort_by:
        rows = _sorted(rows, lambda r: _get(r, sort_by), order != "asc")
    off, lim = max(int(offset or 0), 0), max(1, min(int(limit or DEFAULT_ROWS), MAX_ROWS))
    page = rows[off:off + lim]
    if fields:
        page = [OrderedDict((f, _clean(_get(r, f))) for f in fields) for r in page]
    else:
        page = [_prune(r, rows=5, text=300, depth=4, keys=40) for r in page]
    out = {"path": path, "matched": total, "offset": off, "returned": len(page), "rows": page}
    if off + lim < total:
        out["next_offset"] = off + lim
    return out


def render(result, budget=QUERY_CHARS):
    """A query result as the text the model reads, cut (with a note that says how to ask for less) when it is too long."""
    text = json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str)
    if len(text) <= budget:
        return text
    if isinstance(result, dict) and isinstance(result.get("rows"), list):
        rows = list(result["rows"])

        def shown(rs):
            cut = dict(result, rows=rs, returned=len(rs), cut="the answer was too long: %d of the rows are shown. Use fields to ask for fewer columns, or a smaller limit." % len(rs))
            if len(rs) < len(result["rows"]):
                cut["next_offset"] = result.get("offset", 0) + len(rs)
            return json.dumps(cut, separators=(",", ":"), ensure_ascii=False, default=str)

        while rows and len(shown(rows)) > budget:           # the note is part of the answer, so it is counted
            rows = rows[:max(len(rows) * 2 // 3, len(rows) - 1)] if len(rows) > 1 else []
        return shown(rows)
    return text[:budget] + "... (cut: ask for a narrower path)"


# ── what one question holds ───────────────────────────────────────────────────

class Results:
    """The results one question has read, each under an id the model quotes back to drill into it."""

    def __init__(self, keep=KEEP_RESULTS):
        self._items = OrderedDict()
        self._n = 0
        self._keep = keep

    def add(self, label, data):
        self._n += 1
        rid = "r%d" % self._n
        self._items[rid] = {"label": label, "data": data}
        while len(self._items) > self._keep:
            self._items.popitem(last=False)
        return rid

    def get(self, rid):
        item = self._items.get(str(rid or "").strip().lower())
        if not item:
            raise KeyError("no result '%s' (the ones read so far: %s)" % (rid, ", ".join("%s=%s" % (k, v["label"]) for k, v in self._items.items()) or "none"))
        return item

    def labels(self):
        return [(k, v["label"]) for k, v in self._items.items()]
