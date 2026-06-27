# Сборка релиза: собирает exe и создаёт GitHub Release.
# Требует: gh CLI (https://cli.github.com), авторизованный в GitHub.
# Запуск:  powershell -ExecutionPolicy Bypass -File build_release.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Читаем версию из version.py
$versionLine = Get-Content ".\version.py" | Where-Object { $_ -match '__version__' }
if ($versionLine -match '"([^"]+)"') {
    $ver = $Matches[1]
} else {
    Write-Host "Не удалось прочитать версию из version.py" -ForegroundColor Red
    exit 1
}

Write-Host "Версия: $ver" -ForegroundColor Cyan

# Собираем exe
Write-Host "`nЗапускаю build_exe.ps1..." -ForegroundColor Cyan
& "$PSScriptRoot\build_exe.ps1"

if (-not (Test-Path ".\dist\app.exe")) {
    Write-Host "Сборка не удалась." -ForegroundColor Red
    exit 1
}

# Создаём GitHub Release
Write-Host "`nСоздаю GitHub Release v$ver..." -ForegroundColor Cyan
gh release create "v$ver" ".\dist\app.exe" --title "v$ver" --generate-notes

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nРелиз v$ver опубликован!" -ForegroundColor Green
} else {
    Write-Host "`nОшибка при создании релиза." -ForegroundColor Red
    exit 1
}