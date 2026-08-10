from __future__ import annotations

import json
import time
import argparse
from dataclasses import asdict

from quant_platform.config import get_settings
from quant_platform.container import build_container


def main() -> None:
    """Replay saved daily decisions without retraining or downloading data."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-oos",
        action="store_true",
        help="重播已保存的歷史樣本外模型預測，而非每日決策快照",
    )
    parser.add_argument(
        "--models",
        default="ridge_linear",
        help="模型歷史重播使用的模型名稱，逗號分隔；預設為目前研究主線 ridge_linear",
    )
    args = parser.parse_args()
    started = time.perf_counter()
    print(
        "歷史成交模擬：載入已保存的樣本外模型預測與價格"
        if args.model_oos
        else "歷史成交模擬：載入已保存的每日決策與價格",
        flush=True,
    )
    container = build_container(get_settings())
    validation = (
        container.after_hours_ai_service.model_oos_validation(
            model_names={
                item.strip() for item in args.models.split(",") if item.strip()
            }
        )
        if args.model_oos
        else container.after_hours_ai_service.validation()
    )
    if validation is None:
        if args.model_oos:
            container.research_failure_memory_service.record(
                case_key="model-oos-insufficient-prediction-dates",
                module="盤後 AI 歷史驗證",
                title="歷史樣本外預測日期不足，無法重播",
                severity="critical",
                evidence={"minimum_dates": 20},
                cause="舊模型實驗只保存最新預測，沒有保存每個樣本外日期。",
                correction="重新執行長資料模型研究並分批保存歷史預測。",
            )
        print("沒有足夠的歷史樣本外預測可供重播", flush=True)
        return
    result = {
        "strategy": validation.strategy_name,
        "models": sorted(item.strip() for item in args.models.split(",") if item.strip()),
        "status": validation.status,
        "start": validation.simulation_start.isoformat()
        if validation.simulation_start else None,
        "end": validation.simulation_end.isoformat()
        if validation.simulation_end else None,
        "decision_sessions": validation.decision_sessions,
        "decision_symbols": validation.decision_symbols,
        "candidate_signals": validation.candidate_signals,
        "orders": validation.simulated_orders,
        "round_trips": validation.simulated_round_trips,
        "total_return": validation.total_return,
        "benchmark_return": validation.benchmark_return,
        "excess_return": validation.excess_return,
        "max_drawdown": validation.max_drawdown,
        "turnover": validation.turnover,
        "conclusion": validation.conclusion,
        "market_regimes": [
            {
                "name": item.label,
                "sessions": item.session_count,
                "strategy_return": item.strategy_return,
                "benchmark_return": item.benchmark_return,
                "excess_return": item.excess_return,
                "passed": item.passed,
            }
            for item in validation.regime_results
        ],
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "recent_trades": [
            {
                **asdict(item),
                "event_time": item.event_time.isoformat(),
                "price": str(item.price),
                "gross_amount": str(item.gross_amount),
                "cost": str(item.cost),
            }
            for item in validation.trades[:20]
        ],
    }
    if args.model_oos and validation.strategy_gate != "候選":
        container.research_failure_memory_service.record(
            case_key=(
                "model-oos-"
                f"{validation.simulation_start.date() if validation.simulation_start else 'none'}-"
                f"{validation.simulation_end.date() if validation.simulation_end else 'none'}-"
                f"{'-'.join(sorted(item.strip() for item in args.models.split(',') if item.strip()))}"
            ),
            module="盤後 AI 歷史驗證",
            title="AI 選股歷史驗證未通過晉級門檻",
            severity="warning",
            evidence={
                "sessions": validation.decision_sessions,
                "orders": validation.simulated_orders,
                "return": validation.total_return,
                "benchmark_return": validation.benchmark_return,
                "excess_return": validation.excess_return,
                "max_drawdown": validation.max_drawdown,
                "turnover": validation.turnover,
                "gate": validation.strategy_gate,
                "models": sorted(
                    item.strip() for item in args.models.split(",") if item.strip()
                ),
                "market_regimes": [
                    {
                        "name": item.label,
                        "sessions": item.session_count,
                        "excess_return": item.excess_return,
                        "passed": item.passed,
                    }
                    for item in validation.regime_results
                ],
            },
            cause=validation.conclusion,
            correction="保留此版本供比較；調整資料、成本或風險規則後建立新版本重跑。",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
