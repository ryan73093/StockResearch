# 盤後決策台 — 當前開發交接

記錄時間：2026-10-01 11:00（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則。部署一律 `scripts\deploy.ps1`；只停服務用 `scripts\stop-services.ps1`，只啟動用 `scripts\start-services.ps1`；還原資料庫步驟在「資料庫安全」。
2. `docs/development_roadmap.md`：S3-W01～W05、S4-W06 done；S4-W03、W04、W05 doing；S5 基準版 doing（S5-W07 LINE 等使用者設定）；S1-W05 量測到 10/07。
3. `DEVELOPMENT_HISTORY.md` 最上方幾筆：S4-W06 晉級流程；使用者決定（月退不計入、預算、LINE 摘要）；S4-W04 AI 研究員；除權息解析修正與第一批研究重跑、券商設定、LINE 通知。
4. 使用者要求：所有回覆與進度說明用繁體中文。研究 CLI 一律在專案根目錄以 `$env:PYTHONPATH="src"` 從原始碼執行（`.venv` 裡安裝的是部署版，`python -m` 不加 PYTHONPATH 會跑到舊版）。

## 先驗收：AI 研究員第一晚（2026-10-01 22:00）

- 看 `instance\research\journal.jsonl` 的新輪次（研究頁「AI 研究員」卡）：每輪有分析、假設、理由；被拒絕的設定有原因；通過的設定是開發期候選試驗（`trials.jsonl`），統計檔 `instance\research\stats\development-*.json` 更新。
- 看 `instance\research\agent\usage.jsonl`：每次呼叫都有 token 與費用；本月費用遠低於 US$3。
- 異常時：`llm_error` 看錯誤訊息（HTTP 狀態與 OpenAI 錯誤說明）；`budget_exceeded` 表示預算用完。白天可用 `python -m quant_platform.research agent --dry-run` 檢查提示、`--check` 檢查連線（極小費用）；研究本身只在夜間跑。
- 驗收通過後把路線圖 S4-W04 改為 done，證據寫入開發歷程。

## 當前工作包：S6-W01 今日頁行動卡

目標：有計畫時，一般交易日 3 分鐘內看懂要不要操作；投入日可以直接照抄委託到券商 App。

範圍（需求 §3、§9；路線圖 S6-W01）：
1. 行動卡：今天要做什麼（投入／再平衡／不需操作／等收盤／缺資料），盤後零股時段倒數（13:40 開始收單、14:30 截止；截止後顯示「今日已截止」）。
2. 委託清單：每筆代號、買賣、股數、限價、預估金額與手續費；「複製委託」按鈕（每筆一行，例如「0050 買進 99 股 限價 100.20」），手機可直接貼到券商 App 備忘或對照。
3. 三點原因：取 `PlanDecision.reasons` 前三點；其餘收在「查看依據」。
4. 需要留意（只在有影響時出現）：收盤資料延遲、資料品質警告、可承受回撤接近上限。
5. 資料時間：收盤日期、決策產生時間。

測試：投入日與不需操作日兩種樣式、倒數與截止狀態（以固定時間測）、複製文字內容、375 px 單畫面看完主要資訊。回復：`git revert` 後部署。

待確認：無。

## 今天（2026-10-01）要確認的事

| 時間 | 事項 | 看哪裡 |
|---|---|---|
| 13:30–14:45 | 收盤時效量測寫入四個來源 | 系統頁「收盤資料時效」、`instance\close_availability.jsonl` |
| 13:45–14:25 | LINE 未設定時 `line_plan_advice` 應靜默略過（不報錯） | `instance\*.stderr.log` |
| 22:00 | AI 研究員第一晚（見上方「先驗收」） | 研究頁、`instance\research\journal.jsonl` |
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
| LINE 官方帳號與 token | 使用者目前在遠端，稍後處理：依 `docs/line-notifications.md` 建立 Messaging API channel，在 `.env` 填 `LINE_ENABLED`、`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_TO`，部署後按系統頁測試 | S5-W07 驗收 |
| 台新、國泰對帳單 CSV | 各提供一份範例（可遮蔽帳號）以實作匯入；實際損益以對帳單為準 | S5-W03 |
| 定期定額基準 ETF | 預設 0050 | S3 |

已定（2026-10-01）：月退的退佣不計入、估算用原價；AI 研究員每月 US$3；非投入日的「今天不需操作」保留。
