import os

import pytest

from quant_platform.application import host_memory
from quant_platform.application.host_memory import HostMemory, memory_alert, memory_tile, read_host_memory

GB = 2**30


def test_tile_and_alert_by_hand():
    healthy = HostMemory(total=31 * GB, available=20 * GB, nonpaged_pool=GB, process_objects=1_727, processes=297)
    assert healthy.zombies == 1_430
    assert memory_tile(healthy) == {
        "label": "主機記憶體", "state": "正常", "badge": "badge--ok",
        "detail": "可用 20.0／31.0 GB・殭屍程序 1,430 個（約 0.2 GB）",            # 1,430 × 130 KB
    }
    assert memory_alert(healthy) is None

    leaking = HostMemory(total=31 * GB, available=3 * GB, nonpaged_pool=6 * GB, process_objects=106_401, processes=348)
    assert memory_tile(leaking)["state"] == "建議重開機" and "106,053" in memory_tile(leaking)["detail"]
    assert memory_alert(leaking) == "主機記憶體外洩：殭屍程序 106,053 個（約 13.1 GB），建議重開機"

    warm = HostMemory(total=31 * GB, available=12 * GB, nonpaged_pool=GB, process_objects=25_300, processes=300)
    assert memory_tile(warm)["state"] == "偏高" and memory_alert(warm) is None
    low = HostMemory(total=31 * GB, available=2 * GB, nonpaged_pool=GB, process_objects=500, processes=300)
    assert memory_alert(low) == "主機可用記憶體只剩 2.0 GB"
    assert memory_tile(None)["state"] == "無法讀取"


@pytest.mark.skipif(os.name != "nt", reason="reads Windows kernel counters")
def test_reads_this_windows_host():
    memory = read_host_memory()
    assert memory is not None
    assert 0 < memory.available <= memory.total and memory.processes > 10
    assert memory.process_objects >= memory.processes - 5   # every live process is a kernel object


def test_system_page_and_today_alert(tmp_path, monkeypatch):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    monkeypatch.setattr(container.daily_market_data_pipeline, "is_fresh", lambda *args, **kwargs: True)
    client = create_app(container).test_client()
    monkeypatch.setattr(host_memory, "read_host_memory", lambda: HostMemory(31 * GB, 3 * GB, 6 * GB, 106_401, 348))

    assert "建議重開機" in client.get("/system").get_data(as_text=True)
    today = client.get("/").get_data(as_text=True)
    assert 'class="alert-line"' in today and "殭屍程序 106,053 個" in today
