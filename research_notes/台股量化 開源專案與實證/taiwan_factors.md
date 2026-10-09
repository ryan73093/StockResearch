# Taiwan-specific stock return factors and anomalies: empirical evidence, implementability, and decay

Scope note for the report writer: research done 2026-10-09. Many publisher pages (ScienceDirect, SSRN, MDPI, ResearchGate) returned HTTP 403, so a lot of the academic evidence below is from abstracts, RePEc/IDEAS listings, or search-result excerpts rather than full texts. Each bullet says which. "Long-short" = academic hedge portfolio (not implementable for this user, who is long-only, 100% stocks). "Long-only" = a buy-side portfolio vs an index. Cost context used below: 0.1425% broker fee each way plus 0.3% sell tax means about 0.585% per round trip at list fees.

## 1. Monthly revenue (月營收) momentum, revenue surprises, record-high revenue (月營收創新高)

### Takeaway
This is the most credible Taiwan-specific signal. Several independent academic studies, from Ku (2010) through 2025–2026 papers, find that stock prices underreact to the mandatory monthly revenue reports (due by the 10th). The drift runs about 1 month after an announcement and longer for persistent growth, and one 2026 paper says the 1-month drift survives transaction costs. The main warning: on the day a record-high revenue figure comes out, stocks that already ran up tend to overshoot and pull back, so buying at the next morning's open is the wrong entry.

### Cited Findings
- Institutional setting: under Article 36 of the Securities and Exchange Act, listed firms have disclosed the previous month's unaudited net operating revenue within the first ten days of each month since 1988 — [Fu & Chen, EFMA 2013 submission](https://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2013-Reading/papers/EFMA2013_Submission_Fu_Chen_15May2013.pdf)
- Ku (2010), as summarized by Fu & Chen: stocks with positive revenue surprises earn higher later returns than stocks with negative surprises, i.e. post-revenue-announcement drift exists in Taiwan. Fu & Chen add that two consecutive same-sign surprises predict stronger later returns, and that the effect depends on investor sentiment and is stronger where short-selling is constrained. I could not reach Ku (2010) itself, so sample and magnitudes are unverified — [Fu & Chen, EFMA 2013](https://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2013-Reading/papers/EFMA2013_Submission_Fu_Chen_15May2013.pdf)
- TEJ cites Ku (2010) as finding significantly positive returns to a monthly revenue momentum strategy at 1–12 month horizons — [TEJ, 量化投資因子-月營收資訊實證](https://www.tejwin.com/news/%E9%87%8F%E5%8C%96%E6%8A%95%E8%B3%87%E5%9B%A0%E5%AD%90-%E6%9C%88%E7%87%9F%E6%94%B6%E8%B3%87%E8%A8%8A%E5%AF%A6%E8%AD%89/)
- Lai, Tsai, Lin & Lin, "Trading on Record-Breaking Monthly Revenue Announcements" (SSRN 5345252; now in Finance Research Letters, 2026). Abstract-level results:
  - **Short-term overreaction:** using intraday data, shorting at the next day's open after a record-high monthly revenue announcement earns abnormal returns. The effect is strongest for stocks with big pre-announcement price gains, high prices and rapid revenue growth.
  - **1-month drift:** buying at the close on announcement day and holding about one month (20 trading days) earns significantly positive returns that stay positive after transaction costs. The authors call this PEAD-like underreaction.
  - **Institutional trading:** net trading by institutions reinforces both the overreaction and the underreaction.
  - Sources: [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5345252); [ScienceDirect (FRL)](https://www.sciencedirect.com/science/article/abs/pii/S154461232600440X)
- Hung et al., "Market reaction to monthly revenue momentum" (National Central University; abstract only). Using year-over-year (YoY) growth, they find positive revenue momentum. Prices absorb monthly revenue information independently of quarterly revenue. Persistent revenue growth raises momentum profits, mostly in costly-to-arbitrage shares, and analysts fold revenue surprises into earnings forecasts — [NCU scholars page](https://scholars.ncu.edu.tw/en/publications/market-reaction-to-monthly-revenue-momentum/); [ResearchGate listing](https://www.researchgate.net/publication/392897683_Market_reaction_to_monthly_revenue_momentum_Market_reaction_to_monthly_revenue_momentumW_Hung_et_al)
- Monthly revenue reports give an early read on earnings growth that analyst forecasts absorb only partly (2021, Review of Quantitative Finance and Accounting) — [Springer](https://link.springer.com/article/10.1007/s11156-021-00994-1)
- Hong-Yi Chen (Taipei conference paper) attributes revenue-surprise drift to investor underreaction and studies it together with price and earnings momentum — [Chen, "Price, Earnings, and Revenue Momentum Strategies"](http://pbfea2005.rutgers.edu/TaipeiPBFR&D/990515Papers/6-3.pdf)
- Pre-announcement signals:
  - High-MAX ("lottery") stocks earn higher abnormal returns in the 5 days before revenue announcements (Pacific-Basin Finance Journal (PBFJ), 2023) — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0927538X23001440)
  - Before the announcement, the abnormal implied-volatility skew of warrants is negatively related to cumulative abnormal returns around the revenue release (Journal of Futures Markets, 2025) — [Wiley](https://onlinelibrary.wiley.com/doi/10.1002/fut.70009)
- TEJ practitioner study (full article read):
  - **Data:** January 2013 to June 2021, members of the Taiwan Mid-Cap 100 index at the time; sample starts 2013 because consolidated monthly revenue began then.
  - **Signal and portfolio:** cumulative 3-month YoY revenue growth; top 30% equal-weighted; bought the day after all revenue is out and rebalanced monthly.
  - **Results:** cumulative return about 140% vs TAIEX about 90% (2015–2021). Average monthly turnover about 0.45x. Costs excluded, with TEJ arguing turnover is low enough not to change conclusions.
  - **Weak spots:** worst stretches were roughly −10% in 2018 (trade war) and 2020 (COVID). It underperformed the index when the Fed funds rate was above 2%.
  - Source: [TEJ](https://www.tejwin.com/news/%E9%87%8F%E5%8C%96%E6%8A%95%E8%B3%87%E5%9B%A0%E5%AD%90-%E6%9C%88%E7%87%9F%E6%94%B6%E8%B3%87%E8%A8%8A%E5%AF%A6%E8%AD%89/)
- NCU 2025 master's thesis by 李昱德 (weaker evidence; abstract page read):
  - **Design:** month-over-month (MoM) revenue growth ranking, January 2013 to October 2024, monthly rebalancing, entry at the first session after the month's last revenue announcement; Top 5, Top 10 and Top 15 equal-weighted.
  - **Results:** Top 15 was the most robust on a risk-adjusted basis and resisted bear markets; Top 5 had the highest cumulative return but bigger drawdowns. The dual MoM+YoY Top 15 had Sharpe 1.505, Calmar 1.58 and a 57.04% monthly win rate. Cost treatment not stated.
  - Source: [NCU ETD](https://etd.lib.ncu.edu.tw/detail/2a61234058e39839846030de3c0973e6)
- Tamkang research page (abstract-level, via search summary): revenue momentum persists after controlling for period, season, exchange, industry, size, turnover, B/M, past returns, unexpected earnings and risk, but cumulative returns turn significantly negative 25–36 months after formation — [TKU 營收動能策略](https://teacher.tku.edu.tw/StfFdDtl.aspx?tid=294184)
- Practitioner backtests (weak; in-sample, methods partly undisclosed):
  - CMoney: buying stocks at record-high monthly revenue after each release and rebalancing monthly gave about 16.1% a year; period not stated — [CMoney](https://www.cmoney.tw/notes/note-detail.aspx?nid=116559)
  - cnyes 易策略: latest month's revenue at a 4-year high plus institutional buying gave 23.57% a year over 10 years vs 12.59% for the index — [cnyes](https://hao.cnyes.com/post/6318)
  - A vocus blog: 3-month average revenue YoY above the 12-month average, top 20 by YoY, rebalanced after each revenue release, 2007-01-01 to 2024-10-18, about 20.6% a year (from a search summary; full text not verified) — [vocus](https://vocus.cc/article/6713a8bdfd8978000102b019)
  - FinLab: investment-trust buying combined with revenue momentum gave 33.9% CAGR and Sharpe 1.11 over 2015-01 to 2026-06, vs 0050 at 20.6% and Sharpe 1.10. The rules are paywalled — [FinLab](https://finlab.finance/en/blog/institutional-strategy)

### Inferences
- The strongest academic claims are long-side underreaction (drift over about 20 trading days to a few months) plus a short-term overreaction on record-high days for stocks that already rallied. That points to a long-only design that:
  - enters at the close or later rather than the next open;
  - prefers persistent or accelerating growth (consecutive positive surprises, 3-month YoY above 12-month YoY);
  - avoids names with large run-ups before the announcement.
- A monthly rebalance timed to the revenue calendar (around the 10th–11th) fits a weekly-or-slower schedule. Turnover of about 0.45x per month implies roughly 0.45 × 12 × 0.585% ≈ 3.2% a year of cost drag at list fees, which is manageable against reported spreads.
- The 25–36 month reversal (TKU) means holdings should not be kept long after the growth signal stops.

### Gaps
- No full-text magnitudes for Ku (2010) or Hung et al. (e.g., monthly long-short spread, top-quintile excess). I could not find a peer-reviewed estimate of top-quintile long-only excess per year.
- Post-2020 decay: no source splits revenue-momentum performance before and after 2020. The TEJ sample ends 2021-06. The NCU thesis runs to 2024-10 but gives no subperiod split on its abstract page. A search summary mentioned a thesis finding no significant decline after Ku (2010) was published (only shorter persistence for 3- and 12-month cumulative versions), but I could not verify which repository record it is.
- A Chen et al. paper in Applied Economics (2026) links revenue-announcement drift to policy-related macro uncertainty; only the title was visible.

## 2. Earnings announcement drift (PEAD) and quarterly earnings surprises in Taiwan

### Takeaway
I found no Taiwan paper reporting classic quarterly SUE-based PEAD magnitudes. Taiwan's PEAD-like evidence comes mainly from monthly revenue, which pre-empts much of the quarterly news, and from analyst forecast revisions, which show sizable long-side drift.

### Cited Findings
- Upward revisions in consensus forecast earnings growth (FEG) in Taiwan earn about 2.11% a month over the Taiwan market index; the long-short version earns about 2.81% a month. Gains carry into the following month, consistent with underreaction (Finance Research Letters, 2025; abstract-level) — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1544612325024134)
- Tsai (2014): retail traders as a group show no advance knowledge before annual earnings announcements, but those placing aggressively priced orders are informed — their net trading lines up with contemporaneous and future returns — [Journal of Business Finance & Accounting](https://onlinelibrary.wiley.com/doi/10.1111/jbfa.12093)
- A Taiwan quarterly accrual-anomaly study exists (Managerial and Decision Economics), but I saw only the title — [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1002/mde.3265)
- US context on decay: Martineau argues PEAD in large US stocks has been essentially absent since 2006 — [SocArXiv/IDEAS](https://ideas.repec.org/p/osf/socarx/z7k3p.html)

### Inferences
- Because Taiwan firms publish monthly revenue, by the time quarterly EPS comes out (deadlines roughly May, August, November and March) much of the surprise is already known. A separate quarterly PEAD signal is likely weaker and more redundant than in the US. This is an inference, not tested in a source found.
- Analyst-revision signals look strong on the long side (+2.11% a month), but consensus data for Taiwan small and mid caps is thin. Coverage and data-vendor access limit use in a 20-stock universe.

### Gaps
- No Taiwan SUE or PEAD magnitude, horizon, or post-2015 sample found.
- No evidence on gross-margin or operating-profit surprises in Taiwan.

## 3. Institutional flows: foreign investors (外資), investment trusts (投信), dealers (自營商)

### Takeaway
Daily and weekly three-institution net-buy data is mostly contemporaneous or trend-following. Evidence that it predicts the cross-section on its own is weak or regime-dependent. The best recent practitioner test (2015–2026) found that following 投信 buying alone, or fading 外資 selling, earned about 9–10% a year vs 20.6% for 0050. Flows seem useful only as a secondary filter, for example combined with revenue momentum. Heavy institutional herding predicts lower future returns.

### Cited Findings
- FinLab backtest (full article read), 2015-01 to 2026-06, monthly rebalancing, fees and tax included, 20-day average volume above 300 lots:
  - **Pure 投信 following** (top 10 by 10-day cumulative 投信 net buying): CAGR +9.7%, daily Sharpe 0.44, max drawdown −45.5%.
  - **Buying stocks after 5 straight days of foreign net selling:** CAGR +8.8%, Sharpe 0.48, max drawdown −34.5%.
  - **0050 with dividends:** +20.6%, Sharpe 1.10, max drawdown −34.0%.
  - **投信 + revenue momentum:** +33.9%, Sharpe 1.11.
  - The author concludes that following any single institutional flow signal, with it or against it, underperforms holding 0050.
  - Source: [FinLab](https://finlab.finance/en/blog/institutional-strategy)
- 2025 study of foreign, 投信 and dealer herding: the top 20% of lagged institutional herding scores significantly and negatively predict future excess returns in market collapses and over the full sample. In post-pandemic booms, strong herding goes with higher volatility — [Cogent Economics & Finance 2025](https://www.tandfonline.com/doi/full/10.1080/23322039.2025.2571399)
- 投信 are the main contributors to price discovery. Institutional selling carries more forecasting information than buying, and 投信 show significant negative coefficients across horizons (JRFM 2026; abstract-level) — [JRFM](https://doi.org/10.3390/jrfm19050323)
- Older evidence (1996-07 to 1999-05, VAR): none of the three institutional groups' trading influences stock returns — [International Review of Financial Analysis 2002 via IDEAS](https://ideas.repec.org/a/eee/finana/v11y2002i4p533-547.html)
- Foreign institutions:
  - The volatility of foreign institutions' order imbalance (VOIB) is positively associated with individual stock returns the following month — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S138641812030015X)
  - A literature review notes Huang & Shiu (2009): Taiwan stocks with high foreign ownership outperform those with low foreign ownership. The same review says evidence that foreign institutions pick stocks well across markets, Taiwan included, is inconsistent — [NYU Stern working paper](https://www.stern.nyu.edu/sites/default/files/assets/documents/Foreign%20Institutional%20Investor%20Trading%20and%20Future%20Returns.pdf)
- Market-level evidence: in index futures, 投信 are positive-feedback traders and foreign institutions are negative-feedback traders; foreign net futures volume positively affects futures returns — [ResearchGate](https://www.researchgate.net/publication/286649821_Trading_Behavior_of_Institutional_Investors_and_Stock_Index_Futures_Returns_in_Taiwan). Net foreign purchases lead market returns in some regimes, e.g. after positive NASDAQ days — [Springer chapter](https://link.springer.com/rwe/10.1007/978-1-4614-7750-1_17)
- 投信 small and mid-cap behavior (practitioner, not statistical):
  - 投信 favor small and mid caps that are easier to push for short-term fund performance.
  - Quarter-end 作帳 (window dressing) in March, June, September and December is followed by 結帳 selling.
  - When 投信 stop buying, small caps fall quickly.
  - 投信 flow information is more useful in small and mid caps and in bull markets.
  - Sources: [CMoney 季底作帳](https://www.cmoney.tw/notes/note-detail.aspx?nid=95625); [E大 blog](https://efrontrade.com/2020/09/investment-trust-window-dressing-small-cap-stock-guide.html)

### Inferences
- For a weekly-or-slower long-only book, 投信 buying is at best a confirmation filter on top of a fundamental signal such as revenue. Standalone, it underperformed 0050 by about 11 points a year in 2015–2026.
- Sell-side flows and herding look more informative than buy-side flows. Using heavy institutional herding or crowding as an exclusion filter may beat using buying as an entry signal.
- Growth of passive ETFs relative to active funds (noted in practitioner articles) probably dilutes the 投信 signal over time. Not tested in any source found.

### Gaps
- No peer-reviewed post-2015 cross-sectional test of 投信 net buying in small and mid caps with long-only excess and costs.
- No dealer (自營商) cross-sectional evidence found. Dealer flow is contaminated by warrant hedging; that is background knowledge, not sourced here.

## 4. Ownership concentration: TDCC shareholding distribution (集保戶股權分散表, 大戶/千張大戶), director/supervisor holdings and pledges (董監持股/質押), insiders

### Takeaway
I found no peer-reviewed, out-of-sample evidence that weekly TDCC large-holder changes predict returns. Practitioner material is anecdotal, with undisclosed win rates. Director/supervisor share pledging (董監質押) evidence in Taiwan is about risk (crash risk, return variance, related-party transactions), and its sign is mixed. It is a risk filter, not a return predictor.

### Cited Findings
- TDCC data mechanics:
  - Distribution tables are published weekly. The official site keeps only about one year of history, so long backtests need self-archived or third-party data.
  - Data is released after the last business day of the week, so mid-week use is look-ahead.
  - Sources: [XQ 集保欄位](https://www.xq.com.tw/learn/xspractice/tdccfield/); [TDCC 集保戶股權分散表](https://www.tdcc.com.tw/portal/zh/smWeb/qryStock); [rich01](https://rich01.com/what-is-large-trader-1000/)
- The rich01 practitioner article proposes retail and large-holder thresholds but explicitly says the author has not tested them — [rich01](https://rich01.com/what-is-large-trader-1000/)
- XQ's 千張大戶 case: a 7-year backtest of rising 1,000-lot holder counts combined with price near book value and institutional buying, with ±7% stops and 846 trades. No return figures were shown in what I could access — [XQ blog](https://www.xq.com.tw/xstrader/%E5%A4%A7%E6%88%B6%E6%8C%81%E7%BA%8C%E8%B2%B7%E9%80%B2%E4%B8%AD/)
- 董監質押 in Taiwan theses (abstract-level only; I could not tie each finding to a specific record among the linked listings):
  - Sample 2005–2015: pledging is significantly *negatively* related to crash risk (negative skewness and down-volatility of weekly returns), more so in family firms.
  - Sample 1996–2007: pledging is *positively* related to the variability of stock returns.
  - Higher pledging goes with more related-party transactions.
  - Sources: [NTU thesis listing](https://tdr.lib.ntu.edu.tw/handle/123456789/20769); [Airiti listing](https://www.airitilibrary.com/Publication/Index/U0006-0807201413220400)

### Inferences
- With no credible return evidence, TDCC concentration changes should be treated as an untested hypothesis. Any test needs a weekly release-lag model and self-archived data.
- 董監質押 is best used, if at all, as a governance and risk exclusion screen. The return sign is not established.

### Gaps
- A practitioner summary attributes to 周依晴 (2020) the finding that more holders of ≤10 lots predicts lower 1-month, 1-quarter and 1-year returns, while more holders of ≥100 lots predicts higher quarterly and yearly returns. I could not locate the citing page or the thesis to verify.
- No Taiwan evidence found on insider-trade filings (申報轉讓, 內部人持股異動) as return predictors.
- No evidence on 董監持股 level as a return predictor.

## 5. Margin trading and short selling (融資融券, 券資比, 借券)

### Takeaway
Moderately credible academic evidence says short sellers in Taiwan are informed: shorting flows predict lower returns over 1 to 20+ days. Heavy margin buying predicts lower returns, and stocks with a low short-to-margin ratio (券資比) earn higher risk-adjusted returns (1999–2018). Effects are weaker in bull markets and in small caps, where short squeezes happen. For a long-only book these work mainly as avoid-lists.

### Cited Findings
- Lin, Ho & Ko (PBFJ, 2023): short-term and long-term shorting flows strongly predict future stock returns in Taiwan even under the up-tick rule and price limits. The search summary describes prediction over the next day and next 20 trading days — [IDEAS](https://ideas.repec.org/a/eee/pacfin/v77y2023ics0927538x22001111.html)
- Short sellers identify stocks that subsequently decline when they open positions; their skill at covering is less clear — [NJIT, Daily short covering activity and the weekend effect](https://digitalcommons.njit.edu/fac_pubs/10693)
- 券資比效應 (TWSE and TPEx common stocks, 1999-01 to 2018-12): after risk adjustment, low-券資比 stocks earn higher returns. The result is robust to firm characteristics and subperiods but not significant in bull markets — [Airiti](https://airitilibrary.com/Publication/alDetailedMesh?docid=U0002-2806202013545200)
- 丁碧慧、呂振揚、周賓凰 (2018), 經濟論文 46(3), as summarized by a blog:
  - Heavy margin long positions signal overvaluation and lower later returns; heavy short positions signal undervaluation and relatively better later returns.
  - Higher securities-lending short balances (借券賣出餘額) predict lower returns for about six months.
  - Source: [vocus summary](https://vocus.cc/article/640ae49cfd897800012fe058)
- TEJ: a short-interest-ratio (SIR) sentiment factor predicts negatively in mid and large caps but reverses in small caps because of squeeze risk, so whole-market ranking power is low (per search summary of a TEJ quant monthly issue) — [TEJ 量化投資月報 index](https://www.tejwin.com/news-category/tqnews/)
- High-IVOL stocks have higher margin-short ratios (see Section 7) — [TEJ IVOL](https://www.tejwin.com/en/insight/factor-research-idiosyncratic-volatility/)

### Inferences
- For long-only use, apply as exclusions: avoid heavily margined, high-借券-short names, especially in mid and large caps. Do not chase high 券資比 squeeze plays in small caps, which reverse unpredictably.
- 借券 (SBL) short balances appear more informative than retail 融券. This matches the US finding that long-term shorting flows matter more.

### Gaps
- No post-2020 out-of-sample estimate. The 券資比 study ends 2018.
- No net-of-cost long-only magnitude.

## 6. Price momentum vs reversal, 52-week high, trend-following

### Takeaway
Taiwan is a well-known exception where classic cross-sectional 12-1 momentum is weak or absent. Winner and loser portfolios turn over heavily and short-term reversal dominates, and 52-week-high profits are confined to before 2000. Momentum does show up in refined forms:
- persistent winners;
- intraday-return momentum (IMOM), while overnight-return momentum reverses;
- time-series momentum;
- machine-learning models where momentum variables rank among the top predictors.

In these studies most of the profit comes from the long leg.

### Cited Findings
- Chen, Hsieh & Lee (PBFJ vol 78, 2023): Taiwan is a notable exception to momentum because winner and loser portfolios turn over heavily. A strategy that buys persistent winners and shorts persistent losers earns significant intermediate-term profits. Persistency is positively associated with heterogeneous beliefs — [IDEAS](https://ideas.repec.org/a/eee/pacfin/v78y2023ics0927538x23000094.html)
- Earlier work by the same group, Chen, Chou & Hsieh, "Persistency of the momentum effect" (European Financial Management, 2018). This is probably a non-Taiwan or US sample; I did not confirm the market.
  - More than 40% of winners and losers leave their group the next month, and momentum built on those names loses more than 17% a month.
  - Persistent winners and losers show much stronger momentum.
  - Source: [vLex abstract](https://eu.vlex.com/vid/persistency-of-the-momentum-855636240)
- Bui, Kong, Lin & Lin (PBFJ vol 82, 2023):
  - **Scope:** revisit 86 anomalies in Taiwan.
  - **Results:** neural-network and partial-least-squares long-short portfolios earn 1.20–1.50% a month; 5 of the top 20 predictors are momentum-related.
  - **Interpretation:** momentum contributes to predictability despite Taiwan being "one of the few exceptions to the momentum anomaly".
  - Sources: [IDEAS](https://ideas.repec.org/a/eee/pacfin/v82y2023ics0927538x23002494.html); [NYCU](https://scholar.nycu.edu.tw/en/publications/momentum-in-machine-learning-evidence-from-the-taiwan-stock-marke/)
- Ho, Hsiao, Lo & Yang (PBFJ vol 82, 2023): intraday momentum (IMOM) earns significantly positive returns and overnight momentum (OMOM) significantly negative, both lasting up to one year. IMOM is stable over time and less prone to crashes — [IDEAS](https://ideas.repec.org/a/eee/pacfin/v82y2023ics0927538x23002226.html)
- 52-week high (International Review of Economics & Finance, 2015; snippet-level): 52-week-high and recency strategy profits are limited to before 2000, and crashes cluster after 2000 — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1059056015001859)
- Time-series vs cross-sectional momentum in Taiwan (Journal of Risk and Financial Management (JRFM), 2026; snippet-level): time-series variants average 10.15% annualized excess return vs 6.90% for cross-sectional variants. Long legs supply most of the profit; short legs matter more in crashes — [MDPI JRFM](https://www.mdpi.com/1911-8074/19/7/462)
- Other related PBFJ papers, titles only seen:
  - "Market dynamics and momentum in the Taiwan stock market" (2016) — [IDEAS](https://ideas.repec.org/a/eee/pacfin/v38y2016icp59-75.html)
  - "Retrieving Almost Stochastic Dominance Momentum in Taiwan Stock Market" (PBFJ 83, 2024), via the [NYCU listing](https://scholar.nycu.edu.tw/en/publications/momentum-in-machine-learning-evidence-from-the-taiwan-stock-marke/)
- Implementable short-horizon momentum product: the 00733 index (中小型A級動能50).
  - **Rules:** excludes the top 50 by market cap; liquidity screen; profitable last quarter and positive 20-day beta; picks the top 50 by **20-day alpha**; free-float weighted; rebalanced in January, April, July and October. Turnover is very high — 46 of 50 names replaced in October 2023, 45 in April 2025.
  - Sources: [Taiwan Index SAM50](https://taiwanindex.com.tw/en/indexes/SAM50); [Fubon index intro](https://websys.fsit.com.tw/FubonETF/Fund/IndexIntro.aspx?stkId=00733); [CTEE Oct 2023](https://www.ctee.com.tw/news/20231019701919-430201)
- 00733 performance, NAV-based, as of 2026-09-30: 1-year 58.07%, 3-year annualized 14.55%, 5-year annualized 17.59%. MoneyDJ (2026-05-15): Sharpe 0.78, annualized standard deviation 20.73%, beta 0.95. More than 80% electronics — [Yahoo performance](https://tw.stock.yahoo.com/quote/00733.TW/performance); [MoneyDJ](https://www.moneydj.com/etf/x/basic/basic0009.xdjhtm?etfid=00733.tw)

### Inferences
- Do not use raw 12-1 momentum alone in Taiwan. Better-supported variants include:
  - persistence filters, e.g. stocks in the winner group for two consecutive months;
  - intraday-only return momentum;
  - time-series trend filters;
  - momentum combined with fundamentals.
- Because profits sit mainly in the long leg, these suit long-only use.
- 00733's 5-year 17.59% and 3-year 14.55% are roughly market-like for a 20%-vol product. The big 1-year number reflects the 2026 small and mid-cap rally, not durable alpha. This is an inference; no benchmark-relative alpha was found.

### Gaps
- No sourced long-only top-quintile excess for any momentum variant after costs, and no post-2020 subperiod.
- Sample periods for the 2023 PBFJ papers are not visible on the abstract pages.

## 7. Size, value (B/M, E/P), low volatility, quality/profitability

### Takeaway
- **Size:** Taiwan's size premium is unstable and has recently been *negative* — large beat small by about 10.6% a year in one recent sample, tied to semiconductor and electronics leaders.
- **Value:** evidence is weak or conditional; a TEJ value+size+momentum screen barely beat TAIEX over 2014–2024.
- **Low idiosyncratic volatility:** robust over 2005–2025; low-IVOL stocks beat high-IVOL, driven by high-IVOL underperformance.
- **Quality/profitability:** no direct Taiwan cross-sectional study found.

### Cited Findings
- Size, recent (PBFJ 2025; snippet-level, sample period unverified): average monthly SMB was −0.88%, i.e. large caps beat small caps by about 10.6% a year, with strong growth among large semiconductor and electronics firms a candidate explanation. The size spread is partly forecastable — positive after upward signals, negative after downward ones — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0927538X25003580)
- Size, older studies:
  - Chui & Wei (1998) found no size or B/M effect in Taiwan (as cited in the same search snippet).
  - 1971–1993: the smallest size quintile earned significantly higher abnormal returns, not attributable to January — [Applied Economics Letters 1997 via IDEAS](https://ideas.repec.org/a/taf/apeclt/v4y1997i1p7-12.html)
  - 1989–2008: no size effect, but a significant *negative* size effect — [Nottingham dissertation (Lin 2009)](https://eprints.nottingham.ac.uk/22871/)
- Value:
  - The value premium in Taiwan is linked to how often stocks hit price limits (PBFJ vol 41, 2017; findings not visible) — [IDEAS](https://ideas.repec.org/a/eee/pacfin/v41y2017icp26-45.html)
  - A blog study of Taiwan 1990-07 to 2020-12 says the five-factor model beats three factors and that smaller, higher-B/M groups have higher mean monthly returns — [vocus 布萊茲](https://vocus.cc/article/66a39f1bfd89780001ce921c)
  - A practitioner column says the value factor did poorly in Taiwan over the past decade — [vocus 價值因子](https://vocus.cc/article/63c151d9fd89780001261d0a)
- TEJ value ∩ size ∩ momentum screen, 2014–2024: about 14.27% a year vs TAIEX 13.89% (cumulative about 314.81% vs 300.08%). TEJ's own comment is that it barely beat the index with clearly higher risk — [TEJ 三因子策略](https://www.tejwin.com/insight/%E5%B0%A4%E9%87%91%E6%B3%95%E7%91%AA-%E4%B8%89%E5%9B%A0%E5%AD%90%E6%A8%A1%E5%9E%8B%E7%AD%96%E7%95%A5/)
- Factor timing (應用經濟論叢 117, June 2025; Tung, Lai, Yeh & Lin): economic states 2004–2023 are classified with the Merrill Lynch investment clock. Factors are graded on 2004–2015 data. Over 2016–2023, holding the best factor for the current state beats the market and, in specific states, the best single-factor portfolio — [PDF](https://tjaecon.nchu.edu.tw/upload/2025/09/20250917233547892.pdf). A search summary also attributed to this paper "68 candidate factors, 10 survive FF3 alpha tests, 2003–2023", but I could not open the PDF text to confirm, and it conflicts with the 2004 start in the abstract.
- Low idiosyncratic volatility (TEJ factor research, page read):
  - **Data:** all TWSE and TPEx common stocks, January 2005 to March 2025; IVOL = residual standard deviation from FF3 over 21 days; daily decile sorts.
  - **Signal strength:** mean rank IC is −0.036 at 1 day, −0.064 at 5 days, −0.075 at 10 days and −0.087 at 21 days (t = −50.1; IC negative in 76.75% of periods).
  - **Returns:** low-IVOL deciles beat high-IVOL deciles at every horizon.
  - **Turnover:** 21-day portfolios turn over about 6.7–7x a year.
  - **Who is high-IVOL:** smaller, higher P/E and P/B, lower yield, lower foreign ownership, more illiquid, higher margin-short ratios.
  - **Use:** TEJ suggests IVOL works better as a screen or risk filter than as a standalone long-only alpha. No post-2020 split; costs not discussed.
  - Source: [TEJ IVOL](https://www.tejwin.com/en/insight/factor-research-idiosyncratic-volatility/)
- Low-volatility anomaly and funding liquidity in Taiwan: mispricing is largest when funding liquidity risk is high and reverses significantly when it is low, through temporary price pressure from institutional selling — [ScienceDirect (NAJEF)](https://www.sciencedirect.com/science/article/abs/pii/S1062940818302419)

### Inferences
- The post-2020 regime of mega-cap semiconductor leadership is consistent with the negative SMB estimate. Any small or mid-cap tilt in a 20-stock portfolio carries a structural headwind against a cap-weighted 0050 benchmark, and is a likely reason "good factor" small-cap portfolios lagged 0050 in 2015–2026 (cf. FinLab).
- Low-IVOL screening (exclude the top 20–30% IVOL) is one of the best-supported long-only *filters*. Its value is mainly in avoiding lottery-like losers rather than in picking big winners.
- Value alone is not a credible standalone alpha source in Taiwan's recent history.

### Gaps
- No Taiwan profitability/quality (ROE, gross profitability, QMJ) cross-sectional study with magnitudes found.
- No Taiwan E/P vs B/M comparison found.
- MSCI/FTSE Taiwan factor index performance (momentum, quality, minimum volatility) was not retrieved.

## 8. Dividends, ex-dividend effects (除權息, 填息), and seasonal effects (作帳, January/Lunar New Year, July–August dividend season)

### Takeaway
Evidence is thin. Taiwanese funds do window-dress at quarter-ends: equity holdings jump at quarter-end and revert the next month. But I found no Taiwan study quantifying the return effect for small caps. For 填息 (dividend fill), TEJ has an event study testing whether a stock's past fill probability predicts future fills, but the results are paywalled. I found no Taiwan-specific Lunar New Year or January return evidence in this search.

### Cited Findings
- Wang & Yu (Review of Quantitative Finance and Accounting, 2018): Taiwanese funds' equity holdings jump significantly at quarter-ends and year-end and drop back the next month. This resembles Carhart et al.'s price markup, but the mechanism may differ — [IDEAS](https://ideas.repec.org/a/kap/rqfnac/v50y2018i2d10.1007_s11156-017-0633-1.html)
- US benchmark: quarter-end price inflation runs from about 0.5% a year for large-cap funds to over 2% for small-cap funds — [Carhart, Kaniel, Musto & Reed, JF 2002](https://www.johnhcochrane.com/s/carhart_leaning_for_tape_JF.pdf)
- Practitioner 作帳 statistics: e.g. 77.8–83.3% up-probability and +2.6% to +7.52% over 3-day holds. These cover very few signals, with no costs and no benchmark, so statistical significance cannot be judged — [CMoney](https://www.cmoney.tw/notes/note-detail.aspx?nid=95625)
- TEJ 填息機率 event study: tests whether stocks with higher historical dividend-fill probability fill more often in future. A fill means the post-ex-date price exceeds the pre-ex close. Full results are behind TEJ's paywall — [TEJ](https://www.tejwin.com/news/tquant-lab-%E4%BA%8B%E4%BB%B6%E5%9E%8B%E5%9B%A0%E5%AD%90%E7%A0%94%E7%A9%B6%EF%BC%9A%E8%82%A1%E7%A5%A8%E5%A1%AB%E6%81%AF%E6%A9%9F%E7%8E%87-tej-%E9%87%8F%E5%8C%96%E6%8A%95%E8%B3%87/)
- Taiwan dividend-announcement abnormal returns by ownership structure (thesis; title only) — [Airiti](https://www.airitilibrary.com/Article/Detail/U0002-0407202123041900)
- The 1971–1993 small-firm premium was not attributable to the January effect — [Applied Economics Letters 1997](https://ideas.repec.org/a/taf/apeclt/v4y1997i1p7-12.html)
- Taiwan revenue momentum is reported to have significant seasonal effects (abstract-level) — [TKU](https://teacher.tku.edu.tw/StfFdDtl.aspx?tid=294184)

### Inferences
- Quarter-end window dressing implies a possible "buy before, sell after" pattern in 投信-held small caps, with reversal in the first month of the next quarter. It is unquantified for Taiwan, so treat it as hypothesis only.
- Dividend-fill and ex-dividend-season trading is mostly a tax and timing question for an account that reinvests. No credible excess-return evidence was found.

### Gaps
- No Taiwan study quantifying return effects of 作帳, Lunar New Year, or the July–August ex-dividend season. A follow-up search should try 華藝 or NDLTD with "季底 粉飾 異常報酬", "農曆年 效應 台股", "除息 異常報酬".

## 9. Disposition and attention stocks (處置股/注意股), price limits, day-trading-related effects

### Takeaway
Being flagged as an attention or disposition stock is an *ending* signal. Abnormal returns build over about 8 days before the announcement, peak about 2 days before, and turn negative right after; disposition stocks are not a credible long signal. Price-limit frequency interacts with the value premium. Day-trading alerts produce negative abnormal returns on days 0–3 after the announcement.

### Cited Findings
- For overbought disposition stocks, abnormal returns are significantly positive on the announcement day, over the prior 8 trading days, and over the last few days of disposition. After the announcement they quickly drop to negative and insignificant. For oversold disposition stocks, significant positive abnormal returns appear only in the last 3 days of disposition — [Airiti, 超漲與超跌處置股](https://www.airitilibrary.com/Article/Detail?DocID=U0001-3105201915274300)
- NSYSU thesis, 2016–2020: abnormal returns build from about 8 business days before the announcement, peak about 2 days before, and fall sharply after. Lower-priced stocks are more volatile. Trading strategies worked best for high-priced TWSE and mid-priced TPEx stocks — [NSYSU ETD](https://ethesys.lis.nsysu.edu.tw/ETD-db/ETD-search-c/view_etd?URN=etd-0719121-131013)
- Day-trading alert system introduced by TWSE on 2021-08-27 (International Review of Economics & Finance, 2026): abnormal returns peak about 2 days before the alert. Days 0–3 after the alert show generally negative abnormal returns, stronger in high-retail-participation stocks — [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S1059056026000183)
- TEJ backtest of buying disposition stocks (about 46 months): about 19.71% a year, roughly market-like, with clearly higher risk and many negative months — [TEJ](https://www.tejwin.com/insight/tquant-lab-%E8%99%95%E7%BD%AE%E8%82%A1%E8%A9%B2%E8%B2%B7%E5%97%8E/)
- Price limits interact with the value premium in Taiwan — [PBFJ 2017 via IDEAS](https://ideas.repec.org/a/eee/pacfin/v41y2017icp26-45.html)
- Overnight returns and investor sentiment in Taiwan (PBFJ vol 80, 2023; title only) — [IDEAS](https://ideas.repec.org/a/eee/pacfin/v80y2023ics0927538x23001646.html). Overnight-return momentum reverses (Section 6).
- Taiwan day traders, 1992–2006: individual day traders lose money in aggregate; the sell tax was 0.3% — [Barber, Lee, Liu & Odean](https://faculty.haas.berkeley.edu/odean/papers/Day%20Traders/Day%20Trade%20040330.pdf)

### Inferences
- For a weekly-or-slower long-only portfolio, attention, disposition and day-trading alerts are best used as exits or entry blocks: do not initiate positions in names just flagged. Disposition rules also restrict trading (matching every 5–20 minutes, prepayment), which adds execution risk.
- High overnight-gap stocks (retail attention) tend to reverse, which supports avoiding gap-chasing entries.

### Gaps
- No limit-up (漲停) continuation or reversal evidence with post-2015 data found. The rules changed in 2015 when the daily price limit widened from 7% to 10% — background knowledge, not sourced here.

## 10. Decay after 2020 (TSMC and large-cap dominance) and which signals best fit a long-only, weekly-or-slower, 20-stock portfolio

### Takeaway
After 2020, index concentration rose sharply (TSMC 43.8% of listed market cap by May 2026; TSMC, Delta and MediaTek 52%), and small caps lagged large caps. As a result, most stock-picking factor portfolios have struggled to beat a cap-weighted 0050 benchmark. The best-evidenced, implementable, low-turnover core for Taiwan long-only is:
- monthly revenue momentum, with persistence/acceleration and the right entry timing;
- low-IVOL and low-crowding/low-margin exclusion filters;
- optionally, persistence-filtered or intraday momentum, and analyst revisions where data exists.

Institutional-flow following, TDCC concentration, value and size tilts have weak or negative recent evidence as standalone signals.

### Cited Findings
- Concentration and investor base: TSMC was 43.8% of listed market cap by May 2026, and TSMC + Delta + MediaTek 52%; each NT$1 move in TSMC shifts TAIEX about 8 points. Domestic individuals generated 52.11% of TWSE trading value last year; foreign institutions held 47.42% of listed market value — [Taipei Times, 2026-07-31](https://www.taipeitimes.com/News/feat/archives/2026/07/31/2003861680)
- Recent negative size premium (SMB −0.88% a month) — [PBFJ 2025](https://www.sciencedirect.com/science/article/abs/pii/S0927538X25003580)
- Breadth: with TAIEX near 50,000 in 2026, more than 1,000 individual stocks still had negative YTD returns (an informal netizen tally reported by UDN; weak evidence) — [UDN](https://udn.com/news/story/12806/9792929)
- Over 2015-01 to 2026-06, single institutional-flow strategies earned 8.8–9.7% CAGR vs 0050 at 20.6%; only a combination with revenue momentum (33.9%) beat it — [FinLab](https://finlab.finance/en/blog/institutional-strategy)
- A revenue-momentum mid-cap portfolio beat TAIEX from 2015 to mid-2021 (about 140% vs 90% cumulative) but lagged when US rates were above 2% — [TEJ](https://www.tejwin.com/news/%E9%87%8F%E5%8C%96%E6%8A%95%E8%B3%87%E5%9B%A0%E5%AD%90-%E6%9C%88%E7%87%9F%E6%94%B6%E8%B3%87%E8%A8%8A%E5%AF%A6%E8%AD%89/)
- The value ∩ size ∩ momentum screen gave only +0.4 points a year over TAIEX in 2014–2024, with higher risk — [TEJ](https://www.tejwin.com/insight/%E5%B0%A4%E9%87%91%E6%B3%95%E7%91%AA-%E4%B8%89%E5%9B%A0%E5%AD%90%E6%A8%A1%E5%9E%8B%E7%AD%96%E7%95%A5/)
- Monthly revenue drift after record highs stays positive after transaction costs for a 1-month hold — [Lai et al., SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5345252)
- The low-IVOL effect held over 2005–2025, and TEJ recommends using it as a filter — [TEJ IVOL](https://www.tejwin.com/en/insight/factor-research-idiosyncratic-volatility/)
- Momentum profits in Taiwan come mostly from the long leg (time-series 10.15% vs cross-sectional 6.90% annualized excess) — [MDPI JRFM 2026](https://www.mdpi.com/1911-8074/19/7/462)
- US cross-anomaly evidence: post-publication, trading costs (about 100 bp spreads) nearly eliminate the average anomaly's roughly 30 bp/month gross return — [Chen, Accounting for the Anomaly Zoo](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2019/09/Accounting-for-the-Anomaly-Zoo.pdf)

### Inferences
- **Cost arithmetic** (round trip about 0.585% at list fees):
  - monthly rebalance with 100% turnover ≈ 7.0% a year of drag;
  - 50% monthly turnover ≈ 3.5% a year;
  - weekly rebalance with 30% weekly turnover ≈ 52 × 0.3 × 0.585% ≈ 9.1% a year.
  - So weekly rebalancing needs a signal with weekly persistence; most Taiwan evidence (revenue, IVOL, shorting flows, persistent momentum) supports monthly holding horizons. Broker fee discounts lower this, but the 0.3% tax remains.
- **Ranking for a long-only, weekly-or-slower, 20-stock book** (by evidence quality × implementability × recency):
  1. Monthly revenue momentum (YoY acceleration, consecutive positive surprises, record highs), entered at the close or later, avoiding names with big pre-announcement run-ups; rebalanced monthly after the 10th–11th. Multiple academic studies, out-of-sample to 2021–2024, net-of-cost survival in one 2026 paper.
  2. Exclusion filters: high IVOL, heavy margin, high SBL short balance or shorting flow, top institutional herding/crowding, newly flagged attention/disposition/day-trading-alert stocks. Academic support exists for each; they work mainly by removing losers, which suits a long-only design.
  3. Persistence-filtered or intraday-return momentum and time-series trend, as secondary ranks. Academic, long-leg-driven, but magnitudes and post-2020 results are unverified.
  4. Analyst forecast revisions (+2.11% a month long-only) where consensus coverage exists. Mostly larger caps, which also helps against the size headwind.
  5. Not recommended standalone: 投信/外資 net-buy following, TDCC 大戶 changes, value (B/M), small-size tilt, 作帳 or seasonal timing, buying disposition stocks.
- **Implications of the post-2020 regime:**
  - A 20-stock equal-weight small and mid-cap portfolio is structurally short TSMC versus 0050. Beating the "same cash flow in 0050" benchmark requires either a large-cap-inclusive universe or accepting long stretches of underperformance in mega-cap-led markets (e.g. 2023–2025).
  - Size-neutral or large-cap-universe versions of revenue momentum and low-IVOL screens deserve priority testing.

### Gaps
- No source gives a clean post-2020 vs pre-2020 split for any Taiwan factor (revenue momentum, IVOL, momentum, flows). This is the key open question, and only the user's own point-in-time backtest (e.g. 2015-06 to 2020-09 development and 2020-10 onward validation) can settle it.
- No published Taiwan factor-ETF (00905, 00915, 00939 etc.) methodology or performance retrieved in this pass; only 00733 was covered.
- Full texts of the key 2023–2026 PBFJ/FRL papers were inaccessible (403). Magnitudes such as top-quintile monthly excess, t-stats and sample end dates should be checked before relying on them.
