# Сборка RemoteDesktop в один app.exe (GUI-лаунчер: host + client в одном окне).
# Запуск:  powershell -ExecutionPolicy Bypass -File build_exe.ps1
#
# Результат: .\dist\app.exe — самодостаточный, Python ставить не нужно.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Берём python из локального .venv, если он есть, иначе системный
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Write-Host "Устанавливаю зависимости + PyInstaller..." -ForegroundColor Cyan
& $py -m pip install -r requirements.txt
& $py -m pip install pyinstaller

# Записываем дату сборки в version.py
$buildDate = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$verContent = Get-Content ".\version.py" -Encoding utf8
$verContent = $verContent -replace '__build_date__\s*=\s*"[^"]*"', "__build_date__ = `"$buildDate`""
$verContent | Set-Content ".\version.py" -Encoding utf8
Write-Host "Build date: $buildDate" -ForegroundColor Gray

# Если app.exe запущен — он залочит dist\app.exe, PyInstaller молча НЕ перезапишет
# его, и в релиз уедет старый бинарь. Глушим запущенные копии и удаляем старый exe.
Get-Process -Name app -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
if (Test-Path ".\dist\app.exe") { Remove-Item ".\dist\app.exe" -Force -ErrorAction SilentlyContinue }
if (Test-Path ".\dist\app.exe") {
    Write-Host "ОШИБКА: dist\app.exe залочен (запущен app.exe?). Закройте его." -ForegroundColor Red
    exit 1
}

# Метаданные exe (имя продукта/версия/компания). Безымянный exe без ресурса
# версии выглядит подозрительнее для антивируса — добавляем нормальные поля.
$verStr = ([regex]::Match((Get-Content ".\version.py" -Raw), '__version__\s*=\s*"([^"]+)"')).Groups[1].Value
if (-not $verStr) { $verStr = "0.0.0" }
$vp = $verStr.Split('.'); while ($vp.Count -lt 4) { $vp += '0' }
$vtuple = "$($vp[0]), $($vp[1]), $($vp[2]), $($vp[3])"
$versionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($vtuple), prodvers=($vtuple),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Nikita Sever'),
      StringStruct('FileDescription', 'RemoteDesktop'),
      StringStruct('FileVersion', '$verStr'),
      StringStruct('InternalName', 'app'),
      StringStruct('OriginalFilename', 'app.exe'),
      StringStruct('ProductName', 'RemoteDesktop'),
      StringStruct('ProductVersion', '$verStr'),
      StringStruct('LegalCopyright', '(c) Nikita Sever')
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
$versionInfo | Set-Content ".\version_info.txt" -Encoding ascii

Write-Host "`nСобираю app.exe..." -ForegroundColor Cyan
# --uac-admin: app.exe запрашивает повышение прав при запуске. Нужно, чтобы
# хост мог инжектить ввод в окна программ, запущенных от администратора
# (иначе клики/клавиши не доходят до UAC-elevated окон).
& $py -m PyInstaller --onefile --windowed --noupx --name app `
    --uac-admin `
    --version-file version_info.txt `
    --hidden-import pynput.keyboard._win32 `
    --hidden-import pynput.mouse._win32 `
    --hidden-import customtkinter `
    --hidden-import pygame `
    --hidden-import pygame._sdl2 `
    --hidden-import pygame.display `
    --hidden-import pygame.event `
    --collect-submodules pygame `
    --collect-submodules mss `
    --collect-submodules customtkinter `
    --hidden-import host_ui `
    --hidden-import video `
    --hidden-import audio `
    --hidden-import adaptive `
    --hidden-import rtc_common `
    --hidden-import host_rtc `
    --hidden-import client_rtc `
    --hidden-import service `
    --hidden-import cv2 `
    --collect-submodules cv2 `
    --hidden-import settings_ui `
    --hidden-import settings_config `
    --hidden-import settings_display `
    --hidden-import settings_interface `
    --hidden-import settings_security `
    --hidden-import settings_connection `
    --hidden-import settings_audio `
    --hidden-import settings_autostart `
    app.py

# Проверяем, что exe реально свежий (создан в эту сборку), а не остался старым.
if (Test-Path ".\dist\app.exe") {
    $age = (Get-Date) - (Get-Item ".\dist\app.exe").LastWriteTime
    if ($age.TotalMinutes -gt 5) {
        Write-Host "`nОШИБКА: dist\app.exe не обновился (старый файл). Сборка НЕ удалась." -ForegroundColor Red
        exit 1
    }
    Write-Host "`nГотово: .\dist\app.exe (свежий)" -ForegroundColor Green

    # --- Подпись кода самоподписанным сертификатом ---
    # Подписанный exe резко снижает поведенческие ложные срабатывания антивируса
    # (PDM:Trojan.Win32.Generic у Kaspersky). Сертификат самоподписанный: на ЧУЖИХ
    # ПК он не доверен, но на СВОИХ — после установки публичного .cer как
    # доверенного (см. install_cert.ps1) Windows/Kaspersky считают exe подписанным
    # доверенным издателем. Приватный ключ хранится в Cert:\CurrentUser\My и НЕ
    # экспортируется/НЕ коммитится — наружу уходит только публичный .cer.
    $certSubject = "CN=RemoteDesktop Self-Signed"
    $cert = Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -eq $certSubject -and $_.HasPrivateKey } | Select-Object -First 1
    if (-not $cert) {
        Write-Host "Создаю самоподписанный code-signing сертификат..." -ForegroundColor Cyan
        $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $certSubject `
            -CertStoreLocation Cert:\CurrentUser\My -KeyExportPolicy Exportable `
            -KeyUsage DigitalSignature -KeyAlgorithm RSA -KeyLength 2048 `
            -NotAfter (Get-Date).AddYears(10)
    }
    # Подписываем (с меткой времени, если есть интернет; иначе без неё).
    try {
        Set-AuthenticodeSignature -FilePath ".\dist\app.exe" -Certificate $cert `
            -HashAlgorithm SHA256 -TimestampServer "http://timestamp.digicert.com" -ErrorAction Stop | Out-Null
    } catch {
        Set-AuthenticodeSignature -FilePath ".\dist\app.exe" -Certificate $cert -HashAlgorithm SHA256 | Out-Null
    }
    $sig = Get-AuthenticodeSignature ".\dist\app.exe"
    Write-Host "Подпись exe: $($sig.Status) ($($sig.SignerCertificate.Subject))" -ForegroundColor Green
    # Публичный сертификат рядом с exe — для установки на втором ПК.
    Export-Certificate -Cert $cert -FilePath ".\dist\RemoteDesktop.cer" -Force | Out-Null
    Write-Host "Публичный сертификат: .\dist\RemoteDesktop.cer" -ForegroundColor Gray

    Write-Host "Скопируйте app.exe + RemoteDesktop.cer на оба ПК." -ForegroundColor Yellow
    Write-Host "На КАЖДОМ ПК один раз: install_cert.ps1 (от админа) — чтобы антивирус доверял подписи." -ForegroundColor Yellow
} else {
    Write-Host "`nСборка не удалась — смотрите вывод выше." -ForegroundColor Red
    exit 1
}
