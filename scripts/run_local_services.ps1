[CmdletBinding()]
param()

# Supervises the web, API and worker processes of this project only.
# Started by the "StockResearchLocalServices" scheduled task (at logon) or by
# scripts/start-services.ps1. scripts/stop-services.ps1 asks it to stop by
# creating .runtime\services\stop.flag.

$ErrorActionPreference = 'Stop'
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
Write-SupervisorLog 'Supervisor started.'

# Child Python processes write UTF-8 logs instead of the console code page.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$runningServices = @{}

function Start-StockResearchService {
    param([hashtable]$Definition)

    $name = $Definition.Name
    $process = Start-Process `
        -FilePath $Definition.Executable `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput (Join-Path $instanceRoot "$name.stdout.log") `
        -RedirectStandardError (Join-Path $instanceRoot "$name.stderr.log") `
        -WindowStyle Hidden `
        -PassThru
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
