"""Host memory and leaked kernel process objects (System page, Today alert).

Found 2026-10-01: on this host every exited process stayed behind as a kernel object (about 130 KB
each: address-space descriptors, token, page tables) because a driver kept a reference. After five
days 106,000 of them held about 13 GB and the daily workflow slowed fourfold. The page shows the
available memory and how many process objects exist beyond the live processes, so the leak is seen
before it slows anything. Windows only; elsewhere nothing is shown.
"""

from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes
from dataclasses import dataclass

BYTES_PER_ZOMBIE = 130 * 1024
ZOMBIE_WARN = 20_000    # about 2.6 GB
ZOMBIE_ALERT = 50_000   # about 6.5 GB: worth a reboot
LOW_MEMORY = 0.10       # available share of physical memory


@dataclass(frozen=True)
class HostMemory:
    total: int
    available: int
    nonpaged_pool: int
    process_objects: int
    processes: int

    @property
    def zombies(self) -> int:
        return max(0, self.process_objects - self.processes)

    @property
    def available_ratio(self) -> float:
        return self.available / self.total if self.total else 0.0


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def _pool_tags() -> tuple[int, int]:
    """Live 'Proc' pool allocations (process objects) and nonpaged pool bytes, like poolmon."""
    query = ctypes.WinDLL("ntdll").NtQuerySystemInformation
    query.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    query.restype = ctypes.c_long
    size = 1 << 20
    while True:
        buffer = ctypes.create_string_buffer(size)
        returned = ctypes.c_ulong(0)
        status = query(22, buffer, size, ctypes.byref(returned))   # SystemPoolTagInformation
        if status == 0xC0000004 - (1 << 32) and size < (1 << 26):  # STATUS_INFO_LENGTH_MISMATCH
            size = max(size * 2, returned.value + 4096)
            continue
        if status != 0:
            raise OSError(f"NtQuerySystemInformation 0x{status & 0xFFFFFFFF:08X}")
        break
    raw = buffer.raw
    entry = struct.Struct("<4sIIxxxxQIIQ")
    process_objects = nonpaged = 0
    for index in range(struct.unpack_from("<I", raw, 0)[0]):
        tag, _pa, _pf, _pu, allocs, frees, used = entry.unpack_from(raw, 8 + index * entry.size)
        nonpaged += used
        if tag == b"Proc":
            process_objects = allocs - frees
    return process_objects, nonpaged


def _process_count() -> int:
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(_ProcessEntry)
    count = 0
    ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
    while ok:
        count += 1
        ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    kernel32.CloseHandle(snapshot)
    return count


def read_host_memory() -> HostMemory | None:
    if os.name != "nt":
        return None
    try:
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        process_objects, nonpaged = _pool_tags()
        return HostMemory(status.ullTotalPhys, status.ullAvailPhys, nonpaged, process_objects, _process_count())
    except (AttributeError, OSError, struct.error):
        return None


def _gb(value: float) -> str:
    return f"{value / 2**30:.1f}"


def memory_tile(memory: HostMemory | None) -> dict[str, str]:
    if memory is None:
        return {"label": "主機記憶體", "state": "無法讀取", "badge": "", "detail": "只在 Windows 主機上檢查"}
    zombies = memory.zombies
    if zombies >= ZOMBIE_ALERT or memory.available_ratio < LOW_MEMORY:
        state, badge = "建議重開機", "badge--bad"
    elif zombies >= ZOMBIE_WARN or memory.available_ratio < 0.2:
        state, badge = "偏高", "badge--warn"
    else:
        state, badge = "正常", "badge--ok"
    return {
        "label": "主機記憶體", "state": state, "badge": badge,
        "detail": f"可用 {_gb(memory.available)}／{_gb(memory.total)} GB・殭屍程序 {zombies:,} 個"
                  f"（約 {_gb(zombies * BYTES_PER_ZOMBIE)} GB）",
    }


def memory_alert(memory: HostMemory | None) -> str | None:
    """One line for the Today page when the host is about to slow everything down."""
    if memory is None:
        return None
    if memory.zombies >= ZOMBIE_ALERT:
        return f"主機記憶體外洩：殭屍程序 {memory.zombies:,} 個（約 {_gb(memory.zombies * BYTES_PER_ZOMBIE)} GB），建議重開機"
    if memory.available_ratio < LOW_MEMORY:
        return f"主機可用記憶體只剩 {_gb(memory.available)} GB"
    return None
