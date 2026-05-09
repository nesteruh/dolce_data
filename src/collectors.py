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
    ("User app caches",            "~/Library/Caches",                                        "rm -rf ~/Library/Caches/*"),
    ("Xcode derived data",         "~/Library/Developer/Xcode/DerivedData",                   "rm -rf ~/Library/Developer/Xcode/DerivedData"),
    ("Xcode simulator caches",     "~/Library/Developer/CoreSimulator/Caches",                "rm -rf ~/Library/Developer/CoreSimulator/Caches"),
    ("Xcode archives",             "~/Library/Developer/Xcode/Archives",                      "rm -rf ~/Library/Developer/Xcode/Archives"),
    ("iOS device support files",   "~/Library/Developer/Xcode/iOS DeviceSupport",             "rm -rf ~/Library/Developer/Xcode/iOS\\ DeviceSupport"),
    ("watchOS device support",     "~/Library/Developer/Xcode/watchOS DeviceSupport",         "rm -rf ~/Library/Developer/Xcode/watchOS\\ DeviceSupport"),
    ("User logs",                  "~/Library/Logs",                                          "rm -rf ~/Library/Logs/*"),
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
    stale_files: str = ""                                # large files not modified in 6+ months


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


def _stale_large_files(min_size_mb: int = 50, months: int = 6) -> str:
    """Find files larger than min_size_mb not modified in the last N months."""
    cutoff = time.time() - months * 30.44 * 24 * 3600
    min_size = min_size_mb * 1024 * 1024
    results: list[tuple[str, int, float]] = []

    def _scan(path: Path, depth: int) -> None:
        if depth > 5:
            return
        try:
            for entry in os.scandir(path):
                try:
                    if entry.is_symlink():
                        continue
                    if depth == 0 and entry.name in _SKIP_DIR_SCAN:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        _scan(Path(entry.path), depth + 1)
                    elif entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        if st.st_size >= min_size and st.st_mtime < cutoff:
                            results.append((entry.path, st.st_size, st.st_mtime))
                except (PermissionError, OSError):
                    pass
        except (PermissionError, OSError):
            pass

    _scan(Path.home(), 0)

    if not results:
        return f"(no files >{min_size_mb} MB untouched for {months}+ months)"

    results.sort(key=lambda x: x[1], reverse=True)
    lines = [f"{'Size':<12}{'Last Modified':<14}Path"]
    for path, size, mtime in results[:25]:
        dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        try:
            short = str(Path(path).relative_to(Path.home()))
        except ValueError:
            short = path
        lines.append(f"{_mb(size):<12}{dt:<14}{short[:70]}")

    total = sum(r[1] for r in results)
    lines.append(f"\nTotal: {_mb(total)} across {len(results)} files")
    return "\n".join(lines)


def collect_storage(os_name: str, stale_months: int = 6) -> StorageData:
    data = StorageData(os_name=os_name)

    with ThreadPoolExecutor(max_workers=8) as ex:
        fv     = ex.submit(_volumes)
        fd     = ex.submit(_largest_dirs)
        ft     = ex.submit(_trash_size, os_name)
        f_sd   = ex.submit(collect_safe_deletables, os_name)
        f_stale = ex.submit(_stale_large_files, 50, stale_months)
        flib   = ex.submit(_library_breakdown) if os_name == "macos" else None
        fdl    = ex.submit(_downloads_large)   if os_name == "macos" else None
        fapt   = ex.submit(
            lambda: _mb(_dir_size(Path("/var/cache/apt/archives")))
        ) if os_name == "linux" else None
        fjnl   = ex.submit(
            lambda: _mb(_dir_size(Path("/var/log/journal")))
        ) if os_name == "linux" else None

        data.volumes         = fv.result()
        data.largest_dirs    = fd.result()
        data.trash_size      = ft.result()
        data.safe_deletables = f_sd.result()
        data.stale_files     = f_stale.result()

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
    energy_consumers: str = ""   # psutil CPU% snapshot (all platforms)
    bluetooth: str = ""          # macOS only
    wifi: str = ""               # macOS only
    energy_impact: str = ""      # macOS only: Activity Monitor-style Energy Impact
    drain_history: str = ""      # macOS only: pmset log — last ~1 hour of events
    charge_level: str = ""       # Linux only
    charge_status: str = ""      # Linux only
    design_capacity: str = ""    # Linux only
    current_capacity: str = ""   # Linux only
    power_plan: str = ""         # Windows only


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
    primed: list[tuple[psutil.Process, str]] = []
    for p in psutil.process_iter(["name"]):
        try:
            p.cpu_percent(interval=None)
            primed.append((p, p.info.get("name") or "?"))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(0.8)
    procs = []
    for p, name in primed:
        try:
            cpu = p.cpu_percent(interval=None)
            if cpu > 0:
                procs.append((name, cpu))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x[1], reverse=True)
    lines = [f"{'Process':<35} CPU%"]
    for name, cpu in procs[:10]:
        lines.append(f"{name[:34]:<35} {cpu:.1f}%")
    return "\n".join(lines)


def _macos_energy_impact() -> str:
    """Per-process Energy Impact from macOS top — mirrors Activity Monitor's Energy column."""
    raw = _sh(["top", "-l", "2", "-o", "power", "-n", "10"], timeout=15)
    if raw.startswith("ERROR"):
        return raw

    # top -l 2 outputs two full samples; split on the repeated "Processes:" header
    parts = raw.split("Processes:")
    block = ("Processes:" + parts[-1]) if len(parts) > 1 else raw

    lines: list[str] = []
    in_table = False
    for line in block.splitlines():
        stripped = line.strip()
        if not in_table and "PID" in stripped and "COMMAND" in stripped:
            in_table = True
            lines.append(f"{'Process':<35} {'CPU%':<8} Energy Impact")
            continue
        if in_table and stripped:
            lines.append(stripped)

    return "\n".join(lines) if lines else "(no energy impact data)"


def _macos_battery_drain_history() -> str:
    """Last ~1 hour of battery drain events from pmset log (no sudo needed)."""
    from datetime import datetime as _dt, timedelta, timezone
    raw = _sh(["pmset", "-g", "log"], timeout=10)
    if raw.startswith("ERROR"):
        return raw

    cutoff = _dt.now(timezone.utc) - timedelta(hours=1)
    events: list[str] = []
    for line in raw.splitlines():
        try:
            ts_str = " ".join(line.split()[:3])
            ts = _dt.strptime(ts_str, "%Y-%m-%d %H:%M:%S %z")
        except (ValueError, IndexError):
            continue
        if ts < cutoff:
            continue
        if "%" in line:
            events.append(line.strip())

    if not events:
        return "(no drain events in the last hour)"
    return "\n".join(events[-20:])


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
            fei   = ex.submit(_macos_energy_impact)
            fdh   = ex.submit(_macos_battery_drain_history)

            data.quick_status   = fqs.result()
            data.health_detail  = fhd.result()
            data.power_settings = fps.result()
            data.bluetooth      = fbt.result()
            data.wifi           = fwifi.result()
            data.energy_impact  = fei.result()
            data.drain_history  = fdh.result()

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
    primed: list[tuple[psutil.Process, str, str]] = []
    for p in psutil.process_iter(["name", "username"]):
        try:
            p.cpu_percent(interval=None)
            primed.append((p, p.info.get("name") or "?", p.info.get("username") or ""))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(1.0)
    procs = []
    for p, name, user in primed:
        try:
            cpu = p.cpu_percent(interval=None)
            procs.append((p.pid, name, cpu, user))
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
        data.total_ram  = _gb(vm.total)
        data.swap_usage = f"{_gb(sm.used)} used / {_gb(sm.total)} total ({sm.percent:.1f}%)"

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


def _net_interfaces() -> str:
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    lines = [f"{'Interface':<20}{'Status':<8}{'Address':<22}Speed"]
    for iface, addr_list in addrs.items():
        stat = stats.get(iface)
        up = "UP" if stat and stat.isup else "DOWN"
        speed = f"{stat.speed}Mbps" if stat and stat.speed else ""
        for addr in addr_list:
            if addr.family.name in ("AF_INET", "AF_INET6"):
                lines.append(f"{iface[:19]:<20}{up:<8}{addr.address[:21]:<22}{speed}")
    return "\n".join(lines) if len(lines) > 1 else "(no interfaces found)"


def _net_connections() -> str:
    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        return "ERROR: access denied (run as administrator)"
    lines = [f"{'Proto':<6}{'Local':<26}{'Remote':<26}{'Status':<14}PID"]
    for c in sorted(conns, key=lambda x: x.status or "")[:25]:
        laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
        raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
        proto = "TCP" if c.type.name == "SOCK_STREAM" else "UDP"
        lines.append(f"{proto:<6}{laddr[:25]:<26}{raddr[:25]:<26}{c.status or '':<14}{c.pid or ''}")
    return "\n".join(lines)


def _net_listening() -> str:
    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        return "ERROR: access denied"
    listening = [c for c in conns if c.status == "LISTEN"]
    pid_names: dict[int, str] = {}
    for c in listening:
        if c.pid and c.pid not in pid_names:
            try:
                pid_names[c.pid] = psutil.Process(c.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pid_names[c.pid] = "?"
    lines = [f"{'Port':<8}{'Address':<20}{'PID':<7}Process"]
    for c in sorted(listening, key=lambda x: x.laddr.port if x.laddr else 0)[:25]:
        port = c.laddr.port if c.laddr else 0
        addr = c.laddr.ip if c.laddr else ""
        name = pid_names.get(c.pid, "?") if c.pid else ""
        lines.append(f"{port:<8}{addr:<20}{c.pid or '':<7}{name}")
    return "\n".join(lines)


def _net_bandwidth() -> str:
    io = psutil.net_io_counters(pernic=True)
    lines = [f"{'Interface':<20}{'Sent':<14}{'Received':<14}PktsSent / PktsRecv"]
    for iface, c in sorted(io.items()):
        lines.append(
            f"{iface[:19]:<20}{_mb(c.bytes_sent):<14}{_mb(c.bytes_recv):<14}"
            f"{c.packets_sent} / {c.packets_recv}"
        )
    return "\n".join(lines)


def _net_vpn() -> str:
    vpn_keywords = {"tun", "tap", "vpn", "wg", "utun", "ppp"}
    vpn_ifaces = [
        name for name in psutil.net_if_addrs()
        if any(kw in name.lower() for kw in vpn_keywords)
    ]
    if not vpn_ifaces:
        return "(no VPN interfaces detected)"
    stats = psutil.net_if_stats()
    return "\n".join(
        f"{iface}: {'UP' if stats.get(iface) and stats[iface].isup else 'DOWN'}"
        for iface in vpn_ifaces
    )


def collect_network(os_name: str) -> NetworkData:
    data = NetworkData(os_name=os_name)

    with ThreadPoolExecutor(max_workers=10) as ex:
        f_ifaces  = ex.submit(_net_interfaces)
        f_conns   = ex.submit(_net_connections)
        f_listen  = ex.submit(_net_listening)
        f_bw      = ex.submit(_net_bandwidth)
        f_vpn     = ex.submit(_net_vpn)

        if os_name == "windows":
            f_route = ex.submit(_ps,
                "(Get-NetRoute | Select-Object DestinationPrefix,NextHop,InterfaceAlias,RouteMetric "
                "| Sort-Object RouteMetric | Select-Object -First 20 "
                "| Format-Table -AutoSize | Out-String).Trim()")
            f_dns   = ex.submit(_ps,
                "(Get-DnsClientServerAddress | Select-Object InterfaceAlias,ServerAddresses "
                "| Format-Table -AutoSize | Out-String).Trim()")
            f_fw    = ex.submit(_ps,
                "(Get-NetFirewallProfile | Select-Object Name,Enabled "
                "| Format-Table -AutoSize | Out-String).Trim()")
            f_fwr   = ex.submit(_ps,
                "(Get-NetFirewallRule | Where-Object {$_.Enabled -eq 'True' -and $_.Action -eq 'Allow'} "
                "| Select-Object DisplayName,Direction,Protocol | Sort-Object Direction "
                "| Select-Object -First 20 | Format-Table -AutoSize | Out-String).Trim()")
            f_cn    = ex.submit(_ps,
                "(Get-NetTCPConnection | Where-Object {$_.State -eq 'Established'} "
                "| Select-Object -First 20 LocalAddress,LocalPort,RemoteAddress,RemotePort,OwningProcess "
                "| Format-Table -AutoSize | Out-String).Trim()")
        elif os_name == "macos":
            f_route = ex.submit(_sh, ["netstat", "-rn"])
            f_dns   = ex.submit(_sh, ["scutil", "--dns"])
            f_fw    = ex.submit(_sh, ["pfctl", "-s", "info"])
            f_wifi  = ex.submit(_sh, [
                "/System/Library/PrivateFrameworks/Apple80211.framework"
                "/Versions/Current/Resources/airport", "-I"
            ])
            f_proxy = ex.submit(_sh, ["scutil", "--proxy"])
        elif os_name == "linux":
            f_route = ex.submit(_sh, ["ip", "route", "show"])
            f_dns   = ex.submit(lambda: Path("/etc/resolv.conf").read_text(
                encoding="utf-8", errors="replace"
            ) if Path("/etc/resolv.conf").exists() else "(not found)")
            f_fw    = ex.submit(_sh, ["ufw", "status", "verbose"])
            f_fwi   = ex.submit(_sh, ["iptables", "-L", "-n", "--line-numbers"])
        else:
            f_route = ex.submit(lambda: "(unsupported OS)")
            f_dns   = ex.submit(lambda: "(unsupported OS)")
            f_fw    = ex.submit(lambda: "(unsupported OS)")

        data.interfaces         = f_ifaces.result()
        data.active_connections = f_conns.result()
        data.listening_ports    = f_listen.result()
        data.bandwidth          = f_bw.result()
        data.vpn_detection      = f_vpn.result()
        data.routing_table      = f_route.result()
        data.dns_config         = f_dns.result()
        data.firewall_status    = f_fw.result()

        if os_name == "windows":
            data.firewall_rules    = f_fwr.result()
            data.connections_named = f_cn.result()
        elif os_name == "macos":
            data.wifi_status  = f_wifi.result()
            data.proxy_config = f_proxy.result()
        elif os_name == "linux":
            data.firewall_iptables = f_fwi.result()

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
    data = StartupData(os_name=os_name)

    with ThreadPoolExecutor(max_workers=6) as ex:
        if os_name == "macos":
            f_ua = ex.submit(_sh, ["launchctl", "list"])
            la_path = os.path.expanduser("~/Library/LaunchAgents")
            f_la = ex.submit(lambda: "\n".join(os.listdir(la_path))
                             if os.path.exists(la_path) else "(none)")
            f_ld = ex.submit(lambda: "\n".join(os.listdir("/Library/LaunchDaemons"))
                             if os.path.exists("/Library/LaunchDaemons") else "(none)")

            data.running_items  = f_ua.result()
            data.user_items     = f_la.result()
            data.system_daemons = f_ld.result()

        elif os_name == "linux":
            f_sys = ex.submit(_sh, ["systemctl", "list-unit-files",
                                    "--state=enabled", "--no-pager"])
            f_run = ex.submit(_sh, ["systemctl", "list-units",
                                    "--type=service", "--state=running", "--no-pager"])
            f_bt  = ex.submit(_sh, ["systemd-analyze"])
            f_btp = ex.submit(_sh, ["systemd-analyze", "blame"])
            auto_path = os.path.expanduser("~/.config/autostart")
            f_usr = ex.submit(lambda: "\n".join(os.listdir(auto_path))
                              if os.path.exists(auto_path) else "(none)")

            data.system_items          = f_sys.result()
            data.running_items         = f_run.result()
            data.boot_time_summary     = f_bt.result()
            data.boot_time_per_service = f_btp.result()
            data.user_items            = f_usr.result()

        elif os_name == "windows":
            f_ru = ex.submit(_ps,
                "(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' "
                "| Format-List | Out-String).Trim()")
            f_rs = ex.submit(_ps,
                "(Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' "
                "| Format-List | Out-String).Trim()")
            f_st = ex.submit(_ps,
                "(Get-ScheduledTask | Where-Object {$_.State -ne 'Disabled'} "
                "| Select-Object TaskName,State | Sort-Object TaskName "
                "| Select-Object -First 30 | Format-Table -AutoSize | Out-String).Trim()")
            f_sv = ex.submit(_ps,
                "(Get-Service | Where-Object {$_.StartType -eq 'Automatic' -and $_.Status -eq 'Running'} "
                "| Select-Object Name,DisplayName | Sort-Object Name "
                "| Format-Table -AutoSize | Out-String).Trim()")

            data.user_items      = f_ru.result()
            data.system_items    = f_rs.result()
            data.scheduled_tasks = f_st.result()
            data.auto_services   = f_sv.result()

    return data


# ─────────────────────────────────────────────────────────────────────────────
# User Activity
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UserActivityData:
    os_name: str
    recent_files: str = ""    # recently opened files with timestamps
    frequent_apps: str = ""   # apps used most often
    shell_history: str = ""   # last N unique shell commands
    last_logins: str = ""     # recent login/session events


def _recent_files_windows() -> str:
    return _ps(
        "$sh = New-Object -ComObject WScript.Shell; "
        "Get-ChildItem \"$env:APPDATA\\Microsoft\\Windows\\Recent\" -Filter *.lnk "
        "| Sort-Object LastWriteTime -Descending | Select-Object -First 30 "
        "| ForEach-Object { "
        "  try { $t = $sh.CreateShortcut($_.FullName).TargetPath } "
        "  catch { $t = $_.BaseName }; "
        "  \"$($_.LastWriteTime.ToString('yyyy-MM-dd HH:mm'))  $t\" "
        "} | Out-String"
    )


def _recent_files_macos() -> str:
    return _sh([
        "mdfind", "-onlyin", str(Path.home()),
        "kMDItemLastUsedDate >= $time.now(-30d)",
        "-attr", "kMDItemPath,kMDItemLastUsedDate",
    ])


def _recent_files_linux() -> str:
    xbel = Path.home() / ".recently-used.xbel"
    if not xbel.exists():
        return "(~/.recently-used.xbel not found)"
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(xbel).getroot()
        bookmarks = []
        for bm in root.findall("bookmark"):
            href = bm.get("href", "")
            visited = bm.get("visited") or bm.get("modified") or ""
            if href.startswith("file://"):
                bookmarks.append((visited, href[7:]))
        bookmarks.sort(reverse=True)
        lines = [f"{'Last Used':<22}Path"]
        for visited, path in bookmarks[:25]:
            lines.append(f"{visited[:19]:<22}{path[:70]}")
        return "\n".join(lines) if len(lines) > 1 else "(no recent files)"
    except Exception as exc:
        return f"(error parsing recent files: {exc})"


def _shell_history() -> str:
    candidates = [
        Path.home() / ".zsh_history",
        Path.home() / ".bash_history",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt",
    ]
    for path in candidates:
        if path.exists():
            try:
                raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
                # strip zsh extended-history timestamps (lines starting with ': ')
                cmds = [l for l in raw if l.strip() and not l.startswith(": ")]
                seen: set[str] = set()
                unique: list[str] = []
                for cmd in reversed(cmds):
                    if cmd not in seen:
                        seen.add(cmd)
                        unique.append(cmd)
                    if len(unique) >= 30:
                        break
                return "\n".join(reversed(unique))
            except OSError:
                pass
    return "(no shell history found)"


def _frequent_apps_windows() -> str:
    return _ps(
        "(Get-ChildItem \"$env:APPDATA\\Microsoft\\Windows\\Recent\\AutomaticDestinations\" "
        "| Sort-Object LastWriteTime -Descending | Select-Object -First 20 "
        "| Select-Object BaseName,LastWriteTime "
        "| Format-Table -AutoSize | Out-String).Trim()"
    )


def _frequent_apps_macos() -> str:
    return _sh(["mdfind",
                "kMDItemLastUsedDate >= $time.now(-30d) && kMDItemContentTypeTree == 'com.apple.application-bundle'",
                "-attr", "kMDItemDisplayName,kMDItemLastUsedDate"])


def _frequent_apps_linux() -> str:
    desktop_dirs = [
        Path("/usr/share/applications"),
        Path.home() / ".local/share/applications",
    ]
    apps = []
    for d in desktop_dirs:
        if d.exists():
            for f in d.glob("*.desktop"):
                try:
                    st = f.stat()
                    apps.append((f.stem, st.st_mtime))
                except OSError:
                    pass
    if not apps:
        return "(no .desktop app files found)"
    apps.sort(key=lambda x: x[1], reverse=True)
    return "\n".join(f"{name}" for name, _ in apps[:20])


def _last_logins(os_name: str) -> str:
    if os_name == "windows":
        return _ps(
            "(Get-LocalUser | Where-Object {$_.LastLogon} "
            "| Select-Object Name,LastLogon | Sort-Object LastLogon -Descending "
            "| Format-Table -AutoSize | Out-String).Trim()"
        )
    return _sh(["last", "-n", "10"])


def collect_user_activity(os_name: str) -> UserActivityData:
    data = UserActivityData(os_name=os_name)

    _recent_fn = (
        _recent_files_windows if os_name == "windows" else
        _recent_files_macos   if os_name == "macos"   else
        _recent_files_linux
    )
    _apps_fn = (
        _frequent_apps_windows if os_name == "windows" else
        _frequent_apps_macos   if os_name == "macos"   else
        _frequent_apps_linux
    )

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_recent = ex.submit(_recent_fn)
        f_apps   = ex.submit(_apps_fn)
        f_shell  = ex.submit(_shell_history)
        f_logins = ex.submit(_last_logins, os_name)

        data.recent_files  = f_recent.result()
        data.frequent_apps = f_apps.result()
        data.shell_history = f_shell.result()
        data.last_logins   = f_logins.result()

    return data
