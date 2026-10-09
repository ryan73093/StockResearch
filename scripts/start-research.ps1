param([Parameter(Mandatory = $true)][ValidateSet("data-a", "daily", "model", "statements", "statements-refresh", "rl", "turnover", "exits", "execution", "blends", "rl-sleeves", "model-60", "t0hunt", "ai-researcher", "news-events", "lowdd", "largecap", "largecap2", "evidence", "scan", "scan4", "factors-recent", "archive", "seed", "factors", "finmind")][string]$Name)
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
