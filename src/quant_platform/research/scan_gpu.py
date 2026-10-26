"""The search's first stage on the GPU (2026-10-16; 使用者：我有 GPU，先把 GPU 用起來).

``scan.account`` walks one candidate through the development period session by session: rank the stocks by its
score, keep what is in the keep zone (or still rising / not yet held MIN_HOLD sessions), fill up to TOP best first
with at most INDUSTRY_SHARE of TOP from one industry, weigh the holdings, charge the entries and exits. Here
thousands of candidates sharing a universe, a check calendar, a weighting and a hold make the same walk together,
each session one batch of tensor operations:

* the order: a stable sort by the score after a stable sort by 20-session turnover (the CPU's ``np.lexsort``), NaN
  last; every symbol's place in it;
* the fill: within each industry the not-kept eligible stocks are counted in order, a stock is taken while its
  industry has room, and the first TOP − kept of those taken are the new holdings (the CPU's greedy loop gives the
  same: whether a stock is taken depends only on its own industry's count and on not having reached TOP).

The results are the CPU's to floating-point rounding (tests/test_research_scan_gpu.py compares them session by
session). The rows and daily excess are written as the CPU workers write them (``screen-g<n>.jsonl`` and
``screen-g<n>.excess.npy``), so everything after the first stage reads them unchanged.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

BATCH = 2048
HEAD = 160          # 2026-10-26: the places of the order the industry fill walks first (see accounts)


def _costs(costs) -> tuple[float, float]:
    from quant_platform.research.costs import DEFAULT_SLIPPAGE_BPS

    slippage = getattr(costs, "slippage_bps", DEFAULT_SLIPPAGE_BPS) / 10_000
    fee = costs.fee_rate * costs.fee_discount
    tax = dict(costs.tax_rates).get("stock", 0.003)
    return fee + slippage, fee + tax + slippage


class Device:
    """The arrays on the GPU: percentiles as session × universe × signal × symbol (float16), the rest per session."""

    def __init__(self, arrays) -> None:
        import torch

        from quant_platform.research.gpu import device

        self.torch, self.where = torch, device()
        self.arrays = arrays
        moved = np.ascontiguousarray(np.moveaxis(np.asarray(arrays.percentiles), 3, 0))
        self.percentiles = torch.from_numpy(moved).to(self.where)
        self.next_returns = torch.from_numpy(np.ascontiguousarray(arrays.next_returns.T)).to(self.where)  # session × symbol
        self.calm = torch.from_numpy(np.ascontiguousarray(arrays.calm.T)).to(self.where)
        self.rising = (torch.from_numpy(np.ascontiguousarray(arrays.rising.T.astype(bool))).to(self.where)
                       if arrays.rising is not None else None)
        self.industry = torch.from_numpy(arrays.industry.astype(np.int64)).to(self.where)
        self.industries = int(arrays.industry.max()) + 1 if len(arrays.industry) else 1
        symbols, sessions = arrays.next_returns.shape
        if arrays.liquidity is not None:                  # each session's order by turnover (ties by the row)
            order = np.argsort(-arrays.liquidity.T, axis=1, kind="stable")
        else:
            order = np.tile(np.arange(symbols), (sessions, 1))
        self.by_turnover = torch.from_numpy(order.astype(np.int64)).to(self.where)
        self.symbols, self.sessions = symbols, sessions


def accounts(gpu: Device, items: list[dict], costs) -> tuple[np.ndarray, np.ndarray]:
    """(candidate × session returns after costs, entries per candidate) for candidates that share the universe,
    the check calendar, the weighting and the hold (``scan.account`` for each, all at once)."""
    from quant_platform.research.scan import INDUSTRY_SHARE, KEEP, MIN_HOLD, TOP

    torch, where = gpu.torch, gpu.where
    first = items[0]
    slot = 0 if first["universe"] == "all" else 1
    checks = gpu.arrays.weekly_days if first["check"] == "weekly" else np.ones(gpu.sessions, dtype=bool)
    calm = first["weighting"] == "inverse_vol"
    trend = first.get("hold") == "trend"
    minimum = 0 if first.get("hold") == "free" else MIN_HOLD       # 2026-10-20: "free" sells once out of the zone
    if trend and gpu.rising is None:
        raise ValueError("這次掃描的陣列沒有月線資料（scan 1.2.0 以前），不能評估月線上續抱")
    cost_in, cost_out = _costs(costs)
    names = gpu.arrays.names
    count, symbols = len(items), gpu.symbols
    width = max(len(item["factors"]) for item in items)
    signal = torch.full((count, width), -1, dtype=torch.long, device=where)
    for row, item in enumerate(items):
        signal[row, : len(item["factors"])] = torch.tensor([names.index(name) for name in item["factors"]])
    limit = max(1, int(INDUSTRY_SHARE * TOP))
    held = torch.zeros((count, symbols), dtype=torch.bool, device=where)
    bought = torch.full((count, symbols), -1, dtype=torch.long, device=where)
    previous_weight = torch.zeros((count, symbols), dtype=torch.float64, device=where)
    returns = torch.zeros((count, gpu.sessions - 1), dtype=torch.float64, device=where)
    entries = torch.zeros(count, dtype=torch.long, device=where)
    places = torch.arange(symbols, device=where).expand(count, -1)
    industry = gpu.industry

    def ordered(score):
        base = gpu.by_turnover[t]
        key = torch.where(torch.isnan(score), torch.inf, -score)[:, base]
        return base[torch.sort(key, dim=1, stable=True).indices]                       # candidate × symbol

    for t in range(gpu.sessions - 1):
        today = gpu.percentiles[t, slot]                                                   # signal × symbol
        score = today[signal[:, 0]].float()
        for column in range(1, width):
            used = signal[:, column] >= 0
            more = today[signal[:, column].clamp(min=0)].float()
            score = torch.where(used[:, None], score + more, score)
        valid = ~torch.isnan(score)
        rising = gpu.rising[t] if trend else None
        if checks[t] or (trend and bool(held.any())):
            order = ordered(score)
            rank = torch.empty_like(order).scatter_(1, order, places)
            zone = valid & (rank < TOP * KEEP)
            if trend:
                staying = zone | (valid & rising[None, :])
            else:
                staying = zone | (valid & (t - bought < minimum) & (bought >= 0))
            kept = held & staying
            if checks[t]:
                counts = torch.zeros((count, gpu.industries), dtype=torch.long, device=where)
                counts.scatter_add_(1, industry.expand(count, -1), kept.long())
                cutoff = (TOP - kept.sum(1))[:, None]

                def fill(rows_order, rows_valid, rows_kept, rows_counts, rows_cutoff):
                    in_order = industry[rows_order]
                    fresh = rows_valid.gather(1, rows_order) & ~rows_kept.gather(1, rows_order)
                    one_hot = torch.nn.functional.one_hot(in_order, gpu.industries).to(torch.int16) * fresh[..., None]
                    before = one_hot.cumsum(1, dtype=torch.int16).gather(2, in_order[..., None]).squeeze(2) - fresh.short()
                    taken = fresh & (before < limit - rows_counts.gather(1, in_order))
                    return fresh, taken & (taken.long().cumsum(1) <= rows_cutoff)

                # 2026-10-26 (使用者：要真的上億個、全段篩): the fill only walks the first HEAD places of the order (the
                # one-hot count over every symbol was 60% of a session); a place there is taken exactly as with the
                # whole order (its count reads only places before it). A row that did not fill up there, while fresh
                # stocks remain further down, is filled again over its whole order — the same result as before.
                head = order[:, :HEAD]
                fresh, taken = fill(head, valid, kept, counts, cutoff)
                new = torch.zeros_like(held).scatter_(1, head, taken)
                short = (taken.sum(1) < cutoff[:, 0]) & ((valid & ~kept).sum(1) > fresh.sum(1))
                if bool(short.any()):
                    rows = short.nonzero().squeeze(1)
                    _fresh, whole = fill(order[rows], valid[rows], kept[rows], counts[rows], cutoff[rows])
                    new[rows] = torch.zeros_like(held[rows]).scatter_(1, order[rows], whole)
                any_valid = valid.any(1)[:, None]
                now = (kept | new) & any_valid
                bought = torch.where(now & ~kept, torch.full_like(bought, t), torch.where(now, bought, -1))
                held = now
            else:
                held = kept
        if calm:
            raw = gpu.calm[t].double()[None, :] * held
            total = raw.sum(1, keepdim=True)
            weight = torch.where(total > 0, raw / torch.where(total > 0, total, 1.0), 0.0)
        else:
            sizes = held.sum(1, keepdim=True)
            weight = held.double() / sizes.clamp(min=1)
        was = previous_weight > 0
        added = held & ~was
        cost = (weight * added).sum(1) * cost_in + (previous_weight * (was & ~held)).sum(1) * cost_out
        entries += added.sum(1)
        returns[:, t] = (weight * gpu.next_returns[t].double()[None, :]).sum(1) - cost
        previous_weight = weight
    return returns.cpu().numpy(), entries.cpu().numpy()


def groups(items: list[dict]) -> dict[tuple, list[int]]:
    output: dict[tuple, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        output[(item["universe"], item["check"], item["weighting"], item.get("hold", "days"))].append(index)
    return output


def screen_gpu(arrays_folder: Path, items: list[dict], costs, out_folder: Path, progress=None) -> list[dict]:
    """``scan.screen`` on the GPU: the same rows and daily excess files."""
    from quant_platform.research.scan import load_arrays, metrics

    out_folder.mkdir(parents=True, exist_ok=True)
    arrays = load_arrays(arrays_folder)
    gpu = Device(arrays)
    years = (arrays.days[-1] - arrays.days[0]).days / 365.25
    bench = arrays.bench
    rows: list[dict] = []
    done = 0
    for number, (_key, indexes) in enumerate(sorted(groups(items).items())):
        for start in range(0, len(indexes), BATCH):
            chunk = [items[index] for index in indexes[start:start + BATCH]]
            returns, entries = accounts(gpu, chunk, costs)
            excess = (returns - bench[None, : returns.shape[1]]).astype(np.float16)
            path = out_folder / f"screen-g{number:02d}-{start // BATCH:04d}.jsonl"
            with open(path, "w", encoding="utf-8") as handle:
                for item, row_returns, row_entries in zip(chunk, returns, entries):
                    row = {**item, **metrics(row_returns, bench, years, int(row_entries))}
                    rows.append(row)
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            np.save(path.with_suffix(".excess.npy"), excess)
            done += len(chunk)
            if progress:
                progress(done)
    return rows
