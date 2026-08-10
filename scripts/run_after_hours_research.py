from __future__ import annotations

import json
import time

from quant_platform.config import get_settings
from quant_platform.container import build_container


def main() -> None:
    started = time.perf_counter()
    container = build_container(get_settings())
    print("階段 1/3：訓練模型並保存每個樣本外日期的預測", flush=True)
    model = container.model_research_pipeline.run("TW")
    print(
        f"模型完成：實驗 {model.experiment_count}、候選 {model.candidates}、"
        f"觀測 {model.observations:,}、失敗 {model.failed}、復用 {model.reused}",
        flush=True,
    )
    if model.failures:
        print("模型失敗：" + json.dumps(model.failures, ensure_ascii=False), flush=True)

    print("階段 2/3：更新今日盤後決策", flush=True)
    decision = container.daily_decision_pipeline.run("TW")
    print(
        f"決策完成：{decision.decision_count} 檔；觀察 {decision.observe_count}、"
        f"避免 {decision.avoid_count}、資料不足 {decision.insufficient_count}",
        flush=True,
    )

    print("階段 3/3：重播 AI 選股、零股買賣與 0050 比較", flush=True)
    container.after_hours_ai_service.invalidate_validation_cache()
    validation = container.after_hours_ai_service.validation()
    print(
        f"策略={validation.strategy_name}；狀態={validation.status}；"
        f"交易日={validation.decision_sessions:,}；股票={validation.decision_symbols:,}；"
        f"委託={validation.simulated_orders:,}；完整來回={validation.simulated_round_trips:,}",
        flush=True,
    )
    benchmark = (
        f"{validation.benchmark_return:+.2%}"
        if validation.benchmark_return is not None
        else "無資料"
    )
    excess = (
        f"{validation.excess_return:+.2%}"
        if validation.excess_return is not None
        else "無資料"
    )
    print(
        f"策略報酬={validation.total_return:+.2%}；0050={benchmark}；"
        f"超額={excess}；最大回撤={validation.max_drawdown:+.2%}",
        flush=True,
    )
    print(validation.conclusion, flush=True)
    print(f"總耗時={time.perf_counter() - started:.1f} 秒", flush=True)


if __name__ == "__main__":
    main()
