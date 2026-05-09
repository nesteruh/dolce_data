# 🐧 Linux Command Reference
# Used by all agents when detected OS = Linux (sys.platform == 'linux')
# Format: [Category] → Command — Purpose

---

## ⚠️ Usage Rules
- Always use `sudo` only when strictly necessary and with user approval.
- Prefer non-destructive read-only commands for initial analysis.
- Commands marked ⚠️ require user confirmation before execution.
- Commands marked 🚫 are FORBIDDEN — do not execute under any circumstances.
- Some commands may not be installed by default (e.g., `powertop`, `nvtop`) — check availability first.

---

## 🗄️ STORAGE COMMANDS

### Disk Usage Overview
```bash
# All mounted filesystems
df -h

# Specific filesystem
df -h /

# Inode usage (for inode exhaustion issues)
df -i
```

### File System Analysis
```bash
# Disk usage of home directory (top level)
du -sh ~/*/ 2>/dev/null | sort -rh | head -20

# Largest files in home (recursive)
find ~ -type f -exec du -sh {} + 2>/dev/null | sort -rh | head -20

# Find files larger than 1GB
find ~ -size +1G -type f 2>/dev/null

# Find files larger than 500MB
find ~ -size +500M -type f 2>/dev/null

# Find files in /tmp older than 7 days
find /tmp -mtime +7 -type f 2>/dev/null

# Files in Downloads older than 30 days
find ~/Downloads -mtime +30 -type f 2>/dev/null
```

### Cache Analysis
```bash
# User cache size
du -sh ~/.cache 2>/dev/null

# Per-app cache sizes
du -sh ~/.cache/* 2>/dev/null | sort -rh | head -20

# System journal logs size
du -sh /var/log/journal 2>/dev/null

# APT cache (Debian/Ubuntu)
du -sh /var/cache/apt 2>/dev/null

# DNF/YUM cache (Fedora/RHEL)
du -sh /var/cache/dnf 2>/dev/null
du -sh /var/cache/yum 2>/dev/null

# Snap packages (if applicable)
du -sh /snap 2>/dev/null

# Flatpak data
du -sh ~/.local/share/flatpak 2>/dev/null

# Thumbnails cache
du -sh ~/.cache/thumbnails 2>/dev/null
```

### Trash Analysis
```bash
# User trash size
du -sh ~/.local/share/Trash 2>/dev/null

# List trash contents
ls -lah ~/.local/share/Trash/files 2>/dev/null
```

### Cleanup Commands (⚠️ Require User Confirmation)
```bash
# Clear user cache
# ⚠️ CONFIRM FIRST
rm -rf ~/.cache/*

# Clear APT cache (Debian/Ubuntu)
# ⚠️ CONFIRM FIRST
sudo apt-get clean

# Autoremove unused packages
# ⚠️ CONFIRM FIRST
sudo apt-get autoremove

# Clear DNF cache (Fedora)
# ⚠️ CONFIRM FIRST
sudo dnf clean all

# Clear systemd journal logs older than 7 days
# ⚠️ CONFIRM FIRST
sudo journalctl --vacuum-time=7d

# Clear /tmp manually (use only if /tmp cleanup service is not running)
# ⚠️ CONFIRM FIRST — never delete /tmp itself, only contents
sudo find /tmp -type f -atime +7 -delete

# Clear thumbnail cache
# ⚠️ CONFIRM FIRST
rm -rf ~/.cache/thumbnails/*
```

---

## 🔋 BATTERY COMMANDS

### Battery Status
```bash
# Battery capacity and status
cat /sys/class/power_supply/BAT0/capacity       # charge percentage
cat /sys/class/power_supply/BAT0/status         # Charging / Discharging / Full
cat /sys/class/power_supply/BAT0/energy_full    # current max capacity (µWh)
cat /sys/class/power_supply/BAT0/energy_full_design  # design capacity (µWh)
cat /sys/class/power_supply/BAT0/cycle_count    # cycle count (if supported)
cat /sys/class/power_supply/BAT0/health         # health status

# Using upower (preferred — more readable)
upower -i $(upower -e | grep BAT) 2>/dev/null

# List all power devices
upower -e
```

### Energy Usage by Process
```bash
# PowerTOP (requires install, provides per-process power usage)
# Note: check if installed first
which powertop && sudo powertop --time=5 --html=/tmp/powertop_report.html

# Top processes by CPU (proxy for energy usage)
top -bn1 | head -30

# Processes sorted by CPU
ps aux --sort=-%cpu | head -15
```

### Power Settings (⚠️ Require User Confirmation)
```bash
# Check current power profile (TLP)
sudo tlp-stat -s 2>/dev/null

# Set to power-save profile (TLP)
# ⚠️ CONFIRM FIRST
sudo tlp bat 2>/dev/null

# Set CPU governor to powersave
# ⚠️ CONFIRM FIRST
echo powersave | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

# Set CPU governor to performance (for AC)
# ⚠️ CONFIRM FIRST
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```

---

## 💻 CPU / GPU / RAM COMMANDS

### CPU Overview
```bash
# CPU info
cat /proc/cpuinfo | grep -E "model name|cpu cores" | uniq

# Number of logical CPUs
nproc

# Real-time CPU usage (1 iteration)
top -bn1 | grep "Cpu(s)"

# Load averages
cat /proc/loadavg
uptime

# Per-core CPU usage
mpstat -P ALL 1 1 2>/dev/null || top -bn1

# Top 10 processes by CPU
ps aux --sort=-%cpu | head -11

# CPU frequency
cat /proc/cpuinfo | grep "cpu MHz"
```

### RAM / Memory
```bash
# Memory overview (human-readable)
free -h

# Detailed memory stats
cat /proc/meminfo

# Virtual memory stats (includes swap activity)
vmstat 1 5

# Top 10 processes by RAM
ps aux --sort=-%mem | head -11

# Memory usage in MB per process
ps -eo pid,comm,%mem,rss --sort=-%mem | head -20

# Swap usage
swapon --show
cat /proc/swaps
```

### GPU Usage
```bash
# NVIDIA GPU (requires nvidia-smi)
nvidia-smi

# NVIDIA GPU — continuous monitoring
watch -n 1 nvidia-smi

# NVIDIA GPU — process-level usage
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv

# AMD GPU (requires radeontop)
radeontop -d - -l 1 2>/dev/null

# AMD GPU via sysfs
cat /sys/class/drm/card0/device/gpu_busy_percent 2>/dev/null

# Intel GPU (requires intel_gpu_top)
sudo intel_gpu_top -l 2>/dev/null

# Check installed GPU
lspci | grep -E "VGA|3D|Display"
```

### Process Management
```bash
# List all processes
ps aux

# Find process by name
pgrep -la "processname"

# Process tree
pstree -p

# Graceful quit (⚠️ CONFIRM FIRST)
kill -15 <PID>

# Force quit (⚠️ CONFIRM FIRST — last resort)
kill -9 <PID>

# Kill by name (⚠️ CONFIRM FIRST)
pkill -15 "processname"

# Check zombie processes
ps aux | awk '$8 == "Z"'
```

### System Health
```bash
# System uptime
uptime

# System info
uname -a

# Hardware info
sudo dmidecode -t memory 2>/dev/null | grep -E "Size|Speed|Type"
lshw -short 2>/dev/null

# Temperature sensors (requires lm-sensors)
sensors 2>/dev/null

# Disk health (requires smartmontools)
sudo smartctl -a /dev/sda 2>/dev/null
```

---

## 🚫 FORBIDDEN COMMANDS (Linux)

```bash
# DO NOT RUN — Wipes entire filesystem
rm -rf /
rm -rf /*

# DO NOT RUN — Kills init process (crashes system)
sudo kill -9 1

# DO NOT RUN — Disables SELinux (security vulnerability)
sudo setenforce 0
sudo sed -i 's/SELINUX=enforcing/SELINUX=disabled/' /etc/selinux/config

# DO NOT RUN — Drops all firewall rules
sudo iptables -F
sudo ufw disable

# DO NOT RUN — Fork bomb
:(){ :|:& };:

# DO NOT modify /proc or /sys filesystem entries directly
# (these may crash the system or corrupt kernel state)
```
