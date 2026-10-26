param([switch]$Prepare, [switch]$Cutover)
# One-time move of StockResearch to C:\Dev\StockResearch (2026-10-26, user: move it like the other projects; not under the
# user folder any more). Modelled on VectorDB's scripts\move-to-c-dev.ps1 (2026-10-07):
#   -Prepare : copy everything while the services run (the long part).
#   -Cutover : stop the research pipelines and the services, sync again, fix the paths that name the old folder (.env,
#              the StockResearchLocalServices task, Home's service-controls.json), deploy and start from C:\Dev, restart
#              the research pipelines there. On failure before the new services are up, the task and Home's file are put
#              back and the services start again from the old folder.
# The old folder is copied, never deleted; the owner deletes it after checking the new one.
$ErrorActionPreference = 'Stop'
$oldRoot = Join-Path $env:USERPROFILE 'Project\StockResearch'
$newRoot = 'C:\Dev\StockResearch'
$homeControls = 'C:\Dev\RyanPlatform\.runtime\service-controls.json'
$taskName = 'StockResearchLocalServices'
$logDir = Join-Path $env:TEMP 'stockresearch-cutover'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

function Step([string]$text) { Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $text) }

function Invoke-Robocopy([string]$name) {
  $log = Join-Path $logDir "robocopy-$name-$stamp.log"
  # build\pt_* are pytest scratch folders (28 GB), not project data; 4 threads (16 starved the shared Qdrant, 10/26)
  robocopy $oldRoot $newRoot /MIR /XJ /COPY:DAT /DCOPY:DAT /MT:4 /R:1 /W:1 /NP /NFL /NDL /XD 'pt_*' /UNILOG:$log | Out-Null
  $rc = $LASTEXITCODE
  if ($rc -ge 8) { throw "robocopy $name failed with code $rc (see $log)" }
  Step "robocopy $name rc=$rc (log $log)"
}

function Replace-InFile([string]$path, [string]$old, [string]$new) {
  if (-not (Test-Path -LiteralPath $path)) { return $false }
  $bytes = [IO.File]::ReadAllBytes($path)
  $bom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
  $text = [Text.Encoding]::UTF8.GetString($bytes)
  if ($bom) { $text = $text.Substring(1) }
  if (-not $text.Contains($old)) { return $false }
  [IO.File]::WriteAllText($path, $text.Replace($old, $new), (New-Object Text.UTF8Encoding($bom)))
  return $true
}

if ($Prepare) {
  Step "first copy $oldRoot -> $newRoot (services keep running)"
  New-Item -ItemType Directory -Force -Path $newRoot | Out-Null
  Invoke-Robocopy 'prepare'
  exit 0
}
if (-not $Cutover) { throw 'use -Prepare or -Cutover' }

$now = (Get-Date).TimeOfDay
if (($now -ge [TimeSpan]'13:30' -and $now -lt [TimeSpan]'14:40') -or ($now -ge [TimeSpan]'15:14' -and $now -lt [TimeSpan]'15:50')) {
  throw 'refused: 13:30-14:40 (after-close orders) or 15:14-15:50 (data update and forward records)'
}

# 1. The research pipelines started from the old folder: their names, then the processes (python children first).
$pipelines = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'powershell.exe' -and $_.CommandLine -like "*$oldRoot\scripts\research-pipeline.ps1*" })
$names = @($pipelines | ForEach-Object { if ($_.CommandLine -match '-Name\s+(\S+)') { $Matches[1] } } | Sort-Object -Unique)
Step ("research pipelines running: " + ($names -join ', '))
$ownTree = @($PID)
$parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId
if ($parent) { $ownTree += $parent }
foreach ($pipeline in $pipelines) {
  $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($pipeline.ProcessId)")
  foreach ($child in $children) {
    foreach ($grand in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($child.ProcessId)")) { Stop-Process -Id $grand.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Stop-Process -Id $pipeline.ProcessId -Force -ErrorAction SilentlyContinue
  $host2 = Get-CimInstance Win32_Process -Filter "ProcessId=$($pipeline.ParentProcessId)"
  if ($host2 -and $host2.Name -eq 'conhost.exe' -and $host2.CommandLine -like "*$oldRoot\scripts\research-pipeline.ps1*") { Stop-Process -Id $host2.ProcessId -Force -ErrorAction SilentlyContinue }
}
Start-Sleep 3

# 2. The services, through the project's own stop script (checks PIDs, executables, ports).
Step 'stopping the services'
& (Join-Path $oldRoot 'scripts\stop-services.ps1') -DuringWorkerJob
foreach ($port in 5000, 8000) { if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "port $port still in use" } }
$left = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$oldRoot*" -and $ownTree -notcontains $_.ProcessId -and $_.Name -ne 'bash.exe' -and $_.Name -ne 'claude.exe' })
if ($left) { throw ("still running from the old folder: " + (($left | ForEach-Object { "$($_.ProcessId) $($_.Name)" }) -join ', ')) }

$changedTask = $false
$changedHome = $false
$taskBackup = $null
try {
  # 3. Final sync.
  Invoke-Robocopy 'final'

  # 4. New-location fixes: stale PID files, the .env path, the logon task, Home's controls.
  Get-ChildItem -LiteralPath (Join-Path $newRoot '.runtime\services') -Filter '*.pid' -ErrorAction SilentlyContinue | Remove-Item -Force
  if (Replace-InFile (Join-Path $newRoot '.env') $oldRoot $newRoot) { Step '.env: old paths replaced' }
  foreach ($file in Get-ChildItem -LiteralPath (Join-Path $newRoot '.runtime') -Recurse -File -Include *.json, *.env, *.yml, *.yaml, *.txt -ErrorAction SilentlyContinue) {
    if ($file.Length -lt 1MB -and (Replace-InFile $file.FullName $oldRoot $newRoot)) { Step "runtime: $($file.Name) old paths replaced" }
  }
  $envText = [IO.File]::ReadAllText((Join-Path $newRoot '.env'))
  if ($envText.Contains($oldRoot)) { throw '.env still names the old folder' }

  $task = Get-ScheduledTask -TaskName $taskName
  $taskBackup = $task.Actions | ForEach-Object { @{ Execute = $_.Execute; Arguments = $_.Arguments; WorkingDirectory = $_.WorkingDirectory } }
  $action = New-ScheduledTaskAction -Execute $task.Actions[0].Execute -Argument ($task.Actions[0].Arguments.Replace($oldRoot, $newRoot))
  Set-ScheduledTask -TaskName $taskName -Action $action | Out-Null
  $changedTask = $true
  Step ("task action: " + (Get-ScheduledTask -TaskName $taskName).Actions[0].Arguments)

  if (Test-Path -LiteralPath $homeControls) {
    $backupDir = Join-Path (Split-Path -Parent $homeControls) "path-move-backup-stockresearch-$stamp"
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    Copy-Item -LiteralPath $homeControls -Destination $backupDir
    $oldJson = $oldRoot.Replace('\', '\\')
    $newJson = $newRoot.Replace('\', '\\')
    if (Replace-InFile $homeControls $oldJson $newJson) { $changedHome = $true; Step "Home service-controls.json updated (backup $backupDir)" }
  }

  # Secrets: owner, SYSTEM and Administrators only (as C:\Dev\VectorDB and C:\Dev\DBPostgreSQL).
  $user = "$env:USERDOMAIN\$env:USERNAME"
  icacls (Join-Path $newRoot '.runtime') /inheritance:r /grant:r "${user}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
  foreach ($file in Get-ChildItem -LiteralPath $newRoot -Filter '.env*' -File -Force | Where-Object { $_.Name -notlike '*.example' }) {
    icacls $file.FullName /inheritance:r /grant:r "${user}:F" '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
  }
  Step 'secrets restricted'

  # 5. Deploy from the new folder: pip install . rewrites the quant-* launchers to the new interpreter, then start.
  Step 'deploying from C:\Dev\StockResearch'
  Push-Location $newRoot
  try { & (Join-Path $newRoot 'scripts\deploy.ps1') -DuringWorkerJob } finally { Pop-Location }
}
catch {
  Write-Host "CUTOVER FAILED: $($_.Exception.Message)" -ForegroundColor Red
  if ($changedTask -and $taskBackup) {
    $action = New-ScheduledTaskAction -Execute $taskBackup[0].Execute -Argument $taskBackup[0].Arguments
    Set-ScheduledTask -TaskName $taskName -Action $action | Out-Null
    Write-Host 'task action put back to the old folder' -ForegroundColor Yellow
  }
  if ($changedHome) {
    Copy-Item -LiteralPath (Join-Path $backupDir 'service-controls.json') -Destination $homeControls -Force
    Write-Host 'Home service-controls.json put back' -ForegroundColor Yellow
  }
  if (-not (Get-NetTCPConnection -State Listen -LocalPort 5000 -ErrorAction SilentlyContinue)) {
    Write-Host 'starting the services again from the old folder' -ForegroundColor Yellow
    & (Join-Path $oldRoot 'scripts\start-services.ps1')
  }
  throw
}

# 6. Verify the new processes run from C:\Dev, then restart the research pipelines there.
Start-Sleep 5
$mine = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'quant-web.exe', 'quant-api.exe', 'quant-worker.exe' })
foreach ($process in $mine) { "{0,-16} {1,6} {2}" -f $process.Name, $process.ProcessId, $process.ExecutablePath }
if (@($mine | Where-Object { $_.ExecutablePath -notlike "$newRoot\*" }).Count) { throw 'a service runs from outside C:\Dev\StockResearch' }
foreach ($url in 'http://127.0.0.1:5000/health', 'http://127.0.0.1:8000/api/v1/health') {
  try { $code = (Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 20).StatusCode } catch { $code = $_.Exception.Message }
  "$url $code"
}
foreach ($name in $names) {
  Step "restarting research pipeline $name from C:\Dev\StockResearch"
  & (Join-Path $newRoot 'scripts\start-research.ps1') -Name $name
}
Step 'done; the old folder is kept until the owner deletes it'
