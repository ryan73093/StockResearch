# 台股資料來源評估

更新：2026-07-18

## 建議結論

目前最適合本平台的組合是：

1. **FinMind 作為台股研究主資料源**：同一套介面涵蓋日線、法人、融資融券、借券、持股分級、財報、月營收、股利、期貨選擇權及新聞，最符合因子研究與機器學習需求。
2. **證交所、櫃買中心、期交所 OpenAPI 作為官方核對來源**：負責交易日、公告、市場統計與資料品質交叉檢查。
3. **富果行情 API 作為即時行情來源**：提供即時報價、分鐘 K、逐筆成交、五檔與 WebSocket；不作為完整長期歷史研究的唯一來源。
4. **TEJ 作為商業級升級**：若預算允許且要處理長歷史、調整股價、公司治理、完整財務及存活者偏差，優先購買 TEJ 授權。

## 比較

| 來源 | 適合用途 | 優點 | 主要限制 |
|---|---|---|---|
| FinMind | 日頻量化研究 | 台股資料種類集中、Python 介接容易 | 呼叫次數與部分會員資料限制；需自行做 point-in-time 稽核 |
| 富果行情 | 盤中監控與未來交易 | 即時、分鐘 K、逐筆、五檔、WebSocket | 進階方案付費；歷史 K 線主要為一年內 |
| TWSE／TPEx／TAIFEX | 官方核對 | 官方權威、免費 OpenAPI | 來源分散、格式不一，不是完整研究資料倉儲 |
| TEJ | 專業研究與商業部署 | 長歷史、調整資料、財報、法人、公司治理完整 | 商業授權與成本較高 |
| Yahoo Finance | 海外資產與備援行情 | 免費、全球市場方便 | 台股籌碼與基本面不足，不宜當台股唯一來源 |

## 實作順序

1. 建立 `FinMindProvider`，先加入日線、法人、融資融券、借券、月營收及三大財報。
2. 每個資料集保存 `event_time`、`available_time`、`ingested_at` 與來源版本。
3. 使用官方 OpenAPI 做筆數、交易日與公告核對。
4. 加入富果即時 Adapter，但與研究資料管線分離。
5. 未來導入 TEJ 時，只替換 Provider，不改 Feature、Model、Backtest 與 Portfolio 核心。

## 官方資料

- FinMind 資料清單：https://finmind.github.io/tutor/TaiwanMarket/DataList/
- FinMind 籌碼資料：https://finmind.github.io/tutor/TaiwanMarket/Chip/
- FinMind 基本面資料：https://finmind.github.io/v3/tutor/TaiwanMarket/Fundamental/
- 證交所 OpenAPI：https://openapi.twse.com.tw/
- 期交所 OpenAPI：https://openapi.taifex.com.tw/
- 富果行情文件：https://developer.fugle.tw/docs/data/intro/
- 富果方案：https://developer.fugle.tw/docs/pricing/
- TEJ API：https://api.tej.com.tw/
