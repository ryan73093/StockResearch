# 盤後決策台 — 當前開發交接

記錄時間：2026-10-01 10:40（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則。部署一律 `scripts\deploy.ps1`；只停服務用 `scripts\stop-services.ps1`，只啟動用 `scripts\start-services.ps1`；還原資料庫步驟在「資料庫安全」。
2. `docs/development_roadmap.md`：S3-W01～W05 done；S4-W03、W05 doing；S5 基準版 doing（含 S5-W07 LINE 通知）；S1-W05 量測到 10/07。
3. `DEVELOPMENT_HISTORY.md` 最上方兩筆：除權息解析修正與第一批研究重跑、券商設定、LINE 通知；無人值守第一輪與計畫表單修正。
4. 使用者要求：所有回覆與進度說明用繁體中文。研究 CLI 一律在專案根目錄以 `$env:PYTHONPATH="src"` 從原始碼執行（`.venv` 裡安裝的是部署版，`python -m` 不加 PYTHONPATH 會跑到舊版）。

## 當前工作包：S4-W04 AI 研究員（OpenAI `gpt-6-luna`）

目標：夜間由 LLM 提出策略假設與設定檔，經 Schema 驗證後在開發期回測、登錄試驗、寫研究日誌，再依結果提出下一個假設；只產生研究證據，不碰保留期，不接入每日建議。

使用者決定（2026-10-01）：比照 VectorDB 專案使用 `gpt-6-luna`。VectorDB 做法（`C:\Users\皮咪\Project\VectorDB`，只讀參考）：OpenAI Responses API 以 `urllib` 呼叫（不用 SDK）、`store: false`、金鑰取 `OPENAI_API_KEY` 或 `OPENAI_KEY_ENV_FILE` 指向的 dotenv（只讀其中 `OPENAI_API_KEY`）、每次呼叫記錄 token 與費用（`gpt-6-luna` 每百萬 token：輸入 0.125、快取輸入 0.0125、輸出 0.50 美元）、每月預算硬上限。本專案 `.env` 已有 `OPENAI_API_KEY`（`OPENAI_RESPONSE_MODEL` 目前寫 `gpt-5.6-luna`，研究員另設 `RESEARCH_AGENT_MODEL=gpt-6-luna`）。

範圍：
1. `research/agent/`：Provider adapter（Responses API、JSON 結構化輸出、逾時與重試、不記錄金鑰）、費用帳本（`instance/research/agent/usage.jsonl`）、每月預算與每晚試驗數上限（超過即停並記錄）。
2. 提示內容：研究題目與規範（需求 §7、§8）、StrategySpec JSON Schema、目前資料版本的試驗摘要與淘汰原因（只給開發期結果，不給驗證期與保留期）。輸出：假設、理由、1～N 個設定檔。
3. 驗證：pydantic 解析失敗、指名個股、非目錄 ETF、重複設定都拒絕並寫入日誌；通過的以 `run_trial(kind="candidate", period="development")` 登錄（計入多重檢定）。
4. 研究日誌：`instance/research/journal.jsonl`（假設、理由、設定檔雜湊、試驗編號、結果、淘汰原因、費用）。研究頁顯示最近一輪與本月費用。
5. 排程：worker 夜間（預設 22:00，19:00–07:00 之間）執行一輪；預算或上限用完就停。CLI `python -m quant_platform.research agent --dry-run`（不呼叫 API）與 `--once`。

測試：以假的 Provider 回應測試解析、拒絕規則、預算停止、日誌內容、不讀保留期；費用計算手算。上線前以 `--once` 實際呼叫一次並記錄費用。

回復：worker 工作可由設定關閉（`RESEARCH_AGENT_ENABLED=false`）；試驗紀錄只新增不刪。

待確認：每月預算上限（實作先以每月 3 美元、每晚 20 個試驗為預設，等使用者決定）。

## 今天（2026-10-01）要確認的事

| 時間 | 事項 | 看哪裡 |
|---|---|---|
| 13:30–14:45 | 收盤時效量測寫入四個來源 | 系統頁「收盤資料時效」、`instance\close_availability.jsonl` |
| 13:45–14:25 | LINE 未設定時 `line_plan_advice` 應靜默略過（不報錯） | `instance\*.stderr.log` |
| 13:50 後 | 第一次完整台股流程（09-08 以來第一次）：今日頁 14:30 前有盤後計畫；記錄各子工作耗時（S1-W06），暫停步驟不應出現 | `scheduler_job_runs` |
| 15:15、15:30 | 研究資料補抓（除權息改依表頭解析後的第一次排程）、前向模擬紀錄 | 系統頁「最近執行」、`instance\research\forward\log.jsonl` |
| 流程正常後 | 提供使用者刪除兩個 22.29 GiB 暫存備份的指令（使用者執行）：`instance\backups\quant_platform-pre-s1w03-20260930-204109.db`、`quant_platform-pre-vacuum-20260930-204109.db` | — |

## 環境現況（2026-10-01 10:40）

| 項目 | 狀態 |
|---|---|
| 監督程序 | 排程工作 `StockResearchLocalServices`（使用者登入時觸發），含 Tunnel；紀錄 `instance\supervisor.log` |
| 網站 | Waitress 127.0.0.1:5000；`AUTH_MODE=cloudflare-access`；本輪部署見開發歷程 |
| 資料庫 | `instance\quant_platform.db` 約 9.6 GiB；本輪新增 `broker` 欄（`investment_plans`、`actual_cash_flows`、`actual_trades`）；每日備份 `instance\backups\daily\`；兩個 22.29 GiB 暫存備份待刪 |
| 研究資料 | `instance\research\`：history（10 個序列，除權息已修正）、reports、trials.jsonl（142 筆：第一輪 71 筆舊資料版本＋重跑 71 筆）、stats |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>`；353 通過、1 略過 |
| 同主機其他服務 | VectorDB 5001 與其 Tunnel、PimiServices 共用 cloudflared 服務（YtSummary／AutoLayout）。一律不操作 |

## 待使用者確認或操作

| 項目 | 建議 | 需要時點 |
|---|---|---|
| LINE 官方帳號與 token | 依 `docs/line-notifications.md` 建立 Messaging API channel，在 `.env` 填 `LINE_ENABLED`、`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_TO`，部署後按系統頁測試 | S5-W07 驗收 |
| 台新、國泰實際手續費 | 以對帳單確認折數、最低手續費、當日折或月退；目前標示「待確認」 | S5-W03 |
| 台新、國泰對帳單 CSV | 各提供一份範例（可遮蔽帳號）以實作匯入 | S5-W03 |
| AI 研究員每月預算 | 預設每月 3 美元、每晚 20 個試驗 | S4-W04 |
| LINE 每日摘要 | 非投入日「今天不需操作」是否保留（預設保留） | S5-W07 |
| 定期定額基準 ETF | 預設 0050 | S3 |
