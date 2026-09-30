# 盤後決策台 — 當前開發交接

記錄時間：2026-09-30 21:45（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則。部署一律 `scripts\deploy.ps1`；只停服務用 `scripts\stop-services.ps1`，只啟動用 `scripts\start-services.ps1`。
2. `docs/development_roadmap.md`：S2 只剩 S2-W05；之後回到 S1-W06、S1-W05、S1-W07，再進 S3。
3. `DEVELOPMENT_HISTORY.md` 最上方三筆：Cloudflare 上線、S2 新介面第一版、S1-W03 瘦身。

## 上一輪完成

- 正式網址 `https://stockresearch.pimi-sunsun.com` 上線：Cloudflare Access application `StockResearch`＋規則 `StockResearch owner only`（只允許 ryan73093@gmail.com、必須 Google 登入）。未登入一律 302 到 Access 登入頁；登入後兩端看到同一版新介面。
- 之後每輪交付都要在本機與 Cloudflare 兩端驗收（`AGENTS.md`）。內建瀏覽器已保存使用者的 Cloudflare 與 Google 登入，可直接開正式網址驗收；登入失效時請使用者重新登入，不要代為輸入帳密。

## 當前工作包：S2-W05 備份與開機自動恢復

目標：資料庫每天自動備份並能還原；重開機後網站、排程與 Tunnel 自動恢復。

做法：
1. 新增每日 SQLite 線上備份（`sqlite3` backup API，完成後 `quick_check`，保留最近 7 份，存 `instance\backups\daily\`），排在 03:00（避開 02:30 預測封存與 13:30–14:40）；結果寫入排程紀錄與系統頁。
2. 還原演練腳本：把最新備份還原到暫存路徑、開啟、核對主要資料表筆數，輸出報告；不動正式檔。
3. 確認開機自動恢復：排程工作 `StockResearchLocalServices` 目前是「使用者登入時」觸發；確認 Windows 自動登入或改為開機觸發的影響後，向使用者說明再決定（變更系統排程屬於需要同意的設定）。
4. 系統頁「運作狀態」加上最近一次備份時間與結果。

驗收：備份檔可還原且筆數一致；系統頁顯示備份狀態；兩端驗收。

## 待辦提醒

- 使用者確認 2026-10-01 13:50 台股流程正常後，提供刪除 `instance\backups\quant_platform-pre-s1w03-20260930-204109.db` 與 `quant_platform-pre-vacuum-20260930-204109.db`（各 22.29 GiB）的指令，由使用者執行。
- 2026-10-09 為國慶日補假，確認排程正確略過（交易日曆第一次實際遇到休市）。

## 環境現況（2026-09-30 21:45）

| 項目 | 狀態 |
|---|---|
| 監督程序 | 排程工作 `StockResearchLocalServices` 啟動，含 Tunnel；紀錄 `instance\supervisor.log` |
| 網站 | Waitress 127.0.0.1:5000；`AUTH_MODE=cloudflare-access`（本機 loopback 免登入） |
| Tunnel | `stockresearch-pimi-sunsun`（`e37bb649-…`），4 條連線；日誌 `instance\tunnel.stderr.log` |
| Cloudflare | Access application `c49fc706-9f0e-4080-82ef-79f6ebfa5490`、規則 `ec4e2109-b2f2-491e-9ae9-229a3ef876ac`；AUD 在 `.env` |
| 資料庫 | `instance\quant_platform.db` 9.62 GiB；兩個 22.29 GiB 暫存備份待刪 |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>`；236 通過、1 略過 |
| 同主機其他服務 | VectorDB 5001 與其 Tunnel（PID 10704）、PimiServices 共用 cloudflared 服務（PID 5508，YtSummary／AutoLayout）。一律不操作 |

## 待使用者確認

| 項目 | 建議 | 需要時點 |
|---|---|---|
| 開機自動恢復的觸發方式 | 見上方做法 3 | S2-W05 |
| AI 研究員的 LLM Provider 與每月預算 | Anthropic Claude 或既有 OpenAI 設定 | S4 開始前 |
| 通知管道 | Email 之外是否加 Telegram 或 LINE Messaging API | S1-W06／S6 |
| 券商手續費折扣與最低費用 | 用於成交模型 | S3-W03 |
| 定期定額基準 ETF | 預設 0050 | S3 |
