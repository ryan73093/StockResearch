# 盤後決策台 — 當前開發交接

記錄時間：2026-09-30 20:35（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則。服務啟停一律用 `scripts\stop-services.ps1`、`scripts\start-services.ps1`。
2. `docs/development_roadmap.md`：S1 階段的工作包與門檻。
3. `REQUIREMENTS.md` §6、§7（研究規範不可妥協）。
4. `DEVELOPMENT_HISTORY.md` 最上方三筆：S1-W04、S1-W01、S0。

## 上一輪完成（S1-W04 服務啟停與效能）

- 補抓守門員的兩個錯誤已修正，worker 10 分鐘 CPU 從約 68% 單核降到 0.9 秒。
- Web 改用 Waitress；啟停腳本、單一監督程序、UTF-8 日誌完成；S1-W01 交易日曆已部署並自動更新。
- 首頁 `/` 熱快取仍需 4.8 秒（舊市場總覽輸出約 2 MB），目標移到 S2-W03。

## 當前工作包：S1-W02 資料品質問題排查

目標：查清目前阻擋台股研究的 3 個嚴重問題，建立停牌、下市、代號變更的正式處理規則，讓閘門依規則恢復通過。

最新台股原始階段快照（id 131，2026-09-30 10:05 UTC，`blocked`，534 檔全部有日線）：

| 代號 | 問題 | 觀測值 | 門檻 |
|---|---|---|---|
| 2867.TW | ASSET_STALE 行情落後共同交易日 | 11 個交易日 | 3 |
| 5371.TWO | ASSET_STALE 行情落後共同交易日 | 17 個交易日 | 3 |
| 5371.TWO | SESSION_GAPS 近期共同交易日缺漏比例 | 27% | 25% |

做法：
1. 逐檔核對原因：證交所／櫃買官方行情是否有這兩檔最近交易日的資料；是否為停牌、暫停交易、下市、代號變更或處置股；Yahoo 與官方來源差異。證據寫入開發歷程。
2. 設計處理規則（先寫需求再實作，需求寫進 `REQUIREMENTS.md` §6）：
   - 建議方向：個股層級的落後或缺漏，只把該檔排除在當日研究與決策之外，並列為警告；整體市場層級（覆蓋率、市場落後、時間因果、OHLCV 錯誤）維持阻擋。被排除的標的要在資料品質報告與系統頁列出原因。
   - 停牌／下市：依官方資料標記有效期間，停牌中不列入共同交易日比較；下市者停用並保留歷史（沿用存活者安全股票池）。
   - 這是調整閘門規則，不是放寬門檻：每一檔被排除的標的都要有原因，且排除清單會進入決策的資料快照指紋。
3. 測試：停牌個股不阻擋全市場、停牌個股不進入當日決策、整體市場落後仍阻擋、下市個股停用後歷史保留。

驗收：
- 兩檔的原因與處置有紀錄；閘門在新規則下通過或因市場層級問題正確阻擋。
- 相關測試與全部測試通過；下一個交易日 13:45 後台股流程完成。

回復：`git revert` 本工作包 commit → `stop-services.ps1` → `pip install .` → `start-services.ps1`。

## 之後的順序

S1-W03（資料庫瘦身；用 `stop-services.ps1` 停機後備份）→ S1-W06（暫停非核心收集）→ S1-W05（收盤資料時效實測，需連續 5 個交易日，可背景累積）→ S2（Cloudflare 與新介面骨架，網址 `stockresearch.pimi-sunsun.com`）。

## 環境現況（2026-09-30 20:35）

| 項目 | 狀態 |
|---|---|
| 監督程序 | PID 278120（排程工作 `StockResearchLocalServices` 啟動），紀錄 `instance\supervisor.log` |
| Web | 啟動器 279180、Python 278916，port 5000，Waitress |
| API | 啟動器 268716、Python 189652，port 8000 |
| Worker | 啟動器 274600、Python 23104；閒置 CPU 約 0.15% |
| 交易日曆 | 收錄 2021–2026；快取 `instance\market_calendar\twse_cache.json` 由 worker 每日更新；2027 年尚未公布 |
| 資料庫 | `instance/quant_platform.db` 22.3 GiB + WAL；最新台股品質快照 blocked（見上表） |
| 套件 | 已安裝 waitress；未安裝 pyarrow、duckdb、PyJWT |
| Git | 本輪 commit 後需 push（使用者已完成 GitHub 登入，可直接 push） |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>`；206 通過、1 略過 |
| 同主機其他服務 | VectorDB 5001（PID 248524）與其 Tunnel（PID 10704）、`cloudflared` PID 5508、AutoLayout、YtSummary。一律不操作 |

## 待使用者確認

| 項目 | 建議 | 需要時點 |
|---|---|---|
| 個股問題不再阻擋全市場 | 只排除該檔並列警告（見上方做法 2） | S1-W02 實作前 |
| Access application 建立方式 | 使用者在 Zero Trust 建立，或授權以 API 建立 | S2-W02 |
| AI 研究員的 LLM Provider 與每月預算 | Anthropic Claude 或既有 OpenAI 設定 | S4 開始前 |
| 通知管道 | Email 之外是否加 Telegram 或 LINE Messaging API | S1-W06／S6 |
| 券商手續費折扣與最低費用 | 用於成交模型 | S3-W03 |
| 定期定額基準 ETF | 預設 0050 | S3 |
