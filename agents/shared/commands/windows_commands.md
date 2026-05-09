# 🪟 Windows Command Reference
# Single source of truth for all commands on Windows.
# Agents MUST reference commands by their ID (e.g. `storage.disk_overview`).
# No agent instruction file should contain any shell syntax — only IDs from this file.
# All commands are PowerShell unless the entry notes CMD or WMI.

---

## ⚠️ Global Usage Rules

- Run PowerShell as Administrator only when strictly required and with user approval.
- Run READ commands first — never jump to WRITE/DELETE commands without analysing READ output.
- Commands labelled `RISK: MEDIUM` require one explicit user confirmation.
- Commands labelled `RISK: HIGH` require confirmation plus a summary of what will be changed.
- Commands labelled `FORBIDDEN` must never be executed under any circumstances.
- UAC elevation will be triggered automatically for admin commands — inform the user first.

---

## 📦 Command Format

```
### CMD_ID: <domain>.<name>
- Purpose : what this command does
- Risk    : NONE | MEDIUM | HIGH | FORBIDDEN
- Requires: admin? optional tool? confirmation?
- Command :
  <exact PowerShell command>
```

---

## 🗄️ STORAGE COMMANDS

---

### CMD_ID: `storage.disk_overview`
- **Purpose**: List all drives with free space and total size in GB
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-WmiObject Win32_LogicalDisk |
    Select-Object DeviceID,
      @{N='Total(GB)';E={[math]::Round($_.Size/1GB,2)}},
      @{N='Free(GB)';E={[math]::Round($_.FreeSpace/1GB,2)}},
      @{N='Used(GB)';E={[math]::Round(($_.Size-$_.FreeSpace)/1GB,2)}} |
    Format-Table
  ```

---

### CMD_ID: `storage.largest_dirs`
- **Purpose**: Top 10 largest directories in the user profile
- **Risk**: NONE
- **Requires**: nothing (slow on large profiles)
- **Command**:
  ```powershell
  Get-ChildItem $env:USERPROFILE -Directory |
    ForEach-Object { [PSCustomObject]@{
      Name = $_.FullName
      SizeGB = [math]::Round((Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum / 1GB, 3)
    }} | Sort-Object SizeGB -Descending | Select-Object -First 10 | Format-Table
  ```

---

### CMD_ID: `storage.files_over_1gb`
- **Purpose**: Find files larger than 1 GB in the user profile
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-ChildItem $env:USERPROFILE -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Length -gt 1GB } |
    Select-Object FullName, @{N='SizeGB';E={[math]::Round($_.Length/1GB,2)}} |
    Sort-Object SizeGB -Descending | Format-Table
  ```

---

### CMD_ID: `storage.downloads_old`
- **Purpose**: Files in the Downloads folder not modified in the last 30 days
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-ChildItem "$env:USERPROFILE\Downloads" -File |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Select-Object Name, LastWriteTime, @{N='SizeMB';E={[math]::Round($_.Length/1MB,2)}} |
    Format-Table
  ```

---

### CMD_ID: `storage.user_temp_size`
- **Purpose**: Size of the user-level Temp folder
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  (Get-ChildItem $env:TEMP -Recurse -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1MB
  ```

---

### CMD_ID: `storage.system_temp_size`
- **Purpose**: Size of the system-level Windows Temp folder
- **Risk**: NONE
- **Requires**: admin recommended
- **Command**:
  ```powershell
  (Get-ChildItem "C:\Windows\Temp" -Recurse -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1MB
  ```

---

### CMD_ID: `storage.update_cache_size`
- **Purpose**: Size of Windows Update download cache
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  (Get-ChildItem "C:\Windows\SoftwareDistribution\Download" -Recurse -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1MB
  ```

---

### CMD_ID: `storage.chrome_cache_size`
- **Purpose**: Size of Google Chrome browser cache
- **Risk**: NONE
- **Requires**: Chrome installed
- **Command**:
  ```powershell
  (Get-ChildItem "$env:LOCALAPPDATA\Google\Chrome\User Data\Default\Cache" -Recurse -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1MB
  ```

---

### CMD_ID: `storage.edge_cache_size`
- **Purpose**: Size of Microsoft Edge browser cache
- **Risk**: NONE
- **Requires**: Edge installed
- **Command**:
  ```powershell
  (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Edge\User Data\Default\Cache" -Recurse -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1MB
  ```

---

### CMD_ID: `storage.trash_list`
- **Purpose**: List contents and sizes in the Recycle Bin
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  $shell = New-Object -ComObject Shell.Application
  $shell.Namespace(0xA).Items() | Select-Object Name, Size | Format-Table
  ```

---

### CMD_ID: `storage.clear_user_temp`
- **Purpose**: Delete all files in the user Temp folder
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation
- **Command**:
  ```powershell
  Remove-Item -Path "$env:TEMP\*" -Recurse -Force -ErrorAction SilentlyContinue
  ```

---

### CMD_ID: `storage.clear_update_cache`
- **Purpose**: Stop Windows Update service and clear download cache
- **Risk**: HIGH
- **Requires**: admin + explicit user confirmation
- **Command**:
  ```powershell
  Stop-Service wuauserv -Force
  Remove-Item "C:\Windows\SoftwareDistribution\Download\*" -Recurse -Force -ErrorAction SilentlyContinue
  Start-Service wuauserv
  ```

---

### CMD_ID: `storage.empty_trash`
- **Purpose**: Permanently empty the Recycle Bin
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation
- **Command**:
  ```powershell
  Clear-RecycleBin -Force -ErrorAction SilentlyContinue
  ```

---

### CMD_ID: `storage.run_disk_cleanup`
- **Purpose**: Launch the built-in Windows Disk Cleanup tool (GUI)
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation
- **Command**:
  ```powershell
  cleanmgr /d C:
  ```

---

## 🔋 BATTERY COMMANDS

---

### CMD_ID: `battery.quick_status`
- **Purpose**: Current charge percentage, charging status, and estimated runtime
- **Risk**: NONE
- **Requires**: laptop/battery present
- **Command**:
  ```powershell
  Get-WmiObject Win32_Battery |
    Select-Object Name, BatteryStatus, EstimatedChargeRemaining, EstimatedRunTime |
    Format-List
  ```

---

### CMD_ID: `battery.design_capacity`
- **Purpose**: Battery design capacity and current full-charge capacity
- **Risk**: NONE
- **Requires**: admin recommended
- **Command**:
  ```powershell
  Get-WmiObject -Namespace "root\wmi" -Class BatteryFullChargedCapacity |
    Select-Object FullChargedCapacity | Format-List
  ```

---

### CMD_ID: `battery.generate_report`
- **Purpose**: Generate a full HTML battery report (cycle count, health history, drain analysis)
- **Risk**: NONE
- **Requires**: admin
- **Command**:
  ```powershell
  powercfg /batteryreport /output "$env:USERPROFILE\battery-report.html"
  ```

---

### CMD_ID: `battery.power_plan_current`
- **Purpose**: Show the currently active power plan
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  powercfg /getactivescheme
  ```

---

### CMD_ID: `battery.power_plan_list`
- **Purpose**: List all available power plans
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  powercfg /list
  ```

---

### CMD_ID: `battery.top_energy_consumers`
- **Purpose**: Processes sorted by CPU usage as energy impact proxy
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-Process | Sort-Object CPU -Descending |
    Select-Object -First 15 Name, Id, CPU | Format-Table
  ```

---

### CMD_ID: `battery.set_power_saver`
- **Purpose**: Switch to the Power Saver power plan
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation
- **Command**:
  ```powershell
  powercfg /setactive SCHEME_MAX
  ```

---

### CMD_ID: `battery.set_balanced`
- **Purpose**: Switch to the Balanced power plan
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation
- **Command**:
  ```powershell
  powercfg /setactive SCHEME_BALANCED
  ```

---

### CMD_ID: `battery.set_display_sleep`
- **Purpose**: Set display timeout on battery power (minutes)
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + value parameter `<minutes>`
- **Command**:
  ```powershell
  powercfg /change monitor-timeout-dc <minutes>
  ```

---

### CMD_ID: `battery.set_system_sleep`
- **Purpose**: Set system sleep timeout on battery power (minutes)
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + value parameter `<minutes>`
- **Command**:
  ```powershell
  powercfg /change standby-timeout-dc <minutes>
  ```

---

## 💻 HEALTH (CPU / GPU / RAM) COMMANDS

---

### CMD_ID: `health.cpu_model`
- **Purpose**: CPU name, core count, logical processor count, and max clock speed
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-WmiObject Win32_Processor |
    Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed |
    Format-List
  ```

---

### CMD_ID: `health.cpu_overview`
- **Purpose**: Current CPU load percentage
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-WmiObject Win32_Processor | Select-Object Name, LoadPercentage | Format-Table
  ```

---

### CMD_ID: `health.top_cpu_procs`
- **Purpose**: Top 15 processes by CPU usage
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-Process | Sort-Object CPU -Descending |
    Select-Object -First 15 Name, Id, CPU | Format-Table
  ```

---

### CMD_ID: `health.memory_overview`
- **Purpose**: Total and free physical RAM in GB
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-WmiObject Win32_OperatingSystem |
    Select-Object @{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}},
                  @{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} |
    Format-Table
  ```

---

### CMD_ID: `health.swap_usage`
- **Purpose**: Page file (virtual memory) current usage and allocated size
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-WmiObject Win32_PageFileUsage |
    Select-Object Name, CurrentUsage, AllocatedBaseSize | Format-Table
  ```

---

### CMD_ID: `health.top_mem_procs`
- **Purpose**: Top 15 processes by RAM usage (Working Set in MB)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-Process | Sort-Object WorkingSet64 -Descending |
    Select-Object -First 15 Name, Id,
      @{N='RAM(MB)';E={[math]::Round($_.WorkingSet64/1MB,2)}} | Format-Table
  ```

---

### CMD_ID: `health.gpu_info`
- **Purpose**: GPU name and adapter RAM
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-WmiObject Win32_VideoController |
    Select-Object Name, AdapterRAM, DriverVersion | Format-List
  ```

---

### CMD_ID: `health.gpu_usage`
- **Purpose**: Real-time GPU utilization per process via Performance Counters
- **Risk**: NONE
- **Requires**: GPU performance counters available
- **Command**:
  ```powershell
  Get-Counter '\GPU Engine(*)\Utilization Percentage' -SampleInterval 1 -MaxSamples 3 |
    Select-Object -ExpandProperty CounterSamples |
    Where-Object { $_.CookedValue -gt 0 } |
    Sort-Object CookedValue -Descending |
    Select-Object -First 10 InstanceName, CookedValue | Format-Table
  ```

---

### CMD_ID: `health.uptime`
- **Purpose**: How long the system has been running since last boot
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  (Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
  ```

---

### CMD_ID: `health.startup_items`
- **Purpose**: List all programs configured to run at system startup
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" |
    Select-Object * -ExcludeProperty PSPath,PSParentPath,PSChildName,PSDrive,PSProvider
  ```

---

### CMD_ID: `health.not_responding_procs`
- **Purpose**: List processes that are currently not responding
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-Process | Where-Object { $_.Responding -eq $false } |
    Select-Object Name, Id, CPU | Format-Table
  ```

---

### CMD_ID: `health.find_proc_by_name`
- **Purpose**: Find a running process by name
- **Risk**: NONE
- **Requires**: process name parameter `<name>`
- **Command**:
  ```powershell
  Get-Process -Name "<name>" -ErrorAction SilentlyContinue | Format-Table
  ```

---

### CMD_ID: `health.graceful_kill`
- **Purpose**: Send a stop signal to a process — allows graceful shutdown
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + PID parameter `<pid>`
- **Command**:
  ```powershell
  Stop-Process -Id <pid> -ErrorAction SilentlyContinue
  ```

---

### CMD_ID: `health.force_kill`
- **Purpose**: Force-terminate a process — use only if graceful kill failed
- **Risk**: HIGH
- **Requires**: explicit user confirmation + confirmation graceful kill was tried + PID `<pid>`
- **Command**:
  ```powershell
  Stop-Process -Id <pid> -Force -ErrorAction SilentlyContinue
  ```

---

## 🌐 NETWORK COMMANDS

### CMD_ID: `network.interfaces`
- **Purpose**: List all network adapters with status and IP addresses
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-NetAdapter | Select-Object Name, Status, LinkSpeed, MacAddress | Format-Table
  Get-NetIPAddress | Select-Object InterfaceAlias, AddressFamily, IPAddress | Format-Table
  ```

---

### CMD_ID: `network.routing_table`
- **Purpose**: Show default gateway and routing table
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-NetRoute | Where-Object DestinationPrefix -eq '0.0.0.0/0' | Format-Table
  ```

---

### CMD_ID: `network.active_connections`
- **Purpose**: All established and listening TCP connections with owning process IDs
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-NetTCPConnection | Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort, State, OwningProcess | Sort-Object State | Format-Table
  ```

---

### CMD_ID: `network.listening_ports`
- **Purpose**: Only ports currently listening for incoming connections
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-NetTCPConnection -State Listen | Select-Object LocalAddress, LocalPort, OwningProcess | Sort-Object LocalPort | Format-Table
  ```

---

### CMD_ID: `network.connections_with_process_names`
- **Purpose**: Active connections enriched with process names (not just PIDs)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-NetTCPConnection | Where-Object State -eq 'Established' |
    Select-Object LocalPort, RemoteAddress, RemotePort, OwningProcess,
      @{N='Process';E={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name}} |
    Sort-Object Process | Format-Table
  ```

---

### CMD_ID: `network.bandwidth_by_adapter`
- **Purpose**: Bytes sent/received per network adapter
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-NetAdapterStatistics | Select-Object Name, ReceivedBytes, SentBytes | Format-Table
  ```

---

### CMD_ID: `network.dns_config`
- **Purpose**: Show configured DNS servers per interface
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-DnsClientServerAddress | Where-Object ServerAddresses | Format-Table
  ```

---

### CMD_ID: `network.firewall_status`
- **Purpose**: Check Windows Firewall enabled state per profile (Domain/Private/Public)
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-NetFirewallProfile | Select-Object Name, Enabled | Format-Table
  ```

---

### CMD_ID: `network.firewall_rules_active`
- **Purpose**: List currently enabled inbound firewall rules
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-NetFirewallRule | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' } |
    Select-Object DisplayName, Action, Profile | Sort-Object Action | Format-Table
  ```

---

### CMD_ID: `network.vpn_detection`
- **Purpose**: Detect TAP/WireGuard/VPN adapter interfaces
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-NetAdapter | Where-Object { $_.InterfaceDescription -match "TAP|WireGuard|VPN|Tunnel" } | Format-Table
  ```

---

### CMD_ID: `network.flush_dns`
- **Purpose**: Clear the Windows DNS resolver cache
- **Risk**: LOW
- **Requires**: explicit user confirmation
- **Command**:
  ```powershell
  Clear-DnsClientCache
  ```

---

## 🚀 STARTUP / SERVICE COMMANDS

### CMD_ID: `startup.list_registry_run_user`
- **Purpose**: List current-user startup programs from registry Run key
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" |
    Select-Object * -ExcludeProperty PSPath, PSParentPath, PSChildName, PSDrive, PSProvider
  ```

---

### CMD_ID: `startup.list_registry_run_system`
- **Purpose**: List all-users startup programs from registry Run key
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" |
    Select-Object * -ExcludeProperty PSPath, PSParentPath, PSChildName, PSDrive, PSProvider
  ```

---

### CMD_ID: `startup.list_startup_folder`
- **Purpose**: List programs in the user Startup folder
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup" | Format-Table Name, LastWriteTime
  ```

---

### CMD_ID: `startup.list_scheduled_tasks`
- **Purpose**: List non-Microsoft scheduled tasks that run at logon or boot
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-ScheduledTask | Where-Object {
    $_.State -eq 'Ready' -and $_.TaskPath -notmatch '^\\Microsoft\\'
  } | Select-Object TaskName, TaskPath, State | Format-Table
  ```

---

### CMD_ID: `startup.list_auto_services`
- **Purpose**: List services configured to start automatically
- **Risk**: NONE
- **Requires**: nothing
- **Command**:
  ```powershell
  Get-Service | Where-Object StartType -eq 'Automatic' |
    Select-Object Name, DisplayName, Status | Sort-Object Status | Format-Table
  ```

---

### CMD_ID: `startup.service_status`
- **Purpose**: Check status of a specific service by name
- **Risk**: NONE
- **Requires**: service name parameter `<ServiceName>`
- **Command**:
  ```powershell
  Get-Service -Name "<ServiceName>" | Select-Object Name, DisplayName, Status, StartType | Format-List
  ```

---

### CMD_ID: `startup.disable_service`
- **Purpose**: Set a service startup type to Disabled (does not stop it immediately)
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + service name `<ServiceName>`
- **Command**:
  ```powershell
  Set-Service -Name "<ServiceName>" -StartupType Disabled
  ```

---

### CMD_ID: `startup.stop_and_disable_service`
- **Purpose**: Stop a running service and disable it from starting at boot
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + service name `<ServiceName>`
- **Command**:
  ```powershell
  Stop-Service -Name "<ServiceName>" -Force -ErrorAction SilentlyContinue
  Set-Service -Name "<ServiceName>" -StartupType Disabled
  ```

---

### CMD_ID: `startup.enable_service`
- **Purpose**: Re-enable a disabled service (sets to Automatic startup)
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + service name `<ServiceName>`
- **Command**:
  ```powershell
  Set-Service -Name "<ServiceName>" -StartupType Automatic
  ```

---

### CMD_ID: `startup.remove_registry_run_entry`
- **Purpose**: Remove a startup entry from the user Run registry key
- **Risk**: MEDIUM
- **Requires**: explicit user confirmation + entry name `<EntryName>`
- **Command**:
  ```powershell
  Remove-ItemProperty -Path "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" -Name "<EntryName>"
  ```

---

## 🚫 FORBIDDEN COMMANDS (Windows)
> These command IDs exist only to document what must NEVER be executed.
> Agents must refuse any user request that maps to these.

| CMD_ID (Forbidden)                  | Reason                                              |
|-------------------------------------|-----------------------------------------------------|
| `forbidden.delete_system32`         | Deletes Windows core OS files — completely unrecoverable |
| `forbidden.disable_uac`             | Removes User Account Control — major security risk  |
| `forbidden.disable_defender`        | Disables real-time antivirus protection             |
| `forbidden.kill_lsass`              | Kills authentication service — immediate crash      |
| `forbidden.kill_csrss`              | Kills client/server runtime — crashes the session   |
| `forbidden.delete_user_registry`    | Wipes the current user's registry hive              |
