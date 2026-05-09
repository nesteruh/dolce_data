# 🍎 macOS Command Reference
# Single source of truth for all shell commands on macOS.
# Agents MUST reference commands by their ID (e.g. `storage.disk_overview`).
# No agent instruction file should contain any shell syntax — only IDs from this file.

---

## ⚠️ Global Usage Rules

- Always use `sudo` only when strictly necessary and with explicit user approval.
- Run READ commands first — never jump to WRITE/DELETE commands without analysing READ output.
- Commands labelled `RISK: MEDIUM` require one explicit user confirmation.
- Commands labelled `RISK: HIGH` require confirmation plus a summary of what will be changed.
- Commands labelled `FORBIDDEN` must never be executed under any circumstances.
- Check command availability with `which <tool>` before calling optional tools.

---

## 📦 Command Format

Each entry follows this structure:

```
### CMD_ID: <domain>.<name>
- Purpose : what this command does
- Risk    : NONE | MEDIUM | HIGH | FORBIDDEN
- Requires: sudo? optional tool? confirmation?
- Command :
  <exact shell command>
```

---

## 🗄️ STORAGE COMMANDS

---

### CMD_ID: `storage.disk_overview`
- **Purpose**: Show all mounted volumes with total size, used, and free space
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  df -h
  ```

---

### CMD_ID: `storage.volume_detail`
- **Purpose**: Detailed info for the root volume (filesystem type, mount options)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  diskutil info /
  ```

---

### CMD_ID: `storage.largest_dirs`
- **Purpose**: Top 10 largest directories in the user's home folder (non-recursive)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  du -sh ~/*/  2>/dev/null | sort -rh | head -10
  ```

---

### CMD_ID: `storage.largest_files`
- **Purpose**: Top 10 largest individual files recursively under home
- **Risk**: NONE
- **Requires**: nothing (slow on large home dirs)
- **Command**:
  ```
  find ~ -type f -exec du -sh {} + 2>/dev/null | sort -rh | head -10
  ```

---

### CMD_ID: `storage.files_over_1gb`
- **Purpose**: List all files larger than 1 GB in the home folder
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  find ~ -size +1G -type f 2>/dev/null
  ```

---

### CMD_ID: `storage.files_over_500mb`
- **Purpose**: List all files larger than 500 MB in the home folder
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  find ~ -size +500M -type f 2>/dev/null
  ```

---

### CMD_ID: `storage.downloads_old`
- **Purpose**: List files in ~/Downloads not modified in the last 30 days
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  find ~/Downloads -mtime +30 -type f 2>/dev/null
  ```

---

### CMD_ID: `storage.user_cache_size`
- **Purpose**: Total size of the user-level cache directory
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  du -sh ~/Library/Caches 2>/dev/null
  ```

---

### CMD_ID: `storage.user_cache_breakdown`
- **Purpose**: Per-app cache sizes, sorted largest first
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  du -sh ~/Library/Caches/* 2>/dev/null | sort -rh | head -20
  ```

---

### CMD_ID: `storage.system_cache_size`
- **Purpose**: Size of system-level cache (read-only analysis, not modified)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  du -sh /Library/Caches 2>/dev/null
  ```

---

### CMD_ID: `storage.xcode_derived_data`
- **Purpose**: Size of Xcode derived data (safe to delete)
- **Risk**: NONE
- **Requires**: Xcode installed
- **Command**:
  ```
  du -sh ~/Library/Developer/Xcode/DerivedData 2>/dev/null
  ```

---

### CMD_ID: `storage.xcode_archives`
- **Purpose**: Size of Xcode build archives
- **Risk**: NONE
- **Requires**: Xcode installed
- **Command**:
  ```
  du -sh ~/Library/Developer/Xcode/Archives 2>/dev/null
  ```

---

### CMD_ID: `storage.trash_size`
- **Purpose**: Current size of the user Trash
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  du -sh ~/.Trash 2>/dev/null
  ```

---

### CMD_ID: `storage.trash_list`
- **Purpose**: List contents of the Trash with sizes
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  ls -lah ~/.Trash 2>/dev/null
  ```

---

### CMD_ID: `storage.snapshots_list`
- **Purpose**: List APFS/Time Machine local snapshots
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  tmutil listlocalsnapshots /
  ```

---

### CMD_ID: `storage.clear_user_cache`
- **Purpose**: Delete all files inside the user cache directory
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation
- **Command**:
  ```
  rm -rf ~/Library/Caches/*
  ```

---

### CMD_ID: `storage.clear_xcode_derived`
- **Purpose**: Delete Xcode derived data folder contents (rebuilds on next build)
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation
- **Command**:
  ```
  rm -rf ~/Library/Developer/Xcode/DerivedData/*
  ```

---

### CMD_ID: `storage.empty_trash`
- **Purpose**: Permanently empty the Trash via Finder
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation
- **Command**:
  ```
  osascript -e 'tell application "Finder" to empty trash'
  ```

---

## 🔋 BATTERY COMMANDS

---

### CMD_ID: `battery.quick_status`
- **Purpose**: Current battery charge percentage and power source (AC or battery)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  pmset -g batt
  ```

---

### CMD_ID: `battery.full_profile`
- **Purpose**: Detailed battery hardware info: cycle count, design capacity, max capacity, condition
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  system_profiler SPPowerDataType
  ```

---

### CMD_ID: `battery.health_summary`
- **Purpose**: Filtered view of capacity and health fields only
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  system_profiler SPPowerDataType | grep -E "Cycle Count|Full Charge Capacity|Design Capacity|Condition|Charging"
  ```

---

### CMD_ID: `battery.power_settings`
- **Purpose**: Show all current power management settings (sleep timers, Power Nap, etc.)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  pmset -g
  ```

---

### CMD_ID: `battery.power_log`
- **Purpose**: Last 100 lines of the power management event log
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  pmset -g log | tail -100
  ```

---

### CMD_ID: `battery.top_energy_consumers`
- **Purpose**: Processes sorted by CPU usage as energy impact proxy
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  ps aux --sort=-%cpu | head -15
  ```

---

### CMD_ID: `battery.bluetooth_status`
- **Purpose**: Check whether Bluetooth is enabled (active = power drain)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  system_profiler SPBluetoothDataType | grep "State"
  ```

---

### CMD_ID: `battery.wifi_status`
- **Purpose**: Check whether Wi-Fi radio is on
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  networksetup -getairportpower en0
  ```

---

### CMD_ID: `battery.set_display_sleep`
- **Purpose**: Set display sleep timeout (minutes) when on battery
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation + value parameter `<minutes>`
- **Command**:
  ```
  sudo pmset -b displaysleep <minutes>
  ```

---

### CMD_ID: `battery.set_system_sleep`
- **Purpose**: Set system sleep timeout (minutes) when on battery
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation + value parameter `<minutes>`
- **Command**:
  ```
  sudo pmset -b sleep <minutes>
  ```

---

### CMD_ID: `battery.disable_power_nap`
- **Purpose**: Disable Power Nap so the system does not wake for background tasks on battery
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation
- **Command**:
  ```
  sudo pmset -b powernap 0
  ```

---

### CMD_ID: `battery.enable_power_nap`
- **Purpose**: Re-enable Power Nap on battery
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation
- **Command**:
  ```
  sudo pmset -b powernap 1
  ```

---

### CMD_ID: `battery.disable_wake_on_network`
- **Purpose**: Stop the system from waking when a network request arrives (battery)
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation
- **Command**:
  ```
  sudo pmset -b womp 0
  ```

---

## 💻 HEALTH (CPU / GPU / RAM) COMMANDS

---

### CMD_ID: `health.cpu_overview`
- **Purpose**: One-line CPU usage snapshot
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  top -l 1 -n 0 | grep "CPU usage"
  ```

---

### CMD_ID: `health.load_avg`
- **Purpose**: 1-minute, 5-minute, 15-minute load averages
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  sysctl -n vm.loadavg
  ```

---

### CMD_ID: `health.cpu_core_count`
- **Purpose**: Number of logical and physical CPU cores
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  sysctl -n hw.ncpu && sysctl -n hw.physicalcpu
  ```

---

### CMD_ID: `health.cpu_model`
- **Purpose**: CPU brand string
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  sysctl -n machdep.cpu.brand_string
  ```

---

### CMD_ID: `health.top_cpu_procs`
- **Purpose**: Top 15 processes sorted by CPU usage
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  ps aux --sort=-%cpu | head -16
  ```

---

### CMD_ID: `health.memory_overview`
- **Purpose**: Virtual memory statistics (pages free, active, wired, etc.)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  vm_stat
  ```

---

### CMD_ID: `health.memory_summary`
- **Purpose**: Human-readable PhysMem and Swap line from top
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  top -l 1 -n 0 | grep -E "PhysMem|Swap"
  ```

---

### CMD_ID: `health.total_ram`
- **Purpose**: Total physical RAM installed (in bytes)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  sysctl -n hw.memsize
  ```

---

### CMD_ID: `health.top_mem_procs`
- **Purpose**: Top 15 processes sorted by memory usage with RSS values
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  ps -eo pid,comm,%cpu,%mem,rss --sort=-%mem | head -16
  ```

---

### CMD_ID: `health.zombie_procs`
- **Purpose**: List any defunct (zombie) processes
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  ps aux | grep 'Z'
  ```

---

### CMD_ID: `health.gpu_info`
- **Purpose**: GPU model, VRAM, and Metal support info
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  system_profiler SPDisplaysDataType | grep -E "Chipset Model|VRAM|Metal"
  ```

---

### CMD_ID: `health.uptime`
- **Purpose**: System uptime and load averages in one line
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  uptime
  ```

---

### CMD_ID: `health.hardware_overview`
- **Purpose**: Mac model, chip, memory, serial number
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  system_profiler SPHardwareDataType
  ```

---

### CMD_ID: `health.find_proc_by_name`
- **Purpose**: Find the PID and full path of a running process by name
- **Risk**: NONE
- **Requires**: process name parameter `<name>`
- **Command**:
  ```
  pgrep -la "<name>"
  ```

---

### CMD_ID: `health.graceful_kill`
- **Purpose**: Send SIGTERM to a process — ask it to shut down gracefully
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + PID parameter `<pid>`
- **Command**:
  ```
  kill -15 <pid>
  ```

---

### CMD_ID: `health.force_kill`
- **Purpose**: Send SIGKILL — use only if graceful kill did not work
- **Risk**: HIGH
- **Requires**: explicit user confirmation + confirmation that graceful kill was tried first + PID `<pid>`
- **Command**:
  ```
  kill -9 <pid>
  ```

---

## 🚫 FORBIDDEN COMMANDS (macOS)
> These command IDs exist only to document what must NEVER be executed.
> Agents must refuse any user request that maps to these.

| CMD_ID (Forbidden)                  | Reason                                      |
|-------------------------------------|---------------------------------------------|
| `forbidden.delete_system`           | Deletes macOS system files — unrecoverable  |
| `forbidden.disable_sip`             | Disables System Integrity Protection        |
| `forbidden.kill_window_server`      | Crashes the display session immediately     |
| `forbidden.kill_launchd`            | Kills PID 1 — crashes the entire OS        |
