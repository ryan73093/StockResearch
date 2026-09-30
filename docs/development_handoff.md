# 盤後決策台 — 當前開發交接

記錄時間：2026-10-01 00:40（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則。部署一律 `scripts\deploy.ps1`；只停服務用 `scripts\stop-services.ps1`，只啟動用 `scripts\start-services.ps1`；還原資料庫步驟在「資料庫安全」。
2. `docs/development_roadmap.md`：S2 完成；S1 剩 W05（量測中，到 10/07）、W06（待 10/01 量測）；S3 開始。
3. `DEVELOPMENT_HISTORY.md` 最上方三筆：S1-W07 總經增量、S1-W06／W05、S2-W05 備份。

## 上一輪完成

- S1-W07：總經特徵改為增量寫入，`fred_macro_data` 787 秒 → 11.6 秒；正式庫逐點核對與全量重寫一致。
- 使用者決定：開機自動恢復維持「登入 Windows 時啟動」。

## 今天（2026-10-01）要確認的事

| 時間 | 事項 | 看哪裡 |
|---|---|---|
| 03:00 後 | 第一次排程備份成功，`instance\backups\daily` 有 2 份 | 系統頁「資料庫備份」「最近執行」 |
| 06:30 後 | 美股流程的 `fred_macro_data` 在秒級完成（S1-W07 第一次排程執行） | `scheduler_job_runs` |
| 13:30–14:45 | 量測寫入四個來源（盤後零股約 14:30 後） | 系統頁「收盤資料時效」、`instance\close_availability.jsonl` |
| 13:50 後 | 第一次完整台股流程（09-08 以來第一次）：今日頁在 14:30 前有盤後計畫；記錄各子工作耗時作為 S1-W06 量測，暫停步驟不應出現。依 09-08 以前紀錄，因子研究約 8 分鐘、樣本外回測約 59 分鐘，整條流程預計 15:40 前後結束 | `scheduler_job_runs` 依 `started_at` 排序 |
| 流程正常後 | 提供使用者刪除兩個 22.29 GiB 暫存備份的指令（使用者執行）：`instance\backups\quant_platform-pre-s1w03-20260930-204109.db`、`quant_platform-pre-vacuum-20260930-204109.db` | — |

## 當前工作包：S3-W01 長歷史行情

目標：研究用的長歷史日線（2003／2004 年起），證交所／櫃買官方為主、Yahoo 交叉核對，涵蓋 2008、2011、2015、2020、2022 下跌；存成可重現的研究資料集，不影響每日流程。

已查證的官方來源（2026-10-01 00:30 各 1 次請求）：

| 資料 | 端點 | 可查起日 | 備註 |
|---|---|---|---|
| 個股／ETF 月資料 | 證交所 `rwd/zh/afterTrading/STOCK_DAY?date=YYYYMM01&stockNo=` | 2010-01-04 | 更早日期回「查詢日期小於99年1月4日」 |
| ETF 每日收盤（全部 ETF） | 證交所 `rwd/zh/afterTrading/MI_INDEX?date=YYYYMMDD&type=0099P` | 2004-02-11 | 回應約 0.9 KB；補 2004–2009 |
| 加權指數月資料 | 證交所 `rwd/zh/TAIEX/MI_5MINS_HIST?date=YYYYMM01` | 2003 年起 | 開高低收 |
| 報酬指數月資料 | 證交所 `rwd/zh/TAIEX/MFI94U?date=YYYYMM01` | 2003 年起 | 發行量加權股價報酬指數 |
| 上櫃 ETF 月資料 | 櫃買 `www/zh-tw/afterTrading/tradingStock?code=&date=YYYY/MM/01` | 待測 | 舊版 `st43_result.php` 已無回應 |
| Yahoo | `v8/finance/chart/0050.TW` | 2003 年無資料 | 只作交叉核對；價格已做分割調整 |

已知限制：0050 上市日 2003-06-30 到 2004-02-10 的日線官方查不到（每日收盤表從 2004-02-11 起），研究期間從 2004-02-11 起算；報酬指數可涵蓋 2003 年。0050 在 2025-06 一拆四，官方價格未調整、Yahoo 已調整，交叉核對與總報酬序列（S3-W02）都要處理分割。

做法：
1. `quant_platform/research/history/`：官方用戶端（每次請求間隔 ≥ 3 秒、重試與退避、被拒時停止並說明）、原始回應快取（`instance/research/history/raw/`，可中斷續抓）、每個序列一個 Parquet（`instance/research/history/daily/<序列>.parquet`）與 `manifest.json`（筆數、起訖、來源、SHA-256）。
2. 序列清單：0050、006208、0056、00878、00713、00919（上市）、00679B、00687B（上櫃）、加權指數、報酬指數。交易日以加權指數月資料為準；2004–2009 的 ETF 以每日收盤表補。
3. 品質檢查：重複日期、與交易日比對的缺漏、非正價格、開高低收不一致；交叉核對 Yahoo（考慮分割），輸出差異報告。
4. CLI：`python -m quant_platform.research.history fetch|status|crosscheck`；抓取在背景執行（估計約 2.5 小時），不經過 worker、不寫 SQLite。

驗收：各序列起訖與筆數符合上市日與交易日；涵蓋指定下跌期間；品質檢查無未說明的問題；Yahoo 差異報告；重跑不重抓（快取）且 Parquet 雜湊不變。

## 環境現況（2026-10-01 00:40）

| 項目 | 狀態 |
|---|---|
| 監督程序 | 排程工作 `StockResearchLocalServices`（使用者登入時觸發），含 Tunnel；紀錄 `instance\supervisor.log` |
| 網站 | Waitress 127.0.0.1:5000；`AUTH_MODE=cloudflare-access`（本機 loopback 免登入）；CSS `v2.css?v=2.1.0` |
| Tunnel | `stockresearch-pimi-sunsun`（`e37bb649-…`）；日誌 `instance\tunnel.stderr.log` |
| Cloudflare | Access application `c49fc706-9f0e-4080-82ef-79f6ebfa5490`、規則 `ec4e2109-b2f2-491e-9ae9-229a3ef876ac`；AUD 在 `.env`；內建瀏覽器已登入 |
| 資料庫 | `instance\quant_platform.db` 9.62 GiB；每日備份 `instance\backups\daily\`（目前 1 份）；兩個 22.29 GiB 暫存備份待刪 |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>`；260 通過、1 略過 |
| 同主機其他服務 | VectorDB 5001 與其 Tunnel、PimiServices 共用 cloudflared 服務（YtSummary／AutoLayout）。一律不操作 |

## 待使用者確認

| 項目 | 建議 | 需要時點 |
|---|---|---|
| AI 研究員的 LLM Provider 與每月預算 | Anthropic Claude 或既有 OpenAI 設定 | S4 開始前 |
| 通知管道 | Email 之外是否加 Telegram 或 LINE Messaging API | S6 |
| 券商手續費折扣與最低費用 | 用於成交模型 | S3-W03 |
| 定期定額基準 ETF | 預設 0050 | S3 |
