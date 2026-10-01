# 盤後決策台 — 當前開發交接

記錄時間：2026-10-01 16:45（Asia/Taipei；16:41 依使用者要求停止服務）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則。部署一律 `scripts\deploy.ps1`；只停服務用 `scripts\stop-services.ps1`，只啟動用 `scripts\start-services.ps1`；還原資料庫步驟在「資料庫安全」。測試一律用暫存資料庫（`tests/conftest.py` 會擋下開啟正式資料庫的測試）。
2. `docs/development_roadmap.md`：S6 done；S5 基準版 doing（S5-W07 LINE 等使用者設定）；S4-W03、W04、W05 doing；S1-W05 量測到 10/07。
3. `DEVELOPMENT_HISTORY.md` 最上方幾筆（10/01 下午）：服務中的 Yahoo 全部失敗的根本原因（`PYTHONUTF8=1` 與中文路徑）、股利提醒、任一天查詢、測試誤寫正式資料庫、今日頁一鍵回報成交、情境模擬、每週研究報告、使用教學。
4. 使用者要求：所有回覆與進度說明用繁體中文。研究 CLI 一律在專案根目錄以 `$env:PYTHONPATH="src"` 從原始碼執行（`.venv` 裡安裝的是部署版）。UI 預覽用暫存資料庫（不要用預設設定連正式資料庫）。

## 先驗收：AI 研究員第一晚（2026-10-01 22:00）

只有 worker 在執行才會跑：重開機登入後服務會自動啟動；22:00 時 worker 沒在跑、但 23:00 前啟動也會補跑（misfire 寬限 1 小時），否則順延到隔晚。

- 看 `instance\research\journal.jsonl` 的新輪次（研究頁「AI 研究員」卡）：每輪有分析、假設、理由；被拒絕的設定有原因；通過的設定是開發期候選試驗（`trials.jsonl`），統計檔 `instance\research\stats\development-*.json` 更新。
- 看 `instance\research\agent\usage.jsonl`：每次呼叫都有 token 與費用；本月費用遠低於 US$3。
- 異常時：`llm_error` 看錯誤訊息（HTTP 狀態與 OpenAI 錯誤說明）；`budget_exceeded` 表示預算用完。白天可用 `python -m quant_platform.research agent --dry-run` 檢查提示、`--check` 檢查連線（極小費用）；研究本身只在夜間跑。
- 驗收通過後把路線圖 S4-W04 改為 done，證據寫入開發歷程。

## 當前工作包：S5-W06 舊決策程式退場（等使用者決定範圍）

目標：每日建議已改由投資計畫產生（S5-W02），舊版盤後 AI 只剩今日頁收合的「研究模型觀察」；把 13:50 流程中只為舊決策服務的重運算移出交易時段。

量測（10/01 第一次完整台股流程；第一次嘗試因測試誤寫的 4 檔 ETF 在品質閘門失敗，數字取兩次嘗試與 09/07–09/08 的歷史）：

| 步驟 | 耗時 | 誰在用 |
|---|---|---|
| 行情（官方收盤；部署後最多等 10 分鐘） | 24 秒（官方表已公布時） | 今日建議、持倉估值、資料狀態 |
| 官方名冊比對 | 15 秒 | 資料品質（下市處理） |
| 價格特徵 | 約 4.5 分 | 舊版模型 |
| GPU 模型 | 2–8 分 | 舊版模型 |
| 舊版早盤決策 | 1.5–2 分 | 今日頁「研究模型觀察」 |
| 籌碼與基本面（FinMind 額度常用完） | 0.5–7 分 | 舊版特徵 |
| 總經 | 0.5–1 分 | 舊版特徵 |
| 因子研究 | 約 8 分 | 舊版決策 |
| 樣本外回測 | 約 59 分 | 舊版決策 |
| 策略整合 | 約 5 分 | 舊版決策 |
| 組合風險（九種配置） | 約 1.3 分 | 舊版決策 |
| 每日決策、盤後 AI、報告 | 數分鐘 | 舊版頁面、Email 報告 |

提案（需求 §13 的變更要使用者同意）：
- A（建議）：13:50 只跑行情、名冊比對與原始資料品質（今日頁的品質標示），約 1–11 分鐘；其餘舊版步驟改到 18:30 夜間工作（今日頁「研究模型觀察」改顯示前一晚的結果並標日期）。程式與資料都保留，前向的舊模型預測照樣累積。
- B：舊版步驟全部暫停（`PAUSED_MODULES`），今日頁移除「研究模型觀察」，S8 再決定刪除。
- 記憶體（10/01 15:40 實測）：主機 31 GiB 只剩 3.6 GiB，worker 私有記憶體 7 GB 大多被換到分頁檔（2.3 億次分頁錯誤），因子研究跑了 36 分鐘（平常約 8 分鐘）。舊版步驟移走或暫停也會解除這個壓力。
- C：維持現狀（每天 13:50–約 15:50 佔用 CPU 與每日寫入鎖；像 10/01 這樣閘門失敗會整套重跑；舊版決策更新後第一次開今日頁要重建快取——10/01 15:11 實測 12 秒，之後 40–170 毫秒）。

驗收：台股流程的關鍵部分在 14:00 前完成；今日頁與計畫建議不受影響；暫停或移動的項目列在系統頁。回復：`PAUSED_MODULES`／排程還原或 `git revert`。

## 今天與明天要確認的事

| 時間 | 事項 | 看哪裡 |
|---|---|---|
| 使用者重開機後 | 確認服務已自動啟動（`/health`）；部署新版（`scripts\deploy.ps1`，避開 13:30–14:40 與 22:00 AI 研究員）後驗收：教學 `/help`、今日頁開始使用清單與異常提示、計畫預覽、持倉（回撤、每月底對照、任一天查詢、股利、大跌情境、回報成交帶入）、研究頁本週報告、系統頁除息資料；本機與外網、桌面與 375 px。快速檢查腳本可照 `GET /`、`/help`、`/holdings`、`/plan`、`/research`、`/system` 找關鍵字 | 開發歷程「部署」一筆 |
| 10/01 22:00 | AI 研究員第一晚（見上方） | 研究頁、`instance\research\journal.jsonl` |
| 10/02 13:50 | 部署後第一次台股流程：官方收盤等待（不再逐檔問 Yahoo）、yfinance 不再出現 curl 77 或 possibly delisted；4 檔新 ETF 已有 2020 年起日線 | `scheduler_job_runs`、`instance\worker.stderr.log` |
| 10/02 起 | 除權除息預告每 12 小時更新 | `instance\events\ex_dividends.json`、系統頁背景工作 |
| 到 10/07 | S1-W05 收盤資料時效量測（櫃買與盤後零股何時公布） | 系統頁「收盤資料時效」 |

## 環境現況（2026-10-01 15:10）

| 項目 | 狀態 |
|---|---|
| 監督程序 | 排程工作 `StockResearchLocalServices`（使用者登入時觸發），含 Tunnel；紀錄 `instance\supervisor.log` |
| 網站 | **16:41 依使用者要求停止全部服務**（使用者稍後重開機，登入後排程工作會自動啟動）；Waitress 127.0.0.1:5000、`AUTH_MODE=cloudflare-access`；已安裝的是 12:05 部署的版本，之後的提交都還沒部署——先 `scripts\deploy.ps1`，再依下表驗收 |
| 資料庫 | `instance\quant_platform.db` 約 9.7 GiB；`research_universe` 新增 4 檔 ETF（id 555–558）；每日備份 `instance\backups\daily\`；兩個 22.29 GiB 暫存備份待使用者刪除 |
| 研究資料 | `instance\research\`：history（10 個序列）、reports、trials.jsonl、stats、forward、promotions.jsonl |
| 憑證副本 | `C:\ProgramData\StockResearch\cacert.pem`（yfinance 用，見開發歷程） |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>`；388 通過、1 略過 |
| 同主機其他服務 | VectorDB 5001 與其 Tunnel、PimiServices 共用 cloudflared 服務（YtSummary／AutoLayout）。一律不操作 |

## 待使用者確認或操作

| 項目 | 建議 | 需要時點 |
|---|---|---|
| S5-W06 範圍 | A、B 或 C（見上方） | 下一個工作包 |
| LINE 官方帳號與 token | 依 `docs/line-notifications.md` 建立 Messaging API channel，在 `.env` 填 `LINE_ENABLED`、`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_TO`，部署後按系統頁測試 | S5-W07 驗收 |
| 台新、國泰對帳單 CSV | 各提供一份範例（可遮蔽帳號）以實作匯入 | S5-W03 |
| 兩個 22.29 GiB 暫存備份 | 確認後由使用者自行刪除（指令見最後回覆） | 任何時候 |

已定（2026-10-01）：月退的退佣不計入、估算用原價；AI 研究員每月 US$3；非投入日的「今天不需操作」保留。
