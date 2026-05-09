"""
System data collectors.
All commands are looked up and executed via CommandRegistry,
which reads them from the OS-appropriate markdown file in agents/shared/commands/.
No shell commands are hardcoded here.
"""

from __future__ import annotations

import sys
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
    files_over_1gb: str = ""
    xcode_derived: str = ""     # macOS only
    apt_cache: str = ""         # Linux only
    journal_logs: str = ""      # Linux only


def collect_storage(os_name: str) -> StorageData:
    reg = CommandRegistry(os_name)
    data = StorageData(os_name=os_name)

    data.volumes       = reg.run("storage.disk_overview")
    data.largest_dirs  = reg.run("storage.largest_dirs")
    data.cache_size    = reg.run("storage.user_cache_size")
    data.cache_breakdown = reg.run("storage.user_cache_breakdown")
    data.trash_size    = reg.run("storage.trash_size")
    data.downloads_old = reg.run("storage.downloads_old")
    data.files_over_1gb = reg.run("storage.files_over_1gb")

    if os_name == "macos":
        data.xcode_derived = reg.run("storage.xcode_derived_data")

    if os_name == "linux":
        data.apt_cache    = reg.run("storage.apt_cache_size")
        data.journal_logs = reg.run("storage.journal_log_size")

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
    power_log: str = ""         # macOS only
    charge_level: str = ""      # Linux only
    charge_status: str = ""     # Linux only
    design_capacity: str = ""   # Linux only
    current_capacity: str = ""  # Linux only
    power_plan: str = ""        # Windows only


def collect_battery(os_name: str) -> BatteryData:
    reg = CommandRegistry(os_name)
    data = BatteryData(os_name=os_name)

    data.energy_consumers = reg.run("battery.top_energy_consumers")

    if os_name == "macos":
        data.quick_status   = reg.run("battery.quick_status")
        data.health_detail  = reg.run("battery.health_summary")
        data.power_settings = reg.run("battery.power_settings")
        data.power_log      = reg.run("battery.power_log")
        data.bluetooth      = reg.run("battery.bluetooth_status")
        data.wifi           = reg.run("battery.wifi_status")

    elif os_name == "linux":
        data.charge_level    = reg.run("battery.charge_level")
        data.charge_status   = reg.run("battery.charge_status")
        data.design_capacity = reg.run("battery.design_capacity")
        data.current_capacity = reg.run("battery.current_max_capacity")
        data.health_detail   = reg.run("battery.upower_detail")

    elif os_name == "windows":
        data.quick_status = reg.run("battery.quick_status")
        data.power_plan   = reg.run("battery.power_plan_current")

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
    # Optional extended
    memory_summary: str = ""    # macOS
    total_ram: str = ""         # macOS
    gpu_usage: str = ""         # Windows
    not_responding: str = ""    # Windows
    temperatures: str = ""      # Linux


def collect_health(os_name: str) -> HealthData:
    reg = CommandRegistry(os_name)
    data = HealthData(os_name=os_name)

    # Common across all OSes
    data.cpu_overview  = reg.run("health.cpu_overview")
    data.load_avg      = reg.run("health.load_avg")
    data.cpu_model     = reg.run("health.cpu_model")
    data.core_count    = reg.run("health.cpu_core_count")
    data.top_cpu_procs = reg.run("health.top_cpu_procs")
    data.top_mem_procs = reg.run("health.top_mem_procs")
    data.zombie_procs  = reg.run("health.zombie_procs")
    data.gpu_info      = reg.run("health.gpu_info")
    data.uptime        = reg.run("health.uptime")

    if os_name == "macos":
        data.memory_summary = reg.run("health.memory_summary")
        data.total_ram      = reg.run("health.total_ram")
        data.memory_overview = reg.run("health.memory_overview")
        data.swap_usage     = data.memory_summary   # summary includes swap line

    elif os_name == "linux":
        data.memory_overview = reg.run("health.memory_overview")
        data.swap_usage      = reg.run("health.swap_usage")
        data.temperatures    = reg.run("health.temperatures")

    elif os_name == "windows":
        data.memory_overview = reg.run("health.memory_overview")
        data.swap_usage      = reg.run("health.swap_usage")
        data.gpu_usage       = reg.run("health.gpu_usage")
        data.not_responding  = reg.run("health.not_responding_procs")

    return data
