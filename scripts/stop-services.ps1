[CmdletBinding()]
param(
    # Allow stopping during the 13:30-14:40 after-hours decision window.
    [switch]$DuringTradingWindow,
    # Allow stopping while the worker runs a registered job or during 15:14-15:50 (2026-10-06).
    [switch]$DuringWorkerJob
)

# Stops this project's supervisor, web, API and worker. Every process is
# identified by its full executable path or command line under this project
# before it is stopped; other projects on the host are never touched.

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $projectRoot '.runtime\services'
$stopFlagPath = Join-Path $runtimeRoot 'stop.flag'
$supervisorPidPath = Join-Path $runtimeRoot 'supervisor.pid'
$serviceExecutables = @('quant-web.exe', 'quant-api.exe', 'quant-worker.exe') |
    ForEach-Object { Join-Path $projectRoot ".venv\Scripts\$_" }
$supervisorScript = Join-Path $projectRoot 'scripts\run_local_services.ps1'

$taipeiNow = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'Taipei Standard Time')
$minutes = $taipeiNow.Hour * 60 + $taipeiNow.Minute
$isWeekday = $taipeiNow.DayOfWeek -notin @([DayOfWeek]::Saturday, [DayOfWeek]::Sunday)
if ($isWeekday -and $minutes -ge (13 * 60 + 30) -and $minutes -lt (14 * 60 + 40) -and -not $DuringTradingWindow) {
    throw 'Refusing to stop services during 13:30-14:40 Taipei; pass -DuringTradingWindow to override.'
}
# 2026-10-06: a deploy at 14:41 stopped the 14:40 news collection half-way. The worker's own jobs that
# register in instance\research\jobs (news 14:40, chips 21:30, model retrain 22:45) and the unregistered
# 15:15-15:45 ones (research refresh, forward record, stock snapshot) are not interrupted.
if (-not $DuringWorkerJob) {
    if ($isWeekday -and $minutes -ge (15 * 60 + 14) -and $minutes -lt (15 * 60 + 50)) {
        throw 'Refusing to stop services during 15:14-15:50 Taipei (research refresh, forward record, stock snapshot); pass -DuringWorkerJob to override.'
    }
    $jobsDir = Join-Path $projectRoot 'instance\research\jobs'
    if (Test-Path -LiteralPath $jobsDir) {
        $busy = @(Get-ChildItem -LiteralPath $jobsDir -Filter '*.json' | ForEach-Object {
            try { $job = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return }
            if ($job.status -eq 'running' -and "$($job.command)" -like 'scheduler *' -and $job.pid -and
                (Get-Process -Id ([int]$job.pid) -ErrorAction SilentlyContinue)) { "$($job.command)" }
        })
        if ($busy.Count -gt 0) {
            throw "Refusing to stop services while the worker runs: $($busy -join ', '); pass -DuringWorkerJob to override."
        }
    }
}

function Get-ProjectServiceProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and ($serviceExecutables -contains $_.ExecutablePath)
    }
}

function Get-ProjectSupervisors {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($supervisorScript)
    }
}

function Get-ProjectTunnels {
    # Only the cloudflared started with this project's credential file; the
    # VectorDB and YtSummary tunnels on the same host are never matched.
    $credential = Join-Path $projectRoot '.runtime\cloudflared\stockresearch-pimi-sunsun.json'
    Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($credential)
    }
}

function Get-ProjectPythonChildren {
    # Console-script launchers start python.exe children whose command line
    # names the same launcher; match on the full launcher path.
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $commandLine = $_.CommandLine
        $commandLine -and ($serviceExecutables | Where-Object { $commandLine.Contains($_) })
    }
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

# 1. Ask the supervisor to stop its children and exit.
$supervisors = @(Get-ProjectSupervisors)
if ($supervisors.Count -gt 0) {
    Set-Content -LiteralPath $stopFlagPath -Value (Get-Date -Format o) -Encoding ascii
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline -and @(Get-ProjectSupervisors).Count -gt 0) {
        Start-Sleep -Seconds 2
    }
    foreach ($process in @(Get-ProjectSupervisors)) {
        Write-Host "Supervisor $($process.ProcessId) did not exit; stopping it." -ForegroundColor Yellow
        Stop-Process -Id $process.ProcessId -Force -Confirm:$false
    }
}

# 2. Stop any remaining (for example orphaned) service process trees.
foreach ($process in @(Get-ProjectServiceProcesses)) {
    Write-Host "Stopping $([IO.Path]::GetFileName($process.ExecutablePath)) tree (PID $($process.ProcessId))."
    & "$env:SystemRoot\System32\taskkill.exe" /PID $process.ProcessId /T /F 2>$null | Out-Null
}
foreach ($process in @(Get-ProjectTunnels)) {
    Write-Host "Stopping project tunnel (PID $($process.ProcessId))."
    Stop-Process -Id $process.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
foreach ($process in @(Get-ProjectPythonChildren)) {
    Write-Host "Stopping leftover python child (PID $($process.ProcessId))."
    Stop-Process -Id $process.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue
}

# 3. Verify.
Start-Sleep -Seconds 1
$remaining = @(Get-ProjectServiceProcesses) + @(Get-ProjectPythonChildren) + @(Get-ProjectSupervisors) + @(Get-ProjectTunnels)
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 5000, 8000 })
Remove-Item -LiteralPath $stopFlagPath, $supervisorPidPath -Force -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath $runtimeRoot -Filter '*.pid' -ErrorAction SilentlyContinue | Remove-Item -Force
if ($remaining.Count -gt 0) {
    throw "Processes still running: $(($remaining | ForEach-Object ProcessId) -join ', ')"
}
if ($listeners.Count -gt 0) {
    throw "Ports still listening: $(($listeners | ForEach-Object { "$($_.LocalPort)/PID $($_.OwningProcess)" }) -join ', ')"
}

# 4. Nothing of this project runs now: run records still "running" were interrupted.
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$closer = Join-Path $projectRoot 'scripts\close_interrupted_runs.py'
if ((Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $closer)) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $closed = & $python $closer 2>&1
        if ($closed) { Write-Host ($closed -join [Environment]::NewLine) }
    } catch {
        Write-Host "Could not close interrupted run records: $_" -ForegroundColor Yellow
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}
Write-Host 'StockResearch services stopped.' -ForegroundColor Green
