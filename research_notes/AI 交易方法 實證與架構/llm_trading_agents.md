# LLM trading agents (single and multi-agent), live/real-money LLM trading contests, and their critical evaluations (2023 to Oct 2026)

Notes compiled 2026-10-09. Every number is dated by its test window. Labels used below:
- **[BACKTEST]** = historical simulation written by the method's own authors (in-sample in the sense that the window usually sits inside the LLM's pretraining data).
- **[FORWARD-PAPER]** = forward / live data, simulated money.
- **[LIVE-MONEY]** = real capital on a real venue.
- **[CRITIQUE]** = third-party re-evaluation or bias study.

---

## Q1. Architectures and reported results of TradingAgents, FinMem, FinAgent, FinCon, FinRobot and similar frameworks (2023–2026)

### Takeaway
The flagship agent papers report very large outperformance, for example TradingAgents Sharpe 5.6–8.2 and FinCon +82.9% on TSLA. All of it comes from short author-run backtests: 3–8 months, 3–8 mega-cap US names, mostly naive baselines, and windows that mostly sit before or around the backbone LLM's training cutoff. None of the flagship frameworks published a live-money track record.

### Cited Findings

**TradingAgents (Tauric Research / UCLA et al.; arXiv 2412.20138, Dec 2024) [BACKTEST]**
- Architecture. The design mimics a trading firm:
  - Analyst team: fundamentals, sentiment, news and technical analysts write structured reports.
  - Bull and bear researchers debate for *n* rounds, with a facilitator.
  - A trader agent decides timing and size.
  - A risk-management team (risk-seeking, neutral and conservative) deliberates for *n* rounds.
  - A fund manager approves or updates the trade.
  - All agents use ReAct. They communicate through structured reports and use natural language only in debates. — [TradingAgents paper](https://arxiv.org/html/2412.20138)
- LLMs. "Quick-thinking" gpt-4o-mini / gpt-4o handle summarisation and retrieval. "Deep-thinking" o1-preview handles the analysts, researchers and trader. — [TradingAgents paper](https://arxiv.org/html/2412.20138)
- Data: prices, news, social media (Reddit, X), insider transactions (SEDI), financial statements and 60 technical indicators, from Yahoo, EODHD, FinnHub and Bloomberg. — [TradingAgents paper](https://arxiv.org/html/2412.20138)
- Test window: **1 Jan – 29 Mar 2024 (about 3 months)**. Results are reported for only **AAPL, GOOGL, AMZN**. The setup lists AAPL, NVDA, MSFT, META and GOOGL; AMZN appears only in the results table, and NVDA, MSFT and META have no reported results. — [TradingAgents paper](https://arxiv.org/html/2412.20138)
- Reported results. Format is CR% / annualised return % / Sharpe / MDD%:
  - AAPL: 26.62 / 30.5 / **8.21** / 0.91, against Buy-and-Hold −5.23 / −5.09 / −1.29 / 11.90.
  - GOOGL: 24.36 / 27.58 / **6.39** / 1.69, against B&H 7.78 / 8.09 / 1.35 / 13.04.
  - AMZN: 23.21 / 24.90 / **5.60** / 2.11, against B&H 17.1 / 17.6 / 3.53 / 3.80.
  - The baselines were only B&H, MACD, KDJ+RSI, ZMR and SMA. — [TradingAgents paper](https://arxiv.org/html/2412.20138)
- The authors themselves say the Sharpe ratio is "above expected range". They attribute it to few drawdowns in the window. — [TradingAgents paper](https://arxiv.org/html/2412.20138)
- Transaction costs are not stated.
- The backtest was limited to about 3 months "because of intensive LLM and tool use". — [TradingAgents paper](https://arxiv.org/html/2412.20138)
- The paper claims no look-ahead because agents see data only up to each day. It does not address whether the LLM's pretraining contains the test period. — [TradingAgents paper](https://arxiv.org/html/2412.20138)

**FinMem (Yu et al., Stevens; arXiv 2311.13743, Nov 2023) [BACKTEST]**
- Architecture: three modules. Profiling sets the agent's "character" and risk preference. Layered memory processes hierarchical financial data with an adjustable "cognitive span". Decision-making turns memory into trades. — [FinMem abstract](https://arxiv.org/abs/2311.13743)
- Originally reported window: **6 Oct 2022 – 10 Apr 2023 (about 6 months)** on TSLA, NFLX, AMZN and MSFT. Format is Sharpe / CR%:
  - TSLA 2.679 / 61.78%, against B&H −0.342 / −20.48%.
  - NFLX 2.017 / 36.45%, against B&H 1.326 / 43.08%.
  - AMZN 0.233 / 4.89%, against B&H −0.460 / −13.25%.
  - MSFT 1.440 / 23.26%, against B&H 0.974 / 21.17%.
  - The originally reported figures are as quoted in FINSABER. — [FINSABER](https://arxiv.org/html/2505.07078v5)
- No peer-reviewed venue is listed on the arXiv page. The arXiv abstract does not name the LLM backbone or the test period. — [FinMem abstract](https://arxiv.org/abs/2311.13743)

**FinAgent (Zhang et al.; arXiv 2402.18485, Feb 2024, v3 Jun 2024) [BACKTEST]**
- Architecture: a multimodal "foundation agent". A market-intelligence module ingests numeric, text and visual data (for example, chart images). Other parts are dual-level reflection, diversified memory retrieval, tool augmentation, and injected expert strategies. — [FinAgent abstract](https://arxiv.org/abs/2402.18485)
- Claims across 6 datasets (stocks and crypto) and 9 baselines:
  - an "over 36% average improvement on profit";
  - a 92.27% return on one dataset, an 84.39% relative improvement. — [FinAgent abstract](https://arxiv.org/abs/2402.18485)
- The arXiv page lists no peer-reviewed venue. The abstract gives neither the test window nor the backbone LLM. — [FinAgent abstract](https://arxiv.org/abs/2402.18485)

**FinCon (Yu et al., Stevens/Harvard/The FinAI; NeurIPS 2024; arXiv 2407.06567) [BACKTEST]**
- Architecture:
  - A manager–analyst hierarchy: 7 analyst agents, each on one source (news, filings, earnings-call audio, prices, stock selection), and a single manager agent that trades.
  - Within-episode risk control by **CVaR on the worst 1% of daily PnL**. A CVaR drop or a negative daily PnL triggers a risk-averse stance and self-reflection.
  - Across episodes, **Conceptual Verbal Reinforcement (CVRF)**: it compares consecutive training-episode trajectories and rewrites agent prompts ("textual gradient descent").
  - Working, procedural and episodic memory. — [FinCon paper](https://arxiv.org/html/2407.06567v3)
- Backbone: **GPT-4-Turbo** for all agents, temperature 0.3. — [FinCon paper](https://arxiv.org/html/2407.06567v3)
- Training ran **3 Jan – 4 Oct 2022**. Testing ran **5 Oct 2022 – 10 Jun 2023 (about 8 months)**. Results are the median of 5 runs. — [FinCon paper](https://arxiv.org/html/2407.06567v3)
- Single-stock results in the test window. Format is CR% / Sharpe / MDD%:

  | Stock | FinCon | Buy & Hold |
  |---|---|---|
  | TSLA | 82.87 / 1.97 / 29.7 | 6.43 / 0.15 / 58.2 |
  | AMZN | 24.85 / 0.90 / 25.9 | 2.03 / 0.07 / 34.2 |
  | NIO | 17.46 / 0.34 / 40.6 | −77.2 / −1.45 / 64.0 |
  | MSFT | 31.63 / 1.54 / 15.0 | 27.86 / 1.23 / 15.0 |
  | AAPL | 27.35 / 1.60 / 15.3 | 22.32 / 1.11 / 20.7 |
  | GOOG | 25.08 / 1.05 / 17.5 | 22.42 / 0.89 / 21.2 |
  | NFLX | 69.24 / 2.37 / 20.8 | 57.34 / 1.79 / 20.9 |
  | COIN | 57.05 / 0.83 / 42.7 | −21.76 / −0.31 / 60.2 |

  The same paper's TSLA baselines are FinMem 34.6% and FinAgent 11.96%. — [FinCon paper](https://arxiv.org/html/2407.06567v3)
- Three-stock portfolios:
  - TSLA / MSFT / PFE: FinCon CR 113.8%, Sharpe 3.27, against Markowitz 12.6% and equal-weight 9.3%.
  - AMZN / GM / LLY: FinCon 32.9% / Sharpe 1.37, against equal-weight 15.1%. FinCon's MDD here (21.5%) is *worse* than equal-weight (14.7%), despite the caption claiming it "leads all metrics". — [FinCon paper](https://arxiv.org/html/2407.06567v3)
- Ablation. Removing CVaR drops portfolio-1 CR from 113.8% to 14.7%, and GOOG from 25.1% to −1.5%. That fragility shows how much the headline depends on one component in one window. — [FinCon paper](https://arxiv.org/html/2407.06567v3)
- The paper does not discuss GPT-4-Turbo's knowledge cutoff versus the 2022–23 test window. It reports no variance across seeds, and it scaled only to 3-asset portfolios. — [FinCon paper](https://arxiv.org/html/2407.06567v3)

**Agent Market Arena's reference agents (Oct 2025) [FORWARD-PAPER]**
- AMA implements 4 agent designs:
  - InvestorAgent, a single-agent baseline;
  - TradeAgent and HedgeFundAgent, multi-agent designs with different risk styles;
  - DeepFundAgent, which uses memory-based reasoning.
- Each runs on GPT-4o, GPT-4.1, Claude-3.5-haiku, Claude-sonnet-4 and Gemini-2.0-flash.
- Headline: **agent design explains more of the outcome spread than the backbone LLM.** — [AMA paper](https://arxiv.org/abs/2510.11695)

### Inferences
- The test windows have a common shape: 2022-10 to 2023-06 for FinMem and FinCon, and Q1 2024 for TradingAgents.
  - These are 3–8-month windows on 3–8 famous mega-caps.
  - The flagship gains are concentrated where buy-and-hold was negative or flat (TSLA, NIO, COIN, AAPL in Q1 2024). There, a strategy that is merely more often in cash or short looks spectacular.
- FinCon and FinMem tested on 2022–23 with GPT-4-class models. Their test windows likely overlap pretraining data; FINSABER and Profit Mirage (Q3) state this risk explicitly.
- For TradingAgents, GPT-4o's documented cutoff is Oct 2023 (per [Lopez-Lira et al.](https://arxiv.org/html/2504.14765v2)). So the Q1 2024 window is plausibly *post-cutoff* for gpt-4o, which is a point in its favour.
- Even so, Profit Mirage still measured a 50–56% return/Sharpe decay for TradingAgents when moving to a later post-cutoff window (Q3). The result also rests on 3 stocks over 3 months with no stated costs.
- None of the frameworks is benchmarked against a "same cash-flow index fund" baseline of the kind this project uses (0050 with monthly contributions). Their baselines are single-stock buy-and-hold or simple technical rules.

### Gaps
- **FinRobot (AI4Finance)**: I found no trading-performance results. It is generally described as an open-source agent platform for financial analysis and research reports, not a backtested trader. That description is not verified in this pass.
- Exact backbone LLM and test window for FinAgent and FinMem as stated in their own papers: the arXiv abstracts omit them. FinMem's window and stocks are known only via FINSABER's quotation.
- InvestorBench (2024–25) was not researched in this pass.
- TradingAgents' later arXiv versions (v6 exists) may have extended results. I did not read the final ~17% of the paper.

---

## Q2. Real-money and live-forward contests and benchmarks: who won or lost, by how much, and what was learned about behaviour

### Takeaway
In every live or forward evaluation found, results were short (2–10 weeks), noisy and inconsistent across markets and runs:
- In the one real-money crypto contest (Alpha Arena S1, Oct–Nov 2025), 4 of 6 frontier models lost 42–59%.
- In the US-stock round (S1.5), the best model made about +12% in about 2 weeks.
- Forward paper-trading benchmarks find most models roughly match or trail passive baselines. General capability (LMArena) does not predict trading performance.
- In China A-shares, no model beat the SSE-50 index.

### Cited Findings

**Alpha Arena Season 1 (nof1.ai) [LIVE-MONEY], crypto perpetuals on Hyperliquid**
- Setup:
  - 6 LLMs got **$10,000 of real money each** to trade crypto perpetual futures on Hyperliquid.
  - Line-up: Claude Sonnet 4.5, DeepSeek V3.1 Chat, Gemini 2.5 Pro, GPT-5, Grok 4 and Qwen3 Max.
  - The models received **only numerical market inputs, with no news or sentiment**. The founder said LLMs "do not handle numerical time series data well". — [ForkLog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/); [GNcrypto](https://www.gncrypto.news/ru/news/qwen-wins-alpha-arena-season-1-with-22-percent-returns/)
- Dates: start **17/18 Oct 2025**, end **3 Nov 2025** per ForkLog and the founder. One outlet says 5 Nov. — [ForkLog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)
- **Final account values per ForkLog.** The return column is my arithmetic on the $10k start:
  1. Qwen3 Max: **$12,231 (+22.3%)**. Qwen reportedly overtook DeepSeek only at the very end.
  2. DeepSeek V3.1: **$10,489 (+4.9%)**. DeepSeek had been above $13,000 earlier in the season.
  3. Claude Sonnet 4.5: **$5,799 (−42.0%)**.
  4. Gemini 2.5 Pro: **$5,445 (−45.6%)**.
  5. Grok 4: **$4,208 (−57.9%)**.
  6. GPT-5: **$4,126 (−58.7%)**. — [ForkLog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)
- Conflicting figures:
  - One report lists Claude at −30.8%.
  - A Taiwanese outlet puts GPT-5 at more than −62%.
  - The differences likely reflect different snapshot times. — [TechNews.tw](https://technews.tw/2025/11/04/ai-large-model-trading-contest-qwen-winner/); [ForkLog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)
- An early-season snapshot was widely shared: "Western models lose 80% of capital in one week". It does not reflect final standings. — [Bitcoin Magazine](https://bitcoinmagazine.com/business/alpha-arena-reveals-ai-trading-flaws-western-models-lose-80-capital-in-one-week)
- A mid-season snapshot (not final) from a third-party blog gave trade counts and leverage. These are very low trade counts, with high leverage per position:
  - DeepSeek: 5 trades, a 15× SOL long.
  - Qwen: 8 trades, moderate leverage, with a BNB hedge.
  - Grok 4: 7 trades, ≤5×.
  - Claude: 9 trades, a 20× ETH long.
  - Gemini: 10 trades, a 10× XRP long.
  - GPT-5: 12 trades, 10–15× DOGE/XRP shorts. — [iWeaver blog (JA)](https://www.iweaver.ai/ja/blog/alpha-arena-ai-trader-showdown/)
- Another summary gives a 10–20× leverage band. — [HelloACM](https://helloacm.com/alpha-arena-how-ai-performs-in-the-real-crypto-market/)
- Organisers' and press caveats: the test's "short duration, modest capital, and absence of statistical controls" mean results "should not be viewed as a definitive ranking". The organisers also framed the contest as revealing differences in risk, sizing, holding time, and sensitivity to small prompt changes. — [ForkLog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)

**Alpha Arena Season 1.5 [LIVE-MONEY], US equities**
- Launched **19 Nov 2025** with about **$320K deployed** across several parallel competitions. Each model started with $10,000. New entrants were Kimi 2 and an anonymous "mystery model". — [ForkLog, new season](https://forklog.com/en/new-season-of-ai-trading-tournament-kicks-off/)
- Four modes ran in parallel:
  - **New Baseline**: news plus market data, memory and self-learning.
  - **Monk Mode**: capital preservation and risk focus.
  - **Situational Awareness**: models see competitors' P&L and ranks.
  - **Max Leverage**: high leverage mandatory on every trade. — [ForkLog, 8 Dec 2025](https://forklog.com/en/ai-model-grok-4-2-triumphs-in-trading-tournament/)
- Result. The round was announced as ended on **5 Dec 2025**.
  - Winner: the "Mystery Model", later identified as an experimental **Grok 4.20**. It was **about +12% on average** and profitable in all four modes.
  - **GPT-5.1** placed 2nd and **Gemini 3** 3rd. Exact returns for the others were not given.
  - As of 8 Dec 2025, Grok 4.20 was the only model still profitable. — [ForkLog, 8 Dec 2025](https://forklog.com/en/ai-model-grok-4-2-triumphs-in-trading-tournament/)
  - Techbloat gives Grok 4.20 **+12.11% for 19 Nov – 3 Dec 2025**. — [Techbloat](https://www.techbloat.com/?p=1890791)
- Season 2 plans. nof1 raised **$15M**, co-led by SUI Group and the hedge fund Karatage, to fund Season 2 and a consumer AI-trading product. Season 2 is planned to add web search, extended reasoning and multi-step execution per model. — [AI Weekly](https://aiweekly.co/alerts/nof1-raises-15m-to-run-ai-models-in-live-trading)

**StockBench (Chen et al.; arXiv 2510.02209, v1 Oct 2025, v2 2 Mar 2026) [FORWARD-PAPER, contamination-free window]**
- Setup:
  - Window: **3 Mar – 30 Jun 2025 (82 trading days)**.
  - Universe: the **20 highest-weight DJIA stocks**.
  - Starting capital: $100k cash.
  - Daily decisions at the open, using prices, fundamentals and news.
  - **3 seeds per model, averaged**.
  - **No transaction costs or slippage**. — [StockBench v2](https://arxiv.org/html/2510.02209v2)
- Results. Format is final return / MDD / Sortino; the baseline is equal-weight buy-and-hold:

  | Model | Return | MDD | Sortino | Rank |
  |---|---|---|---|---|
  | Kimi-K2 | 1.9% | −11.8% | 0.042 | **1st** |
  | Qwen3-235B-Ins | 2.4% | −11.2% | — | 2nd |
  | GLM-4.5 | 2.3% | −13.7% | — | 3rd |
  | Qwen3-235B-Think | 2.5% | −14.9% | — | 4th |
  | o3 | 1.9% | −13.2% | — | 5th |
  | Claude-4-Sonnet | 2.2% | −14.2% | — | 7th |
  | GPT-5 | 0.3% | −13.1% | — | 9th |
  | **Equal-weight B&H** | **0.4%** | **−15.2%** | — | **12th** |
  | GPT-OSS-120B | −0.9% | — | — | 13th |
  | GPT-OSS-20B | −2.8% | — | — | 14th |

  — [StockBench v2](https://arxiv.org/html/2510.02209v2)
- **Regime split.** In a **downturn (Jan–Apr 2025) all LLM agents underperformed passive.** In an **upturn (May–Aug 2025) all outperformed**. Rankings reshuffled considerably between the two. — [StockBench v2](https://arxiv.org/html/2510.02209v2)
- Other findings:
  - Reasoning ("Think") models did not consistently beat instruct models.
  - More stocks meant higher variance and a lower mean return.
  - Removing news and fundamentals reduced returns. — [StockBench v2](https://arxiv.org/html/2510.02209v2)
- The abstract says "most models struggle to outperform the simple buy-and-hold baseline". That is in tension with the table, where most beat a 0.4% baseline by about 1–2 points over 4 months with no costs. — [StockBench abstract](https://arxiv.org/abs/2510.02209); [StockBench v2](https://arxiv.org/html/2510.02209v2)

**LiveTradeBench (Yu, Li, You, UIUC; arXiv 2511.03628, Nov 2025) [FORWARD-PAPER, live stream]**
- Setup:
  - Live run **18 Aug – 24 Oct 2025, 50 trading days**.
  - US stocks: 15 large caps (AAPL, MSFT, NVDA, META, JPM, V, XOM, CAT, TSLA, PG, KO, AMZN, WMT, JNJ, UNH) plus cash.
  - **Polymarket**: 10 binary markets.
  - Agents output target portfolio weights.
  - **21 LLMs**, with no fees, spread or slippage. — [LiveTradeBench paper](https://arxiv.org/html/2511.03628v1)
- Stock results. Cumulative returns ranged **+1.78% (Qwen3-235B-Thinking) to +6.25% (GPT-4.1)**. Selected models, as CR / Sharpe / MDD:
  - GPT-4.1: 6.25% / 2.64 / 1.92%.
  - o3: 6.04% / 2.57 / 2.27%.
  - GPT-5: 5.31% / 2.19 / 2.53%.
  - Qwen2.5-72B: 5.15% / 2.18 / 2.22%.
  - Grok-4: 4.30% / 1.75 / 1.92%.
  - Claude-Opus-4: 3.93% / 1.72 / 2.11%.
  - Gemini-2.5-Pro: 1.95% / 0.61 / 2.85%. — [LiveTradeBench paper](https://arxiv.org/html/2511.03628v1)
- **Polymarket results were mostly large losses**:
  - Qwen3-235B-Thinking −57.6%.
  - o3 −54.8%.
  - Kimi-K2 −53.4%.
  - Claude-Sonnet-4 −40.3%.
  - The best were Claude-Sonnet-3.7 at +20.5% and Grok-4 at +7.4%. — [LiveTradeBench paper](https://arxiv.org/html/2511.03628v1)
- **There is no passive baseline (SPY or equal-weight) in the paper.**
- Spearman correlation between LMArena rank and cumulative return is **0.054 for stocks and −0.38 for Polymarket**. The Sharpe correlation across the two markets is about 0.
- Reasoning models did not outperform and were more volatile on Polymarket. Delaying decisions reduced returns, which suggests the agents do use live signals. — [LiveTradeBench paper](https://arxiv.org/html/2511.03628v1)
- Open-source platform: [GitHub ulab-uiuc/live-trade-bench](https://github.com/ulab-uiuc/live-trade-bench).

**Agent Market Arena (AMA; arXiv 2510.11695, Oct 2025) [FORWARD-PAPER]**
- AMA describes itself as "the first lifelong, real-time" multi-market benchmark. It paper-trades TSLA, BMRN, BTC and ETH, with a daily market digest that experts verify. Fees are not included.
- Finding: agent frameworks show distinct risk styles, and the backbone LLM matters less than the agent design. Numeric leaderboard values were not retrieved. — [AMA paper](https://arxiv.org/abs/2510.11695)

**AI-Trader (HKU; arXiv 2512.10971, Dec 2025) [FORWARD-PAPER], including a China A-share leg**
- Windows and baselines:
  - **Nasdaq-100, hourly, 1 Oct – 7 Nov 2025**, against QQQ.
  - **SSE-50 A-shares, daily, same window**, against the SSE-50.
  - 10 crypto pairs, daily, 1–14 Nov 2025, against the CD5 index.
- Agents get only holdings, prices and tools, and must fetch information themselves. — [AI-Trader paper](https://arxiv.org/html/2512.10971v1)
- US results, as CR / Sortino / MDD. Three models beat QQQ, but with much higher volatility (about 25% versus 17%):
  - QQQ: 1.87% / 1.51 / −3.9%.
  - MiniMax-M2: 9.56% / 4.42 / −4.9%.
  - DeepSeek-v3.1: 8.39% / 3.73 / −8.6%.
  - Claude-3.7-Sonnet: 3.11% / 1.13 / −8.1%.
  - GPT-5: 1.56% / 0.70 / −10.6%.
  - Qwen3-Max: 0.39% / 0.32 / −9.4%.
  - Gemini-2.5-Flash: −0.06% / 0.09 / −7.7%. — [AI-Trader paper](https://arxiv.org/html/2512.10971v1)
- **A-share results. No model beat the SSE-50**, which returned 1.65% with Sortino 2.19 and MDD −2.0%:
  - MiniMax-M2: 1.31%.
  - Claude-3.7: 0.84%.
  - DeepSeek-v3.1: −1.23%.
  - Gemini-2.5-Flash: −1.53%.
  - GPT-5: −3.53%.
  - Qwen3-Max: −3.86%.
  - DeepSeek went from best in the US to negative in China. The authors read this as weak cross-market generalisation. They argue excess returns come more easily in liquid markets than in "policy-driven" ones; that is an interpretation, not a test.
  - Gemini-2.5-Flash overtraded A-shares, reaching up to about 30 positions. — [AI-Trader paper](https://arxiv.org/html/2512.10971v1)
- Crypto, 1–14 Nov 2025. The CD5 index returned −14.3%. Models ranged from −12.2% (DeepSeek) to −18.6% (Gemini-2.5-Flash), so most did worse than the index. — [AI-Trader paper](https://arxiv.org/html/2512.10971v1)
- Single run, with no reported variance across repeats. — [AI-Trader paper](https://arxiv.org/html/2512.10971v1)

**TraderBench (ICLR 2026) [CRITIQUE / adversarial simulation]**
- 12 models (8B open models up to frontier models), about 50 tasks. Crypto trading is tested under 4 increasingly severe market-manipulation transforms. An options track scores P&L, Greeks and risk.
- **7 of 12 models scored about 33 on crypto, with less than 1 point of change across adversarial conditions.** The authors read this as fixed, non-adaptive strategies.
- **Extended thinking added +26 on retrieval but +0.3 on crypto trading and −0.1 on options.** — [TraderBench (ICLR 2026)](https://iclr.cc/virtual/2026/10016348)

**System-level behaviour (Sep 2026)**
- Ross, So, De Simone, Pozniak and Lo (arXiv 2609.04373, 3 Sep 2026) ran an agent-based market simulation.
  - **Frontier LLM traders show correlated behaviour that increases with capability.** That correlation helps when shared reasoning is right and becomes a non-diversifiable risk floor under shared misinformation, which the authors call a "capability paradox".
  - The models used and the numbers are not given on the abstract page. — [Ross et al. 2026](https://arxiv.org/abs/2609.04373)

### Inferences
- Alpha Arena S1 in aggregate: $60,000 in, $42,298 out, or **−29.5% across all six models** (arithmetic on ForkLog's figures). The winner's +22% over about 2.5 weeks with 10–20× leverage is statistically indistinguishable from luck.
- The S1 ordering (Qwen first, GPT-5 last) does not match S1.5, where GPT-5.1 came 2nd. It also does not match AI-Trader, where Qwen3-Max was near the bottom in the US and last in A-shares and crypto. Model rank is not stable across venues or rounds.
- The forward paper-trading benchmarks of 2025 (StockBench, LiveTradeBench, AI-Trader US leg) all ran in rising US large-cap markets with no costs.
  - Small positive returns there are mostly market beta.
  - StockBench's own regime split (all agents lose to passive in the Jan–Apr 2025 downturn) matches FINSABER's "too aggressive in bears" finding (Q3).
- The only Asian-market evidence found is AI-Trader's 5-week SSE-50 leg, where every model trailed the index. That is weak but negative evidence for East Asian large caps. I found nothing on Taiwan (TWSE).
- Recurring behavioural themes across sources:
  - Overtrading by some models (GPT-5 in Alpha Arena per some reports, Gemini-2.5-Flash in A-shares).
  - High leverage concentration in crypto.
  - Strong run-to-run and prompt sensitivity, as acknowledged by nof1, StockBench's multi-seed averaging and TraderBench's flat scores.
  - Reasoning effort does not translate into trading skill (StockBench, LiveTradeBench, TraderBench).

### Gaps
- **Official nof1 leaderboard and blog numbers** (per-model fees, trade counts, Sharpe and holding times for S1 and S1.5) could not be fetched. The nof1 blog URL returned 404. All Alpha Arena figures here are from secondary press (ForkLog, TechNews, Techbloat).
- **No evidence found of an Alpha Arena Season 2 having run or reported results in 2026.** Searches returned only the $15M raise and plans. Status as of Oct 2026 is unknown.
- Behavioural anecdotes from S1 secondary coverage could not be tied to a verified page, so they are omitted above: Gemini persistently shorting everything, Grok holding a 10× DOGE long, Qwen's early 20× BTC long, and GPT-5 described as both "largely inactive" and a "restless scalper".
- One search summary mentioned a separate Alpha Arena-style US-stock contest from 23 Oct 2025, where Claude Sonnet 4.5 was best at +4.75% as of 12 Nov 2025. I could not verify the source.
- AMA's per-agent numeric results and current leaderboard state were not retrieved.
- A third-party GitHub repo (sfwn/AI-Trader) showed a 24 Oct 2025 NASDAQ snapshot, with DeepSeek +10.6% against QQQ +2.3%. It is not confirmed as official, so it is not used.
- No live LLM-agent evaluation on Taiwan, Japanese or Korean equities was found.

---

## Q3. Critical evaluations: does the LLM advantage survive longer periods and broader universes? Look-ahead and memorisation, survivorship and selection bias, and the protocol critics recommend

### Takeaway
When re-tested over 20 years and 60–90-stock survivorship-free universes, FinMem and FinAgent **underperform buy-and-hold on Sharpe and show no significant alpha** (FINSABER).

Separately, LLMs demonstrably memorise pre-cutoff prices, index levels, macro prints and headline dates. Agent returns fall **50–72%** once tests move past the training cutoff (Profit Mirage). Prompting the model to "ignore the future" and masking names both fail.

Critics converge on this protocol:
- post-cutoff or live-forward data as the *primary* test;
- long windows across regimes;
- broad, point-in-time universes;
- passive and quant baselines with costs;
- multiple seeds and significance tests;
- counterfactual or memorisation probes.

### Cited Findings

**FINSABER: "Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?" (Li, Kim, Cucuringu, Ma; arXiv 2505.07078, May 2025, v5) [CRITIQUE]**
- Motivation: prior LLM-strategy papers typically use less than 1 year, fewer than 10 stocks, and only naive baselines such as buy-and-hold. — [FINSABER abstract](https://arxiv.org/abs/2505.07078)
- Data:
  - Daily prices for 7,000+ US equities, 2000–2024, **including delisted historical S&P 500 members**.
  - 15.7M news records.
  - 10-K and 10-Q filings.
  - Commission of $0.0049 per share (minimum $0.99), long-only, with rolling windows. — [FINSABER](https://arxiv.org/html/2505.07078v5)
- **Reproduction of FinMem's own window (Oct 2022 – Apr 2023) with GPT-4o-mini.** Format is Sharpe / CR, compared with FinMem's originally reported numbers:
  - TSLA: 0.927 / 19.9%, against the reported 2.679 / 61.8%.
  - NFLX: 1.704 / 32.5%, against the reported 2.017 / 36.4%.
  - AMZN: 0.297 / 2.8%, against the reported 0.233 / 4.9%.
  - **MSFT: −0.554 / −7.1%**, against the reported 1.440 / 23.3%.
  - So the gains were already smaller in reproduction, and below B&H on NFLX and MSFT. — [FINSABER](https://arxiv.org/html/2505.07078v5)
- **Same 4 stocks, extended to 2004–2024.** Format is Sharpe / annual return:
  - NFLX: B&H 0.622 / 23.9%; FinMem 0.293 / 12.6%; FinAgent −0.419 / 22.5%.
  - AMZN: B&H 0.551 / 16.0%; FinMem 0.188 / 5.7%; FinAgent 0.364 / 12.7%.
  - MSFT: B&H 0.461 / 11.2%; FinMem 0.203 / 4.6%; FinAgent 0.285 / 11.1%.
  - Only TSLA had FinMem marginally above B&H (0.641 against 0.630). — [FINSABER](https://arxiv.org/html/2505.07078v5)
- **Composite universes, 2004–2024, symbols re-selected each window from historical S&P 500 constituents.** Format is Sharpe / annual return / MDD:

  | Universe | Buy & Hold | FinMem | FinAgent | Best simple quant baseline |
  |---|---|---|---|---|
  | Random Five (91 symbols) | 0.315 / 6.7% / −35.1% | **−0.253 / −0.1%** / −24.2% | 0.094 / 4.5% / −28.1% | ARIMA 0.255 / 6.9% / −21.7% |
  | Momentum (84) | 0.384 / 9.9% / −32.6% | 0.025 / 3.6% / −23.3% | 0.104 / 14.0% / −20.7% | ARIMA 0.542 / 13.3% / −18.3% |
  | Volatility Effect (63) | 0.703 / 7.9% / −14.1% | −0.228 / 4.1% / −10.9% | 0.241 / 5.0% / −10.3% | PPO 0.514 / 5.8% / −8.8% |
  | FinCon selector (80) | 0.389 / 6.9% / −30.9% | −0.292 / −1.7% / −20.8% | −0.076 / 5.2% / −15.6% | ARIMA 0.532 / 10.7% / −16.0% |

  — [FINSABER](https://arxiv.org/html/2505.07078v5)
- Paired t-tests of B&H against the LLM agents: p = 3e-6 to 0.012, so the agents are significantly worse.
- **Alpha.** Neither LLM agent's alpha was significant (all p > 0.34). FinMem's alpha was negative in every scenario. FinAgent's +6.57% Momentum alpha had p = 0.345. — [FINSABER](https://arxiv.org/html/2505.07078v5)
- **Regimes.** Sharpe by regime:
  - Bull years (S&P 500 ≥ +20%): B&H 0.61, FinAgent 0.12, FinMem −0.19.
  - Bear years (≤ −20%): B&H −0.28, FinAgent −0.38, FinMem −0.97.
  - The authors' reading: LLM agents are "overly conservative in bull markets … overly aggressive in bear markets". — [FINSABER](https://arxiv.org/html/2505.07078v5)
- **Biases named.**
  - Survivorship: popular test stocks such as TSLA and AMZN are "historical winners", which embeds survivorship and look-ahead bias.
  - GPT-4o may have seen the test data. The authors argue any leakage would *favour* the LLMs, which makes their negative result conservative.
  - Recommendation: prioritise trend detection and regime-aware risk control over adding framework complexity. — [FINSABER](https://arxiv.org/html/2505.07078v5)

**Profit Mirage (Li, Zeng, Xing, Xu, Xu; arXiv 2510.07920, Oct 2025) [CRITIQUE]**
- Five agents were tested with **GPT-4o (cutoff Oct 2023)**: FinMem, FinAgent, QuantAgent, FinCON and TradingAgents.
- Windows on NASDAQ-100 stocks, chosen to have similar market returns (+13.79% against +13.35%):
  - **pre-cutoff Q2–Q3 2021**;
  - **post-cutoff Q3–Q4 2024**. — [Profit Mirage](https://arxiv.org/html/2510.07920v1)
- **Post-cutoff decay:**
  - Sharpe fell **51.5% (QuantAgent) to 62.2% (FinCON)**, with TradingAgents at 55.7%.
  - Total return fell **50.2% (TradingAgents) to 71.9% (FinMem)**. — [Profit Mirage](https://arxiv.org/html/2510.07920v1)
- Counterfactual perturbation of inputs:
  - Prediction consistency stays high (0.69–0.82), meaning outputs barely change when the inputs change.
  - The input-dependency score is low (0.28–0.38).
  - FinMem is the most "memory-driven" agent; TradingAgents is the least. — [Profit Mirage](https://arxiv.org/html/2510.07920v1)
- **FinLake-Bench** has 2,000 dated Q&A items covering Jan 2022 – Jun 2023. LLM accuracy was:
  - 85.4% on price queries;
  - 90.2% on trend questions;
  - 92.9% on event impacts.
  - This is direct evidence of memorisation. — [Profit Mirage](https://arxiv.org/html/2510.07920v1)
- Fine-tuning on financial data raised in-distribution accuracy (Qwen2.5-7B from 51.6% to 72.2%) but lowered accuracy on unseen data by about 18–22%. — [Profit Mirage](https://arxiv.org/html/2510.07920v1)
- A UT Austin McCombs commentary summarises this: the best agents "lose half their performance or more" and many become statistically zero after the cutoff. — [McCombs commentary](https://news.mccombs.utexas.edu/research/keeping-humans-in-the-loop/)

**"The Memorization Problem: Can We Trust LLMs' Economic Forecasts?" (Lopez-Lira, Tang, Zhu, Univ. of Florida; arXiv 2504.14765, Apr 2025, revised) [CRITIQUE]**
- Main model: **GPT-4o (gpt-4o-2024-08-06, cutoff Oct 2023)**, plus Llama-3.1-70B. — [Lopez-Lira et al.](https://arxiv.org/html/2504.14765v2)
- Pre- versus post-cutoff accuracy:

  | What is recalled | Pre-cutoff | Post-cutoff |
  |---|---|---|
  | Daily index level (S&P 500, DJIA, Nasdaq), no context | MAPE **0.53–1.80%**, direction **69–81%** | MAPE 13–20%, direction 44–49% |
  | Macro rates | MAE 0.03–0.15 pts | MAE 0.26–0.95 pts |
  | Nonfarm payrolls | MAPE **0.00%** | MAPE 97% |
  | WSJ headline year / exact date | 98.5% / 47.0% | 28.8% / 7.9% |
  | Earnings-call company identity (anonymised transcripts) | **100% for AAPL, META, MSFT**; above 85% for all Magnificent 7 | — |

  — [Lopez-Lira et al.](https://arxiv.org/html/2504.14765v2)
- **Instructions do not stop recall.** Telling the model to use only pre-2010 data still gave 98% threshold accuracy on GDP. A rolling "don't use knowledge after t−1" instruction left S&P 500 MAPE at 0.81%. — [Lopez-Lira et al.](https://arxiv.org/html/2504.14765v2)
- **Masking fails.** GPT-4o identified the firm (Ethan Allen), the quarter and the year from a fully anonymised transcript. — [Lopez-Lira et al.](https://arxiv.org/html/2504.14765v2)
- Recommended protocol:
  - Treat **post-cutoff data as the primary test, not a robustness check**, while noting that small post-cutoff samples have low power.
  - Run memorisation tests for each specific setting. A positive test is conclusive; a negative test is only a lower bound.
  - Classify tasks as future-invariant or not. Sentiment, risk and forecasting are not future-invariant.
  - Prefer models with temporal cutoffs.
  - Do not rely on prompts or anonymisation alone. — [Lopez-Lira et al.](https://arxiv.org/html/2504.14765v2)

**Glasserman & Lin, "Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis" (arXiv 2309.17322, Sep 2023; later in J. Financial Data Science) [CRITIQUE]**
- The authors anonymised company names in headlines before GPT sentiment scoring.
- In-sample, anonymised headlines did *better*. This points to a "distraction effect", where general knowledge about named firms biases the reading of tone, that outweighed look-ahead.
- Out-of-sample, look-ahead concerns faded but distraction remained. — [Glasserman & Lin](https://arxiv.org/abs/2309.17322)

**Look-ahead mitigation and point-in-time model work (2025–2026)**
- **Look-Ahead-Bench** (Benhenda; arXiv 2601.13770, Jan 2026):
  - Measures look-ahead bias through *alpha decay across temporally distinct regimes* in practical finance workflows, not Q&A probes.
  - Finds "significant lookahead bias" in Llama 3.1 8B/70B and DeepSeek 3.2.
  - Point-in-time "Pitinf" models generalise better as they scale.
  - Code at benstaf/lookaheadbench. No numeric decay figures are on the abstract page. — [Look-Ahead-Bench](https://arxiv.org/abs/2601.13770)
- **FinCAD, "Summoning the Oracle to Slay It"** (arXiv 2605.24564, May 2026). Per the search summary of the abstract; not fetched in full:
  - Defines "parametric look-ahead bias", meaning bias in the weights that input-side protections cannot remove.
  - Proposes inference-time context-aware decoding to suppress memorised outcomes.
  - Tested on five 7–14B models and five mega-caps. It **cuts in-sample returns by up to 67.1% on memorised dates**, while 2025 out-of-sample returns stay within $8K of baseline and Sharpe within 0.10.
  - On an 11-model leaderboard, it raised the in-sample versus out-of-sample rank correlation from 0.779 to 0.846. — [FinCAD](https://arxiv.org/abs/2605.24564)
- **Merchant & Levy** (Univ. of Chicago; arXiv 2512.06607, Dec 2025):
  - Argue that backtests of LLM predictive ability are "likely overly optimistic" because of memorised earnings and prices.
  - Propose decoding-time logit adjustment using two small models, one fine-tuned on information to forget and one on information to retain, instead of retraining with a cutoff. — [Merchant & Levy](https://arxiv.org/html/2512.06607v1)

**Live and forward designs as the critics' remedy**
- LiveTradeBench streams prices and news live, "eliminating dependence on offline backtesting and preventing information leakage". — [LiveTradeBench](https://arxiv.org/html/2511.03628v1)
- StockBench uses a recent window that is "continuously updated to avoid overlap with the training corpora". — [StockBench abstract](https://arxiv.org/abs/2510.02209)
- TraderBench refreshes its scenarios with new market data to limit contamination. — [TraderBench](https://iclr.cc/virtual/2026/10016348)

### Inferences
- The biases in agent papers form a consistent, layered stack:
  1. **Parametric look-ahead**: the model knows the outcome.
  2. **Selection and survivorship**: TSLA, AMZN, NFLX, COIN and other ex-post winners.
  3. **Window selection**: 3–8 months, often a downtrend for the tested stock, which flatters any de-risking rule.
  4. **Weak baselines**: single-stock buy-and-hold or simple technicals, with no index or quant factor model.
  5. **Missing frictions**: no costs, slippage or liquidity limits.
  6. **No variance reporting**: single seed or median of 5.
- Each layer biases results in the LLM's favour. When FINSABER removes layers 2–5, the advantage disappears. When Profit Mirage removes layer 1, about half to two-thirds of returns disappear.
- A defensible evaluation protocol synthesised from these critics:
  - Use only post-cutoff data for the specific model snapshot. Pin the model version, because silent model updates move the cutoff.
  - Prefer **forward live paper trading**.
  - Test over ≥ several years and multiple regimes, on a point-in-time universe that includes delisted names.
  - Benchmark against an investable passive index *and* simple quant baselines, net of realistic costs.
  - Run multiple seeds and prompts, report dispersion and significance (paired tests, alpha t-stats), and apply multiple-testing correction across model and prompt variants.
  - Probe memorisation directly (dated Q&A, counterfactual input perturbation). Do not rely on name or date masking alone.
- For a Taiwan-market platform:
  - LLM memorisation of TWSE prices and news has not been studied.
  - Lopez-Lira et al.'s size-quintile cross-section showed much looser recall (MAPE 11–25% pre-cutoff) than for index levels or Magnificent 7 prices. It is plausible, but untested, that less-covered markets leak less.
  - Recall of TWSE names is not zero by default, so the same protocol applies.
  - This matches the project's existing rule of selecting from 2015-06 onward and separately checking from 2020-10. For any LLM component, the "separate check" window must also be after the model's training cutoff. Otherwise it is not out of sample.

### Gaps
- FINSABER's rolling-window length and step sit in a truncated appendix and were not read.
- FINSABER does not test FinCon or TradingAgents directly. FinCon appears only as a stock *selector*.
- Look-Ahead-Bench's numeric decay figures and markets were not on the abstract page.
- FinCAD numbers come from a search summary of its abstract, not the full text.
- Another 2026 statistical test for parametric look-ahead bias, by Gao et al. and cited by FinCAD, was not found.
- No independent re-evaluation of TradingAgents over a long horizon or broad universe was found beyond Profit Mirage's one-quarter windows.
- No study quantifies LLM memorisation of Taiwanese or other Asian stock data.

---

## Q4. Cost and latency of running such agents daily

### Takeaway
Cost figures are rarely reported in the papers.
- TradingAgents needs about **11 LLM calls plus 20+ tool calls per ticker-decision**. A hosting vendor estimates **about US$0.20–0.80 per ticker per run** with gpt-4o and gpt-4o-mini.
- That cost is the stated reason the TradingAgents backtest was limited to about 3 months.
- None of StockBench, LiveTradeBench, AI-Trader or FinCon reported token or dollar cost in the sections read.

### Cited Findings
- TradingAgents: "each prediction involves about 11 LLM calls and 20+ tool calls". The system needs no GPU and runs on API credits. The backtest covers only about 3 months "because of intensive LLM and tool use". — [TradingAgents paper](https://arxiv.org/html/2412.20138)
- A vendor estimate (Railway deploy template, methodology not disclosed) puts one TradingAgents run at **about $0.20–$0.80 per ticker** with gpt-4o for deep reasoning, gpt-4o-mini for quick steps and 1 debate round. It says **doubling debate rounds roughly doubles cost**. — [Railway TradingAgents template](https://railway.com/deploy/trading-agents)
- StockBench reports no cost, token or latency figures, but notes error modes: thinking models made fewer arithmetic errors but more output-schema errors. — [StockBench v2](https://arxiv.org/html/2510.02209v2)
- LiveTradeBench does not report cost or tokens. It notes context-length limits that force news to be truncated to titles and abstracts. — [LiveTradeBench paper](https://arxiv.org/html/2511.03628v1)
- The FinCon pages read did not report API cost. FinCon's design aims to reduce "unnecessary peer-to-peer communication costs" by routing beliefs selectively. — [FinCon paper](https://arxiv.org/html/2407.06567v3)
- AI-Trader reports no token or cost per decision in the content read. — [AI-Trader paper](https://arxiv.org/html/2512.10971v1)
- Extended "thinking" (more tokens) gave essentially no trading benefit in TraderBench: +0.3 on crypto and −0.1 on options. — [TraderBench](https://iclr.cc/virtual/2026/10016348)

### Inferences
- My arithmetic on the vendor range:
  - 20 tickers per day costs about $4–16 per day, or about $120–480 per month.
  - 50 tickers per day costs about $10–40 per day.
  - A 1-year daily backtest on 50 tickers (about 12,500 decisions) costs about $2.5k–10k.
- Those costs explain why author backtests stay short and narrow. They also make broad, long, multi-seed validation (Q3's protocol) expensive, by a factor of seeds × prompts × models.
- Latency: about 11 sequential LLM calls including o1-class reasoning means minutes per ticker, not milliseconds. This suits daily or weekly decisions and rules out intraday reaction. This is an inference: no source measured wall-clock latency.

### Gaps
- No primary source reports measured tokens per decision or dollars per day for TradingAgents, FinCon or FinMem, or for Alpha Arena's inference costs.
- No measured end-to-end latency per decision was found.
- Current (Oct 2026) API prices were not verified against provider pricing pages.
