from threading import Event

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.application.stock_refresh import StockResearchRefreshCoordinator


def test_missing_taiwan_stock_is_normalized_added_and_refreshed(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'stock-auto-refresh.db'}")
    )
    requested: list[str] = []

    def fake_loader(symbol: str) -> None:
        requested.append(symbol)
        container.research_universe_service.add_asset(
            symbol, "TW", benchmark_symbol="0050.TW"
        )

    container.stock_research_service._missing_data_loader = fake_loader
    view = container.stock_research_service.get("2316")

    assert requested == ["2316.TW"]
    assert view.symbol == "2316.TW"
    assert view.asset is not None
    assert view.asset.market == "TW"
    assert view.auto_refresh_attempted
    assert view.auto_refresh_error is None


def test_failed_auto_refresh_returns_an_explainable_empty_view(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'stock-auto-refresh-error.db'}")
    )

    def failing_loader(symbol: str) -> None:
        raise ValueError(f"找不到 {symbol} 的日線行情，請確認股票代號")

    container.stock_research_service._missing_data_loader = failing_loader
    view = container.stock_research_service.get("2316.TW")

    assert view.asset is None
    assert view.auto_refresh_attempted
    assert "2316.TW" in (view.auto_refresh_error or "")


def test_stock_page_reports_automatic_refresh(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'stock-auto-refresh-page.db'}")
    )

    def fake_loader(symbol: str) -> None:
        container.research_universe_service.add_asset(
            symbol, "TW", benchmark_symbol="0050.TW"
        )

    container.stock_research_service._missing_data_loader = fake_loader
    response = create_app(container).test_client().get("/stocks?symbol=2316")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "2316.TW 研究資料已備妥" in body
    assert "行情、台股研究資料、特徵、模型、因子與回測流程已執行" in body


def test_refresh_coordinator_returns_immediately_and_deduplicates_work():
    started = Event()
    release = Event()
    calls: list[str] = []

    reported: list[tuple[str, int]] = []

    def slow_refresh(symbol: str, progress) -> None:
        calls.append(symbol)
        progress("測試階段", 42)
        reported.append(("測試階段", 42))
        started.set()
        assert release.wait(timeout=5)

    coordinator = StockResearchRefreshCoordinator(slow_refresh)
    first = coordinator.ensure("2316.tw")
    assert started.wait(timeout=2)
    second = coordinator.ensure("2316.TW")

    assert first.state in {"queued", "running"}
    assert second.state == "running"
    assert second.stage == "測試階段"
    assert second.percent == 42
    assert calls == ["2316.TW"]
    assert reported == [("測試階段", 42)]

    release.set()
    for _ in range(100):
        state = coordinator.status("2316.TW")
        if state is not None and state.state == "succeeded":
            break
        Event().wait(0.01)
    assert coordinator.status("2316.TW").state == "succeeded"


def test_sqlite_database_uses_wal_and_busy_timeout(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'wal.db'}")
    )
    with container.database.engine.connect() as connection:
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()

    assert journal_mode == "wal"
    assert busy_timeout == 30_000


def test_stock_feature_values_use_human_units(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'feature-format.db'}")
    )
    app = create_app(container)
    display = app.jinja_env.globals["feature_value_display"]

    assert display("return_5d", -0.12345) == "-12.35%"
    assert display("monthly_revenue", 320_550_000) == "+3.21 億元"
    assert display("quarterly_eps", 3.53) == "3.53 元／股"
    assert display("institutional_net_buy", 87_927) == "+87,927 股"
    assert display("pe_ratio", 10.33) == "10.33 倍"


def test_stock_refresh_status_endpoint_does_not_reload_the_whole_page_repeatedly(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'refresh-status.db'}")
    )
    app = create_app(container)
    client = app.test_client()

    status = client.get("/stocks/refresh-status?symbol=2324").get_json()
    template = (
        app.root_path.replace("\\", "/")
    )
    with open(
        f"{template}/templates/stocks.html", encoding="utf-8"
    ) as template_file:
        source = template_file.read()

    assert status == {
        "symbol": "2324.TW", "state": "idle", "stage": "閒置",
        "percent": 0, "error": None,
    }
    assert "setTimeout(()=>location.reload(),4000)" not in source
    assert "fetch(refreshNotice.dataset.statusUrl" in source
    assert "stockRefreshBar" in source


def test_stock_page_is_chinese_first_and_has_interactive_indicators(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'stock-chart.db'}")
    )
    app = create_app(container)
    with open(
        f"{app.root_path.replace(chr(92), '/')}/templates/stocks.html", encoding="utf-8"
    ) as template_file:
        source = template_file.read()
    with open(
        f"{app.root_path.replace(chr(92), '/')}/templates/_glossary.html", encoding="utf-8"
    ) as glossary_file:
        glossary = glossary_file.read()

    for term in ("週均線（MA5）", "月均線（MA20）", "季均線（MA60）", "布林通道", "RSI", "MACD"):
        assert term in source
        assert term in glossary
    assert "原始欄位：" not in source
    assert "Refresh incomplete" not in source
    assert "Moving Average Convergence Divergence" in glossary


def test_global_echarts_formatter_prevents_float_artifacts():
    from pathlib import Path

    source = Path(__file__).parents[1] / "src" / "quant_platform" / "dashboard" / "static" / "js" / "echarts_formatters.js"
    formatter = source.read_text(encoding="utf-8")

    assert "window.echarts.init" in formatter
    assert "tooltip.valueFormatter" in formatter
    assert "numeric.toFixed(4)" in formatter
    assert "numeric.toFixed(6)" in formatter
    assert 'backgroundColor = "rgba(7, 17, 30, 0.30)"' in formatter
    assert 'backdrop-filter:blur(8px)' in formatter


def test_stock_features_have_chinese_hover_help_and_dense_narrow_columns(tmp_path):
    from pathlib import Path

    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'feature-help.db'}")
    )
    app = create_app(container)
    helper = app.jinja_env.globals["feature_help"]
    root = Path(app.root_path)
    template = (root / "templates" / "stocks.html").read_text(encoding="utf-8")
    styles = (root / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert "相對強弱指標" in helper("rsi_14")
    assert "需搭配其他指標" in helper("unknown_feature")
    assert 'data-feature-help="{{ feature_help(name) }}"' in template
    assert 'tabindex="0"' in template
    assert ".indicator-list-v1>div:hover::after" in styles
    assert ".indicator-list-v1{grid-template-columns:repeat(3,minmax(0,1fr))}" in styles
    assert ".indicator-list-v1{grid-template-columns:repeat(2,minmax(0,1fr))}" in styles


def test_stock_page_explains_decisions_predictions_backtests_and_feature_groups(tmp_path):
    from pathlib import Path

    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'stock-conclusions.db'}")
    )
    app = create_app(container)
    root = Path(app.root_path)
    template = (root / "templates" / "stocks.html").read_text(encoding="utf-8")
    sidebar = (root / "templates" / "_sidebar.html").read_text(encoding="utf-8")

    groups = app.jinja_env.globals["group_stock_features"](
        (("rsi_14", 52.0), ("institutional_net_buy", 1000.0), ("pe_ratio", 18.0))
    )
    assert [group["label"] for group in groups] == [
        "價格與趨勢", "籌碼與資金流", "基本面與估值"
    ]
    for text in (
        "系統決策", "系統為什麼這樣判斷", "未來五日報酬",
        "怎麼用", "回測結論", "「研究中」是什麼", "依用途分類",
    ):
        assert text in template
    for text in ("核心決策", "進階研究", "資料與記憶", "執行與監控"):
        assert text in sidebar
    assert "sidebarToggle" in sidebar
    assert "每日決策" in sidebar
