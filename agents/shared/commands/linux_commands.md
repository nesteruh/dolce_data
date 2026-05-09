# 🐧 Linux Command Reference
# Single source of truth for all shell commands on Linux.
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
- Adjust battery path (`BAT0`) if the system uses `BAT1` or another index.

---

## 📦 Command Format

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
- **Purpose**: Show all mounted filesystems with total, used, and free space
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  df -h
  ```

---

### CMD_ID: `storage.inode_usage`
- **Purpose**: Check inode usage per filesystem (useful when disk is "full" despite available space)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  df -i
  ```

---

### CMD_ID: `storage.largest_dirs`
- **Purpose**: Top 10 largest directories in the user's home folder
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
- **Requires**: nothing
- **Command**:
  ```
  find ~ -type f -exec du -sh {} + 2>/dev/null | sort -rh | head -10
  ```

---

### CMD_ID: `storage.files_over_1gb`
- **Purpose**: List files larger than 1 GB in the home folder
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  find ~ -size +1G -type f 2>/dev/null
  ```

---

### CMD_ID: `storage.files_over_500mb`
- **Purpose**: List files larger than 500 MB in the home folder
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  find ~ -size +500M -type f 2>/dev/null
  ```

---

### CMD_ID: `storage.downloads_old`
- **Purpose**: Files in ~/Downloads not modified in the last 30 days
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  find ~/Downloads -mtime +30 -type f 2>/dev/null
  ```

---

### CMD_ID: `storage.user_cache_size`
- **Purpose**: Total size of the user cache directory
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  du -sh ~/.cache 2>/dev/null
  ```

---

### CMD_ID: `storage.user_cache_breakdown`
- **Purpose**: Per-application cache sizes, sorted largest first
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  du -sh ~/.cache/* 2>/dev/null | sort -rh | head -20
  ```

---

### CMD_ID: `storage.apt_cache_size`
- **Purpose**: Size of APT package download cache (Debian / Ubuntu)
- **Risk**: NONE
- **Requires**: apt-based distro
- **Command**:
  ```
  du -sh /var/cache/apt 2>/dev/null
  ```

---

### CMD_ID: `storage.dnf_cache_size`
- **Purpose**: Size of DNF/YUM package cache (Fedora / RHEL)
- **Risk**: NONE
- **Requires**: dnf or yum
- **Command**:
  ```
  du -sh /var/cache/dnf 2>/dev/null
  ```

---

### CMD_ID: `storage.journal_log_size`
- **Purpose**: Size of systemd journal logs
- **Risk**: NONE
- **Requires**: systemd
- **Command**:
  ```
  du -sh /var/log/journal 2>/dev/null
  ```

---

### CMD_ID: `storage.trash_size`
- **Purpose**: Size of the user Trash
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  du -sh ~/.local/share/Trash 2>/dev/null
  ```

---

### CMD_ID: `storage.trash_list`
- **Purpose**: List contents of the Trash
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  ls -lah ~/.local/share/Trash/files 2>/dev/null
  ```

---

### CMD_ID: `storage.thumbnail_cache_size`
- **Purpose**: Size of the thumbnail cache
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  du -sh ~/.cache/thumbnails 2>/dev/null
  ```

---

### CMD_ID: `storage.clear_user_cache`
- **Purpose**: Delete all files in the user cache directory
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation
- **Command**:
  ```
  rm -rf ~/.cache/*
  ```

---

### CMD_ID: `storage.clear_apt_cache`
- **Purpose**: Remove all APT downloaded package files
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation + apt-based distro
- **Command**:
  ```
  sudo apt-get clean
  ```

---

### CMD_ID: `storage.apt_autoremove`
- **Purpose**: Remove unused dependency packages
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation + apt-based distro
- **Command**:
  ```
  sudo apt-get autoremove
  ```

---

### CMD_ID: `storage.clear_dnf_cache`
- **Purpose**: Clean all DNF cached data
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation + dnf
- **Command**:
  ```
  sudo dnf clean all
  ```

---

### CMD_ID: `storage.trim_journal_logs`
- **Purpose**: Delete systemd journal logs older than 7 days
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation
- **Command**:
  ```
  sudo journalctl --vacuum-time=7d
  ```

---

### CMD_ID: `storage.clear_thumbnails`
- **Purpose**: Delete thumbnail cache (rebuilds automatically)
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation
- **Command**:
  ```
  rm -rf ~/.cache/thumbnails/*
  ```

---

## 🔋 BATTERY COMMANDS

---

### CMD_ID: `battery.charge_level`
- **Purpose**: Current battery charge percentage
- **Risk**: NONE
- **Requires**: `/sys/class/power_supply/BAT0/` to exist
- **Command**:
  ```
  cat /sys/class/power_supply/BAT0/capacity 2>/dev/null
  ```

---

### CMD_ID: `battery.charge_status`
- **Purpose**: Charging / Discharging / Full status
- **Risk**: NONE
- **Requires**: `/sys/class/power_supply/BAT0/` to exist
- **Command**:
  ```
  cat /sys/class/power_supply/BAT0/status 2>/dev/null
  ```

---

### CMD_ID: `battery.health_condition`
- **Purpose**: Battery health string (Good / Degraded / etc.)
- **Risk**: NONE
- **Requires**: `/sys/class/power_supply/BAT0/` to exist
- **Command**:
  ```
  cat /sys/class/power_supply/BAT0/health 2>/dev/null
  ```

---

### CMD_ID: `battery.current_max_capacity`
- **Purpose**: Current full-charge capacity in micro-watt-hours
- **Risk**: NONE
- **Requires**: `/sys/class/power_supply/BAT0/` to exist
- **Command**:
  ```
  cat /sys/class/power_supply/BAT0/energy_full 2>/dev/null
  ```

---

### CMD_ID: `battery.design_capacity`
- **Purpose**: Original design capacity in micro-watt-hours
- **Risk**: NONE
- **Requires**: `/sys/class/power_supply/BAT0/` to exist
- **Command**:
  ```
  cat /sys/class/power_supply/BAT0/energy_full_design 2>/dev/null
  ```

---

### CMD_ID: `battery.cycle_count`
- **Purpose**: Number of charge cycles (not all hardware reports this)
- **Risk**: NONE
- **Requires**: `/sys/class/power_supply/BAT0/cycle_count` to exist
- **Command**:
  ```
  cat /sys/class/power_supply/BAT0/cycle_count 2>/dev/null
  ```

---

### CMD_ID: `battery.upower_detail`
- **Purpose**: Full battery report via upower (capacity, state, technology)
- **Risk**: NONE
- **Requires**: `upower` installed
- **Command**:
  ```
  upower -i $(upower -e | grep BAT) 2>/dev/null
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

### CMD_ID: `battery.powertop_report`
- **Purpose**: Generate an HTML energy usage report per-process
- **Risk**: NONE
- **Requires**: `powertop` installed + sudo
- **Command**:
  ```
  sudo powertop --time=5 --html=/tmp/powertop_report.html
  ```

---

### CMD_ID: `battery.set_cpu_powersave`
- **Purpose**: Set CPU frequency governor to power-save mode
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation + cpufreq support
- **Command**:
  ```
  echo powersave | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
  ```

---

### CMD_ID: `battery.set_cpu_performance`
- **Purpose**: Set CPU frequency governor to performance mode (AC use)
- **Risk**: MEDIUM
- **Requires**: sudo + explicit user confirmation + cpufreq support
- **Command**:
  ```
  echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
  ```

---

## 💻 HEALTH (CPU / GPU / RAM) COMMANDS

---

### CMD_ID: `health.cpu_model`
- **Purpose**: CPU model name and core count from /proc/cpuinfo
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  cat /proc/cpuinfo | grep -E "model name|cpu cores" | uniq
  ```

---

### CMD_ID: `health.cpu_core_count`
- **Purpose**: Number of logical CPU cores available
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  nproc
  ```

---

### CMD_ID: `health.cpu_overview`
- **Purpose**: Real-time CPU usage percentages (1 snapshot)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  top -bn1 | grep "Cpu(s)"
  ```

---

### CMD_ID: `health.load_avg`
- **Purpose**: 1-minute, 5-minute, 15-minute load averages
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  cat /proc/loadavg
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
- **Purpose**: Human-readable memory and swap summary
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  free -h
  ```

---

### CMD_ID: `health.memory_detail`
- **Purpose**: Full /proc/meminfo with all memory categories
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  cat /proc/meminfo
  ```

---

### CMD_ID: `health.swap_usage`
- **Purpose**: Active swap devices and their usage
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  swapon --show
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
  ps aux | awk '$8 == "Z"'
  ```

---

### CMD_ID: `health.uptime`
- **Purpose**: System uptime and load averages
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  uptime
  ```

---

### CMD_ID: `health.gpu_list`
- **Purpose**: List GPU adapters detected by the system
- **Risk**: NONE
- **Requires**: `lspci` installed
- **Command**:
  ```
  lspci 2>/dev/null | grep -Ei "VGA|3D|Display"
  ```

---

### CMD_ID: `health.nvidia_gpu_status`
- **Purpose**: NVIDIA GPU utilization, temperature, memory usage
- **Risk**: NONE
- **Requires**: `nvidia-smi` installed
- **Command**:
  ```
  nvidia-smi
  ```

---

### CMD_ID: `health.amd_gpu_usage`
- **Purpose**: AMD GPU busy percentage via sysfs
- **Risk**: NONE
- **Requires**: AMD GPU present
- **Command**:
  ```
  cat /sys/class/drm/card0/device/gpu_busy_percent 2>/dev/null
  ```

---

### CMD_ID: `health.temperatures`
- **Purpose**: CPU and hardware sensor temperatures
- **Risk**: NONE
- **Requires**: `lm-sensors` installed and configured
- **Command**:
  ```
  sensors 2>/dev/null
  ```

---

### CMD_ID: `health.find_proc_by_name`
- **Purpose**: Find PID and full command for a running process by name
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
- **Requires**: explicit user confirmation + confirmation graceful kill was tried + PID `<pid>`
- **Command**:
  ```
  kill -9 <pid>
  ```

---

## 🌐 NETWORK COMMANDS

### CMD_ID: `network.interfaces`
- **Purpose**: List all network interfaces with IP addresses
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  ip addr show
  ```

---

### CMD_ID: `network.routing_table`
- **Purpose**: Show default gateway and routing table
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  ip route show
  ```

---

### CMD_ID: `network.active_connections`
- **Purpose**: All listening and established connections with process names
- **Risk**: NONE
- **Requires**: nothing (`ss` is part of iproute2, available on all modern Linux)
- **Command**:
  ```
  ss -tulnp
  ```

---

### CMD_ID: `network.listening_ports`
- **Purpose**: Only ports actively accepting incoming connections
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  ss -tlnp
  ```

---

### CMD_ID: `network.bandwidth_by_process`
- **Purpose**: Per-process network bandwidth usage
- **Risk**: NONE
- **Requires**: `nethogs` (check availability first with `which nethogs`)
- **Command**:
  ```
  which nethogs && sudo nethogs -t -c 5 2>/dev/null || cat /proc/net/dev
  ```

---

### CMD_ID: `network.interface_stats`
- **Purpose**: Bytes sent/received per interface (fallback bandwidth view)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  cat /proc/net/dev
  ```

---

### CMD_ID: `network.dns_config`
- **Purpose**: Show configured DNS servers
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  resolvectl status 2>/dev/null || cat /etc/resolv.conf
  ```

---

### CMD_ID: `network.firewall_status_ufw`
- **Purpose**: Check ufw firewall status and rules (Debian/Ubuntu)
- **Risk**: NONE
- **Requires**: `ufw` installed
- **Command**:
  ```
  sudo ufw status verbose 2>/dev/null
  ```

---

### CMD_ID: `network.firewall_status_iptables`
- **Purpose**: Check iptables firewall rules (all distros)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  sudo iptables -L -n --line-numbers 2>/dev/null
  ```

---

### CMD_ID: `network.vpn_detection`
- **Purpose**: Detect active VPN tunnel interfaces (tun*, wg*, vpn*)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```
  ip addr show | grep -E "tun|wg|vpn|tap"
  ```

---

### CMD_ID: `network.flush_dns`
- **Purpose**: Clear the local DNS cache
- **Risk**: LOW
- **Requires**: explicit user confirmation
- **Command**:
  ```
  sudo resolvectl flush-caches 2>/dev/null || sudo systemctl restart nscd 2>/dev/null
  ```

---

## 🚀 STARTUP / SERVICE COMMANDS

### CMD_ID: `startup.list_enabled_services`
- **Purpose**: List all systemd services set to start automatically
- **Risk**: NONE
- **Requires**: systemd
- **Command**:
  ```
  systemctl list-unit-files --type=service --state=enabled
  ```

---

### CMD_ID: `startup.list_running_services`
- **Purpose**: List all currently active (running) systemd services
- **Risk**: NONE
- **Requires**: systemd
- **Command**:
  ```
  systemctl list-units --type=service --state=running
  ```

---

### CMD_ID: `startup.list_user_autostart`
- **Purpose**: List desktop-session autostart entries for the current user
- **Risk**: NONE
- **Requires**: nothing (XDG standard path)
- **Command**:
  ```
  ls -la ~/.config/autostart/ 2>/dev/null
  ```

---

### CMD_ID: `startup.list_user_services`
- **Purpose**: List user-scope systemd services
- **Risk**: NONE
- **Requires**: systemd user session
- **Command**:
  ```
  systemctl --user list-unit-files --type=service --state=enabled 2>/dev/null
  ```

---

### CMD_ID: `startup.boot_time_summary`
- **Purpose**: Total boot time breakdown
- **Risk**: NONE
- **Requires**: systemd
- **Command**:
  ```
  systemd-analyze
  ```

---

### CMD_ID: `startup.boot_time_per_service`
- **Purpose**: Which services took the longest to start at boot
- **Risk**: NONE
- **Requires**: systemd
- **Command**:
  ```
  systemd-analyze blame | head -20
  ```

---

### CMD_ID: `startup.service_status`
- **Purpose**: Check status of a specific service
- **Risk**: NONE
- **Requires**: service name parameter `<service>`
- **Command**:
  ```
  systemctl status <service>
  ```

---

### CMD_ID: `startup.disable_service`
- **Purpose**: Prevent a service from starting at boot (does not stop it now)
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + service name `<service>`
- **Command**:
  ```
  sudo systemctl disable <service>
  ```

---

### CMD_ID: `startup.stop_and_disable_service`
- **Purpose**: Stop a running service and disable it from starting at boot
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + service name `<service>`
- **Command**:
  ```
  sudo systemctl disable --now <service>
  ```

---

### CMD_ID: `startup.enable_service`
- **Purpose**: Re-enable a previously disabled service to start at boot
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + service name `<service>`
- **Command**:
  ```
  sudo systemctl enable <service>
  ```

---

### CMD_ID: `startup.disable_user_autostart`
- **Purpose**: Disable a desktop autostart entry by setting Hidden=true
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + desktop file name `<name>.desktop`
- **Command**:
  ```
  echo "Hidden=true" >> ~/.config/autostart/<name>.desktop
  ```

---

## 🚫 FORBIDDEN COMMANDS (Linux)
> These command IDs exist only to document what must NEVER be executed.
> Agents must refuse any user request that maps to these.

| CMD_ID (Forbidden)                  | Reason                                       |
|-------------------------------------|----------------------------------------------|
| `forbidden.rm_root`                 | Wipes the entire root filesystem             |
| `forbidden.kill_init`               | Kills PID 1 (systemd/init) — crashes the OS |
| `forbidden.disable_selinux`         | Removes mandatory access control permanently |
| `forbidden.flush_iptables`          | Drops all firewall rules immediately         |
| `forbidden.fork_bomb`               | Exhausts system resources — denial of service|
