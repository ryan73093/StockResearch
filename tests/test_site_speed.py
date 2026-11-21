"""2026-11-21 (使用者：整個 UI 現在非常非常的慢，點什麼都很慢): what made the clicks slow, kept from coming back.

Measured by clicking every link and window of the site (487 of them): the dashboard's big windows took 4–6 s (17,000
fixed-number lookups that each stat'ed the registry), a rule's forward page 0.9 s (66 MB decisions parsed per click),
every research page 0.3 s (2,362 job files listed with a Path object each), the pool's other bases 40 s (the whole pool
worked out again).
"""

import json
import os

from quant_platform.research import numbers


def test_a_fixed_number_costs_one_stat_not_a_resolve_and_a_copy(tmp_path, monkeypatch):
    """The distribution window named 17,000 rules; each lookup resolved the path (three stats) and copied the 35,000-entry dict."""
    research = tmp_path / "research"
    research.mkdir()
    (research / "trials.jsonl").write_text(
        "".join(json.dumps({"trial_id": trial, "spec_hash": f"{trial:064x}", "kind": "candidate"}) + "\n" for trial in range(1, 50)),
        encoding="utf-8")
    numbers.label(research, f"{1:064x}")                      # loaded
    stats = []
    real = os.stat

    def counting(path, *args, **kwargs):
        stats.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(numbers.os, "stat", counting)
    for trial in range(1, 40):
        assert numbers.label(research, f"{trial:064x}") == f"#{trial}"
    assert len(stats) == 39                                   # one stat per lookup


def test_a_changed_registry_is_read_again(tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    path = research / "trials.jsonl"
    path.write_text(json.dumps({"trial_id": 5, "spec_hash": "a" * 64}) + "\n", encoding="utf-8")
    assert numbers.number_of(research, "a" * 64) == 5 and numbers.number_of(research, "b" * 64) is None
    path.write_text(json.dumps({"trial_id": 5, "spec_hash": "a" * 64}) + "\n" + json.dumps({"trial_id": 9, "spec_hash": "b" * 64}) + "\n",
                    encoding="utf-8")
    assert numbers.number_of(research, "b" * 64) == 9 and numbers.spec_of(research, 9) == "b" * 64
    assert numbers.number_of(tmp_path / "nowhere", "a" * 64) is None        # no registry: nothing, not another folder's numbers


def test_the_distribution_view_reads_the_fixed_numbers_once(tmp_path, monkeypatch):
    from quant_platform.research import distribution

    calls = []
    real = numbers.numbers
    monkeypatch.setattr(numbers, "numbers", lambda research: calls.append(1) or real(research))
    monkeypatch.setattr(numbers, "label", lambda *args: (_ for _ in ()).throw(AssertionError("asked per rule")))
    (tmp_path / "trials.jsonl").write_text(json.dumps({"trial_id": 3, "spec_hash": "c" * 64}) + "\n", encoding="utf-8")
    rows = [{"spec_hash": "c" * 64, "trial_id": 3, "name": "甲", "tier": "T0 候選", "category": {"family": "多因子"},
             "final_value": 5_000_000.0, "benchmark_final_value": 3_000_000.0, "report_file": None, "contributed": 1_000_000.0}]
    monkeypatch.setattr(distribution, "_contributed", lambda *args, **kwargs: 1_000_000.0)
    distribution.view(tmp_path, rows)
    assert len(calls) <= 2                                    # once per track at most, never once per rule


def test_the_jobs_list_is_newest_first_without_a_path_object_per_file(tmp_path):
    from quant_platform.research.jobs import JobLog

    folder = tmp_path / "jobs"
    folder.mkdir()
    for minute in range(1, 41):
        (folder / f"20261101-12{minute:02d}00-1.json").write_text(json.dumps({
            "id": f"j{minute}", "name": f"工作{minute}", "command": "x", "pid": 0, "status": "done",
            "started_at": f"2026-11-01T12:{minute:02d}:00+08:00", "done": 1, "total": 1, "current": "", "summary": "",
            "finished_at": None}), encoding="utf-8")
    (folder / "notes.txt").write_text("not a job", encoding="utf-8")
    rows = JobLog(tmp_path).jobs(limit=30)
    assert len(rows) == 30 and rows[0]["name"] == "工作40" and rows[-1]["name"] == "工作11"


def test_a_read_only_decision_store_parses_the_file_once_while_it_does_not_change(tmp_path):
    from quant_platform.research.decisions import DecisionStore

    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    path = folder / "decisions.json"
    entry = {"through": "2026-11-13", "days": {"2026-11-12": ["2330"]}, "why": {"2026-11-12": {"2330": "新進前 20 名"}}}
    path.write_text(json.dumps({"a@2026-10-01": entry}), encoding="utf-8")
    first, second = DecisionStore(tmp_path, shared=True), DecisionStore(tmp_path, shared=True)
    assert first._data is second._data                        # one parse
    assert first.why("a@2026-10-01") == {"2026-11-12": {"2330": "新進前 20 名"}}
    path.write_text(json.dumps({"a@2026-10-01": {**entry, "through": "2026-11-14"}, "b@2026-10-01": entry}), encoding="utf-8")
    third = DecisionStore(tmp_path, shared=True)
    assert third._data is not first._data and "b@2026-10-01" in third._data         # the file changed: read again
    own = DecisionStore(tmp_path)                             # a store that may write never shares
    assert own._data is not third._data and own._data == third._data


def test_another_basis_of_the_pool_is_the_seed_view_with_the_old_designs_rows(tmp_path, monkeypatch):
    """Switching the basis worked the whole pool out again (the chained forward record over 190 MB): 40 s a click."""
    from quant_platform.research import pool

    def forbidden(*args, **kwargs):
        raise AssertionError("worked out again")

    monkeypatch.setattr(pool, "daily_rows", forbidden)
    monkeypatch.setattr(pool, "rule_rows", lambda research, basis: [])
    seed = {"daily": [{"spec_hash": "a"}], "forward_stocks": ["kept"], "rules": [{"old": 1}], "total": 1, "basis": "seed",
            "basis_label": "x", "counts": {"approved": 9}}
    other = pool.pool_view_on(tmp_path, "dca", seed)
    assert other["daily"] == seed["daily"] and other["forward_stocks"] == ["kept"]            # the new design's rows: the seed's
    assert other["rules"] == [] and other["total"] == 0 and other["basis"] == "dca" and other["counts"]["approved"] == 0
    assert seed["basis"] == "seed" and seed["total"] == 1                                       # the seed view is not changed


def test_a_stock_page_reads_the_kept_forward_view_not_the_whole_summary(tmp_path, monkeypatch):
    """The first stock page after each restart took 16 s: it worked out the forward accounts' whole summary (213,000
    record lines) in the page, while every other page reads the view the warm-up keeps."""
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard import app as app_module
    from quant_platform.dashboard import warm
    from quant_platform.research import stock_forward

    def forbidden(self):
        raise AssertionError("the whole summary was worked out in the page")

    monkeypatch.setattr(stock_forward.StockForwardTracker, "summary", forbidden)
    warm.write(tmp_path / "research", "forward", [])                      # what the warm-up keeps
    client = app_module.create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'stock.db'}", scheduler_in_web=False))).test_client()
    response = client.get("/stock/2330")
    assert response.status_code in (200, 404)                             # no data in this folder; not a 500 from the summary


def test_a_slow_click_is_written_to_the_web_log(tmp_path, caplog, monkeypatch):
    """A click the owner found slow left no trace and could not be found afterwards (11/20's 12.5 s overview was only
    seen by the 16:00 check): over ``SLOW_REQUEST_SECONDS`` it is logged with its path."""
    import logging
    import time

    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard import app as app_module

    app = app_module.create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'slow.db'}", scheduler_in_web=False)))
    app.add_url_rule("/_slow-test", "slow_test", lambda: (time.sleep(0.05), "ok")[1])
    client = app.test_client()
    monkeypatch.setattr(app_module, "SLOW_REQUEST_SECONDS", 0.01)
    with caplog.at_level(logging.WARNING, logger="quant_platform.web.requests"):
        assert client.get("/_slow-test?a=1").status_code == 200
    assert any("SLOW GET /_slow-test?a=1 -> 200 in" in record.getMessage() for record in caplog.records)
    caplog.clear()
    monkeypatch.setattr(app_module, "SLOW_REQUEST_SECONDS", 5.0)
    with caplog.at_level(logging.WARNING, logger="quant_platform.web.requests"):
        assert client.get("/_slow-test").status_code == 200
    assert not [record for record in caplog.records if "SLOW" in record.getMessage()]


def test_a_view_made_inside_the_page_is_written_to_the_web_log(tmp_path, caplog, monkeypatch):
    """The 12-53 s pages are views the warm-up had not written since the process started (the minutes after a deploy)
    or not within 15 minutes: the page makes the view itself. That is logged with the view's name and its seconds."""
    import logging

    from quant_platform.dashboard import v2

    monkeypatch.setattr(v2, "INLINE_VIEW_LOG_SECONDS", -1.0)
    with caplog.at_level(logging.WARNING, logger=v2.logger.name):
        assert v2.ready("a-view-nobody-wrote", tmp_path / "research", lambda: 7) == 7
    assert any("view a-view-nobody-wrote made inside the page" in record.getMessage() for record in caplog.records)
    caplog.clear()
    monkeypatch.setattr(v2, "INLINE_VIEW_LOG_SECONDS", 60.0)
    with caplog.at_level(logging.WARNING, logger=v2.logger.name):
        assert v2.ready("another-view-nobody-wrote", tmp_path / "research", lambda: 8) == 8
    assert not caplog.records
