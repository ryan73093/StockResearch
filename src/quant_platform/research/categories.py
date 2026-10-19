"""2026-10-09 (使用者：策略清單裡面我要看到分類): a rule's category and traits, read from its spec.

The category is what the rule bets on — the family of its factors: a trained model, a blend of accounts,
trends and momentum, reversals, calm stocks, cheap or high-yield stocks, chip flows, revenue and
statements, trading volume and size, or several of these at once. Traits are how it holds and trades:
the share kept in 0050, how often it decides, the weighting, the risk controls, the stock universe and
who proposed it. A factor with a negative weight bets the other way (a reversed oscillator buys the
oversold: a reversal).
"""

from __future__ import annotations

FAMILY_FACTORS = {
    "趨勢動能": ("trend_200", "momentum_12_1", "momentum_6", "momentum_3", "high_52w", "breakout_55",
                 "ma_cross_20_60", "macd_hist", "rsi_14", "kd_k", "bollinger_b", "fip_12", "imom_12", "resid_mom_12",
                 "high_52w_count_60", "range_position_60", "industry_mom_6", "industry_mom_1"),
    "反轉": ("reversal_1", "reversal_5d", "overnight_reversal_12", "industry_laggard_5"),
    "低波動": ("low_volatility_60", "low_volatility_250", "low_max_return", "low_ivol_60"),
    "價值殖利率": ("dividend_yield", "earnings_yield", "book_to_price"),
    "籌碼": ("foreign_holding", "foreign_holding_change", "foreign_buy_20", "trust_buy_20", "margin_growth_20",
             "short_margin_ratio", "dealer_buy_20", "dealer_hedge_20", "trust_quarter_end",
             # 2026-10-17: the lending and short-selling, day-trading and notice families
             "low_sbl_short", "sbl_cover_20", "low_day_trade_20", "low_lending_fee_60", "low_lending_20",
             "lending_fee_not_rising", "low_attention_20", "low_disposition_60"),
    "公司事件": ("dividend_raise", "no_cash_issue", "low_issuance_250", "dividend_ahead",
                 "low_insider_sell_60", "buyback_60", "low_pledge", "pledge_heavy"),
    "營收財報": ("revenue_yoy", "revenue_yoy_3m", "revenue_accel", "roe_ttm", "gross_margin", "operating_margin_change", "eps_growth",
                 "low_debt"),
    "成交量規模": ("liquidity", "volume_surge", "market_cap", "low_pv_corr_20"),
}
FACTOR_FAMILY = {factor: family for family, factors in FAMILY_FACTORS.items() for factor in factors}
OPPOSITE = {"趨勢動能": "反轉", "反轉": "趨勢動能"}
FAMILY_ORDER = ("T0 共識清單", "機器學習", "深度學習", "RL 機器人", "技術分析機器人", "組合帳戶", "趨勢動能", "反轉", "低波動",
                "價值殖利率", "籌碼", "公司事件", "營收財報", "成交量規模", "多因子", "反向（學著賠）", "其他")
FAMILY_NOTES = {
    "機器學習": "把 35 個因子交給逐年滾動訓練的模型打分數",
    "技術分析機器人": "只看價格與成交量的機器人：強化學習版自己決定買進和賣出；第三版是深度學習替股票排名",
    "深度學習": "深度學習模型（讀每檔最近 40 個交易日）打分數",
    "RL 機器人": "強化學習決定各訊號的權重，或每天自己決定每檔比重與現金（含它的對照組：同樣訊號平均）",
    "公司事件": "看股利調升、現金增資、股本膨脹、即將除息等公司事件",
    "反向（學著賠）": "把訊號反過來用、或學著賠最多（趣味與檢查，不是策略）",
    "T0 共識清單": "所有 T0 候選各自替股票排名，取平均成一張清單，帳戶只交易這一張",
    "組合帳戶": "帳戶分成幾份，各自照不同規則操作",
    "趨勢動能": "買正在漲、站上均線或接近高點的股票",
    "反轉": "買最近跌深或超賣的股票",
    "低波動": "買走勢平穩的股票",
    "價值殖利率": "買本益比、淨值比低或殖利率高的股票",
    "籌碼": "看外資、投信、融資券的動向",
    "營收財報": "看月營收成長與季財報",
    "成交量規模": "看成交值、量能放大或市值",
    "多因子": "同時用兩類以上的因子",
    "其他": "",
}


def _core(core: float) -> str:
    return "一半放 0050" if abs(core - 0.5) < 1e-9 else (f"{core:.0%} 放 0050" if core else "全部個股")


ROBOT_FACTORS = ("rl_blocks", "block_equal", "rl_rolling", "rolling_equal", "rl_rolling32", "rolling32_equal", "rl_rolling33", "rolling33_equal", "rl_all", "all_equal", "rl_free", "rl_free41",
                 "rl_timing", "tech_equal")


def _family(factor: str, weight: float) -> str:
    if factor == "rl_worst" or (weight < 0 and (factor.startswith("ml_") or factor in ROBOT_FACTORS)):
        return "反向（學著賠）"
    if factor == "ml_tech_deep":                                 # 2026-10-20: the technical robot's third version
        return "技術分析機器人"
    if factor.startswith("ml_deep"):
        return "深度學習"
    if factor in ROBOT_FACTORS:
        return "RL 機器人"
    if factor.startswith(("ml_gbm", "ml_tech")):
        return "機器學習"
    if factor.endswith("_consensus"):
        return "T0 共識清單"
    if factor.startswith("rl_tech"):
        return "技術分析機器人"
    family = FACTOR_FAMILY.get(factor, "其他")
    return OPPOSITE.get(family, family) if weight < 0 else family


def classify(spec: dict, source: str = "") -> dict[str, object]:
    """{"family", "families" (each family used), "detail" (for a multi-factor rule), "traits"}."""
    traits: list[str] = []
    if spec.get("kind") == "blend":
        parts = [classify(sleeve.get("rule") or {}) for sleeve in spec.get("sleeves") or []]
        families = [item["family"] for item in parts]
        traits.append(_core(float(spec.get("core") or 0)))
        traits.append("組合：" + "＋".join(dict.fromkeys(families)))
        output = {"family": "組合帳戶", "families": list(dict.fromkeys(families)), "detail": "＋".join(families),
                  "traits": traits}
    else:
        factors = spec.get("factors")
        if factors is None and spec.get("factor"):           # an older monthly stock rule
            factors = {spec["factor"]: 1.0, **(spec.get("extra") or {})}
        factors = factors or {}
        families = list(dict.fromkeys(_family(name, float(weight)) for name, weight in factors.items()))
        if "反向（學著賠）" in families:
            family = "反向（學著賠）"
        elif "RL 機器人" in families:
            family = "RL 機器人"
        elif "技術分析機器人" in families:
            family = "技術分析機器人"
        elif "深度學習" in families:
            family = "深度學習"
        elif "機器學習" in families:
            family = "機器學習"
        elif len(families) > 1:
            family = "多因子"
        else:
            family = families[0] if families else "其他"
        traits.append(_core(float(spec.get("core") or 0)))
        check = spec.get("check") or spec.get("rebalance")
        traits.append({"weekly": "每週決策", "monthly": "每月決策", "quarterly": "每季決策",
                       "revenue": "營收公布後每月決策"}.get(str(check), "每天決策"))
        if spec.get("weighting") == "inverse_vol":
            traits.append("依波動度配置")
        if spec.get("industry_cap"):
            traits.append("產業上限")
        if spec.get("universe") == "all":
            traits.append("上市＋上櫃")
        if spec.get("switching") not in (None, "none"):
            traits.append("看成本才換股")
        if spec.get("exclude") not in (None, "none"):
            traits.append("不買樂透型股票")
        if spec.get("large_caps"):               # equal amounts unless weighted by calmness (its own trait)
            equal = spec.get("weighting", "equal") == "equal"
            traits.append(f"只挑市值前 {spec['large_caps']} 大" + ("、每檔等額" if equal else ""))
        if spec.get("stop_loss"):
            traits.append("停損")
        if spec.get("market_filter") not in (None, "none"):
            traits.append("大盤濾網")
        if spec.get("account_filter") not in (None, "none"):
            traits.append("帳戶濾網")
        if spec.get("smooth"):
            traits.append("分數平均")
        if spec.get("exit_model") not in (None, "none"):
            traits.append("學習出場")
        if spec.get("vol_scale") not in (None, "none"):
            traits.append("波動大時減碼")
        if spec.get("news_veto") == "v1":
            traits.append("新聞否決")
        elif spec.get("news_veto") == "v1-off":
            traits.append("新聞否決對照")
        output = {"family": family, "families": families,
                  "detail": "＋".join(families) if family == "多因子" else "", "traits": traits}
    if source.startswith("AI 研究員"):
        output["traits"] = [*output["traits"], "AI 研究員提出"]
    return output


NO_0050 = "使用者 2026-10-09：0050 是自己另外持有的部位，策略一律 100% 個股；含 0050 的規則只留紀錄、不採用"


def uses_0050(spec: dict) -> bool:
    """A rule that keeps part of the account in 0050 (a core share, a blend's 0050 part) or moves into it (the
    account filter): not a strategy the owner can use (NO_0050)."""
    if spec.get("kind") == "blend":
        return bool(spec.get("core")) or any(uses_0050(sleeve.get("rule") or {}) for sleeve in spec.get("sleeves") or [])
    return bool(spec.get("core")) or spec.get("account_filter") not in (None, "none")


def summary(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Per category, in a fixed order: how many rules, how many in each tier, and the best one."""
    from quant_platform.research.daily import TIERS

    output = []
    for family in FAMILY_ORDER:
        mine = [row for row in rows if (row.get("category") or {}).get("family") == family]
        if not mine:
            continue
        best = max(mine, key=lambda row: (-TIERS.index(row["tier"]), row.get("excess") if row.get("excess") is not None else -9))
        output.append({"family": family, "note": FAMILY_NOTES.get(family, ""), "count": len(mine),
                       "tiers": {tier: sum(1 for row in mine if row["tier"] == tier) for tier in TIERS},
                       "best": best})
    return output
