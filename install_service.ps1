<#
.SYNOPSIS
    Registers RemoteDesktop host to start automatically at user logon, so the
    machine is always reachable (like AnyDesk's unattended access).

.DESCRIPTION
    Creates a Scheduled Task that runs `app.exe --rd-host` AT LOGON of the
    current user, with highest privileges and auto-restart on failure.

    IMPORTANT — why logon (user session) and NOT a SYSTEM service:
    A Windows service / SYSTEM task runs in session 0 and can only capture
    session-0's desktop, NOT the logged-in user's screen. For screen sharing
    to actually work, the host MUST run inside the user's interactive session.
    Therefore this registers a per-user logon task, which is the correct
    "service for correct operation" for a remote-desktop host.

    Configure connection/password FIRST by running app.exe once and saving
    settings (relay address + password); --rd-host reads that saved config.

.PARAMETER ExePath
    Path to app.exe. Default: dist\app.exe next to this script, else this dir.

.PARAMETER TaskName
    Scheduled Task name. Default: "RemoteDesktopHost".

.EXAMPLE
    .\install_service.ps1
    .\install_service.ps1 -ExePath "C:\Path\to\app.exe"
#>

[CmdletBinding()]
param(
    [string]$ExePath,
    [string]$TaskName = "RemoteDesktopHost"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

if (-not $ExePath) {
    $candidates = @(
        (Join-Path $scriptDir "dist\app.exe"),
        (Join-Path $scriptDir "app.exe")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { $ExePath = $c; break } }
}
if (-not $ExePath -or -not (Test-Path $ExePath)) {
    Write-Error "app.exe не найден. Укажите путь: -ExePath C:\...\app.exe"
    exit 1
}
$ExePath = (Resolve-Path $ExePath).Path
Write-Host "Exe: $ExePath"

# Текущий пользователь — задача запускается в ЕГО сессии (нужно для захвата экрана)
$user = "$env:USERDOMAIN\$env:USERNAME"
Write-Host "Пользователь: $user"

# Удаляем старую задачу, если есть (идемпотентность)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Удаляю существующую задачу '$TaskName'..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $ExePath -Argument "--rd-host" `
    -WorkingDirectory (Split-Path -Parent $ExePath)

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user

$principal = New-ScheduledTaskPrincipal -UserId $user `
    -LogonType Interactive -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "RemoteDesktop host (автозапуск при входе пользователя)" -Force | Out-Null

Write-Host ""
Write-Host "Задача '$TaskName' зарегистрирована." -ForegroundColor Green
Write-Host "Хост будет стартовать автоматически при входе в Windows."
Write-Host "Запустить сейчас:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Статус:            Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Удалить:           .\uninstall_service.ps1"
Write-Host ""
Write-Host "ВАЖНО: сначала запустите app.exe один раз, укажите relay/пароль и" -ForegroundColor Yellow
Write-Host "включите хост — эти настройки сохранятся и будут использованы --rd-host." -ForegroundColor Yellow
