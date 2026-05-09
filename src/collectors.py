"""
System data collectors.
All commands are looked up and executed via CommandRegistry,
which reads them from the OS-appropriate markdown file in agents/shared/commands/.
No shell commands are hardcoded here.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from src.command_registry import CommandRegistry


# ─────────────────────────────────────────────────────────────────────────────
# Safe-to-delete catalogue (macOS)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SafeDeletableItem:
    description: str
    path: str
    size: str
    delete_cmd: str
    exists: bool


MACOS_SAFE_DELETABLES: list[tuple[str, str, str]] = [
    ("User app caches",            "~/Library/Caches",                                         "rm -rf ~/Library/Caches/*"),
    ("Xcode derived data",         "~/Library/Developer/Xcode/DerivedData",                    "rm -rf ~/Library/Developer/Xcode/DerivedData"),
    ("Xcode simulator caches",     "~/Library/Developer/CoreSimulator/Caches",                 "rm -rf ~/Library/Developer/CoreSimulator/Caches"),
    ("Xcode archives",             "~/Library/Developer/Xcode/Archives",                       "rm -rf ~/Library/Developer/Xcode/Archives"),
    ("iOS device support files",   "~/Library/Developer/Xcode/iOS DeviceSupport",              "rm -rf ~/Library/Developer/Xcode/iOS\\ DeviceSupport"),
    ("watchOS device support",     "~/Library/Developer/Xcode/watchOS DeviceSupport",          "rm -rf ~/Library/Developer/Xcode/watchOS\\ DeviceSupport"),
    ("User logs",                  "~/Library/Logs",                                           "rm -rf ~/Library/Logs/*"),
    ("Crash reports",              "~/Library/Logs/DiagnosticReports",                        "rm -rf ~/Library/Logs/DiagnosticReports/*"),
    ("Saved application state",    "~/Library/Saved Application State",                       "rm -rf ~/Library/Saved\\ Application\\ State/*"),
    ("Trash",                      "~/.Trash",                                                 "Empty Trash in Finder"),
    ("npm cache",                  "~/.npm",                                                   "npm cache clean --force"),
    ("Homebrew cache",             "~/Library/Caches/Homebrew",                               "brew cleanup"),
    ("pip cache",                  "~/.cache/pip",                                            "pip cache purge"),
    ("Yarn cache",                 "~/Library/Caches/Yarn",                                   "yarn cache clean"),
    ("CocoaPods cache",            "~/Library/Caches/CocoaPods",                              "pod cache clean --all"),
    ("Gradle caches",              "~/.gradle/caches",                                        "rm -rf ~/.gradle/caches"),
    ("Maven repository",           "~/.m2/repository",                                        "rm -rf ~/.m2/repository"),
    ("Docker data",                "~/.docker",                                                "docker system prune -a"),
]


def _get_path_size(reg: CommandRegistry, path: str) -> tuple[str, bool]:
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        return ("not found", False)
    result = reg.run("storage.path_size", path=expanded, timeout=6)
    if result.startswith("ERROR:"):
        return ("error", True)
    size = result.split()[0] if result.strip() else "0B"
    return (size, True)


def _parse_size_bytes(item: SafeDeletableItem) -> float:
    if not item.exists:
        return -1.0
    s = item.size.upper()
    try:
        if s.endswith("G"):
            return float(s[:-1]) * 1024 ** 3
        if s.endswith("M"):
            return float(s[:-1]) * 1024 ** 2
        if s.endswith("K"):
            return float(s[:-1]) * 1024
        if s.endswith("B"):
            return float(s[:-1])
        return float(s)
    except ValueError:
        return 0.0


def collect_safe_deletables(os_name: str) -> list[SafeDeletableItem]:
    if os_name != "macos":
        return []
    reg = CommandRegistry(os_name)
    with ThreadPoolExecutor(max_workers=len(MACOS_SAFE_DELETABLES)) as pool:
        futures = {
            pool.submit(_get_path_size, reg, path): (desc, path, cmd)
            for desc, path, cmd in MACOS_SAFE_DELETABLES
        }
        items: list[SafeDeletableItem] = []
        for future in as_completed(futures):
            desc, path, cmd = futures[future]
            size, exists = future.result()
            items.append(SafeDeletableItem(
                description=desc, path=path,
                size=size, delete_cmd=cmd, exists=exists,
            ))
    return sorted(items, key=_parse_size_bytes, reverse=True)


def detect_os() -> str:
    """Return 'macos', 'linux', or 'windows'."""
    p = sys.platform
    if p == "darwin":
        return "macos"
    if p.startswith("linux"):
        return "linux"
    if p == "win32":
        return "windows"
    return "unknown"


def _run_parallel(
    reg: CommandRegistry,
    tasks: dict[str, str],
    timeouts: dict[str, int] | None = None,
) -> dict[str, str]:
    """
    Execute multiple reg.run() calls concurrently.

    tasks:    {field_name: cmd_id}
    timeouts: {field_name: seconds} — overrides the default 15s per command
    Returns   {field_name: result_string}
    """
    timeouts = timeouts or {}
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {
            executor.submit(reg.run, cmd_id, timeouts.get(field, 15)): field
            for field, cmd_id in tasks.items()
        }
        for future in as_completed(futures):
            field = futures[future]
            try:
                results[field] = future.result()
            except Exception as exc:
                results[field] = f"ERROR: {exc}"
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StorageData:
    os_name: str
    volumes: str = ""
    largest_dirs: str = ""
    cache_size: str = ""
    cache_breakdown: str = ""
    trash_size: str = ""
    downloads_old: str = ""
    library_breakdown: str = ""                          # macOS only
    downloads_large: str = ""                            # macOS only — large files with sizes
    apt_cache: str = ""                                  # Linux only
    journal_logs: str = ""                               # Linux only
    safe_deletables: list = field(default_factory=list)  # list[SafeDeletableItem]


def collect_storage(os_name: str) -> StorageData:
    reg = CommandRegistry(os_name)
    data = StorageData(os_name=os_name)

    tasks: dict[str, str] = {
        "volumes":          "storage.disk_overview",
        "largest_dirs":     "storage.largest_dirs",
        "trash_size":       "storage.trash_size",
    }
    if os_name == "macos":
        tasks["library_breakdown"] = "storage.area_breakdown"
        tasks["downloads_large"]   = "storage.downloads_large"
    if os_name == "linux":
        tasks["apt_cache"]    = "storage.apt_cache_size"
        tasks["journal_logs"] = "storage.journal_log_size"

    # Run shell commands and safe-deletables size scan concurrently
    with ThreadPoolExecutor(max_workers=2) as outer:
        cmd_future  = outer.submit(_run_parallel, reg, tasks, {"largest_dirs": 12})
        safe_future = outer.submit(collect_safe_deletables, os_name)
        for field_name, value in cmd_future.result().items():
            setattr(data, field_name, value)
        data.safe_deletables = safe_future.result()

    return data


# ─────────────────────────────────────────────────────────────────────────────
# Battery
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BatteryData:
    os_name: str
    quick_status: str = ""
    health_detail: str = ""
    power_settings: str = ""
    energy_consumers: str = ""
    bluetooth: str = ""         # macOS only
    wifi: str = ""              # macOS only
    charge_level: str = ""      # Linux only
    charge_status: str = ""     # Linux only
    design_capacity: str = ""   # Linux only
    current_capacity: str = ""  # Linux only
    power_plan: str = ""        # Windows only


def collect_battery(os_name: str) -> BatteryData:
    reg = CommandRegistry(os_name)
    data = BatteryData(os_name=os_name)

    tasks: dict[str, str] = {
        "energy_consumers": "battery.top_energy_consumers",
    }

    if os_name == "macos":
        tasks["quick_status"]   = "battery.quick_status"
        tasks["health_detail"]  = "battery.health_summary"
        tasks["power_settings"] = "battery.power_settings"
        tasks["bluetooth"]      = "battery.bluetooth_status"
        tasks["wifi"]           = "battery.wifi_status"

    elif os_name == "linux":
        tasks["charge_level"]    = "battery.charge_level"
        tasks["charge_status"]   = "battery.charge_status"
        tasks["design_capacity"] = "battery.design_capacity"
        tasks["current_capacity"] = "battery.current_max_capacity"
        tasks["health_detail"]   = "battery.upower_detail"

    elif os_name == "windows":
        tasks["quick_status"] = "battery.quick_status"
        tasks["power_plan"]   = "battery.power_plan_current"

    for field, value in _run_parallel(reg, tasks).items():
        setattr(data, field, value)

    return data


# ─────────────────────────────────────────────────────────────────────────────
# Health (CPU / GPU / RAM)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HealthData:
    os_name: str
    cpu_overview: str = ""
    load_avg: str = ""
    cpu_model: str = ""
    core_count: str = ""
    top_cpu_procs: str = ""
    memory_overview: str = ""
    swap_usage: str = ""
    top_mem_procs: str = ""
    zombie_procs: str = ""
    gpu_info: str = ""
    uptime: str = ""
    memory_summary: str = ""    # macOS
    total_ram: str = ""         # macOS
    gpu_usage: str = ""         # Windows
    not_responding: str = ""    # Windows
    temperatures: str = ""      # Linux


def collect_health(os_name: str) -> HealthData:
    reg = CommandRegistry(os_name)
    data = HealthData(os_name=os_name)

    tasks: dict[str, str] = {
        "cpu_overview":  "health.cpu_overview",
        "load_avg":      "health.load_avg",
        "cpu_model":     "health.cpu_model",
        "core_count":    "health.cpu_core_count",
        "top_cpu_procs": "health.top_cpu_procs",
        "top_mem_procs": "health.top_mem_procs",
        "zombie_procs":  "health.zombie_procs",
        "gpu_info":      "health.gpu_info",
        "uptime":        "health.uptime",
    }

    if os_name == "macos":
        tasks["memory_summary"]  = "health.memory_summary"
        tasks["total_ram"]       = "health.total_ram"
        tasks["memory_overview"] = "health.memory_overview"

    elif os_name == "linux":
        tasks["memory_overview"] = "health.memory_overview"
        tasks["swap_usage"]      = "health.swap_usage"
        tasks["temperatures"]    = "health.temperatures"

    elif os_name == "windows":
        tasks["memory_overview"] = "health.memory_overview"
        tasks["swap_usage"]      = "health.swap_usage"
        tasks["gpu_usage"]       = "health.gpu_usage"
        tasks["not_responding"]  = "health.not_responding_procs"

    for field, value in _run_parallel(reg, tasks).items():
        setattr(data, field, value)

    if os_name == "macos":
        data.swap_usage = data.memory_summary  # summary includes the swap line

    return data
