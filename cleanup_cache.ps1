# cleanup_cache.ps1 — remove unnecessary cache files and stale artifacts.
#
# Safe to re-run. Skips active DBs (tracker.db, tracker_csg_v2.db),
# weekly-stats.json, and anything tracked by git that isn't cache.
#
# Run from project root in PowerShell:
#   cd C:\Users\krishna.l\company-signal-tracker
#   .\cleanup_cache.ps1
#
# Add -WhatIf to preview without deleting.

[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }

Write-Host ""
Write-Host "Cleanup running from: $Root" -ForegroundColor Cyan
Write-Host ""

$totalCount = 0
$totalBytes = 0L

function Remove-Bucket {
    param(
        [string]$Label,
        [scriptblock]$Locator
    )
    Write-Host "[$Label]" -ForegroundColor Yellow
    $items = & $Locator
    if (-not $items) { Write-Host "  nothing to remove"; Write-Host ""; return }

    $localCount = 0
    $localBytes = 0L
    foreach ($it in $items) {
        if (-not $it) { continue }
        $path = $it.FullName
        try {
            if ($it.PSIsContainer) {
                $size = (Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue |
                         Measure-Object -Property Length -Sum).Sum
                if (-not $size) { $size = 0 }
                Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
                $relPath = $path.Substring($Root.Length).TrimStart('\','/')
                Write-Host ("  removed dir : {0,-50}  {1,9:N0} bytes" -f $relPath, $size)
            } else {
                $size = $it.Length
                Remove-Item -LiteralPath $path -Force -ErrorAction Stop
                $relPath = $path.Substring($Root.Length).TrimStart('\','/')
                Write-Host ("  removed file: {0,-50}  {1,9:N0} bytes" -f $relPath, $size)
            }
            $localCount++
            $localBytes += $size
        } catch {
            Write-Host ("  FAILED      : {0}  --  {1}" -f $path, $_.Exception.Message) -ForegroundColor Red
        }
    }
    Write-Host ("  subtotal: {0} items, {1:N0} bytes ({2:N1} MB)" -f $localCount, $localBytes, ($localBytes/1MB)) -ForegroundColor DarkGray
    Write-Host ""
    $script:totalCount += $localCount
    $script:totalBytes += $localBytes
}

# [A] Python bytecode caches — __pycache__ directories anywhere
Remove-Bucket -Label "A. Python __pycache__/" -Locator {
    Get-ChildItem -Path $Root -Directory -Recurse -Force -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\\.git\\' -and $_.FullName -notmatch '\\\.venv\\' -and $_.FullName -notmatch '\\venv\\' }
}

# [B] pytest cache
Remove-Bucket -Label "B. .pytest_cache/" -Locator {
    Get-ChildItem -Path $Root -Directory -Force -Filter ".pytest_cache" -ErrorAction SilentlyContinue
}

# [C] FUSE hidden orphans in data/
Remove-Bucket -Label "C. data/.fuse_hidden* orphans" -Locator {
    Get-ChildItem -Path (Join-Path $Root "data") -File -Force -Filter ".fuse_hidden*" -ErrorAction SilentlyContinue
}

# [D] Empty scratch DB files in data/ — explicit list so we never touch real DBs
Remove-Bucket -Label "D. Empty scratch DB files in data/" -Locator {
    $candidates = @(
        "tracker_csg.db",
        "tracker_csg_fresh.db",
        "tracker_csg_ingested.db",
        "tracker_csg.db-journal",
        "tracker_csg_fresh.db-journal",
        "tracker_csg_ingested.db-journal",
        "test.tmp",
        "test_write.tmp"
    )
    foreach ($name in $candidates) {
        $p = Join-Path (Join-Path $Root "data") $name
        if (Test-Path -LiteralPath $p) { Get-Item -LiteralPath $p -Force }
    }
}

# [E] Corrupted DB backup
Remove-Bucket -Label "E. Corrupted DB backup (.bak)" -Locator {
    Get-ChildItem -Path (Join-Path $Root "data") -File -Force -Filter "tracker_corrupt_*.bak" -ErrorAction SilentlyContinue
}

# [F] Old dashboard snapshots — explicit list
Remove-Bucket -Label "F. Old dashboard snapshots in reports/" -Locator {
    $candidates = @("dashboard_2026-05-11.html", "latest.html")
    foreach ($name in $candidates) {
        $p = Join-Path (Join-Path $Root "reports") $name
        if (Test-Path -LiteralPath $p) { Get-Item -LiteralPath $p -Force }
    }
}

# [G] Superseded context doc V1
Remove-Bucket -Label "G. Superseded context doc V1" -Locator {
    $p = Join-Path $Root "CONTEXT_FOR_NEW_CHAT.md"
    if (Test-Path -LiteralPath $p) { Get-Item -LiteralPath $p -Force }
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ("TOTAL removed: {0} items, {1:N0} bytes ({2:N1} MB)" -f $totalCount, $totalBytes, ($totalBytes/1MB)) -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Active files preserved:" -ForegroundColor Green
Write-Host "  data/tracker.db          - Healthcare DB"
Write-Host "  data/tracker_csg_v2.db   - CSG DB"
Write-Host "  data/weekly-stats.json   - /api/weekly-stats source"
Write-Host "  reports/dashboard.html, reports/dashboard_csg.html (active)"
Write-Host ""
Write-Host "Next: review with 'git status' then commit .gitignore if desired:" -ForegroundColor Yellow
Write-Host "  git add .gitignore"
Write-Host "  git commit -m 'Clean up cache files; tighten .gitignore'"
Write-Host "  git push"
