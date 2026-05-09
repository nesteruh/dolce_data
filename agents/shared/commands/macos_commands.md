# 🍎 macOS Command Reference
# Used by all agents when detected OS = macOS (sys.platform == 'darwin')
# Format: [Category] → Command — Purpose

---

## ⚠️ Usage Rules
- Always use `sudo` only when strictly necessary and with user approval.
- Prefer non-destructive read-only commands for initial analysis.
- Commands marked ⚠️ require user confirmation before execution.
- Commands marked 🚫 are FORBIDDEN — do not execute under any circumstances.

---

## 🗄️ STORAGE COMMANDS

### Disk Usage Overview
```bash
# Total disk usage for all volumes
df -h

# Human-readable disk usage for specific path
df -h /

# Disk Utility info (machine-readable)
diskutil info /
```

### File System Analysis
```bash
# Top 20 largest items in home directory (non-recursive)
du -sh ~/*/  2>/dev/null | sort -rh | head -20

# Top 20 largest files anywhere in home (recursive)
find ~ -type f -exec du -sh {} + 2>/dev/null | sort -rh | head -20

# Size of a specific folder
du -sh /path/to/folder

# Find files larger than 1GB in home
find ~ -size +1G -type f 2>/dev/null

# Find files larger than 500MB
find ~ -size +500M -type f 2>/dev/null

# Find files modified more than 30 days ago in Downloads
find ~/Downloads -mtime +30 -type f 2>/dev/null
```

### Cache Analysis
```bash
# Size of user cache directory
du -sh ~/Library/Caches

# List each app's cache with size
du -sh ~/Library/Caches/* 2>/dev/null | sort -rh | head -20

# System-level caches (read-only for analysis)
du -sh /Library/Caches 2>/dev/null

# Xcode derived data (safe to delete)
du -sh ~/Library/Developer/Xcode/DerivedData 2>/dev/null

# Xcode archives
du -sh ~/Library/Developer/Xcode/Archives 2>/dev/null

# iOS device support files
du -sh ~/Library/Developer/Xcode/iOS\ DeviceSupport 2>/dev/null
```

### Trash Analysis
```bash
# Size of user Trash
du -sh ~/.Trash 2>/dev/null

# List Trash contents
ls -lah ~/.Trash 2>/dev/null
```

### Cleanup Commands (⚠️ Require User Confirmation)
```bash
# Clear user cache directory
# ⚠️ CONFIRM FIRST
rm -rf ~/Library/Caches/*

# Clear Xcode derived data
# ⚠️ CONFIRM FIRST
rm -rf ~/Library/Developer/Xcode/DerivedData/*

# Empty Trash
# ⚠️ CONFIRM FIRST
osascript -e 'tell application "Finder" to empty trash'

# Clear DNS cache
# ⚠️ CONFIRM FIRST
sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder

# Clear font cache
# ⚠️ CONFIRM FIRST
sudo atsutil databases -remove
```

### APFS Snapshots
```bash
# List APFS local snapshots (Time Machine)
tmutil listlocalsnapshots /

# Check snapshot size
tmutil listlocalsnapshotdates

# 🚫 DO NOT DELETE snapshots without explicit user request
```

---

## 🔋 BATTERY COMMANDS

### Battery Status
```bash
# Comprehensive battery information
system_profiler SPPowerDataType

# Quick battery status
pmset -g batt

# Battery capacity info (cycle count, design capacity, max capacity)
system_profiler SPPowerDataType | grep -E "Cycle Count|Full Charge Capacity|Design Capacity|Condition|Charging"

# Power management settings
pmset -g

# Power management log (last events)
pmset -g log | tail -100
```

### Energy Usage by Process
```bash
# Top energy consumers (sort by energy impact)
top -o energy -n 10 -l 1 | head -30

# Activity Monitor energy data via CLI (requires powermetrics, sudo)
# ⚠️ Requires sudo
sudo powermetrics --samplers tasks --show-process-energy -n 1 -i 3000

# List processes with high energy impact using ps
ps aux --sort=-%cpu | head -15
```

### Power Settings (⚠️ Require User Confirmation)
```bash
# Set display sleep to 5 minutes on battery
# ⚠️ CONFIRM FIRST
sudo pmset -b displaysleep 5

# Set system sleep to 15 minutes on battery
# ⚠️ CONFIRM FIRST
sudo pmset -b sleep 15

# Disable Power Nap on battery
# ⚠️ CONFIRM FIRST
sudo pmset -b powernap 0

# Enable Power Nap on battery
# ⚠️ CONFIRM FIRST
sudo pmset -b powernap 1

# Disable wake for network access on battery
# ⚠️ CONFIRM FIRST
sudo pmset -b womp 0
```

### Bluetooth / WiFi (for power saving)
```bash
# Check Bluetooth status
system_profiler SPBluetoothDataType | grep "State"

# Check WiFi status
networksetup -getairportpower en0
```

---

## 💻 CPU / GPU / RAM COMMANDS

### CPU Overview
```bash
# Real-time CPU usage (1 sample)
top -l 1 -n 0 | grep "CPU usage"

# Top 10 processes by CPU
top -l 1 -o cpu -n 10 | head -30

# Load averages
sysctl -n vm.loadavg

# Number of CPU cores
sysctl -n hw.ncpu
sysctl -n hw.physicalcpu

# CPU frequency and info
sysctl -n machdep.cpu.brand_string

# Process list sorted by CPU
ps aux --sort=-%cpu | head -15
```

### RAM / Memory
```bash
# Memory usage summary
vm_stat

# Human-readable memory stats
top -l 1 -n 0 | grep -E "PhysMem|Swap"

# Page ins/outs (high pageouts = RAM pressure)
vm_stat | grep "Page"

# Process list sorted by memory
ps aux --sort=-%mem | head -15

# Memory usage in MB per process
ps -eo pid,comm,%mem,rss --sort=-%mem | head -20

# Available memory
sysctl -n hw.memsize   # total physical RAM in bytes
```

### GPU Usage
```bash
# Apple Silicon GPU — requires powermetrics (sudo)
# ⚠️ Requires sudo
sudo powermetrics --samplers gpu_power -n 1 -i 2000

# GPU info
system_profiler SPDisplaysDataType

# Check if discrete GPU is active (Intel Macs)
pmset -g | grep "GPU"
```

### Process Management
```bash
# List all processes
ps aux

# Find process by name
pgrep -la "AppName"

# Graceful quit a process (⚠️ CONFIRM FIRST)
kill -15 <PID>

# Force quit a process (⚠️ CONFIRM FIRST — last resort)
kill -9 <PID>

# Force quit by name (⚠️ CONFIRM FIRST)
killall "AppName"

# Check zombie processes
ps aux | grep 'Z'
```

### System Health
```bash
# System uptime
uptime

# System info
uname -a

# Hardware overview
system_profiler SPHardwareDataType

# Thermal state (overheating check)
sudo powermetrics --samplers smc -n 1 -i 1000 | grep -E "temperature|fan"
```

---

## 🚫 FORBIDDEN COMMANDS (macOS)

```bash
# DO NOT RUN — Will corrupt system
rm -rf /System/*
rm -rf /Library/System/*
rm -rf /usr/bin/*
rm -rf /sbin/*

# DO NOT RUN — Disables System Integrity Protection
csrutil disable

# DO NOT RUN — Kills display server (crashes session)
sudo killall WindowServer

# DO NOT RUN — Kills launchd (crashes system)
sudo kill -9 1
```
