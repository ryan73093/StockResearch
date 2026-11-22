"""A rule's settings said in full (2026-11-22, 使用者：停損設多少？買的時候用什麼策略？具體上的參數是什麼？完全沒有)."""
from quant_platform.research import pool
from quant_platform.research.daily import DailyRule


def _rows(spec):
    sections = pool.settings_sections(spec)
    assert len(sections) == 1
    return {row["item"]: row["value"] for row in sections[0]["rows"]}


def test_every_question_is_answered_and_an_unused_setting_says_so():
    plain = DailyRule(name="x", factors={"momentum_3": 1.0, "high_52w": 3.0}, top=20, min_hold=0).canonical()
    rows = _rows(plain)
    for item in ("買什麼（排名依據）", "買幾檔", "什麼時候決定", "怎麼成交", "每檔買多少", "什麼時候賣", "停損", "停利", "大盤不好時",
                 "不買的股票（否決）", "分散", "可以買的股票", "交易成本（回測與前向都扣）", "資金"):
        assert rows[item], item
    assert rows["停損"] == "沒有設停損" and rows["停利"] == "沒有設停利" and rows["不買的股票（否決）"] == "沒有"
    assert rows["大盤不好時"].startswith("沒有設") and rows["分散"] == "沒有上限"
    assert "佔 25%" in rows["買什麼（排名依據）"] and "佔 75%" in rows["買什麼（排名依據）"]      # the weights as shares
    assert "跌出前 60 名就賣" in rows["什麼時候賣"] and "0.3%" in rows["交易成本（回測與前向都扣）"]


def test_the_settings_a_rule_has_are_said_with_their_numbers():
    spec = DailyRule(name="x", factors={"momentum_3": 1.0}, top=10, keep=2, min_hold=20, stop_loss=0.15, cooldown=10,
                     vol_scale="us_hot_half", industry_cap=0.3, exclude="c1", check="weekly").canonical()
    rows = _rows(spec)
    assert "15%" in rows["停損"] and "10 個交易日" in rows["停損"]
    assert "跌出前 20 名" in rows["什麼時候賣"] and "滿 20 個交易日" in rows["什麼時候賣"]
    assert "美國景氣過熱" in rows["大盤不好時"] and "30%" in rows["分散"] and rows["不買的股票（否決）"] != "沒有"
    line = pool.settings_line(spec)
    assert line.startswith("買：") and "｜停損：" in line and "｜大盤不好時：" in line and "每週決定" in line


def test_a_blend_has_a_table_for_each_part():
    one = DailyRule(name="a", factors={"momentum_3": 1.0}).canonical()
    two = DailyRule(name="b", factors={"high_52w": 1.0}, stop_loss=0.2).canonical()
    blend = {"kind": "blend", "name": "組合", "sleeves": [{"share": 0.4, "rule": one}, {"share": 0.6, "rule": two}]}
    sections = pool.settings_sections(blend)
    assert [section["title"] for section in sections] == ["第 1 份：帳戶的 40%", "第 2 份：帳戶的 60%"]
    assert pool.settings_line(blend).startswith("組合：40% 買") and pool.settings_sections({"description": "old"}) == []


def test_a_factor_without_a_short_name_is_named_by_its_label_not_its_code():
    from quant_platform.research.daily import RULE_FACTORS
    from quant_platform.research.scan import SHORT

    for name in ("t0_consensus", "block_equal", "rl_blocks", "rl_rolling35", "rolling_equal", "revenue_yoy_3m"):
        if name in SHORT or name not in RULE_FACTORS:
            continue
        line = pool.settings_line({"factors": {name: 1.0}, "top": 20})
        assert name not in line and line.startswith("買："), (name, line)
