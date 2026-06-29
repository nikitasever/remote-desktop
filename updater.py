"""
Self-update module for remote-desktop.
Uses GitHub Releases as the update source. Stdlib only.
"""

import json
import os
import sys
import subprocess
import tempfile
import urllib.request
import urllib.error
from version import __version__, __build_date__

GITHUB_API_URL = "https://api.github.com/repos/nikitasever/remote-desktop/releases/latest"
ASSET_NAME = "app.exe"


def _parse_version(v: str):
    """Parse version string like '1.2.3' into tuple of ints."""
    v = v.lstrip("vV")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_frozen() -> bool:
    """True if running as a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def check_for_update() -> tuple:
    """
    Check GitHub for a newer release.
    Returns (has_update, latest_version, download_url, changelog).
    On error returns (False, __version__, "", "").
    """
    try:
        req = urllib.request.Request(GITHUB_API_URL, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return (False, __version__, "", "")

    tag = data.get("tag_name", "")
    latest = tag.lstrip("vV")
    changelog = data.get("body", "") or ""

    # Find the exe asset
    download_url = ""
    asset_updated = ""
    for asset in data.get("assets", []):
        if asset.get("name", "").lower() == ASSET_NAME:
            download_url = asset["browser_download_url"]
            asset_updated = asset.get("updated_at", "")
            break

    if not download_url:
        return (False, latest, "", changelog)

    # Newer version number → update
    if _parse_version(latest) > _parse_version(__version__):
        return (True, latest, download_url, changelog)

    # Same version — compare build dates (covers re-uploaded releases)
    if _parse_version(latest) == _parse_version(__version__) and asset_updated and __build_date__:
        if asset_updated > __build_date__:
            return (True, latest, download_url, changelog)

    return (False, latest, "", changelog)


def download_update(url: str, progress_callback=None) -> str:
    """
    Download the new exe to a temp file.
    progress_callback(bytes_downloaded, total_bytes) is called periodically.
    Returns path to the downloaded file.
    """
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".exe", prefix="rd_update_")
            tmp_path = tmp.name
            downloaded = 0
            chunk_size = 64 * 1024
            try:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    tmp.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)
            finally:
                tmp.close()
    except (urllib.error.URLError, OSError, ConnectionError) as exc:
        raise RuntimeError(f"Не удалось скачать обновление: {exc}") from exc
    return tmp_path


def apply_update(new_exe_path: str):
    """
    Replace the running exe with the new one via a helper batch script.
    Only works when frozen (PyInstaller exe). In dev mode prints a message.
    """
    if not is_frozen():
        print(f"[updater] Dev mode: new exe downloaded to {new_exe_path}")
        print("[updater] apply_update only works in frozen (PyInstaller) builds.")
        return

    current_exe = sys.executable
    exe_dir = os.path.dirname(current_exe)
    exe_name = os.path.basename(current_exe)
    old_name = exe_name.replace(".exe", ".old.exe")
    old_path = os.path.join(exe_dir, old_name)
    log_path = os.path.join(exe_dir, "_update.log")
    pid = os.getpid()

    def _q(path):
        # PowerShell single-quoted literal: double any embedded single quotes
        return "'" + path.replace("'", "''") + "'"

    # ВАЖНО (антивирус): НЕ используем скрытое окно + base64 -EncodedCommand —
    # это топ-маркер малвари (PDM/эвристика Kaspersky именно на него и реагирует).
    # Вместо этого пишем обычный читаемый .ps1 и запускаем его через -File в
    # МИНИМИЗИРОВАННОМ (не скрытом) окне. Все пути (в т.ч. кириллица) лежат ВНУТРИ
    # файла как PS-литералы, а сам файл — в %TEMP% (ascii-путь), поэтому в команду
    # кириллица не попадает (это и был старый баг 'ascii codec'). Файл — UTF-8 с
    # BOM, чтобы PowerShell корректно прочитал кириллицу в путях.
    ps_content = (
        "$ErrorActionPreference = 'Continue'\r\n"
        f"$log = {_q(log_path)}\r\n"
        "function Log($m) { \"$(Get-Date -Format o) $m\" | Out-File -FilePath $log -Append -Encoding utf8 }\r\n"
        "Log 'Update started'\r\n"
        f"$cur = {_q(current_exe)}\r\n"
        f"$old = {_q(old_path)}\r\n"
        f"$new = {_q(new_exe_path)}\r\n"
        f"try {{ Wait-Process -Id {pid} -Timeout 30 -ErrorAction Stop }} catch {{}}\r\n"
        "Start-Sleep -Seconds 2\r\n"
        "try {\r\n"
        "    if (Test-Path $old) { Remove-Item $old -Force }\r\n"
        "    Rename-Item $cur $old -Force; Log 'Renamed current to old'\r\n"
        "    Move-Item $new $cur -Force; Log 'Moved new exe in place'\r\n"
        # explorer.exe запускает exe в обычной сессии/десктопе пользователя и
        # полностью отвязывает от хелпера. Start-Process оставляет --windowed exe
        # ВИСЕТЬ на старте (проверено) — explorer нет.
        "    explorer.exe $cur; Log 'Launched new version'\r\n"
        "} catch {\r\n"
        "    Log \"FAIL: $_\"\r\n"
        "    if ((Test-Path $old) -and -not (Test-Path $cur)) {\r\n"
        "        Rename-Item $old (Split-Path $cur -Leaf) -Force; Log 'Restored old exe'\r\n"
        "        explorer.exe $cur\r\n"
        "    }\r\n"
        "}\r\n"
        # хелпер удаляет сам себя из %TEMP% по завершении
        "try { Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force } catch {}\r\n"
    )

    helper = os.path.join(tempfile.gettempdir(), "rd_update_helper.ps1")
    with open(helper, "w", encoding="utf-8-sig") as f:
        f.write(ps_content)

    # CRITICAL для PyInstaller --windowed: у замороженного app нет консоли и его
    # std-хэндлы невалидны — subprocess.Popen ОБЯЗАН перенаправить stdin/out/err в
    # DEVNULL, иначе дочерний процесс не стартует в реальном exe. Окно
    # минимизировано (видимое, не скрытое) — намеренно, чтобы не походить на малварь.
    try:
        devnull = open(os.devnull, "wb")
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Minimized", "-File", helper],
            stdin=subprocess.DEVNULL,
            stdout=devnull,
            stderr=devnull,
            close_fds=True,
        )
    except Exception:
        # Запасной путь: запустить хелпер через cmd 'start'.
        os.system(f'start "" /min powershell -NoProfile -ExecutionPolicy Bypass -File "{helper}"')
    os._exit(0)


def check_and_update(progress_callback=None):
    """
    All-in-one: check for update, download if available, apply.
    Raises RuntimeError with user-facing message on no-update or errors.
    """
    has_update, latest, url, changelog = check_for_update()
    if not has_update:
        raise RuntimeError(f"Установлена актуальная версия ({__version__}).")
    new_exe = download_update(url, progress_callback)
    apply_update(new_exe)


def cleanup_old_update():
    """Delete leftover files from a previous update. Call at app startup."""
    if not is_frozen():
        return
    exe_dir = os.path.dirname(sys.executable)
    # NB: keep _update.log — it survives the relaunch as a diagnostic trail
    # ("Launched new version" present == the relaunch step ran).
    for name in ("app.old.exe", "_update.bat", "_update.ps1"):
        path = os.path.join(exe_dir, name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
