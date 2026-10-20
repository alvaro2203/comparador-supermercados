#Requires -Version 5.1
<#
.SYNOPSIS
    Registers (or updates) the daily Windows Task Scheduler job for the scraper.
.PARAMETER At
    Local time of the daily run, HH:mm. Defaults to 06:30, after Mercadona publishes the day's prices.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_windows_task.ps1 -At 07:00
#>
param(
    [string]$At = "06:30",
    [string]$TaskName = "SupermarketScraperDaily"
)
$ErrorActionPreference = "Stop"
$Script = Join-Path $PSScriptRoot "run_daily.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Daily supermarket catalog and price download" -Force | Out-Null

Write-Host "Task '$TaskName' scheduled daily at $At. Run it now with: Start-ScheduledTask -TaskName $TaskName"
