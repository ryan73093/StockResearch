[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 240
)

# Starts this project's supervisor through the "StockResearchLocalServices"
# scheduled task so the services run in the user's session, detached from any
# terminal or AI tool session. Falls back to WMI process creation when the
# task is missing.

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$supervisorScript = Join-Path $projectRoot 'scripts\run_local_services.ps1'
$runtimeRoot = Join-Path $projectRoot '.runtime\services'
$taskName = 'StockResearchLocalServices'
$serviceExecutables = @('quant-web.exe', 'quant-api.exe', 'quant-worker.exe') |
    ForEach-Object { Join-Path $projectRoot ".venv\Scripts\$_" }

$supervisors = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($supervisorScript)
})
if ($supervisors.Count -gt 0) {
    Write-Host "Supervisor already running (PID $($supervisors[0].ProcessId))." -ForegroundColor Cyan
    exit 0
}
$orphans = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and ($serviceExecutables -contains $_.ExecutablePath)
})
if ($orphans.Count -gt 0) {
    throw "Unsupervised service processes are running ($(($orphans | ForEach-Object ProcessId) -join ', ')); run scripts\stop-services.ps1 first."
}
Remove-Item -LiteralPath (Join-Path $runtimeRoot 'stop.flag') -Force -ErrorAction SilentlyContinue

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Start-ScheduledTask -TaskName $taskName
    Write-Host "Started scheduled task $taskName."
}
else {
    $commandLine = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$supervisorScript`""
    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $commandLine
        CurrentDirectory = $projectRoot
    }
    if ($result.ReturnValue -ne 0) {
        throw "Win32_Process.Create failed with code $($result.ReturnValue)"
    }
    Write-Host "Started supervisor via WMI (PID $($result.ProcessId))."
}

$probes = @{
    web = 'http://127.0.0.1:5000/health'
    api = 'http://127.0.0.1:8000/api/v1/health'
}
$ready = @{}
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline -and $ready.Count -lt $probes.Count) {
    Start-Sleep -Seconds 3
    foreach ($name in $probes.Keys) {
        if ($ready.ContainsKey($name)) { continue }
        try {
            $watch = [Diagnostics.Stopwatch]::StartNew()
            $response = Invoke-WebRequest -UseBasicParsing -Uri $probes[$name] -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                $ready[$name] = $watch.ElapsedMilliseconds
            }
        }
        catch {
            # Not listening yet.
        }
    }
}

foreach ($name in 'supervisor', 'web', 'api', 'worker', 'tunnel') {
    $pidPath = Join-Path $runtimeRoot "$name.pid"
    $value = if (Test-Path -LiteralPath $pidPath) { (Get-Content -LiteralPath $pidPath -Raw).Trim() } else { '-' }
    $state = if ($ready.ContainsKey($name)) { "health 200 in $($ready[$name]) ms" } else { '' }
    Write-Host ("{0,-10} PID {1,-8} {2}" -f $name, $value, $state)
}
if ($ready.Count -lt $probes.Count) {
    throw "Services not healthy within $TimeoutSeconds seconds; see instance\*.stderr.log and instance\supervisor.log"
}
Write-Host 'StockResearch services are healthy.' -ForegroundColor Green
