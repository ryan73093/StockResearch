# 盤中、衍生品與替代資料（v3.6）

## 設計目的

歷史資料今天可以下載，不代表回測當時已經看得到。此模組把三個時間分開：

- `event_time`：成交、價格或事件實際發生的時間。
- `available_time`：來源正式提供該版本、模型才允許使用的時間。
- `ingested_at`：平台實際下載並保存的時間。

查詢 `as_of=T` 時先排除 `available_time > T` 的版本，再於相同事件與邏輯紀錄鍵中選取當時最新修訂。來源事後更正不會覆寫舊版本。

## 已介接來源

| 資料 | 來源 | 權限／限制 | 單位範例 |
|---|---|---|---|
| 台股一分鐘 K | FinMind `TaiwanStockKBar` | Sponsor；2019 起，單次一天／本平台單次最多 31 天 | 元／股、股 |
| 期貨日資料 | FinMind `TaiwanFuturesDaily` | 依官方方案 | 點／元、口 |
| 選擇權日資料 | FinMind `TaiwanOptionDaily` | 依官方方案 | 履約價、點／元、口 |
| 期貨／選擇權逐筆 | FinMind Tick datasets | Sponsor Pro 整日物件檔；歷史範圍依官方文件 | 點／元、口 |
| 臺指選擇權 VIX | FinMind `TaiwanOptionVix` | Backer 或 Sponsor | %（波動率指數） |
| 集中市場零股行情 | 證交所 OpenAPI `TWT53U` | 公開的目前快照；不是歷史 API | 元、元／股、股、筆 |

官方參考：[FinMind 技術面](https://finmind.github.io/tutor/TaiwanMarket/Technical/)、[FinMind 衍生性金融商品](https://finmind.github.io/tutor/TaiwanMarket/Derivative/)、[證交所 OpenAPI](https://openapi.twse.com.tw/)、[期交所 OpenAPI](https://openapi.taifex.com.tw/)。

FinMind 歷史分鐘與逐筆下載是盤後研究資料。本平台依官方更新時間設定 `available_time`，不把每根分鐘 K 的時間誤當成當時已能從批次 API 取得。要做真正盤中決策，必須另接即時串流來源並保存接收時間。

## 網頁操作

1. 開啟 `/point-in-time-data`。
2. 選擇資料集與代號，例如 `tw_odd_lot_daily / 2330`、`tw_futures_daily / TX`、`tw_options_daily / TXO`。
3. 選擇日期後按「下載並保存」。
4. 在右側輸入歷史時間執行 As-of 查詢。
5. 查看事件、來源可用、匯入時間與每個欄位單位。

證交所零股 API 只提供目前快照，因此歷史資料必須由平台每日累積。若在週末下載，紀錄代表「週末當下來源仍提供的最新快照」，不會虛構成前一交易日當時已保存。

## FastAPI

- `GET /api/v1/point-in-time/datasets`
- `GET /api/v1/point-in-time/observations?dataset_key=tw_futures_daily&entity_id=TX&as_of=...`
- `POST /api/v1/point-in-time/ingestions`

寫入端點在正式環境仍受 `API_WRITE_TOKEN` 保護。

## 自動排程

`.env` 範例：

```dotenv
FINMIND_TOKEN=
PIT_AUTO_INGESTION_ENABLED=false
PIT_DEFAULT_ENTITIES=tw_futures_daily:TX,tw_options_daily:TXO
PIT_LOOKBACK_DAYS=7
```

先手動確認來源權限與回傳內容，再將 `PIT_AUTO_INGESTION_ENABLED` 改成 `true`。自動工作會併入台股每日研究流程；全部來源失敗會讓該次排程失敗並保留原因，部分成功則保留成功資料及失敗清單。

## 尚未完成

- MOPS 法說會事件與文件版本。
- Google Trends 取樣／正規化修訂。
- Reddit、Twitter 等社群授權與合規資料源。
- 分鐘特徵、期權 IV／Greeks／波動率曲面、期貨基差與零股流動性因子。
- 大量逐筆資料的 Parquet/object storage 分層；目前 SQLite 適合小規模研究驗證。
