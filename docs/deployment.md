# 正式環境部署（v2.4）

## 架構與責任

- `postgres`：保存 point-in-time 行情、特徵、因子、回測、模型、排程與研究報告。
- `redis`：提供跨 Web、API 與 worker 的分散式執行鎖；AOF 開啟以保留短期狀態。
- `init`：等待基礎服務健康後建立資料表與預設研究資料，只執行一次。
- `web`：Gunicorn 執行 Flask Dashboard，不在 Web worker 內啟動排程。
- `api`：Uvicorn 執行 FastAPI。
- `worker`：APScheduler 常駐工作者，依資料庫排程執行每日研究流程。

## 啟動

需先安裝 Docker Desktop（含 Compose v2）。

```powershell
Copy-Item .env.docker.example .env.docker
# 編輯 .env.docker，至少替換 SECRET_KEY 與 POSTGRES_PASSWORD
docker compose --env-file .env.docker config
docker compose --env-file .env.docker up --build -d
docker compose --env-file .env.docker ps
```

Dashboard：`http://127.0.0.1:5000`；API 文件：`http://127.0.0.1:8000/docs`。

## 驗收

```powershell
Invoke-RestMethod http://127.0.0.1:5000/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
docker compose --env-file .env.docker logs --tail 100 worker
```

正式容器的健康回應必須同時顯示 `database=connected`、`redis=connected`、
`lock_backend=redis`。若 Redis 不可用，`REDIS_REQUIRED=true` 會阻止服務以不安全的本機鎖繼續運作。

若要開啟 Google 登入，需在 Google Cloud 建立 Web application OAuth client，將正式 HTTPS callback 完整加入 Authorized redirect URIs，並設定 `GOOGLE_OAUTH_ENABLED=true`、Client ID／Secret 與完全一致的 `GOOGLE_REDIRECT_URI`。API 寫入端點一律使用 `.env.docker` 的 `API_WRITE_TOKEN`，不得放入前端程式碼。

## 備份與還原

每日備份 PostgreSQL，並將備份檔複製到容器主機之外；不要只依賴 Docker volume。

```powershell
docker compose --env-file .env.docker exec -T postgres pg_dump -U quant -d quant -Fc -f /tmp/quant.dump
docker compose --env-file .env.docker cp postgres:/tmp/quant.dump .\backups\quant.dump
```

還原前應先停止 `web`、`api`、`worker`，在另一套測試環境演練並核對資料列數。實際還原會覆寫資料，必須先保存現況備份。

## 使用雲端資料庫

平台已透過 SQLAlchemy 與 `DATABASE_URL` 支援 PostgreSQL 連線格式，容器部署也已使用 `postgresql+psycopg://...`。可以把資料放在 Supabase、Neon、Google Cloud SQL 或 Azure Database for PostgreSQL，但正式切換前仍必須：

1. 安裝 `infra` 相依套件並建立專用資料庫使用者。
2. 使用連線池或供應商建議的 pooled connection；啟用 TLS。
3. 先在測試資料庫執行 schema 建立、完整流程與併發排程測試。
4. 啟用每日備份；正式交易研究建議另加時間點還原。
5. PostgreSQL 作為正式來源，SQLite 只留在開發或離線快取；不可讓兩邊各自寫入形成分叉。

媒體檔、原始影片與大型逐字稿放物件儲存，不要直接塞入 PostgreSQL。資料庫只保存 metadata、內容雜湊、切塊、結構化特徵、向量與稽核紀錄。

### 費用與目前決定（2026-07-26）

- 現在的本機 SQLite：軟體費用為 0，缺點是常駐工作與網頁同時大量寫入時容易鎖住，而且電腦關機就不會更新。
- Supabase Free：每月 0 美元，含 500 MB 資料庫與 1 GB 檔案空間；一週沒有活動會暫停，而且不含自動備份。Pro 目前從每月 25 美元起。
- Neon Free：每月 0 美元，官方目前列出每個專案 0.5 GB 儲存與每月 100 CU-hours，閒置可縮到零；較適合先做低成本 PostgreSQL 測試。
- Google Cloud SQL：正式受管服務，依 CPU、記憶體、儲存和網路流量計費，不是固定免費方案。

目前**沒有遷移、沒有建立付費帳號，也沒有產生雲端費用**。先保留 SQLite；等資料量、24 小時排程或多人使用真的需要時，再由使用者選擇供應商並明確確認費用後遷移。

## 尚未包含

- Alembic 版本式 migration（目前是首次建表與向前相容 `create_all`）。
- 多台 worker 任務佇列與工作租約續期。
- TLS、反向代理、集中式日誌、異地備份與監控告警。
- Google OAuth 與使用者資料隔離。

上述項目是後續正式上線前的必要工作；目前 v2.4 是可重現的單主機容器基線。
