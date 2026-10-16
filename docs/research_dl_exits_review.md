# 深度學習選股與學習出場：文獻回顧與重做計畫

更新：2026-10-17。使用者 2026-10-16 指正：深度學習（`dl-1.0.0`、`dl-risk-1.0.0`）與學習出場（`exit-1.0.0`）都只試了最簡單的版本，不能就此結案。本檔整理文獻與實務證據、說明 v1 為什麼失敗，並提出重做設計和**跑之前就要登錄的門檻**。這裡只有計畫，沒有試驗結果；結果出來後寫進 `docs/research_method.md` §7–§9 與 `DEVELOPMENT_HISTORY.md`。和 [AI 交易方法 實證與架構](../reports/AI%20交易方法%20實證與架構.md) 重疊的文獻（GKX、Avramov、Robeco、台灣 Bui 等、RL 重測）只摘要，細節看那份報告。

證據等級（每筆文獻都標）：

| 等級 | 意思 |
|---|---|
| A | 同儕審查、長期樣本外，而且含成本或有獨立重現 |
| B | 同儕審查、有樣本外，但只看毛報酬或多空組合，或只在美國 |
| C | 作者自己報的 benchmark（多半是 A 股 CSI300、測試期 2～3 年）或 working paper |
| D | 實務文章、模擬資料、單一短樣本 |

## 0. 結論

1. **dl-1.0.0 的 IC 好、前 20 名弱，主因是設計，不是深度學習本身**：標籤用排名、損失用 MSE、選 epoch 看 IC、輸入全部轉成百分位、只用 3 個種子。這五點都讓模型去學「整個橫斷面的排序」，而不是「最前面 2% 的股票」。gbm-1.0.0 犯過同一個錯（排名標籤 → 學成防守型），gbm-1.1.0 改成超額報酬就好了。
2. **文獻對 DL 的期待要放低**：在工程化因子上，樹模型通常不輸 DL（Qlib 的 Alpha158：LightGBM IC 0.0448、GRU 0.0315）；DL 的優勢出現在原始價量序列上（Alpha360：GRU 0.0493、LightGBM 0.0400）。跨股注意力模型（HIST、MASTER、FactorVAE）的增益都是作者自己在 A 股報的，沒有獨立重現，也沒有台灣的證據。比較實際的目標是讓 DL 成為 GBM 之外**相關性低的第二個模型**，再把兩者合併。
3. **風險模型不要拿來否決個股**：大跌風險本來就集中在漲多、量增的股票（Chen、Hong、Stein 2001），而動能也在不確定性高的股票裡最強（Zhang 2006）。所以「不買最危險的一成」在機制上就是反動能。文獻裡對動能有效的風險管理，是**整個策略依風險調整曝險**，不是剔除個股，而且證據多半來自美國的多空組合；平台自己試過三次「降曝險到現金」都失敗。建議改成：用預測風險決定**配置權重**、用相關群集做**分散上限**，否決只用在「扣掉波動與動能之後還剩的風險」。
4. **exit-1.0.0 過度交易，是決策設計的問題**：每天拿一個「20 日的價值」和一趟成本比，等於每檔持股每個月被檢查 20 次。只要預測有雜訊，就算真實優勢是 0，一個月內也幾乎一定會跌破門檻一次（§4.1 試算約 79%）。文獻的答案是把出場放回**考慮成本的整體投組決策**：有不交易區（buy/hold spread）、只往目標部分調整、門檻隨不確定性和檢查次數變大、價值要算進「明天還能再決定」（最佳停止）。
5. **建議順序**（期望值高、成本低的先做）：

| 順序 | 設計 | 理由 | 登錄試驗數 |
|---|---|---|---|
| 0 | 免費診斷（§2.4、§4.1） | 不登錄試驗，先確認失敗原因 | 0 |
| 1 | E1 成本與不確定性推導的不交易區，套在機器學習規則上 | 證據等級最高（Novy-Marx & Velikov）、平台最大的拖累就是機器學習規則的成本 | 2（兩個底層規則） |
| 2 | D1 DL 重做：超額標籤＋偏重前段的排序損失＋不轉排名的序列特徵＋10 個種子 | 直接修 v1 的五個設計錯誤 | 2（損失 A／B） |
| 3 | D2 DL 與 GBM 分數合併 | 文獻裡合併幾乎都比單一模型好，成本極低 | 1 |
| 4 | R1 預測風險決定配置權重（不否決） | 依波動配置在平台已經提高報酬；換成預測值成本低 | 1 |
| 5 | E2 每週決策的最佳停止出場（擬合 Q 迭代） | 修 v1「不知道明天還能再決定」的錯；歷史資料可算出兩個分支 | 1 |
| 6 | R2 相關群集上限 | 產業上限已經壓過回撤；群集是它的一般化 | 1 |
| 7 | D3 跨股注意力（D1 過了才做） | 證據只有 C 級 | 1 |
| 8 | E3 三重關卡＋後設標籤出場 | 證據 D 級，但給「趨勢結束了沒」一個機率 | 1 |
| 9 | R3 去掉波動之後的非對稱風險否決 | 殘差裡可能不剩多少資訊 | 1 |
| — | 暫不做：D4 端到端投組損失、E4 完整 RL 部位管理、R4 依整體風險降曝險到現金 | 同類做法在平台已失敗（RL 4.x、RL 1.x、大盤／帳戶濾網、vol_scale） | 0 |

合計 13 個登錄試驗，全部計入 DSR 的試驗數。

---

## 1. 共同規約（所有設計都適用）

- **先登錄再跑**：版本、標籤、損失、特徵、種子、門檻都在跑之前寫進試驗登錄；門檻沒過就停，不調結構或超參數再跑（只允許表上列出的變體）。
- **期間**：遵守 `research/daily.py` 新設計。模型逐年滾動：year Y 的模型只用標籤在 Y 之前結束的樣本，訓練與保留之間至少隔一個標籤期間（purge／embargo，López de Prado 2018）。模型層的門檻看 2016–2020 的樣本外；2020-10 之後只當檢查，不拿來挑；乾淨的證據只有前向觀察與每月滾動前向測試（`rolling.py`，模型要支援 `train_until`）。
- **資料時點**：籌碼表在收盤後公布，平台已經一律從下一個交易日起用（`chips.py` 的 `shift(1)`）。序列特徵必須沿用同一套已平移的面板，不能直接讀原始日表。月營收不放進新模型（使用者視為落後資訊）。
- **報告分段**：2020 前後、含或不含台積電、上漲期與下跌期分開報告；所有規則都是 100% 個股、不含 0050。
- **長時間程式**：用 `scripts\start-research.ps1` 背景啟動並登錄到 `instance/research/jobs/`，不在交易時段（13:30–14:40）跑。

---

## 2. 深度學習橫斷面選股

### 2.1 v1 的實際設定（`research/model_deep.py`）

GRU 32 單元、1 層；輸入是每檔最近 40 個交易日、14 個欄位，全部是當天在合格股票中的百分位（日報酬、成交變化、12 個因子）；標籤是之後 20 個交易日報酬的排名，損失 MSE；每週一筆樣本；逐年滾動，最後五分之一的週數做保留，用保留期的 IC 選 epoch（最多 8 個）；3 個種子平均。樣本外 IC 每年 +0.03～+0.13（平均約 0.075），但前 20 名只比中位數多 +0.2%／20 日（GBM 合併模型 +1.15%）。

### 2.2 證據摘要

**基準研究（美國為主）**

| 研究 | 做了什麼 | 結果 | 等級 | 對我們的意思 |
|---|---|---|---|---|
| Gu、Kelly、Xiu 2020，RFS（[PDF](https://dachxiu.chicagobooth.edu/download/ML.pdf)） | 美股 1957–2016，94 個特徵＋交互作用，樣本外 1987–2016 | 3 層網路月 R² 0.40%；多空十分位夏普 1.35（未扣成本）；每月周轉 110%～130%；4、5 層沒比 3 層好；網路用多個隨機種子合併 | B | 淺網路、多種子；毛報酬好，但周轉很高 |
| Chen、Pelger、Zhu 2024，Management Science（[arXiv](https://arxiv.org/abs/1904.00745)） | 用無套利條件當訓練目標（GAN 找最難定價的測試資產）＋LSTM 讀 178 個總經序列 | 作者報告樣本外夏普、解釋變異、定價誤差都優於前饋網路 | B | 「目標函數對準經濟目的」比網路結構重要；但這是多空 SDF，不是長多前 20 名 |
| Kelly、Kuznetsov、Malamud、Xu 2025，NBER w33351（[連結](https://www.nber.org/papers/w33351)） | 在 SDF 裡放 Transformer，跨股共享資訊（AIPM） | 未扣成本夏普很高；作者承認多半來自流動性差的股票、2002 年後少 26%、沒有成本與周轉分析 | C | 跨股注意力在美國多空 SDF 有用，長多、扣成本後未知 |
| Kelly、Malamud、Zhou 2024，JF（[SFI](https://www.sfi.ch/en/publications/the-virtue-of-complexity-in-return-prediction)）；Nagel 2025（[BFI](https://bfi.uchicago.edu/wp-content/uploads/2025/08/BFI_WP_2025-104.pdf)） | 「複雜模型比較好」 | Nagel：所謂複雜度的好處，有一部分等於依波動調整的動能 | B／C | 不要預期加大模型就會變好 |
| Avramov、Cheng、Metzker 2023，Management Science（[SSRN](https://papers.ssrn.com/abstract=3450322)） | 重做深度學習選股並加經濟限制 | 排除微型股、困境股後獲利大減（報導：−62%），加成本後再惡化；DL 訊號的長方比傳統異常因子好 | A | 在合格股票（≥10 元、日均 2,000 萬）裡預期會打折 |
| Jensen、Kelly、Malamud、Pedersen（RFS 即將刊出，[SSRN](https://papers.ssrn.com/abstract=4187217)） | Portfolio-ML：把成本放進目標、直接學權重 | 不考慮成本的報酬預測會依賴變化快的小型股特徵，扣成本後差；直接學權重的淨夏普較好 | B | 出場與換手要和選股一起考慮（見 §4） |
| Blitz、Hanauer、Hoogteijling、Howard 2023（[Robeco](https://www.robeco.com/en-uk/insights/2023/07/the-term-structure-of-machine-learning-alpha)） | 同一套 ML 改預測 1、3、6、12 個月 | 1 個月的模型 2004 年後扣成本失效；長期間的模型交易少、淨報酬好 | B | 平台的 gbm-1.3.0（60 日）卻挑不出領先股——期間要和用途分開：選股用 20 日，換手用持續性（§4 E1） |
| Fischer & Krauss 2018，EJOR（[討論稿](https://iwf.rw.fau.de/files/2015/12/11-2017.pdf)）；後續重現 | S&P 500 成分股，LSTM 每天選前 10 名 | 未扣成本每天 +0.46%（1992–2015），但主要來自 1990 年代，2008 年後每天不到 0.1% | B | 每日 DL 選股在成熟市場會衰減 |

**新興市場與台灣**

| 研究 | 結果 | 等級 |
|---|---|---|
| Hanauer & Kalsbach 2023，Emerging Markets Review（[Robeco](https://robeco.com/en-uk/insights/2023/10/using-machine-learning-for-emerging-market-equity-returns)；[Alpha Architect](https://alphaarchitect.com/machine-learning-and-emerging-market-stock-returns/)） | 32 個新興市場，非線性模型樣本外贏線性；扣成本、不能放空、只用大型股後仍有淨超額，**前提是用有效率的交易規則**；套利限制的影響比線性模型小 | A |
| Cakici、Fieberg、Metko、Zaremba 2023，JEDC（[連結](https://open.icm.edu.pl/items/f88ffcae-dc39-44e1-b48a-602d7612de55)） | 46 國、148 個特徵；預測力主要來自動能、反轉、價值、規模；**合併多個模型最好**；效果依市值與國家而異 | B |
| Bui、Kong、Lin、Lin 2023，Pacific-Basin Finance Journal（[RePEc](https://ideas.repec.org/a/eee/pacfin/v82y2023ics0927538x23002494.html)） | 台灣 86 個異常因子；神經網路與 PLS 多空每月 1.2%～1.5%；兩層網路樣本外 R² 0.92%，比深的好；小型股較可預測；前 20 重要變數有 5 個和動能有關 | B（台灣唯一的同儕審查 ML 橫斷面研究；多空、毛報酬） |
| Leippold、Wang、Zhou 2022，JFE（[SFI](https://www.sfi.ch/en/publications/machine-learning-in-the-chinese-stock-market)） | 中國：散戶多的小型股短期可預測性強；流動性與基本面比價格趨勢重要；扣成本後仍顯著 | A |
| Jiang、Kelly、Xiu 2023，JF（[Yale](https://economics.yale.edu/research/re-imagining-price-trends)） | 把 K 線圖丟進 CNN；學到的型態和傳統趨勢訊號不同，美國學的模型在國際市場一樣有效 | B |
| 台灣的 GRU／Transformer 橫斷面選股 | 搜尋只找到單檔、指數或碩博士論文，沒有長多、扣成本的同儕審查證據 | — |

**Qlib 基準與跨股模型（A 股 CSI300，作者自報，C 級）**

[Qlib benchmarks](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)（每個模型 20 個種子的平均 ± 標準差；組合是每天持有前 50 名、最多換 5 檔）：

| 特徵 | 模型 | IC | 年化超額 | IR |
|---|---|---|---|---|
| Alpha158（工程化因子） | LightGBM | 0.0448 | 9.0% | 1.02 |
| | DoubleEnsemble（GBDT＋樣本加權＋特徵挑選） | 0.0521 | 11.6% | 1.34 |
| | MLP | 0.0376 | 9.0%±2% | 1.14±0.23 |
| | XGBoost | 0.0498 | 7.8% | 0.91 |
| | GRU（20 個特徵） | 0.0315 | 3.4%±2% | 0.52±0.25 |
| | Transformer | 0.0264 | 2.7%±2% | 0.40±0.26 |
| Alpha360（原始 60 日價量） | LightGBM | 0.0400 | 5.6% | 0.76 |
| | GRU | 0.0493 | 7.2%±2% | 0.97±0.33 |
| | GATs（圖注意力） | 0.0476 | 8.2%±2% | 1.11±0.26 |
| | HIST（概念共享資訊） | 0.0522 | 9.9%±2% | 1.37±0.27 |

讀法：

- 工程化因子上，樹模型贏 DL；原始序列上，DL 贏樹模型。v1 把 GRU 放在它較弱的那一邊（全是因子百分位）。
- **IC 和前段報酬對不太起來**：MLP 的 IC 比 XGBoost 低，年化超額卻比較高；Alpha360 上 HIST 的 IC 和 GRU 差 0.003，年化差 2.7 個百分點，這個差距只比種子間的標準差（±2%）大一點。
- DL 模型的 IC 在不同種子間幾乎不變（±0.00），**年化超額卻有 ±2%、IR ±0.3**：前段選股對種子很敏感，3 個種子不夠。
- 跨股模型：HIST（[arXiv](https://arxiv.org/abs/2110.13716)）、MASTER（AAAI 2024，[arXiv](https://arxiv.org/abs/2312.15235)；盤中與跨股注意力交替、用市場資訊做特徵閘門，測試期只有 2020Q3–2022Q4）、FactorVAE（AAAI 2022，[連結](https://ojs.aaai.org/index.php/AAAI/article/view/20369)；用未來報酬引導的先驗-後驗訓練，同時輸出風險）、TRA（KDD 2021，[arXiv](https://arxiv.org/abs/2106.12950)）、StockFormer（IJCAI 2023，[連結](https://www.ijcai.org/proceedings/2023/0530)；Transformer＋RL）。都是作者自報、A 股或美股小樣本、測試期短、沒有獨立重現。

**排序損失、前段導向的訓練**

| 研究 | 結果 | 等級 |
|---|---|---|
| Poh、Lim、Zohren、Roberts 2021，JFDS（[arXiv](https://arxiv.org/abs/2012.07149)） | 美股 1980–2019、月換股、橫斷面動能特徵；LambdaMART 多空夏普 2.16、ListNet 1.97，MLP 回歸再排序 0.27、傳統動能 0.55。**沒有算成本** | B（多空、毛報酬） |
| Kwiatkowski & Chudziak 2025（[arXiv](https://arxiv.org/abs/2510.14156)） | 110 檔 S&P 500、測試約 1.5 年、每天前 5 名長多：Margin、ListNet 損失的夏普 0.75、0.74，MSE 0.66；**IC 最高的 RankNet 報酬只是中等** | D |
| Feng 等 2019，TOIS（RSR，[arXiv](https://arxiv.org/abs/1809.09441)） | 點估計＋成對排序損失，直接對準「選出報酬最高的股票」 | C |
| Clarke、de Silva、Thorley 2002，FAJ（[CFA](https://rpc.cfainstitute.org/research/financial-analysts-journal/2002/portfolio-constraints-and-the-fundamental-law-of-active-management)） | 轉移係數：限制（不能放空、周轉上限）讓 IC 轉成報酬的比例大降；報導的數字：周轉限 50% 時 0.73，再加不能放空等限制降到 0.3 | B（數字為二手） |
| Brandt、Santa-Clara、Valkanov 2009，RFS；Simon、Weibels、Zimmermann 2026，Management Science（[DPPP](https://pubsonline.informs.org/doi/10.1287/mnsc.2025.00721)） | 直接用特徵學投組權重、目標是效用；神經網路版每月確定等值報酬比線性多 43～102 bps，對成本與放空限制穩健 | B |

### 2.3 為什麼 IC 好、前 20 名弱

1. **排名標籤＋MSE 只獎勵「中間排對」**。排名把 +60% 和 +15% 壓成 1.0 和 0.9；MSE 的最佳解是條件期望排名。IC 0.075 時預測的變異只解釋約 0.6% 的標籤變異，分數擠在 0.5 附近，前 20 名由很小的差距決定。模型最容易學到的是「誰不會墊底」（低波動、高殖利率），這正是 gbm-1.0.0 的現象（分數和低波動、高殖利率相關 0.4～0.6），gbm-1.1.0 改成截尾超額報酬後前 20 名每年都正。
2. **IC 衡量整個橫斷面，長多前 20 名只用到最前面 2%**。IC 有一部分來自辨認輸家；異常因子的獲利多半來自空方（Avramov 等），我們不能放空。轉移係數的觀點相同：等權 20 檔、不能放空，IC 能轉成報酬的比例很低。
3. **選 epoch 用 IC**，等於再對錯的目標挑一次。
4. **輸入全是百分位**：丟掉幅度（漲停和 +2% 在某些天排名差不多）與市場層級的資訊（全市場一起跌時百分位不變）。Qlib 的結果顯示 DL 的優勢來自原始序列；v1 的輸入讓 GRU 只能學 GBM 已經會的事。
5. **種子太少**：Qlib 20 個種子的年化超額標準差約 2%，前段選股是分數的極端值，最受種子影響。GKX 也用多種子合併。

### 2.4 階段 0：免費診斷（描述，不登錄試驗）

用已經存的 `deep.npz` 分數與 GBM 分數算：

- 分數十分位 × 之後 20 日超額的曲線：v1 的優勢是不是集中在「避開最後一成」。
- 只在分數前五分之一裡算 IC（前段 IC）。
- 3 個種子各自的前 20 名重疊率（低於五成就表示前段幾乎是雜訊）。
- DL 與 GBM 分數的每日排序相關（判斷 D2 有沒有希望）。
- dl-risk 最高一成：之後 20 日超額報酬（如果是正的，就證實否決砍到贏家）、和 6 個月動能的相關。

### 2.5 樣本量

約 1,000 檔合格股票 × 2,800 個交易日 ≈ 280 萬個股票日，橫斷面很大；但 20 日標籤在時間上只有約 140 段互不重疊的期間，而且同一天的股票高度相關，「哪一種型態在哪一種市場有效」的有效樣本很小。文獻一致的做法：淺網路（GKX 3 層最好；台灣 Bui 等 2 層最好）、多種子合併、強正則化與提前停止。v1 的參數只有約 4,600 個，問題不在容量。可以增加的資料：每天都當樣本（約多 5 倍，但高度相關，需 purge）、訓練時放進上市＋上櫃全部約 1,900 檔（打分只在合格股票裡排）、同時學多個期間（多任務）。RTX 5070 Ti 16 GB 對這些規模都足夠（dl-risk 12 年 × 3 種子 4 分鐘；D1 估計每個變體 1 小時內）。

### 2.6 重做設計（依期望值排序）

#### D1　dl-2.0.0：對準前段的序列模型

- **標籤**：之後 20 個交易日報酬減當天合格股票中位數，每天頭尾 1% 截掉（與 gbm-1.1.0 相同）。
- **損失（兩個登錄變體）**：
  - A：Huber（δ = 5%）點估計 ＋ 1.0 × 偏重前段的 ListNet。每天一組；標籤分布為 softmax（超額 ÷ τ），τ = 當天超額標準差 × 0.5，讓前段的權重大；Huber 那一項保留幅度（只用排序損失會失去尺度）。
  - B：二元分類「之後 20 日是否在當天前 10%」，加權交叉熵（正例權重 9）。
- **輸入**（每天橫斷面 z 分數、截在 ±5，**不轉百分位**）：
  - 60 個交易日的序列：日超額報酬、日內高低幅度、收盤對 20／60／200 日均線、成交值對自己 60 日平均（log）、外資／投信／自營商買賣超佔流通股數、融資餘額變化、借券賣出餘額變化（籌碼用已平移一天的面板）。
  - t 日的快照：GBM 目前用的因子（不含月營收），接在 GRU 輸出後面。
  - 市場狀態：加權指數對 200 日均線、漲跌家數、指數 20 日報酬。
- **模型**：GRU 64 單元 1 層 → 和快照、市場狀態串接 → MLP（64、32，dropout 0.1）。約 3～4 萬個參數。
- **訓練**：每天都是樣本；year Y 只用標籤在 Y 之前結束的樣本；最後 20% 的日子做保留，中間隔 20 個交易日；epoch 用保留期的「前 20 名比中位數多」選（最多 20 個）；10 個種子，各自轉排名後平均。訓練用上市＋上櫃全部股票，打分只在規則的合格股票裡排。
- **事先登錄的門檻（模型層）**：2016–2020 樣本外每年「前 20 名之後 20 日超額 − 中位數」的平均 ≥ GBM 合併模型同年、同程式算出的值，而且 5 年裡至少 4 年 ≥ +0.6%（一趟成本）。2021–2026 平均 > 0 只當檢查。兩個變體都登錄，取哪一個在跑之前不決定；兩個都過才看規則層，都計入試驗數。
- **規則層**：前 20 名、同產業最多 3 成、每週決策、100% 個股，照選手池 T 等級判定。
- **主要失敗風險**：序列特徵只在小型股有用，合格股票池裡沒有增益；或 DL 只是重學 GBM 已有的因子（D2 會看到兩者相關太高）。

#### D2　DL＋GBM 合併分數

- **做法**：兩個模型的分數每天各自轉排名後 50／50 平均（比例事先固定，不學權重）。
- **依據**：Cakici 等 46 國、GKX、Qlib 的 DoubleEnsemble 都顯示合併最好；兩個不同家族的模型錯在不同地方。
- **門檻**：2016–2020 合併後前 20 名比 GBM 單獨多 ≥ 0.2 個百分點／20 日，而且兩者分數的每日排序相關平均 ≤ 0.7；規則層兩段期間的期末都要贏 GBM 單獨的同一個規則。
- **前提**：D1 至少有一個變體的 2016–2020 前 20 名差距 > 0（不必過 D1 門檻）。
- **風險**：DL 與 GBM 相關太高，合併只是多付計算。

#### D3　跨股注意力（MASTER／HIST 精簡版；D1 過了才做）

- **做法**：沿用 D1 的時間編碼；每天所有合格股票再過一層 4 頭自注意力（股票之間交換資訊），加上 MASTER 式的市場狀態閘門（市場狀態決定哪些特徵放大）。損失與訓練同 D1 過門檻的那個變體。
- **門檻**：2016–2020 前 20 名差距比 D1 多 ≥ 0.2 個百分點，而且 5 年裡至少 4 年較好；否則停。
- **風險**：A 股的增益（HIST 對 GRU：IC +0.003）比種子差異還小；沒有獨立重現、沒有台灣證據；參數多，有效樣本小。

#### D4　直接學投組（暫不做）

網路輸出分數 → 前 40 名的 softmax 權重 → 損失＝−（下週組合超額 − 0.6% × 換手）。Brandt 等、DPPP、Portfolio-ML 支持這個方向，但平台的 RL 4.x（sparsemax 自由權重）已經 T3，問題相同：目標雜訊大、容易背答案。D1 與 D2 都證明 DL 有訊號之後再考慮。

---

## 3. 風險模型怎麼用才不砍掉贏家

### 3.1 證據摘要

| 研究 | 結果 | 等級 | 對我們的意思 |
|---|---|---|---|
| Chen、Hong、Stein 2001，JFE（[NBER](https://www.nber.org/papers/w7687)） | 負偏態（大跌風險）在過去報酬高、成交量相對趨勢增加的股票最高 | A | 大跌模型必然偏向動能贏家 |
| Zhang 2006，JF（[PDF](https://www.cis.upenn.edu/~mkearns/finread/UncertaintyAndReturns.pdf)） | 資訊不確定性越高，好消息後的漂移越強 | A | 高風險股也是續漲最強的股票 |
| Barroso & Santa-Clara 2015，JFE（[連結](https://ciencia.ucp.pt/en/publications/momentum-has-its-moments/)） | 依動能自己的波動調整整個策略的曝險，幾乎消除崩盤、夏普約加倍 | B（美國多空） | 管的是**整個策略**，不是個股；而且需要可以放大曝險 |
| Daniel & Moskowitz 2016，JFE（[NBER](https://www.nber.org/papers/w20439)） | 動能崩盤發生在空頭後、波動高、市場急反彈時，主要是輸家反彈；動態調整讓夏普約加倍 | B（美國多空） | 崩盤主要來自輸家反彈；「長多只買贏家時崩盤較小」是評論者的推論（[Swedroe](https://www.etf.com/sections/index-investor-corner/swedroe-downside-momentum)），若成立，這個方法對我們的好處也較小 |
| Cederburg、O'Doherty、Wang、Yan 2020，JFE（[PDF](https://www.lehigh.edu/~xuy219/research/COWY.pdf)） | 103 個策略做波動管理，樣本外多數（報導 72 個）夏普與確定等值報酬變差 | A | 依風險降曝險的整體證據是負的 |
| Harvey 等 2018，JPM（[Duke](https://scholars.duke.edu/publication/1370354)） | 波動目標化對股票：左尾變輕、夏普小幅提高 | B | 不能加槓桿時，上漲年會少賺 |
| 平台自己（`research_method.md` §8） | 大盤 200 日均線濾網、帳戶 200 日均線濾網、依自己波動減碼到現金：全部期末大減、回撤只淺幾點；依波動配置權重：期末 1,794 → 2,126 萬、回撤沒變小；同產業最多 3 成：回撤 −52% → −45%；dl-risk 否決：16 個規則全變差 | — | 「降曝險」路線三次失敗；「配置」與「分散」兩條路有正面紀錄 |

結論：AUC 0.738 對 0.704 的增量很小，模型主要學的是波動；而高波動是對稱的，大跌機率高的股票大漲機率也高。否決最危險的一成就是砍掉右尾。

### 3.2 設計（依期望值排序）

#### R1　預測風險決定配置權重（不否決）

- **做法**：選股完全不變，每檔金額 ∝ 1／σ̂。σ̂ 是 DL 預測的之後 20 日下方半標準差（新標籤，和 dl-risk 同一個 GRU 結構），對照組是現在的「依 60 日實現波動配置」。
- **門檻**：事先列名單的 8 個最強 T0 候選中，至少 5 個回撤淺 ≥ 1 個百分點，且期末不少於原版的 95%。
- **風險**：預測波動和實現波動相關 > 0.9，結果幾乎一樣（先在階段 0 算這個相關）。

#### R2　相關群集上限

- **做法**：每月用過去 120 日報酬的相關做階層分群（群數事先固定，例如 15 群），同一群最多 3 成，加在產業上限之上。
- **依據**：追強勢股的回撤來自 20 檔一起回落；產業上限已經把回撤壓了 7 個百分點，但同一個題材常跨產業。
- **門檻**：8 個 T0 候選中至少 5 個回撤淺 ≥ 3 個百分點，期末損失 ≤ 10%。
- **風險**：長多 20 檔可以分散的空間有限；空頭時所有相關都升高。

#### R3　去掉波動之後的非對稱風險否決

- **做法**：新標籤「之後 20 日最低累積報酬 ≤ −2 × σ20」（σ20 ＝ 60 日日波動 × √20，用波動標準化），或同時學大跌與大漲、取「大跌機率 − 大漲機率」（兩個標籤二選一，跑之前決定，只登錄一個）。只否決在同一個波動十分位、同一個動能五分位裡風險最高的一成。
- **模型門檻**：波動十分位內的平均 AUC ≥ 0.55；2016–2020 被否決的那一成，之後 20 日超額 < 0 的年份 ≥ 4／5（證明否決的是輸家，不只是會大跌的股票）。
- **規則門檻**：8 個 T0 候選中至少 5 個期末不少於 95%、回撤淺 ≥ 2 個百分點。
- **風險**：Chen、Hong、Stein 的結果表示大跌資訊大多就是「漲多、量增」，扣掉波動與動能之後可能不剩多少。

不建議：R4 依整體預測風險把部位降到現金。文獻整體是負的（Cederburg 等），平台三次失敗，而有效的動能風險管理是在美國多空、可以放大曝險的條件下。

---

## 4. 學習出場與部位管理

### 4.1 exit-1.0.0 為什麼過度交易

設定：疊在趨勢 T1 規則上，每個交易日對每檔持股預測「留著比換成下一名，之後 20 日多賺多少」，少於 −0.6%（一趟成本）就賣，20 個交易日內不買回。訓練樣本是規則自己的持股、每週一筆。結果每月約 380 筆委託、一年周轉 93 次、費稅吃掉投入的 56%，期末 43 萬。

四個設計錯誤：

1. **重複檢查的雜訊**：假設真實優勢是 0，預測誤差的標準差 1%，單次跌破 −0.6% 的機率 Φ(−0.6) ≈ 27%。預測每天高度相關，一個月 20 次檢查約等於 5 次獨立檢查，至少跌破一次的機率 ≈ 1 − 0.73⁵ ≈ **79%**。也就是沒有任何訊號，幾乎每檔持股一個月內也會被賣掉。要把一個月的誤賣率壓到 5%，門檻要約 2.3 倍預測誤差，而不是一趟成本。
2. **沒有把「明天還能再決定」算進價值**：這是最佳停止問題。可以等的選擇權本身有價值，門檻應該比一次性決策嚴格（Dixit 1989：進出決策在沉沒成本與不確定性下有遲滯區，不確定性越大區間越寬）。
3. **成本沒有攤到實際持有期間**：一次換股的好處只持續到替補股票自己被賣掉為止。策略一旦每天換，替補平均只持有一兩天，20 日的優勢只實現一小部分，成本卻每次都付全額。正確的比法是「每天的優勢 × 替補預期持有天數 ≥ 成本」，而持有天數又取決於策略本身——過度交易會自我強化。
4. **訓練與使用不一致**：訓練是規則持股、每週一筆；使用是每天、而且持股已經被出場模型改變過（分布偏移）。20 天不買回又讓帳戶一直換到名次較後的股票。

模型本身的辨別力也很弱（10 年只有 4 年「賣掉的比留著的差」，平均差 0.6%）。Akepanidtaworn、Di Mascio、Imas、Schmidt 2023（JF，[NBER](https://www.nber.org/papers/w29076)；783 個機構投組、440 萬筆交易）發現專業經理人**買得好、賣得差，賣出決策輸給隨機賣**，損失和買進賺到的差不多。「學會該賣哪一檔」本來就難，所以任何學習出場都必須和「隨機賣同樣多檔」比。

階段 0 的免費診斷：用 exit-1.0.0 存下的預測，算預測的日變動標準差、跌破門檻的頻率，以及「跌破之後隔天又回到門檻上」的比例，驗證上面的試算。

### 4.2 證據摘要

| 研究 | 結果 | 等級 | 對我們的意思 |
|---|---|---|---|
| Novy-Marx & Velikov 2016，RFS（[NBER](https://www.nber.org/papers/w20721)） | 「買進門檻比續抱門檻嚴」（buy/hold spread）是**最有效的單一降成本方法**；每月周轉 < 50% 的異常因子設計得當時淨報酬仍顯著 | A | 出場的第一個答案是不交易區，不是預測模型 |
| Gârleanu & Pedersen 2013，JF（[NBER](https://www.nber.org/papers/w15205)） | 有成本與可預測報酬時：瞄準「現在與未來最佳部位的加權平均」、每次只往目標部分調整；衰減慢的訊號權重較高 | A（理論＋期貨實證） | 換手幅度要看訊號的持續性 |
| Martin & Schöneborn 2011（[arXiv](https://arxiv.org/abs/1103.4934)）；de Lataillade 等 2012（[arXiv](https://arxiv.org/abs/1203.5957)） | 線性成本下最佳策略有緩衝區；成本小時緩衝寬度約與成本的立方根成正比（比成本本身大得多）；門檻可由動態規劃算出 | 理論 | 門檻應由成本與訊號持續性推導，不是固定一趟成本 |
| DeMiguel、Martín-Utrera、Nogales、Uppal 2020，RFS（[RePEc](https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2180-2222..html)） | 有成本時，合併多個特徵可以讓不同特徵要求的交易互相抵銷，顯著特徵從 6 個變 15 個 | A | 合併分數（D2）本身就能降換手 |
| Jensen 等（Portfolio-ML，[SSRN](https://papers.ssrn.com/abstract=4187217)） | 把成本放進目標、直接學權重，淨夏普較好 | B | 同上 |
| Hanauer & Kalsbach 2023 | 新興市場 ML 的淨超額要靠有效率的交易規則才留得住 | A | 同上 |
| [Qlib TopkDropout](https://qlib.readthedocs.io/en/latest/component/strategy.html) | 實務：持有前 K 名，每天最多換 n_drop 檔，周轉 ≈ 2 × n_drop ÷ K | 實務 | 換股數上限是簡單的安全閥 |
| Shiryaev、Xu、Zhou 2008，Quantitative Finance（討論見 [Majumdar & Bouchaud](https://arxiv.org/abs/0809.2878)） | 幾何布朗運動下的最佳賣出時點是「全有或全無」：好股票抱到底、壞股票立刻賣 | 理論 | 出場的核心是判斷「這檔的趨勢還在不在」，不是每天比 20 日報酬 |
| Dai、Zhang、Zhu 2010，SIFIN（[SIAM](https://epubs.siam.org/doi/10.1137/090770552)）；Dai 等 2016，MOR（[連結](https://pubsonline.informs.org/doi/10.1287/moor.2015.0743)） | 多空兩狀態的趨勢追蹤：以「處於多頭的後驗機率」低於門檻曲線時賣，門檻由成本與動態規劃決定 | 理論 | E3 的分類器就是在估這個後驗機率 |
| Longstaff & Schwartz 2001，RFS；Becker、Cheridito、Jentzen 2019，JMLR（[arXiv](https://arxiv.org/abs/1804.05394)） | 用回歸估「繼續持有的價值」、由後往前推（最小平方蒙地卡羅）；深度版的最佳停止 | 理論／方法 | E2 的方法 |
| Ernst、Geurts、Wehenkel 2005，JMLR（[連結](https://jmlr.org/papers/v6/ernst05a.html)）；Metelli 等 2020，ICML（[arXiv](https://arxiv.org/abs/2002.06836)） | 擬合 Q 迭代（批次 RL，每次一個樹模型回歸）；動作持續（降低決策頻率）能改善學到的策略 | 方法 | 用現有的提升樹做多步出場、每週才決策 |
| Moody & Saffell 2001，IEEE TNN；Zhang、Zohren、Roberts 2020（[arXiv](https://arxiv.org/abs/1911.10107)） | 把成本放進獎勵的 RL 會學到少交易；期貨上在重成本下仍賺錢、趨勢中不換部位 | B／C（期貨、時間序列） | 只證明「成本進獎勵」有用，不證明個股出場能學 |
| Kruthof & Müller 2025（[RePEc](https://ideas.repec.org/a/eee/finlet/v75y2025ics154461232500131x.html)）；FinRL 重現性 | 股票 RL 300 多年樣本外不贏 1／N，加 0.1% 成本變負；結果難重現 | A | 完整 RL 部位管理暫不做 |
| López de Prado 2018（AFML 第 3 章）；Joubert 2022、Meyer 等 2023（JFDS） | 三重關卡標籤（停利、停損、時間）、後設標籤（第二個模型決定要不要照第一個模型做、部位多大）；校準後的機率改善固定規則的部位大小 | D（主要是模擬與實務） | E3 的方法，證據弱 |
| Lo & Remorov 2017，JFM（[MIT](https://dspace.mit.edu/handle/1721.1/107017)）；Kaminski & Lo 2014 | 美股個股：緊的停損因成本與負自我相關輸給買進持有；寬停損在多空狀態切換的資產上可以降下方風險 | A | 平台 15% 移動停損失敗與此一致；出場門檻要寬、要看狀態 |
| Han、Zhou、Zhu 2016（[摘要](https://alphaarchitect.com/taming-the-momentum-roller-coaster-fact-or-fiction/)） | 動能多空加 10% 停損，最差月大幅改善；未扣成本 | C | 好處主要來自空方，長多參考價值有限 |

### 4.3 重做設計（依期望值排序）

#### E1　成本與不確定性推導的不交易區（先做）

- **底層**：兩個（各登錄一次）：機器學習（含財報）每週前 20 名、同產業最多 3 成、100% 個股（每月 30～37 筆、費稅佔投入 113%～131%）；趨勢 T1（站上 200 日均線、依波動配置）。
- **把分數換成預期報酬**：每個訓練年用等滲回歸（isotonic）把模型分數對應到之後 20 日超額 μ̂；只用該年以前的資料。
- **持續性**：分數在 20 個交易日後的橫斷面自我相關 ρ（只用訓練年估），預期持有期間 H = 1 ÷ (1 − ρ)（以 20 日為單位）。這就是 Gârleanu-Pedersen 的「衰減慢的訊號權重高」。
- **換股條件**（每週決策日）：把持股 i 換成最好的未持有股票 j，只在
  (μ̂ⱼ − μ̂ᵢ) × H − z × σ̂Δ × H ≥ c
  時成立。c = 0.6%（一趟成本）；σ̂Δ 是合併模型各成員（或自助樣本模型）對這個差的標準差；z = 1。條件依序檢查，**每週最多換 3 檔**（TopkDropout 式安全閥）。
- **硬性事件**（下市、跌停賣不掉、處置）照引擎原本規則。
- **門檻**（每個底層各自判定）：費稅佔投入少 ≥ 30%；2015-06 起期末 ≥ 原規則；2020-10 起期末不少於原規則的 95%；回撤不深於原規則 2 個百分點以上。
- **為什麼不算參數搜尋**：c、z、上限 3 檔在跑之前固定，H 與 σ̂Δ 由訓練資料推導，不看回測結果調。
- **風險**：分數校準不準（μ̂ 的尺度錯，H 再放大錯誤）；分數衰減比估計快，留下太多已經變弱的股票。

#### E2　每週決策的最佳停止出場（擬合 Q 迭代，exit-2.0.0）

- **決策點**：每週決策日；動作 = 留（a = 0）或換成規則的下一名（a = 1）。
- **狀態**：exit-1.0.0 的 12 個欄位，加上持股的模型分數與名次、替補的分數、兩者差距、持有週數、分數的持續性估計。
- **一步報酬**（5 個交易日，減掉當期合格股票中位數報酬 m，只看「這個位置」的超額）：留＝rᵢ − m；換＝rⱼ − m − c。
- **Q 迭代**：Q₍ₖ₊₁₎(x, a) = r(x, a) + γ × maxₐ′ Qₖ(x′, a′)，γ = 0.97／週，迭代 8 次，每次一個 `HistGradientBoostingRegressor`（設定同 exit-1.0.0）。x′ 是「留下的股票」或「替補股票」下週的狀態。**我們的零股委託不影響價格，兩個分支都能從歷史資料算出來**，所以不需要探索、沒有 exit-1.0.0 那種只學到自己持股的偏差。
- **樣本**：每週、規則排名前 60 名的所有股票都當成「假如它是持股」的狀態（不只規則實際的持股）。
- **決策**：換 if Q(x, 1) − Q(x, 0) > z × s(x)，s(x) 是 5 個自助樣本模型的標準差，z = 1；每週最多換 3 檔。
- **訓練**：逐年滾動；year Y 只用在 Y 前 60 個交易日以前就結束的轉移。
- **模型層門檻（先算，不過就不進引擎）**：2016–2020 樣本外，模型選擇換掉的那些持股，之後 60 個交易日「持股 − 替補」平均 ≤ −1.2%（兩趟成本），而且 5 年裡至少 4 年比「隨機換同樣多檔」差；每週平均換股 ≤ 3 檔。
- **規則層門檻**：兩段期間的期末都 ≥ 底層規則、每月委託 ≤ 底層的 1.2 倍、回撤不深於 2 個百分點以上。
- **風險**：辨別力仍然不足（像 v1）→ 模型幾乎從不換，結果等於底層規則（無害、也沒有好處）；Q 迭代的外推誤差（迭代次數少、用自助樣本估不確定性來控制）。

#### E3　三重關卡＋後設標籤出場

- **做法**：每週對每檔持股標記：之後先碰到上關卡（+1.5 × σ20）、下關卡（−1.5 × σ20）還是時間關卡（40 個交易日）。標籤＝先碰到下關卡。GBM 分類器（狀態同 E2），在訓練年最後 20% 用等滲回歸校準機率。
- **賣出條件**：對稱關卡下持有的期望值約為 (1 − 2p̂) × 1.5 × σ20，所以在 p̂ ≥ 0.5 + (c + z × se) ÷ (3 × σ20) 時賣（c = 0.6%、z = 1）；每週最多 3 檔。
- **門檻**：2016–2020 的 Brier 技巧分數比基準率高 ≥ 0.02；賣掉的比留著的之後 40 日差 ≥ 1.2%，5 年裡至少 4 年；規則層同 E2。
- **依據與風險**：它在估 Dai 等模型裡「還在多頭的機率」；但三重關卡與後設標籤的實證多是模擬與實務文章（D 級），平台的 15% 移動停損已經失敗。

#### E4　完整 RL 部位管理（暫不做）

個股 RL 的獨立重測是負的，平台 6 個 RL 版本都沒有贏過固定比例或自己的對照。E2 的擬合 Q 迭代已經是「把成本與未來決策算進價值」的最小版本；E2 在模型層門檻就失敗的話，更大的 RL 沒有理由會成功。

---

## 5. 待確認事項

1. E1 的 c、z、每週上限 3 檔是事先固定、不搜尋的常數；使用者是否同意這不算「只差參數的相似策略」？
2. 訓練 D1 時放進上櫃與小型股（打分只在合格股票裡排），是否接受？
3. R2 的群數、R3 的兩個標籤擇一，要在跑之前由使用者或研究者決定並登錄。
4. 全部 13 個登錄試驗都會讓 DSR 門檻變高；建議依 §0 的順序做，前面的沒過就重新評估後面的，不要一次全跑。

## 參考文獻（本檔新增；其餘見 AI 交易方法報告）

- Akepanidtaworn, Di Mascio, Imas, Schmidt (2023). Selling Fast and Buying Slow. *JF* 78(6). https://www.nber.org/papers/w29076
- Avramov, Cheng, Metzker (2023). Machine Learning vs. Economic Restrictions. *Management Science*. https://papers.ssrn.com/abstract=3450322
- Barroso, Santa-Clara (2015). Momentum Has Its Moments. *JFE*. https://ciencia.ucp.pt/en/publications/momentum-has-its-moments/
- Becker, Cheridito, Jentzen (2019). Deep Optimal Stopping. *JMLR*. https://arxiv.org/abs/1804.05394
- Blitz, Hanauer, Hoogteijling, Howard (2023). The Term Structure of Machine Learning Alpha. https://www.robeco.com/en-uk/insights/2023/07/the-term-structure-of-machine-learning-alpha
- Brandt, Santa-Clara, Valkanov (2009). Parametric Portfolio Policies. *RFS* 22(9).
- Bui, Kong, Lin, Lin (2023). Momentum in Machine Learning: Evidence from the Taiwan Stock Market. *PBFJ* 82. https://ideas.repec.org/a/eee/pacfin/v82y2023ics0927538x23002494.html
- Cakici, Fieberg, Metko, Zaremba (2023). Machine Learning Goes Global. *JEDC* 155. https://open.icm.edu.pl/items/f88ffcae-dc39-44e1-b48a-602d7612de55
- Cederburg, O'Doherty, Wang, Yan (2020). On the Performance of Volatility-Managed Portfolios. *JFE* 138(1). https://www.lehigh.edu/~xuy219/research/COWY.pdf
- Chen, Hong, Stein (2001). Forecasting Crashes. *JFE* 61(3). https://www.nber.org/papers/w7687
- Chen, Pelger, Zhu (2024). Deep Learning in Asset Pricing. *Management Science*. https://arxiv.org/abs/1904.00745
- Clarke, de Silva, Thorley (2002). Portfolio Constraints and the Fundamental Law of Active Management. *FAJ* 58(5). https://rpc.cfainstitute.org/research/financial-analysts-journal/2002/portfolio-constraints-and-the-fundamental-law-of-active-management
- Dai, Zhang, Zhu (2010). Trend Following Trading under a Regime Switching Model. *SIFIN* 1. https://epubs.siam.org/doi/10.1137/090770552
- Daniel, Moskowitz (2016). Momentum Crashes. *JFE*. https://www.nber.org/papers/w20439
- de Lataillade, Deremble, Potters, Bouchaud (2012). Optimal Trading with Linear Costs. https://arxiv.org/abs/1203.5957
- DeMiguel, Martín-Utrera, Nogales, Uppal (2020). A Transaction-Cost Perspective on the Multitude of Firm Characteristics. *RFS* 33(5). https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2180-2222..html
- Dixit (1989). Entry and Exit Decisions under Uncertainty. *JPE* 97(3).
- Duan, Wang, Zhang, Li (2022). FactorVAE. *AAAI*. https://ojs.aaai.org/index.php/AAAI/article/view/20369
- Ernst, Geurts, Wehenkel (2005). Tree-Based Batch Mode Reinforcement Learning. *JMLR* 6. https://jmlr.org/papers/v6/ernst05a.html
- Feng et al. (2019). Temporal Relational Ranking for Stock Prediction. *TOIS*. https://arxiv.org/abs/1809.09441
- Fischer, Krauss (2018). Deep Learning with LSTM Networks for Financial Market Predictions. *EJOR*. https://iwf.rw.fau.de/files/2015/12/11-2017.pdf
- Gao, Wang, Yang (2023). StockFormer. *IJCAI*. https://www.ijcai.org/proceedings/2023/0530
- Gârleanu, Pedersen (2013). Dynamic Trading with Predictable Returns and Transaction Costs. *JF* 68(6). https://www.nber.org/papers/w15205
- Gu, Kelly, Xiu (2020). Empirical Asset Pricing via Machine Learning. *RFS*. https://dachxiu.chicagobooth.edu/download/ML.pdf
- Han, Zhou, Zhu (2016). Taming Momentum Crashes: A Simple Stop-Loss Strategy. https://alphaarchitect.com/taming-the-momentum-roller-coaster-fact-or-fiction/
- Hanauer, Kalsbach (2023). Machine Learning and the Cross-Section of Emerging Market Stock Returns. *EMR*. https://robeco.com/en-uk/insights/2023/10/using-machine-learning-for-emerging-market-equity-returns
- Harvey et al. (2018). The Impact of Volatility Targeting. *JPM* 45(1). https://scholars.duke.edu/publication/1370354
- Jensen, Kelly, Malamud, Pedersen. Machine Learning and the Implementable Efficient Frontier. *RFS*（即將刊出）. https://papers.ssrn.com/abstract=4187217
- Jiang, Kelly, Xiu (2023). (Re-)Imag(in)ing Price Trends. *JF* 78. https://economics.yale.edu/research/re-imagining-price-trends
- Kelly, Kuznetsov, Malamud, Xu (2025). Artificial Intelligence Asset Pricing Models. NBER w33351. https://www.nber.org/papers/w33351
- Kelly, Malamud, Zhou (2024). The Virtue of Complexity in Return Prediction. *JF* 79(1). https://www.sfi.ch/en/publications/the-virtue-of-complexity-in-return-prediction
- Kruthof, Müller (2025). *Finance Research Letters* 75. https://ideas.repec.org/a/eee/finlet/v75y2025ics154461232500131x.html
- Kwiatkowski, Chudziak (2025). On Evaluating Loss Functions for Stock Ranking. https://arxiv.org/abs/2510.14156
- Leippold, Wang, Zhou (2022). Machine Learning in the Chinese Stock Market. *JFE* 145. https://www.sfi.ch/en/publications/machine-learning-in-the-chinese-stock-market
- Li et al. (2024). MASTER: Market-Guided Stock Transformer. *AAAI*. https://arxiv.org/abs/2312.15235
- Lin et al. (2021). Temporal Routing Adaptor (TRA). *KDD*. https://arxiv.org/abs/2106.12950
- Lo, Remorov (2017). Stop-Loss Strategies with Serial Correlation, Regime Switching, and Transaction Costs. *JFM* 34. https://dspace.mit.edu/handle/1721.1/107017
- Longstaff, Schwartz (2001). Valuing American Options by Simulation. *RFS* 14(1).
- López de Prado (2018). *Advances in Financial Machine Learning*. Wiley.
- Martin, Schöneborn (2011). Mean Reversion Pays, but Costs. https://arxiv.org/abs/1103.4934
- Metelli et al. (2020). Control Frequency Adaptation via Action Persistence in Batch RL. *ICML*. https://arxiv.org/abs/2002.06836
- Microsoft Qlib benchmarks. https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md ；TopkDropout：https://qlib.readthedocs.io/en/latest/component/strategy.html
- Moody, Saffell (2001). Learning to Trade via Direct Reinforcement. *IEEE TNN* 12(4).
- Nagel (2025). BFI WP 2025-104. https://bfi.uchicago.edu/wp-content/uploads/2025/08/BFI_WP_2025-104.pdf
- Novy-Marx, Velikov (2016). A Taxonomy of Anomalies and Their Trading Costs. *RFS* 29(1). https://www.nber.org/papers/w20721
- Poh, Lim, Zohren, Roberts (2021). Building Cross-Sectional Systematic Strategies by Learning to Rank. *JFDS*. https://arxiv.org/abs/2012.07149
- Shiryaev, Xu, Zhou (2008). Thou Shalt Buy and Hold. *Quantitative Finance* 8(8)；討論：https://arxiv.org/abs/0809.2878
- Simon, Weibels, Zimmermann (2026). Deep Parametric Portfolio Policies. *Management Science*. https://pubsonline.informs.org/doi/10.1287/mnsc.2025.00721
- Xu et al. (2021). HIST. https://arxiv.org/abs/2110.13716
- Zhang (2006). Information Uncertainty and Stock Returns. *JF* 61(1). https://www.cis.upenn.edu/~mkearns/finread/UncertaintyAndReturns.pdf
- Zhang, Zohren, Roberts (2020). Deep Reinforcement Learning for Trading. *JFDS*. https://arxiv.org/abs/1911.10107
