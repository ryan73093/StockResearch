from unittest.mock import Mock

from quant_platform.application.analytics import DEFAULT_UNIVERSE
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app


def test_dashboard_and_health(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(build_container(settings))
    client = app.test_client()
    dashboard = client.get("/market/legacy")
    assert dashboard.status_code == 200
    assert "Research Command Center" in dashboard.get_data(as_text=True)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.get_json()["database"] == "connected"

    universe = client.get("/universe")
    assert universe.status_code == 200
    assert "股票池與資料排程" in universe.get_data(as_text=True)

    portfolios = client.get("/portfolios")
    assert portfolios.status_code == 200
    assert "Portfolio & Risk Lab" in portfolios.get_data(as_text=True)

    for path in ("/market/legacy", "/guide", "/progress", "/account", "/decisions", "/stocks", "/data", "/data-pipeline", "/data-quality", "/macro-data", "/odd-lot", "/paper-trading", "/rl-lab", "/shadow-trading", "/promotions", "/model-governance", "/news", "/reports", "/automation", "/universe", "/features", "/factors", "/strategies", "/backtests", "/ensembles", "/portfolios", "/models"):
        response = client.get(path)
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert body.count('data-auth-state="local"') == 1
        assert 'href="/models"' in body
        assert "本頁名詞解釋" in body

    models = client.get("/models")
    assert "自動模型研究" in models.get_data(as_text=True)
    assert "最佳參數" in models.get_data(as_text=True) or "尚未執行模型研究" in models.get_data(as_text=True)
    assert "使用指南" in client.get("/guide").get_data(as_text=True)
    assert "每日決策工作台" in client.get("/decisions").get_data(as_text=True)
    assert "台股研究資料" in client.get("/taiwan-data").get_data(as_text=True)
    assert "模型解釋" in client.get("/explain?symbol=0050").get_data(as_text=True)
    assert "開發藍圖" in client.get("/roadmap").get_data(as_text=True)
    progress_page = client.get("/progress").get_data(as_text=True)
    assert "系統建置進度" in progress_page
    assert "盤後策略打贏 0050" in progress_page
    assert "規劃中" in progress_page
    assert "OpenAI embedding：關閉" in progress_page
    assert "以台積電 2330 為例" in client.get("/guide").get_data(as_text=True)
    assert "單一股票研究" in client.get("/stocks?symbol=0050").get_data(as_text=True)
    rl_page = client.get("/rl-lab?symbol=2330").get_data(as_text=True)
    assert "強化學習實驗室" in rl_page
    assert "新台幣（TWD）" in rl_page
    assert "bps" in rl_page
    assert "執行／復用樣本外研究" in rl_page
    assert "504 筆訓練" in rl_page
    assert "63 筆測試" in rl_page
    assert "訓練／復用 CPU 代理" in rl_page
    assert "不需要 GPU" in rl_page
    assert "PyTorch PPO／DQN" in rl_page
    assert "CUDA 裝置" in rl_page
    shadow_page = client.get("/shadow-trading").get_data(as_text=True)
    assert "影子交易" in shadow_page
    assert "硬性關閉" in shadow_page
    assert "研究名目本金" in shadow_page
    promotion_page = client.get("/promotions").get_data(as_text=True)
    assert "晉級審查與券商沙盒" in promotion_page
    assert "真實交易" in promotion_page
    assert "硬性關閉" in promotion_page
    governance_page = client.get("/model-governance").get_data(as_text=True)
    assert "模型治理與漂移監控" in governance_page
    assert "冠軍" in governance_page
    assert "PSI" in governance_page
    quality_page = client.get("/data-quality").get_data(as_text=True)
    assert "資料品質與缺漏監控" in quality_page
    assert "Point-in-time" in quality_page
    assert "研究閘門" in quality_page
    pipeline_page = client.get("/data-pipeline").get_data(as_text=True)
    assert "現在到底有哪些資料" in pipeline_page
    assert "大戶持股集中度" in pipeline_page
    assert "未建置" in pipeline_page


def test_dashboard_keeps_expanded_stock_pool_out_of_homepage_analytics(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'expanded.db'}")
    container = build_container(settings)
    container.research_universe_service.add_asset("2316.TW", "TW")
    analytics = Mock(wraps=container.quant_analytics_service)
    container.quant_analytics_service = analytics

    response = create_app(container).test_client().get("/market/legacy")

    assert response.status_code == 200
    requested_symbols = analytics.command_center.call_args.args[0]
    assert requested_symbols == [
        symbol
        for symbol in DEFAULT_UNIVERSE
        if symbol in set(container.research_universe_service.active_symbols())
    ]
    assert "2316.TW" not in requested_symbols
