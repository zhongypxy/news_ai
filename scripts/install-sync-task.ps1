[CmdletBinding()]
param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [string]$TaskName = 'GlobalAI-Daily-Sync'
)
$ErrorActionPreference = 'Stop'
$taskRepoRoot = (Resolve-Path -LiteralPath $RepoPath).Path
$taskSyncScript = Join-Path $taskRepoRoot 'scripts\sync-windows.ps1'
if (-not (Test-Path -LiteralPath $taskSyncScript)) { throw 'Sync script not found.' }
$taskTimezone = Get-TimeZone
if ($taskTimezone.BaseUtcOffset.TotalHours -ne 8 -or $taskTimezone.SupportsDaylightSavingTime) {
    throw 'This installer requires a UTC+8 Windows timezone without DST. Configure the task manually otherwise.'
}
$taskExisting = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($taskExisting) { throw 'A task with this name already exists. Inspect it before updating.' }
$taskArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -RepoPath "{1}"' -f $taskSyncScript, $taskRepoRoot
$taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArguments -WorkingDirectory $taskRepoRoot
$taskMorning = New-ScheduledTaskTrigger -Daily -At '10:00'
$taskFallback = New-ScheduledTaskTrigger -Daily -At '11:00'
$taskLogon = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$taskOptions = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger @($taskMorning, $taskFallback, $taskLogon) -Settings $taskOptions -Principal $taskPrincipal -Description 'Synchronize verified AI daily reports from GitHub (UTC+8 10:00, 11:00 and logon).' | Out-Null
Write-Output 'Sync task installed: 10:00, 11:00 and sign-in; UTC+8, current user.'
