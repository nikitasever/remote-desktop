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

Write-Host "`nСобираю app.exe..." -ForegroundColor Cyan
# --uac-admin: app.exe запрашивает повышение прав при запуске. Нужно, чтобы
# хост мог инжектить ввод в окна программ, запущенных от администратора
# (иначе клики/клавиши не доходят до UAC-elevated окон).
& $py -m PyInstaller --onefile --windowed --noupx --name app `
    --uac-admin `
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
    Write-Host "Скопируйте app.exe на оба ПК и запускайте двойным кликом." -ForegroundColor Yellow
} else {
    Write-Host "`nСборка не удалась — смотрите вывод выше." -ForegroundColor Red
    exit 1
}
