param([ValidateSet("data-a", "daily", "model", "statements", "statements-refresh", "rl", "turnover", "exits", "execution", "blends", "rl-sleeves", "model-60", "t0hunt", "ai-researcher", "news-events", "lowdd", "factors-recent", "archive", "seed", "factors", "finmind")][string]$Name = "daily")
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
        # 2026-10-09: 100% stocks aiming at a drawdown within 5 points of 0050's; the statistics.
        Invoke-Step "1-rules" @("-m", "quant_platform.research", "daily", "--name", "lowdd", "--broker", "cathay")
        Invoke-Step "2-stats" @("-m", "quant_platform.research", "stats", "--family", "daily")
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
        Invoke-Step "rl" @("-m", "quant_platform.research", "rl-sleeves", "--broker", "cathay", "--sleeves-version", "rl-sleeves-1.1.0")
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
