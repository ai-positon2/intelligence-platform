#!/usr/bin/env python3
"""Inject the Kairo PPC chat widget into the built Ad Intelligence index.html."""
import sys, io
idx, snip = sys.argv[1], sys.argv[2]
s = io.open(idx, encoding="utf-8").read()
if "PPC AI Chat Widget" in s:
    print("widget already present"); sys.exit(0)
w = io.open(snip, encoding="utf-8").read()
s = s.replace("</body>", "\n" + w + "\n  </body>", 1) if "</body>" in s else s + w
io.open(idx, "w", encoding="utf-8").write(s)
print("widget injected")
