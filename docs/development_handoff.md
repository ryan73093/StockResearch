# 盤後決策台 — 當前開發交接

記錄時間：2026-10-01 01:00（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則。部署一律 `scripts\deploy.ps1`；只停服務用 `scripts\stop-services.ps1`，只啟動用 `scripts\start-services.ps1`；還原資料庫步驟在「資料庫安全」。
2. `docs/development_roadmap.md`：S1 剩 W05（量測到 10/07）、W06（待 10/01 量測）；S3、S4-W01～W03、S5-W01 進行中。
3. `DEVELOPMENT_HISTORY.md` 最上方：S5-W02～W04（今日建議、實際帳戶、影子帳戶）、S5-W01／S4-W01～W03、S1-W07。研究模組說明在 `docs/system_architecture.md` 目標程式結構 `research/` 一列與決策 D12。
4. 使用者要求：所有回覆與進度說明用繁體中文。

## 當前工作包：S3 長歷史資料驗收與第一批研究

程式已完成並有測試（研究相關約 60 項）；正在等官方資料下載完成。

**背景下載**：2026-10-01 00:17 啟動的獨立程序（PID 記在 `instance\research\history\fetch.pid`，命令 `python -m quant_platform.research.history fetch`，以 `PYTHONPATH=src` 從原始碼執行，不經過服務）。紀錄 `instance\research\history\fetch.log`、錯誤 `fetch.err.log`；原始回應快取 `instance\research\history\raw\`。約 3,000 次請求、每次間隔 ≥ 3 秒，預計 03:30～04:00 完成。中斷時重新執行同一命令即可續抓（過去月份不重抓）。**不要在 13:30–14:40 執行抓取。**

**2026-10-01 01:23 已啟動無人值守接續**：`scripts/research_first_run.py`（PID 在 `instance\research\first_run.pid`，紀錄 `instance\research\first_run.log`）會等下載程序結束，自動跑完下列 1～6 步（13:20–14:45 暫停網路步驟）。接手時先看紀錄；若中斷，可手動依序執行（都在專案根目錄、`$env:PYTHONPATH="src"`）：
1. `python -m quant_platform.research.history status`：各序列筆數、起訖、缺交易日、分割註記、下跌期涵蓋。預期 0050 從 2004-02-11 起、2025-06 有分割註記。
2. `python -m quant_platform.research.history actions`：證交所 TWT49U 與櫃買 exDailyQ 除權息（約 50 次請求）、總報酬序列、Yahoo 股利核對。
3. `python -m quant_platform.research.history crosscheck`：官方收盤 vs Yahoo（分割調整後）與總報酬 vs Yahoo 還原收盤價的漂移。
4. `python -m quant_platform.research.history oddlot --every 10`：盤後零股成交價相對收盤的分布（約 560 次請求），用來確認預設滑價 10 bps 是否合理（`research/costs.py`）。
5. `python -m quant_platform.research baselines --period full`：4 個基準對定期定額，研究頁「定期定額對照」會顯示。
6. `python -m quant_platform.research batch --name first --period development`：68 個候選試驗（只用開發期），接著 `python -m quant_platform.research stats --period development` 算 DSR、PBO、bootstrap。
7. 驗收紀錄寫入開發歷程；研究頁加「試驗排行」（含總試驗數與檢定結果，S6-W04 的一部分）；結果誠實呈現，沒有通過檢定就說沒有。

之後：通過開發期門檻的少數候選進驗證期（`trial --period validation`），驗證通過才可用一次保留期。晉級門檻見路線圖 S4。

## 今天（2026-10-01）要確認的事

| 時間 | 事項 | 看哪裡 |
|---|---|---|
| 03:00 後 | 第一次排程備份成功，`instance\backups\daily` 有 2 份 | 系統頁「資料庫備份」「最近執行」 |
| 06:30 後 | 美股流程的 `fred_macro_data` 在秒級完成（S1-W07 第一次排程執行） | `scheduler_job_runs` |
| 13:30–14:45 | 收盤時效量測寫入四個來源 | 系統頁「收盤資料時效」、`instance\close_availability.jsonl` |
| 13:50 後 | 第一次完整台股流程（09-08 以來第一次）：今日頁 14:30 前有盤後計畫；記錄各子工作耗時（S1-W06 量測），暫停步驟不應出現；因子研究約 8 分鐘、樣本外回測約 59 分鐘，預計 15:40 前後結束 | `scheduler_job_runs` |
| 流程正常後 | 提供使用者刪除兩個 22.29 GiB 暫存備份的指令（使用者執行）：`instance\backups\quant_platform-pre-s1w03-20260930-204109.db`、`quant_platform-pre-vacuum-20260930-204109.db` | — |

## 環境現況（2026-10-01 01:00）

| 項目 | 狀態 |
|---|---|
| 監督程序 | 排程工作 `StockResearchLocalServices`（使用者登入時觸發），含 Tunnel；紀錄 `instance\supervisor.log` |
| 網站 | Waitress 127.0.0.1:5000；`AUTH_MODE=cloudflare-access`；CSS `v2.css?v=2.3.0`；01:22 部署（計畫、實際帳戶、依計畫的今日建議、研究排行） |
| Cloudflare | Access application `c49fc706-9f0e-4080-82ef-79f6ebfa5490`、規則 `ec4e2109-b2f2-491e-9ae9-229a3ef876ac`；內建瀏覽器已登入 |
| 資料庫 | `instance\quant_platform.db` 9.62 GiB；新增空資料表 `investment_plans`、`actual_cash_flows`、`actual_trades`；每日備份 `instance\backups\daily\`（1 份）；兩個 22.29 GiB 暫存備份待刪 |
| 研究資料 | `instance\research\`：history（下載中）、reports、trials.jsonl（尚無試驗） |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>`；315 通過、1 略過 |
| 同主機其他服務 | VectorDB 5001 與其 Tunnel、PimiServices 共用 cloudflared 服務（YtSummary／AutoLayout）。一律不操作 |

## 待使用者確認

| 項目 | 建議 | 需要時點 |
|---|---|---|
| AI 研究員的 LLM Provider 與每月預算 | Anthropic Claude 或既有 OpenAI 設定 | S4-W04（目前 blocked） |
| 券商與手續費折扣、最低手續費、零股最低費用 | 研究暫用不打折、最低 20 元 | S3-W03、S5-W03 |
| 實際帳戶的成交紀錄方式 | 手動回報或券商對帳單 CSV（需知道券商與格式） | S5-W03 |
| 通知管道 | Email 之外是否加 Telegram 或 LINE Messaging API | S6 |
| 定期定額基準 ETF | 預設 0050 | S3 |
