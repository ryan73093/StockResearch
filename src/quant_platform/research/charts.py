"""2026-10-25 (使用者：研究總覽各類策略完全看不懂，每年比 0050 寫的數字是什麼意思；不是要你做成滿版的 dashboard 嗎，只給我
這麼多文字的表格，線圖呢): inline SVG charts for the research dashboard — lines over time and grouped bars — in the page's
chart colours (CSS .v1–.v3 and .vref, validated with the dataviz palette check for both themes), each drawn twice: a
wide one and a narrow one for phones, so the text stays readable. A line chart has one hover column a point listing
every line's value there, and names its series at the line ends (wide); a bar carries its own hover title."""

from __future__ import annotations

import math
from datetime import date, datetime
from html import escape

SIZES = {"wide": (760, 300), "narrow": (360, 250)}


def ticks(low: float, high: float, count: int = 5) -> list[float]:
    """Round axis values covering low..high."""
    span = high - low
    if span <= 0:
        return [low]
    raw = span / count
    step = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        if raw <= step * factor:
            step *= factor
            break
    first = math.ceil(low / step) * step
    return [first + index * step for index in range(int((high - first) / step) + 1)]


def _x(value) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).timestamp()
    return datetime.fromisoformat(str(value)).timestamp()


def line(points: list[dict], lines: list[dict], y_text, x_text, label: str, x_key: str = "x",
         guides: list[tuple[float, str]] = (), band: tuple[str, str] | None = None, step: bool = False,
         marks: list[tuple[object, str]] = ()) -> str:
    """``lines`` = [{key, label, css ("v1"…"v3", "vref"), dashed}]; ``x_text(x, full)`` formats an axis or hover
    date; ``guides`` = horizontal reference lines (value, text); ``band`` = (low key, high key) shaded under the first
    line; ``step``: best-so-far lines (flat until the next change)."""
    return "".join(_line(points, lines, y_text, x_text, label, x_key, guides, band, step, marks, size) for size in SIZES)


def _line(points, lines, y_text, x_text, label, x_key, guides, band, step, marks, size) -> str:
    width, height = SIZES[size]
    wide = size == "wide"
    left, right, top, bottom = (60, 150, 14, 28) if wide else (46, 10, 12, 26)
    xs = [_x(point[x_key]) for point in points]
    values = [point[item["key"]] for point in points for item in lines if point.get(item["key"]) is not None]
    if band:
        values += [point[key] for point in points for key in band if point.get(key) is not None]
    values += [value for value, _text in guides]
    if not xs or not values:
        return ""
    low, high = min(values), max(values)
    pad = (high - low) * 0.06 or abs(high) * 0.1 or 1.0
    low, high = low - pad, high + pad
    x0, x1 = min(xs), max(xs)
    x1 = x1 if x1 > x0 else x0 + 1

    def sx(value: float) -> float:
        return left + (value - x0) / (x1 - x0) * (width - left - right)

    def sy(value: float) -> float:
        return top + (high - value) / (high - low) * (height - top - bottom)

    parts = [f'<svg class="viz viz--{size}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">']
    for value in ticks(low, high, 5 if wide else 4):
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{sy(value):.1f}" y2="{sy(value):.1f}"/>'
                     f'<text x="{left - 6}" y="{sy(value) + 4:.1f}" text-anchor="end">{escape(y_text(value))}</text>')
    shown, last_x = set(), -1e9
    for index in sorted({0, len(xs) - 1, *range(0, len(xs), max(1, len(xs) // (6 if wide else 3)))}):
        text = x_text(points[index][x_key], False)
        x = sx(xs[index])
        if text in shown or x - last_x < (56 if wide else 60):
            continue
        shown.add(text)
        last_x = x
        anchor = "start" if index == 0 else ("end" if index == len(xs) - 1 else "middle")
        parts.append(f'<text x="{x:.1f}" y="{height - 8}" text-anchor="{anchor}">{escape(text)}</text>')
    if band:
        upper = [(sx(x), sy(point[band[1]])) for x, point in zip(xs, points) if point.get(band[1]) is not None]
        lower = [(sx(x), sy(point[band[0]])) for x, point in zip(xs, points) if point.get(band[0]) is not None]
        if upper and lower:
            shape = " ".join(f"{x:.1f},{y:.1f}" for x, y in upper + lower[::-1])
            parts.append(f'<polygon class="band" points="{shape}"/>')
    for value, text in guides:
        parts.append(f'<line class="guide" x1="{left}" x2="{width - right}" y1="{sy(value):.1f}" y2="{sy(value):.1f}"/>'
                     f'<text class="lbl" x="{left + 4}" y="{sy(value) - 4:.1f}">{escape(text)}</text>')
    for number, (at, text) in enumerate(marks):          # two rows, so close marks do not overlap
        x = sx(_x(at))
        if left <= x <= width - right:
            parts.append(f'<line class="mark" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{height - bottom}"/>')
            if wide:                                      # a phone keeps the lines, not the words
                parts.append(f'<text x="{x + 3:.1f}" y="{top + 10 + 13 * (number % 2)}">{escape(text)}</text>')
    ends = []
    for item in lines:
        coords, previous, last = [], None, None
        for x, point in zip(xs, points):
            value = point.get(item["key"])
            if value is None:
                continue
            if step and previous is not None:
                coords.append(f"{sx(x):.1f},{sy(previous):.1f}")
            coords.append(f"{sx(x):.1f},{sy(value):.1f}")
            previous, last = value, (x, value)
        if not coords:
            continue
        dashed = ' stroke-dasharray="5 4"' if item.get("dashed") else ""
        parts.append(f'<polyline class="{item["css"]}"{dashed} points="{" ".join(coords)}"/>')
        if last:
            ends.append([sy(last[1]), item, last[1]])
    for index, (x, point) in enumerate(zip(xs, points)):   # hover: one column a point, every line's value in it
        named = [f"{item['label']}：{y_text(point[item['key']])}" for item in lines if point.get(item["key"]) is not None]
        if not named:
            continue
        before = sx(xs[index - 1]) if index else sx(x)
        after = sx(xs[index + 1]) if index + 1 < len(xs) else sx(x)
        x_left, x_right = (sx(x) + before) / 2 if index else left, (sx(x) + after) / 2 if index + 1 < len(xs) else width - right
        parts.append(f'<rect class="col" x="{x_left:.1f}" y="{top}" width="{max(x_right - x_left, 1):.1f}" '
                     f'height="{height - top - bottom}"><title>{escape(x_text(point[x_key], True))}{chr(10)}'
                     f'{escape(chr(10).join(named))}</title></rect>')
    if wide and ends:                                   # the series named at the line ends, kept 15px apart
        ends.sort(key=lambda end: end[0])
        for index in range(1, len(ends)):
            ends[index][0] = max(ends[index][0], ends[index - 1][0] + 15)
        for y, item, value in ends:
            parts.append(f'<circle class="key {item["css"]}" cx="{width - right + 10}" cy="{y - 4:.1f}" r="4"/>'
                         f'<text class="lbl" x="{width - right + 18}" y="{y:.1f}">{escape(item.get("short") or item["label"])} '
                         f'{escape(y_text(value))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def bars(groups: list[dict], series: list[dict], y_text, label: str, value_labels: bool = True) -> str:
    """Grouped bars from a zero line: ``groups`` = [{label, short (the phone's label), note, values: {key: value},
    titles: {key: text}}],
    ``series`` = [{key, label, css}]. Negative values hang below zero. Values are printed on the wide chart's bars."""
    return "".join(_bars(groups, series, y_text, label, value_labels, size) for size in SIZES)


def _bars(groups, series, y_text, label, value_labels, size) -> str:
    width, height = SIZES[size]
    wide = size == "wide"
    notes = any(group.get("note") for group in groups)
    left, right, top, bottom = (54, 8, 26, 44 if notes else 28) if wide else (40, 6, 14, 40 if notes else 26)
    values = [value for group in groups for value in group["values"].values() if value is not None] + [0.0]
    if not groups or len(values) == 1:
        return ""
    low, high = min(values), max(values)
    pad = (high - low) * 0.08 or 1.0
    low, high = (low - pad if low < 0 else 0.0), (high + pad if high > 0 else 0.0)

    def sy(value: float) -> float:
        return top + (high - value) / (high - low) * (height - top - bottom)

    slot = (width - left - right) / len(groups)
    gap = 2.0
    inner = slot * (0.78 if len(series) > 1 else 0.6)
    bar = max(3.0, (inner - gap * (len(series) - 1)) / len(series))
    parts = [f'<svg class="viz viz--{size}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">']
    for value in ticks(low, high, 5 if wide else 4):
        css = "zero" if abs(value) < 1e-12 else "grid"
        parts.append(f'<line class="{css}" x1="{left}" x2="{width - right}" y1="{sy(value):.1f}" y2="{sy(value):.1f}"/>'
                     f'<text x="{left - 6}" y="{sy(value) + 4:.1f}" text-anchor="end">{escape(y_text(value))}</text>')
    placed: list[tuple[float, float, float]] = []                       # value labels drawn: (left, right, y)
    for number, group in enumerate(groups):
        center = left + slot * (number + 0.5)
        start = center - (bar * len(series) + gap * (len(series) - 1)) / 2
        for index, item in enumerate(series):
            value = group["values"].get(item["key"])
            if value is None:
                continue
            x = start + index * (bar + gap)
            y0, y1 = sy(0.0), sy(value)
            top_y, length = min(y0, y1), max(abs(y1 - y0), 1.0)
            title = (group.get("titles") or {}).get(item["key"]) or f"{group['label']} {item['label']}：{y_text(value)}"
            parts.append(f'<rect class="{item["css"]}" x="{x:.1f}" y="{top_y:.1f}" width="{bar:.1f}" height="{length:.1f}" rx="2">'
                         f'<title>{escape(title)}</title></rect>')
            if wide and value_labels:
                text = y_text(value)
                ty = y1 - 4 if value >= 0 else y1 + 13
                half = len(text) * 3.4 + 2
                box = (x + bar / 2 - half, x + bar / 2 + half, ty)
                if not any(box[0] < other[1] and other[0] < box[1] and abs(box[2] - other[2]) < 13 for other in placed):
                    placed.append(box)
                    parts.append(f'<text class="lbl" x="{x + bar / 2:.1f}" y="{ty:.1f}" text-anchor="middle">{escape(text)}</text>')
        name = group["label"] if wide else group.get("short") or group["label"]
        parts.append(f'<text x="{center:.1f}" y="{height - bottom + 16}" text-anchor="middle">{escape(name)}</text>')
        if group.get("note") and (wide or slot >= 52):
            parts.append(f'<text class="lbl" x="{center:.1f}" y="{height - bottom + 31}" text-anchor="middle">{escape(group["note"])}</text>')
    parts.append("</svg>")
    return "".join(parts)
