"""Shared test setup.

Tests must never open the production database. On 2026-10-01 a full test run added four rows to
``instance/quant_platform.db`` because importing ``quant_platform.api.app`` built an app (and a
container on the default database) at import time. This guard is installed before any test module
is imported, so such a mistake now fails loudly instead of writing to the live data.
"""
from __future__ import annotations

import os
from pathlib import Path

from quant_platform.database import engine

# 2026-10-28: the knowledge base's vectors go to the shared Qdrant server (C:\Dev\DBQdrant) when the project's .env
# names it; a test indexing its own rows there would delete the real collection's points — tests use the embedded store
os.environ["KNOWLEDGE_QDRANT_URL"] = ""

PRODUCTION_DATABASE = (Path(__file__).resolve().parents[1] / "instance" / "quant_platform.db").resolve()

_open_database = engine.Database.__init__


def _refuse_production_database(self, url: str) -> None:
    if url.startswith("sqlite:///") and Path(url.removeprefix("sqlite:///")).resolve() == PRODUCTION_DATABASE:
        raise RuntimeError(f"tests must not open the production database ({PRODUCTION_DATABASE})")
    _open_database(self, url)


engine.Database.__init__ = _refuse_production_database
