from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any, Protocol

import numpy as np


def _bounded_training_sample(
    features: np.ndarray,
    target: np.ndarray,
    max_rows: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Keep a deterministic, time-spanning sample for expensive daily models."""
    if len(features) <= max_rows:
        return features, target
    indexes = np.linspace(0, len(features) - 1, max_rows, dtype=int)
    return features[indexes], target[indexes]


MODEL_VERSION = "1.1.0"


class Regressor(Protocol):
    feature_importance_: np.ndarray

    def fit(self, features: np.ndarray, target: np.ndarray) -> "Regressor": ...

    def predict(self, features: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    family: str
    backend: str
    available: bool
    description: str
    search_space: str = "不自動調整"


class HistoricalMeanRegressor:
    def fit(self, features: np.ndarray, target: np.ndarray) -> "HistoricalMeanRegressor":
        self._mean = float(np.mean(target))
        self.feature_importance_ = np.zeros(features.shape[1], dtype=float)
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        return np.full(features.shape[0], self._mean, dtype=float)


class RidgeRegressor:
    def __init__(self, alpha: float = 10.0) -> None:
        self.alpha = alpha

    def fit(self, features: np.ndarray, target: np.ndarray) -> "RidgeRegressor":
        self._mean = features.mean(axis=0)
        self._scale = features.std(axis=0)
        self._scale[self._scale < 1e-12] = 1.0
        standardized = (features - self._mean) / self._scale
        design = np.column_stack([np.ones(len(standardized)), standardized])
        penalty = np.eye(design.shape[1]) * self.alpha
        penalty[0, 0] = 0.0
        self._coefficients = np.linalg.pinv(design.T @ design + penalty) @ design.T @ target
        raw = np.abs(self._coefficients[1:])
        self.feature_importance_ = raw / raw.sum() if raw.sum() else raw
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        standardized = (features - self._mean) / self._scale
        return np.column_stack([np.ones(len(standardized)), standardized]) @ self._coefficients


@dataclass(slots=True)
class _Stump:
    feature: int
    threshold: float
    left: float
    right: float
    gain: float

    def predict(self, features: np.ndarray) -> np.ndarray:
        return np.where(features[:, self.feature] <= self.threshold, self.left, self.right)


def _best_stump(features: np.ndarray, target: np.ndarray) -> _Stump:
    baseline_error = float(np.sum((target - target.mean()) ** 2))
    best = _Stump(0, float(np.median(features[:, 0])), float(target.mean()), float(target.mean()), 0.0)
    for feature in range(features.shape[1]):
        column = features[:, feature]
        for threshold in np.unique(np.quantile(column, [0.15, 0.3, 0.5, 0.7, 0.85])):
            left_mask = column <= threshold
            if left_mask.sum() < 5 or (~left_mask).sum() < 5:
                continue
            left = float(target[left_mask].mean())
            right = float(target[~left_mask].mean())
            predictions = np.where(left_mask, left, right)
            error = float(np.sum((target - predictions) ** 2))
            gain = baseline_error - error
            if gain > best.gain:
                best = _Stump(feature, float(threshold), left, right, gain)
    return best


class BaggedStumpsRegressor:
    """Deterministic low-depth tree ensemble available without optional ML wheels."""

    def __init__(self, estimators: int = 40, seed: int = 42) -> None:
        self.estimators = estimators
        self.seed = seed

    def fit(self, features: np.ndarray, target: np.ndarray) -> "BaggedStumpsRegressor":
        features, target = _bounded_training_sample(features, target, 30_000)
        rng = np.random.default_rng(self.seed)
        self._stumps: list[_Stump] = []
        importance = np.zeros(features.shape[1], dtype=float)
        for _ in range(self.estimators):
            indexes = rng.integers(0, len(features), len(features))
            stump = _best_stump(features[indexes], target[indexes])
            self._stumps.append(stump)
            importance[stump.feature] += max(stump.gain, 0.0)
        self.feature_importance_ = importance / importance.sum() if importance.sum() else importance
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        return np.mean([stump.predict(features) for stump in self._stumps], axis=0)


class GradientBoostedStumpsRegressor:
    def __init__(self, estimators: int = 40, learning_rate: float = 0.08) -> None:
        self.estimators = estimators
        self.learning_rate = learning_rate

    def fit(self, features: np.ndarray, target: np.ndarray) -> "GradientBoostedStumpsRegressor":
        features, target = _bounded_training_sample(features, target, 30_000)
        self._base = float(target.mean())
        predictions = np.full(len(target), self._base, dtype=float)
        self._stumps: list[_Stump] = []
        importance = np.zeros(features.shape[1], dtype=float)
        for _ in range(self.estimators):
            stump = _best_stump(features, target - predictions)
            self._stumps.append(stump)
            predictions += self.learning_rate * stump.predict(features)
            importance[stump.feature] += max(stump.gain, 0.0)
        self.feature_importance_ = importance / importance.sum() if importance.sum() else importance
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        output = np.full(len(features), self._base, dtype=float)
        for stump in self._stumps:
            output += self.learning_rate * stump.predict(features)
        return output


SKLEARN_AVAILABLE = find_spec("sklearn") is not None
XGBOOST_AVAILABLE = find_spec("xgboost") is not None
LIGHTGBM_AVAILABLE = find_spec("lightgbm") is not None
CATBOOST_AVAILABLE = find_spec("catboost") is not None
def _torch_cuda_available() -> bool:
    if find_spec("torch") is None:
        return False
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


TORCH_CUDA_AVAILABLE = _torch_cuda_available()


class TorchCudaMlpRegressor:
    """Small tabular MLP that uses CUDA when explicitly available."""

    def __init__(self, **parameters: Any) -> None:
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("torch_cuda_mlp requires an available CUDA device")
        self._torch = torch
        self._device = torch.device("cuda")
        self._epochs = int(parameters.get("epochs", 24))
        self._batch_size = int(parameters.get("batch_size", 4096))
        self._learning_rate = float(parameters.get("learning_rate", 0.001))
        self.device = "cuda"

    def fit(self, features: np.ndarray, target: np.ndarray) -> "TorchCudaMlpRegressor":
        torch = self._torch
        features, target = _bounded_training_sample(features, target, 120_000)
        self._mean = features.mean(axis=0).astype(np.float32)
        self._scale = features.std(axis=0).astype(np.float32)
        self._scale[self._scale < 1e-6] = 1.0
        matrix = ((features - self._mean) / self._scale).astype(np.float32)
        labels = target.astype(np.float32).reshape(-1, 1)
        torch.manual_seed(42)
        torch.cuda.manual_seed_all(42)
        self._model = torch.nn.Sequential(
            torch.nn.Linear(matrix.shape[1], 64),
            torch.nn.SiLU(),
            torch.nn.Dropout(0.05),
            torch.nn.Linear(64, 32),
            torch.nn.SiLU(),
            torch.nn.Linear(32, 1),
        ).to(self._device)
        optimizer = torch.optim.AdamW(
            self._model.parameters(), lr=self._learning_rate, weight_decay=1e-4
        )
        loss_function = torch.nn.SmoothL1Loss(beta=0.01)
        generator = torch.Generator(device="cpu").manual_seed(42)
        order = torch.randperm(len(matrix), generator=generator)
        self._model.train()
        for _ in range(self._epochs):
            for start in range(0, len(order), self._batch_size):
                indexes = order[start:start + self._batch_size].numpy()
                batch_x = torch.from_numpy(matrix[indexes]).to(self._device)
                batch_y = torch.from_numpy(labels[indexes]).to(self._device)
                optimizer.zero_grad(set_to_none=True)
                loss = loss_function(self._model(batch_x), batch_y)
                loss.backward()
                optimizer.step()
        self.feature_importance_ = np.zeros(matrix.shape[1], dtype=float)
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        torch = self._torch
        matrix = ((features - self._mean) / self._scale).astype(np.float32)
        output: list[np.ndarray] = []
        self._model.eval()
        with torch.inference_mode():
            for start in range(0, len(matrix), self._batch_size):
                batch = torch.from_numpy(
                    matrix[start:start + self._batch_size]
                ).to(self._device)
                output.append(self._model(batch).squeeze(1).cpu().numpy())
        return np.concatenate(output).astype(float) if output else np.array([], dtype=float)


class SklearnRandomForestRegressor:
    def __init__(self, **parameters: Any) -> None:
        from sklearn.ensemble import RandomForestRegressor

        self._model = RandomForestRegressor(
            n_estimators=int(parameters.get("n_estimators", 120)),
            max_depth=parameters.get("max_depth", 5),
            min_samples_leaf=int(parameters.get("min_samples_leaf", 8)),
            max_features=parameters.get("max_features", 0.7),
            random_state=42,
            n_jobs=-1,
        )

    def fit(self, features: np.ndarray, target: np.ndarray) -> "SklearnRandomForestRegressor":
        features, target = _bounded_training_sample(features, target, 60_000)
        self._model.fit(features, target)
        self.feature_importance_ = np.asarray(self._model.feature_importances_, dtype=float)
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        return np.asarray(self._model.predict(features), dtype=float)


class SklearnSvmRegressor:
    def __init__(self, **parameters: Any) -> None:
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.svm import SVR

        self._max_training_rows = int(parameters.get("max_training_rows", 4_000))
        self._model = make_pipeline(
            StandardScaler(),
            SVR(
                C=float(parameters.get("C", 1.0)),
                epsilon=float(parameters.get("epsilon", 0.01)),
                gamma=parameters.get("gamma", "scale"),
            ),
        )

    def fit(self, features: np.ndarray, target: np.ndarray) -> "SklearnSvmRegressor":
        # RBF-SVR training grows super-linearly with the number of rows. Keep the
        # most recent portion of the already point-in-time-safe training window so
        # an expanding TW universe cannot stall the whole daily decision pipeline.
        if len(features) > self._max_training_rows:
            features = features[-self._max_training_rows :]
            target = target[-self._max_training_rows :]
        self._model.fit(features, target)
        self.feature_importance_ = np.zeros(features.shape[1], dtype=float)
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        return np.asarray(self._model.predict(features), dtype=float)


MODEL_CATALOG = (
    ModelSpec("historical_mean", "baseline", "numpy", True, "訓練區間平均報酬基準，用來檢查複雜模型是否真的更好"),
    ModelSpec("ridge_linear", "linear", "numpy", True, "帶正則化的線性報酬模型；支援 AutoML 調整懲罰強度", "alpha：0.1～100（對數）"),
    ModelSpec("bagged_stumps", "tree_ensemble", "numpy", True, "以重抽樣決策樹樁降低單一模型變異", "樹數：20／40／60／80"),
    ModelSpec("gradient_boosted_stumps", "boosting", "numpy", True, "依序修正殘差的淺層提升模型", "樹數：20～80；學習率：0.02～0.15"),
    ModelSpec("random_forest", "tree_ensemble", "scikit-learn", SKLEARN_AVAILABLE, "隨機森林；支援 AutoML 調整樹數、深度與葉節點樣本", "樹數：80～200；深度：3～8；葉樣本：5～20；特徵比：0.5～1.0"),
    ModelSpec("svm_rbf", "kernel", "scikit-learn", SKLEARN_AVAILABLE, "支援向量迴歸；自動標準化並調整 C、epsilon 與 gamma", "C：0.05～10；epsilon：0.001～0.05；gamma：scale／auto"),
    ModelSpec("torch_cuda_mlp", "deep_learning", "PyTorch CUDA", TORCH_CUDA_AVAILABLE, "使用 GPU 訓練的表格型多層感知器；與 CPU 模型使用相同樣本外切分", "固定結構基準，避免額外調參污染樣本外資料"),
    ModelSpec("xgboost", "boosting", "xgboost", XGBOOST_AVAILABLE, "選用套件 Adapter；尚未納入 v1 自動搜尋"),
    ModelSpec("lightgbm", "boosting", "lightgbm", LIGHTGBM_AVAILABLE, "選用套件 Adapter；尚未納入 v1 自動搜尋"),
    ModelSpec("catboost", "boosting", "catboost", CATBOOST_AVAILABLE, "選用套件 Adapter；尚未納入 v1 自動搜尋"),
    ModelSpec("lstm_gru_transformer", "deep_learning", "pytorch", False, "長序列模型，待資料量與序列驗證環境成熟後導入"),
)


def build_model(name: str, parameters: dict[str, Any] | None = None) -> Regressor:
    parameters = parameters or {}
    factories = {
        "historical_mean": HistoricalMeanRegressor,
        "ridge_linear": RidgeRegressor,
        "bagged_stumps": BaggedStumpsRegressor,
        "gradient_boosted_stumps": GradientBoostedStumpsRegressor,
        "random_forest": SklearnRandomForestRegressor,
        "svm_rbf": SklearnSvmRegressor,
        "torch_cuda_mlp": TorchCudaMlpRegressor,
    }
    if name not in factories:
        raise ValueError(f"model is not available in the local runtime: {name}")
    if name == "historical_mean":
        return factories[name]()
    return factories[name](**parameters)


def suggest_parameters(trial: Any, model_name: str) -> dict[str, Any]:
    if model_name == "ridge_linear":
        return {"alpha": trial.suggest_float("alpha", 0.1, 100.0, log=True)}
    if model_name == "bagged_stumps":
        return {"estimators": trial.suggest_int("estimators", 20, 80, step=20), "seed": 42}
    if model_name == "gradient_boosted_stumps":
        return {"estimators": trial.suggest_int("estimators", 20, 80, step=20), "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.15, log=True)}
    if model_name == "random_forest":
        return {"n_estimators": trial.suggest_int("n_estimators", 80, 200, step=40), "max_depth": trial.suggest_int("max_depth", 3, 8), "min_samples_leaf": trial.suggest_int("min_samples_leaf", 5, 20), "max_features": trial.suggest_float("max_features", 0.5, 1.0)}
    if model_name == "svm_rbf":
        return {
            "C": trial.suggest_float("C", 0.05, 10.0, log=True),
            "epsilon": trial.suggest_float("epsilon", 0.001, 0.05, log=True),
            "gamma": trial.suggest_categorical("gamma", ["scale", "auto"]),
            "max_training_rows": 4_000,
        }
    return {}
