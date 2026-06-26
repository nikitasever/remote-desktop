<#
.SYNOPSIS
    Registers a Scheduled Task that runs the remote-desktop host at system startup
    (before any user logs in), under the SYSTEM account.

.DESCRIPTION
    Uses Register-ScheduledTask (Windows 10+) to create a task that:
      - Triggers at system startup (boot)
      - Runs whether or not a user is logged on
      - Runs as NT AUTHORITY\SYSTEM
      - Auto-restarts on failure (via service.py --restart-delay)

    The task invokes service.py with the parameters you supply here.
    Requires an elevated (Administrator) PowerShell session.

.PARAMETER Mode
    Connection mode: "relay" or "listen".

.PARAMETER Relay
    Relay address (host:port). Required if Mode is "relay".

.PARAMETER Id
    Relay room ID. Default: "default".

.PARAMETER ListenPort
    Direct-listen port. Required if Mode is "listen".

.PARAMETER Password
    Shared E2E password (required).

.PARAMETER PythonPath
    Path to python.exe. Default: python.exe in the venv next to this script's repo.

.PARAMETER TaskName
    Name of the Scheduled Task. Default: "RemoteDesktopHost".

.EXAMPLE
    # Relay mode
    .\install_service.ps1 -Mode relay -Relay "vps.example.com:5800" -Id myroom -Password "SECRET"

    # Direct mode
    .\install_service.ps1 -Mode listen -ListenPort 5900 -Password "SECRET"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("relay", "listen")]
    [string]$Mode,

    [string]$Relay,
    [string]$Id = "default",
    [int]$ListenPort = 5900,

    [Parameter(Mandatory)]
    [string]$Password,

    [string]$PythonPath,
    [string]$TaskName = "RemoteDesktopHost"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --- Resolve paths ---
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$servicePy = Join-Path $scriptDir "service.py"

if (-not (Test-Path $servicePy)) {
    Write-Error "service.py not found at $servicePy"
    exit 1
}

if (-not $PythonPath) {
    # Try venv in the parent repo directory first
    $venvPy = Join-Path (Split-Path -Parent $scriptDir) ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) {
        $PythonPath = $venvPy
    } else {
        $venvPy = Join-Path $scriptDir ".venv\Scripts\python.exe"
        if (Test-Path $venvPy) {
            $PythonPath = $venvPy
        } else {
            $PythonPath = "python.exe"
        }
    }
}

Write-Host "Python: $PythonPath"
Write-Host "Service script: $servicePy"

# --- Build arguments for service.py ---
$svcArgs = @("`"$servicePy`"")

if ($Mode -eq "relay") {
    if (-not $Relay) {
        Write-Error "-Relay is required when Mode is 'relay'"
        exit 1
    }
    $svcArgs += "--relay", $Relay, "--id", $Id
} else {
    $svcArgs += "--listen", $ListenPort
}

$svcArgs += "--password", $Password

$argString = $svcArgs -join " "

# --- Check for admin ---
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Warning @"
This script must be run as Administrator to register a SYSTEM-level Scheduled Task.
Re-run this PowerShell session as Administrator.
"@
    exit 1
}

# --- Remove existing task if present (idempotent) ---
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task '$TaskName'..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# --- Create Scheduled Task ---
$action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument $argString `
    -WorkingDirectory $scriptDir

$trigger = New-ScheduledTaskTrigger -AtStartup

$principal = New-ScheduledTaskPrincipal `
    -UserId "NT AUTHORITY\SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Remote Desktop Host (unattended mode) - runs service.py at boot as SYSTEM" `
    -Force

Write-Host ""
Write-Host "Scheduled Task '$TaskName' registered successfully." -ForegroundColor Green
Write-Host "It will start automatically at next boot."
Write-Host "To start it now:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To check status:  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host ""
Write-Host "To uninstall:     .\uninstall_service.ps1 -TaskName '$TaskName'"
