[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$scriptsRoot = Join-Path $projectRoot '.venv\Scripts'
$instanceRoot = Join-Path $projectRoot 'instance'

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

foreach ($definition in $serviceDefinitions) {
    if (-not (Test-Path -LiteralPath $definition.Executable -PathType Leaf)) {
        throw "Missing service executable: $($definition.Executable)"
    }
}

New-Item -ItemType Directory -Path $instanceRoot -Force | Out-Null
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
    $runningServices[$name] = [pscustomobject]@{
        Process = $process
        StartedAt = Get-Date
        FailedProbes = 0
    }
}

foreach ($definition in $serviceDefinitions) {
    Start-StockResearchService -Definition $definition
}

while ($true) {
    Start-Sleep -Seconds 5
    foreach ($definition in $serviceDefinitions) {
        $entry = $runningServices[$definition.Name]
        if ($null -eq $entry -or $entry.Process.HasExited) {
            Start-StockResearchService -Definition $definition
            continue
        }
        if ($null -eq $definition.ProbeUri) {
            continue
        }
        if (((Get-Date) - $entry.StartedAt).TotalSeconds -lt 30) {
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
            # A failed health probe is counted below. Three consecutive failures
            # are required before replacing the complete process tree.
        }
        $entry.FailedProbes += 1
        if ($entry.FailedProbes -lt 3) {
            continue
        }
        & "$env:SystemRoot\System32\taskkill.exe" `
            /PID $entry.Process.Id /T /F 2>$null | Out-Null
        Start-StockResearchService -Definition $definition
    }
}
