"""Shared test setup.

Tests must never open the production database. On 2026-10-01 a full test run added four rows to
``instance/quant_platform.db`` because importing ``quant_platform.api.app`` built an app (and a
container on the default database) at import time. This guard is installed before any test module
is imported, so such a mistake now fails loudly instead of writing to the live data.
"""
from __future__ import annotations

import multiprocessing
import os
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from quant_platform.database import engine

# 2026-10-28: the knowledge base's vectors go to the shared Qdrant server (C:\Dev\DBQdrant) when the project's .env
# names it; a test indexing its own rows there would delete the real collection's points — tests use the embedded store
os.environ["KNOWLEDGE_QDRANT_URL"] = ""
# 2026-11-02: the Sunday suite runs inside the worker, whose environment points HOME_SSO_CONFIG at the live Home SSO —
# every app a test built then sent its requests to the login page (302), and ten Access tests failed on 10/24 and 10/31
# while passing from a shell. Tests build the app without the live sign-in.
os.environ.pop("HOME_SSO_CONFIG", None)

# 2026-11-12 (D-043): a process started by a test (multiprocessing "spawn") imports the file the run was started from.
# A launcher that calls pytest at the top of its file then runs the whole suite again inside every such process - the
# S11-D run of 11/11 did that for ever. The suite does not run inside a started process; start it with
# scripts/run-tests.ps1 (python -m pytest).
if multiprocessing.parent_process() is not None:
    raise SystemExit("the test suite was started again inside a process a test started: run scripts/run-tests.ps1")

PRODUCTION_DATABASE = (Path(__file__).resolve().parents[1] / "instance" / "quant_platform.db").resolve()

_open_database = engine.Database.__init__


def _refuse_production_database(self, url: str) -> None:
    if url.startswith("sqlite:///") and Path(url.removeprefix("sqlite:///")).resolve() == PRODUCTION_DATABASE:
        raise RuntimeError(f"tests must not open the production database ({PRODUCTION_DATABASE})")
    _open_database(self, url)


engine.Database.__init__ = _refuse_production_database


@event.listens_for(Engine, "connect")
def _tests_do_not_wait_for_the_disk(dbapi_connection, _record) -> None:
    """2026-11-21 (使用者：整個 UI 非常慢; checking that fix, 49 page tests ran for 14 minutes and more): the tests' temp folder
    is on D: (D-053, a hard disk) where one durable commit takes 280 ms - C: takes 2.6 ms (100 commits: 28 s against 0.26 s,
    measured) - and building the app in a test is about 100 commits: 30 s. A test's database is thrown away; it does not wait
    for the disk (D: with this: 8 ms a commit). Tests only: the site's database keeps its own settings."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.execute("PRAGMA synchronous=OFF")


@pytest.fixture(autouse=True)
def _pool_sources_without_the_live_batches(monkeypatch):
    """2026-11-21 (使用者：整個 UI 非常慢; the check of that fix ran 14 minutes, one test stuck for ten): a pool built in a
    test's fresh folder has no sources snapshot, so it asked for the rules of EVERY batch (research/pool.py
    ``_every_batch_once``) - and dozens of the daily batches read the LIVE ``instance/research`` (the registry, 41,000
    report files, the models): minutes in every test process, and a test reading the real data. No test names a rule by
    the batch it came from; the one that checks the rebuild (tests/test_research_pool_sources.py) replaces
    ``_every_batch`` itself."""
    from quant_platform.research import pool

    monkeypatch.setattr(pool, "_every_batch_once", lambda: {})
