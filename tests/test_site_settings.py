"""2026-11-22 (使用者 D-059：所有的策略，我都不知道你的參數到底是什麼；距三十號，我又不知道要多久，沒有一個可以上線的): the pages say
what a strategy's settings are under its row, and how long until the first T0."""
import json
from datetime import date


def _app(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    return create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'settings.db'}", scheduler_in_web=False)))


def test_a_rules_settings_line_is_shown_under_its_row_from_the_view_the_warm_up_keeps(tmp_path):
    from flask import render_template_string

    from quant_platform.dashboard import warm

    app = _app(tmp_path)
    warm.write(tmp_path / "research", "settings", {"a" * 64: "買：動能 分數前 20 名｜賣：跌出前 60 名就賣｜停損：沒有設停損"})
    template = '{% from "v2/_settings.html" import settings_note %}[{{ settings_note(h, 7) }}]'
    with app.test_request_context():
        shown = render_template_string(template, h="a" * 64)
        unknown = render_template_string(template, h="b" * 64)           # a rule without settings (an old design): nothing
        nothing = render_template_string(template, h=None)               # an account that is no rule: nothing
    assert 'class="row-note row-note--keep"' in shown and 'colspan="7"' in shown and "停損：沒有設停損" in shown
    assert unknown == "[]" and nothing == "[]"


def test_the_settings_view_has_a_line_for_every_daily_rule_that_has_a_report(tmp_path, monkeypatch):
    from quant_platform.dashboard import v2
    from quant_platform.research.daily import DailyRule

    research = tmp_path / "research"
    (research / "reports").mkdir(parents=True)
    spec = DailyRule(name="x", factors={"momentum_3": 1.0, "high_52w": 3.0}, top=20, min_hold=0, stop_loss=0.15).canonical()
    (research / "reports" / "settings-test-r1.json").write_text(json.dumps({"spec": spec}), encoding="utf-8")
    monkeypatch.setattr(v2, "pool_view", lambda *args, **kwargs: {"daily": [
        {"spec_hash": "h1", "report_file": "settings-test-r1.json"}, {"spec_hash": "h2", "report_file": None},
        {"spec_hash": "h3", "report_file": "settings-test-missing.json"}]})
    found = v2._settings_view(research)
    assert list(found) == ["h1"]                                       # no report, no line; nothing made up
    assert found["h1"].startswith("買：") and "停損：從買進後的最高價跌超過 15%" in found["h1"] and "賣：" in found["h1"]


def test_the_overview_and_the_home_page_say_how_long_until_the_first_t0(tmp_path):
    from quant_platform.dashboard import warm
    from quant_platform.research.countdown import countdown

    wait = countdown(date(2026, 11, 22), date(2026, 11, 30), date(2026, 11, 20), 54, 60, lambda day: day.weekday() < 5,
                     not_behind_both=347, judged=324)
    app = _app(tmp_path)
    client = app.test_client()
    assert 'id="t0-countdown"' not in client.get("/research").get_data(as_text=True)        # nothing kept yet: the block is left out
    warm.write(tmp_path / "research", "start", {"countdown": wait, "steps": [], "today": [], "red": [], "due": "2026-11-20",
                                                "at": "2026-11-22T17:50", "first_t0": "2026-11-30"})
    for path in ("/research", "/"):
        body = client.get(path).get_data(as_text=True)
        assert 'id="t0-countdown"' in body, path
        assert "還有 6 個交易日（8 天）" in body and "11/30（一）" in body and "11/23（一）" in body, path
        assert "54<small>／60 個交易日" in body and "347" in body and "以前不會有 T0" in body, path
        assert "會被判的有 <b>324</b> 個" in body and 'href="/research/t0-list"' in body, path       # both counts, related, not two answers
