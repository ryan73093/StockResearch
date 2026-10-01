# 盤後決策台 — 開發歷程

每輪交付一筆，最新在最上方。記錄目標、做法、測試結果、證據、commit 與回復方式。v3.9 以前的研究平台版本紀錄見 [`docs/archive/module-status-v2.7-v3.9.md`](docs/archive/module-status-v2.7-v3.9.md)。

## 2026-10-01 — 測試誤寫正式資料庫：原因與防護

- 發現（14:3x）：正式資料庫 `research_universe` 在 14:02:01 多了 4 筆（id 555–558：00713.TW、00919.TW、00679B.TWO、00687B.TWO），但加入這四檔的 `e0c85d4` 尚未部署；時間對應當時的完整測試。
- 原因：`quant_platform/api/app.py` 在模組載入時執行 `app = create_api()`。測試只要 `from quant_platform.api.app import create_api`，就會以預設設定（讀專案根目錄 `.env`，`DATABASE_URL` 指向正式資料庫）建立容器：`create_schema()`（含累加式遷移）與 `ensure_default_universe()` 都作用在正式資料庫，`.env` 的值也被載入測試程序。這從初始版本就存在；以往遷移與預設股票池都已是最新，所以沒有留下痕跡。測試的外部呼叫都用假物件，沒有以真實金鑰對外連線。
- 影響：只有這 4 筆股票池資料（部署後本來就會寫入的同樣內容），沒有刪改其他資料；台股流程 #3608 特徵建置因四檔尚無日線而「部分完成」，下一次台股流程會補抓。資料保留不回復。
- 修正：`api/app.py` 改為第一次存取 `app` 時才建立（模組 `__getattr__`；`uvicorn quant_platform.api.app:app` 與 `compose.yaml` 照常可用）；`quant-api` 的 `main()` 只建一次容器並直接傳入 app（原本建兩次）。新增 `tests/conftest.py`：測試期間 `Database` 拒絕開啟 `instance/quant_platform.db`。`AGENTS.md` 測試規則補上一條。
- 測試：`test_test_isolation.py`（防護生效；子程序 import 模組不建立資料庫、存取 `app` 才建立）；全部 383 通過、1 略過。測試後正式股票池仍為 id 558 為止、啟用中台股 536 檔，沒有新寫入。
- 回復：`git revert`（防護只影響測試）。

## 2026-10-01 — 今日頁一鍵回報成交（需求 §4）

- 今日頁委託單每筆加「回報成交」：開啟 `/holdings?symbol=…&side=…&shares=…&price=…#trade`，持倉頁的成交表單帶入建議的代號、買賣、股數與限價並醒目標示，提醒改成實際成交與對帳單手續費再記錄。使用手冊投入日步驟同步。
- 測試：今日頁連結、持倉頁預填與提示。

## 2026-10-01 — S7-W06 情境模擬第一版

- 持倉頁「如果台股大跌」：股票型 ETF 跌 20%、30%、55%（接近 2008 年金融海嘯）時，帳戶剩多少、少多少、占總資產比例；債券 ETF 與現金不變（簡化假設，畫面註明）。超過計畫可承受回撤的情境標「超過可承受回撤」。`actual_account.stress_scenarios`；使用手冊同步。
- 測試：手算（0050 10,000、00679B 5,000、現金 5,000 → 跌 55% 少 5,500、占 27.5%，超過 25% 的容忍）。

## 2026-10-01 — S6-W04 每週研究報告

- `research/weekly.py`：一週（週一～週日，台北）的研究摘要——AI 輪次（含異常次數）與假設、AI 新增與被拒絕的設定（主要拒絕原因前三名）、本週新試驗與通過開發期門檻的數量、最好的新設定、開發期目前資料版本與累計試驗數、最佳 DSR、PBO、晉級事件、前向模擬中的候選、模型費用、最近的「下一步構想」與白話結論。研究頁最上方新增「本週研究報告」；worker 每週日 23:30 存檔（`weekly_research_report`）。需求 §8 更新；路線圖 S6 全部完成。
- 測試：週界線、只計入本週（日誌、試驗、費用）、拒絕原因分類、結論文字、存檔名稱；全部 380 通過、1 略過。

## 2026-10-01 — S6-W02 實際帳戶回撤與每月底對照；worker 紀錄

- `ActualAccountService`：新增 `bar_history`（每日收盤，容器由 `market_bars` 提供，同一天多個來源只取一筆），依入出金、成交與每日收盤逐日重建實際帳戶市值（休市日的紀錄算在下一個交易日；沒有收盤時用最近成交價），以單位淨值算最大回撤（排除新投入的錢）。持倉頁「與定期定額比較」加「你的最大回撤」，並新增收合的「每月底對照」：最近 12 個月每月最後一個交易日的實際帳戶、定期定額影子帳戶與差額。
- worker 加上 logging 設定（INFO；APScheduler 每分鐘的例行訊息只留 WARNING）——之前 `instance\worker.stderr.log` 一直是空的，今天的行情變慢只能從資料庫推斷。
- 測試：回撤與月底手算（9,960.20 → 7,980.20，−19.88%；9 月底 10,950.20 對影子 9,960.20，差 990）。

## 2026-10-01 — S6-W03 計畫變更前的影響摘要

- 已有計畫時，計畫頁的按鈕改為「預覽變更」：上方出現「確認變更（將存成第 N 版）」，逐項列出原本 → 改成（每月投入、薪資日、策略、券商、可承受回撤、年數、目標），以及影響：下次薪資入帳日、每年投入金額、兩個策略的歷史全期間 XIRR／最大回撤／3 年勝率、券商費率改用哪一家。按「確認儲存」才會存成新版本；表單錯誤一樣在頁首說明、不存檔。第一次建立計畫直接「儲存為第 1 版」。使用手冊同步。
- 測試：預覽不存檔且列出變更與影響、確認後產生第 2 版、預覽時的錯誤；相關 23 項通過。

## 2026-10-01 — S1-W05 第一天量測：台股行情改為等待官方收盤

- 量測（10/01，S1-W05 第一天）：證交所收盤表與 Yahoo 0050 都在 13:51（收盤後 21 分鐘）第一次取得；櫃買與盤後零股到 14:12 尚未出現。
- 發現：13:50:00 的台股流程開跑時官方收盤表還沒公布，程式不等待，536 檔全部改由 Yahoo 逐檔抓；當天 yfinance 又一直回空資料，每檔多三次重試與 3 秒等待，14:06 才完成 247 檔（前兩天由官方表一次寫入 531 檔，只要幾秒）。0050 在 13:50:04 第一個寫入，所以依計畫的今日建議不受影響，但後續研究步驟大幅延後。
- 修正：
  - `DailyMarketDataPipeline`：台股當天官方收盤還沒公布時，每 30 秒重試一次，最多等 `TW_OFFICIAL_CLOSE_WAIT_MINUTES`（預設 10 分鐘），拿到後一次寫入；等不到才改走 Yahoo。資料庫已有當天官方資料（重跑）時不等待。
  - 官方快照不再把「還沒公布」的空結果快取 15 分鐘。
  - Yahoo：yfinance 連續 3 檔回空資料後，30 分鐘內直接改用 chart API，不再每檔重試。
- 測試：等到官方收盤（3 次輪詢）、等不到改走 Yahoo、重跑不等、yfinance 斷路器；全部 376 通過、1 略過。
- 回復：`.env` 設 `TW_OFFICIAL_CLOSE_WAIT_MINUTES=0` 即恢復不等待；或 `git revert`。

## 2026-10-01 — S6-W06 使用教學、今日頁異常提示、補齊 ETF 股票池

- 使用者要求（10/01 13:5x）：「幫我建好系統教學，其他請繼續開發」。
- S6-W05 首頁異常提示：今日頁最上方一行（連到系統頁），只在有問題時出現，最多三項：資料庫備份逾期、某個排程今天最近一次執行失敗、交易日 15:00 後台股行情仍未更新、LINE 最近一則失敗。全新安裝還沒有備份不算問題。
- 股票池補齊：研究目錄另外四檔 ETF（00713、00919、00679B.TWO、00687B.TWO）原本不在股票池，沒有每日行情——選「股債 80/20」的計畫會一直缺 00679B 收盤，持有這幾檔也無法估值。加入內建清單（名稱、類別），服務啟動時自動登記，下一次台股流程開始抓 2020 年起的日線。
- `docs/user-guide.md`（唯一內容來源）：系統是什麼與網址；第一次使用（建立計畫、記錄帳戶現況的兩種記法——完整補記或從今天開始記，說明買進用的錢也要記成入金、設定 LINE）；每個交易日的時間表（13:30 收盤 → 13:45 建議 → 14:30 撮合 → 回報成交）、投入日四步驟、限價怎麼來、沒成交怎麼辦；每月例行（入金、股利、對帳單、月退不計入）；各頁說明；名詞解釋（盤後零股、限價、bps、手續費、證交稅、定期定額基準、影子帳戶、XIRR、最大回撤、滾動勝率、超額、DSR、PBO、三段期間、前向模擬）；常見問題；安全與限制。
- 網站：`/help` 教學頁（左側或上方章節目錄，捲動時標示目前章節；`/guide` 已被舊版研究平台的「使用指南」使用，保留不動）；每頁側欄（電腦、iPad）與手機頂端列加問號按鈕；系統頁「專案資訊」第一個分頁改為「使用教學」。今日頁「開始使用」清單：建立投資計畫、記錄入金、回報已持有的 ETF（選用）、設定 LINE（選用），依資料庫實際狀態打勾；還沒建立計畫時放在最上方，有計畫但還沒入金時放在側欄，兩者都完成就不再出現。
- 需求 §9 補上教學與清單；路線圖新增 S6-W06。
- 測試：`test_user_guide.py` 3 項（教學頁內嵌六個章節、五個主頁面都有教學連結、舊版 `/guide` 不受影響、系統頁第一個分頁；排程失敗時出現一行提示；清單隨計畫與入金打勾並消失）；全部 372 通過、1 略過。

## 2026-10-01 — S6-W01 今日頁行動卡

- 盤點：倒數、複製委託、為什麼、我的計畫、資料狀態在 S5-W02 已有。補上的部分：
  - 盤後零股時段三段倒數（交易日都顯示）：13:40 前「13:40 開始收單（還有 X 分）」、13:40–14:30「收單中・距 14:30 截止 X 分」、之後「今日盤後零股已截止」。有計畫時只在投入、再平衡、等收盤、缺資料四種狀態出現；沒有計畫的舊版畫面也套用。
  - 「為什麼」只列前三點，其餘收在「查看其他依據」。
  - 「需要留意」卡只在有影響時出現：資料品質警告（含今日排除檔數）、今日收盤尚未齊全。
  - 委託說明改為「限價＝收盤價加滑價後進位…依對帳單回報成交」（原文仍寫 10 bps）。
- 驗收（`af61096`，12:05 部署）：正式今日頁（尚無計畫）12:06 顯示「13:40 開始收單（還有 1 小時 34 分）」，375 px 無溢出；有計畫的投入日版面以樣本資料渲染後放進瀏覽器分頁檢查：375 px 下行動卡、倒數、委託單、需要留意、為什麼、我的計畫都在 16 px 邊界內，沒有溢出元素（正式環境未建立任何計畫）。
- 測試：`test_investment_plan.py` 新增今日頁版面 1 項（倒數屬性、複製文字、原因收合、品質警告、缺收盤提示）；全部 369 通過、1 略過。
- 回復：`git revert` 後部署。

## 2026-10-01 — S4-W06 晉級流程

- 目標：候選從開發期走到使用者核准的固定關卡，每步有證據；未核准的策略不會出現在計畫頁與今日頁。
- `research/promotion.py`：
  - 關卡（路線圖 S4 門檻）：開發期——目前資料版本、滾動 3 年與 5 年勝率 ≥ 60% 且中位超額 > 0（期間太短沒有 5 年視窗時只看 3 年）、最大回撤不比定期定額深超過 5 個百分點、DSR ≥ 0.95、PBO ≤ 0.2（統計檔須是同一資料版本）。驗證期——同樣的視窗與回撤門檻，另跑成本加倍與晚一天執行兩個試驗，3 年勝率須 ≥ 50%。保留期——只評估一次（沿用 `periods.check_gate`），同樣門檻。前向模擬——通過保留期後寫入 `forward/tracked/`，從隔天起至少 40 個交易日。核准——只有使用者能在研究頁核准或撤銷。
  - 晉級紀錄 `instance/research/promotions.jsonl`：只追加、每筆帶前一筆雜湊，`verify()` 偵測修改與刪除；每筆附試驗編號、報告檔、未通過原因與設定檔內容。每個設定只審一次。
  - `strategy_catalog()`：內建基準＋已核准的設定（代號 `approved:<雜湊前 16 碼>`）；計畫頁的策略選項、計畫表單驗證、今日建議都改用它；策略被撤銷後今日頁提示「已不可用，請到計畫頁改選」。
  - 試驗報告新增 `spec`（設定檔內容），試驗紀錄新增 `benchmark_max_drawdown`、`execution_lag`、`cost_scale`，讓後續關卡不必依賴原始程式。
- 執行：每晚 AI 研究員之後自動推進（worker `research_agent` 工作）；CLI `python -m quant_platform.research promote`。研究頁新增「晉級流程」卡：沒有候選時說明原因；有候選時列出關卡、結果與未通過原因，可核准或撤銷（需勾選「我已看過證據」）。
- 正式資料實測（11:58）：開發期 68 個設定中 5 個通過 3 年與 5 年視窗門檻，0 個通過全部關卡（最佳 DSR 0.42），沒有產生任何新試驗或晉級紀錄。
- 測試：`test_research_promotion.py` 6 項（門檻手算、統計關卡需同資料版本、驗證期未通過即停且只審一次、全流程到核准與撤銷且計畫表單可選、紀錄防竄改、研究頁說明與核准防呆）；全部 368 通過、1 略過。
- 部署（`4215713`，12:01）：外網研究頁「晉級流程」顯示「目前沒有候選…68 個設定中 5 個通過 3 年與 5 年滾動視窗門檻；但最佳 DSR 0.42（時點：每月 16 日全數買進 0050）未達 0.95」；本機研究頁與計畫頁 375 px 無水平捲動；計畫頁策略仍只有 4 個內建基準。
- 回復：晉級紀錄只追加；撤銷以新紀錄表示；`git revert` 後部署即回到只有內建基準可選。

## 2026-10-01 — 使用者決定：月退不計入、AI 研究員預算、LINE 摘要保留

- 使用者回覆（11:4x）：LINE 目前在遠端無法設定，稍後處理（S5-W07 改為 blocked）；折扣是月退，退佣「基本上算 bonus、太難計算，以對帳單的損益為主」；AI 研究員每月 US$3；非投入日的「今天不需操作」可保留。
- 券商設定檔（`research/costs.py`）：國泰、台新改為成交時收取的原價（0.1425%、零股每筆最低 1 元，不打折），退佣視為額外收入不計入；兩者標示為已定（不再顯示「待確認」）。今日建議的「為什麼」寫「手續費以○○估算（原價、每筆最低 1 元；月退的退佣不計入），實際以對帳單為準」。實際帳戶照舊以使用者回報的手續費（對帳單）入帳。研究預設仍是保守估計（最低 20 元）。
- 需求 §5、§8、§11、§14 與路線圖、交接同步；§14 只剩券商對帳單 CSV 格式。
- 測試：券商手算改為原價（9,919.80 元 → 手續費 14 元；100 元 → 最低 1 元）；全部 362 通過、1 略過。
- 部署（`293440b`，11:52）：本機與外網計畫頁的國泰、台新顯示「不打折、每筆最低 1 元・折讓：月退（不計入）」與對帳單為準的說明，無「待確認」；375 px 無水平捲動。

## 2026-10-01 — S4-W04 AI 研究員（gpt-6-luna）

- 使用者決定（10/01）：比照 VectorDB 專案使用 `gpt-6-luna`。參考 VectorDB `gpt_client.py`、`ai_costs.py`（唯讀）：Responses API 以 `urllib` 呼叫、`store: false`、不自動重試、呼叫前檢查每月預算硬上限、每次呼叫記錄 token 與當時單價（`gpt-6-luna` 每百萬 token：輸入 0.125、快取輸入 0.0125、輸出 0.50 美元）。
- `research/agent/llm.py`：金鑰取 `OPENAI_API_KEY` 或 `OPENAI_KEY_ENV_FILE` 指向的 dotenv（只讀其中的 `OPENAI_API_KEY`）；JSON 物件輸出（輸入沒有「json」字樣時自動補一句，否則 OpenAI 回 400）；沒有單價的模型拒絕呼叫（無法控管預算）；失敗的呼叫也記帳；HTTP 錯誤只帶 OpenAI 的錯誤訊息（不含金鑰）。費用帳本 `instance/research/agent/usage.jsonl`。
- `research/agent/researcher.py`：每輪把研究題目、規範、成本與門檻、StrategySpec JSON Schema、開發期有 3 年以上資料的標的（0050、0056、006208；訊號可用 TAIEX）、目前資料版本的開發期試驗結果、多重檢定次數與最近 5 輪日誌交給模型；模型回傳對上一輪的分析、假設、理由、設定檔與 v1 表達不了的研究構想。設定檔逐一以 pydantic 驗證，拒絕不合 Schema、開發期資料不足、與已測規則相同（以排除名稱與說明後的規則雜湊判斷，涵蓋內建基準、規則批次與過去 AI 輪次）、超過每輪上限者，原因寫入日誌；通過者以 `run_trial(kind="candidate", period="development")` 登錄（計入多重檢定）。每晚最多 3 輪、每輪 4 個、共 12 個試驗，預算用完或模型錯誤即停；有新試驗時自動重算統計檢定；以鎖檔避免同時執行。研究日誌 `instance/research/journal.jsonl`（只追加）。
- 排程與介面：worker 工作 `research_agent`（每晚 22:00，`RESEARCH_AGENT_HOUR` 只能設 19–23 或 0–6）；CLI `python -m quant_platform.research agent`（白天拒絕執行）、`--dry-run`（只印提示）、`--check`（極小的連線檢查）。研究頁「AI 研究員」卡顯示模型、本月費用／上限、每晚上限與最近三輪的假設、結果、拒絕數；系統頁排程表列出 22:00 AI 研究員。設定 `RESEARCH_AGENT_*`（`.env.example`）。
- 實測：`--dry-run` 提示約 9,400 字（目前 68 個開發期試驗）；`--check` 前兩次 HTTP 400（json_object 需要輸入含「json」，修正後）成功：輸入 41、輸出 38 tokens，US$0.000024。研究本身依需求 §8 只在夜間執行，第一晚 22:00。
- 測試：`test_research_agent.py` 9 項（費用手算、金鑰檔、JSON 解析與預算硬上限、失敗記帳與無單價模型、設定檔驗證與拒絕原因、提示不含驗證期結果、每晚上限與錯誤停止與統計、鎖、研究頁）；排程清單加 `research_agent`。全部 362 通過、1 略過。
- 部署（`3f99cc7`，10:32，無資料表變更）：外網與本機研究頁「AI 研究員・每晚 22:00・gpt-6-luna・本月 US$0.0000／上限 US$3.00・尚未執行」，375 px 無水平捲動；系統頁排程表有 22:00 AI 研究員；worker 啟動無錯誤。
- 回復：`.env` 設 `RESEARCH_AGENT_ENABLED=false` 後重新部署即停止；試驗紀錄與日誌只追加，不需回復。

## 2026-10-01 — 除權息解析修正與第一批研究重跑、券商設定、LINE 通知

使用者 10/01 指示：計畫頁填寫報錯要修；AI 研究員比照 VectorDB 用 `gpt-6-luna`；券商預計台新與國泰；用 LINE 通知；其他研究繼續。

- **除權息解析錯誤（凌晨第一輪的資料不可信）**：證交所 TWT49U 自 2009 年起拿掉「權值」「息值」兩欄，只剩「權值+息值」與「權/息」；舊程式依固定欄位讀，把現金股利讀成股票股利（0050 2010-10-25 的 2.20 元變成配股比率 1.040073），證交所 ETF 的現金股利筆數都是 0。改為依回應的 `fields` 表頭讀（無表頭的快取依列長判斷），「權/息」決定是現金或股票部分；櫃買權值欄的 −0.01 捨入殘值不再當成配股；同日除權又除息而只有合計時停止並要求查明細。新增「前收 − 股利 = 參考價（±1 升降單位）」檢查，寫入 `actions.json` 的 `reference_mismatches`。
  - 重建結果（官方來源）：現金股利 0050 32 次、006208 24、0056 26、00713 21、00878 24、00919 14、00679B 37、00687B 37；參考價不符 0 筆；配股 0 筆；分割 0050 2025-06-18 1→4。總報酬年化幾乎不變（0050 13.99%；舊解析把股利當除息日再投入，效果相近），但回測引擎的時點不同（舊：除息日直接增加單位；新：除息後 25 天現金入帳）。
  - Yahoo 交叉核對修正：Yahoo 不回傳 0050 的分割事件（價格卻已調整），改用官方分割註記並在報告標示 `split_source`；Yahoo 2014 年前的 0050 價格未調整分割（1,215 天），比對時以原價比較並計數；Yahoo 的分割前股利以分割後單位表示（0050 2.70 → 0.675），股利核對先除以分割比率。剩餘差異都是 Yahoo 端：0056 缺 2009、2011、2012 股利，006208 缺 2012 股利，0050 2014-10-24 把未調整股利套在已調整價格（還原價差 7.3%）、2022-08-02 多一筆股利。扣除後總報酬與 Yahoo 還原價的年化漂移：00713 +0.011%、00878 0.000%、00919 −0.018%、00679B −0.002%、00687B +0.078%。0050 對加權報酬指數 22.6 年年化 13.99% 對 13.28%，年度差異來自成分不同（2024 年 +17%），不作為誤差判準。
- **資料版本（研究規範 §7 補充）**：`MarketData.fingerprint_until(end)` 逐日串接雜湊，只涵蓋模擬結束日以前的資料；引擎 1.2.0 的輸入雜湊與報告的 `data_fingerprint` 改用它（另存 `dataset_fingerprint`）。研究 CLI 一次載入全部目錄序列，同一期間的試驗共用同一資料指紋，每日新增行情不再讓開發期的同一設定變成新試驗。`registry.current_basis`：排行、各候選 DSR 與 PBO 只用目前資料版本的試驗；舊試驗保留在雜湊鏈中並繼續計入多重檢定次數（保守，DSR 試驗數 136）；統計檔記錄 `basis`，研究頁不混用不同資料版本的統計。
- **滑價與券商**：盤後零股抽樣 558 個交易日（每 10 個交易日一天，2010 起）成交價相對收盤的中位數 0050 +13.3 bps（P90 +57.0）、0056 +18.2、00878 +15.6、00713 +9.4、00919 +5.7、006208 0.0；預設滑價 10 → 20 bps（今日建議限價也是收盤＋20 bps 進位）。`research/costs.py` 新增券商設定檔：保守估計（不打折、最低 20 元，研究預設）、國泰證券、台新證券（電子下單 2.8 折、零股最低 1 元；台新月退）。官網確認：國泰盤中零股手續費＝價金 × 0.1425% × 折扣、定期定額每筆 1 元（至 2026-12-31）；台新存才富定期定額／預約零股 2 萬元以下每筆 1 元。折數取自公開整理，標示「待確認」，等使用者以對帳單確認。研究 CLI 新增 `--broker`。
- **計畫與帳務的券商欄位**：計畫頁新增「券商」選項（新版本保存）；今日建議依計畫券商估手續費與股數，「為什麼」寫明限價與手續費假設；持倉頁入金與成交可選券商帳戶，成交未填手續費時依該券商估算；定期定額影子帳戶用計畫券商的費率。資料表 `investment_plans`、`actual_cash_flows`、`actual_trades` 各加 `broker` 欄（服務啟動時的相容性遷移，`ALTER TABLE ADD COLUMN`，有預設值）。
- **LINE 通知**（`application/notifications.py`，設定見 `docs/line-notifications.md`）：LINE Notify 已停止服務，改用 Messaging API push（使用者自己的官方帳號 → 自己）。worker 工作 `line_plan_advice`（週一至週五 13–14 時每 5 分鐘，程式只在交易日 13:45–14:25 動作）：投入日收盤到了就推委託建議，14:15 仍缺收盤或現金不足 1 股時提醒；非投入日推「今天不需操作」（`LINE_DAILY_SUMMARY=false` 可關）。每日資料流程（含補跑）與夜間備份失敗時通知。每則記在 `notification_deliveries`（channel=`line`），同一則每天最多送一次；請求帶 `X-Line-Retry-Key`，只對逾時與 5xx 重試、同一則不會重複送達；token 不寫入紀錄或畫面。系統頁新增「LINE 通知」狀態與測試按鈕。需求 §11、§14 更新。
- **重跑第一批研究（09:53–09:55，修正後資料、保守成本、滑價 20 bps）**：
  - 基準（全期間 2004-02-11～2026-09-30，每月 5 日 1 萬元）：每月 6 日扣款 XIRR 17.19%（定期定額 17.21%），3 年勝率 45%、中位超額 −0.09%；定期不定額（200 日均線）3 年勝率 12%、中位 −2.53%、最差 −19.0%；股債 80/20（2017 起）3 年勝率 9%、中位 −10.7%。
  - 開發期 68 個設定（試驗 #75～#142）：8 個通過開發期門檻（3 年勝率 ≥ 60% 且中位超額 > 0），排行第一「每月 26 日全數買進 0050」3 年勝率 71%、中位 +0.50%、5 年勝率 86%；但扣除 136 次試驗的多重檢定後最佳 DSR 0.42（「每月 16 日全數買進 0050」，月超額 +0.040%，95% 區間 −0.005%～+0.106%），未達 0.95；PBO 0.02。結論：**目前沒有設定能證明勝過定期定額**。報告 `instance/research/stats/development-20261001-095513.json`。
- 測試：新增除權息短格式與合併事件、櫃買捨入殘值、參考價檢查、Yahoo 分割回補與未調整日、分割感知股利核對、指紋範圍、資料版本排行、預設成本與券商手算、計畫與帳務的券商、舊表加欄位、LINE 通知 8 項；手算測試明確寫出所用滑價。全部 353 通過、1 略過。
- 部署（`113b33f`，資料表加欄位）：10:12 停止服務 → 完整備份 `instance\backups\daily\quant_platform-20261001-101226.db`（10.37 GB、173 秒、quick_check ok、53 表）→ `scripts\deploy.ps1` → 唯讀核對三個表都有 `broker` 欄、預設值正確（各 0 筆）。驗收時發現兩個問題並修正部署：計畫頁策略旁的全期間回測摘要被 68 份開發期報告擠掉（`latest_reports` 改為先依期間與類型過濾，`a75a508`）；系統頁「LINE 通知」方塊的長設定名在 375 px 撐出水平捲動（`f6c349f`，並在專案資訊加「LINE 設定」分頁）。10:22 本機與外網（Cloudflare，已登入）確認：計畫頁券商三選項與「待確認」、策略回測摘要；持倉頁入金與成交的券商選單；系統頁 LINE「未設定」、測試按鈕停用、13:45 排程；研究頁「目前資料版本 68 個設定；舊版 68 筆不列入，但多重檢定以 136 次計」與排行；計畫、持倉、研究、系統頁 375 px 都無水平捲動（CSS `v2.css?v=2.3.2`）。
- 回復：程式 `git revert` 後以 `scripts\deploy.ps1` 部署；新增的 `broker` 欄位可保留（舊程式忽略）或以 `ALTER TABLE ... DROP COLUMN broker` 移除，亦可停服務後以上述備份還原（步驟見 `AGENTS.md`）；研究資料可重跑 `python -m quant_platform.research.history actions` 產生；`trials.jsonl` 只新增，不需回復。

## 2026-10-01 — 無人值守第一輪研究、計畫表單修正、前向模擬與研究摘要

- 01:23–03:40 無人值守執行 `scripts/research_first_run.py`：官方資料 3,086 次請求（之後皆可由快取重建）；00:17 起的下載在 00679B 遇到櫃買日期「106/01/17*」中斷，`roc_date` 改為去除星號後續抓完成（`236806e`），接續腳本改為建置或除權息步驟非 0 結束就停止。資料集：0050 5,568 筆（2004-02-11～2026-09-30，缺 5 個交易日＝2025-06 分割停止交易）、TAIEX 與報酬指數各 5,844 筆、0056 4,607、006208 3,275（198 天無成交）、00713、00878、00919、00679B、00687B；0050、0056、TAIEX 涵蓋 2008、2011、2015、2020、2022 五段下跌。第一輪基準與 68 個試驗用了錯誤的除權息解析，結果作廢（見上一筆；紀錄保留在雜湊鏈中）。
- 計畫頁「填寫報錯」（`b11703a`，09:30 部署）：薪資日欄位是 `type=number max=28`，填 29～31 會被瀏覽器擋下、伺服器也只收 1～28。改為一般文字欄位：薪資日 1～31（「月底」＝31，短月份用當月最後一天，今日建議與研究現金流一致），金額接受「1萬」「NT$ 12,000 元」、全形數字，回撤接受「30%」；錯誤顯示在頁首「沒有儲存：…」。網站記錄 4xx／5xx 請求與表單被拒原因（`quant_platform.web.requests`）。正式網址驗證：薪資日 99 回 400 並顯示原因；「1萬」「30%」可儲存（以測試資料庫驗證，正式環境未代填）。
- S5-W05 前向模擬（`88e8d33`）：每個交易日 15:30 記錄內建基準（標準現金流每月 5 日 1 萬元，2026-10-01 起）到 `instance/research/forward/log.jsonl`，只追加；研究頁顯示天數與相對定期定額。
- S4-W05 研究摘要（`1576e6a`）：研究頁「第一批研究結論」依研究方向分組，列出最佳設定、通過開發期門檻的數量，並依 DSR、PBO 給出白話結論；沒有通過就直接說沒有。

## 2026-10-01 — S5-W02～W04 依計畫的今日建議、實際帳戶與定期定額影子帳戶

- S5-W03 實際帳戶（持倉頁）：使用者記錄入金、出金、現金股利，並依券商成交回報記錄成交（手續費、證交稅空白時依券商公式估算）；平均成本法算持股、已實現損益與現金；拒絕未來日期、賣出超過當時持股、出金超過現金；紀錄以「作廢＋原因」處理，不刪除（新資料表 `actual_cash_flows`、`actual_trades`，服務啟動時自動建立）。估值用 `market_bars` 最新收盤，沒有行情時以成本估並提示。
- S5-W04 定期定額影子帳戶：每筆入金同一天以盤後零股買 0050（研究引擎 `simulate(..., contributions=入金)`、相同成本與成交模型），顯示期末差額與兩者 XIRR；有出金時提示解讀限制。長歷史研究資料未建立前顯示易懂提示（不顯示本機路徑）。
- S5-W02 今日建議第一版 `application/plan_decision.py`：依最新計畫版本、所選基準策略、實際帳戶現金與持股、當日收盤，在投入日產生盤後零股委託（限價＝收盤＋10 bps 進位）；非投入日「不需操作」並告知下次投入日；13:30 前等待收盤；收盤未到時等待資料；本月入金未記錄時以計畫金額計算並說明；均線／回撤倍數與區間再平衡都寫在「為什麼」。有計畫時今日頁以此為主，舊版盤後 AI 移到收合的「研究模型觀察（未晉級・僅供參考）」（需求 §1）；沒有計畫時版面不變、提示建立計畫。
- 驗收：本機預覽以空白暫存資料庫檢查電腦與手機版面（持倉頁、今日頁有計畫／無計畫兩種）；部署後本機與外網確認持倉頁表單與空白狀態、今日頁的計畫提示。**正式環境未代填任何計畫或帳務資料。**
- 測試：新增 `test_actual_account.py` 5 項（帳務手算、驗證、作廢、影子帳戶手算、頁面流程）、`test_plan_decision.py` 7 項（投入日推算、不需操作、收盤前、定期定額手算、已記錄入金、均線加倍、缺收盤、再平衡）；全部 315 通過、1 略過。

## 2026-10-01 — S5-W01 投資計畫頁、S4-W01～W03 研究紀律

- S5-W01：計畫頁 `/plan` 從空殼改為可填寫——每月投入（1,000～10,000,000 元）、薪資日（1～28）、採用策略（先限 4 個內建基準，旁邊顯示該基準的全期間回測摘要）、可承受回撤（5%～80%）、目標、預計年數、修改原因。每次儲存新增一個版本（資料表 `investment_plans`，只新增不修改），頁面列出版本紀錄。部署（00:54）時服務啟動自動建立新資料表（純新增）。本機預覽以空白暫存資料庫檢查電腦與手機版面（375 px 無溢出）；正式網址確認頁面與 4 個策略選項。**計畫內容由使用者填寫，開發者未代填。**
- S4-W01 試驗登錄 `research/registry.py`：`instance/research/trials.jsonl` 只新增，每筆帶前一筆雜湊與自身雜湊；同一組輸入重跑沿用原紀錄；修改、刪除、重排都會被 `verify()` 偵測（測試涵蓋）。
- S4-W02 期間與保留期 `research/periods.py`：開發 2004-02-11～2016-12、驗證 2017～2021、保留 2022-01～2026-09；候選不可用全期間、保留期前必須有驗證期試驗、每個候選只評估一次保留期；內建基準可用任何期間。已知限制：2025 年起的資料曾被舊研究模組使用過，乾淨的檢驗是前向模擬。
- S4-W03 統計檢定 `research/statistics.py`、`significance.py`：Deflated Sharpe Ratio（以論文 1,000 次試驗期望最大 Sharpe ≈ 3.26 驗證）、PBO（CSCV；純雜訊約 0.5、有真實優勢時接近 0）、月超額報酬的移動區塊 bootstrap 信賴區間（固定種子可重現）；每期間以「全部候選試驗數」計算 DSR。穩健性：成本倍數（`--cost-scale`）、晚一天執行（`--execution-lag`，只延後策略、不延後基準）。美股同規則對照待美股長歷史。
- 測試：新增 `test_research_registry.py` 5 項、`test_research_statistics.py` 4 項、`test_investment_plan.py` 3 項；全部 298 通過、1 略過。

## 2026-10-01 — S1-W07 總經特徵增量寫入

- 使用者決定（2026-10-01）：開機自動恢復維持「登入 Windows 時啟動」（需求 §10）。
- 盤點（唯讀，53 秒）：`feature_values` 2,534 萬筆中約 1,396 萬筆每檔相同——總經 7 項 7,198,905 筆、`twii_return_20d` 84 萬、`day_of_week` 85 萬完全相同；另 6 個跨資產特徵各有 1 個日期部分標的的值不同。`fred_macro_data` 每次把 7 個序列全部重寫給每一檔啟用中的個股與 ETF（中位數 787 秒，最長 1,638 秒）。另查到 `factor_research` 以 `list_features(symbols)` 一次載入台股全部特徵（09-08 以前完整流程約 8 分鐘可完成）。
- 做法：`MacroDataPipeline._materialize` 改為增量——以覆蓋筆數與最後日期最常見的標的為參考，比較其已存序列與由最新修訂算出的序列，只寫新增或修訂的觀測點；覆蓋不同（新標的、不完整）的標的寫完整序列；全部在同一次 upsert（單一交易）完成。儲存內容與全量重寫相同，只有未變動列的 `computed_at` 保留舊值；讀取端完全不變。新增 `feature_series_coverage()`（每檔每特徵筆數與最後日期，正式庫 6.5 秒）。執行結果新增 `changed_points`、`rebuilt_symbols`。
- 驗證：
  - 測試：首次全寫、無變動 0 筆、新月份只寫 7 點、CPI 基期修訂只改 1 個年增率點＋公債 1 點、新標的補完整序列，每一步都與全量重寫的結果集合相同。
  - 正式庫唯讀乾跑（部署前）：5.4 秒，應寫 0 筆（已存序列與計算結果逐點相同）。
  - 部署（00:17）後手動執行一次（等同排程步驟）：11.6 秒，下載 13,225 筆，新增 22 個修訂、寫入 11,946 筆（22 點 × 543 檔），`rebuilt_symbols` 0。
  - 正式庫唯讀核對：543 檔啟用中個股／ETF 在 13,209 個觀測點上筆數、數值、可用時間完全一致，與計算序列 0 差異、0 缺漏。
- 共用特徵只存一份（縮小主檔）需要改讀取端並以模型訓練矩陣驗證，移到 S3-W06 與特徵快照 Parquet 化一起做。
- 測試：`test_macro_data.py` 新增 2 項；全部 260 通過、1 略過。
- 回復：`git revert` 後以 `scripts\deploy.ps1` 部署即恢復全量重寫；資料不需轉換。

## 2026-10-01 — S1-W06 暫停非核心收集，S1-W05 收盤時效量測上線

- 盤點每日台股流程（`AutomationService.execute`）：研究流程之後依序執行盤中／衍生品收集、盤中特徵、法說會、模型治理 `refresh()`（台股、美股都跑，載入全量預測）、模擬交易處理、影子交易、晉級複驗、盤後 AI、報告；報告 `generate()` 每次都同步 RAG 文件與向量索引。每日決策（`DailyDecisionPipeline`）與盤後 AI 只讀取模型、特徵、市場狀態、策略整合、組合研究與各研究結果的 `promotion_gate`，不讀取上述暫停模組。
- 設定 `PAUSED_MODULES`（`config/settings.py`；未設定＝七項全部暫停，`none`＝全部恢復，未知名稱拒絕啟動）：`point_in_time`、`intraday_features`、`earnings_calls`、`model_governance`、`shadow_trading`、`promotions`、`rag_index`。報告本文與 Email 照常，只略過 RAG 索引（`generate(..., index=False)`）。系統頁「每日排程」列出暫停清單。
- 需求 §13 補充：新聞資料集仍隨籌碼資料收集（`news_sentiment_daily` 是現行綜合模型候選特徵），九種組合配置仍在每日流程（現行決策讀取其結果），分別在 S4、S5 處理。
- 量測基準：09/21–09/30 的台股流程都在原始資料品質閘門停止（S1-W02 處理的下市個股），因此暫停步驟這段期間實際上沒有執行，沒有變更前的完整流程可比較；10/01 13:50 會是第一次完整台股流程，以它記錄耗時。現有數字（09/20 起中位數）：台股行情 293 秒、特徵 200 秒、模型 111 秒、決策 44 秒；總經 `fred_macro_data` 787 秒（最長 1,638 秒），每次重寫 7,198,905 筆特徵——列為 S1-W07 的主要對象。
- S1-W05 量測：`application/close_availability.py` `CloseAvailabilityProbe`，worker 工作 `close_availability_probe`（週一至週五 13–14 時每分鐘觸發，程式只在交易日 13:30–14:45 動作）。每個來源當天第一次看到資料就寫一筆到 `instance/close_availability.jsonl`，之後不再查詢該來源：證交所 MI_INDEX 收盤表、櫃買 OpenAPI 收盤（日期＝當日）、Yahoo 0050 日 K 收盤與官方一致、證交所盤後零股 TWT53U（當日）。系統頁新增「收盤資料時效」（最近 5 個交易日，13:30 後幾分鐘）。以 09/30 資料實測四個解析器：證交所 1,382 筆（0050 收盤 112.05）、櫃買 11,772 筆、Yahoo 112.05 與官方一致、盤後零股 1,368 筆。
- 測試：`test_automation.py` 新增預設暫停 1 項、設定解析 1 項（原「全部執行」測試改為 `paused_modules=frozenset()`）；新增 `test_close_availability.py` 4 項；排程清單加 `close_availability_probe`。全部測試 258 通過、1 略過。
- 部署：2026-09-30 23:55（S1-W06）、10-01 00:00（S1-W05 量測）；本機與外網系統頁都顯示暫停清單、13:30 量測排程與「收盤資料時效」卡。
- 回復：`.env` 設 `PAUSED_MODULES=none` 後重啟即恢復全部步驟；量測工作可在 `_add_maintenance_jobs` 移除，紀錄檔可保留。

## 2026-09-30 — S2-W05 每日備份與還原演練

- `application/database_backup.py` `DatabaseBackupService`：SQLite backup API 單一步驟（`pages=-1`）取得一致快照，其他連線照常寫入 WAL；副本先寫 `.partial`，改為 `journal_mode=DELETE` 成為獨立檔，`quick_check` 通過後記錄全部 50 個資料表筆數，改名並寫入 `instance/backups/daily/manifest.jsonl`；保留最新 7 份（只輪替本目錄 `quant_platform-*.db`）；寫入 `database_backup`／`SYSTEM` 執行紀錄；超過 26 小時無新備份視為逾期。
- 排程：worker 新增 `database_backup` 工作（每日 03:00，錯過 2 小時內補跑，不需每日流程的鎖）；`_add_calendar_refresh_job` 改名 `_add_maintenance_jobs`（02:30 預測封存、03:00 備份、每小時日曆檢查）。
- `scripts/database_backup.py`：`status`、`run`（手動備份）、`drill`（還原到 `instance/backups/drill/`、唯讀開啟、quick_check、逐表筆數與 manifest 比對、報告 JSON，預設刪除還原出的副本；不動正式檔）。還原正式檔的步驟寫在 `AGENTS.md`「資料庫安全」。
- 系統頁：新增「資料庫備份」狀態（最近時間、大小、份數）與「每日排程」表（02:30 封存、03:00 備份、06:30 美股、13:50 台股休市日略過，及背景工作說明）；「最近排程」改名「最近執行」。
- 實測（23:41 部署後）：
  - 手動備份 `quant_platform-20260930-234151.db`：9.62 GiB，總計 141.5 秒（複製約 22 秒，其餘為 quick_check 與筆數），quick_check ok，50 表。
  - 還原演練 `drill-20260930-234642.json`：複製 4.6 秒、quick_check ok、50 表筆數與 manifest 一致、台股最新行情 2026-09-30，passed。
  - 兩端：本機 `http://127.0.0.1:5000/system` 與外網（已登入，23:46）都顯示「資料庫備份 正常｜最近 09/30 23:41・9.6 GiB・共 1 份（上限 7）」與每日排程表。
- 開機自動恢復：排程工作 `StockResearchLocalServices` 在使用者登入時觸發（與 VectorDB 的 `PimiServices-VectorDB-User` 相同）；重開機後登入即自動恢復 web、api、worker、Tunnel。重開機後、登入前網站離線；是否改為開機觸發或自動登入屬系統設定，列入需求 §14 待使用者決定，未變更。
- 測試：新增 `test_database_backup.py` 8 項（獨立副本與筆數、持有未提交交易時的快照、輪替、逾期、失敗紀錄、演練通過與筆數不符、系統頁狀態）；排程工作清單加 `database_backup`。
- 回復：停用備份只需移除排程工作的 `database_backup` 註冊並重新部署；備份目錄可整個保留。

## 2026-09-30 — S2-W03 第二版：電腦／iPad／手機版面與深色主題

- 使用者要求（2026-09-30）：介面電腦與 iPad 也會用，要有暗色主題。
- 版面：同一套 HTML 三種版面——電腦（≥1200 px）完整側邊欄（品牌、五項導覽、台股時段、主題切換、登入 email）；iPad（768–1199 px）84 px 圖示側欄；手機（<768 px）頂端列（品牌、時段、主題）＋底部五分頁。今日頁在 ≥1100 px 分主欄（委託單、觀察清單）與側欄（為什麼、資料狀態、交易守則）；單欄時以 `order` 依重要性交錯（委託 → 為什麼 → 觀察 → 資料狀態 → 守則）。手機表格改逐筆卡片（`data-label`）。
- 主題：深色為預設；`sr_theme` cookie（dark／light，一年、SameSite=Lax、https 加 Secure）由伺服器直接輸出 `<html data-theme>`，不會閃爍；非法值一律深色。Mermaid 圖隨主題重新渲染。台股慣例紅漲綠跌、買紅賣綠（`--up`／`--down`）。
- 內容：今日行動卡加委託筆數、預估金額、模擬權益、可用現金；持倉加權重條；研究頁 AI 研究員說明與工具分組兩欄，暫停模組收合；系統頁狀態磚（手機兩欄）與文件直欄分頁（≥1024 px）。
- 導覽的台股時段：盤前、盤中、已收盤（13:30–13:40）、盤後零股進行中（13:40–14:30）、今日已收盤、休市（附原因與下一交易日），依官方交易日曆。
- 驗收（內建瀏覽器，5055 預覽＋部署後 5000 與外網）：375、820、1180、1440 px 各頁整頁水平溢出 0；深淺色切換後重新整理仍維持；外網登入後側欄顯示擁有者 email、CSS `v2.css?v=2.1.0`。
- 測試：`test_dashboard_v2.py` 改為檢查側欄與底部分頁各一份導覽、`aria-current`；新增主題 cookie 1 項、台股時段 7 項。全部測試 252 通過、1 略過。
- 回復：`git revert` 本 commit 後以 `scripts\deploy.ps1` 部署。

## 2026-09-30 — S2-W02 Cloudflare 上線

- 使用者回饋（2026-09-30）：其他專案由 AI 自行完成 Cloudflare 設定，不應要求使用者手動複製 AUD。查證 `C:\ProgramData\PimiServices\Important System Log\2026-09-10 AutoLayout Cloudflare Deployment.md`：當時由 AI 在 Cloudflare 後台建立 Access application。改為由開發者在內建瀏覽器操作；使用者只完成 Cloudflare 登入（登入由本人操作，登入狀態保存在內建瀏覽器）。
- Cloudflare Zero Trust（帳號 `c55301fc…`、團隊網域 `raspy-mode-cc1c`）：
  - 新增規則 `StockResearch owner only`（id `ec4e2109-b2f2-491e-9ae9-229a3ef876ac`）：Allow；Include Emails `ryan73093@gmail.com`；Require Login Methods Google。沒有沿用 `AutoLayout Google users`（Include Everyone）與 `Ryan and Eva only`，避免放寬到其他人。
  - 新增 self-hosted application `StockResearch`（id `c49fc706-9f0e-4080-82ef-79f6ebfa5490`）：目的地 `stockresearch.pimi-sunsun.com`、只接受 Google、instant authentication、工作階段 24 小時。既有三個應用程式未變更。
- `.env` 加入 `AUTH_MODE=cloudflare-access`、`PUBLIC_URL`、`ACCESS_TEAM_DOMAIN`、`ACCESS_AUD`（64 位 AUD tag）、`ACCESS_ALLOWED_EMAILS`；未輸出 `.env` 其他內容。
- 重啟（21:39）：`supervisor.log` 顯示 "Supervisor started with tunnel"；Tunnel PID 296120 註冊 4 條 QUIC 連線（khh01×2、tpe01×2）。VectorDB Tunnel（PID 10704）與 PimiServices 共用服務（PID 5508）未變更。
- 驗收：
  - 本機 `http://127.0.0.1:5000/` 200，顯示新版今日頁（loopback 例外）。
  - 外網未登入：`/`、`/holdings`、`/system/docs/roadmap` 皆 302 到 `raspy-mode-cc1c.cloudflareaccess.com/cdn-cgi/access/login/stockresearch.pimi-sunsun.com`（kid 與 AUD 一致）；帶偽造 `Cf-Access-Jwt-Assertion` 仍 302。
  - 外網登入後（內建瀏覽器，21:41）：`https://stockresearch.pimi-sunsun.com/` 顯示今日頁（委託 2 筆、資料 09/30 13:30）；系統頁「外網發布：Cloudflare Access」、專案資訊文件可讀。
- 回復：`.env` 的 `AUTH_MODE` 改回 `development` 並重啟（Tunnel 不會啟動）；需要撤下時在 Zero Trust 刪除 `StockResearch` application 與規則、刪除 DNS 記錄、`cloudflared tunnel delete stockresearch-pimi-sunsun`。

## 2026-09-30 — S2-W01～W04 Cloudflare 準備與新介面第一版

- 使用者決定（2026-09-30）：S2 提前到 S1-W05／W06 之前；同意由開發者以本機 cloudflared 建立 Tunnel 與 DNS；Access application 由使用者在 Zero Trust 建立並提供 AUD。
- S2-W02 Tunnel：`cloudflared tunnel create --credentials-file .runtime\cloudflared\stockresearch-pimi-sunsun.json stockresearch-pimi-sunsun`（id `e37bb649-0237-434f-b5c7-833722d87f9d`；憑證不放共用 `.cloudflared`，避免影響 YtSummary 啟動腳本）；`tunnel route dns` 新增 CNAME `stockresearch.pimi-sunsun.com`。團隊網域沿用 VectorDB 設定 `https://raspy-mode-cc1c.cloudflareaccess.com`。
- S2-W01 Access 驗證：`dashboard/cloudflare_access.py`（沿用 VectorDB 模式）：RS256、audience、issuer、exp、email 驗證；`ACCESS_ALLOWED_EMAILS` 允許清單；直接連 127.0.0.1／localhost 的 loopback 請求免登入；經 Tunnel 的請求雖來自 127.0.0.1，但 Host 是公開網址，因此必須有 JWT；設定不完整一律 503；跨來源寫入 403；安全標頭。`AUTH_MODE=cloudflare-access` 時舊 Google OAuth 寫入檢查略過。新增設定 `AUTH_MODE`、`PUBLIC_URL`、`ACCESS_TEAM_DOMAIN`、`ACCESS_AUD`、`ACCESS_ALLOWED_EMAILS`；依賴 `PyJWT[crypto]`。
- Tunnel 監督：`run_local_services.ps1` 在 `.env` 為 `AUTH_MODE=cloudflare-access` 且憑證存在時才把 cloudflared 納入監督（開發模式永遠不公開）；`stop-services.ps1` 以本專案憑證路徑辨識 cloudflared，不碰 VectorDB、YtSummary 的 Tunnel。
- S2-W03／W04 新介面：`dashboard/v2.py` blueprint＋`templates/v2/`＋`static/css/v2.css`（淺色、手機優先、深色模式跟隨系統；桌面頂部導覽、手機底部 5 分頁）。
  - 今日 `/`：行動卡（交易／不需操作／回看三種顏色、14:30 倒數）、委託單與「複製委託」、每筆理由只取「訊號」「風險」、觀察清單收合、資料時間與品質狀態一行。
  - 持倉 `/holdings`：模擬帳戶權益、現金、報酬、曝險、持股與最近委託。
  - 計畫 `/plan`：S5 前的空狀態。
  - 研究 `/research`：舊版工具分組入口，暫停模組標示「S8 決定去留」。
  - 系統 `/system`：服務、台美股行情時效、資料品質（含排除檔數）、交易日曆、外網發布狀態、最近排程；專案資訊分頁（路線圖、架構、需求、交接、開發歷程）即時讀 repo 原始檔，Mermaid 圖在瀏覽器渲染；文件讀取限白名單。
  - 舊市場總覽移到 `/market`；舊側邊欄加「回到新版介面」。
- 驗收（內建瀏覽器，375 px）：修正前整頁被表格撐寬（grid 子元素 min-width），改為 `minmax(0, 1fr)` 與表格區塊內捲動後無水平溢出；系統頁架構分頁 6 張圖渲染成功。
- 部署事故：一次在服務執行中直接 `pip install .`，安裝被鎖定的執行檔中斷，套件被移除一半，服務反覆重啟約 4 分鐘（21:17–21:21）。以停止 → 重新安裝 → 啟動恢復，新增 `scripts/deploy.ps1` 固定此順序並寫入 `AGENTS.md`。`site-packages` 內留有 pip 中斷產生的 `~uant-research-platform` 殘留目錄（只造成警告），未手動刪除。
- 測試：新增 `test_cloudflare_access.py` 11 項、`test_dashboard_v2.py` 8 項；`test_dashboard.py` 首頁改測 `/market`。全部測試 236 通過、1 略過。
- 待辦：使用者建立 Access application 並提供 AUD → 寫入 `.env` → 重啟（Tunnel 隨之啟動）→ 本機與 Cloudflare 兩端驗收。

## 2026-09-30 — S1-W03 資料庫瘦身，以及停牌個股規則

- 使用者決定（2026-09-30）：今晚執行瘦身；停牌個股改為只排除該檔。
- 盤點（唯讀）：`model_predictions` 2,530 萬筆／233 個實驗，每次重跑都把全部歷史樣本外預測再存一份（每個實驗約 66 萬筆）；`feature_values` 2,533 萬筆幾乎全是 1.0.0 版，體積主要來自總經與跨資產特徵被複製到每一檔（例如 `treasury_10y` 228 萬筆）。
- 索引：兩表的唯一索引已涵蓋所有查詢的開頭欄位；移除 11 個未使用單欄索引（預測 7、特徵 4），並同步修改 `database/models.py`。
- 保留政策 `application/prediction_archive.py`：資料庫只留使用中的實驗（每組最新、每組最新 rank IC 為正、每市場／標籤最佳 CANDIDATE、未淘汰的模型登錄）；其餘逐實驗寫 Parquet（zstd），讀回核對筆數、記錄 SHA-256 到 `manifest.jsonl` 後才刪除。排程每天 02:30 自動執行（`prediction_archive` 工作，使用每日流程同一把鎖）。
- 一次性執行 `scripts/slim_database.py`（20:40–21:16，服務停機約 36 分鐘）：
  - 完整備份 `instance/backups/quant_platform-pre-s1w03-20260930-204109.db`（22.29 GiB，68 秒，quick_check ok）。
  - 封存 217 個實驗、19,340,138 筆（670 秒），Parquet 共 247 MB；資料庫保留 16 個實驗、5,959,558 筆（與原總數 25,299,696 相符）。
  - `VACUUM INTO` 238 秒；新檔 quick_check ok、所有資料表筆數與壓縮前一致。
  - 主檔 **22.29 GiB → 9.62 GiB**。腳本最後改名時因自身連線未關閉（Python sqlite3 的 `with` 不會關閉連線）失敗；驗證已通過，手動完成改名並補寫 `slim-report-20260930-204109.json`；腳本已改用 `contextlib.closing`。
  - 暫存：`instance/backups/` 內兩個 22.29 GiB 檔（變更前備份、壓縮前檔）。隔日台股流程正常後可刪除，刪除指令交由使用者執行。
  - 3 GiB 目標未達：剩餘體積主要是特徵表（資料 3.06 GiB＋索引），需把總經／跨資產特徵改為每市場只存一份，列為新工作包。
- 停牌規則（資料品質 v2、決策 1.1.0）：個股落後超過 3 個交易日或近期缺漏達 25% 改為警告並排除（`excludes_asset`），不阻擋全市場；同時排除超過 5 檔或 2% 時視為來源問題並阻擋。每日決策以特徵日期與交易日曆計算落後交易日數，超過 3 日者標「資料不足」、不列候選。
- 測試：新增 `test_prediction_archive.py` 3 項、`test_decision_exclusion.py` 1 項、`test_data_quality.py` 停牌 2 項；排程工作清單加 `prediction_archive`。
- 回復：`stop-services.ps1` → 以 `quant_platform-pre-s1w03-*.db` 替換主檔 → 啟動；封存的預測可由 Parquet 依 `manifest.jsonl` 回灌。

## 2026-09-30 — S1-W02 下市個股處理

- 起點：台股原始品質快照 #131 被 3 個嚴重問題阻擋——2867.TW 落後 11 個交易日；5371.TWO 落後 17 個交易日、近期缺漏 27%。
- 查證：
  - 資料庫：兩檔的官方行情分別停在 2026-08-19、08-21；Yahoo 之後仍有平盤、成交量 0 的資料（2867 固定 9.70 元到 9/11；5371 固定 83.5 元到 9/3），屬停止交易期間的填補值。
  - 官方：2026-09-29 證交所 `MI_INDEX`、櫃買最新收盤、證交所 `t187ap03_L` 與櫃買 `mopsfin_t187ap03_O` 公司名冊都查無兩檔，判定已下市（或終止櫃檯買賣）。既有下市資訊依賴 FinMind `TaiwanStockDelisting`，目前 FinMind 回應 HTTP 402（額度用完），所以股票池沒有更新。
- 新增 `application/listing_reconciliation.py` `TaiwanListingReconciliationService`：比對證交所＋櫃買現行名冊與啟用中的個股（不含 ETF、指數）；不在名冊且連續 3 個交易日以上無成交者停用，並把所有未結束的股票池區間在最後成交日關閉（`end_is_exact=False`，原因附在 reason），避免股票池擴充把它重新加回。防護：名冊少於 1,500 家不動作；單次超過 10 檔時全部暫停並要求人工確認。寫入 `tw_listing_reconciliation` 執行紀錄。
- 接入：`DailyResearchPipeline` 在台股行情更新後、早期決策與品質閘門之前執行；失敗只記錄，不中斷流程。
- 測試：`tests/test_listing_reconciliation.py` 5 項（停用並關閉區間、近期有成交者保留、名冊不完整不動作、超過上限要求人工確認、執行紀錄）；全部測試 211 通過、1 略過。
- 部署（20:25）：`stop-services.ps1` → `pip install .` → `start-services.ps1`（監督 282792、web 3660、api 282688、worker 277108；web `/health` 12 ms）。
- 正式資料執行（20:28）：名冊 1,988 家、比對 529 檔；停用 2867.TW（最後成交 8/19，28 個交易日無成交）、5371.TWO（最後成交 8/21，26 個交易日）。重跑台股原始品質快照 #132：`warning`、阻擋 0、落後標的 0、允許研究；剩 11 個不阻擋警告（少數個股缺融資融券或估值資料）。
- 待決定：停牌但仍在名冊上的個股目前仍會觸發個股落後阻擋，建議改為只排除該檔（`REQUIREMENTS.md` §14）。
- 回復：`git revert` 本工作包 commit 並重新部署；若要恢復兩檔，`set_active` 設回 True 並把對應股票池區間的 `valid_to` 改回空值。

## 2026-09-30 — S1-W04 服務啟停與效能

- 使用者同意重啟服務（2026-09-30）；GitHub push 由使用者完成，`origin/main` = `cd436d2`。
- 現況盤點（停機前，20:02）：三個服務的監督程序（PID 9988）已不存在，服務處於無人監督狀態；排程工作 `StockResearchLocalServices`（登入時執行）上次 9/26 21:27 啟動，結束代碼 `0xC000013A`（被中斷）。Worker 自 9/26 起累計 CPU 93,411 秒；停機前 20 秒內用掉 13.6 秒（約 68% 單核），正卡在 17:43 開始的美股補跑。
- CPU 根因（以排程紀錄查證）：
  1. 補抓守門員 `run_startup_catch_up` 只看最近 30 筆執行紀錄判斷「今天是否成功」，一次完整流程會寫 7–9 筆子紀錄，成功紀錄很快被擠出，於是每 15 分鐘重跑整套流程。9/28 起美股完整流程跑了 13 次（每次總經資料約 13 分鐘）。
  2. 同一段程式把資料庫的 naive UTC 時間當成台北時間，差 8 小時；台股 14:00 的成功紀錄被當成 06:00，早於 13:50 的排定時間。
  3. 9/28 休市誤報造成台股流程失敗 82 次（S1-W01 已修正）。
  - 更正：S1-W01 紀錄中懷疑的 `run_universe_backfill` 經查證只在「跨過目標的那一批」觸發一次全套研究，不是 CPU 元凶。
- 修正：`SchedulerJobRunRepository.latest_succeeded(job_name, market, since)`（以 UTC 查詢）、`AutomationService.succeeded_since`；守門員改用資料庫直接查詢。
- Web 改用 Waitress（8 threads，`channel_timeout=120`）；未安裝時退回 Flask 開發伺服器並記錄警告。`pyproject.toml` 新增 `waitress>=3,<4`。
- 服務腳本：
  - `scripts/run_local_services.ps1`：單一執行個體、PID 檔（`.runtime\services\`）、停止旗標、`instance\supervisor.log`、子程序 UTF-8 日誌（先前日誌為 Big5 亂碼）、新程序 120 秒後才開始健康檢查。
  - `scripts/stop-services.ps1`：停止旗標 → 以完整執行檔路徑核對並結束殘留程序樹 → 驗證 5000／8000 已釋放；13:30–14:40 預設拒絕。
  - `scripts/start-services.ps1`：觸發排程工作啟動（脫離工具工作階段），等待健康檢查並列出 PID。
  - `.gitignore` 加入 `.runtime/`。
- 部署（20:05–20:09）：`stop-services.ps1` 結束三個程序樹 → `pip install .`（含 Waitress）→ `start-services.ps1`。新 PID：監督 278120、web 279180（Python 278916）、api 268716（Python 189652）、worker 274600（Python 23104）。VectorDB 5001 回應 200，`cloudflared` PID 5508、10704 未受影響。
- 驗收：
  - `/health` 13 ms；`/data-quality` 111 ms；`/ai-trading` 冷啟 11.9 秒、熱快取 1.9–2.0 秒；首頁 `/` 冷啟 6.6 秒、熱快取 4.8 秒（輸出約 1.98 MB 的舊版市場總覽）。
  - 10 分鐘 CPU：worker 0.9 秒、api 0.8 秒、web 15.2 秒（含量測頁面請求）。
  - 重啟後 12 分鐘內排程紀錄 0 筆（守門員正確判定兩市場今日已完成、補資料已達標）。
  - S1-W01 生效：worker 啟動 23 秒後自動更新日曆快取（`fetched_at` 12:09:14 UTC）。
- 未達項目：首頁 < 2 秒。舊首頁會在 S2-W03 由輕量「今日」頁取代，效能目標移到該工作包。
- 測試：`test_universe_scheduler.py` 新增「今日成功紀錄被 40 筆新紀錄覆蓋、且以 UTC 儲存」情境；全部測試 206 通過、1 略過。
- 回復：`stop-services.ps1` → `git revert` 本工作包 commit → `pip install .` → `start-services.ps1`。

## 2026-09-30 — S1-W01 官方交易日曆

- 使用者決定（2026-09-30）：可以 push；公開網址用 `stockresearch.pimi-sunsun.com`；繼續開發。
- 目標：交易日判斷改用證交所官方開休市日期，休市日不再被判為缺資料；真正缺資料時仍阻擋。
- 來源查核：證交所 `rwd/zh/holidaySchedule/holidaySchedule?date=YYYY0101&response=json` 提供 2021–2026 年（2020 年以前與 2027 年回傳 0 筆）；舊端點忽略年份參數。表中「開始交易日」「最後交易日」是交易日提示，其餘（含「市場無交易，僅辦理結算交割作業」）為休市。颱風休市不在官方表內（例如 2024 年 7、10 月的颱風假）。
- 新模組 `src/quant_platform/market_calendar/`：
  - `core.py`：`TradingCalendar`（週末必休、`previous_trading_day`、`next_trading_day`、`sessions_after`、`covers`；連續休市超過 31 天視為資料錯誤）。
  - `twse.py`：證交所解析與下載（3 次重試）；0 筆年度回傳「尚未公布」，不當成沒有假日。
  - `store.py`：三層資料——隨程式發布的 `data/twse_closures.json`（2021–2026，122 個休市日）、`instance/market_calendar/twse_cache.json`（每日更新）、`instance/market_calendar/manual_closures.json`（人工補登）；`refresh_if_due` 每天最多一次，失敗 6 小時後再試。
  - CLI `python -m quant_platform.market_calendar`：`status`、`refresh`、`add-closure`、`remove-closure`、`export-packaged`。
- 接入：`DailyMarketDataPipeline.expected_session_date`、`TaiwanOfficialDailyBarProvider`（補抓區間跳過休市日）、`DataQualityService`（市場落後改算交易日；當年未收錄時發 `CALENDAR_COVERAGE` 警告）、`AfterHoursAiService`（休市標題顯示假日名稱；長假後預覽改以前一交易日判斷）、排程（定時工作與補抓守門員在台股休市日略過；新增每小時觸發、每日最多一次的日曆更新工作）。台股行情過期訊息附上颱風補登指令。容器新增 `market_calendar`，快取放在資料庫檔同一目錄（測試用暫存資料庫時不寫入正式 `instance/`）。`pyproject.toml` 加入套件資料。
- 美股維持週一至週五判斷（研究對照用途）；NYSE 假日列為後續項目。
- 測試：新增 `tests/test_trading_calendar.py` 20 項（解析、2026 中秋與教師節、農曆年結算日、未收錄年度、人工颱風休市、更新與退避、休市日不誤報、交易日缺資料仍阻擋、排程略過、資料品質警告、盤後決策休市標題與預覽）；`test_universe_scheduler.py` 預期工作清單加上 `market_calendar_refresh`。全部測試：205 通過、1 略過（66 秒）。pytest 需加 `--basetemp` 指向可寫目錄（本機沙箱不允許系統暫存目錄）。
- 實測：`refresh --year 2026 --year 2027` → 2026 年 24 個休市日、2027 年尚未公布；`status` 顯示收錄 2021–2026、當年已收錄。離線建置 wheel 確認包含 `market_calendar/data/twse_closures.json`（24.9 KB）。
- 部署：執行中的 web／api／worker 使用 `.venv` 已安裝的舊版套件；服務執行時 `pip install .` 可能因執行檔被鎖定而中斷，因此重新安裝與重啟併入 S1-W04（先建立有 PID 核對的啟停腳本）。本次未停止或重啟任何服務。下一個會受影響的休市日是 2026-10-09（國慶日補假）。
- 新發現（交給 S1-W04）：`scheduler/runner.py` 的 `run_universe_backfill` 每 10 分鐘執行一次，達到目標檔數後，每批都會重跑模型、因子、集成、組合與決策研究；這與 worker 累計約 25 小時 CPU、`model_predictions` 約 10 GiB 的現象一致，需量測確認。
- Push：本工具環境停用互動登入，GitHub 認證需使用者在自己的終端機執行一次 `git push`。
- 回復：`git revert` 本工作包 commit；已產生的 `instance/market_calendar/` 可直接刪除。

## 2026-09-30 — S0 基線保全與重新規劃

- 背景：2026-09-29 產品方向審查指出開發重心偏向增加研究模組，每日流程與決策核心尚未穩定。使用者確認新方向：單人使用的台股盤後決策台，AI 當研究員找出相同現金流下勝過定期定額的規則；前端 Flask + Jinja + HTML；發布到 Cloudflare（參照 VectorDB、AutoLayout）；資料庫優化由開發者決定；開發必須有路線圖、需求、開發記錄、交接記錄與系統架構圖。
- S0-W01 Git 基線：使用者安裝 Git 2.56.0。2026-08-13 clone 後從未 commit；45 個修改檔與 10 個未追蹤檔（含 Google Trends、官方日線來源、兩份審查文件）以 `c084e06` 一次 commit。commit 前檢查：`.env`、`instance/` 已被忽略；候選檔案掃描無金鑰；`.gitignore` 新增 `.mypy_cache/`、`.ruff_cache/`。未 push。
- S0-W02 資料庫量測（`dbstat` 唯讀，耗時 480 秒）：主檔 22.3 GiB、WAL 約 1.45 GiB。`model_predictions` 含 9 個索引約 10.2 GiB；`feature_values` 含 6 個索引約 8.6 GiB；其他資料表約 3.5 GiB；`market_bars` 0.13 GiB。結論：行情留 SQLite；瘦身對象是預測與特徵，改存 Parquet（S1-W03）。先前「把歷史行情移出 SQLite」的建議已更正。
- 環境觀察：
  - Dashboard `http://127.0.0.1:5000/` 在 60 秒內無回應；API 8000 可回應。
  - `quant-worker.exe`（PID 22024）自 9/26 起累計 CPU 約 25.5 小時。
  - 最新 worker 日誌：台股資料品質閘門阻擋（3 個嚴重問題）；漏跑補抓因上一次仍在執行而跳過。
  - 交易日判斷只跳過週末（`universe.py:281`、`taiwan_daily_bars.py:52`），9/25、9/28 休市會被誤判為缺資料。
  - C 槽可用 510 GB；`.venv` 尚未安裝 pyarrow、duckdb、PyJWT、waitress。
- S0-W03 規劃文件：新增 `REQUIREMENTS.md`、`AGENTS.md`、`CLAUDE.md`（匯入 AGENTS.md）、`docs/development_roadmap.md`（S0–S8 共 9 階段、42–61 工作天初估）、`docs/system_architecture.md`（6 張 Mermaid 圖：總覽、盤後時間軸、決策識別碼鏈、AI 研究迴圈、儲存分層、部署拓撲，另含架構決策與模組處置表）、`docs/development_handoff.md`。舊路線圖、模組狀態、進度效益與架構文件移入 `docs/archive/`；README 開頭改為新產品定位與文件索引。
- Cloudflare 參照：VectorDB 使用專屬 Tunnel `vectordb-pimi-sunsun`（`--url http://127.0.0.1:5001`，憑證在該專案 `.runtime/cloudflared/`），網站以 `access_control.py` 驗證 Access JWT；AutoLayout 使用 Access + Google 並在應用層做允許清單。本專案沿用相同模式（S2）。
- 測試：本輪只有文件與 `.gitignore` 變更，未執行測試。
- 回復：文件變更可 `git revert` 規劃 commit；歸檔檔案以 `git mv` 移回。

### 需求變更紀錄

- 2026-09-30：產品由「多模組 AI 量化研究平台」改為「單人盤後決策台」；登入改由 Cloudflare Access 負責；新增 §8 AI 研究員規則與 §13 暫停清單。來源：使用者本日對話與 `docs/product-direction-review-2026-09-29.md`。
