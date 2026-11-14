"""2026-11-14 (使用者：先做一個只能看的頁面「照這個規則，今天你的帳戶要買賣什麼」包括沒買到時隔天怎麼調整; D-045)."""
from quant_platform.research import my_orders


def _entry(holdings, cash=0, orders=()):
    return {"rule_hash": "r", "name": "rule", "orders": list(orders), "cash": cash,
            "holdings": [{"code": code, "name": code + "名", "shares": shares, "value": shares * price}
                         for code, shares, price in holdings]}


def test_cash_alone_buys_the_rule_in_proportion_and_keeps_a_reserve():
    entry = _entry([("1111", 100, 100.0), ("2222", 50, 200.0)])          # the rule's account: 20,000
    result = my_orders.plan(entry, 40_000, {})
    assert result["ok"] and result["total"] == 40_000 and not result["sells"]
    bought = {row["code"]: row["shares"] for row in result["buys"]}
    assert bought == {"1111": 197, "2222": 98}                            # twice the rule's, less the 1.2% reserve
    assert all(row["why"] == "規則持有、你還沒有" for row in result["buys"])
    assert result["cash_after"] >= 0
    part = result["buys"][0]["parts"][0]
    assert part["session"].startswith("盤後零股") and part["limit"] == 101.0


def test_what_the_rule_no_longer_holds_is_sold_and_round_lots_go_to_the_fixed_price_session():
    entry = _entry([("1111", 2000, 50.0)], orders=[{"code": "3333", "name": "x", "side": "SELL"}])
    result = my_orders.plan(entry, 0, {"1111": 1000, "3333": 1500}, {"1111": 50.0, "3333": 20.0})
    sold = result["sells"][0]
    assert (sold["code"], sold["shares"], sold["why"]) == ("3333", 1500, "規則今天賣掉了")
    assert [part["shares"] for part in sold["parts"]] == [1000, 500]
    assert result["buys"][0]["code"] == "1111" and result["buys"][0]["why"] == "你的比規則少（之前沒買到，或你的錢變多）"


def test_an_order_that_did_not_fill_yesterday_is_only_today_s_difference():
    """Nothing is carried over: the shortfall shows again while the rule still holds the stock, and is gone when it
    does not (使用者 2026-11-14：當天策略就當天做，隔天要參照前一天狀況提出策略)."""
    still = my_orders.plan(_entry([("1111", 100, 100.0), ("2222", 100, 100.0)]), 10_000, {"1111": 100}, {})
    assert [(row["code"], row["shares"]) for row in still["buys"]] == [("2222", 98)]
    gone = my_orders.plan(_entry([("1111", 100, 100.0)], cash=10_000), 10_000, {"1111": 100}, {})
    assert not gone["buys"] and not gone["sells"]


def test_a_stock_closed_at_its_limit_is_not_an_order():
    entry = _entry([("1111", 100, 110.0), ("2222", 100, 90.0)])
    result = my_orders.plan(entry, 30_000, {"3333": 100}, {"1111": 110.0, "2222": 90.0, "3333": 90.0},
                            {"1111": 100.0, "2222": 89.0, "3333": 100.0})
    waiting = {row["code"]: row["why"] for row in result["waiting"]}
    assert waiting == {"1111": "今天收在漲停，盤後買不到", "3333": "今天收在跌停，盤後賣不掉"}
    assert [row["code"] for row in result["buys"]] == ["2222"] and not result["sells"]


def test_a_small_difference_is_left_alone():
    entry = _entry([("1111", 1000, 100.0)])
    result = my_orders.plan(entry, 1_600, {"1111": 990}, {"1111": 100.0})
    assert not result["buys"] and [row["code"] for row in result["left"]] == ["1111"]


def test_buys_are_cut_when_the_sales_do_not_pay_for_them():
    entry = _entry([("1111", 100, 100.0)])
    result = my_orders.plan(entry, 0, {"2222": 100}, {"1111": 100.0, "2222": 100.0})
    assert result["sells"][0]["shares"] == 100 and result["buys"][0].get("cut") and result["cash_after"] >= 0


def test_a_holding_without_a_close_today_is_named_and_left_out():
    result = my_orders.plan(_entry([("1111", 100, 100.0)]), 10_000, {"9999": 5})
    assert result["unknown"] == ["9999"] and result["total"] == 10_000


def test_nothing_to_follow_or_nothing_to_invest():
    assert not my_orders.plan(_entry([]), 10_000, {})["ok"]
    assert not my_orders.plan(_entry([("1111", 1, 1.0)]), 0, {})["ok"]


def test_the_rules_offered_are_ordered_by_the_combined_score():
    entries = [{"rule_hash": "a"}, {"rule_hash": "b"}, {"rule_hash": "c", "error": "x"}]
    rows = [{"spec_hash": "a", "name": "A", "tier": "T0 候選", "combined": 0.2},
            {"spec_hash": "b", "name": "B", "tier": "T1", "combined": 0.9},
            {"spec_hash": "c", "name": "C", "tier": "T1", "combined": 1.0}, {"spec_hash": "d", "name": "D", "tier": "T1"}]
    assert [row["rule_hash"] for row in my_orders.choices(entries, rows)] == ["b", "a"]
