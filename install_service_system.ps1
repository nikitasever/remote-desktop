<#
.SYNOPSIS
    Регистрирует RemoteDesktop host как задачу, выполняемую от имени
    NT AUTHORITY\SYSTEM — это включает захват ЗАЩИЩЁННОГО рабочего стола
    (UAC consent, экран входа/блокировки, Ctrl+Alt+Del / Winlogon).

.DESCRIPTION
    КОМПРОМИСС / зачем именно SYSTEM:
      * install_service.ps1 (обычный) запускает host в ИНТЕРАКТИВНОЙ сессии
        пользователя. Это правильно для обычного экрана, НО такой процесс
        НЕ имеет прав открыть рабочий стол Winlogon: во время UAC удалённый
        зритель видит чёрный/застывший кадр.
      * Чтобы видеть защищённый стол, процессу нужно:
          (а) находиться в интерактивной сессии (session 1), НЕ в session 0;
          (б) иметь привилегии SYSTEM (SeTcbPrivilege и пр.), чтобы
              OpenInputDesktop()/SetThreadDesktop() сработали на Winlogon.
        secure_capture.py делает GDI-снимок после SetThreadDesktop на
        input-desktop — это работает только при выполнении этих двух условий.

    КАК ЭТО ДОСТИГАЕТСЯ ЗДЕСЬ:
        Scheduled Task с принципалом NT AUTHORITY\SYSTEM, RunLevel Highest,
        триггер AtLogOn. Планировщик при входе пользователя запускает задачу
        SYSTEM, и (в отличие от Windows-сервиса в session 0) AtLogOn-задача
        привязывается к интерактивной сессии вошедшего пользователя. Таким
        образом процесс — SYSTEM В СЕССИИ 1, что и нужно для secure-desktop.

    ОГРАНИЧЕНИЯ / ЧЕСТНО:
      * Надёжность «SYSTEM в session 1» через Task Scheduler зависит от версии
        Windows. Если на вашей системе задача всё же стартует в session 0
        (host увидит пустой стол и обычный захват не заработает) — используйте
        ВАРИАНТ Б ниже: настоящий Windows-сервис, который зовёт
        WTSGetActiveConsoleSessionId() + CreateProcessAsUser/DuplicateTokenEx с
        токеном winlogon.exe активной сессии и запускает app.exe --rd-host там.
        Этот вариант сложнее (нужен service-wrapper на C#/pywin32 service.py с
        обработкой смены сессии), но даёт гарантированный SYSTEM-в-консоли.
      * Захват защищённого стола — для ПРОСМОТРА. Инъекция ввода на secure
        desktop ограничена системой (даже для SYSTEM нужен SetThreadDesktop на
        потоке инъекции; SAS/Ctrl+Alt+Del программно не эмулируется). Просмотр
        UAC работает; кликать по самой кнопке UAC — best-effort/может не пройти.

    Настройте relay/пароль ОДИН раз, запустив app.exe и сохранив настройки —
    --rd-host читает их из сохранённого конфига (общий для пользователя).

.PARAMETER ExePath
    Путь к app.exe. По умолчанию dist\app.exe рядом со скриптом, либо эта папка.

.PARAMETER TaskName
    Имя задачи. По умолчанию "RemoteDesktopHostSystem" (НЕ конфликтует с обычной).

.EXAMPLE
    # Запускать из АДМИНИСТРАТИВНОГО PowerShell:
    .\install_service_system.ps1
    .\install_service_system.ps1 -ExePath "C:\Path\to\app.exe"
#>

[CmdletBinding()]
param(
    [string]$ExePath,
    [string]$TaskName = "RemoteDesktopHostSystem"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Требуются права администратора (регистрация задачи от SYSTEM).
$id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object System.Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Запустите этот скрипт в PowerShell ОТ ИМЕНИ АДМИНИСТРАТОРА."
    exit 1
}

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
Write-Host "Принципал: NT AUTHORITY\SYSTEM (для захвата защищённого стола)" -ForegroundColor Cyan

# Удаляем старую задачу, если есть (идемпотентность)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Удаляю существующую задачу '$TaskName'..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $ExePath -Argument "--rd-host" `
    -WorkingDirectory (Split-Path -Parent $ExePath)

# AtLogOn без -User: срабатывает при входе ЛЮБОГО пользователя и (на большинстве
# версий Windows) привязывает SYSTEM-процесс к его интерактивной сессии.
$trigger = New-ScheduledTaskTrigger -AtLogOn

# Принципал SYSTEM с наивысшими правами.
$principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" `
    -LogonType ServiceAccount -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "RemoteDesktop host от SYSTEM (захват защищённого стола: UAC/вход)" `
    -Force | Out-Null

Write-Host ""
Write-Host "Задача '$TaskName' зарегистрирована (SYSTEM)." -ForegroundColor Green
Write-Host "Запустить сейчас:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Проверить сессию:  процесс app.exe должен быть в session 1 (Task Manager -> Details -> Session ID)."
Write-Host "Удалить:           Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host ""
Write-Host "ВНИМАНИЕ: если app.exe окажется в session 0 — захват экрана не заработает;" -ForegroundColor Yellow
Write-Host "тогда используйте обычную задачу (install_service.ps1) для экрана и держите" -ForegroundColor Yellow
Write-Host "эту SYSTEM-задачу только если она реально стартует в интерактивной сессии." -ForegroundColor Yellow
Write-Host "Захват защищённого стола (UAC) активируется автоматически в secure_capture.py." -ForegroundColor Yellow
