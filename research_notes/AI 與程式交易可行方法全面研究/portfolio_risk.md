# Portfolio construction, strategy ensembles, sizing, regimes and robust evaluation: turning 17 long-only "T0" rules into one low-turnover account

Scope note for the report writer: the target is a small long-only Taiwan stock account (NT$300k start + NT$10k on the 5th of each month, no leverage, no shorting, no ETFs/0050 inside; 0050 with the same cash flow is only the benchmark). Today there are 17 "T0 candidate" rules, each holding 20 stocks and placing 10–40 orders a month. Every finding below has a source. My own reasoning and calculations are kept under "Inferences". Several numbers come from abstracts or secondary summaries rather than full papers; those are marked.

## Q1. How should many strategies or signals be combined: equal weight, inverse-vol, risk parity, HRP, minimum variance or shrunk mean-variance across sleeves, or one merged signal? And how to net overlapping holdings?

### Takeaway
With 17 sleeves and only about 5–11 years of usable daily history (2015-06 onward), estimation error dominates any optimiser. Equal weight (1/N), or a simple risk-based scheme (inverse-vol or HRP over clusters of rules), is the defensible way to weight sleeves; sample mean-variance would need centuries of data to beat 1/N. For this account the bigger lever is where the rules are combined, not how the sleeves are weighted. Merging the 17 rules into one consensus score or target and trading a single netted book removes offsetting trades. Running them as separate sleeves is why the current equal-share blend (#2189) places 437 orders a month.

### Cited Findings
- **Project baseline.** Run #2189 put all 17 T0 candidates in equal shares in one account. It reached NT$17.35M with a −30.1% maximum drawdown and qualified as a T0 candidate, but it places 437 orders a month and its fees plus taxes came to "112%". The project marked it impractical. — [DEVELOPMENT_HISTORY.md, 2026-10-10 entry](C:\Users\皮咪\Project\StockResearch\DEVELOPMENT_HISTORY.md)
- **Earlier project statistics.** On 2026-10-03 the project's own statistics over 132 distinct rule settings gave a best deflated Sharpe ratio (DSR) of 0.17 and a probability of backtest overfitting (PBO) of 0.12. Seven settings passed the window gates and none passed every gate. — [DEVELOPMENT_HISTORY.md, 2026-10-03 entry](C:\Users\皮咪\Project\StockResearch\DEVELOPMENT_HISTORY.md)
- **DeMiguel, Garlappi & Uppal (2009, RFS 22(5):1915–1953).** They tested 14 optimising models on 7 datasets. None was consistently better than 1/N on Sharpe ratio, certainty-equivalent return or turnover. — [IDEAS/RePEc](https://ideas.repec.org/a/oup/rfinst/v22y2009i5p1915-1953.html)
- **How much data mean-variance needs to beat 1/N.** Calibrated to US equity data, the required estimation window is about 3,000 months for 25 assets and about 6,000 months for 50 assets (roughly 250 and 500 years). — [London Business School summary](https://london.edu/faculty-and-research/academic-research/o/optimal-versus-naive-diversification-how-inefficient-is-the-1n-portfolio-strategy-p7805). An earlier working-paper version gives about 50 years for 4 assets at 20% idiosyncratic volatility and about 500 years at 10%. That is a different specification from the published one. — [Stanford OR seminar abstract](https://or.stanford.edu/seminars/demiguel_111105.html)
- **What drives the result (replication).** In an unpublished student thesis using 1990–2025 data, whether optimisers beat 1/N depended on M/N: the estimation-window length divided by the number of assets. — [Univ. of Pavia thesis](https://unitesi.unipv.it/handle/20.500.14239/34894)
- **HRP, López de Prado (2016, JPM), "Building Diversified Portfolios that Outperform Out-of-Sample".** In Monte Carlo experiments, hierarchical risk parity (HRP) had lower out-of-sample variance than Markowitz's critical line algorithm (CLA), even though minimum variance is CLA's own objective. HRP was also less risky out of sample than traditional risk parity. — [SSRN 2708678](https://papers.ssrn.com/abstract=2708678)
- **HRP replication.** A 2022 Copenhagen Business School master's thesis reproduced the result. In its simulations HRP's out-of-sample variance was below minimum-variance, equal-risk-contribution, inverse-variance and equal-weight portfolios. The thesis also reports that HRP was less robust in some respects. — [CBS thesis](https://research-api.cbs.dk/ws/portalfiles/portal/76452070/1332322_Master_Thesis_Hierarchical_Risk_Parity.pdf)
- **Context for optimisers.** Naive equal weighting has frequently beaten both mean-variance and risk-based optimisers out of sample (citing DeMiguel et al. 2009). — [Wikipedia: Hierarchical Risk Parity](https://en.wikipedia.org/wiki/Hierarchical_Risk_Parity)
- **Signal blending vs portfolio blending: Ghayur, Heaney & Platt (2018, FAJ 74(3)).** Their tests matched factor exposure across the two methods.
  - At low to moderate tracking error, portfolio blending (mixing sleeves) had higher information ratios. At high tracking error, signal blending (one composite score) did better.
  - A summary puts the crossover at an average factor exposure of about 0.9.
  - The authors say this challenges the earlier consensus that favoured signal blending.
  - Sources: [CFA Institute / FAJ](https://rpc.cfainstitute.org/research/financial-analysts-journal/2018/faj-v74-n3-5); [Alpha Architect summary](https://alphaarchitect.com/constructing-long-only-multifactor-strategies-portfolio-blending-vs-signal-blending/)
- **Case for one combined signal.** Polbennikov, Desclée & Dubois (fixed income) found that a portfolio optimised on a 50/50 blend of value and momentum signals performed significantly better than a 50/50 blend of two separately optimised portfolios. Combining signals first also avoids offsetting trades in the same name across sleeves. — [State Street GA note](https://ssga.com/us/en/intermediary/insights/constructing-and-implementing-a-safi-portfolio)
- **Credit markets.** Blonk & Messow conclude that an integrated (signal-blended) approach is probably better for credit. — [Quantpedia](https://vvv.quantpedia.com/?p=34554)
- **Differences can be small.** In Alpha Architect's value-plus-momentum test, separate and combined factor portfolios had very similar Sharpe ratios. — [Alpha Architect](https://alphaarchitect.com/value-and-momentum-investing-combine-or-separate/)
- **Combining signals needs independence.** Combining multiple signals raises the information ratio only if the signals are sufficiently independent. — [CFA-oriented note (secondary)](https://www.pastpaperhero.com/resources/cfa-level3-portfolio-construction-and-pm-process-alpha-research-signals-and-ic)

### Inferences
- **Sleeve weighting is a second-order choice here.** All 17 rules are long-only Taiwan stock portfolios, so their returns share market beta and are probably highly correlated. Choosing between 1/N, inverse-vol and HRP will then change the result much less than choosing where to combine. The correlations should be measured from the daily equity curves before spending effort on optimisers.
- **Where HRP adds value.** HRP's useful part is the clustering step. Group the rules by the family of their drivers (price/technical, chips/margin, fundamental, event, model-score). Give each cluster equal weight, then equal weight within each cluster. This stops five near-duplicate momentum rules from carrying 5/17 of the risk. It also matches the project rule against near-identical variants.
- **Signal-level merge (recommended).** Each day, for each rule, convert its score into a cross-sectional percentile, or a 0/1 "would hold" vote. The consensus score is the mean percentile or the vote count across rules or clusters. Hold the top K names (K roughly 20–30 for a NT$300k account) under a buy/hold band (see Q2). The vote count automatically nets out a name that one rule adds while another drops it.
- **Why Ghayur et al. points the same way.** Their crossover implies that signal blending wins when the account is meant to carry strong exposures. That describes a concentrated 20–30 stock account, not a low-tracking-error index tilt.
- **Netting with sleeves kept.** If the sleeves stay separate, aggregate the sleeves' target weights into one net target per stock and trade only the net difference from current holdings. Same-day sells in one sleeve and buys of the same stock in another then cancel. The size of the saving can only be measured by re-running #2189 with netting.
- **Constraint check.** None of these methods needs 0050 or ETFs inside the account, so they are consistent with the "100% individual stocks" rule.

### Gaps
- I did not retrieve HRP's exact out-of-sample variance figures (I recall roughly CLA 0.116, inverse-variance 0.093, HRP 0.067 in López de Prado's Monte Carlo, but did not verify this).
- I found no source quantifying the Ledoit–Wolf covariance shrinkage gain for weighting sleeves.
- I did not fetch AQR's "Long-Only Style Investing: Don't Just Mix, Integrate" (Fitzgibbons et al. 2017, JPM) or the Riskfolio-Lib/PyPortfolioOpt documentation.
- No source measures how much netting overlapping sleeve holdings cuts the order count. It has to be measured on this project's #2189 data.

## Q2. How can turnover and order count be reduced: no-trade bands, partial rebalancing, rebalancing frequency, Gârleanu–Pedersen, cost-aware optimisers, minimum trade sizes? How much net return do they save?

### Takeaway
The best-documented simple tool is a buy/hold rank spread: a strict threshold to enter a stock and a looser one to keep it. Novy-Marx & Velikov call it the single most effective simple cost-mitigation technique, and they find most anomalies with one-sided monthly turnover below 50% survive costs when designed this way. Gârleanu & Pedersen formalise "trade only part of the way toward an aim portfolio that over-weights slow-decaying signals". In their 2009 draft the net Sharpe ratio was about 20% higher than the best static rule (the published magnitude was not verified). For a NT$300k account the binding limit is order count, plus any fixed minimum fee per order, more than basis points of spread.

### Cited Findings
- **Novy-Marx & Velikov (2016, RFS 29(1):104–147), "A Taxonomy of Anomalies and Their Trading Costs".**
  - A buy/hold spread lets investors keep stocks they would not buy fresh. It is "the single most effective simple cost mitigation strategy".
  - Most anomalies with one-sided monthly turnover below 50% still earn statistically significant net spreads when designed to mitigate costs. Few strategies with higher turnover do.
  - Trading costs always reduce both profitability and statistical significance.
  - Capacity is inversely related to turnover.
  - Sources: [NBER w20721](https://www.nber.org/papers/w20721); [author PDF](https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf)
- **How those costs were measured.** Novy-Marx & Velikov use Hasbrouck's (2009) effective bid-ask spread across 23 strategies. This ignores price impact, so it is the cost a small liquidity demander faces. — [Alpha Architect summary](https://alphaarchitect.com/trading-costs-destroy-factor-investing/)
- **Equal-weighted strategies.** An unofficial summary says trading costs hurt equal-weighted strategies most, because they lean toward small, illiquid stocks. Not confirmed against the paper. — [Scribd summary (unverified)](https://www.scribd.com/document/411644500/Polya)
- **Gârleanu & Pedersen (2013, JF 68(6):2309–2340), "Dynamic Trading with Predictable Returns and Transaction Costs".**
  - The optimal new portfolio is a linear combination of the current portfolio and an "aim portfolio".
  - The aim portfolio is a weighted average of the current Markowitz portfolio and the expected Markowitz portfolios on all future dates.
  - Predictors with slower alpha decay get more weight in the aim.
  - Their two principles: "aim in front of the target" and "trade partially towards the current aim".
  - Sources: [NBER w15205](https://www.nber.org/papers/w15205); [NBER PDF](https://www.nber.org/system/files/working_papers/w15205/w15205.pdf)
- **Gârleanu & Pedersen empirical test.** On commodity futures, with 5-day, 12-month and 5-year return signals, their policy had the best net-of-cost performance of all strategies considered. In the 2009 draft its net Sharpe ratio was about 20% higher than the best static strategy's. The published figure may differ. — [NBER PDF](https://www.nber.org/system/files/working_papers/w15205/w15205.pdf)
- **Copenhagen thesis follow-ups (two University of Copenhagen master's thesis listings).**
  - One supports the dynamic policy in-sample on 2009–2024 stock-index data when costs are high.
  - Another finds that out of sample the model builds large positions too aggressively, and that adding constraints improved net performance.
  - These are student theses. [KU listing 1](https://www.math.ku.dk/english/calendar/specialeforsvar/speciale-mdsten); [KU listing 2](https://www.math.ku.dk/english/calendar/events/speciale-plk/)
- **Netting sleeves.** Combining signals first and building one portfolio avoids offsetting trades across sleeves. — [State Street GA](https://ssga.com/us/en/intermediary/insights/constructing-and-implementing-a-safi-portfolio)
- **Project data point.** The 17-sleeve equal-share blend places 437 orders a month, with fees and taxes of "112%". Individual rules place 10–40 a month. — [DEVELOPMENT_HISTORY.md](C:\Users\皮咪\Project\StockResearch\DEVELOPMENT_HISTORY.md)

### Inferences
- **Concrete design for this account.** Enter when the consensus rank is in the top K (for example 20). Exit only when the rank falls below about 2K–3K, or when a hard sell rule fires (a stop, or a listing or delisting event). This is Novy-Marx & Velikov's buy/hold spread.
  - The ratio of exit rank to entry rank is the main turnover dial. It should be chosen once on the 2015-06 to 2020-09 development data, not swept in fine steps (the project rule against "only differs by a parameter" variants).
- **Partial trading (Gârleanu–Pedersen style) without an optimiser.**
  - Move a fraction λ of the way toward the target each rebalance, or only when the drift exceeds a band.
  - For a 20–30 stock equal-weight book, the simpler equivalent is: never resize a held position unless its weight drifts outside a band (for example ±50% of its target weight), and buy or sell only whole names.
  - Weight slow signals (fundamentals, chips/holdings trends) more than fast ones (5-day reversal) in the consensus score, as the aim-portfolio logic implies.
- **Use the monthly NT$10,000 as the rebalancing flow.** Each month, buy the most underweight or newly entered names with new cash instead of selling overweights. This cuts sell orders, and in Taiwan sells carry the transaction tax. This is standard practice; I found no fetched source for its magnitude.
- **Minimum trade size.** For a NT$300k account with 20 names, a position is about NT$15k. Any fixed minimum commission per order makes small trim and top-up orders very expensive in percentage terms. Skip any trade below a minimum notional (for example NT$3–5k) or below a weight change of about 1–2% of the account. Broker minimum-fee and odd-lot details were not verified here.
- **Order budget as a constraint.** Treat orders per month as an explicit gate: one merged book of 20–30 names with buy/hold bands should land near the single-rule range of 10–40 orders a month, not 437. Re-running #2189 as a single netted, banded book is the direct test.
- **Turnover target.** Novy-Marx & Velikov's survival line was one-sided monthly turnover below 50%. A practical target here is consensus-book one-sided turnover of roughly 20–40% a month.

### Gaps
- I did not retrieve rebalancing-frequency or tolerance-band studies for equity portfolios (for example Vanguard's rebalancing research or Donohue & Yip 2003), so I have no fetched magnitudes for calendar versus threshold rebalancing.
- I have no fetched magnitude for buy/hold-spread savings in basis points per year (the paper's tables were not retrieved).
- Taiwan commission, minimum fee, odd-lot rules and the 0.3% sell-side securities transaction tax were not verified in this pass.
- The published Gârleanu–Pedersen net-Sharpe improvement was not verified.

## Q3. Labelling and sizing: triple-barrier labelling, meta-labelling, Kelly or fractional Kelly at strategy level, volatility targeting the whole account, CPPI and drawdown rules (Grossman–Zhou). What do they cost and when do they help long-only equity?

### Takeaway
In a long-only account with no leverage, every sizing overlay can only lower exposure by holding cash. Each one therefore trades expected return, and so terminal wealth against the 0050 dollar-cost-averaging benchmark, for smaller drawdowns.
- **Volatility targeting.** Harvey et al. (2018) find it raises equity Sharpe ratios through the leverage effect and shrinks left tails. But the real-time evidence is poor: Cederburg et al. (2020) find the volatility-managed version worse out of sample for 72 of 103 equity strategies. Conventional vol targeting even increased maximum drawdown in the UK, Canada, Australia and Hong Kong.
- **Momentum risk scaling** is the strongest case: Sharpe rose from 0.53 to 0.97 and crashes were largely removed.
- **Half Kelly** keeps about 75% of full-Kelly growth with half the volatility.
- **Drawdown floors** (Grossman–Zhou, CPPI) guarantee their limit only in continuous time with no costs, and they cost a lot of return in bull markets.
- **Meta-labelling** (a second model sizing or filtering a primary rule's trades) is principled but has thin published out-of-sample evidence. Calibrating its probabilities matters for fixed sizing rules.

### Cited Findings
- **Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018, JPM 45(1):14–33), "The Impact of Volatility Targeting".**
  - Higher Sharpe ratios for risk assets (equities, credit), which the authors attribute to the leverage effect. The effect on Sharpe ratios is negligible for bonds, currencies and commodities.
  - Vol targeting reduces the likelihood of extreme returns, and left-tail events are less severe.
  - It reduced maximum drawdowns for balanced and risk-parity portfolios.
  - Sources: [Man Group summary](https://www.man.com/insights/the-impact-of-volatility-targeting); [SSRN 3175538](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538)
- **Harvey et al. detail (secondary summary).** In US equities, the volatility of volatility falls from about 4.6% unscaled to about 1.8% scaled. — [Quantpedia summary](https://quantpedia.com/the-impact-of-volatility-targeting-on-equities-bonds-commodities-and-currencies)
- **Conditional volatility targeting (FAJ 2020).**
  - Conventional vol targeting increased maximum drawdown in the UK, Canada, Australia and Hong Kong SAR, and improved the Sharpe ratio in only 8 of 10 markets.
  - A conditional variant, which scales only in extreme volatility states, cut maximum drawdown in every equity market tested, by 6.6% on average.
  - Source: [CFA Institute / FAJ](https://rpc.cfainstitute.org/research/financial-analysts-journal/2020/conditional-volatility-targeting)
- **Cederburg, O'Doherty, Wang & Yan (2020, JFE), "On the Performance of Volatility-Managed Portfolios".**
  - Across 103 equity strategies, spanning regressions show positive alphas, but those strategies are not implementable in real time.
  - Feasible out-of-sample versions earn lower Sharpe ratios and certainty-equivalent returns. The cause is structural instability in the regressions.
  - Sources: [paper PDF](https://www.lehigh.edu/~xuy219/research/COWY.pdf); [Alpha Architect](https://alphaarchitect.com/the-performance-of-volatility-managed-portfolios/)
- **Cederburg et al., breadth of failure.** Volatility-managed versions underperformed out of sample for 72 of 103 strategies. — [CXO Advisory](https://www.cxoadvisory.com/?p=32455)
- **Cederburg et al., exceptions.** Momentum, ROE and BAB were the exceptions with less real-time degradation, and even these weakened when Fama–French factors were included. — [Alpha Architect](https://alphaarchitect.com/the-performance-of-volatility-managed-portfolios/)
- **Barroso & Santa-Clara (2015, JFE), "Momentum has its moments".** Scaling momentum by its own recent volatility "nearly erases crashes and about doubles the Sharpe ratio". — [UCP record](https://ciencia.ucp.pt/en/publications/momentum-has-its-moments/)
  - Secondary figures for 1927–2011: Sharpe ratio 0.53 → 0.97, excess kurtosis 18.2 → 2.7, skew −2.5 → −0.4. — [ETF.com (Swedroe)](https://www.etf.com/node/94292.md)
- **Global replication of risk-managed momentum (Univ. of Vaasa thesis, 1995–2019).** The Sharpe-ratio gain held in every region and subsample. Significant abnormal returns appeared in Europe and Asia-Pacific but not in North America or Japan. — [Vaasa thesis](https://osuva.uwasa.fi/handle/11111/8125)
- **Daniel & Moskowitz (2016, JFE), "Momentum Crashes" (1927–2013).**
  - Crashes happen in "panic" states: after market declines, when market volatility is high, and at the same time as market rebounds. They are partly forecastable.
  - After sell-offs, past losers' betas can exceed 3 while winners' fall below 0.5.
  - A dynamic version that scales momentum using forecasts of its mean and variance roughly doubles alpha and Sharpe ratio, and the result holds internationally.
  - Sources: [NBER w20439](https://www.nber.org/system/files/working_papers/w20439/w20439.pdf); [Chicago Booth Review](https://www.chicagobooth.edu/review/understanding-momentum-crashes)
- **Fractional Kelly.**
  - Long-run growth is maximised by maximising expected log wealth. The growth curve peaks at the Kelly fraction and over-betting is penalised more than under-betting.
  - Using growth ≈ μf − σ²f²/2: half Kelly gives about 75% of the maximum growth with volatility halved. At twice Kelly, excess growth falls to zero.
  - Under full Kelly, the chance that the account eventually halves is roughly one half.
  - Practitioners use ½ or ¼ Kelly when confidence in the parameters is low.
  - Sources: [LuxAlgo Kelly note (secondary)](https://www.luxalgo.com/library/concept/kelly-criterion/); [MacLean–Thorp–Ziemba related material](https://c.mql5.com/forextsd/forum/108/the_capital_growth_theory_of_investment_-_part_i.pdf)
- **Kelly and estimation error.**
  - The plug-in estimate of the Kelly portfolio is biased and overestimates the true Kelly portfolio when log returns are Gaussian (Han, Yu & Mathew).
  - Full Kelly using shrinkage estimates of returns is equivalent to fractional Kelly with full information (Rising & Wyner).
  - Over short horizons, full and high-fraction Kelly were very risky (MacLean, Thorp, Zhao & Ziemba 2011).
  - Sources: [arXiv 2503.17927](https://www.arxiv.org/pdf/2503.17927); [EFMA 2011 paper](https://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2011-Braga/papers/0103.pdf)
- **Grossman & Zhou (1993, Mathematical Finance 3(3):241–276).**
  - The constraint is that wealth must stay at or above α times its running maximum.
  - The optimal policy holds risky assets in proportion to the surplus W − αM. That is CPPI with a floor tied to the running peak.
  - Letting past drawdowns decay raises long-run growth but weakens control of absolute drawdown.
  - With transaction costs the policy is hard to implement near the limit, and it loses its growth-optimality in discrete time.
  - Sources: [IDEAS](https://ideas.repec.org/a/bla/mathfi/v3y1993i3p241-276.html); [arXiv 2609.23272](https://arxiv.org/pdf/2609.23272); ["The Grossman and Zhou investment strategy is not always optimal"](https://lup.lub.lu.se/record/223851)
- **Basic CPPI on SPY, 5 years from January 2015.** CPPI returned 3.92% a year against SPY's 11.39%, with maximum drawdown −11.55% against −19.35%. In 2008 the CPPI touched its floor but finished ahead of SPY. — [Quantpedia](https://quantpedia.com/introduction-to-cppi-constant-proportion-portfolio-insurance/)
- **200-day moving-average filter on the S&P 500.**
  - Test from 1960 (no dividends): CAGR 6.45% against 7% for buy-and-hold, maximum drawdown 28%. — [Quantified Strategies](https://quantifiedstrategies.substack.com/p/trend-following-strategy-in-s-and-33c)
  - Test 1999–2018 (switching into T-bills, by a product sponsor): 5.36% against 4.86%. — [ETF Trends / Pacer (promotional)](https://www.etftrends.com/trend-following-etf-strategy-reduce-risk-capture-long-term-returns/)
- **Meta-labelling.** Joubert, "Meta-Labeling: Calibration and Position Sizing" (JFDS): calibrating the secondary model's probabilities significantly improves fixed position-sizing rules. Sizing methods learned from training data gain no significant benefit from calibration. — [Wikipedia: Meta-Labeling](https://en.wikipedia.org/wiki/Meta-Labeling)
- **Bet sizing in López de Prado's *Advances in Financial Machine Learning* (2018).** The bet-sizing chapter covers sizing from predicted probabilities, averaging active bets, discretising size, and dynamic bet sizes with limit prices. — [Shortform summary](https://www.shortform.com/pdf/advances-in-financial-machine-learning-pdf-marcos-lopez-de-prado)

### Inferences
- **Vol targeting without leverage only sells.** With no leverage it reduces to holding cash when realised volatility exceeds a target. If the target is below the account's usual volatility, the account is under-invested most of the time. That is a direct drag against the always-invested 0050 benchmark. Vol targeting is only worth testing in the conditional, extreme-state-only form (Bongaerts et al. style), and only judged on terminal wealth versus 0050 and on DSR, not on drawdown alone. Hong Kong's worse drawdown under conventional vol targeting is the nearest Asian warning.
- **Momentum-family rules.** If several of the 17 rules are momentum or technical, scaling only that cluster by its own trailing volatility (Barroso–Santa-Clara), or cutting it after market declines with high volatility (Daniel–Moskowitz), has the strongest evidence of any overlay.
  - Caveat: both papers study long-short momentum, whose crashes come mainly from the short loser leg. A long-only winners book gets less benefit. This is my reasoning and should be tested.
- **Kelly is awkward at the strategy level here.** Rule-level mean returns are the most error-prone input: mean errors are much more damaging than covariance errors, per the Chopra & Ziemba result, which I recall but did not re-verify. The rules were also picked as the best of a large scan. If Kelly is used at all, only ¼ to ½ Kelly on shrunk, deflated return estimates is defensible. In a no-leverage account that mostly just sets the cash share.
- **Drawdown floors.** Grossman–Zhou/CPPI with α of about 0.7 (a 30% floor) would have de-risked in 2022-style drawdowns but gives up a lot of return in rebounds. The SPY CPPI example gave up about 7.5 percentage points a year for about 7.8 points less drawdown. For a 10-year dollar-cost-averaging account whose stated benchmark is 0050, this is likely a poor trade unless the user's drawdown tolerance requires it.
- **Triple-barrier labelling and meta-labelling (background from López de Prado 2018, not re-fetched).**
  - Each primary-rule entry is labelled by which barrier is hit first: profit-take, stop-loss, or a time limit.
  - A secondary classifier predicts the chance the primary trade succeeds, from features such as regime, liquidity or chips. Its output filters entries (skip when the probability is low) or sizes them.
  - For this account its best use is as a filter that reduces orders, not as a sizing engine.
  - It is a new trial and must be counted in DSR and PBO. Training also needs purged and embargoed cross-validation, because triple-barrier labels overlap in time.

### Gaps
- I did not retrieve the Harvey et al. (2018) equity-only drawdown table.
- I found no Taiwan-specific evidence for volatility targeting, CPPI or Kelly.
- I found no head-to-head out-of-sample comparison of Grossman–Zhou/CPPI against a 200-day filter on long-only stock portfolios with costs.
- I found no published out-of-sample magnitude for meta-labelling on equity stock selection; the Joubert paper's numbers were not retrieved.
- Primary sources for triple-barrier labelling were not fetched.

## Q4. Regime detection: hidden Markov models, breadth, index trend, volatility, macro. What out-of-sample value has been shown for switching between strategy families in equities? Any Taiwan or Asia evidence?

### Takeaway
Out-of-sample evidence for regime switching is much thinner than in-sample fits. Most hidden-Markov-model (HMM) studies switch between asset classes, not stock-selection families. The most robust documented regime effect for stock selection is state-dependent momentum: momentum works after up markets or persistent states, and fails or crashes after declines with high volatility and at turning points. Taiwan research says exactly this: momentum is weak overall, positive when the market state persists, reverses at transitions, and is more negative after DOWN markets than in the US. I found no Taiwan study of HMM-based switching between strategy families.

### Cited Findings
- **Kritzman, Page & Turkington (2012, FAJ 68(3):22–39), "Regime Shifts: Implications for Dynamic Strategies".**
  - They fit HMMs to the drivers of returns (equity turbulence, currency turbulence, inflation, growth), not to asset returns directly.
  - They backtested regime-dependent allocation out of sample, and dynamic allocation was significantly more effective than static allocation.
  - Sources: [IDEAS](https://ideas.repec.org/a/taf/ufajxx/v68y2012i3p22-39.html); [MPRA literature review](https://mpra.ub.uni-muenchen.de/121552/1/MPRA_paper_121552.pdf)
- **Few studies test regimes out of sample.** Many authors use Markov switching to fit data in-sample, but far fewer attempt out-of-sample forecasting. A two-state HMM on 40 years of US, Japan and Germany data beat buy-and-hold out of sample (authors not named in the snippet). — [MPRA review](https://mpra.ub.uni-muenchen.de/121552/1/MPRA_paper_121552.pdf)
- **Nystrup et al. (DTU thesis).** An adaptively estimated two-state Gaussian HMM beat a rebalancing strategy out of sample after transaction costs and a realistic implementation lag. This is asset allocation, not stock selection. — [DTU](https://www2.imm.dtu.dk/pubdb/pubs/6808-full.html)
- **Regime-switching factor investing with HMMs.** This paper tests regime detection on Fama–French, Carhart and AQR factor portfolios. Its out-of-sample results were not visible in what I retrieved. — [JRFM 13(12):311 mirror](https://0-www-mdpi-com.brum.beds.ac.uk/1911-8074/13/12/311)
- **Regime-switching factor model (Journal of Risk).** Regime-dependent means and covariances fed into mean-variance optimisation consistently beat competing portfolios in a long out-of-sample test (abstract only). — [Risk.net](https://risk.net/node/7535001)
- **Daniel & Moskowitz (2016).** Momentum crashes are partly forecastable from market declines plus high volatility. Scaling momentum dynamically roughly doubles its Sharpe ratio, and this holds across international equity markets. — [NBER w20439](https://www.nber.org/system/files/working_papers/w20439/w20439.pdf)
- **UK test of Cooper, Gutierrez & Hameed's market-state framework (IRFA 2014).** UK momentum returns were not related to market states defined that way. Sentiment indicators helped, but mainly because of the post-subprime period. — [White Rose eprints](https://eprints.whiterose.ac.uk/89930)
- **Taiwan, Lin, Ko, Feng & Yang (2016, Pacific-Basin Finance Journal), "Why is there no momentum in the Taiwan stock market?".**
  - Earlier work generally finds no momentum premium in Taiwan, which they attribute to frequent transitions between market states.
  - Momentum profits are significantly positive when the market stays in the same state, and reverse significantly during transitions.
  - Profits are concentrated in high-attention stocks.
  - Source: [NAU experts page](https://experts.nau.edu/en/publications/why-is-there-no-momentum-in-the-taiwan-stock-market/)
- **Taiwan, Du, Huang & Liao (2009, Journal of Economics and Business).** Using Cooper–Gutierrez–Hameed market states, DOWN markets are more frequent in Taiwan and momentum profits after DOWN markets are more negative than in the US. I matched the paper to the URL from search-result context only. — [Academia Sinica WP listing (verify)](https://ideas.repec.org/p/sin/wpaper/04-a007.html)
- **Taiwan, weekly data 1999–2008.** Momentum was positive and significant in up markets and negative but insignificant in down markets, with disposition and overconfidence effects appearing in different states. Authors were not identified. — [Airiti record](https://www.airitilibrary.com/Publication/Index/U0002-0206200923383100)
- **Taiwan volatility regimes, Shyu & Hsia (2008).** A switching-regime ARCH (SWARCH-L) model on Taiwan monthly returns fits best. The smoothed probabilities of the medium- and high-volatility regimes lead the coincident economic index. — [Inderscience](https://www.inderscience.com/offers.php?id=21804); [IDEAS](https://ideas.repec.org/a/ids/ijelfi/v2y2008i4p433-450.html)
- **Asia-Pacific risk-managed momentum.** Risk-managed momentum produced significant abnormal returns in Asia-Pacific (but not Japan) over 1995–2019. — [Vaasa thesis](https://osuva.uwasa.fi/handle/11111/8125)

### Inferences
- **Simplest regime split worth one trial for Taiwan.**
  - Classify the market each day using only information available that day: (a) the TAIEX total-return index above or below its 12-month level or 200-day average, (b) whether its trailing 1-month volatility is above its own 80th–90th percentile, and (c) whether the state changed recently.
  - In UP or persistent states, give more weight to momentum/technical rules in the consensus score. In DOWN, transition or high-volatility states, give more weight to defensive and fundamental (quality/value/low-volatility) and chip-based rules.
  - This is supported by the Taiwan state-dependence papers and by Daniel–Moskowitz. It is not proven out of sample for a rule ensemble.
- **Weight shifts, not switches.** Implement the regime view as a tilt in rule or cluster weights (for example 50/50 moving to 70/30), not a hard switch. Hard switches create whipsaw turnover that undoes the Q2 savings, and the evidence for clean regime timing out of sample is weak.
- **HMM.** A two-state Gaussian HMM on TAIEX daily returns and volatility, re-estimated only on past data, is the standard version. Evidence of value for switching stock-selection families is indirect (asset-allocation papers only), so it should count as one more trial, not a tuned family.
- **Breadth and macro.** I found no fetched evidence on market breadth or interest rates as switchers for Taiwan stock-selection families, so these have the lowest priority.

### Gaps
- I did not retrieve the original Cooper, Gutierrez & Hameed (2004, JF) paper or Ang & Bekaert's regime-switching work.
- I found no out-of-sample study of switching between momentum and defensive stock-selection families in Taiwan or Asia, and no fetched evidence on market breadth, rate or macro regimes for Taiwan stock selection.
- The Du/Huang/Liao link should be verified.

## Q5. How should the combined account be evaluated robustly (combinatorial purged cross-validation, deflated Sharpe ratio, probability of backtest overfitting) when its components were themselves picked from a large scan?

### Takeaway
The 17 T0 rules were chosen from scans of 39,676 candidates, and a four-signal scan screened 329,004 in its first stage. Any account built from them inherits that selection bias, and every combination choice (weighting scheme, band width, regime tilt) is a further trial.
- **Deflated Sharpe ratio (DSR).** Use the full trial count, or the effective number of independent trials estimated by clustering correlated trials.
- **Probability of backtest overfitting (PBO).** Compute it by combinatorially symmetric cross-validation (CSCV) on the whole candidate matrix, not just the 17 survivors.
- **Combinatorial purged cross-validation (CPCV).** Use it for a distribution of backtest paths when the account includes trained models such as gbm or meta-labels.
- **Final check.** Spend the untouched 2020-10+ final validation period once, on the single combined design.

### Cited Findings
- **Scan sizes.** The broad two-stage scan covered 39,676 candidates. The four-signal scan screened 329,004 in its first stage; its second stage was interrupted and later resumed, giving 13,575 that passed the quick screen, 12 full backtests and 3 T0 candidates. — [git log 8e1c3c4, "Broad two-stage strategy scan: 39,676 candidates"](C:\Users\皮咪\Project\StockResearch\DEVELOPMENT_HISTORY.md); [DEVELOPMENT_HISTORY.md, 2026-10-10](C:\Users\皮咪\Project\StockResearch\DEVELOPMENT_HISTORY.md)
- **Deflated Sharpe ratio, Bailey & López de Prado (2014, JPM 40(5):94–107).**
  - The DSR corrects a Sharpe ratio for selection bias across many trials, for backtest overfitting, and for non-normal returns.
  - It is the probabilistic Sharpe ratio evaluated at a hurdle SR* equal to the expected maximum Sharpe ratio of N unskilled trials.
  - E[max SR] ≈ μ + σ·[(1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e))], where γ ≈ 0.5772.
  - The standard error is adjusted for skewness and kurtosis.
  - Sources: [SSRN 2460551](https://papers.ssrn.com/abstract=2460551); [Wikipedia: Deflated Sharpe ratio](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio)
- **Counting trials for the DSR.**
  - The DSR's honesty depends on counting N honestly. Correlated trials lower the effective N, which López de Prado estimates by clustering (the Optimal Number of Clusters algorithm) or from the eigenvalues of the correlation matrix. — [Wikipedia: Deflated Sharpe ratio](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio)
  - An illustrative blog example: going from 1 to 10 trials lets pure noise reach a Sharpe ratio of 0.48, and going from 100 to 1,000 adds about another 0.24. A highly correlated 275-variant sweep worked out to about 10 effective trials. — [Quantt blog (secondary)](https://www.quantt.co.uk/resources/sharpe-ratio-of-pure-noise)
- **Probability of backtest overfitting, Bailey, Borwein, López de Prado & Zhu (J. Computational Finance 20(4), 2017).**
  - PBO is estimated with combinatorially symmetric cross-validation (CSCV), a model-free, non-parametric method.
  - The authors argue that standard hold-out methods are unreliable for investment backtests.
  - Sources: [SSRN 2326253](https://papers.ssrn.com/abstract=2326253); [Risk.net](https://www.risk.net/journal-of-computational-finance/2471206/the-probability-of-backtest-overfitting)
- **Ready-made PBO tool.** The R package `pbo` implements CSCV and reports PBO, performance degradation, probability of loss and stochastic dominance. — [CRAN pbo README](https://cran.hafro.is/web/packages/pbo/readme/README.html)
- **PBO only sees the trials it is given.** Leaving failed experiments out of the matrix biases PBO downward. It complements, but does not replace, walk-forward and genuine forward testing. — [LuxAlgo PBO note (secondary)](https://www.luxalgo.com/library/concept/probability-of-backtest-overfitting/)
- **Combinatorial purged cross-validation (CPCV).**
  - The data is split into contiguous groups. For every combination of test groups, the training set is purged and embargoed around the test period.
  - The output is a distribution of Sharpe ratios and drawdowns rather than one path, at a high computational cost. A single walk-forward run cannot show how sensitive its result is to the chosen split.
  - Sources: [Wikipedia: Purged cross-validation](https://en.wikipedia.org/wiki/Purged_cross-validation); [fynance CPCV docs](https://fynance.readthedocs.io/en/latest/generated/fynance.data.combinatorial_purged_cv.html)

### Inferences
- **How high the bar is (my calculation, DSR formula, μ = 0).** The expected maximum standardised Sharpe ratio from luck alone is:

  | Trials (N) | Expected max, in units of the cross-trial Sharpe std. dev. |
  |---|---|
  | 17 | ≈ 1.83 |
  | 132 | ≈ 2.63 |
  | 39,676 | ≈ 4.18 |
  | 329,004 | ≈ 4.64 |

  - The standard error of an annualised Sharpe ratio is about √(1/T). Over the 2015-06 to 2020-09 development window (T ≈ 5.3 years) that is about 0.43.
  - So the best of about 40k independent zero-skill rules would show an annualised Sharpe ratio near 1.8 from luck alone.
  - Correlation between candidates lowers the effective N a lot, so the DSR has to be computed with a clustered effective N, not the raw count.
- **Choose the combination method before looking.** Pick the combination method (for example cluster-equal-weight consensus with a buy/hold band) a priori, from the literature above. Treat it as a single new trial and log it in the registry. Avoid trying several combination variants on the final period.
- **PBO for the ensemble.** Run CSCV on the matrix of daily account returns for all distinct candidates that reached full backtest, plus the ensemble variants tried, rather than only the 17 survivors. Otherwise PBO is biased downward. The project's earlier PBO of 0.12 was over 132 settings and does not cover the scans.
- **CPCV and purging.** If gbm models, meta-labels or regime HMMs are trained, use CPCV with purging and an embargo of at least the label horizon. Report the distribution of path Sharpe ratios and terminal wealth relative to 0050 under the same cash flow.
- **CPCV path count.** From López de Prado (2018), as I recall it: the number of backtest paths is (k/N)·C(N,k), so N = 6 and k = 2 give 15 splits and 5 paths. One secondary page conflates splits and paths, so verify against the book.
- **Account-level gates.** Beyond Sharpe, gate the combined account on: orders per month (for example ≤ 40), one-sided turnover, fees and taxes as a share of contributions, maximum drawdown, and terminal wealth relative to 0050 DCA in both the development period and the final period.

### Gaps
- I did not fetch the original CSCV algorithm details or the CPCV path formula from primary text; the CSCV description and path count above are background knowledge.
- I found no published study applying PBO or DSR to an ensemble whose components were selected from a large scan. The correct trial accounting for such two-level selection is my inference, not a cited result.
- I did not retrieve Harvey & Liu's "Backtesting" haircut or Harvey, Liu & Zhu's t > 3 hurdle in this pass.
