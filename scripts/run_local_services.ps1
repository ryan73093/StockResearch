[CmdletBinding()]
param([switch]$Headless)

# Supervises the web, API and worker processes of this project only.
# Started by the "StockResearchLocalServices" scheduled task (at logon) or by
# scripts/start-services.ps1. scripts/stop-services.ps1 asks it to stop by
# creating .runtime\services\stop.flag.

$ErrorActionPreference = 'Stop'

# 2026-10-21: with Windows Terminal as the default terminal (Windows 11) the scheduled task's -WindowStyle Hidden is
# ignored and the supervisor showed as a blank powershell window after every deploy; closing it would stop the site
# (the owner closed such windows on 10-20). The supervisor starts itself again with no window at all (conhost
# --headless, created through WMI so it is outside the task) and this visible copy exits.
if (-not $Headless) {
    $command = "conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Headless"
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $command; CurrentDirectory = (Split-Path -Parent $PSScriptRoot); ProcessStartupInformation = $startup }
    if ($created.ReturnValue -ne 0) {
        throw "Could not start the supervisor without a window: Win32_Process.Create returned $($created.ReturnValue)"
    }
    exit 0
}

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$scriptsRoot = Join-Path $projectRoot '.venv\Scripts'
$instanceRoot = Join-Path $projectRoot 'instance'
$runtimeRoot = Join-Path $projectRoot '.runtime\services'
$supervisorPidPath = Join-Path $runtimeRoot 'supervisor.pid'
$stopFlagPath = Join-Path $runtimeRoot 'stop.flag'
$supervisorLog = Join-Path $instanceRoot 'supervisor.log'

$serviceDefinitions = @(
    @{
        Name = 'web'
        Executable = Join-Path $scriptsRoot 'quant-web.exe'
        ProbeUri = 'http://127.0.0.1:5000/health'
    },
    @{
        Name = 'api'
        Executable = Join-Path $scriptsRoot 'quant-api.exe'
        ProbeUri = 'http://127.0.0.1:8000/api/v1/health'
    },
    @{
        Name = 'worker'
        Executable = Join-Path $scriptsRoot 'quant-worker.exe'
        ProbeUri = $null
    }
)

# The public tunnel runs only when the site enforces Cloudflare Access, so a
# development-mode origin (no login gate) is never exposed to the internet.
$tunnelCredential = Join-Path $projectRoot '.runtime\cloudflared\stockresearch-pimi-sunsun.json'
$envFile = Join-Path $projectRoot '.env'
$accessEnforced = (Test-Path -LiteralPath $envFile) -and (
    Select-String -LiteralPath $envFile -Pattern '^\s*AUTH_MODE\s*=\s*cloudflare-access\s*$' -Quiet
)
$cloudflared = @(
    (Get-Command cloudflared.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
    'C:\Program Files (x86)\cloudflared\cloudflared.exe',
    'C:\Program Files\cloudflared\cloudflared.exe',
    (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\cloudflared.exe'),
    (Join-Path (Split-Path -Parent $projectRoot) 'YtSummary\.tools\cloudflared\cloudflared.exe'),
    # 2026-10-26: moved to C:\Dev (no YtSummary beside it); the machine's shared copy, as C:\Dev\VectorDB finds it
    (Join-Path $env:USERPROFILE 'Project\YtSummary\.tools\cloudflared\cloudflared.exe')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if ($accessEnforced -and $cloudflared -and (Test-Path -LiteralPath $tunnelCredential)) {
    $serviceDefinitions += @{
        Name = 'tunnel'
        Executable = $cloudflared
        Arguments = @(
            'tunnel', '--no-autoupdate', 'run',
            '--credentials-file', "`"$tunnelCredential`"",
            '--url', 'http://127.0.0.1:5000',
            'stockresearch-pimi-sunsun'
        )
        ProbeUri = $null
    }
}

New-Item -ItemType Directory -Path $instanceRoot, $runtimeRoot -Force | Out-Null

function Write-SupervisorLog([string]$Message) {
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $PID, $Message
    Add-Content -LiteralPath $supervisorLog -Value $line -Encoding UTF8
}

# One supervisor per project: a second copy would start duplicate services.
if (Test-Path -LiteralPath $supervisorPidPath) {
    $existingPid = (Get-Content -LiteralPath $supervisorPidPath -Raw).Trim()
    if ($existingPid -match '^\d+$' -and [int]$existingPid -ne $PID) {
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$existingPid" -ErrorAction SilentlyContinue
        if ($existing -and $existing.CommandLine -like '*run_local_services.ps1*') {
            Write-SupervisorLog "Another supervisor ($existingPid) is running; exiting."
            exit 0
        }
    }
}
foreach ($definition in $serviceDefinitions) {
    if (-not (Test-Path -LiteralPath $definition.Executable -PathType Leaf)) {
        throw "Missing service executable: $($definition.Executable)"
    }
}
Remove-Item -LiteralPath $stopFlagPath -Force -ErrorAction SilentlyContinue
Set-Content -LiteralPath $supervisorPidPath -Value $PID -Encoding ascii
$tunnelState = if ($serviceDefinitions.Name -contains 'tunnel') { 'with tunnel' } else { 'without tunnel (Access not enforced or credential missing)' }
Write-SupervisorLog "Supervisor started $tunnelState."
# 2026-10-14: research pipelines a reboot cut short start again (Windows Update restarted the computer at 04:29)
try {
    & (Join-Path $PSScriptRoot "resume-research.ps1") | ForEach-Object { Write-SupervisorLog "Research: $_" }
} catch {
    Write-SupervisorLog "Research resume failed: $_"
}

# Child Python processes write UTF-8 logs instead of the console code page.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$runningServices = @{}

function Start-StockResearchService {
    param([hashtable]$Definition)

    $name = $Definition.Name
    $startArguments = @{
        FilePath = $Definition.Executable
        WorkingDirectory = $projectRoot
        RedirectStandardOutput = (Join-Path $instanceRoot "$name.stdout.log")
        RedirectStandardError = (Join-Path $instanceRoot "$name.stderr.log")
        WindowStyle = 'Hidden'
        PassThru = $true
    }
    if ($Definition.Arguments) {
        $startArguments.ArgumentList = $Definition.Arguments
    }
    $process = Start-Process @startArguments
    Set-Content -LiteralPath (Join-Path $runtimeRoot "$name.pid") -Value $process.Id -Encoding ascii
    $runningServices[$name] = [pscustomobject]@{
        Process = $process
        StartedAt = Get-Date
        FailedProbes = 0
    }
    Write-SupervisorLog "Started $name (PID $($process.Id))."
}

function Stop-StockResearchService {
    param([string]$Name)

    $entry = $runningServices[$Name]
    if ($null -ne $entry -and -not $entry.Process.HasExited) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $entry.Process.Id /T /F 2>$null | Out-Null
        Write-SupervisorLog "Stopped $Name (PID $($entry.Process.Id))."
    }
    Remove-Item -LiteralPath (Join-Path $runtimeRoot "$Name.pid") -Force -ErrorAction SilentlyContinue
}

try {
    foreach ($definition in $serviceDefinitions) {
        Start-StockResearchService -Definition $definition
    }

    while ($true) {
        Start-Sleep -Seconds 5
        if (Test-Path -LiteralPath $stopFlagPath) {
            Write-SupervisorLog 'Stop flag found; stopping services.'
            break
        }
        foreach ($definition in $serviceDefinitions) {
            $entry = $runningServices[$definition.Name]
            if ($null -eq $entry -or $entry.Process.HasExited) {
                Write-SupervisorLog "$($definition.Name) exited; restarting."
                Start-StockResearchService -Definition $definition
                continue
            }
            if ($null -eq $definition.ProbeUri) {
                continue
            }
            # Building the container against the large SQLite file can take a
            # while; give a fresh process two minutes before probing it.
            if (((Get-Date) - $entry.StartedAt).TotalSeconds -lt 120) {
                continue
            }
            try {
                $response = Invoke-WebRequest `
                    -UseBasicParsing `
                    -Uri $definition.ProbeUri `
                    -TimeoutSec 5
                if ($response.StatusCode -eq 200) {
                    $entry.FailedProbes = 0
                    continue
                }
            }
            catch {
                # Counted below; three consecutive failures replace the process tree.
            }
            $entry.FailedProbes += 1
            if ($entry.FailedProbes -lt 3) {
                continue
            }
            Write-SupervisorLog "$($definition.Name) failed 3 health probes; restarting."
            Stop-StockResearchService -Name $definition.Name
            Start-StockResearchService -Definition $definition
        }
    }
}
finally {
    foreach ($definition in $serviceDefinitions) {
        Stop-StockResearchService -Name $definition.Name
    }
    Remove-Item -LiteralPath $stopFlagPath, $supervisorPidPath -Force -ErrorAction SilentlyContinue
    Write-SupervisorLog 'Supervisor exited.'
}
