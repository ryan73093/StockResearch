from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from quant_platform.machine_learning.model_zoo import build_model, suggest_parameters


@dataclass(frozen=True, slots=True)
class AutoMLResult:
    parameters: dict[str, object]
    best_score: float | None
    trial_count: int
    engine: str


class AutoMLSearch:
    """Nested time-series parameter search using only the pre-OOS research period."""

    def __init__(self, trials: int = 8, seed: int = 42) -> None:
        self.trials = trials
        self.seed = seed

    def search(self, model_name: str, features: np.ndarray, target: np.ndarray, event_times: np.ndarray, label_available: np.ndarray) -> AutoMLResult:
        if model_name in {"historical_mean", "torch_cuda_mlp"}:
            return AutoMLResult(
                {}, None, 0,
                "baseline_no_search" if model_name == "historical_mean" else "fixed_cuda_baseline",
            )
        try:
            import optuna
        except ImportError:
            return AutoMLResult({}, None, 0, "optuna_unavailable")
        unique_dates = sorted(set(event_times.tolist()))
        tuning_end = unique_dates[int(len(unique_dates) * 0.52)]
        tuning_dates = [value for value in unique_dates if value <= tuning_end]
        if len(tuning_dates) < 80:
            return AutoMLResult({}, None, 0, "insufficient_tuning_history")

        def objective(trial: object) -> float:
            parameters = suggest_parameters(trial, model_name)
            scores: list[float] = []
            for fraction in (0.68, 0.84):
                split_index = int(len(tuning_dates) * fraction)
                validation_start = tuning_dates[split_index]
                validation_end = tuning_dates[min(split_index + max(len(tuning_dates) // 10, 15), len(tuning_dates)) - 1]
                train_mask = np.array([event < validation_start and available <= validation_start for event, available in zip(event_times, label_available)])
                valid_mask = np.array([validation_start <= event <= validation_end for event in event_times])
                if train_mask.sum() < 60 or valid_mask.sum() < 15:
                    continue
                model = build_model(model_name, parameters).fit(features[train_mask], target[train_mask])
                predicted = model.predict(features[valid_mask])
                rmse = float(np.sqrt(np.mean((target[valid_mask] - predicted) ** 2)))
                if math.isfinite(rmse):
                    scores.append(rmse)
            return float(np.mean(scores)) if scores else 1e6

        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=self.seed))
        study.optimize(objective, n_trials=self.trials, show_progress_bar=False)
        return AutoMLResult(dict(study.best_params), float(study.best_value), len(study.trials), "optuna_tpe_nested_time_split")
