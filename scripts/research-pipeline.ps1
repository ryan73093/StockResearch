param([ValidateSet("data-a", "daily", "model", "statements", "statements-refresh", "rl", "turnover", "exits", "execution", "blends", "rl-sleeves", "model-60", "t0hunt", "ai-researcher", "news-events", "lowdd", "largecap", "largecap2", "evidence", "scan", "scan4", "robots", "resume4", "robot2", "wide4", "consensus", "trials", "leakcheck", "devconsensus", "modelf", "costswitch", "rawv2", "techmodel", "hypotheses", "minhold", "engine13", "engine131", "dividends", "engine140", "t0scan5", "timefactors", "factormom", "audit", "robot20", "shortdata", "shortfactors", "alerts", "robot21", "rebuild141", "lendingdata", "lendingfactors", "rebuild142", "corporate", "issuance", "dealermodel", "robot30", "robot40", "rebuild143", "dealer2", "deep1", "robot41", "reductions", "robot31", "rebuild150", "worst", "dlrisk", "riskrules", "dealer3", "scan4gpu", "timing", "rolling", "rolling-holdout", "forward-rolling", "deep2a", "deep2b", "deep2cost", "deep2leak", "deep2risk", "deep2cluster", "deep2r3", "deep2scan", "deep2large", "exit2", "coresat", "deep3", "deep2wide", "robustness", "rolling-regrade", "rolling-blends", "spread", "deep2timing", "trim", "blendscan", "insiderdata", "buybackdata", "disclosurestudy", "factorcorr", "modelh", "deep2neutral", "deep24", "deep2top10", "costall", "pledgestudy", "threefam", "industry", "techdeep", "robot32", "robot33", "meta1", "winner", "deep26", "modelgm", "rolesplit", "robot34", "holding", "holding2_a", "holding2_b", "holding3", "holding4_a", "holding4_b", "top10blend", "bigscan", "bigscan2", "listupdate", "robustfront", "exec_a", "exec_b", "llmreplay", "monthmodels", "next10blend", "llmtrader", "scancalm", "factors-recent", "archive", "seed", "factors", "finmind", "finmind-paid", "finmind-paid-b", "finmind-paid-c", "paid-factors", "newdata", "intraday", "limitscheck", "finmind-paid-c2", "newdata-now", "finmind-paid-rest", "holders2", "holders3", "liveflow", "modeli", "monthfill", "deep27", "modelj", "jdeep", "split", "split_a", "split_b", "splitblend", "splitblends", "splitblends_a", "splitblends_b", "bigscan3", "fmdaily_a", "fmdaily_b", "openfeas", "opentight", "govbank", "engine160", "veto2", "knowledge", "veto3_a", "veto3_b", "vetoblends_a", "vetoblends_b", "veto4", "veto5", "forwardrepair", "veto6", "blendroll", "bigscanroll", "govbank2", "branches", "robot35", "paidveto", "robot35s", "deephalf", "blendsteady", "stress", "protect", "bearguard_a", "bearguard_b", "bearguardpost", "bearguard", "bearguard3", "bearguard4", "bearguard5", "blendguard", "forwardfix", "forwardaudit", "forwardwhy", "entryonly", "leakfix", "fullscan", "modelk", "leakfixend")][string]$Name = "daily")
# Long research runs, detached from any terminal or assistant session (使用者 2026-10-04):
# start with scripts\start-research.ps1 -Name <name> (outside the assistant's process tree, 2026-10-06).
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
# 2026-10-20 (使用者：沒講 cmd 在幹嘛、也沒有 log): what this run is, in its title should a window ever show, and every
# step with its time in <name>-<stamp>-pipeline.log
$pipelineLog = Join-Path $logs "$Name-$stamp-pipeline.log"
try { $Host.UI.RawUI.WindowTitle = "StockResearch 研究流程：$Name（進度：網站 研究 › 執行中的程式；紀錄：$pipelineLog）" } catch { }
function Write-Note([string]$Text) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Text"
    Write-Host $line
    Add-Content -Path $pipelineLog -Value $line -Encoding UTF8
}
Write-Note "開始研究流程 $Name（PID $PID）"
# 2026-10-21 (使用者 2026-10-16：隨時看得到研究有沒有在跑；稽核：排隊中的流程網頁上看不到): a wait shows on
# 研究 › 執行中的程式 as 等待中 with its reason, and in the step log
function Wait-While([string]$Reason, [scriptblock]$Condition, [int]$Seconds = 120) {
    if (-not (& $Condition)) { return }
    $waitId = (& $python -m quant_platform.research pipeline-wait --pipeline $Name --wait-reason $Reason --pid $PID | Select-Object -Last 1)
    Write-Note "等待：$Reason"
    while (& $Condition) { Start-Sleep -Seconds $Seconds }
    if ($waitId) { & $python -m quant_platform.research pipeline-wait --finish $waitId | Out-Null }
    Write-Note "等待結束：$Reason"
}
# 2026-10-14: a record of this run (running, then done) so a reboot's casualty is started again when the services
# start (scripts/resume-research.ps1)
$records = Join-Path $root "instance\research\pipelines"
New-Item -ItemType Directory -Force $records | Out-Null
$recordPath = Join-Path $records "$Name.json"
$pipelineStarted = (Get-Date).ToString("s")
function Write-PipelineRecord([string]$Status) {
    $record = [ordered]@{ name = $Name; pid = $PID; started_at = $pipelineStarted; status = $Status }
    if ($Status -ne "running") { $record.finished_at = (Get-Date).ToString("s") }
    [System.IO.File]::WriteAllText($recordPath, ($record | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))
}
Write-PipelineRecord "running"

function Invoke-Step([string]$Label, [string[]]$Arguments) {
    # 2026-10-20: three pipelines ended with the board at once (3-4 GB and a CPU each) and a deploy timed out; the
    # board and the month's list are rebuilt by one run at a time — wait while another research process runs the same
    foreach ($exclusive in @("forward-board", "--step select", "--step holdout")) {
        if (($Arguments -join " ") -like "*$exclusive*") {
            Wait-While "等另一個研究流程的 $exclusive 步驟跑完（同時只跑一個）" {
                Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
                    $_.CommandLine -like "*quant_platform.research*$exclusive*" } } 60
        }
    }
    $out = Join-Path $logs "$Name-$stamp-$Label.log"
    $err = Join-Path $logs "$Name-$stamp-$Label.err.log"
    Write-Note "步驟 $Label 開始：python $($Arguments -join ' ')（輸出：$out）"
    $process = Start-Process -FilePath $python -ArgumentList $Arguments -WorkingDirectory $root -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    Write-Note "步驟 $Label 結束（結束碼 $($process.ExitCode)）"
    # 2026-10-24: the restart from the Start menu on 10-23 03:27 killed fmdaily_a's download step; the pipeline logged
    # the code, ran to its end and wrote "done", so it was never resumed (fmdaily_b, killed whole, was). A step ended by
    # the system (0x40010004 terminated, 0xC000013A console closed, 0xC0000142 / 0xC000026B shutdown) stops the
    # pipeline as "interrupted": resume-research.ps1 starts it again (each pipeline resumes where it can).
    if ($process.ExitCode -in @(1073807364, -1073741510, -1073741502, -1073741205)) {
        Write-Note "步驟 $Label 被系統結束（關機、重開機或主控台關閉）：流程標成中斷，服務下次啟動時自動重開"
        Write-PipelineRecord "interrupted"
        exit 1
    }
}

switch ($Name) {
    "data-a" {
        # R15 stage A (2026-10-05): lists, TPEx prices, quarterly statements (listed and TPEx), TPEx chips.
        Invoke-Step "1-lists" @("-m", "quant_platform.research.history", "finmind", "--codes", "lists")
        Invoke-Step "2-tpex-prices" @("-m", "quant_platform.research.history", "finmind", "--codes", "tpex", "--datasets", "TaiwanStockPrice")
        Invoke-Step "3-statements" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "statements")
        Invoke-Step "4-tpex-chips" @("-m", "quant_platform.research.history", "finmind", "--codes", "tpex")
    }
    "daily" {
        # The new design (S9-W02): 2015-06 onward, decisions every trading day, the owner's account.
        # Every batch (a rule already run on this data version is not run again), then the statistics
        # for the listed and the listed-plus-TPEx data versions (2026-10-05: R6 tpex, R7 overlays).
        foreach ($batch in @("factors", "risk", "chips", "combos", "tpex", "overlays")) {
            Invoke-Step "daily-$batch" @("-m", "quant_platform.research", "daily", "--name", $batch, "--broker", "cathay")
        }
        Invoke-Step "stats-twse" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "stats-all" @("-m", "quant_platform.research", "stats", "--family", "daily", "--universe", "all")
    }
    "model" {
        # R15 stage B (2026-10-06): train the walk-forward models, run the model rules, the statistics.
        # gbm-1.1.0 (excess-return label); gbm-1.0.0 ran on 2026-10-06 00:29 with --model-version gbm-1.0.0
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "gbm-1.1.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "model-excess", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "statements" {
        # 2026-10-06: the statement table, factor strength with the statement factors, the statement-aware
        # model (gbm-1.2.0), the statement rules, the statistics.
        Invoke-Step "1-table" @("-m", "quant_platform.research.history", "fundamentals")
        Invoke-Step "2-strength" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "3-train" @("-m", "quant_platform.research", "model", "--model-version", "gbm-1.2.0")
        Invoke-Step "4-rules" @("-m", "quant_platform.research", "daily", "--name", "statements", "--broker", "cathay")
        Invoke-Step "5-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "statements-refresh" {
        # The quarterly statements refresh (2026-10-07; the worker starts it the night after a filing
        # deadline): companies still trading without the due quarter, then the statement table again.
        Invoke-Step "1-statements" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "statements", "--refresh-quarter", "due")
        Invoke-Step "2-table" @("-m", "quant_platform.research.history", "fundamentals")
    }
    "rl" {
        # R15 stage C1 (2026-10-06): the RL exposure overlay on the best trend rule, walk-forward 2017 on.
        Invoke-Step "rl" @("-m", "quant_platform.research", "rl", "--broker", "cathay", "--rl-version", "rl-overlay-1.1.0")
    }
    "lowdd" {
        # 2026-10-09: 100% stocks aiming at a drawdown within 5 points of 0050's (calm stocks; volatility
        # scaling into cash), then the statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "lowdd", "--broker", "cathay")
        Invoke-Step "2-volscale" @("-m", "quant_platform.research", "daily", "--name", "volscale", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "largecap" {
        # 2026-10-09: 100% stocks picked among the 100 largest only, equal amounts; then the statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "largecap", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "largecap2" {
        # 2026-10-09: three more large-cap rules (calm weighting, revenue confirmation, trend and model in one
        # score); then the statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "largecap2", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "evidence" {
        # 2026-10-10: the six ideas from the Taiwan-quant review (revenue calendar, trend quality, a lottery
        # gate, a four-factor score), registered before the new factors' strength; then the statistics and
        # the factor strength including the three new trend-quality factors.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "evidence", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-factors" @("-m", "quant_platform.research", "factors", "--recent")
    }
    "scan" {
        # 2026-10-10: the broad two-stage search (research/scan.py): about 40,000 candidates screened on
        # 2015-06..2020-09 in 6 processes, the best 80 distinct ones through the full engine; then the statistics.
        Invoke-Step "1-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "scan4" {
        # 2026-10-10: the same search with four signals a set (about 330,000 candidates); finalists must differ
        # from every earlier scan finalist too. Then the statistics.
        Invoke-Step "1-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--signals-min", "4", "--signals-max", "4")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "robots" {
        # 2026-10-10 (使用者：兩個都做＋純技術分析的 RL 機器人): the new model without monthly revenue and its
        # scan, the technical RL robot and its rules, RL 3.0 across the T0 candidates, then the statistics.
        Invoke-Step "1-model" @("-m", "quant_platform.research", "model", "--model-version", "gbm-1.4.0")
        Invoke-Step "2-scan-model" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "20", "--require", "ml_gbm_quality")
        Invoke-Step "3-tech-rl" @("-m", "quant_platform.research", "tech-rl")
        Invoke-Step "4-tech-rules" @("-m", "quant_platform.research", "daily", "--name", "techbot", "--broker", "cathay")
        Invoke-Step "5-rl3" @("-m", "quant_platform.research", "rl-strategies", "--broker", "cathay")
        Invoke-Step "6-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "resume4" {
        # 2026-10-10: the four-signal scan's second stage (its first stage finished; it stopped on a newer
        # model's seed), then every T0 candidate in equal shares as one account, then the statistics.
        Invoke-Step "1-scan4-stage2" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--finalists", "80", "--resume", "instance/research/scans/scan-1.0.0-20261010-021730")
        Invoke-Step "2-t0equal" @("-m", "quant_platform.research", "daily", "--name", "t0equal", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "robot2" {
        # 2026-10-10: the technical robot's second version (weekly decisions), its two rules, the statistics.
        Invoke-Step "1-tech-rl" @("-m", "quant_platform.research", "tech-rl", "--tech-version", "tech-rl-1.1.0")
        Invoke-Step "2-tech-rules" @("-m", "quant_platform.research", "daily", "--name", "techbot2", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "wide4" {
        # 2026-10-10: the four-signal scan's second stage again, choosing distinct finalists from its best
        # 5,000 (not 600: the top 600 were near-copies of each other), then the statistics.
        Invoke-Step "1-scan4-wide" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--finalists", "80", "--pool", "5000", "--resume", "instance/research/scans/scan-1.0.0-20261010-021730")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "consensus" {
        # 2026-10-11: the T0 consensus list (top 20 and top 40), the crowding / size / overlap diagnostics of the
        # T0 candidates and the list (not trials), then the statistics.
        Invoke-Step "1-consensus" @("-m", "quant_platform.research", "daily", "--name", "consensus", "--broker", "cathay")
        Invoke-Step "2-diagnose" @("-m", "quant_platform.research", "diagnose", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "trials" {
        # 2026-10-11: effective trials of the earlier scans (sampled replays), then the statistics with them.
        Invoke-Step "1-scan-trials" @("-m", "quant_platform.research", "scan-trials", "--broker", "cathay", "--workers", "6")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "leakcheck" {
        # 2026-10-11: the truncation check — every factor recomputed from data cut at 5 random sessions.
        Invoke-Step "1-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "devconsensus" {
        # 2026-10-11: the clean test of the consensus method (180 development-period finalists), then statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "devconsensus", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "modelf" {
        # 2026-10-12: gbm-2.0.0 (the ensemble), then — only if it passes its registered gate — its rule, the scan
        # of sets containing it (scan 1.1.0: stability-chosen finalists), and the statistics.
        Invoke-Step "1-model" @("-m", "quant_platform.research", "model", "--model-version", "gbm-2.0.0")
        Invoke-Step "2-rule" @("-m", "quant_platform.research", "daily", "--name", "modelf", "--broker", "cathay")
        Invoke-Step "3-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "20", "--require", "ml_gbm_ens")
        Invoke-Step "4-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "costswitch" {
        # 2026-10-12: cost-aware switching on the two consensus frames, then the statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "costswitch", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "rawv2" {
        # 2026-10-13: five new price-and-volume factors — their strength (description), then the scan of every
        # 1-4 signal set holding at least one of those that pass the novelty gate (scan 1.1.0), the statistics.
        Invoke-Step "1-factors" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "2-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--signals-min", "1", "--signals-max", "4", "--require-new")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "techmodel" {
        # 2026-10-13: the technical robot's second version, step 1 — the ensemble on the 20 technical factors
        # (gbm-2.1.0) and its weekly top-20 rule (judged against #2090), then the statistics.
        Invoke-Step "1-model" @("-m", "quant_platform.research", "model", "--model-version", "gbm-2.1.0")
        Invoke-Step "2-rule" @("-m", "quant_platform.research", "daily", "--name", "techmodel", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "hypotheses" {
        # 2026-10-13: the owner's waiting hypothesis checks (started by the worker when one is waiting).
        Invoke-Step "1-check" @("-m", "quant_platform.research", "hypotheses", "--broker", "cathay")
    }
    "minhold" {
        # 2026-10-13: the four best rules without the 20-session minimum holding, then the statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "minhold", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "engine13" {
        # 2026-10-13 (使用者：請符合市場交易規則；被擋下隔天重新評估): engine 1.3.0 — the new-factor scan's second
        # stage again, every T0 candidate and T1 run again, the statistics, then the common-start board and the
        # three-month replays.
        Invoke-Step "1-rawv2-stage2" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--finalists", "80", "--pool", "5000", "--resume", "instance/research/scans/scan-1.1.0-20261013-092329")
        Invoke-Step "2-rerun" @("-m", "quant_platform.research", "daily", "--name", "rerun13", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "dividends" {
        # 2026-10-13 (使用者：所有交易都要照台股的規則和機制): each stock's dividend policy — cash per share and
        # its payment date, stock dividends, cash capital increases — so the engine pays cash dividends as
        # cash on the payment date and stock dividends as whole shares (FinMind TaiwanStockDividend).
        Invoke-Step "1-dividends" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "TaiwanStockDividend")
    }
    "engine140" {
        # 2026-10-13 (使用者：確實檢查所有交易都照台股的交易規則和機制): the dividend policies (resumes where the
        # download stopped), then the same 211 rules on engine 1.4.0 — whole shares, cash dividends paid as cash,
        # ex-dates moved off typhoon closures, the exchange's limits on ex-rights days, fees per order part with
        # the period's minimum, at most 1% of a stock's turnover per order — the statistics, the board, and the
        # trade audit of every tracked rule.
        # a dividends download already running (pipeline "dividends") is waited for, not started twice
        while (Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*finmind*TaiwanStockDividend*" }) {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-dividends" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "TaiwanStockDividend")
        Invoke-Step "2-rerun" @("-m", "quant_platform.research", "daily", "--name", "rerun140", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "5-audit" @("-m", "quant_platform.research", "trade-audit", "--broker", "cathay")
    }
    "t0scan5" {
        # 2026-10-14 (使用者：繼續尋找 T0；制度要修正到所有策略，包含機器學習與 RL): scan 1.2.0 — every set of 1–3 of
        # the 47 signals, both universes and weightings, holding by the trend (69,372 candidates) — screened on
        # 2015-06..2020-09 now (the screen needs no dividends); the full-engine stage, the model, robot and blend
        # rules on engine 1.4.0, the statistics and the board wait for engine140 (dividends, the 211 rules).
        $screened = Get-ChildItem (Join-Path $root "instance\research\scans") -Directory -Filter "scan-1.2.0-*" |
            Where-Object { Test-Path (Join-Path $_.FullName "summary.json") }
        if (-not $screened) {                     # a restart after a reboot does not screen again
            Invoke-Step "1-screen" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "0")
        }
        while (Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*research-pipeline.ps1*-Name engine140*" }) {
            Start-Sleep -Seconds 300
        }
        $scanFolder = (Get-ChildItem (Join-Path $root "instance\research\scans") -Directory -Filter "scan-1.2.0-*" | Sort-Object Name | Select-Object -Last 1).FullName
        Invoke-Step "2-finalists" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--finalists", "80", "--pool", "5000", "--resume", $scanFolder)
        Invoke-Step "3-models" @("-m", "quant_platform.research", "daily", "--name", "rerun_models", "--broker", "cathay")
        Invoke-Step "4-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "5-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "timefactors" {
        # 2026-10-14 (使用者：因子裡面有考慮到時間的部分嗎？): the calendar and event-time family — its strength
        # first, then every 1-4 signal set with at least one time factor that passes the novelty gate (scan 1.2.0,
        # holding by the trend), the statistics and the board. Waits for t0scan5 (and so for engine140).
        while (Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*research-pipeline.ps1*-Name t0scan5*" -or $_.CommandLine -like "*research-pipeline.ps1*-Name engine140*" }) {
            Start-Sleep -Seconds 300
        }
        # the big screen keeps out of 12:00–14:45: the 13:50 after-close orders need the machine
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"12:00" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:45") {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-factors" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "2-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require-time")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "5-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "factormom" {
        # 2026-10-14: factor momentum (research/factor_timing.py) — one untuned rule, then the statistics and the
        # board. Waits for the time-factor search and keeps out of 12:00–14:45.
        while (Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*research-pipeline.ps1*-Name timefactors*" -or $_.CommandLine -like "*research-pipeline.ps1*-Name t0scan5*" }) {
            Start-Sleep -Seconds 300
        }
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"12:00" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:45") {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-rule" @("-m", "quant_platform.research", "daily", "--name", "factormom", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "robot20" {
        # (named robot20: "robot2" is the 2026-10-10 weekly robot 1.1.0, and a switch runs every case that matches)
        # 2026-10-14 (使用者：RL 為什麼學到不交易？目標設計有問題？): the technical robot 2.0 — always invested, it
        # chooses the 20 technical factors' weights every 20 sessions, rewarded by the account against 0050 over those
        # sessions (research/rl_timing.py); trained walk-forward, then its rule against "every factor alike" and
        # technical factor momentum, the statistics and the board. Waits for factormom; keeps out of 12:00–14:45.
        while (Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*research-pipeline.ps1*-Name factormom*" -or $_.CommandLine -like "*research-pipeline.ps1*-Name timefactors*" }) {
            Start-Sleep -Seconds 300
        }
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"12:00" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:45") {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-train" @("-m", "quant_platform.research", "tech-rl2")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "robot2", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "shortdata" {
        # 2026-10-14 (資料缺口：借券賣出餘額、當沖比例): FinMind's daily short-sale balances (margin and securities
        # lending) and day trading, every listed and TPEx code; the download pauses 13:30–15:30 by itself.
        Invoke-Step "1-short" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "TaiwanDailyShortSaleBalances")
        Invoke-Step "2-daytrade" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "TaiwanStockDayTrading")
    }
    "shortfactors" {
        # 2026-10-14: once shortdata is in — the chip tables rebuilt with the lending and day-trading data, the
        # factors' strength, every 1-4 signal set with a short factor that passes the novelty gate (holding by the
        # trend), the statistics, the board and the leak check. Keeps out of 12:00–14:45.
        while (Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*research-pipeline.ps1*-Name shortdata*" -or $_.CommandLine -like "*research-pipeline.ps1*-Name robot2*" -or $_.CommandLine -like "*research-pipeline.ps1*-Name factormom*" }) {
            Start-Sleep -Seconds 300
        }
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"12:00" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:45") {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-chips" @("-m", "quant_platform.research.history", "chips")
        Invoke-Step "2-factors" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "3-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require-short")
        Invoke-Step "4-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "5-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "6-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "engine131" {
        # 2026-10-13 evening: engine 1.3.1 (an unsold delisted holding is worthless) — the same 64 rules
        # again, each with the fixed 20-session minimum, with none, and with hold "trend" (使用者：還在漲就留著),
        # and the new-factor scan's 19 finalists as chosen and with hold "trend"; then the statistics, the
        # common-start board and the three-month replays.
        Invoke-Step "1-rerun" @("-m", "quant_platform.research", "daily", "--name", "rerun131", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "news-events" {
        # R15 D (2026-10-09): score the collected headlines not scored yet (also every trading day 21:45).
        Invoke-Step "1-score" @("-m", "quant_platform.research", "news-events")
    }
    "ai-researcher" {
        # S9-W07 (2026-10-09): one weekly round of the new-design AI researcher, then the statistics.
        Invoke-Step "1-round" @("-m", "quant_platform.research", "researcher")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "t0hunt" {
        # 2026-10-09: factor strength (with the revenue event factor), five fixed rules, the statistics.
        Invoke-Step "1-factors" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "t0hunt", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "model-60" {
        # 2026-10-09: the model that learns the next 60 sessions (gbm-1.3.0), its two rules, the statistics.
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "gbm-1.3.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "model-60", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "blends" {
        # 2026-10-09: accounts split across strategy families (research/blend.py), then the statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "blends", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "rl-sleeves" {
        # R15 stage C3 (2026-10-09): the RL allocator across the model rule, the trend rule and 0050.
        Invoke-Step "rl" @("-m", "quant_platform.research", "rl-sleeves", "--broker", "cathay", "--sleeves-version", "rl-sleeves-2.0.0")
    }
    "execution" {
        # R4 (2026-10-07): the tracked rules' stock orders against the cached odd-lot auctions.
        Invoke-Step "1-check" @("-m", "quant_platform.research", "execution", "--broker", "cathay")
    }
    "exits" {
        # R15 C1b (2026-10-07): train the learned exit, run the trend rule with it, the statistics.
        Invoke-Step "1-train" @("-m", "quant_platform.research", "exits")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "exits", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "turnover" {
        # 2026-10-07: the T0 candidate with less trading (5-session score average, weekly decisions), then the statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "turnover", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "audit" {
        Invoke-Step "audit" @("-m", "quant_platform.research", "trade-audit", "--broker", "cathay")
        # 2026-10-25 (使用者：所有策略確定都合理嗎): every forward account's decisions ranked again the way a backtest does
        Invoke-Step "forward-audit" @("-m", "quant_platform.research", "forward-audit")
        # 2026-10-26 (使用者：你現在全部都是正確的嗎): the factor truncation check (new factors too) and the search's
        # screen against the engine, weekly; the research overview shows every check's latest result
        Invoke-Step "leakcheck" @("-m", "quant_platform.research", "leakcheck")
        Invoke-Step "screen-check" @("-m", "quant_platform.research", "screen-check")
    }
    "forwardaudit" { Invoke-Step "forward-audit" @("-m", "quant_platform.research", "forward-audit") }
    "leakfixend" {
        # 2026-10-27: leakfix's last steps again — the list's holdout and the board failed on a shadowed import
        # (broker_costs, fixed), the restart on a reason with a space (Windows PowerShell split it); no spaces now
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "2-forward" @("-m", "quant_platform.research", "forward", "--restart-uses", "block_volume_20,ml_gbm_j,rl_rolling35,rolling35_equal", "--reason", "2026-10-26防偷看檢查：鉅額交易量補0修正、模型J與RL3.5重訓後從起算日重新記錄")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "4-audit" @("-m", "quant_platform.research", "forward-audit")
    }
    "modelk" {
        # 2026-10-26 (使用者：付費的資料你練了沒; research_method §7「模型 K」, written before the run): every paid table in
        # one model (gbm-2.7.0), after the leak fix has retrained model J (its yardstick) and freed the CPU
        Wait-While "等防偷看修正流程 leakfix 跑完（模型 J 是模型 K 的比較基準）" { [bool](Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "powershell.exe" -and $_.CommandLine -match "research-pipeline\.ps1" -and $_.CommandLine -match "-Name leakfix" }) } 120
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-model" @("-m", "quant_platform.research", "model", "--model-version", "gbm-2.7.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "modelk", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-refit" @("-m", "quant_platform.research", "rolling", "--step", "models", "--missing-only")
        Invoke-Step "5-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "6-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "7-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "8-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "fullscan" {
        # 2026-10-26 (使用者：不要再用 2015～2020 篩、全部都測過一遍、要真的上億個、付費資料要進去、最嚴格最完整; research_method
        # §7「全段大搜尋」, written before the run): research/scan_full.py — phase a (1-4 signals), phase b (5 of phase a's 50
        # most passing), each a stage 1 on the GPU in two processes over 2015-06..cutoff and up to 1,500 finalists in the
        # engine; then the month's list grades them, their forward accounts, the board, the leak check, the audits.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-phase-a" @("-m", "quant_platform.research", "scan-full", "--phase", "a", "--broker", "cathay")
        Invoke-Step "2-phase-b" @("-m", "quant_platform.research", "scan-full", "--phase", "b", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "5-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "6-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "7-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "8-leakcheck" @("-m", "quant_platform.research", "leakcheck")
        Invoke-Step "9-audit" @("-m", "quant_platform.research", "forward-audit")
        Invoke-Step "10-screen-check" @("-m", "quant_platform.research", "screen-check")
    }
    "leakfix" {
        # 2026-10-26 (the leak check, 106 factors): block_volume_20 counted a missing row as 0 only for the codes the
        # block-trade table holds somewhere — whether a stock would ever have a block trade leaked; model J learned
        # from it and the robot 3.5 reads model J. The factor is fixed (chips._paid); the old model J and robot 3.5 are
        # copied aside (models' "*.leaky-20261026"), retrained, their rules run again, the October list regrades them,
        # their forward accounts are recorded again from 9/1, then the board, the leak check and the forward audit.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        # (not $name: PowerShell names are case-blind, and $Name is this script's validated parameter — the first run
        # at 22:08 failed to copy anything that way; the yearly model J was retrained over its old files)
        $models = Join-Path $root "instance\research\models"
        $robotKept = Join-Path $models "block-rl-3.5.0.leaky-20261026"
        if (-not (Test-Path -LiteralPath $robotKept)) { Copy-Item -LiteralPath (Join-Path $models "block-rl-3.5.0") -Destination $robotKept -Recurse }
        $refit = Join-Path $models "gbm-2.6.0\monthly\2026-08-31.pkl"
        if (Test-Path -LiteralPath $refit) {
            $aside = Join-Path $models "gbm-2.6.0.superseded-20261026\monthly"
            New-Item -ItemType Directory -Force -Path $aside | Out-Null
            Move-Item -LiteralPath $refit -Destination (Join-Path $aside "2026-08-31.pkl") -Force
        }
        Invoke-Step "1-model-j" @("-m", "quant_platform.research", "model", "--model-version", "gbm-2.6.0")
        Invoke-Step "2-robot35" @("-m", "quant_platform.research", "block-rl35")
        Invoke-Step "3-rules" @("-m", "quant_platform.research", "daily", "--name", "leakfix", "--no-half", "--broker", "cathay")
        Invoke-Step "4-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "5-refit" @("-m", "quant_platform.research", "rolling", "--step", "models", "--missing-only")
        Invoke-Step "6-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--regrade-uses", "block_volume_20,ml_gbm_j,rl_rolling35,rolling35_equal", "--workers", "2", "--broker", "cathay")
        Invoke-Step "7-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "8-forward" @("-m", "quant_platform.research", "forward", "--restart-uses", "block_volume_20,ml_gbm_j,rl_rolling35,rolling35_equal", "--reason", "2026-10-26防偷看檢查：鉅額交易量補0修正、模型J與RL3.5重訓後從起算日重新記錄")
        Invoke-Step "9-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "10-leakcheck" @("-m", "quant_platform.research", "leakcheck")
        Invoke-Step "11-audit" @("-m", "quant_platform.research", "forward-audit")
    }
    "forwardwhy" {
        # 2026-10-26: the engine now writes the reason on every order (a partial sale's was guessed from the rule);
        # every live account recorded again from its start so the orders since 9/1 carry it, then the board and the check
        Invoke-Step "1-restart" @("-m", "quant_platform.research", "forward", "--restart-live", "--reason", "2026-10-26：引擎在每筆委託寫下實際原因（部分賣出原本是依規則設定猜的），重新記錄讓9/1以來的委託都帶原因；決定與結果不變")
        Invoke-Step "2-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "3-audit" @("-m", "quant_platform.research", "forward-audit")
    }
    "lendingdata" {
        # 2026-10-15 (借券賣出餘額低是強因子，沿著借券這條線): FinMind's securities-lending deals (lots and fee rate of
        # each deal) for every listed and TPEx code; the download pauses 13:30–15:30 by itself.
        Invoke-Step "1-lending" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "TaiwanStockSecuritiesLending")
    }
    "lendingfactors" {
        # 2026-10-15: once lendingdata is in — the chip tables rebuilt with the lending deals, the factors' strength,
        # every 1-4 signal set with a lending factor that passes the novelty gate (holding by the trend), the
        # statistics, the board and the leak check. Keeps out of 12:00–14:45 and of 19:30–22:15 (the 21:30 chip
        # update rewrites the same files while a scan would read them).
        while (Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*research-pipeline.ps1*-Name lendingdata*" }) {
            Start-Sleep -Seconds 300
        }
        while (((Get-Date).TimeOfDay -ge [TimeSpan]"12:00" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:45") -or
               ((Get-Date).TimeOfDay -ge [TimeSpan]"19:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"22:15")) {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-chips" @("-m", "quant_platform.research.history", "chips")
        Invoke-Step "2-factors" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "3-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require-lending")
        Invoke-Step "4-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "5-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "6-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "rebuild142" {
        # 2026-10-15: engine 1.4.2 (equal factor values share their percentile, an equal score goes to the more liquid
        # stock — both had gone by stock code). After lendingfactors (it runs on 1.4.2 already): every rule in the
        # registry not yet on 1.4.2 again, the statistics, the board, the trade audit and the leak check.
        while (Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*research-pipeline.ps1*-Name lendingdata*" -or $_.CommandLine -like "*research-pipeline.ps1*-Name lendingfactors*" }) {
            Start-Sleep -Seconds 300
        }
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"12:00" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:45") {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-rerun-all" @("-m", "quant_platform.research", "daily", "--name", "rerun_all", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "4-audit" @("-m", "quant_platform.research", "trade-audit", "--broker", "cathay")
        Invoke-Step "5-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "corporate" {
        # 2026-10-15 (公司財務政策): after rebuild142 — the chip tables rebuilt with the dividend changes and cash
        # capital increases, the factors' strength, every 1-4 signal set with one of them that passes the novelty gate
        # (holding by the trend), the statistics, the board and the leak check. Keeps out of 12:00–14:45 and of
        # 19:30–22:15 (the 21:30 chip update rewrites the same files).
        while (Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*research-pipeline.ps1*-Name lending*" -or $_.CommandLine -like "*research-pipeline.ps1*-Name rebuild142*" }) {
            Start-Sleep -Seconds 300
        }
        while (((Get-Date).TimeOfDay -ge [TimeSpan]"12:00" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:45") -or
               ((Get-Date).TimeOfDay -ge [TimeSpan]"19:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"22:15")) {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-chips" @("-m", "quant_platform.research.history", "chips")
        Invoke-Step "2-factors" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "3-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require-corporate")
        Invoke-Step "4-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "5-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "6-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "issuance" {
        # 2026-10-16 (股本膨脹): the chip tables rebuilt with the stock dividends, the factors' strength, every 1-4
        # signal set with low_issuance_250 that passes the novelty gate (holding by the trend), the statistics, the
        # board and the leak check. Keeps out of 12:00–14:45 and 19:30–22:15 (the 21:30 chip update).
        while (Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*research-pipeline.ps1*-Name corporate*" -or $_.CommandLine -like "*research-pipeline.ps1*-Name rebuild142*" }) {
            Start-Sleep -Seconds 300
        }
        while (((Get-Date).TimeOfDay -ge [TimeSpan]"12:00" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:45") -or
               ((Get-Date).TimeOfDay -ge [TimeSpan]"19:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"22:15")) {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-chips" @("-m", "quant_platform.research.history", "chips")
        Invoke-Step "2-factors" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "3-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require-issuance")
        Invoke-Step "4-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "5-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "6-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "dealermodel" {
        # 2026-10-16: the chip tables rebuilt with the dealers' own trading and hedging, the factors' strength, model G
        # (gbm-2.2.0: the ensemble on every factor family but monthly revenue), every 1-4 signal set with a dealer
        # factor that passes the novelty gate; then — only if model G passes its registered gate against gbm-2.0.0 —
        # its rule and every set with it; the statistics, the board and the leak check. Keeps out of 12:00–14:45 and
        # 19:30–22:15 (the 21:30 chip update rewrites the same files).
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-chips" @("-m", "quant_platform.research.history", "chips")
        Invoke-Step "2-factors" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "3-model-g" @("-m", "quant_platform.research", "model", "--model-version", "gbm-2.2.0")
        Invoke-Step "4-scan-dealer" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require-dealer")
        Invoke-Step "5-rule-g" @("-m", "quant_platform.research", "daily", "--name", "modelg", "--broker", "cathay")
        Invoke-Step "6-scan-g" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require", "ml_gbm_ext")
        Invoke-Step "7-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "8-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "9-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "dealer2" {
        # 2026-10-16: dealermodel started at once (the waiting one could not be stopped); the same steps
        # 2026-10-16: the chip tables rebuilt with the dealers' own trading and hedging, the factors' strength, model G
        # (gbm-2.2.0: the ensemble on every factor family but monthly revenue), every 1-4 signal set with a dealer
        # factor that passes the novelty gate; then — only if model G passes its registered gate against gbm-2.0.0 —
        # its rule and every set with it; the statistics, the board and the leak check. Keeps out of 12:00–14:45 and
        # 19:30–22:15 (the 21:30 chip update rewrites the same files).
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-chips" @("-m", "quant_platform.research.history", "chips")
        Invoke-Step "2-factors" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "3-model-g" @("-m", "quant_platform.research", "model", "--model-version", "gbm-2.2.0")
        Invoke-Step "4-scan-dealer" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require-dealer")
        Invoke-Step "5-rule-g" @("-m", "quant_platform.research", "daily", "--name", "modelg", "--broker", "cathay")
        Invoke-Step "6-scan-g" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require", "ml_gbm_ext")
        Invoke-Step "7-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "8-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "9-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "robot30" {
        # 2026-10-16 (使用者：RL 找不到 T0？學習策略有問題還是因子不夠？): the robot 3.0 — eight blocks, four offsets, a
        # held-out fifth, abstaining when it does not beat the blocks alike — then its rule and its yardstick, and the
        # statistics. The rules wait out 21:15–21:50 (the chip update rewrites the files they read).
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "block-rl3")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "robot30", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "robot40" {
        # 2026-10-16 (使用者：不要給 RL 加一堆限制，只受交易所規則；RL 沒打贏 0050 之前不要停): the robot 4.0 — every session
        # its own weights for every stock and for cash, learned from every session (research/rl_free.py) — then its
        # rule (free: executed under the exchange's rules) and the statistics. The rule waits out 21:15–21:50.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "free-rl4", "--broker", "cathay")
        Invoke-Step "2-rule" @("-m", "quant_platform.research", "daily", "--name", "robot40", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "dlrisk" {
        # 2026-10-16 (使用者：深度學習能不能用到交易裡輔助這些策略？GPU 先用起來): the drop-risk model on the GPU.
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-risk-1.0.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "risk", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "rolling" {
        # 2026-10-16 (使用者：訓練到兩個月前，後面兩個月做前向測試；模型也要每月更新): the monthly rolling forward test
        # (research/rolling.py). 1: every rule graded on data up to the cutoff (the end of the month two months back).
        # 2: the models retrained to the cutoff — after any full rerun on the same models has finished, so one batch
        # does not mix model sets. 3: the passing rules' fresh accounts from the cutoff to the latest session.
        Invoke-Step "1-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--workers", "2", "--broker", "cathay")
        while (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "quant_platform\.research daily --name rerun_all" }) {
            Start-Sleep -Seconds 120
        }
        Invoke-Step "2-models" @("-m", "quant_platform.research", "rolling", "--step", "models")
        Invoke-Step "3-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
    }
    "deep2a" {
        # 2026-10-17 (使用者：深度學習繼續優化; docs/research_dl_exits_review.md D1): the second deep version, Huber +
        # top-weighted ListNet (dl-2.0.0) — the development years first, the other years only past its registered gate
        # (research/model_deep2.py); then its rule (deep2_batch: only past the gate) and the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-2.0.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "deep2b" {
        # 2026-10-17: the same second version learning the day's top tenth (dl-2.1.0), its own registered gate.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-2.1.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "deep2cost" {
        # 2026-10-17 (使用者：你覺得要分析什麼就分析): stage 0 of the review (free), then the three registered rules with
        # cost-aware switching (deep2_cost_batch: dl-2.1.0, model G, the half-and-half blend), then the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-diagnose" @("-m", "quant_platform.research", "deep2-diagnose")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2cost", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "deep2leak" {
        # 2026-10-17: the cost-aware dl-2.1.0 rule (#4913) ended at 3,659 萬 against 0050's 982 萬 — before believing
        # it, its scores are rebuilt from data cut at six sampled sessions and compared with the kept ones.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-leakcheck" @("-m", "quant_platform.research", "deep2-leakcheck")
    }
    "deep2risk" {
        # 2026-10-17 (R1): the three cost-aware rules with each pick's money set by its predicted drop probability.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2risk", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "deep2cluster" {
        # 2026-10-17 (R2): the three cost-aware rules, at most 3 in 10 picks from one group of stocks that moved together.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2cluster", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "deep2r3" {
        # 2026-10-17 (R3): the vol-neutral drop-risk model (its gate in meta.json), then — only past it — the three
        # cost-aware rules without the riskiest tenth of each volatility-and-momentum group, then the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-risk-2.0.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2r3", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "deep2scan" {
        # 2026-10-17 (使用者：繼續): the two-stage search with the second deep model's score in every set — each signal
        # alone first over the development period (only those beating 0050 alone), sets of up to four with it, the
        # finalists through the engine; then the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "2", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require", "ml_deep2_top", "--prefilter")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "deep2large" {
        # 2026-10-17: the three cost-aware rules ranking only the 100 largest listed stocks, then the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2large", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "exit2" {
        # 2026-10-17 (E2): the weekly optimal-stopping exit on dl-2.1.0's ranking — the models and the model gate first.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "exit2")
    }
    "coresat" {
        # 2026-10-17: the T0 consensus (cost-aware) 63% + the deep rule 37% in one account, then the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "coresat", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "deep3" {
        # 2026-10-17 (D3): dl-3.0.0 — the development years first, the rest only past its gate against dl-2.1.0;
        # then its rules (deep3_batch: only past the gate) and the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-3.0.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep3", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "deep2wide" {
        # 2026-10-18 (S9-W15): dl-2.2.0 learning from the TPEx and small stocks too — the development years first, the
        # rest only past its gate against dl-2.1.0; then its rules (only past the gate) and the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-2.2.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2wide", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "robustness" {
        # 2026-10-18: the ten best T0 candidates with every cost doubled and filled a session late (a diagnostic).
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-check" @("-m", "quant_platform.research", "robustness", "--broker", "cathay", "--top", "60")
    }
    "rolling-regrade" {
        # 2026-10-18 (使用者：回撤上限放寬並標註高風險): the month's rolling selection graded again on the new tiers — every
        # registered rule (the ones made since the first grading too) on data up to the cutoff — then its forward test,
        # the forward accounts (high-risk passers from the day after the cutoff) and the common-start board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--workers", "2", "--broker", "cathay")
        Invoke-Step "2-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "3-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "4-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "spread" {
        # 2026-10-18 (使用者：錢賺得多就可以；繼續再研究): accounts whose sleeves fall at different times (the deep rule
        # with the calm scan rule; the consensus, the deep rule and the calm rule); then the statistics, and the month's
        # rolling selection adds them and the other blends (only the rules not yet graded this month), their forward
        # test, the forward accounts and the common-start board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "spread", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "deep2timing" {
        # 2026-10-18 (使用者：繼續研究有什麼更好的策略): the deep rule #4916 filled at the next session's close and open;
        # then the statistics; once the spread pipeline is done (one writer of the month's selection at a time), the
        # month's rolling selection adds the new rules, their forward test, the forward accounts and the board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2timing", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        $spreadRecord = Join-Path $records "spread.json"
        while ($true) {
            $state = if (Test-Path $spreadRecord) { Get-Content $spreadRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
            $alive = $state -and $state.status -eq "running" -and (Get-Process -Id $state.pid -ErrorAction SilentlyContinue)
            if (-not $alive) { break }
            Start-Sleep -Seconds 60
        }
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "trim" {
        # 2026-10-18 (使用者：繼續研究有什麼更好的策略): the deep rules and the T0 consensus, a holding grown past twice its
        # target sold back to it; the statistics; once spread and deep2timing are done (one writer of the month's
        # selection at a time), the month adds the new rules, their forward test, the forward accounts and the board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "trim", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        foreach ($other in @("spread", "deep2timing")) {
            $otherRecord = Join-Path $records "$other.json"
            while ($true) {
                $state = if (Test-Path $otherRecord) { Get-Content $otherRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
                $alive = $state -and $state.status -eq "running" -and (Get-Process -Id $state.pid -ErrorAction SilentlyContinue)
                if (-not $alive) { break }
                Start-Sleep -Seconds 60
            }
        }
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "robot35" {
        # 2026-10-23 (research_method §7「RL 機器人 3.5」, written before the run): the robot with FinMind's paid data
        # (the holders and margin-and-loans slots, models I and J) and its yardstick
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "block-rl35")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "robot35", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "5-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "6-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "7-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "stress" {
        # 2026-10-24 (使用者：2008 年的金融海嘯你撐得過嗎; research_method §7「大空頭壓力測試」, written before the run):
        # the strategies' models retrained from 2004 into models_long (the graphics card), then every strategy from 2007
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "stress-train")
        Invoke-Step "2-run" @("-m", "quant_platform.research", "stress")
    }
    "protect" {
        # 2026-10-24 research_method §7「空頭減碼檢驗」: the stress test's models are already in models_long
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-run" @("-m", "quant_platform.research", "stress-protect")
    }
    "bearguard_a" {
        # 2026-10-24 research_method §7「空頭減碼第二版」: the stress strategies with each way, graded the usual way
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", $Name, "--broker", "cathay")
        # the statistics, the monthly lists and the forward accounts follow in bearguardpost, once both halves are graded
    }
    "bearguard_b" {
        # 2026-10-24 research_method §7「空頭減碼第二版」: the stress strategies with each way, graded the usual way
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", $Name, "--broker", "cathay")
        # the statistics, the monthly lists and the forward accounts follow in bearguardpost, once both halves are graded
    }
    "bearguard" {
        # 2026-10-24: the guarded rules the split run missed (BEAR_GUARD_ORIGINS fixed), then the steps every batch takes
        Wait-While "等 bearguardpost 做完名單與前向" { @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "research-pipeline\.ps1.*-Name bearguardpost" }).Count -gt 0 } 60
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "bearguard", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "bearguard5" {
        # 2026-10-25 research_method §7「空頭防護第五版」: the half guard timed by the deep risk model
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", $Name, "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "7-guards" @("-m", "quant_platform.research", "stress-guards")
    }
    "entryonly" {
        # 2026-10-26 research_method §7「選股條件只管買進」: the 55 fixed rules with eligibility "entry", graded the usual way
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "entryonly", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "7-guards" @("-m", "quant_platform.research", "stress-guards")
    }
    "bearguard4" {
        # 2026-10-25 research_method §7「空頭防護第四版」: the deep risk veto only below TAIEX's 200-session average
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "bearguard4", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "7-guards" @("-m", "quant_platform.research", "stress-guards")
    }
    "bearguard3" {
        # 2026-10-25 research_method §7「空頭減碼第三版」: the half guard on each family's five richest, the staged guard on
        # the stress strategies, graded the usual way, then the steps every batch takes
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "bearguard3", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "7-guards" @("-m", "quant_platform.research", "stress-guards")
    }
    "bearguardpost" {
        # 2026-10-24: after bearguard_a and bearguard_b, the steps every batch takes (as paidveto): the passing rules go
        # onto this month's rolling lists and forward accounts
        Wait-While "等 bearguard_a／bearguard_b 評完" { @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "daily --name bearguard_[ab]" }).Count -gt 0 } 60
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "2-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "3-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "4-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "5-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "blendguard" {
        # 2026-10-25 (research_method §7 組合搜尋第八輪, written before the run): blends of the rules guarded by TAIEX's
        # 200-session line (half or none below it), the development months up to the cutoff
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-blends" @("-m", "quant_platform.research", "daily", "--name", "blendguard", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "blendsteady" {
        # 2026-10-24 (research_method §7 組合搜尋第七輪, written before the run): blends of the rules that beat 0050 in every
        # period up to the cutoff
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-blends" @("-m", "quant_platform.research", "daily", "--name", "blendsteady", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "deephalf" {
        # 2026-10-24 (research_method §7「深度學習一年一訓與每月重訓各半」, written before the run)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "deephalf", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "robot35s" {
        # 2026-10-24 (research_method §7「RL 機器人 3.5」): the robot 3.5 and its yardstick in the three holding styles,
        # sold at the next open and bought at the next close (the robot is already trained: no training step)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "robot35s", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "paidveto" {
        # 2026-10-23 (research_method §7「付費資料否決補到趨勢與 RL」, written before the run): the paid vetoes (h2, m1)
        # on the trend family's eight passing rules and h2 on the robot family's six richest
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "paidveto", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "branches" {
        # 2026-10-23 (research_method §7「分點」, written before the run): after the branches' download (fmdaily_a/_b),
        # the branches chosen month by month and their two stock signals (research/branches.py), then the strength
        Wait-While "等分點下載完（fmdaily_a、fmdaily_b）" { [bool](Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "finmind-daily" -and $_.CommandLine -match "branches" }) } 600
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-signals" @("-m", "quant_platform.research", "branches")
        Invoke-Step "2-strength" @("-m", "quant_platform.research", "factors", "--recent", "--only", "branch_informed_20,low_daytrade_5")
    }
    "govbank2" {
        # 2026-10-23 (research_method §7「八大行庫」的下一版): the banks' unusually large buying of a stock as an event
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-events" @("-m", "quant_platform.research", "govbank-events")
    }
    "bigscanroll" {
        # 2026-10-22 (research_method §7 大規模搜尋第四輪): the streamed search with the development months up to the
        # month's cutoff (the monthly list's way); the 50 signals most often past round one's screen, 3-4 a set
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        $round1 = Join-Path $root "instance\research\scans\scan-large-1.0.0-20261020-105730"
        Invoke-Step "1-scan" @("-m", "quant_platform.research", "scan-large", "--broker", "cathay", "--signals-min", "3", "--signals-max", "4", "--universes", "all", "--signals-top", "50", "--signals-from", $round1, "--dev-end", "cutoff", "--finalists", "80")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "blendroll" {
        # 2026-10-22 (research_method §7 組合搜尋第六輪): the blend search on the months up to the month's cutoff
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "blendroll", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "blendscan" {
        # 2026-10-18 (使用者：繼續研究有什麼更好的策略): the search over two- and three-rule accounts — stage 1 on the
        # members' registered development months (research/blend_scan.py), the 20 finalists run as blends and
        # registered; the statistics; the month's rolling selection adds them, their forward test, the forward accounts
        # and the board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "blendscan", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "insiderdata" {
        # 2026-10-19 (使用者：資料先用免費的): insiders' advance notices of share transfers from the Market Observation
        # Post System, listed and OTC, every weekday since 2015-06 (one request per day and market, 3 s apart; the
        # download itself stops for 13:30–14:40 and resumes where it left off).
        Invoke-Step "1-download" @("-m", "quant_platform.research.history", "insider")
    }
    "buybackdata" {
        # 2026-10-19 (使用者：還有什麼方法可以找到更好的策略): companies' buyback programs from the Market Observation
        # Post System, listed and OTC, one request per market and year since 2015.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-download" @("-m", "quant_platform.research.history", "buyback")
    }
    "disclosurestudy" {
        # 2026-10-19 (使用者：還有什麼方法可以找到更好的策略): once the insiders' notices and the buyback programs are
        # downloaded (their pipelines done), the event study of both factors (research_method §7, criteria written first).
        foreach ($other in @("insiderdata", "buybackdata")) {
            $otherRecord = Join-Path $records "$other.json"
            while ($true) {
                $state = if (Test-Path $otherRecord) { Get-Content $otherRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
                $alive = $state -and $state.status -eq "running" -and (Get-Process -Id $state.pid -ErrorAction SilentlyContinue)
                if (-not $alive) { break }
                Start-Sleep -Seconds 120
            }
        }
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-events" @("-m", "quant_platform.research", "factors", "--events", "low_insider_sell_60,buyback_60")
    }
    "factorcorr" {
        # 2026-10-20 (使用者：那些因子有高度相關): every pair of factors' rankings, averaged; the families that move together
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-correlations" @("-m", "quant_platform.research", "factors", "--correlations")
    }
    "modelh" {
        # 2026-10-20 (使用者：持續優化每一個策略): model H (gbm-2.3.0: model G with the insiders' notices and the buybacks),
        # walk-forward; its rules only past its registered gate against gbm-2.2.0; the statistics; the month's rolling
        # selection adds what is new, the forward test, the accounts and the board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-model" @("-m", "quant_platform.research", "model", "--model-version", "gbm-2.3.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "modelh", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "5-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "6-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "7-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "deep2neutral" {
        # 2026-10-20 (使用者：持續優化每一個策略): the deep rules with the score ranked within each volatility tenth; the
        # statistics; the month's rolling selection adds what is new, the forward test, the accounts and the board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2neutral", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "deep24" {
        # 2026-10-20 (使用者：持續優化每一個策略): dl-2.4.0 learns the top tenth of the volatility-adjusted excess — the
        # development years first, the rest only past its gate; its rules (only past the gate); the statistics; once the
        # other pipelines that add to the month are done, the month's rolling selection adds what is new and the rest.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-2.4.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep24", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        foreach ($other in @("modelh", "deep2neutral")) {
            $otherRecord = Join-Path $records "$other.json"
            while ($true) {
                $state = if (Test-Path $otherRecord) { Get-Content $otherRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
                $alive = $state -and $state.status -eq "running" -and (Get-Process -Id $state.pid -ErrorAction SilentlyContinue)
                if (-not $alive) { break }
                Start-Sleep -Seconds 120
            }
        }
        Invoke-Step "4-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "5-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "6-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "7-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "deep2top10" {
        # 2026-10-20 (使用者：持續優化每一個策略): #4941 holding the deep score's top 10; the statistics
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "deep2top10", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "costall" {
        # 2026-10-20 (使用者：持續優化每一個策略): cost-aware switching on every passing rule that does not switch that way
        # yet; the statistics; once the other pipelines that add to the month are done, the month adds what is new.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "costall", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        foreach ($other in @("modelh", "deep24", "deep2top10")) {
            $otherRecord = Join-Path $records "$other.json"
            while ($true) {
                $state = if (Test-Path $otherRecord) { Get-Content $otherRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
                $alive = $state -and $state.status -eq "running" -and (Get-Process -Id $state.pid -ErrorAction SilentlyContinue)
                if (-not $alive) { break }
                Start-Sleep -Seconds 120
            }
        }
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "robot33" {
        # 2026-10-20: the robot 3.3 (3.2 without the cash years) and its yardstick (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "block-rl33")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "robot33", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "bigscan" {
        # 2026-10-20 (使用者：沒看到 1 億個策略不要說有在研究): the streamed search: every 3- and 4-signal set x 2
        # universes x 2 weightings x 2 decision calendars x 3 holding styles; a stopped run goes on from its last block
        # (the folder is kept in instance/research/scans/bigscan-current.txt), then the statistics and the board
        $marker = Join-Path $root "instance/research/scans/bigscan-current.txt"
        $arguments = @("-m", "quant_platform.research", "scan-large", "--broker", "cathay", "--signals-min", "3", "--signals-max", "4", "--finalists", "80")
        if (Test-Path $marker) { $arguments += @("--resume", (Get-Content $marker -Raw -Encoding UTF8).Trim()) }
        Invoke-Step "1-scan" $arguments
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "bigscan2" {
        # 2026-10-20 (使用者：沒看到 1 億個策略不要說有在研究): the second round — every 5-signal set of the 50 signals
        # passing the first round's stage 1 most often x the whole market x 2 weightings x 2 calendars x 3 holding styles
        # (25,425,120). It waits for the first round to finish (its summary.json written — 10-20 22:4x the first round
        # stopped on a GPU error and its process was gone without finishing), sends that round's finalists to the month's
        # list and the forward accounts, then searches; a stopped second round goes on from its own folder.
        $first = Join-Path $root "instance/research/scans/scan-large-1.0.0-20261020-105730"
        Wait-While "等大規模搜尋第一輪跑完（summary.json）" { -not (Test-Path (Join-Path $first "summary.json")) } 300
        Invoke-Step "0-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "3", "--broker", "cathay")
        Invoke-Step "0-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "0-forward" @("-m", "quant_platform.research", "forward")
        $arguments = @("-m", "quant_platform.research", "scan-large", "--broker", "cathay", "--signals-min", "5", "--signals-max", "5",
                       "--universes", "all", "--signals-top", "50", "--signals-from", $first, "--finalists", "80")
        $marker = Join-Path $root "instance/research/scans/bigscan-current.txt"
        if (Test-Path $marker) {
            $folder = (Get-Content $marker -Raw -Encoding UTF8).Trim()
            $progress = Join-Path $folder "progress.json"
            if ((Test-Path $progress) -and ((Get-Content $progress -Raw -Encoding UTF8 | ConvertFrom-Json).sizes -contains 5)) {
                $arguments += @("--resume", $folder)
            }
        }
        Invoke-Step "1-scan" $arguments
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "3", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "holding2_a" {
        # 2026-10-20 (使用者：ML、DL、RL、技術分析、LLM 都要加入評估): half of every family's holding styles
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "holding2_a", "--broker", "cathay")
    }
    "holding2_b" {
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "holding2_b", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "3", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "holding3" {
        # 2026-10-20 (使用者：20 天、月線上都沒看到): every passing strategy in the holding styles it has not run, the
        # statistics, the month's list, the forward accounts and the board (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "holding3", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "3", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "holding4_a" {
        # 2026-10-20 (使用者：持有 20 天、月線上續抱都是策略): half of every other strategy in its two other holding styles
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "holding4_a", "--broker", "cathay")
    }
    "holding4_b" {
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "holding4_b", "--broker", "cathay")
        # the other half first: the statistics and the month's list see both
        Wait-While "等另一半（holding4_a）跑完" {
            Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*--name holding4_a*" } }
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "3", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "next10blend" {
        # 2026-10-21: #6090 (the deep top 10 filled at the next close) sharing an account with the calm and consensus
        # rules, the statistics, the month's list, the forward accounts and the board (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "next10blend", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "top10blend" {
        # 2026-10-20: #5983 (the deep model's top 10 held 20 sessions) sharing an account with the calm and consensus
        # rules, the statistics, the month's list, the forward accounts and the board (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "top10blend", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "llmtrader" {
        # 2026-10-20 (使用者：LLM 能不能做成交易 agent): the LLM trader's decision after the close (research/llm_trader.py)
        Invoke-Step "1-decide" @("-m", "quant_platform.research", "llm-trader")
    }
    "holding" {
        # 2026-10-20 (使用者：20 天、月線上都沒看到；持有方式都是策略): the 30 best strategies in all three holding styles,
        # the statistics, the month's list, the forward accounts and the board
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "holding", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "3", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "rolesplit" {
        # 2026-10-20 (使用者：因子、策略、DL、RL、ML 各負責什麼): the two role accounts, the statistics, the month's list,
        # the forward accounts and the board (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "rolesplit", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "robot34" {
        # 2026-10-20 (使用者：RL 負責什麼): the robot 3.4 over the three roles and its yardstick (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "block-rl34")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "robot34", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "modelgm" {
        # 2026-10-20: model G refitted every month (gbm-2.4.0), its gate against gbm-2.2.0, its rule, the statistics, the
        # month's list, the forward accounts and the board (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-model" @("-m", "quant_platform.research", "model", "--model-version", "gbm-2.4.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "modelgm", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "5-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "6-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "7-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "deep26" {
        # 2026-10-20: dl-2.1.0 refitted every month (dl-2.6.0), its gate against dl-2.1.0, its two rules, the statistics,
        # the month's list, the forward accounts and the board (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-2.6.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep26", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "5-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "6-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "7-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "winner" {
        # 2026-10-20: the frontier's single rules with the winners running on (winner_hold 0.5), the statistics, the
        # month's list and the forward accounts (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "winner", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "meta1" {
        # 2026-10-20 (使用者：前向都是賠錢；持續優化每一個策略): the meta-label model on the deep model's picks, its
        # registered gate, then the veto on #4941 and #4913 (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "meta-label")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "meta1", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "robot32" {
        # 2026-10-20 (使用者：RL 你還有沒有在優化？): the robot 3.2 — 3.1's yearly slots with model G and the deep model in
        # the model slot and a defensive slot — its two rules (robot and the nine alike), the statistics, the board
        # (research_method §7)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "block-rl32")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "robot32", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "techdeep" {
        # 2026-10-20 (使用者：技術分析機器人有沒有在優化？): the technical robot's third version — dl-2.1.0 on prices and
        # volumes only (dl-2.5.0), its registered gate against gbm-2.1.0, then its two rules (research_method §7). Waits
        # for the month's other model work (the GPU and the rolling list).
        foreach ($other in @("modelh", "deep24", "costall")) {
            $otherRecord = Join-Path $records "$other.json"
            while ($true) {
                $state = if (Test-Path $otherRecord) { Get-Content $otherRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
                $alive = $state -and $state.status -eq "running" -and (Get-Process -Id $state.pid -ErrorAction SilentlyContinue)
                if (-not $alive) { break }
                Start-Sleep -Seconds 120
            }
        }
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-2.5.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "techdeep", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "5-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "6-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "7-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "industry" {
        # 2026-10-20 (使用者：持續優化每一個策略): the industry family (daily.INDUSTRY_FACTORS) — its strength, then
        # every 1-4 signal set holding one that passes the novelty gate, the statistics, the board and the leak check
        # (research_method §7, criteria written first). Waits for the two model trainings (they use most of the CPU).
        foreach ($other in @("modelh", "deep24")) {
            $otherRecord = Join-Path $records "$other.json"
            while ($true) {
                $state = if (Test-Path $otherRecord) { Get-Content $otherRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
                $alive = $state -and $state.status -eq "running" -and (Get-Process -Id $state.pid -ErrorAction SilentlyContinue)
                if (-not $alive) { break }
                Start-Sleep -Seconds 120
            }
        }
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-factors" @("-m", "quant_platform.research", "factors", "--recent")
        Invoke-Step "2-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "6", "--finalists", "80", "--signals-min", "1", "--signals-max", "4", "--require-industry")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "5-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "pledgestudy" {
        # 2026-10-20 (使用者：持續優化每一個策略): directors' pledged shares, a page a month since 2015; then the event
        # study of heavy pledging (research_method §7, criteria written first)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-download" @("-m", "quant_platform.research.history", "pledge")
        Invoke-Step "2-events" @("-m", "quant_platform.research", "factors", "--events", "pledge_heavy")
    }
    "threefam" {
        # 2026-10-20 (使用者：持續優化每一個策略): the deep model, the T0 consensus and model G, a third each; the statistics
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "threefam", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "rolling-blends" {
        # 2026-10-18: blends of daily rules (no 0050 part) join the monthly rolling selection — only the rules not yet
        # graded this month are graded on data up to the cutoff and added; then the forward test, the forward accounts
        # (a passing blend from the day after the cutoff) and the common-start board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "2-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "3-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "4-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "scancalm" {
        # 2026-10-18 (使用者：市場不好時不要比 0050 跌得多；繼續再研究): the two-stage search whose finalists also never
        # fell more than a point further than 0050 in its 10% falls of the development period; then the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "2", "--finalists", "40", "--pool", "8000", "--signals-min", "1", "--signals-max", "4", "--prefilter", "--calm")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "forward-rolling" {
        # 2026-10-17 (使用者：前向觀察照現在的月份往前推兩個月起算): once the month's selection is graded and its models
        # are trained (the rolling run has reached its forward test, or finished), every forward account starts again
        # from the day after the cutoff and is recorded through the latest session; then the common-start board.
        $rollingRecord = Join-Path $records "rolling.json"
        while ($true) {
            $state = if (Test-Path $rollingRecord) { Get-Content $rollingRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
            $alive = $state -and $state.status -eq "running" -and (Get-Process -Id $state.pid -ErrorAction SilentlyContinue)
            $holdout = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "quant_platform\.research rolling --step holdout" }
            if (-not $alive -or $holdout) { break }
            Start-Sleep -Seconds 60
        }
        Invoke-Step "1-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "2-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "monthmodels" {
        # 2026-10-21 (使用者 2026-10-16：每月滾動，模型也要更新；稽核：深度學習只有逐年模型): once the GPU is free (the
        # streamed search uses it), every model the forward accounts read refitted to the month's cutoff, the accounts
        # reading them recorded again from their start, the board
        Wait-While "等顯示卡空出來（大規模搜尋在用）" {
            Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*scan-large*" } } 300
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-models" @("-m", "quant_platform.research", "rolling", "--step", "models", "--refit-accounts")
        Invoke-Step "2-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "llmreplay" {
        # 2026-10-21 (使用者：不要跟我說前向觀察只能做一天): the LLM trader's decisions from the cutoff day (8/31) to the
        # latest session, each with that day's information, then the forward accounts from 9/1 and the board
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-replay" @("-m", "quant_platform.research", "llm-trader", "--replay-from", "2026-08-31")
        Invoke-Step "2-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "exec_a" {
        # 2026-10-21: half of every passing strategy in the fill timings it has not run (daily.execution_all_batch)
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "exec_a", "--broker", "cathay")
    }
    "exec_b" {
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "exec_b", "--broker", "cathay")
        # the other half first: the statistics and the month's list see both
        Wait-While "等另一半（exec_a）跑完" {
            Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*--name exec_a*" } }
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "3", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "robustfront" {
        # 2026-10-21: the money-against-falls frontier (#5983, #4941, #5050, #6086, #6087, #5054) and the blends they
        # replaced (#5155, #5163) with every cost doubled and filled a session late (a diagnostic, not a trial)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-check" @("-m", "quant_platform.research", "robustness", "--broker", "cathay", "--trials", "5983,4941,5050,6086,6087,5054,5155,5163")
    }
    "listupdate" {
        # 2026-10-20 23:0x: the month's list already graded (rolling --step select) but its forward test, forward accounts
        # and board stopped (the owner closed the windows): those three steps only
        Invoke-Step "1-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "2-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "rolling-holdout" {
        # 2026-10-16: the month's passing rules' forward test refreshed through the day (the worker, trading days 22:30)
        Invoke-Step "1-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
    }
    "timing" {
        # 2026-10-16 (使用者：盤前或盤中買會不會比較好？): the strongest T0 candidates filled at the next session's close
        # and at its open, against their own after-hours fills; then the statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "timing", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "dealer3" {
        # 2026-10-16 (使用者：CPU 一直滿載，可以中止改用 GPU): dealer2's search was stopped at 208,620 of 683,784 on the
        # CPU; its remaining steps again, the searches' first stage on the GPU.
        Invoke-Step "1-scan-dealer" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "2", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require-dealer")
        Invoke-Step "2-rule-g" @("-m", "quant_platform.research", "daily", "--name", "modelg", "--broker", "cathay")
        Invoke-Step "3-scan-g" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "2", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "4", "--require", "ml_gbm_ext")
        Invoke-Step "4-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "scan4gpu" {
        # 2026-10-16 (GPU): every signal alone over the development period, then every set of four of those that beat
        # 0050 alone — a depth the CPU could not reach; finalists through the engine as always.
        Invoke-Step "1-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "2", "--finalists", "80", "--pool", "5000", "--signals-min", "4", "--signals-max", "4", "--prefilter")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "riskrules" {
        # 2026-10-16: the deep risk model passed its gate (2016-2020 AUC 0.738 against volatility's 0.704): the eight
        # strongest T0 candidates with its two overlays, then the statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "risk", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "worst" {
        # 2026-10-16 (使用者：訓練一個賠最多的模型，反著做的趣味): the reverse robot (GPU), then the three reverse rules.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "worst-rl")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "worst", "--broker", "cathay")
    }
    "rebuild150" {
        # 2026-10-16 (the mechanism audit): engine 1.5.0 — capital reductions, idle cash reinvested on decision days,
        # refused orders retried alone, the stop-loss and the market filter every session, stock dividends credited on
        # distribution. Every rule not yet on 1.5.0 again, the statistics, the board, the trade audit, the leak check.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rerun-all" @("-m", "quant_platform.research", "daily", "--name", "rerun_all", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "4-audit" @("-m", "quant_platform.research", "trade-audit", "--broker", "cathay")
        Invoke-Step "5-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "rebuild143" {
        # 2026-10-16: engine 1.4.3 (a trend holding out of the zone and below its average is sold that session, the
        # rule's decision days or not). After tonight's other pipelines: every rule not yet on 1.4.3 again, the
        # statistics, the board, the trade audit and the leak check.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rerun-all" @("-m", "quant_platform.research", "daily", "--name", "rerun_all", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "4-audit" @("-m", "quant_platform.research", "trade-audit", "--broker", "cathay")
        Invoke-Step "5-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "deep1" {
        # 2026-10-16 (使用者：deep learning 的項目有沒有研究？): the recurrent network over each stock's last 40 sessions,
        # then — only past its registered gate against gbm-2.0.0 — its two rules and every set with it, the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-1.0.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep", "--broker", "cathay")
        Invoke-Step "3-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "4", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "3", "--require", "ml_deep")
        Invoke-Step "4-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "robot41" {
        # 2026-10-16: the robot 4.1 — the free agent trained in the owner's account — then its rule and the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "free-rl41", "--broker", "cathay")
        Invoke-Step "2-rule" @("-m", "quant_platform.research", "daily", "--name", "robot41", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "reductions" {
        # 2026-10-16 (audit): both exchanges' capital reductions, each TWSE one's details (about 400 requests).
        Invoke-Step "1-fetch" @("-m", "quant_platform.research.history", "reductions")
    }
    "robot31" {
        # 2026-10-16 (the audit: 3.0's blocks were chosen on years it was tested on): the robot 3.1 — each test year's
        # slot factors chosen from what was known before the year — then its rule and its yardstick, the statistics.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "block-rl31")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "robot31", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "rebuild141" {
        # 2026-10-14 夜（使用者：到底還有哪些東西是用舊的方式？）: every trained model again on the corrected data —
        # the seven score models, the learned exit, the technical robots 1.0 and 1.1 and the robot 2.1 — then every rule
        # in the registry on engine 1.4.1, the statistics, the board, the trade audit and the leak check.
        foreach ($version in @("gbm-1.0.0", "gbm-1.1.0", "gbm-1.2.0", "gbm-1.3.0", "gbm-1.4.0", "gbm-2.0.0", "gbm-2.1.0")) {
            Invoke-Step "1-model-$version" @("-m", "quant_platform.research", "model", "--model-version", $version)
        }
        Invoke-Step "2-exits" @("-m", "quant_platform.research", "exits")
        Invoke-Step "3-tech-rl-10" @("-m", "quant_platform.research", "tech-rl", "--tech-version", "tech-rl-1.0.0")
        Invoke-Step "4-tech-rl-11" @("-m", "quant_platform.research", "tech-rl", "--tech-version", "tech-rl-1.1.0")
        Invoke-Step "5-robot21" @("-m", "quant_platform.research", "factor-rl21")
        Invoke-Step "6-rerun-all" @("-m", "quant_platform.research", "daily", "--name", "rerun_all", "--broker", "cathay")
        Invoke-Step "7-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "8-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "9-audit" @("-m", "quant_platform.research", "trade-audit", "--broker", "cathay")
        Invoke-Step "10-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "robot21" {
        # 2026-10-14: the robot 2.1 over every factor and model score, its rule and yardstick, the statistics.
        Invoke-Step "1-train" @("-m", "quant_platform.research", "factor-rl21")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "robot21", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "alerts" { Invoke-Step "alerts" @("-m", "quant_platform.research.history", "alerts") }
    "factors-recent" { Invoke-Step "factors-recent" @("-m", "quant_platform.research", "factors", "--recent") }
    "archive" { Invoke-Step "archive" @("scripts/archive_legacy_tables.py") }
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
    "finmind-paid" {
        # 2026-10-21 (使用者：FinMind Sponsor 你要做什麼分析; 路線圖 S9-W22): once the token shows a paid plan (the
        # worker checks every evening), the paid tables with each stock's whole history (listed, TPEx and delisted);
        # the holder brackets into one table with the depository's weekly files; the holder factors' strength from
        # 2015-06 (only them, over the latest full report). The rules come after, from what is strong.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-holders" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "TaiwanStockHoldingSharesPer")
        Invoke-Step "2-build" @("-m", "quant_platform.research.history", "holders")
        Invoke-Step "3-strength" @("-m", "quant_platform.research", "factors", "--recent", "--only", "big_holder_pct,big_holder_up_4w,holders_down_4w,retail_down_4w")
        Invoke-Step "4-download" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "paid")
        Invoke-Step "5-lists" @("-m", "quant_platform.research.history", "finmind", "--codes", "paid-lists")
        Invoke-Step "6-tables" @("-m", "quant_platform.research.history", "paid")
        Invoke-Step "7-strength" @("-m", "quant_platform.research", "factors", "--recent", "--only", "margin_safe,margin_in_profit,low_pledge_loan_20,block_premium_20,block_volume_20,broker_concentration_5")
    }
    "finmind-paid-rest" {
        # 2026-10-21 00:0x: finmind-paid's fourth and fifth steps again, at 2,900 an hour (it and finmind-paid-b had each
        # waited a full hour on the limit; the wait is now until FinMind's next hour)
        Invoke-Step "1-download" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "paid", "--per-hour", "2900")
        Invoke-Step "2-lists" @("-m", "quant_platform.research.history", "finmind", "--codes", "paid-lists")
    }
    "finmind-paid-b" {
        # 2026-10-21: a request takes about 2 seconds (a stock's whole history), so one downloader makes about 1,400
        # an hour of the plan's 6,000: the factors' tables fetched alongside the holder brackets (finmind-paid)
        Invoke-Step "1-download" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "TaiwanStockMarginMaintenance,TaiwanStockBlockTrade,TaiwanStockBrokerDailyConcentration", "--per-hour", "2900")
    }
    "finmind-paid-c" {
        # 2026-10-21: the unrestricted loans, and the exchange's limits and adjusted prices to check the engine against
        Invoke-Step "1-download" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "TaiwanStockLoanCollateralBalance,TaiwanStockPriceLimit,TaiwanStockPriceAdj")
    }
    "finmind-paid-c2" {
        # 2026-10-21: once finmind-paid-b is done, the adjusted prices (the last of the paid tables) alongside
        # finmind-paid's own fourth step (the loans and the limits), the two within the plan's 6,000 an hour
        Wait-While "等 finmind-paid-b 下載完（每小時上限）" { [bool](Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "TaiwanStockMarginMaintenance,TaiwanStockBlockTrade" }) } 120
        Invoke-Step "1-download" @("-m", "quant_platform.research.history", "finmind", "--codes", "all", "--datasets", "TaiwanStockPriceAdj", "--per-hour", "2900")
    }
    "paid-factors" {
        # 2026-10-21: once no FinMind download runs, the paid tables into history/paid and the six factors' strength
        Wait-While "等 FinMind 付費資料下載完" { [bool](Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "quant_platform\.research\.history finmind" }) } 120
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-lists" @("-m", "quant_platform.research.history", "finmind", "--codes", "paid-lists")
        Invoke-Step "2-tables" @("-m", "quant_platform.research.history", "paid")
        Invoke-Step "3-strength" @("-m", "quant_platform.research", "factors", "--recent", "--only", "margin_safe,margin_in_profit,low_pledge_loan_20,block_premium_20,block_volume_20,broker_concentration_5")
    }
    "limitscheck" {
        # 2026-10-21 (使用者 2026-10-14：確實照當時的台股規則): once the paid downloads are done, the engine's limit-up and
        # limit-down prices and locks against the exchange's (FinMind TaiwanStockPriceLimit) from 2015-06
        $paidRecord = Join-Path $records "paid-factors.json"
        Wait-While "等 FinMind 付費資料下載與分析（paid-factors）" { $state = if (Test-Path $paidRecord) { Get-Content $paidRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }; -not $state -or $state.status -eq "running" } 120
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-check" @("-m", "quant_platform.research", "limits-check")
    }
    "intraday" {
        # 2026-10-21 (使用者 2026-10-16：盤中買會不會比較有效; research_method §7「盤中成交時機」): once the paid downloads
        # are done (the plan's hourly requests are theirs until then), the strongest rules' decisions and the next
        # session's minute bars (about 4,000 requests).
        $paidRecord = Join-Path $records "paid-factors.json"
        Wait-While "等 FinMind 付費資料下載與分析（paid-factors）" { $state = if (Test-Path $paidRecord) { Get-Content $paidRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }; -not $state -or $state.status -eq "running" } 120
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-study" @("-m", "quant_platform.research", "intraday")
    }
    "knowledge" {
        # 2026-10-22 (使用者：失敗紀錄都要記起來，作為知識庫; 向量資料庫可用 BGE-M3、Qdrant): the knowledge base rebuilt
        # and its new or changed entries encoded as BGE-M3 vectors into the embedded Qdrant, on the CPU (the scheduler
        # starts it hourly at :45 when a record changed)
        Invoke-Step "1-index" @("-m", "quant_platform.research", "knowledge", "--index", "--device", "cpu")
    }
    "forwardfix" {
        # 2026-10-25 (使用者：最強的策略 9/7 到 10/5 都沒交易，怪怪的；所有策略確定合理嗎): the forward accounts had loaded
        # prices from 2024 only, so cost-aware switching never swapped; every live account recorded again from its start
        # with the backtest's years (old records and decisions archived), then the board, the full decision check and
        # the monthly list's forward refreshed from the new records
        Invoke-Step "1-restart" @("-m", "quant_platform.research", "forward", "--restart-live", "--reason", "2026-10-25修正：前向只載入2024年起的股價，看成本才換股找不到2015到2020的資料而從不換股；改成和回測一樣從2013年載入，全部帳戶封存後從起算日重新記錄")
        Invoke-Step "2-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "3-audit" @("-m", "quant_platform.research", "forward-audit")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
    }
    "forwardrepair" {
        # 2026-10-22 (194 forward accounts out of step after the 10-21 23:31 refit race): those accounts archived and
        # recorded again from their start (stock_forward.restart_accounts; the old records kept in log-archive.jsonl),
        # then the board
        Invoke-Step "1-restart" @("-m", "quant_platform.research", "forward", "--restart-broken", "--reason", "2026-10-22修復：10/21晚上模型重訓重來時，另一個研究流程把舊的決定寫回去，紀錄與決定不一致；封存後從起算日重新記錄")
        Invoke-Step "2-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "veto6" {
        # 2026-10-22 (research_method §7「否決第六版」): holders and earnings together on 8; the lending vetoes on 20 each
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "veto6", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "veto5" {
        # 2026-10-22 (research_method §7「否決第五版」): the price and earnings vetoes on the 20 richest deep-learning rules,
        # after the blend vetoes (at most two simulation batches at once)
        Wait-While "等組合否決（vetoblends）跑完" {
            Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*-Name vetoblends_*" } } 120
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "veto5", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "veto4" {
        # 2026-10-22 (research_method §7「否決第三版」的下一版): the holders' veto on the other 29 passing deep-learning
        # top-20 rules; the statistics, the month's list (only the new rules), its forward test, accounts and board
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "veto4", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "vetoblends_a" {
        # 2026-10-22 (research_method §7「否決第三版」的組合): half of the passing blends with their sleeves' holders'-veto
        # versions (same shares); waits for veto3 (its versions are the twins)
        Wait-While "等否決第三版（veto3_b）跑完" {
            Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*-Name veto3_b*" } } 120
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "vetoblends_a", "--broker", "cathay")
    }
    "vetoblends_b" {
        Wait-While "等否決第三版（veto3_b）跑完" {
            Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*-Name veto3_b*" } } 120
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "vetoblends_b", "--broker", "cathay")
        Wait-While "等另一半（vetoblends_a）跑完" {
            Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*--name vetoblends_a*" } }
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "veto3_a" {
        # 2026-10-22 (research_method §7「否決第三版」): half of the holders' veto on the 120 richest passing top-20 single
        # rules and of both vetoes together on 23
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "veto3_a", "--broker", "cathay")
    }
    "veto3_b" {
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "veto3_b", "--broker", "cathay")
        # the other half first: the statistics and the month's list see both
        Wait-While "等另一半（veto3_a）跑完" {
            Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*--name veto3_a*" } }
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "veto2" {
        # 2026-10-22 (research_method §7「否決第二版」, written before the run): the margin veto (m1) on the ten richest
        # passing single rules without a veto and the holders' veto (h2) on three; the statistics, the month's list
        # (only the new rules), its forward test, the forward accounts and the board
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "veto2", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "engine160" {
        # 2026-10-22 (engine 1.6.0, roadmap S9-W23): the overrides (the exchange's limits where the engine's reckoning
        # differs), then the limits check with them for this process only — the switch itself goes on 10-31
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-overrides" @("-m", "quant_platform.research", "limits-overrides")
        Invoke-Step "2-check" @("-m", "quant_platform.research", "limits-check", "--official")
    }
    "govbank" {
        # 2026-10-22 (research_method §7「八大行庫」, written before the run): the government banks' table from the day
        # files, the two stock factors' strength (short history) and the market-wide net buying as a market signal
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-table" @("-m", "quant_platform.research.history", "paid", "--datasets", "TaiwanStockGovernmentBankBuySell")
        Invoke-Step "2-strength" @("-m", "quant_platform.research", "factors", "--recent", "--only", "govbank_net_20,govbank_dip_buy_20")
        Invoke-Step "3-market" @("-m", "quant_platform.research", "market-signals")
    }
    "opentight" {
        # 2026-10-22 (開盤賣做不做得到): the frontier's rules that sell at the open (#6490, #6759, #6695) with costs doubled
        # and the opening-auction fill capped at 0.2% of the day instead of 0.5% (a diagnostic, not a trial)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-check" @("-m", "quant_platform.research", "robustness", "--broker", "cathay", "--trials", "6490,6759,6695")
    }
    "openfeas" {
        # 2026-10-22 (開盤賣做不做得到): the sampled sells' opening auction against the first odd-lot match and the
        # auction's depth (research/intraday.py feasibility; the stored minute bars, no download)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-check" @("-m", "quant_platform.research", "open-feasibility")
    }
    "fmdaily_a" {
        # 2026-10-22 (使用者：還有什麼要加入分析的; FinMind Sponsor until 11-21): the government banks' days (one request
        # a session), then half of the branches' stock-days (history/finmind_daily.py), 2,700 requests an hour
        Invoke-Step "1-govbank" @("-m", "quant_platform.research.history", "finmind-daily", "--kind", "govbank", "--per-hour", "2700")
        Invoke-Step "2-branches" @("-m", "quant_platform.research.history", "finmind-daily", "--kind", "branches", "--part", "0/2", "--per-hour", "2700")
    }
    "fmdaily_b" {
        # the other half of the branches' stock-days, alongside (together with fmdaily_a under the plan's 6,000 an hour)
        Invoke-Step "1-branches" @("-m", "quant_platform.research.history", "finmind-daily", "--kind", "branches", "--part", "1/2", "--per-hour", "2700")
    }
    "bigscan3" {
        # 2026-10-22 (research_method §7「大規模串流搜尋第三輪：新資訊」): the 50 signals passing round one most often and
        # the five new ones (fewer holders, small holders leaving, margin maintenance, models I and J), sets of 3 and 4
        # holding at least one new signal; then the statistics, the month's list, its forward test, accounts and board
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        $first = Join-Path $root "instance/research/scans/scan-large-1.0.0-20261020-105730"
        Invoke-Step "1-scan" @("-m", "quant_platform.research", "scan-large", "--broker", "cathay", "--signals-min", "3", "--signals-max", "4", "--universes", "all", "--signals-top", "50", "--signals-from", $first, "--require-any", "holders_down_4w,retail_down_4w,margin_safe,ml_gbm_i,ml_gbm_j", "--finalists", "80")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "splitblends_a" {
        # 2026-10-22 (research_method §7): half of the passing blends with their sleeves' split versions
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "splitblends_a", "--broker", "cathay")
    }
    "splitblends_b" {
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "splitblends_b", "--broker", "cathay")
        Wait-While "等另一半（splitblends_a）跑完" {
            Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*--name splitblends_a*" } }
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "splitblends" {
        # 2026-10-22 (research_method §7): every passing blend with its sleeves' split versions (same shares);
        # the statistics, the month's list, its forward test, accounts and board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "splitblends", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "splitblend" {
        # 2026-10-22 (research_method §7): the frontier's blends with their sleeves' split versions; the
        # statistics, the month's list, its forward test, accounts and board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "splitblend", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "split_a" {
        # 2026-10-22 (research_method §7「開盤賣、收盤買」): half of every passing single rule without a split version
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "split_a", "--broker", "cathay")
    }
    "split_b" {
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "split_b", "--broker", "cathay")
        # the other half first: the statistics and the month's list see both
        Wait-While "等另一半（split_a）跑完" {
            Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*--name split_a*" } }
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "split" {
        # 2026-10-22 (盤中成交時機研究; research_method §7): the strongest single rules selling at the next open and
        # buying at the next close; the statistics, the month's list, its forward test, accounts and board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "split", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "jdeep" {
        # 2026-10-22 (research_method §7): model J with the deep model in the four strongest deep rules' frames;
        # the statistics, the month's list, its forward test, accounts and board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "jdeep", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "modelj" {
        # 2026-10-22 (FinMind 付費資料; research_method §7): model J (gbm-2.6.0: model I with the paid tables'
        # factors), walk-forward; its rules only past its gate against gbm-2.5.0; the month's list, forward test
        # and accounts; the new model refitted to the month's cutoff with its accounts recorded again; the board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-model" @("-m", "quant_platform.research", "model", "--model-version", "gbm-2.6.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "modelj", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "5-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "6-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "7-models" @("-m", "quant_platform.research", "rolling", "--step", "models", "--missing-only", "--refit-accounts")
        Invoke-Step "8-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "9-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "deep27" {
        # 2026-10-22 (集保因子第五版; research_method §7): dl-2.7.0 (dl-2.1.0 with model I's features), the
        # development years first, the rest only past its gate against dl-2.1.0; its rules in the four strongest
        # deep rules' frames; the statistics; the month's list, forward test and accounts; the new model refitted
        # to the month's cutoff with its accounts recorded again; the board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-2.7.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep27", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "5-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "6-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "7-models" @("-m", "quant_platform.research", "rolling", "--step", "models", "--missing-only", "--refit-accounts")
        Invoke-Step "8-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "9-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "monthfill" {
        # 2026-10-22: a model made mid-month (its rules already have forward accounts from the cutoff) refitted to the
        # month's cutoff — only the models without it — and its accounts recorded again from their start
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-models" @("-m", "quant_platform.research", "rolling", "--step", "models", "--missing-only", "--refit-accounts")
        Invoke-Step "2-forward" @("-m", "quant_platform.research", "forward")
    }
    "modeli" {
        # 2026-10-22 (集保因子第四版; research_method §7): model I (gbm-2.5.0: model H with the holder brackets),
        # walk-forward; its rules only past its gate against gbm-2.3.0; the statistics; the month's list (only the new
        # rules), its forward test, the accounts and the board.
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-model" @("-m", "quant_platform.research", "model", "--model-version", "gbm-2.5.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "modeli", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "4-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "5-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "6-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "7-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "liveflow" {
        # 2026-10-22 (使用者 2026-10-20：我也要這個——盤中資金流向): the worker starts it at 08:57 on trading days; the
        # whole market's snapshot every 15 seconds until 13:30 (research/live_flow.py; the page /live)
        Invoke-Step "1-session" @("-m", "quant_platform.research", "live-flow")
    }
    "holders3" {
        # 2026-10-22 (research_method §7「集保因子第三版」): the four strongest deep rules, no new buys of the fifth whose
        # chips disperse most; the statistics, the month's list (only the new rules), its forward test, accounts, board
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "holders3", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "holders2" {
        # 2026-10-22 (research_method §7「集保因子第二版」): the four strongest deep rules' frames with the holder factors;
        # the statistics, the month's list (only the new rules), its forward test, the forward accounts and the board
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "holders2", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
    "newdata-now" {
        # 2026-10-21: the holder factors' strength came first (two strong) — their rules now; newdata later adds the paid
        # factors' (the rules run here are reused there)
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "newdata", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "newdata" {
        # 2026-10-21 (使用者付了 FinMind Sponsor：請繼續研究; research_method §7「FinMind 付費資料的因子與策略」): once the
        # paid factors' strength is in (the paid-factors pipeline done), the strong new factors' rules
        # (daily.new_data_batch), the statistics, the month's list (only the new rules), its forward test, the forward
        # accounts and the board.
        $paidRecord = Join-Path $records "paid-factors.json"
        Wait-While "等付費資料的因子強弱分析（paid-factors）" { $state = if (Test-Path $paidRecord) { Get-Content $paidRecord -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }; -not $state -or $state.status -eq "running" } 120
        Wait-While "13:30～14:40 盤後單時段，研究程式避開" { (Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40" } 300
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "newdata", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-select" @("-m", "quant_platform.research", "rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay")
        Invoke-Step "4-holdout" @("-m", "quant_platform.research", "rolling", "--step", "holdout", "--broker", "cathay")
        Invoke-Step "5-forward" @("-m", "quant_platform.research", "forward")
        Invoke-Step "6-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
    }
}
Write-PipelineRecord "done"
