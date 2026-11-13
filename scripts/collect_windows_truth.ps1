# Collect ground truth from the monitored Windows host for verify_windows_dashboard.py.
$ErrorActionPreference = 'Stop'
$computer = Get-CimInstance Win32_ComputerSystem
$operatingSystem = Get-CimInstance Win32_OperatingSystem
$systemDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"

$boot = $operatingSystem.LastBootUpTime.ToUniversalTime()
$uptime = ((Get-Date).ToUniversalTime() - $boot).TotalSeconds

[pscustomobject]@{
  hostname          = $computer.Name
  total_memory_bytes = [double]$computer.TotalPhysicalMemory
  free_memory_bytes  = [double]$operatingSystem.FreePhysicalMemory * 1KB
  c_free_bytes       = [double]$systemDrive.FreeSpace
  uptime_seconds     = [double]$uptime
  last_boot_utc      = $boot.ToString('o')
  process_count      = (Get-Process).Count
  running_services   = (Get-Service | Where-Object Status -eq 'Running').Count
} | ConvertTo-Json
