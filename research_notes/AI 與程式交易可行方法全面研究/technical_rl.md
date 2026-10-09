# Systematic technical analysis on individual stocks and RL trading agents: evidence for a pure-TA RL robot on Taiwan stocks

Scope: (1) which technical signals have out-of-sample (OOS) evidence, (2) which RL formulations held up and which were fragile, (3) documented failure modes and remedies, (4) alternatives that reach "robot decides buy/sell" more reliably. Project context: PPO, one shared policy across all stocks, state = 20 technical-factor percentiles + market state + position, action = in/out, reward = next-day excess over the average stock minus costs. Training reward stayed near 0 and OOS was noise. A weekly (5-day reward) version is training. The factor scan found technical combos (52-week high + RSI + MACD) that pass the project gate.

Evidence-quality flags used below: **[IS]** = in-sample or authors' own backtest only; **[2nd]** = number taken from a secondary summary, not from the paper's tables; **[weak]** = blog, thesis, GitHub or non-peer-reviewed.

---

## Q1. Which technical signals have real out-of-sample evidence (including data-snooping-adjusted tests and Asia/Taiwan evidence)?

### Takeaway
Classic single-series timing rules (MA crossovers, filters, trading-range breaks, oscillators such as RSI, KD and MACD) look good in early or in-sample data. They mostly fail once you correct for data snooping, add even low costs, or test after about 1990, and this includes the Taiwan index. The technical evidence that survives is **cross-sectional and slow, built from many horizons**: the multi-horizon moving-average "trend factor", nearness to the 52-week high, 12-month time-series momentum, and learned chart patterns (CNN). The large gross numbers sit mostly in small, illiquid, high-turnover stocks. Among the project's combo inputs, the 52-week high is the component with by far the strongest literature support. RSI and MACD as standalone timing rules have weak support.

### Cited Findings

**Classic timing rules and data-snooping corrections**
- Brock, Lakonishok & LeBaron (1992, JF) tested moving-average and trading-range-break rules on the DJIA, 1897–1986, with a bootstrap. Buy signals beat sell signals, returns after sell signals were negative, and the results contradicted random-walk, AR(1), GARCH-M and EGARCH nulls. Costs were not netted. **[IS for a single, much-studied series]** — [IDEAS/JF](https://ideas.repec.org/a/bla/jfinan/v47y1992i5p1731-64.html). AAII cites trading-range-break buy-signal returns of +0.63% vs −0.24% after sell signals over 10 days, against a 0.17% unconditional mean **[2nd]** — [AAII](https://www.aaii.com/journal/article/two-technical-analysis-rulespass-academic-muster)
- Sullivan, Timmermann & White (1999, JF) expanded BLL's 26 rules to a universe of about 7,846 rules and applied White's Reality Check (WRC) to 100 years of DJIA data. The best rule looked significant over the long sample. In the true OOS decade 1987–1996 the best rule earned about 8.63%/yr before costs and was statistically insignificant **[2nd]** — [LSE record](https://researchonline.lse.ac.uk/id/eprint/119144); [FMG DP303](https://www.fmg.ac.uk/sites/default/files/2020-11/dp303.pdf)
- Hsu & Kuan (2005, J. Financial Econometrics) ran about 39,832 rules (filters, MA, support/resistance, channel breakouts, plus "complex" strategies) through WRC and Hansen's SPA test on DJIA, S&P 500, NASDAQ Composite and Russell 2000. Significant profits appeared only in "young" markets (NASDAQ, Russell 2000), not in mature ones (DJIA, S&P 500). Sample about 1989/90–2002/03; secondary sources disagree on the exact dates — [IDEAS](https://ideas.repec.org/a/oup/jfinec/v3y2005i4p606-628.html); [CXO summary](https://www.cxoadvisory.com/technical-trading/technical-trading-thoroughly-tested)
- Bajgrowicz & Scaillet (2012, JFE) studied the DJIA 1897–2011 with a false-discovery-rate correction, which has more power than WRC. An investor **could not have identified the future best-performing rules in advance** (persistence fails), and **even in-sample, the edge is erased by low transaction costs** — [IDEAS/JFE](https://ideas.repec.org:443/a/eee/jfinec/v106y2012i3p473-491.html); [CFA digest](https://rpc.cfainstitute.org/research/cfa-digest/2013/02/technical-trading-revisited-false-discoveries-persistence-tests-and-transaction-costs-digest)
- Park & Irwin (2007, J. Economic Surveys) reviewed 95 "modern" studies: 56 positive, 20 negative, 19 mixed. Profits looked consistent "at least until the early 1990s". Most studies suffer from data snooping, ex post rule selection, and poor handling of risk and costs — [IDEAS](https://ideas.repec.org/a/bla/jecsur/v21y2007i4p786-826.html)
- A 2023 study (Financial Markets and Portfolio Management) covered 23 developed and 18 emerging index markets. In-sample performance was "sizable and highly statistically significant" even at 50 bp one-way costs. Out of sample it was "mostly insignificant or negative", and the authors note that in-sample performance is by construction the product of data snooping — [Springer](https://link.springer.com/article/10.1007/s11408-023-00433-2)
- Allen & Karjalainen (1999, JFE) used a genetic algorithm to evolve rules on the S&P 500, 1928–1995, selecting only on pre-test data. Net of costs the rules did **not** beat buy-and-hold OOS. They did sort days into high-return/low-volatility ones, which the authors attribute mostly to low-order serial correlation in index returns. The 1995 working paper's favorable 1970–89 result did not survive into the published version — [JFE PDF](https://www.cs.montana.edu/courses/spring2007/536/materials/Lopez/genetic.pdf); [SSRN](https://papers.ssrn.com/abstract=6996)
- Lo, Mamaysky & Wang (2000, JF) built kernel-regression pattern recognition (head-and-shoulders, double bottoms and so on) on US stocks, 1962–1996. Several patterns carry "incremental information" (the conditional return distribution differs from the unconditional), especially for NASDAQ stocks. The paper tests information content, **not** trading profitability, and the authors note that patterns optimal for detecting anomalies need not be optimal for profit — [NBER w7613](https://www.nber.org/papers/w7613); [CFA digest](https://rpc.cfainstitute.org/-/media/documents/article/cfa-digest/2001/dig-v31-n1-811-pdf.pdf)
- Under the adaptive-markets view, MA-rule predictability that held in DJIA, FT30 and TOPIX is expected to last "only for a limited period of time" — [Urquhart et al. (Hull repository)](https://hull-repository.worktribe.com/OutputFile/374551)
- In the US, a moving-average-of-prices predictor beat the historical mean in and out of sample, but its power sat in the most recent ≤10 daily lags and vanished beyond 20 days (Ma et al. 2019, as cited) — [search summary pointing to vLex record](https://international.vlex.com/vid/further-mining-the-predictability-855658655) **[2nd]**

**Cross-sectional and slow technical signals (the ones that survive)**
- **Trend factor** (Han, Zhou & Zhu 2016, JFE 122(2):352–375). Each month, regress next-month returns on price-normalized SMAs at lags 3, 5, 10, 20, 50, 100, 200, 400, 600, 800 and 1000 days. Average the coefficients over the last 12 months, then sort into quintiles, long-short, equal-weighted. For US stocks 1926–2014 it earned 1.63%/month gross with Sharpe 0.47 (monthly), versus momentum 0.79%/0.10, short-term reversal 0.79%/0.23 and the market 0.62%/0.12. Max drawdown was −20% vs −99% for momentum. Turnover was about 65.6%/month, with break-even cost 1.24% vs 0.68% for momentum. It works best where information is uncertain: high idiosyncratic volatility, high turnover, low analyst coverage and young firms. **Returns are gross and equal-weighted** — [CXO summary](https://www.cxoadvisory.com/technical-trading/trend-factor-and-stock-returns); [WUSTL record](https://profiles.wustl.edu/en/publications/a-trend-factor-any-economic-gains-from-using-information-over-inv/). A later thesis re-examines it with a skip period — [Aalto](https://aaltodoc.aalto.fi/items/d7b72224-fcc3-4917-abe3-655d8b4badc0/full)
- **Time-series momentum** (Moskowitz, Ooi & Pedersen 2012, JFE) covered 58 futures and forwards (equity indices, FX, commodities, bonds), 1985–2009. A trailing 12-month excess return predicts the next month, and the 12-month TSMOM was positive for **every one of the 58** contracts. The effect persists about a year, then partly reverses, and does best in extreme markets. Note: futures, not individual stocks — [SSRN](https://papers.ssrn.com/abstract=2089463); [AQR data](https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data)
- **52-week high** (George & Hwang 2004, JF): nearness to the 52-week high forecasts returns better than past returns and explains much of momentum. Buying stocks near the high and selling those far from it earned about 0.45%/month abnormal in the US — [IJBESAR Taiwan paper citing G&H](https://ijbesar.ihu.gr/docs/volume16_issue1/16_01_07.pdf); [CXO](https://cxoadvisory.com/technical-trading/the-52-week-high-as-a-momentum-indicator-for-individual-stocks)
- **CNN on price-chart images** (Jiang, Kelly & Xiu 2023, JF 78:3193–3249). Models were trained on US stocks 1993–2000 and tested OOS 2001–2019. Decile long-short portfolios sorted by predicted up-probability reached Sharpe ratios of about 7.2, 6.8 and 4.9 for the 5-, 20- and 60-day image models, **equal-weighted and apparently gross**. In the 500 largest stocks the Sharpe fell to "slightly above 1" **[2nd]**. The abstract says the patterns are robust to specification changes, work at monthly scale, and transfer to international markets — [SSRN abstract](https://papers.ssrn.com/abstract=3756587); [BSIC summary](https://bsic.it/technical-analysis-on-steroids/)

**Taiwan and Asia**
- A 2011 study (Review of Pacific Basin Financial Markets and Policies) tested Taiwan technical analysis, 1975–2006, with bootstrapped WRC and Hansen SPA, adjusting for non-synchronous trading and costs. It concluded that "reasonable returns cannot be obtained from technical analysis" in Taiwan — [NTU scholars record](https://scholars.lib.ntu.edu.tw/entities/publication/230ed60c-a9b8-40a3-9840-03bbb3c17bad); [World Scientific DOI](https://www.worldscientific.com/doi/10.1142/S0219091511002238)
- On the TAIEX, variable-length MA and trading-range-break rules (1975–2007) had buy-signal returns above sell-signal returns. Predictive power was considerable in 1975–85 and 1986–96 but "less effective over 1997–2007", attributed partly to liberalization (Kung) — [AgEcon Search record](https://ageconsearch.umn.edu/record/143216) (URL attribution from the search tool; verify)
- A 2006 study by Chang et al. reportedly found MA rules beat buy-and-hold in Taiwan after costs. This is a secondary mention only and was not verified **[2nd][weak]** — [ResearchGate page where cited](https://www.researchgate.net/publication/227653152_Data_Snooping_on_Technical_Analysis_Evidence_from_the_Taiwan_Stock_Market)
- An open-source Taiwan project tested 289 crossover and 512 filter rules (MA, KD, MACD, RSI, RSIMA), long-only, on Taiwan 50, High Dividend 50 and Mid-Cap 100 constituents. It used 2016–2020 in-sample, 2021–2022 OOS, and WRC. It claims KD is the most predictive signal and that many rules survive costs OOS. **[weak]**: no peer review, a 2-year OOS window, and implausible annualized returns (for example about 1,792% for one MACD crossover on 2327) — [GitHub TA-RealityCheck](https://github.com/alexpychen/TA-RealityCheck)
- 52-week high in Taiwan:
  - Tsai (2005, NTU thesis, 1989–2003): an adjusted 52-week measure was the most profitable of six momentum variants for short-term holding **[weak]** — [NTU TDR](https://tdr.lib.ntu.edu.tw/handle/123456789/38650)
  - A 2022 NTU thesis found that 52-week high was not significantly better than other momentum at a 6-month holding, but nearly first-order stochastically dominant at 12 months **[weak]** — [NTU TDR](https://tdr.lib.ntu.edu.tw/handle/123456789/84014)
  - Liao, Chang & Wang (2023): accounting for updates to the 52-week high itself raises momentum profits in Taiwan, a result the authors limit to relatively inefficient markets — [IJBESAR](https://ijbesar.ihu.gr/docs/volume16_issue1/16_01_07.pdf); [IDEAS](https://ideas.repec.org/a/tei/journl/v16y2023i1p71-86.html)
  - A Taiwan 1982–2012 study found the 52-week-high strategy "determinant" for momentum returns but hurt by strongly negative January returns. Anchoring and recency biases coexist — [IDEAS (Rev. Econ. & Finance 2016)](https://ideas.repec.org/a/eee/reveco/v43y2016icp121-138.html)
- Ku & Lin (2002): of 26 candidate factors, none had stable power to explain average Taiwan stock returns, though all captured co-movement — [IDEAS RPBFMP](https://ideas.repec.org/a/wsi/rpbfmp/v05y2002i01ns0219091502000675.html)
- MACD-type rules reportedly perform poorly in volatile markets (Dunis & Chen 2005, cited secondhand) **[2nd]** — [ResearchGate "Revisiting MACD and RSI"](https://www.researchgate.net/publication/276039141_Revisiting_the_Performance_of_MACD_and_RSI_Oscillators)

### Inferences
- The literature ranks the project's combo inputs as follows. **52-week high** is strong: replicated in the US and broadly supportive in Taiwan, though horizon- and January-dependent. **Multi-horizon MA trend** is strong cross-sectionally but gross and equal-weighted. **RSI, KD and MACD as timing rules** are weak or non-robust after snooping corrections. Used as cross-sectional percentiles combined with slow trend, they mostly act as short-term reversal and overbought conditioning on top of the trend. That role is defensible, but expect most of the edge to come from the slow trend component.
- The surviving signals operate at **monthly or 6–12-month horizons**: trend factor (monthly rebalance), TSMOM (12-month lookback, 1-month hold), 52-week high (6–12-month holds). A **next-day** reward is badly mismatched to the horizon at which technical information has documented value. Even the short-horizon CNN result (5-day) is concentrated in small and illiquid names.
- Taiwan index-level TA appears to have decayed after the late 1990s. Any rule found on 2015+ data should be expected to decay, so walk-forward re-estimation matters more than one fixed policy.
- The project's broad scan (39,676 candidates, per git log) and every RL hyperparameter or seed trial are a "universe of rules" in the STW/Hsu-Kuan sense. The gate needs a multiple-testing correction across the whole family (WRC, SPA, FDR or deflated Sharpe), not only across the final candidates. Bajgrowicz-Scaillet's persistence failure is the specific risk: the best in-sample rule does not stay best.

### Gaps
- No peer-reviewed study found of KD, RSI or MACD **on individual Taiwan stocks** with SPA/WRC correction and a long OOS window. The only one is the non-peer-reviewed GitHub project.
- No Taiwan replication of the Han-Zhou-Zhu trend factor was found.
- Not retrieved: Jiang-Kelly-Xiu value-weighted Sharpe, turnover and net-of-cost results; the exact horizon breakdown in Lo-Mamaysky-Wang.
- The 2021 Pacific-Basin Finance Journal paper on moving averages of **trading volume** (DOI 10.1016/j.pacfin.2021.101494) was located, but its sample and results were not retrieved — [IDEAS](https://ideas.repec.org/a/eee/pacfin/v65y2021ics0927538x21000019.html).

---

## Q2. Which RL formulations (state, action, reward, horizon, training scheme) produced robust results, and which were fragile?

### Takeaway
Independent reviews conclude that DRL trading papers usually show "statistically significant" wins over weak baselines but rarely decent net profitability. Robustness evaluations find most DRL agents degrade quickly OOS. The formulations that did work share a pattern:
1. **Inputs that already encode a known premium**: volatility-normalized multi-horizon returns, MACD, a supervised signal.
2. **Volatility-scaled positions.**
3. **Costs inside the reward.**
4. **A risk-adjusted objective**: Sharpe, differential Sharpe or Sortino.
5. **Many assets pooled into one shared network.**
6. **Walk-forward retraining.**

Even then, the evidence is mostly the authors' own backtests without seed variance. The strongest "RL" results (Moody-Saffell RRL, Lim et al.'s DMN) are effectively **direct, differentiable policy optimization of Sharpe**, not value-based long-horizon credit assignment.

### Cited Findings

**Surveys and critical evaluations**
- Hambly, Xu & Yang (2023, Mathematical Finance 33(3):437–503) survey MDPs, value-based and policy-based methods, and deep RL. Applications covered: execution, portfolio allocation, hedging, market making, order routing and robo-advising. It is a methods survey and not evidence of trading profitability — [IDEAS](https://ideas.repec.org/a/bla/mathfi/v33y2023i3p437-503.html); [arXiv 2112.04553](https://arxiv.org/abs/2112.04553v2)
- Pricope (2021) reviewed about 9 DRL trading studies. They generally report "statistically significant improvements" over baselines, but "no decent profitability level was obtained" for most, and the field is "in the very early stages". Specific findings:
  - Flaws: omitted costs in key studies, tiny universes (one stock or three contracts), training sets that miss crises, no slippage or latency, almost no live tests.
  - Numbers: a DDPG Dow-30 study reported 25.87%/yr vs 16.40% for the index; an ensemble 13–15%/yr at Sharpe 1.30 **[IS]**.
  - Recommendations: risk-aware rewards (Sharpe, Sortino), uncorrelated indicators, rolling retraining ("covariance shifts"), and awareness that on short timescales "fees might overcome the actual small gains".
  - Source: [arXiv 2106.00123](https://arxiv.org/html/2106.00123v1)
- A robustness benchmark of DRL for online portfolio selection (INISTA 2023) found "most Deep Reinforcement Learning algorithms were not robust", generalizing poorly and degrading quickly in backtests — [arXiv 2306.10950](https://www.arxiv.org/abs/2306.10950)
- PRUDEX-Compass (TMLR 2023) argues that most FinRL evaluation uses only profit metrics. It proposes 6 axes and 17 measures (profitability, risk control, universality, diversity, reliability, explainability) and evaluates 8 methods on 4 long-horizon datasets. Its result tables were not retrieved — [arXiv 2302.00586](https://arxiv.org/abs/2302.00586v2); [TMLR PDF](https://personal.ntu.edu.sg/boan/papers/TMLR23-PRUDEX.pdf)
- A 2026 Bitcoin DRL study illustrates validation-to-test collapse. The top validation model had Sharpe 2.11 on validation and −0.55 on test. "Beating" buy-and-hold meant beating a baseline with Sharpe −0.87. **[weak; URL attribution from search summary, verify]** — [arXiv 2607.16028](https://arxiv.org/pdf/2607.16028)

**Formulations with the best documented results**
- **Moody & Saffell, "direct reinforcement" / recurrent RL (NIPS 1998; IEEE TNN 12(4):875–889, 2001).** The policy is trained directly on a risk-adjusted performance function. The **differential Sharpe ratio** is an online, exponentially weighted Sharpe gradient, with transaction costs inside the objective. Results:
  - DSR-trained traders were steadier than profit-trained ones, and both beat an MSE-forecasting system.
  - Direct RL beat Q-learning systems and forecast-based trading.
  - In USD/GBP intraday with costs, RRL earned more than Q-learning **with fewer trades** **[2nd]**.
  - Monthly S&P 500 1970–1994 showed OOS predictability.
  - Sources: [NIPS 1998](https://papers.nips.cc/paper/1998/hash/4e6cd95227cb0c280e99a195be5f6615-Abstract.html); [TNN 2001 record](https://cs.utexas.edu/~shivaram/readings/b2hd-MoodySaffell2001.html); [Moody talk abstract](https://mailman.cs.uchicago.edu/mailman/public/colloquium/2001-February/000039.html)
- **Zhang, Zohren & Roberts (2019/2020, JFDS), "Deep RL for Trading", 50 liquid futures, OOS 2011–2019.**
  - State: the last 60 days of normalized close; 1-, 2- and 3-month and 1-year returns, each divided by EWMA vol (60-day) scaled to the horizon; MACD normalized by 63-day price std then 252-day std; RSI(30).
  - Actions: {−1, 0, 1} for DQN/PG, continuous [−1, 1] for A2C.
  - Reward: R_t = A_{t−1}·(σ_tgt/σ_{t−1})·r_t − bp·p_{t−1}·|(σ_tgt/σ_{t−1})A_{t−1} − (σ_tgt/σ_{t−2})A_{t−2}|, i.e. a **volatility-scaled position, with cost on the change in scaled position** at bp = 20 bp. Discount **γ = 0.3**.
  - Model: 2-layer LSTM (64, 32), one model per asset class shared across contracts, **expanding-window retrain every 5 years**.
  - Portfolio Sharpe: DQN 1.29, A2C 1.05, PG 0.75, vs long-only 0.06, Sign(R) TSMOM 0.44, MACD 0.09. DQN and A2C stayed profitable at 25 bp.
  - Caveats: long-only beat RL in **equity indices** in the test period; vanilla DQN was "unstable and variable"; PG learned slowly and hit local maxima; **no seed variance was reported** **[IS-ish: single walk-forward, authors' backtest]**.
  - Sources: [ar5iv 1911.10107](https://ar5iv.labs.arxiv.org/html/1911.10107); [arXiv abstract](https://arxiv.org/abs/1911.10107)
- **Deep Momentum Networks** (Lim, Zohren & Roberts 2019, JFDS). This is a **supervised** network with a **negative-Sharpe loss**, outputting tanh positions. It learns trend estimation and sizing jointly, which makes it effectively Moody-style direct optimization. On 88 futures, Sharpe was more than 2× traditional TSMOM **without costs**. It kept outperforming only up to **2–3 bp** costs. A turnover regularization term was added for illiquid assets, because "following small reversions in regimes of strong trend can lead to larger transaction costs" — [arXiv 1904.04912](https://arxiv.org/abs/1904.04912); [Zohren slides](https://cfe.columbia.edu/sites/default/files/content/slides/StefanZohren_Columbia-Bloomberg.pdf)
- **AlphaStock** (Wang et al., KDD 2019). One **shared** network scores all stocks, with cross-asset attention, and goes long winners and short losers. The RL objective is the **Sharpe ratio over the holding period, net of costs**, since the authors argue a "far-sighted steady" policy beats a short-sighted one. US annualized Sharpe was 2.132 vs market 0.239 and another DRL baseline (FDDR) 1.141 **[IS, no independent replication found]**. The learned winners had high long-term growth, low volatility, high intrinsic value and were recently undervalued — [arXiv 1908.02646](https://arxiv.org/pdf/1908.02646)
- **EIIE** (Jiang, Xu & Liang 2017). One identical network is applied per asset with shared parameters ("Ensemble of Identical Independent Evaluators"), plus portfolio-vector memory and online stochastic batch learning. On 30-minute crypto data it claimed "at least 4-fold returns in 50 days" at 0.25% commission. These were three ~50-day backtests in 2016–17 crypto. **[IS/short windows; a 2023 robustness study finds most DRL not robust]** — [arXiv 1706.10059](https://arxiv.org/abs/1706.10059)
- **FinRL ensemble** (Yang et al., ICAIF 2020): PPO, A2C and DDPG, with the agent chosen by rolling-window validation Sharpe, on Dow 30. Reproductions:
  - Test 2020-07 to 2021-06: ensemble Sharpe 2.81 vs DJIA 2.02 (A2C 2.24, PPO 2.23, DDPG 2.05).
  - FinRL-Meta rolling quarterly test 2020-07 to 2022-03: ensemble 1.53 vs DJIA 1.32 (A2C 1.37, PPO 0.99, DDPG 0.88).
  - The margin over the index is small and period-dependent; **single PPO underperformed the index** in the longer test — [FinRL-Meta arXiv 2211.03107](https://arxiv.org/pdf/2211.03107); [HackerNoon reproduction](https://sia.hackernoon.com/finrls-implementation-of-drl-algorithms-for-stock-trading); [ensemble paper](https://openfin.engineering.columbia.edu/sites/default/files/content/publications/ensemble.pdf)
- **RL on top of a known signal.** Brini & Tantari studied DRL traders where returns are driven by known mean-reverting factors, so an exact dynamic-programming optimum exists. Value-based DRL can retrieve the signal. Agents that "leverage classical strategies" **outperform the benchmark when price dynamics are misspecified**. RL adds value mainly as a cost-aware trading and sizing layer **given** a predictive signal — [arXiv 2104.14683](https://arxiv.org/abs/2104.14683)
- **Imitation, behavior cloning or demonstrations combined with RL:**
  - Dueling DQN with behavior cloning of a pre-built "investment expert" beat comparison strategies on annualized yield, Sharpe and Calmar (Yang et al. 2024) **[IS]** — [J. Systems & Management](https://xtglxb.sjtu.edu.cn/EN/Y2024/V33/I1/150)
  - A trend-labeling algorithm used as expert, giving an "imitation reward" mixed with the RL reward, beat RL-only agents — [arXiv 2411.08637](https://arxiv.org/html/2411.08637v1)
  - A demonstration buffer plus behavior cloning in RDPG used a **hindsight-optimal expert** (long at lows, short at peaks), which carries look-ahead risk — [arXiv 2312.15730](https://arxiv.org/pdf/2312.15730)
  - Survey: imitation learning helps agents mimic expert investors — [arXiv 2408.10932](https://arxiv.org/pdf/2408.10932)

### Inferences
- **Robust-ish ingredients, as repeated across the better papers:**
  - Volatility-normalized trend features. ZZR's state is essentially TSMOM plus MACD plus RSI; the network learns *how to trade* a known premium.
  - Volatility-scaled positions.
  - Cost charged on position change inside the reward.
  - A Sharpe-type objective computed over a holding period, not a single noisy day.
  - Parameter sharing across many assets (EIIE, AlphaStock, ZZR per asset class) to multiply samples.
  - Walk-forward retraining.
  - Agent ensembles or validation-based agent selection (FinRL).
- **Fragile patterns:** single stock or a small universe; test windows of weeks to 2 years; a test period that is a bull market for long-biased agents; no seed variance; hindsight experts; costs omitted or flat and tiny; baselines limited to buy-and-hold rather than the best simple rule.
- ZZR's γ = 0.3 means the "RL" is nearly myopic: about a 1.4-step effective horizon. Its value comes from cost-aware, position-dependent decisions and joint sizing, not long-horizon planning. That supports treating the project's robot as a **cost-aware execution/sizing layer over a supervised technical score**, not as an agent discovering alpha from raw percentiles.
- Parameter sharing across all stocks (the current design) is standard and sound. A shared per-stock in/out head without cross-sectional interaction, however, loses what AlphaStock and EIIE exploit: relative ranking within the same day. A cross-sectional formulation (score all stocks, then hold the top-K) aligns with where the technical evidence lives (Q1).
- The baseline the RL robot must beat is the **gate-passing 52-week-high + RSI + MACD rule itself**, on the same cash-flow account, net of costs. Beating "average stock" or 0 is not enough.

### Gaps
- No well-cited evidence was found on **distributional RL**, **offline RL** (CQL, IQL, decision transformers) or **curriculum learning** specifically for equity trading with credible OOS tests. These remain untested ideas, not documented remedies.
- Not retrieved: the Millea (2021) and Sun et al. (2023) survey texts; PRUDEX/TradeMaster result tables (which methods win on which axes).
- No independent replication of AlphaStock, and no multi-seed variance reports for ZZR or FinRL, were found.

---

## Q3. Specific failure modes (daily reward noise, non-stationarity, overfitting, "do nothing" because of costs) and documented remedies

### Takeaway
The project's symptoms match documented failure modes. Training reward near 0 with OOS noise is what you get when a **per-step daily reward has tiny signal-to-noise**, **switching costs are charged immediately while the benefit is buried in noise**, and the reward is **excess over the average stock**, under which "always in" and "always out" both earn about 0. The policy-gradient signal is then mostly noise, and the policy drifts to a degenerate or near-random state.

Documented remedies:
- Lengthen the decision and reward horizon, or use a holding-period Sharpe.
- Use differential Sharpe or volatility-scaled rewards.
- Feed a known signal into the state, or train on top of a supervised score.
- Give asymmetric or imitation rewards.
- Charge realistic costs on position changes, with position in the state.
- Sanity-check on a planted synthetic signal.
- Retrain walk-forward.
- Use ensembles and report seed variance.

### Cited Findings
- **Low SNR makes policy gradients noisy.**
  - A PPO study on daily Dow-30 data frames markets as low signal-to-noise, with very noisy policy gradients under stochastic dynamics — [arXiv 2112.04755](https://arxiv.org/pdf/2112.04755)
  - Rewarding a model on subsequent returns gives so little usable signal that optimization becomes unstable and generalization suffers. Under uncertain feedback the model drifted toward **neutral** predictions. Their fix was an **asymmetric reward**, where correct calls earn more than wrong calls lose — [arXiv 2411.11059](https://arxiv.org/pdf/2411.11059)
- **Noise mostly slows convergence when a true signal exists.** In a differentiable-RL trading study, noisy returns raised the steps needed to reach 99% of optimal reward from about 1,500 to about 4,000. With enough training the strategy matched the noise-free one — [arXiv 2112.02944](https://arxiv.org/pdf/2112.02944)
- **Costs versus doing nothing.**
  - Costs plus noisy prices can make staying flat the optimal policy. Reward raw P&L without costs and the agent "learns to churn".
  - Recommended debugging step: verify the agent can learn a **planted signal on synthetic mean-reverting data** before using real data. If it cannot, the setup is broken, not the market **[weak]** — [QuantJourney](https://quantjourney.substack.com/p/reinforcement-learning-for-trading)
- **Unrealistic flat costs.** A flat 10 bp cost ignores size, volatility and volume, letting backtested agents trade at unrealistic frequency — [arXiv 2603.29086](https://arxiv.org/html/2603.29086v1)
- **Turnover from fast reversals.** DMN gains came partly from fast reversion inside trends, which drives transaction costs. Remedy: a turnover regularization term in the loss — [Zohren slides](https://cfe.columbia.edu/sites/default/files/content/slides/StefanZohren_Columbia-Bloomberg.pdf); [arXiv 1904.04912](https://arxiv.org/abs/1904.04912)
- **Algorithm instability.** Vanilla DQN was unstable, which motivated fixed targets, Double DQN and Dueling DQN. PG with episode-end updates learned slowly and got stuck in local maxima. A2C had high turnover — [ar5iv 1911.10107](https://ar5iv.labs.arxiv.org/html/1911.10107)
- **Entropy collapse.** A SAC trading agent collapsed exploration entropy into a degenerate policy that looked fine in training but lost money to fees; the author switched to PPO **[weak]** — [GitHub](https://github.com/Thomas-lcb/Deep-Reinforcemen-tLearning-Trading-Agent)
- **Non-stationarity.** Reviewers recommend retraining on rolling windows because "covariance shifts" — [arXiv 2106.00123](https://arxiv.org/html/2106.00123v1). ZZR use expanding-window retraining every 5 years — [ar5iv](https://ar5iv.labs.arxiv.org/html/1911.10107). Taiwan TA power faded after 1997 — [AgEcon](https://ageconsearch.umn.edu/record/143216)
- **Overfitting and selection.**
  - Most DRL portfolio agents were "not robust" and degraded quickly in backtests — [arXiv 2306.10950](https://arxiv.org/abs/2306.10950)
  - The best in-sample technical rules do not persist — [Bajgrowicz & Scaillet](https://ideas.repec.org:443/a/eee/jfinec/v106y2012i3p473-491.html)
  - Validation Sharpe 2.11 became test −0.55 in a crypto DRL study **[weak, verify]** — [arXiv 2607.16028](https://arxiv.org/pdf/2607.16028)
- **Reward choice.** Differential-Sharpe-trained traders were steadier, and RRL traded less than Q-learning — [NIPS 1998](https://papers.nips.cc/paper/1998/hash/4e6cd95227cb0c280e99a195be5f6615-Abstract.html). Volatility scaling in the reward sizes positions up in calm regimes and down in turbulent ones — [arXiv 1911.10107](https://arxiv.org/abs/1911.10107)

### Inferences
These apply to the project's PPO robot. They are reasoned estimates: the numerical inputs are illustrative assumptions, not sourced facts.

- **Why daily training reward stays near 0.**
  - Suppose a decent daily technical score has a cross-sectional IC of about 0.03 and daily cross-sectional return dispersion of about 2.5%. A top-decile stock's expected next-day excess is then about 0.03 × 2.5% × 1.75 ≈ **0.13%/day**, against about **2.5%** noise. That is a per-sample SNR of about 0.05.
  - A Taiwan round trip costs roughly 0.1425% × 2 brokerage + 0.3% securities transaction tax ≈ **0.585%**, before broker discounts. (These are standard Taiwan cost parameters, not sourced in this note; confirm against the project's cost model.)
  - Every in/out switch therefore costs about 4–5 days of expected edge, and each day's benefit is invisible under noise.
  - Because the reward is excess over the average stock, both "always in" and "always out" have expected reward of about 0. PPO sees an almost flat landscape plus large noise, plus a certain penalty for switching. The predictable outcome is a near-constant or near-random policy, with training reward ≈ 0 and OOS ≈ noise.
- **What the weekly version buys.** If the edge persists across the 5 days, a 5-day reward improves per-decision SNR by at most about √5 ≈ 2.2× and amortizes the cost over 5 days. That helps, but it is still far below the 20-day to 12-month horizons where the 52-week-high, trend and TSMOM evidence lives (Q1).
- **Remedies ranked by evidence strength:**
  1. Put the **gate-passing technical score itself**, or a supervised or learning-to-rank score, into the state, and let RL only decide entry, exit and sizing. This follows Brini-Tantari and ZZR, whose state *is* a known premium.
  2. Reward over a **holding period** (20 days or until exit) as a **Sharpe or differential-Sharpe** of the account, with **volatility scaling** and cost charged on position change (Moody-Saffell, ZZR, AlphaStock).
  3. Use **behavior cloning from the gate-passing rule** as initialization, then PPO fine-tuning with a KL penalty or trust region toward the rule's policy (imitation papers). This avoids the do-nothing basin and makes "beat the rule" the explicit test.
  4. Add hysteresis or turnover regularization (DMN): enter at the top-K rank, exit only below a wider band.
  5. Run a planted-signal synthetic test to validate the pipeline.
  6. Retrain walk-forward, run multiple seeds and an agent ensemble, report the median and spread, and count every configuration as a trial in the multiple-testing correction.

### Gaps
- No peer-reviewed study was found that quantifies the "learns to do nothing" collapse in equity RL or tests remedies head-to-head. The evidence is mechanistic, from preprints and blogs.
- No quantitative study was found comparing daily vs weekly vs monthly decision horizons for the same RL equity agent.

---

## Q4. Alternatives that reach "the robot decides buy and sell" more reliably

### Takeaway
The more reliable route to an autonomous buy/sell robot is a **supervised or ranking model for entry, combined with a rule-based or learned exit and sizing layer**: learning-to-rank on technical features, meta-labelling with triple-barrier exits, or Sharpe-loss end-to-end networks. RL, if kept, should sit on top. Genetic-programming rule search has mostly failed to replicate after costs. Contextual bandits have no credible equity evidence in the sources found.

### Cited Findings
- **Learning to rank.** Poh, Lim, Zohren & Roberts (2020/21) replaced sort-by-regression with learning-to-rank algorithms (pairwise and listwise) for cross-sectional momentum, giving "approximately threefold" higher Sharpe ratios than traditional approaches **[authors' backtest]** — [arXiv 2012.07149](https://arxiv.org/abs/2012.07149v1). Quantpedia lists a 1980–2019 monthly equity backtest with indicative 35.9% return and 16.6% volatility **[2nd]** — [Quantpedia](https://vvv.quantpedia.com/?p=11891). A transformer re-ranking follow-up on 31 currencies raised Sharpe about 20% over plain LTR and about 2× over traditional methods — [export.arxiv 2105.10019](https://export.arxiv.org/pdf/2105.10019). Whether LambdaMART specifically was among the rankers was not confirmed from the abstract.
- **Sharpe-loss supervised (direct) networks.** DMN learns trend and position size jointly with a Sharpe loss: more than 2× TSMOM Sharpe gross, but the edge survives only to 2–3 bp costs without turnover regularization — [arXiv 1904.04912](https://arxiv.org/abs/1904.04912). Moody-Saffell's direct reinforcement beat Q-learning — [NIPS 1998](https://papers.nips.cc/paper/1998/hash/4e6cd95227cb0c280e99a195be5f6615-Abstract.html)
- **Supervised CNN on chart images.** Jiang-Kelly-Xiu predicted the up-probability of the 5/20/60-day return from images, with strong US OOS results 2001–2019 and international transfer. Most of the effect sits in non-large-caps — [SSRN](https://papers.ssrn.com/abstract=3756587); [BSIC](https://bsic.it/technical-analysis-on-steroids/)
- **Meta-labelling and triple barrier.**
  - Design rationale (López de Prado 2018): fixed-threshold labels ignore changing volatility and ignore positions that stop-loss or take-profit orders would have closed. Triple-barrier labels (profit-take, stop-loss, time limit) fix that. A secondary model (meta-label) predicts whether the primary rule's signal will hit the profit barrier, and its probability sizes the bet — [Hudson & Thames](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy/); [Wikipedia](https://en.wikipedia.org/wiki/Meta-Labeling)
  - Joubert et al. (JFDS), "Meta-Labeling: Calibration and Position Sizing": calibrating meta-model probabilities significantly improves fixed position-sizing methods; methods that fit sizing functions from training data gain no significant advantage — [LinkedIn publication listing](https://ae.linkedin.com/in/jacquesjoubert) **[2nd]**
  - Counter-evidence: on 60.5M EURUSD ticks (2022–23), triple-barrier plus meta-models produced no reliable edge for the tested rules **[weak]** — [mql5](https://www.mql5.com/en/articles/23310); "Why meta labeling is not a silver bullet" — [QuantConnect forum](https://www.quantconnect.com/forum/discussion/14706/why-meta-labeling-is-not-a-silver-bullet/) **[weak]**
- **Genetic algorithms and genetic programming of rule trees.**
  - Allen & Karjalainen (1999): no excess return after costs OOS on the S&P 500 — [JFE PDF](https://www.cs.montana.edu/courses/spring2007/536/materials/Lopez/genetic.pdf)
  - A later GP variant (monthly data, complexity penalty in fitness, co-evolved separate buy and sell rules) reportedly beat buy-and-hold over 12 years after costs **[weak, thesis/tech report, unreplicated]** — [WPI tech report](https://ftp.cs.wpi.edu/pub/techreports/pdf/03-16.pdf)
  - A review notes that the few GP successes "have tended to be difficult to replicate" — [WPI](https://digital.wpi.edu/downloads/2r36tx594?locale=en)
- **Imitation learning of a rule**, via behavior cloning or demonstration buffers, improved RL agents in several preprints (see Q2). Experts must be causal (implementable) rules, not hindsight-optimal labels — [arXiv 2411.08637](https://arxiv.org/html/2411.08637v1); [arXiv 2312.15730](https://arxiv.org/pdf/2312.15730)
- **Ensembles.** Validation-selected ensembles of PPO, A2C and DDPG beat each single agent in the FinRL reproductions, with only a modest margin over the index (Sharpe 1.53 vs 1.32) — [FinRL-Meta](https://arxiv.org/pdf/2211.03107)

### Inferences
- A design more likely to beat the current gate-passing rule, in order of build priority:
  - **(a) Entry and selection.** A cross-sectional score from the strong technical factors (52-week-high nearness, multi-horizon MA trend ratios, short-term reversal / RSI / MACD percentiles), learned with a ranking objective (LambdaMART/LTR) or a Sharpe-loss network and re-estimated walk-forward. Hold the top-K, rebalancing weekly or monthly, with a hold-band so turnover stays well below the cost break-even.
  - **(b) Exit.** Either rule-based (rank falls below the band, time limit, or volatility-scaled stop), or a meta-label classifier trained on triple-barrier outcomes of the entry rule, whose calibrated probability sets size.
  - **(c) Optional RL overlay.** PPO initialized by behavior cloning of (a)+(b), with the score in the state, a holding-period differential-Sharpe reward and a KL constraint to the cloned policy. Judged only on whether it beats (a)+(b) net of costs on the same 300k + 10k/month account vs 0050.
- This is "robot decides buy and sell" in a form where each component has an independent, testable OOS record. It also keeps the trial count explicit for the project's multiple-testing gate.
- GP/GA rule search is the alternative most likely to reproduce the snooping problem the project's gate is designed to stop. If used, the complexity penalty and every evolved rule must count as trials.

### Gaps
- **Contextual bandits** for stock entry/exit: no credible peer-reviewed equity evidence was found in this search.
- No study was found that directly compares RL vs meta-labelling vs LTR on the same equity universe with the same costs.
- No Taiwan-specific evidence was found for LTR, meta-labelling or CNN chart models. Whether the Jiang-Kelly-Xiu international-transfer result includes Taiwan was not confirmed.
