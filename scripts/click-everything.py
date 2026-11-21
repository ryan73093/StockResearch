"""Click everything the site offers as a link or a window, time each click twice, and list the slow ones.

2026-11-21 (使用者：整個 UI 現在非常非常的慢，點什麼都很慢，你自己點過嗎): the checks timed three pages; the 4.6 s windows of the
overview's charts (34 of them) and a 40 s pool view were never clicked. This opens every link the entry pages carry and
every window the dashboard opens by script, twice (the second shows what is cached and what is not), against the running
site. GET only; what costs money or writes is skipped (knowledge ai=1, the hypothesis form, promotion).

    .venv\\Scripts\\python.exe scripts\\click-everything.py            # the site on 127.0.0.1:5000
    .venv\\Scripts\\python.exe scripts\\click-everything.py --limit 2    # exit code 1 when a click takes longer than 2 s

Read the result as the owner's click: median under 0.3 s, nothing over 1 s on the second click, nothing over 3 s on the
first. A first click after a deploy is slow for about a minute (the pages' views are made again); run it after that.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
import urllib.request

SKIP = ("ai=1", "/promotion", "line-test", "/static/", ".json", "/logout", "/auth/", "download", "mailto:", "${")
LEGACY = ("/ai-trading", "/google-trends", "/point-in-time-data", "/odd-lot", "/reports", "/news", "/rl-lab", "/shadow-trading",
          "/promotions", "/model-governance", "/intraday-features", "/corporate-events", "/explain", "/market/legacy",
          "/database", "/automation", "/macro-data", "/taiwan-data", "/data-pipeline", "/data-quality", "/stocks",
          "/strategies", "/models", "/backtests", "/ensembles", "/factors", "/features", "/progress", "/decisions",
          "/roadmap", "/portfolios", "/paper-trading", "/universe", "/account", "/index", "/guide")
ENTRY = ["/", "/menu", "/research", "/research/pool", "/research/roles", "/research?tab=orders", "/research/my-orders",
         "/research?tab=factors", "/research?tab=ml", "/research?tab=news", "/research?tab=hypothesis", "/research?tab=jobs",
         "/research?tab=legacy", "/research/rl", "/research/knowledge", "/research/agents", "/issues", "/market",
         "/market/live", "/stock", "/stock/2330", "/holdings", "/plan", "/system", "/project", "/help"]


def quoted(path: str) -> str:
    """A path with its non-ASCII characters percent-encoded (the pages print some links raw)."""
    return urllib.parse.quote(path, safe="/?&=%#:+@,;~!$'()*-._")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", default="http://127.0.0.1:5000")
    parser.add_argument("--limit", type=float, default=0.0, help="seconds: exit 1 when a click takes longer")
    parser.add_argument("--show", type=int, default=30, help="how many of the slowest to print")
    args = parser.parse_args()

    def get(path: str, timeout: int = 240) -> tuple[object, float, str]:
        started = time.perf_counter()
        try:
            response = urllib.request.urlopen(urllib.request.Request(args.site + quoted(path), headers={"Accept": "text/html"}),
                                              timeout=timeout)
            body, code = response.read(), response.status
        except Exception as exc:  # noqa: BLE001 - a click that fails is the finding
            body, code = b"", getattr(exc, "code", type(exc).__name__)
        return code, time.perf_counter() - started, body.decode("utf-8", "replace")

    links: dict[str, str] = {}
    for page in ENTRY:
        _code, _sec, body = get(page)
        for href in re.findall(r'href="(/[^"#]*)', body):
            href = href.replace("&amp;", "&")
            if any(part in href for part in SKIP) or href in ENTRY or href.startswith(LEGACY):
                continue
            links.setdefault(href, page)
    _code, _sec, overview = get("/research")
    families = sorted(set(re.findall(r'data-family="([^"]+)"', overview)))
    windows = ["/research/stress", "/research/eras", "/research/searches", "/research/checks"]
    for track in ("recent", "full", "forward"):
        windows.append(f"/research/distribution?track={track}")
        windows += [f"/research/distribution?track={track}&family={family}" for family in families]
    windows += [f"/research/family/{family}" for family in families]
    targets = list(dict.fromkeys(ENTRY + windows + sorted(links)))
    kept, seen = [], {}
    for target in targets:                 # rule, forward and stock pages are alike: a spread of each kind
        kind = re.sub(r"[0-9a-f]{64}|/stock/\d+|\d+", "N", target.split("?")[0])
        seen[kind] = seen.get(kind, 0) + 1
        if seen[kind] <= (8 if kind.startswith(("/research/rules", "/research/forward", "/research/recent", "/stock/N")) else 80):
            kept.append(target)
    print(f"{len(kept)} clicks ({len(targets)} found), {len(families)} categories", flush=True)
    rows = []
    for target in kept:
        code1, first, _ = get(target)
        _code2, second, body = get(target)
        rows.append((max(first, second), first, second, code1, len(body) // 1024, target))
    rows.sort(reverse=True)
    print(f"{'worst':>7} {'1st':>7} {'2nd':>7}  code  {'KB':>6}  click")
    for worst, first, second, code, kb, target in rows[: args.show]:
        print(f"{worst:6.2f}s {first:6.2f}s {second:6.2f}s  {code}  {kb:6d}  {target[:96]}")
    times = sorted(row[0] for row in rows)
    print(f"\n{len(rows)} clicked: median {times[len(times) // 2]:.2f}s, {sum(t > 1 for t in times)} over 1 s, "
          f"{sum(t > 3 for t in times)} over 3 s, slowest {times[-1]:.1f}s")
    failed = [(row[3], row[5][:70]) for row in rows if row[3] != 200]
    if failed:
        print("not 200:", failed[:10])
    return 1 if args.limit and times[-1] > args.limit else 0


if __name__ == "__main__":
    sys.exit(main())
