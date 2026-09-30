[CmdletBinding()]
param(
    # Allow stopping during the 13:30-14:40 after-hours decision window.
    [switch]$DuringTradingWindow
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
Start-Sleep -Seconds 2
foreach ($process in @(Get-ProjectPythonChildren)) {
    Write-Host "Stopping leftover python child (PID $($process.ProcessId))."
    Stop-Process -Id $process.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue
}

# 3. Verify.
Start-Sleep -Seconds 1
$remaining = @(Get-ProjectServiceProcesses) + @(Get-ProjectPythonChildren) + @(Get-ProjectSupervisors)
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
Write-Host 'StockResearch services stopped.' -ForegroundColor Green
