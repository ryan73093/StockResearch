# Quant Research Platform

目前版本：v3.8.0。新增上市／上櫃法說會與重大訊息，保存公司發言時間、法說會時間、官方簡報、影音、內容指紋與修訂版本；事件可以先公告後舉行，回測只從公告可用時間開始使用。既有 26 項盤中／衍生品／零股特徵與所有明確單位維持可用。沒有 Docker 或付費 Token 仍可使用 SQLite 與公開官方來源。真實交易維持硬性關閉，Google 登入與 Docker 延後至發布前。

一套以 Clean Architecture 建構的 AI 量化研究平台。Flask 提供研究 Dashboard，FastAPI 提供資料與模型服務 API；兩者共用 domain、application 與 infrastructure，避免商業邏輯綁死在 Web framework。

## 已完成功能

- Flask 市場研究入口頁與系統狀態頁
- FastAPI 健康檢查端點
- SQLAlchemy 研究實驗資料模型與 Repository
- 環境變數設定、application service 與 dependency container
- 可直接執行的 SQLite 開發模式（正式環境可切 PostgreSQL）
- 單元與整合測試
- Point-in-time 日線資料模型：`event_time`、`available_time`、`ingested_at`
- Yahoo Finance Provider 與可替換的 Provider port
- 冪等 ingestion、自然鍵去重與資料覆蓋統計
- OHLC、成交量、時區與未收盤日 K 品質閘門
- Flask Data Vault 與 FastAPI coverage/ingestion endpoints
- Quant Command Center：真實 benchmark 曲線、Regime、Factor snapshot、回測、Portfolio、Risk 與每日研究報告
- SMA 5/20 趨勢基準回測：訊號 lag 一個 session 並納入 10 bps 換倉成本
- Inverse-volatility risk parity、VaR/CVaR、Beta、Correlation 與 Max Drawdown
- 16 個美台 ETF、兩年日線、跨資產熱圖、因子橫斷面排名與策略排名
- 深色科技終端介面，統一 label／正文／標題／KPI 四級字體尺度
- 資料庫驅動的 Research Universe，可新增、啟用與軟停用標的
- 台股 13:35、美股 06:30（台北時間）自動增量更新與 Scheduler Audit Log；台股決策日期不符當日收盤快照時，盤後 AI 強制不交易
- 版本化 Point-in-time Feature Store：14 個報酬、趨勢、動能、波動、量價、風險與日曆特徵
- Leakage-safe Label Store：未來 5/20 日報酬、5 日方向與相對 benchmark 超額報酬
- 每日流程已串接「行情更新 → Feature/Label materialization」，兩階段各自保留 audit run
- Point-in-time Market Regime Engine：Bull/Bear/Range × High/Normal/Low Vol，逐日保存信心與 lineage
- Factor Research Engine：IC、Rank IC、ICIR、正 IC 比例、超額報酬 spread、turnover、decay 與分 Regime 表現
- Factor promotion gate：目前股票池不足 30 檔時強制標示 `SMALL_UNIVERSE`，禁止把探索結果當成策略
- Bias-safe Walk-forward Engine：252 日 rolling train、63 日非重疊 OOS test，只保存未參與調參的績效
- Next-open execution：收盤訊號延遲至下一交易日開盤成交，分開計算 overnight / intraday P&L
- 交易成本、滑價、波動度部位縮放、停損停利、Trade/Fold/Equity 完整稽核資料
- 三個模組化基準策略：Trend Momentum、Mean Reversion、Regime-aware；目前全部維持 `RESEARCH`
- 動態 Strategy Ensemble：僅用 t-1 前的 OOS 績效與已知 Regime 配置三策略，保存每日權重、貢獻、換手與成本
- Ensemble Lab：ECharts 顯示動態集成相對等權績效、權重歷史與逐標的 promotion gate
- 市場首頁將台股研究標的與全球重大指標分區，重大指標含 NASDAQ、S&P 500、SOX、黃金、USD/TWD、台股指數、USD/JPY、VIX、Brent；台股價格使用可縮放 K 線
- Portfolio & Risk Engine：Equal Weight、Inverse Volatility、Minimum Variance、Mean-Variance、Risk Parity、HRP、CVaR、Kelly 與 Black-Litterman 九種配置方法
- 60 日 rolling、21 日再平衡、t-1 資訊限制、持倉漂移、單一標的/產業上限、現金與 5 bps 換手成本
- Portfolio Risk：VaR、CVaR、Beta、最大回撤、有效持股數、分散化比率、平均相關性、產業/因子曝險、流動性代理與六種壓力測試
- Portfolio/Risk Lab：ECharts 比較 Portfolio、等權與 SPY/0050.TW，展示最新權重、風險診斷及逐方法 promotion gate
- 可解釋人工智慧 v1：19 項台股特徵以公告可用時間合併，保存樣本外排列重要性、局部反事實貢獻、模型分歧與研究門檻
- 中文開發藍圖與操作範例：`/roadmap` 記錄所有階段狀態，`/guide` 提供 2330 從資料更新到決策解讀的完整範例
- 盤後零股研究：`/odd-lot` 可調整每月預算、薪資日、手續費折數、最低費用與滑價，比較三種低頻投入策略
- 每頁名詞解釋：由側邊欄下方的「？ 本頁名詞解釋」展開，不必離開目前頁面
- AutoML v1：Optuna 巢狀時間切分、Random Forest、SVM、最佳參數保存、可重現種子與相同資料快照復用
- 模型研究非同步操作回饋、精簡可展開模型目錄與完整參數搜尋範圍
- 規則式技術分析自動解讀，以及 7 項時點一致跨資產特徵
- 自動排程與通知中心：台／美股排程保存在資料庫，可調整時間、啟停、重試與成功／失敗通知
- SMTP Email Adapter：未設定時安全略過並保存原因，寄送失敗不會抹除已完成的研究結果
- 正式環境容器基線：PostgreSQL、Redis AOF、初始化服務、Gunicorn Web、Uvicorn API 與獨立排程 worker
- Redis token-safe 分散式鎖：多個服務不會同時執行相同每日流程；正式模式 Redis 失效時拒絕不安全降級
- Google 登入安全基線：只要求 openid/email/profile，不保存 Google access token，登入 state 綁定瀏覽器且只能使用一次
- 正式環境寫入邊界：Dashboard 未完成登入時不可修改資料，FastAPI 寫入端點要求 `API_WRITE_TOKEN`
- 台股模擬交易 v1：待成交／成交／取消／拒絕狀態機，下一根可用日 K 開盤成交，沒有未來資料就保持等待
- 模擬帳務與風控：新台幣現金、股數、平均成本、已實現／未實現損益、14.25 bps 手續費、證交稅、10 bps 滑價、單股 20% 與總曝險 80% 上限
- 數值單位：台股資料頁明確顯示元、元／股、股、張、倍、%、天、年、月與筆；來源未定義單位時不猜測
- 人工晉級閘門：樣本外候選、資料指紋、折數、超額報酬、Sharpe、回撤與影子觀察共 11 項後端檢核
- 核准安全狀態機：待審、核准、拒絕、撤銷與到期；新實驗、資料失效或門檻失效會由每日排程自動撤銷
- 本機券商沙盒：只有有效人工核准可送入，回條持久化且同一來源委託冪等；不連網、不保存券商憑證、真實交易永遠關閉
- 模型登錄庫：每個市場／模型／標籤只保留最新挑戰者，舊快照可追溯封存，冠軍升降級保存審查人與理由
- 漂移監控：以 252 個基準交易日對 63 個近期交易日計算特徵 PSI，並比較最新實驗的方向率、Rank IC 與候選狀態
- 冠軍安全邊界：候選、觀測數、折數、方向率、Rank IC、R² 與漂移監控必須全數通過；嚴重漂移或監控資料不足會每日自動降級
- Point-in-time 修訂解析：行情查詢依 `as_of` 先排除當時尚未公開版本，同一交易日只回傳當時可見的最新修訂版
- 資料品質快照：保存市場、階段、SHA-256 資料指紋、日線／特徵覆蓋率、缺漏、時效、時間因果與 OHLCV 證據
- 兩段式研究閘門：行情、台股研究資料與總經更新後先檢查原始資料；特徵建立後再檢查完整資料，嚴重問題禁止模型、因子與回測繼續執行
- 盤中與衍生資料 v1：9 種資料集目錄，涵蓋台股一分鐘 K、期貨／選擇權日資料與逐筆成交、臺指選擇權 VIX、盤後零股、法說會與 Google Trends 規劃
- 通用 Point-in-time Observation：事件、來源可用、匯入三個時間，內容雜湊、邏輯修訂鍵、來源 URI 與 As-of 最新可見版本解析
- 官方資料介接：FinMind v4 Bearer Token、台股分鐘／期貨／選擇權 Adapter，以及證交所 `TWT53U` 目前零股行情快照；每個數值附元、元／股、股、口、筆或百分比單位
- 可選每日盤中／衍生更新：預設關閉，設定 `PIT_AUTO_INGESTION_ENABLED=true` 後併入台股每日流程，失敗保留明確資料集與原因
- 盤中與衍生特徵 v1：分鐘量價 6 項、盤後零股流動性 7 項、期貨 6 項、選擇權 5 項與臺指選擇權波動率 2 項
- 不可變 Feature Revision：每筆保存事件時間、模型可用時間、計算時間、輸入 SHA-256 與來源 lineage；每日台股排程自動重算最近 14 天
- 法說會與重大事件 v1：證交所上市公司、櫃買中心上櫃公司每日重大訊息，單一公司最新 MOPS 法說會明細、簡報與影音
- 事件 Point-in-time：公告可用時間與法說會時間分開，延期、取消、內容或文件更新新增版本；每日台股排程自動更新

## 快速開始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m quant_platform.dashboard.app
```

瀏覽 `http://127.0.0.1:5000`。API 可另開終端執行：

```powershell
.\.venv\Scripts\python.exe -m quant_platform.api.app
```

API 文件位於 `http://127.0.0.1:8000/docs`。

Docker 正式環境基線請先安裝 Docker Desktop，再依 [部署說明](docs/deployment.md) 建立 `.env.docker` 並執行 `docker compose --env-file .env.docker up --build -d`。目前開發主機未安裝 Docker CLI，因此 Compose 結構已驗證，但尚未在這台主機實際拉取與啟動映像。

Data Vault 位於 `http://127.0.0.1:5000/data`，可輸入 `SPY`、`0050.TW` 等代號更新日線。Yahoo 提供 session date 而非權威 publication timestamp，因此目前以正常收盤後 15 分鐘作為保守的 `available_time`，且不保存尚未收盤的當日日 K。

盤中與衍生資料位於 `http://127.0.0.1:5000/point-in-time-data`。不需 Token 即可下載證交所目前零股行情；FinMind 期貨／選擇權日資料可依方案嘗試，台股分鐘 K 與逐筆成交需要官方對應會員權限。可輸入歷史時間執行 As-of 查詢，確認回測當時真正可見的版本。詳細規則見 [盤中與衍生資料說明](docs/point-in-time-intraday.md)。

盤中與衍生特徵位於 `http://127.0.0.1:5000/intraday-features`。先在資料頁下載來源，再按「建立／更新特徵」；輸入 `2330`、`TX` 或 `TXO` 可查看最新修訂、模型可用時間、來源與明確單位。API 為 `GET /api/v1/intraday-features` 與 `POST /api/v1/pipelines/intraday-features`；計算規則見 [盤中與衍生特徵說明](docs/intraday-derivative-features.md)。

法說會與重大事件位於 `http://127.0.0.1:5000/corporate-events`。輸入 `ALL` 取得當日上市／上櫃法說會公告；輸入 `2330` 等單一代號可補充最新官方簡報與影音。API 為 `GET /api/v1/corporate-events` 與 `POST /api/v1/pipelines/corporate-events`；限制與操作見 [法說會事件說明](docs/corporate-events.md)。完整模組進度與投資研究效益見 [平台進度與效益](docs/platform-progress-and-benefits.md)。

Universe Manager 位於 `http://127.0.0.1:5000/universe`。新增標的後會自動加入 Dashboard 與排程；停用不刪除歷史資料。手動執行可使用：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m quant_platform.scheduler.cli --market TW
.\.venv\Scripts\python.exe -m quant_platform.scheduler.cli --market US
```

Feature Store 位於 `http://127.0.0.1:5000/features`。可查看特徵定義、版本、lookback、實際覆蓋筆數與標籤最後可用時間，也可獨立重建台股或美股 Store。API 對應 `GET /api/v1/features/coverage` 與 `POST /api/v1/pipelines/features/{market}`。

Factor Lab 位於 `http://127.0.0.1:5000/factors`。可查看最新市場 Regime、因子方向、5/20 日 Rank IC、ICIR、top-minus-bottom benchmark excess return、換手、衰退與最佳 Regime。API 對應 `GET /api/v1/factors/research` 與 `POST /api/v1/pipelines/factor-research/{market}`。

Backtest Lab 位於 `http://127.0.0.1:5000/backtests`。可查看完整 OOS equity、benchmark、fold 參數選擇、交易與退出原因、成本及 Promotion Gate 失敗條件。API 對應 `GET /api/v1/backtests`、`GET /api/v1/backtests/{run_id}` 與 `POST /api/v1/pipelines/backtests/{market}`。

Ensemble Lab 位於 `http://127.0.0.1:5000/ensembles`。Portfolio & Risk Lab 位於 `http://127.0.0.1:5000/portfolios`，可比較九種配置器的 OOS 績效、權重、產業/因子曝險、VaR/CVaR 與壓力測試。API 對應 `GET /api/v1/portfolios`、`GET /api/v1/portfolios/{run_id}` 與 `POST /api/v1/pipelines/portfolios/{market}`。

模型解釋頁位於 `http://127.0.0.1:5000/explain?symbol=2330`；開發藍圖位於 `http://127.0.0.1:5000/roadmap`；含實際操作範例的使用指南位於 `http://127.0.0.1:5000/guide`。模型解釋 API 為 `GET /api/v1/explanations/{symbol}`。

自動排程與通知位於 `http://127.0.0.1:5000/automation`。在 `.env` 設定 `EMAIL_ENABLED=true`、SMTP 主機、寄件人與 `REPORT_EMAIL_TO` 後，可先使用「寄送測試 Email」驗證；密碼只從環境變數讀取，不會出現在網頁或通知紀錄。

帳號與資料權限位於 `http://127.0.0.1:5000/account`。Google Cloud Web client 的 redirect URI 必須與 `GOOGLE_REDIRECT_URI` 完全一致；本機預設為 `http://127.0.0.1:5000/auth/google/callback`。正式環境需使用 HTTPS、設定 `AUTH_COOKIE_SECURE=true`，並為 FastAPI 寫入請求加入 `Authorization: Bearer <API_WRITE_TOKEN>`。

模擬交易位於 `http://127.0.0.1:5000/paper-trading`。輸入 `2330` 或 `0050`、買賣方向與股數即可送出；訂單不會立刻用已知收盤價成交，必須等下一個交易日的日線資料更新後，由每日台股排程或「檢查待成交委託」按鈕處理。詳細規則見 [模擬交易說明](docs/paper-trading.md)。

強化學習環境位於 `http://127.0.0.1:5000/rl-lab?symbol=2330`。目前可比較全現金、八成目標權重每日再平衡與二十日動能三個基準代理；成交固定採下一交易日開盤並計入與模擬交易相同的手續費、證交稅及滑價。這是 PPO／DQN 訓練前的防洩漏環境驗證，不是已訓練投資訊號。

按下「執行／復用樣本外研究」會建立 expanding walk-forward 實驗：504 筆訓練、63 筆驗證、63 筆測試，邊界各保留 5 個交易日隔離區。每折測試互不重疊，資料指紋、參數、績效與研究門檻會保存於 Research Database；相同資料快照不重複運算。

按下「訓練／復用 CPU 代理」會以固定亂數種子的 CEM 搜尋八個線性策略係數。市場特徵的 scaler 只用該折訓練資料估計，代理只以驗證資料選模，測試資料只在最後評估一次。這個純 NumPy 基準不需要 GPU，且每折權重、scaler、種子與績效都會保存。

PPO／DQN 使用統一 Agent Adapter 與相同 walk-forward 分段。PPO 輸出 0%～80% 連續持倉；DQN 從 0%、20%、40%、60%、80% 五檔選擇。PyTorch 是選用依賴，可用 `pip install .[rl]` 安裝；沒有安裝時其餘平台仍可正常使用。小型網路預設限制為一個 CPU 執行緒，降低 Windows MKL 不穩定與不必要的資源占用。

影子交易位於 `http://127.0.0.1:5000/shadow-trading`。選擇股票與 CPU／PPO／DQN 後，系統把最新 checkpoint 的目標持倉轉成獨立假想委託，等待下一根日線再以開盤加滑價評估。影子紀錄不修改模擬帳戶；Disabled Broker Adapter 會硬性拒絕所有外部 submit／cancel。

晉級審查與券商沙盒位於 `http://127.0.0.1:5000/promotions`。研究先執行 11 項門檻；只有全部通過才顯示待人工審查，且後端仍要求審查人與至少 10 個字元的理由。核准最多 30 天，資料或實驗更新後由每日台股排程重新驗證；本機沙盒只保存受理回條，不會觸及外部券商。詳細規則見 [晉級審查說明](docs/promotion-sandbox.md)。

模型治理與漂移監控位於 `http://127.0.0.1:5000/model-governance`。執行同步後，系統登錄每個市場與模型的最新實驗，使用 PSI 及新快照品質建立監控證據。只有全部品質門檻通過的挑戰者才顯示人工升級表單；冠軍出現嚴重漂移或監控資料不足時由每日排程自動降級。詳細規則見 [模型治理說明](docs/model-governance.md)。

資料品質與缺漏監控位於 `http://127.0.0.1:5000/data-quality`。可分別檢查台股與美股，查看日線與特徵覆蓋率、共同交易日缺漏、落後標的、時間因果、OHLCV 異常及台股／總經資料警告。每日完整研究會自動執行原始與完整兩段閘門；詳細規則見 [資料品質說明](docs/data-quality.md)。

財經記憶與研究問答位於 `http://127.0.0.1:5000/reports`。新聞與研究文件在本機切塊，使用 `BAAI/bge-small-zh-v1.5` 建立 512 維語意向量；有 CUDA 時自動使用 GPU，否則回退 CPU。`OPENAI_EMBEDDING_ENABLED` 保持關閉，只有回答問題時會把問題與最多 4 段本機檢索證據送到 OpenAI Responses API。設定、GPU 安裝與安全邊界見 [研究知識庫說明](docs/rag-knowledge.md)。

> 本專案位於中文路徑時建議使用一般安裝，不使用 editable (`-e`)；部分 Windows Python 3.11 環境會以 CP950 讀取 `.pth`，導致啟動失敗。修改程式後重新執行安裝即可。

## 原則

研究結果不代表未來績效。平台目標是建立可重現、可審計、能正確處理偏誤的研究流程，而非保證超越大盤。詳細架構與路線圖見 `docs/architecture.md`。
