# 盤後決策台 — 當前開發交接

記錄時間：2026-09-30 23:55（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則。部署一律 `scripts\deploy.ps1`；只停服務用 `scripts\stop-services.ps1`，只啟動用 `scripts\start-services.ps1`；還原資料庫步驟在「資料庫安全」。
2. `docs/development_roadmap.md`：S2 已完成；接著 S1-W06 → S1-W05 → S1-W07，再進 S3。
3. `DEVELOPMENT_HISTORY.md` 最上方三筆：S2-W05 備份、S2-W03 第二版介面、Cloudflare 上線。

## 上一輪完成

- 介面第二版：電腦完整側邊欄、iPad 圖示側欄、手機底部分頁；深色預設（`sr_theme` cookie 切換）；台股紅漲綠跌；導覽顯示台股時段。
- 每日 03:00 資料庫線上備份（保留 7 份）與還原演練；系統頁顯示備份狀態與每日排程表。首次備份與演練都通過。

## 當前工作包：S1-W06 暫停非核心收集

目標：每個交易日的流程只跑決策需要的步驟，暫停需求 §13 列出的模組收集；保留程式與既有資料；量測耗時與 CPU 下降。

盤點（`application/automation.py` `AutomationService.execute`，台股流程在 `DailyResearchPipeline.run` 之後依序執行）：

| 步驟 | 處置 |
|---|---|
| `point_in_time_data.run_scheduled`（盤中／衍生品，受 `pit_auto_ingestion_enabled` 控制） | 暫停 |
| `intraday_features.run(lookback_days=14)` | 暫停 |
| `earnings_calls.refresh("ALL")`（法說會） | 暫停 |
| `model_governance.refresh()`（台股與美股都跑，載入全量預測） | 暫停 |
| `shadow_trading.run_daily()` | 暫停 |
| `promotions.revalidate_all()` | 暫停 |
| `universe_expansion.run_batch()`、`paper_trading.process_pending()`、盤後 AI 產生與送模擬、報告與 Email | 保留 |

做法：
1. 新增設定 `PAUSED_MODULES`（或逐項布林），預設暫停上表六項；`execute` 依設定略過並在執行摘要記錄「已暫停」。不刪程式、不刪資料。
2. 查 `decision_support.py`、`after_hours_ai.py` 實際讀取哪些研究輸出（因子、樣本外回測、策略整合、組合風險）。不被每日決策讀取的步驟移到夜間或每週；被讀取的保留。先列證據再改。
3. 量測：用 `scheduler_job_runs` 各子工作起訖時間與 worker CPU，比較變更前（09/30 台股流程）與變更後（第一個交易日）的總耗時。
4. 研究頁的暫停模組說明與需求 §13 保持一致。

驗收：暫停項目不再出現在每日執行紀錄；盤後決策、模擬交易與報告照常；耗時與 CPU 下降有數字；全部測試通過；兩端驗收。

## 待辦提醒

- 2026-10-01 03:00：第一次排程備份，確認系統頁「最近執行」出現「資料庫備份 成功」、`instance\backups\daily` 有 2 份。
- 使用者確認 2026-10-01 13:50 台股流程正常後，提供刪除 `instance\backups\quant_platform-pre-s1w03-20260930-204109.db` 與 `quant_platform-pre-vacuum-20260930-204109.db`（各 22.29 GiB）的指令，由使用者執行。
- S1-W05 收盤資料時效實測要連續 5 個交易日；越早開始記錄越好。
- 2026-10-09 為國慶日補假，確認排程正確略過（交易日曆第一次實際遇到休市）。

## 環境現況（2026-09-30 23:55）

| 項目 | 狀態 |
|---|---|
| 監督程序 | 排程工作 `StockResearchLocalServices`（使用者登入時觸發），含 Tunnel；紀錄 `instance\supervisor.log` |
| 網站 | Waitress 127.0.0.1:5000；`AUTH_MODE=cloudflare-access`（本機 loopback 免登入）；CSS `v2.css?v=2.1.0` |
| Tunnel | `stockresearch-pimi-sunsun`（`e37bb649-…`）；日誌 `instance\tunnel.stderr.log` |
| Cloudflare | Access application `c49fc706-9f0e-4080-82ef-79f6ebfa5490`、規則 `ec4e2109-b2f2-491e-9ae9-229a3ef876ac`；AUD 在 `.env`；內建瀏覽器已登入 |
| 資料庫 | `instance\quant_platform.db` 9.62 GiB；每日備份 `instance\backups\daily\`（目前 1 份）；兩個 22.29 GiB 暫存備份待刪 |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>`；252 通過、1 略過 |
| 同主機其他服務 | VectorDB 5001 與其 Tunnel、PimiServices 共用 cloudflared 服務（YtSummary／AutoLayout）。一律不操作 |

## 待使用者確認

| 項目 | 建議 | 需要時點 |
|---|---|---|
| 開機自動恢復方式（需求 §14） | 維持登入時啟動（與 VectorDB 一致）；若要重開機後不登入也上線，需由使用者在工作排程器把 `StockResearchLocalServices` 改為「不論使用者是否登入都執行」並輸入 Windows 密碼 | S2 收尾，不阻擋開發 |
| AI 研究員的 LLM Provider 與每月預算 | Anthropic Claude 或既有 OpenAI 設定 | S4 開始前 |
| 通知管道 | Email 之外是否加 Telegram 或 LINE Messaging API | S1-W06／S6 |
| 券商手續費折扣與最低費用 | 用於成交模型 | S3-W03 |
| 定期定額基準 ETF | 預設 0050 | S3 |
