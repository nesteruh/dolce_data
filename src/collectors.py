"""
System data collectors.
All commands are looked up and executed via CommandRegistry,
which reads them from the OS-appropriate markdown file in agents/shared/commands/.
No shell commands are hardcoded here.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from src.command_registry import CommandRegistry


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
    xcode_derived: str = ""     # macOS only
    apt_cache: str = ""         # Linux only
    journal_logs: str = ""      # Linux only


def collect_storage(os_name: str) -> StorageData:
    reg = CommandRegistry(os_name)
    data = StorageData(os_name=os_name)

    tasks: dict[str, str] = {
        "volumes":        "storage.disk_overview",
        "largest_dirs":   "storage.largest_dirs",
        "cache_size":     "storage.user_cache_size",
        "cache_breakdown": "storage.user_cache_breakdown",
        "trash_size":     "storage.trash_size",
        "downloads_old":  "storage.downloads_old",
    }
    if os_name == "macos":
        tasks["xcode_derived"] = "storage.xcode_derived_data"
    if os_name == "linux":
        tasks["apt_cache"]    = "storage.apt_cache_size"
        tasks["journal_logs"] = "storage.journal_log_size"

    for field, value in _run_parallel(reg, tasks, timeouts={"largest_dirs": 12}).items():
        setattr(data, field, value)

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
