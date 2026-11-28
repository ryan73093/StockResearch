"""2026-11-28 空頭防護第十五版: the US-overheating guard on a reproducible draw of rules that carry the half guard."""
from quant_platform.research import daily


def _spec(number: int) -> dict:
    return daily.DailyRule(name=f"規則 {number}：前 20 名・加權指數跌破 200 日均線就減碼一半｜持有：固定持有 20 天",
                           factors={"momentum_3": 1.0, "reversal_1": 1.0}, top=10 + number, check="weekly",
                           vol_scale="taiex_half").canonical()


def test_the_twin_changes_the_guard_and_nothing_else():
    spec = _spec(1)
    twin = daily.us_hot_twin(spec, 77)
    after = twin.canonical()
    assert after["vol_scale"] == "us_hot_half"
    assert {key: value for key, value in after.items() if key not in ("vol_scale", "name")} == {
        key: value for key, value in spec.items() if key not in ("vol_scale", "name")}
    assert "美國景氣過熱" in twin.name and "#77" in twin.name and "加權指數跌破 200 日均線就減碼一半" not in twin.name
    assert len(twin.name) <= 80


def test_the_draw_reads_no_result_is_the_same_again_and_leaves_registered_settings_out():
    items = [(number, daily.DailyRule.model_validate(_spec(number)).rule_hash, _spec(number)) for number in range(40)]
    first = daily.us_hot_sample(items, set(), 10)
    again = daily.us_hot_sample(list(reversed(items)), set(), 10)
    assert [rule.rule_hash for rule in first] == [rule.rule_hash for rule in again] and len(first) == 10
    assert len({rule.rule_hash for rule in first}) == 10
    assert sorted(int(rule.name.split("#")[1].split("）")[0]) for rule in first) != list(range(10))   # not the first ten
    left = daily.us_hot_sample(items, {first[0].rule_hash}, 10)
    assert len(left) == 9 and first[0].rule_hash not in {rule.rule_hash for rule in left}
    assert daily.BATCHES["bear15"] is daily.us_hot_twins_sample
