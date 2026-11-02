param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Reason,
    [switch]$DryRun
)
# 2026-11-03 (使用者：目前用舊算法在跑的選手池重跑停不掉……這件事會發生就是很荒謬): the project's own research flows stop
# through this script, as the services stop through stop-services.ps1 - never by ending processes by hand. Only the
# processes proven to be the flow's: the pipeline (powershell running research-pipeline.ps1 -Name <Name>, the PID in
# instance\research\pipelines\<Name>.json) and its descendants (the project's python steps, their multiprocessing
# workers, the console host), each created after its parent (a reused PID is not the flow's). The record becomes
# "stopped" with the reason (resume-research.ps1 leaves it stopped), the steps' jobs and waits too, and the flow's log
# says so. Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop-research.ps1 -Name rolling -Reason "..."
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$research = Join-Path $root "instance\research"
$recordPath = Join-Path $research "pipelines\$Name.json"
if (-not (Test-Path -LiteralPath $recordPath)) { throw "沒有這個研究流程的紀錄：$recordPath" }
$record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
$all = @(Get-CimInstance Win32_Process)
$pattern = "research-pipeline\.ps1 -Name " + [regex]::Escape($Name) + "(\s|$)"
$pipelines = @($all | Where-Object { $_.Name -eq "powershell.exe" -and $_.CommandLine -match $pattern })
$byParent = @{}
foreach ($process in $all) {
    $key = [int]$process.ParentProcessId
    if (-not $byParent.ContainsKey($key)) { $byParent[$key] = New-Object System.Collections.ArrayList }
    [void]$byParent[$key].Add($process)
}
$tree = New-Object System.Collections.ArrayList
$foreign = New-Object System.Collections.ArrayList
$queue = New-Object System.Collections.Queue
foreach ($pipeline in $pipelines) { [void]$tree.Add($pipeline); $queue.Enqueue($pipeline) }
while ($queue.Count -gt 0) {
    $parent = $queue.Dequeue()
    if (-not $byParent.ContainsKey([int]$parent.ProcessId)) { continue }
    foreach ($child in $byParent[[int]$parent.ProcessId]) {
        if ($child.CreationDate -lt $parent.CreationDate) { continue }           # an older process: a reused PID
        $ours = ($child.Name -eq "conhost.exe") -or
            ($child.Name -eq "python.exe" -and $child.CommandLine -match "quant_platform|multiprocessing\.spawn")
        if (-not $ours) { [void]$foreign.Add($child); continue }
        [void]$tree.Add($child)
        $queue.Enqueue($child)
    }
}
$stamp = Get-Date
$lines = @("研究流程 $Name：狀態 $($record.status)，紀錄的 PID $($record.pid)")
foreach ($process in $tree) {
    $command = ($process.CommandLine -replace '\s+', ' ')
    $lines += "  {0,6} {1} {2}" -f $process.ProcessId, $process.Name, $command.Substring(0, [Math]::Min(140, $command.Length))
}
foreach ($process in $foreign) {
    $lines += "  {0,6} {1}：不是本專案研究流程的程序，不動" -f $process.ProcessId, $process.Name
}
$lines | ForEach-Object { Write-Output $_ }
if ($DryRun) { return }

# the pipeline first, so it starts no next step; then its steps
foreach ($process in $tree) {
    try { Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop } catch { }
}
$ended = @($tree | ForEach-Object { [int]$_.ProcessId })
$jobs = Join-Path $research "jobs"
foreach ($file in Get-ChildItem -LiteralPath $jobs -Filter "*.json" -ErrorAction SilentlyContinue) {
    try { $job = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { continue }
    if ($job.status -ne "running" -or [int]$job.pid -notin $ended) { continue }
    $job.status = "stopped"
    $job | Add-Member -NotePropertyName summary -NotePropertyValue "停止：$Reason" -Force
    $job | Add-Member -NotePropertyName finished_at -NotePropertyValue $stamp.ToString("yyyy-MM-ddTHH:mm:sszzz") -Force
    [System.IO.File]::WriteAllText($file.FullName, ($job | ConvertTo-Json -Depth 6), (New-Object System.Text.UTF8Encoding($false)))
}
$record.status = "stopped"
$record | Add-Member -NotePropertyName finished_at -NotePropertyValue $stamp.ToString("s") -Force
$record | Add-Member -NotePropertyName reason -NotePropertyValue $Reason -Force
[System.IO.File]::WriteAllText($recordPath, ($record | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))
$log = Get-ChildItem -LiteralPath (Join-Path $research "logs") -Filter "$Name-*-pipeline.log" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime | Select-Object -Last 1
$note = "$($stamp.ToString('yyyy-MM-dd HH:mm:ss')) 停止（scripts\stop-research.ps1，結束 $($ended.Count) 個程序）：$Reason"
if ($log) { Add-Content -LiteralPath $log.FullName -Value $note -Encoding UTF8 }
Write-Output $note
