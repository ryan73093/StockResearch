param([Parameter(Mandatory = $true)][ValidateSet("data-a", "daily", "model", "statements", "statements-refresh", "rl", "turnover", "exits", "execution", "blends", "rl-sleeves", "model-60", "t0hunt", "ai-researcher", "news-events", "lowdd", "largecap", "largecap2", "evidence", "scan", "scan4", "robots", "resume4", "robot2", "wide4", "consensus", "trials", "leakcheck", "devconsensus", "modelf", "costswitch", "rawv2", "techmodel", "hypotheses", "minhold", "engine13", "engine131", "dividends", "engine140", "t0scan5", "timefactors", "factormom", "audit", "robot20", "shortdata", "shortfactors", "alerts", "robot21", "rebuild141", "lendingdata", "lendingfactors", "rebuild142", "corporate", "issuance", "dealermodel", "robot30", "robot40", "rebuild143", "dealer2", "deep1", "robot41", "reductions", "robot31", "rebuild150", "worst", "dlrisk", "factors-recent", "archive", "seed", "factors", "finmind")][string]$Name)
# Start a long research run outside the assistant's process tree (2026-10-06): at 02:36 a Claude desktop
# update closed the pipeline that had been started with Start-Process from the assistant's shell, and the
# FinMind download with it, half-way. Win32_Process.Create makes the new process a child of the WMI
# service instead, so it keeps running whatever happens to the session that started it. No scheduled
# task or other setting is created. Progress: the website's 研究 › 執行中的程式; output: instance\research\logs.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root "scripts\research-pipeline.ps1"
$command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Name $Name"
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $command; CurrentDirectory = $root }
if ($result.ReturnValue -ne 0) {
    throw "Win32_Process.Create failed with $($result.ReturnValue)"
}
Write-Output "Started research pipeline '$Name' (PID $($result.ProcessId)); progress: website Research > Jobs."
