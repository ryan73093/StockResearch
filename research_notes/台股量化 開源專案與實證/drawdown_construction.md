# 只做多選股（動能／趨勢）組合：什麼方法真的能降低回撤、組合怎麼建構、要付出多少報酬

查證日期：2026-10-09。來源以學術論文摘要、作者自架 PDF、AQR／Alpha Architect／CXO Advisory／Quantpedia／Robeco 等研究摘要為主；多數全文無法取得，數字以摘要或二手整理為準，引用前應回原文核對版本（工作論文與期刊版數字常不同）。
與本專案的對照：20 檔個股、只做多、週／日調整，基準是同一筆現金流全部買 0050。**文獻中大部分降回撤證據來自多空（long-short）因子組合，下文逐條標示「多空」或「只做多」。**

## 1. 動能崩盤文獻與波動度縮放（Daniel & Moskowitz 2016、Barroso & Santa-Clara 2015、動態動能）對「只做多」動能有沒有用

### Takeaway
動能崩盤主要來自空方（過去輸家在空頭後的反彈中「往上崩」），只做多組合的「崩盤」形態是：跟大盤一起跌，然後在 V 型反彈中大幅落後大盤；波動度縮放在多空因子上把 Sharpe 約提高一倍、幾乎消除崩盤，但真實時點（out-of-sample）、扣成本後的證據大幅縮水，而且我找不到直接檢驗「只做多動能＋波動度縮放到現金」的嚴謹研究——對只做多組合，波動度縮放等同於「高波動時降持股、留現金」的擇時，最接近的證據是指數層級的波動度目標（Sharpe 0.40→約 0.50，左尾變薄）。

### Cited Findings
- **崩盤由空方驅動（多空）**：Daniel & Moskowitz（JFE 122(2), 2016）指出空頭市場中上漲／下跌 beta 的不對稱「大部分由過去輸家造成」；大跌之後，輸家十分位的 beta 可升到 3 以上，贏家則低於 0.5，因此多空動能在快速反彈時等同持有很大的負 beta。— [NBER w20439](https://www.nber.org/papers/w20439)、[NBER PDF](https://www.nber.org/system/files/working_papers/w20439/w20439.pdf)
- **崩盤的具體幅度**：1932 年那段，過去輸家十分位上漲 232%，贏家只漲 32%；2009 年輸家漲 163%，贏家只漲 8%。崩盤出現在「恐慌狀態」：市場下跌之後、波動高的時候，並與市場反彈同時發生。— [NBER PDF](https://www.nber.org/system/files/working_papers/w20439/w20439.pdf)、[Chicago Booth Review](https://www.chicagobooth.edu/review/understanding-momentum-crashes)
- Daniel & Moskowitz 的可實作動態動能（依預測的動能均值與變異數調整曝險）約把靜態動能的 alpha 與 Sharpe 提高一倍（多空）。— [NBER w20439](https://www.nber.org/papers/w20439)
- **只做多的說法來自評論者而非原論文**：Larry Swedroe 認為只做多動能不會出現那麼深的崩盤；Stockopedia 指出去掉空方能保留大部分長期報酬、避開最嚴重的崩盤，但代價是不再市場中性，會跟大盤一起回撤。— [ETF.com Swedroe](https://www.etf.com/sections/index-investor-corner/swedroe-downside-momentum)、[Stockopedia](https://www.stockopedia.com/blog/avoiding-momentum-crashes-969367)
- **Barroso & Santa-Clara（多空）**：以過去 6 個月實現波動度縮放多空組合到固定波動目標；1927–2011，Sharpe 由 0.53 升到 0.97（2012 工作論文版；期刊版摘要只說「幾乎消除崩盤、Sharpe 近乎加倍」）。未管理的動能在 1932 年兩個月內 −91.59%，2009 年三個月內 −73.42%。— [工作論文 2012](https://www.readkong.com/page/managing-the-risk-of-momentum-cid-3-9123248)、[ETF.com 摘要](https://www.etf.com/node/94292.md)、[CFA Institute blog（Barroso）](https://blogs.cfainstitute.org/blog/2018/01/17/timing-the-market-momentum-and-beyond)
- **Moreira & Muir（多空因子＋市場）**：波動度管理在市場、價值、動能、獲利力、ROE、投資因子上都有顯著風險調整後報酬，且在衰退期承擔較少風險；作者也承認一般投資人難以交易動能因子。— [NBER w22208](https://www.nber.org/system/files/working_papers/w22208/w22208.pdf)
- **真實時點檢驗大幅縮水**：Cederburg, O'Doherty, Wang & Yan（JFE 138, 2020）檢驗 103 個股票策略：波動度管理版本在直接比較中「沒有系統性勝過」未管理版本；spanning regression 的正 alpha 無法即時實作；樣本外 72／103 個策略落後未管理版。MOM、ROE、BAB 是少數沒有顯著退化的，但加入 Fama-French 因子後這三個的樣本外表現也下降。— [Alpha Architect 摘要](https://alphaarchitect.com/does-portfolio-timing-based-on-volatility-signals-outperform-buy-and-hold/)、[CXO 摘要](https://www.cxoadvisory.com/?p=32455)、[論文 PDF](https://www.lehigh.edu/~xuy219/research/COWY.pdf)
- 同組作者另一篇：以「下檔波動」縮放在真實時點交易中顯著優於以總波動縮放。— [Downside 論文 PDF](https://www.lehigh.edu/~xuy219/research/Downside.pdf)
- **扣成本後**：Barroso & Detzel（JFE 140, 2021）——即使用 6 種降成本技巧，扣交易成本後，除市場本身以外的因子做波動度管理「一般為零異常報酬且顯著降低 Sharpe」；市場的波動度管理是例外，且只在高情緒期有效。摘要沒有單獨說動能存活。— [SUFE 學術摘要](https://academicnewsletter.sufe.edu.cn/info/355328)
- **國際證據較有利於動能**：45 個市場、1982–2021：波動度管理對市場、價值、獲利力、尤其動能最有希望；套用降成本技巧後，只有以 6 個月（下檔）波動縮放的動能對成本穩健（且只是部分穩健）。— [Schwarz 等, J. Empirical Finance 2024](https://www.sciencedirect.com/science/article/pii/S092753982400094X)、[Duisburg-Essen 出版頁](https://www.hecf.wiwi.uni-due.de/en/research/publications/on-the-performance-of-volatility-managed-equity-factors-international-and-further-evidence-15874)
- **最接近「只做多股票＋波動度目標」的證據（指數層級）**：Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert（JPM 2018，約 60 種資產、日資料最早自 1926、目標波動 10%）：股票的 Sharpe 由 0.40 升到 0.48–0.51；機制是槓桿效應（下跌時波動上升→自動減碼，類似時間序列動能）；所有資產類別的極端報酬機率下降；對平衡型與風險平價組合降低最大回撤；債券、匯率、商品的 Sharpe 幾乎不變。股票單獨的最大回撤數字在我取得的摘要中沒有。— [Man Institute](https://www.man.com/maninstitute/the-impact-of-volatility-targeting)、[Duke PDF](https://people.duke.edu/~charvey/Research/Published_Papers/P135_The_impact_of.pdf)、[Quantpedia](https://quantpedia.com/the-impact-of-volatility-targeting-on-equities-bonds-commodities-and-currencies)
- **三種改良方法對照（多空）**：Hanauer & Windmueller「Enhanced Momentum Strategies」：美國 1930–2017，特質（idiosyncratic）動能、固定波動縮放、動態縮放三者都約讓 Sharpe 與 t 值加倍，偏態、峰態、最大回撤都下降；**特質動能降最大回撤最多**；48 國 1991–2017 樣本中，特質動能的 Sharpe 改善超過波動縮放法的兩倍；三者的損益兩平交易成本都上升（儘管週轉更高）；再把特質動能用其實現波動縮放，Sharpe 再提高。— [Quantpedia 摘要](https://vvv.quantpedia.com/?p=5402)
- **市場狀態**：Cooper, Gutierrez & Hameed（JF 2004）：1929–1995，市場前期上漲後動能月均獲利 0.93%，前期下跌後 −0.37%。英國資料（Galariotis 等 2014）沒有複製出這個關係。— [SUFE 摘要](https://academicnewsletter.sufe.edu.cn/info/361185)、[White Rose（英國檢驗）](https://eprints.whiterose.ac.uk/89930)
- 加密貨幣動能套用 Barroso & Santa-Clara 的波動目標，仍未勝過市場（不同資產，旁證）。— [Springer 2025](https://link.springer.com/article/10.1007/s11408-025-00474-9)

### Inferences
- 只做多 20 檔動能組合的回撤 ≈ 市場 beta × 大盤跌幅 ＋ 選股相對表現；Daniel & Moskowitz 的機制在只做多組合上表現為「反彈期大幅落後」（2009 年贏家 +8% 對輸家 +163%），這會直接吃掉對 0050 的超額報酬，而不一定加深絕對回撤。
- 對只做多帳戶，「波動度縮放」只能縮（降持股到現金），不能加槓桿放大；多空文獻裡 Sharpe 加倍的一部分來自低波動期加槓桿，只做多版本拿不到這部分。可期待的效果較接近 Harvey 等的指數層級結果：Sharpe 小幅改善、左尾變薄，但在牛市期持有現金會拖累對「全額買 0050」的報酬。
- Cederburg 與 Barroso & Detzel 的結果顯示：縮放參數若是事後挑的，樣本外大多失效；用 6 個月（下檔）波動、固定規則、並先寫死參數，是文獻中最站得住的版本。

### Gaps
- 沒找到嚴謹檢驗「只做多贏家組合＋波動度目標（不足部分放現金）」的論文或其回撤／報酬數字；Barroso & Santa-Clara、Hanauer & Windmueller 的多空兩腿分拆結果也沒有在摘要中出現。
- Harvey 等研究中股票單獨的最大回撤降幅沒有取得。
- Barroso & Detzel 對動能因子扣成本後的具體數字未取得。

## 2. 趨勢／市場狀態濾網（大盤 200 日均線、Faber GTAA、Antonacci 絕對／雙動能）套在選股組合上：降回撤 vs 報酬成本、假訊號、訊號延遲

### Takeaway
趨勢濾網在指數與資產配置層級有大量「降低最大回撤」的回測證據（例如已開發市場價值指數 61.2%→18.7%、60/40 實質回撤 49.2%→27.3%），但報酬增益多半集中在少數大空頭（2000–02、2008），樣本外與長期檢驗顯示約 80% 的賣出訊號事後是錯的、5–10 年期間勝敗約各半，而且 V 型急跌（2020 年 3 月、2024 年 8 月這類）時月線濾網通常來不及出場、又會錯過反彈。我沒找到把大盤 200 日均線濾網套在個股動能組合上的同儕審查研究；部落格級的回測結果有好有壞。

### Cited Findings
- **指數層級（Alpha Architect，1973–2017，12 個月均線，跌破轉 T-bills，未扣成本）**：S&P 500 年化 10.87% 對買進持有 10.52%；EAFE 9.85% 對 8.49%（作者並說「100% 有效的擇時系統不可能」）。— [Alpha Architect：How to use trend following within a portfolio](https://alphaarchitect.com/how-to-use-trend-following-within-a-portfolio/)、[Avoiding the Big Drawdown PDF](https://alphaarchitect.com/wp-content/uploads/2021/08/Avoiding_the_Big_Drawdown_with_Trend-Following_Investment_Strategies.pdf)（兩頁的數字歸屬依搜尋摘要，未逐頁核對）
- **價值／成長國家指數＋趨勢濾網**：簡單趨勢濾網比相對動能濾網有更低波動與更小回撤；10 期均線濾網讓 1976 年以來已開發市場價值指數最大回撤由 61.2% 降到 18.7%（指數，非個股；假設性回測）。— [CXO：Value vs. Growth with Trend/Momentum Filters](https://www.cxoadvisory.com/value-premium/value-vs-growth-with-trendmomentum-filters/)
- **60/40 加趨勢**：最大實質回撤由 49.2% 降到 27.3%。— [ETF.com Swedroe：Trend Following As Insurance](https://www.etf.com/sections/index-investor-corner/swedroe-trend-following-insurance)
- **Zakamulin「Fooled by Data-Mining」（2013，1930–2012 樣本外模擬，2–24 月均線，計入成本）**：認為 Faber 的均線擇時結果受資料探勘汙染；平均約 80% 的賣出訊號「失敗」（出場期間 T-bills 沒有勝過股票）；超額表現集中在少數短期事件，投資人要長期忍受落後基準。— [ETF.com Swedroe：Beware Lure of Market Timing](https://www.etf.com/sections/index-investor-corner/swedroe-beware-lure-market-timing)、[Capital Spectator](https://www.capitalspectator.com/2013/04/page/2/)
- **超長期（1860–2009，單邊成本 0.25%）**：5 或 10 年持有期，擇時規則勝出與落後的機率約相等（平均勝出幅度大於平均落後幅度）；結論是「混合」，較長持有期、等權重過去報酬、動態回顧期間較有利。另一份 1870–2014 的均線比較中，擇時 Sharpe 勝過買進持有，但 1932 年後沒有統計上顯著的勝出。— [CXO：very long run](https://www.cxoadvisory.com/technical-trading/market-timing-with-moving-averages-over-the-very-long-run)、[CXO：long-run MA horse race](https://www.cxoadvisory.com/technical-trading/long-run-moving-average-horse-race-for-timing-the-u-s-stock-market)
- **訊號頻率與延遲**：Zakamulin & Giner（2025 專書章節）：較高頻交易反應稍快，但增加假訊號（whipsaw）與成本；較低頻減少多餘交易但稍晚辨識反轉；整體而言交易頻率對趨勢策略績效影響有限。「零延遲」均線降低延遲卻犧牲準確度，交易規則並未穩定勝過傳統均線。— [RePEc 章節](https://econpapers.repec.org/RePEc:spr:sprchp:978-3-031-90907-8_14)
- **選股動能＋趨勢濾網（部落格級，低可信度）**：美國前十分位動能，跌破 10 個月均線就轉 10 年期公債：濾網砍掉最嚴重的崩盤，但單一動能部位最差仍約 −47%，慢速濾網在出場前已吐回一大段。— [BestFolio](https://bestfolio.app/blog/five-momentum-strategies-1928)
- **反例（部落格級）**：2011–2026，SPY／IWM／EFA 取前 1 名輪動，加上「12 個月報酬為正」的絕對動能閘門：最大回撤反而由 −33.4% 變 −35.7%，年化由 9.0% 掉到 4.6%；約 12% 時間在現金，跌後出場、漲回才進場，錯過 2019、2020、2023 的反彈。— [chat2invest](https://www.chat2invest.com/research/dual-momentum-absolute-gate)
- **Antonacci 雙動能（作者自報，資產層級）**：39 年年化 17.43% 對 ACWI 8.85%，最大回撤 22.7% 對 60.21%。— [Antonacci, Medium](https://medium.com/@garyantonacci_30463/extended-backtest-of-global-equities-momentum-dual-momentum-eb12902612e0)；一個公開的樣本外複製發現原論文的雙動能主張不成立（雙動能 4.32%／年 對無動能基準 6.19%，最大回撤更深）。— [GitHub dual-momentum-oos](https://github.com/FranciscoAC-burst/dual-momentum-oos)
- **市場狀態可預測動能報酬**：見第 1 節 Cooper 等（上漲後 0.93%／月、下跌後 −0.37%／月），這是「大盤在空頭時不要做動能」的學術依據之一，但英國未複製。— [SUFE 摘要](https://academicnewsletter.sufe.edu.cn/info/361185)、[White Rose](https://eprints.whiterose.ac.uk/89930)
- 一篇台灣中文財經文章建議「加權指數在 200 日均線之上才持有動能組合」，聲稱大幅減少崩盤而幾乎不影響長期超額報酬，但沒有任何數據或出處，且自承數字是「教學示意」。— [ctyeh.com](https://ctyeh.com/finance/12781)

### Inferences
- 對「報酬要贏 0050」的目標，趨勢濾網的主要成本是牛市中的假出場與反彈期晚進場；文獻顯示它的好處集中在緩跌型、持續數月的大空頭（台股 2008、2022 屬此類），對急跌急漲型（2020 年 3 月、2024 年 8 月）幫助有限甚至有害。
- 濾網應視為一次獨立試驗（參數只挑一組：例如 200 日或 10 個月），並報告：在現金的時間比例、進出次數、每次出場後錯過的反彈，與相同現金流 0050 比較。

### Gaps
- 沒找到同儕審查的「大盤 200 日均線濾網＋個股動能組合（只做多）」研究，也沒找到台股版本的實證。
- Zakamulin 論文本身的假訊號次數、延遲成本表沒有取得。

## 3. 個股層級停損（Kaminski & Lo 2014、Han, Zhou & Zhu 2016 停損動能）對只做多動能的證據

### Takeaway
理論上，報酬若是隨機漫步，停損「永遠」降低期望報酬；只有在報酬有正自我相關（動能）或狀態切換時才可能有正貢獻。Han-Zhou-Zhu 的 10%／15% 停損把多空動能的最差單月損失由約 −50% 壓到 −11%～−17%，但改善「大部分來自輸家（空方）那一側」，且是毛報酬、週轉上升；對只做多組合，直接證據很薄。

### Cited Findings
- **Kaminski & Lo（J. Financial Markets 18, 2014）**：報酬無可預測性時，「停損溢酬」永遠為負；有正序列相關（動能）時可以為正，且與報酬持續性的強度成正比。1950–2004 美國股票月資料、以長期公債為避險資產，某些停損規則在「停損出場期間」每月增加 50–100 bp。— [MIT DSpace PDF](https://dspace.mit.edu/bitstream/handle/1721.1/114876/Lo_When%20Do%20Stop-Loss.pdf)、[MIT 紀錄頁](https://dspace.mit.edu/entities/publication/bb69ca4b-0cdc-487f-831d-63b2e84fafee)
- **Lo & Remorov（2017）延伸**：在有序列相關的狀態切換報酬下，停損可勝過買進持有，但高交易成本會吃掉優勢。— [UPC 論文引述](https://upcommons.upc.edu/bitstream/handle/2117/169672/stop-loss%20rules.pdf)
- **Han, Zhou & Zhu「Taming Momentum Crashes」（多空）**：6 個月動能前十分位，當月股價跌破月初價 10% 就賣出；等權最大單月損失由 −49.79% 降到 −11.36%（2014 版 −11.34%），市值加權由約 −65% 降到約 −23%。— [Alpha Architect](https://alphaarchitect.com/2016/08/taming-the-momentum-roller-coaster-fact-or-fiction/)、[CXO](https://www.cxoadvisory.com/?p=25071)
- **15% 停損版本（CXO 整理，1926–2013，等權多空，毛報酬）**：月均報酬 0.99%→1.93%；月標準差 6.01%→4.61%；月 Sharpe 0.17→0.40；三因子 alpha 1.27%→2.01%；最差四個月 −49.8／−39.4／−35.2／−34.5% → −17.4／−14.8／−13.8／−13.1%。10% 停損 Sharpe 0.50、最差月 −15.4%；20% 停損 Sharpe 0.32。**「大部分改善來自輸家那一側」**；贏家側每月約 10% 持股被停損，贏家側週轉由 39% 升到 49%（輸家側 45%→59%）；沒有測只做多版本；停損時的股票流動性差，摩擦成本可能很高，也有資料探勘疑慮。— [CXO](https://www.cxoadvisory.com/?p=25071)
- Alpha Architect 對此較懷疑：需要每天檢查每個持股，建議認真考慮的人先把報酬扣掉 300–600 bp，這可能吃掉全部風控好處。— [Alpha Architect](https://alphaarchitect.com/2016/08/taming-the-momentum-roller-coaster-fact-or-fiction/)

### Inferences
- 只做多組合拿不到 Han-Zhou-Zhu 改善的主要來源（空方輸家反彈）；贏家側的停損主要是把「月內大跌的個股」提早換掉，效果應遠小於論文頭條數字，且台股有 10% 漲跌停，跌停鎖死時停損單無法成交，實際停損價會比設定差。
- 停損與每日決策的平台設計相容，但週轉會上升（贏家側約 +10 個百分點／月）；要在扣成本（台股手續費＋證交稅 0.3%）後評估。

### Gaps
- 沒有找到只做多、贏家側停損的獨立績效數字（報酬、最大回撤）；也沒有台股的停損動能實證。

## 4. 跨策略分散、因子擇時、整合評分 vs 分開子組合、殘差動能、特質動能、「溫水煮青蛙」連續資訊動能

### Takeaway
以「換一種動能定義」來降崩盤風險的證據比「擇時」強：殘差／特質動能在多空檢驗中最大回撤約減半、Sharpe 約加倍，國際樣本更明顯；連續資訊（FIP）動能的報酬持續且不反轉。因子擇時（以估值價差調整因子權重）幾乎沒有淨效益。只做多多因子組合中，「整合成一個分數」比「分開子組合再混合」在 AQR 檢驗中多約 1%／年、資訊比率 +40%，但這個優勢被後續研究質疑為統計巧合。

### Cited Findings
- **殘差動能（Blitz, Huij & Martens 2011，美國 1926–2009，多空十分位）**：傳統動能在 1930 年代與 2000 年代的損失超過 80%，殘差動能在同樣事件的回撤不到一半。— [CXO：Stripping Risks from a Stock Momentum Strategy](https://www.cxoadvisory.com/momentum-investing/stripping-risks-from-a-stock-momentum-strategy/)
- **殘差動能，FTSE World 1993-06～2012-09（多空十分位，毛報酬）**：市場調整版 Sharpe 0.69 對傳統動能 0.05，波動幾乎減半、最大回撤減少一半以上。— [CXO：Purified Stock Momentum with Crash Suppression](https://www.cxoadvisory.com/momentum-investing/purified-stock-momentum-with-crash-suppression/)（兩篇 CXO 摘要的數字歸屬依搜尋整理，未逐頁核對）
- Huij & Lansdorp（2017）複製 Blitz 等：較低的時變 Fama-French 因子曝險與較高報酬風險比，在不同全球股票池與樣本外期間都穩健（談的是報酬風險比，不是回撤）。— [EUR repository](https://repub.eur.nl/pub/100801)
- **Robeco（2023）**：殘差動能等替代動能定義報酬與價格動能相近、回撤較淺；但其數字是殘差動能＋EPS 修正＋新聞情緒的組合分數，多空前後五分位、MSCI 已開發市場，不能全歸功於殘差動能；並指出動能崩盤常發生在空頭之後、伴隨市場反彈。— [Robeco quant chart](https://www.robeco.com/en-uk/insights/2023/02/quant-chart-taming-momentum-crashes)
- **特質動能 vs 波動縮放（多空）**：見第 1 節 Hanauer & Windmueller——特質動能降最大回撤最多，國際樣本 Sharpe 改善為波動縮放的兩倍以上，且一月表現最好（傳統動能一月為負）。— [Quantpedia](https://vvv.quantpedia.com/?p=5402)
- **Frog in the Pan（Da, Gurun & Warachka, RFS 27(7), 2014）**：在相同過去報酬下，以「資訊離散度 ID」（小幅變動天數的相對頻率）雙重排序，1927–2007：連續資訊股票的動能報酬 5.94%，離散資訊股票 −2.07%，單調遞減；連續資訊造成的延續不會長期反轉；媒體報導較多時資訊較離散、會減弱動能。工作論文版（6 個月持有）為 8.86% 對 2.91%。— [作者 PDF](https://www3.nd.edu/~zda/Frog.pdf)、[IDEAS](https://ideas.repec.org/a/oup/rfinst/v27y2014i7p2171-2218..html)、[Alpha Architect](https://alphaarchitect.com/2015/11/frog-in-the-pan-identifying-the-highest-quality-momentum-stocks/)、[CFA Digest](https://rpc.cfainstitute.org/research/cfa-digest/2015/03/frog-in-the-pan-continuous-information-and-momentum-digest-summary)
- **整合 vs 混合（只做多）**：Fitzgibbons, Friedman, Pomorski & Serban（AQR，JPM）：MSCI World 大型股、1993-02～2015-12，整合分數組合比分開子組合混合每年多約 1% 超額報酬、資訊比率高約 40%；因子越多優勢越大；負相關因子（價值＋動能）差距更大；原因是避開「某一因子極高、另一因子極低」的股票。— [AQR](https://www.aqr.com/Insights/Research/White-Papers/Long-Only-Style-Investing)、[Swedroe Substack](https://larryswedroe.substack.com/p/the-integration-of-factors-advantage)、[ai-CIO](https://www.ai-cio.com/news/factor-investing-allocation-vs-integration/)
- **反駁**：Rüegg & Leippold（European Financial Management 2018）用穩健績效檢定，找不到整合法較佳的證據，認為先前結果像統計巧合；整合法對低風險異常的曝險較高、風險較低，但沒有轉成績效改善。— [vLex](https://eu.vlex.com/vid/the-mixed-vs-the-855646895)、[UZH ZORA](https://www.zora.uzh.ch/entities/publication/dc20d342-1870-47a7-bdc5-6fb75df5ac3e)
- **低波動＋動能（北歐，只做多）**：所有只做多組合的風險調整後報酬都勝過市場；「先篩動能、再篩低波動」的雙重篩選 Sharpe 最高。— [Applied Economics 2024](https://www.tandfonline.com/doi/full/10.1080/00036846.2024.2337806)
- **因子擇時很難**：Asness, Chandra, Ilmanen & Israel（JPM 2017）：美國大型股 1968–2016，依估值價差把因子權重調在 50%–150%；估值價差與下一年報酬的相關性很弱（BAB 甚至為負）；毛報酬下：價值 Sharpe 0.13→0.16、動能 0.26→0.33、BAB 0.53→0.40、三因子組合約持平（0.55→0.54）；擇時的好處多半是「多加了價值曝險」，直接策略性配置價值更有效率；Asness 認為因子擇時可能比市場擇時更難。（CXO 摘要對未擇時三因子基準有 0.39／0.55 的內部不一致。）— [CXO：Valuation-based Factor Timing](https://www.cxoadvisory.com/value-premium/valuation-based-factor-timing/)、[AQR Supplement](https://www.aqr.com/Insights/Research/Journal-Article/Contrarian-Factor-Timing-is-Deceptively-Difficult-Supplement)、[AQR：Going Deep on Contrarian Factor Timing](https://www.aqr.com/Insights/Perspectives/Going-Deep-on-Contrarian-Factor-Timing)

### Inferences
- 對只做多 20 檔，最有證據的「降崩盤但不犧牲報酬」方向是**改變排序訊號本身**（殘差／特質動能、FIP 篩選、動能×低波動雙重篩選），而非在組合外層加擇時；這些都是選股訊號，不會讓帳戶長期持有現金，因此對「贏 0050」的拖累較小。但上述回撤減半的數字幾乎都是多空結果，只做多版本的回撤主要仍由大盤決定。
- 同一個帳戶內放「動能＋品質／低波動」時，整合分數是較自然的做法（20 檔名額有限，分開子組合各只有 10 檔會更集中），但不要期待 AQR 的 +1%／年一定存在。

### Gaps
- 殘差動能、FIP 的只做多回撤數字未取得；台股的殘差動能證據只有一篇蒙古學士論文（殘差反轉不顯著），可信度低。— [UFE repository](https://www.repository.ufe.edu.mn/xmlui/handle/8524/1739?show=full)
- 沒有找到「動能＋品質＋價值 分開子組合」在只做多下對最大回撤的具體數字。

## 5. 組合建構：等權、反波動、風險平價、最小變異數；個股上限、產業上限

### Takeaway
各種風險權重組合持股重疊 >80%、超額報酬相關約 0.9，差異主要來自隱含的因子傾斜：等權偏小型股、不防禦；最小變異數／最大分散偏低 beta、低回撤但容易高度集中，集中時回撤反而很深。美國前 250 大的 87 年檢驗中，反（特質）波動權重的風險調整後表現最好，但四種權重「結果相近」。對只有 20 檔的組合，簡單反波動或等權是文獻上較安全的選擇；最小變異數在小樣本下估計誤差大。產業上限對回撤的證據我沒找到。

### Cited Findings
- **Leote de Carvalho, Lu & Moulin（2011/2012「Demystifying Equity Risk-Based Strategies」）**：比較等權、等風險預算（權重×波動相等，即反波動）、等風險貢獻、最小變異數、最大分散；除等權外都比市場指數防禦；持股重疊 >80%，等權／等風險預算／等風險貢獻的超額報酬相關約 90%；最小變異數與最大分散偏重低 beta、低配小型股，等權類偏小型股；報酬與 Sharpe 為毛數字。— [CXO 摘要](https://cxoadvisory.com/?p=17431)、[CXO：Translating Risk Strategies into Common Factors](https://www.cxoadvisory.com/strategic-allocation/translating-risk-strategies-into-common-factors/)
- 資產配置層級的比較：等權的 Ulcer Index 與單月最大回撤相對較高，最小變異數兩者都壓低。— [CXO：Tests of Strategic Allocations Based on Risk Metrics](https://www.cxoadvisory.com/volatility-effects/tests-of-strategic-allocations-based-on-risk-metrics/)
- 最小變異數常因高度集中在少數資產而出現大回撤；反波動組合也可能高度集中。— [BI Norwegian Business School 碩士論文](https://biopen.bi.no/bi-xmlui/bitstream/handle/11250/2487330/1760489.pdf)、[CFA Institute：How to Build Better Low-Volatility Equity Strategies](https://rpc.cfainstitute.org/blogs/enterprising-investor/2024/how-to-build-better-low-volatility-equity-strategies)
- 一個 2017–2020 的美股回測（5 bp 成本）：等權最大回撤約 31%、最小變異數約 14%；這只是單一期間。— [arXiv 2010.04404](https://arxiv.org/pdf/2010.04404)（歸屬依搜尋摘要）
- **Alpha Architect（美國前 250 大，1927–2014）**：比較等權、動能權重（1+12 月報酬）、反特質波動權重（過去 12 個月日報酬對市值加權市場回歸的殘差波動，不考慮共變異）、市值權重：低波動權重風險調整後最好，其次動能、等權、市值；「整體結果相近」；假設性回測。— [Alpha Architect：Which asset allocation weights work the best?](https://alphaarchitect.com/which-asset-allocation-weights-work-the-best/)、[ETF Trends](https://www.etftrends.com/2015/11/which-smart-beta-etf-weighting-scheme-outperforms/)
- Alpha Architect 的動能 ETF（QMOM）對 50 檔持股等權，理由是要的是動能因子曝險，不想多押單一公司。— [Picture Perfect Portfolios 訪談整理](https://pictureperfectportfolios.com/qmom-etf-strategy-review-alpha-architect-us-quantitative-momentum)
- **Du Plessis & Hallerbach（J. Alternative Investments，美國 49 產業組合）**：對橫斷面與時間序列動能做「自身波動」或「標的波動」加權，都能提高 Sharpe、降低峰態、降低平均回撤與整體波動；效果取決於動能與波動是否負相關。— [IASG 摘要](https://www.iasg.com/?p=2150)
- 資產類別層級：簡單等權與風險平價在過去 30 年有最一致的風險調整後表現。— [CXO：Asset Allocation Strategy Horse Race](https://www.cxoadvisory.com/strategic-allocation/asset-allocation-strategy-horse-race)
- 台灣 ETF 研究（重尾、非對稱波動下的組合最佳化）存在，但對象是台灣相關 ETF 而非個股。— [arXiv 2607.16450](https://arxiv.org/pdf/2607.16450)

### Inferences
- 20 檔組合中，用每檔的波動（或特質波動）倒數加權，等於在選股訊號之外再加一層低波動傾斜；文獻顯示這能降波動、略升風險調整報酬，但在高波動成長股主導的多頭（台股 2023–2024 AI 族群）會少賺。
- 台股市值極度集中（台積電），只做多 20 檔且不得持有 0050 時，與 0050 的追蹤差異主要來自是否持有權值股；產業上限會限制電子股權重，可能降低集中風險，但也直接拉低在電子多頭中的相對報酬——這需要用本專案資料實測。

### Gaps
- 沒有找到「20 檔」等級小組合的權重方法比較，也沒有找到個股上限、產業上限對最大回撤影響的量化研究。
- Leote de Carvalho 論文本身的回撤表沒有取得。

## 6. 週轉控制：緩衝帶／遲滯（仍在前 2N 就續抱）、調整頻率、部分調整——淨報酬證據

### Takeaway
買持價差（buy/hold spread、緩衝帶）是文獻中最有效的降成本方法：在不顯著降低毛報酬的情況下降低成本；把月調整改成季調整雖然也降低同樣多的成本，但訊號變舊造成的毛報酬損失約等於節省的成本，平均沒有淨效益；只交易低成本股票最差。單邊月週轉低於 50% 的異常大多扣成本後仍顯著，更高週轉的少有存活。

### Cited Findings
- **Novy-Marx & Velikov（RFS 29(1), 2016）**：買持價差是最有效的降成本技巧（允許繼續持有不會主動買進的股票）；單邊月週轉 <50% 的異常在設計成降成本時多數仍有顯著淨價差，週轉更高的很少；成本在每個案例都降低獲利與顯著性；等權結果有誤導性；成本以價差估計，未含大單價格衝擊。— [NBER w20721](https://www.nber.org/papers/w20721)、[作者 PDF](https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf)
- **Novy-Marx & Velikov（FAJ 2019「Comparing Cost-Mitigation Techniques」，7 個基準策略含 3 個動能策略）**：測試 10%／30% 緩衝帶（前 10% 買進、跌出前 30% 才賣）：在每個案例都降低交易成本，且未顯著降低毛報酬；月改季：降成本幅度與緩衝帶相近，但毛報酬損失約等於節省的成本，平均無淨效益；只交易便宜股票：最差，21 個案例中 10 個淨報酬下降；大型股池中 7 個策略都沒有統計上顯著的淨報酬；在規模策略上加動能篩選可達成「負交易成本」並使淨報酬近乎加倍。— [CFA Institute 摘要](https://rpc.cfainstitute.org/research/financial-analysts-journal/2019/ip-v4-n1-4-comparing-cost-mitigation-techniques)
- 波動度擇時的動能週轉也很高，在其上疊加買持價差可降低週轉與成本。— [Gothenburg 碩士論文](https://gupea.ub.gu.se/items/23f70255-c2d5-46a8-bc7b-265542988fd7)
- 國際 45 市場：套用降成本技巧後只有 6 個月（下檔）波動縮放的動能對成本穩健。— [Schwarz 等 2024](https://www.sciencedirect.com/science/article/pii/S092753982400094X)
- 停損動能會使贏家側週轉由 39% 升到 49%／月。— [CXO](https://www.cxoadvisory.com/?p=25071)
- 趨勢策略的交易頻率（每日 vs 較低頻）對績效影響有限。— [Zakamulin & Giner 章節](https://econpapers.repec.org/RePEc:spr:sprchp:978-3-031-90907-8_14)
- 動能屬於高週轉策略，容量有限；交易成本會摧毀許多因子投資的紙上報酬。— [Alpha Architect：Trading costs destroy factor investing?](https://alphaarchitect.com/trading-costs-destroy-factor-investing/)

### Inferences
- 對每天都能決策的 20 檔組合，「買進門檻嚴、賣出門檻寬」（例如前 20 名買進、跌出前 40 名才賣）比「降低調整頻率」更符合文獻：保住訊號時效又少交易。台股來回成本（手續費兩邊＋證交稅 0.3%）偏高，緩衝帶的相對價值比美股更大。
- 緩衝帶的寬度本身是參數；依專案規範應只挑一組事先決定的寬度，而不是掃描 2N／3N。

### Gaps
- FAJ 2019 中三個動能策略各自的週轉降幅與淨報酬數字沒有取得；「部分調整（只調一部分權重）」的淨報酬研究沒有找到。

## 7. 台股動能組合的回撤（2008、2015、2018-10、2020-03、2022、2024-08）

### Takeaway
沒有找到公開、可信的台股動能組合逐次回撤數字。台股動能本身的證據就分歧：多篇研究找不到顯著的價格動能（1981–2010、1996–2010），有的只在特定季節（財報公布後）或市場狀態不變時存在，產業動能比個股動能強。大盤層級的已知事件：2008 年（2008 年高點起算）約 −56%、2020 年約 −29%（數週內）、2022 年約 −32%（逐月緩跌）、2024-08-05 單日 −8.35%（史上最大單日跌點）。

### Cited Findings
- **市場狀態**：Lin 等（Pacific-Basin Finance Journal 2016）：早期研究在台灣找不到動能溢酬；市場維持同一狀態時動能獲利顯著為正，跨越市場狀態轉換時出現顯著反轉；獲利集中在受投資人注意的股票（歸因過度自信）。— 搜尋摘要，原頁面未逐一核對：[CUFE 摘要頁](https://sf.cufe.edu.cn/info/1198/6130.htm)、[CUFE 摘要頁 2](https://sf.cufe.edu.cn/info/1198/6599.htm)
- **季節性**：Fu & Wood（Applied Economics Letters 2010）：台灣動能只出現在年報申報截止後的月份；其他月份買輸家賣贏家有顯著報酬，農曆新年與中秋前後的反向報酬特別高。— [University of Portsmouth](https://researchportal.port.ac.uk/en/publications/momentum-in-taiwan-seasonality-matters/)、[Applied Economics Letters](https://informahealthcare.com/doi/abs/10.1080/00036840902917589)
- **價格動能與 52 週高點在 1981–2010 不獲利**；五年低點反向策略經三因子調整後仍有異常報酬（中央大學碩論 2013）。— [NCU 機構典藏](https://ir.lib.ncu.edu.tw/handle/987654321/60826)
- 中期延續存在但比美國弱；反向策略只在 3 年持有期有效（中央大學碩論 2006）。— [NCU 機構典藏](https://ir.lib.ncu.edu.tw/handle/987654321/12280)
- 1978-01～2000-06：產業動能比個股動能更有利，且延續到第 36 個月（淡江）。— [淡江教師頁](https://teacher.tku.edu.tw/StfFdDtl.aspx?tid=13693)
- 以 1996-07～2010-06 月報酬建構組合，台股動能策略無法取得顯著正報酬；另有 2008–2012 的研究認為有動能效應；詹錦宏、吳莉禎的研究在納入 2008 金融海嘯後，各動能策略與傳統分散投資沒有顯著差異。— 搜尋摘要，以下來源與各論點的對應未逐一核對：[政大論文 1](https://thesis.lib.nccu.edu.tw/thesis/detail/b3774936eac4c58deaf9dc8e74f036cc/)、[政大論文 2](https://thesis.lib.nccu.edu.tw/thesis/detail/d8de5482a9bbdb85e277ec06852aef36)、[大葉 9701115](https://people.dyu.edu.tw/paper/9701115_c.pdf)、[大葉 9605030](https://people.dyu.edu.tw/paper/9605030_c.pdf)
- 高雄科大有一份「再探台灣股票市場動能策略」PDF，但抓取後無法解析文字，內容未讀到。— [NKUST PDF](https://fin.nkust.edu.tw/uploads/bulletin_file/file/5ee9e3b71d41c865a30000c0/%E5%86%8D%E6%8E%A2%E5%8F%B0%E7%81%A3%E8%82%A1%E7%A5%A8%E5%B8%82%E5%A0%B4%E5%8B%95%E8%83%BD%E7%AD%96%E7%95%A5.pdf)
- 台大 2023 碩論：多數年頻財務指標因子有因子動能（前 12 月報酬與下月正相關），多數月頻指標相反。— [NTU PDF](https://tdr.lib.ntu.edu.tw/bitstream/123456789/88739/1/ntu-111-2.pdf)（歸屬依搜尋摘要）
- **2008**：加權指數 2008 年高點約 9,295（5 月中），11 月 20 日全年低點 4,090（央行年報）；以此計約 −56%；全年收 4,591、年跌 46.0%。— [央行年報 PDF](https://www.cbc.gov.tw/en/public/Attachment/961814234971.PDF)、[Taipei Times 2008-07](https://www.taipeitimes.com/News/biz/archives/2008/07/17/2003417667)
- **2020**：1 月 14 日 30 年高點 12,179；3 月 16 日進入空頭（2015 年以來首次）；3 月 19 日收盤低點 8,681.34（約 −29%）；全年 +22.8% 並創年底新高。— [Bloomberg 2020-03-16](https://www.bloomberg.com/news/articles/2020-03-16/taiwan-stocks-slump-into-bear-market-for-first-time-since-2015)、[TWSE 2020 Market Highlights](https://www.twse.com.tw/downloads/zh/about/company/factbook/2021/0.0105.html)、[Taipei Times 2020-03-20](https://www.taipeitimes.com/News/biz/archives/2020/03/20/2003733007)
- **2022**：1 月 4 日收盤高點 18,526.35，10 月 25 日低點 12,666.12（約 −32%）；全年 −22.40%；6 月底已跌逾 19%。— [TWSE 2022 Market Highlights](https://www.twse.com.tw/downloads/zh/about/company/factbook/2023/0.0105.html)、[Bloomberg 2022-06-30](https://www.bloomberg.com/news/articles/2022-06-30/taiwan-stocks-slide-approaching-a-technical-bear-market)、[Taipei Times 2022-07-01](https://taipeitimes.com/News/front/archives/2022/07/01/2003780917)
- **2024-08-05**：單日 −8.35%（−1,807.21 點）收 19,830.88，為史上最大收盤跌點；AI 權值股跌停 10%；隔日早盤回到 20,621。— [自由時報／Taipei Times](https://news.ltn.com.tw/news/focus/breakingnews/4759878)、[Taiwan News](https://www.taiwannews.com.tw/en/news/5914594)
- 2020 年 3 月（未附資料來源、自稱教學示意）：前一年漲逾 50% 的科技硬體與半導體設備等動能股跌幅大於大盤；3 月下旬反彈由前期弱勢的生技與傳產領漲，對純動能投資人形成雙重打擊。— [ctyeh.com](https://ctyeh.com/finance/12781)（低可信度）

### Inferences
- 台股的崩跌形態分成兩類：緩跌數月（2008、2022）與急跌急漲（2020-03、2024-08）。依第 1、2 節文獻，前者是趨勢濾網／波動縮放可能有幫助的情境，後者則是動能組合最容易「跌得比大盤多、反彈又跟不上」的情境。
- 台股動能訊號偏弱且有季節性與狀態依賴，這使「在組合外層加更多擇時」的空間更小；把力氣放在訊號品質（殘差動能、產業動能、FIP）與交易成本控制，較符合證據。

### Gaps
- 沒有找到台股動能（或任何台股因子）組合在 2008、2015、2018-10、2020-03、2022、2024-08 的逐次回撤數字；需用本專案資料庫自行計算（TEJ 有台股因子資料，但未取得公開數字）。
- 2007 年 10 月的前波高點（2008 年回撤真正的起點）與 2015、2018-10 的指數跌幅沒有取得可引用來源。
- TEJ 的「波動率調整動能（VAM）」文章以 2020–2024 台灣中型 100 回測，但摘要未列結果。
