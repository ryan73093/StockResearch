# Asian-market AI / quantitative stock selection (China A-shares, Japan, Korea, Taiwan) and what transfers to Taiwan daily long-only rules

Scope note: research done 2026-10-10 with web search plus page fetches. Many Chinese sell-side (金工) numbers come from reposts (Sina, BigQuant, 知乎) rather than the original PDFs. Each bullet says whether I fetched the page and checked the figure ("verified page") or only saw it in a search-engine summary ("search summary; page mapping not verified"). Nearly all sell-side and vendor numbers are **backtests**, and most do not separate in-sample from out-of-sample clearly. Only a few are live tracking after publication, and those are flagged.

Our context for the inferences: Taiwan stocks, long-only 20-stock rules, never holding 0050 (it is the benchmark only), daily data (OHLCV, institutional flows, margin/short, PER/PBR, monthly revenue, quarterly statements), decisions made after the close, odd lots.

---

## Q1. Chinese sell-side 金工 research 2020–2026: which daily-frequency selection approaches report out-of-sample results, and what failed

### Takeaway
Since 2020 the A-share work that reports results is mostly (a) deep-learning (GRU, then Transformer-family) models on price/volume features, retrained on a rolling basis, rebalanced weekly; and (b) high-frequency (minute/tick/order-book) factors aggregated to daily values (高频因子低频化) and fed into index-enhancement portfolios. Both report 20–30% annual excess in backtests and IR around 3. Live tracking is much weaker and swings hard: in 2026, Huatai's flagship AI factor had +5% year-to-date excess by end of April and, per an unverified later summary, about zero by mid-June. The biggest documented failure is the Jan–Feb 2024 micro-cap crowding crash. Implicit small-cap tilts, leverage (DMA, market-neutral products) and evaporating liquidity produced roughly 10–17% excess drawdowns in a week or two, and a smaller repeat came in January 2025. Of these sources, the only ones that need only daily data are the ones built on daily price/volume, turnover/liquidity and fundamentals. The high-frequency results need minute or tick data that we do not have.

### Cited Findings

**Deep learning on price/volume (Huatai 华泰金工 "人工智能" series)**
- 2017 baseline (monthly rebalance): GRU and LSTM performed about the same, with LSTM slightly better. Industry-neutral all-A GRU portfolios had annual excess of about 14.65–19.43% vs CSI300 and 19.77–26.27% vs CSI500, depending on how many stocks were picked per industry. Backtest. Source: search summary of the [Huatai 2017-11-24 report PDF](https://crm.htsc.com.cn/doc/2017/10750101/a682a53b-cb78-412c-8215-0799a4dec87f.pdf).
- 2023-12 "基于全频段量价特征的选股模型": a GRU trained on 27 high-frequency factors (minute bars, tick trades and tick orders) to predict 10-day returns. All-A 2017–2023: weekly RankIC 9.10%, TOP-group annual excess 26.20% **before costs**. A multi-task GRU on daily, weekly and monthly K-lines reached 31.05% annual excess. Blending high- and low-frequency factors 1:3 raised weekly RankIC to 11.47%. Source: search summary of Huatai reposts, e.g. [Sina 2025-01-06](https://finance.sina.com.cn/roll/2025-01-06/doc-ineczhrv1841570.shtml) and [BigQuant](https://bigquant.com/square/paper/a8d607fd-defd-4cab-b569-06205481a867) (page mapping not verified). The construction (27 high-frequency deep-learning factors plus low-frequency multi-task factors) is confirmed on the [verified Sina repost of 2026-05-06](https://finance.sina.com.cn/wm/2026-05-06/doc-inhwxhnt5995548.shtml).
- GRU plus a "patch" model on long price/volume sequences, 2017-01-04 to 2024-02-29, all-A: weekly RankIC rose from 8.86% to 9.58%, and the top-decile annual excess rose from 21.15% to 24.65%. Backtest. Source: [Sina repost "如何捕捉长时间序列量价数据的规律"](http://stockfinance.sina.cn/stock/go.php/paper/reportid/763730804918/index.phtml) (search summary).
- 2025-03: Transformer, iTransformer and Crossformer on a "GPT feature set". Ensemble weekly RankIC 11.64%, all-A long-side annual excess 25.94%. CSI1000 enhancement with at least 80% constituents: annual excess 20.25%, IR 3.60. Sample 2017 to 2025-02. Backtest. Source: [Sina repost 2025-03-25](https://finance.sina.com.cn/roll/2025-03-25/doc-ineqwwwh2275742.shtml) (search summary).
- **Live tracking, verified page** ([Sina repost of Huatai, published 2026-05-06, data to 2026-04-30](https://finance.sina.com.cn/wm/2026-05-06/doc-inhwxhnt5995548.shtml)):
  - Fusion factor TOP layer vs all-A equal weight: 2026 YTD excess +5.32%, 5-day RankIC 11.3%, "annualized excess since the 2017 backtest start" 29.14%. That figure mixes backtest and tracking.
  - AI CSI1000 enhanced portfolio: 2026 YTD excess +1.01%, since-2017 annualized excess 21.13%, tracking error 6.10%, IR 3.47, max excess drawdown 7.55%.
  - Portfolio constraints: at least 80% in index constituents, single-stock active weight ≤0.8%, Barra style exposures <0.3, weekly two-way turnover ≤30%, weekly rebalance, two-way cost 0.4% as stated.
  - The report itself warns that models "reflect historical patterns, may fail if market dynamics change", and that attribution is hard.
- Huatai also runs an LLM text strategy (LLM-FADT, based on analyst-report text) vs CSI500: since-2017 annualized excess 25.40%, IR 2.00, 2026 YTD +1.0%. Source: [same verified page](https://finance.sina.com.cn/wm/2026-05-06/doc-inhwxhnt5995548.shtml). It needs text data we lack.

**High-frequency factors aggregated to daily (高频因子低频化)**
- Rationale: the higher the data frequency, the weaker the autocorrelation and the shorter the forecast horizon, so intraday information is aggregated into daily factors. One 2023-08 report fed 55 such daily variables into a deep fully-connected network. Source: [BigQuant "高频漫谈"](https://bigquant.com/wiki/doc/Q9QlJEl59y) and related search summary.
- 方正金工 2023 review: 11 high-frequency price/volume factors after daily aggregation. Rank ICIR above 4 for each, and the composite factor returned 49.23% with only 1.74% max drawdown. That return/drawdown pair is almost certainly long-short or under some other special basis. Source: [BigQuant repost](https://bigquant.com/square/paper/fd8bd8ac-c67e-49a0-adef-1c44d742089b) (search summary).
- Huatai "GPT因子工厂2.0", high-frequency factors in index enhancement: CSI300 annual excess 13.78% (IR 2.73), CSI500 18.40% (IR 2.68), CSI1000 31.32% (IR 4.20). Backtest. Source: [Sina 2024-09-27](https://finance.sina.com.cn/roll/2024-09-27/doc-incqrmin7588926.shtml) (search summary). The same source notes that traditional formula-mining (GP) struggles to build daily sampling into high-frequency factor expressions.
- 中信 "高频量价选股因子初探": order-imbalance (VOI) factors are positively related to returns at high frequency, but the sign flips after daily/monthly aggregation. VOI1, all-market monthly rebalance 2010–2019: IC −2.74%, annualized IR −1.62, long-short 12.45%/yr, Sharpe 1.69. Source: [report PDF](https://asset.quant-wiki.com/pdf/%E4%B8%AD%E4%BF%A1%E5%9B%A0%E5%AD%90%E6%B7%B1%E5%BA%A6%E7%A0%94%E7%A9%B6%E7%B3%BB%E5%88%979%EF%BC%9A%E9%AB%98%E9%A2%91%E9%87%8F%E4%BB%B7%E9%80%89%E8%82%A1%E5%9B%A0%E5%AD%90%E5%88%9D%E6%8E%A2.pdf) (search summary).
- An older index-enhancement study (data from 2011): adding mid/high-frequency factors raised CSI500 enhancement annual excess by 3.9% (IR +0.59) and CSI300 by 2.4% (IR +0.42), without a material increase in turnover. Source: [Sohu repost "市场微观结构探析系列之四"](https://www.sohu.com/a/395192884_619347) (search summary).
- 海通 says its high-frequency factors have been tracked out of sample in weekly reports since 2019-05 with "excellent and stable" results. Source: [海通 选股因子系列(六十九)：高频因子的现实与幻想](https://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/649434713134/index.phtml) (search summary; self-reported).
- An anti-overfitting screen practitioners use: a factor must work in sample for at least 7 years and out of sample for at least 6 months, with no more than 20% difference between the two, and high-frequency factors are tested with daily rather than weekly/monthly rebalancing. Source: [BigQuant 高频漫谈](https://bigquant.com/wiki/doc/Q9QlJEl59y).
- 方正 has gone further, to 30-second and 15-second data, asking whether finer bars add information over minute-bar factors. This shows the research frontier keeps moving to higher frequency. Source: [Sina 2024-12-02](https://finance.sina.com.cn/roll/2024-12-02/doc-incyafyn5411544.shtml) (title only).

**Formula / GP-mined price-volume alphas (GTJA 191, Alpha158) and decay**
- GTJA's 191 alphas are built from volume, price, volatility and VWAP. Source: [IEEE DataPort alpha191](https://ieee-dataport.org/documents/alpha191) and search summary. I found **no** out-of-sample evaluation of the 191 set specific to A-shares.
- AlphaAgent preprint: on CSI500, the IC of Alpha158, GP-mined factors and RSI fell from about 0.022–0.036 to near zero over five years. The authors call alpha decay pervasive in A-shares. Not peer reviewed. Source: [arXiv 2502.16789](https://arxiv.org/html/2502.16789v1).
- Tsinghua PBCSF China factor study, 1997–2017: 11 of 56 factors were effective. Most were liquidity or trading-friction factors (standardized turnover, turnover volatility). Source: [PBCSF PDF](https://www.pbcsf.tsinghua.edu.cn/__local/6/70/A3/A837B68FBEC544F2998B5EAD455_9925BD88_14DD4F.pdf?e=.pdf) (search summary).

**Northbound (foreign) flow factors**
- Northbound holdings moved from daily to **quarterly disclosure from 2024-08-19**, which removes the daily holding-change signal. Verified page: [Jiemian 2025](https://m.jiemian.com/article/12614255.html). The article has no factor backtest.
- 2023 commentary: northbound trading was only about 6% of A-share turnover and holdings about 3% of market cap, and 华创 said it "is not really smart money" lately. Source: [2023-09 news PDF](https://e.chinacqsb.com/attachment/202309/22/d97b6144-8ab8-458b-8bc7-a74aeb0ed023.pdf) (search summary; page mapping likely but not verified).
- An academic study on 2018–2023 daily data found that changes in northbound holdings positively predict excess returns beyond standard risk factors, but the effect is **short-lived**. Source: [periodical record](https://search.napstic.cn/literature/periodical/010tzyj202410007) (search summary).
- 安信 (date unclear, data from 2017): northbound flow used for single-stock timing won only about 51% of the time. Picking the top-5 net-inflow stocks per industry weekly gave a 55.6% win rate on excess return and a payoff ratio of 1.21. Search summary; page mapping not verified (candidate: [cs.com.cn 2021-07-07](https://cs.com.cn/gppd/gsyj/202107/t20210707_6181617.html)).

**2024 micro-cap (微盘股) crowding crash: the key failure case**
- Verified page ([21世纪经济报道 2024-02-07](https://www.21jingji.com/article/20240207/herald/3b1928645724307cde0210cc6e7acb82.html)):
  - The Wind micro-cap index (the smallest 400 A-shares, equal weight, rebalanced daily) had gained more than 50% in 2023 at one point before the crash.
  - One-day fund losses on 5 Feb 2024: 金元顺安产业臻选 −15.56%, 国金量化精选 −10.65%, 国金量化多因子 −9.98%.
  - Many leading quant private funds (天演, 明汯, 灵均, 稳博, 龙旗, 九坤, 幻方 1000-enhanced products) lost more than 15% in the week to 2 Feb. One DMA product lost more than 30% in January, and dozens of quant funds were down more than 20% YTD.
  - Causes cited:
    - Crowding: big quants held thousands of small and micro-caps, which created a positive feedback loop.
    - Liquidity: per 海通, when the leading themes fall outside micro-cap sectors, micro-cap turnover collapses and prices gap.
    - DMA and neutral-product deleveraging: forced selling of small caps and buying of large caps.
    - Model drift: models trained on recent data had drifted toward small caps.
- Wider timeline (search summary of [EEO 2024-02-24](http://m.eeo.com.cn/2024/0224/639051.shtml), [Yicai](https://www.yicai.com/news/101986104.html) and [Sina 2024-02-21](https://finance.sina.cn/2024-02-21/detail-inaivmeu5545237.d.html)):
  - Late January: small-cap liquidity dried up. Snowball products hit knock-in levels, the index-futures discount widened, and neutral products unwound.
  - 5–7 Feb: mainstream quant funds suffered roughly 10%+ **excess** drawdowns while the "national team" bought CSI500/1000 ETFs, which drained liquidity from micro-caps outside those indices.
  - Reported weekly moves: CSI500 −9.23% vs 衍复500 −10.4% and 天演500 −11.76%; CSI1000 −13.19% vs 稳博1000 −16.93%. Per-page attribution not verified.
- The crash was foreseeable. In Dec 2023, 思源量化's 王雄 warned that micro-caps had built up too much excess return, and that neutral products long small/micro-caps and short CSI500 futures were prone to rapid drawdowns. Source: [中国基金报 2023-12-17](https://www.chnfund.com/article/AR20231217042957598) (search summary).
- A smaller repeat: in January 2025 several 10bn-RMB quant managers had products down more than 10% in a micro-cap correction ("跨年暴击"). Source: [Tencent News 2025-01-15](https://news.qq.com/rain/a/20250115A025J300) (headline/snippet only). After the 2024 crash, the industry repeatedly flagged micro-cap concentration risk. Source: [证券时报](https://www.stcn.com/article/detail/1150049.html).
- An earlier global parallel, the 2018–2020 quant equity crisis ("cornered by big growth"). Source: [Robeco](https://robeco.com/zh-hk/insights/2021/02/the-quant-equity-crisis-of-2018-2020-cornered-by-big-growth) (title only).

### Inferences
- The A-share "AI stock selection" edge from 2017–2026 is concentrated in short-horizon (5–10 day) price/volume patterns. These are traded weekly with turnover caps (30% two-way per week), in a market whose retail activity creates short-term predictability.
- Weekly turnover of that size costs much more in Taiwan. A 30% weekly two-way cap is about 15% one-way per week, roughly 7.8× NAV one-way per year. At Taiwan's standard 0.1425%×2 fee plus 0.3% sell tax (about 0.585% per round trip, before broker discounts), that is roughly a 4.5%/yr drag. This is my arithmetic, using the cost figures in [FinLab's ML article](https://finlab.finance/blog/machine-learning-predict-stock-price).
- Two parts are directly transferable with daily data: (1) rolling-retrained GBDT/GRU models on daily price/volume/turnover features, plus risk constraints (style-exposure caps, single-name active-weight caps, turnover caps); (2) the governance lesson of 2024: put a **hard cap on size/micro-cap exposure and a liquidity floor**, and watch crowding.
- What does not transfer: the high-frequency factors (27 HF factors, VOI, 方正 HF set) and the text/LLM strategies. They are the largest single source of A-share excess, and they need data we do not have.
- The northbound case is a warning about regulatory or data-regime risk: a flow factor can die when disclosure frequency changes. Taiwan's daily disclosure of 三大法人 flows is currently intact. That makes it a Taiwan advantage, but also a single point of failure.

### Gaps
- I could not locate the original PDFs for most Huatai/方正/海通 numbers, so I could not confirm cost assumptions, rolling-retrain schedules or exact in-sample/out-of-sample splits.
- A search summary claimed that by a 2026-06-18 Huatai update the fusion factor's YTD excess had fallen to about zero and the AI CSI1000 enhanced portfolio was −8.03% YTD (since-2017 annualized 19.78%, IR 3.15). I could not find that page, so this is **unverified**. If true, it would mean a roughly 9-point excess drop in seven weeks of live tracking.
- No A-share-specific out-of-sample test of the GTJA 191 alphas was found. Nothing usable was found on 东方/开源/国盛 factor series specifically, or on analyst / institutional site-visit (机构调研) factors.
- I found no quantified post-2024 change in A-share index-enhancement design (for example new size-exposure limits) beyond qualitative "concentration risk" commentary.

---

## Q2. Qlib benchmarks (CSI300) and academic A-share ML evidence

### Takeaway
On Qlib's public CSI300 benchmark (test roughly 2017 to 2020-08), simple models on hand-crafted daily features beat most deep nets. LightGBM on Alpha158 earned 9.0% annual excess (IR 1.02), DoubleEnsemble (a LightGBM ensemble) 11.6% (IR 1.34), and **plain linear regression 6.9% (IR 0.92)**. Transformer and TabNet earned 2–3% on Alpha158 and were negative on raw Alpha360 features. Recent third-party reruns on later periods show Alpha158 + LightGBM excess near zero, which is consistent with decay. The academic anchor, Leippold, Wang & Zhou (JFE 2022), finds that A-share predictability comes mostly from liquidity and fundamental signals, is strongest for small, retail-heavy stocks at short horizons, and is weak for price trend (momentum).

### Cited Findings

**Qlib official benchmark, CSI300, Alpha158**
Mean of 20 seeds. Figures are annualized excess return over the benchmark, IR and max drawdown. Verified page: [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md).

| Model | Ann. excess | IR | IC | Max DD |
|---|---|---|---|---|
| Linear | 6.92% | 0.92 | 0.0397 | −15.1% |
| LightGBM | 9.01% | 1.02 | 0.0448 (Rank IC 0.0469) | −10.4% |
| XGBoost | 7.80% | 0.91 | — | — |
| CatBoost | 7.65% | 0.80 | — | — |
| MLP | 8.95% | 1.14 | — | — |
| DoubleEnsemble | 11.58% | 1.34 | 0.0521 | −9.2% |
| TRA | 7.18% | 1.08 | — | — |
| GRU (20 LightGBM-selected features) | 3.44% | 0.52 | — | — |
| LSTM | 3.81% | 0.56 | — | — |
| Transformer | 2.73% | 0.40 | — | — |
| TabNet | 2.27% | 0.37 | — | — |

**Qlib official benchmark, CSI300, Alpha360 (raw 60-day price/volume sequences)** [same page]
- Best: HIST 9.87% (IR 1.37), IGMTF 9.46%, TRA 9.20%, TCTS 8.93%, GRU 7.20% (IR 0.97), LightGBM 5.58% (IR 0.76).
- Negative: Transformer −2.70%, TabNet −3.69%, KRNN −4.65%.

**Qlib caveats and setup**
- The README says the models are untuned, and that the backtest changed in v0.8.0, so results are not comparable across versions. [Same page](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- The standard CSI300 config trains on 2008–2014, validates on 2015–2016 and tests from 2017 to 2020-08 against the SH000300 benchmark. Source: [Qlib config mirror](https://gitcode.com/weixin_43949714/qlib/blob/main/examples/portfolio/config_enhanced_indexing.yaml) (third-party mirror).
- Alpha158 is a hand-engineered tabular set built from daily price/volume. Alpha360 is raw daily price/volume time series. [README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)

**Recent third-party reruns** (periods and settings differ; flagged)
- AutoScientist-Quant: LightGBM + Alpha158 on CSI300 gave about −0.1% annualized, IR −0.007, IC 0.010, max DD −13.2%. [arXiv 2608.28632](https://arxiv.org/pdf/2608.28632) (search summary).
- AlphaSeek: LightGBM 0.07% annualized, IR 0.0092. [arXiv 2608.13913](https://arxiv.org/pdf/2608.13913) (search summary).
- A memory-controlled LLM-trading benchmark using Qlib defaults on CSI300 with a top-20 portfolio: LightGBM total excess +29.30% vs a +36.92% buy-and-hold benchmark, Sharpe 1.29, IR 0.33. The test window may be 2022 to 2025-12. [arXiv 2605.28359](https://arxiv.org/pdf/2605.28359) (search summary).

**Leippold, Wang & Zhou (2022), "Machine learning in the Chinese stock market", JFE 145(2):64–82** ([SFI summary](https://www.sfi.ch/en/publications/machine-learning-in-the-chinese-stock-market); DOI 10.1016/j.jfineco.2021.08.017)
- Large signal set (about 1,160 signals per the summary).
- The strongest predictors are **liquidity and fundamentals**. Price-trend signals matter less than in the US.
- Heavy retail participation raises **short-horizon** predictability, especially in small stocks.
- Large caps and SOEs are more predictable at **longer** horizons.
- The ML portfolios stay economically significant after transaction costs.

### Inferences
- For a Taiwan daily model, the evidence says to start with GBDT (LightGBM or a DoubleEnsemble-style ensemble) on hand-built daily features, plus a linear baseline. Deep sequence models on raw prices are not a good place to start: they underperformed on Qlib, and FinLab's Taiwan evidence (Q5) points the same way.
- Expect strong decay. Qlib's 2017–2020 numbers did not survive into 2022–2025 reruns, so the IR from any backtest should be haircut heavily. Keep using our final-validation-period discipline.
- Leippold et al.'s "short horizon × small stocks × retail" result also describes where crowding and the 2024 crash happened. Short-horizon small-cap signals are both the most predictive and the most fragile.

### Gaps
- Qlib CSI500 tables are incomplete on the README. I did not get official numbers for periods after 2020.
- The test periods for the three recent reruns are not confirmed from full texts.
- I did not retrieve Leippold et al.'s exact portfolio Sharpe or return numbers (abstract-level only).

---

## Q3. Japan: Kaggle JPX lessons, Japanese quant practice, momentum weakness and what works instead

### Takeaway
Japan is the classic "no price momentum" market: medium-term momentum is weak or absent, while short-term and 1–12-month reversals and value carry more of the return. Asset managers report that value has been revived by the TSE's 2023 PBR/ROE reform push. Goldman Sachs Asset Management notes that Japanese trends reverse faster and that value and momentum are nearly uncorrelated there. The JPX Kaggle competition (2022) mostly showed how noisy daily cross-sectional ranking is. The winner was a student, and a re-study found LightGBM only marginally better than linear or MLP models, with any edge shrinking further after look-ahead leakage was removed.

### Cited Findings
**Momentum**
- Chui, Titman & Wei (2001) found momentum in Hong Kong, Malaysia, Singapore and Thailand, but **none in Japan, Taiwan, Korea or Indonesia**. Search summary of [NCU repository](https://ir.lib.ncu.edu.tw/handle/987654321/62859). Titman's slides call Japan the only major market without momentum in pre-2000 studies. [Wharton/Jacobs Levy slides](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2022/10/Titman.pdf)
- Titman's framework: retail "noise" traders generate short-term reversals that partly offset momentum. [Same slides](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2022/10/Titman.pdf)
- Iwanaga (2024): Japanese residual momentum is **not significant** after adjusting for short- and long-term reversal. [IDEAS record](https://ideas.repec.org/a/eee/finana/v93y2024ics1057521924001224.html)
- Momentum in Japan loses about 2–4% on average each January (seasonality). [Mikova & Teplova](https://www.unwe.bg/eajournal/en/journalissues/article/6752)
- Chang, Fu & Ng (1999), via a secondary citation in search results: Japanese stocks show no short-term continuation and reverse over 1-month to 1-year horizons, and value did not beat glamour in that older sample. Search summary; secondary.
- A thesis argues momentum exists in Japan but is hidden by skewed and leptokurtic returns, so rank- or sign-based measures detect it. [U. Hawaii, "Rank and Sign Momentum in Japan"](https://scholarspace.manoa.hawaii.edu/items/fd71a76a-1d0c-4be5-86d1-adcaeb72b50a) (unreviewed thesis).

**Value and recent factor behaviour**
- After 2023, momentum picked up: SuMi Trust says momentum returns had been negligible since the GFC but have been well above their long-term average since the TSE's March 2023 intervention. [SuMi Trust AM factor paper](https://hk.sumitrust-am.com/docs/factor-investment-japanese-equity-marketpdf/download) (search summary; page mapping likely).
- Goldman Sachs AM (2026): Japan has rotated toward value since COVID, unlike the US and Europe, and TSE reforms are a structural tailwind. Japanese trends reverse faster than elsewhere. Value and momentum are largely uncorrelated in Japan. The Japan value factor was positive over 1991–2026 while US and Europe value were largely negative. [GSAM "Japanese paradox: systematic path to alpha"](https://am.gs.com/en-jp/institutions/insights/article/2026/japanese-paradox-systematic-path-to-alpha) (search summary).
- Russell, Q1 2024: Value and Momentum strongly outperformed in Japan, while Small Caps, Quality and Low Vol lagged. Only about 40% of active products beat TOPIX. [Russell Investments](https://russellinvestments.com/uk/blog/q1-2024-active-management-review)
- TSE reform trigger: about half of listed firms traded at PBR < 1 with ROE < 8%. From 2023 such firms are expected to disclose improvement plans, and firms responded with buybacks, dividends and unwinding cross-shareholdings. [Asiae](https://view.asiae.co.kr/en/article/2024020208410441326)

**Kaggle "JPX Tokyo Stock Exchange Prediction" (2022)**
- Task: rank about 2,000 stocks daily, scored on the return spread of the top-200 vs bottom-200 portfolios. Ran 2022-04-05 to 2022-10-07. [JPX news release](https://www.jpx.co.jp/english/corporate/news/news-releases/6020/20221206-01.html)
- First place went to student Shoki Sakai of Shizuoka University. [Shizuoka Univ.](https://www.inf.shizuoka.ac.jp/english/news/detail.html?CN=154751)
- A Manchester study on the same data: Ridge and MLP gave poor risk-adjusted returns. LightGBM was best (daily Sharpe-like score 0.039), falling to about 0.028 once a simulation removed forward-looking bias. [EUDL 2026 paper](https://eudl.eu/pdf/10.4108/eai.22-5-2026.2365110)
- Japanese competition designers note that finance competitions feel luck-driven because the data are very noisy and the train and test regimes differ. [Zenn article](https://zenn.dev/gamella/articles/eaf7fe5a96bdf0)

### Inferences
- Japan and Taiwan share "no classic momentum" status in early studies (Chui-Titman-Wei). Japan's lesson for Taiwan is to test short-term reversal and value/quality, and to treat raw 12-1 momentum skeptically. But see Q5: an ML study in Taiwan finds momentum variables useful when combined nonlinearly.
- The value revival in Japan was driven by a regulatory catalyst (PBR reform). Taiwan has no exact equivalent, though its high-dividend ETF boom creates a different, flow-driven valuation catalyst.
- The JPX competition is a reminder that a one-shot daily ranking contest over a few months is mostly noise. This supports our project's rule against judging rules on short windows.

### Gaps
- I did not find the JPX winning solutions' write-ups. Their presentations are on the JPX YouTube channel, which I could not access.
- Nothing specific was found on Nomura or Daiwa quant research outputs.
- No quantified 2022–2024 Japan value-spread numbers were found, only qualitative asset-manager commentary.

---

## Q4. Korea: KOSPI/KOSDAQ ML studies and retail-heavy dynamics

### Takeaway
Korean cross-sectional ML evidence matches the US and China pattern: tree models (GBM/XGBoost) beat linear models. One study reports a GBM hedge return of 2.89% per month (Sharpe 0.93, 1987–2018) using risk, price-trend and liquidity variables, with median regression helping in an outlier-heavy emerging market. Retail-trading features add predictability, mainly in small, high-attention stocks. Index-level daily direction forecasting with deep learning does not beat simple baselines. KOSDAQ-specific cross-sectional ML work is thin.

### Cited Findings
- Noh, Jang & Yang (2023), Korea 1987–2018: GBM beats linear models in both accuracy and portfolio performance. The GBM hedge return under median regression is **2.89% per month, annualized Sharpe 0.93**. Risk, price-trend and liquidity variables are the important predictors. Median regression is argued to suit outlier-prone emerging markets. [Sookmyung repository "Forecasting Korean Stock Returns with Machine Learning"](https://scholarworks.sookmyung.ac.kr/handle/2020.sw.sookmyung/151891) (search summary; long-short, monthly).
- Kim, Kim & Park (2020): blending anomaly signals into expected returns gives long-short excess returns that stay positive relative to FF5 and q-factor models. [IDEAS, Emerging Markets Finance & Trade](https://ideas.repec.org/a/mes/emfitr/v56y2020i15p3763-3784.html) (search summary).
- Kang, Ryu & Webb (2025): tree-based models outperform other ML models, and **36-month momentum** is the dominant predictor in interpretability tools. [IDEAS, Financial Innovation 2025](https://ideas.repec.org/a/spr/fininn/v11y2025i1d10.1186_s40854-025-00870-0.html) (search summary).
- An image-based CNN on Korean stocks beats benchmarks, especially at short horizons. [KAIST repository](https://koasas.kaist.ac.kr/handle/10203/337992)
- Student replication of Gu-Kelly-Xiu on Korea: adding retail-trading features raised XGBoost's out-of-sample R² to 0.8255% for all stocks. Predictability was greater in low-cap and high-retail-attention stocks. Not peer reviewed. [HKUST UROP 2024-25](https://ebookshelf.hkust.edu.hk/flippingbook/G23630/223/). The same report references Ahn, Fan, Noh & Park (2024), "Overnight-Intraday Return Gap and the Retail Ebb and Flow".
- Daily KOSPI direction: even the best deep-learning models did not predict significantly better than a simple base model. [Ewha](https://pure.ewha.ac.kr/en/publications/is-it-possible-to-forecast-kospi-direction-using-deep-learning-me/). Google Trends was not useful for KOSPI200. [PMC](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5685607/)

### Inferences
- Korea, like Taiwan, is retail-heavy and early studies found no classic momentum (Chui-Titman-Wei). Recent ML work there finds momentum and liquidity useful inside nonlinear models. The transferable pattern: GBDT on risk, price-trend and liquidity features, robust losses (median or rank objectives), and the expectation that edge concentrates in small, high-attention names, which is exactly where capacity and crowding risk sit.
- The overnight-intraday gap and "retail ebb and flow" idea can be computed from daily open and close prices, so it fits our data (overnight return = open/previous close; intraday = close/open).

### Gaps
- No KOSDAQ-specific cross-sectional ML study with out-of-sample portfolio numbers was found.
- I could not verify the full texts or test periods for Kim-Kim-Park or Kang-Ryu-Webb.

---

## Q5. Taiwan updates 2023–2026: TEJ / TQuant Lab, FinLab, academic ML, 三大法人 / 融資券 / 當沖, ETF rebalancing events, 處置/注意股

### Takeaway
- **Academic.** The strongest peer-reviewed Taiwan ML result is Bui, Kong, Lin & Lin (PBFJ 2023). Neural networks and PLS on 86 anomalies give long-short returns of 1.20–1.50% per month, and momentum variables are 5 of the top 20 predictors. ML thus revives momentum in a market long considered momentum-free.
- **Practitioner, long-only vs 0050.** FinLab's own out-of-sample test (LightGBM ranking, top 30 equal weight, monthly, 2019-01 to 2026-06, costs included) found a real but small signal (Rank IC 0.027). The strategy **lost to dividend-adjusted 0050**: CAGR 26.0% vs 29.9%, max drawdown −45.8% vs −34.0%. Simple institutional-flow following also lags: following 投信 alone gave 7.4% CAGR over 2015–2025.
- **Most promising Taiwan-specific daily-data edges.**
  - Event and flow effects: index and ETF rebalancing (adds strengthen around announcement and effective dates, then reverse; markets front-run from the review base date).
  - 處置股: negative first-days drift after announcement, then mean reversion. The new 5-day rule from 2026-08-10 makes the old statistics stale.
  - Combining chip (institutional flow) signals with monthly revenue momentum. FinLab claims 30.1% CAGR for 投信 + 月營收成長, but that sits behind a paywall and is unverified.

### Cited Findings

**Academic ML / anomalies (Taiwan)**
- Bui, Kong, Lin & Lin (2023), "Momentum in machine learning: Evidence from the Taiwan stock market", Pacific-Basin Finance Journal 82:102178. 86 anomalies. NN and PLS long-short returns of **1.20%–1.50% per month**. **5 of the top 20 predictors are momentum-related**. Taiwan was previously "one of the few exceptions to the momentum anomaly". [NYCU scholar page](https://scholar.nycu.edu.tw/en/publications/momentum-in-machine-learning-evidence-from-the-taiwan-stock-marke/)
- A related NCCU thesis finds that cross-sectional momentum as the target gives higher prediction accuracy than time-series momentum under the same models. [NCCU thesis](https://thesis.lib.nccu.edu.tw/detail/e24eea0c988ba23851d3a50462dbc010/) (search summary)
- NCCU thesis on TEJ data, 2010–2025: 52 firm-level factors; LightGBM ranking was best, with full-market annualized return **15.90%, Sharpe 2.50, max DD −5.90%**. The author notes that **transaction costs and leverage limits are not included**, so the result is idealized and probably long-short. [NCCU thesis](https://thesis.lib.nccu.edu.tw/thesis/detail/cf3746d1cb1f3395292698a356f29edd) (search summary)
- A Taiwan thesis using 4 fundamental, 15 technical and 3 chip indicators with RF, XGBoost, PLS and SVM reports beating the market-weighted index and 0050 on return and Sharpe (details not visible). [NDLTD](https://ndltd.ncl.edu.tw/r/596d7g) (search summary)
- Hsu, Yu, Hu & Huang: Taiwan's institutional dominance is linked to its rule requiring **daily disclosure** of institutional trading. They use ML on 2019–2022 data to predict institutional trading patterns, not returns. [IDEAS Applied Economics Letters 2025](https://ideas.repec.org/a/taf/apeclt/v32y2025i7p1034-1038.html) (search summary; mapping likely)

**Retail / day-trader evidence (Taiwan)**
- Barber, Lee, Liu & Odean (RFS 2009), full Taiwan trade records 1995–1999: individuals' aggregate portfolio loses **3.8 percentage points a year** (2.2% of GDP), almost all through aggressive orders. Institutions gain 1.5 pp a year, and foreign institutions take nearly half of institutional profits. [IDEAS RFS 2009](https://ideas.repec.org/a/oup/rfinst/v22y2009i2p609-632.html)
- Day traders, 1992–2006: fewer than 1% earn reliable positive abnormal returns net of fees. The top 500 earn 61.3 bps a day before fees and 37.9 after; the bottom group −11.5 and −28.9. [Barber et al. PDF](https://faculty.haas.berkeley.edu/odean/papers/day%20traders/Day%20Trading%20Skill%20110523.pdf)
- About 97% of day traders are likely to lose in future day trading. [IDEAS RAPS 2020](https://ideas.repec.org/a/oup/rasset/v10y2020i1p61-93..html)
- Attention-grabbing events raise individuals' net buying: a previous-day extreme negative return, a large previous-day **margin-purchase increase**, a large short-position decrease, and heavy news coverage. [NTU thesis](https://tdr.lib.ntu.edu.tw/jspui/handle/123456789/38613?mode=full) (search summary; buying behavior only, not subsequent returns)
- Margin and day-trading sentiment, 2011–2017 (TW50 and Mid-Cap 100): margin balance is positively related to same-day returns more often in TW50, and day-trade volume co-moves with returns more in mid-caps, which are easier to push around. Short-interest increases are linked to weaker prices over the long run (in a separate China-concept-stock study). Search summary of [Airiti records](https://www.airitilibrary.com/Article/Detail/U0078-2712201714434926); page mapping not verified. I found **no clean Taiwan study showing "margin surge → future reversal"**.
- Day trading reached 47.3% of total sell value on 2021-05-13 (a single-day figure). [TechNews](https://finance.technews.tw/2021/05/14/foreign-media-use-taiwan-stocks-as-a-warning/)

**FinLab (vendor, self-published backtests)**
- Verified page, [FinLab "機器學習預測股價沒用？…台股 8 年樣本外實測"](https://finlab.finance/blog/machine-learning-predict-stock-price):
  - Model: LightGBM regressor. Features: RSI, 120-day momentum, ADX, close/MA240, P/B, monthly revenue YoY. The article says chip features are included, but the code has none.
  - Label: next-month return in excess of the cross-sectional median.
  - Training: a single fit on data through 2018-12-31. Out-of-sample period 2019-01 to 2026-06.
  - Portfolio: top 30 stocks, equal weight, 10% cap, monthly rebalance. Universe: 60-day average traded value above NT$50m. Costs: 0.1425% fee and 0.3% tax, no slippage.
  - Out-of-sample results:

    | Metric | ML ranking strategy | 0050 (dividends included) |
    |---|---|---|
    | CAGR | 26.0% | 29.9% |
    | Daily Sharpe | 0.98 | 1.38 |
    | Max drawdown | −45.8% | −34.0% |
    | Avg. monthly turnover | 164% | — |

  - Rank IC 0.027. Decile excess rises roughly monotonically from 1.68% to 2.64% per month. All 15 CPCV folds had positive Rank IC (0.015–0.048, mean 0.030).
  - In sample, Rank IC was 0.529 and CAGR 338%, which the article attributes to leakage.
  - FinLab's own explanation for losing to 0050: high turnover, 30-stock diversification against a TSMC-heavy benchmark, and a small IC.
- Verified page, [FinLab "外資 vs 投信 vs 自營商"](https://finlab.finance/blog/institutional-investors-comparison):
  - Setup: 2015–2025; buy the top 15 by 10-day net-buy amount (thresholds 外資 > 10,000 shares, 投信 / 自營 > 5,000 shares) with 20-day average volume > 300 lots; monthly rebalance; costs not stated.
  - Results: following 外資 CAGR 12.4% (Sharpe 0.65, max DD −36.0%); 投信 7.4% (0.35, −39.3%); 自營商 15.0% (0.68, −29.5%).
  - 投信 + monthly-revenue-growth filter: claimed CAGR 30.1%, Sharpe 0.95. The rules are behind a VIP paywall and **unverifiable**.
  - Caveats in the article: net buying ≠ conviction (hedging, ETF creations), and the disclosure lag.
- Other FinLab pages (search summary; mapping between page and number not individually verified): [stock-selection page](https://finlab.finance/tools/stock-selection), [stock screener](https://finlab.finance/tools/stock-screener), [Claude Code chip backtest](https://finlab.finance/blog/claude-code-finlab-quant-trading).
  - Each of six classic factors alone lost to dividend-adjusted 0050 over 2015–2026. A four-factor composite gave slightly lower return but a 9-point shallower drawdown (daily Sharpe 1.46 vs 1.22).
  - Raising the assumed fee from 0.1425% to 0.5% (a slippage proxy) cut one four-factor strategy's CAGR from about 20.1% to 15.0%.
  - Pure 投信 net-buy ranking gave 18.1%. A "投信 + 外資 persistent buying" three-factor version gave 19.8% (max DD −32.7%), against dividend-adjusted 0050 at 25.0%.
  - Over a matching window, "投信買超 + momentum" gave 16.52% (max DD −39.3%) vs 0050 at 25.05%.
- Older FinLab "Smart ETF 00905 2.0" rebuild: 12 value, quality and momentum factors; multi-factor test-set CAGR about 24% with drawdown below the market. [FinLab](https://www.finlab.tw/smart-etf-00905-%E7%A8%8B%E5%BC%8F%E9%A9%97%E8%AD%89%E5%AF%A6%E4%BD%9C/) (search summary)

**TEJ / TQuant Lab**
- Platform: Taiwan backtest stack on the TEJ API, with point-in-time datasets, Zipline, Pyfolio and Alphalens.
- Published example strategies:
  - VAM (volatility-adjusted cross-sectional momentum): the top 10 by VAM among the 100 highest-volume stocks, monthly, 2020–2024, with a slippage model capping fills at 2.5% of volume and an impact coefficient of 0.05.
  - LambdaMART learning-to-rank on momentum features for Taiwan 50 constituents: monthly top 10, walk-forward retraining.
  - A "基石價值" value screen (top 30 by cash yield, annual rebalance).
  - A MAD/MRAT (MA21/MA200) trend strategy.
- Sources: TEJ articles syndicated on LINE Today, e.g. [1](https://today.line.me/tw/v3/article/eL5pGnB), [2](https://today.line.me/tw/v3/article/PG5VNmo), [3](https://today.line.me/tw/v3/article/2D3Z52B) (search summary; which article holds which strategy is not verified). **Performance tables were truncated, so no verified return numbers.**

**ETF / index rebalancing as tradable events**
- 00878 reviews twice a year (May and November) and now implements changes over **five trading days**. Its single-stock weight cap fell from 15% to 10%, with holdings capped at 6% of an issuer's market cap.
- 0056 reviews in June and December.
- 00919 has expanded to 40 holdings (from 30) to shrink per-name flow, reviews in May and December, may replace at most 8 names in December, and raised its single-stock cap to 15%. In 2026-06, 00919 changed 18 names in and 18 out.
- Sources for these three bullets: [經理人 2026 ETF 換股](https://www.managertoday.com.tw/articles/view/72307), [天下](https://www.cw.com.tw/article/5130307), [鉅亨](https://news.cnyes.com/news/id/6004807) (search summary; details vary across sources).
- 0056 additions event study (2008–2023, NYCU thesis): cumulative abnormal return peaks and then drifts down. **There is no excess return after the effective date** (event day +11), and the five-day implementation window did not push prices up. [NYCU thesis](https://thesis.lib.nycu.edu.tw/items/747f27e4-5e08-4b6d-ac1d-e22b725f1a3e) (search summary)
- NCCU 2023 thesis on ETFs with AUM above NT$10bn: inclusions performed better from the **review base date to announcement** than from announcement to effective date, so the market front-runs. The thesis suggests computing the list from prospectus rules at the base date. [NCCU](https://ah.lib.nccu.edu.tw/item?item_id=164506) (search summary)
- Taiwan 50 changes:
  - 2007 thesis: additions show abnormal returns on the day after announcement and the day before the effective date, cumulative abnormal returns last about two weeks, and there is excess volume.
  - 2016 thesis (TW50 and Mid-Cap 100): positive abnormal returns for adds, negative for deletes.
  - 2003–2021 thesis: adds strengthen in the two days before the effective date and deletions weaken, then both reverse after adjustment.
  - Sources: NCCU repositories, e.g. [thesis a1a6b2df](https://thesis.lib.nccu.edu.tw/thesis/detail/a1a6b2df63ea2d7b14b498e1e00dff3d) and [thesis 14053cd0](https://thesis.lib.nccu.edu.tw/detail/14053cd0746ffacc29b902b28d8a4284/) (search summary).
  - Most recent change seen: added 貿聯-KY, 創意, 南電 and 臻鼎-KY; removed 康霈*, 中鋼, 台塑 and 和泰車; effective after the close on 2026-06-18. [工商時報 2026-06-08](https://www.ctee.com.tw/news/20260608700506-430201) (search summary).

**處置 / 注意 stocks**
- NTU thesis, disposals announced 2018 to 2021H1: abnormal returns are **negative in the first 3 days after announcement, then recover**. There is no significant positive excess 20 days after the disposal ends. Market cap, number of limit hits, short/margin ratio and director holdings explain cross-sectional differences. [NTU](https://tdr.lib.ntu.edu.tw/handle/123456789/84175) (search summary)
- NTU 2019 thesis, 2013–2018: overheated (超漲) stocks show significant positive abnormal returns at the announcement and late in the disposal, then turn negative quickly. Oversold (超跌) stocks are significantly positive only in the last 3 days of disposal. [NTU](https://tdr.lib.ntu.edu.tw/handle/123456789/73402) (search summary)
- NCCU, first-time disposals 2020-03 to 2022-06: volume and volatility rise before the attention notice. During disposal, volume and volatility drop, information asymmetry rises, and returns show high positive autocorrelation. Everything normalizes after disposal. [NCCU](https://thesis.lib.nccu.edu.tw/detail/b4a7f7a19a881b2ad6fdddfc4e644144/) (search summary)
- Rule change: the disposal period is shortened from 10 to 5 trading days for first and repeat disposals, effective **2026-08-10**. This comes from a secondary source and should be verified against TWSE/TPEx. Search summary; candidate sources [數位時代](https://www.bnext.com.tw/article/91706/taiwan-stock-disposal-mechanism) and [Money Weekly](https://www.moneyweekly.com.tw/_Article?AID=130616).

### Inferences
- Beating 0050 with a diversified long-only 20–30-stock rule is structurally hard in 2019–2026. The benchmark is TSMC-heavy and returned about 25–30% CAGR in FinLab's windows. Even a signal with consistently positive out-of-sample IC (FinLab's 0.027–0.030) lost on CAGR and drawdown after costs and 164% monthly turnover. A 20-stock rule must keep turnover low, and it probably needs factors with a larger IC than generic technical plus value features.
- Taiwan's daily 三大法人 disclosure is unusual in Asia. China's northbound data went quarterly in 2024, and Korea has no clear equivalent in the sources found. By FinLab's numbers, flows alone are weak; their claimed value is as a filter combined with monthly revenue momentum, which is another Taiwan-specific daily/monthly dataset. This is consistent with Leippold et al.'s finding that fundamentals plus liquidity dominate in retail-heavy Asia.
- ETF rebalancing gives a rules-based, daily-data-computable event. The evidence suggests the edge is **before announcement** (base date to announcement), not after the effective date. With 00878's five-day staggering, 00919's 40 names and 8-name cap, and 00878's 10% weight cap, the post-announcement edge is likely shrinking (my inference; no post-2024 study found).
- Disposal stocks: the "avoid or short-term-fade in the first days after a disposal announcement" pattern fits our rules as an exclusion filter. But samples before 2026-08-10 are built on the 10-day regime, so any rule using them needs a regime flag.

### Gaps
- No verified performance numbers for TQuant Lab VAM or LTR strategies; the tables were truncated.
- FinLab's best claimed combinations (投信 + 月營收, 30.1% CAGR) are paywalled and unverifiable.
- No 2023–2026 peer-reviewed study was found that quantifies 融資/融券 or 當沖 ratios as cross-sectional return predictors in Taiwan with out-of-sample tests.
- No study after 2024 quantifies 00878/00919/0056 rebalancing abnormal returns under the new staggered-implementation rules.
- No NTHU-specific ML stock-selection paper surfaced.

---

## Q6. Which methods need only daily data (what we have) vs intraday or alternative data (what we lack)

### Takeaway
Most of the documented A-share excess since 2020 comes from intraday data (minute, tick and order-book factors) or text (analyst reports, LLMs), which we lack. What transfers to our dataset is daily price/volume/turnover feature sets (Alpha158-style) with GBDT, liquidity and turnover factors, short-term reversal, fundamentals and monthly revenue, and daily institutional-flow and margin data. Taiwan's daily flow disclosure and monthly revenue are data advantages A-share quants do not have in the same form. Event strategies (index/ETF rebalancing, disposal stocks) need only daily data plus public rule documents and announcement lists.

### Cited Findings
- Alpha158 (Qlib's best-performing tabular feature set with GBDT) and Alpha360 are built from daily price/volume. [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- GTJA 191 alphas use daily volume, price, volatility and VWAP. [IEEE DataPort alpha191](https://ieee-dataport.org/documents/alpha191). VWAP can be approximated from daily traded value ÷ volume (my note).
- Huatai's flagship fusion factor needs minute bars plus tick trades and orders (27 HF factors). [Sina repost 2026-05-06](https://finance.sina.com.cn/wm/2026-05-06/doc-inhwxhnt5995548.shtml). The 方正 and 中信 VOI factors likewise need high-frequency data. [BigQuant](https://bigquant.com/square/paper/fd8bd8ac-c67e-49a0-adef-1c44d742089b), [中信 PDF](https://asset.quant-wiki.com/pdf/%E4%B8%AD%E4%BF%A1%E5%9B%A0%E5%AD%90%E6%B7%B1%E5%BA%A6%E7%A0%94%E7%A9%B6%E7%B3%BB%E5%88%979%EF%BC%9A%E9%AB%98%E9%A2%91%E9%87%8F%E4%BB%B7%E9%80%89%E8%82%A1%E5%9B%A0%E5%AD%90%E5%88%9D%E6%8E%A2.pdf)
- LLM-FADT needs analyst-report text. [Same Sina page](https://finance.sina.com.cn/wm/2026-05-06/doc-inhwxhnt5995548.shtml)
- Leippold et al.'s dominant predictors (liquidity and fundamentals) are daily- or quarterly-computable. [SFI](https://www.sfi.ch/en/publications/machine-learning-in-the-chinese-stock-market)
- Korean ML predictors (risk, price trend, liquidity) are also daily-computable. [Sookmyung](https://scholarworks.sookmyung.ac.kr/handle/2020.sw.sookmyung/151891)
- Taiwan daily institutional disclosure. [IDEAS](https://ideas.repec.org/a/taf/apeclt/v32y2025i7p1034-1038.html)
- FinLab features: technical indicators plus P/B plus monthly revenue YoY, all daily or monthly. [FinLab](https://finlab.finance/blog/machine-learning-predict-stock-price)

### Inferences

**Feasible with our data**

| Approach | Data needed | Evidence | Main risk |
|---|---|---|---|
| GBDT / ensemble ranking on Alpha158-style daily features plus turnover/liquidity, with a linear baseline | Daily OHLCV, traded value | Qlib 2017–2020 IR about 1.0–1.3; Leippold; Korea GBM | Decay after 2020 (reruns near zero); small-cap concentration |
| Short-term reversal / overnight-intraday decomposition | Daily open/close | Japan reversal literature; Korean retail ebb-and-flow; Titman noise-trader logic | Turnover cost under Taiwan's 0.3% sell tax |
| Institutional flow (外資 / 投信 / 自營) as a filter plus monthly-revenue momentum | Daily flows, monthly revenue | FinLab: flows alone 7–15% CAGR, combined claim 30.1% (unverified) | Disclosure-lag effects; crowding with other retail quants following the same public data |
| Margin purchase surges as attention/sentiment signals | Daily margin/short | Evidence only on retail buying behavior; no clean return study found | Treat as an exploratory hypothesis, not established |
| Value / quality / dividend | PER/PBR, quarterly statements | Japan value revival; FinLab classic factors individually lost to 0050, composite improved drawdown | Long stretches of underperformance vs a TSMC-heavy 0050 |
| Index/ETF rebalancing front-running | Daily market cap, dividend yields, liquidity, plus each ETF's public methodology | Edge is from base date to announcement | Rule changes (staggered execution, more names) erode it |
| 處置/注意 exclusion or timing filters | TWSE/TPEx daily disposal/attention announcement lists | Thesis evidence above | We may not have this list stored. It is public daily data, but it is not one of the data types listed for our project. The 2026-08-10 regime change also matters. |

**Not feasible with our data**
- Minute/tick/order-book factors (Huatai HF GRU, 方正 HF set, VOI).
- Analyst-report text / LLM text factors.
- Analyst forecast revisions and institutional site visits (调研). The Taiwan equivalents would need separate data.
- Intraday execution alpha.

**Portfolio-construction lessons that are data-independent and transferable**
- Cap size and style exposures (Huatai uses Barra exposure < 0.3).
- Cap turnover (Huatai uses ≤30% weekly two-way).
- Single-name caps.
- A liquidity floor.
- Live monitoring of crowding in micro-caps.
- These would have limited the 2024 A-share losses, and they map onto a 20-stock long-only rule as a size/liquidity screen plus turnover budget.

### Gaps
- I did not verify whether the project's data includes daily traded value (needed for VWAP approximation and liquidity factors) or TWSE disposal/attention lists. Check against the project's database.
- No Asia source quantified how much of A-share ML excess survives when restricted to daily-only features after 2020. Qlib reruns suggest little for Alpha158 + LightGBM on CSI300, but that is a large-cap universe.
