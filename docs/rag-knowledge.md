# 研究知識庫與可追溯 RAG v3.5

## 設計理念

研究助手不能只輸出看似合理的文字。系統先把每日報告、因子、回測、模型與投資組合研究保存為版本化知識文件，再切成可追溯片段；檢索、回答與拒答都保存稽核紀錄。沒有證據時不產生結論，也不保證報酬。

## 目前正式資料流

- 新聞原文、研究文件、切塊與向量都保存在本機 SQLite。
- `BAAI/bge-small-zh-v1.5` 在本機產生 512 維中文語意向量；不呼叫 OpenAI Embeddings API。
- 有 CUDA 時自動使用 GPU，否則回退 CPU；模型第一次下載後以離線模式載入。
- 檢索分數為 70% 向量相似度加 30% 詞彙重疊。
- 每份文件最多取一個切塊，避免同一文件壟斷所有來源。
- 只有使用者提問時，才把問題與最多 4 段本機找到的文字證據傳給 OpenAI Responses API。
- 回答必須使用 `[來源1]` 格式並能點回來源；API 失敗或引用無效時自動降級為本機證據摘要。

## 環境設定

在 `.env` 設定：

```text
OPENAI_ENABLED=true
OPENAI_API_KEY=你的金鑰
OPENAI_RESPONSE_MODEL=gpt-5.6-luna
OPENAI_EMBEDDING_ENABLED=false
LOCAL_EMBEDDING_BACKEND=sentence-transformer
LOCAL_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
LOCAL_EMBEDDING_DEVICE=auto
LOCAL_EMBEDDING_BATCH_SIZE=32
LOCAL_EMBEDDING_OFFLINE=true
```

金鑰只從環境變數讀取，不保存到資料庫或頁面。`OPENAI_ENABLED` 只控制回答；`OPENAI_EMBEDDING_ENABLED=false` 保證新聞入庫、向量化與相似度搜尋不把資料送到 embedding API。切換本機模型後，索引會依模型名稱與維度重建，不混用不同向量空間。

## Windows GPU 安裝

GTX 1060 使用官方 CUDA 11.8 wheel：

```powershell
.\.venv\Scripts\python.exe -m pip install ".[rag-gpu]"
.\.venv\Scripts\python.exe -m pip install --force-reinstall torch==2.7.1 --index-url https://download.pytorch.org/whl/cu118
```

用下列方式確認不是只有安裝、而是真的能運算：

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

## 使用方式

1. 在「財經記憶與研究問答」更新新聞，系統會同時產生台股報告並同步本機向量。
2. 也可執行 `python scripts/build_financial_memory.py` 做一次完整重建；內容沒有改變時不重複建立向量。
3. 輸入「為什麼今天沒有候選？」或「哪些因子失效？」。
4. 檢查回答的來源、相關度、文件鍵、切塊編號與原始研究頁。
5. 證據不足時先補研究資料，不應要求模型猜測。

## API

- `GET /api/v1/reports?question=...`：取得報告、索引狀態與問答結果。
- `POST /api/v1/reports/generate/{market}`：產生報告並同步索引。
- `POST /api/v1/reports/reindex`：冪等同步全部研究文件。

## 稽核欄位與單位

- 向量維度：維。
- 文件與切塊：份、個。
- 問答紀錄：筆。
- 處理延遲：毫秒。
- 綜合相關度：百分比。
- 每筆稽核保存問題、檢索來源、分數、回答模式、模型、拒答原因與建立時間。

## 安全邊界

- RAG 只引用平台已保存的研究證據，不繞過資料品質、樣本外或晉級門檻。
- 外部 LLM 不能直接呼叫券商 Adapter。
- 真實下單仍為硬性關閉。
- 啟用外部 API 前應確認資料傳輸政策與成本上限。

## 產業資訊與影音摘要怎麼成為決策記憶

LLM 的「記憶」不得只存在對話內容。產業新聞、法說會、研究摘要與財經影片應保存成外部、版本化的 RAG 記憶，每筆至少包含：

- `event_time`：事件實際發生時間。
- `available_time`：市場當時何時能取得，回測只能從此刻之後使用。
- `ingested_at`：平台匯入時間。
- 來源網址、語言、國家／市場、公司與產業標籤。
- 內容雜湊、修訂版號、有效期限與新鮮度。
- 結構化立場、信心、影響期間、催化劑與風險；原文切塊保留引用。

美國與日本財經專家影片可以作為「弱情緒特徵」，但不能直接產生委託。必須去除重複觀點並保存影片發布時間與逐字稿可用時間，再用 point-in-time 測試其對 1／5／20 日超額報酬、波動與成交量是否有增量解釋力。比較時至少要有「不含影音」基準；若加入後樣本外結果沒有穩定改善，就不接入正式決策。

雲端模式建議把文件、切塊、結構化事件與查詢稽核保存於 PostgreSQL，向量使用 `pgvector`；大型影片與完整逐字稿檔案放物件儲存，只在資料庫保存位置、雜湊與權限。LLM 只負責檢索與萃取，最終下單仍由可重現的統計／機器學習模型、風險限制與晉級閘門決定。
