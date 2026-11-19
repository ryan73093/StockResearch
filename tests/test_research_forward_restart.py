"""2026-11-08: the accounts opened before the models' refit, recorded again; the GPU given back after a worker job."""
import json
import sys
from types import SimpleNamespace

from quant_platform.research import gpu
from quant_platform.research.stock_forward import StockForwardTracker, unexplained


def test_only_differences_without_a_13_50_reason_are_unexplained():
    rows = [{"rule_hash": "a", "differed": 3, "first": {"day": "2026-10-02"}},
            {"rule_hash": "b", "differed": 1, "first": {"day": "2026-11-03", "table": {"4958": {}}}},
            {"rule_hash": "c", "differed": 0, "first": None}]
    assert unexplained(rows) == {"a"}


def test_an_account_restarted_leaves_the_log_and_goes_to_the_archive_line_by_line(tmp_path):
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    rows = [{"rule_hash": "a", "date": "2026-10-01", "text": "中文"}, {"rule_hash": "b", "date": "2026-10-01", "text": "留下"},
            {"rule_hash": "a", "date": "2026-10-02", "text": "中文"}]
    (folder / "log.jsonl").write_text(chr(10).join(json.dumps(row, ensure_ascii=False) for row in rows) + chr(10), encoding="utf-8")
    tracker = StockForwardTracker(tmp_path)
    assert tracker._archive_records({"a"}, "2026-11-08T23:00:00") == 2
    assert [row["rule_hash"] for row in tracker.records()] == ["b"]
    assert [row["date"] for row in tracker.archived()] == ["2026-10-01", "2026-10-02"]
    assert not (folder / "log.partial").exists()


def test_the_gpu_is_given_back_only_when_a_job_loaded_pytorch(monkeypatch):
    calls = []
    fake = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: calls.append("emptied")))
    monkeypatch.setitem(sys.modules, "torch", fake)
    gpu.release()
    assert calls == ["emptied"]
    calls.clear()
    monkeypatch.delitem(sys.modules, "torch")
    gpu.release()
    assert calls == []


def test_a_forward_only_experiment_has_a_fixed_number(tmp_path):
    """2026-11-08: 8 forward accounts (the news veto, the LLM trader, their controls) had no number."""
    from quant_platform.research import numbers
    from quant_platform.research.stock_forward import forward_only

    rules = [rule for rule, _reason in forward_only()]
    assert len(rules) >= 8 and len({rule.rule_hash for rule in rules}) == len(rules)
    assert numbers.label(tmp_path, rules[0].rule_hash) == "#E1" and numbers.label(tmp_path, rules[3].rule_hash) == "#E4"
    assert numbers.experiment_spec("#e4") == rules[3].rule_hash and numbers.experiment_spec("E99") is None
    assert numbers.label(tmp_path, "no-such-rule") == ""


def test_the_overview_summarises_the_longest_run_on_the_lists():
    """2026-11-08: the rules with the most sessions on the lists: how many ahead of 0050, the middle one, the best first."""
    from quant_platform.research.dashboard import chain_summary

    part = {"from": "2026-09-01", "to": "2026-11-06"}
    chained = {"a": {"sessions": 44, "excess": 0.10, "return": 0.02, "benchmark_return": -0.08, "parts": [part]},
               "b": {"sessions": 44, "excess": -0.02, "return": -0.10, "benchmark_return": -0.08, "parts": [part]},
               "c": {"sessions": 44, "excess": -0.05, "return": -0.13, "benchmark_return": -0.08, "parts": [part]},
               "d": {"sessions": 5, "excess": 0.30, "return": 0.20, "benchmark_return": -0.10, "parts": [part]}}
    found = chain_summary(chained, {"a": "甲"}, top=2)
    assert (found["rules"], found["sessions"], found["ahead"], found["median"]) == (3, 44, 1, -0.02)
    assert (found["start"], found["end"], found["benchmark"]) == ("2026-09-01", "2026-11-06", -0.08)
    assert [row["rule_hash"] for row in found["top"]] == ["a", "b"] and found["top"][0]["name"] == "甲"
    # 2026-11-12: the other money (both sold that day) beside it; a record made before it reads as the counted one
    assert found["ahead_sold"] == found["ahead"] and found["median_sold"] == found["median"]
    both = chain_summary({key: {**row, "sold_excess": 0.5} for key, row in chained.items()}, {"a": "甲"}, top=2)
    assert both["ahead_sold"] == both["rules"] and both["median_sold"] == 0.5
    assert chain_summary({}, {}) is None


def test_accounts_that_double_another_are_counted_once():
    """2026-11-13 (problem register P3): the same start, value, trades, costs and realized gains is one result; the
    holdings today are compared by their codes."""
    from quant_platform.research.dashboard import distinct_accounts

    def row(value, codes, trades=40, since="2026-10-01"):
        return {"since": since, "value": value, "trades_total": trades, "costs": 812.5, "realized": -1200.0,
                "holdings": [{"code": code} for code in codes]}

    rows = [row(301_000.0, ["2330", "2317"]), row(301_000.0, ["2317", "2330"]), row(301_000.0, ["2330", "2317"], trades=41),
            row(298_500.0, ["2330", "2317"]), row(301_000.0, ["2330", "2317"], since="2026-09-01")]
    assert distinct_accounts(rows) == {"accounts": 5, "results": 4, "holdings": 2}
    assert distinct_accounts([]) == {"accounts": 0, "results": 0, "holdings": 0}


def test_the_overview_says_how_each_kind_of_rule_is_doing_now():
    """2026-11-18 (使用者：RL、技術分析機器人、多因子、深度學習、機器學習每一個現在的狀況是怎麼樣，你也沒有寫；我現在根本
    不知道到底可不可以上線): per kind — tested, passing, on every list, not behind 0050 by both moneys (what T0 asks),
    the middle rule, the beta, the best; the kinds with the most rules holding first."""
    from quant_platform.research.categories import FAMILY_HOW, FAMILY_ORDER
    from quant_platform.research.dashboard import NOW_SKIP, status_now

    part = {"from": "2026-09-01", "to": "2026-11-18"}

    def record(counted, sold, beta, sessions=52):
        return {"sessions": sessions, "excess": counted, "sold_excess": sold, "beta": beta, "benchmark_return": -0.14,
                "parts": [part]}
    chained = {"m1": record(0.09, 0.10, 0.7), "m2": record(-0.03, 0.02, 0.8), "m3": record(0.01, 0.0, 0.9),
               "d1": record(-0.16, -0.12, 1.4), "d2": record(0.02, -0.01, 1.5),
               "new": record(0.30, 0.30, 0.5, sessions=31),            # joined a later list: not in the longest run
               "gone": {"sessions": 52, "excess": 0.5, "parts": []}}
    rows = [{"spec_hash": key, "name": f"規則 {key}", "category": {"family": family}} for key, family in
            (("m1", "多因子"), ("m2", "多因子"), ("m3", "多因子"), ("d1", "深度學習"), ("d2", "深度學習"), ("new", "機器學習"))]
    families = [{"family": "多因子", "rules": 10, "short_rules": 2, "passing": 4},
                {"family": "深度學習", "rules": 20, "short_rules": 0, "passing": 9},
                {"family": "機器學習", "rules": 7, "short_rules": 0, "passing": 1},
                {"family": "反轉", "rules": 5, "short_rules": 0, "passing": 0},
                {"family": "其他", "rules": 1, "short_rules": 0, "passing": 0}]
    log = [{"date": "2026-11-18", "family": "深度學習", "what": "模型 Q 的同一個標籤", "result": "沒過", "verdict": "失敗"},
           {"date": "2026-11-18", "family": "機器學習", "what": "模型 Q", "result": "過", "verdict": "部分"}]
    found = status_now(chained, rows, families, log)
    assert (found["sessions"], found["needed"], found["rules"], found["holding"], found["benchmark"]) == (52, 60, 5, 2, -0.14)
    assert [row["family"] for row in found["families"]] == ["多因子", "深度學習", "機器學習", "反轉"]   # 其他 is left out
    many, deep, tree, none = found["families"]
    assert (many["tested"], many["passing"], many["listed"], many["holding"]) == (12, 4, 3, 2)
    assert (many["median"], many["median_sold"], many["beta"]) == (0.01, 0.02, 0.8)
    assert many["best"]["rule_hash"] == "m1" and many["best"]["name"] == "規則 m1" and many["latest"] is None
    assert (deep["listed"], deep["holding"], deep["best"]["rule_hash"]) == (2, 0, "d2")    # d2: one money only
    assert deep["latest"]["what"] == "模型 Q 的同一個標籤"            # by the log's own family, not by the word 模型
    assert (tree["listed"], tree["holding"], tree["median"], tree["best"]) == (0, 0, None, None)
    assert tree["latest"]["verdict"] == "部分" and (none["passing"], none["listed"]) == (0, 0)
    assert all(row["how"] for row in found["families"]) and found["top"] == 3 and len(found["latest"]) == 2
    assert set(FAMILY_HOW) == set(FAMILY_ORDER) - set(NOW_SKIP)       # every kind shown says how it is made
    assert "一億多種" in FAMILY_HOW["多因子"] and "掃描" in FAMILY_HOW["多因子"] and "組合搜尋" in FAMILY_HOW["組合帳戶"]
    assert status_now({}, rows, [], log) is None


def test_the_t0_list_names_the_rules_holding_up_with_what_to_read_beside_them(tmp_path):
    """2026-11-19 (使用者：我現在根本不知道到底可不可以上線): the rules T0 would be judged on today — not behind 0050 by
    both moneys over the longest run — with the beta, the reconciliation, the kind and the day first registered."""
    import json

    from quant_platform.research.numbers import first_dates
    from quant_platform.research.progress import t0_list

    part = {"from": "2026-09-01", "to": "2026-11-18"}

    def record(counted, sold, beta, sessions=52):
        return {"sessions": sessions, "excess": counted, "sold_excess": sold, "beta": beta, "return": counted - 0.14,
                "benchmark_return": -0.14, "parts": [part]}
    chained = {"a": record(0.09, 0.10, 0.7), "b": record(0.02, 0.01, 1.1), "c": record(-0.03, 0.02, 0.8),
               "late": record(0.3, 0.3, 0.5, sessions=31), "t2": record(0.2, 0.2, 0.6), "half": record(0.2, 0.2, 0.6)}
    pool = [{"spec_hash": key, "name": f"規則 {key}", "tier": tier, "category": {"family": family}, "uses_0050": uses}
            for key, tier, family, uses in (("a", "T0 候選", "多因子", False), ("b", "T0 候選（高風險）", "機器學習", False),
                                            ("c", "T0 候選", "多因子", False), ("late", "T0 候選", "機器學習", False),
                                            ("t2", "T2", "多因子", False), ("half", "T0 候選", "多因子", True))]
    found = t0_list(chained, pool, {"b": {"problems": ["11-18 接不上"]}}, {"a": "2026-10-27", "b": "2026-08-20"})
    assert (found["sessions"], found["needed"], found["rules"], found["benchmark"]) == (52, 60, 3, -0.14)
    assert [row["spec_hash"] for row in found["rows"]] == ["a", "b"]         # c: one money only; the worse money first
    first, second = found["rows"]
    assert (first["family"], first["beta"], first["reconciled"], first["first"], first["after_start"]) == ("多因子", 0.7, True, "2026-10-27", True)
    assert (second["reconciled"], second["problems"], second["after_start"]) == (False, 1, False)
    assert (found["beta"], found["low_beta"], found["unreconciled"], found["after_start"]) == (1.1, 1, 1, 1)
    assert found["kinds"] == [("多因子", 1), ("機器學習", 1)]
    lines = [{"trial_id": 5, "kind": "candidate", "spec_hash": "a" * 16, "created_at": "2026-10-27T10:00:00+08:00"},
             {"trial_id": 9, "kind": "candidate", "spec_hash": "a" * 16, "created_at": "2026-11-02T10:00:00+08:00"}]
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    assert first_dates(tmp_path) == {"a" * 16: "2026-10-27"}                 # the first registration, not a later run
