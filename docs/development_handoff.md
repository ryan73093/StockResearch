# 盤後決策台 — 當前開發交接

記錄時間：2026-09-30 20:00（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則（服務與資料庫安全、commit 作者、驗收方式）。
2. `docs/development_roadmap.md`：S1 階段的工作包與門檻。
3. `REQUIREMENTS.md` §3、§6、§10。
4. `DEVELOPMENT_HISTORY.md` 最上方一筆：本輪量測數據與環境觀察。

## 上一輪完成（S0）

- Git 基線 `c084e06`；規劃文件 commit 緊接其後（見 `git log`）。尚未 push，push 前需使用者同意。
- 新方向、需求、路線圖、架構圖已由使用者確認（2026-09-30）。

## 當前工作包：S1-W01 官方交易日曆

目標：交易日判斷改用證交所官方開休市日期，休市日不再被判為缺資料；真正缺資料時仍阻擋。

範圍：
- 新增交易日曆模組（建議 `src/quant_platform/calendar/`）：
  - 來源：證交所「市場開休市日期」（依民國年度查詢），保存日期、是否開市、原因、來源 URL、取得時間。表中「僅辦理結算交割、市場無交易」的日期視為不開市。
  - 人工補登：颱風等臨時休市可由系統頁或 CLI 新增，保存操作時間與原因。
  - 年度資料缺漏時回傳明確告警，並退回「週一到週五」規則且在資料品質報告標示。
- 替換呼叫點：
  - `src/quant_platform/application/universe.py:267` `expected_session_date`（第 281 行只跳過週末）。
  - `src/quant_platform/data_sources/taiwan_daily_bars.py:45` `expected_session_date`（第 52 行）。
  - `src/quant_platform/application/after_hours_ai.py:2226`、`:2466` 的週末判斷（「今日休市」文字）。
- 美股市場目前同樣只跳過週末；本工作包先處理台股，美股假日列為 S1-W01 後續項目或併入 S1-W02。

測試（新增 `tests/test_trading_calendar.py`，並補既有排程測試）：
- 2026-09-25（中秋節）、2026-09-28（教師節）休市：期望交易日回推到 2026-09-24，閘門不報缺資料。
- 一般交易日收盤後缺當日行情：仍阻擋。
- 週末、國定假日、補班日、人工補登的臨時休市。
- 年度資料缺漏：告警並退回週間規則。

驗收：
- 上述測試通過；`tests/test_universe_scheduler.py`、`tests/test_market_data.py`、`tests/test_data_quality.py` 維持通過。
- 重新安裝套件並重啟服務後，下一個交易日的排程紀錄顯示正確的期望交易日。

回復：`git revert <commit>` → `.\.venv\Scripts\python.exe -m pip install .` → 依 `AGENTS.md` 核對後重啟本專案服務。

## 建議接續順序

S1-W01 → S1-W04（服務啟停腳本、Waitress、首頁無回應與 worker 高 CPU）→ S1-W02（3 個嚴重品質問題）→ S1-W03（資料庫瘦身，需要 S1-W04 的停機腳本）→ S1-W06（暫停非核心收集）→ S1-W05（收盤資料時效實測，需連續 5 個交易日，可在其他工作包進行時背景累積）。之後進入 S2（Cloudflare 與新介面骨架）。

## 環境現況（2026-09-30 19:30 量測）

| 項目 | 狀態 |
|---|---|
| Web `quant-web.exe` | PID 21688，port 5000；首頁 60 秒無回應 |
| API `quant-api.exe` | PID 21940，port 8000；可回應 |
| Worker `quant-worker.exe` | PID 22024；自 9/26 起累計 CPU 約 25.5 小時；台股品質閘門阻擋中 |
| 服務監督 | `scripts/run_local_services.ps1`（健康檢查失敗三次重啟）；監督程序 PID 未核對 |
| 資料庫 | `instance/quant_platform.db` 22.3 GiB + WAL 約 1.45 GiB |
| 磁碟 | C 槽可用 510 GB |
| 套件 | 已有 yfinance；未安裝 pyarrow、duckdb、PyJWT、waitress |
| Git | `C:\Program Files\Git\cmd\git.exe` 2.56.0；遠端 `https://github.com/ryan73093/StockResearch.git` |
| 同主機其他服務 | VectorDB Waitress 5001（PID 248524）與其 Tunnel（PID 10704）；另一個 `cloudflared` PID 5508；AutoLayout、YtSummary。一律不操作 |

## 待使用者確認

| 項目 | 建議 | 需要時點 |
|---|---|---|
| push 到 GitHub | commit 後推送 `main` | 隨時 |
| Cloudflare 子網域 | `stock.pimi-sunsun.com` | S2 開始前 |
| Access application 建立方式 | 使用者在 Zero Trust 建立，或授權以 API 建立 | S2-W02 |
| AI 研究員的 LLM Provider 與每月預算 | Anthropic Claude 或既有 OpenAI 設定 | S4 開始前 |
| 通知管道 | Email 之外是否加 Telegram 或 LINE Messaging API | S1-W06／S6 |
| 券商手續費折扣與最低費用 | 用於成交模型 | S3-W03 |
| 定期定額基準 ETF | 預設 0050 | S3 |
