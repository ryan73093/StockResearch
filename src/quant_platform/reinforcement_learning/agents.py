from __future__ import annotations

import copy
import importlib.util
import math
import random
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from quant_platform.reinforcement_learning.environment import (
    CpuPolicySearchTrainer,
    EpisodeMetrics,
    OfflineTradingEnvironment,
    TradingObservation,
    WalkForwardPolicySummary,
    WalkForwardSplit,
    evaluate_policy,
)


@dataclass(frozen=True, slots=True)
class TorchCapability:
    installed: bool
    version: str | None
    cuda_available: bool
    cuda_device_count: int
    device_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class TorchAgentConfig:
    hidden_size: int = 32
    learning_rate: float = 0.0003
    training_episodes: int = 6
    gamma: float = 0.99
    random_seed: int = 20260719
    requested_device: str = "auto"
    torch_num_threads: int = 1
    ppo_update_epochs: int = 4
    ppo_clip_ratio: float = 0.2
    ppo_entropy_coefficient: float = 0.01
    dqn_batch_size: int = 64
    dqn_replay_capacity: int = 5000
    dqn_target_update_steps: int = 250
    dqn_epsilon_start: float = 0.9
    dqn_epsilon_end: float = 0.05
    dqn_epsilon_decay_steps: int = 2500


@dataclass(frozen=True, slots=True)
class TorchAgentFoldResult:
    fold_number: int
    random_seed: int
    device: str
    parameter_count: int
    train_objective: float
    validation_objective: float
    train_metrics: EpisodeMetrics
    validation_metrics: EpisodeMetrics
    test_metrics: EpisodeMetrics
    feature_mean: tuple[float, ...]
    feature_std: tuple[float, ...]
    checkpoint: dict[str, list[Any]]


@dataclass(frozen=True, slots=True)
class NeuralAgentResearch:
    experiment_id: int
    symbol: str
    algorithm: str
    label: str
    experiment_version: str
    data_fingerprint: str
    data_is_current: bool
    fold_count: int
    config: TorchAgentConfig
    summary: WalkForwardPolicySummary
    folds: tuple[TorchAgentFoldResult, ...]
    computed_at: Any
    reused: bool = False


class AgentAdapter(Protocol):
    algorithm: str
    label: str

    def train_fold(
        self, environment: OfflineTradingEnvironment, split: WalkForwardSplit
    ) -> TorchAgentFoldResult: ...


def torch_capability() -> TorchCapability:
    if importlib.util.find_spec("torch") is None:
        return TorchCapability(
            installed=False, version=None, cuda_available=False,
            cuda_device_count=0, device_name="CPU（PyTorch 未安裝）",
            reason="尚未安裝選用套件；可執行 pip install .[rl] 後啟用。",
        )
    import torch

    cuda_available = bool(torch.cuda.is_available())
    return TorchCapability(
        installed=True, version=str(torch.__version__),
        cuda_available=cuda_available,
        cuda_device_count=int(torch.cuda.device_count()),
        device_name=(torch.cuda.get_device_name(0) if cuda_available else "CPU"),
        reason=("CUDA 可用" if cuda_available else "PyTorch 已安裝，CUDA 不可用，將使用 CPU"),
    )


def _torch_modules():
    capability = torch_capability()
    if not capability.installed:
        raise RuntimeError(capability.reason)
    import torch
    from torch import nn, optim

    return torch, nn, optim


def _seed_everything(torch, seed: int, num_threads: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(max(1, int(num_threads)))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def _device(torch, requested: str):
    normalized = requested.lower()
    if normalized == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("設定要求 CUDA，但目前 PyTorch 無法使用 CUDA")
    if normalized == "cpu":
        return torch.device("cpu")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _state(observation: TradingObservation, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    market = np.array((
        observation.return_1d, observation.momentum_5d,
        observation.momentum_20d, observation.volatility_20d,
        observation.drawdown,
    ), dtype=np.float32)
    return np.concatenate((
        (market - mean.astype(np.float32)) / std.astype(np.float32),
        np.array((observation.cash_weight, observation.position_weight), dtype=np.float32),
    )).astype(np.float32)


def _checkpoint(module) -> dict[str, list[Any]]:
    return {
        name: value.detach().cpu().numpy().tolist()
        for name, value in module.state_dict().items()
    }


def _ppo_actor(torch, nn, hidden: int):
    class Actor(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.body = nn.Sequential(
                nn.Linear(7, hidden), nn.Tanh(),
                nn.Linear(hidden, hidden), nn.Tanh(),
                nn.Linear(hidden, 2),
            )

        def concentration(self, value):
            return torch.nn.functional.softplus(self.body(value)) + 1.0

    return Actor()


def _dqn_network(nn, hidden: int):
    class QNetwork(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.body = nn.Sequential(
                nn.Linear(7, hidden), nn.ReLU(),
                nn.Linear(hidden, hidden), nn.ReLU(),
                nn.Linear(hidden, 5),
            )

        def forward(self, value):
            return self.body(value)

    return QNetwork()


def policy_from_neural_research(research: NeuralAgentResearch):
    """Load the newest fold checkpoint as a CPU inference-only policy."""
    torch, nn, _ = _torch_modules()
    torch.set_num_threads(max(1, research.config.torch_num_threads))
    fold = research.folds[-1]
    mean = np.asarray(fold.feature_mean, dtype=float)
    std = np.asarray(fold.feature_std, dtype=float)
    if research.algorithm == "ppo":
        network = _ppo_actor(torch, nn, research.config.hidden_size)
    elif research.algorithm == "dqn":
        network = _dqn_network(nn, research.config.hidden_size)
    else:
        raise ValueError(f"Unsupported neural policy: {research.algorithm}")
    state = network.state_dict()
    network.load_state_dict({
        name: torch.as_tensor(fold.checkpoint[name], dtype=value.dtype)
        for name, value in state.items()
    })
    network.eval()

    def policy(observation: TradingObservation) -> float:
        value = torch.as_tensor(_state(observation, mean, std))
        with torch.no_grad():
            if research.algorithm == "ppo":
                concentration = network.concentration(value)
                return float(concentration[0] / concentration.sum()) * 0.8
            action = int(torch.argmax(network(value)))
            return DqnAgentAdapter.action_weights[action]

    return policy


class _TorchAdapterBase:
    algorithm = "base"
    label = "PyTorch 代理"

    def __init__(self, config: TorchAgentConfig | None = None) -> None:
        self.config = config or TorchAgentConfig()
        if self.config.hidden_size < 8:
            raise ValueError("Hidden size must be at least 8")
        if self.config.training_episodes < 1:
            raise ValueError("Training episodes must be positive")

    @staticmethod
    def objective(metrics: EpisodeMetrics) -> float:
        return CpuPolicySearchTrainer.objective(metrics)

    @staticmethod
    def _scaler(environment, split):
        return CpuPolicySearchTrainer._fit_scaler(
            environment, split.train_start, split.train_end
        )


class PpoAgentAdapter(_TorchAdapterBase):
    algorithm = "ppo"
    label = "PPO 連續持倉代理"

    def train_fold(
        self, environment: OfflineTradingEnvironment, split: WalkForwardSplit
    ) -> TorchAgentFoldResult:
        torch, nn, optim = _torch_modules()
        seed = self.config.random_seed + split.fold_number
        _seed_everything(torch, seed, self.config.torch_num_threads)
        device = _device(torch, self.config.requested_device)
        mean, std = self._scaler(environment, split)

        class Critic(nn.Module):
            def __init__(self, hidden: int) -> None:
                super().__init__()
                self.body = nn.Sequential(
                    nn.Linear(7, hidden), nn.Tanh(),
                    nn.Linear(hidden, hidden), nn.Tanh(),
                    nn.Linear(hidden, 1),
                )

            def forward(self, value):
                return self.body(value).squeeze(-1)

        actor = _ppo_actor(torch, nn, self.config.hidden_size).to(device)
        critic = Critic(self.config.hidden_size).to(device)
        actor_optimizer = optim.Adam(actor.parameters(), lr=self.config.learning_rate)
        critic_optimizer = optim.Adam(critic.parameters(), lr=self.config.learning_rate)

        def deterministic_policy(observation):
            state = torch.as_tensor(_state(observation, mean, std), device=device)
            with torch.no_grad():
                concentration = actor.concentration(state)
                fraction = concentration[0] / concentration.sum()
            return float(fraction.cpu()) * environment.max_position_weight

        best_state = copy.deepcopy(actor.state_dict())
        best_validation = float("-inf")
        best_validation_metrics = evaluate_policy(
            environment, deterministic_policy, split.validation_start, split.validation_end
        )
        for _ in range(self.config.training_episodes):
            observation = environment.reset(split.train_start, split.train_end)
            states: list[np.ndarray] = []
            actions: list[float] = []
            old_log_probs: list[float] = []
            rewards: list[float] = []
            terminated = False
            while not terminated:
                state_array = _state(observation, mean, std)
                state_tensor = torch.as_tensor(state_array, device=device)
                concentration = actor.concentration(state_tensor)
                distribution = torch.distributions.Beta(concentration[0], concentration[1])
                action_fraction = distribution.sample().clamp(1e-5, 1 - 1e-5)
                log_probability = distribution.log_prob(action_fraction)
                observation, reward, terminated, _ = environment.step(
                    float(action_fraction.detach().cpu()) * environment.max_position_weight
                )
                states.append(state_array)
                actions.append(float(action_fraction.detach().cpu()))
                old_log_probs.append(float(log_probability.detach().cpu()))
                rewards.append(reward * 100.0)
            returns: list[float] = []
            running = 0.0
            for reward in reversed(rewards):
                running = reward + self.config.gamma * running
                returns.append(running)
            returns.reverse()
            state_batch = torch.as_tensor(np.asarray(states), device=device)
            action_batch = torch.as_tensor(actions, dtype=torch.float32, device=device)
            old_log_batch = torch.as_tensor(old_log_probs, dtype=torch.float32, device=device)
            return_batch = torch.as_tensor(returns, dtype=torch.float32, device=device)
            for _ in range(self.config.ppo_update_epochs):
                values = critic(state_batch)
                advantages = return_batch - values.detach()
                advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
                concentrations = actor.concentration(state_batch)
                distribution = torch.distributions.Beta(
                    concentrations[:, 0], concentrations[:, 1]
                )
                log_probs = distribution.log_prob(action_batch)
                ratio = torch.exp(log_probs - old_log_batch)
                clipped = torch.clamp(
                    ratio, 1 - self.config.ppo_clip_ratio, 1 + self.config.ppo_clip_ratio
                )
                actor_loss = -torch.min(ratio * advantages, clipped * advantages).mean()
                actor_loss -= self.config.ppo_entropy_coefficient * distribution.entropy().mean()
                actor_optimizer.zero_grad()
                actor_loss.backward()
                torch.nn.utils.clip_grad_norm_(actor.parameters(), 1.0)
                actor_optimizer.step()
                critic_loss = torch.nn.functional.mse_loss(values, return_batch)
                critic_optimizer.zero_grad()
                critic_loss.backward()
                torch.nn.utils.clip_grad_norm_(critic.parameters(), 1.0)
                critic_optimizer.step()
            validation = evaluate_policy(
                environment, deterministic_policy,
                split.validation_start, split.validation_end,
            )
            score = self.objective(validation)
            if score > best_validation:
                best_validation = score
                best_validation_metrics = validation
                best_state = copy.deepcopy(actor.state_dict())
        actor.load_state_dict(best_state)
        train_metrics = evaluate_policy(
            environment, deterministic_policy, split.train_start, split.train_end
        )
        test_metrics = evaluate_policy(
            environment, deterministic_policy, split.test_start, split.test_end
        )
        return TorchAgentFoldResult(
            fold_number=split.fold_number, random_seed=seed, device=str(device),
            parameter_count=sum(value.numel() for value in actor.parameters()),
            train_objective=self.objective(train_metrics),
            validation_objective=best_validation,
            train_metrics=train_metrics, validation_metrics=best_validation_metrics,
            test_metrics=test_metrics,
            feature_mean=tuple(float(value) for value in mean),
            feature_std=tuple(float(value) for value in std),
            checkpoint=_checkpoint(actor),
        )


class DqnAgentAdapter(_TorchAdapterBase):
    algorithm = "dqn"
    label = "DQN 五檔持倉代理"
    action_weights = (0.0, 0.2, 0.4, 0.6, 0.8)

    def train_fold(
        self, environment: OfflineTradingEnvironment, split: WalkForwardSplit
    ) -> TorchAgentFoldResult:
        torch, nn, optim = _torch_modules()
        seed = self.config.random_seed + 1000 + split.fold_number
        _seed_everything(torch, seed, self.config.torch_num_threads)
        device = _device(torch, self.config.requested_device)
        mean, std = self._scaler(environment, split)

        online = _dqn_network(nn, self.config.hidden_size).to(device)
        target = _dqn_network(nn, self.config.hidden_size).to(device)
        target.load_state_dict(online.state_dict())
        optimizer = optim.Adam(online.parameters(), lr=self.config.learning_rate)
        replay: deque[tuple[np.ndarray, int, float, np.ndarray, bool]] = deque(
            maxlen=self.config.dqn_replay_capacity
        )
        total_steps = 0

        def deterministic_policy(observation):
            state = torch.as_tensor(_state(observation, mean, std), device=device)
            with torch.no_grad():
                action = int(torch.argmax(online(state)).cpu())
            return self.action_weights[action]

        best_state = copy.deepcopy(online.state_dict())
        best_validation = float("-inf")
        best_validation_metrics = evaluate_policy(
            environment, deterministic_policy, split.validation_start, split.validation_end
        )
        rng = np.random.default_rng(seed)
        for _ in range(self.config.training_episodes):
            observation = environment.reset(split.train_start, split.train_end)
            terminated = False
            while not terminated:
                state = _state(observation, mean, std)
                epsilon = self.config.dqn_epsilon_end + (
                    self.config.dqn_epsilon_start - self.config.dqn_epsilon_end
                ) * math.exp(-total_steps / self.config.dqn_epsilon_decay_steps)
                if rng.random() < epsilon:
                    action = int(rng.integers(len(self.action_weights)))
                else:
                    with torch.no_grad():
                        action = int(torch.argmax(
                            online(torch.as_tensor(state, device=device))
                        ).cpu())
                next_observation, reward, terminated, _ = environment.step(
                    self.action_weights[action]
                )
                next_state = _state(next_observation, mean, std)
                replay.append((state, action, reward * 100.0, next_state, terminated))
                observation = next_observation
                total_steps += 1
                if len(replay) >= self.config.dqn_batch_size:
                    indices = rng.choice(len(replay), self.config.dqn_batch_size, replace=False)
                    batch = [replay[int(index)] for index in indices]
                    states = torch.as_tensor(np.asarray([item[0] for item in batch]), device=device)
                    actions = torch.as_tensor([item[1] for item in batch], device=device).long()
                    rewards = torch.as_tensor([item[2] for item in batch], device=device)
                    next_states = torch.as_tensor(np.asarray([item[3] for item in batch]), device=device)
                    dones = torch.as_tensor([item[4] for item in batch], device=device).float()
                    predicted = online(states).gather(1, actions.unsqueeze(1)).squeeze(1)
                    with torch.no_grad():
                        expected = rewards + self.config.gamma * (1 - dones) * target(next_states).max(1).values
                    loss = torch.nn.functional.smooth_l1_loss(predicted, expected)
                    optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(online.parameters(), 1.0)
                    optimizer.step()
                if total_steps % self.config.dqn_target_update_steps == 0:
                    target.load_state_dict(online.state_dict())
            validation = evaluate_policy(
                environment, deterministic_policy,
                split.validation_start, split.validation_end,
            )
            score = self.objective(validation)
            if score > best_validation:
                best_validation = score
                best_validation_metrics = validation
                best_state = copy.deepcopy(online.state_dict())
        online.load_state_dict(best_state)
        train_metrics = evaluate_policy(
            environment, deterministic_policy, split.train_start, split.train_end
        )
        test_metrics = evaluate_policy(
            environment, deterministic_policy, split.test_start, split.test_end
        )
        return TorchAgentFoldResult(
            fold_number=split.fold_number, random_seed=seed, device=str(device),
            parameter_count=sum(value.numel() for value in online.parameters()),
            train_objective=self.objective(train_metrics),
            validation_objective=best_validation,
            train_metrics=train_metrics, validation_metrics=best_validation_metrics,
            test_metrics=test_metrics,
            feature_mean=tuple(float(value) for value in mean),
            feature_std=tuple(float(value) for value in std),
            checkpoint=_checkpoint(online),
        )
