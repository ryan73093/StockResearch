from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from quant_platform.container import build_container


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an auditable Quant Platform pipeline")
    parser.add_argument("--market", choices=["US", "TW"], required=True)
    parser.add_argument("--full-refresh", action="store_true")
    args = parser.parse_args()
    result = build_container().daily_research_pipeline.run(
        market=args.market,
        full_refresh=args.full_refresh,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
