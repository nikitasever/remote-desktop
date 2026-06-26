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

Write-Host "`nСобираю app.exe..." -ForegroundColor Cyan
& $py -m PyInstaller --onefile --windowed --name app `
    --hidden-import pynput.keyboard._win32 `
    --hidden-import pynput.mouse._win32 `
    --collect-submodules mss `
    app.py

if (Test-Path ".\dist\app.exe") {
    Write-Host "`nГотово: .\dist\app.exe" -ForegroundColor Green
    Write-Host "Скопируйте app.exe на оба ПК и запускайте двойным кликом." -ForegroundColor Yellow
} else {
    Write-Host "`nСборка не удалась — смотрите вывод выше." -ForegroundColor Red
}
