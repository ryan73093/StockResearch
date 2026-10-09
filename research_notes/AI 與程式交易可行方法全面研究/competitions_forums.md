# Public stock-prediction competitions and practitioner communities: what wins on future data, and what survives live trading

Scope note: research done 2026-10-10. Kaggle discussion and write-up pages were read directly in a browser (Kaggle pages do not render for plain fetch); these are marked "(verified page)". Items seen only in a search-engine summary are marked "(search summary)". Every finding carries an evidence label:

- **[FUTURE]**: scored on data that did not exist when the model was frozen (Kaggle private leaderboard in "forecasting" competitions, Numerai live rounds, fund returns).
- **[LIVE-FUND]**: real-money fund or platform returns (company-reported unless noted).
- **[BACKTEST]**: historical simulation, including cross-validation and broker research.
- **[OPINION]**: forum or practitioner commentary.

Limits: reddit.com is blocked both for search and for the browser in this environment, and the web-search budget ran out near the end. I could not read r/algotrading, Elite Trader, Wilmott or Nuclearphynance threads directly (see Gaps under Q5). I found no JoinQuant/RiceQuant community posts; Chinese practitioner evidence comes from financial media, MSCI and broker summaries.

Our context for the inferences: Taiwan stocks, long-only, 100% individual stocks (0050 only as the same-cash-flow benchmark), decisions after the close on daily data, about 40 factors, GBDT walk-forward models, a large factor-combination scan, and PPO reinforcement learning that already failed.

---

## Q1. Kaggle "JPX Tokyo Stock Exchange Prediction" (2022): winners, shake-up, what worked

### Takeaway
The JPX contest was the closest public analogue to our problem: rank about 2,000 Japanese stocks every day, scored over roughly four months of future data. It mostly showed how hard the problem is:
- The winning scores were statistically hard to tell apart from luck.
- Community post-mortems found that the 1st- and 2nd-place notebooks contained data-handling bugs, and that their scores collapsed when the bugs were fixed.
- 4th place was a one-line rule: rank by 1-day return and push stocks just before ex-dividend to the bottom.
- The well-built ML entries that placed (5th, 7th) were deliberately minimal: a few features and very shallow LightGBM.

### Cited Findings
**Task and scoring**
- Task: each day, rank about 2,000 TSE stocks. Score the top 200 against the bottom 200, with linear weights from 2 down to 1. The daily spread return is the long-minus-short return from the close of t+1 to the close of t+2. The score is mean/std of the daily spread, which is a Sharpe ratio. Run April 5 to October 7, 2022. 19,264 participants. [FUTURE] — [JPX news release](https://www.jpx.co.jp/english/corporate/news/news-releases/6020/20221206-01.html); [problem designer's article, Zenn (verified page)](https://zenn.dev/gamella/articles/eaf7fe5a96bdf0)
- The problem designer (AlpacaTech) reported, in his own pre-contest tests:
  - Fundamental plus technical features reached about 1.5x the performance of a technical-only baseline.
  - Nikkei 225 option data helped once it was engineered well.
  - Performance was stable over time except during COVID-era periods. [BACKTEST]
  - Source: [Zenn (verified page)](https://zenn.dev/gamella/articles/eaf7fe5a96bdf0)

**Final private leaderboard**
- Top scores [FUTURE] — [Kaggle leaderboard (verified page)](https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/leaderboard):

  | Rank | Team | Private Sharpe |
  |---|---|---|
  | 1 | kisa031 | 0.381 |
  | 2 | aa | 0.356 |
  | 3 | xiaobenla | 0.352 |
  | 4 | ddm | 0.347 |
  | 5 | JonnydosSantos | 0.339 |
  | 7 | BigStonks | 0.301 |
  | 10 | — | 0.280 |
  | 20 | — | 0.248 |
  | 49 | — | 0.193 |

- The board lists 1,039 teams.
- The winner was Shoki Sakai of Shizuoka University, team kisa031. — [Shizuoka Univ.](https://www.inf.shizuoka.ac.jp/english/news/detail.html?CN=154751)

**How large luck was**
- A community analysis (discussion #320323) put random-ranking scores at roughly Normal(μ=0, σ=0.13785). — cited in the [4th place write-up (verified page)](https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/writeups/ddm-4th-place-solution)
  - On that basis the 4th-place team concluded that a score between −0.3 and +0.3 cannot show a model is good, and that trying many models raises the odds of a lucky score (a multiple-comparisons problem). They gave up on ML.
  - Inference: the winning 0.381 is about 2.8σ of that random distribution. Over 1,000+ teams, several random entries would be expected near that level.

**4th place: a one-line rule**
- Private score 0.347. Rank stocks by 1-day return, highest first (short-term continuation), and push any stock with ExpectedDividend > 0 to the bottom. [FUTURE] — [4th place write-up (verified page)](https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/writeups/ddm-4th-place-solution)
- Why the dividend rule works: the target spans the ex-dividend date, so returns over that window are mechanically negative.
- The team's second submission was the exact reverse ranking (1-day reversal) and scored −0.196. Submitting both flips guaranteed one positive score.
- A related thread was titled "Shorting dividend days = easy score boost". — [JPX discussion list (verified page)](https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion?sort=votes)

**1st and 2nd place: code flaws**
- A participant who copied the potential 1st-place notebook found two problems. [OPINION, with a reproduction] — ["About 1st place solution" (verified page)](https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion/363838)
  - Features were built on all data at once rather than per security, so they depended on the row order. The cause was a global ffill/bfill.
  - The z-scores in live inference (one day of data at a time) meant something different from those in training.
  - Adding one line that sorts the data by Close changed the late-submission score to −0.085.
- The same author re-ran the 2nd-place solution with its logic flaws fixed: it scored −0.214. They concluded the top 2 were "PURE LUCK", and that the 4th and 5th solutions had no obvious flaws. — ["Errors, Luck, and Winners" (verified page)](https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion/364262)
- Conflicting account: a Japanese blog says the 1st-place presentation at the JPX ceremony used learning-to-rank ("ランク学習"). That blog is a secondary source and does not quote the presentation. — (search summary of [zenn.dev/mege](https://zenn.dev/mege/articles/ad754e0a80de6f))

**5th place, as reviewed in a third-party deck** [FUTURE result, BACKTEST analysis] — [ghtaro speakerdeck (verified page)](https://speakerdeck.com/ghtaro/makedeko-jpx-kagglekonpe5wei-jie-fa-gong-you)
- Model: LightGBM regression with max_depth=2, learning_rate=0.2, 2,000 trees and no early stopping.
- Features, about 7 in total:
  - 2-day reversal: P(T)/P(T−2)
  - Skip-month momentum: P(T−25)/P(T−231)
  - 231-day mean absolute return (volatility)
  - Cross-sectional rank of the 11-day average volume
  - Cross-sectional rank of the close price
  - Cross-sectional means of the reversal and momentum signals
  - SecuritiesCode
- Target: 2-day market-neutral return.
- Training rows: each day, only the 250 best and 250 worst stocks by target.
- Validation: expanding window predicting one month ahead. Adding a 20-day purge removed the falsely strong 10- and 20-day horizons, which had been leakage.
- Feature importance: a lower price rank and a lower volume rank raised predicted returns (small, illiquid names). SecuritiesCode did not reliably help.
- The 5th-place author blamed a private-period drop on higher implied volatility. The presenter could not confirm this.

**Other placed entries**
- 7th on the board (written up as "8th Place"): one LightGBM per TSE 33-sector group, trained on the price and stock-list files and tuned with Optuna on num_leaves, max_depth, learning_rate and n_estimators. Private 0.301. — [write-up (verified page)](https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/writeups/bigstonks-8th-place-solution)
- A participant noted that 41st place was a simple linear regression. They argued the results showed ML was no better than a rule-based momentum indicator in this contest. [OPINION] — [discussion #359475 (verified page)](https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion/359475)

### Inferences
- For a daily cross-sectional ranking of an Asian market over a few months, top scores of about 0.3–0.38 (Sharpe of a top-200/bottom-200 spread) were indistinguishable from good luck. The ranking among the top 10 carries almost no information about skill.
- Our own single-rule results over a short validation window carry the same σ problem. Testing many rules inflates the best one, which supports the deflated-Sharpe and one-shot final-validation rules we already use.
- The robust lessons are about hygiene rather than models:
  - Compute features per security and never across the whole panel.
  - Make training-time and inference-time normalization identical; daily cross-sectional z-scores must be computed the same way in both.
  - Purge the gap between training and validation by at least the label horizon.
  - Handle ex-dividend mechanics in labels and adjusted prices. This matters a lot for Taiwan, where most companies go ex-dividend in July–August.
- 5th place used extreme-only training rows and a tiny feature set with depth-2 trees. A low-complexity GBDT on a handful of classic factors was competitive with anything more complex. Our ~40-factor GBDT is not under-powered by competition standards; if anything, it risks being over-parameterized.

### Gaps
- No primary 1st-place write-up exists on Kaggle. The winner presented only at the JPX ceremony (video on JPX's YouTube channel, not reviewed). The "learning-to-rank" claim is unverified.
- The exact length of the private scoring window and the public/private rank changes were not extracted. The leaderboard footer shows Kaggle's generic note "approximately 1% of the test data", which looks like a template.

---

## Q2. Other Kaggle financial competitions with future-data evaluation (Ubiquant, Optiver, Jane Street 2021 and 2024–25, G-Research, Two Sigma)

### Takeaway
Across these contests, winners shared four traits:
- **Model:** a GBDT (LightGBM or CatBoost), usually blended with one or two neural nets that add diversity.
- **Features:** cross-sectional "market context" features, meaning per-timestamp means or ranks of the strongest features.
- **Validation:** purged or embargoed time-series cross-validation, and selection by cross-validation rather than the public leaderboard.
- **Retraining (in the two most recent contests):** frequent online updates on newly released labels, which were the largest single source of gain.

Pure deep nets rarely won. Where they did (Jane Street 2021), they were simple and heavily regularized.

Shake-ups on the future-data leaderboards were large: teams moved more than 1,000 places, and leaders after 6 weeks finished outside the top 10. Even so, some teams repeated near the top across contests, so skill shows up, just noisily.

### Cited Findings
**Ubiquant Market Prediction (2022, China A-shares, 300 anonymized features, scored in several updates on new market data)** [FUTURE]
- Scale: 21,178 joined, 2,949 made submissions, 2,435 teams. The winning submission was made within the final 3 days. — [Kaggle recap (verified page)](https://www.kaggle.com/competitions/ubiquant-market-prediction/discussion/338711)
- **1st place** — [1st place write-up (verified page)](https://www.kaggle.com/competitions/ubiquant-market-prediction/writeups/k-i-y-1st-place-solution-our-betting-strategy)
  - Models: LightGBM ×5 folds and TabNet ×5 folds, simple average. Custom MLPs gave a bigger lift on the leaderboard but were dropped because they were unstable in cross-validation.
  - Key features: the 300 given features plus 100 new ones. Each new feature is the per-time_id cross-sectional mean of one of the 100 features most correlated with the target.
  - The new features lifted the single LightGBM from CV 0.141 to 0.154 and from LB 0.141 to 0.149.
  - Data: only the most recent 2.4M rows, trading data length for features because of RAM.
  - Validation: purged group time-series CV for feature engineering and tuning. Training used KFold with a capped number of boosting rounds to limit overfitting.
  - Their second, more overfit submission (MLP and CatBoost added, about 150 more features, unlimited training) finished only at silver level (0.1158).
  - The team called itself lucky that market conditions suited its model.
- **2nd place** — [2nd place write-up (verified page)](https://www.kaggle.com/competitions/ubiquant-market-prediction/writeups/davide-stenner-2nd-place-solution-robust-cv-and-lg)
  - Five LightGBMs, purged K-fold with embargo.
  - Features: the 300 originals, 100 time-id means, and 5 row-wise aggregates (mean, std, 10/50/90% quantiles across features).
  - The number of trees was chosen by CV correlation.
  - Tried without success: AE-MLP, feature neutralization and PCA.
  - Rank across the five live updates: 34 → 12 → 4 → 2 → 2. Scores: 0.0828 → 0.1159 → 0.1331 → 0.1282 → 0.1232.
- Other write-ups: 3rd place used a transformer averaged over 5 seeds. 5th place used a single NN. The 17th-place write-up was titled "1000+ on public lb", meaning the team rose more than 1,000 places on future data. — [discussion list (verified page)](https://www.kaggle.com/competitions/ubiquant-market-prediction/discussion?search=place&sort=votes)
- Commenters on the 1st-place post said NN-based results "did not look robust". [OPINION]

**Optiver – Trading at the Close (2023–24, Nasdaq closing auction, scored on future data)** [FUTURE] — [1st place write-up by hyd (verified page)](https://www.kaggle.com/competitions/optiver-trading-at-the-close/writeups/hyd-1st-place-solution)
- Model: blend of CatBoost (0.5), GRU (0.3) and Transformer (0.2), all sharing the same 300 features.
- Scores (MAE): CV 5.8117, private 5.4030.
- Online learning: retrain every 12 days, 5 times in total. CatBoost was retrained from scratch and the NNs were fine-tuned.
  - Test-set MAE improved from 5.4438 with no online learning, to 5.4157 with one update, to 5.4030 with five updates.
  - The final run was actually cut to 4 updates by the time limit.
- Validation was simple: the first 400 days for training and the last 81 days held out. CV tracked the leaderboard closely.
- Post-processing: subtract the weighted cross-sectional mean of predictions.
- Did not help: 1D-CNN or MLP in the ensemble, multi-day GRU input, larger transformers.
- The 1st-place author called himself "really lucky".
- About 4,000 teams took part (from a student slide deck, [Google Slides](https://docs.google.com/presentation/d/1C6eS-og4hsXxU47HZJNA90A4Y6jZf_6M/htmlpresent), search summary). An [ESANN 2024 paper](https://www.esann.org/sites/default/files/proceedings/2024/ES2024-159.pdf) reviewing the contest found deep learning and support vector regression were the most common entrant approaches (search summary). The winner used neither alone.

**Jane Street Market Prediction (2020–21, live updates through Aug 2021)** [FUTURE] — [1st place write-up (verified page)](https://www.kaggle.com/competitions/jane-street-market-prediction/writeups/cats-trading-yirun-s-solution-1st-place-training-s)
- Final submission: a blend of a supervised autoencoder + MLP (Yirun Zhang) and XGBoost (teammates). The single AE-MLP model alone scored 6022.202 on the private leaderboard, still 1st.
- Details:
  - Validation: 5-fold purged group time-series split with a 31-day gap; the first 85 days were dropped.
  - Targets: all five resp horizons turned into a multi-label target.
  - Sample weights: the mean absolute resp, so large moves count more.
  - Network: Gaussian noise layer and swish activation.
  - Seeds: 3, using only the models from the last two folds (which saw more data).
- A Numerai forum reply said sample-weight training was the solution's key element. [OPINION] — [Numerai forum](https://forum.numer.ai/t/autoencoder-and-multitask-mlp-on-new-dataset-from-kaggle-jane-street/4338)
- One commenter on the write-up had expected "the simplest solution" to win gold. [OPINION]

**Jane Street Real-Time Market Data Forecasting (Oct 14, 2024 – Jul 12, 2025)** [FUTURE]
- Scale: 25,703 registrations, 3,643 participants, 2,781 teams. — [Kaggle recap (verified page)](https://www.kaggle.com/competitions/jane-street-real-time-market-data-forecasting/discussion/593758)
- The private leaderboard "is calculated with all of the future data". Top scores: 1st ms capital 0.013890, 2nd Patrick Yam 0.013273, 3rd 0.013163, 8th 0.010434, 49th 0.008132. — [leaderboard (verified page)](https://www.kaggle.com/competitions/jane-street-real-time-market-data-forecasting/leaderboard)
- Jane Street's design notes: the competition offers a lagged responder precisely to enable online learning. The firm hired the winner of its previous Kaggle contest, who then designed this one. — [Jane Street ML page](https://janestreet.com/machine-learning)
- **8th place** — [8th place write-up (verified page)](https://www.kaggle.com/competitions/jane-street-real-time-market-data-forecasting/writeups/evgeniia-grigoreva-private-lb-8th-solution)
  - Base model: GRU with a one-day sequence.
  - Auxiliary targets: rolling averages of responders over different horizons, adding about +0.001.
  - Extra features: market averages per date/time and per-symbol rolling statistics, adding about +0.002.
  - Ensemble: 6 models, LB 0.0112 versus 0.0105 for the best single model.
  - Online learning: one gradient step per day on the newly released labels, learning rate 3e-4. It added about +0.008 on CV, the largest effect in the solution.
  - Did not work for her: MLP, time-series transformers, cross-symbol attention.
- **2nd place, independent reproduction** — [cweill reproduction (verified page)](https://github.com/cweill/jane-street-causal-forecasting)
  - Architecture: GRU with cross-asset attention and 9 auxiliary targets, plus online Adam updates on the previous day's labels.
  - On a later window, online learning raised the score from 0.01433 (frozen) to 0.02078. The author warns that this does not establish leaderboard equivalence.
- The 19th-place finisher said the "secret sauce is good online learning". His own model was a simple MLP needing no offline training. [OPINION] — [discussion #598156 (verified page)](https://www.kaggle.com/competitions/jane-street-real-time-market-data-forecasting/discussion/598156)

**G-Research Crypto Forecasting (2021–22, final ranking from 3 months of live data)** [FUTURE]
- All top 3 used LightGBM. Winners said feature development and testing affected final scores far more than model development. The noisy data caused large leaderboard jumps. Prize pool $125k. — [G-Research wrap-up](https://www.gresearch.com/news/wrapping-up-the-g-research-crypto-forecasting-competition/) (search summary)
- Shake-up statistics [FUTURE] — ["The final Shakeup" (verified page)](https://www.kaggle.com/competitions/g-research-crypto-forecasting/discussion/323055)
  - One team moved from rank 1809 to 206.
  - One team moved from 33 to 5.
  - The leader after 6 weeks finished 13th or 14th.
  - One participant in the top 150 on every prior update dropped about 1,200 places at the final update.
  - The 2nd-place finisher estimated a 60% chance he would have fallen below 2nd with another update.
  - Counterpoint: the winners had also placed 3rd in an earlier Optiver contest and were 12th in Ubiquant at the time, which suggests persistent skill.
- The leader after 6 weeks (13th final) used 17 features: lagged EMAs, returns and volatility, plus their cross-sectional timestamp averages, with binning. Models: LightGBM and Keras NN ensembles. Target engineering split each asset's target into own return and a beta/market component. — [write-up (verified page)](https://www.kaggle.com/competitions/g-research-crypto-forecasting/writeups/tom-forbes-13th-place-final-1st-place-6-weeks-in-f)

**Two Sigma Financial Modeling Challenge (2016–17)** [FUTURE]
- After the contest, many participants found that their best private-score submissions were ones they had not selected, often because those had low public scores. [OPINION, with actual scores] — ["How good was your choice of 2 final models?" (verified page)](https://www.kaggle.com/competitions/two-sigma-financial-modeling/discussion/29546)
  - 8th place: its best private score was 0.0336 on a submission with a low public score.
  - 23rd place: a "too crazy" ExtraTrees submission scored 0.0273 private versus 0.0132 public.
  - Several participants called the final ranking largely luck.

**Cross-competition statistics**
- Among 2025 ML competition winners, XGBoost and LightGBM each appeared in 14 winning solutions and CatBoost in 8. GBDTs remain "the go-to tabular competition winner's modelling tool". Jane Street 2024–25 was the most popular competition of 2025 (over 3,700 teams, $120k). — [ML Contests "State of ML Competitions 2025"](https://mlcontests.com/state-of-machine-learning-competitions-2025/)

### Inferences
- Methods that win on future data and are transferable to daily, long-only Taiwan selection:
  1. GBDT as the backbone. An NN is optional and worth adding only if it is stable in cross-validation, purely for ensemble diversity.
  2. Cross-sectional context features. Add the daily market mean or rank of our strongest factors, and convert raw factors to daily cross-sectional ranks or z-scores.
  3. Purged or embargoed walk-forward CV, with model choice by CV rather than recent-period performance.
  4. Frequent retraining. Online learning was the biggest single lever in Optiver and Jane Street 2024–25. Those were intraday problems with fast-decaying signals, so the gain from more frequent retraining in daily cross-sectional equities is probably smaller.
     - Worth testing: monthly versus quarterly retrain cadence in our walk-forward.
  5. Auxiliary and multi-horizon targets (Jane Street 2021 and 2024–25, Numerai): train on several horizons, such as 5, 10 and 20 days, and blend.
  6. Sample weighting toward large-move or extreme rows (Jane Street 2021 sample weights; JPX 5th place trained on extremes only).
- Shake-ups imply that a single out-of-sample window, even three months long, cannot rank closely matched strategies. Our selection should reward stability across many sub-periods rather than the top in-sample score.
- Two failure reports cut against blanket feature neutralization: AE-MLP, PCA and neutralization did not help Ubiquant 2nd place. (For Numerai's opposite live evidence, see Q3.)

### Gaps
- No 1st-place write-up was found for Jane Street 2024–25 (ms capital) or Two Sigma.
- The G-Research winners' write-up was not read; only the official blog summary was available.
- The Optiver team count (~4,000) comes from secondary sources.
- Exact public-to-private rank-change distributions were not extracted for Ubiquant or Optiver.

---

## Q3. Numerai: what live staking and fund results show; Numerai's published lessons (feature neutralization, era-wise training, target ensembles, MMC/TC, Signals)

### Takeaway
Numerai is the longest-running future-data-scored stock-ranking competition: weekly, then daily, rounds with stakes at risk. Its hedge fund reported its best year in 2024: 25.45% net, Sharpe 2.75, one down month. A large drawdown came in late 2023.

The community's live evidence favors:
- Partial feature neutralization, which smooths scores and raised Sharpe in one 30-week live test.
- Ensembles across several targets and horizons.
- Rewarding contributions that are orthogonal to the meta-model.

Numerai moved payouts from the opaque TC to the locally computable CORR + MMC because TC barely persisted round to round. Numerai Signals explicitly neutralizes submissions against size, value, momentum, country and sector factors: well-known signals are treated as worthless to the fund.

### Cited Findings
**Fund results**
- 2024: Numerai One net return 25.45%, net Sharpe 2.75 (risk-free rate 0%), a single down month, "best year in our history". AUM grew from about $60M to $450M over three years. JPMorgan Asset Management secured $500M of capacity. [LIVE-FUND, company-reported] — [Numerai blog](https://blog.numer.ai/jpmorgan-secures-500m-capacity/)
- AUM: about $560M at end-2025 and about $700M by July 2026. The company says the Stake-Weighted Meta Model "continues to outperform" its internal benchmark models. [LIVE-FUND, company claim] — [Decrypt press release](https://decrypt.co/373781/numerai-completes-third-strategic-nmr-buyback-bringing-total-repurchases-to-3-2-million?amp=1)
- A forum post of Dec 16, 2023 refers to "the unfortunate recent big drawdown of the hedge fund" without giving its size. It argued that some well-performing models contributed little because of low stakes, and compared stake-weighted, equal-weighted and top-N-by-rolling-correlation aggregation. No numbers were in the text. [OPINION] — [Numerai forum](https://forum.numer.ai/t/about-the-stake-weighted-meta-model/6858)

**Feature neutralization, live test (rounds 209–238, 30 weeks)** [FUTURE] — [Numerai forum "Live Results on FN"](https://forum.numer.ai/t/live-results-on-fn/1369)
- Fully neutralized models had a Sharpe on correlation "significantly above 1", versus about 0.6 for non-neutralized models, with very similar mean correlation (the two correlated at about 0.75).
- They also had much higher MMC.
- Participants later settled on 50–75% partial neutralization and described it as smoothing performance rather than changing its direction.
- The author warned that wider adoption could erode the MMC advantage.

**Targets and era overlap** [BACKTEST guidance] — [Numerai forum "Super Massive Data Release: Deep Dive"](https://forum.numer.ai/t/super-massive-data-release-deep-dive/4053) (search summary)
- Other targets can look weak on the main target yet help an ensemble.
- Training on a 60-day target gave more stable models when scored on the 20-day target.
- With 20-day targets, keep every 4th weekly era to avoid overlap; with 60-day targets, every 12th; otherwise purge carefully.
- Choosing a target because it did well on validation is "yet another way to overfit".

**Official target-ensemble recipe** — [Numerai docs, Models](https://docs.numer.ai/numerai-tournament/models) (search summary)
- Gaussianize each model's predictions per era, scale to unit standard deviation, take a weighted sum, re-gaussianize, then optionally neutralize.

**Scoring changes**
- From rounds starting Jan 2, 2024, payouts moved to fixed multipliers of 0.5×CORR + 2×MMC, replacing TC.
- MMC is a model's covariance with the target after neutralizing it to the meta-model.
- A forum backtest found that a positive TC in one round had "practically no correlation" with future TC.
- Sources: [MMC docs](https://docs.numer.ai/numerai-tournament/scoring/meta-model-contribution-mmc); [MMC staking announcement](https://forum.numer.ai/t/mmc-staking-starts-jan-2-2024/6827); [TC backtest](https://forum.numer.ai/t/a-true-contribution-backtest/5154) (search summary)

**Numerai Signals scoring** — [Numerai Signals scoring docs (verified page)](https://docs.numer.ai/numerai-signals/scoring)
- Submissions are neutralized against "Barra factors (like size, value, momentum, etc.)", country, sector and custom features, and are scored on the residual.
- Current payout: 0.3×Alpha + 0.8×MPC.
- A combination of well-known signals leaves "little to no orthogonal component after neutralization".
- Signals favors 20- and 60-day horizons because short-horizon signals are hard for a large fund to trade.

### Inferences
- Numerai rewards orthogonal residual alpha for a market-neutral fund. We hold long-only Taiwan stocks against a 0050 benchmark. For us, raw exposure to size, value and momentum is a legitimate source of return, so full neutralization is not the right objective.
- The transferable parts are:
  1. Partial neutralization as a risk-smoothing tool: test 0%, 50% and 100% against style factors.
  2. Ensembles across label horizons.
  3. Purging overlapping labels; with 20-day labels, daily samples overlap heavily.
  4. Treating target choice as a hyperparameter that can overfit.
- The 2023 drawdown and the 2024 record year show that even a large, diverse ensemble has regime-dependent years. One year of our live (forward) record says little.

### Gaps
- Numerai's full-year 2025 and 2023 returns and the 2023 drawdown size were not found.
- Meta-model live correlation by year (public on Numerai's site) was not extracted.
- The official feature-neutralization tutorial text was not read directly.

---

## Q4. WorldQuant BRAIN and the "alpha factory": how many alphas, combination, decay, correlation limits

### Takeaway
Public information describes an industrial process:
- Researchers mine large numbers of short-horizon formulaic alphas (holding periods of about 0.6–6.4 days).
- Each alpha is weak and only loosely correlated with the others (mean pairwise correlation about 16%).
- Alphas are accepted only above Sharpe/fitness thresholds and below a self-correlation cap of about 0.7 against alphas already submitted.
- Thousands to "billions" of them are combined with a scalable optimizer.

The edge comes from combination and capacity across many weak signals, not from any single rule. Little audited performance is public.

### Cited Findings
- "101 Formulaic Alphas" (Kakushadze, with WorldQuant co-authors in an earlier version): average holding period about 0.6–6.4 days, mean pairwise correlation 15.9%. Returns track volatility closely; turnover has no significant effect on returns and explains little of the correlation between alphas. 80 of the 101 were "in production" at the time of writing. [BACKTEST] — [arXiv 1601.00991](https://arxiv.org/pdf/1601.00991) (search summary of abstract)
- "How to Combine a Billion Alphas" (Kakushadze & Yu) gives an explicit algorithm whose cost "scales linearly with N" for optimal weights across a very large number of alphas. It says binary clustering of alphas is not observed in practice. — [arXiv 1603.05937 (verified abstract)](https://arxiv.org/abs/1603.05937)
- BRAIN submission thresholds as reported by community sources (not official docs):
  - Sharpe > 1.25 for US delay-1 alphas
  - Fitness ≥ 1.0
  - Turnover between 1% and 70%
  - At most 10% of the book in one stock
  - Self-correlation ≤ 0.70 against the user's existing alphas (single source)
  - Sources: [Quantt guide](https://www.quantt.co.uk/resources/worldquant-brain); [GitHub checklist](https://github.com/RussellDash332/WQ-Brain); [arXiv 2608.11250 (AgonAlpha)](https://arxiv.org/pdf/2608.11250) (search summary)
- Crowd-sourced alpha businesses struggled around 2020: a QuantRocket/industry summary notes WorldQuant layoffs and office closures in January 2020. Unverified; I could not confirm the exact source page (see Gaps).

### Inferences
- The alpha-factory model maps onto our factor-combination scan: many weak, low-correlated signals plus a self-correlation cap and a combination layer. This matches the user's 2026-10-04 direction: find strong factors, then build mutually different strategies rather than near-duplicates.
- A concrete import: when the scan accepts a new rule, require its daily-return correlation with already-accepted rules to be at most about 0.7. Judge the portfolio of rules, not individual rules.
- The BRAIN thresholds are tuned for long-short, market-neutral alphas with short holding periods and high turnover. A long-only, after-close Taiwan rule set will have lower turnover and carries market beta. The same numeric cutoffs do not apply directly.

### Gaps
- No public, audited live performance for WorldQuant BRAIN alphas or for WorldQuant's funds was found.
- Official BRAIN documentation could not be read (it blocks automated access, per the Quantt author).
- The January 2020 WorldQuant layoff claim (130 staff, five offices) appeared only in a search summary of articles about Quantopian; the primary article was not verified.
- The search budget ran out before I could look for public data on alpha decay half-lives.

---

## Q5. Quantopian, QuantConnect and Western practitioner forums: lessons for retail systematic equity traders

### Takeaway
The best hard evidence is Quantopian's 888-algorithm study:
- Backtest Sharpe explained almost nothing of out-of-sample Sharpe (R² < 0.025).
- The more backtests an author ran, the bigger the shortfall.
- Volatility, drawdown, higher moments and hedging were more informative than Sharpe.

Quantopian's crowd-sourced fund failed and returned capital in February 2020; the platform shut in November 2020. QuantConnect closed Alpha Streams v1 because its vetting rewarded all-regime backtests, which encouraged overfitting.

Practitioner post-mortems converge on three points: understand why an edge exists; individual traders cannot win the crowded, institution-style equity-factor game; and individuals' advantage is freedom from institutional constraints.

### Cited Findings
**Quantopian's 888-algorithm study**
- "All that Glitters Is Not Gold" (Wiecki, Campbell, Lent, Stauth, 2016): 888 algorithms with at least 6 months of true out-of-sample results. The in-sample period ran from 2010 to deployment (Jan–Jun 2015), and the out-of-sample period from June 2015 to February 2016. Minute data with estimated trading frictions. [FUTURE] — [CXO Advisory summary](https://www.cxoadvisory.com/big-ideas/in-sample-vs-out-of-sample-performance-of-888-trading-strategies); [Quantpedia summary](https://quantpedia.com/quantopians-academic-paper-about-in-vs-out-of-sample-performance-of-trading-alg/)
  - Common backtest metrics such as Sharpe, information ratio and alpha predicted out-of-sample performance poorly (R² < 0.025).
  - The more backtests a user ran, the larger the gap between in-sample and out-of-sample Sharpe. The authors say this supports a deflated Sharpe ratio.
  - Volatility, maximum drawdown and portfolio-construction features such as hedging had significant predictive value. Higher moments (skew, tail ratio, kurtosis) were important in the random forest.
  - Non-linear classifiers on backtest features reached R² = 0.17 on hold-out data.
  - Conflict: the abstract says the top 10 strategies picked by ML reached an out-of-sample Sharpe of 1.8, versus 0.7 for the 10 with the highest in-sample Sharpe (CXO). The Quantpedia summary instead reports a Sharpe of 1.2 for the ML-built portfolio. The authors warn the ML may have learned what worked in that specific out-of-sample period.

**Quantopian shutdown**
- Timeline [LIVE-FUND] — [Wikipedia](https://en.wikipedia.org/wiki/Quantopian); [eFinancialCareers](https://www.efinancialcareers.com/news/2020/11/quantopian-shutdown) (search summary)
  - Live trading ended in 2017.
  - Paper trading ended in 2019.
  - In February 2020, the firm returned investors' money because its strategies underperformed.
  - Daily contests ended in May 2020.
  - The community was taken offline on November 14, 2020.
  - Reasons cited:
    - Backtests were weak predictors of live results.
    - The black-box IP model meant only outputs could be vetted.
    - Allocation constraints required unique, slow, liquid alphas.
    - Equity quant themes underperformed as valuations rose.
- QuantRocket's lessons [OPINION] — [QuantRocket blog (verified page)](https://www.quantrocket.com/blog/quantopian-shutting-down/)
  - "You can't crowdsource alpha."
  - Without a strategy's rationale you cannot tell edge from luck.
  - The difference between research and data mining is that "a researcher has an idea to go with the data".
- Robot Wealth (Kris Longmore) [OPINION] — [Robot Wealth (verified page)](https://robotwealth.com/my-thoughts-on-quantopians-closing/)
  - "unique alpha is hard, particularly for the individual".
  - Quantopian's required alphas (unique, slow, liquid, market-neutral) were "a game dominated by well-resourced firms".
  - Independent traders, being their own risk managers, can "go hunting for alpha wherever you like".

**QuantConnect Alpha Streams v1**
- QuantConnect stopped supporting v1, saying the submission and filtering process rewarded strategies built to perform in every regime, which it called unrealistic and overfitting-prone. It proposed many small factors that work only some of the time, switched by regime. [OPINION / platform decision] — [QuantConnect forum "Alpha Streams Refactoring 2.0"](https://www.quantconnect.com/forum/discussion/13441/alpha-streams-refactoring-2-0/) (search summary)
- An older forum post put the submission rejection rate at 80–90%. — [QuantConnect forum](https://www.quantconnect.com/forum/discussion/6737/9-ways-to-get-your-alpha-rejected/p1) (search summary)

### Inferences
- Quantopian's evidence argues directly against ranking rules by in-sample Sharpe from a big scan. Better: penalize by the number of trials (deflated Sharpe), and give weight to drawdown, volatility and tail statistics, which carried more out-of-sample information.
- The Alpha Streams lesson matters for us. A filter demanding that a rule beat 0050 in every sub-period selects overfit rules. Robust rules may legitimately lag in some regimes. It is better to require the right sign over the whole period, with bounded drawdown, than a win in every slice.
- "Boring wins" and "simple beats complex" are consistent with the competition evidence in Q1–Q2: simple momentum (JPX 4th), shallow GBDTs, and failed complex NNs. But I could not source the forum consensus directly (see Gaps).

### Gaps
- reddit.com (r/algotrading) is blocked for both search and browser here; Elite Trader, Wilmott and Nuclearphynance threads were not reached before the search budget ran out. I have no sourced statements of forum consensus on trend following, momentum, mean reversion, position sizing or regime filters for retail long-only equity systems. If needed, this should be a follow-up with a different access method.
- No aggregate live performance of QuantConnect Alpha Streams alphas was published that I could find.
- No dated source confirms whether Alpha Streams 2.0 launched.
- Quantopian contest results and live allocation returns by strategy were not found.

---

## Q6. Chinese practitioner communities and broker research (2020–2026): what still works in A-shares, the 2024 micro-cap crash, lessons

### Takeaway
A-share quant 2020–2026 was dominated by price/volume (量价) and high-frequency-derived factors fed into ML models (GBDT, deep nets) for index-enhancement products. It worked very well in aggregate:
- 2025 private quant index-enhancement products averaged a 16.75% excess return over their benchmark index (CSI 1000 products: 17.49%), with about 90% positive.

The returns relied heavily on small and micro-cap exposure, and that crowding caused the January–February 2024 crash:
- CSI 2000 fell 9.49% on Feb 5 and about 33% year to date.
- Leading quant products lost 10–17% in a week, an excess return of −2% to −4%.
- MSCI attributes about 30% of the worst quant funds' NAV loss to small-cap exposure.
- DMA leverage, snowball-note knock-ins and futures basis set it off.

A second shock followed in April 2024 (new delisting rules, micro-cap index −8.8% and −10.5% on two consecutive days). Practitioners responded by excluding the bottom 10% of stocks by market cap, adding annual-report risk screens and monitoring crowding.

### Cited Findings
**2024 micro-cap crash** [LIVE-FUND / market data]
- CSI 2000 fell 9.49% on Feb 5, 2024 and 32.76% year to date; the Jan 2 – Feb 5 decline (32.84%) was the largest since the index began.
- Many large quant managers' index-enhancement products fell 10–17% in a week, an excess return of −2% to −4%. One public quant fund fell more than 21% in a week. — [Yicai](https://www.yicai.com/news/101985905.html); [10jqka](https://news.10jqka.com.cn/20240223/c655254548.shtml) (search summary)
- Mechanism, as practitioners and 浙商证券 described it:
  1. Investors sold CSI 500 ahead of snowball-note knock-ins, widening the discount on CSI 500/1000 futures.
  2. DMA products (market-neutral swaps with 2–4x leverage) and neutral funds unwound.
  3. That drained micro-cap liquidity.
- On Feb 5 regulators limited DMA net selling. — [Wallstreetcn](https://wallstreetcn.com/articles/3709236); [Jiemian](https://www.jiemian.com/article/9657664.html) (search summary)
- MSCI's analysis [verified page] — [MSCI quick take, Mar 19, 2024](https://www.msci.com/research-and-insights/quick-take/zhong-guo-liang-hua-ji-jin-mian-lin-de-wei-pan-gu-yong-ji-yu-liu-dong-xing-jin-suo-)
  - Crowding in long micro- and small-cap positions had risen for two years. The composite crowding score of the smallest-cap stocks hit a five-year peak at end-2023.
  - Small-cap exposure explains about 30% of the YTD NAV decline of the weakest quant public funds.
  - Over the same period the MSCI China A Index was +1.2% YTD (to Feb 23).
- 浙商证券 [OPINION / broker] (search summary)
  - Quant products are homogeneous because they use the same factor strategies.
  - In small/micro caps, price-volume and high-frequency factors are far more effective than fundamental factors.
  - In 2023, the micro-cap style's excess over CSI 300 reached 76.6% at its peak.
- Second shock: on April 15–16, 2024, after the new 国九条 delisting rules, the micro-cap index fell 8.8% and 10.5% (sources differ slightly). — [Yicai](https://www.yicai.com/news/102074491.html); [CS.com.cn](https://www.cs.com.cn/xwzx/hg/202404/t20240418_6403002.html) (search summary)
- After February 2024, one private manager removed the bottom 10% of the market by cap from every product's universe. — [search summary of Chinese media](https://www.yicai.com/news/102074491.html); exact page not verified.
- 衍复投资: if micro-caps are removed from the Wind small-cap index, its 2017–2023 annualized return is about −0.5%. In other words, equal-weight micro-cap indices overstate the small-cap premium. [BACKTEST / OPINION] — [Yicai](https://www.yicai.com/news/102070288.html) (search summary)
- A blogger's simple backtest: a naive small-cap strategy started in 2016 earns only 5.58% annualized, so its return is very sensitive to the start date. [BACKTEST, weak] — [Xueqiu](https://www.xueqiu.com/1330152048/286172245) (search summary)

**2025 rebound and later**
- 私募排排网 [LIVE-FUND, aggregator]
  - About 1,000 private index-enhancement products: 2025 average return 45.08%, average excess 16.75%, nearly 90% with positive excess.
  - 172 CSI 1000 enhancement products: average return 49.78%, excess 17.49%, 95.93% positive.
  - 35 CSI 300 enhancement products: average return 31.22%.
  - Source: [10jqka, Jan 21, 2026](https://news.10jqka.com.cn/20260121/c674173909.shtml) (search summary)
- Public enhanced index funds: average 2025 excess return 5.35% across 291 funds (Wind). (search summary, same source cluster)
- First half of 2026 (third-party data): CSI 500 enhancement excess averaged only 0.85%, versus 4.07% for CSI 1000 and 7.28% for CSI 300. [LIVE-FUND] (search summary; source not pinned to a verified page)

**Models and factors**
- A Southwest Securities researcher (CS.com.cn, Dec 1, 2025) [BACKTEST, not peer-reviewed; no clear out-of-sample split or costs] — [CS.com.cn (verified page)](https://cs.com.cn/qs/202512/t20251201_6526009.html)
  - LightGBM with a LambdaRank objective on the top 90% of stocks by market cap, holding the top 100, reached Sharpe 1.86 versus 1.12 for MSE-trained LightGBM, with 46% lower turnover.
  - LambdaRank underperformed in the choppy 2022–2023 markets and did better in the trending 2024–2025 markets.
  - The author states that about 40% of institutional strategies are similar (crowding).
- 国金证券 high-frequency factor tracking (May 2026) [BACKTEST + out-of-sample tracking] — [fxbaogao summary](https://www.fxbaogao.com/detail/5443159) (search summary)
  - Price-range, price-volume divergence, regret-aversion and slope-convexity factors performed well overall out of sample.
  - Price-volume divergence had been unstable for years but strong last year.
  - High-frequency factors were neutral in 2026.
  - An equal-weight composite CSI 1000 enhancement reported 8.34% annualized excess (window unclear).
  - These factors need tick or order-book data.
- 东吴证券 has tracked price-volume correlation factors (RPV, SRV) monthly from January 2014 to June 2025 and says technical analysis still works in A-shares. No numbers were visible. — [fxbaogao](https://www.fxbaogao.com/detail/4929195) (search summary)
- Analyst-revision factor (REC, built from analysts revising their own EPS forecasts): about 14.8% annualized long-short return, IR about 2.36, since 2010 (data only to about 2020). A-share analysts give about half of CSI 300 stocks a "buy" each month, so the rating level itself carries little information. [BACKTEST] — [Sina](https://finance.sina.cn/2020-09-14/detail-iivhuipp4303960.d.html?vt=4) (search summary)
- No 2024–2026 test of a 机构调研 (institutional site-visit) factor was found. Only activity counts and a one-week anecdote (+5% average) appeared, which is not factor evidence. (search summary)
- Alpha158 (Qlib's 158 daily OHLCV factors) with LightGBM is a standard baseline on BigQuant and DolphinDB. I found no verifiable 2024–2026 live record for it. — [BigQuant wiki](https://bigquant.com/wiki/doc/nODcNAKYPJ) (search summary)

### Inferences
- The A-share experience is the most relevant crowding warning for Taiwan small caps:
  - A price-volume ML model that silently loads on the smallest, least liquid stocks can show great backtests and strong live years (2021–2023, 2025), then lose much of a year's excess in weeks when liquidity reverses.
  - JPX 5th place's feature importance (low price, low volume, higher predicted return) shows the same pull toward small, illiquid names.
- Concrete safeguards to test:
  - A minimum liquidity and market-cap floor, for example excluding the bottom 10% by cap or ADV, as the A-share managers did.
  - Measuring each rule's size and liquidity exposure, and reporting performance with and without the micro-cap tail.
  - A crowding or attention indicator. Taiwan analogues: margin-balance growth, retail day-trading share, turnover spikes.
  - Delisting and annual-report risk screens. Taiwan has its own 全額交割 and 變更交易 (restricted-trading) lists.
- Learning-to-rank objectives (LambdaRank) plausibly suit a long-only top-N selection better than MSE, but they are regime-sensitive (the reported 2022–2023 underperformance). Test as one ensemble member, not a replacement.
- Most "high-frequency factors turned daily" need tick data we do not have. The daily-data analogues (price-volume correlation, price-volume divergence, overnight versus intraday returns from OHLC) are testable.

### Gaps
- No JoinQuant, RiceQuant or BigQuant community posts were reached. Their forums need login or JS, and the search budget ran out. The "community view" here is media and broker research.
- Original broker PDFs were not read; most numbers are reposts or summaries with unclear in-sample and out-of-sample splits.
- No reliable live record of Alpha158 + GBDT strategies was found.
- 2024–2026 evidence for analyst-revision and 机构调研 factors in A-shares is missing.
- The CSI 500 enhancement 2025 full-year average and the source of the 2026 H1 figures were not verified.
