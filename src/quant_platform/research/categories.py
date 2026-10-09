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
                 "ma_cross_20_60", "macd_hist", "rsi_14", "kd_k", "bollinger_b"),
    "反轉": ("reversal_1", "reversal_5d"),
    "低波動": ("low_volatility_60", "low_volatility_250", "low_max_return"),
    "價值殖利率": ("dividend_yield", "earnings_yield", "book_to_price"),
    "籌碼": ("foreign_holding", "foreign_holding_change", "foreign_buy_20", "trust_buy_20", "margin_growth_20",
             "short_margin_ratio"),
    "營收財報": ("revenue_yoy", "revenue_yoy_3m", "revenue_accel", "roe_ttm", "gross_margin", "operating_margin_change", "eps_growth",
                 "low_debt"),
    "成交量規模": ("liquidity", "volume_surge", "market_cap"),
}
FACTOR_FAMILY = {factor: family for family, factors in FAMILY_FACTORS.items() for factor in factors}
OPPOSITE = {"趨勢動能": "反轉", "反轉": "趨勢動能"}
FAMILY_ORDER = ("機器學習", "組合帳戶", "趨勢動能", "反轉", "低波動", "價值殖利率", "籌碼", "營收財報", "成交量規模",
                "多因子", "其他")
FAMILY_NOTES = {
    "機器學習": "把 35 個因子交給逐年滾動訓練的模型打分數",
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


def _family(factor: str, weight: float) -> str:
    if factor.startswith("ml_gbm"):
        return "機器學習"
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
        if "機器學習" in families:
            family = "機器學習"
        elif len(families) > 1:
            family = "多因子"
        else:
            family = families[0] if families else "其他"
        traits.append(_core(float(spec.get("core") or 0)))
        check = spec.get("check") or spec.get("rebalance")
        traits.append({"weekly": "每週決策", "monthly": "每月決策", "quarterly": "每季決策"}.get(str(check), "每天決策"))
        if spec.get("weighting") == "inverse_vol":
            traits.append("依波動度配置")
        if spec.get("industry_cap"):
            traits.append("產業上限")
        if spec.get("universe") == "all":
            traits.append("上市＋上櫃")
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
        if spec.get("news_veto") == "v1":
            traits.append("新聞否決")
        elif spec.get("news_veto") == "v1-off":
            traits.append("新聞否決對照")
        output = {"family": family, "families": families,
                  "detail": "＋".join(families) if family == "多因子" else "", "traits": traits}
    if source.startswith("AI 研究員"):
        output["traits"] = [*output["traits"], "AI 研究員提出"]
    return output


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
