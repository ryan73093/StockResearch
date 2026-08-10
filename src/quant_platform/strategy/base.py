from __future__ import annotations

from typing import Protocol

import pandas as pd


class SignalStrategy(Protocol):
    name: str
    version: str
    warmup: int
    parameter_grid: tuple[dict[str, float | int], ...]

    def generate(
        self,
        frame: pd.DataFrame,
        parameters: dict[str, float | int],
        regimes_by_date: dict,
    ) -> pd.Series: ...
