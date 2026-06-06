#!/usr/bin/env bash
# Build the Ad Intelligence React app and copy it into the Flask static dir.
# Never fails the deploy: if Node/build is unavailable, the committed build in
# ad_intelligence/ is served as-is.
set +e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FE="$ROOT/apps/ad-intelligence"
DEST="$ROOT/ad_intelligence"
echo "[build-frontend] starting"
[ -d "$FE" ] || { echo "[build-frontend] no frontend source; serving committed build"; exit 0; }
command -v npm >/dev/null 2>&1 || { echo "[build-frontend] npm unavailable; serving committed build"; exit 0; }
cd "$FE" || exit 0
echo "[build-frontend] node $(node -v 2>/dev/null) / npm $(npm -v 2>/dev/null)"
( npm ci --no-audit --no-fund || npm install --no-audit --no-fund ) || { echo "[build-frontend] deps failed; serving committed build"; exit 0; }
npm run build || { echo "[build-frontend] vite build failed; serving committed build"; exit 0; }
mkdir -p "$DEST/assets"
rm -f "$DEST"/assets/* 2>/dev/null
cp -r dist/* "$DEST"/ 2>/dev/null
python3 "$ROOT/scripts/inject_widget.py" "$DEST/index.html" "$ROOT/scripts/ad_intelligence_widget.html" || echo "[build-frontend] widget inject skipped"
echo "[build-frontend] done"
exit 0
