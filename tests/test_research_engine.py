from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.compare import compare_to_benchmark, rolling_windows
from quant_platform.research.costs import CostModel, affordable_shares, fill_price
from quant_platform.research.engine import simulate
from quant_platform.research.market import MarketData
from quant_platform.research.metrics import max_drawdown, unit_values, xirr
from quant_platform.research.spec import BASELINES, Allocation, Sizing, StrategySpec, json_schema


def weekdays(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


SESSIONS = weekdays(date(2020, 1, 1), date(2020, 3, 31))


def market(closes=None, dividends=None, ratios=None, assets=("0050",)) -> MarketData:
    return MarketData(
        sessions=SESSIONS,
        closes=closes or {asset: {day: 100.0 for day in SESSIONS} for asset in assets},
        dividends=dividends or {},
        unit_ratios=ratios or {},
        tax_kind={"0050": "stock_etf", "00679B": "bond_etf"},
        fingerprint="test",
    )


PLAN = ContributionPlan(monthly_amount=10_000, day_of_month=5)
DCA = BASELINES["benchmark_dca"]
# The hand calculations below use 10 bps slippage (fills at 100.10).
TEN_BPS = CostModel(slippage_bps=10.0)


def test_default_costs_are_conservative_with_20_bps_slippage():
    from quant_platform.research.costs import BROKERS, broker_costs

    assert CostModel().slippage_bps == 20 and CostModel().minimum_fee == 20 and CostModel().fee_discount == 1
    assert fill_price(100.0, "BUY", 20) == 100.20
    assert broker_costs(None) == CostModel() and broker_costs("unknown") == CostModel()
    cathay = broker_costs("cathay")
    assert (cathay.fee_discount, cathay.minimum_fee) == (0.28, 1)
    assert cathay.fee(9_919.8) == 3              # floor(9,919.8 × 0.1425% × 0.28) = floor(3.958)
    assert cathay.fee(100) == 1                  # the odd-lot minimum
    assert not any(profile.confirmed for profile in BROKERS.values())

    result = simulate(DCA, market(), PLAN)
    # 99 × 100.20 + 20 leaves 60.20; 100 shares next (10,060.20 − 10,040 → 20.20);
    # 99 in March (10,020.20 − 9,939.80 → 80.40).
    assert [trade.shares for trade in result.trades] == [99, 100, 99]
    assert result.final_cash == pytest.approx(80.4)


def test_costs_and_fills_follow_broker_rules():
    costs = TEN_BPS
    assert costs.fee(9_909.9) == 20            # floor(14.12) is below the minimum
    assert costs.fee(100_000) == 142           # floor(142.5)
    assert costs.tax(100_000, "stock_etf", "SELL") == 100
    assert costs.tax(100_000, "bond_etf", "SELL") == 0
    assert costs.tax(100_000, "stock_etf", "BUY") == 0
    assert fill_price(100.0, "BUY", 10) == 100.10
    assert fill_price(49.99, "BUY", 10) == 50.05   # crosses into the 0.05 tick band
    assert fill_price(100.0, "SELL", 10) == 99.90
    assert fill_price(20.0, "BUY", 10) == 20.02
    assert affordable_shares(10_000, 100.10, costs) == 99


def test_contribution_schedule_moves_to_the_next_session():
    assert PLAN.schedule(SESSIONS, date(2020, 1, 1), date(2020, 3, 31)) == [
        (date(2020, 1, 6), 10_000.0),   # 01-05 is a Sunday
        (date(2020, 2, 5), 10_000.0),
        (date(2020, 3, 5), 10_000.0),
    ]
    month_end = ContributionPlan(monthly_amount=1, day_of_month=31)
    # February clamps to 02-29 (Saturday) and moves to 03-02; March pays on 03-31.
    assert [day for day, _ in month_end.schedule(SESSIONS, date(2020, 2, 1), date(2020, 3, 31))] == [
        date(2020, 3, 2), date(2020, 3, 31),
    ]
    assert month_end.schedule(SESSIONS, date(2020, 2, 1), date(2020, 2, 29)) == []


def test_dca_by_hand():
    result = simulate(DCA, market(), PLAN, costs=TEN_BPS)

    # 99 shares (9,909.90 + fee 20), then 100 and 100 shares with the leftovers.
    assert [trade.shares for trade in result.trades] == [99, 100, 100]
    assert result.fees == 60 and result.taxes == 0
    assert result.final_cash == pytest.approx(10.1)
    assert result.final_value == pytest.approx(299 * 100 + 10.1)
    assert result.total_contributed == 30_000


def test_dividends_are_paid_after_the_lag_on_units_held_at_the_ex_date():
    data = market(dividends={"0050": {date(2020, 2, 10): 1.0}})

    result = simulate(DCA, data, PLAN, costs=TEN_BPS, dividend_lag_days=25)

    assert result.dividends_received == pytest.approx(199.0)   # 99 + 100 units on 02-10
    assert result.final_cash == pytest.approx(10.1 + 199.0)     # paid 03-06, after the 03-05 buy


def test_a_session_without_a_trade_postpones_the_orders():
    closes = {"0050": {day: 100.0 for day in SESSIONS if day != date(2020, 2, 5)}}

    result = simulate(DCA, market(closes=closes), PLAN)

    assert [trade.day for trade in result.trades] == [date(2020, 1, 6), date(2020, 2, 6), date(2020, 3, 5)]


def test_splits_multiply_units():
    closes = {"0050": {day: (100.0 if day < date(2020, 2, 10) else 25.0) for day in SESSIONS}}
    data = market(closes=closes, ratios={"0050": {date(2020, 2, 10): 4.0}})

    result = simulate(DCA, data, PLAN, costs=TEN_BPS)

    march = result.trades[-1]
    assert march.price == 25.03 and march.shares == affordable_shares(10_040.1, 25.03, TEN_BPS)
    held = 199 * 4 + march.shares
    assert result.final_value == pytest.approx(held * 25.0 + result.final_cash)


def test_moving_average_sizing_keeps_a_reserve_when_prices_are_strong():
    rising = {"0050": {day: 100.0 + index for index, day in enumerate(SESSIONS)}}
    spec = StrategySpec(
        name="test ma", allocation=Allocation(weights={"0050": 1.0}),
        sizing=Sizing(type="moving_average", ma_sessions=5, weak_multiplier=2.0, strong_multiplier=0.5),
    )

    result = simulate(spec, market(closes=rising), PLAN)

    # January has only 4 sessions of history, so the multiplier is neutral.
    first, second = result.trades[0], result.trades[1]
    assert first.amount > 9_000
    assert second.amount < 5_000 + 200     # close above its 5-session average: half of new money
    assert result.final_cash > 9_000


def test_two_asset_allocation_uses_new_money_for_the_gaps():
    spec = BASELINES["rebalance_80_20"]
    result = simulate(spec, market(assets=("0050", "00679B")), PLAN, costs=TEN_BPS, end=date(2020, 1, 31))

    bought = {trade.asset: trade.shares for trade in result.trades}
    assert bought == {"0050": 79, "00679B": 19}
    assert result.final_cash == pytest.approx(10_000 - 79 * 100.10 - 20 - 19 * 100.10 - 20)


def test_same_inputs_give_the_same_hashes():
    first = simulate(DCA, market(), PLAN)
    second = simulate(DCA, market(), PLAN)
    doubled = simulate(DCA, market(), PLAN, costs=CostModel().scaled(2))

    assert first.output_hash == second.output_hash and first.input_hash == second.input_hash
    assert doubled.input_hash != first.input_hash


def test_rolling_windows_against_itself_never_win():
    report = rolling_windows(DCA, DCA, market(), PLAN, CostModel(), date(2020, 1, 1), date(2020, 3, 31), 1)

    assert report["count"] == 2 and report["win_ratio"] == 0 and report["median_excess"] == 0


def test_compare_report_has_the_main_metric_and_a_stable_hash():
    report = compare_to_benchmark(BASELINES["ma_value"], market(), PLAN, window_months=(1,))
    again = compare_to_benchmark(BASELINES["ma_value"], market(), PLAN, window_months=(1,))

    assert report["benchmark"]["spec"] == "定期定額基準"
    assert "0y" in report["windows"] and report["report_hash"] == again["report_hash"]


def test_research_page_lists_the_latest_baseline_reports(tmp_path):
    import json

    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False)
    )).test_client()
    assert "尚無回測結果" in client.get("/research").get_data(as_text=True)

    report = compare_to_benchmark(BASELINES["ma_value"], market(), PLAN, window_months=(1,))
    report.update(generated_at="20261001-010000", period="full", kind="baseline")
    folder = tmp_path / "research" / "reports"
    folder.mkdir(parents=True)
    (folder / "full-report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (folder / "full-broken.json").write_text("{", encoding="utf-8")
    for index in range(60):  # a large candidate batch must not push the baselines out
        candidate = {**report, "period": "development", "kind": "candidate", "generated_at": "20261001-020000",
                     "strategy": {**report["strategy"], "spec_hash": f"{index:064d}", "spec": f"候選 {index}"}}
        (folder / f"development-{index}.json").write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")

    body = client.get("/research").get_data(as_text=True)
    assert "定期不定額（200 日均線）" in body and "定期定額對照" in body
    assert "全期間 XIRR" in client.get("/plan").get_data(as_text=True)


def test_metrics_by_hand():
    assert xirr([(date(2020, 1, 1), -1000), (date(2020, 12, 31), 1100)]) == pytest.approx(0.1, abs=1e-3)
    assert xirr([(date(2020, 1, 1), -1000)]) is None
    units = unit_values([100, 110, 210, 105], [100, 0, 100, 0])
    assert units == pytest.approx([1.0, 1.1, 1.1, 0.55])
    assert max_drawdown([1.0, 1.2, 0.9, 1.3])[0] == pytest.approx(-0.25)


def test_specs_only_accept_catalog_etfs():
    with pytest.raises(ValidationError):
        StrategySpec(name="stock picking", allocation=Allocation(weights={"2330": 1.0}))
    with pytest.raises(ValidationError):
        StrategySpec(name="bad weights", allocation=Allocation(weights={"0050": 0.5, "0056": 0.4}))
    with pytest.raises(ValidationError):
        StrategySpec.model_validate({"name": "x", "allocation": {"weights": {"0050": 1}}, "leverage": 2})
    assert all(spec.spec_hash for spec in BASELINES.values())
    assert json_schema()["title"] == "StrategySpec"
