# Academic ML for cross-sectional stock return prediction and portfolio formation (2018–2026): what replicates, outside the US, and for long-only books

Scope note for the report writer: magnitudes below are gross unless marked "net". "L/S" = long-short decile/quintile spread; "VW"/"EW" = value-/equal-weighted; "top decile" = long-only leg. Flags used: **[US-only]**, **[L/S-only]**, **[pre-2015 test]**, **[secondary source]** (summary, not the paper's own tables). Our context: long-only 20-stock Taiwan rules, daily close decisions, ~40 factors, GBDT walk-forward (IC ~0.06), excess-return labels, 0050 as benchmark only.

## 1. Gu–Kelly–Xiu (2020) and international replications: long-only vs long-short, dominant predictors, trees vs NN

### Takeaway
ML (NN and boosted trees) beats linear factor combinations mainly in **long-short, equal-weighted, small-cap-heavy** portfolios. The long-only leg gains much less. In GKX's own US VW deciles, the NN4 top decile's Sharpe (0.81) is about equal to a 3-variable OLS top decile (0.79). Outside the US, the ML edge is real in China, emerging markets, and pooled global samples. The only peer-reviewed-adjacent evidence that includes **Taiwan** is weak or mixed: in Choi–Jiang–Zhang, locally trained ML VW L/S Sharpe ratios in Taiwan (2012–2017) sit below the market's own Sharpe.

### Cited Findings
**Gu, Kelly & Xiu (RFS 2020)** [US-only] [pre-2015 test mostly]
- Sample 1957-03 to 2016-12. Training 1957–1974, validation 1975–1986, out-of-sample test 1987–2016 (30 years), with ~920 predictors (94 characteristics × macro interactions + industry). — [GKX RFS 2020 PDF](https://dachxiu.chicagobooth.edu/download/ML.pdf)
- Monthly stock-level out-of-sample R² is 0.33% for NN1 and peaks at 0.40% for NN3. Four- and five-layer nets do not improve on NN3 ("benefits of deep learning are limited"). Among the top-1,000 large stocks, trees and NNs reach R²oos of 0.52%–0.70%. — [GKX](https://dachxiu.chicagobooth.edu/download/ML.pdf)
- VW L/S NN4 averages 2.26%/month (27.1%/yr) with 20.1% annual volatility, an out-of-sample Sharpe of 1.35. EW NN4 L/S Sharpe is 2.45. S&P 500 market timing with NN reaches Sharpe 0.77 vs 0.51 for buy-and-hold. — [GKX](https://dachxiu.chicagobooth.edu/download/ML.pdf)
- **Long-only leg (VW top decile, Table 7):** NN4 averages 1.75%/month (SD 7.51%, Sharpe 0.81). OLS-3+H (size/BM/momentum only) gets 1.34%/month (Sharpe 0.79), RF 1.53% (0.73), GBRT+H 1.17% (0.69), ENet+H 0.84% (0.43). The NN4 bottom decile is −0.52%/month, so a large part of the L/S edge comes from the short leg. — [GKX Table 7](https://dachxiu.chicagobooth.edu/download/ML.pdf)
- **Turnover (VW, monthly, Table 8):** OLS-3 58%; NN1–NN5 121–127%; GBRT+H 144%; RF 134%; ENet+H 152%. — [GKX Table 8](https://dachxiu.chicagobooth.edu/download/ML.pdf)
- **Dominant predictors:** price-trend variables (short-term reversal mom1m, 12-month momentum, momentum change, industry momentum), then liquidity (turnover, turnover volatility, size, dollar volume), then volatility (return volatility, idiosyncratic volatility, beta). — [GKX](https://dachxiu.chicagobooth.edu/download/ML.pdf)

**Economic-restriction re-tests of GKX-type models (US)**
- Avramov, Cheng & Metzker (Management Science 2023): ML profits concentrate in hard-to-arbitrage stocks. Dropping microcaps, distressed firms, or high-volatility periods "considerably attenuates profitability", and performance deteriorates further under reasonable trading costs because of high turnover and extreme positions. They also note that deep learning signals stay profitable in long positions and recent years. — [INFORMS](https://pubsonline.informs.org/doi/fpi/10.1287/mnsc.2022.4449)
- VW L/S returns are 0.95%–2.18%/month (FF6 alpha 0.62%–1.87%). — [Advisor Perspectives summary](https://www.advisorperspectives.com/articles/2023/03/06/the-promise-of-machine-learning) [secondary]
- The portfolio payoff falls 62% when microcaps are excluded. — [CUHK press release](https://www.media-outreach.vn/View/45101/cuhk-business-school-research-looks-at-the-limitations-of-using-artificial-intelligence-to-pick-stocks) [secondary]
- One summary reports monthly turnover of 87%–163%. — [arXiv 2505.01921 literature summary](https://arxiv.org/pdf/2505.01921) [secondary; attribution not fully verified]
- Azevedo, Hoegner & Velikov ("The Expected Returns on Machine-Learning Strategies", AEA 2025) take ML anomaly strategies (up to 320 Chen–Zimmermann signals, out-of-sample from 2000) and apply three adjustments: trading costs, post-publication decay (only signals already published), and post-decimalization liquidity. Average performance falls ~57%. The haircut ranges from −23% (best LSTM) to −92% (FFNN2). — [Quantpedia summary](https://quantpedia.com/the-expected-returns-of-machine-learning-strategies/); [AEA program](https://www.aeaweb.org/conference/2025/program/paper/TihddefB)
- In an earlier version, net monthly returns ran 0.64% (ENet) to 1.42% (LSTM1) with turnover >50%. — [Quantpedia](https://quantpedia.com/the-expected-returns-of-machine-learning-strategies/) [version differences flagged by summary]

**China: Leippold, Wang & Zhou (JFE 2022)** — the strongest non-US long-only evidence
- 1,160 signals (90 stock characteristics, 11 macro variables, industry dummies) with China-specific factors such as abnormal turnover. Training 2000–2008, validation 2009–2011, test 2012-01 to 2020-06 (103 months). — [Leippold et al. JFE PDF](https://zibs.zju.edu.cn/_upload/article/files/21/78/16f84f524d7daebc5d2831d43ec9/d107b0a1-790d-4d4d-b87b-e168849235ca.pdf)
- Out-of-sample R² reaches 2.71%, "almost sevenfold" GKX's best 0.40%. **Liquidity** is the most important predictor group, followed by fundamentals. Price momentum "only play[s] a minor role". Retail-driven short-horizon predictability is concentrated in small stocks; SOEs and large stocks are more predictable at longer horizons. — [Leippold et al.](https://zibs.zju.edu.cn/_upload/article/files/21/78/16f84f524d7daebc5d2831d43ec9/d107b0a1-790d-4d4d-b87b-e168849235ca.pdf)
- **VW long-only (top decile), monthly gross / annual Sharpe:** NN4 4.50% / 1.68; NN3 4.36% / 1.76 (best); VASA 4.04% / 1.64; LASSO 3.37% / 1.50; GBRT 2.59% / 1.31; RF 2.22% / 1.07; OLS-3 2.45% / 0.90. The 1/N market portfolio gets about 1.56% / 0.64. VW L/S: NN3 Sharpe 3.45 vs GKX's 1.35. — [Leippold Tables 6 & 10](https://zibs.zju.edu.cn/_upload/article/files/21/78/16f84f524d7daebc5d2831d43ec9/d107b0a1-790d-4d4d-b87b-e168849235ca.pdf)
- Costs: the authors estimate ~25 bps as a reasonable cost. At an 80 bps cost, NN4 long-only Sharpe falls from 1.68 to 1.46 and L/S from 2.91 to 2.34. Excluding stocks locked at price limits barely matters (Table 11, equal-weighted long-only: NN4 Sharpe 1.78 → 1.70). Restricting to the top-70% size universe lowers returns, but ML still dominates OLS-3 and 1/N. — [Leippold](https://zibs.zju.edu.cn/_upload/article/files/21/78/16f84f524d7daebc5d2831d43ec9/d107b0a1-790d-4d4d-b87b-e168849235ca.pdf)
- Model ranking: NNs best, then regularized linear, with trees (GBRT, RF) lagging. — [Leippold](https://zibs.zju.edu.cn/_upload/article/files/21/78/16f84f524d7daebc5d2831d43ec9/d107b0a1-790d-4d4d-b87b-e168849235ca.pdf)

**Global / multi-region**
- Tobek & Hronec (working-paper version): 153 published anomalies, using only anomalies published before each estimation date, in a **liquid universe** (~1,000 most liquid US stocks; average cap $12bn in the US and Europe, $5bn in Japan and Asia-Pacific). Test 1995-01 to 2018-12, quintile sorts, monthly rebalance. NN, GBRT and RF beat least squares in all regions. The NN forecast is an ensemble of 5 random seeds, which gives "a great improvement". — [Tobek & Hronec PDF](https://library.utia.cas.cz/separaty/2020/E/hronec-0533567.pdf)
- Tobek & Hronec, long-only: the long-only NN leg earns about **5%/yr more than the market**, and the long leg supplies most of the L/S return. Turnover is about 100%/month. Net-of-cost annual returns are about 10% for ML strategies in the US, with a global NN net Sharpe close to 1. US-trained models capture most predictability abroad. Locally trained Japanese models do worse because the early-1990s bubble sits in the training window. — [Tobek & Hronec](https://library.utia.cas.cz/separaty/2020/E/hronec-0533567.pdf)
- Cakici, Fieberg, Metko & Zaremba (JEDC 2023): 46 markets, 148 characteristics, 11 algorithms. Forecast combination (ensemble) is "a clear winner". The pooled global VW L/S combination earns 1.51%/month with Sharpe 1.49. Predictability is dominated by momentum, reversal, value and size, and depends on firm size, information recency, the number of listed firms and idiosyncratic risk. — [ICM repository record](https://open.icm.edu.pl/items/f88ffcae-dc39-44e1-b48a-602d7612de55); figures via search snippet of the [full text](https://open.icm.edu.pl/bitstreams/7d3aefed-ecb3-4bc2-b2fc-850e0c4d622e/download) [secondary for numbers]
- A follow-up ("The More, the Better?", JBF 2026) finds global training data adds little over local data. Gains concentrate in smaller, high-idiosyncratic-risk markets. — [ICM record](https://open.icm.edu.pl/items/c1db3eb1-5d9c-48c1-a537-fe7303f5fea6)
- Hanauer & Kalsbach (Emerging Markets Review 2023): 32 EMs, >15,000 stocks, 36 standard characteristics, pooled EM training. Out-of-sample 2002-01 to 2021-12, quintiles VW relative to the country index. L/S is ~0.8%/month for OLS/ENet, ~1.0% for RF/GBRT and 1.2% for NN and the all-model ensemble. Factor-adjusted alpha is ~0.2%, ~0.5% and ~0.7%. Ensembling could add "up to 2% per annum" net of costs. Top features are price-to-52-week-high, idiosyncratic volatility and turnover. Long-only top-quintile numbers are not given in text. — [Robeco summary (co-author's firm)](https://robeco.com/en-uk/insights/2023/10/using-machine-learning-for-emerging-market-equity-returns) [L/S-only in available text]
- Choi, Jiang & Zhang ("Alpha Go Everywhere", ABFER 2023 working paper): Datastream data, 36 characteristics. NNs trained per market beat US-trained global models, and **regression trees underperform linear models when the number of observations is low**. Pooled global NN L/S Sharpe is 3.90 EW / 1.69 VW, vs market 0.96 / 0.53 and best linear 2.59 / 1.04 (test 1990–2017, gross). — [Choi–Jiang–Zhang PDF](https://www.abfer.org/media/abfer-events-2023/annual-conference/papers-investment/AC23P3084-Alpha-Go-Everywhere-Machine-Learning-and-International-Stock-Returns.pdf)
- **Taiwan (Choi–Jiang–Zhang)**: train 2007–2010, validation 2010–2012, test 2012–2017, 93,079 stock-months. Locally trained annualized L/S Sharpe ratios:

  | | Market | OLS | RF | GBRT+H | NN4 | NN3 |
  |---|---|---|---|---|---|---|
  | EW | 0.95 | 0.98 | 1.08 | 0.95 | 0.99 | — |
  | VW | 1.09 | −0.65 | 0.70 | 0.29 | 0.24 | −0.24 |

  With US-trained models over the Taiwan sample, EW Sharpe is market 0.43, GBRT 1.30, RF 1.20, NN2 1.26, and VW is market 0.40, RF 0.49, NN5 0.43. Taiwan is the weakest Asian market in the table. US-trained EW figures for comparison: Korea GBRT 2.20 (VW RF 1.07); Japan NN1 1.86 (VW NN2 0.91). — [Choi–Jiang–Zhang Tables 1–2, Appendix B](https://www.abfer.org/media/abfer-events-2023/annual-conference/papers-investment/AC23P3084-Alpha-Go-Everywhere-Machine-Learning-and-International-Stock-Returns.pdf)
- **Taiwan (Bui, Kong, Lin & Lin, Pacific-Basin Finance Journal 2023):** 86 anomalies. NN and PLS L/S spreads are 1.20%–1.50%/month. Five of the top-20 predictors are momentum-related, although Taiwan is usually a market where plain momentum fails. — [IDEAS/RePEc abstract](https://ideas.repec.org/a/eee/pacfin/v82y2023ics0927538x23002494.html) [abstract only; sample period, weighting and costs not seen] [L/S-only]
- Japan: Abe & Nakayama (2018) use MSCI Japan constituents, 25 factors and one-month-ahead returns. Deeper NNs beat shallow NNs, SVR and RF. — [arXiv 1801.01777](https://arxiv.org/pdf/1801.01777) [pre-2015 test; practitioner working paper]
- Europe: Drobetz & Otto (Journal of Asset Management 2021) find classification-based portfolio formation with an SVM, which avoids estimating stock-level expected returns, performs better than the NN regression approach. — [Springer](https://link.springer.com/10.1057/s41260-021-00237-x) [abstract only]
- Korea: I found only index-level ML studies for KOSPI. Results are mixed, and ensembles did not improve accuracy in one PLOS ONE study. — [PLOS ONE / PMC](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5685607/) [not cross-sectional]

### Inferences
- For a long-only book the honest benchmark is **top-quantile ML vs top-quantile simple linear composite**, not L/S Sharpe. In GKX the risk-adjusted long-only gap is close to zero (0.81 vs 0.79 VW). Larger long-only gains appear in retail-dominated, less efficient markets (China; Tobek–Hronec's ~5%/yr global long leg).
- Taiwan-specific academic evidence is thin, short-sampled (Choi et al. test is only 2012–2017) and mixed. VW ML spreads in Taiwan did not beat simple holding of the market on a Sharpe basis. That is consistent with our need to judge each rule against 0050-equivalent cash flows rather than against L/S statistics.
- Predictor importance shifts toward **liquidity/turnover, volatility and 52-week-high** in Asian and emerging markets (Leippold, Hanauer–Kalsbach). These are features we should keep and test as interactions in GBDT. Momentum is weak in China but useful in Taiwan ML (Bui et al.).
- Model class: GBDT is a defensible default. NNs win in several papers, but in small samples trees can underperform linear models (Choi et al.). Taiwan's daily panel is large in rows but short in independent months.

### Gaps
- Leippold et al. long-only figures appear to be top-decile VW realized returns. The table formatting was garbled in extraction, so values were cross-checked against Table 10 (cost table), which is clean.
- I could not access full texts for Bui et al. (Taiwan), Drobetz & Otto (Europe numbers), Hanauer–Kalsbach long-only figures, or Cakici et al. tables (403). No Korea cross-sectional ML paper with firm characteristics was found.
- No published study found that tests GKX-style ML on Taiwan with a daily decision frequency or long-only 20-stock portfolios.

## 2. Complexity, SDF models, autoencoders, and image-based CNNs (Re-Imag(in)ing Price Trends)

### Takeaway
- **Image CNNs (Jiang–Kelly–Xiu 2023):** the most relevant result here for a daily-data shop. Gross Sharpe ratios are very high, but most of the power sits in the **first week** with ~700%/month turnover. Monthly and quarterly versions keep Sharpe 1.3–2.4 (EW, gross) with 60–175% monthly turnover. Patterns transfer to 26 non-US markets (not Taiwan), but internationally the predictability dies after ~5 days.
- **SDF and autoencoder models:** high Sharpe ratios that shrink sharply in large or liquid stocks.
- **Virtue of complexity:** a market-timing (time-series) result, now contested.

### Cited Findings
**Jiang, Kelly & Xiu, "(Re-)Imag(in)ing Price Trends" (JF 2023)** [US core; international transfer]
- Setup: images of OHLC, moving average and volume over 5/20/60 days, used to predict the probability of an up move over 5/20/60 days. US 1993–2019; CNN trained and validated on 1993–2000 and held fixed for the 2001–2019 test. — [JKX JF PDF](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Weekly, EW:** H-L Sharpe is 7.15 (5-day image), 6.75 (20-day) and 4.89 (60-day). Benchmarks score 0.07 (MOM), 1.76 (STR), 2.84 (WSTR) and 2.92 (TREND). **The long-only top decile alone earns Sharpe >1.8 across CNN models** (2.89 and 54%/yr gross for I5/R5). Turnover is ~690%/month. — [JKX Table I](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Weekly, VW:** H-L Sharpe is 1.4–1.7 (I5/R5 1.49; I20/R5 1.74), with the VW top decile around Sharpe 0.9. Turnover is ~730–760%/month. — [JKX Table I](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Monthly (EW, CNN trained on 20-day returns):** H-L Sharpe is 2.35 (I5/R20), 2.16 (I20/R20) and 1.29 (I60/R20), with turnover ~155–175%/month (about the same as STR). Top decile Sharpe is about 1.0–1.1. **Quarterly (I5/R60):** H-L Sharpe ~1.3 at ~60%/month turnover, comparable to monthly momentum's turnover. — [JKX Table II](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- Most monthly-strategy return accrues on days 1–5. Days 6–20 still give Sharpe 0.4, 1.2 and 0.8 for 5-, 20- and 60-day images. — [JKX](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- Restricted to the largest 500 stocks, Sharpe ratios stay above 1.0 for both EW and VW. With 10 bps costs (large stocks) and 20 bps (others), net EW Sharpe reaches up to 4.0 weekly, 1.5 monthly and 0.9 quarterly. — [JKX](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- Interpretation: known signals (dollar volume, size, reversal, Amihud) explain only ~10% of CNN forecast variation. One approximating rule: a stock that closes near the **low end of its recent high–low range** tends to have high future returns. Image scaling, which normalizes each stock to its own range, is a key ingredient. — [JKX](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- No single image length or supervision horizon dominates. Quarterly strategies often benefit from models supervised on **shorter** horizons. — [JKX fn. 10](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **International:** the US model applied without retraining to 26 markets (Datastream, incl. Japan, South Korea, Hong Kong, Singapore, India, China via CSMAR; **Taiwan not included**; mostly 1993–2019) gives EW I5/R5 H-L Sharpe of 3.6 on average vs 2.3 for locally retrained models. VW improves from 1.0 to 1.9. Gains are largest in small markets; for large markets local retraining is equal or better. **For monthly strategies, international predictability is insignificant beyond the first five days.** — [JKX Section V.A, Table X](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- Related CNN/ResNet trend work reports one-day L/S turnover above 3,200%/month vs 281% for momentum. — [arXiv 2408.08483](https://arxiv.org/pdf/2408.08483) [secondary]

**Chen, Pelger & Zhu, "Deep Learning in Asset Pricing" (Management Science 2024)** [US-only] [L/S SDF portfolio]
- GAN-based SDF out-of-sample annual Sharpe is 2.6, vs 1.7 for its linear special case, 1.5 for the deep-learning return-forecasting approach and 0.8 for FF5. It explains 8% of individual-return variation and 23% of expected returns. — [arXiv v4](https://arxiv.org/pdf/1904.00745v4)
- Size sensitivity: Sharpe is 1.4 on the 1,500 largest stocks and 0.9 on the 550 largest, where linear models "collapse". Dropping the smallest 40% gives Sharpe 1.73. — [arXiv v4](https://arxiv.org/pdf/1904.00745v4)

**Gu, Kelly & Xiu, "Autoencoder Asset Pricing Models" (J. Econometrics 2021)** [US-only]
- Conditional autoencoder with characteristic-driven betas and a no-arbitrage restriction; out-of-sample pricing errors are smaller and generally insignificant vs leading factor models. — [SSRN](https://papers.ssrn.com/abstract=3335536)
- International re-test (Nechvátalová 2025, IRFA): moving to a liquid sample cuts profitability 60%–85% before costs, and after costs only liquid strategies stay profitable. **Long-only liquid strategies reach Sharpe 0.65–0.78.** — [UTIA PDF](https://library.utia.cas.cz/separaty/2025/E/nechvatalova-0639180.pdf) [via search snippet]

**Kelly, Malamud & Zhou, "The Virtue of Complexity in Return Prediction" (JF 2024)** [market timing, not cross-section]
- Theory and evidence that over-parameterized models (parameters > observations, e.g. random Fourier features) can beat parsimonious ones in return prediction. — [SFI](https://www.sfi.ch/en/publications/the-virtue-of-complexity-in-return-prediction)
- Critique, Nagel ("Seemingly Virtuous Complexity", NBER w34104, 2025): with short training windows, the RFF forecast collapses to a recency-weighted average of past returns, i.e. **volatility-timed momentum**. The same machinery builds the same strategy on simulated reversal data, where it fails. — [NBER](https://www.nber.org/papers/w34104)
- Reply, Kelly & Malamud ("Understanding the Virtue of Complexity", SFI 25-96): the challenges have "little bearing" on the results. — [SSRN](https://papers.ssrn.com/abstract=5346842)
- Another paper finds the KMZ and Nagel momentum strategies lose significance after controlling for the historical-average benchmark. — [arXiv 2608.23761](https://arxiv.org/pdf/2608.23761)

### Inferences
- For daily-data long-only Taiwan rules, image/price-pattern ML is the closest academic analogue. The evidence says the edge is mostly **days 1–5**, which needs ~700%/month turnover. That is incompatible with Taiwan's round-trip costs (see §5 inference). Only the 20–60-day supervised variants (turnover 60–175%/month) are plausibly implementable, and their international evidence beyond day 5 is weak.
- The cheap, testable takeaway from JKX is a **range-normalized close position** feature: close relative to the N-day high–low range, with volume scaled the same way. It can be added to a GBDT factor set rather than building a CNN.
- SDF, autoencoder and complexity papers are mainly L/S or market-timing results. Their size sensitivity (CPZ Sharpe 2.6 → 0.9 on the largest 550 stocks) is the relevant warning for a 20-stock long-only book.

### Gaps
- Per-country international Sharpe ratios in JKX Table X were garbled in extraction. Only averages are reported here. No Taiwan image-CNN replication found.
- I did not verify the KMZ empirical Sharpe numbers.

## 3. Target engineering: ranks vs excess vs risk-adjusted returns, horizon length, classification, meta-labelling

### Takeaway
The best-documented choices:
- **Market-relative (abnormal) continuous return targets** beat raw or CAPM-adjusted and binary targets (Chen–Hanauer–Kalsbach).
- **Longer prediction horizons** (3–12 months) cut turnover and win net of costs post-2004 (Blitz et al.).
- Learning-to-rank losses can help, but the evidence rests on a single research group.
- Classification has mixed evidence; meta-labelling has essentially no peer-reviewed cross-sectional equity evidence.

### Cited Findings
- **Chen, Hanauer & Kalsbach (SSRN 2024), "Design Choices, Machine Learning, and the Cross-Section of Stock Returns"** [US, 1987–2021] [L/S]
  - 1,056 model variants: monthly top-minus-bottom returns range 0.13%–1.98% and Sharpe 0.08–1.82. The non-standard error from design choices is 59% larger than the standard error.
  - Return relative to the market (RET−MKT) is the best target and CAPM-adjusted is the worst. **Continuous targets beat** discrete ones.
  - All models do worse after 2004.
  - Source: [Quantpedia summary](https://quantpedia.com/design-choices-in-ml-and-the-cross-section-of-stock-returns/) [secondary]
- **Blitz, Hanauer, Hoogteijling & Howard, "The Term Structure of Machine Learning Alpha" (JFDS 2023)** [US] [L/S]
  - One-month-target models have strong gross alphas, but net performance is "close to zero after 2004" because of turnover.
  - Models trained on 3-, 6- or 12-month horizons do "considerably better" net, especially in 2004–2021, and load more on slower signals (quality, value).
  - A buy-hold-style cost-mitigation rule helps at all horizons.
  - Exact numbers were not available in the sources reached. — [Robeco summary](https://robeco.com/en-us/insights/2023/07/the-term-structure-of-machine-learning-alpha); [CXO Advisory](https://www.cxoadvisory.com/?p=44408) [secondary]
- **JKX:** aligning the supervision horizon with the holding period is the default. Yet no single supervision horizon dominates, and quarterly strategies sometimes do better with shorter-horizon labels. — [JKX](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Learning to rank (Poh, Lim, Zohren & Roberts 2020):** for cross-sectional momentum, pairwise/listwise ranking models give an "approximately threefold" Sharpe boost over traditional regress-then-rank approaches (monthly rebalance; Quantpedia lists the backtest as 1980–2019). — [arXiv 2012.07149](https://arxiv.org/abs/2012.07149v1) [gross; single group]
  - Follow-up on 31 currencies: ~30% Sharpe gain from context-aware learning to rank. — [arXiv 2105.10019](https://arxiv.org/pdf/2105.10019)
  - Follow-up on data-scarce settings: complex models over-fit with few samples. — [arXiv 2208.09968](https://arxiv.org/pdf/2208.09968)
- **Classification:** in Europe, SVM classification-based portfolio formation beat the NN regression approach. — [Drobetz & Otto 2021](https://link.springer.com/10.1057/s41260-021-00237-x)
- GKX found NN4/NN5 add nothing over NN3 on monthly regression targets, and used Huber loss for robustness in trees and linear models. — [GKX](https://dachxiu.chicagobooth.edu/download/ML.pdf)
- JKX trained CNNs as **classifiers** (probability of a positive return) and sorted on predicted probability. — [JKX](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Meta-labelling:** Joubert (JFDS), "Meta-Labeling: Calibration and Position Sizing", tests six position-sizing algorithms. Calibration significantly helps fixed sizing rules but not sizing functions learned from training data. Meta-labelling aims to raise the precision of a primary signal. — [Wikipedia summary with JFDS references](https://en.wikipedia.org/wiki/Meta-Labeling) [secondary; full text not accessed]

### Inferences
- Our excess-return label (stock minus market) matches the best-performing target family. The next most evidence-backed change is **horizon**: train on 20–60 trading-day forward excess returns (or an average of several horizons) instead of next-day, and hold positions longer. This targets the turnover problem directly.
- A cross-sectional **rank or percentile transform of the label** is cheap and robust to Taiwan's limit-up/down tails. Direct evidence that it beats continuous excess returns was not found (Chen et al. favour continuous). Test both as one trial, not a sweep.
- Meta-labelling is mostly practitioner literature. Use it only as a second-stage "should I take this trade" filter on an existing rule, and count it as a separate trial.

### Gaps
- No peer-reviewed comparison of rank vs raw vs volatility-scaled targets on Asian equities found.
- Exact numbers from the Term Structure paper (net alpha by horizon, turnover) were not retrievable: the SSRN/TUM pages were blocked.

## 4. Ensembles, forecast combination, time-varying model selection, rolling vs expanding windows

### Takeaway
Forecast combination is the most consistently replicated design win across US, global, EM and Chinese samples. Averaging model types and random seeds beats the best single model on both accuracy and returns. In the one systematic study, expanding windows beat rolling windows (+0.20%/month, US). Formal time-varying model selection has limited direct evidence.

### Cited Findings
- Cakici et al. (46 markets): the forecast combination is "a clear winner" in accuracy and returns. The authors attribute this to reduced forecast variance. — [ICM record](https://open.icm.edu.pl/items/f88ffcae-dc39-44e1-b48a-602d7612de55) [secondary for wording]
- Chen–Hanauer–Kalsbach: ensembles (ENS ML, ENS NN) have higher mean and median returns than all nine single algorithms, and the best of 1,056 models used ENS ML. RF is the worst on average. Expanding windows give **+0.20%/month** over rolling. Non-linear models beat linear only under some choices, e.g. continuous targets and expanding windows. — [Quantpedia](https://quantpedia.com/design-choices-in-ml-and-the-cross-section-of-stock-returns/) [secondary]
- Hanauer & Kalsbach (EM): the NN and all-model combination both earn 1.2%/month L/S (vs ~1.0% trees, ~0.8% linear). Ensembling could raise net expected returns by up to 2%/yr. — [Robeco](https://robeco.com/en-uk/insights/2023/10/using-machine-learning-for-emerging-market-equity-returns)
- Tobek & Hronec: averaging 5 NN seeds gives "a great improvement" in mispricing-strategy performance. — [Tobek & Hronec](https://library.utia.cas.cz/separaty/2020/E/hronec-0533567.pdf)
- GKX refit models annually on expanding training samples with a rolling fixed-length validation window. GBRT used ~30 features early in the sample, rising to ~50 later. — [GKX](https://dachxiu.chicagobooth.edu/download/ML.pdf)
- Leippold et al. kept GKX's scheme: refit yearly, expanding training, same-size validation. They propose an ex-ante model-selection step using a conditional superior predictive ability (CSPA) test. — [Leippold](https://zibs.zju.edu.cn/_upload/article/files/21/78/16f84f524d7daebc5d2831d43ec9/d107b0a1-790d-4d4d-b87b-e168849235ca.pdf)
- Qlib CSI300 benchmark (daily, 2017–2020 test, 20 seeds): **DoubleEnsemble** (a sample-reweighting, feature-selecting GBDT ensemble) has the best Alpha158 results: IC 0.0521, ICIR 0.42, annualized return 11.6%, IR 1.34. Plain LightGBM: IC 0.0448, annualized return 9.0%, IR 1.02. — [Qlib benchmarks README](https://raw.githubusercontent.com/microsoft/qlib/main/examples/benchmarks/README.md)
- Global vs local training: global data adds little except for small, high-idiosyncratic-risk markets ([Cakici et al. JBF 2026](https://open.icm.edu.pl/items/c1db3eb1-5d9c-48c1-a537-fe7303f5fea6)). Locally trained NNs beat US-trained ones ([Choi–Jiang–Zhang](https://www.abfer.org/media/abfer-events-2023/annual-conference/papers-investment/AC23P3084-Alpha-Go-Everywhere-Machine-Learning-and-International-Stock-Returns.pdf)). But US-trained models captured most predictability in Tobek–Hronec's liquid universe, and local Japan training was hurt by the bubble period ([Tobek & Hronec](https://library.utia.cas.cz/separaty/2020/E/hronec-0533567.pdf)).

### Inferences
- Cheapest robust upgrade to our GBDT walk-forward: average several GBDTs (different seeds, feature subsamples, label horizons), optionally plus a regularized linear model. Combining models is a different design, not a near-duplicate parameter tweak, so it fits our research rules. Treat the ensemble as one pre-specified trial.
- Keep expanding windows as the default. If concept drift is suspected (e.g. post-2020 retail surge in Taiwan), test recency-weighted expanding rather than short rolling windows. Short windows are what turned KMZ's complex model into volatility-timed momentum (Nagel).

### Gaps
- No Asian-market study isolating rolling vs expanding windows, or testing formal regime-switching model selection, was found.

## 5. Transaction-cost-aware ML, turnover control, portfolio-level optimisation

### Takeaway
Turnover is the main reason ML gains fail to replicate net. Typical monthly-rebalanced ML deciles turn over 100–150%/month; weekly CNN and daily LLM strategies turn over 700%/month to 190%/day. Methods that survive either lengthen the horizon, use buffer or partial rebalancing, or learn portfolio weights directly with a net-of-cost objective (JKMP). Those methods shift weight toward slow signals (value, quality) and away from short-term reversal.

### Cited Findings
- **Jensen, Kelly, Malamud & Pedersen, "Machine Learning and the Implementable Efficient Frontier" (RFS, in press 2026)** [US] [out-of-sample 1981–2020]
  - Cost-agnostic ML over-relies on "fleeting small-scale characteristics" and earns poor net returns. — [SFI](https://www.sfi.ch/en/publications/machine-learning-and-the-implementable-efficient-frontier); [SSRN](https://papers.ssrn.com/abstract=4187217)
  - Example: 1-month reversal in small stocks. — [Macrosynergy](https://macrosynergy.com/?p=43465) [secondary]
  - Learning portfolio weights directly with an economic, net-of-cost objective ("Portfolio-ML") gives the best implementable frontier. — [SFI](https://www.sfi.ch/en/publications/machine-learning-and-the-implementable-efficient-frontier)
  - The net frontier of plain Markowitz-ML "immediately dives into negative expected return". Net Sharpe declines along the frontier as risk and position size grow. — [Macrosynergy](https://macrosynergy.com/?p=43465) [secondary]
  - Economic feature importance: **quality and value** matter most for the implementable frontier, and short-term reversal adds little for a large investor. — [Inquire Europe summary](https://www.inquire-europe.org/news/in-case-you-missed-it-machine-learning-and-the-implementable-efficient-frontier/) [secondary]
  - Exact net Sharpe ratios were not retrievable.
- **Blitz et al. (Term Structure):** a buy-hold-style turnover rule plus longer-horizon targets turns post-2004 net ML alpha from about zero to positive. — [Robeco](https://robeco.com/en-us/insights/2023/07/the-term-structure-of-machine-learning-alpha) [secondary]
- **Lopez-Lira & Tang:** partial rebalancing (25% of the gap per day) cuts turnover from ~190% to ~46% per day. Gross Sharpe barely changes (2.97 → 2.89). At 10 bps round-trip, net Sharpe is better with partial rebalancing (1.34 vs 1.29). — [arXiv 2304.07619v6](https://arxiv.org/pdf/2304.07619v6)
- **JKX:** quarterly CNN strategies at ~60%/month turnover (similar to momentum) keep H-L Sharpe ~1.3 gross and ~0.9 net (EW). — [JKX](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf)
- **Tobek & Hronec:** monthly turnover is ~100%, which they say "can be easily reduced by staggered portfolio rebalancing". Liquid-universe ML stays significantly profitable net, at ~10%/yr in the US. — [Tobek & Hronec](https://library.utia.cas.cz/separaty/2020/E/hronec-0533567.pdf)
- **Leippold et al. (China):** long-only NN Sharpe holds up at 80 bps per-trade cost assumptions (1.68 → 1.46). — [Leippold](https://zibs.zju.edu.cn/_upload/article/files/21/78/16f84f524d7daebc5d2831d43ec9/d107b0a1-790d-4d4d-b87b-e168849235ca.pdf)
- **Blitz, Hoogteijling, Lohre & Messow (JPM 2023):** ML's edge over traditional models is often eroded by heavy trading; overall ML is "more evolution than revolution". — [Robeco summary](https://www.robeco.com/en-us/insights/2023/04/researchers-have-just-been-scratching-the-surface-of-ml-in-asset-management) [secondary]
- **Qlib CSI300 benchmark:** the default backtest charges 5 bps to buy and 15 bps to sell (min 5 CNY), uses Top-k=50 with n_drop=5 per day (a built-in turnover cap) and blocks trades at ±9.5% limits. — [Qlib LightGBM config](https://raw.githubusercontent.com/microsoft/qlib/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml)

### Inferences
- Taiwan's list-price round trip (commissions both sides plus the 0.3% sell-side securities transaction tax, roughly 0.5–0.6% before broker discounts) is project-context knowledge, not sourced in this note. It is several times the 10–20 bps US assumptions under which daily or weekly ML strategies already break even (Lopez-Lira: unprofitable at 20 bps round-trip). For daily-decision 20-stock rules, a **turnover budget is the binding constraint**: e.g. hold until rank drops below a looser exit threshold (enter top-20, exit beyond top-40/60), limit daily replacements (Qlib's n_drop idea), or partial rebalancing.
- The implementable-frontier result implies slow features (value, quality, profitability) should get more weight in a long-only Taiwan book than short-term reversal, even if reversal raises daily IC. IC ~0.06 measured on next-day returns can overstate net value.
- A cost-aware objective can be approximated without Portfolio-ML: score candidates on predicted excess return minus expected round-trip cost amortized over the expected holding period.

### Gaps
- JKMP and Term-Structure numeric tables (net Sharpe by method and horizon, turnover) could not be retrieved.
- No study found that applies cost-aware ML specifically to Taiwan or another market with a 0.3% sell tax.

## 6. Graph/relational models and transformer-style cross-sectional models (HIST, MASTER, StockMixer)

### Takeaway
On the one public, multi-seed, common-protocol benchmark (Qlib, China CSI300, daily, 2017–2020), graph and attention models improve IC only modestly over a tuned LightGBM:
- HIST IC 0.052 vs LightGBM 0.040 on Alpha360 raw-price features.
- vs 0.045–0.050 for GBDTs on Alpha158 engineered factors.
- Plain Transformers and TabNet are worse than LightGBM.

Reproducibility of headline papers is weak: confidential codebases, data-source drift, and few code releases.

### Cited Findings
- **Qlib CSI300, Alpha158 (engineered factors):** LightGBM IC 0.0448, RankIC 0.0469, annual return 9.0%, IR 1.02. XGBoost IC 0.0498, IR 0.91. Linear IC 0.0397, IR 0.92. MLP IR 1.14. Transformer IC 0.0264, IR 0.40. TabNet IC 0.0204. GATs (20 features) IC 0.0349, IR 0.73. DoubleEnsemble best, IR 1.34. — [Qlib README](https://raw.githubusercontent.com/microsoft/qlib/main/examples/benchmarks/README.md)
- **Qlib CSI300, Alpha360 (raw price/volume windows):** HIST IC 0.0522, RankIC 0.0667, annual 9.9%, IR 1.37. IGMTF IR 1.35. TRA IR 1.28. GATs IR 1.11. LightGBM IC 0.0400, IR 0.76. Transformer IR −0.34. KRNN IR −0.54. — [Qlib README](https://raw.githubusercontent.com/microsoft/qlib/main/examples/benchmarks/README.md)
- Qlib protocol: train 2008–2014, validation 2015–2016, test 2017-01-01 to 2020-08-01, TopkDropout (50, 5), benchmark SH000300. — [Qlib config](https://raw.githubusercontent.com/microsoft/qlib/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml)
- **HIST** (Xu et al.) mines concept-oriented shared information. Concepts are extracted from the web, and the public repo runs on Qlib Alpha360 for CSI100/CSI300. — [arXiv 2110.13716](https://arxiv.org/pdf/2110.13716)
- **MASTER** (AAAI 2024): original experiments ran on a confidential Qlib-based business codebase. The public Qlib version uses a different data source and timespan ("may differ in values") and drops the DropExtremeLabel preprocessing. A June 2025 repo notice flags a flaw in the Qlib version. — [MASTER GitHub](https://github.com/SJTU-DMTai/MASTER)
- **StockMixer** (AAAI 2024): an MLP-mixer architecture claiming state-of-the-art results. No independent replication was found. — [AAAI](https://ojs.aaai.org/index.php/AAAI/article/view/28681)
- A review of GNN methods in finance finds only ~24% of papers release code. — [arXiv 2111.15367](https://arxiv.org/pdf/2111.15367)

### Inferences
- For our setting, GBDT on engineered factors is already close to the Qlib frontier. Our IC ~0.06 is above Qlib's CSI300 LightGBM 0.045, though on a different market and label.
- Relational information (industry, co-movement, supply chain) is more cheaply added as **features**: industry-relative ranks, peer-group momentum, lead–lag peer returns. A full GNN/transformer stack has unverified and modest gains.
- Web-mined concept graphs risk hindsight bias, since the graph may be built after the fact. Any relational graph must be point-in-time.

### Gaps
- No peer-reviewed, independent replication of HIST, MASTER or StockMixer was found.
- Qlib's "annualized return" column is presumably excess return over CSI300 net of the configured costs. That is my understanding of Qlib's default analysis, not confirmed in the fetched README text.
- No Taiwan application of these architectures in peer-reviewed literature was found.

## 7. LLM / text-based return prediction (magnitudes and decay)

### Takeaway
LLM news signals predict next-day drift, but:
- the effect is concentrated in small stocks and **negative** news, i.e. the short leg;
- it has decayed quickly as adoption grew (Lopez-Lira–Tang Sharpe 6.5 → 1.2 from late 2021 to mid-2024);
- it becomes unprofitable at about 20 bps round-trip.

For a long-only daily Taiwan book with ~0.5%+ round-trip costs, academic evidence does not support LLM news as a standalone alpha. At most it is a feature or veto.

### Cited Findings
- **Lopez-Lira & Tang** (arXiv v6, Oct 2025) [US] [L/S]
  - Data: 4,123 US stocks' headlines, Oct 2021–May 2024, using post-knowledge-cutoff headlines.
  - The GPT-4 long-short daily strategy on overnight news earns 34 bps/day gross. Sharpe is 2.97 for overnight and 2.63 for intraday news.
  - **Long leg: 8 bps/day, Sharpe 0.78; short leg: 26 bps/day, Sharpe 2.01.**
  - Sharpe declines from 6.54 (2021Q4) to 3.68 (2022), 2.33 (2023) and **1.22 (Jan–May 2024)**, consistent with LLM adoption improving efficiency.
  - Costs: at 5 bps round-trip the cumulative return is >300%; at 10 bps >100%; at 20 bps **unprofitable**. Daily turnover is ~190%.
  - Excluding stocks ≤$5 and below the NYSE 20th size percentile, the gross cumulative return is still >300%.
  - Older models (GPT-1/2, BERT) fail to predict.
  - Source: [arXiv 2304.07619v6](https://arxiv.org/pdf/2304.07619v6)
- **Chen, Kelly & Xiu, "Expected Returns and Large Language Models"** [16 markets, 13 languages]
  - LLM embeddings (ChatGPT, LLaMA, RoBERTa, BERT), with ridge on next-day returns, beat word-based models (SESTM, Word2vec, Loughran–McDonald). Prices respond slowly to news, and gains are larger for complex narratives and negation. — [Sydney seminar listing](https://business.sydney.edu.au/events/research/2024/finance/dacheng-xiu)
  - The discussant notes the training label is the 3-day return around the article. Assuming daily costs of 10 bps (large) and 20 bps (small), **net annual Sharpe is ~1.5**. L/S returns are significantly lower for large firms. — [Discussion slides (S. Z. Li)](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2024/09/ExpectedReturnAndLLM_Discussion_SophiaZhengziLi_NoPause.pdf)
  - A simple benchmark, the Jiang–Li–Wang "news momentum" signal, earns 34% EW and 25% VW annually over 2000–2019 and survives costs. — [Discussion slides](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2024/09/ExpectedReturnAndLLM_Discussion_SophiaZhengziLi_NoPause.pdf)

### Inferences
- Both papers find the LLM edge strongest in the short leg and small, illiquid names, the opposite of what a long-only, cost-heavy Taiwan book can harvest. The documented decay further argues against investing here before price, volume and fundamental factors are exhausted.

### Gaps
- Gross Sharpe tables and per-market results (including whether Taiwan or Chinese-language news is covered) from Chen–Kelly–Xiu were not retrieved.

## 8. The most robust simple ML recipe for a small practitioner (what the literature converges on)

### Takeaway
- **Model and features:** a regularized GBDT and/or small NN ensemble on a few dozen well-known, point-in-time characteristics (price trend, liquidity/turnover, volatility, value, quality), standardized cross-sectionally.
- **Target:** market-relative excess returns.
- **Training:** expanding-window walk-forward, refit roughly yearly.
- **Ensembling:** average across seeds and model types.
- **Implementation:** longer prediction and holding horizons plus explicit turnover control, judged on long-only net performance in liquid names.

Expect ML's long-only edge over a simple linear composite to be small, of the order of a few %/yr. Single-specification backtests carry large non-standard errors.

### Cited Findings
- ML's advantage shrinks once practical factors are included, but can matter with a rigorous process. — [Blitz et al. JPM 2023 via Robeco](https://www.robeco.com/en-us/insights/2023/04/researchers-have-just-been-scratching-the-surface-of-ml-in-asset-management); [SSRN](https://papers.ssrn.com/abstract%3D4321398)
- Design-choice dispersion: Sharpe ranges 0.08–1.82 across reasonable specifications, with non-standard error 1.59× the standard error. Recommendations: market-relative continuous targets, expanding windows, ensembles, and robustness across choices. — [Chen–Hanauer–Kalsbach via Quantpedia](https://quantpedia.com/design-choices-in-ml-and-the-cross-section-of-stock-returns/)
- Realistic haircut: costs plus post-publication decay plus modern liquidity cut ML strategy returns ~57% on average. — [Azevedo–Hoegner–Velikov via Quantpedia](https://quantpedia.com/the-expected-returns-of-machine-learning-strategies/)
- Excluding microcaps removes ~62% of a GKX-type payoff. — [CUHK release on Avramov et al.](https://www.media-outreach.vn/View/45101/cuhk-business-school-research-looks-at-the-limitations-of-using-artificial-intelligence-to-pick-stocks)
- Long-only legs are where ML shows ~+5%/yr over the market in a liquid global universe ([Tobek & Hronec](https://library.utia.cas.cz/separaty/2020/E/hronec-0533567.pdf)) and Sharpe 0.65–0.78 for liquid long-only autoencoder strategies ([Nechvátalová 2025](https://library.utia.cas.cz/separaty/2025/E/nechvatalova-0639180.pdf)). The exception is China, a retail-heavy market, where long-only VW Sharpe reaches 1.5–1.8 ([Leippold et al.](https://zibs.zju.edu.cn/_upload/article/files/21/78/16f84f524d7daebc5d2831d43ec9/d107b0a1-790d-4d4d-b87b-e168849235ca.pdf)).
- Use only published or point-in-time features. Restricting features to already-published anomalies costs ~0.52%/month of backtest return in US data, i.e. ignoring it inflates results. — [Quantpedia on Chen–Hanauer–Kalsbach](https://quantpedia.com/design-choices-in-ml-and-the-cross-section-of-stock-returns/) [secondary; summary wording ambiguous]; see also [Tobek & Hronec](https://library.utia.cas.cz/separaty/2020/E/hronec-0533567.pdf) (only anomalies published before estimation).
- Trees can underperform linear models when observations are few; market-specific training beats transferring US models for NNs. — [Choi–Jiang–Zhang](https://www.abfer.org/media/abfer-events-2023/annual-conference/papers-investment/AC23P3084-Alpha-Go-Everywhere-Machine-Learning-and-International-Stock-Returns.pdf)
- Short-horizon price patterns carry the most gross alpha but need market-maker-level costs. — [JKX](https://economics.yale.edu/sites/default/files/2023-11/The%20Journal%20of%20Finance%20-%202023%20-%20JIANG%20-%20Re%25E2%2580%2590%20Imag%20in%20ing%20Price%20Trends_0.pdf); [Lopez-Lira & Tang](https://arxiv.org/pdf/2304.07619v6)

### Inferences (mapped to our context: Taiwan, long-only 20 stocks, daily close, ~40 factors, GBDT IC ~0.06, excess labels, 0050 benchmark only)
1. **Keep the GBDT core, and ensemble it.** Seeds, feature-bagging and 2–3 label horizons, plus optionally a ridge/ENet model. This is the most-replicated improvement (Cakici et al.; Chen–Hanauer–Kalsbach; Hanauer–Kalsbach; Tobek–Hronec).
2. **Lengthen the label horizon** to 20–60 trading days of market-relative excess return, and decouple decision frequency from holding period. Decide daily, but only swap a name when the newcomer's score beats the incumbent's by a cost-covering margin, with hysteresis bands. Evidence: Blitz et al. term structure; JKX horizon results; Lopez-Lira partial rebalancing; JKMP.
3. **Evaluate only what we can trade:** a long-only top-20 net of Taiwan costs, vs the same cash flows in 0050. Do not use L/S Sharpe or IC alone. GKX shows ML's L/S advantage can vanish in the long leg, and Choi et al. show Taiwan VW ML spreads below the market's Sharpe.
4. **Feature priorities** for Asia (Leippold; Hanauer–Kalsbach; GKX):
   - liquidity/turnover and abnormal turnover;
   - idiosyncratic volatility;
   - 52-week-high proximity;
   - short- and intermediate-term price trend (momentum works in Taiwan ML per Bui et al.);
   - value/quality, for the low-turnover part of the book (JKMP);
   - the JKX range-position feature (close within recent high–low range).
5. **Lower priority / likely not worth it now:** GNN/transformer architectures (modest, poorly replicated gains); LLM news alpha (short-leg, decaying, cost-fragile); high-frequency CNN images (turnover). Complexity-style RFF models (contested).
6. **Research hygiene:** treat each design change (target horizon, ensemble, cost-aware selection) as one pre-registered trial. Design-choice non-standard errors are larger than standard errors, so a single best backtest overstates expected live performance.

### Gaps
- No academic study found that directly validates a long-only, daily-decision, ~20-stock ML strategy in Taiwan net of Taiwan's sell tax. All Taiwan academic evidence found is monthly, L/S or short-sample.
- I could not verify GKX-style long-only results for Japan or Korea.
