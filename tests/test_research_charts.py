"""2026-10-25 (使用者：我的 dashboard 呢、線圖呢): research/charts.py draws every chart at four widths, with hover text."""
from datetime import date

from quant_platform.research import charts


def test_a_line_chart_is_drawn_at_four_widths_with_hover_columns_and_end_labels():
    points = [{"x": date(2015 + index, 1, 1), "a": 100.0 * (index + 1), "b": 50.0 + index} for index in range(6)]
    svg = charts.line(points, [{"key": "a", "label": "策略", "css": "v1", "short": "策略"},
                               {"key": "b", "label": "0050", "css": "vref", "dashed": True}],
                      lambda value: f"{value:.0f} 萬", lambda x, full: x.strftime("%Y-%m") if full else str(x.year), "測試")
    assert svg.count("<svg") == 4 and "viz--xwide" in svg and "viz--narrow" in svg
    assert svg.count('class="col"') == 24                                   # one hover column a point, each size
    assert "2020-01\n策略：600 萬\n0050：55 萬" in svg                       # every line's value in the column
    assert "策略 600 萬" in svg and 'stroke-dasharray="5 4"' in svg         # named at the line end; 0050 dashed


def test_bars_hang_below_zero_and_carry_titles():
    groups = [{"label": "2008", "note": "撐過 0／20", "values": {"bench": -0.55, "plain": -0.63},
               "titles": {"bench": "0050 跌 -55%"}},
              {"label": "2022", "values": {"bench": -0.34, "plain": -0.25}}]
    svg = charts.bars(groups, [{"key": "bench", "label": "0050", "css": "vref"}, {"key": "plain", "label": "策略", "css": "v1"}],
                      lambda value: f"{value * 100:+.0f}%", "空頭")
    assert svg.count("<svg") == 4 and "<title>0050 跌 -55%</title>" in svg and "<title>2022 策略：-25%</title>" in svg
    assert "撐過 0／20" in svg and 'class="zero"' in svg
    assert charts.bars([], [], str, "空") == "" and charts.ticks(0, 10, 5) == [0, 2, 4, 6, 8, 10]
