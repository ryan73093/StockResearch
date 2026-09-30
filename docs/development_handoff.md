# 盤後決策台 — 當前開發交接

記錄時間：2026-10-01 00:15（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則。部署一律 `scripts\deploy.ps1`；只停服務用 `scripts\stop-services.ps1`，只啟動用 `scripts\start-services.ps1`；還原資料庫步驟在「資料庫安全」。
2. `docs/development_roadmap.md`：S2 已完成；S1 剩 W05（量測中）、W06（待 10/01 量測）、W07。
3. `DEVELOPMENT_HISTORY.md` 最上方三筆：S1-W06／W05 量測、S2-W05 備份、S2-W03 第二版介面。

## 上一輪完成

- 介面第二版（電腦／iPad／手機、深色預設）、每日 03:00 備份與還原演練（S2 完成）。
- S1-W06：`PAUSED_MODULES` 預設暫停七項非核心步驟（盤中／衍生品、盤中特徵、法說會、模型治理、影子交易、晉級複驗、RAG 索引）。
- S1-W05：收盤資料時效量測程式上線，交易日 13:30–14:45 每分鐘記錄四個來源首次公布時間，系統頁「收盤資料時效」。

## 今天（2026-10-01）要確認的事

| 時間 | 事項 | 看哪裡 |
|---|---|---|
| 03:00 後 | 第一次排程備份成功，`instance\backups\daily` 有 2 份 | 系統頁「資料庫備份」「最近執行」 |
| 13:30–14:45 | 量測寫入四個來源（盤後零股約 14:30 後） | 系統頁「收盤資料時效」、`instance\close_availability.jsonl` |
| 13:50 後 | 第一次完整台股流程（S1-W02 修好品質閘門後）：今日頁在 14:30 前有盤後計畫；記錄各子工作耗時作為 S1-W06 量測，暫停步驟不應出現 | `scratchpad` 可用的查詢：`scheduler_job_runs` 依 `started_at` 排序 |
| 流程正常後 | 提供使用者刪除兩個 22.29 GiB 暫存備份的指令（使用者執行）：`instance\backups\quant_platform-pre-s1w03-20260930-204109.db`、`quant_platform-pre-vacuum-20260930-204109.db` | — |

## 當前工作包：S1-W07 市場共用特徵正規化

目標：總經與跨資產這類「全市場共用」特徵只存一份，不再複製到每一檔；主檔 < 3 GiB；總經步驟從約 13 分鐘降到秒級；特徵讀取結果與正規化前完全一致。

現況（已查證）：`application/macro_data.py` `MacroDataPipeline._materialize` 把 7 個 FRED 序列的每個觀測值寫給每一檔啟用中的個股與 ETF（約 540 檔），每次執行重寫 7,198,905 筆（`fred_macro_data` 中位數 787 秒，每天台股、美股流程各跑一次以上）。

盤點結果（2026-10-01 00:30 唯讀查詢，53 秒；依 `feature_name`、`feature_version`、`event_time` 分組比較各 symbol 的值與可用時間）：

| 類別 | 特徵 | 筆數 | 每檔是否相同 | 寫入位置 |
|---|---|---|---|---|
| 總經 | `treasury_10y`、`treasury_2y`、`yield_curve_10y2y`（各 228 萬）、`fed_funds_rate`、`unemployment_rate`、`cpi_yoy`、`gdp_yoy` | 7,198,905 | 完全相同（545 檔，含美股 ETF） | `MacroDataPipeline._materialize` |
| 跨資產 | `twii_return_20d` | 840,737 | 完全相同（534 檔台股） | `feature_engineering/cross_asset.py`（逐檔 as-of join） |
| 跨資產 | `vix_level`、`sp500_return_20d`、`sox_return_20d`、`gold_return_20d`、`brent_return_20d`、`usdtwd_return_20d` | 約 5,068,000 | 各有 1 個日期部分標的的值不同（可用時間相同）；其餘相同 | 同上 |
| 日曆 | `day_of_week` | 850,735 | 完全相同 | 技術特徵引擎 |

合計約 1,396 萬筆（全部 2,534 萬筆的 55%）。只靠這項大約能讓主檔從 9.6 GiB 降到 5–6 GiB；< 3 GiB 還要再處理預測（596 萬筆）與回測逐日點（`backtest_equity_points` 202 萬筆），列入後續評估。

設計要點（避免改變模型輸入）：
- 舊資料的覆蓋範圍不是「所有標的 × 所有日期」：總經只寫給寫入當時啟用中的個股／ETF；跨資產只寫在該檔有 K 線的日期。直接展開到所有要求的 symbol 會讓停牌、下市、上市前的日期多出列。
- 驗收改以「模型訓練矩陣與每日決策結果一致」為準：遷移前後對同一批標的建立 `ModelResearchPipeline._dataset` 的輸入矩陣與每日決策分數，逐格比對；原始列層級的差異（上述 6 個跨資產特徵各 1 個日期）要列出並說明。
- 讀取端全部經過 `SqlAlchemyFeatureLabelStoreRepository`（`list_features`、`list_recent_features`、`list_latest_features`、`feature_coverage`），再搜尋其他直接查 `feature_values` 的 SQL（資料品質、資料庫檢視頁）。

做法：
1. 儲存改為一份：總經以保留代號（例如 `@GLOBAL`）、跨資產與 `day_of_week` 以市場代號（`@TW`）寫入；讀取時依「該 symbol 在該日期有基礎特徵列」展開，重現原本的覆蓋範圍。
2. 寫入端：總經只寫新的觀測值（不再每次重寫）；跨資產每個交易日只算一次。
3. 一致性驗證（見上）＋遷移腳本（停服務、備份、刪除複製列、VACUUM INTO、quick_check、筆數報告），沿用 `scripts/slim_database.py` 的安全步驟。
4. 部署時點：台股流程結束後（14:40 以後），並在 10/01 第一次完整台股流程觀察完之後；不要在 13:30–14:40 停服務。

驗收：總經步驟秒級；模型訓練矩陣與每日決策逐格一致（差異列出說明）；主檔縮小量有數字；全部測試通過；兩端驗收。

## 環境現況（2026-10-01 00:15）

| 項目 | 狀態 |
|---|---|
| 監督程序 | 排程工作 `StockResearchLocalServices`（使用者登入時觸發），含 Tunnel；紀錄 `instance\supervisor.log` |
| 網站 | Waitress 127.0.0.1:5000；`AUTH_MODE=cloudflare-access`（本機 loopback 免登入）；CSS `v2.css?v=2.1.0` |
| Tunnel | `stockresearch-pimi-sunsun`（`e37bb649-…`）；日誌 `instance\tunnel.stderr.log` |
| Cloudflare | Access application `c49fc706-9f0e-4080-82ef-79f6ebfa5490`、規則 `ec4e2109-b2f2-491e-9ae9-229a3ef876ac`；AUD 在 `.env`；內建瀏覽器已登入 |
| 資料庫 | `instance\quant_platform.db` 9.62 GiB；每日備份 `instance\backups\daily\`（目前 1 份）；兩個 22.29 GiB 暫存備份待刪 |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>`；258 通過、1 略過 |
| 同主機其他服務 | VectorDB 5001 與其 Tunnel、PimiServices 共用 cloudflared 服務（YtSummary／AutoLayout）。一律不操作 |

## 待使用者確認

| 項目 | 建議 | 需要時點 |
|---|---|---|
| 開機自動恢復方式（需求 §14） | 維持登入時啟動（與 VectorDB 一致）；若要重開機後不登入也上線，需由使用者在工作排程器把 `StockResearchLocalServices` 改為「不論使用者是否登入都執行」並輸入 Windows 密碼 | 不阻擋開發 |
| AI 研究員的 LLM Provider 與每月預算 | Anthropic Claude 或既有 OpenAI 設定 | S4 開始前 |
| 通知管道 | Email 之外是否加 Telegram 或 LINE Messaging API | S6 |
| 券商手續費折扣與最低費用 | 用於成交模型 | S3-W03 |
| 定期定額基準 ETF | 預設 0050 | S3 |
