# LLM-driven automated quantitative research ("AI researcher" agents): evidence, overfitting controls, architecture

Notes compiled 2026-10-09. All numbers are as self-reported by the cited paper unless stated otherwise. "IS" = in-sample / selection window; "OOS" = held-out test window. A recurring caution applies to every result below: most papers re-use the same Qlib evaluation code, and at least one 2026 paper reports lookahead bugs in that shared pipeline (see Q2).

## Q1. What the main systems do, on which data, with which out-of-sample results and costs

### Takeaway
Between 2023 and 2026 the field moved from "LLM writes formula factors with a human" (Alpha-GPT, 2023) to closed loops that propose hypotheses, write code, backtest on Qlib and iterate (R&D-Agent(Q), AlphaAgent, QuantaAlpha, AutoScientist-Quant). Almost all evidence is on Chinese A-shares (CSI300/CSI500) with daily OHLCV data. Self-reported OOS annualized excess returns range from about 5% to 14% with IR of about 0.6 to 1.7. Re-evaluations by other groups give much lower numbers, sometimes negative. The headline numbers of one system (QuantaAlpha) fell by roughly 6x between its v1 and v3 arXiv versions.

### Cited Findings

**Alpha-GPT (2023) / Alpha-GPT 2.0 (2024), IDEA Research, HKUST (Saizhuo Wang, Jian Guo et al.)**
- Alpha-GPT is a human-AI interactive alpha-mining system: the LLM turns a quant's natural-language idea into formula alphas. v1 was posted 2023-07-31; v2 (2025-09-20) appeared in the EMNLP 2025 System Demonstration track. The abstract gives no quantitative results — [arXiv 2308.00016](https://arxiv.org/abs/2308.00016)
- Alpha-GPT 2.0 (2024-02-15, labeled a "draft / work in progress") extends the human-in-the-loop design to alpha mining, modeling and analysis. Its memory holds an annotated alpha base, a processed finance-literature corpus, and experiment logs — [arXiv 2402.09746](https://arxiv.org/abs/2402.09746)
- The only performance claim found for the Alpha-GPT line is that the system placed in the top 10 of more than 41,000 teams in the WorldQuant International Quant Championship. No IC, return or test-period figures were found for 2.0 — [Alpha-GPT 2.0 PDF](https://arxiv.org/pdf/2402.09746)

**QuantAgent (2024-02-06; same group)**
- QuantAgent uses two loops. In the inner loop the LLM refines its answers against a knowledge base. In the outer loop those answers are tested in real-world scenarios and the results are written back into the knowledge base. The authors claim "provable efficiency". The abstract gives no data, period or IC/return numbers — [arXiv 2402.03755](https://arxiv.org/abs/2402.03755)

**R&D-Agent(Q) / RD-Agent-Quant (Microsoft Research Asia; arXiv 2025-05, revised 2025-09; NeurIPS 2025 Datasets & Benchmarks)**
- **Main experiment:** CSI300 daily data. Train 2008-01 to 2014-12, validation 2015-01 to 2016-12, test 2017-01 to 2020-08-01 — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- **Main OOS results (CSI300, 2017 to 2020-08):**
  - R&D-Agent(Q) with o3-mini: IC 0.0532, ICIR 0.428, ARR 14.21%, IR 1.738, MDD -7.42%.
  - R&D-Agent(Q) with GPT-4o: ARR 11.44%, IR 1.317.
  - Baselines: Alpha158 ARR 5.70% (IR 0.846), Alpha360 4.38%, LightGBM 3.97%, GRU 3.44%, Transformer 2.93%, TRA 6.49%.
  - The headline claim is "up to 2x higher annualized returns than classical factor libraries using 70% fewer factors."
  - Results are the median of 5 seeds — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- **Second OOS window (Appendix D.1):** CSI500 and NASDAQ100, train 2008–2021, validation 2022–2023, test 2024-01-01 to 2025-06-30.
  - o4-mini on CSI500: IC 0.0288, IR 2.17, MDD -6.56% (best baseline TRA: IR 0.60).
  - o4-mini on NASDAQ100: IC 0.0162, IR 1.77.
  - This table reports no ARR.
  - Stated knowledge cutoffs: GPT-4o 2023-10-01, which is before this test window; o4-mini 2024-06-01, "almost entirely" before it — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- **Costs and runtime:**
  - Total cost is under $10 for every R&D-Agent(Q) workflow.
  - A joint run lasts 12 hours: a bandit run had 44 loops, 24 valid, 8 accepted as new SOTA. Factor-only and model-only runs last 6 hours each.
  - Timeouts: 600 s per coding task, 3600 s per backtest, and up to 10 self-debug iterations per task — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2); [GitHub microsoft/RD-Agent](https://github.com/microsoft/RD-Agent)
- **Transaction costs:** buy 0.05%, sell 0.15%, minimum 5 CNY, trades at the close, top-50 portfolio with the bottom 5 dropped. Appendix D.1 gives different OOS costs (0.5% per trade for CSI300, 0.1% for NASDAQ100). The paper is internally inconsistent here — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)

**AlphaAgent (Sun Yat-sen University et al.; arXiv 2025-02; KDD 2025)**
- **Data and split:** CSI500 and S&P500, OHLCV only. Train 2015-01 to 2019-12, validation 2020, test 2021-01 to 2025-01. The text elsewhere says 2021–2024 — [arXiv 2502.16789v2](https://arxiv.org/html/2502.16789v2); [ACM DL](https://dl.acm.org/doi/pdf/10.1145/3711896.3736838)
- **OOS results with GPT-3.5-turbo:**
  - CSI500: IC 0.0212, ICIR 0.194, annual excess 11.00%, IR 1.488, MDD -9.36%.
  - S&P500: IC 0.0056, annual excess 8.74%, IR 1.05, MDD -9.10%.
  - Costs: CSI500 buy 0.05% / sell 0.15%; S&P500 sell 0.05% only.
  - Strategy: top-50 dropout-5, with a LightGBM (max depth 4) combiner — [arXiv 2502.16789v2](https://arxiv.org/html/2502.16789v2)
- **Baselines in the same table (CSI500 annual excess):** LightGBM -1.18%, LSTM 4.96%, TRA 2.91%, AlphaForge 3.45%, RD-Agent 0.78%, DeepSeek-R1 best-of-10 1.58%, o1 best-of-10 0.46% — [arXiv 2502.16789v2](https://arxiv.org/html/2502.16789v2)
- **Efficiency claims:** an 81% higher "hit ratio" (0.29 vs 0.16 without the regularizers) and 23–30% fewer tokens. No dollar costs are reported — [arXiv 2502.16789v2](https://arxiv.org/html/2502.16789v2)

**QuantaAlpha (arXiv 2026-02, v3 2026-05)**
- **Method:** evolutionary, trajectory-level mutation and crossover of whole mining runs, with AST-based redundancy limits and complexity limits.
- **Split:** CSI300, train 2016–2020, validation 2021, test 2022-01 to 2025-12-26 — [arXiv 2602.07085 (HTML v3)](https://arxiv.org/html/2602.07085)
- **v1 headline (2026-02-06):** IC 0.1501, ARR 27.75%, MDD 7.98% (GPT-5.2), plus 160% / 137% four-year cumulative excess return when transferred to CSI500 / S&P500 — [arXiv 2602.07085v1](https://arxiv.org/abs/2602.07085v1)
- **v3 headline (2026-05-18):** IC 0.0472, ARR 4.68%, IR 0.645, MDD 11.8%; transfer cumulative excess about 40.28% (CSI500) and 19.1% (S&P500). The abstract page gives no reason for the change — [arXiv 2602.07085](https://arxiv.org/abs/2602.07085)
- **Same-paper comparisons (v3):** TRA, a plain deep-learning model, beats QuantaAlpha on ARR (6.81%), IR (1.05) and MDD (8.51%). Other rows: LSTM ARR 6.01%, RD-Agent (GPT-5.2) 3.58%, AlphaAgent (GPT-5.2) 1.11%, Alpha158 2.66% — [arXiv 2602.07085 (HTML v3)](https://arxiv.org/html/2602.07085)
- **Cost:** about 1.8M tokens and about 20 hours per run (AlphaAgent about 1.5M tokens, RD-Agent about 2.3M). Settings: 10 parallel directions x 5 iterations, 3 expressions per hypothesis — [arXiv 2602.07085 (HTML v3)](https://arxiv.org/html/2602.07085)

**AutoScientist-Quant (Cambridge / Google authors; arXiv 2026-08, v2 2026-09)**
- **Method:** a single budget-aware controller decides whether to improve, combine, pivot or stop, and also handles library selection and model tuning.
- **Split:** CSI300 (main); train 2015-01 to 2020-01, validation 2020-01 to 2021-06, agent-visible feedback window 2021-06 to 2023-06, held-out test 2023-06 to 2026-05 — [arXiv 2608.28632](https://arxiv.org/html/2608.28632)
- **CSI300 held-out results (backbone GPT-OSS-120B):**
  - AutoScientist-Quant custom factors: IC 0.028, ARR 1.8%, IR 0.26.
  - Custom factors + Alpha158: IC 0.034, ARR 3.5%, IR 0.50, MDD -9.5%.
  - LightGBM on Alpha158: ARR -0.1%.
  - AlphaAgent: -2.3% (custom) / -1.0% (+Alpha158).
  - QuantaAlpha: -1.6% (custom) / -0.2% (+Alpha158).
  - [arXiv 2608.28632](https://arxiv.org/html/2608.28632)

**LLM + MCTS formula mining ("Navigating the Alpha Jungle", Tsinghua; arXiv 2025-05; AAAI)**
- **Data and split:** CSI300 and CSI1000, 10-day and 30-day targets. Train 2011–2020, test 2021-01 to 2024-11 — [arXiv 2505.11122v3](https://arxiv.org/html/2505.11122v3); [AAAI](https://ojs.aaai.org/index.php/AAAI/article/view/37069)
- **Cost per run (from a figure):** about $74.4 with GPT-4.1 (IR 1.23) and about $7.5 with Gemini-2.0-flash-lite (IR 1.27). The cheap model did as well as the expensive one — [arXiv 2505.11122v3](https://arxiv.org/html/2505.11122v3)

**Chain-of-Alpha (arXiv 2025-08)**
- A dual chain (generation chain plus optimization chain) on A-shares. A third-party overview reports CSI500 annualized return about 13.24% and IR about 1.42 — [alphaXiv overview](https://www.alphaxiv.org/overview/2508.06312)
- Both arXiv versions have since been **withdrawn**. An arXiv admin note says the submitter lacked the rights to agree to the license — [arXiv 2508.06312v2](https://arxiv.org/abs/2508.06312v2)

**AlphaForge (AAAI 2025; non-LLM generative-predictive network plus dynamic factor weighting)**
- Claims to beat MLP, LightGBM, XGBoost, GP, RL (AlphaGen) and DSO on CSI300 and CSI500. CSI300 baseline ICs: DSO 2.55%, RL 2.09%, GP 1.29% — [arXiv 2406.18394](https://arxiv.org/html/2406.18394v5); [AAAI](https://ojs.aaai.org/index.php/AAAI/article/view/33365)
- A secondary summary reports about 21.68% excess return over CSI500 in roughly 9 months of real-money trading. This was not verified in the paper text — [alphaXiv overview](https://alphaxiv.org/overview/2406.18394v5)

**Other 2025–2026 systems**
- **Evolutionary Factor Search** (LLM + evolutionary algorithm for sparse portfolios; v1 2025-07, v2 2026-08): tested on Fama-French benchmarks plus US, Hong Kong and China equities — [arXiv 2507.17211](https://arxiv.org/abs/2507.17211)
- **QuantEvolve** (Qraft Technologies, 2025-10): quality-diversity evolutionary strategy search that keeps the best strategy per behavioral niche (risk, turnover) — [arXiv 2510.18569](https://arxiv.org/pdf/2510.18569)
- **MadEvolve** (2026-05): an AlphaEvolve-style code evolution applied to Bitcoin trading — [arXiv 2605.23007](https://arxiv.org/pdf/2605.23007)
- **The DeepMind AlphaEvolve source paper** (2025-06) targets algorithms and infrastructure, not finance — [arXiv 2506.13131](https://arxiv.org/abs/2506.13131)
- **AlphaDiverse** (2026-09): post-trains small local Planner and Realizer agents with SFT plus GRPO, rewarding both predictive quality and diversity, on four Chinese universes. Feedback uses inner-period data only, and the final model is frozen before the outer-period test. The abstract gives no numbers — [arXiv 2609.29014](https://arxiv.org/abs/2609.29014)

### Inferences
- Self-reported gains depend heavily on the test window and on who runs the baselines:
  - RD-Agent appears as ARR 0.78% in AlphaAgent's paper (CSI500 2021–24) and 3.58% in QuantaAlpha's paper (CSI300 2022–25).
  - In its own paper RD-Agent(Q) reports 14.21% (CSI300 2017–20).
  - The same Alpha158 + LightGBM baseline ranges from +5.7% to -0.1% depending on the paper and period.
- The most recent and most careful re-evaluation (AutoScientist-Quant, test 2023-06 to 2026-05) puts every LLM factor-mining system at an ARR of roughly -2% to +3.5% on CSI300. That is economically marginal before any extra slippage.
- R&D-Agent(Q)'s main CSI300 test window (2017–2020) lies before the training cutoffs of the LLMs it uses. Its strongest numbers are therefore exposed to memorization (see Q2). Its cleaner 2024–2025 test reports IC and IR but not ARR.
- LLM API cost is not the binding constraint: runs cost about $7.5–$74 or 1.5–2.3M tokens. The binding constraints are statistical: how many trials, how long the clean OOS window is, and pipeline correctness.

### Gaps
- No primary quantitative results were found for Alpha-GPT 2.0, for QuantAgent, or for Chain-of-Alpha's experiments (the paper is withdrawn).
- AlphaForge's own CSI300 row and the source of its live-trading claim were not retrieved.
- No paper found reports per-year OOS returns in a table. AlphaAgent shows decay only in a figure.
- No paper states why QuantaAlpha's numbers changed between v1 and v3. The link to the pipeline bug described by AutoScientist-Quant is my inference.

## Q2. How they try to avoid data snooping, and critiques showing results shrink under honest testing

### Takeaway
The standard controls are:
- a fixed train / validation / test split in Qlib;
- novelty filters (AST subtree similarity, factor-correlation thresholds);
- complexity penalties (formula length, parameter and feature counts);
- LLM-judged "hypothesis consistency" or "overfitting-risk" scores.

None of the flagship systems (R&D-Agent(Q), AlphaAgent, QuantaAlpha, LLM+MCTS) applies a formal multiple-testing correction tied to the number of trials. The strongest 2026 critiques document three things:
- lookahead bugs in the shared evaluation code;
- LLM memorization of the test period;
- a deflated-Sharpe study in which every LLM-discovered strategy failed certification.

### Cited Findings
**Controls used by the systems**
- **R&D-Agent(Q):**
  - Drops a new factor if its maximum IC-correlation with the current SOTA library is at least 0.99.
  - Updates the SOTA library only when a candidate beats the incumbent.
  - The LLM sees only schema-level information and no explicit date boundaries.
  - Reports the median of 5 seeds.
  - The paper does not discuss formal multiple-testing correction (deflated Sharpe, White's Reality Check) — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- **AlphaAgent:**
  - Originality is measured as the size of the largest common isomorphic subtree between factor ASTs, compared against an "alpha zoo".
  - An LLM scores hypothesis-description-expression alignment, weighted 0.5 / 0.5.
  - A complexity term β3·log(1+|features|) is used, but no numeric thresholds or β weights are disclosed.
  - The authors attribute the decay of Alpha158, GP and RSI factors (IC falling from 0.022–0.036 to about 0) to crowding and overfitting, while AlphaAgent factors held an IC of about 0.02 — [arXiv 2502.16789v2](https://arxiv.org/html/2502.16789v2)
- **QuantaAlpha:**
  - Symbol length at most 250 (200 in a stricter run) and at most 6 base features (4 in the stricter run).
  - Factor pool admission requires |corr| < 0.7 with every pooled factor on 2021 validation data, and the pool is capped at 50% of mined factors.
  - Seed robustness across 3 seed sets: IC standard deviation 0.0021.
  - Cost sensitivity at 1.5x and 2x costs is described as "stable" but no numbers are given — [arXiv 2602.07085 (HTML v3)](https://arxiv.org/html/2602.07085)
- **LLM+MCTS:** "overfitting risk" is a qualitative LLM score (temperature 0.1) that looks for complexity, parameter count and p-hacking, averaged into the MCTS reward — [arXiv 2505.11122v3](https://arxiv.org/html/2505.11122v3)

**Critique 1: lookahead in the shared evaluation pipeline**
- AutoScientist-Quant (2026-08/09) says the evaluation code it reused from AlphaAgent, QuantaAlpha and R&D-Agent-Quant had two lookahead problems:
  - "Information metrics were computed over the full sample rather than the declared test segment," so selection feedback included the evaluation period.
  - "A failed segment filter scored models on their own training span."
- After fixing both, AlphaAgent and QuantaAlpha show negative ARR on CSI300 for 2023-06 to 2026-05. The authors exclude R&D-Agent-Quant from their baselines because its loop selects alphas on the same window it reports — [arXiv 2608.28632](https://arxiv.org/html/2608.28632)
- QuantaAlpha's own headline fell from IC 0.1501 / ARR 27.75% (v1, 2026-02) to IC 0.0472 / ARR 4.68% (v3, 2026-05) — [v1](https://arxiv.org/abs/2602.07085v1); [v3](https://arxiv.org/abs/2602.07085)

**Critique 2: search-aware deflation (Gençay, "What survives honest evaluation?", 2026-08-27)**
- **Design:**
  - The agent can act only through registry-validated tools whose feature space excludes lookahead by construction.
  - Every evaluation is logged in a trial ledger.
  - Performance is deflated by the trial count using the Deflated Sharpe Ratio (DSR) and the Probability of Backtest Overfitting (PBO).
  - Data: US 453-stock point-in-time universe (design 2017–2021, evaluation 2022–2025) and 39 ETFs (design 2007–2016, evaluation 2017–2025).
  - Models: gpt-4.1, with a replication on claude-sonnet-5 — [arXiv 2608.27734](https://arxiv.org/html/2608.27734)
- **Main results:**
  - Every LLM-discovered strategy was rejected, across both models, all budgets (up to 100 candidates) and 5 repeated runs.
  - Best gpt-4.1 design-period Sharpe was 1.69. The deflation threshold rose to 1.21, DSR was 0.86, and evaluation Sharpe was 0.18 (CI -0.80 to +1.14). The strategy returned +4.7% versus +40.8% for buy-and-hold.
  - The claude-sonnet-5 best fell from 0.44 to -0.33 (PBO 0.51).
  - The best in-sample Sharpe rose with every trial, but the deflation threshold rose faster.
  - [arXiv 2608.27734](https://arxiv.org/html/2608.27734); [abstract](https://arxiv.org/abs/2608.27734)
- **Lookahead is not caught by statistics:** a deliberately leaky "oracle" with an in-sample Sharpe of about 35 passed both DSR and PBO. Leakage prevention therefore has to be structural; statistical correction does not replace it — [arXiv 2608.27734](https://arxiv.org/html/2608.27734)
- **Evidence requirements:**
  - Pre-registered hypotheses face lower evidential bars than brute-force search.
  - A true Sharpe of 0.6 needs about 11 years to reach t ≈ 2, and 9 years certifies about 0.7.
  - Passive benchmarks were certified, while a human trader's production rule system (gold) also failed: design Sharpe 0.33, evaluation -0.12.
  - [arXiv 2608.27734](https://arxiv.org/html/2608.27734)
- The same paper states that prior LLM strategy-discovery systems mostly present backtested returns and that none applies "a multiple-testing correction tied to the search's own trial count" — [arXiv 2608.27734](https://arxiv.org/pdf/2608.27734)

**Critique 3: LLM memorization of the test period**
- Gao, Jiang and Yan propose a no-retraining diagnostic for lookahead in LLM forecasts. In a Lopez-Lira-and-Tang-style setup, part of the in-sample edge comes from recall, and the memorization-saturated share largely disappears out of sample — [arXiv 2512.23847](https://arxiv.org/pdf/2512.23847)
- Look-Ahead-Bench (2026-01) reports "significant" lookahead bias, measured as alpha decay across regimes, for Llama 3.1 8B/70B and DeepSeek 3.2 — [arXiv 2601.13770](https://arxiv.org/abs/2601.13770)
- The FinCAD preprint names "parametric look-ahead bias" (a model trained in 2024 already encodes 2018–2020 moves) and proposes a decoding-time correction — [arXiv 2605.24564](https://arxiv.org/html/2605.24564)
- The Evolutionary Factor Search paper mines on pre-2025 data and tests after 2025, beyond its models' mid-to-late-2024 cutoffs. It notes that a 59-day Hang Seng window is too noisy to rank methods — [arXiv 2507.17211](https://arxiv.org/pdf/2507.17211)
- The MCTS factor-mining paper itself states that exhaustive search for weak signals "inherently increases the likelihood of discovering spurious relationships" — [arXiv 2505.11122v3](https://arxiv.org/html/2505.11122v3)

**Benchmarks and surveys**
- AlphaBench (ICLR 2026) benchmarks LLMs in formula alpha mining on CSI300 constituents for 2020–2025, splitting results into a 2023–2024 bear market and a later bull market. Smaller open models score substantially lower. This is based on a search snippet only; the PDF was unreadable — [ICLR 2026 PDF](https://proceedings.iclr.cc/paper_files/paper/2026/file/4f3820576130a8f796ddbf204c841487-Paper-Conference.pdf)
- A 2026-08 survey of agentic quant trading finds that "strong model or forecasting capability does not reliably translate into trading performance" under live and reliability conditions. It also finds that most work stops at signal discovery and rarely integrates portfolio construction, execution or risk control — [arXiv 2608.31041](https://arxiv.org/abs/2608.31041)

### Inferences
- The flagship papers' anti-overfitting machinery targets factor crowding and redundancy (AST similarity, correlation caps, complexity). It does not address search-induced selection bias. Validation-window selection plus a single test window with no trial-count deflation is the norm.
- Three independent failure modes stack:
  - pipeline leakage (bugs);
  - model memorization when the test window predates the LLM cutoff;
  - multiple testing.
- Honest designs need all three controls: structural leakage prevention, a post-cutoff or point-in-time test, and a trial ledger with DSR/PBO.
- On honest testing, the reported edge shrinks toward zero. The evidence:
  - QuantaAlpha v1 → v3 fell from 27.75% to 4.68% ARR.
  - In AutoScientist-Quant's re-run, AlphaAgent and QuantaAlpha were negative.
  - In Gençay's study, 0 of all LLM strategies were certified.
- For a platform whose development window starts in 2015 and whose check window starts in 2020-10, almost the entire period predates current frontier LLM cutoffs. Any LLM researcher must therefore be treated as able to "remember" Taiwan market history. Only post-cutoff live (forward) records would be memorization-free.
- Applying Gençay's arithmetic (t ≈ SR·√years) to a ~6-year check window (2020-10 to 2026-10): only rules with a true Sharpe of about 0.8 or more could reach t ≈ 2, even before deflating for the number of trials an AI researcher would generate.

### Gaps
- Pre-fix versus post-fix numbers for the same method were not found, so the size of the bug's effect cannot be isolated from the change of window.
- No study was found applying White's Reality Check, Hansen's SPA or Holm to LLM alpha-mining agents on Chinese or Taiwan data.
- The magnitude of memorization effects in Look-Ahead-Bench was not retrieved.

## Q3. Comparisons with non-LLM baselines (GP, RL formula search, LightGBM on Alpha158/Alpha360 in Qlib)

### Takeaway
In their own papers, LLM agents beat Qlib's Alpha158/Alpha360 + LightGBM and the GP/RL formula searchers. Margins are small in IC terms (about +0.01 to +0.02) and large only in ARR, which is the most fragile metric. Plain deep-learning baselines (TRA, LSTM) often match or beat the LLM systems on the same table. Under independent re-evaluation, the advantage over Alpha158 + LightGBM is a few percentage points of ARR at most.

### Cited Findings
- **R&D-Agent(Q) paper (CSI300, 2017–2020):** IC 0.0532 vs Alpha158 0.0341, Alpha360 0.0420, LightGBM 0.0277; ARR 14.21% vs 5.70% / 4.38% / 3.97% — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- **AlphaAgent paper (CSI500, 2021–2024):** AlphaAgent IC 0.0212 vs TRA 0.0198, LSTM 0.0175, AlphaForge 0.0146, LightGBM 0.0120. In annual excess return, LSTM (4.96%) beat every LLM baseline except AlphaAgent — [arXiv 2502.16789v2](https://arxiv.org/html/2502.16789v2)
- **QuantaAlpha v3 (CSI300, 2022–2025):** QuantaAlpha IC 0.0472 vs TRA 0.0421. On ARR, TRA 6.81% and LSTM 6.01% beat QuantaAlpha's 4.68% — [arXiv 2602.07085 (HTML v3)](https://arxiv.org/html/2602.07085)
- **AutoScientist-Quant (CSI300, test 2023-06 to 2026-05):** LightGBM on Alpha158 ARR -0.1%. The best LLM system reached +3.5% only when its factors were added on top of Alpha158; with custom factors alone it reached 1.8% — [arXiv 2608.28632](https://arxiv.org/html/2608.28632)
- **AlphaForge (non-LLM, AAAI 2025):** CSI300 baselines DSO IC 2.55%, RL (AlphaGen) 2.09%, GP 1.29% — [arXiv 2406.18394](https://arxiv.org/html/2406.18394v5)
- **AlphaAgent decay comparison:** Alpha158, GP and RSI factors decayed from IC 0.022–0.036 to near zero over 2021–2024. The evidence is shown in a figure only — [arXiv 2502.16789v2](https://arxiv.org/html/2502.16789v2)
- **Gençay (US):** the simple time-series-momentum baseline had an evaluation Sharpe of 0.49, and passive SPY 0.85. Both were better than any LLM-discovered strategy — [arXiv 2608.27734](https://arxiv.org/html/2608.27734)

### Inferences
- The incremental value of an LLM researcher over a well-tuned conventional pipeline (a standard factor library + GBDT, or a small deep model) is unproven once the evaluation is cleaned up. Where LLM factors help, the best-documented use is as additions to an existing library (AutoScientist-Quant's "+Alpha158" rows), not as replacements.
- None of these comparisons uses a "same cash flow into an index ETF" benchmark like the platform's 0050 dollar-cost-averaging comparison. Most report excess return over the index with top-50 daily rebalancing at Chinese A-share costs, which do not transfer directly to Taiwan (0.1425% commission, 0.3% sell tax).

### Gaps
- No head-to-head of LLM agents against a modern GP with the same compute budget and the same multiple-testing treatment was found.
- No comparison on Taiwan data was found.

## Q4. Practical architecture: propose → code → sandboxed backtest → feedback; budgets, human-in-the-loop, result registry

### Takeaway
The converged architecture has five parts:
- a hypothesis or idea agent;
- a coding agent with a self-debug loop and a knowledge base of past code and feedback;
- a sandboxed, standardized backtest (Qlib inside Docker);
- an evaluator that writes feedback and decides SOTA updates;
- a scheduler (bandit, MCTS, evolutionary, or budget controller).

Production users add an investment-committee gate and the same thresholds for human and AI ideas. The 2026 best practice adds a tool registry that makes lookahead inexpressible, plus a trial ledger feeding DSR/PBO.

### Cited Findings
- **R&D-Agent(Q) units:**
  - Specification: background, data interface, output format, execution environment.
  - Synthesis: hypotheses from history and SOTA, organized as an "idea forest".
  - Implementation (Co-STEER): a DAG task scheduler plus a knowledge base of task-code-feedback records.
  - Validation: a redundancy filter followed by the Qlib backtest.
  - Analysis: feedback to synthesis.
  - Scheduler: a two-armed contextual Thompson-sampling bandit choosing between factor and model, using an 8-dimensional performance vector — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
- **RD-Agent repository:**
  - Scenarios: `fin_quant` (joint factor and model), `fin_factor`, `fin_model`, `fin_factor_report` (factors extracted from research reports).
  - Requires Docker without sudo and a conda environment, and "currently only supports Linux".
  - Uses LiteLLM backends (OpenAI, Azure, DeepSeek) and needs JSON mode and embeddings.
  - Traces go to `./git_ignore_folder/traces` and are viewed in a Streamlit or Flask UI.
  - MIT license, with a disclaimer that it is not ready for investment use — [GitHub microsoft/RD-Agent](https://github.com/microsoft/RD-Agent)
- **Budgets:**
  - R&D-Agent(Q): 12 hours, 44 loops, under $10 — [arXiv 2505.15155v2](https://arxiv.org/html/2505.15155v2)
  - QuantaAlpha: about 20 hours and about 1.8M tokens per run — [arXiv 2602.07085](https://arxiv.org/html/2602.07085)
  - LLM+MCTS: $7.5–$74.4 per run — [arXiv 2505.11122v3](https://arxiv.org/html/2505.11122v3)
  - AutoScientist-Quant: one controller conditions every decision on the remaining global budget — [arXiv 2608.28632](https://arxiv.org/abs/2608.28632)
- **Feedback isolation:** AutoScientist-Quant keeps an agent-visible feedback window (2021-06 to 2023-06) disjoint from the held-out test window. AlphaDiverse limits research feedback to inner-period data and freezes the model before the outer-period test — [arXiv 2608.28632](https://arxiv.org/html/2608.28632); [arXiv 2609.29014](https://arxiv.org/abs/2609.29014)
- **Gençay's design recommendations:**
  - Only registry-validated, leakage-safe tools.
  - One evaluation entry point that logs every candidate to a ledger.
  - Structured errors so the agent can self-correct.
  - Report DSR, PBO and OOS confidence intervals for every result, with an "evaporation curve" of the deflation threshold against search size.
  - Long held-out windows.
  - Pre-registration where possible.
  - Passive and human baselines evaluated under the same instruments.
  - [arXiv 2608.27734](https://arxiv.org/html/2608.27734)
- **Man Group AlphaGPT (production example):**
  - Three agents plus an orchestrator: an "Idea Person" proposes hypotheses, an "Implementer" writes Python against proprietary databases, and an "Evaluator" runs statistical, risk and economic-rationale checks.
  - The full process is logged.
  - The Investment Committee reviews the hypothesis, rationale and results; technology teams do code review and tests.
  - The same thresholds apply to AI-generated and human-generated strategies.
  - Multiple testing ("p-hacking") is acknowledged as a risk that speed amplifies (article dated 2025-11-13) — [Man Group](https://www.man.com/insights/what-ai-can-do-for-alpha)
- **Man Group cost controls (secondary source):** after going live, monitoring compares live performance with research results and tracks expected decay. Token costs "add up quickly", and Man built dashboards to monitor research spend — [AI Street newsletter](https://aistreet.beehiiv.com/p/inside-man-group-s-alphagpt)
- **Alpha-GPT 2.0's agent toolset:** alpha computation, backtesting, algorithmic search enhancement, deployment and alpha-base maintenance, with human checkpoints — [arXiv 2402.09746](https://arxiv.org/abs/2402.09746)

### Inferences
- A redesigned "AI researcher" for the Taiwan platform maps naturally onto pieces that already exist there: daily research design, trial registration, a one-shot final validation, and factor-strength analysis. The key additions suggested by the literature are:
  - a closed tool registry so the LLM cannot write code that reads future data;
  - every LLM proposal counted as a registered trial, with DSR/PBO computed over the ledger;
  - the LLM never seeing the check or final window's results;
  - the LLM's role framed as generating economically motivated, diverse hypotheses for factor families (pre-registered), not as optimizing backtests.
- Given the low dollar cost per run, the scarce budget to manage is the trial count against the finite clean OOS window, not tokens.

### Gaps
- No system was found that documents a full research registry schema (fields, versioning, data fingerprints) beyond Gençay's trial ledger and RD-Agent's traces.
- Human-in-the-loop effort, such as review hours per accepted factor, is not quantified anywhere.

## Q5. Evidence of production use by funds, and application to Taiwan or emerging markets

### Takeaway
Production evidence is limited to qualitative statements. Man Group's Man Numeric uses an internal "AlphaGPT" whose signals pass the same investment-committee gates as human signals, and press reports say several dozen signals were approved for live trading. No live performance numbers are disclosed. Taiwan-specific evidence is essentially one 2025 master's thesis with no out-of-sample test. All academic evidence is on China A-shares and US large caps.

### Cited Findings
- **Man Group:**
  - Man Numeric uses an agentic system that generates, codes and backtests strategies. Per senior PM Ziang Fang, "several dozen" signals have been approved for live trading. He says he "wouldn't call it autopilot", and hallucinations and inconsistent outputs persist. Hedgeweek attributes this account to Bloomberg — [Hedgeweek](https://hedgeweek.com/news/man-group-deploys-agentic-ai-for-quant-signal-discovery)
  - Man's own 2025-11-13 article says the system "has produced signals that meet our standards" and "still can't totally outperform humans in idea generation". It discloses no performance or cost figures — [Man Group](https://www.man.com/insights/what-ai-can-do-for-alpha)
  - Claims that AlphaGPT "tests hundreds [of ideas] in a week" come from marketing-style secondary content and are unverified — [Youmind post](https://youmind.com/landing/x-viral-articles/hedge-fund-ai-quant-trading)
- **AlphaForge:** claims real-money investment results in its abstract. The ~21.68% excess over CSI500 in about 9 months comes only from a secondary summary — [alphaXiv overview](https://alphaxiv.org/overview/2406.18394v5)
- **QuantEvolve:** authored at Qraft Technologies, an asset-management technology firm. The paper is research; no production deployment was confirmed — [arXiv 2510.18569](https://arxiv.org/pdf/2510.18569)
- **Self-published GitHub project:** reports a "deployed" LLM factor system whose single best factor had a walk-forward 2021-01 to 2025-08 net CAGR of 13.83%, Sharpe 1.55 and MDD -4.25% at 5 bp one-way cost. It is a single selected factor with no multiple-testing correction, and it is not peer reviewed — [GitHub Jing-Lavinia](https://github.com/Jing-Lavinia/LLM-Driven-Full-Stack-Alpha-Mining)
- **Taiwan (NCU master's thesis, 2025-07):**
  - Huang Chun-He (黃浚赫), National Central University, Department of Information Management.
  - Claude 3.7 Sonnet generated 30 economically motivated factors through staged prompting.
  - 14 factors were "effective" in Taiwan and 19 in the US; LLM factors worked better in the US overall.
  - The VACF factor's Taiwan return went from -5.37% (basic Q1 strategy) to 12.90% after quantile optimization and 20.12% after adding Bollinger Bands.
  - No data period and no out-of-sample test are stated — [NCU repository](https://ir.lib.ncu.edu.tw/handle/987654321/98222)
- The 2026-08 agentic-trading survey reports that signal discovery dominates current work and that live evaluation is rare — [arXiv 2608.31041](https://arxiv.org/abs/2608.31041)

### Inferences
- The one credible production case (Man Group) uses the LLM as a fast junior researcher inside an existing, heavily gated research process with live-versus-backtest decay monitoring. It is not an autonomous alpha machine. That matches the platform's stance of "AI as researcher, not trader".
- The Taiwan thesis shows the typical failure pattern Q2 warns about: post-hoc optimization (quantile choice, adding Bollinger Bands) on the same data, with no held-out test. It does not show that LLM factors work in Taiwan.
- Overall, a Taiwan redesign would be a research bet without external precedent. The literature supports its value mainly as a hypothesis generator and code accelerator under strict registry, leakage and deflation controls. It does not support it as a source of reliably positive excess return. A reasonable success criterion is "produces diverse, pre-registered factor hypotheses that survive DSR on the 2020-10+ check window and forward tracking", not a backtest ARR target.

### Gaps
- No public live track record (returns, decay) of any LLM-researched signal was found, whether from Man Group or from academic authors.
- No peer-reviewed study of LLM factor mining on TWSE/TPEx data, or on other emerging markets beyond mainland China, was found.
- Whether the Taiwan thesis used TWSE, TPEx or both, and over which period, is not stated on the repository page.
