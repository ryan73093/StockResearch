from __future__ import annotations

import time

from quant_platform.config import get_settings
from quant_platform.container import build_container


def main() -> None:
    started = time.perf_counter()
    container = build_container(get_settings())
    print("開始 CUDA MLP 樣本外研究", flush=True)
    result = container.model_research_pipeline.run(
        "TW", model_names=("torch_cuda_mlp",)
    )
    print(
        f"GPU 模型完成：實驗 {result.experiment_count}、候選 {result.candidates}、"
        f"觀測 {result.observations:,}、失敗 {result.failed}",
        flush=True,
    )
    if result.failures:
        print(f"失敗原因：{result.failures}", flush=True)
    container.daily_decision_pipeline.run("TW")
    container.after_hours_ai_service.invalidate_validation_cache()
    validation = container.after_hours_ai_service.validation()
    print(validation.conclusion, flush=True)
    print(f"總耗時={time.perf_counter() - started:.1f} 秒", flush=True)


if __name__ == "__main__":
    main()
