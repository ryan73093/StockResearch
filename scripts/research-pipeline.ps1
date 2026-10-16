param([ValidateSet("data-a", "daily", "model", "statements", "statements-refresh", "rl", "turnover", "exits", "execution", "blends", "rl-sleeves", "model-60", "t0hunt", "ai-researcher", "news-events", "lowdd", "largecap", "largecap2", "evidence", "scan", "scan4", "robots", "resume4", "robot2", "wide4", "consensus", "trials", "leakcheck", "devconsensus", "modelf", "costswitch", "rawv2", "techmodel", "hypotheses", "minhold", "engine13", "engine131", "dividends", "engine140", "t0scan5", "timefactors", "factormom", "audit", "robot20", "shortdata", "shortfactors", "alerts", "robot21", "rebuild141", "lendingdata", "lendingfactors", "rebuild142", "corporate", "issuance", "dealermodel", "robot30", "robot40", "rebuild143", "dealer2", "deep1", "robot41", "reductions", "robot31", "rebuild150", "worst", "dlrisk", "riskrules", "dealer3", "scan4gpu", "timing", "factors-recent", "archive", "seed", "factors", "finmind")][string]$Name = "daily")
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
    $out = Join-Path $logs "$Name-$stamp-$Label.log"
    $err = Join-Path $logs "$Name-$stamp-$Label.err.log"
    Start-Process -FilePath $python -ArgumentList $Arguments -WorkingDirectory $root -NoNewWindow -Wait `
        -RedirectStandardOutput $out -RedirectStandardError $err
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
    "audit" { Invoke-Step "audit" @("-m", "quant_platform.research", "trade-audit", "--broker", "cathay") }
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
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40") {
            Start-Sleep -Seconds 300
        }
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
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40") {
            Start-Sleep -Seconds 300
        }
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
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40") {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-train" @("-m", "quant_platform.research", "block-rl3")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "robot30", "--broker", "cathay")
        Invoke-Step "3-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "robot40" {
        # 2026-10-16 (使用者：不要給 RL 加一堆限制，只受交易所規則；RL 沒打贏 0050 之前不要停): the robot 4.0 — every session
        # its own weights for every stock and for cash, learned from every session (research/rl_free.py) — then its
        # rule (free: executed under the exchange's rules) and the statistics. The rule waits out 21:15–21:50.
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40") {
            Start-Sleep -Seconds 300
        }
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
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40") {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-train" @("-m", "quant_platform.research", "worst-rl")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "worst", "--broker", "cathay")
    }
    "rebuild150" {
        # 2026-10-16 (the mechanism audit): engine 1.5.0 — capital reductions, idle cash reinvested on decision days,
        # refused orders retried alone, the stop-loss and the market filter every session, stock dividends credited on
        # distribution. Every rule not yet on 1.5.0 again, the statistics, the board, the trade audit, the leak check.
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40") {
            Start-Sleep -Seconds 300
        }
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
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40") {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-rerun-all" @("-m", "quant_platform.research", "daily", "--name", "rerun_all", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
        Invoke-Step "3-board" @("-m", "quant_platform.research", "forward-board", "--broker", "cathay")
        Invoke-Step "4-audit" @("-m", "quant_platform.research", "trade-audit", "--broker", "cathay")
        Invoke-Step "5-leakcheck" @("-m", "quant_platform.research", "leakcheck")
    }
    "deep1" {
        # 2026-10-16 (使用者：deep learning 的項目有沒有研究？): the recurrent network over each stock's last 40 sessions,
        # then — only past its registered gate against gbm-2.0.0 — its two rules and every set with it, the statistics.
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40") {
            Start-Sleep -Seconds 300
        }
        Invoke-Step "1-train" @("-m", "quant_platform.research", "model", "--model-version", "dl-1.0.0")
        Invoke-Step "2-rules" @("-m", "quant_platform.research", "daily", "--name", "deep", "--broker", "cathay")
        Invoke-Step "3-scan" @("-m", "quant_platform.research", "scan", "--broker", "cathay", "--workers", "4", "--finalists", "80", "--pool", "5000", "--signals-min", "1", "--signals-max", "3", "--require", "ml_deep")
        Invoke-Step "4-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
    }
    "robot41" {
        # 2026-10-16: the robot 4.1 — the free agent trained in the owner's account — then its rule and the statistics.
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40") {
            Start-Sleep -Seconds 300
        }
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
        while ((Get-Date).TimeOfDay -ge [TimeSpan]"13:30" -and (Get-Date).TimeOfDay -lt [TimeSpan]"14:40") {
            Start-Sleep -Seconds 300
        }
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
}
Write-PipelineRecord "done"
