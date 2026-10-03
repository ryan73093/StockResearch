import json
from datetime import date, timedelta

import pytest

from quant_platform.research.costs import BROKERS, CostModel, fill_price, stock_tick
from quant_platform.research.legacy_challenger import (
    BENCHMARK, LegacyData, Variant, early_large, evaluate, exchange_events, rankings, roc_date, signal_quality,
    simulate, unit_factors,
)


def weekdays(start, count):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def data(days, closes, predictions, traded=50_000_000.0, factors=None):
    return LegacyData(
        sessions=days,
        closes={symbol: dict(zip(days, values)) for symbol, values in closes.items()},
        traded_value={symbol: {day: traded for day in days} for symbol in closes},
        factors=factors or {},
        predictions=predictions,
    )


def test_stock_ticks_and_fills():
    assert [stock_tick(price) for price in (9.99, 10, 49.9, 50, 99, 100, 499, 500, 999, 1000)] == [
        0.01, 0.05, 0.05, 0.1, 0.1, 0.5, 0.5, 1.0, 1.0, 5.0]
    # 600 + 20 bps = 601.2 → up to the NT$1 tick for stocks (an ETF would use 0.05).
    assert fill_price(600.0, "BUY", 20, stock_tick) == 602.0 and fill_price(600.0, "BUY", 20) == 601.2


def test_official_events_and_unit_factors(tmp_path):
    raw = tmp_path / "raw"
    (raw / "twse_ex_rights").mkdir(parents=True)
    (raw / "tpex_ex_rights").mkdir(parents=True)
    (raw / "twse_ex_rights" / "2025.json").write_text(json.dumps({"payload": {
        "fields": ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價", "權值+息值", "權/息"],
        "data": [["114年07月16日", "2330", "台積電", "1,100.00", "1,095.00", "5.00", "息"],
                 ["114年08月01日", "1234", "某公司", "50.00", "45.00", "5.00", "權息"]]}}), encoding="utf-8")
    (raw / "tpex_ex_rights" / "2025.json").write_text(json.dumps({"tables": [{
        "fields": ["除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價", "權/息"],
        "data": [["114/09/10", "6488", "環球晶", "400.00", "392.00", "除息"]]}]}), encoding="utf-8")

    events = exchange_events(raw, 2025)
    assert roc_date("115年01月02日") == date(2026, 1, 2) and roc_date("114/09/10") == date(2025, 9, 10)
    assert events["2330.TW"][date(2025, 7, 16)] == ("cash", 1100.0, 1095.0)
    assert events["1234.TW"][date(2025, 8, 1)][0] == "shares" and "6488.TWO" in events

    days = [date(2025, 7, 15), date(2025, 7, 16), date(2025, 7, 17), date(2025, 7, 18)]
    closes = dict(zip(days, [1100.0, 1096.0, 1098.0, 274.5]))           # cash dividend, then a 1:4 split
    factors = unit_factors(closes, {}, {date(2025, 7, 16): events["2330.TW"][date(2025, 7, 16)]})
    assert factors[date(2025, 7, 16)] == pytest.approx(1100 / 1095) and factors[date(2025, 7, 18)] == 4


def test_ranking_needs_liquidity_price_and_a_prediction():
    days = weekdays(date(2024, 1, 1), 25)
    market = data(days, {"A.TW": [20.0] * 25, "B.TW": [20.0] * 25, "C.TW": [5.0] * 25, BENCHMARK: [100.0] * 25},
                  {days[24]: {"A.TW": 0.01, "B.TW": 0.03, "C.TW": 0.09}})
    market.traded_value["B.TW"] = {day: 1_000_000.0 for day in days}     # too thin to trade odd lots in
    assert rankings(market)[days[24]] == ["A.TW"]                         # C is under NT$10, B too thin


def test_strategies_by_hand():
    days = weekdays(date(2024, 1, 1), 40)                                 # contribution on 01-05
    closes = {"A.TW": [20.0] * 40, "B.TW": [40.0] * 40, "C.TW": [30.0] * 40, BENCHMARK: [100.0] * 40}
    predictions = {day: {"A.TW": 0.03, "B.TW": 0.02, "C.TW": 0.01} for day in days}
    predictions.update({day: {"A.TW": 0.01, "B.TW": 0.02, "C.TW": 0.03} for day in days[20:]})   # C leads later
    market = data(days, closes, predictions)
    costs = BROKERS["cathay"].cost_model()                                # minimum fee NT$1

    hold = simulate(market, Variant("top2_hold", "x", 2, "never"), costs, days[0], days[-1])
    # 01-05 ranks A, B: 5,000 each → A 249 × 20.05 + fee 7, B 124 × 40.10 + fee 7 (cash 21.15).
    # 02-05 ranks C, B: 10,021.15 / 2 → C 166 × 30.10 + 7, B 124 × 40.10 + 7 (cash 38.15).
    assert (hold.trades, hold.taxes) == (4, 0)
    assert hold.final_value == pytest.approx(249 * 20 + 248 * 40 + 166 * 30 + 38.15)

    weekly = simulate(market, Variant("top2_weekly", "x", 2, "every_5"), costs, days[0], days[-1])
    # 01-29 (session 21) ranks C, B: A sold 249 × 19.95 = 4,967.55 − fee 7 − tax 14; C 164 × 30.10 + 7.
    # 02-05: 10,024.30 cash, equal targets 9,952.15 → C +166, B +124 (cash 41.30); no trade below one share.
    assert (weekly.trades, weekly.taxes) == (6, 14)
    assert weekly.final_value == pytest.approx(330 * 30 + 248 * 40 + 41.30)

    dca = simulate(market, None, costs, days[0], days[-1])
    # 01-05: 99 × 100.2 + fee 14 = 9,933.80; 02-05: 10,066.20 → 100 × 100.2 + 14.
    assert dca.trades == 2 and dca.final_value == pytest.approx(199 * 100 + 20_000 - 199 * 100.2 - 28)


def test_dividends_and_splits_grow_units_on_both_sides():
    days = weekdays(date(2024, 1, 1), 10)
    closes = {BENCHMARK: [100.0, 100.0, 100.0, 100.0, 100.0, 98.0, 98.0, 24.5, 24.5, 24.5]}
    factors = {BENCHMARK: {days[5]: 100 / 98, days[7]: 4.0}}             # NT$2 dividend, then a 1:4 split
    market = data(days, closes, {}, factors=factors)

    run = simulate(market, None, CostModel(minimum_fee=1, slippage_bps=0), days[0], days[-1])
    # 01-05: 99 × 100 + fee 14 = 9,914 (cash 86); dividend reinvested at once, split ×4: value unchanged.
    assert run.values[-1] == pytest.approx(99 * 100 / 98 * 4 * 24.5 + 86)


def test_signal_quality_and_evaluation_report():
    days = weekdays(date(2021, 1, 4), 900)
    symbols = [f"{1000 + index}.TW" for index in range(25)]
    # Each stock grows at its own steady pace; the model ranks them exactly by that pace.
    closes = {symbol: [20.0 * (1 + 0.0002 * (index + 1)) ** step for step in range(900)]
              for index, symbol in enumerate(symbols)}
    closes[BENCHMARK] = [100.0 * 1.0003 ** step for step in range(900)]
    predictions = {day: {symbol: 0.001 * (index + 1) for index, symbol in enumerate(symbols)} for day in days[30:]}
    market = data(days, closes, predictions)

    quality = signal_quality(market, rankings(market))
    assert quality["mean_rank_ic"] == pytest.approx(1.0) and quality["top5_minus_average_5d"] > 0

    report = evaluate(market, brokers=("cathay",), months=12, variants=(Variant("top5_hold", "x", 5, "never"),),
                      random_seeds=3, random_window_seeds=2)
    rows = {row["variant"]: row for row in report["results"]}
    assert set(rows) == {"dca_0050", "top5_hold"} and rows["top5_hold"]["windows_3y"]["count"] > 0
    legacy = rows["top5_hold"]
    assert legacy["full_excess"] > 0                                    # faster growers beat the slower 0050
    # The model picks the fastest growers, random picks the average, the worst picks the slowest.
    assert legacy["full_excess"] > legacy["random_control"]["full_excess_mean"] > legacy["worst_control"]["full_excess"]
    assert legacy["model_contribution"] > 0 and "「x」同時勝過 0050、同規則隨機選股與選預測最差的" in report["verdict"]
    assert "選預測最差的也勝過隨機選股" not in report["verdict"]       # here the worst picks lose to random ones
    # The ranking never changes, so the matched random control never changes names either: 5 buys a month.
    assert legacy["random_control"]["trades_mean"] == legacy["trades"]
    # Every stock traded the same before the period, so the sensitivity run keeps all 25 and agrees.
    sensitivity = {row["variant"]: row for row in report["sensitivity"]["results"]}
    assert report["sensitivity"]["symbols"] == 25 and sensitivity["top5_hold"]["full_excess"] == legacy["full_excess"]
    assert report["sensitivity"]["scan"][0]["top5_hold"]["model"] == legacy["full_excess"]
    assert "倖存者偏差較小）時，「x」比 0050 多" in report["verdict"]
    assert "結論：「x」在倖存者偏差較小的股票池仍有加分" in report["verdict"]
    assert report["period"]["benchmark_growth"] == pytest.approx(1.0003 ** 869, rel=1e-4)   # sessions 30–899


def test_an_edge_that_rests_on_stocks_that_were_small_is_not_promoted():
    days = weekdays(date(2021, 1, 4), 900)
    symbols = [f"{1000 + index}.TW" for index in range(25)]
    closes = {symbol: [20.0 * (1 + 0.0002 * (index + 1)) ** step for step in range(900)]
              for index, symbol in enumerate(symbols)}
    closes[BENCHMARK] = [100.0 * 1.0003 ** step for step in range(900)]
    # Right about the ten that were small before the period (and grew), backwards about the large ones.
    predictions = {day: {symbol: (0.1 + 0.001 * index if index >= 15 else 0.001 * (15 - index))
                         for index, symbol in enumerate(symbols)} for day in days[30:]}
    market = data(days, closes, predictions)
    for symbol in symbols[15:]:
        market.traded_value[symbol] = {day: (1e6 if day < days[30] else 5e7) for day in days}

    report = evaluate(market, brokers=("cathay",), months=12, variants=(Variant("top5_hold", "x", 5, "never"),),
                      random_seeds=3, random_window_seeds=2, early_count=10, scan_sizes=(10,))
    legacy = {row["variant"]: row for row in report["results"]}["top5_hold"]
    inner = {row["variant"]: row for row in report["sensitivity"]["results"]}["top5_hold"]
    assert legacy["full_excess"] > legacy["random_control"]["full_excess_mean"] > legacy["worst_control"]["full_excess"]
    assert inner["full_excess"] < inner["worst_control"]["full_excess"]   # among the large ones it is backwards
    assert "「x」同時勝過 0050" in report["verdict"]
    assert "結論：贏 0050 的部分要靠當時還小、後來長大進入股票池的公司" in report["verdict"]
    assert report["headline"].startswith("「x」看起來勝過 0050，但優勢要靠後來長大的公司")


def test_the_research_page_view_of_a_report(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app
    from quant_platform.research.legacy_challenger import challenger_view, save_report

    days = weekdays(date(2021, 1, 4), 600)
    symbols = [f"{1000 + index}.TW" for index in range(25)]
    closes = {symbol: [20.0 * (1 + 0.0002 * (index + 1)) ** step for step in range(600)]
              for index, symbol in enumerate(symbols)}
    closes[BENCHMARK] = [100.0 * 1.0003 ** step for step in range(600)]
    predictions = {day: {symbol: 0.001 * (index + 1) for index, symbol in enumerate(symbols)} for day in days[30:]}
    report = evaluate(data(days, closes, predictions), brokers=("conservative", "cathay"), months=12,
                      variants=(Variant("top5_hold", "x", 5, "never"),), random_seeds=2, random_window_seeds=1,
                      scan_sizes=(10, 20))
    view = challenger_view(json.loads(json.dumps(report)))              # as read back from the saved file
    assert view["broker"] == "國泰證券" and [row["key"] for row in view["rows"]] == ["dca_0050", "top5_hold"]
    legacy = view["rows"][1]
    assert legacy["scan"][-1] == legacy["excess"] and len(legacy["scan"]) == 3 and view["scan_sizes"] == [10, 20]
    assert view["headline"] == report["headline"] and challenger_view(None) is None

    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False)
    )).test_client()
    assert "舊版挑戰者評估" not in client.get("/research?tab=legacy").get_data(as_text=True)
    save_report(report, tmp_path / "research" / "legacy")
    body = client.get("/research?tab=legacy").get_data(as_text=True)
    assert "舊版挑戰者評估" in body and report["headline"] in body and "前 20 名" in body


def test_early_large_keeps_the_stocks_most_traded_before_the_first_prediction():
    days = weekdays(date(2024, 1, 1), 30)
    market = data(days, {"A.TW": [20.0] * 30, "B.TW": [20.0] * 30, "C.TW": [20.0] * 30},
                  {day: {"A.TW": 0.01, "B.TW": 0.02, "C.TW": 0.03} for day in days[20:]})
    market.traded_value["A.TW"] = {day: 9e7 for day in days}
    market.traded_value["B.TW"] = {day: (1e6 if day < days[20] else 9e9) for day in days}   # grew later
    market.traded_value["C.TW"] = {day: 5e7 for day in days}
    assert early_large(market, 2) == {"A.TW", "C.TW"}


def test_controls_pick_the_worst_or_at_random_reproducibly():
    from quant_platform.research.legacy_challenger import control, picks_for

    ranked = ["A", "B", "C", "D", "E"]
    rule = Variant("top2_monthly", "x", 2, "monthly")
    assert picks_for(ranked, rule, date(2024, 1, 5)) == ["A", "B"]
    assert picks_for(ranked, control(rule, "worst"), date(2024, 1, 5)) == ["E", "D"]
    first = picks_for(ranked, control(rule, "random", 1), date(2024, 1, 5))
    assert first == picks_for(ranked, control(rule, "random", 1), date(2024, 1, 5)) and len(set(first)) == 2
    assert control(rule, "random", 1).key == "random_top2_monthly"


def test_random_controls_change_as_many_names_as_the_model():
    from quant_platform.research.legacy_challenger import control, picks_for

    rule = control(Variant("top2_monthly", "x", 2, "monthly"), "random", 3)
    ranked = ["A", "B", "C", "D", "E", "F", "G", "H"]
    held, model = ["G", "H"], ["A", "B"]
    # The model kept both of its names: so does the control.
    assert set(picks_for(ranked, rule, date(2024, 2, 5), held, model)) == {"G", "H"}
    # The model swapped one name (B → C): the control keeps one of its two and adds one it did not hold.
    swapped = picks_for(["A", "C", "B", "D", "E", "F", "G", "H"], rule, date(2024, 2, 5), held, model)
    assert len(set(swapped) & {"G", "H"}) == 1 and len(set(swapped)) == 2
    # A held name that is no longer eligible is replaced even though the model changed nothing.
    replaced = picks_for(ranked[:-1], rule, date(2024, 2, 5), held, model)
    assert "G" in replaced and "H" not in replaced and len(set(replaced)) == 2
