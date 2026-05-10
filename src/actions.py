"""
Action execution layer — registry-based. 

Adding a new action requires exactly ONE thing: a function decorated with
@action(...).  The registry auto-derives VALID_TYPES, type labels, default
risks, and the parser regex — nothing else needs updating.

Handler signature:
    (os_name: str, payload: str) -> tuple[bool, str, str]
Return value:
    (success, output_to_display, error_message)

Payload conventions (| separates parts):
    Single value  — ACTION_lock_screen:
    Path|content  — ACTION_create_file: ~/Desktop/note.txt | Hello!
    Src|dst       — ACTION_copy_file: ~/a.txt | ~/b.txt
    Path|mode     — ACTION_set_permissions: ~/script.sh | 755
    SSID|pass     — ACTION_connect_wifi: MyNet | secret
    Level 0-100   — ACTION_set_volume: 50
    Title|Body    — ACTION_send_notification: Done | File copied.
"""

from __future__ import annotations

import os   
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psutil


# ─────────────────────────────────────────────────────────────────────────────
# Core data classes (public API — unchanged)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Action:
    type: str
    payload: str
    description: str
    risk: str


@dataclass
class ActionResult:
    action: Action
    success: bool
    output: str
    error: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Def:
    type_name: str
    label: str
    default_risk: str
    handler: Callable[[str, str], tuple[bool, str, str]]


_REGISTRY: dict[str, _Def] = {}


def action(type_name: str, *, label: str, risk: str = "LOW") -> Callable:
    """Decorator — register a handler function as an executable action."""
    def decorator(fn: Callable) -> Callable:
        _REGISTRY[type_name] = _Def(type_name, label, risk, fn)
        return fn
    return decorator


# Public accessors (replacing the old scattered constants)
def type_label(t: str) -> str:
    return _REGISTRY[t].label if t in _REGISTRY else t

def default_risk(t: str) -> str:
    return _REGISTRY[t].default_risk if t in _REGISTRY else "MEDIUM"

def valid_types() -> set[str]:
    return set(_REGISTRY)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _os() -> str:
    p = sys.platform
    if p == "darwin":         return "macos"
    if p.startswith("linux"): return "linux"
    if p == "win32":          return "windows"
    return "unknown"


def _split(payload: str, n: int = 2) -> list[str]:
    """Split payload on | into exactly n parts, padding with '' if needed."""
    parts = [p.strip() for p in payload.split("|", n - 1)]
    while len(parts) < n:
        parts.append("")
    return parts


def _sh(cmd: list[str] | str, shell: bool = False,
         timeout: int = 30) -> tuple[bool, str, str]:
    """Run a subprocess; return (success, output, error)."""
    r = subprocess.run(cmd, shell=shell, capture_output=True,
                       text=True, timeout=timeout)
    combined = (r.stdout + "\n" + r.stderr).strip()
    if r.returncode == 0:
        return True, combined, ""
    return False, "", combined


def _macos_wifi_iface() -> str:
    """Return the macOS Wi-Fi interface name (usually en0)."""
    r = subprocess.run(["networksetup", "-listallhardwareports"],
                       capture_output=True, text=True, timeout=8)
    lines = r.stdout.splitlines()
    for i, line in enumerate(lines):
        if "Wi-Fi" in line or "AirPort" in line:
            for j in range(i, min(i + 4, len(lines))):
                if "Device:" in lines[j]:
                    return lines[j].split("Device:")[-1].strip()
    return "en0"


# ─────────────────────────────────────────────────────────────────────────────
# ── Shell & app ──────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

@action("shell", label="$ Shell command", risk="MEDIUM")
def _shell(os_name: str, payload: str) -> tuple[bool, str, str]:
    r = subprocess.run(payload, shell=True, capture_output=True,
                       text=True, timeout=60)
    out = (r.stdout + "\n" + r.stderr).strip()
    if r.returncode == 0:
        return True, out or "Done", ""
    return False, "", out or f"Command failed (exit {r.returncode}): {payload}"


@action("open_app", label="⌘  Open app", risk="LOW")
def _open_app(os_name: str, payload: str) -> tuple[bool, str, str]:
    app = payload.strip()
    # Take only the first semicolon-separated item if LLM chained multiple apps
    app = app.split(";")[0].strip()
    # Strip "open_app", "openapp", "open " prefixes the LLM sometimes prepends
    for prefix in ("open_app ", "openapp ", "open app ", "open "):
        if app.lower().startswith(prefix):
            app = app[len(prefix):].strip()
            break
    if os_name == "macos":
        return _sh(["open", "-a", app])
    if os_name == "linux":
        ok, out, err = _sh(["gtk-launch", app])
        if not ok:
            ok, out, err = _sh(app, shell=True)
        return ok, f"Launched {app}" if ok else out, err
    if os_name == "windows":
        return _sh(["powershell", "-Command", f'Start-Process "{app}"'])
    return False, "", "Unsupported OS"


@action("open_file", label="📂 Open file", risk="LOW")
def _open_file(os_name: str, payload: str) -> tuple[bool, str, str]:
    path = os.path.expanduser(payload.strip())
    if not os.path.exists(path):
        return False, "", f"File not found: {path}"
    if os_name == "macos":
        return _sh(["open", path])
    if os_name == "linux":
        return _sh(["xdg-open", path])
    if os_name == "windows":
        os.startfile(path)  # type: ignore[attr-defined]
        return True, f"Opened {path}", ""
    return False, "", "Unsupported OS"


# ─────────────────────────────────────────────────────────────────────────────
# ── Process management ───────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _find_procs(payload: str) -> list[psutil.Process]:
    """Return processes matching a PID (int) or name substring."""
    name = payload.strip()
    # Strip LLM-generated verb prefixes — e.g. "force quit Google Chrome" → "Google Chrome"
    for prefix in ("force quit ", "force-quit ", "force_quit ", "terminate ",
                   "kill ", "quit ", "close ", "stop "):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
            break
    try:
        return [psutil.Process(int(name))]
    except ValueError:
        pass
    name_lower = name.lower()
    found = []
    for p in psutil.process_iter(["name", "pid"]):
        try:
            if name_lower in (p.info.get("name") or "").lower():
                found.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return found


@action("kill_process", label="⚡ Kill process", risk="MEDIUM")
def _kill_process(os_name: str, payload: str) -> tuple[bool, str, str]:
    procs = _find_procs(payload.strip())
    if not procs:
        return False, "", f"No process matching '{payload}'"
    killed = []
    for p in procs:
        try:
            name = p.name()
            p.terminate()
            killed.append(f"{name} (PID {p.pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            return False, "", str(e)
    return True, f"Terminated: {', '.join(killed)}", ""


@action("force_quit_app", label="💀 Force quit", risk="HIGH")
def _force_quit(os_name: str, payload: str) -> tuple[bool, str, str]:
    procs = _find_procs(payload.strip())
    if not procs:
        return False, "", f"No process matching '{payload}'"
    killed = []
    for p in procs:
        try:
            name = p.name()
            p.kill()
            killed.append(f"{name} (PID {p.pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            return False, "", str(e)
    return True, f"Force-killed: {', '.join(killed)}", ""


@action("restart_process", label="🔄 Restart process", risk="HIGH")
def _restart_process(os_name: str, payload: str) -> tuple[bool, str, str]:
    procs = _find_procs(payload.strip())
    if not procs:
        return False, "", f"No process matching '{payload}'"
    p = procs[0]
    try:
        name = p.name()
        cmdline = p.cmdline()
        p.terminate()
        try:
            p.wait(timeout=5)
        except psutil.TimeoutExpired:
            p.kill()
        time.sleep(0.5)
        subprocess.Popen(cmdline)
        return True, f"Restarted {name}", ""
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as e:
        return False, "", str(e)


# ─────────────────────────────────────────────────────────────────────────────
# ── File operations ──────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

@action("create_file", label="✏️  Create file", risk="LOW")
def _create_file(os_name: str, payload: str) -> tuple[bool, str, str]:
    path_str, content = _split(payload)
    path = Path(os.path.expanduser(path_str))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True, f"Created: {path}\nContent: {content[:120]!r}", ""
    except OSError as e:
        return False, "", str(e)


@action("copy_file", label="📋 Copy file", risk="LOW")
def _copy_file(os_name: str, payload: str) -> tuple[bool, str, str]:
    src_str, dst_str = _split(payload)
    if not dst_str:
        return False, "", "Expected: source | destination"
    src, dst = Path(os.path.expanduser(src_str)), Path(os.path.expanduser(dst_str))
    if not src.exists():
        return False, "", f"Source not found: {src}"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(src), str(dst), dirs_exist_ok=True) if src.is_dir() else shutil.copy2(str(src), str(dst))
        return True, f"Copied: {src}  →  {dst}", ""
    except OSError as e:
        return False, "", str(e)


@action("move_file", label="✂️  Move / rename", risk="MEDIUM")
def _move_file(os_name: str, payload: str) -> tuple[bool, str, str]:
    src_str, dst_str = _split(payload)
    if not dst_str:
        return False, "", "Expected: source | destination"
    src, dst = Path(os.path.expanduser(src_str)), Path(os.path.expanduser(dst_str))
    if not src.exists():
        return False, "", f"Source not found: {src}"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return True, f"Moved: {src}  →  {dst}", ""
    except OSError as e:
        return False, "", str(e)


@action("rename_file", label="🏷️  Rename", risk="MEDIUM")
def _rename_file(os_name: str, payload: str) -> tuple[bool, str, str]:
    src_str, new_name = _split(payload)
    src = Path(os.path.expanduser(src_str))
    if not src.exists():
        return False, "", f"Not found: {src}"
    dst = src.parent / new_name.strip()
    try:
        src.rename(dst)
        return True, f"Renamed: {src.name}  →  {dst.name}", ""
    except OSError as e:
        return False, "", str(e)


@action("delete_file", label="🗑️  Delete", risk="HIGH")
def _delete_file(os_name: str, payload: str) -> tuple[bool, str, str]:
    path = Path(os.path.expanduser(payload.strip()))
    if not path.exists():
        return False, "", f"Not found: {path}"
    try:
        shutil.rmtree(str(path)) if path.is_dir() else path.unlink()
        kind = "directory" if path.is_dir() else "file"
        return True, f"Deleted {kind}: {path}", ""
    except OSError as e:
        return False, "", str(e)


@action("mkdir", label="📁 Create folder", risk="LOW")
def _mkdir(os_name: str, payload: str) -> tuple[bool, str, str]:
    path = Path(os.path.expanduser(payload.strip()))
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, f"Created folder: {path}", ""
    except OSError as e:
        return False, "", str(e)


@action("set_permissions", label="🔐 Set permissions", risk="MEDIUM")
def _set_permissions(os_name: str, payload: str) -> tuple[bool, str, str]:
    path_str, mode = _split(payload)
    if not mode:
        return False, "", "Expected: path | mode  (e.g. ~/script.sh | 755)"
    path = os.path.expanduser(path_str)
    if not os.path.exists(path):
        return False, "", f"Not found: {path}"
    if os_name == "windows":
        return False, "", "set_permissions is not supported on Windows"
    return _sh(["chmod", mode.strip(), path])


@action("list_directory", label="📋 List directory", risk="LOW")
def _list_directory(os_name: str, payload: str) -> tuple[bool, str, str]:
    path = Path(os.path.expanduser(payload.strip() or "~"))
    if not path.exists():
        return False, "", f"Not found: {path}"
    try:
        entries = sorted(path.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        lines = [f"{'Name':<40} {'Size':>10}  Modified"]
        import datetime
        for e in entries[:50]:
            try:
                st = e.stat()
                size = f"{st.st_size:,}" if e.is_file() else "<dir>"
                mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                icon = "📄" if e.is_file() else "📁"
                lines.append(f"{icon} {e.name:<38} {size:>10}  {mtime}")
            except OSError:
                lines.append(f"? {e.name}")
        if len(list(path.iterdir())) > 50:
            lines.append(f"… (showing first 50 of {sum(1 for _ in path.iterdir())} items)")
        return True, "\n".join(lines), ""
    except PermissionError as e:
        return False, "", str(e)


@action("compress", label="🗜️  Compress", risk="LOW")
def _compress(os_name: str, payload: str) -> tuple[bool, str, str]:
    src_str, out_str = _split(payload)
    if not out_str:
        return False, "", "Expected: source | output.zip"
    src = Path(os.path.expanduser(src_str))
    out = Path(os.path.expanduser(out_str))
    if not src.exists():
        return False, "", f"Source not found: {src}"
    fmt = "zip" if out.suffix == ".zip" else "gztar"
    base = str(out.parent / out.stem.replace(".tar", ""))
    try:
        shutil.make_archive(base, fmt, str(src.parent), src.name)
        return True, f"Compressed {src.name}  →  {out.name}", ""
    except Exception as e:
        return False, "", str(e)


@action("extract", label="📦 Extract archive", risk="LOW")
def _extract(os_name: str, payload: str) -> tuple[bool, str, str]:
    arc_str, dst_str = _split(payload)
    arc = Path(os.path.expanduser(arc_str))
    dst = Path(os.path.expanduser(dst_str)) if dst_str else arc.parent
    if not arc.exists():
        return False, "", f"Archive not found: {arc}"
    try:
        dst.mkdir(parents=True, exist_ok=True)
        shutil.unpack_archive(str(arc), str(dst))
        return True, f"Extracted {arc.name}  →  {dst}", ""
    except Exception as e:
        return False, "", str(e)


# ─────────────────────────────────────────────────────────────────────────────
# ── System settings ──────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

@action("set_volume", label="🔊 Set volume", risk="LOW")
def _set_volume(os_name: str, payload: str) -> tuple[bool, str, str]:
    try:
        level = max(0, min(100, int(payload.strip())))
    except ValueError:
        return False, "", "Payload must be a number 0-100"
    if os_name == "macos":
        return _sh(["osascript", "-e", f"set volume output volume {level}"])
    if os_name == "linux":
        ok, out, err = _sh(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"])
        if not ok:
            ok, out, err = _sh(["amixer", "sset", "Master", f"{level}%"])
        return ok, f"Volume set to {level}%" if ok else out, err
    if os_name == "windows":
        script = (
            f"$obj = New-Object -com WScript.Shell;"
            f"$obj.SendKeys([char]173);"  # mute/unmute workaround
            f"(New-Object -ComObject WScript.Shell).SendKeys([char]173)"
        )
        # Windows: use nircmd if available, else PowerShell audio API
        ok, out, err = _sh(["nircmd", "setsysvolume", str(int(level / 100 * 65535))])
        if not ok:
            return False, "", "Install nircmd or use system volume slider"
        return ok, f"Volume set to {level}%", err
    return False, "", "Unsupported OS"


@action("set_brightness", label="☀️  Set brightness", risk="LOW")
def _set_brightness(os_name: str, payload: str) -> tuple[bool, str, str]:
    try:
        level = max(0, min(100, int(payload.strip())))
    except ValueError:
        return False, "", "Payload must be a number 0-100"
    frac = level / 100
    if os_name == "macos":
        ok, out, err = _sh(["brightness", str(frac)])
        if not ok:
            return False, "", "Install brightness CLI first: brew install brightness"
        return True, f"Brightness set to {level}%", ""
    if os_name == "linux":
        ok, out, err = _sh(["brightnessctl", "set", f"{level}%"])
        if not ok:
            ok, out, err = _sh(["xrandr", "--output", "eDP-1",
                                 "--brightness", str(frac)])
        return ok, f"Brightness set to {level}%" if ok else out, err
    if os_name == "windows":
        script = (f"(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                  f".WmiSetBrightness(1,{level})")
        return _sh(["powershell", "-Command", script])
    return False, "", "Unsupported OS"


@action("toggle_wifi", label="📶 Toggle Wi-Fi", risk="LOW")
def _toggle_wifi(os_name: str, payload: str) -> tuple[bool, str, str]:
    want = payload.strip().lower()  # "on", "off", or "" (toggle)
    if os_name == "macos":
        iface = _macos_wifi_iface()
        if not want:
            r = subprocess.run(["networksetup", "-getairportpower", iface],
                               capture_output=True, text=True)
            want = "off" if "On" in r.stdout else "on"
        return _sh(["networksetup", "-setairportpower", iface, want])
    if os_name == "linux":
        if not want:
            r = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True)
            want = "off" if "enabled" in r.stdout else "on"
        return _sh(["nmcli", "radio", "wifi", want])
    if os_name == "windows":
        # WinRT Radio API — no admin required.
        # IAsyncOperation returns __ComObject in PS; use Status polling instead of GetAwaiter.
        _load = (
            "$null=[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime];"
            "$null=[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime];"
        )
        _await = "while($__op.Status -eq 0){Start-Sleep -Milliseconds 30}"
        if not want:
            detect = (
                _load
                + "$__op=[Windows.Devices.Radios.Radio]::GetRadiosAsync();"
                + _await + ";"
                + "$__r=$__op.GetResults()|Where-Object{$_.Kind.ToString()-eq'WiFi'}|Select-Object -First 1;"
                + "if($__r){$__r.State.ToString()}else{'NotFound'}"
            )
            r = subprocess.run(["powershell", "-Command", detect],
                               capture_output=True, text=True, timeout=15)
            target = "Off" if "On" in r.stdout else "On"
        else:
            target = "On" if want == "on" else "Off"
        script = (
            _load
            + "$__op=[Windows.Devices.Radios.Radio]::RequestAccessAsync();"
            + _await + ";"
            + "$__op=[Windows.Devices.Radios.Radio]::GetRadiosAsync();"
            + _await + ";"
            + "$__radios=$__op.GetResults();"
            + "$__wifi=$__radios|Where-Object{$_.Kind.ToString()-eq'WiFi'};"
            + "foreach($__r in $__wifi){"
            + f"$__st=[Windows.Devices.Radios.RadioState]::{target};"
            + "$__op=$__r.SetStateAsync($__st);"
            + _await
            + "};"
            + f"Write-Output 'Wi-Fi {target.lower()}'"
        )
        return _sh(["powershell", "-Command", script], timeout=20)
    return False, "", "Unsupported OS"


@action("connect_wifi", label="🔗 Connect Wi-Fi", risk="LOW")
def _connect_wifi(os_name: str, payload: str) -> tuple[bool, str, str]:
    ssid, password = _split(payload)
    if not ssid:
        return False, "", "Expected: SSID | password"
    if os_name == "macos":
        iface = _macos_wifi_iface()
        cmd = ["networksetup", "-setairportnetwork", iface, ssid]
        if password:
            cmd.append(password)
        return _sh(cmd)
    if os_name == "linux":
        cmd = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        return _sh(cmd)
    if os_name == "windows":
        return _sh(["netsh", "wlan", "connect", f"name={ssid}"])
    return False, "", "Unsupported OS"


@action("disconnect_wifi", label="📵 Disconnect Wi-Fi", risk="LOW")
def _disconnect_wifi(os_name: str, payload: str) -> tuple[bool, str, str]:
    if os_name == "macos":
        iface = _macos_wifi_iface()
        return _sh(["networksetup", "-setairportpower", iface, "off"])
    if os_name == "linux":
        return _sh(["nmcli", "radio", "wifi", "off"])
    if os_name == "windows":
        return _sh(["netsh", "wlan", "disconnect"])
    return False, "", "Unsupported OS"


@action("toggle_bluetooth", label="🔵 Toggle Bluetooth", risk="LOW")
def _toggle_bluetooth(os_name: str, payload: str) -> tuple[bool, str, str]:
    want = payload.strip().lower()
    if os_name == "macos":
        if not want:
            r = subprocess.run(["blueutil", "--power"], capture_output=True, text=True)
            want = "off" if r.stdout.strip() == "1" else "on"
        ok, out, err = _sh(["blueutil", "--power", "1" if want == "on" else "0"])
        if not ok:
            return False, "", "Install blueutil first: brew install blueutil"
        return ok, f"Bluetooth {'on' if want == 'on' else 'off'}", err
    if os_name == "linux":
        state = "unblock" if want in ("on", "") else "block"
        return _sh(["rfkill", state, "bluetooth"])
    if os_name == "windows":
        _load = (
            "$null=[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime];"
            "$null=[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime];"
        )
        _await = "while($__op.Status -eq 0){Start-Sleep -Milliseconds 30}"
        if not want:
            detect = (
                _load
                + "$__op=[Windows.Devices.Radios.Radio]::GetRadiosAsync();"
                + _await + ";"
                + "$__r=$__op.GetResults()|Where-Object{$_.Kind.ToString()-eq'Bluetooth'}|Select-Object -First 1;"
                + "if($__r){$__r.State.ToString()}else{'NotFound'}"
            )
            r = subprocess.run(["powershell", "-Command", detect],
                               capture_output=True, text=True, timeout=15)
            target = "Off" if "On" in r.stdout else "On"
        else:
            target = "On" if want == "on" else "Off"
        script = (
            _load
            + "$__op=[Windows.Devices.Radios.Radio]::RequestAccessAsync();"
            + _await + ";"
            + "$__op=[Windows.Devices.Radios.Radio]::GetRadiosAsync();"
            + _await + ";"
            + "$__radios=$__op.GetResults();"
            + "$__bt=$__radios|Where-Object{$_.Kind.ToString()-eq'Bluetooth'};"
            + "foreach($__r in $__bt){"
            + f"$__st=[Windows.Devices.Radios.RadioState]::{target};"
            + "$__op=$__r.SetStateAsync($__st);"
            + _await
            + "};"
            + f"Write-Output 'Bluetooth {target.lower()}'"
        )
        return _sh(["powershell", "-Command", script], timeout=20)
    return False, "", "Unsupported OS"


@action("toggle_dark_mode", label="🌙 Toggle dark mode", risk="LOW")
def _toggle_dark_mode(os_name: str, payload: str) -> tuple[bool, str, str]:
    if os_name == "macos":
        script = ('tell app "System Events" to tell appearance preferences '
                  'to set dark mode to not dark mode')
        return _sh(["osascript", "-e", script])
    if os_name == "linux":
        r = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface",
                             "color-scheme"], capture_output=True, text=True)
        current = r.stdout.strip()
        new = "prefer-light" if "dark" in current else "prefer-dark"
        return _sh(["gsettings", "set", "org.gnome.desktop.interface",
                    "color-scheme", new])
    if os_name == "windows":
        key = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        script = (f'$v = (Get-ItemProperty "{key}").AppsUseLightTheme;'
                  f'Set-ItemProperty "{key}" -Name AppsUseLightTheme -Value (1 - $v)')
        return _sh(["powershell", "-Command", script])
    return False, "", "Unsupported OS"


@action("toggle_do_not_disturb", label="🔕 Toggle DND", risk="LOW")
def _toggle_dnd(os_name: str, payload: str) -> tuple[bool, str, str]:
    want = payload.strip().lower()  # "on", "off", or "" toggle
    if os_name == "macos":
        # macOS Ventura+: Focus modes — fall back to older DND defaults key
        enable = "true" if want == "on" else ("false" if want == "off" else None)
        if enable is None:
            r = subprocess.run(
                ["defaults", "-currentHost", "read",
                 "com.apple.notificationcenterui", "doNotDisturb"],
                capture_output=True, text=True)
            enable = "false" if r.stdout.strip() == "1" else "true"
        ok, out, err = _sh(["defaults", "-currentHost", "write",
                             "com.apple.notificationcenterui",
                             "doNotDisturb", "-boolean", enable])
        if ok:
            subprocess.run(["killall", "NotificationCenter"],
                           capture_output=True)
        return ok, f"Do Not Disturb {'on' if enable == 'true' else 'off'}", err
    if os_name == "linux":
        state = "true" if want in ("on", "") else "false"
        return _sh(["gsettings", "set",
                    "org.gnome.desktop.notifications", "show-banners", state])
    if os_name == "windows":
        # Windows Focus Assist via registry
        value = "1" if want in ("on", "") else "0"
        script = (f'Set-ItemProperty -Path "HKCU:\\Software\\Microsoft\\Windows\\'
                  f'CurrentVersion\\CloudStore\\Store\\DefaultAccount\\Current\\'
                  f'default$windows.data.notifications.quiethourssettings\\" '
                  f'-Name Data -Value ([byte[]](0x02,0x00,0x00,0x00,{value}))'
                  f' -ErrorAction SilentlyContinue')
        return _sh(["powershell", "-Command", script])
    return False, "", "Unsupported OS"


@action("set_sleep_timer", label="💤 Set sleep timer", risk="LOW")
def _set_sleep_timer(os_name: str, payload: str) -> tuple[bool, str, str]:
    try:
        minutes = int(payload.strip())
    except ValueError:
        return False, "", "Payload must be minutes (integer)"
    if os_name == "macos":
        ok, out, err = _sh(["sudo", "pmset", "-b", "displaysleep", str(minutes)])
        return ok, f"Display sleep set to {minutes} min", err
    if os_name == "linux":
        seconds = minutes * 60
        return _sh(["xset", "dpms", str(seconds), str(seconds + 60),
                    str(seconds + 120)])
    if os_name == "windows":
        return _sh(["powercfg", "/change", "monitor-timeout-dc", str(minutes)])
    return False, "", "Unsupported OS"


@action("lock_screen", label="🔒 Lock screen", risk="LOW")
def _lock_screen(os_name: str, payload: str) -> tuple[bool, str, str]:
    if os_name == "macos":
        return _sh(["osascript", "-e",
                    'tell application "System Events" to '
                    'keystroke "q" using {command down, control down}'])
    if os_name == "linux":
        ok, out, err = _sh(["loginctl", "lock-session"])
        if not ok:
            ok, out, err = _sh(["xdg-screensaver", "lock"])
        return ok, "Screen locked", err
    if os_name == "windows":
        return _sh(["rundll32.exe", "user32.dll,LockWorkStation"])
    return False, "", "Unsupported OS"


@action("sleep_now", label="😴 Sleep now", risk="MEDIUM")
def _sleep_now(os_name: str, payload: str) -> tuple[bool, str, str]:
    if os_name == "macos":
        return _sh(["pmset", "sleepnow"])
    if os_name == "linux":
        return _sh(["systemctl", "suspend"])
    if os_name == "windows":
        return _sh(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    return False, "", "Unsupported OS"


@action("empty_trash", label="🗑️  Empty trash", risk="HIGH")
def _empty_trash(os_name: str, payload: str) -> tuple[bool, str, str]:
    if os_name == "macos":
        return _sh(["osascript", "-e",
                    'tell application "Finder" to empty trash'])
    if os_name == "linux":
        trash = Path.home() / ".local/share/Trash"
        try:
            shutil.rmtree(str(trash / "files"), ignore_errors=True)
            shutil.rmtree(str(trash / "info"), ignore_errors=True)
            (trash / "files").mkdir(exist_ok=True)
            (trash / "info").mkdir(exist_ok=True)
            return True, "Trash emptied", ""
        except OSError as e:
            return False, "", str(e)
    if os_name == "windows":
        return _sh(["powershell", "-Command",
                    "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"])
    return False, "", "Unsupported OS"


@action("send_notification", label="🔔 Notification", risk="LOW")
def _send_notification(os_name: str, payload: str) -> tuple[bool, str, str]:
    title, body = _split(payload)
    if not title:
        return False, "", "Expected: Title | body message"
    if os_name == "macos":
        script = f'display notification "{body}" with title "{title}"'
        return _sh(["osascript", "-e", script])
    if os_name == "linux":
        return _sh(["notify-send", title, body])
    if os_name == "windows":
        script = (f'[Windows.UI.Notifications.ToastNotificationManager,'
                  f'Windows.UI.Notifications,ContentType=WindowsRuntime]'
                  f' | Out-Null; '
                  f'$t = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;'
                  f'$x = [Windows.UI.Notifications.ToastNotificationManager]'
                  f'::GetTemplateContent($t);'
                  f'$x.GetElementsByTagName("text")[0].AppendChild($x.CreateTextNode("{title}")) | Out-Null;'
                  f'$x.GetElementsByTagName("text")[1].AppendChild($x.CreateTextNode("{body}")) | Out-Null;'
                  f'$n = [Windows.UI.Notifications.ToastNotification]::new($x);'
                  f'[Windows.UI.Notifications.ToastNotificationManager]'
                  f'::CreateToastNotifier("PowerShell").Show($n)')
        return _sh(["powershell", "-Command", script])
    return False, "", "Unsupported OS"


# ─────────────────────────────────────────────────────────────────────────────
# Executor  (dispatch is now a single registry lookup)
# ─────────────────────────────────────────────────────────────────────────────

class ActionExecutor:
    def __init__(self) -> None:
        self.os_name = _os()

    def execute(self, action: Action) -> ActionResult:
        defn = _REGISTRY.get(action.type)
        if not defn:
            return ActionResult(action, False, "",
                                f"Unknown action type: {action.type!r}. "
                                f"Available: {', '.join(sorted(_REGISTRY))}")
        try:
            success, output, error = defn.handler(self.os_name, action.payload)
            return ActionResult(action, success, output, error)
        except Exception as exc:
            return ActionResult(action, False, "", str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Parser  (regex built from registry — always in sync)
# ─────────────────────────────────────────────────────────────────────────────

def _build_re() -> re.Pattern[str]:
    types = "|".join(re.escape(t) for t in _REGISTRY)
    return re.compile(rf"^ACTION_({types}):\s*(.*)$", re.IGNORECASE)


def parse_actions(
    text: str,
    suggestions: list[str],
    risk_levels: list[str],
) -> list[Action]:
    """
    Extract ACTION_<type>: <payload> lines from agent LLM output.
    Each ACTION line inherits description and risk from the nearest preceding
    SUGGESTION line. Orphan ACTION lines (no preceding SUGGESTION) fall back
    to the payload as description and the type's default risk.
    """
    _re = _build_re()
    lines = text.splitlines()

    sug_line_nums: list[int] = []
    for ln, line in enumerate(lines):
        if line.strip().upper().startswith("SUGGESTION"):
            sug_line_nums.append(ln)

    actions: list[Action] = []

    for ln, line in enumerate(lines):
        m = _re.match(line.strip())
        if not m:
            continue

        action_type = m.group(1).lower()
        payload = m.group(2).strip()

        nearest_sug_idx = -1
        for sug_pos, sug_ln in enumerate(sug_line_nums):
            if sug_ln < ln:
                nearest_sug_idx = sug_pos
            else:
                break

        if nearest_sug_idx >= 0 and nearest_sug_idx < len(suggestions):
            description = suggestions[nearest_sug_idx]
            risk = (risk_levels[nearest_sug_idx]
                    if nearest_sug_idx < len(risk_levels) else "LOW")
        else:
            description = payload
            risk = default_risk(action_type)

        actions.append(Action(
            type=action_type,
            payload=payload,
            description=description,
            risk=risk,
        ))

    return actions
