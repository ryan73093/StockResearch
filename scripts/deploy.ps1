[CmdletBinding()]
param(
    [switch]$DuringTradingWindow,
    [switch]$HomeAuthOnly
)

# Deploy code changes in the only safe order: stop services (so no launcher
# executable is locked) -> reinstall the package -> start services.
# Installing while services run can leave the package half-uninstalled.

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if ($HomeAuthOnly) {
    & $python (Join-Path $PSScriptRoot 'deploy-home-auth.py') --check-only
    if ($LASTEXITCODE -ne 0) { throw 'Home authentication preflight failed; services are unchanged.' }
}

$stopArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'stop-services.ps1'))
if ($DuringTradingWindow) { $stopArguments += '-DuringTradingWindow' }
& powershell.exe @stopArguments
if ($LASTEXITCODE -ne 0) { throw 'stop-services.ps1 failed; nothing was installed.' }

Push-Location $projectRoot
try {
    if ($HomeAuthOnly) {
        & $python (Join-Path $PSScriptRoot 'deploy-home-auth.py')
    } else {
        & $python -m pip install . -q --disable-pip-version-check
    }
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed; services are stopped. Fix the error and run this script again.' }
    & $python -c "import quant_platform.dashboard.app, quant_platform.scheduler.runner"
    if ($LASTEXITCODE -ne 0) { throw 'Installed package does not import; services are stopped.' }
}
finally {
    Pop-Location
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start-services.ps1')
if ($LASTEXITCODE -ne 0) { throw 'start-services.ps1 reported unhealthy services.' }
