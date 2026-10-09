# Automated alpha discovery (GP / RL / LLM factor mining) and LLM trading agents: out-of-sample evidence 2023–2026

Scope note: research done 2026-10-10. Almost all numbers below are authors' self-reported results on their own splits; "OOS" means the paper's held-out test period, not live trading, unless stated. Where an LLM is involved, the test period is compared with the model's training period when the source states it. Platform context used for inferences: Taiwan stocks, long-only 20-stock rules, daily data since 2013, ~40 factors, exhaustive 1–4 factor percentile-sum scan (~370k), dev screen 2015-06..2020-09, unseen validation 2020-10+, DSR/PBO already computed (`docs/research_method.md` §7 counts "distinct rules" as DSR trials: 679 cumulative, 238 in the pool), LLM news-event scorer forward-only from 2026-10, weekly LLM "researcher", LLM budget $5/month.

## Q1. Formulaic alpha mining (WorldQuant 101, GP, AlphaGen, AlphaForge, QuantFactor REINFORCE, Alpha-GPT, AlphaAgent, RD-Agent(Q), FAMA, LLM-MCTS): reported OOS results, test periods, overfitting/decay

### Takeaway
Every method reports beating its baselines on a held-out test window, but the evidence is weak in three ways. (1) Test ICs are small (about 0.02–0.05 daily cross-sectional IC). (2) Each paper's own method wins, and competitors re-run by rival authors look much worse (RD-Agent made 14.2%/yr in its own paper and 0.78%/yr in AlphaAgent's re-run). (3) The windows are short, mostly on Chinese A-shares (CSI300/500) with Qlib and low assumed costs. There is no independent multi-market, cost-realistic replication of any of these systems. One cross-paper pattern does hold: GP-mined and classic technical libraries (Alpha158) decay toward zero IC within a few years.

### Cited Findings
**WorldQuant 101 (Kakushadze 2016)**
- The 101 alphas are published as formulas and code. Their average holding period is about 0.6–6.4 days and their average pairwise correlation is low (15.9%). Alpha returns are strongly correlated with volatility but not with turnover. — [hedgefundalpha summary of Kakushadze, Lauprete & Tulchinsky](https://hedgefundalpha.com/strategies/101-formulaic-alphas/); [alphaXiv 1601.00991](https://alphaxiv.org/overview/1601.00991v3)
- A search found no published backtest of the 101 alphas on Taiwan stocks. — (negative search result; see Gaps)

**GP baselines and decay**
- AlphaForge re-ran the baselines on CSI300/CSI500: train 2010–2016, validate 2017, test 2018, then retrain annually so the test covers 2018–2022. The label is the 20-day VWAP return. Test IC / RankIC (%):

  | Method | CSI300 IC | CSI300 RankIC | CSI500 IC | CSI500 RankIC |
  |---|---|---|---|---|
  | GP | 1.29 | 2.72 | 0.37 | 2.34 |
  | DSO | 2.55 | 3.88 | 1.38 | 4.56 |
  | RL (AlphaGen, Yu et al. 2023) | 2.09 | 2.72 | 1.91 | 4.03 |
  | LightGBM | 0.84 | 1.85 | 1.75 | 3.81 |
  | AlphaForge | 4.40 | 5.89 | 2.84 | 5.57 |

  — [AlphaForge, arXiv 2406.18394 (AAAI 2025)](https://arxiv.org/html/2406.18394)
- On CSI500 over the 2021–2024 test, AlphaAgent's decay chart shows Alpha158, GP and RSI factor ICs falling from about 0.022–0.036 to near zero, and RankICs from about 0.020–0.042 to near zero. — [AlphaAgent, arXiv 2502.16789](https://arxiv.org/html/2502.16789v1)
- gplearn is built for symbolic regression with a scikit-learn API. A practitioner blog notes that native gplearn has no cross-sectional (per-stock) dimension and cannot search window lengths as constants. Forks such as gpquant and gplearn_cross_factor add Sharpe, RankIC, IR, quantile and monotonicity fitness functions. — [aggregated from pypi gpquant](https://pypi.org/project/gpquant/0.1.4); [gp-cross-factor](https://pypi.org/project/gp-cross-factor); [Tencent Cloud blog](https://cloud.tencent.com/developer/article/2099791)
- A 2024 warm-start GP paper claims superior OOS prediction and portfolio returns on 2020–2024 Chinese data (self-reported). — [arXiv 2412.00896](https://ar5iv.labs.arxiv.org/html/2412.00896)

**AlphaGen (Yu et al., KDD 2023)**
- AlphaGen generates a *set* of formulaic alphas with RL (PPO). The reward is the performance of the downstream linear combination model, so each new alpha is rewarded for synergy with the existing pool rather than for standalone IC. Accepted at KDD '23 ADS track. — [arXiv 2306.12964](https://arxiv.org/abs/2306.12964)
- In AlphaForge's re-run (table above), AlphaGen/RL reached test RankIC 2.72% on CSI300 and 4.03% on CSI500 for 2018–2022. — [AlphaForge](https://arxiv.org/html/2406.18394)

**QuantFactor REINFORCE (IEEE Trans. Signal Processing 2025)**
- Replaces PPO with REINFORCE plus a variance-reducing baseline, and adds an information-ratio reward-shaping term that favours factors stable across volatility regimes. Reports a 3.83% boost in correlation with returns over recent alpha-mining methods. Datasets are not named in the abstract. — [arXiv 2409.05144](https://arxiv.org/abs/2409.05144)

**AlphaForge (AAAI 2025)**
- Two stages: a generative-predictive network mines a zoo of 100 factors, then a *dynamic* combiner selects and reweights them over time based on recent performance. Pool-size results were non-monotonic and peaked at about 10 factors, which the authors read as only about 10 factors being relevant at any time. — [AlphaForge](https://arxiv.org/html/2406.18394)
- Live claim: 3M RMB invested in CSI500 for about 9 months (dates not given), about 21.68% excess return over CSI500 (self-reported, unaudited). — [AlphaForge](https://arxiv.org/html/2406.18394)

**Alpha-GPT / Alpha-GPT 2.0**
- Alpha-GPT 2.0 (Feb 2024) is a human-in-the-loop framework covering alpha mining, modeling and analysis. The abstract is marked "Draft. Work in progress" and reports no experiments or metrics. — [arXiv 2402.09746](https://arxiv.org/abs/2402.09746)

**FAMA (Findings of ACL 2024)**
- Cross-Sample Selection feeds diverse factors as in-context examples to counter the homogeneity of LLM-generated factors. Chain-of-Experience reuses successful past mining trajectories. On S&P 500 it reports RankIC +0.006 and RankICIR +0.105 over SOTA, a 38.4% annualized return, and a Sharpe of "667.2%" (implausible as stated; unverified). — [search summary of ACL Anthology 2024.findings-acl.233](https://preview.aclanthology.org/setup/2024.findings-acl.233)
- An August 2026 survey warns that memory/evolution mechanisms such as Chain-of-Experience can overfit to historical validation regimes unless tested under strict OOS and cross-market protocols. — [Agentic Quantitative Trading survey, arXiv 2608.31041](https://arxiv.org/abs/2608.31041) (as quoted in [FaVOR](https://arxiv.org/pdf/2608.30192) search summary)

**AlphaAgent (KDD 2025; GPT-3.5-turbo)**
- Splits: train 2015-01–2019-12, validate 2020, test 2021-01–2024-12 (968 trading days on CSI500, 1,004 on S&P500). Inputs are OHLCV only. LightGBM uses 4 base alphas plus the new alphas. The Qlib portfolio holds the top 50 and drops 5. Costs are 0.05% buy / 0.15% sell on CSI500 and 0.05% sell only on S&P500. — [arXiv 2502.16789](https://arxiv.org/html/2502.16789v1)
- Test results:

  | Market | IC | ICIR | Annual excess return | IR | MDD |
  |---|---|---|---|---|---|
  | CSI500 | 0.0212 | 0.19 | 11.00% | 1.49 | −9.36% |
  | S&P500 | 0.0056 | 0.055 | 8.74% | 1.05 | −9.10% |

  Baselines in the same table:

  | Baseline | CSI500 AR | CSI500 IR | S&P500 AR | S&P500 IR |
  |---|---|---|---|---|
  | AlphaForge | 3.45% | 0.33 | 2.10% | 0.26 |
  | RD-Agent (GPT-4-turbo) | 0.78% | 0.07 | 1.69% | 0.17 |
  | OpenAI-o1 | 0.46% | 0.06 | — | — |
  | DeepSeek-R1 best-of-10 | — | — | 2.75% | 0.24 |
  | LSTM | 4.96% | 0.62 | — | — |

  On S&P500, LSTM and Transformer were negative from 2023-02. — [arXiv 2502.16789](https://arxiv.org/html/2502.16789v1)
- Internal inconsistencies:
  - The abstract says "past 5 years" but the test window is 4 years.
  - The introduction claims 30% fewer tokens, but Section 4.5 says a 23% efficiency gain.
  - Ablation: hit ratio of effective factors is 0.29 vs 0.16 without the regularisers. — [arXiv 2502.16789](https://arxiv.org/html/2502.16789v1)
- Code: github RndmVariableQ/AlphaAgent. — [alphaXiv listing](https://alphaxiv.org/resources/2502.16789v2)

**RD-Agent(Q) (Microsoft; NeurIPS 2025)**
- Main CSI300 experiment: train 2008–2014, validate 2015–2016, test 2017-01 to 2020-08-01. Extension: CSI500 and NASDAQ100 with train 2008–2021, validate 2022–2023, test 2024 to 2025-06. — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- CSI300 test (Table 1):

  | Model | IC | ARR | IR | MDD |
  |---|---|---|---|---|
  | RD-Agent(Q) o3-mini | 0.0532 | 14.21% | 1.74 | −7.42% |
  | RD-Agent(Q) GPT-4o | 0.0497 | 11.44% | 1.32 | — |
  | Alpha158 | 0.0341 | 5.70% | 0.85 | — |
  | Alpha360 | 0.0420 | 4.38% | 0.67 | — |
  | MASTER | 0.0215 | 8.96% | 1.34 | — |
  | LightGBM | 0.0277 | 3.97% | 0.57 | — |

  — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- Cost and runtime: under $10 per run; 6 h per module, 12 h for the joint framework. The bandit run had 44 loops, of which 24 were valid and 8 were selected as SOTA. The abstract claims "70% fewer factors", while the body says "22% of factors"; the two are not reconciled. — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- The paper says the LLM backends' cutoffs are "completely or nearly prior to the test period" but gives no dates. — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)

**LLM + MCTS ("Navigating the Alpha Jungle", AAAI 2026; GPT-4.1)**
- Data: CSI300 and CSI1000, 10- and 30-day targets. Train 2011–2020, test 2021-01 to 2024-11 (no separate validation described). — [arXiv 2505.11122](https://arxiv.org/html/2505.11122v1)
- Ablation (LightGBM):

  | Method | IC | RankIC | AR | IR |
  |---|---|---|---|---|
  | MCTS + frequent-subtree avoidance | 0.0549 | 0.0512 | 11.07% | 1.18 |
  | Best baseline (ToT) | 0.0459 | — | 8.68% | 0.93 |

  — [arXiv 2505.11122](https://arxiv.org/html/2505.11122v1)
- Search budgets: LLM methods used 1,000–3,000 generations; GP/RL methods up to 600,000 evaluations or 24 h. — [arXiv 2505.11122](https://arxiv.org/html/2505.11122v1)
- Leakage probe: GPT-4.1 asked directly for "high-performing alphas" scored RankIC 0.0242, close to the 0.0179 random baseline. The full framework scored 0.0714. — [arXiv 2505.11122](https://arxiv.org/html/2505.11122v1)

**FaVOR (Aug 2026)**
- Decomposes an economic hypothesis into observable conditions, validates that each factor reflects its condition, then integrates them into an interpretable composite. Claims to beat baselines on CSI500 and S&P500 in 2025; no numbers in the abstract. Code at github.com/damilab/FaVOR. — [arXiv 2608.30192](https://arxiv.org/abs/2608.30192)

**Benchmarks**
- AlphaBench (ICLR 2026) is the first systematic LLM benchmark for formulaic alpha mining, covering factor generation, evaluation and search. It concludes there is promise for automation but robustness, search efficiency and practical usability remain unresolved. — [ICLR 2026 poster](https://iclr.cc/virtual/2026/poster/10008434)
- AlphaEval (2025) argues that the closed-source nature of most alpha-mining models hinders reproducibility. It proposes a backtest-free evaluation on five dimensions: predictive power, stability, robustness to market perturbations, financial logic and diversity. — [arXiv 2508.13174](https://arxiv.org/html/2508.13174v1)

### Inferences
- **Home-field advantage.** Who runs the comparison matters as much as which method is used. RD-Agent is 14.2%/yr on CSI300 2017–2020 in its own paper and 0.78%/yr on CSI500 2021–2024 in AlphaAgent's paper. AlphaForge is 3.45% in AlphaAgent's table. AlphaGen's RankIC is about 2.7% in AlphaForge's table. Treat every self-reported improvement as an upper bound.
- **LLM training cutoff vs test window.** RD-Agent(Q)'s main CSI300 test (2017–2020) lies *inside* the training data of GPT-4o / o3-mini. Only its CSI500/NASDAQ100 2024–2025.6 extension is plausibly post-cutoff. AlphaAgent's 2021–2024 test overlaps GPT-3.5-turbo's commonly cited training cutoff of Sept 2021 (from general knowledge, not verified in this session); most of its test is post-cutoff. Alpha Jungle's test (2021–2024.11) is largely before GPT-4.1's training cutoff. Its "ask the LLM for good alphas" probe suggests little direct memorisation of formula performance, but the probe does not rule out the LLM knowing which factor *families* worked in that era.
- **Costs and turnover.** These results assume daily rebalancing of a top-50 Qlib portfolio with 0.05–0.15% one-way costs. Taiwan round-trip costs are roughly 0.585% (0.1425% commission each side plus 0.3% sell tax; general knowledge, before discounts). For a long-only 20-stock rule, the short-horizon alphas (101-style, 0.6–6.4 day holding) would lose most of their edge to costs. The transferable part is the *process* and the *regularisers*, not the formulas.
- **Signal strength.** An IC of about 0.02–0.05 is the realistic order of magnitude for mined price/volume alphas. AlphaAgent's S&P IC of 0.0056 alongside a claimed IR of 1.05 suggests the portfolio result depends heavily on construction choices.

### Gaps
- No independent replication of AlphaGen, AlphaAgent, RD-Agent(Q) or FAMA by a third party on new markets or new periods was found.
- No study of these systems on Taiwan stocks was found.
- AlphaGen's own reported numbers (CSI300/500 IC tables) could not be extracted (PDF not parsable in this session). Only AlphaForge's re-run numbers are given above.
- "AlphaEvolve" (Cui et al., SIGMOD 2021) and "AutoAlpha" (2020) were not verified in this session. No numbers are reported here.
- The original 101-alphas test market and the alphas' out-of-sample performance in other markets were not verified.
- The FAMA ablations and the exact test period could not be accessed.

## Q2. Safeguards in alpha mining and the multiple-testing problem ("factor zoo")

### Takeaway
Current mining systems use four kinds of guardrails:
- novelty/originality constraints (AST-subtree similarity, correlation or IC dedup, frequent-subtree avoidance);
- complexity penalties (symbol length, free parameters, number of raw features);
- economic-hypothesis alignment checks, often LLM-judged;
- dynamic or limited combination (keep about 10 live factors).

None of the mining papers applies a formal multiple-testing correction for the thousands to hundreds of thousands of candidates evaluated. The finance literature says the needed hurdle is much higher than t=2: about 3.0 (Harvey-Liu-Zhu), and 3.4 cross-sectional / 3.8 time-series when about 2 million random strategies define the null (Chordia-Goyal-Saretto). Even genuine published anomalies lose about 26% of their return out of sample and about 58% after publication (US).

### Cited Findings
**Guardrails used in mining systems**
- AlphaAgent's three regularisers:
  - originality = largest common AST subtree vs an alpha zoo such as Alpha101;
  - hypothesis alignment = LLM scores whether the description fits the hypothesis and whether the expression fits the description;
  - complexity = symbolic length + free-parameter count + log(1+#features).

  No numeric thresholds are given. — [arXiv 2502.16789](https://arxiv.org/html/2502.16789v1)
- RD-Agent(Q):
  - new factors with max IC correlation ≥ 0.99 against the SOTA library are dropped;
  - the LLM sees only schema-level information, never raw market data or explicit temporal splits;
  - walk-forward validation;
  - baselines get 5 seeds and the median is reported.

  It does not describe a multiple-testing correction. — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- Alpha Jungle:
  - frequent-subtree avoidance: mine the top-3 frequent "root genes" among effective alphas and tell the LLM to avoid them;
  - an LLM "Overfitting Risk" score at temperature 0.1.

  The in-sample vs OOS RankIC gap shrinks as search depth grows. — [arXiv 2505.11122](https://arxiv.org/html/2505.11122v1)
- AlphaForge: annual retraining and a combiner that keeps about 10 factors at a time, with time-varying weights. — [arXiv 2406.18394](https://arxiv.org/html/2406.18394)
- QuantFactor REINFORCE: IR reward shaping penalises unstable factors. — [arXiv 2409.05144](https://arxiv.org/abs/2409.05144)
- FaVOR: validates each factor against an explicit, decomposed economic hypothesis instead of optimising returns only. — [arXiv 2608.30192](https://arxiv.org/abs/2608.30192)
- Monitoring IC: one study finds the time-varying component drives about 95% of IC variability. Short-window realised IC is therefore a poor monitor of decay. — [arXiv 2010.08601](https://arxiv.org/pdf/2010.08601)

**Multiple-testing hurdles from the finance literature**
- Harvey, Liu & Zhu (RFS 2016): a newly discovered factor needs a t-ratio above 3.0. A secondary source counts 316 factors; the count varies between versions. A. Chen (2021) notes the implied hurdle is 2.81 at a 5% FDR and 3.39 for a tighter bound. — [NBER w20592](https://www.nber.org/papers/w20592); [A. Chen slides](https://business.unl.edu/academic-programs/departments/finance/about/seminar-series/documents/AndrewChen2.pdf)
- Chordia, Goyal & Saretto (RFS 2020): use more than 2 million randomly generated strategies (2.1–2.4M depending on version) as the null. The t-hurdles are 3.8 (time-series) and 3.4 (cross-sectional). Ignoring multiple testing gives about 45% false rejections. — [IDEAS/RFS 33(5)](https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2134-2179..html)
- Counterpoint: A. Chen (2022) argues most claimed cross-sectional findings are likely true, because most published t-stats exceed 3. — [arXiv 2206.15365](https://arxiv.org/pdf/2206.15365)

**Decay and replication**
- McLean & Pontiff (JF 2016), 97 predictors: returns are 26% lower out of sample, which is an upper bound on data mining, and 58% lower after publication. Predictors with stronger in-sample returns decline more. — [JF 2016 PDF](https://Www.Gwern.net/doc/economics/2016-mclean.pdf)
- Jacobs & Müller (231 anomalies, 39 markets): the US is the only country with a reliable post-publication decline. This is from a secondary summary only. — [Quantpedia](https://quantpedia.com/?p=26147)
- Jensen, Kelly & Pedersen (JF 2023): 153 factors in 93 countries.
  - Replication rate is about 85% (CAPM alpha basis), 82.4% globally, and about 55–56% for raw US returns depending on version.
  - Bayesian FDR is 0.1%.
  - Contrast: Hou-Xue-Zhang reported a 35% replication rate. — [NBER w28432](https://www.nber.org/papers/w28432); [Alpha Architect summary](https://alphaarchitect.com/is-there-a-replication-crisis-in-finance/)

**Statistical tools**
- Deflated Sharpe Ratio (Bailey & López de Prado, JPM 2014): corrects for selection under multiple testing and for non-normal returns. Inputs are the number of trials and the *variance of trial Sharpe ratios*. A known critique is that the trial count is only as good as the researcher's record. — [SSRN 2460551](https://papers.ssrn.com/abstract=2460551); [CXO critique](https://cxoadvisory.com/big-ideas/sharper-sharpe-ratio)
- Probability of Backtest Overfitting (Bailey, Borwein, López de Prado & Zhu): uses combinatorially symmetric cross-validation. PBO is the fraction of splits in which the in-sample winner falls into the bottom half out of sample. — [pbo vignette](https://cran.hafro.is/web/packages/pbo/vignettes/pbo.html); [paper](https://carmamaths.org/jon/backtest.pdf)

### Inferences
- **Under-counted trials.** The platform's DSR counts "distinct rules" in the pool (238; 679 cumulative). The dev-period screen, however, evaluated 39,676 + 329,004 candidates. A DSR or PBO computed on the post-screen pool is optimistic, because selection already happened in the screen.
- **Fix the trial count.** Either compute DSR with N = all screened candidates, or with an *effective* N. The effective N can be estimated by clustering candidate return series, for example the number of clusters at correlation < 0.9. The variance of trial Sharpes should come from the full screen, not the finalists.
- **Use a higher hurdle.** Require a dev-period t-stat in the 3.4–3.8 range (Chordia et al. for the cross-section) before a combo reaches validation.
- **Protect the validation period.** The 2020-10+ holdout is the platform's strongest protection, but only if it is used once. Validating 80 finalists and then ranking them by validation performance turns validation into a second selection stage. Count those 80 as trials for any validation-period DSR.
- **Borrow the cheap guardrails.** The following transfer directly to a percentile-sum scan:
  - correlation dedup against the existing pool (RD-Agent uses IC-corr ≥ 0.99; a stricter 0.7–0.8 on the return or holding overlap of rules fits the "don't make near-duplicate strategies" directive in AGENTS.md);
  - a complexity penalty (fewer factors preferred at equal performance);
  - a requirement for an a-priori economic hypothesis per factor family.
- **Expect decay.** Taiwan anomaly decay data were not found, but McLean-Pontiff suggests haircutting dev-period excess by at least 26%. Jacobs-Müller suggests that outside the US post-publication decay is less reliable, so Taiwan anomalies may persist longer but are noisier.

### Gaps
- No paper in the LLM/RL mining literature was found that reports a DSR/PBO or FDR-adjusted significance for its mined factors.
- No Taiwan-specific factor-zoo replication rate was found in this search. JKP's 93-country data likely includes Taiwan, but per-country results were not retrieved.

## Q3. LLM trading agents (TradingAgents, FinMem, FinAgent, FinCon, StockBench, FINSABER, Alpha Arena, Agent Market Arena, LiveTradeBench): latest evidence, look-ahead contamination, cost

### Takeaway
The 2025–2026 evidence is consistently negative for LLMs as direct traders. Strong headline backtests (for example TradingAgents' AAPL Sharpe 8.21 over 5 months) come from short windows, few tickers and pre-cutoff periods. Under longer, broader or post-cutoff tests:
- FINSABER: 2004–2024, survivorship-free S&P500 subsets; buy-and-hold significantly beats FinMem and FinAgent, and LLM alpha p > 0.34.
- Profit Mirage: almost every published agent fails to beat a random baseline after its knowledge cutoff.
- StockBench: post-cutoff Mar–Jun 2025, 20 DJIA stocks; returns of −2.8% to +2.5% vs buy-and-hold 0.4%, no costs modelled.
- Live contests (Alpha Arena S1/S1.5): most models lose, and the winners differ between seasons.

Agent framework design matters more than the LLM backbone, and LLM leaderboard rank does not predict trading results.

### Cited Findings
**FINSABER (2025)**
- Re-runs show FinMem's reported Sharpe collapsing, for 2022-10-06 to 2023-04-10:
  - TSLA: 2.679 reported vs 0.927 re-run (GPT-4o-mini);
  - MSFT: 1.440 reported vs −0.554 re-run.
- 2004–2024 composite on S&P500 constituents including delisted names (Sharpe / annual return):

  | Selection | Buy and hold | FinMem | FinAgent |
  |---|---|---|---|
  | Random-five | 0.315 / 6.7% | −0.253 / −0.1% | 0.094 / 4.5% |

  Paired t-tests: buy and hold > FinMem, p = 3.0e-6. All LLM CAPM-alpha p-values exceed 0.34.
- Regimes: LLM agents are too conservative in bull markets (FinAgent Sharpe 0.12 vs buy-and-hold 0.61) and too aggressive in bear markets (FinMem −0.97 vs −0.28).
- Cost: one composite backtest cost $198.24 for FinAgent and $31.79 for FinMem (GPT-4o-mini). The full composite experiments cost about $700, excluding embeddings.

— [arXiv 2505.07078](https://arxiv.org/html/2505.07078)

**Profit Mirage (Oct 2025)**
- Rolling the test calendar past each model's knowledge cutoff, "almost every published LLM-based agent fails to beat a random baseline".
- Under counterfactual input changes, the worst model left 82.13% of predictions unchanged.
- On FinLake-Bench, GPT-4o and peers answer "did the market rise on date X" correctly more than 85% of the time.
- Fine-tuning on financial data raises in-distribution accuracy but generalisation drops sharply.
- The proposed FactFin reports OOS Sharpe 1.4× the best baseline (self-reported).

— [arXiv 2510.07920](https://arxiv.org/html/2510.07920v1)

**StockBench (Oct 2025)**
- Test March 3 – June 30, 2025 (82 trading days, after mainstream cutoffs). Top-20 DJIA stocks, $100k each, daily decisions, 3 seeds, no trading costs.
- Returns:

  | Model | Return |
  |---|---|
  | Qwen3-235B-Think | 2.5% (best) |
  | Kimi-K2 | 1.9% |
  | Claude-4-Sonnet | 2.2% |
  | GPT-5 | 0.3% |
  | Equal-weight buy-and-hold | 0.4% (MDD −15.2%) |
  | GPT-OSS-20B | −2.8% (worst) |

- The abstract says most models fail to beat buy-and-hold, while Section 3.2 says most outperform; the paper contradicts itself.
- Every agent underperformed buy-and-hold in the Jan–Apr 2025 downturn.
- GPT-5 without search accurately described AAPL's 2021 trend, which is the stated reason for using only post-cutoff windows.

— [arXiv 2510.02209](https://arxiv.org/html/2510.02209)

**TradingAgents**
- Reported AAPL cumulative return of 26.62% with Sharpe 8.21 for 2024-06-19 to 2024-11-19. A blog recomputation says buy-and-hold *gained* 9.12% in that window, not the −5.23% the paper reports (unverified single source). — [DEV Community](https://dev.to/trow126/the-most-starred-llm-trading-paper-claims-buy-and-hold-lost-523-it-actually-gained-912-1jj6); [arXiv 2412.20138](https://arxiv.org/pdf/2412.20138)
- One independent evaluation reports TradingAgents' Sharpe falling from 0.43 to 0.22 after costs, and underperforming buy-and-hold on 4 of 5 stocks. The source is an alphaXiv overview of a 2026 review; the exact provenance is unclear. — [alphaXiv overview 2605.16895](https://www.alphaxiv.org/overview/2605.16895)
- "The Alpha Illusion" (May 2026) argues that reported alpha from FinCon, FinMem and TradingAgents-style agents is not deployment evidence. It proposes minimum reporting protocols P1–P6: temporal integrity, real frictions, counterfactual robustness, calibration ("language confidence is not tradable probability"), numerical execution, and multi-agent disaggregation. It recommends LLMs as "auditable information interfaces" feeding separate calibration, risk and execution modules. — [arXiv 2605.16895](https://arxiv.org/abs/2605.16895)

**Live contests and benchmarks**
- Alpha Arena Season 1 (Nof1, ended about 2025-11-03/04; crypto perpetuals on Hyperliquid, $10k each, about 17 days):
  - Qwen3-Max +22% (about $12,231); DeepSeek about $10,489;
  - GPT-5 down more than 62%;
  - Gemini 2.5 Pro, Claude Sonnet 4.5 and Grok 4 also lost; 4 of 6 lost overall.
  - Nof1's founder said LLMs handle numerical time series poorly.

  — [Forklog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/); [GNcrypto](https://www.gncrypto.news/ua/news/qwen-wins-alpha-arena-season-1-with-22-percent-returns/)
- Alpha Arena Season 1.5 (US stocks: TSLA, NVDA, MSFT, AMZN, NDX; about 2 weeks, ended about 2025-12-03/05; four formats including 20x leverage):
  - only the "mystery model" Grok 4.20 was profitable, +12.11% ($4,844 across four $10k competitions);
  - GPT-5.1 and Gemini 3 lost;
  - one report says the Season 1 winner Qwen3-Max lost up to 70%.

  — [Forklog](https://forklog.com/en/ai-model-grok-4-2-triumphs-in-trading-tournament/); [aihola](https://aihola.com/article/grok-wins-alpha-arena-trading)
- A Bloomberg Law piece (May 2026) is headlined that AI trading bots "are mostly losing" (paywalled). — [Bloomberg Law](https://news.bgov.com/banking-law/ai-bots-auditioning-for-wall-street-trading-are-mostly-losing)
- Agent Market Arena (Oct 2025) is a live crypto and stock benchmark with 4 agent designs × 5 LLMs (GPT-4o, GPT-4.1, Claude-3.5-haiku, Claude-sonnet-4, Gemini-2.0-flash). Agent frameworks show markedly distinct risk behaviour, while backbones "contribute less to outcome variation". — [arXiv 2510.11695](https://arxiv.org/abs/2510.11695)
- LiveTradeBench (Nov 2025; live US stocks and Polymarket): high LMArena scores do not imply superior trading outcomes. — [arXiv 2511.03628](https://www.arxiv.org/abs/2511.03628)
- Aug 2026 survey: "strong model or forecasting capability does not reliably translate into trading performance under live market conditions". — [arXiv 2608.31041](https://arxiv.org/abs/2608.31041)

**Look-ahead mitigation work**
- Look-Ahead-Bench (Jan 2026): standard LLMs (Llama 3.1, DeepSeek 3.2) show significant look-ahead bias measured as alpha decay across regimes. "Point-in-time" models show less. — [arXiv 2601.13770](https://arxiv.org/abs/2601.13770)
- BlindTrade (2026) anonymises tickers and company details. — [arXiv 2603.17692](https://arxiv.org/html/2603.17692)
- A 2026 paper on mitigating look-ahead bias with LLMs notes that input-side safeguards do not address bias stored in the model's weights. — [arXiv 2605.24564](https://arxiv.org/pdf/2605.24564)

### Inferences
- The platform's existing decisions (AI as researcher, not trader; real orders permanently off) are supported by every independent evaluation found.
- Live contests of 2–3 weeks with n=1 path have no statistical power. The S1 winner was a top loser in S1.5. Treat them as anecdotes.
- **Cost.** At FINSABER's GPT-4o-mini prices, a 20-year multi-stock agent backtest cost $30–$200 per configuration. A $5/month budget cannot support an LLM-in-the-loop daily trading backtest over 2015–2026 for 20+ stocks. It can only support sparse, high-leverage calls such as weekly hypothesis proposals or per-event scoring.
- **Contamination.** For any LLM component, the backtest window 2015–2025 is pre-cutoff for current models. Only data after the model's training cutoff (in practice forward data from 2026-10) is clean for evaluating LLM *judgements*.

### Gaps
- No explicit per-decision token or dollar costs were found for TradingAgents or StockBench.
- Agent Market Arena's numeric returns were not retrieved.
- Official Nof1 final tables were not fetched; the figures come from secondary press with some date discrepancies.
- No LLM-agent trading evaluation on Taiwan stocks was found.

## Q4. LLMs for features rather than decisions (news/announcement events, earnings-call tone, embeddings in GBDT, Chinese/Taiwanese text)

### Takeaway
The more credible positive evidence is for LLMs as *feature extractors* evaluated on post-cutoff or cross-market data:
- LLM news embeddings predict returns across 16 markets and 13 languages, best where text has negation or complex narratives (Chen-Kelly-Xiu).
- GPT-4 headline scores predict drift on post-cutoff headlines, especially for small stocks and negative news, but the edge shrinks as LLM adoption rises (Lopez-Lira & Tang).
- In China, LLM news tone predicts returns, is absorbed within about 2 days, and is strongest for retail-heavy, high-friction firms (Tan-Wu-Zhang).

For Taiwan, the only LLM-on-material-information study found is a 2024 NCCU master's thesis. It reports LLM signals work better on the short side than the long side. Signal half-lives are short, so for a long-only, low-turnover 20-stock rule the most plausible use is as a negative-news exclusion filter, not as a ranking factor.

### Cited Findings
**US and international news**
- Lopez-Lira & Tang (v6, Oct 2025):
  - GPT-4 scores on *post-knowledge-cutoff* headlines significantly predict subsequent drift, particularly for small stocks and negative news;
  - forecasting ability rises with model size;
  - "strategy returns decline as LLM adoption rises";
  - about 90% portfolio-day hit rate for the (non-tradable) initial reaction.

  — [arXiv 2304.07619](https://arxiv.org/abs/2304.07619)
- Chen, Kelly & Xiu, "Expected Returns and Large Language Models" (2023):
  - LLM embeddings (ChatGPT, LLaMA, RoBERTa, BERT and others) significantly beat past-return signals and simpler NLP;
  - gains are largest with negation or complex narratives;
  - evidence covers 16 equity markets and news in 13 languages;
  - prices respond slowly to news indicating limits to arbitrage;
  - real-time news alerts give higher Sharpe ratios.

  — [Oxford seminar abstract](https://nlpecofin.web.ox.ac.uk/node/3041271); [Kelly slides](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2024/09/Kelly-WhartonJL.pdf); [Sydney seminar](https://business.sydney.edu.au/events/research/2024/finance/dacheng-xiu)
- Glasserman & Lin (2023), GPT-3.5 headline sentiment:
  - anonymised headlines produced *higher* in-sample returns, which the authors attribute to a negative "distraction effect" from company knowledge, strongest for large firms;
  - out of sample the difference loses significance, implying most of the in-sample effect reflected knowledge of the future.

  — [arXiv 2309.17322](https://arxiv.org/abs/2309.17322)
- Counterpoint for earnings-call sentiment: the information loss from anonymisation is "more pervasive and severe" than look-ahead bias. — [arXiv 2511.15364](https://arxiv.org/html/2511.15364v1)

**Embeddings and earnings calls**
- FinBERT embeddings of 10-K MD&A (255 S&P500 firms, 2011–2025) give a long-short Sharpe of 0.86 and FF5 alpha of 7.27%/yr (t = 2.30). The result survives OOS, placebo and sector-neutral tests. Single-author preprint, June 2026. — [arXiv 2606.29290](https://www.alphaxiv.org/abs/2606.29290)
- Earnings-call embeddings added to 446 JKP features (1,214 total) in a gradient-boosting regressor, with a 30% most-recent holdout, masked numbers/dates, and a test period after FinBERT's training data. The target appears to be analyst beliefs, not returns. — [arXiv 2511.15214](https://arxiv.org/pdf/2511.15214)
- ECC Analyzer: directly asking LLMs to predict volatility from earnings calls was "largely ineffective, akin to random guessing". LLMs are better as tools to understand filings than as direct predictors. — [arXiv 2404.18470](https://arxiv.org/pdf/2404.18470)
- FOMC communications: LLM embeddings beat benchmarks out of sample for short and medium-term market returns (ICLR 2025). — [ICLR 2025](https://iclr.cc/virtual/2025/33860)
- A paper combining quantitative factors with LLM newsflow representations exists (Oct 2025); details not retrieved. — [arXiv 2510.15691](https://arxiv.org/pdf/2510.15691)

**China**
- Tan, Wu & Zhang, "LLMs and Return Prediction in China" (Nov 2024):
  - seven models (BERT, RoBERTa, FinBERT, Baichuan, ChatGLM, InternLM, ensemble) on Chinese news;
  - long-short annualised returns of 35–67% depending on the model (gross, likely including the short leg, which is hard to implement in China);
  - signals take about 2 days to be incorporated;
  - strongest for firms with more information frictions, more retail holdings and more complex news.

  — [ABFER 2024 slides](https://abfer.org/media/abfer-events-2024/cmd/Large-Language-Models-and-Return-Prediction-in-China_Lin-Tan.pdf)
- Zhang et al. (2023) benchmark ChatGPT, Erlangshen-RoBERTa and Chinese FinBERT for sentiment factors on Chinese news summaries with backtests. — [arXiv 2306.14222](https://arxiv.org/html/2306.14222v2)

**Taiwan**
- NCCU 2024 master's thesis (柯昱均): ChatGPT on material information (重大訊息) and news for Taiwan 50 constituents, with event study, abnormal returns, and ML stacking/voting.
  - LLM signals were best for short and long-short strategies, especially in unstable or reversing markets.
  - Long strategies were weaker, which the author attributes to diverse positive-news sources and possible information leakage.
  - Ternary classification was less stable than binary.
  - The full text is embargoed until 2030-01-08; no period, return or cost numbers are available. The data source (MOPS or not) is not stated in the abstract.

  — [NCCU thesis](https://thesis.lib.nccu.edu.tw/thesis/detail/459546170333a82855689827ceefbbd3)
- Another NCCU thesis built a Taiwan SESTM news-sentiment score (Ke-Kelly-Xiu 2019 method, not an LLM). It significantly predicts individual-stock returns, with relatively more predictive power for returns *before* the news than in the US. — [NCCU thesis](https://thesis.lib.nccu.edu.tw/detail/b388207d80ba366428d5c82a8b659da9/) (from search summary)

### Inferences
- The Taiwan market is retail-heavy. Tan et al. find LLM signals strongest for retail-heavy firms, so Taiwan may be a relatively favourable venue for LLM text signals. This is untested here.
- Signal persistence is about 2 days in China, and US drift concentrates in small caps and negative news. For a long-only 20-stock rule with low turnover, a positive-news buy signal decays faster than the rule trades.
- The more defensible uses are:
  - exclude or delay buying names with fresh negative material news (Lopez-Lira's negative-news drift; the NCCU short-side result);
  - extract *structured event types* from 重大訊息, such as treasury-stock buybacks, capital increases, guidance changes, auditor/CFO changes and asset disposals, as categorical features for the existing factor scan, rather than a free-form sentiment score.
- Keeping the news scorer forward-only (from 2026-10) is correct. Glasserman-Lin and Profit Mirage show that pre-cutoff LLM scores of historical news are contaminated. Anonymisation is not a free fix, since it can destroy information (arXiv 2511.15364).
- **Cheaper historical alternative.** Embeddings from a small, fixed-cutoff open model (e.g. a Chinese BERT/FinBERT whose pretraining predates the backtest start), fed into a GBDT, can be backtested historically with far less contamination than a frontier chat LLM. This follows the Chen-Kelly-Xiu and FinBERT-embedding evidence. Cost is near zero beyond compute.

### Gaps
- No peer-reviewed study of LLM features on MOPS 重大訊息 with returns after costs was found. The NCCU thesis numbers are embargoed.
- No Taiwan-specific evidence on embedding features inside GBDT stock models was found.
- The Chen-Kelly-Xiu per-country results (whether Taiwan is among the 16 markets) were not retrieved.

## Q5. Practical architecture for a researcher-agent loop (hypothesis → code → backtest → critique) and how to prevent p-hacking

### Takeaway
The published LLM mining systems that report the best held-out results all share a closed loop: hypothesis (with economic rationale) → implementation → evaluation → feedback, with explicit novelty and complexity gates and an LLM that never sees test data. That describes RD-Agent(Q), AlphaAgent, Alpha Jungle and FaVOR.

None of them solves the multiple-testing problem. Their protection is a single fixed train/valid/test split, and the reviewed evidence says that is not enough when thousands of candidates are tried.

For this platform the brute-force scan already exhausts the 1–4 factor combination space. The LLM's marginal value is therefore not in searching combinations. It is in:
1. proposing *new raw factors and event features* with a written hypothesis;
2. critiquing results against a fixed checklist;
3. structured text extraction.

All of this must sit under a trial ledger, a deflated hurdle, and a sealed, use-once holdout. For LLM-originated ideas, that holdout must be post-cutoff forward data.

### Cited Findings
**Reference loops**
- RD-Agent(Q) loop:
  - Research: hypothesis generation and task design, with a multi-armed bandit choosing whether to work on factors or models.
  - Development: the Co-STEER code agent implements and backtests.
  - Feedback.

  44 loops, 24 valid, 8 promoted to SOTA; under $10 per run; the LLM sees schema only. — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- AlphaAgent: three agents (idea → factor → evaluation) iterated over rounds, with originality, alignment and complexity regularisers. The hit ratio of effective factors nearly doubled (0.16 → 0.29) with the constraints. — [arXiv 2502.16789](https://arxiv.org/html/2502.16789v1)
- Alpha Jungle: an LLM generates and refines formulas inside MCTS with backtest feedback, frequent-subtree avoidance, an LLM overfitting-risk score, and an explicit leakage probe. — [arXiv 2505.11122](https://arxiv.org/html/2505.11122v1)
- FaVOR: hypothesis decomposition → per-factor validation against the hypothesis → interpretable integration. — [arXiv 2608.30192](https://arxiv.org/abs/2608.30192)
- "The Alpha Illusion" recommends modular designs in which LLMs are auditable information interfaces and calibration, risk and execution are separate modules. It proposes reporting tiers P1–P6. — [arXiv 2605.16895](https://arxiv.org/abs/2605.16895)

**Statistical guardrails**
- DSR requires the number of trials *and* the variance of trial Sharpe ratios. Its reliability depends on recording every alternative tried. — [SSRN 2460551](https://papers.ssrn.com/abstract=2460551); [CXO](https://cxoadvisory.com/big-ideas/sharper-sharpe-ratio)
- PBO via CSCV estimates how often the in-sample best lands in the OOS bottom half. — [pbo vignette](https://cran.hafro.is/web/packages/pbo/vignettes/pbo.html)
- Hurdles of t ≥ 3.0 (HLZ) and t ≥ 3.4 cross-sectional (Chordia et al., with about 2M random strategies as the null). — [NBER w20592](https://www.nber.org/papers/w20592); [IDEAS](https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2134-2179..html)

**Evaluation hygiene**
- Use post-cutoff windows only for LLM judgements (StockBench rationale; Profit Mirage). — [arXiv 2510.02209](https://arxiv.org/html/2510.02209); [arXiv 2510.07920](https://arxiv.org/html/2510.07920v1)
- Counterfactual perturbation: if an LLM's output does not change when key inputs change, it is recalling rather than reasoning. — [arXiv 2510.07920](https://arxiv.org/html/2510.07920v1)
- Memory or experience reuse (as in FAMA's Chain-of-Experience) risks overfitting to the validation regime. — [arXiv 2608.31041](https://arxiv.org/abs/2608.31041) via [FaVOR summary](https://arxiv.org/pdf/2608.30192)

**Budget reference points**
- AlphaAgent used GPT-3.5-turbo. Alpha Jungle used 1,000–3,000 LLM generations per run (GPT-4.1). RD-Agent(Q) costs under $10 per run. — [AlphaAgent](https://arxiv.org/html/2502.16789v1); [Alpha Jungle](https://arxiv.org/html/2505.11122v1); [RD-Agent(Q)](https://arxiv.org/html/2505.15155v2)

### Inferences
**Concrete loop for this platform**

1. **Hypothesis card (LLM, weekly; about 1–3 calls).** The LLM sees only the factor catalogue (schema), factor-strength summaries from the *dev period*, and the list of existing rules with their correlation clusters. It never sees validation-period (2020-10+) results.
   - Output: a JSON card with the hypothesis, the economic mechanism, the factor(s) or new raw feature, the expected sign, the expected holding horizon, and a falsification condition.
   - Card types: (a) a *new factor/feature* to compute, which is where an LLM adds value beyond the exhaustive scan; (b) a factor-family-level claim. Never a parameter tweak (AGENTS.md already bans near-duplicates).
2. **Novelty gate (code, not LLM).** Reject the card if:
   - the proposed factor's dev-period cross-sectional rank correlation with any existing factor is ≥ 0.7–0.8 (the AlphaAgent/RD-Agent idea at a stricter threshold); or
   - the resulting rule's holdings overlap with any pool rule is above a set threshold; or
   - it is a parameter neighbour of an existing rule.
3. **Complexity gate.** Prefer at most 2–3 factors, no free thresholds, and a penalty per extra factor (AlphaAgent's symbol-length / free-parameter idea).
4. **Pre-registration and trial ledger.** Every card, and every scan candidate, gets an ID and counts toward N *before* it is run. DSR uses N = all candidates ever evaluated in that family, or an effective N from clustering, with trial-Sharpe variance from the full screen. This fixes the current under-count, where the pool has 238 rules but the screen covered about 370k.
5. **Dev evaluation.** Require a t-stat of about 3.4 or higher on monthly excess vs the 0050 cash-flow benchmark over 2015-06..2020-09, plus PBO via CSCV across sub-periods, before validation.
6. **Use-once validation (2020-10+).** Each candidate is evaluated exactly once (already a platform rule). Report DSR with N = number of candidates that reached validation.
7. **Contamination rule for LLM-originated ideas.** The 2020-10..2025 period is *in* the LLM's pretraining. The LLM likely "knows" that AI/semiconductor names led Taiwan in 2023–2024, so an LLM-proposed rule that wins on 2020-10+ may be hindsight. Tag such rules as "LLM-origin". Require forward (post-cutoff, from 2026-10) confirmation before promotion. This is the equivalent of Profit Mirage's "roll the calendar past the cutoff".
8. **Critique step (LLM, cheap).** A fixed checklist prompt reviews each result: costs, turnover, concentration, regime dependence, survivorship, look-ahead in factor timestamps (e.g. monthly revenue release dates), and whether the effect is just a known factor such as size or momentum. The LLM critiques; it does not choose winners. Promotion is decided by code thresholds.
9. **Forward monitor.** Haircut expected excess by at least 26% (McLean-Pontiff). Judge decay on long windows, because realised IC is about 95% time-varying noise.

**Where to spend the $5/month**
- Weekly hypothesis cards (a few thousand tokens each).
- The critique step.
- Forward-only event extraction on 重大訊息, after filtering to material event categories by rules first.
- Do *not* use the budget for LLM-driven formula search (Alpha Jungle-style) or daily LLM trading backtests. Those need thousands of calls ($10+ per run for RD-Agent; $30–$200 per configuration in FINSABER) and add little over an exhaustive scan of 40 factors.

**What not to adopt**
- End-to-end LLM trading agents (FinMem / FinAgent / TradingAgents style).
- Leaderboard-driven model choice (LiveTradeBench, AMA).
- Reliance on any single short live contest.

### Gaps
- No published study tests a researcher-agent loop *with* formal multiple-testing control (DSR/FDR over all LLM-generated candidates) against one without. The recommended combination is an inference from separate literatures.
- No evidence was found on how many LLM-proposed hypotheses per month a small platform needs before forward validation has enough power. With 11 years of dev data and monthly evaluation, forward confirmation of a +0.5%/month edge would likely take years (power not computed here).
