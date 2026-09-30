# 盤後決策台 — 當前開發交接

記錄時間：2026-09-30 21:40（Asia/Taipei）。交接模型：Claude（Opus 5.5）。本檔只留當前工作包；完成後把紀錄移到 `DEVELOPMENT_HISTORY.md`，再換成下一個工作包。

## 接手前必讀

1. `AGENTS.md`：開發規則。部署一律 `scripts\deploy.ps1`；只停服務用 `scripts\stop-services.ps1`，只啟動用 `scripts\start-services.ps1`。
2. `docs/development_roadmap.md`：S2 的工作包與狀態（使用者決定 S2 提前）。
3. `REQUIREMENTS.md` §2、§9、§10。
4. `DEVELOPMENT_HISTORY.md` 最上方兩筆：S2 第一版、S1-W03 瘦身。

## 上一輪完成

- S1-W03：主檔 22.29 → 9.62 GiB；1,934 萬筆預測封存為 Parquet（247 MB）；每天 02:30 自動封存。
- 停牌規則：個股落後只排除該檔（資料品質 v2、決策 1.1.0）。
- S2：Tunnel 與 DNS 已建立；Access 驗證程式完成；新介面五頁第一版上線（本機）。

## 當前工作包：S2-W02 啟用 Cloudflare 發布（等使用者提供 AUD）

使用者要在 Cloudflare Zero Trust 建立 self-hosted Access application：名稱 `StockResearch`、hostname `stockresearch.pimi-sunsun.com`、Google 登入、Allow 規則 Include Emails（擁有者 Google 帳號，預設 ryan73093@gmail.com，以使用者回覆為準）、不設 Bypass；並提供 Application Audience (AUD) Tag。

收到 AUD 後：
1. 在 `.env` 加入（不要輸出 `.env` 其他內容）：
   ```
   AUTH_MODE=cloudflare-access
   PUBLIC_URL=https://stockresearch.pimi-sunsun.com
   ACCESS_TEAM_DOMAIN=https://raspy-mode-cc1c.cloudflareaccess.com
   ACCESS_AUD=<使用者提供>
   ACCESS_ALLOWED_EMAILS=<擁有者 email>
   ```
2. `stop-services.ps1` → `start-services.ps1`（沒有程式變更，不需重新安裝）。監督程序看到 `AUTH_MODE=cloudflare-access` 會一併啟動 Tunnel；`instance\supervisor.log` 應顯示 "with tunnel"，`instance\tunnel.stderr.log` 應有 "Registered tunnel connection"。
3. 驗收：
   - 本機 `http://127.0.0.1:5000/` 仍可直接使用（loopback 例外）。
   - 未登入的 `https://stockresearch.pimi-sunsun.com/` 應被 Cloudflare 導到 Access 登入頁（`cloudflareaccess.com`）。
   - 使用者用手機登入後看到新版「今日」頁；系統頁「外網發布」顯示 Cloudflare Access。
   - 若出現 1033／502：先看 Tunnel 程序與日誌；若是網站 401，核對 AUD 與團隊網域。
4. 在開發歷程記錄兩端驗收時間與證據，路線圖 S2-W01／W02 改為 done。

## 接著

- S2-W05：每日 SQLite 線上備份（保留 7 份）與還原演練；確認重開機後服務與 Tunnel 自動恢復。
- 使用者確認隔日台股流程正常後，可刪除 `instance\backups\` 的兩個 22.29 GiB 暫存檔（刪除指令交由使用者執行）。
- 之後：S1-W06（暫停非核心收集，含模型治理的全量預測載入）→ S1-W05（收盤時效實測）→ S1-W07（特徵表正規化）→ S3。

## 環境現況（2026-09-30 21:40）

| 項目 | 狀態 |
|---|---|
| 監督程序 | 排程工作 `StockResearchLocalServices` 啟動；紀錄 `instance\supervisor.log`；目前不含 Tunnel（AUTH_MODE=development） |
| 網站 | Waitress 127.0.0.1:5000；新首頁熱快取 20–50 ms |
| Tunnel | `stockresearch-pimi-sunsun`，id `e37bb649-0237-434f-b5c7-833722d87f9d`，憑證 `.runtime\cloudflared\stockresearch-pimi-sunsun.json`；DNS CNAME 已建立；程序尚未啟動 |
| cloudflared | `C:\Users\皮咪\Project\YtSummary\.tools\cloudflared\cloudflared.exe`（2026.7.3） |
| 資料庫 | `instance\quant_platform.db` 9.62 GiB；備份 `instance\backups\quant_platform-pre-s1w03-20260930-204109.db`、`quant_platform-pre-vacuum-20260930-204109.db`（各 22.29 GiB） |
| 預測封存 | `instance\research\predictions\`（217 檔，manifest.jsonl） |
| 套件 | waitress、pyarrow、PyJWT[crypto] 已安裝；site-packages 有 pip 中斷留下的 `~uant-research-platform` 殘留目錄（只造成警告） |
| 測試 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <可寫目錄>`；236 通過、1 略過 |
| 同主機其他服務 | VectorDB 5001 與其 Tunnel、YtSummary Tunnel、AutoLayout。一律不操作 |

## 待使用者確認

| 項目 | 建議 | 需要時點 |
|---|---|---|
| Access application 與 AUD | 見上方 | 現在 |
| AI 研究員的 LLM Provider 與每月預算 | Anthropic Claude 或既有 OpenAI 設定 | S4 開始前 |
| 通知管道 | Email 之外是否加 Telegram 或 LINE Messaging API | S1-W06／S6 |
| 券商手續費折扣與最低費用 | 用於成交模型 | S3-W03 |
| 定期定額基準 ETF | 預設 0050 | S3 |
