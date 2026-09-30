# 盤後決策台 — 開發歷程

每輪交付一筆，最新在最上方。記錄目標、做法、測試結果、證據、commit 與回復方式。v3.9 以前的研究平台版本紀錄見 [`docs/archive/module-status-v2.7-v3.9.md`](docs/archive/module-status-v2.7-v3.9.md)。

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
