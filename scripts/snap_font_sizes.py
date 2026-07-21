#!/usr/bin/env python3
"""Snap every hardcoded `font-size:<n>px` to the canonical type scale.

Only bare pixel declarations are touched. `font-size:clamp(...)`,
`font-size:var(...)`, `font-size:inherit`, and non-px units are left as-is
(the regex only matches a number directly after the colon). Run from repo root.
"""
import re
import sys
from pathlib import Path

SCALE = [10.0, 11.5, 13.0, 15.0, 17.0, 20.0, 24.0, 30.0, 40.0, 56.0]

def snap(v: float) -> float:
    # nearest step; ties go to the larger value (better legibility)
    best = SCALE[0]
    best_d = abs(v - best)
    for s in SCALE:
        d = abs(v - s)
        if d < best_d or (d == best_d and s > best):
            best, best_d = s, d
    return best

def fmt(v: float) -> str:
    return (f"{v:.1f}".rstrip("0").rstrip(".")) + "px"

PAT = re.compile(r"(font-size:\s*)(\d+(?:\.\d+)?)px", re.IGNORECASE)

def process(text: str):
    changes = {}
    def repl(m):
        orig = float(m.group(2))
        new = snap(orig)
        if fmt(new) != m.group(2) + "px":
            changes[(m.group(2) + "px", fmt(new))] = changes.get((m.group(2) + "px", fmt(new)), 0) + 1
        return m.group(1) + fmt(new)
    out = PAT.sub(repl, text)
    return out, changes

def main(paths):
    total = 0
    agg = {}
    for p in paths:
        p = Path(p)
        if not p.is_file():
            continue
        src = p.read_text(encoding="utf-8")
        out, changes = process(src)
        n = sum(changes.values())
        if out != src:
            p.write_text(out, encoding="utf-8")
        if n:
            total += n
            print(f"{p}: {n} snapped")
            for (a, b), c in sorted(changes.items()):
                agg[(a, b)] = agg.get((a, b), 0) + c
    print(f"\nTOTAL snapped: {total}")
    print("mapping (old -> new: count):")
    for (a, b), c in sorted(agg.items(), key=lambda kv: -kv[1]):
        print(f"  {a:>8} -> {b:<7} x{c}")

if __name__ == "__main__":
    main(sys.argv[1:])
