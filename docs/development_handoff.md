# 盤後決策台 — 當前開發交接

記錄時間：2026-09-30 21:00（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則（服務與資料庫安全、commit 作者、驗收方式）。
2. `docs/development_roadmap.md`：S1 階段的工作包與門檻。
3. `REQUIREMENTS.md` §6、§10。
4. `DEVELOPMENT_HISTORY.md` 最上方兩筆：S1-W01 交付內容，以及 S0 的量測數據與環境觀察。

## 上一輪完成（S1-W01 官方交易日曆）

- `src/quant_platform/market_calendar/` 已接入每日行情、官方收盤來源、資料品質、盤後決策與排程；全部測試 205 通過。
- 程式已 commit，**尚未部署到執行中的服務**（服務跑的是 `.venv` 內已安裝的舊版），部署在本工作包完成。
- 尚未 push：工具環境無法做 GitHub 互動登入，等使用者在自己的終端機 push 一次後，Git Credential Manager 會記住憑證。

## 當前工作包：S1-W04 服務啟停與效能

目標：用可核對身分的腳本安全啟停本專案服務；找出並修正 worker 長時間高 CPU 與首頁無回應；部署 S1-W01。

範圍：
1. 啟停腳本（參考 `C:\Users\皮咪\Project\VectorDB\scripts\start-published-site.ps1`、`start-dedicated-tunnel.ps1` 的做法，不修改 VectorDB）：
   - `scripts/start-services.ps1`、`scripts/stop-services.ps1`：PID 檔放 `.runtime/`；停止前核對 PID、執行檔完整路徑（`.venv\Scripts\quant-web.exe` 等）、命令列、port 5000／8000。
   - 先處理 `scripts/run_local_services.ps1` 監督程序：它會在服務退出後自動重啟，停服務前要先停它（核對其 PowerShell 程序命令列包含 `run_local_services.ps1`）。
   - 啟動以脫離工具工作階段的方式執行（例如 WMI `Win32_Process.Create`）。
2. Web 改用 Waitress（新增依賴 `waitress`）；`/health` 在 1 秒內回應。
3. Worker 高 CPU：
   - 先量測：暫停前記錄 CPU、目前正在執行的工作（worker 日誌、`scheduler_job_runs`）。
   - 主要線索：`scheduler/runner.py:20` `run_universe_backfill` 每 10 分鐘執行；當 `data_ready_assets >= target_ready_assets` 時每批都重跑 `model_research_pipeline`、`factor_research_pipeline`、`ensemble_research_pipeline`、`portfolio_research_pipeline`、`daily_decision_pipeline`。註解寫的是「達標時跑一次」，實作是「達標後每次都跑」。
   - 修正方向：只在「首次達標」或「有新標的完成補資料」時觸發一次，並記錄觸發原因；研究工作移到夜間時段（與 S1-W06 一致）。
4. 部署 S1-W01：停服務 → `.\.venv\Scripts\python.exe -m pip install .`（非 editable）→ 啟動 → 確認 worker 日誌出現 `market_calendar_refresh` 工作、`instance/market_calendar/twse_cache.json` 已存在。

驗收：
- 首頁與 `/health` 2 秒內回應；worker 閒置時 CPU 接近 0（量測 10 分鐘）。
- 啟停腳本只影響本專案程序（VectorDB 5001、各 `cloudflared`、AutoLayout、YtSummary 不受影響）。
- 下一個交易日（10/1）13:45 後排程紀錄的期望交易日正確；10/9（國慶日補假）排程略過。

回復：`git revert` 本工作包 commit → `pip install .` → 用舊的 `scripts/run_local_services.ps1` 啟動。

時段限制：避開 13:30–14:40 重啟服務。

## 之後的順序

S1-W02（3 個嚴重品質問題）→ S1-W03（資料庫瘦身，需要本工作包的停機腳本）→ S1-W06（暫停非核心收集）→ S1-W05（收盤資料時效實測，需連續 5 個交易日，可背景累積）→ S2（Cloudflare 與新介面骨架，網址 `stockresearch.pimi-sunsun.com`）。

## 環境現況（2026-09-30 21:00）

| 項目 | 狀態 |
|---|---|
| Web `quant-web.exe` | PID 21688，port 5000；首頁 60 秒無回應 |
| API `quant-api.exe` | PID 21940，port 8000；可回應 |
| Worker `quant-worker.exe` | PID 22024；自 9/26 起累計 CPU 約 25.5 小時；台股品質閘門阻擋中 |
| 服務監督 | `scripts/run_local_services.ps1`（失敗三次重啟）；監督程序 PID 未核對 |
| 資料庫 | `instance/quant_platform.db` 22.3 GiB + WAL 約 1.45 GiB |
| 交易日曆 | `instance/market_calendar/twse_cache.json` 已由 CLI 建立（2026 年）；服務尚未載入新程式 |
| 磁碟 | C 槽可用約 510 GB |
| 套件 | 未安裝 pyarrow、duckdb、PyJWT、waitress |
| Git | `C:\Program Files\Git\cmd\git.exe` 2.56.0；遠端 `https://github.com/ryan73093/StockResearch.git`；本機領先遠端，待使用者 push |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>` |
| 同主機其他服務 | VectorDB Waitress 5001（PID 248524）與其 Tunnel（PID 10704）；另一個 `cloudflared` PID 5508；AutoLayout、YtSummary。一律不操作 |

## 待使用者確認

| 項目 | 建議 | 需要時點 |
|---|---|---|
| Access application 建立方式 | 使用者在 Zero Trust 建立，或授權以 API 建立 | S2-W02 |
| AI 研究員的 LLM Provider 與每月預算 | Anthropic Claude 或既有 OpenAI 設定 | S4 開始前 |
| 通知管道 | Email 之外是否加 Telegram 或 LINE Messaging API | S1-W06／S6 |
| 券商手續費折扣與最低費用 | 用於成交模型 | S3-W03 |
| 定期定額基準 ETF | 預設 0050 | S3 |
