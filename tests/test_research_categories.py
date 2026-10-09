"""2026-10-09: a rule's category and traits (使用者：策略清單裡面我要看到分類)."""

from quant_platform.research.categories import classify, summary


def test_categories_follow_the_factors():
    assert classify({"factors": {"trend_200": 1.0}, "core": 0.5, "check": "weekly"}) == {
        "family": "趨勢動能", "families": ["趨勢動能"], "detail": "", "traits": ["一半放 0050", "每週決策"]}
    assert classify({"factors": {"rsi_14": -1.0}})["family"] == "反轉"            # buying the oversold
    assert classify({"factors": {"ml_gbm_statements": 1.0, "trend_200": 1.0}})["family"] == "機器學習"
    mixed = classify({"factors": {"trend_200": 1.0, "revenue_yoy": 1.0}, "weighting": "inverse_vol", "stop_loss": 0.15})
    assert mixed["family"] == "多因子" and mixed["detail"] == "趨勢動能＋營收財報"
    assert {"依波動度配置", "停損", "全部個股", "每天決策"} <= set(mixed["traits"])
    assert classify({"factor": "high_52w", "rebalance": "monthly"})["family"] == "趨勢動能"      # an older stock rule
    assert "AI 研究員提出" in classify({"factors": {"revenue_accel": 1.0}}, "AI 研究員 20261010-1")["traits"]


def test_a_blend_is_its_own_category():
    spec = {"kind": "blend", "core": 0.5, "sleeves": [{"rule": {"factors": {"ml_gbm_statements": 1.0}}, "share": 0.25},
                                                      {"rule": {"factors": {"trend_200": 1.0}}, "share": 0.25}]}
    item = classify(spec)
    assert item["family"] == "組合帳戶" and item["detail"] == "機器學習＋趨勢動能"
    assert item["traits"] == ["一半放 0050", "組合：機器學習＋趨勢動能"]


def test_the_summary_counts_tiers_and_names_the_best():
    rows = [{"name": "甲", "tier": "T1", "excess": 1.0, "category": {"family": "趨勢動能"}},
            {"name": "乙", "tier": "T0 候選", "excess": 0.5, "category": {"family": "趨勢動能"}},
            {"name": "丙", "tier": "T3", "excess": -1.0, "category": {"family": "籌碼"}}]
    first, second = summary(rows)
    assert first["family"] == "趨勢動能" and first["count"] == 2 and first["best"]["name"] == "乙"
    assert first["tiers"]["T1"] == 1 and second["family"] == "籌碼"
