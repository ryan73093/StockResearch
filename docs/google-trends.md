# Google Trends point-in-time 匯入

Google Trends 官方 API 目前仍是限量 alpha，平台不依賴非官方、可能隨時改版的網頁端點。現階段支援匯入 Google Trends 網頁「搜尋熱度隨時間變化」圖表下載的 CSV；取得 alpha 權限後，可在不改變儲存與研究語意的前提下補上線上 Provider。

Dashboard 位於 `/google-trends`，可直接上傳 CSV 與執行 As-of 查詢。FastAPI 提供 `GET /api/v1/google-trends` 與 `POST /api/v1/google-trends/imports`；正式環境的 POST 仍受 `API_WRITE_TOKEN` 保護。

## 為何不能把 CSV 期間直接當成當時已知

網頁資料是抽樣、依每次查詢的時間與地區正規化，再縮放到 0～100；後續重新下載可能得到不同歷史值。因此：

- `event_time` 是資料期間的起點（UTC）。
- `available_time` 與 `ingested_at` 是這份 CSV 實際被取得／匯入的時間。
- 同一期間的不同下載內容保留成修訂，不覆寫舊值。
- As-of 查詢只會看到當時已匯入的版本，避免回測偷看到日後重新抽樣或修訂的數字。
- 小時資料會依瀏覽器本地時區顯示，現階段拒絕直接匯入，避免把時間錯當 UTC。

## 使用方式

先在 Google Trends 網頁選擇關鍵字、地區與期間，下載 CSV，再執行：

```powershell
.\.venv\Scripts\python.exe .\scripts\import_google_trends_csv.py .\downloads\multiTimeline.csv --downloaded-at 2026-08-13T10:30:00+08:00
```

若 CSV 標題無法穩定識別關鍵字，可依欄位順序重複指定 `--entity-id`：

```powershell
.\.venv\Scripts\python.exe .\scripts\import_google_trends_csv.py .\downloads\multiTimeline.csv --entity-id 台積電 --entity-id TSMC
```

未提供 `--downloaded-at` 時採執行當下 UTC，不會把舊檔案誤標成過去已知。日、週、月、年 CSV 均可匯入；`<1` 保存為數值 `0.5` 並保留原始標籤。

Web 與 API 單次匯入限制為 5 MB。小時資料的 CSV 會被拒絕，因為 Google Trends 網頁依瀏覽器本地時區顯示，缺少明確 UTC offset 時不可安全合併到 point-in-time 資料。

## 研究限制

0～100 是相對搜尋熱度，不是搜尋次數。低流量字詞可能顯示 0，資料也含隱私保護造成的統計雜訊。跨查詢直接比較不同的 0～100 序列並不可靠；應固定查詢設定、保留地區與取得時間，並將 Trends 僅視為多個研究訊號之一。
