# 盤後決策台 — 開發歷程

每輪交付一筆，最新在最上方。記錄目標、做法、測試結果、證據、commit 與回復方式。v3.9 以前的研究平台版本紀錄見 [`docs/archive/module-status-v2.7-v3.9.md`](docs/archive/module-status-v2.7-v3.9.md)。

## 2026-10-01 — S1-W07 總經特徵增量寫入

- 使用者決定（2026-10-01）：開機自動恢復維持「登入 Windows 時啟動」（需求 §10）。
- 盤點（唯讀，53 秒）：`feature_values` 2,534 萬筆中約 1,396 萬筆每檔相同——總經 7 項 7,198,905 筆、`twii_return_20d` 84 萬、`day_of_week` 85 萬完全相同；另 6 個跨資產特徵各有 1 個日期部分標的的值不同。`fred_macro_data` 每次把 7 個序列全部重寫給每一檔啟用中的個股與 ETF（中位數 787 秒，最長 1,638 秒）。另查到 `factor_research` 以 `list_features(symbols)` 一次載入台股全部特徵（09-08 以前完整流程約 8 分鐘可完成）。
- 做法：`MacroDataPipeline._materialize` 改為增量——以覆蓋筆數與最後日期最常見的標的為參考，比較其已存序列與由最新修訂算出的序列，只寫新增或修訂的觀測點；覆蓋不同（新標的、不完整）的標的寫完整序列；全部在同一次 upsert（單一交易）完成。儲存內容與全量重寫相同，只有未變動列的 `computed_at` 保留舊值；讀取端完全不變。新增 `feature_series_coverage()`（每檔每特徵筆數與最後日期，正式庫 6.5 秒）。執行結果新增 `changed_points`、`rebuilt_symbols`。
- 驗證：
  - 測試：首次全寫、無變動 0 筆、新月份只寫 7 點、CPI 基期修訂只改 1 個年增率點＋公債 1 點、新標的補完整序列，每一步都與全量重寫的結果集合相同。
  - 正式庫唯讀乾跑（部署前）：5.4 秒，應寫 0 筆（已存序列與計算結果逐點相同）。
  - 部署（00:17）後手動執行一次（等同排程步驟）：11.6 秒，下載 13,225 筆，新增 22 個修訂、寫入 11,946 筆（22 點 × 543 檔），`rebuilt_symbols` 0。
  - 正式庫唯讀核對：543 檔啟用中個股／ETF 在 13,209 個觀測點上筆數、數值、可用時間完全一致，與計算序列 0 差異、0 缺漏。
- 共用特徵只存一份（縮小主檔）需要改讀取端並以模型訓練矩陣驗證，移到 S3-W06 與特徵快照 Parquet 化一起做。
- 測試：`test_macro_data.py` 新增 2 項；全部 260 通過、1 略過。
- 回復：`git revert` 後以 `scripts\deploy.ps1` 部署即恢復全量重寫；資料不需轉換。

## 2026-10-01 — S1-W06 暫停非核心收集，S1-W05 收盤時效量測上線

- 盤點每日台股流程（`AutomationService.execute`）：研究流程之後依序執行盤中／衍生品收集、盤中特徵、法說會、模型治理 `refresh()`（台股、美股都跑，載入全量預測）、模擬交易處理、影子交易、晉級複驗、盤後 AI、報告；報告 `generate()` 每次都同步 RAG 文件與向量索引。每日決策（`DailyDecisionPipeline`）與盤後 AI 只讀取模型、特徵、市場狀態、策略整合、組合研究與各研究結果的 `promotion_gate`，不讀取上述暫停模組。
- 設定 `PAUSED_MODULES`（`config/settings.py`；未設定＝七項全部暫停，`none`＝全部恢復，未知名稱拒絕啟動）：`point_in_time`、`intraday_features`、`earnings_calls`、`model_governance`、`shadow_trading`、`promotions`、`rag_index`。報告本文與 Email 照常，只略過 RAG 索引（`generate(..., index=False)`）。系統頁「每日排程」列出暫停清單。
- 需求 §13 補充：新聞資料集仍隨籌碼資料收集（`news_sentiment_daily` 是現行綜合模型候選特徵），九種組合配置仍在每日流程（現行決策讀取其結果），分別在 S4、S5 處理。
- 量測基準：09/21–09/30 的台股流程都在原始資料品質閘門停止（S1-W02 處理的下市個股），因此暫停步驟這段期間實際上沒有執行，沒有變更前的完整流程可比較；10/01 13:50 會是第一次完整台股流程，以它記錄耗時。現有數字（09/20 起中位數）：台股行情 293 秒、特徵 200 秒、模型 111 秒、決策 44 秒；總經 `fred_macro_data` 787 秒（最長 1,638 秒），每次重寫 7,198,905 筆特徵——列為 S1-W07 的主要對象。
- S1-W05 量測：`application/close_availability.py` `CloseAvailabilityProbe`，worker 工作 `close_availability_probe`（週一至週五 13–14 時每分鐘觸發，程式只在交易日 13:30–14:45 動作）。每個來源當天第一次看到資料就寫一筆到 `instance/close_availability.jsonl`，之後不再查詢該來源：證交所 MI_INDEX 收盤表、櫃買 OpenAPI 收盤（日期＝當日）、Yahoo 0050 日 K 收盤與官方一致、證交所盤後零股 TWT53U（當日）。系統頁新增「收盤資料時效」（最近 5 個交易日，13:30 後幾分鐘）。以 09/30 資料實測四個解析器：證交所 1,382 筆（0050 收盤 112.05）、櫃買 11,772 筆、Yahoo 112.05 與官方一致、盤後零股 1,368 筆。
- 測試：`test_automation.py` 新增預設暫停 1 項、設定解析 1 項（原「全部執行」測試改為 `paused_modules=frozenset()`）；新增 `test_close_availability.py` 4 項；排程清單加 `close_availability_probe`。全部測試 258 通過、1 略過。
- 部署：2026-09-30 23:55（S1-W06）、10-01 00:00（S1-W05 量測）；本機與外網系統頁都顯示暫停清單、13:30 量測排程與「收盤資料時效」卡。
- 回復：`.env` 設 `PAUSED_MODULES=none` 後重啟即恢復全部步驟；量測工作可在 `_add_maintenance_jobs` 移除，紀錄檔可保留。

## 2026-09-30 — S2-W05 每日備份與還原演練

- `application/database_backup.py` `DatabaseBackupService`：SQLite backup API 單一步驟（`pages=-1`）取得一致快照，其他連線照常寫入 WAL；副本先寫 `.partial`，改為 `journal_mode=DELETE` 成為獨立檔，`quick_check` 通過後記錄全部 50 個資料表筆數，改名並寫入 `instance/backups/daily/manifest.jsonl`；保留最新 7 份（只輪替本目錄 `quant_platform-*.db`）；寫入 `database_backup`／`SYSTEM` 執行紀錄；超過 26 小時無新備份視為逾期。
- 排程：worker 新增 `database_backup` 工作（每日 03:00，錯過 2 小時內補跑，不需每日流程的鎖）；`_add_calendar_refresh_job` 改名 `_add_maintenance_jobs`（02:30 預測封存、03:00 備份、每小時日曆檢查）。
- `scripts/database_backup.py`：`status`、`run`（手動備份）、`drill`（還原到 `instance/backups/drill/`、唯讀開啟、quick_check、逐表筆數與 manifest 比對、報告 JSON，預設刪除還原出的副本；不動正式檔）。還原正式檔的步驟寫在 `AGENTS.md`「資料庫安全」。
- 系統頁：新增「資料庫備份」狀態（最近時間、大小、份數）與「每日排程」表（02:30 封存、03:00 備份、06:30 美股、13:50 台股休市日略過，及背景工作說明）；「最近排程」改名「最近執行」。
- 實測（23:41 部署後）：
  - 手動備份 `quant_platform-20260930-234151.db`：9.62 GiB，總計 141.5 秒（複製約 22 秒，其餘為 quick_check 與筆數），quick_check ok，50 表。
  - 還原演練 `drill-20260930-234642.json`：複製 4.6 秒、quick_check ok、50 表筆數與 manifest 一致、台股最新行情 2026-09-30，passed。
  - 兩端：本機 `http://127.0.0.1:5000/system` 與外網（已登入，23:46）都顯示「資料庫備份 正常｜最近 09/30 23:41・9.6 GiB・共 1 份（上限 7）」與每日排程表。
- 開機自動恢復：排程工作 `StockResearchLocalServices` 在使用者登入時觸發（與 VectorDB 的 `PimiServices-VectorDB-User` 相同）；重開機後登入即自動恢復 web、api、worker、Tunnel。重開機後、登入前網站離線；是否改為開機觸發或自動登入屬系統設定，列入需求 §14 待使用者決定，未變更。
- 測試：新增 `test_database_backup.py` 8 項（獨立副本與筆數、持有未提交交易時的快照、輪替、逾期、失敗紀錄、演練通過與筆數不符、系統頁狀態）；排程工作清單加 `database_backup`。
- 回復：停用備份只需移除排程工作的 `database_backup` 註冊並重新部署；備份目錄可整個保留。

## 2026-09-30 — S2-W03 第二版：電腦／iPad／手機版面與深色主題

- 使用者要求（2026-09-30）：介面電腦與 iPad 也會用，要有暗色主題。
- 版面：同一套 HTML 三種版面——電腦（≥1200 px）完整側邊欄（品牌、五項導覽、台股時段、主題切換、登入 email）；iPad（768–1199 px）84 px 圖示側欄；手機（<768 px）頂端列（品牌、時段、主題）＋底部五分頁。今日頁在 ≥1100 px 分主欄（委託單、觀察清單）與側欄（為什麼、資料狀態、交易守則）；單欄時以 `order` 依重要性交錯（委託 → 為什麼 → 觀察 → 資料狀態 → 守則）。手機表格改逐筆卡片（`data-label`）。
- 主題：深色為預設；`sr_theme` cookie（dark／light，一年、SameSite=Lax、https 加 Secure）由伺服器直接輸出 `<html data-theme>`，不會閃爍；非法值一律深色。Mermaid 圖隨主題重新渲染。台股慣例紅漲綠跌、買紅賣綠（`--up`／`--down`）。
- 內容：今日行動卡加委託筆數、預估金額、模擬權益、可用現金；持倉加權重條；研究頁 AI 研究員說明與工具分組兩欄，暫停模組收合；系統頁狀態磚（手機兩欄）與文件直欄分頁（≥1024 px）。
- 導覽的台股時段：盤前、盤中、已收盤（13:30–13:40）、盤後零股進行中（13:40–14:30）、今日已收盤、休市（附原因與下一交易日），依官方交易日曆。
- 驗收（內建瀏覽器，5055 預覽＋部署後 5000 與外網）：375、820、1180、1440 px 各頁整頁水平溢出 0；深淺色切換後重新整理仍維持；外網登入後側欄顯示擁有者 email、CSS `v2.css?v=2.1.0`。
- 測試：`test_dashboard_v2.py` 改為檢查側欄與底部分頁各一份導覽、`aria-current`；新增主題 cookie 1 項、台股時段 7 項。全部測試 252 通過、1 略過。
- 回復：`git revert` 本 commit 後以 `scripts\deploy.ps1` 部署。

## 2026-09-30 — S2-W02 Cloudflare 上線

- 使用者回饋（2026-09-30）：其他專案由 AI 自行完成 Cloudflare 設定，不應要求使用者手動複製 AUD。查證 `C:\ProgramData\PimiServices\Important System Log\2026-09-10 AutoLayout Cloudflare Deployment.md`：當時由 AI 在 Cloudflare 後台建立 Access application。改為由開發者在內建瀏覽器操作；使用者只完成 Cloudflare 登入（登入由本人操作，登入狀態保存在內建瀏覽器）。
- Cloudflare Zero Trust（帳號 `c55301fc…`、團隊網域 `raspy-mode-cc1c`）：
  - 新增規則 `StockResearch owner only`（id `ec4e2109-b2f2-491e-9ae9-229a3ef876ac`）：Allow；Include Emails `ryan73093@gmail.com`；Require Login Methods Google。沒有沿用 `AutoLayout Google users`（Include Everyone）與 `Ryan and Eva only`，避免放寬到其他人。
  - 新增 self-hosted application `StockResearch`（id `c49fc706-9f0e-4080-82ef-79f6ebfa5490`）：目的地 `stockresearch.pimi-sunsun.com`、只接受 Google、instant authentication、工作階段 24 小時。既有三個應用程式未變更。
- `.env` 加入 `AUTH_MODE=cloudflare-access`、`PUBLIC_URL`、`ACCESS_TEAM_DOMAIN`、`ACCESS_AUD`（64 位 AUD tag）、`ACCESS_ALLOWED_EMAILS`；未輸出 `.env` 其他內容。
- 重啟（21:39）：`supervisor.log` 顯示 "Supervisor started with tunnel"；Tunnel PID 296120 註冊 4 條 QUIC 連線（khh01×2、tpe01×2）。VectorDB Tunnel（PID 10704）與 PimiServices 共用服務（PID 5508）未變更。
- 驗收：
  - 本機 `http://127.0.0.1:5000/` 200，顯示新版今日頁（loopback 例外）。
  - 外網未登入：`/`、`/holdings`、`/system/docs/roadmap` 皆 302 到 `raspy-mode-cc1c.cloudflareaccess.com/cdn-cgi/access/login/stockresearch.pimi-sunsun.com`（kid 與 AUD 一致）；帶偽造 `Cf-Access-Jwt-Assertion` 仍 302。
  - 外網登入後（內建瀏覽器，21:41）：`https://stockresearch.pimi-sunsun.com/` 顯示今日頁（委託 2 筆、資料 09/30 13:30）；系統頁「外網發布：Cloudflare Access」、專案資訊文件可讀。
- 回復：`.env` 的 `AUTH_MODE` 改回 `development` 並重啟（Tunnel 不會啟動）；需要撤下時在 Zero Trust 刪除 `StockResearch` application 與規則、刪除 DNS 記錄、`cloudflared tunnel delete stockresearch-pimi-sunsun`。

## 2026-09-30 — S2-W01～W04 Cloudflare 準備與新介面第一版

- 使用者決定（2026-09-30）：S2 提前到 S1-W05／W06 之前；同意由開發者以本機 cloudflared 建立 Tunnel 與 DNS；Access application 由使用者在 Zero Trust 建立並提供 AUD。
- S2-W02 Tunnel：`cloudflared tunnel create --credentials-file .runtime\cloudflared\stockresearch-pimi-sunsun.json stockresearch-pimi-sunsun`（id `e37bb649-0237-434f-b5c7-833722d87f9d`；憑證不放共用 `.cloudflared`，避免影響 YtSummary 啟動腳本）；`tunnel route dns` 新增 CNAME `stockresearch.pimi-sunsun.com`。團隊網域沿用 VectorDB 設定 `https://raspy-mode-cc1c.cloudflareaccess.com`。
- S2-W01 Access 驗證：`dashboard/cloudflare_access.py`（沿用 VectorDB 模式）：RS256、audience、issuer、exp、email 驗證；`ACCESS_ALLOWED_EMAILS` 允許清單；直接連 127.0.0.1／localhost 的 loopback 請求免登入；經 Tunnel 的請求雖來自 127.0.0.1，但 Host 是公開網址，因此必須有 JWT；設定不完整一律 503；跨來源寫入 403；安全標頭。`AUTH_MODE=cloudflare-access` 時舊 Google OAuth 寫入檢查略過。新增設定 `AUTH_MODE`、`PUBLIC_URL`、`ACCESS_TEAM_DOMAIN`、`ACCESS_AUD`、`ACCESS_ALLOWED_EMAILS`；依賴 `PyJWT[crypto]`。
- Tunnel 監督：`run_local_services.ps1` 在 `.env` 為 `AUTH_MODE=cloudflare-access` 且憑證存在時才把 cloudflared 納入監督（開發模式永遠不公開）；`stop-services.ps1` 以本專案憑證路徑辨識 cloudflared，不碰 VectorDB、YtSummary 的 Tunnel。
- S2-W03／W04 新介面：`dashboard/v2.py` blueprint＋`templates/v2/`＋`static/css/v2.css`（淺色、手機優先、深色模式跟隨系統；桌面頂部導覽、手機底部 5 分頁）。
  - 今日 `/`：行動卡（交易／不需操作／回看三種顏色、14:30 倒數）、委託單與「複製委託」、每筆理由只取「訊號」「風險」、觀察清單收合、資料時間與品質狀態一行。
  - 持倉 `/holdings`：模擬帳戶權益、現金、報酬、曝險、持股與最近委託。
  - 計畫 `/plan`：S5 前的空狀態。
  - 研究 `/research`：舊版工具分組入口，暫停模組標示「S8 決定去留」。
  - 系統 `/system`：服務、台美股行情時效、資料品質（含排除檔數）、交易日曆、外網發布狀態、最近排程；專案資訊分頁（路線圖、架構、需求、交接、開發歷程）即時讀 repo 原始檔，Mermaid 圖在瀏覽器渲染；文件讀取限白名單。
  - 舊市場總覽移到 `/market`；舊側邊欄加「回到新版介面」。
- 驗收（內建瀏覽器，375 px）：修正前整頁被表格撐寬（grid 子元素 min-width），改為 `minmax(0, 1fr)` 與表格區塊內捲動後無水平溢出；系統頁架構分頁 6 張圖渲染成功。
- 部署事故：一次在服務執行中直接 `pip install .`，安裝被鎖定的執行檔中斷，套件被移除一半，服務反覆重啟約 4 分鐘（21:17–21:21）。以停止 → 重新安裝 → 啟動恢復，新增 `scripts/deploy.ps1` 固定此順序並寫入 `AGENTS.md`。`site-packages` 內留有 pip 中斷產生的 `~uant-research-platform` 殘留目錄（只造成警告），未手動刪除。
- 測試：新增 `test_cloudflare_access.py` 11 項、`test_dashboard_v2.py` 8 項；`test_dashboard.py` 首頁改測 `/market`。全部測試 236 通過、1 略過。
- 待辦：使用者建立 Access application 並提供 AUD → 寫入 `.env` → 重啟（Tunnel 隨之啟動）→ 本機與 Cloudflare 兩端驗收。

## 2026-09-30 — S1-W03 資料庫瘦身，以及停牌個股規則

- 使用者決定（2026-09-30）：今晚執行瘦身；停牌個股改為只排除該檔。
- 盤點（唯讀）：`model_predictions` 2,530 萬筆／233 個實驗，每次重跑都把全部歷史樣本外預測再存一份（每個實驗約 66 萬筆）；`feature_values` 2,533 萬筆幾乎全是 1.0.0 版，體積主要來自總經與跨資產特徵被複製到每一檔（例如 `treasury_10y` 228 萬筆）。
- 索引：兩表的唯一索引已涵蓋所有查詢的開頭欄位；移除 11 個未使用單欄索引（預測 7、特徵 4），並同步修改 `database/models.py`。
- 保留政策 `application/prediction_archive.py`：資料庫只留使用中的實驗（每組最新、每組最新 rank IC 為正、每市場／標籤最佳 CANDIDATE、未淘汰的模型登錄）；其餘逐實驗寫 Parquet（zstd），讀回核對筆數、記錄 SHA-256 到 `manifest.jsonl` 後才刪除。排程每天 02:30 自動執行（`prediction_archive` 工作，使用每日流程同一把鎖）。
- 一次性執行 `scripts/slim_database.py`（20:40–21:16，服務停機約 36 分鐘）：
  - 完整備份 `instance/backups/quant_platform-pre-s1w03-20260930-204109.db`（22.29 GiB，68 秒，quick_check ok）。
  - 封存 217 個實驗、19,340,138 筆（670 秒），Parquet 共 247 MB；資料庫保留 16 個實驗、5,959,558 筆（與原總數 25,299,696 相符）。
  - `VACUUM INTO` 238 秒；新檔 quick_check ok、所有資料表筆數與壓縮前一致。
  - 主檔 **22.29 GiB → 9.62 GiB**。腳本最後改名時因自身連線未關閉（Python sqlite3 的 `with` 不會關閉連線）失敗；驗證已通過，手動完成改名並補寫 `slim-report-20260930-204109.json`；腳本已改用 `contextlib.closing`。
  - 暫存：`instance/backups/` 內兩個 22.29 GiB 檔（變更前備份、壓縮前檔）。隔日台股流程正常後可刪除，刪除指令交由使用者執行。
  - 3 GiB 目標未達：剩餘體積主要是特徵表（資料 3.06 GiB＋索引），需把總經／跨資產特徵改為每市場只存一份，列為新工作包。
- 停牌規則（資料品質 v2、決策 1.1.0）：個股落後超過 3 個交易日或近期缺漏達 25% 改為警告並排除（`excludes_asset`），不阻擋全市場；同時排除超過 5 檔或 2% 時視為來源問題並阻擋。每日決策以特徵日期與交易日曆計算落後交易日數，超過 3 日者標「資料不足」、不列候選。
- 測試：新增 `test_prediction_archive.py` 3 項、`test_decision_exclusion.py` 1 項、`test_data_quality.py` 停牌 2 項；排程工作清單加 `prediction_archive`。
- 回復：`stop-services.ps1` → 以 `quant_platform-pre-s1w03-*.db` 替換主檔 → 啟動；封存的預測可由 Parquet 依 `manifest.jsonl` 回灌。

## 2026-09-30 — S1-W02 下市個股處理

- 起點：台股原始品質快照 #131 被 3 個嚴重問題阻擋——2867.TW 落後 11 個交易日；5371.TWO 落後 17 個交易日、近期缺漏 27%。
- 查證：
  - 資料庫：兩檔的官方行情分別停在 2026-08-19、08-21；Yahoo 之後仍有平盤、成交量 0 的資料（2867 固定 9.70 元到 9/11；5371 固定 83.5 元到 9/3），屬停止交易期間的填補值。
  - 官方：2026-09-29 證交所 `MI_INDEX`、櫃買最新收盤、證交所 `t187ap03_L` 與櫃買 `mopsfin_t187ap03_O` 公司名冊都查無兩檔，判定已下市（或終止櫃檯買賣）。既有下市資訊依賴 FinMind `TaiwanStockDelisting`，目前 FinMind 回應 HTTP 402（額度用完），所以股票池沒有更新。
- 新增 `application/listing_reconciliation.py` `TaiwanListingReconciliationService`：比對證交所＋櫃買現行名冊與啟用中的個股（不含 ETF、指數）；不在名冊且連續 3 個交易日以上無成交者停用，並把所有未結束的股票池區間在最後成交日關閉（`end_is_exact=False`，原因附在 reason），避免股票池擴充把它重新加回。防護：名冊少於 1,500 家不動作；單次超過 10 檔時全部暫停並要求人工確認。寫入 `tw_listing_reconciliation` 執行紀錄。
- 接入：`DailyResearchPipeline` 在台股行情更新後、早期決策與品質閘門之前執行；失敗只記錄，不中斷流程。
- 測試：`tests/test_listing_reconciliation.py` 5 項（停用並關閉區間、近期有成交者保留、名冊不完整不動作、超過上限要求人工確認、執行紀錄）；全部測試 211 通過、1 略過。
- 部署（20:25）：`stop-services.ps1` → `pip install .` → `start-services.ps1`（監督 282792、web 3660、api 282688、worker 277108；web `/health` 12 ms）。
- 正式資料執行（20:28）：名冊 1,988 家、比對 529 檔；停用 2867.TW（最後成交 8/19，28 個交易日無成交）、5371.TWO（最後成交 8/21，26 個交易日）。重跑台股原始品質快照 #132：`warning`、阻擋 0、落後標的 0、允許研究；剩 11 個不阻擋警告（少數個股缺融資融券或估值資料）。
- 待決定：停牌但仍在名冊上的個股目前仍會觸發個股落後阻擋，建議改為只排除該檔（`REQUIREMENTS.md` §14）。
- 回復：`git revert` 本工作包 commit 並重新部署；若要恢復兩檔，`set_active` 設回 True 並把對應股票池區間的 `valid_to` 改回空值。

## 2026-09-30 — S1-W04 服務啟停與效能

- 使用者同意重啟服務（2026-09-30）；GitHub push 由使用者完成，`origin/main` = `cd436d2`。
- 現況盤點（停機前，20:02）：三個服務的監督程序（PID 9988）已不存在，服務處於無人監督狀態；排程工作 `StockResearchLocalServices`（登入時執行）上次 9/26 21:27 啟動，結束代碼 `0xC000013A`（被中斷）。Worker 自 9/26 起累計 CPU 93,411 秒；停機前 20 秒內用掉 13.6 秒（約 68% 單核），正卡在 17:43 開始的美股補跑。
- CPU 根因（以排程紀錄查證）：
  1. 補抓守門員 `run_startup_catch_up` 只看最近 30 筆執行紀錄判斷「今天是否成功」，一次完整流程會寫 7–9 筆子紀錄，成功紀錄很快被擠出，於是每 15 分鐘重跑整套流程。9/28 起美股完整流程跑了 13 次（每次總經資料約 13 分鐘）。
  2. 同一段程式把資料庫的 naive UTC 時間當成台北時間，差 8 小時；台股 14:00 的成功紀錄被當成 06:00，早於 13:50 的排定時間。
  3. 9/28 休市誤報造成台股流程失敗 82 次（S1-W01 已修正）。
  - 更正：S1-W01 紀錄中懷疑的 `run_universe_backfill` 經查證只在「跨過目標的那一批」觸發一次全套研究，不是 CPU 元凶。
- 修正：`SchedulerJobRunRepository.latest_succeeded(job_name, market, since)`（以 UTC 查詢）、`AutomationService.succeeded_since`；守門員改用資料庫直接查詢。
- Web 改用 Waitress（8 threads，`channel_timeout=120`）；未安裝時退回 Flask 開發伺服器並記錄警告。`pyproject.toml` 新增 `waitress>=3,<4`。
- 服務腳本：
  - `scripts/run_local_services.ps1`：單一執行個體、PID 檔（`.runtime\services\`）、停止旗標、`instance\supervisor.log`、子程序 UTF-8 日誌（先前日誌為 Big5 亂碼）、新程序 120 秒後才開始健康檢查。
  - `scripts/stop-services.ps1`：停止旗標 → 以完整執行檔路徑核對並結束殘留程序樹 → 驗證 5000／8000 已釋放；13:30–14:40 預設拒絕。
  - `scripts/start-services.ps1`：觸發排程工作啟動（脫離工具工作階段），等待健康檢查並列出 PID。
  - `.gitignore` 加入 `.runtime/`。
- 部署（20:05–20:09）：`stop-services.ps1` 結束三個程序樹 → `pip install .`（含 Waitress）→ `start-services.ps1`。新 PID：監督 278120、web 279180（Python 278916）、api 268716（Python 189652）、worker 274600（Python 23104）。VectorDB 5001 回應 200，`cloudflared` PID 5508、10704 未受影響。
- 驗收：
  - `/health` 13 ms；`/data-quality` 111 ms；`/ai-trading` 冷啟 11.9 秒、熱快取 1.9–2.0 秒；首頁 `/` 冷啟 6.6 秒、熱快取 4.8 秒（輸出約 1.98 MB 的舊版市場總覽）。
  - 10 分鐘 CPU：worker 0.9 秒、api 0.8 秒、web 15.2 秒（含量測頁面請求）。
  - 重啟後 12 分鐘內排程紀錄 0 筆（守門員正確判定兩市場今日已完成、補資料已達標）。
  - S1-W01 生效：worker 啟動 23 秒後自動更新日曆快取（`fetched_at` 12:09:14 UTC）。
- 未達項目：首頁 < 2 秒。舊首頁會在 S2-W03 由輕量「今日」頁取代，效能目標移到該工作包。
- 測試：`test_universe_scheduler.py` 新增「今日成功紀錄被 40 筆新紀錄覆蓋、且以 UTC 儲存」情境；全部測試 206 通過、1 略過。
- 回復：`stop-services.ps1` → `git revert` 本工作包 commit → `pip install .` → `start-services.ps1`。

## 2026-09-30 — S1-W01 官方交易日曆

- 使用者決定（2026-09-30）：可以 push；公開網址用 `stockresearch.pimi-sunsun.com`；繼續開發。
- 目標：交易日判斷改用證交所官方開休市日期，休市日不再被判為缺資料；真正缺資料時仍阻擋。
- 來源查核：證交所 `rwd/zh/holidaySchedule/holidaySchedule?date=YYYY0101&response=json` 提供 2021–2026 年（2020 年以前與 2027 年回傳 0 筆）；舊端點忽略年份參數。表中「開始交易日」「最後交易日」是交易日提示，其餘（含「市場無交易，僅辦理結算交割作業」）為休市。颱風休市不在官方表內（例如 2024 年 7、10 月的颱風假）。
- 新模組 `src/quant_platform/market_calendar/`：
  - `core.py`：`TradingCalendar`（週末必休、`previous_trading_day`、`next_trading_day`、`sessions_after`、`covers`；連續休市超過 31 天視為資料錯誤）。
  - `twse.py`：證交所解析與下載（3 次重試）；0 筆年度回傳「尚未公布」，不當成沒有假日。
  - `store.py`：三層資料——隨程式發布的 `data/twse_closures.json`（2021–2026，122 個休市日）、`instance/market_calendar/twse_cache.json`（每日更新）、`instance/market_calendar/manual_closures.json`（人工補登）；`refresh_if_due` 每天最多一次，失敗 6 小時後再試。
  - CLI `python -m quant_platform.market_calendar`：`status`、`refresh`、`add-closure`、`remove-closure`、`export-packaged`。
- 接入：`DailyMarketDataPipeline.expected_session_date`、`TaiwanOfficialDailyBarProvider`（補抓區間跳過休市日）、`DataQualityService`（市場落後改算交易日；當年未收錄時發 `CALENDAR_COVERAGE` 警告）、`AfterHoursAiService`（休市標題顯示假日名稱；長假後預覽改以前一交易日判斷）、排程（定時工作與補抓守門員在台股休市日略過；新增每小時觸發、每日最多一次的日曆更新工作）。台股行情過期訊息附上颱風補登指令。容器新增 `market_calendar`，快取放在資料庫檔同一目錄（測試用暫存資料庫時不寫入正式 `instance/`）。`pyproject.toml` 加入套件資料。
- 美股維持週一至週五判斷（研究對照用途）；NYSE 假日列為後續項目。
- 測試：新增 `tests/test_trading_calendar.py` 20 項（解析、2026 中秋與教師節、農曆年結算日、未收錄年度、人工颱風休市、更新與退避、休市日不誤報、交易日缺資料仍阻擋、排程略過、資料品質警告、盤後決策休市標題與預覽）；`test_universe_scheduler.py` 預期工作清單加上 `market_calendar_refresh`。全部測試：205 通過、1 略過（66 秒）。pytest 需加 `--basetemp` 指向可寫目錄（本機沙箱不允許系統暫存目錄）。
- 實測：`refresh --year 2026 --year 2027` → 2026 年 24 個休市日、2027 年尚未公布；`status` 顯示收錄 2021–2026、當年已收錄。離線建置 wheel 確認包含 `market_calendar/data/twse_closures.json`（24.9 KB）。
- 部署：執行中的 web／api／worker 使用 `.venv` 已安裝的舊版套件；服務執行時 `pip install .` 可能因執行檔被鎖定而中斷，因此重新安裝與重啟併入 S1-W04（先建立有 PID 核對的啟停腳本）。本次未停止或重啟任何服務。下一個會受影響的休市日是 2026-10-09（國慶日補假）。
- 新發現（交給 S1-W04）：`scheduler/runner.py` 的 `run_universe_backfill` 每 10 分鐘執行一次，達到目標檔數後，每批都會重跑模型、因子、集成、組合與決策研究；這與 worker 累計約 25 小時 CPU、`model_predictions` 約 10 GiB 的現象一致，需量測確認。
- Push：本工具環境停用互動登入，GitHub 認證需使用者在自己的終端機執行一次 `git push`。
- 回復：`git revert` 本工作包 commit；已產生的 `instance/market_calendar/` 可直接刪除。

## 2026-09-30 — S0 基線保全與重新規劃

- 背景：2026-09-29 產品方向審查指出開發重心偏向增加研究模組，每日流程與決策核心尚未穩定。使用者確認新方向：單人使用的台股盤後決策台，AI 當研究員找出相同現金流下勝過定期定額的規則；前端 Flask + Jinja + HTML；發布到 Cloudflare（參照 VectorDB、AutoLayout）；資料庫優化由開發者決定；開發必須有路線圖、需求、開發記錄、交接記錄與系統架構圖。
- S0-W01 Git 基線：使用者安裝 Git 2.56.0。2026-08-13 clone 後從未 commit；45 個修改檔與 10 個未追蹤檔（含 Google Trends、官方日線來源、兩份審查文件）以 `c084e06` 一次 commit。commit 前檢查：`.env`、`instance/` 已被忽略；候選檔案掃描無金鑰；`.gitignore` 新增 `.mypy_cache/`、`.ruff_cache/`。未 push。
- S0-W02 資料庫量測（`dbstat` 唯讀，耗時 480 秒）：主檔 22.3 GiB、WAL 約 1.45 GiB。`model_predictions` 含 9 個索引約 10.2 GiB；`feature_values` 含 6 個索引約 8.6 GiB；其他資料表約 3.5 GiB；`market_bars` 0.13 GiB。結論：行情留 SQLite；瘦身對象是預測與特徵，改存 Parquet（S1-W03）。先前「把歷史行情移出 SQLite」的建議已更正。
- 環境觀察：
  - Dashboard `http://127.0.0.1:5000/` 在 60 秒內無回應；API 8000 可回應。
  - `quant-worker.exe`（PID 22024）自 9/26 起累計 CPU 約 25.5 小時。
  - 最新 worker 日誌：台股資料品質閘門阻擋（3 個嚴重問題）；漏跑補抓因上一次仍在執行而跳過。
  - 交易日判斷只跳過週末（`universe.py:281`、`taiwan_daily_bars.py:52`），9/25、9/28 休市會被誤判為缺資料。
  - C 槽可用 510 GB；`.venv` 尚未安裝 pyarrow、duckdb、PyJWT、waitress。
- S0-W03 規劃文件：新增 `REQUIREMENTS.md`、`AGENTS.md`、`CLAUDE.md`（匯入 AGENTS.md）、`docs/development_roadmap.md`（S0–S8 共 9 階段、42–61 工作天初估）、`docs/system_architecture.md`（6 張 Mermaid 圖：總覽、盤後時間軸、決策識別碼鏈、AI 研究迴圈、儲存分層、部署拓撲，另含架構決策與模組處置表）、`docs/development_handoff.md`。舊路線圖、模組狀態、進度效益與架構文件移入 `docs/archive/`；README 開頭改為新產品定位與文件索引。
- Cloudflare 參照：VectorDB 使用專屬 Tunnel `vectordb-pimi-sunsun`（`--url http://127.0.0.1:5001`，憑證在該專案 `.runtime/cloudflared/`），網站以 `access_control.py` 驗證 Access JWT；AutoLayout 使用 Access + Google 並在應用層做允許清單。本專案沿用相同模式（S2）。
- 測試：本輪只有文件與 `.gitignore` 變更，未執行測試。
- 回復：文件變更可 `git revert` 規劃 commit；歸檔檔案以 `git mv` 移回。

### 需求變更紀錄

- 2026-09-30：產品由「多模組 AI 量化研究平台」改為「單人盤後決策台」；登入改由 Cloudflare Access 負責；新增 §8 AI 研究員規則與 §13 暫停清單。來源：使用者本日對話與 `docs/product-direction-review-2026-09-29.md`。
