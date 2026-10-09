# LLM text-to-signal methods for stock returns: out-of-sample evidence, decay, look-ahead risk, Chinese/Taiwan evidence, costs

Notes compiled 2026-10-09. Every result is dated. "IS" = in-sample (sample overlaps the LLM's pre-training data or the authors' own design period); "OOS" = after the model's training cutoff and/or rolling out-of-sample estimation; "post-pub" = evidence after the paper became public (Lopez-Lira & Tang first circulated April 2023).

## 1. Lopez-Lira & Tang, "Can ChatGPT Forecast Stock Price Movements?" — method, results, decay, follow-ups

### Takeaway
GPT-4 headline scores do predict next-day returns in a sample that starts after GPT-4's training cutoff, but the tradable part is a 1–2 day drift worth about 34 bp/day before costs. It is concentrated in small stocks and the short leg, disappears at about 20 bp round-trip cost, and its Sharpe fell from 6.54 (2021Q4) to 1.22 (Jan–May 2024), most of that decline after the paper went public in April 2023.

### Cited Findings
**Versions and design**
- First version 6 Apr 2023. arXiv v6 is stamped 28 Oct 2025, and the HTML copy says "This Version: August 24, 2026". An SSRN version (abstract 4412788) is dated 9 Apr 2026. — [arXiv 2304.07619v6](https://arxiv.org/html/2304.07619v6); [AdvisorAnalyst summary, 3 Sep 2026](https://advisoranalyst.com/2026/09/03/can-chatgpt-accurately-forecast-stock-price-direction.html/)
- Sample (current version): Oct 2021–May 2024. 159,137 firm-headline-date observations on 4,123 US firms (~85% of 4,875 CRSP firms). Headlines are matched to RavenPack plus web-scraped headlines. 82% are overnight, 18% intraday; 67.5% are press releases. — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- Model: gpt-4-0314, whose training data stops in Sep 2021, so the sample is post-cutoff (OOS with respect to model knowledge). Llama-2's cutoff is Sep 2022, so the authors say its results "may be overstated". They argue against leakage because GPT-4 does better in the later part of the sample. — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- Trading rule: overnight news (before 9:00) is bought or sold at the open and closed at that day's close; after-close news is traded at the next open and closed at the next close. The portfolio is long positive-score names and short negative-score names, rebalanced daily, with at least 2 firms per leg. — [arXiv v6](https://arxiv.org/html/2304.07619v6)

**Size of the effect (GPT-4, before costs unless stated)**
- The initial reaction is mostly untradable: hit rates of 93.3% overnight and 88.8% intraday, with mean returns of 3.06% and 4.44%. — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- Tradable drift: hit rates of 58% overnight and 55% intraday. Mean drift is 0.34% overnight and 0.50% intraday. Sharpe is 2.97 overnight and 2.63 intraday. The drift lasts 1–2 trading days. — [arXiv v6](https://arxiv.org/html/2304.07619v6); [AdvisorAnalyst](https://advisoranalyst.com/2026/09/03/can-chatgpt-accurately-forecast-stock-price-direction.html/)
- Long vs short leg (overnight drift): the long leg earns 8 bp/day (Sharpe 0.78) and the short leg 26 bp/day (Sharpe 2.01). Predictability is stronger after negative news. — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- Cumulative long-short return over 2021m10–2024m5 is ~700%. With costs it falls to >300% at 5 bp round-trip and >100% at 10 bp, and the strategy is **unprofitable at 20 bp**. Turnover is ~190%/day. Rebalancing 25% of the book cuts turnover to ~46%/day (Sharpe at 10 bp: 1.29 at full rebalancing vs 1.34 at 25%). — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- Small stocks (below the 20th NYSE size percentile): the overnight drift coefficient is 0.087 (t=4.09) at baseline, and the small-firm interaction adds 0.404 (t=4.75), about 5× larger. The intraday interaction is 0.683 (t=3.29). Excluding stocks priced ≤$5 and stocks below the 20th percentile still leaves >300% cumulative before costs. — [arXiv v6](https://arxiv.org/html/2304.07619v6). The April 2024 version used a 10th-percentile cutoff and reported a coefficient ">4×" larger. — [UCLA Anderson PDF, Apr 2024](https://www.anderson.ucla.edu/sites/default/files/document/2024-04/4.19.24%20Alejandro%20Lopez%20Lira%20ChatGPT_V3.pdf)
- Earnings reports are one category where markets react efficiently: GPT-4 tracks the initial reaction but adds minimal drift prediction. — [search summary of arXiv v6](https://arxiv.org/html/2304.07619v6)

**Model comparison (overnight drift Sharpe, same sample)**
- GPT-4 2.97; GPT-3.5 1.66; DistilBart-MNLI 1.26; BART-large 1.05; Llama2-70B 0.97. FinBERT −0.33: its drift hit rate is 48% and it labels 75% of overnight news neutral. GPT-1, GPT-2 and BERT have initial hit rates below 65% and negative drift Sharpe ratios. — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- "GPT-4 subsumes traditional sentiment analysis methods": RavenPack sentiment is insignificant once the GPT-4 score is included (intraday drift regression). — [arXiv v6](https://arxiv.org/html/2304.07619v6)

**Decay / post-publication**
- Annualized Sharpe of the GPT-4 overnight strategy by period: 6.54 (2021Q4), 3.68 (2022), 2.33 (2023), 1.22 (Jan–May 2024). The authors call this "suggestive evidence" that rising LLM adoption reduces predictability. — [arXiv v6](https://arxiv.org/html/2304.07619v6); [AdvisorAnalyst](https://advisoranalyst.com/2026/09/03/can-chatgpt-accurately-forecast-stock-price-direction.html/)
- The 2024 version only had "suggestive evidence: a general decline in the performance of the ChatGPT-based strategy". The latest abstract states "Strategy returns decline as LLM adoption rises, consistent with improved price efficiency." — [arXiv abstract](https://arxiv.org/abs/2304.07619)

**Follow-ups and replications (2024–2026)**
- Kirtac & Germano, *Finance Research Letters* 62 (2024) 105227: 965,375 US news articles, 2010–2023. OPT (a GPT-3-class model) reaches 74.4% accuracy. Its long-short strategy has Sharpe 3.05 after 10 bp costs and returns 355% over Aug 2021–Jul 2023. The Loughran-McDonald dictionary strategy has Sharpe 1.23. — [UCL Discovery](https://discovery.ucl.ac.uk/id/eprint/10209715); [WASSA 2024 version](https://aclanthology.org/2024.wassa-1.1.pdf)
- Gao, Jiang & Yan (arXiv Dec 2025) re-run the headline→next-day-return setup with Llama-3.3-70B. A 1-SD increase in the LLM prediction goes with a 0.197% higher next-day return. They find memorization "substantially amplifies" apparent predictive power (see §3). — [arXiv 2512.23847](https://arxiv.org/html/2512.23847)
- "Buy the Rumor, Sell the News" (arXiv 14 Aug 2026): 4.57M articles on ~3,000 US stocks over 2023–2026. An LLM teacher is distilled into a compact classifier with 17 event tags. The cumulative move in the news direction at the close of publication day is 2.8× its value 20 days later, i.e. it partly reverses. For rumors, the rumor day captures the entire move. Quantified fundamental news (earnings, dividends, guidance, analyst actions) keeps drifting for weeks. Soft news (launches, macro commentary, leadership) gives its move back. — [arXiv 2608.14014](https://arxiv.org/abs/2608.14014)
- "What Does ChatGPT Make of Historical Stock Returns?": when an LLM forecasts from price history rather than text, it puts larger positive weights on recent returns (extrapolation) and is miscalibrated. — [arXiv 2409.11540](https://arxiv.org/pdf/2409.11540); [AEA 2026 program](https://www.aeaweb.org/conference/2026/program/paper/zNrQ4Yn6)

### Inferences
- The OOS label is only half true. The sample is post-cutoff for GPT-4, but the research design (models, holding rule, sample) was chosen by the authors on the same data. Genuine post-publication evidence is the 2023 and Jan–May 2024 sub-periods: Sharpe 2.33 and 1.22, before costs and with ~190% daily turnover. Net of realistic costs the post-2023 strategy is likely flat or negative.
- The effect is a liquidity/limits-to-arbitrage premium. It sits in small caps, the short leg, and a 1–2 day window, which is exactly where a retail long-only account cannot harvest it. Taiwan round-trip costs for a retail account (0.3% securities transaction tax on sells plus ~0.1425% commission per side before broker discounts — regulatory figures not re-verified in this session) are well above the 20 bp break-even, so a daily headline-drift strategy copied to Taiwan would very likely be unprofitable.
- "Buy the Rumor" suggests the slower, cost-tolerant part of news is quantified fundamental news (earnings, guidance, dividends), not general sentiment. This fits weekly or longer holding periods better.

### Gaps
- I did not find an independent, peer-reviewed replication of Lopez-Lira & Tang on a fresh post-2024 sample with net-of-cost results. The decay numbers come from the authors' own sample, which ends May 2024.
- Five-factor alpha values (Table 5) and the Section 8 market-efficiency analysis of the v6 paper were not read (the text was truncated).

## 2. Embedding approaches, "LLM as feature extractor vs decision maker", event extraction, earnings calls

### Takeaway
Two designs both work in backtests. In the first, an LLM turns each article into an embedding and a supervised model (ridge or a neural net) learns returns. In the second, the LLM is prompted for a score. Which one wins depends on the paper: embeddings plus supervision give the highest Sharpe in Chen-Kelly-Xiu, while direct GPT-4 prompting beats embeddings in Lopez-Lira & Tang. Ensembles of several models are consistently best. Earnings-call and filing evidence is thinner and weaker: markets price earnings news fast.

### Cited Findings
**Chen, Kelly & Xiu, "Expected Returns and Large Language Models"** (SSRN 4416687; first posted 2022/2023, revised Feb 2026)
- Data: about 25 years of Refinitiv (Thomson Reuters) single-name news and press releases, covering 16 equity markets and news in 13 languages. — [finm-33200 discussion page](https://finm-33200.github.io/discussions/expected_returns_llms.html); [SJTU seminar abstract](https://acem.sjtu.edu.cn/academic/82735.html)
- Method: take the last-layer embedding of each article (~1,000 dimensions) and estimate E[r_{t+1}|x] = x′θ by ridge (or a neural net) on a rolling window. Stocks are sorted into decile long-short portfolios out of sample. — [finm-33200](https://finm-33200.github.io/discussions/expected_returns_llms.html)
- Daily equal-weighted annualized Sharpe: ChatGPT embeddings 4.62; LLaMA2 4.16; BERT and Word2vec >3; SESTM (word-based) 3.43; Loughran-McDonald 2.29; ensemble of all models 5.11; neural net on embeddings 5.83 (vs ~4.7 for linear). No value-weighted or after-cost numbers are reported there. The page notes daily Sharpe measures "predictive association" rather than tradability. — [finm-33200](https://finm-33200.github.io/discussions/expected_returns_llms.html)
- Strategy correlations: ChatGPT vs LLaMA ~60%; two LLaMA versions ~80%. — [finm-33200](https://finm-33200.github.io/discussions/expected_returns_llms.html)
- Monthly frequency: the most recent day's news has no monthly profitability because the signal "decays too quickly". Averaging embeddings over 3–36 months helps, peaking at 12–24 months. Word methods degrade with negation and LLMs do not. — [finm-33200](https://finm-33200.github.io/discussions/expected_returns_llms.html)
- Kelly's slides (Wharton Jacobs Levy, Sep 2024) conclude: "Larger LLMs perform better"; "Polyglot methodology"; "Fast and slow return prediction content in news text"; "Not all are accessible with prompts"; "Best strategy is an ensemble of many LLMs". — [Kelly slides PDF](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2024/09/Kelly-WhartonJL.pdf)
- The discussant (Sophia Zhengzi Li) summarizes the findings as slow price reaction plus an LLM advantage over word models in both sentiment labeling and return forecasting. — [discussion slides](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2024/09/ExpectedReturnAndLLM_Discussion_SophiaZhengziLi_NoPause.pdf)
- "A Financial Brain Scan of the LLM" uses this method as a benchmark: embeddings plus logistic regression give Sharpe 4.91. — [arXiv 2508.21285](https://arxiv.org/pdf/2508.21285)

**Prompting vs embeddings, head to head (Lopez-Lira & Tang v6, same sample)**
- Overnight initial-reaction hit rate: GPT-4 prompt 93.3%, OpenAI embeddings 84.6%, MiniLM embeddings 80.2%. Overnight drift Sharpe: 2.97, 2.53 and 1.42. Intraday drift Sharpe: 2.63, 1.71 and 0.79. The embedding models were trained on a rolling 6-month window (105,742 overnight samples), retrained monthly, with a 9-month warm-up. — [arXiv v6](https://arxiv.org/html/2304.07619v6)

**Multilingual (Japan)**
- "Aligning Multilingual News for Stock Return Prediction" (arXiv Oct 2025), Tokyo-listed stocks: portfolios on full Japanese-news embeddings beat English-news ones. With aligned embeddings, Sharpe is 4.36 for Japanese vs 3.42 for English. The authors call these "idealized" (not net of costs). — [arXiv 2510.19203](https://arxiv.org/html/2510.19203v1)

**Event extraction / LLM as labeler**
- "Buy the Rumor": an LLM labels 17 event tags and is distilled into a compact classifier. Fundamental-event tags drift for weeks while soft-news tags reverse. The output is a per-tag drift table meant as a prior for forecasting models. — [arXiv 2608.14014](https://arxiv.org/abs/2608.14014)
- "Structured Event Representation and Stock Return Predictability" (Gang Li, arXiv Dec 2025) exists as a lead. Results not read. — [arXiv 2512.19484](https://arxiv.org/pdf/2512.19484v1)

**Earnings calls / filings**
- Ghosal, "LLM-Driven Investment Models: Evidence from Earnings Call Transcripts" (SSRN): sentence-level embeddings of 2019–2023 transcripts predict post-earnings returns. Long-short backtests reportedly beat traditional NLP after transaction costs, signal lag and portfolio constraints. Not peer-reviewed as far as I could tell. — [SSRN 6510327](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6510327)
- Chung & Tanaka-Ishii (ICAIF 2023): textual and contextual earnings-call features improve post-earnings-drift prediction out of sample by 16.9%–108.5% over an earnings/fundamentals-only baseline, across four abnormal-return definitions. These are NLP features, not necessarily an LLM. — [ACM DL](https://dl.acm.org/doi/pdf/10.1145/3604237.3626861)
- "Anonymization and Information Loss" (arXiv Nov 2025): sentiment from anonymized earnings-call transcripts gives smaller coefficients and lower adjusted R² than raw transcripts. — [arXiv 2511.15364](https://arxiv.org/pdf/2511.15364)
- "Same Company, Same Signal" (arXiv Dec 2024) uses few-shot LLM prompting on transcripts to forecast post-earnings **volatility**, not returns. — [arXiv 2412.18029](https://arxiv.org/pdf/2412.18029)
- Kim, Muhn & Nikolaev, "Bloated Disclosures: Can ChatGPT Help Investors Process Information?", is a lead on GPT summaries of disclosures. Numbers not verified in this session. — [arXiv 2306.10224](https://arxiv.org/html/2306.10224v4)

**Practitioner write-ups**
- No empirical LLM-signal note from AQR or Man Group was found. AQR's research page has the perspective piece "In the Age of AI, A Quant's Edge is Human". — [AQR Research](https://www.aqr.com/Insights/Research). Bryan Kelly (AQR) presents the Chen-Kelly-Xiu results in the slides above. — [Kelly slides](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2024/09/Kelly-WhartonJL.pdf)

### Inferences
- **Feature extractor vs decision maker.** The Chen-Kelly-Xiu evidence favors the LLM as feature extractor: embeddings, supervised on realized returns, ensembled across models. The Lopez-Lira & Tang evidence shows a strong instruction-following model prompted for a score can beat cheap embeddings on short-horizon headline drift. Both are LLM-as-signal, not LLM-as-trader. The one negative datapoint on LLM-as-forecaster (2409.11540) concerns forecasting from return history, where LLMs extrapolate.
- **Horizon fit.** For a weekly or monthly, cost-sensitive account, the relevant evidence is the slow component: Chen-Kelly-Xiu's 12–24-month averaged embeddings and the multi-week drift after quantified fundamental news. It is not the next-day headline drift.
- **Correlation.** Embedding signals from different LLMs are only ~60–80% correlated, so an ensemble adds value. This is consistent with the project's goal of combining distinct factors rather than parameter variants.

### Gaps
- Chen-Kelly-Xiu country-level ("Polyglot Portfolios") numbers, and in particular whether Taiwan, Hong Kong or China are among the 16 markets, could not be read: the slide charts are images and SSRN returned 403. Value-weighted and after-cost results are also missing.
- No reliable after-cost evidence on LLM earnings-call signals from a peer-reviewed source was found.

## 3. Look-ahead / memorization risk and how papers handle it

### Takeaway
Any backtest whose test window falls before the LLM's training cutoff is contaminated. Frontier models recall exact pre-cutoff index levels and macro numbers, and memorization measurably inflates headline→return predictability. Accepted fixes are (a) testing only after the cutoff, (b) chronologically consistent models trained only on data available at each date (ChronoBERT/ChronoGPT, now in JFE), and (c) anonymization. Anonymization mainly removes "distraction" and costs information. Prompt instructions such as "pretend it is date X" do not work.

### Cited Findings
- Lopez-Lira, Tang & Zhu, "The Memorization Problem: Can We Trust LLMs' Economic Forecasts?" (arXiv 2504.14765, Apr 2025): before its Oct 2023 cutoff, GPT-4o recalls some S&P 500 levels exactly, unemployment rates to 0.1 pp, and quarterly GDP. Before the cutoff you cannot tell forecasting from retrieval. — [alphaXiv overview](https://www.alphaxiv.org/ja/overview/2504.14765v1); [QuantPedia summary](https://quantpedia.com/the-memorization-problem-can-we-trust-llms-forecasts/)
- Gao, Jiang & Yan, "Detecting Lookahead Bias in LLM Forecasts" (arXiv 2512.23847, Dec 2025): they estimate a "Lookahead Propensity" (LAP), the likelihood a prompt was in the training corpus, using pre-training-data detection. A positive forecast×LAP interaction signals bias. With Llama-3.3-70B (cutoff Dec 2023, 2024 as placebo period), memorization "substantially amplifies" apparent predictive power. They cite 2025 evidence that masking and instructions to respect time boundaries fail to stop recall. — [arXiv 2512.23847](https://arxiv.org/html/2512.23847)
- Glasserman & Lin, "Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis" (arXiv Sep 2023; *Journal of Financial Data Science* 6(1), 2024): company identifiers are stripped from headlines. In-sample, anonymized headlines *outperform* the originals, so the "distraction effect" (general knowledge about the firm) outweighs look-ahead bias. The anonymization gain is strongest for large firms. They recommend anonymization both for backtests and for live use. — [arXiv 2309.17322](https://arxiv.org/abs/2309.17322); [PM Research](https://www.pm-research.com/content/iijjfds/6/1/25)
- "Anonymization and Information Loss" (arXiv 2511.15364, Nov 2025): anonymizing earnings-call transcripts lowers coefficients and R², so anonymization also removes real signal. — [arXiv 2511.15364](https://arxiv.org/pdf/2511.15364)
- "AI's predictable memory in financial analysis" (*Economics Letters*, 2025): context-free recall of historical returns serves as a memorization proxy. The bias depends on data frequency, model size and aggregation; smaller models and finer-grained data show negligible bias. The authors conclude look-ahead bias cannot be measured precisely. — [ScienceDirect S0165176525004392](https://www.sciencedirect.com/science/article/pii/S0165176525004392)
- He, Lv, Manela & Wu, "Chronologically Consistent Large Language Models" (arXiv 2502.21206; *Journal of Financial Economics* 2026): ChronoBERT and ChronoGPT are trained only on text available at each date. ChronoGPT has yearly checkpoints for 1999–2024, each 124M parameters with a December cutoff, released on Hugging Face. In next-day news→return prediction, high-minus-low Sharpe is 4.80 (ChronoBERT) and 4.92 (ChronoGPT), above GPT-2 XL, ModernBERT, BERT, FinBERT and StoriesLM and comparable to a much larger Llama. The authors conclude look-ahead bias in this task is "modest", with little evidence of it when moving from headlines to full text. Versions disagree on training-token counts (21B vs 70B) and validation counts (77/78 vs 77/83). — [arXiv 2502.21206](https://arxiv.org/abs/2502.21206); [JFE](https://www.sciencedirect.com/science/article/pii/S0304405X26001455); [HF checkpoint](https://huggingface.co/manelalab/chrono-gpt-v1-20111231)
- ChronoGPT-Instruct (arXiv Oct 2025) adds instruction-following, chronologically consistent models with released instruction data. — [arXiv 2510.11677](https://arxiv.org/html/2510.11677)
- Lopez-Lira & Tang handle the issue by using a post-cutoff sample (GPT-4 cutoff Sep 2021, sample from Oct 2021) and checking that GPT-4 does not do better early in the sample. — [arXiv v6](https://arxiv.org/html/2304.07619v6)
- MemGuard-Alpha (arXiv 2603.26797) studies the limits of membership inference for filtering contaminated signals (title-level lead only). — [arXiv 2603.26797](https://arxiv.org/pdf/2603.26797)

### Inferences
- Two results look in tension and probably are not. Chrono models say the bias is modest for small models and sentiment-type tasks. Gao-Jiang-Yan and Lopez-Lira-Tang-Zhu say it is large for big frontier models and for prompts that touch memorable outcomes. Risk rises with model size and with how famous the firm or event is.
- For this project, which picks rules on 2015-06 onward and checks them on 2020-10 onward: any modern LLM (cutoffs 2023–2025+) is pre-cutoff for both windows, so its text scores on 2015–2024 Taiwan news would be in-sample in the memorization sense. Defensible options: (1) build the signal now and paper-trade or forward-record it from the model's cutoff onward; (2) use small, older or chronologically consistent encoders for historical backfill; (3) anonymize company names and test the gap between anonymized and raw scores as a contamination diagnostic. Option (3) is weaker for Taiwan large caps (TSMC and similar), which the model knows well.
- Taiwan large caps are where both the distraction effect (Glasserman-Lin: stronger for large firms) and memorization are worst.

### Gaps
- No chronologically consistent **Chinese-language** model was found. ChronoBERT/ChronoGPT appear to be English-only (inferred from the descriptions, not verified).
- No study quantifies memorization for Traditional Chinese / Taiwan stock news specifically.

## 4. Chinese-language news, A-shares, Taiwan (MOPS 重大訊息, PTT/Dcard)

### Takeaway
A-share evidence is large-sample and strong in backtests. The most comprehensive study uses ~2.2M stock-tagged Chinese news articles, and LLM/BERT news tone gives long-short annualized returns of roughly 35–90%. The effect is concentrated in small caps, zero-shorting stocks, non-SOEs, zero-analyst-coverage and retail-heavy names. Small Chinese encoders often match or beat ChatGPT. For Taiwan I found no published LLM study on MOPS material announcements, and only pre-LLM or BERT-era studies on news/PTT sentiment, with weak or index-level results.

### Cited Findings
**A-shares**
- "Large Language Models and Return Prediction in China" (ABFER 2024 slides; presenter Lin Tan). Data funnel: 28,259,596 raw articles → 8,372,112 tagged with a single stock code → 2,233,748 tagged with an A-share stock event → 2,193,371 with returns. Models: BERT, FinBERT, RoBERTa and Chinese LLMs (Baichuan, ChatGLM, InternLM) plus an ensemble. Signals are "news tone" and a supervised "return forecast" from article representations. — [ABFER PDF](https://abfer.org/media/abfer-events-2024/cmd/Large-Language-Models-and-Return-Prediction-in-China_Lin-Tan.pdf) (numbers below are from text extracted from the appendix tables; table titles were not recoverable)
  - First long-short table (title not recoverable; probably the news-tone sort): equal-weighted annualized long-short ranges from 64.28% (ChatGLM) to 84.40% (Baichuan), with t ≈ 8–9.5, and the ensemble EW is 88.52% (t=9.88). Value-weighted ranges from 35.09% (InternLM) to 66.54% (Baichuan), t ≈ 3.2–5.6. A second, similar table (possibly the return-forecast sort or a risk-adjusted version) has an ensemble EW long-short of 91.32% (t=11.76) and VW of 66.75% (t=6.42). The short leg is about as large as or larger than the long leg (e.g. Baichuan EW short −43.28% vs long +41.11%).
  - Next-day return regressions: tone coefficients are significant for every model (t 9.0–11.1), but adjusted R² is only 0.04%–0.12%. Tone also predicts next-day SUE (earnings surprise), with coefficient 4.63 (t=8.65).
  - Heterogeneity (sorted by tone, EW long-short): small-cap 111.57% vs large-cap 71.85%; zero-shorting stocks 107.47% vs shortable 76.51%; non-SOE 94.92% vs SOE 58.76%; zero analyst coverage 104.61% vs covered 74.98%; high retail ownership 94.34% vs low 81.22%. Value-weighted SOE long-short is 36.89% (t=2.47).
  - Holding-period table (VW, large cap): 1-day long-short −0.10% (t=−0.01), 5-day 8.68% (t=1.22), 10-day 7.30% (t=1.27), all insignificant. Small cap VW: 1-day 49.13% (t=4.12), 5-day 35.57%, 10-day 22.73%. Daily-rebalanced turnover is ~90%; weekly (5-day) is ~19%.
  - The effect survives CH4 (China 4-factor) adjustment: tone EW long-short is 62.72% after adjustment vs 61.43% raw. It is also present in both foreign and domestic media.
  - Tone predicts next-day order imbalance positively for large and extra-large orders (t 5.4–8.2) and negatively for small (retail) orders (t −8.3). Institutions trade with the news and retail trades against it.
- Zhang, Hua, Xu, Kong, Zuo & Guo, "Unveiling the Potential of Sentiment: Can LLMs Predict Chinese Stock Price Movements?" (arXiv 2306.14222, Jun 2023, rev. May 2024). Sentiment factors from Chinese news summaries are backtested on A-shares. Annual excess return / Sharpe: ChatGPT ~23.2% / 0.64; Erlangshen-RoBERTa-110M ~24.1% / 0.68; Chinese FinBERT ~19.9% / 0.48. The 110M Chinese model beat GPT-3.5 on every metric. — [arXiv 2306.14222](https://arxiv.org/pdf/2306.14222); [abstract page](https://arxiv.org/abs/2306.14222)
- "Can ChatGPT predict Chinese equity premiums?" (*Finance Research Letters*, 2024): GPT-3.5 scores on >1.86M headlines; weekly scores significantly predict Shanghai Composite and CSI 300 premiums and beat bag-of-words. This is index-level, not a cross-sectional signal. — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1544612324006615)
- TPM Vol. 32 No. 4 (2025): an A-share dataset built from China Securities Journal and Shanghai Securities News. ChatGPT sentiment features beat DeepSeek on direction accuracy and OOS R², and an iterative weighted combination is best out of sample. No portfolio test was shown in the excerpt. — [TPM PDF](https://tpmap.org/submission/index.php/tpm/article/download/2869/2154)
- *Applied Economics Letters* (2026): DeepSeek sentiment on 29,077 Wind "Lujiazui Financial Breakfast" headlines, 1 Jan 2024–21 Jan 2026, against Shanghai Composite daily returns. DeepSeek and Kimi scores correlate 0.88 and 0.87. Index-level, no formal OOS split shown. — [Taylor & Francis](https://www.tandfonline.com/doi/full/10.1080/13504851.2026.2662546)

**Taiwan**
- No study was found that applies an LLM to MOPS (公開資訊觀測站) material-information announcements (重大訊息) and measures abnormal returns. A practical data note: one developer pipeline scrapes the MOPS real-time 重大訊息 page and backfills it with the next-day full version from TWSE OpenAPI. — [GitHub PR](https://github.com/peterpita-tech/Peterpita/pull/2); [TEJ guide to MOPS](https://www.tejwin.com/insight/%E5%85%AC%E9%96%8B%E8%B3%87%E8%A8%8A%E8%A7%80%E6%B8%AC%E7%AB%99%E8%B3%87%E6%96%99/)
- PTT: Discover Computing (2025) pretrains a Chinese FinBERT on Taiwan market news, adapts it to PTT Stock board after-hours chat (盤後閒聊), and feeds daily sentiment into a CNN-BiLSTM-SA model for next-day trend. This is a classification study, not a portfolio test. — [Springer](https://link.springer.com/article/10.1007/s10791-025-09515-3)
- PeerJ CS (2021) scores news plus PTT posts with BERT and feeds them to an LSTM for price forecasting, with data to 31 Mar 2020. — [PeerJ](https://peerj.com/articles/cs-408.pdf)
- *Algorithms* (2026), Mini-TAIEX futures: FinGPT-internLM, FinGPT-llama and FinMA produce sentiment from 13 news outlets plus PTT Stock board threads, used as features for LSTM/GRU/Informer/PatchTST. Index futures, not stocks. — [MDPI](https://doi.org/10.3390/a19010069)
- Negative evidence (unreviewed): an open-source PTT Stock board project tested dictionary, LLM-labeled and BERT sentiment and found most measures not significantly related to next-day returns. — [GitHub tw-stock-sentiment](https://github.com/jameschen108/tw-stock-sentiment)
- Pre-LLM: news-text sentiment is significantly related to TAIEX returns. Adding negative sentiment to a trading strategy gives 13.308% return and Sharpe 0.759, better than without it. — [Journal of Social Sciences and Philosophy (Academia Sinica)](https://www.rchss.sinica.edu.tw/jssp/journals/1653)
- Theses (leads; contents only partially verified):
  - NCCU 2026 master's thesis by 劉騏賓 applies Gemma 4 E4B and Llama 3.1 8B to 2015–2024 news to extract multidimensional sentiment for volatility risk. Market not confirmed. — [NCCU thesis](http://thesis.lib.nccu.edu.tw/detail/23951384a3a7a9d680e95435d95a75f6/)
  - NTU 2025 PhD thesis: a LLaMA-2 finance-tuned model on 10-K disclosures (US), plus RavenPack news, where more relevant negative news brings larger abnormal returns. — [Airiti](https://www.airitilibrary.com/Article/Detail/U0001-0150250227353003)
  - 「應用大型語言模型與提示設計於股市非結構性資料極性分析」 — [Airiti](https://www.airitilibrary.com/Article/Detail/U0001-1202230808591079)
  - 「基於大語言模型的文字情緒分析之研究－以金融投資新聞為例」 (2026) — [Airiti](https://www.airitilibrary.com/Article/Detail/U0034-1401202615214479)
  - 「ChatGPT與臺灣AI概念股之異常報酬研究」 is an event study of the ChatGPT launch, not a text signal. — [Airiti](https://www.airitilibrary.com/Article/Detail/U0017-1812202411463553)

### Inferences
- The A-share pattern mirrors the US: the effect is strongest where arbitrage is hard (small caps, no shorting, retail-heavy, no analyst coverage). For value-weighted large caps it is insignificant at 1–10 day holding. Taiwan's tradable universe for a ~150-stock long-only account is closer to the "large-cap, VW" cells, where A-share evidence is weakest.
- The A-share data also shows institutions trade with news tone while retail trades against it, and the short leg carries most of the return. A long-only Taiwan account gets only the weaker long leg.
- Small Chinese encoders (Erlangshen-110M, BERT/FinBERT/RoBERTa) match or beat ChatGPT/GLM-class models on Chinese news. Combined with the Chrono evidence, a cheap local encoder plus supervised training is a credible baseline for Traditional Chinese.
- MOPS 重大訊息 are the Taiwan analogue of the "quantified fundamental news" that drifts for weeks in the US. They are timestamped, mandatory and free, so event extraction (type of announcement plus numeric surprise) rather than generic sentiment is the most promising untested direction for Taiwan.

### Gaps
- No peer-reviewed study on LLM signals for Taiwan individual stocks with portfolio returns, OOS windows and costs was found. NDLTD (臺灣博碩士論文知識加值系統) could not be searched directly. The Airiti thesis leads above were not read in full.
- Dcard: no finance-sentiment study found.
- The sample period, exact table titles and transaction-cost treatment of the ABFER China paper could not be verified from the extracted text. Co-authors are not verified.

## 5. Implementation costs: tokens per article, $/day for ~150 stocks, cheaper alternatives

### Takeaway
At 2026 list prices, scoring every news item and MOPS announcement for ~150 Taiwan stocks with a small hosted model costs well under US$1–2/day, roughly $5–30/month, and batch APIs halve that. Cost is not the constraint; contamination-free validation and trading costs are. Price quotes come from aggregators that disagree, so verify them on official pricing pages.

### Cited Findings
- Batch discounts: Google, OpenAI and Anthropic each price their asynchronous batch lane at 50% of the synchronous rate. xAI's discount is ~20% on four older models only. — [Digital Applied, Aug 2026](https://www.digitalapplied.com/blog/llm-batch-api-pricing-landscape-2026). Gemini batch is 50% of the interactive price. — [Google Gemini docs](https://ai.google.dev/gemini-api/docs/batch-api). OpenAI batch jobs can take up to 48h at peak. — [TokenMix](https://tokenmix.ai/blog/openai-batch-api-pricing)
- Example list prices per 1M tokens (input/output), from aggregators that conflict on model names:
  - GPT-5.4 Mini $0.75/$4.50 (batch $0.375/$2.25)
  - GPT-5.4 Nano $0.20/$1.25 (batch $0.10/$0.625)
  - Gemini 2.5 Flash-Lite $0.10/$0.40
  - Gemini 2.5 Flash $0.30/$2.50
  - DeepSeek V4-Flash $0.14/$0.28 before an Aug 2026 change; V4.1 Flash at peak $0.30 miss / $0.006 cache-hit / $1.20 output, halved off-peak
  
  — [CloudZero](https://www.cloudzero.com/blog/llm-api-pricing-comparison/); [TokenMix](https://tokenmix.ai/blog/openai-batch-api-pricing); [CloudZero DeepSeek](https://www.cloudzero.com/blog/deepseek-pricing/); [pricepertoken](https://pricepertoken.com/pricing-page/model/deepseek-deepseek-v4-pro)
- Chinese tokenization: GPT-4/GPT-5/Claude tokenizers give roughly 1 token per Chinese character. — [UNM explainer](https://stat.unm.edu/~ronald/ALFF/tokenization_explorer.html). On o200k the Chinese:English token ratio is mostly 1.0–1.35×. — [Lee Han Chung blog](https://leehanchung.github.io/blogs/2024/05/15/gpt-4o-tokenizer/). Other measurements conflict (0.97 to ~2.7 characters per token depending on tokenizer and corpus), and none isolates Traditional Chinese. — [arXiv 2604.14210](https://arxiv.org/pdf/2604.14210)
- Published pipelines use either headlines only (Lopez-Lira & Tang: one-line prompt per headline) or full articles and embeddings (Chen-Kelly-Xiu). An SSRN-page excerpt surfaced by search says full articles carry more information than alerts or headlines, and that AI-generated summaries predict best. This was not verified against the paper because SSRN returned 403. The SJTU seminar abstract likewise ties the LLM edge to reading the full article. — [arXiv v6](https://arxiv.org/html/2304.07619v6); [SSRN 4416687](https://ssrn.com/abstract=4416687); [SJTU seminar abstract](https://acem.sjtu.edu.cn/academic/82735.html)
- Cheaper alternatives with evidence:
  - Distilling an LLM teacher into a compact classifier (Buy the Rumor, 4.57M articles). — [arXiv 2608.14014](https://arxiv.org/abs/2608.14014)
  - 124M-parameter ChronoGPT matching a much larger Llama on Sharpe. — [arXiv 2502.21206](https://arxiv.org/abs/2502.21206)
  - Erlangshen-110M beating ChatGPT on Chinese news. — [arXiv 2306.14222](https://arxiv.org/pdf/2306.14222)
  - MiniLM embeddings giving overnight drift Sharpe 1.42 vs GPT-4's 2.97. — [arXiv v6](https://arxiv.org/html/2304.07619v6)

### Inferences
- Worked estimate (my arithmetic, assumptions stated):
  - Volume: 150 stocks × ~3–5 items/day (news plus MOPS 重訊) ≈ 450–750 items/day.
  - Tokens: full article ≈ 1,000–2,000 Chinese characters ≈ 1,000–2,000 input tokens, plus ~300 tokens of cached instructions. Output is a short JSON score/event tag (~50 tokens). Total ≈ 0.5–1.5M input and ~40K output tokens/day.
  - Cost per day: about $0.10–0.25 on Nano/Flash-Lite/DeepSeek-Flash-class models, ~$1.2 on a "mini" model at standard price, ~$0.6 with batch. Headlines only (≈40 tokens each) cut this by roughly 20–40×.
  - Embeddings: with a local open encoder they cost only local GPU/CPU time.
- Historical backfill: 10 years × 250 days × 750 items ≈ 1.9M articles ≈ 2–4B input tokens, about $200–400 on Nano/Flash-Lite-class or $1.5–3K on a mini-class model at standard price (halved with batch). Because a backfill before the model's cutoff is contaminated (§3), much of that spend buys an in-sample number. Forward recording from today, or a small chronologically safe local encoder for history, gives better value.
- Batch APIs (24–48h turnaround) suit end-of-day or weekly decisions. They are not suitable for intraday reaction, which the evidence says is anyway untradable at Taiwan retail costs.

### Gaps
- Official provider pricing pages were not checked directly. The model names and prices above come from third-party aggregators that conflict with one another, so confirm before budgeting.
- No measured tokens-per-character figure for Traditional Chinese financial news on current tokenizers was found. Measure it on a sample with the provider's token counter.
- No source was found on the cost of commercial Taiwan news feeds or on licensing for LLM processing of news text. The MOPS announcements themselves are free and public.
