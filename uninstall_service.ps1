<#
.SYNOPSIS
    Removes the Remote Desktop Host scheduled task.

.PARAMETER TaskName
    Name of the Scheduled Task to remove. Default: "RemoteDesktopHost".

.EXAMPLE
    .\uninstall_service.ps1
    .\uninstall_service.ps1 -TaskName "MyCustomTaskName"
#>

[CmdletBinding()]
param(
    [string]$TaskName = "RemoteDesktopHost"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --- Check for admin ---
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Warning "This script must be run as Administrator."
    exit 1
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Task '$TaskName' not found -- nothing to remove."
    exit 0
}

# Stop the task if running
if ($existing.State -eq 'Running') {
    Write-Host "Stopping task '$TaskName'..."
    Stop-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Task '$TaskName' removed successfully." -ForegroundColor Green
