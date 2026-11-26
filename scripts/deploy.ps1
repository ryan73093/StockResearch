[CmdletBinding()]
param(
    [switch]$DuringTradingWindow,
    [switch]$DuringWorkerJob,
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
if ($DuringWorkerJob) { $stopArguments += '-DuringWorkerJob' }

# 2026-11-26: the first clicks after a deploy computed the pages' views inside the click (the home page 30.4 s that
# morning). They are computed here with the code about to be installed, while the old site still serves; every version
# of the code keeps its own folder (dashboard/warm.py), so the new web reads them from its first request. A deploy takes
# a minute or two longer; the time the site is down is the same.
if (-not $HomeAuthOnly) {
    $checkArguments = $stopArguments + '-CheckOnly'
    & powershell.exe @checkArguments
    if ($LASTEXITCODE -ne 0) { throw 'The services may not be stopped now; nothing was computed or installed.' }
    Write-Host 'Computing the page views with the new code (the site keeps serving)...'
    Push-Location $projectRoot
    try {
        $env:PYTHONPATH = Join-Path $projectRoot 'src'
        $env:PYTHONUTF8 = '1'
        # Python logs to stderr; with 'Stop' a redirected caller would turn a log line into a stopped deploy
        $ErrorActionPreference = 'Continue'
        & $python -m quant_platform.dashboard.warm (Join-Path $projectRoot 'instance\research')
        $computed = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($computed -ne 0) { throw 'The new code could not compute the page views; the services are unchanged.' }
    }
    finally {
        Remove-Item Env:\PYTHONPATH, Env:\PYTHONUTF8 -ErrorAction SilentlyContinue
        Pop-Location
    }
}

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
    if (-not $HomeAuthOnly) {
        # another session may have changed a source file after the views above were computed: they are then of another
        # version and the new web would not read them. Compute them again with what was installed (only then does the
        # site stay down a minute or two longer). A failure here does not keep the services down.
        $researchDir = Join-Path $projectRoot 'instance\research'
        $env:PYTHONUTF8 = '1'
        $ErrorActionPreference = 'Continue'
        & $python -c "import sys; from quant_platform.dashboard import warm; sys.exit(0 if warm.prepared(sys.argv[1]) else 3)" $researchDir
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'The code changed while the page views were computed; computing them with the installed code...' -ForegroundColor Yellow
            & $python -m quant_platform.dashboard.warm $researchDir
            if ($LASTEXITCODE -ne 0) { Write-Host 'Could not compute them; the first clicks will compute them as before.' -ForegroundColor Yellow }
        }
        $ErrorActionPreference = 'Stop'
        Remove-Item Env:\PYTHONUTF8 -ErrorAction SilentlyContinue
    }
}
finally {
    Pop-Location
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start-services.ps1')
if ($LASTEXITCODE -ne 0) { throw 'start-services.ps1 reported unhealthy services.' }
