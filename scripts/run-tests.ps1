# The one way to run the test suite (docs/rules/workflow.md, D-043).
#
# 2026-11-11: the session doing S11-D wrote its own launcher (build/run_tests_idle.py) that called pytest.main at the
# top of the file. One test starts two processes with multiprocessing "spawn"; each started process imported the
# launcher again, which ran the whole suite again inside it, and ended; the pool replaced the ended process, for ever.
# The run never ended, the session waited for it, and the work package stayed uncommitted until somebody looked.
#
# This script: `python -m pytest` (no launcher of our own), lowest priority, no window, its own temp folder, a log,
# and a time limit - when the limit passes it stops the processes it started and ends with code 3. It always ends.
#
#   scripts\run-tests.ps1                      the whole suite
#   scripts\run-tests.ps1 tests\test_x.py      some files
#   scripts\run-tests.ps1 -LimitMinutes 10 tests\test_x.py
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(ValueFromRemainingArguments = $true)] [string[]] $Paths,
    [int] $LimitMinutes = 30,
    [int] $LimitSeconds = 0,                    # for the script's own test
    [string] $Python = ""
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }   # read by tools as UTF-8
$root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $root ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $Python)) { Write-Output "找不到 Python：$Python"; exit 2 }
if (-not $Paths -or $Paths.Count -eq 0) { $Paths = @("tests") }

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$folder = Join-Path $root "build\test-runs"
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$log = Join-Path $folder "$stamp.log"
$errors = Join-Path $folder "$stamp.err.log"
$temp = Join-Path $root "build\pytest-$stamp-$PID"

$arguments = @("-X", "utf8", "-m", "pytest") + $Paths + @("-q", "-p", "no:cacheprovider", "--basetemp", $temp)
$quoted = ($arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join " "
$old = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $root "src"
$env:PYTHONIOENCODING = "utf-8"
try {
    $process = Start-Process -FilePath $Python -ArgumentList $quoted -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput $log -RedirectStandardError $errors -PassThru
} finally {
    $env:PYTHONPATH = $old
}
function Remove-Scratch {
    # the temp folder of this run (made by this script): the suite's scratch grew to 28 GB when nothing removed it
    try { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction Stop } } catch { }
}
$handle = $process.Handle                       # kept, or ExitCode is empty after the process ends
try { $process.PriorityClass = "Idle" } catch { }
$limit = if ($LimitSeconds -gt 0) { $LimitSeconds } else { $LimitMinutes * 60 }
Write-Output ("{0:HH:mm:ss} 測試開始（PID {1}，時限 {2} 秒，紀錄 {3}）" -f (Get-Date), $process.Id, $limit, $log)

$ended = $process.WaitForExit($limit * 1000)
if (-not $ended) {
    # only the processes this script started: the one above and what it started
    & taskkill.exe /T /F /PID $process.Id 2>&1 | Out-Null
    $process.WaitForExit(15000) | Out-Null
    $last = Get-Content -LiteralPath $log -Tail 1 -Encoding UTF8 -ErrorAction SilentlyContinue
    Remove-Scratch
    Write-Output ("{0:HH:mm:ss} 沒有在時限（{1} 秒）內跑完，已停掉這次啟動的程序。最後一行：{2}" -f (Get-Date), $limit, $last)
    exit 3
}
$process.WaitForExit()
$code = $process.ExitCode
Remove-Scratch
$summary = Get-Content -LiteralPath $log -Encoding UTF8 -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '\d+ (passed|failed|error)' } | Select-Object -Last 1
Get-Content -LiteralPath $log -Encoding UTF8 -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '^(FAILED|ERROR) ' } | Select-Object -First 30
Write-Output ("{0:HH:mm:ss} 測試結束，結束碼 {1}：{2}" -f (Get-Date), $code, $summary)
exit $code
