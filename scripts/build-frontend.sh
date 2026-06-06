#!/usr/bin/env bash
# Build the Ad Intelligence React app and copy it into the Flask static dir.
# In CI (CI=true) it fails loudly on error; locally it never fails (keeps the
# committed build in ad_intelligence/ so the app always works).
set +e
_fail(){ echo "[build-frontend] $1"; [ "${CI:-}" = "true" ] && exit 1 || exit 0; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FE="$ROOT/apps/ad-intelligence"
DEST="$ROOT/ad_intelligence"
echo "[build-frontend] starting"
[ -d "$FE" ] || _fail "no frontend source"
command -v npm >/dev/null 2>&1 || _fail "npm unavailable"
cd "$FE" || _fail "cannot enter frontend dir"
echo "[build-frontend] node $(node -v 2>/dev/null) / npm $(npm -v 2>/dev/null)"
( npm ci --no-audit --no-fund || npm install --no-audit --no-fund ) || _fail "deps install failed"
npm run build || _fail "vite build failed"
mkdir -p "$DEST/assets"
rm -f "$DEST"/assets/* 2>/dev/null
cp -r dist/* "$DEST"/ 2>/dev/null
python3 "$ROOT/scripts/inject_widget.py" "$DEST/index.html" "$ROOT/scripts/ad_intelligence_widget.html" || echo "[build-frontend] widget inject skipped"
echo "[build-frontend] done"
exit 0
