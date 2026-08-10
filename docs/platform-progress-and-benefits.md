# AI Quant Platform：目前進度、效益與待開發項目

更新版本：v3.8.0

## 已開發且目前可使用

| 模組 | 已完成能力 | 實際效益 | 使用入口 |
|---|---|---|---|
| 基礎架構 | Clean Architecture、依賴注入、Flask、FastAPI、SQLAlchemy、設定、日誌、測試 | 新資料源、策略與模型可替換，不必重寫整個網站 | `/`、`/api/v1/health` |
| 行情與 Universe | 台／美日線、基準、股票池、歷史 membership、排程稽核 | 研究標的與基準一致，降低存活者偏差與手動更新成本 | `/data`、`/universe` |
| 台股研究資料 | 法人、融資融券、借券、估值、月營收、季財報衍生特徵 | 不只看技術線圖，可分析籌碼、估值與財務品質 | `/taiwan-data` |
| Point-in-time 資料 | 事件、可用、匯入時間、不可變修訂、As-of 查詢 | 避免回測偷看尚未公告或事後修正的資料 | `/point-in-time-data` |
| 盤中與衍生品 | 分鐘 K、期貨、選擇權、VIX、零股快照與 26 項特徵 | 研究盤中量價、期權情緒、未平倉與零股流動性 | `/intraday-features` |
| Feature／Label Store | 版本化特徵、未來 5／20 日報酬、方向、超額報酬標籤 | 讓不同模型使用同一份可重現資料，降低 leakage | `/features` |
| Regime／Factor | 牛熊盤整、高低波動、IC、Rank IC、ICIR、衰退與分狀態統計 | 判斷因子在哪種市場有效，不固定使用單一策略 | `/factors` |
| Bias-safe Backtest | Walk-forward、樣本外、次日開盤、成本、滑價、停損停利與完整交易證據 | 把漂亮但不可信的回測與可驗證結果分開 | `/backtests` |
| Strategy Ensemble | 動能、均值回歸、狀態策略與動態權重 | 降低依賴單一策略，依近期樣本外結果調整比重 | `/ensembles` |
| Model Zoo／AutoML | 基準模型、Random Forest、SVM、可選 Boosting、Optuna、實驗去重 | 自動找參數並保存失敗實驗，不重複研究同一快照 | `/models` |
| Explainable AI／治理 | 排列重要性、局部反事實、模型分歧、冠軍／挑戰者、PSI 漂移 | 看懂推薦原因，模型失效時可降級，而非盲信黑盒 | `/explain`、`/model-governance` |
| Portfolio／Risk | 九種配置、VaR、CVaR、Beta、曝險、流動性與壓力測試 | 將選股轉成受限制部位，避免單一股票或產業過度集中 | `/portfolios` |
| 新聞、法說會與事件 | 新聞情緒、上市／上櫃法說會公告、MOPS 簡報與影音、事件修訂 | 研究管理層展望與事件衝擊，並保存可追溯官方證據 | `/news`、`/corporate-events` |
| RAG 每日報告 | 研究文件、向量／詞彙混合檢索、引用、拒答、稽核、可選 OpenAI | 可詢問「為何推薦／風險在哪」，回答受平台證據約束 | `/reports` |
| 自動排程 | 台／美每日流程、重試、鎖、Email Adapter、資料／特徵／法說會更新 | 減少人工重跑，保留每次成功或失敗紀錄 | `/automation` |
| 模擬、RL 與影子交易 | 模擬帳戶、成本、PPO／DQN 研究介面、影子決策、晉級閘門、安全沙盒 | 在不動用真實資金下驗證執行、部位與代理穩定性 | `/paper-trading`、`/rl-lab`、`/shadow-trading`、`/promotions` |

## 已有基礎，但仍需擴充

| 項目 | 現況 | 還缺什麼 |
|---|---|---|
| 台股分鐘／逐筆資料 | Adapter 與特徵完成 | FinMind 對應會員權限、長期累積與正式分鐘級回測 |
| 法說會歷史 | 今日公告與單股最新明細可累積 | 啟用日前的可信 vintage 歷史、簡報全文抽取與事件研究引擎 |
| 深度學習 | PyTorch、PPO、DQN 與統一介面已有 | LSTM、GRU、Transformer、TFT、Informer、N-BEATS、GNN 的公平樣本外實作 |
| Explainable AI | 排列重要性與反事實可用 | 固定樹模型版本後的正式 SHAP、文字模型歸因 |
| AutoML／實驗治理 | Optuna 與資料指紋可用 | MLflow 正式服務、模型 Artifact 儲存與跨主機執行 |
| RAG | 本機向量與可選 OpenAI Adapter 可用 | 實際 API 金鑰驗收、法說會／簡報全文索引、權限隔離 |
| 正式部署 | PostgreSQL／Redis／Docker 設計與設定檔已有 | 目前電腦未裝 Docker；發布前才做正式容器、備份、HTTPS 與監控驗收 |

## 尚未開發或未完成驗收

1. Google Trends 的版本化搜尋熱度與關鍵字管理。
2. Reddit／社群情緒；Twitter/X 需先確認授權與 API 成本，不使用不穩定爬蟲冒充正式資料源。
3. 法說會事件的 1／5／20 日異常報酬、成交量與法人流向研究引擎。
4. 選擇權完整波動率曲面、Skew、期限結構與 Greeks。
5. 完整 ALFRED 總經 vintage，以及付費來源交叉驗證。
6. 更完整的深度學習 Model Zoo、正式 SHAP 與 MLflow。
7. Google 帳號正式登入、多人資料隔離與發布環境驗收。
8. 正式券商 API、對帳、熔斷與小資金上線；目前真實交易仍硬性關閉。

## 對投資決策真正能帶來什麼

目前平台能提升的是研究品質與決策紀律：自動收資料、避免時間洩漏、比較策略相對大盤的樣本外績效、解釋模型、控制部位與風險、保存每天的證據。它不能保證報酬，也不應讓使用者在候選門檻未通過時「無腦買」。真正能否超越 0050／SPY，必須由更長的真實時間、成本後影子交易及持續樣本外結果證明。
