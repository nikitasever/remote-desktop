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
    new_basename = os.path.basename(new_exe_path)

    ps_path = os.path.join(exe_dir, "_update.ps1")
    log_path = os.path.join(exe_dir, "_update.log")
    pid = os.getpid()

    ps_content = f"""
$ErrorActionPreference = 'Stop'
$log = '{log_path}'
function Log($msg) {{ "$(Get-Date -Format o) $msg" | Out-File $log -Append -Encoding utf8 }}
Log 'Update started'
try {{
    Log 'Waiting for PID {pid}...'
    try {{ Wait-Process -Id {pid} -Timeout 30 -ErrorAction Stop }} catch {{}}
    Start-Sleep -Seconds 2
    $cur = '{current_exe}'
    $old = '{os.path.join(exe_dir, old_name)}'
    $new = '{new_exe_path}'
    if (Test-Path $old) {{ Remove-Item $old -Force; Log 'Removed old exe' }}
    Rename-Item $cur $old -Force; Log 'Renamed current to old'
    Move-Item $new $cur -Force; Log 'Moved new exe in place'
    Log 'Launching new version...'
    Start-Process $cur
    Log 'Done'
}} catch {{
    Log "FAIL: $_"
    $old = '{os.path.join(exe_dir, old_name)}'
    $cur = '{current_exe}'
    if ((Test-Path $old) -and -not (Test-Path $cur)) {{
        Rename-Item $old (Split-Path $cur -Leaf) -Force
        Log 'Restored old exe'
    }}
}}
Remove-Item '{ps_path}' -Force -ErrorAction SilentlyContinue
"""

    with open(ps_path, "w", encoding="utf-8-sig") as f:
        f.write(ps_content)

    subprocess.Popen(
        ["powershell.exe", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-File", ps_path],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
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
    for name in ("app.old.exe", "_update.bat", "_update.ps1", "_update.log"):
        path = os.path.join(exe_dir, name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
