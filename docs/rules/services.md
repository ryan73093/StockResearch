# 服務與部署

適用：啟停服務、部署、Cloudflare 外網、操作程序。決定原文在 `docs/decisions.md`。

## 主機上的其他專案

- 主機同時執行其他專案：VectorDB（5001 與其 Tunnel）、AutoLayout（4173）、YtSummary（8001）及其他 `cloudflared` 程序。只操作核對過的本專案程序；動到共用的服務（DBQdrant、Home、cloudflared）前先看會不會影響其他專案（D-024）。
- 操作本專案服務前核對：PID、執行檔完整路徑（`.venv\Scripts\quant-web.exe`、`quant-api.exe`、`quant-worker.exe`）、命令列、port（5000、8000）。無法確認身分時先蒐集證據，不要停止程序。
- 專案在 `C:\Dev\StockResearch`；舊資料夾 `C:\Users\皮咪\Project\StockResearch` 只留給使用者回復或刪除，不在那裡改或跑任何東西；16:00 核對（`invariants.placement`）（D-024）。

## 啟停與部署

- 啟停一律用腳本，不要直接結束程序：
  - `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop-services.ps1`：先以停止旗標請監督程序關閉服務，再以完整執行檔路徑核對並結束殘留程序樹，最後確認 5000／8000 已釋放。13:30–14:40（台北）預設拒絕執行；15:14–15:50（研究資料更新、前向紀錄、個股快照）與 worker 有登錄中的工作在跑時（新聞 14:40、籌碼 21:30、模型重訓 22:45）也拒絕，`-DuringWorkerJob` 可略過（2026-10-06 曾在 14:41 部署而打斷新聞收集）。部署前先看研究頁「執行中的程式」。
  - `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-services.ps1`：觸發 Windows 排程工作 `StockResearchLocalServices`（登入時也會自動執行），服務因此脫離終端機與 AI 工具工作階段；等待 web、api 健康檢查通過並列出 PID。
- 監督程序 `scripts/run_local_services.ps1` 同時只允許一個；PID 檔在 `.runtime\services\`，紀錄在 `instance\supervisor.log`，服務日誌為 UTF-8（`instance\*.stderr.log`）。
- 服務從 `.venv\Lib\site-packages` 執行已安裝的套件。部署程式變更一律用 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy.ps1`（停止 → `pip install .` → 匯入檢查 → 啟動；中文路徑不要用 editable 安裝）。不要在服務執行中直接 `pip install`：2026-09-30 曾因此把套件移除一半，服務反覆啟動失敗。

## 外網與驗收

- 發布到 Cloudflare：本專案專屬 Tunnel，網域 `pimi-sunsun.com`。外網登入用 Home SSO（`home.pimi-sunsun.com`，Google 登入、只允許擁有者；`dashboard/cloudflare_access.py`、`.runtime/home-sso.json`），沒登入的請求被拒；`AUTH_MODE=cloudflare-access` 時監督程序才會開 Tunnel（D-005）。
- 每輪交付在本機（`http://127.0.0.1:5000`）以瀏覽器驗收內容，再跑 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check-public.ps1` 確認外網：本專案 Tunnel 已連線，公開網址由 Home SSO 把關、沒登入的資料請求被拒（Tunnel 轉送的就是這個本機來源）。記錄時間與證據（D-042）。
- 不為了驗收請使用者登入；瀏覽器窗格已在登入狀態時才順便開外網頁面看；不代替使用者輸入 Google 帳號或密碼（D-042）。
