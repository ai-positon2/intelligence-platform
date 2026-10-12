"""Push today's Pacing Desk run to the Intelligence Platform.

    python3 push.py                       # sends out/dashboard.json
    python3 push.py --file other.json --url https://.../api/pacing/seed

Needs PACING_SEED_SECRET in the environment: the same value set on the
platform's Railway service. The platform checks the payload, recomputes the
portfolio rollup itself, and makes this run the one everyone sees.

The daily routine is unchanged otherwise:

    python3 run.py --as-of 2026-10-12
    python3 validate.py 2026-10-12          # must print 0 issues
    python3 export_dashboard.py 2026-10-12  # writes out/dashboard.json
    python3 push.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://intelligence.position2.com/p2/paid-marketing/api/pacing/seed"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", default=str(Path(__file__).parent / "out" / "dashboard.json"))
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()
    secret = os.environ.get("PACING_SEED_SECRET", "")
    if not secret:
        print("PACING_SEED_SECRET is not set (it must match the platform's).", file=sys.stderr)
        return 1
    body = Path(args.file).read_bytes()
    json.loads(body)                      # fail here, not on the server, if it isn't JSON
    req = urllib.request.Request(args.url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Seed-Secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print("Pushed:", r.read().decode("utf-8"))
            return 0
    except urllib.error.HTTPError as e:
        print("Push failed: %s %s" % (e.code, e.read().decode("utf-8", "replace")[:500]), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
