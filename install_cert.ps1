<#
.SYNOPSIS
    Устанавливает публичный сертификат RemoteDesktop как ДОВЕРЕННЫЙ, чтобы
    Windows и антивирус (Kaspersky) считали app.exe подписанным доверенным
    издателем и не помечали его как PDM:Trojan.Win32.Generic.

.DESCRIPTION
    app.exe подписан самоподписанным сертификатом. Сам по себе он не доверен —
    этот скрипт добавляет ПУБЛИЧНЫЙ сертификат (RemoteDesktop.cer) в хранилища
    "Доверенные корневые центры" и "Доверенные издатели" локального компьютера.
    После этого поведенческая эвристика антивируса перестаёт ругаться.

    ЗАПУСКАТЬ ОТ ИМЕНИ АДМИНИСТРАТОРА на КАЖДОМ ПК, где будет запускаться app.exe.
    Файл RemoteDesktop.cer лежит рядом с app.exe (кладётся сборкой).

    Импортируется ТОЛЬКО публичный сертификат — приватного ключа здесь нет,
    подделать подпись с его помощью нельзя.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_cert.ps1
    powershell -ExecutionPolicy Bypass -File install_cert.ps1 -CerPath C:\path\RemoteDesktop.cer
#>

[CmdletBinding()]
param([string]$CerPath)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Проверка прав администратора
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ОШИБКА: нужны права администратора. ПКМ по PowerShell -> Запуск от администратора." -ForegroundColor Red
    exit 1
}

# Ищем .cer: параметр -> рядом со скриптом -> рядом в dist
if (-not $CerPath) {
    $candidates = @(
        (Join-Path $scriptDir "RemoteDesktop.cer"),
        (Join-Path $scriptDir "dist\RemoteDesktop.cer")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { $CerPath = $c; break } }
}
if (-not $CerPath -or -not (Test-Path $CerPath)) {
    Write-Host "Не найден RemoteDesktop.cer. Укажите путь: -CerPath C:\...\RemoteDesktop.cer" -ForegroundColor Red
    exit 1
}
$CerPath = (Resolve-Path $CerPath).Path
Write-Host "Сертификат: $CerPath"

Import-Certificate -FilePath $CerPath -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Write-Host "Добавлен в 'Доверенные корневые центры' (LocalMachine\Root)." -ForegroundColor Green
Import-Certificate -FilePath $CerPath -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
Write-Host "Добавлен в 'Доверенные издатели' (LocalMachine\TrustedPublisher)." -ForegroundColor Green

Write-Host ""
Write-Host "Готово. Теперь app.exe считается подписанным доверенным издателем." -ForegroundColor Green
Write-Host "Если антивирус уже держит app.exe в карантине — восстановите и добавьте в исключения." -ForegroundColor Yellow
