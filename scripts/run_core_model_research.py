from __future__ import annotations

import argparse
import json
import time

from quant_platform.config import get_settings
from quant_platform.container import build_container


DEFAULT_MODELS = (
    "historical_mean",
    "ridge_linear",
    "random_forest",
    "torch_cuda_mlp",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="逗號分隔的模型名稱",
    )
    parser.add_argument(
        "--profile",
        choices=("price_core", "comprehensive"),
        default="price_core",
    )
    parser.add_argument("--max-assets", type=int, default=100)
    args = parser.parse_args()
    models = tuple(item.strip() for item in args.models.split(",") if item.strip())
    started = time.perf_counter()
    container = build_container(get_settings())
    print(
        f"開始模型研究：{', '.join(models)}；特徵層={args.profile}",
        flush=True,
    )
    result = container.model_research_pipeline.run(
        "TW",
        model_names=models,
        feature_profile=args.profile,
        max_assets=args.max_assets,
    )
    for model_name, error in result.failures.items():
        container.research_failure_memory_service.record(
            case_key=f"model-run-{result.run_id}-{model_name}",
            module="模型研究",
            title=f"{model_name} 訓練或驗證失敗",
            severity="critical",
            evidence={
                "run_id": result.run_id,
                "model": model_name,
                "feature_profile": args.profile,
                "max_assets": args.max_assets,
                "error": error,
            },
            cause=error,
            correction="保留失敗版本；修正後建立新實驗，不覆蓋舊紀錄。",
        )
    print(json.dumps({
        "run_id": result.run_id,
        "status": result.status,
        "experiments": result.experiment_count,
        "candidates": result.candidates,
        "observations": result.observations,
        "failed": result.failed,
        "reused": result.reused,
        "failures": result.failures,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
