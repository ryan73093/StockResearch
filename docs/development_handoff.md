# 盤後決策台 — 當前開發交接

記錄時間：2026-09-30 20:40（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則，特別是「資料庫安全」與服務啟停腳本。
2. `docs/development_roadmap.md`：S1-W03 的內容與門檻。
3. `REQUIREMENTS.md` §6（儲存與備份）。
4. `DEVELOPMENT_HISTORY.md` 的 S0 量測數據（各資料表大小）與最上方的 S1-W02、S1-W04。

## 上一輪完成

- S1-W04：補抓守門員修正、Waitress、啟停腳本；worker 閒置 CPU 約 0.15%。
- S1-W02：每日官方名冊比對，2867.TW、5371.TWO 已停用；台股原始品質快照 #132 允許研究。

## 當前工作包：S1-W03 資料庫瘦身

目標：主檔從 22.3 GiB 降到約 3 GiB 以下，並建立可還原的備份與保留政策；預測與特徵的歷史改存 Parquet，資料不遺失。

2026-09-30 量測（`dbstat`）：`model_predictions` 含 9 個索引約 10.2 GiB；`feature_values` 含 6 個索引約 8.6 GiB；其他約 3.5 GiB；`market_bars` 0.13 GiB。

步驟（使用者已授權資料庫優化方式由開發者決定；預計停機 30–60 分鐘，避開 13:30–14:40）：
1. 盤點：列出兩張表的所有索引，搜尋 `database/repositories.py` 與其他查詢實際用到的欄位；定義「使用中」的預測（最新決策、模型登錄、每個模型／標籤最新實驗引用的 experiment_id）與使用中的特徵版本。先以唯讀查詢量測各分類筆數。
2. `stop-services.ps1` → `PRAGMA wal_checkpoint(TRUNCATE)` → 以 SQLite backup API 完整備份到 `instance\backups\`（C 槽可用約 510 GB），記錄路徑與 SHA-256。
3. 新增依賴 `pyarrow`；非使用中的預測依 experiment_id、舊版特徵依版本匯出到 `instance\research\`（每檔記錄筆數與 SHA-256），核對筆數後才刪除。
4. 移除查詢未使用的單欄索引，並同步修改 `database/models.py`，避免新安裝重建。
5. `VACUUM INTO` 新檔 → 驗證（integrity_check、筆數、主要頁面）→ 替換主檔，舊檔保留到還原演練完成。
6. 程式改動：模型研究每次實驗的預測寫 Parquet，資料庫只保留使用中的預測；加入保留政策與測試。
7. 還原演練：從備份還原到暫存路徑並開啟查核；每日 SQLite 線上備份排程可併入 S2-W05。

驗收：主檔大小、還原演練、首頁與決策頁正常、全部測試通過、隔日台股流程完成。

回復：`stop-services.ps1` → 用步驟 2 的備份檔替換主檔 → `start-services.ps1`。

## 之後的順序

S1-W06（暫停非核心收集）→ S1-W05（收盤資料時效實測，需連續 5 個交易日）→ S2（Cloudflare 與新介面骨架，網址 `stockresearch.pimi-sunsun.com`）。

## 環境現況（2026-09-30 20:40）

| 項目 | 狀態 |
|---|---|
| 監督程序 | PID 282792（排程工作 `StockResearchLocalServices`），紀錄 `instance\supervisor.log` |
| Web／API／Worker | 啟動器 3660／282688／277108；Waitress；閒置 CPU 低 |
| 交易日曆 | 2021–2026；worker 每日更新快取 |
| 台股資料品質 | 快照 #132 `warning`，允許研究 |
| 資料庫 | `instance/quant_platform.db` 22.3 GiB + WAL |
| 套件 | 已安裝 waitress；未安裝 pyarrow、duckdb、PyJWT |
| Git | 可直接 push（使用者已完成 GitHub 登入） |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>`；211 通過、1 略過 |
| 同主機其他服務 | VectorDB 5001 與其 Tunnel（PID 10704）、`cloudflared` PID 5508、AutoLayout、YtSummary。一律不操作 |

## 待使用者確認

| 項目 | 建議 | 需要時點 |
|---|---|---|
| S1-W03 停機時段 | 今晚或非交易時段，停機 30–60 分鐘 | S1-W03 開始前 |
| 停牌個股是否阻擋全市場 | 只排除該檔並列警告，排除清單納入資料指紋 | 下次出現停牌個股前 |
| Access application 建立方式 | 使用者在 Zero Trust 建立，或授權以 API 建立 | S2-W02 |
| AI 研究員的 LLM Provider 與每月預算 | Anthropic Claude 或既有 OpenAI 設定 | S4 開始前 |
| 通知管道 | Email 之外是否加 Telegram 或 LINE Messaging API | S1-W06／S6 |
| 券商手續費折扣與最低費用 | 用於成交模型 | S3-W03 |
| 定期定額基準 ETF | 預設 0050 | S3 |
