from __future__ import annotations

from quant_platform.container import build_container


def main() -> None:
    container = build_container()
    snapshot = container.health_service.check()
    if snapshot.status != "healthy":
        raise SystemExit(
            f"Infrastructure initialization failed: database={snapshot.database}, "
            f"redis={snapshot.redis}"
        )
    print(
        f"Quant Platform schema initialized; database={snapshot.database}; "
        f"redis={snapshot.redis}; lock={snapshot.lock_backend}"
    )


if __name__ == "__main__":
    main()
