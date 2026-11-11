# 交付與文件

適用：工作包的開始與收尾、文件同步、測試、commit。決定原文在 `docs/decisions.md`。

## 工作包

- 一個工作包一個對話。先寫目標、範圍、測試與回復方式，再實作；持續推進到驗收或具體的外部阻礙。使用者只問評估或診斷時，只讀不改。
- 開工前看 `git status`、相關測試與服務狀態；保留既有的使用者變更。
- 需求以 `REQUIREMENTS.md` 和使用者最新明確的決定為準；發現衝突先列出差異再動手。
- 狀態（資料日期、在跑的流程、紅燈、名單、串接前向天數）不手寫，跑 `python -m quant_platform.research status`；回報的數字對得回程式輸出，沒核對過的不說做完（D-034）。

## 每種資訊只寫一個地方

| 資訊 | 寫在 |
|---|---|
| 使用者的決定（原話、日期、事故） | `docs/decisions.md`（D-015） |
| 硬規則；依工作類型的細節 | `AGENTS.md`；`docs/rules/` |
| 需求 | `REQUIREMENTS.md`（修改現行條文） |
| 交易規則；T0、截止日、可以上線的定義 | `docs/trading_rules.md`（引擎改版時同一個 commit 改它寫的版本，16:00 核對） |
| 階段、工作包、狀態、工時、日期表 | `docs/development_roadmap.md` |
| 架構與流程圖 | `docs/system_architecture.md` |
| 當前工作包、等使用者決定、下一步 | `docs/development_handoff.md`（只有這三欄） |
| 研究的現行結論、判定準則、結果紀錄 | `docs/research/conclusions.md`、`docs/research/criteria.md`、`docs/research/results.md` |
| 每輪交付、測試結果、證據、commit | `DEVELOPMENT_HISTORY.md`（最新在最上方） |

## 收尾

1. `DEVELOPMENT_HISTORY.md` 加一筆；交接檔換成下一個工作包；路線圖狀態；受影響的需求或架構。違反過規則的事件寫進開發歷程（D-015）。
2. 跑相關測試與 `tests/test_docs_guard.py`（字數上限、交接檔三欄、定義只一處、決定編號存在且都被引用、提到的檔案存在、路線圖與架構落後改程式的 commit 不超過 30 個）；16:00 的需求檢查每天也跑同一套。
3. 每個工作包至少一個 commit：`git -c user.name=Ryan -c user.email=ryan73093@gmail.com commit -F <訊息檔>`，不改 Git 全域設定；commit 後直接 push（D-041）。

## 使用者的決定

- 給使用者看的每一則文字都用白話的繁體中文（含進度、背景工作報告、每輪總結）；程式碼、commit 訊息、識別名稱可以用英文（D-010）。
- 新決定當輪加進 `docs/decisions.md`、在 `AGENTS.md` 或 `docs/rules/` 改一句並引用編號；能用程式核對的加進 `research/invariants.py`（D-015）。
- 不拿文件已經回答、或和現況無關的問題問使用者；真的只有使用者能決定、和當前工作有關才問，一次問清楚並附建議（D-014）。
- 不得自行對使用者的決定開例外（D-015）。D-023 的「同類的事直接照做、不要問」和它衝突，等使用者決定（交接檔）。
- 修正順序：先 D（文件與工作方式），再 B（系統站得住）、C（量尺）；P1 上線的路等 D、B、C 做完、先和使用者討論（D-028）。問題照清冊格式寫：問題、關鍵證據、導致、解法選項、排除後效益。

## 測試

- Python 變更跑相關測試：`.\.venv\Scripts\python.exe -m pytest tests\<檔案> -q -p no:cacheprovider --basetemp <可寫目錄>`（受限環境寫不進系統暫存的 `pytest-of-*` 時，`--basetemp` 指向工作階段暫存區或 `build\`）；階段結束跑全部測試並記錄通過數。

## Windows

- 主要 shell 是 Windows PowerShell 5.1；Git 在 `C:\Program Files\Git\cmd\git.exe`，PATH 沒更新時用完整路徑。
- 多行 commit 訊息寫進 UTF-8 無 BOM 的暫存檔（5.1 的 `Set-Content -Encoding utf8` 會加 BOM），用 `git commit -F <檔案>`，不要把 here-string 當參數。
- PowerShell 以 `python -c "..."` 執行含 `>`、`<` 或引號的 SQL 會被當成重導向；寫成腳本檔再執行。

## 寫作風格

文件採直接的工程語氣：目標 → 做法 → 驗收 → 待確認事項。完成狀態以證據更新；限制寫成「適用範圍、前提、下一個工作包」。
