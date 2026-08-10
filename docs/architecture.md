# 系統架構與演進路線

## 架構決策

Flask 負責研究 Dashboard；FastAPI 負責資料、模型推論與未來券商整合；獨立 scheduler worker 執行每日研究流程。三者只呼叫 application use cases，不直接放置因子、策略或資料庫邏輯。正式環境以 PostgreSQL 保存 point-in-time 市場資料與研究 metadata，Redis 提供跨行程 token-safe 執行鎖；本機預設 SQLite 與行程鎖，讓開發環境可立即啟動。大量行情日後應以 Parquet/object storage 分層保存。

## 目錄責任

| 目錄 | 用途 |
|---|---|
| `config/` | 環境變數、資料源與模型設定；不保存 secret |
| `domain/` | 不依賴 framework 的 Entity、Value Object 與規則 |
| `application/` | Use case、port/interface、流程協調與依賴反轉 |
| `database/` | SQLAlchemy engine、model、migration、repository adapter |
| `data_sources/` | yfinance、FinMind、FRED、Polygon 等 connector |
| `data_pipeline/` | point-in-time ingest、驗證、版本、corporate action |
| `application/point_in_time_data.py` | 盤中、衍生品與替代資料目錄、權限、As-of 查詢及排程 use case |
| `data_sources/finmind_pit.py` | FinMind v4 分鐘／期貨／選擇權 Adapter；批次發布時間不冒充即時行情 |
| `data_sources/twse_pit.py` | 證交所零股最新快照 Adapter 與來源路由器 |
| `data_sources/mops_events.py` | TWSE／TPEx 每日重大訊息與 MOPS 單股最新法說會明細、簡報、影音 Adapter |
| `feature_engineering/` | 技術、籌碼、財務、情緒、總經、跨資產特徵 |
| `feature_engineering/intraday_derivatives.py` | 分鐘、零股、期貨、選擇權與波動率特徵；輸出不可變 Feature Revision |
| `factor/` | 因子定義、IC/Rank IC、衰退、正交化、條件化研究 |
| `regime/` | 牛熊、趨勢、波動、流動性與 macro regime 辨識 |
| `strategy/` | 動能、價值、均值回歸、事件策略與 ensemble |
| `labels/` | 多期間報酬、超額報酬、分類與風險調整 label |
| `backtest/` | point-in-time 回測、walk-forward、成本、偏誤防護 |
| `risk/` | VaR/CVaR、曝險、流動性、尾部與 stress test |
| `portfolio/` | MVO、Black-Litterman、Risk Parity、HRP、Kelly、CVaR |
| `machine_learning/` | sklearn、boosting model zoo、pipeline、calibration |
| `deep_learning/` | sequence、Transformer、TFT、N-BEATS、GNN |
| `reinforcement_learning/` | PPO/DQN 環境、reward、約束、離線評估 |
| `optimizer/` | Optuna、資源預算、實驗去重、promotion gate |
| `explainability/` | SHAP、Permutation Importance、模型卡、決策理由 |
| `research/` | 實驗、artifact、lineage、regime 最佳策略查詢 |
| `rag/` | 策略、報告、回測與模型說明的索引、檢索、引用 |
| `report/` | 每日市場、選股、風險、組合與模型監控報告 |
| `dashboard/` | Flask UI；不含商業邏輯 |
| `api/` | FastAPI router/schema；供資料、推論與外部整合 |
| `scheduler/` | Prefect（日常流程）與 Airflow（大型 DAG）adapter |
| `broker/` | 券商 API、order state machine、冪等、reconciliation |
| `monitoring/` | 資料品質、drift、績效、風險限制與告警 |
| `utils/` | 小型通用工具，禁止成為無邊界雜物桶 |
| `tests/` | unit、integration、contract、backtest regression |
| `docs/` | ADR、資料字典、runbook、模型卡、研究規範 |

能力會在相應階段才建立 package，避免大量空檔案造成「看似完整、其實不可用」。

## 實作順序

1. **Foundation（目前）**：設定、DI、DB、Flask/FastAPI、測試、健康檢查。
2. **Point-in-time Data Platform（進行中）**：台股/美股日線、benchmark、品質檢查、資料庫 Universe、台美分市場排程與 Audit Log 已完成；下一步加入 corporate actions 與正式交易所日曆。
3. **Feature + Label Store（已完成 v3.7）**：版本化技術、台股籌碼／基本面、跨資產、FRED 與 26 項分鐘／衍生品／零股特徵，4 個延遲可用標籤、lineage timestamp、輸入指紋、不可變修訂、冪等 materialization 與排程串接。
4. **Regime + Factor Engine（已完成 v1）**：benchmark point-in-time regime、IC、Rank IC、ICIR、excess-return spread、turnover、decay、分 regime 統計與小股票池 promotion gate；下一版擴大 survivorship-safe universe 並加入 Newey-West、FDR 與因子正交化。
5. **Bias-safe Backtest（已完成 v1）**：rolling walk-forward、next-open execution、commission/slippage、volatility sizing、stop-loss/take-profit、OOS-only metrics、Fold/Trade/Equity lineage 與嚴格 promotion gate；歷史 universe membership、稅、流動性與 market impact 仍在 gate 內明確阻擋。
6. **Strategy Ensemble + Portfolio + Risk（已完成 v1）**：三策略動態集成；九種受約束 Portfolio 配置、rolling OOS、成本、曝險與壓力測試均已保存。因 OOS 長度、survivorship-safe universe 與上游 gate 尚未合格，目前全部維持 `RESEARCH`。
7. **Model Zoo + AutoML + XAI**：MLflow/Optuna、SHAP、drift、champion/challenger。
8. **RAG Copilot + Daily Report（已完成 v2）**：研究文件依內容雜湊切塊，保存向量契約、混合檢索、`[來源N]` 引用與問答稽核；本機 CPU 為預設，可透過 DI 切換 OpenAI Embeddings／Responses，且不允許無證據推薦。
9. **Paper Trading → Broker**：先 shadow/paper、限額、reconciliation，再考慮實盤。

## 不可妥協的研究規範

- 資料同時保存 `event_time`、`available_time`、`ingested_at`；回測只能讀當時已可得資料。行情遵守事件≤公開≤匯入；預先公告的排程事件允許公開≤事件，但仍必須公開≤匯入。
- universe 必須 point-in-time，保存下市與失敗公司，避免 survivorship bias。
- 在歷史 universe membership 尚未完成前，單資產回測只能維持 `RESEARCH`，不得被解讀為 survivorship-safe 的選股證據。
- train/validation/test 依時間切割；轉換器與特徵選擇只 fit 在 training window。
- Feature 的 `available_time` 取當期輸入資料的可用時間；forward label 的 `available_time` 取 horizon 終點，訓練樣本選取不得只依 `event_time`。
- 超參數與策略搜尋須納入 multiple testing；保存完整失敗實驗，不只展示最佳結果。
- Factor ranking 只代表研究優先級；未滿 30 檔的橫斷面一律 `SMALL_UNIVERSE`，不得送入策略或組合層。
- 績效必須含費用、稅、滑價、成交量限制與無法成交情境。
- 「長期超越大盤」是研究假設，不是工程保證；promotion gate 使用 benchmark-relative、risk-adjusted、out-of-sample 指標。
