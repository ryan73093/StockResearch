param([switch]$DryRun)
# Resume research pipelines a reboot killed (2026-10-14: Windows Update restarted the computer at 04:29 and the
# engine140 and t0scan5 pipelines were gone). research-pipeline.ps1 writes instance\research\pipelines\<name>.json
# when it starts (status running, its PID) and when it ends (done). A record still "running" whose process is
# gone and which started before the last boot was cut by that boot: it is started again with start-research.ps1.
# A pipeline that ended any other way is left alone. Every pipeline resumes where it can (downloads keep their
# files, a rule already run on the same data is not run again, a finished screen is not screened again).
# Called by the services supervisor when it starts (run_local_services.ps1); safe to run by hand.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$folder = Join-Path $root "instance\research\pipelines"
if (-not (Test-Path $folder)) { return }
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
foreach ($file in Get-ChildItem $folder -Filter "*.json") {
    try {
        $record = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        continue
    }
    # 2026-10-24: "interrupted" - a step the system ended (research-pipeline.ps1 Invoke-Step) - resumes too
    if ($record.status -notin @("running", "interrupted")) { continue }
    $started = [datetime]::Parse($record.started_at)
    if ($record.status -eq "running" -and $started -ge $boot) { continue }   # began after this boot: not a reboot's casualty
    $alive = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$record.pid)" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*research-pipeline.ps1*-Name $($record.name)*" }
    if ($alive) { continue }
    if ($DryRun) {
        Write-Output "would resume $($record.name) (started $($record.started_at), boot $boot)"
        continue
    }
    $record.status = "resumed"
    $record | Add-Member -NotePropertyName resumed_at -NotePropertyValue (Get-Date).ToString("s") -Force
    [System.IO.File]::WriteAllText($file.FullName, ($record | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))
    & (Join-Path $root "scripts\start-research.ps1") -Name $record.name
}
