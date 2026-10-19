"""2026-10-19 (使用者：還有什麼方法可以找到更好的策略): companies' buyback programs (公開資訊觀測站 t35sc09)."""
from datetime import date

from quant_platform.research.history import buyback

TABLE = """<html><body><table>
<tr><td>上市公司買回自己公司股份彙總統計表</td></tr>
<tr><td>日期：114/01/01~114/03/31</td><td>出表日：115/10/19</td></tr>
<tr><th>序 號</th><th>公司 代號</th><th>公司 名稱</th><th>董事會 決議日期</th><th>買回 目的</th></tr>
<tr><td>1</td><td>2371</td><td>大同</td><td>114/01/06</td><td>3</td><td>3,562,297,314</td><td>75,500,000</td>
<td>42.00</td><td>46.30</td><td>114/01/08</td><td>114/03/05</td><td>Y</td><td><a href="#">查詢</a></td>
<td>75,500,000</td><td>75,500,000</td><td>100.00</td><td>3,362,327,473</td><td>44.53</td><td>3.23</td><td></td></tr>
<tr><td>2</td><td>8482</td><td>商億-KY</td><td>114/01/15</td><td>1</td><td>500,000,000</td><td>1,000,000</td>
<td>60.00</td><td>90.00</td><td>114/01/16</td><td>114/03/15</td><td></td><td></td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table></body></html>"""


def test_each_program_parses_with_what_the_announcement_said():
    rows = buyback.parse(TABLE)
    assert len(rows) == 2 and buyback.answered(TABLE)
    first, second = rows
    assert first["code"] == "2371" and first["board_date"] == date(2025, 1, 6) and first["purpose"] == "3"
    assert first["planned_shares"] == 75_500_000 and first["price_high"] == 46.3
    assert first["window_end"] == date(2025, 3, 5) and first["average_price"] == 44.53   # known only afterwards
    assert second["executed_shares"] is None and second["finished"] == ""              # still running
    assert "average_price" not in buyback.KNOWN_AT_ANNOUNCEMENT


def test_the_fetch_asks_each_year_once_and_the_last_two_again(tmp_path):
    asked = []

    def post(market, year, today):
        asked.append((market, year))
        return TABLE
    buyback.fetch(tmp_path, date(2017, 3, 1), post=post, sleep=lambda seconds: None)
    assert len(asked) == 6                                                   # 2015-2017, two markets
    asked.clear()
    buyback.fetch(tmp_path, date(2017, 3, 1), post=post, sleep=lambda seconds: None)
    assert sorted(asked) == [("otc", 2016), ("otc", 2017), ("sii", 2016), ("sii", 2017)]
    built = buyback.build(tmp_path)
    assert built["programs"] == 2 and built["protect"] == 1                  # the same programs once
