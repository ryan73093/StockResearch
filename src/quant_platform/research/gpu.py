"""The GPU, when there is one (2026-10-16; 使用者：我有 GPU，先把 GPU 用起來).

The owner's machine has an NVIDIA RTX 5070 Ti (16 GB) and a CUDA build of PyTorch; until now every model trained on
the CPU while the CPU ran several pipelines at once. ``device()`` gives the GPU when PyTorch sees one and
``QUANT_NO_GPU`` is not set (tests and reproducibility checks can turn it off), else the CPU. Results on the GPU
differ from the CPU's only by floating-point rounding (and, rarely, which of two equal scores is taken).
"""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def device():
    import torch

    if os.environ.get("QUANT_NO_GPU") or not torch.cuda.is_available():
        return torch.device("cpu")
    return torch.device("cuda")


def name() -> str:
    import torch

    where = device()
    return torch.cuda.get_device_name(where) if where.type == "cuda" else "CPU"
