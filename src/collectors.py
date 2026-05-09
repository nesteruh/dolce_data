"""
System data collectors.
All data is collected via Python standard library (psutil, pathlib, os, platform).
Subprocess is used only where Python has no equivalent:
  macOS  — ioreg, pmset, system_profiler, networksetup
  Linux  — upower
  Windows — powercfg (power plan), PowerShell (GPU / not-responding processes)
"""

from __future__ import annotations

import datetime
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import psutil


def _safe_run(reg: CommandRegistry, cmd_id: str, **params) -> str:
    """Run a CMD_ID and return its output, or empty string if the ID is missing."""
    try:
        return reg.run(cmd_id, **params)
    except (ValueError, RuntimeError):
        return ""


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


def _dir_size(path: Path) -> int:
    """Recursively sum bytes for a directory tree, silently skipping errors."""
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    total += _dir_size(Path(entry.path))
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except (PermissionError, OSError):
                pass
    except (PermissionError, OSError):
        pass
    return total


def _mb(n: int) -> str:
    return f"{n / 1024**2:.2f} MB"

def _gb(n: int) -> str:
    return f"{n / 1024**3:.2f} GB"

def _fmt_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024**3:.1f}G"
    if n >= 1024 ** 2:
        return f"{n / 1024**2:.1f}M"
    if n >= 1024:
        return f"{n / 1024:.1f}K"
    return f"{n}B"


def _sh(cmd: list[str], timeout: int = 8) -> str:
    """Run a subprocess; return stdout or 'ERROR: ...' string."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = r.stdout.strip()
        if r.returncode != 0 and not out:
            return f"ERROR: {r.stderr.strip() or 'exit ' + str(r.returncode)}"
        return out
    except subprocess.TimeoutExpired:
        return f"ERROR: timed out after {timeout}s"
    except FileNotFoundError:
        return "ERROR: command not found"
    except Exception as exc:
        return f"ERROR: {exc}"


def _ps(cmd: str, timeout: int = 8) -> str:
    """Run a PowerShell command on Windows."""
    return _sh(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
        timeout=timeout,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Safe deletables — sizes computed in Python
# ─────────────────────────────────────────────────────────────────────────────

def _get_path_size(path: str) -> tuple[str, bool]:
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        return ("not found", False)
    return (_fmt_bytes(_dir_size(Path(expanded))), True)


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
    with ThreadPoolExecutor(max_workers=len(MACOS_SAFE_DELETABLES)) as pool:
        futures = {
            pool.submit(_get_path_size, path): (desc, path, cmd)
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


_SKIP_DIR_SCAN = {"AppData", "Library", ".cache"}


def _volumes() -> str:
    lines = [f"{'Device':<16}{'Total':<12}{'Used':<12}{'Free':<12}Use%"]
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
            lines.append(
                f"{part.device[:15]:<16}{_gb(u.total):<12}{_gb(u.used):<12}"
                f"{_gb(u.free):<12}{u.percent:.1f}%"
            )
        except (PermissionError, OSError):
            pass
    return "\n".join(lines)


def _largest_dirs() -> str:
    """Size of every immediate subdirectory in home, sorted largest first."""
    home = Path.home()
    results = []
    try:
        for entry in os.scandir(home):
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name in _SKIP_DIR_SCAN:
                continue
            results.append((entry.name, _dir_size(Path(entry.path))))
    except (PermissionError, OSError):
        pass
    results.sort(key=lambda x: x[1], reverse=True)
    lines = ["Directory                    Size"]
    for name, size in results[:15]:
        lines.append(f"{name:<30}{_gb(size)}")
    return "\n".join(lines)


def _trash_size(os_name: str) -> str:
    if os_name == "windows":
        trash = Path("C:\\$Recycle.Bin")
    elif os_name == "macos":
        trash = Path.home() / ".Trash"
    else:
        trash = Path.home() / ".local/share/Trash/files"
    if not trash.exists():
        return "0 MB (empty or inaccessible)"
    return _mb(_dir_size(trash))


def _library_breakdown() -> str:
    lib = Path.home() / "Library"
    targets = [
        ("Caches",              lib / "Caches"),
        ("Application Support", lib / "Application Support"),
        ("Developer",           lib / "Developer"),
        ("Logs",                lib / "Logs"),
    ]
    lines = ["Area                       Size"]
    for name, path in targets:
        if path.exists():
            lines.append(f"{name:<27}{_gb(_dir_size(path))}")
    return "\n".join(lines)


def _downloads_large() -> str:
    downloads = Path.home() / "Downloads"
    if not downloads.exists():
        return ""
    MIN = 50 * 1024 * 1024
    big: list[tuple[str, int]] = []
    try:
        for entry in os.scandir(downloads):
            if entry.is_file(follow_symlinks=False):
                try:
                    sz = entry.stat(follow_symlinks=False).st_size
                    if sz >= MIN:
                        big.append((entry.name, sz))
                except OSError:
                    pass
    except (PermissionError, OSError):
        pass
    if not big:
        return ""
    big.sort(key=lambda x: x[1], reverse=True)
    return "\n".join(f"{_mb(sz):<14}{name}" for name, sz in big[:20])


def collect_storage(os_name: str) -> StorageData:
    data = StorageData(os_name=os_name)

    with ThreadPoolExecutor(max_workers=7) as ex:
        fv   = ex.submit(_volumes)
        fd   = ex.submit(_largest_dirs)
        ft   = ex.submit(_trash_size, os_name)
        f_sd = ex.submit(collect_safe_deletables, os_name)
        flib = ex.submit(_library_breakdown) if os_name == "macos" else None
        fdl  = ex.submit(_downloads_large)   if os_name == "macos" else None
        fapt = ex.submit(
            lambda: _mb(_dir_size(Path("/var/cache/apt/archives")))
        ) if os_name == "linux" else None
        fjnl = ex.submit(
            lambda: _mb(_dir_size(Path("/var/log/journal")))
        ) if os_name == "linux" else None

        data.volumes         = fv.result()
        data.largest_dirs    = fd.result()
        data.trash_size      = ft.result()
        data.safe_deletables = f_sd.result()

        if flib:
            data.library_breakdown = flib.result()
        if fdl:
            data.downloads_large = fdl.result()
        if fapt:
            data.apt_cache = fapt.result()
        if fjnl:
            data.journal_logs = fjnl.result()

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


def _battery_quick_status() -> str:
    batt = psutil.sensors_battery()
    if batt is None:
        return "No battery detected (desktop or driver not reporting)"
    status = "Charging" if batt.power_plugged else "Discharging"
    secs = batt.secsleft
    time_left = ""
    if secs not in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED) and secs > 0:
        h, m = divmod(secs // 60, 60)
        time_left = f", {h}h {m:02d}m remaining"
    return f"{batt.percent:.1f}% ({status}{time_left})"


def _energy_consumers() -> str:
    """Sample top CPU consumers over a brief interval."""
    for p in psutil.process_iter():
        try:
            p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(0.8)
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent"]):
        try:
            cpu = p.info["cpu_percent"] or 0.0
            if cpu > 0:
                procs.append((p.info["name"] or "?", cpu))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x[1], reverse=True)
    lines = [f"{'Process':<35} CPU%"]
    for name, cpu in procs[:10]:
        lines.append(f"{name[:34]:<35} {cpu:.1f}%")
    return "\n".join(lines)


def _linux_power_supply() -> dict[str, str]:
    """Read /sys/class/power_supply for battery data."""
    base = Path("/sys/class/power_supply")
    if not base.exists():
        return {}
    for entry in base.iterdir():
        try:
            if (entry / "type").read_text().strip() != "Battery":
                continue
        except OSError:
            continue

        def _read(name: str) -> str:
            try:
                return (entry / name).read_text().strip()
            except OSError:
                return "(no data)"

        return {
            "charge_level":    _read("capacity") + "%",
            "charge_status":   _read("status"),
            "design_capacity": _read("charge_full_design") or _read("energy_full_design"),
            "current_capacity": _read("charge_full") or _read("energy_full"),
        }
    return {}


def _macos_battery_health() -> str:
    """Parse ioreg output and return only the key battery health fields."""
    import re
    raw = _sh(["ioreg", "-rn", "AppleSmartBattery"])
    if raw.startswith("ERROR"):
        return raw

    def _val(key: str) -> str:
        m = re.search(rf'"{key}"\s*=\s*(\S+)', raw)
        return m.group(1).strip('"') if m else "?"

    cycle_count  = _val("CycleCount")
    design_cap   = _val("DesignCapacity")
    max_cap      = _val("AppleRawMaxCapacity")
    temperature  = _val("Temperature")
    is_charging  = _val("IsCharging")
    fully_charged = _val("FullyCharged")

    try:
        health = f"{int(max_cap) / int(design_cap) * 100:.1f}%"
    except (ValueError, ZeroDivisionError):
        health = "?"

    try:
        temp_c = f"{int(temperature) / 100:.1f}°C"
    except ValueError:
        temp_c = temperature

    return "\n".join([
        f"Cycle count:     {cycle_count} / 1000",
        f"Design capacity: {design_cap} mAh",
        f"Max capacity:    {max_cap} mAh",
        f"Battery health:  {health}",
        f"Temperature:     {temp_c}",
        f"Is charging:     {is_charging}",
        f"Fully charged:   {fully_charged}",
    ])


def collect_battery(os_name: str) -> BatteryData:
    data = BatteryData(os_name=os_name)

    with ThreadPoolExecutor(max_workers=6) as ex:
        fe = ex.submit(_energy_consumers)

        if os_name == "macos":
            fqs   = ex.submit(_battery_quick_status)
            fhd   = ex.submit(_macos_battery_health)
            fps   = ex.submit(_sh, ["pmset", "-g"])
            fbt   = ex.submit(_sh, ["system_profiler", "SPBluetoothDataType", "-detailLevel", "mini"])
            fwifi = ex.submit(_sh, ["networksetup", "-getairportnetwork", "en0"])

            data.quick_status   = fqs.result()
            data.health_detail  = fhd.result()
            data.power_settings = fps.result()
            data.bluetooth      = fbt.result()
            data.wifi           = fwifi.result()

        elif os_name == "linux":
            fps_f = ex.submit(_linux_power_supply)
            fhd   = ex.submit(_sh, ["upower", "-i",
                                    "/org/freedesktop/UPower/devices/battery_BAT0"])
            ps = fps_f.result()
            data.charge_level     = ps.get("charge_level", "(no data)")
            data.charge_status    = ps.get("charge_status", "(no data)")
            data.design_capacity  = ps.get("design_capacity", "(no data)")
            data.current_capacity = ps.get("current_capacity", "(no data)")
            data.health_detail    = fhd.result()

        elif os_name == "windows":
            fqs = ex.submit(_battery_quick_status)
            fpp = ex.submit(_ps, "powercfg /getactivescheme")

            data.quick_status = fqs.result()
            data.power_plan   = fpp.result()

        data.energy_consumers = fe.result()

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


def _cpu_overview() -> str:
    pct = psutil.cpu_percent(interval=0.5)
    freq = psutil.cpu_freq()
    freq_str = f"{freq.current:.0f} MHz" if freq else "N/A"
    return f"{pct:.1f}% used, {freq_str}"


def _load_avg() -> str:
    try:
        one, five, fifteen = psutil.getloadavg()
        cores = psutil.cpu_count(logical=True) or 1
        return f"{one:.2f} / {five:.2f} / {fifteen:.2f}  (logical cores: {cores})"
    except AttributeError:
        pct = psutil.cpu_percent(interval=0.3)
        return f"N/A on Windows — current CPU utilisation: {pct:.1f}%"


def _cpu_model() -> str:
    return platform.processor() or platform.machine() or "(unknown)"


def _core_count() -> str:
    logical = psutil.cpu_count(logical=True)
    physical = psutil.cpu_count(logical=False)
    return f"{physical} physical, {logical} logical"


def _sample_cpu_procs(top_n: int = 15) -> str:
    for p in psutil.process_iter():
        try:
            p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(1.0)
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "username"]):
        try:
            procs.append((
                p.info["pid"],
                p.info["name"] or "?",
                p.info["cpu_percent"] or 0.0,
                p.info["username"] or "",
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x[2], reverse=True)
    lines = [f"{'PID':<8}{'Name':<35}{'CPU%':<8}User"]
    for pid, name, cpu, user in procs[:top_n]:
        lines.append(f"{pid:<8}{name[:34]:<35}{cpu:<8.1f}{user[:20]}")
    return "\n".join(lines)


def _sample_mem_procs(top_n: int = 15) -> str:
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "username"]):
        try:
            rss = p.info["memory_info"].rss if p.info["memory_info"] else 0
            procs.append((p.info["pid"], p.info["name"] or "?", rss, p.info["username"] or ""))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x[2], reverse=True)
    lines = [f"{'PID':<8}{'Name':<35}{'RSS':<14}User"]
    for pid, name, rss, user in procs[:top_n]:
        lines.append(f"{pid:<8}{name[:34]:<35}{_mb(rss):<14}{user[:20]}")
    return "\n".join(lines)


def _zombie_procs() -> str:
    zombies = []
    for p in psutil.process_iter(["pid", "name", "status"]):
        try:
            if p.info["status"] == psutil.STATUS_ZOMBIE:
                zombies.append(f"{p.info['pid']} {p.info['name']}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return "\n".join(zombies) if zombies else "(none)"


def _memory_overview() -> str:
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    return (
        f"RAM:  {_gb(vm.used)} used / {_gb(vm.total)} total  ({vm.percent:.1f}%)\n"
        f"Swap: {_gb(sm.used)} used / {_gb(sm.total)} total  ({sm.percent:.1f}%)"
    )


def _uptime() -> str:
    delta = int(datetime.datetime.now().timestamp() - psutil.boot_time())
    h, rem = divmod(delta, 3600)
    m = rem // 60
    return f"{h}h {m}m since last boot"


def _gpu_info_windows() -> str:
    return _ps(
        "(Get-WmiObject Win32_VideoController | "
        "Select-Object Name,AdapterRAM,DriverVersion | "
        "Format-List | Out-String).Trim()"
    )


def _gpu_info_macos() -> str:
    return _sh(["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"])


def _gpu_info_linux() -> str:
    out = _sh(["nvidia-smi", "--query-gpu=name,memory.total,utilization.gpu",
               "--format=csv,noheader"])
    if not out.startswith("ERROR"):
        return out
    return _sh(["glxinfo", "-B"])


def _not_responding_windows() -> str:
    return _ps(
        "(Get-Process | Where-Object { $_.Responding -eq $false } | "
        "Select-Object Id,Name | Format-Table -AutoSize | Out-String).Trim()"
    )


def _temperatures_linux() -> str:
    try:
        temps = psutil.sensors_temperatures()
        if not temps:
            return "(no temperature sensors found)"
        lines = []
        for sensor_name, entries in temps.items():
            for entry in entries:
                label = entry.label or "core"
                lines.append(f"{sensor_name}/{label}: {entry.current:.1f}°C")
        return "\n".join(lines[:20])
    except AttributeError:
        return "(not supported on this platform)"


def collect_health(os_name: str) -> HealthData:
    data = HealthData(os_name=os_name)

    _gpu_fn = (
        _gpu_info_windows if os_name == "windows" else
        _gpu_info_macos   if os_name == "macos"   else
        _gpu_info_linux
    )

    with ThreadPoolExecutor(max_workers=10) as ex:
        f_cpu_ov = ex.submit(_cpu_overview)
        f_load   = ex.submit(_load_avg)
        f_model  = ex.submit(_cpu_model)
        f_cores  = ex.submit(_core_count)
        f_cpu_p  = ex.submit(_sample_cpu_procs)
        f_mem_p  = ex.submit(_sample_mem_procs)
        f_zombie = ex.submit(_zombie_procs)
        f_mem_ov = ex.submit(_memory_overview)
        f_uptime = ex.submit(_uptime)
        f_gpu    = ex.submit(_gpu_fn)
        f_nr     = ex.submit(_not_responding_windows) if os_name == "windows" else None
        f_temp   = ex.submit(_temperatures_linux)     if os_name == "linux"   else None

        data.cpu_overview    = f_cpu_ov.result()
        data.load_avg        = f_load.result()
        data.cpu_model       = f_model.result()
        data.core_count      = f_cores.result()
        data.top_cpu_procs   = f_cpu_p.result()
        data.top_mem_procs   = f_mem_p.result()
        data.zombie_procs    = f_zombie.result()
        data.memory_overview = f_mem_ov.result()
        data.uptime          = f_uptime.result()
        data.gpu_info        = f_gpu.result()

        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
        data.total_ram      = _gb(vm.total)
        data.memory_summary = data.memory_overview
        data.swap_usage     = f"{_gb(sm.used)} used / {_gb(sm.total)} total ({sm.percent:.1f}%)"

        if os_name == "windows":
            data.gpu_usage      = data.gpu_info
            data.not_responding = f_nr.result()
        elif os_name == "linux":
            data.temperatures = f_temp.result()

    return data


# ─────────────────────────────────────────────────────────────────────────────
# Network
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NetworkData:
    os_name: str
    interfaces: str = ""
    routing_table: str = ""
    active_connections: str = ""
    listening_ports: str = ""
    bandwidth: str = ""
    dns_config: str = ""
    firewall_status: str = ""
    vpn_detection: str = ""
    # OS-specific
    wifi_status: str = ""       # macOS
    proxy_config: str = ""      # macOS
    firewall_apps: str = ""     # macOS
    firewall_iptables: str = "" # Linux
    firewall_rules: str = ""    # Windows
    connections_named: str = "" # Windows


def collect_network(os_name: str) -> NetworkData:
    reg = CommandRegistry(os_name)
    data = NetworkData(os_name=os_name)

    # Common across all OSes
    data.interfaces        = _safe_run(reg, "network.interfaces")
    data.routing_table     = _safe_run(reg, "network.routing_table")
    data.active_connections = _safe_run(reg, "network.active_connections")
    data.listening_ports   = _safe_run(reg, "network.listening_ports")
    data.dns_config        = _safe_run(reg, "network.dns_config")
    data.vpn_detection     = _safe_run(reg, "network.vpn_detection")

    if os_name == "macos":
        data.bandwidth      = _safe_run(reg, "network.bandwidth_by_process")
        data.firewall_status = _safe_run(reg, "network.firewall_status")
        data.firewall_apps  = _safe_run(reg, "network.firewall_apps")
        data.wifi_status    = _safe_run(reg, "network.wifi_status")
        data.proxy_config   = _safe_run(reg, "network.proxy_config")

    elif os_name == "linux":
        data.bandwidth        = _safe_run(reg, "network.bandwidth_by_process")
        data.firewall_status  = _safe_run(reg, "network.firewall_status_ufw")
        data.firewall_iptables = _safe_run(reg, "network.firewall_status_iptables")

    elif os_name == "windows":
        data.bandwidth         = _safe_run(reg, "network.bandwidth_by_adapter")
        data.firewall_status   = _safe_run(reg, "network.firewall_status")
        data.firewall_rules    = _safe_run(reg, "network.firewall_rules_active")
        data.connections_named = _safe_run(reg, "network.connections_with_process_names")

    return data


# ─────────────────────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StartupData:
    os_name: str
    user_items: str = ""
    system_items: str = ""
    running_items: str = ""
    boot_time_summary: str = ""
    boot_time_per_service: str = ""
    # OS-specific
    system_daemons: str = ""    # macOS
    scheduled_tasks: str = ""   # Windows
    auto_services: str = ""     # Windows


def collect_startup(os_name: str) -> StartupData:
    reg = CommandRegistry(os_name)
    data = StartupData(os_name=os_name)

    if os_name == "macos":
        data.user_items    = _safe_run(reg, "startup.list_user_agents")
        data.system_items  = _safe_run(reg, "startup.list_system_agents")
        data.system_daemons = _safe_run(reg, "startup.list_system_daemons")
        data.running_items = _safe_run(reg, "startup.list_running_noapple")

    elif os_name == "linux":
        data.user_items           = _safe_run(reg, "startup.list_user_autostart")
        data.system_items         = _safe_run(reg, "startup.list_enabled_services")
        data.running_items        = _safe_run(reg, "startup.list_running_services")
        data.boot_time_summary    = _safe_run(reg, "startup.boot_time_summary")
        data.boot_time_per_service = _safe_run(reg, "startup.boot_time_per_service")

    elif os_name == "windows":
        data.user_items       = _safe_run(reg, "startup.list_registry_run_user")
        data.system_items     = _safe_run(reg, "startup.list_registry_run_system")
        data.scheduled_tasks  = _safe_run(reg, "startup.list_scheduled_tasks")
        data.auto_services    = _safe_run(reg, "startup.list_auto_services")

    return data
