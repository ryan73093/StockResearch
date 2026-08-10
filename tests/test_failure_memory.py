from datetime import UTC, datetime, timedelta

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app


def test_failure_memory_preserves_evidence_and_counts_recurrences(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'failure-memory.db'}")
    )
    service = container.research_failure_memory_service
    first_time = datetime(2026, 8, 1, 8, tzinfo=UTC)
    second_time = first_time + timedelta(hours=3)

    service.record(
        case_key="invalid-return-definition",
        module="歷史驗證",
        title="報酬定義不一致",
        severity="critical",
        evidence={"official": 0.14, "replay": 0.48},
        cause="兩條計算路徑使用不同權重生效時間。",
        correction="統一在下一交易日開盤生效。",
        occurred_at=first_time,
    )
    service.record(
        case_key="invalid-return-definition",
        module="歷史驗證",
        title="報酬定義不一致",
        severity="critical",
        evidence={"official": 0.13, "replay": 0.47},
        cause="兩條計算路徑使用不同權重生效時間。",
        correction="統一在下一交易日開盤生效。",
        occurred_at=second_time,
    )

    cases = service.list_recent()
    assert len(cases) == 1
    assert cases[0].repeat_count == 2
    assert cases[0].first_occurred_at == first_time
    assert cases[0].last_occurred_at == second_time
    assert '"replay": 0.47' in cases[0].evidence_json

    resolved = service.resolve(
        "invalid-return-definition", "下一交易日開盤重播已通過一致性檢查。"
    )
    assert resolved.status == "resolved"
    assert resolved.repeat_count == 2
    assert resolved.verification == "下一交易日開盤重播已通過一致性檢查。"


def test_ai_trading_page_shows_failure_memory(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'failure-page.db'}")
    )
    container.research_failure_memory_service.record(
        case_key="gpu-no-result",
        module="GPU 深度學習",
        title="GPU 工作中斷且沒有模型成績",
        severity="critical",
        evidence={"model": "torch_cuda_mlp", "saved": False},
        cause="運算程序提前結束。",
        correction="分離背景程序並保存階段進度。",
    )
    container.research_failure_memory_service.record(
        case_key="after-hours-close-proxy-20260802",
        module="盤後 AI 風控研究",
        title="盤後零股代理研究",
        severity="warning",
        evidence={"return": 0.1, "result_adopted": False},
        cause="缺少實際盤後零股成交價。",
        correction="累積成交快照。",
    )

    response = create_app(container).test_client().get("/ai-trading")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "失敗案例記憶：2 筆" in body
    assert "GPU 工作中斷且沒有模型成績" in body
    assert "運算程序提前結束" in body
    assert "分離背景程序並保存階段進度" in body
    assert "torch_cuda_mlp" in body
    assert "最新風控研究" in body
    assert "1 條路線已驗證" in body
    assert "盤後零股同日成交代理" in body
