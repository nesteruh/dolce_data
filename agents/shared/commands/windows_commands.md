# 🪟 Windows Command Reference
# Used by all agents when detected OS = Windows (sys.platform == 'win32')
# Format: [Category] → Command — Purpose
# Note: Commands are PowerShell unless labeled CMD or WMI.

---

## ⚠️ Usage Rules
- Run PowerShell commands with appropriate execution policy.
- Use `Start-Process -Verb RunAs` for admin-required commands — prompt user for UAC approval.
- Commands marked ⚠️ require user confirmation before execution.
- Commands marked 🚫 are FORBIDDEN — do not execute under any circumstances.
- All paths use Windows-style backslash (`\`). Adjust for PowerShell vs CMD context.

---

## 🗄️ STORAGE COMMANDS

### Disk Usage Overview
```powershell
# All drives with free/total space
Get-PSDrive -PSProvider FileSystem | Select-Object Name, Used, Free, @{N='Total';E={$_.Used + $_.Free}} | Format-Table

# WMI disk info (bytes)
Get-WmiObject -Class Win32_LogicalDisk | Select-Object DeviceID, Size, FreeSpace | Format-Table

# Disk usage in GB (human-readable)
Get-WmiObject -Class Win32_LogicalDisk | Select-Object DeviceID,
  @{N='Total(GB)';E={[math]::Round($_.Size/1GB,2)}},
  @{N='Free(GB)';E={[math]::Round($_.FreeSpace/1GB,2)}},
  @{N='Used(GB)';E={[math]::Round(($_.Size-$_.FreeSpace)/1GB,2)}} | Format-Table
```

### File System Analysis
```powershell
# Top 20 largest folders in user profile
Get-ChildItem $env:USERPROFILE -Directory | 
  ForEach-Object { [PSCustomObject]@{
    Name = $_.FullName
    SizeGB = [math]::Round((Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | 
      Measure-Object -Property Length -Sum).Sum / 1GB, 3)
  }} | Sort-Object SizeGB -Descending | Select-Object -First 20 | Format-Table

# Find files larger than 1GB in user profile
Get-ChildItem $env:USERPROFILE -Recurse -ErrorAction SilentlyContinue |
  Where-Object { $_.Length -gt 1GB } | 
  Select-Object FullName, @{N='SizeGB';E={[math]::Round($_.Length/1GB,2)}} | 
  Sort-Object SizeGB -Descending

# Files in Downloads older than 30 days
Get-ChildItem "$env:USERPROFILE\Downloads" -File |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
  Select-Object Name, LastWriteTime, @{N='SizeMB';E={[math]::Round($_.Length/1MB,2)}}
```

### Cache Analysis
```powershell
# Windows Temp folder size
(Get-ChildItem $env:TEMP -Recurse -ErrorAction SilentlyContinue | 
  Measure-Object -Property Length -Sum).Sum / 1MB

# System Temp folder size (requires admin)
(Get-ChildItem "C:\Windows\Temp" -Recurse -ErrorAction SilentlyContinue | 
  Measure-Object -Property Length -Sum).Sum / 1MB

# Windows Update cache
(Get-ChildItem "C:\Windows\SoftwareDistribution\Download" -Recurse -ErrorAction SilentlyContinue | 
  Measure-Object -Property Length -Sum).Sum / 1MB

# Browser caches (Chrome)
(Get-ChildItem "$env:LOCALAPPDATA\Google\Chrome\User Data\Default\Cache" -Recurse -ErrorAction SilentlyContinue | 
  Measure-Object -Property Length -Sum).Sum / 1MB

# Browser caches (Edge)
(Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Edge\User Data\Default\Cache" -Recurse -ErrorAction SilentlyContinue | 
  Measure-Object -Property Length -Sum).Sum / 1MB
```

### Recycle Bin
```powershell
# Check Recycle Bin size (CMD)
# ⚠️ Use CMD for this
# DIR /s "C:\$Recycle.Bin"

# PowerShell — list Recycle Bin contents
$shell = New-Object -ComObject Shell.Application
$recycleBin = $shell.Namespace(0xA)
$recycleBin.Items() | Select-Object Name, Size
```

### Cleanup Commands (⚠️ Require User Confirmation)
```powershell
# Clear user Temp folder
# ⚠️ CONFIRM FIRST
Remove-Item -Path "$env:TEMP\*" -Recurse -Force -ErrorAction SilentlyContinue

# Run Windows built-in Disk Cleanup (GUI)
# ⚠️ CONFIRM FIRST
cleanmgr /d C:

# Run Disk Cleanup silently (sageset to configure first)
# ⚠️ CONFIRM FIRST
cleanmgr /sagerun:1

# Clear Windows Update download cache (requires admin)
# ⚠️ CONFIRM FIRST
Stop-Service wuauserv -Force
Remove-Item "C:\Windows\SoftwareDistribution\Download\*" -Recurse -Force -ErrorAction SilentlyContinue
Start-Service wuauserv

# Empty Recycle Bin
# ⚠️ CONFIRM FIRST
Clear-RecycleBin -Force -ErrorAction SilentlyContinue
```

---

## 🔋 BATTERY COMMANDS

### Battery Status
```powershell
# Battery status via WMI
Get-WmiObject -Class Win32_Battery | Select-Object Name, BatteryStatus, EstimatedChargeRemaining, EstimatedRunTime | Format-List

# Battery status codes (BatteryStatus):
# 1=Discharging, 2=AC+Battery, 3=Fully Charged, 4=Low, 5=Critical, 6=Charging

# Battery design capacity and full charge capacity
Get-WmiObject -Namespace "root\wmi" -Class BatteryStaticData
Get-WmiObject -Namespace "root\wmi" -Class BatteryFullChargedCapacity

# Generate full battery report (HTML file)
# ⚠️ Requires admin — outputs to C:\battery-report.html
powercfg /batteryreport /output "C:\Users\$env:USERNAME\battery-report.html"

# Battery cycle count (Windows 8+)
powercfg /batteryreport
```

### Power Settings
```powershell
# Current power plan
powercfg /getactivescheme

# List all power plans
powercfg /list

# Power usage by app (last 7 days)
powercfg /srumutil    # generates detailed report

# Check connected standby support
powercfg /a
```

### Power Settings Changes (⚠️ Require User Confirmation)
```powershell
# Switch to Power Saver plan
# ⚠️ CONFIRM FIRST
powercfg /setactive SCHEME_MAX    # Power Saver

# Switch to Balanced plan
# ⚠️ CONFIRM FIRST  
powercfg /setactive SCHEME_BALANCED

# Set display timeout on battery (e.g., 5 minutes = 300 seconds)
# ⚠️ CONFIRM FIRST
powercfg /change monitor-timeout-dc 5

# Set sleep timeout on battery (e.g., 15 minutes)
# ⚠️ CONFIRM FIRST
powercfg /change standby-timeout-dc 15
```

---

## 💻 CPU / GPU / RAM COMMANDS

### CPU Overview
```powershell
# CPU info
Get-WmiObject -Class Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed | Format-List

# CPU usage (current)
Get-WmiObject -Class Win32_Processor | Select-Object Name, LoadPercentage

# Real-time CPU usage per process (top 15)
Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Name, Id, CPU, @{N='CPU%';E={[math]::Round($_.CPU,2)}} | Format-Table

# Load average equivalent — processor queue
Get-Counter '\System\Processor Queue Length'

# CPU temperature (requires third-party or WMI extension)
Get-WmiObject -Namespace "root/wmi" -Class MSAcpi_ThermalZoneTemperature 2>/dev/null | 
  Select-Object @{N='TempC';E={($_.CurrentTemperature - 2732) / 10}} | Format-Table
```

### RAM / Memory
```powershell
# Memory overview
Get-WmiObject -Class Win32_OperatingSystem | 
  Select-Object @{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}},
                @{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} | Format-Table

# Detailed memory info
Get-WmiObject -Class Win32_PhysicalMemory | Select-Object BankLabel, Capacity, Speed, Manufacturer | Format-Table

# Page file (virtual memory / swap equivalent)
Get-WmiObject -Class Win32_PageFileUsage | Select-Object Name, CurrentUsage, AllocatedBaseSize | Format-Table

# Top 15 processes by memory usage
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 15 Name, Id, 
  @{N='RAM(MB)';E={[math]::Round($_.WorkingSet64/1MB,2)}} | Format-Table
```

### GPU Usage
```powershell
# GPU info
Get-WmiObject -Class Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion | Format-List

# GPU usage via Performance Counter
Get-Counter '\GPU Engine(*)\Utilization Percentage' -SampleInterval 1 -MaxSamples 3 |
  Select-Object -ExpandProperty CounterSamples |
  Where-Object { $_.CookedValue -gt 0 } |
  Sort-Object CookedValue -Descending |
  Select-Object -First 10 InstanceName, CookedValue | Format-Table

# NVIDIA GPU (if nvidia-smi is installed)
& "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe" 2>/dev/null
```

### Process Management
```powershell
# List all processes
Get-Process | Sort-Object CPU -Descending | Format-Table Name, Id, CPU, WorkingSet

# Find process by name
Get-Process -Name "ProcessName" -ErrorAction SilentlyContinue

# Graceful stop (⚠️ CONFIRM FIRST)
Stop-Process -Id <PID> -ErrorAction SilentlyContinue

# Force stop (⚠️ CONFIRM FIRST — last resort)
Stop-Process -Id <PID> -Force -ErrorAction SilentlyContinue

# Kill by name (⚠️ CONFIRM FIRST)
Stop-Process -Name "AppName" -Force -ErrorAction SilentlyContinue

# Find and kill hung processes (not responding)
Get-Process | Where-Object { $_.Responding -eq $false } | 
  Select-Object Name, Id, CPU | Format-Table
```

### Startup Items
```powershell
# List startup programs (registry)
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" |
  Select-Object * -ExcludeProperty PSPath, PSParentPath, PSChildName, PSDrive, PSProvider

# List current user startup
Get-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" |
  Select-Object * -ExcludeProperty PSPath, PSParentPath, PSChildName, PSDrive, PSProvider

# List startup folder items
Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
```

### System Health
```powershell
# System uptime
(Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime

# Windows version
Get-WmiObject -Class Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture

# Event log errors (last 50)
Get-EventLog -LogName System -EntryType Error -Newest 50 | 
  Select-Object TimeGenerated, Source, Message | Format-Table -AutoSize

# Check disk health
Get-PhysicalDisk | Select-Object FriendlyName, HealthStatus, OperationalStatus, Size | Format-Table
```

---

## 🚫 FORBIDDEN COMMANDS (Windows)

```powershell
# DO NOT RUN — Deletes system32 (catastrophic)
Remove-Item -Path "C:\Windows\System32" -Recurse -Force

# DO NOT RUN — Disables UAC (security vulnerability)
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name EnableLUA -Value 0

# DO NOT RUN — Corrupts boot record
# format C: /fs:NTFS

# DO NOT RUN — Deletes user registry hive
Remove-Item -Path "HKCU:\*" -Recurse -Force

# DO NOT RUN — Disables Windows Defender
Set-MpPreference -DisableRealtimeMonitoring $true

# DO NOT RUN — Kills critical processes
Stop-Process -Name "lsass" -Force    # Authentication service — crashes system
Stop-Process -Name "csrss" -Force    # Client/Server Runtime — crashes system
Stop-Process -Name "winlogon" -Force # Windows Logon — crashes session
```
