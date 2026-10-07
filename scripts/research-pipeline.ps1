param([ValidateSet("data-a", "daily", "model", "statements", "statements-refresh", "rl", "factors-recent", "archive", "seed", "factors", "finmind")][string]$Name = "daily")
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
        Invoke-Step "rl" @("-m", "quant_platform.research", "rl", "--broker", "cathay")
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
