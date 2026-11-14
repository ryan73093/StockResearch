"""2026-11-15: what the UI review of 11/13–11/14 (docs/ui_review_2026-11-13.md) found and the fixes must keep.

Each test is named after the finding: a number printed on a page opens (E-1), a rule's short name (U4), the month's
"usable" by both moneys (T-2), the site's own not-found page (E-14), the notes' marks rendered (R-7, E-10), the home
page's entry (U1), the pages used after going live say so (U2).
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app

TAIPEI = ZoneInfo("Asia/Taipei")


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'ui.db'}", scheduler_in_web=False)
    return create_app(build_container(settings)).test_client()


def _trials(tmp_path, rows):
    folder = tmp_path / "research"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "trials.jsonl").write_text(
        "".join(json.dumps({"trial_id": trial, "spec_hash": spec, "kind": "candidate"}) + "\n" for trial, spec in rows),
        encoding="utf-8")
    return folder


def test_any_run_of_a_rule_opens_the_rule_and_names_its_fixed_number(tmp_path):
    """E-1: the dashboard printed a later run's trial id ("#34653"); typed in the number box it said "no such strategy"."""
    from quant_platform.research import numbers

    first, later = "a" * 64, "b" * 64
    research = _trials(tmp_path, [(7958, first), (8000, later), (34653, first)])
    assert numbers.spec_of(research, 7958) == first and numbers.spec_of(research, 34653) is None
    assert numbers.spec_of_run(research, 34653) == first and numbers.label_of_run(research, 34653) == "#7958"
    assert numbers.spec_of_run(research, 99999) is None and numbers.label_of_run(research, None) == ""


def test_the_number_box_follows_a_later_runs_number_to_its_rule(client, tmp_path):
    _trials(tmp_path, [(7958, "a" * 64), (34653, "a" * 64)])
    answer = client.get("/research/no/34653")
    assert answer.status_code == 302 and answer.headers["Location"].endswith("/research/rules/" + "a" * 64)
    assert client.get("/research/no/12345").headers["Location"].endswith("/research/pool")      # really unknown


@pytest.mark.parametrize(("name", "wanted", "unwanted"), [
    ("深度學習前一成・不買技術機器人最不看好的五分之一：前 10 名、同產業最多 3 成、每週、前 30 名內續抱、看成本才換股、只管買進｜持有：持有 20 天",
     ("深度學習前一成", "前 10 名", "抱 20 天"), ("同產業", "看成本")),
    ("掃描：近 52 週高＋外資持股＋融資增加＋營益率增：前 20 名、同產業最多 3 成、每週決策＋看成本才換股｜持有：固定持有 20 天",
     ("近 52 週高＋外資持股 等 4 個", "前 20 名", "抱 20 天"), ("掃描", "同產業")),
    ("組合搜尋：深度學習第二版・前一成#6501 31%＋深度學習第二版＋模型 G#6880 32%＋8 個強訊號區塊平均・…・加權指數跌破 200 日均線就減碼一半",
     ("組合 #6501＋#6880", "跌市減碼一半"), ("31%",)),
    ("深度學習第二版・前一成：市值前 100 大挑 20 名、同產業最多 3 成、每週決策、前 60 名內續抱、看成本才換股｜持有：月線上續抱",
     ("前 20 名", "月線上續抱"), ("前 60 名",)),
    ("每天 月營收年增率：前 20 名", ("月營收年增率：前 20 名",), ("每天",)),
])
def test_a_rules_short_name_keeps_what_tells_it_apart(name, wanted, unwanted):
    """U4 (D-046 item 9): 60–120 characters in a table cell became seven lines; the short name is a third of it."""
    from quant_platform.research.short_name import short

    found = short(name)
    assert len(found) <= 45 and all(part in found for part in wanted) and not any(part in found for part in unwanted)


def test_the_months_usable_is_not_behind_0050_by_both_moneys():
    """T-2 (D-046 item 9): the home page judged "beats 0050" on book value, the overview on the money counted."""
    from quant_platform.research import rolling

    rows = [{"rule_hash": "a", "usable": True, "value": 330.0, "benchmark_value": 320.0, "start": "2026-10-01"},
            {"rule_hash": "b", "usable": True, "value": 330.0, "benchmark_value": 320.0, "start": "2026-10-01"},
            {"rule_hash": "c", "usable": False, "start": "2026-10-01"}]
    accounts = [{"rule_hash": "a", "since": "2026-10-01", "date": "2026-11-13", "kept": {"ahead": 5.0}, "sell_all": {"ahead": 9.0}},
                {"rule_hash": "b", "since": "2026-10-01", "date": "2026-11-13", "kept": {"ahead": -5.0}, "sell_all": {"ahead": 9.0}},
                {"rule_hash": "c", "since": "2026-09-01", "kept": {"ahead": 1.0}, "sell_all": {"ahead": 1.0}}]   # last month's
    found = {row["rule_hash"]: row for row in rolling.with_money(rows, accounts, "2026-09-30")}
    assert found["a"]["usable"] is True and found["a"]["ahead_kept"] == 5 and found["a"]["ahead_sold"] == 9
    assert found["b"]["usable"] is False and found["b"]["book_usable"] is True      # ahead on paper only: not usable
    assert "ahead_kept" not in found["c"] and found["c"]["usable"] is False        # no account of this month: as it was
    view = rolling.summary({"cutoff": "2026-09-30", "graded": [], "holdout": list(found.values())})
    assert view["money"] == {"rows": 2, "kept": 1, "sold": 2, "both": 1} and len(view["usable"]) == 1


def test_a_wrong_address_gets_the_sites_own_page(client):
    """E-14: a mistyped address answered "404 Not Found" in English with no navigation."""
    page = client.get("/research/rules/not-a-rule", headers={"Accept": "text/html"})
    body = page.get_data(as_text=True)
    assert page.status_code == 404 and "找不到這一頁" in body and "輸入編號" in body and 'href="/research"' in body
    assert "找不到這一頁" in client.get("/no-such-page", headers={"Accept": "text/html"}).get_data(as_text=True)
    assert client.get("/research/pool.jsonx", headers={"Accept": "application/json"}).status_code == 404


def test_the_notes_marks_are_rendered_not_shown():
    """R-7, E-10: the optimization log showed ** and backticks, the knowledge base's gist a table's pipes."""
    from quant_platform.dashboard.v2 import inline_md
    from quant_platform.research.knowledge import gist

    assert str(inline_md("過：**#12241** 用 `bear12` <b>")) == "過：<b>#12241</b> 用 <code>bear12</code> &lt;b&gt;"
    found = gist({"text": "結果：`DailyRule.large_caps` 的規則\n| 規則 | 期末 |\n|--|--|\n| 甲 | 100 |"})
    assert "`" not in found and "|" not in found and "DailyRule.large_caps" in found and "甲、100" in found


def test_the_optimization_log_is_newest_first_and_later_notes_are_listed(tmp_path):
    """R-7: 10-30 came after 10-29; the newest row was 10/30 with nothing said about November."""
    from quant_platform.dashboard import v2

    path = tmp_path / "results.md"
    path.write_text("\n".join([
        f"### {v2.OPTIMIZATIONS_HEADING}", "", "| 日期 | 家族 | 做了什麼 | 結果 | 判定 |", "|---|---|---|---|---|",
        "| 2026-10-29 | 組合 | 甲 | 一 | 沒過 |", "| 2026-10-30 | 組合 | 乙 | 二 | 過 |", "",
        "## 2026-11-13 量尺：回測排名和前向", "內容", "### 付費資料沒更新（2026-11-02，重現性）", "內容",
        "## 2026-11-12 從現行結論移出的原文", "## 2026-10-20 舊的"]), encoding="utf-8")
    assert [row["date"] for row in v2.research_optimizations(path)] == ["2026-10-30", "2026-10-29"]
    assert v2.later_notes(path, "2026-10-30") == [{"date": "2026-11-13", "title": "量尺：回測排名和前向"},
                                                   {"date": "2026-11-02", "title": "付費資料沒更新（重現性）"}]


def test_the_home_page_starts_with_what_the_platform_is(client):
    """U1 (D-046 item 9): the first thing on the home page said nothing about what the platform does."""
    body = client.get("/").get_data(as_text=True)
    assert 'data-tab="從這裡開始"' in body and "全部買 0050" in body and "不會下單" in body
    assert body.index('id="start"') < body.index("資料狀態")               # the entry is the first tab


def test_the_pages_used_after_going_live_say_so(client):
    """U2 (D-046 item 9): 今日、持倉、計畫 were empty or old with no word on why."""
    for path in ("/holdings", "/plan"):
        assert "上線以後才會用到" in client.get(path).get_data(as_text=True)
    holdings = client.get("/holdings").get_data(as_text=True)
    assert 'id="paper"' not in holdings and 'id="paper"' in client.get("/holdings?paper=1").get_data(as_text=True)   # H-1
    assert "舊版 AI 模擬帳戶" in client.get("/system").get_data(as_text=True)


def test_the_orders_page_says_whose_orders_and_the_state_of_the_day(client, tmp_path):
    """O-1, O-3, E-7: on a Saturday the page said "not computed yet" and "order before 14:30"."""
    from quant_platform.dashboard import v2

    class Closed:
        def is_trading_day(self, day):
            return False

        def next_trading_day(self, day):
            return datetime(2026, 11, 16).date()

    folder = tmp_path / "research"
    folder.mkdir(parents=True, exist_ok=True)
    found = v2.orders_view(folder, datetime(2026, 11, 14, 10, 0, tzinfo=TAIPEI), Closed(), {})
    assert found["state"]["badge"] == "今天休市" and "11/16" in found["state"]["text"] and found["report"] is None

    class Open(Closed):
        def is_trading_day(self, day):
            return True

    late = v2.orders_view(folder, datetime(2026, 11, 13, 19, 52, tzinfo=TAIPEI), Open(), {})
    assert late["state"]["badge"] == "今天的還沒算出來"
    early = v2.orders_view(folder, datetime(2026, 11, 13, 9, 0, tzinfo=TAIPEI), Open(), {})
    assert early["state"]["badge"] == "13:50 起計算"


def test_a_failed_check_says_what_it_affects_and_when_it_runs_next(tmp_path):
    """R-11: three ✗ with no word on what they affect or when they are looked at again."""
    from quant_platform.research.checks import correctness

    rows = {row["key"]: row for row in correctness(tmp_path, datetime(2026, 11, 14, 12, 0, tzinfo=TAIPEI))}
    assert "偏高" in rows["leak"]["impact"] and rows["leak"]["next"].startswith("11/15")          # the next Sunday
    assert rows["decisions"]["next"].startswith("11/16") and "13:50" in rows["forward"]["remedy"]


def test_a_factor_without_a_unit_never_shows_an_exponent():
    """E-16: the stock page showed "-4.9e-05"."""
    from quant_platform.research.snapshot import display

    assert display("some_new_factor", -4.9e-05) == "-0.000049（原始分數）"
    assert display("some_new_factor", 12.3456) == "12.35（原始分數）" and "e" not in display("some_new_factor", 1.2e-7)


def test_the_same_question_must_show_the_same_number_on_every_page(monkeypatch):
    """U3: the home page said 339, the overview 1%; one page had 978, 983 and 990 萬 for the same 0050."""
    import io
    import urllib.request

    from quant_platform.research import invariants

    bodies = {"/": '<span data-figure="chain-both" data-value="30">49／60</span><b data-figure="month-both" data-value="21">21</b>',
              "/research": '<div data-figure="chain-both" data-value="30">11/30</div><div data-figure="bench-0050" data-value="978">978 萬</div>',
              "/research/roles": '<div data-figure="month-both" data-value="21">21</div>'}
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: io.BytesIO(bodies[url.split("5000", 1)[1]].encode("utf-8")))
    assert invariants.same_figures("http://127.0.0.1:5000") == []
    bodies["/research"] = bodies["/research"].replace('data-value="30"', 'data-value="31"')
    found = invariants.same_figures("http://127.0.0.1:5000")
    assert len(found) == 1 and "串接前向兩種算法都不輸 0050 的規則數" in found[0] and "首頁 寫 30" in found[0] and "研究總覽 寫 31" in found[0]
    bodies["/research"] = "nothing marked"
    assert invariants.same_figures("http://127.0.0.1:5000") == ["頁面上找不到：同樣的錢買 0050 的期末（2015-06 起）"]
