from __future__ import annotations

from datetime import UTC, datetime

from quant_platform.config import get_settings
from quant_platform.container import build_container


def main() -> None:
    memory = build_container(get_settings()).research_failure_memory_service
    cases = (
        {
            "case_key": "gpu-cuda-unknown-20260802",
            "module": "GPU 深度學習",
            "title": "CUDA 工作中斷，沒有保存模型成績",
            "severity": "critical",
            "evidence": {
                "model": "torch_cuda_mlp",
                "result": "沒有模型、預測或績效入庫",
                "error": "CUDA unknown error",
            },
            "cause": "GPU 研究程序在 CUDA 執行階段中斷，排程紀錄未正確結束。",
            "correction": "改用獨立背景程序、保存階段進度，成功入庫前一律顯示失敗。",
            "occurred_at": datetime(2026, 8, 2, 1, 43, tzinfo=UTC),
        },
        {
            "case_key": "oos-predictions-only-two-days",
            "module": "模型預測保存",
            "title": "六個舊模型各只保存 2 天預測",
            "severity": "critical",
            "evidence": {
                "models": 6,
                "prediction_dates": 2,
                "rows_per_model": 186,
                "experiment_version": "1.2.0",
            },
            "cause": "舊實驗建立時只保存最新預測，後續沒有強制重跑歷史預測。",
            "correction": "新版實驗檢查預測覆蓋率，不足時禁止復用並分批重建。",
            "occurred_at": datetime(2026, 8, 2, 6, 5, tzinfo=UTC),
        },
        {
            "case_key": "unadjusted-corporate-actions-oos-replay",
            "module": "盤後 AI 歷史驗證",
            "title": "未還原除權息與分割價格，績效不可採用",
            "severity": "critical",
            "evidence": {
                "invalid_strategy_return": 2.16282625,
                "invalid_benchmark_return": 1.2738718781,
                "max_drawdown": -0.3413715483,
                "sessions": 474,
                "orders": 525,
            },
            "cause": "歷史成交使用原始開盤與收盤價，跨越股票分割時沒有同步調整持股數量。",
            "correction": "成交與估值改用 adjusted_close 比例還原的開盤與收盤價後重新計算。",
            "occurred_at": datetime.now(UTC),
        },
        {
            "case_key": "daily-decision-replay-underperformed-202607",
            "module": "每日決策重播",
            "title": "短期決策重播跑輸 0050",
            "severity": "warning",
            "evidence": {
                "sessions": 11,
                "orders": 6,
                "strategy_return": -0.0089217,
                "benchmark_return": 0.0269595297,
                "excess_return": -0.0358812297,
            },
            "cause": "只有 11 個可驗證決策日且訊號集中，樣本與分散度都不足。",
            "correction": "保留作為失敗基準，不升級；持續累積向前決策並與多年樣本外策略分開。",
            "occurred_at": datetime(2026, 8, 2, 5, 50, tzinfo=UTC),
        },
    )
    for case in cases:
        saved = memory.record(**case)
        print(f"已記錄：{saved.case_key}｜{saved.title}")


if __name__ == "__main__":
    main()
