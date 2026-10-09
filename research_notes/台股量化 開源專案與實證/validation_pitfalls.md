# 回測可信度：防過度擬合的驗證方法、公開回測常見陷阱、發表後衰退（含台股資料陷阱）

> 研究範圍：專業與嚴肅散戶量化如何判斷回測是否為真；公開（GitHub／部落格）回測不可信的常見原因；異象發表後的衰退。
> 記號：SR = 每期（月或日）夏普或資訊比率（IR，超額報酬 ÷ 追蹤誤差）；T = 觀測期數；N = 獨立試驗數；γ3 = 偏態；γ4 = 峰態（非超額，常態 = 3）；Φ = 標準常態 CDF；γ ≈ 0.5772（Euler–Mascheroni 常數）。
> 工作試算以 `.venv` 的 scipy 計算（腳本在工作階段暫存區，不在專案內），數字標為「試算」。

## 問題一：約 150 次試驗後找到的策略、月超額 +0.2%～1.2%、追蹤誤差約 18%／年，DSR 與最短紀錄長度公式表示前向觀察要跑多久？

### Takeaway
以 2015-06～2026-09（約 136 個月）回測、150 次獨立試驗計算，純運氣下「最好的那一個」的期望月超額約 **+1.19%**（年化 IR ≈ 0.79），剛好是題目區間的上緣；也就是說，區間內每個數字在 N=150 下都和運氣無法區分（DSR ≤ 0.51）。改看前向（新資料）期：在 95% 單尾信心下證明 IR>0，年化 IR 0.8（月超額 1.2%）約需 **4.4 年**，IR 0.4（0.6%）約需 **17 年**，IR 0.13（0.2%）約需 **152 年**；如果再把衰退打五折，所需年數變成四倍。結論：前向期只能當「否決／停損」工具，幾年內不可能「證明」0.2%～0.8% 月超額的能力。

### Cited Findings
- **PSR 與夏普估計量的變異（含偏態、峰態）**：PSR(SR*) = Φ[ (SR̂ − SR*)·√(T−1) / √(1 − γ3·SR̂ + ((γ4−1)/4)·SR̂²) ]；分母就是 SR̂ 的漸近變異，負偏、厚尾會放大它。— [LuxAlgo DSR 概念頁](https://www.luxalgo.com/library/concept/deflated-sharpe-ratio.md)；[PortfolioOptimizer：PSR 與 MinTRL](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/)
- **最短紀錄長度（MinTRL，Bailey & López de Prado 2012，“The Sharpe Ratio Efficient Frontier”，Journal of Risk）**：MinTRL = 1 + (1 − γ3·SR̂ + ((γ4−1)/4)·SR̂²)·(z₁₋α / (SR̂ − SR*))²，單位與 SR 的期數相同（月資料就是月）。PortfolioOptimizer 版本省略 “1+”，因為它用 T 而非 T−1 計算。— [PortfolioOptimizer](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/)；[Journal of Risk 條目](https://www.risk.net/journal-risk/2223785/sharpe-ratio-efficient-frontier)
- **MinTRL 實例**：一個年化 SR≈1、136 個月觀測的 BTC RSI 策略，要在 95% 信心下證明真實年化 SR>0.75，需要約 184 個月，也就是還要再約 48 個月（約 4 年）。— [PortfolioOptimizer](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/)
- **期望最大夏普（多重試驗門檻）**：SR₀ = √V·[(1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e))]，V 是各試驗 SR 的變異；**DSR = PSR(SR₀)**。輸入必須用「每期」SR，否則 DSR 會飽和到接近 1；N=1 時 SR₀=0，DSR 退化為對 0 的 PSR。— [LuxAlgo](https://www.luxalgo.com/library/concept/deflated-sharpe-ratio.md)；[fynance 實作原始碼](https://fynance.readthedocs.io/en/v2.10.0/_modules/fynance/research/guards.html)；原文：Bailey & López de Prado 2014，JPM 40(5):94–107 — [SSRN 2460551](https://papers.ssrn.com/abstract=2460551)
- **DSR 論文的數值例**（自原文 PDF 擷取文字）：某策略 5 年日資料（T=1250）年化 SR 2.5，投資人要求揭露 N、試驗間 SR 變異、T、偏態與峰態後，算出真實 SR>0 的機率只有約 90%，未達 95% 而拒絕；若只做了 N=46 次獨立試驗，DSR 會是 0.9505 而可接受；若報酬為常態，可容許到 N=88。— [Bailey & López de Prado, The Deflated Sharpe Ratio（作者網站 PDF）](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- **原文立場**：「幾乎所有學術期刊與投資提案中的回測都缺少最重要的資訊：試驗次數」；沒有控制搜尋範圍的回測「不論表現多好都沒有價值」。— [Bailey & López de Prado 2014 PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- **何時停止測試（1/e 法則）**：在理論上站得住的設定集合中，先隨機抽約 37% 測量，之後逐一測試，遇到第一個勝過先前全部的就停。— [Bailey & López de Prado 2014 PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- **N 的計算是最難的部分**：研究者常低報 N；相關性高的參數組不應視為獨立試驗，可用特徵值或 PCA／分群估計有效試驗數。— [LuxAlgo](https://www.luxalgo.com/library/concept/deflated-sharpe-ratio.md)；[Pseudo-Mathematics 論文頁](https://scholarworks.wmich.edu/math_pubs/40/)

### Inferences（試算；假設月追蹤誤差 = 18%/√12 = 5.196%，報酬常態，試驗間 SR 變異 V = 1/T，即「全部無能力、只有抽樣雜訊」的基準）
- **換算**：月超額 0.2%／0.4%／0.6%／0.8%／1.0%／1.2% → 月 IR 0.039／0.077／0.116／0.154／0.193／0.231 → 年化 IR 0.13／0.27／0.40／0.53／0.67／0.80。
- **運氣門檻 SR₀（T=136 個月）**：N=10 → 年化 0.47（相當於月超額 0.70%）；N=30 → 0.62（0.92%）；**N=150 → 0.79（1.19%）**。即：150 個毫無能力的規則，最好的那個平均就會顯示約 +1.19%／月 的超額。
- **DSR（T=136）**：

  | 月超額 | N=1（PSR） | N=10 | N=30 | N=150 |
  |---|---|---|---|---|
  | 0.2% | 0.67 | 0.13 | 0.05 | 0.01 |
  | 0.6% | 0.91 | 0.41 | 0.24 | 0.09 |
  | 1.0% | 0.99 | 0.75 | 0.57 | 0.34 |
  | 1.2% | 0.996 | 0.86 | 0.73 | 0.51 |

  沒有任何一格在 N≥10 時達到 0.95。
- **前向期所需長度（新資料，N=1，對 0 檢定）**：

  | 月超額 | 年化 IR | MinTRL 95% 單尾 | 80% 檢定力 | t ≥ 1.96 | t ≥ 3（HLZ 門檻） |
  |---|---|---|---|---|---|
  | 0.2% | 0.13 | 152 年 | 348 年 | 216 年 | 506 年 |
  | 0.4% | 0.27 | 38 年 | 87 年 | 54 年 | 127 年 |
  | 0.6% | 0.40 | 17 年 | 39 年 | 24 年 | 56 年 |
  | 0.8% | 0.53 | 9.7 年 | 22 年 | 13.5 年 | 32 年 |
  | 1.0% | 0.67 | 6.3 年 | 14 年 | 8.6 年 | 20 年 |
  | 1.2% | 0.80 | 4.4 年 | 9.7 年 | 6.0 年 | 14 年 |

  簡化式：年數 ≈ (z / 年化IR)²；80% 檢定力 ≈ ((1.645 + 0.842) / 年化IR)² ≈ 6.2 / IR²。
- **若實盤 IR 只剩回測的一半**（McLean–Pontiff 發表後下降 58%，見問題四）：月 1.2% 的策略要 17 年、1.0% 要 24.5 年才達 95% MinTRL。
- **非常態影響有限**：月超額 1.0%、偏態 −0.5、峰態 6 時，MinTRL 由 6.3 年增為 7.0 年。IR 很小時，影響 MinTRL 的主要是 IR 本身，偏態與峰態是次要的。
- **MinBTL 對照**：MinBTL ≈ 2·ln N / E[max]²（年）。N=150、想要年化 0.8 的運氣上限：約 15.7 年回測；N=30：10.6 年。這和 11.3 年（2015-06 起）的開發期加驗證期相比，說明 150 次試驗已超過資料長度能支撐的次數。
- **前向期的實用定位（試算）**：真實年化 IR 0.8 的策略，一年落後基準的機率 Φ(−0.8)=21%，三年 8%，五年 4%；真實 IR 0.4：一年 34%、三年 24%、五年 19%；IR=0（純運氣）：任何期間都是 50%。所以前向期幾年內最多能「否決」明顯失效的規則，無法「確認」小幅超額。
- **對本專案的含意**：
  - 如果 2020-10 之後的驗證期對每個規則只評估一次，它就是一個新資料檢定，但約 6 年資料只有在年化 IR ≥ 1.645/√6 ≈ 0.67（月超額約 1.0%）時才會顯著。
  - 如果有多個規則都進入驗證期（例如「開發期與驗證期都贏就跑 qualified」），驗證期本身又成為多重檢定，計算 DSR 時應把進入驗證期的規則數當作 N。
  - 相關試驗應先分群估計有效 N，V 改用 150 次試驗的實際 IR 變異，而不是上面的 1/T 基準。
  - 定期定額帳戶的「超額」應以時間加權報酬計算，再算 IR；資金加權報酬會因本金逐月變大而扭曲 TE 與 IR。

### Gaps
- 未能取得 DSR 論文數值例的完整參數：PDF 公式是圖形字型，擷取不到；依記憶是 N=100、V=1/2、偏態 −3、峰態 10，但這組參數未經核實。
- 未取得 MinTRL 原始論文（Journal of Risk 2012）全文。上面的公式來自兩個彼此一致的二手來源，以及 DSR 原文的關鍵字段落。
- 「150 次試驗」的實際相關結構與 V 值需要用專案試驗紀錄計算，這裡只用了無能力基準。

## 問題二：專業機構與嚴肅散戶建議怎樣的驗證流程？

### Takeaway
共識是：(1) 事先寫下假設與試驗計畫，並記錄所有試驗（N）；(2) 單一路徑的 walk-forward 或單次 holdout 不夠，要用 purged/embargoed CV 或 CPCV 取得多條路徑，再用 PBO 衡量「樣本內最佳在樣本外落入後半」的機率；(3) 用 DSR、Harvey–Liu 調整或 t>3 門檻處理多重檢定；(4) 真正的樣本外只有實盤與新資料，而且不可根據實盤結果調整模型。Quantopian 888 個策略的實證顯示，回測夏普幾乎無法預測樣本外表現（R² < 0.025），回測做得越多，落差越大。

### Cited Findings
- **Quantopian（Wiecki 等 2016，“All that Glitters Is Not Gold”）**：
  - 888 個至少有 6 個月樣本外表現的策略，回測夏普對樣本外表現的解釋力 R² < 0.025。
  - 波動、最大回撤等高階動差與避險等組合特徵有顯著預測力。
  - 回測次數越多，回測與樣本外的落差越大。
  - 用非線性模型以回測行為特徵預測，在 hold-out 上可達 R² = 0.17。
  - 樣本內 2010 至 2015 上半年，樣本外 2015-06～2016-02。
  - 作者皆為 Quantopian 員工。
  - 來源：[Quantpedia 摘要](https://quantpedia.com/quantopians-academic-paper-about-in-vs-out-of-sample-performance-of-trading-alg/)；[CXO Advisory](https://www.cxoadvisory.com/big-ideas/in-sample-vs-out-of-sample-performance-of-888-trading-strategies)；SSRN 2745220
- **Harvey, Liu & Zhu 2016（RFS 29(1):5–68）**：考慮大量資料探勘後，新因子的 t 值門檻應約 3.0，而不是 2.0；他們認為金融經濟學多數已發表的發現可能是錯的。— [NBER w20592](https://www.nber.org/papers/w20592)；[SSRN 2513152](https://papers.ssrn.com/abstract=2513152)。他們整理的因子數為 316 個（313 篇文章）。— [arXiv 2206.15365](https://arxiv.org/pdf/2206.15365)，該文另主張多數已發表的預測因子「可能為真」，是反方觀點。
- **Harvey & Liu 2015 “Backtesting”（JPM 41(1):13–28）**：
  - 用 Bonferroni、Holm、BHY 調整 p 值，換回調整後 t 值與夏普，得到「折扣（haircut）」。
  - 他們指出常見的「夏普打五折」只是經驗法則，套用它是嚴重錯誤：折扣是非線性的，年化夏普 <0.4 時折扣幾乎都 >50%，>1.0 時最多約 25%（後者數字來自 2013 工作論文的摘要）。
  - 作者偏好 BHY。
  - 來源：[Duke PDF](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF)；[CXO 摘要](https://www.cxoadvisory.com/big-ideas/navigating-the-data-snooping-icebergs/)；[quantstrat profitHurdle](https://rdrr.io/github/braverock/quantstrat/man/profitHurdle.html)
- **MinBTL（Bailey, Borwein, López de Prado & Zhu 2014，Notices of the AMS）**：
  - MinBTL < 2·ln N / E[max_N]²（年）。
  - 試驗越多，回測過度擬合的機率越高；多數分析師與學者不報告試驗次數。
  - 只試 7 種設定，就預期能找到一個 2 年回測年化夏普 >1、但樣本外期望夏普為 0 的策略。
  - 只有 5 年日資料時，不應試超過 45 種變化。
  - 來源：[論文頁](https://scholarworks.wmich.edu/math_pubs/40/)；[IAQF 講座摘要](https://iaqf.org/event-813217)；[Berkeley 2017 簡報](https://cdar.berkeley.edu/sites/default/files/dhb-risk-2017.pdf)
- **PBO 與 CSCV（Bailey, Borwein, López de Prado & Zhu 2017，J. Computational Finance 20(4)）**：
  - 把報酬期間切成 S 段，列舉全部 C(S, S/2) 種「一半樣本內、一半樣本外」的組合。
  - 每種組合中，取樣本內最佳設定，計算它在樣本外的相對排名 r，再轉成 λ = ln(r/(1−r))。
  - PBO = λ ≤ 0（樣本內冠軍在樣本外落入後半）的比例。
  - 也可畫樣本外對樣本內的迴歸：斜率接近 0 或為負，代表樣本內排名沒有資訊。
  - 作者認為傳統 hold-out 用在回測評估上不夠。
  - 來源：[論文頁](https://scholarworks.wmich.edu/math_pubs/42)；[CRAN pbo 套件](https://packages.oit.ncsu.edu/cran/web/packages/pbo/readme/README.html)；[fynance PBO](https://fynance.readthedocs.io/en/latest/generated/fynance.research.pbo.html)
- **Purged CV、embargo、CPCV（López de Prado，《Advances in Financial Machine Learning》2018 第 7 章）**：
  - walk-forward 只評估一條歷史路徑，容易調到符合那條路徑，而且變異大。
  - purging：刪除標籤期間與測試期重疊的訓練樣本。
  - embargo：在測試期後加間隔，處理殘餘序列相關（一個經驗值是測試集筆數的 1%）。
  - CPCV：把資料切 N 塊，每次取 k 塊做測試，例如 6 選 2 有 15 組，可組出多條回測路徑，得到夏普的分布而不是單一數字。
  - purging 不能防止特徵本身用到未來資料，也不能處理大量試驗造成的選擇偏誤，這些仍需另外控制。
  - 來源：[Wikipedia：Purged cross-validation](https://en.wikipedia.org/wiki/Purged_cross-validation)；[Towards AI：CPCV](https://towardsai.com/p/l/the-combinatorial-purged-cross-validation-method)；[LuxAlgo](https://www.luxalgo.com/library/concept/purged-cross-validation/)
- **López de Prado「回測不是研究工具」**：
  - 回測只估計規則從某個型態賺了多少，不能說明那個型態是訊號還是雜訊。
  - 最普遍的錯誤是「跑演算法 → 回測 → 重複，直到回測好看」。
  - 主要期刊的多數回測沒有校正多重檢定。
  - 來源：[Oxford Business Law Blog 2018](https://blogs.law.ox.ac.uk/business-law-blog/blog/2018/03/machine-learning-funds-and-investment-malpractice)；[GARP：The 10 Reasons Most ML Funds Fail](https://www.garp.org/white-paper/the-10-reasons-most-machine-learning-funds-fail)；[mathinvestor.org](https://mathinvestor.org/2017/08/mathematics-and-economics-a-reality-check/)
  - HKUST 講義的看法：回測不是實驗、不證明任何事，但可作為部位大小、週轉率、成本承受度與情境的健全性檢查。— [Palomar 講義](https://palomar.home.ece.ust.hk/MAFS5310_lectures/slides_backtesting.pdf)
- **Arnott, Harvey & Markowitz 2019 “A Backtesting Protocol in the Era of Machine Learning”（JFDS 1(1):64–74）**：
  - 不要隨意挑選、轉換、清理或 winsorize 資料。
  - 資料量通常不足以支撐增加的模型複雜度。
  - 歷史已被反覆檢視，真正的樣本外只剩實盤資料與新的歷史資料。
  - 注意結構變化；不要依實盤結果「微調」模型。
  - 獎勵好的流程，而不是好的結果。
  - 論文用「股票代號第三個字母」做出漂亮回測，示範假結果可以多麼逼真。
  - 來源：[SSRN 3275654](https://papers.ssrn.com/abstract=3275654)；[Alpha Architect 摘要](https://alphaarchitect.com/protocol-to-prevent-quants-gone-wild/)；[Research Affiliates](https://www.researchaffiliates.com/insights/journal-papers/702-a-backtesting-protocol-in-the-era-of-machine-learning)
- **CFM（Capital Fund Management）**：提出折算樣本內回測損益的框架，並指出團隊行為也是過度擬合來源：回測不如預期時，策略被拆成基本構件逐一檢視。— [arXiv 1902.01802 “How should you discount your backtest PnL?”](https://arxiv.org/pdf/1902.01802)
- **AQR（Ilmanen 等 2021，JOIM 19(4)）**：
  - 價值、動能、carry、防禦四個因子在六類資產、約一世紀資料上都存在樣本外，但幅度縮小。
  - 樣本外溢酬估計約下降 30%，較可能是原研究過度擬合，而不是發表後的套利交易。
  - 作者把每個因子分成「發現期、發現前、發表後」三段，作為過度擬合檢定。
  - 因子擇時可預測性有限，可能不足以抵銷成本。
  - 來源：[AQR](https://www.aqr.com/Insights/Research/Journal-Article/How-Do-Factor-Premia-Vary-Over-Time-A-Century-of-Evidence)；[SSRN 3400998](https://papers.ssrn.com/abstract=3400998)
- **Man AHL 的說法**：Bloomberg 2016 報導的摘要提到，Man AHL 花三年建立機器學習模型；另有量化人士稱演算法在實盤測試中的失敗率達 90%。原文付費，只看到摘要。— [Bloomberg](https://www.bloomberg.com/news/articles/2016-11-10/hedge-funds-beware-most-machine-learning-talk-is-really-hokum)

### Inferences
- **可操作的流程（綜合以上來源）**：
  1. 先寫假設與經濟理由，預先登錄試驗計畫和 N 的上限（MinBTL 反推：N ≈ exp(年數 × E[max]² / 2)；11.3 年資料、運氣上限年化 0.8，約只容許 37 個有效試驗）。
  2. 所有試驗都登錄，含失敗的。
  3. 開發期用 CPCV 或 CSCV 算 PBO，PBO 應明顯低於 0.5。
  4. 用 DSR（V 與有效 N 取自實際試驗）或 Harvey–Liu BHY 折扣，把夏普轉成「扣除選擇偏誤」的值。
  5. 驗證期每條規則只評估一次，並把進入驗證期的規則數計入 N。
  6. 前向或模擬交易期事先定好停損條件（例如 2～3 年落後幅度），不可依前向結果微調。
- Quantopian 的結果暗示：選規則時，回撤、波動、成本承受度、跨期穩定性等特徵，比回測夏普或超額報酬本身更可靠。

### Gaps
- 沒有找到 Two Sigma、AQR、Man 公開的「內部孵化期多長」具體數字；Man AHL 的方法只見於付費報導摘要。
- 沒有取得 AFML 第 7 章原文中 CPCV 的建議參數（只來自部落格與 Wikipedia）。
- PBO 的 logit 公式與 λ ≤ 0 判準取自套件文件，未直接核對論文原文。

## 問題三：公開回測（GitHub／部落格）常見的失效原因

### Takeaway
公開回測最常見的失效有：用到發布前的資料（look-ahead，包含財報與營收日期、還原價）、只用現存股票（survivorship）、忽略成本與稅和無法成交的情況（漲跌停、處置股、零股）、用收盤價產生訊號又以同一收盤價成交、資料窺探與挑選期間，以及不揭露試驗次數。每一項單獨就足以把「看起來的 alpha」變成假象。

### Cited Findings
- **倖存者偏差**：
  - 只用目前上市公司的資料集，會漏掉破產或下市的公司，使策略看起來更強。— [LuxAlgo](https://www.luxalgo.com/blog/survivorship-bias-in-backtesting-explained)
  - 美股例：深度價值組合年化報酬在未納入下市資訊時為 30.1%，納入後降到 24.6%。— [Alpha Architect：Dealing with delistings](https://alphaarchitect.com/dealing-with-delistings-a-critical-aspect-for-stock-selection-research/)
  - Yahoo Finance 通常沒有下市代號的歷史價格。— [R-bloggers](https://r-bloggers.com/2021/08/when-yahoo-finance-doesnt-have-de-listed-tickers-needed)
- **微型股與加權方式**：微型股只占總市值 3.2%，卻占股票檔數 60.7%，等權重與寬鬆斷點會讓微型股主導結果。— [Hou, Xue & Zhang 2020（IDEAS）](https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html)；[Alpha Architect](https://alphaarchitect.com/replicating-anomalies/)
- **資料窺探與試驗數**：
  - 回測次數越多，樣本外落差越大。— [Quantpedia（Quantopian 研究）](https://quantpedia.com/quantopians-academic-paper-about-in-vs-out-of-sample-performance-of-trading-alg/)
  - 不揭露試驗數的回測沒有價值。— [Bailey & López de Prado 2014 PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
  - 只試 7 個設定就能預期出現 2 年夏普 >1 的假策略。— [IAQF](https://iaqf.org/event-813217)
- **還原價**：還原價 = 原始價 × 還原係數；台股配股會增加股數，單純從價格扣股息不夠。yfinance 的 Close 已做拆股與配股調整，Adj Close 再加股息調整，重複調整會出錯（第三方文件，非官方）。— [iThome 鐵人賽](https://ithelp.ithome.com.tw/articles/10358317)；[thelinuxcode yfinance 指南](https://thelinuxcode.com/what-the-yfinance-library-is-and-isnt-a-practical-guide-to-yahoo-finance-data-in-python/)
- **規則需事先定義**：規則必須在回測開始前全部定好，不能看到結果後再修改。— [Arnott, Harvey & Markowitz 摘要](https://alphaarchitect.com/protocol-to-prevent-quants-gone-wild/)

### Inferences
- **還原價的前視偏差**：後還原價（以今天為基準往回調整）在每次除權息後都會改寫過去價位。以價格門檻（例如「股價 < 10 元」、整張金額、跳動單位、漲跌停判斷）或成交價模擬時，必須用當時的原始價；股息另以現金流計入。只有報酬率計算適合用還原價。
- **收盤成交**：用收盤價算出訊號後又在同一收盤價成交，是最常見的隱性前視。合理做法是次日開盤或次日 VWAP 成交，或改用台股盤後定價（見問題四），但後者有成交不確定性。
- **漲跌停無法成交**：鎖漲停時買不到、鎖跌停時賣不掉；用收盤價成交的回測，會在最強與最弱的日子拿到不可能的成交。
- **期間挑選**：2015-06 之後的台股剛好包含長期多頭與電子股集中，同一規則換成 2008 前後或 2000 年代初可能完全不同。至少要報告分段結果與最差子期間。
- **公開專案的紅旗清單**：
  - 只有一條權益曲線，沒有 N 與失敗試驗。
  - 樣本內外沒有分開，或樣本外被反覆使用。
  - 股票池是今天的成分股。
  - 財報與營收用季底或月底日期當可用日。
  - 沒有手續費、證交稅與滑價。
  - 等權重持有大量微型股。
  - 年化夏普 >2 但沒有說明容量。
  - 只報告勝率。

### Gaps
- 找不到對台股 GitHub 開源回測專案做系統性「失效稽核」的研究；本節多為通則加推論。

## 問題四：台股特有的資料陷阱

### Takeaway
台股回測要以「可公開取得日」對齊資料：月營收最晚在次月 10 日，季報在 5/15、8/14、11/14，年報 3/31（資本額 100 億以上與金融保險業 3/16）。還要把下市櫃股、上櫃轉上市造成的代號後綴切換、除權息與減資、7%→10% 漲跌幅（2015-06-01）、處置股撮合限制（2026-08-10 起新制），以及零股撮合規則建模。成本至少是手續費 0.1425%（買賣各一次，最低 20 元）加證交稅 0.3%（賣出）。

### Cited Findings
- **月營收**：
  - 證交法第 36 條要求上市公司在每月 10 日前公告並申報上月營運情形；遇假日順延至下一個上班日；逾期罰 24 萬～240 萬元。
  - 2026 年起，多數金控可延至每月 15 日申報。
  - 來源：[MoneyWeekly](https://www.moneyweekly.com.tw/_Article?AID=150373)；[經濟日報](https://money.udn.com/money/story/5607/9162636)
- **季報與年報期限**：
  - 一般公司：Q1 5/15、Q2 8/14、Q3 11/14、年報次年 3/31。
  - 實收資本額 100 億元以上及金融保險業的年報提前到 3/16（2025 年度報告）；金控的季報期限較晚，例如 Q3 為 11/29，遇假日順延。
  - 來源：[經濟日報](https://money.udn.com/money/story/5607/9360080)；[台灣英文新聞（2021 Q3 共 941 家，11/14 遇假日順延至 11/15）](https://www.taiwannews.com.tw/zh/news/4334181)；[證交所手冊 PDF](https://www.twse.com.tw/staticFiles/listed/manual/ff80808167ee6fdf0169dc26e2960aaf.pdf)
  - 有文章把大型企業年報期限寫成 3/15，與證交所公告的 3/16 不同，應以證交所為準。
- **漲跌幅**：2015-06-01 起，上市與上櫃漲跌幅由 7% 放寬為 10%；跌停價要依升降單位調整，不一定剛好 10%。— [CMoney](https://www.cmoney.tw/notes/note-detail.aspx?nid=32436)；[ctyeh](https://ctyeh.com/finance/15062)
- **交易成本**：
  - 手續費 0.1425%，買賣各收一次，未滿 20 元按 20 元計。
  - 證交稅一般股票 0.3%，只在賣出時收；現股當沖 0.15%（減半期限各來源說法不同，需查最新公告）。
  - 來源：[Money101](https://www.money101.com.tw/blog/%e8%b2%b7%e8%b3%a3%e8%82%a1%e7%a5%a8%e6%89%8b%e7%ba%8c%e8%b2%bb%e6%80%8e%e9%ba%bc%e7%ae%97%ef%bc%9f%e4%b8%80%e5%bc%b5%e5%9c%96%e8%a1%a8%e8%ae%93%e4%bd%a0%e4%b8%80%e7%9c%8b%e5%b0%b1%e6%87%82)
- **處置股（舊制，適用 2026-08-10 以前的回測期間）**：
  - 第一次處置約每 5 分鐘撮合一次；單筆 10 張以上或單日累計 30 張以上須預收款券。
  - 第二次處置（30 個營業日內第二次以上）約每 20 分鐘撮合一次，全部委託預收款券。
  - 處置期間 10 個營業日（當沖占比過高者 12 日）；停止融資融券與當沖。
  - 來源：[股感](https://www.stockfeel.com.tw/%E8%99%95%E7%BD%AE%E8%82%A1%E7%A5%A8%E6%98%AF%E4%BB%80%E9%BA%BC%E6%84%8F%E6%80%9D%EF%BC%9F%E8%B7%9F%E5%85%A8%E9%A1%8D%E4%BA%A4%E5%89%B2%E8%82%A1%E6%9C%89%E4%BB%80%E9%BA%BC%E4%B8%8D%E5%90%8C%EF%BC%9F)；[NOWnews](https://www.nownews.com/news/6862698)
- **處置股（新制，2026-08-10 起）**：
  - 處置期間縮為 5 個營業日（當沖占比過高者 7 日）。
  - 撮合改為約每 2 分鐘一次；仍保留預收款券或圈存。
  - 每半年檢討一次制度。
  - 來源：[經濟日報：處置期變 5 日、每 2 分鐘撮合](https://money.udn.com/money/amp/story/5607/9668249)；[經濟日報：新機制 8/10 上路](https://money.udn.com/money/amp/story/5607/9668143)
- **零股**：
  - 盤中零股自 2024-12-02 起每 5 秒撮合（先前 2022-12-19 由 3 分鐘縮為 1 分鐘）。
  - 證交所規劃 2027-07 縮為 1 秒、首撮提前到 9:00（尚未上路）。
  - 盤後零股 13:40～14:30 收單，14:30 一次集合競價。
  - 盤後定價交易 14:00～14:30 以整股、當日收盤價成交，與盤後零股不同。
  - 來源：[TechNews：12/2 起 5 秒](https://finance.technews.tw/2024/11/25/odd-lot-trade-matching-interval-reduced-to-5-seconds/)；[經濟日報：縮短至 1 秒](https://money.udn.com/money/amp/story/5607/9614528)；[證交所交易制度說明 PDF](https://www.twse.com.tw/downloads/zh/trading/introduce/introduce4-1.pdf)；[ctyeh](https://ctyeh.com/finance/14959)
- **下市資料**：
  - 政府開放資料平台有證交所「終止上市公司」資料集（含終止日期與代號，每日更新），只涵蓋上市。— [data.gov.tw 11543](https://data.gov.tw/en/datasets/11543)
  - 本次沒有找到 FinMind 官方對下市櫃資料集的說明。— [FinMind PyPI](https://pypi.org/project/FinMind/)
- **yfinance 與台股**：
  - 上櫃股要用 `.TWO` 後綴，否則抓不到資料。— [iThome](https://ithelp.ithome.com.tw/articles/10358317)
  - 2023 年一篇 iThome 文章說 yfinance 台股只有 2008 年後的資料、沒有上櫃股，與前一條矛盾，可能已過時。— [iThome 10310700](https://ithelp.ithome.com.tw/articles/10310700)
  - twstock 會自動判斷上市或上櫃，資料來自證交所與櫃買中心。— [twstock 文件](https://twstock.readthedocs.io/zh-tw/latest/reference/stock.html)

### Inferences
- **營收與財報可用日**：
  - 月營收不要用「營收月份月底」或「次月 1 日」當可用日。保守做法是次月 11 日（或順延後的第一個交易日）開盤後才可用；最精確的是用 MOPS 實際公告日。
  - 季報以法定期限隔日為可用日；要提前使用，就必須有逐家實際公告日。
  - 期限規則歷年有變（例如大型公司年報 3/16 的規定），回測應以當年規則建表。
- **代號與市場切換**：上櫃轉上市時，代號通常不變但市場別改變。yfinance 用 `.TW` 或 `.TWO` 擇一抓取時，可能只抓到一段歷史；要以轉板日分段合併並檢查接縫。下市股則要補價格，並明確設定退場報酬：併購以收購價，經營不善下市要計入大幅虧損。
- **處置股與零股**：
  - 回測期間大多處於舊制。處置股在 5 或 20 分鐘撮合、預收款券的限制下，策略的成交價與可成交量都不同。被處置的往往正是近期大漲、動能策略最想買的股票。
  - 以 30 萬啟動資金加每月 1 萬投入時，高價股常只能買零股。盤後零股一天只撮合一次，成交量小、價格可能偏離收盤，應該模擬價差與未成交情況。
- **漲停買不到**：若隔日開盤即漲停且全天鎖住，市價買單大多無法成交，回測應判為未成交。這對動能與營收突破類規則的影響最大。
- **成本基準**：一次完整買賣約 0.1425%×2 + 0.3% ≈ 0.585%（未打折），零股還有最低 20 元手續費，例如買 5,000 元的零股，手續費率就變成 0.4%。

### Gaps
- FinMind `TaiwanStockMonthRevenue` 的 `date` 欄位意義沒有查到官方文件，不確定它是營收月份的次月 1 日還是實際公告日。若是前者，用它當可用日會有最多約 10 天的前視偏差，需要以本機資料抽查（例如比對 MOPS 公告日）。
- FinMind 是否有完整的下市櫃名單與下市股價格，未查到官方說明。
- 盤中零股開放日期（記憶中是 2020-10-26）未在本次來源中核實。
- 櫃買中心下櫃名單的開放資料來源未找到。
- 處置新制的預收門檻（舊制是單筆 10 張、單日 30 張）在新制是否改變，未找到明確說明。

## 問題五：異象發表後的衰退與對實盤預期的含意

### Takeaway
學術異象在樣本外平均下降約 26%，發表後下降約 58%。AQR 的百年資料顯示樣本外約下降 30%，並歸因於過度擬合。Hou–Xue–Zhang 在控制微型股後，有 65% 的異象無法通過 |t| ≥ 1.96，82% 無法通過 2.78。Jensen–Kelly–Pedersen 則認為多數因子可以重現，但同樣看到發表後衰退。實務上，自己挑出來的回測超額至少應打五折再看，越是從大量試驗中挑出的「最好」結果，折扣越大。

### Cited Findings
- **McLean & Pontiff 2016（J. Finance 71(1)）**：
  - 97 個報酬預測因子（79 篇論文）的組合報酬，在樣本外下降 26%，發表後下降 58%。
  - 樣本外下降是資料探勘效果的上限估計；58% − 26% = 32% 歸因於讀了論文的投資人交易。
  - 樣本內報酬越高的因子，發表後下降越多。
  - 早期版本的數字不同（例如 35%，或 25% 與 56%），引用時應以 2016 年刊出版本為準。
  - 來源：[Gwern 存檔 PDF](https://Www.Gwern.net/doc/economics/2016-mclean.pdf)；[Counterpoint Funds PDF](https://counterpointfunds.com/wp-content/uploads/2017/07/PredictabilityMcleanPontiff.pdf)
- **Hou, Xue & Zhang 2020（RFS 33(5)）**：
  - 用 NYSE 斷點加市值加權處理微型股後，452 個異象中有 65%（交易摩擦類 96%）無法通過 |t| ≥ 1.96；門檻提高到 2.78 時失敗率 82%。
  - 能重現的異象，經濟規模也比原文小得多；價值、動能、投資、獲利類表現最好。
  - 有學者批評這篇論文誇大了「重現危機」。
  - 來源：[IDEAS](https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html)；[Alpha Architect](https://alphaarchitect.com/replicating-anomalies/)
- **Jensen, Kelly & Pedersen 2023（J. Finance 78(5)）**：
  - 貝氏模型下，多數因子可重現；美股有超過八成因子在一致化建構與多重檢定調整後仍顯著；93 國的新資料中仍然有效。
  - 他們也看到發表後衰退但仍為正，樣本內越強的因子衰退越多。
  - 對 Hou–Xue–Zhang 的回應：許多「失敗」是原本就不顯著的因子。
  - 來源：[NBER w28432](https://www.nber.org/papers/w28432)；[Alpha Architect](https://alphaarchitect.com/is-there-a-replication-crisis-in-finance/)
- **Ilmanen 等（AQR）2021**：樣本外溢酬約下降 30%，較可能來自過度擬合，而非套利交易。— [AQR](https://www.aqr.com/Insights/Research/Journal-Article/How-Do-Factor-Premia-Vary-Over-Time-A-Century-of-Evidence)
- **系統化策略衰退**：一篇 arXiv 論文估計，樣本外夏普約為樣本內的 57%（下降 43%）。— [arXiv 2105.01380 “Why and how systematic strategies decay”](https://arxiv.org/pdf/2105.01380)
- **Quantopian**：回測夏普幾乎無法預測樣本外表現（R² < 0.025）。— [CXO Advisory](https://www.cxoadvisory.com/big-ideas/in-sample-vs-out-of-sample-performance-of-888-trading-strategies)

### Inferences
- 學術因子已經過同儕審查與較長期間的檢驗，仍下降約 26%～58%。散戶或 AI 在單一市場、約 11 年資料、約 150 次試驗中找到的規則，合理的預期衰退應不少於這個範圍。以問題一的試算，N=150 時連月 1.2% 的回測超額都只到運氣門檻，期望的真實超額可能接近 0。
- **規劃用折扣**：
  - 回測月超額 × 0.4～0.5 當實盤預期，依 McLean–Pontiff 的 58% 與 arXiv 的 43%。
  - 從大量試驗中挑出的冠軍，先用 DSR 扣除選擇偏誤，再乘上衰退折扣。
  - 樣本內越強，預期衰退越大（McLean–Pontiff 與 JKP 都觀察到）。
- **方向上的含意**：可重現性最高的是有經濟理由的大類因子（價值、動能、獲利、投資）。這符合專案「先找強因子、再組合不同策略」的方向；參數微調型的變體在多重檢定下最容易落入假陽性。

### Gaps
- 沒有找到台股專屬的「發表後衰退」大樣本研究；上述數字主要來自美股與全球資料。
- JKP 論文中發表後衰退的具體百分比未取得原表，只有二手摘要。
