param([ValidateSet("seed", "factors", "finmind")][string]$Name = "seed")
# Long research runs, detached from any terminal or assistant session (使用者 2026-10-04):
# start with
#   Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts\research-pipeline.ps1','-Name','seed'
# Progress shows on the website (研究 › 執行中的程式); each step's output goes to instance\research\logs.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
$python = Join-Path $root ".venv\Scripts\python.exe"
$logs = Join-Path $root "instance\research\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

function Invoke-Step([string]$Label, [string[]]$Arguments) {
    $out = Join-Path $logs "$Name-$stamp-$Label.log"
    $err = Join-Path $logs "$Name-$stamp-$Label.err.log"
    Start-Process -FilePath $python -ArgumentList $Arguments -WorkingDirectory $root -NoNewWindow -Wait `
        -RedirectStandardOutput $out -RedirectStandardError $err
}

switch ($Name) {
    "seed" {
        # The owner's account: NT$300,000 to start, then NT$10,000 on the 5th of every month (2026-10-04).
        $flow = @("--broker", "cathay", "--initial", "300000", "--monthly", "10000")
        Invoke-Step "1-screen" (@("-m", "quant_platform.research", "stocks", "--period", "development", "--name", "all", "--screen") + $flow)
        Invoke-Step "2-windows" (@("-m", "quant_platform.research", "stocks", "--period", "development", "--name", "all", "--top", "40") + $flow)
        Invoke-Step "3-validation" (@("-m", "quant_platform.research", "stocks", "--period", "validation", "--name", "all", "--passed") + $flow)
        Invoke-Step "4-final" (@("-m", "quant_platform.research", "stocks", "--period", "holdout", "--name", "qualified") + $flow)
    }
    "factors" { Invoke-Step "factors" @("-m", "quant_platform.research", "factors") }
    "finmind" { Invoke-Step "finmind" @("-m", "quant_platform.research.history", "finmind") }
}
