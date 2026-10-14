"""2026-10-14 (資料缺口：處置／注意股): TWSE's attention and disposition announcements as factors."""
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research.chips import ALERT_FACTORS, ChipStore
from quant_platform.research.history import alerts

NOTICE = {"stat": "OK", "data": [
    [1, "1760", "寶齡富錦", "1", "最近六個營業日累積收盤價漲幅達30.18%。", "108.01.02", "90.00", "84.91"],
    [2, "076234", "國巨國泰7C購01", "1", "權證", "108.01.02", "1.0", ""],
    [3, "1760", "寶齡富錦", "2", "成交量放大", "108.01.04", "95.00", "85.00"]]}
PUNISH = {"stat": "OK", "data": [
    [1, "108/01/07", "1760", "寶齡富錦", 1, "連續三次", "108/01/08～108/01/21", "第一次處置", "…", ""],
    [2, "108/01/25", "082297", "可成富邦82購01", 1, "連續三次", "108/01/28～108/02/19", "第一次處置", "…", ""]]}


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_the_announcements_parse_to_common_stocks_only():
    assert alerts.parse_notice(NOTICE) == [(date(2019, 1, 2), "1760"), (date(2019, 1, 4), "1760")]
    assert alerts.parse_punish(PUNISH) == [(date(2019, 1, 7), "1760", date(2019, 1, 8), date(2019, 1, 21), 1)]


def test_counts_use_announcements_before_the_session(tmp_path):
    raw = tmp_path / "raw" / "twse_alerts"
    (raw / "notice").mkdir(parents=True)
    (raw / "punish").mkdir(parents=True)
    import json

    (raw / "notice" / "201901.json").write_text(json.dumps(NOTICE, ensure_ascii=False), encoding="utf-8")
    (raw / "punish" / "2019.json").write_text(json.dumps({"payload": PUNISH}, ensure_ascii=False), encoding="utf-8")
    assert alerts.build(tmp_path) == {"notice": 2, "disposition": 1}
    days = weekdays(date(2018, 12, 24), date(2019, 3, 29))
    chips = ChipStore(tmp_path, days, ["1760.TW", "1101.TW"], np.full((2, len(days)), 50.0))
    assert chips.alerts_available()
    attention = chips.alert_matrix("low_attention_20")
    at = {day: index for index, day in enumerate(days)}
    assert attention[0, at[date(2019, 1, 2)]] == 0 and attention[0, at[date(2019, 1, 3)]] == -1     # the day after
    assert attention[0, at[date(2019, 1, 7)]] == -2 and attention[1].max() == 0 == attention[1].min()
    assert attention[0, at[date(2019, 3, 29)]] == 0                                                  # 20 sessions on
    disposed = chips.alert_matrix("low_disposition_60")
    assert disposed[0, at[date(2019, 1, 7)]] == 0 and disposed[0, at[date(2019, 1, 8)]] == -1
    assert set(ALERT_FACTORS) == {"low_attention_20", "low_disposition_60"}


def test_alert_factors_are_rule_factors_but_not_model_features():
    from quant_platform.research import daily, model, scan

    assert set(ALERT_FACTORS) <= set(daily.FACTOR_LABELS) and set(ALERT_FACTORS) <= set(scan.signals())
    assert not set(ALERT_FACTORS) & set(model.features())
