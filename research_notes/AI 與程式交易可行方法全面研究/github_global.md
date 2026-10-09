# Global open-source quant / AI-trading ecosystem on GitHub: methods shipped and out-of-sample evidence

Scope note: worldwide repos and their linked papers (researched 2026-10-10). Notation: AR = annualized (excess) return, IR = information ratio, MDD = max drawdown, IC = information coefficient (Pearson, daily cross-section), OOS = out-of-sample. "Self-reported" = numbers produced by the repo/paper authors with no independent replication found.

Our context, for relevance notes: single-user Taiwan-stock platform, long-only 100% individual stocks, daily after-close decisions, 2013+ daily OHLCV + institutional flows + margin/short + PER/PBR + quarterly statements + monthly revenue; 40 factors, GBM walk-forward, ~370k factor-combination candidates screened → 17 T0 candidates, PPO RL failed; latest commit says the equal-share T0 portfolios pass but trade ~437 orders/month.

---

## 1. Microsoft Qlib: Alpha158/Alpha360, model-zoo benchmark, rolling/DDG-DA, RD-Agent(Q)

### Takeaway
On Qlib's own CSI300 benchmark (test 2017-01 → 2020-08, 20 seeds), gradient-boosted trees on the engineered Alpha158 features are the strong, cheap default (LightGBM AR 9.0%, IR 1.02; DoubleEnsemble-on-LGBM best at 11.6%, IR 1.34), while deep models only beat trees on the raw-sequence Alpha360 input (HIST 9.9%, IR 1.37) and vanilla Transformer/TabNet lose money there. The margins between top models (2–3 pp/yr) are similar to the seed std of deep models (±2–3 pp), the same 2017–2020 test window has been reused by the whole community for years, and later papers testing 2021–2025 find Alpha158-style price/volume signals decayed toward zero IC — so the table is a ranking of model families, not evidence of a durable edge.

### Cited Findings
**Benchmark design (Qlib examples/benchmarks)**
- Reference config (LightGBM/Alpha158): market csi300, benchmark SH000300; train 2008-01-01→2014-12-31, valid 2015-01-01→2016-12-31, test/backtest 2017-01-01→2020-08-01; `TopkDropoutStrategy` topk=50, n_drop=5; open_cost 0.0005, close_cost 0.0015, min_cost 5, deal_price close, limit_threshold 0.095; account 100,000,000 — [Qlib LightGBM Alpha158 config](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml)
- Table values are mean ± std over 20 random seeds; README warns the backtest from v0.8.0 "differs substantially" from earlier versions, that resources were limited so some models "may have greater potential", and that the CSI500 section is incomplete; "Alpha158 (with selected 20 features)" means 20 features picked by LightGBM importance; DoubleEnsemble's base model is LGBM, TCTS's is GRU — [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)

**Feature sets**
- Alpha158 = 9 K-bar shape features (KMID, KLEN, KUP, KLOW, KSFT…) + 4 price ratios (OPEN/HIGH/LOW/VWAP ÷ close) + 29 rolling operators × windows {5,10,20,30,60} = 145 (ROC, MA, STD, BETA, RSQR, RESI, MAX, MIN, QTLU/QTLD 80/20% quantiles, RANK, RSV, IMAX/IMIN/IMXD Aroon-style, CORR/CORD price–volume correlation, CNTP/CNTN/CNTD up/down-day counts, SUMP/SUMN/SUMD RSI-like, VMA/VSTD, WVMA, VSUMP/VSUMN/VSUMD); every price feature is normalised by today's close, volume by today's volume — [Qlib loader.py](https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py)
- Alpha360 = no operators: raw 60-day history (lags 59…0) of CLOSE, OPEN, HIGH, LOW, VWAP, VOLUME = 360 features, prices ÷ latest close, volume ÷ latest volume — [Qlib loader.py](https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py)

**CSI300 / Alpha158 (test 2017-01→2020-08) — sorted by AR** — all from [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)

| Model | IC | ICIR | RankIC | AR | IR | MDD |
|---|---|---|---|---|---|---|
| DoubleEnsemble (LGBM base) | 0.0521 | 0.4223 | 0.0502 | 11.58% ±1 | 1.343 | -9.2% |
| LightGBM | 0.0448 | 0.3660 | 0.0469 | 9.01% | 1.016 | -10.4% |
| MLP | 0.0376 | 0.2846 | 0.0429 | 8.95% ±2 | 1.141 | -11.0% |
| TFT (20 feats) | 0.0358 | 0.2160 | 0.0116 | 8.47% ±2 | 0.813 | -18.2% |
| XGBoost | 0.0498 | 0.3779 | 0.0505 | 7.80% | 0.907 | -11.7% |
| CatBoost | 0.0481 | 0.3366 | 0.0454 | 7.65% | 0.803 | -10.9% |
| TRA | 0.0440 | 0.3535 | 0.0540 | 7.18% ±2 | 1.084 | -7.6% |
| Linear | 0.0397 | 0.3000 | 0.0472 | 6.92% | 0.921 | -15.1% |
| TRA (20 feats) | 0.0404 | 0.3197 | 0.0490 | 6.49% | 1.009 | -8.6% |
| GATs (20 feats) | 0.0349 | 0.2511 | 0.0462 | 4.97% | 0.734 | -7.8% |
| ALSTM (20 feats) | 0.0362 | 0.2789 | 0.0463 | 4.70% ±3 | 0.699 | -10.7% |
| SFM | 0.0379 | 0.2959 | 0.0464 | 4.65% | 0.567 | -12.8% |
| Localformer | 0.0356 | 0.2756 | 0.0468 | 4.38% | 0.660 | -9.5% |
| LSTM (20 feats) | 0.0318 | 0.2367 | 0.0435 | 3.81% ±3 | 0.556 | -12.1% |
| GRU (20 feats) | 0.0315 | 0.2450 | 0.0428 | 3.44% | 0.516 | -10.2% |
| Transformer | 0.0264 | 0.2053 | 0.0407 | 2.73% | 0.397 | -11.0% |
| TCN | 0.0279 | 0.2181 | 0.0421 | 2.62% | 0.413 | -10.9% |
| TabNet | 0.0204 | 0.1554 | 0.0333 | 2.27% ±4 | 0.368 | -10.9% |

**CSI300 / Alpha360 (same window) — sorted by AR** — [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)

| Model | IC | RankIC | AR | IR | MDD |
|---|---|---|---|---|---|
| HIST (graph, concept-shared info) | 0.0522 | 0.0667 | 9.87% ±2 | 1.373 | -6.8% |
| IGMTF | 0.0480 | 0.0606 | 9.46% | 1.351 | -7.2% |
| TRA | 0.0485 | 0.0587 | 9.20% ±3 | 1.279 | -8.3% |
| TCTS (GRU base) | 0.0508 | 0.0599 | 8.93% ±3 | 1.226 | -8.6% |
| GATs | 0.0476 | 0.0598 | 8.24% | 1.108 | -8.9% |
| AdaRNN | 0.0464 | 0.0539 | 7.53% ±3 | 1.020 | -9.4% |
| GRU | 0.0493 | 0.0584 | 7.20% | 0.973 | -8.2% |
| ADD | 0.0430 | 0.0559 | 6.67% | 0.899 | -8.6% |
| LSTM | 0.0448 | 0.0549 | 6.47% | 0.896 | -8.8% |
| ALSTM | 0.0497 | 0.0599 | 6.26% | 0.865 | -9.9% |
| TCN | 0.0441 | 0.0519 | 6.04% | 0.830 | -10.2% |
| LightGBM | 0.0400 | 0.0499 | 5.58% | 0.763 | -6.6% |
| DoubleEnsemble | 0.0390 | 0.0486 | 4.62% | 0.615 | -9.2% |
| XGBoost / CatBoost | 0.0394 / 0.0378 | — | 3.44% / 2.92% | 0.45 / 0.38 | — |
| Localformer | 0.0404 | 0.0542 | 2.46% | 0.321 | -11.0% |
| MLP | 0.0273 | 0.0396 | 0.29% | 0.027 | -13.9% |
| Sandwich | 0.0258 | 0.0337 | 0.05% | 0.000 | -17.5% |
| Transformer | 0.0114 | 0.0327 | -2.70% | -0.338 | -16.5% |
| TabNet | 0.0099 | 0.0290 | -3.69% | -0.389 | -21.5% |
| KRNN | 0.0173 | 0.0270 | -4.65% ±5 | -0.542 | -29.2% |

**CSI500 (incomplete)**: Alpha158 LightGBM IC 0.0399, AR 12.84%, IR 1.565, MDD -6.4%; CatBoost AR 4.96%; MLP 0.43%; Linear 3.82% (MDD -48.8%). Alpha360 LightGBM AR 5.05%, IR 0.766; MLP 0.22%. The CSI500 DoubleEnsemble rows show AR/IR/MDD identical to the Linear row (3.82%/0.1723/-48.76%) — likely a copy error in the README — [Qlib benchmarks README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)

**Rolling retraining / concept drift (examples/benchmarks_dynamic)**
- Alpha158, label horizon 20 days, rolling interval 20 trading days, test 2017-01→2020-08: RR[LightGBM] (plain rolling retrain) IC 0.0816, AR 7.71%, IR 1.32, MDD -9.1% → DDG-DA[LightGBM] IC 0.0878, AR 12.61%, IR 2.01, MDD -7.4%. But RR[Linear] AR 8.57%, IR 1.37 → DDG-DA[Linear] AR 7.64%, IR 1.19 (higher IC, lower return). Yahoo-sourced qlib data lacks VWAP, which makes DDG-DA's lower-level optimisation unsolvable — [Qlib benchmarks_dynamic README](https://github.com/microsoft/qlib/blob/main/examples/benchmarks_dynamic/README.md)

**RD-Agent(Q) (Microsoft, NeurIPS 2025)**
- Multi-agent LLM loop: Research stage (hypothesis → task) + Development stage (Co-STEER code agent) + backtest feedback; a multi-armed bandit chooses between factor and model directions; claims "up to 2X" AR of classical factor libraries with ">70% fewer factors"; cost "under $10" per run — [arXiv 2505.15155](https://arxiv.org/abs/2505.15155)
- CSI300, same Qlib split (test 2017-01→2020-08). Their own baselines: LightGBM AR 3.97% IR 0.57; Alpha101 5.12%/0.58; Alpha158 5.70%/0.85; Alpha360 4.38%/0.67; AutoAlpha 4.00%/0.43; TRA 6.49%/1.01; MASTER 8.96%/1.34. RD-Agent results: R&D-Factor(GPT-4o) IC 0.0489, AR 14.61%, IR 1.68; R&D-Model(o3-mini) AR 10.09%, IR 1.70; R&D-Agent(Q)(o3-mini) IC 0.0532, AR 14.21%, IR 1.74, MDD -7.4% — [arXiv 2505.15155 (HTML)](https://arxiv.org/html/2505.15155)
- Extra OOS check (Table 2): train 2008–2021, valid 2022–2023, test 2024→2025-06 on CSI500 and NASDAQ-100: R&D-Agent(Q)(o4-mini) IC 0.0288 / 0.0162, IR 2.17 / 1.77, MDD -6.6% / -6.3%. Authors' caveats: relies solely on the LLM's internal financial knowledge; results reported from a single run per configuration (baselines: median of 5 seeds) — [arXiv 2505.15155 (HTML)](https://arxiv.org/html/2505.15155)
- Independent re-test of RD-Agent on 2021-01→2025-01 (CSI500): IC 0.0113, AR 0.78%, IR 0.07, MDD -20.9%; S&P500 AR 1.69% — i.e. much weaker in a later window run by competing authors — [AlphaAgent, arXiv 2502.16789](https://arxiv.org/abs/2502.16789)

**Decay of Alpha158-type features after 2020**
- AlphaAgent's test window 2021-01→2025-01 (CSI500 and S&P500, OHLCV only, costs CSI500 0.05% buy / 0.15% sell): LightGBM AR -1.18% (CSI500) and -2.64% (S&P500); Transformer 4.11% / -4.55%; LSTM 4.96% / -1.51%; TRA 2.91% / -8.51% (MDD -49.6% on S&P500); StockMixer ≈0. On CSI500, Alpha158, GP-mined and RSI factor ICs fell from ~0.02–0.036 to near zero over the test window (read from a figure, attributed to crowding/overfitting) — [AlphaAgent, arXiv 2502.16789](https://arxiv.org/abs/2502.16789)

### Inferences
- Model-family ranking that holds across both Qlib tables: GBDT ≥ linear ≈ MLP on hand-crafted features; recurrent/graph models only pay off when fed raw sequences; generic Transformer/TabNet are bottom-tier. The best "deep" result on Alpha360 (HIST 9.87%) is roughly equal to LightGBM on Alpha158 (9.01%), so feature engineering + GBDT matches the best deep model at a fraction of the cost. This supports keeping our GBM walk-forward as the core learner.
- The cheapest documented improvement over plain LightGBM is DoubleEnsemble (an ensemble of LGBM sub-models; its sample re-weighting / feature-selection mechanism is described in the original DoubleEnsemble paper, not re-verified on this pass): +2.6 pp AR / +0.33 IR on CSI300 Alpha158. Its CSI500 numbers are unusable (copy error), so treat as one-market evidence.
- Linear regression on Alpha158 reaches IC 0.0397 / AR 6.9% — about 75% of LightGBM — which means most of the "signal" lives in the features, not in model nonlinearity.
- Baselines are not stable across papers: LightGBM-on-CSI300 over the identical 2017–2020 window is 9.01% in the Qlib README but 3.97% in the RD-Agent paper (Alpha158 5.70%). Any "X beats LightGBM by N pp" claim needs the same Qlib version, data snapshot and backtest config — the README itself says the 0.8.0 backtest changed results substantially.
- The 2017-01→2020-08 test window has been reused by every Qlib paper since ~2020; it functions as a community validation set, not a true OOS set. The only post-2020 windows found (AlphaAgent 2021–2024, RD-Agent Table 2 2024–2025) show price/volume ML signals shrinking sharply. For Taiwan, expect technical (Alpha158-like) factors to decay similarly; non-price data we hold (institutional flows, monthly revenue, statements) is not in Alpha158/360 at all and is where differentiation is more plausible.
- Rolling retrain (every 20 days) and drift adaptation (DDG-DA) give mixed gains (big for LightGBM, negative AR for Linear) on a single window; worth a test only after the static walk-forward is settled.
- TopkDropout (hold 50, replace at most 5 per day) is Qlib's built-in turnover control and the default portfolio construction in nearly every benchmark/paper below — directly relevant to our 437-orders/month problem.

### Gaps
- The README page does not state whether "Annualized Return" is excess return with or without cost; I believe Qlib's workflow reports excess-return-with-cost vs the benchmark, but did not verify on this pass.
- Default Alpha158 label expression (I believe `Ref($close,-2)/Ref($close,-1)-1`, i.e. trade at t+1, measured t+1→t+2) was not verified — loader.py contains only features.
- No independent replication of the Qlib model-zoo table found; DoubleAdapt and Qlib online-serving/Online Manager docs not fetched; DDG-DA paper's own multi-dataset results not fetched.
- Whether RD-Agent's feedback loop ever sees the 2017–2020 test period (vs only valid) is not stated in the extracted text.

---

## 2. FinRL / FinRL-Meta / ElegantRL (deep RL for trading)

### Takeaway
FinRL's evidence is mostly self-reported, single-window, and partly inconsistently reported (Sharpe ratios flagged as wrong by the organisers themselves); its own maintainers name low signal-to-noise, survivorship bias, backtest overfitting and seed/hyperparameter instability as the core problems, and their main mitigation is ensembles plus PBO-style overfitting tests. An independent long-horizon benchmark (FINSABER) includes FinRL agents among the strategies that did not beat buy-and-hold over 2004–2024. Our failed PPO attempts are consistent with this record.

### Cited Findings
- FinRL (2020) implements DRL agents (A2C, DDPG, PPO, SAC, TD3 etc.) for stock trading against buy-and-hold, mean-variance, min-variance, momentum and equal-weight baselines; its stock demo trains 2009-01-01→2020-06-30 and tests a single year 2020-07-01→2021-06-30 — [FinRL paper arXiv 2011.09607](https://arxiv.org/abs/2011.09607)
- FinRL-Meta (NeurIPS 2022) states that reliable financial RL benchmarks are hard because of "low signal-to-noise ratio of financial data, survivorship bias of historical data, and backtesting overfitting" — [FinRL-Meta arXiv 2211.03107](https://arxiv.org/abs/2211.03107)
- FinRL contests 2023–2025 (200+ participants, 46/21/85 teams): 2023 data-centric DJIA-30 task — winners (added indicators; "101 alpha factors"; PPO-switch ensemble) had high reported Sharpe in period 1 (paper footnote: Sharpe ratios were reported incorrectly) but lower cumulative return than DJIA (e.g. 3.50% vs 5.42%), and near-zero/negative return in period 2 — [FinRL Contests arXiv 2504.02281](https://arxiv.org/abs/2504.02281)
- 2025 FinRL-DeepSeek task (Nasdaq-100, train 2013–2018, test 2019–2023): winners' cumulative returns 191–343% vs Nasdaq-100 164.5%, but with MDDs of -28% to -92% (Queen's Gambit PPO: 342.7% return, MDD -92.5%); listed Sharpe values (e.g. 0.0448 for S&P 500, 0.95 for winner) appear non-annualised/inconsistent, and one team's Rachev ratio was flagged as incorrectly reported — [FinRL Contests arXiv 2504.02281](https://arxiv.org/abs/2504.02281)
- 2024 stock-ensemble study (Dow-30, 2021-01→2023-12, rolling 30/5/5-day windows): PPO 63.4% cum. return, Sharpe 1.55; best ensemble 62.6%, Sharpe 1.48, MDD -9.0%; DJIA 18.95%; min-variance 13.9% — [FinRL Contests arXiv 2504.02281](https://arxiv.org/abs/2504.02281)
- Organisers' lessons: policy instability (sensitive to hyperparameters, noise, seeds; worse in crypto); ensembles as main mitigation but ensembles converge when the action space is small; strong period-1 results weakened afterwards ("generalization to unseen markets remains a challenge"); FinRL-Crypto rejected agents with high probability of backtest overfitting at 10% significance; GPU-vectorised envs reached 227,212 samples/s with 2,048 parallel envs (1,650× one env) — [FinRL Contests arXiv 2504.02281](https://arxiv.org/abs/2504.02281)
- FinRL team's own crypto paper says earlier DRL studies "optimistically reported increased profits in backtesting, which may suffer from the false positive issue due to model overfitting", and proposes a hypothesis test on probability of overfitting — [arXiv 2209.05559](https://arxiv.org/abs/2209.05559)
- A 2025 portfolio paper lists FinRL-SAC / FinRL-DDPG as baselines that score below newer methods, and notes naive fine-tuning "often fails to enhance model performance on test data" — [arXiv 2505.12759](https://arxiv.org/abs/2505.12759)
- FINSABER (KDD 2026) included FinRL A2C, PPO, SAC, TD3 among strategies in its 2004–2024 bias-controlled backtests — [FINSABER arXiv 2505.07078](https://arxiv.org/abs/2505.07078)

### Inferences
- Direct "RL agent outputs positions" has no credible long-horizon OOS evidence in the open-source world; the best-documented successes are short windows with large drawdowns. Where RL does show repeatable value is as a *search engine* over formula space (AlphaGen / QuantFactor REINFORCE, Section 4), where the environment is deterministic and the reward is IC on a training period.
- If RL is revisited, the community's own minimum bar is: multiple seeds, ensembles, PBO/deflated-Sharpe rejection, and multiple disjoint test periods — none of which a single PPO run satisfies.

### Gaps
- ElegantRL-specific benchmarks and FINSABER's per-algorithm numbers for the FinRL agents were not extracted.
- No fully independent replication study of FinRL found.

---

## 3. Backtesting / live frameworks and portfolio/ML-finance libraries: what they ship and what their docs warn

### Takeaway
The frameworks themselves ship engines, not edges; the most useful transferable content is their anti-bias tooling (freqtrade's lookahead-analysis, hyperopt warnings) and portfolio-construction advice (PyPortfolioOpt: don't trust expected-return inputs; HRP lowers OOS variance in simulations but equal-weight is a hard bar). Maintenance has shifted: nautilus_trader is the most actively developed, vectorbt active, zipline lives on as zipline-reloaded, backtrader upstream is frozen. Meta-labelling/triple-barrier (mlfinlab) has only vendor-reported support and one negative independent test.

### Cited Findings
**Maintenance status (2026 snapshots; aggregator data, moderate reliability)**
- nautilus_trader: release 1.231.0 on 2026-08-02, intended as the final 1.x (Cython) release; v2 Rust+PyO3 runtime at release-candidate stage for backtest/live/risk; breaking API changes — [newreleases.io nautilus_trader 1.231.0](https://newreleases.io/project/github/nautechsystems/nautilus_trader/release/v1.231.0); ~58k weekly PyPI downloads — [pypistats nautilus-trader](https://pypistats.org/packages/nautilus-trader)
- vectorbt: PyPI 1.1.0, ~121.5k weekly downloads, last push 2026-04-25, 125 open issues; maintainer says it "requires increasing effort to maintain" — [pypistats vectorbt](https://pypistats.org/packages/vectorbt); [gittrend vectorbt](https://gittrend.io/repo/polakowo/vectorbt); [vectorbt Patreon](https://www.patreon.com/vectorbt)
- zipline-reloaded (community continuation after Quantopian shut in 2020): PyPI 3.1.1, ~3.9k weekly downloads — [pypistats zipline-reloaded](https://pypistats.org/packages/zipline-reloaded)
- backtrader: last PyPI release 1.9.78.123 on 2023-04-19; last GitHub push 2024-08-19; activity moved to forks — [Snyk advisor backtrader](https://snyk.io/advisor/python/backtrader); [gittrend backtrader](https://gittrend.io/repo/mementum/backtrader)

**freqtrade (crypto bot; strategy-design lessons transfer)**
- Hyperopt docs: parameter precision capped at 3 decimals because finer values "will usually result in overfitted results"; "running too many epochs at once may not produce greater results" (gains flatten ~500–1000 epochs); different random states "will most likely produce different results"; loss-function choice can give "completely different results"; position-stacking results "cannot be reproduced in dry/live trading" — [freqtrade hyperopt docs](https://www.freqtrade.io/en/stable/hyperopt/)
- `lookahead-analysis`: re-runs backtests per signal on truncated data and compares indicator columns with the full-data run, reporting biased entries/exits/indicators. Listed causes: negative `shift()`, `iloc[]` row access, uncontrolled loops, `.mean()/.min()/.max()` over the whole dataframe instead of `rolling()`, MACD with signal period 1. Removing biased conditions "usually" makes the strategy much worse because the bias was driving the profit. Caveats: only triggered signals are verified; cross-pair ranking logic gives false positives — [freqtrade lookahead-analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/)

**Portfolio construction (PyPortfolioOpt, HRP)**
- PyPortfolioOpt docs: expected returns are "rather difficult to know with any certainty"; mean historical returns are "subject to large uncertainty", a problem for an optimiser that "will maximise erroneous inputs"; supplying expected returns "can do more harm than good"; advice: put effort into the risk model instead — [PyPortfolioOpt Expected Returns docs](https://pyportfolioopt.readthedocs.io/en/latest/ExpectedReturns.html)
- HRP (López de Prado 2016, JPM): Monte Carlo shows lower OOS variance than CLA minimum-variance and inverse-variance — [SSRN 2708678](https://papers.ssrn.com/abstract=2708678); an independent CBS thesis confirms lower OOS variance in Monte Carlo vs min-variance, ERC, IVP and equal weight, but finds HRP less robust to covariance misspecification — [CBS master thesis](https://research-api.cbs.dk/ws/portalfiles/portal/76452070/1332322_Master_Thesis_Hierarchical_Risk_Parity.pdf); naive equal-weight frequently outperforms mean-variance and risk-based optimisations OOS — [Wikipedia: HRP](https://en.wikipedia.org/wiki/Hierarchical_Risk_Parity)

**mlfinlab / Hudson & Thames (meta-labelling, triple barrier)**
- Hudson & Thames' 2019 research update concluded that event-based sampling + triple-barrier labels + meta-labelling "improves the performance of the strategies" (vendor research; OOS design not visible) — [Hudson & Thames research update Mar 2019](https://hudsonthames.org/?p=3448)
- Independent MQL5 test: triple-barrier + meta-model on 60.5M EURUSD ticks (2022–2023), purged CV, OOS AUC → "no bar family produces a reliable edge" for RSI/Bollinger/ADX primary rules (one market, blog-level) — [MQL5 article 23310](https://www.mql5.com/en/articles/23310)

### Inferences
- For our platform the transferable pieces are: (a) an automated look-ahead check that recomputes each day's signals on data truncated at that day and diffs against the full run (freqtrade's method is generic and cheap to copy); (b) equal-weight or inverse-volatility sizing rather than return-forecast-driven mean-variance; (c) a turnover cap like TopkDropout instead of full daily rebalancing.
- Meta-labelling is a reasonable *filter* experiment on top of an existing T0 rule (predict "will this signal pay after costs"), but evidence is thin; it must be judged on net return vs the raw rule over the same held-out period with purged/embargoed splits because labels overlap.

### Gaps
- LEAN/QuantConnect, bt, Riskfolio-Lib, Hummingbot, vectorbt PRO, alphalens/pyfolio/empyrical (and their "-reloaded" forks): no benchmark evidence collected on this pass; mlfinlab's current licensing/maintenance status not verified.
- No public, systematic study found of which community-shared framework strategies survived live trading.

---

## 4. Formulaic alpha mining: 101 Alphas, GP, AlphaGen, AlphaForge, QuantFactor REINFORCE, LLM miners (AlphaAgent, RD-Agent)

### Takeaway
All alpha-mining papers report big IC gains over GBDT/MLP baselines on CSI300/CSI500, but the numbers are self-reported, use 20-day-return labels with mostly 1–4-year test windows, and shrink sharply when re-run by competitors (AlphaGen self-reports CSI300 IC 0.0725 for 2020–21; AlphaForge's re-run over 2018–22 gives "RL" 0.0209). The design lessons that recur are robust: mine a *set* of low-correlated formulas scored by the combined model, keep formulas short, combine linearly, re-weight dynamically, and penalise similarity to known alpha zoos (crowding/decay).

### Cited Findings
- 101 Formulaic Alphas (Kakushadze, WorldQuant, 2016): explicit formulas for 101 "real-life" alphas; average holding period 0.6–6.4 days; average pairwise correlation 15.9%; returns strongly correlated with volatility; no significant dependence on turnover — [arXiv 1601.00991](https://arxiv.org/abs/1601.00991)
- AlphaGen (KDD 2023): RL (PPO) generator of formula tokens whose reward is the improvement of a *linear combination model* of the alpha pool (synergy rather than single-alpha IC); 6 raw features (OHLCV + vwap); label 20-day forward return; train 2009–2018, valid 2019, test 2020–2021; 10 seeds. Test IC/RankIC CSI300: AlphaGen 0.0725/0.0806; XGBoost 0.0404/0.0576; LightGBM 0.0259/0.0324; MLP 0.0250/0.0401; GP_top 0.0078/0.0157; PPO_top -0.0166. CSI500: AlphaGen 0.0438/0.0727 vs XGBoost 0.0353/0.0639. Backtest top-50/drop-5 on CSI300 2020–21 shown only as a figure; no cost figures; limitations: linear combiner, pool limited to "a few dozen" alphas, formulas capped at 20 tokens — [AlphaGen arXiv 2306.12964 (ar5iv)](https://ar5iv.labs.arxiv.org/html/2306.12964)
- AlphaForge (AAAI 2025): generative-predictive network (differentiable surrogate of factor fitness + diversity loss) for mining, then *daily dynamic* selection of top factors by recent IC/ICIR and a linear "Mega-Alpha" refit; annual retraining, test 2018–2022, label 20-day VWAP return. IC (%) CSI300: AlphaForge 4.40, static-weight ablation 2.43, DSO 2.55, RL(AlphaGen) 2.09, GP 1.29, MLP 1.22, LGBM 0.84, XGB 0.41; CSI500: 2.84 vs RL 1.91, LGBM 1.75. Reported live trading: ~3M RMB in CSI500 for ~9 months, +21.68% excess return (self-reported). Backtest: top-50, max 5 changes/day, costs not specified. Authors prefer linear combiners to avoid overfitting — [AlphaForge arXiv 2406.18394](https://arxiv.org/abs/2406.18394)
- QuantFactor REINFORCE (2024–25): argues PPO is ill-suited because the formula-generation MDP is near-deterministic; uses REINFORCE with a variance-bounded baseline and IR-shaped reward. Test IC CSI300: QFR 0.0588, AlphaGen 0.0500, GP 0.0445, MLP 0.0123; CSI500: QFR 0.0708, AlphaGen 0.0544, GP 0.0557; authors say AlphaGen converges to local optima and overfits training data; one author affiliated with a hedge fund; no independent replication — [arXiv 2409.05144](https://arxiv.org/abs/2409.05144)
- AlphaAgent (2025): LLM idea/factor/eval agents with three anti-decay regularisers: originality (AST max-common-subtree similarity vs an existing alpha zoo such as Alpha101), hypothesis–expression alignment, and complexity penalties (length, free parameters, feature count). Train 2015–2019, valid 2020, test 2021-01→2025-01, OHLCV only. Test results CSI500: AR 11.00%, IR 1.488, IC 0.0212, MDD -9.4%; S&P500: AR 8.74%, IR 1.05, IC 0.0056. Baselines in same window: AlphaForge 3.45% (CSI500) / 2.45% (S&P), RD-Agent 0.78% / 1.69%, DeepSeek-R1 best-of-10 1.58% / 2.75%, o1 best-of-10 0.46% / 2.29%. Hit ratio of valid factors 0.29 with constraints vs 0.16 without — [AlphaAgent arXiv 2502.16789](https://arxiv.org/abs/2502.16789)
- AlphaAgent caveats (from the extraction): best-of-N/20-trial selection of reported runs; AlphaAgent used GPT-3.5-turbo while baselines used other models; only one 4-year test window and two markets; intro and table disagree (tokens 30% vs 23%; IR 1.5 vs 1.488) — [AlphaAgent arXiv 2502.16789](https://arxiv.org/abs/2502.16789)
- RD-Agent(Q) factor branch (R&D-Factor GPT-4o) on CSI300 2017–2020: IC 0.0489, AR 14.61%, IR 1.68 using ~22% of the factor count of Alpha158/360 — [arXiv 2505.15155 (HTML)](https://arxiv.org/html/2505.15155)

### Inferences
- Replication shrinkage is large: AlphaGen's own CSI300 IC 0.0725 (test 2020–21) vs 0.0500 in QFR's table and 0.0209 in AlphaForge's 2018–22 annual-retrain re-run; RD-Agent's AR 14% (2017–20, own run) vs 0.78% (2021–24, AlphaAgent's run). Treat any single paper's IC as an upper bound.
- The robust ideas map well onto our "find strong factors, then combine different ones" direction: (1) score a candidate by its *marginal* contribution to the existing pool (AlphaGen), not its standalone IC — directly addresses our 17 similar-looking T0 candidates; (2) penalise structural similarity to factors already in the pool (AlphaAgent originality); (3) keep expressions short and combine linearly; (4) re-select/re-weight factors from recent IC (AlphaForge dynamic combination gave 4.40 vs 2.43 static IC).
- All these papers use 20-day forward return labels and top-50/drop-5 portfolios on 300–500-stock universes; WorldQuant's 101 alphas hold 0.6–6.4 days. With Taiwan's materially higher round-trip costs than the 0.20% assumed for China in these papers (our platform's own cost model applies), very short-horizon formulas are likely to be cost-dominated; 20-day labels with a turnover cap are the transferable setting.
- LLM miners' only distinctive advantage over GP/RL is access to priors beyond price/volume; on OHLCV-only data their edge over GP/AlphaForge is modest and noisily measured. Our richer data (flows, revenue, statements) is where an LLM hypothesis generator would plausibly add value — under the same point-in-time and multiple-testing rules as any scan.

### Gaps
- Alpha-GPT and gplearn-specific OOS numbers not collected; QFR test period not extracted.
- No cost-inclusive AR figures for AlphaGen/AlphaForge (figures only); AlphaForge live-trading claim unaudited.

---

## 5. Large AI-trading repos 2024–2026: TradingAgents, FinGPT, ai-hedge-fund, LLM agents — what they do and honest evaluation

### Takeaway
The most-starred LLM trading repos are demos: ai-hedge-fund (≈64k stars) explicitly does not trade and publishes no performance; FinGPT reports sentiment-classification F1, not trading results; TradingAgents' headline Sharpe 5.6–8.2 comes from a 3-month, 3-stock window with no stated costs. The one large bias-controlled re-evaluation (FINSABER, 2004–2024, survivorship-free S&P 500) found LLM agents' advantages "deteriorate significantly", CAPM alpha insignificant, and regime behaviour backwards (too cautious in bull markets, too aggressive in bear markets). A 2025–26 literature shows LLM backtests before the model's training cutoff are contaminated by memorisation.

### Cited Findings
- TradingAgents (2024): roles = fundamentals/sentiment/news/technical analysts, bull/bear researchers, trader, risk team (risky/neutral/conservative), fund manager; gpt-4o-mini/gpt-4o for quick tasks, o1-preview for deep reasoning; test 2024-01-01→2024-03-29 on AAPL, GOOGL, AMZN: cumulative return 26.62% / 24.36% / 23.21%, Sharpe 8.21 / 6.39 / 5.60 vs buy-and-hold -5.23% / 7.78% / 17.10%; no transaction costs mentioned; authors say the window is short "due to intensive LLM and tool use (11 LLM calls & 20+ tool calls/prediction)" and that the AAPL Sharpe exceeds their expected range; training-data look-ahead not discussed — [TradingAgents arXiv 2412.20138](https://arxiv.org/abs/2412.20138)
- virattt/ai-hedge-fund: "proof of concept for an AI-powered hedge fund"; "the system does not actually make any trades"; paper-trading mode with fake money; backtester exists; no performance results published; data via Financial Datasets API; 63.9k stars, 11.2k forks — [ai-hedge-fund GitHub](https://github.com/virattt/ai-hedge-fund)
- FinGPT: LoRA-tuned sentiment models (v3.3 on Llama2-13B weighted F1 FPB 0.882, FiQA-SA 0.874, TFNS 0.903, NWGI 0.643 vs GPT-4 0.833/0.630/0.808, FinBERT 0.880/0.596/0.733/0.538); FinGPT-Forecaster (next-week DOW-30 movement demo) with no accuracy or return figures; 21.4k stars — [FinGPT GitHub](https://github.com/AI4Finance-Foundation/FinGPT)
- FINSABER (KDD 2026): 2004–2024, prices + news + 10-K/10-Q; survivorship handled by historical S&P 500 constituents incl. delisted; rolling 2-year windows; commissions $0.0049/share, $0.99 min. Original FinMem claim (TSLA, 2022-10→2023-04) Sharpe 2.679; over 2004–2024 composite universes FinMem Sharpe -0.29 to 0.03 and FinAgent -0.08 to 0.24 vs buy-and-hold 0.32–0.70; paired t-tests p from 3.0e-6 to 0.0117; CAPM alpha not significant for any LLM agent (all p > 0.34); regime Sharpe: buy-and-hold 0.61 bull / -0.28 bear, FinAgent 0.12 / -0.38, FinMem -0.19 / -0.97 — [FINSABER arXiv 2505.07078](https://arxiv.org/abs/2505.07078)
- Look-ahead via memorisation: "Lookahead Propensity" stays positive in-sample and falls to ~0 right after the training cutoff; LLM headline-return predictability is amplified on high-LAP firm-dates and loses significance post-cutoff — [arXiv 2512.23847](https://huggingface.co/papers/2512.23847); FinCAD suppression cut in-sample backtest returns by up to 67.1% on memorised dates while leaving 2025 OOS results nearly unchanged — [arXiv 2605.24564](https://arxiv.org/abs/2605.24564); "we cannot distinguish whether a model demonstrates genuine forecasting ability or simply recalls memorized information" — [The Memorization Problem, arXiv 2504.14765](https://arxiv.org/html/2504.14765v2); point-in-time trained models (DatedGPT) proposed as a fix — [arXiv 2603.11838](https://arxiv.org/html/2603.11838v1); logit-adjustment unlearning — [arXiv 2512.06607](https://www.arxiv.org/pdf/2512.06607)
- Secondary summary of Look-Ahead-Bench (2026): commercial LLMs tested on stock selection in-sample (Apr–Sep 2021) vs post-cutoff (Jul–Dec 2024); point-in-time models gave stable alpha across both (secondary source, numbers unverified) — [HedgeFundAlpha](https://hedgefundalpha.com/education/your-llms-alpha-might-be-mere-memorization/)

### Inferences
- None of these repos provides evidence relevant to choosing Taiwan stock-selection rules. Their usable parts are engineering patterns (role-split analyst prompts, news/sentiment pipelines).
- Any LLM-in-the-loop evaluation must use only dates after the model's training cutoff, which for current models leaves a short window — far shorter than our 2015-06/2020-10 research design. LLMs are better positioned as hypothesis generators whose output (a formula on point-in-time data) is evaluated by the existing non-LLM backtest, as in RD-Agent/AlphaAgent.

### Gaps
- FinRobot, OpenBB agents and newer 2025–26 agent repos not examined; no honest live-trading track records found for any of them.

---

## 6. Recurring lessons: look-ahead, survivorship, overfitting, costs — and what survives live

### Takeaway
The strongest cross-project lesson is that backtest performance barely predicts live performance once many variants have been tried (Quantopian, 888 algorithms: backtest Sharpe R² < 0.025; more backtesting → larger IS/OOS gap). Documented live evidence in open source is nearly nonexistent (AlphaForge's 9-month self-report is the only one found). What the credible projects converge on: point-in-time data and automated look-ahead checks, survivorship-free universes, multiple seeds/windows, PBO-style overfitting tests, linear/simple combiners, turnover caps, and equal-weight/risk-based sizing.

### Cited Findings
- Quantopian "All that Glitters Is Not Gold" (Wiecki et al., 2016): 888 algorithms, IS 2010→deployment (Jan–Jun 2015), OOS Jun 2015→Feb 2016; backtest Sharpe offers "little value in predicting out of sample performance (R² < 0.025)"; volatility, max drawdown and hedging features had significant predictive value; more backtesting/tuning widened the IS–OOS gap; nonlinear classifiers on backtest features reached R² = 0.17 on hold-out and a portfolio chosen by them beat one chosen by highest backtest Sharpe — [Quantpedia summary](https://quantpedia.com/quantopians-academic-paper-about-in-vs-out-of-sample-performance-of-trading-alg/); [CXO Advisory summary](https://www.cxoadvisory.com/big-ideas/in-sample-vs-out-of-sample-performance-of-888-trading-strategies)
- FinRL-Meta names low SNR, survivorship bias and backtest overfitting as the three obstacles — [arXiv 2211.03107](https://arxiv.org/abs/2211.03107); FinRL-Crypto rejects agents by probability of backtest overfitting at 10% — [arXiv 2504.02281](https://arxiv.org/abs/2504.02281)
- Typical code-level look-ahead bugs: negative shift, iloc row access, full-sample mean/min/max — [freqtrade lookahead-analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/)
- Survivorship control by historical constituents including delisted names changed LLM-strategy conclusions — [FINSABER arXiv 2505.07078](https://arxiv.org/abs/2505.07078)
- Alpha decay/crowding: Alpha158, GP and RSI factor ICs fell to near zero on CSI500 2021–2024 — [AlphaAgent arXiv 2502.16789](https://arxiv.org/abs/2502.16789)
- Turnover control is built into the standard open-source portfolio rule (hold top 50, replace ≤5/day) — [Qlib LightGBM config](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml); [AlphaForge arXiv 2406.18394](https://arxiv.org/abs/2406.18394)
- The only live-money result found: AlphaForge ~3M RMB, CSI500, ~9 months, +21.68% excess (self-reported) — [AlphaForge arXiv 2406.18394](https://arxiv.org/abs/2406.18394)
- Return-forecast-driven optimisation is fragile; equal weight is a hard OOS bar — [PyPortfolioOpt docs](https://pyportfolioopt.readthedocs.io/en/latest/ExpectedReturns.html); [Wikipedia: HRP](https://en.wikipedia.org/wiki/Hierarchical_Risk_Parity)

### Inferences
- Our ~370k-candidate scan is the Quantopian situation at a larger scale: the screening statistic (development-period excess return) will have weak predictive power for the final test, so (a) the multiple-testing correction already required by REQUIREMENTS §7 is the most important safeguard, and (b) secondary features that Quantopian found predictive — volatility, drawdown, simplicity, consistency across sub-periods — should weigh in selection more than peak return.
- Our T0 set problem (437 orders/month) has a standard open-source answer: TopkDropout-style rules (hold K, swap at most N per day/week, ranked by a combined score) or AlphaForge's 5-changes/day cap — implementable without changing the signals.
- No open-source project provides evidence that deep RL or LLM agents survive live equity trading; GBDT/linear factor models with conservative portfolio rules remain the best-documented approach.

### Gaps
- No systematic public dataset of live results from open-source strategies (beyond Quantopian 2016) was found; QuantConnect/Numerai-style live tournament evidence not collected on this pass.
- Deflated Sharpe / PBO implementations in specific repos (e.g. mlfinlab, pypbo) not verified.
