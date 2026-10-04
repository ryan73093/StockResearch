# Database slimming (roadmap S9-W06, owner 2026-10-04): stop the services, copy the database, empty the
# archived legacy research tables and VACUUM (scripts/slim_legacy_tables.py), then always start the services
# again. The log and the backup path go to instance\research\logs\slim-<time>.log. Not between 13:30 and
# 14:40 (stop-services.ps1 refuses). Start detached:
#   Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts\slim-legacy-tables.ps1'
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logs = Join-Path $root "instance\research\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$log = Join-Path $logs "slim-$stamp.log"
function Write-Log([string]$Text) { "$(Get-Date -Format s) $Text" | Out-File -Append -Encoding utf8 $log }

try {
    Write-Log "stop services"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "scripts\stop-services.ps1") 2>&1 | Out-File -Append -Encoding utf8 $log
    if ($LASTEXITCODE -ne 0) { throw "stop-services failed ($LASTEXITCODE)" }
    $backup = Join-Path $root "instance\backups\pre-slim-$stamp"
    New-Item -ItemType Directory -Force $backup | Out-Null
    foreach ($suffix in @("", "-wal", "-shm")) {
        $source = Join-Path $root "instance\quant_platform.db$suffix"
        if (Test-Path $source) { Copy-Item $source $backup }
    }
    Write-Log "backup: $backup"
    $env:PYTHONPATH = "src"
    $env:PYTHONIOENCODING = "utf-8"
    & (Join-Path $root ".venv\Scripts\python.exe") (Join-Path $root "scripts\slim_legacy_tables.py") --backup $backup 2>&1 | Out-File -Append -Encoding utf8 $log
    Write-Log "slim exit $LASTEXITCODE"
}
catch {
    Write-Log "error: $_"
}
finally {
    Write-Log "start services"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "scripts\start-services.ps1") 2>&1 | Out-File -Append -Encoding utf8 $log
    Write-Log "done"
}
