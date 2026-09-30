# 盤後決策台 — 開發歷程

每輪交付一筆，最新在最上方。記錄目標、做法、測試結果、證據、commit 與回復方式。v3.9 以前的研究平台版本紀錄見 [`docs/archive/module-status-v2.7-v3.9.md`](docs/archive/module-status-v2.7-v3.9.md)。

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
