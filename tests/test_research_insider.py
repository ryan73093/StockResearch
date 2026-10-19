"""2026-10-19 (使用者：資料先用免費的): insiders' advance notices of share transfers (公開資訊觀測站)."""
import gzip
from datetime import date, datetime

import pytest

from quant_platform.research.history import insider

TABLE = """<html><body><table>
<tr><th>異動情形</th><th>申報日期</th><th>公司 代號</th><th>公司名稱</th><th>申報人身分</th><th>姓名</th>
<th>預定轉讓方式及股數</th><th>每日於盤中交易 最大得轉讓股數</th><th>受讓人</th><th>目前持有股數</th><th>預定轉讓總股數</th>
<th>預定轉讓後持股</th><th>有效轉讓期間</th><th>是否申報持 股未完成轉讓</th></tr>
<tr><th>轉讓方式</th><th>轉讓股數</th><th>自有持股</th><th>保留運用決定 權信託股數</th><th>自有持股</th>
<th>保留運用決定 權信託股數</th><th>自有持股</th><th>保留運用決定 權信託股數</th></tr>
<tr><td></td><td>114/03/05</td><td>2883</td><td>凱基金</td><td>子公司經理人配偶</td><td>某甲之配偶</td>
<td>一般交易(每日得轉讓股數限制)</td><td>60,000</td><td>18,420,237</td><td></td><td>67,298</td><td>0</td><td>60,000</td>
<td>0</td><td>7,298</td><td>0</td><td>114/03/08 ~ 114/04/07</td><td>是</td></tr>
<tr><td>114/03/06</td><td>4770</td><td>上品</td><td>董事本人</td><td>某乙</td><td>贈與</td><td></td><td></td><td>某丙</td>
<td>4,594,464</td><td>0</td><td>114,205</td><td>0</td><td>4,480,259</td><td>0</td><td>114/03/06 ~ 114/03/08</td><td></td></tr>
<tr><td colspan="18">(1) 以上資料係由各公司自行建檔提供參考 總計：2筆</td></tr>
</table></body></html>"""


def test_the_days_table_parses_from_each_filing_date_on():
    rows = insider.parse(TABLE)
    assert len(rows) == 2 and insider.answered(TABLE)
    sale, gift = rows
    assert sale["notice_date"] == date(2025, 3, 5) and sale["code"] == "2883" and sale["method"].startswith("一般交易")
    assert sale["shares"] == 60_000 and sale["held_own"] == 67_298 and sale["after_own"] == 7_298
    assert sale["period"] == "114/03/08 ~ 114/04/07" and sale["incomplete"] == "是"
    assert gift["code"] == "4770" and gift["method"] == "贈與" and gift["shares"] is None and gift["transferee"] == "某丙"
    assert gift["total_own"] == 114_205 and gift["change"] == ""
    assert insider.answered("<html>無持股轉讓之情形</html>") and not insider.answered("<html><script></script></html>")


def test_the_download_resumes_waits_out_the_quiet_hours_and_retries_refusals(tmp_path):
    calls, sleeps = [], []
    answers = iter(["<html>busy</html>"] + [TABLE] * 50)

    def post(day, market):
        calls.append((day, market))
        return next(answers)
    times = iter([datetime(2026, 10, 19, 13, 40)] + [datetime(2026, 10, 19, 15, 0)] * 100)
    found = insider.download(tmp_path, date(2026, 10, 6), post=post, sleep=sleeps.append, now=lambda: next(times),
                             first=date(2026, 10, 1))
    # weekdays 10/1, 10/2, 10/5 (before today), two markets; the first answer refused once
    assert found == {"wanted": 6, "stored": 6, "refused": 1}
    assert sleeps[0] == 300 and insider.BACKOFF in sleeps                    # waited out 13:30–14:40, then backed off
    assert insider.raw_path(tmp_path, "OY", date(2026, 10, 5)).is_file()
    again = insider.download(tmp_path, date(2026, 10, 6), post=post, sleep=sleeps.append,
                             now=lambda: datetime(2026, 10, 19, 20, 0), first=date(2026, 10, 1))
    assert again["wanted"] == 0                                              # stored days are not asked again
    built = insider.build(tmp_path)
    assert built["filings"] == 12 and built["on_market"] == 6 and built["days"] == 6


def test_three_refusals_stop_the_download(tmp_path):
    with pytest.raises(insider.Refused):
        insider.download(tmp_path, date(2026, 10, 2), post=lambda day, market: "", sleep=lambda seconds: None,
                         now=lambda: datetime(2026, 10, 19, 20, 0), first=date(2026, 10, 1))
    assert not list(tmp_path.rglob("*.gz"))
    path = insider.raw_path(tmp_path, "SY", date(2026, 10, 1))
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(TABLE.encode()))
    assert insider.build(tmp_path)["filings"] == 2
