#!/usr/bin/env python3
"""
Ears — Standalone Cross-Platform Audio Captioning Utility
Unified CLI, Headless FastAPI Server, and Native System Tray Entrypoint.
"""

import os
import sys
import glob
import time
import socket
import logging
import argparse
import threading
import webbrowser
from typing import List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# When launched via `pythonw.exe` on Windows, `sys.stdout` and `sys.stderr` are `None`.
# Redirect them to `data/ears_runtime.log` so `print()` and `uvicorn` never fail on `NoneType.isatty()`.
if sys.stdout is None or sys.stderr is None:
    _log_dir = os.path.join(BASE_DIR, "data")
    os.makedirs(_log_dir, exist_ok=True)
    _log_stream = open(os.path.join(_log_dir, "ears_runtime.log"), "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_stream
    if sys.stderr is None:
        sys.stderr = _log_stream
elif hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _inject_virtualenv_site_packages() -> None:
    """
    If launched via system `python3` outside an activated virtualenv,
    automatically attach local `./venv` or sibling `conciergeweb/venv` site-packages.
    Also refresh Windows PATH from the Registry & WinGet package folders so newly installed
    `ffmpeg.exe` is always discoverable even when launched from Explorer/WScript.
    """
    candidate_patterns = [
        os.path.join(BASE_DIR, "venv", "lib", "python*", "site-packages"),
        os.path.join(BASE_DIR, ".venv", "lib", "python*", "site-packages"),
        os.path.join(BASE_DIR, "venv", "Lib", "site-packages"),
        "/home/fuzz/Projects/ears/venv/lib/python*/site-packages",
        "/home/fuzz/Projects/conciergeweb/venv/lib/python*/site-packages",
    ]
    for pattern in candidate_patterns:
        for sp in sorted(glob.glob(pattern), reverse=True):
            if os.path.isdir(sp) and sp not in sys.path:
                sys.path.insert(0, sp)

    if sys.platform.startswith("linux"):
        # Allow isolated venv to import system PyGObject (`gi`) & AyatanaAppIndicator3 for Ubuntu Desktop Tray
        for dist_dir in ["/usr/lib/python3/dist-packages"] + sorted(glob.glob("/usr/lib/python3.*/dist-packages")):
            if os.path.isdir(dist_dir) and dist_dir not in sys.path:
                sys.path.append(dist_dir)
    elif sys.platform == "darwin":
        # Ensure Homebrew paths (Apple Silicon /opt/homebrew/bin and Intel /usr/local/bin) are in PATH
        cur_path = os.environ.get("PATH", "")
        for brew_bin in ("/opt/homebrew/bin", "/usr/local/bin"):
            if os.path.isdir(brew_bin) and brew_bin not in cur_path:
                cur_path = brew_bin + os.pathsep + cur_path
        os.environ["PATH"] = cur_path

    if sys.platform == "win32":
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        extra_paths = [
            os.path.join(BASE_DIR, "runtime", "ffmpeg"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links"),
        ]
        winget_ffmpeg_glob = os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Microsoft",
            "WinGet",
            "Packages",
            "*FFmpeg*",
            "*",
            "bin",
        )
        extra_paths.extend(glob.glob(winget_ffmpeg_glob))
        try:
            import winreg
            for root_key, sub_key in [
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                (winreg.HKEY_CURRENT_USER, r"Environment"),
            ]:
                try:
                    k = winreg.OpenKey(root_key, sub_key, 0, winreg.KEY_READ)
                    val, _ = winreg.QueryValueEx(k, "Path")
                    winreg.CloseKey(k)
                    if val:
                        extra_paths.extend(os.path.expandvars(p) for p in val.split(";") if p)
                except Exception:
                    pass
        except Exception:
            pass
        cur_path = os.environ.get("PATH", "")
        for p in extra_paths:
            if p and os.path.isdir(p) and p.lower() not in cur_path.lower():
                cur_path = p + os.pathsep + cur_path
        os.environ["PATH"] = cur_path


_inject_virtualenv_site_packages()

from backend.storage import load_config, save_config, get_storage_paths  # noqa: E402
from backend.hardware import probe_and_select_hardware  # noqa: E402


def get_lan_ips() -> List[str]:
    """Return non-loopback IPv4 addresses on this machine for the startup banner."""
    ips = []
    try:
        from backend.discovery import get_local_interfaces
        for iface in get_local_interfaces():
            ip = iface.get("ip", "")
            if ip and not ip.startswith("127.") and not ip.startswith("172.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                ips.append(ip)
        except Exception:
            pass
    return ips


def has_gui_display() -> bool:
    """Return True if a desktop GUI session is available for the System Tray."""
    if sys.platform == "win32" or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _open_browser_when_ready(port: int, timeout_sec: float = 30.0) -> None:
    """Wait until the local FastAPI/Uvicorn port is accepting connections before opening the browser."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                webbrowser.open(f"http://127.0.0.1:{port}")
                return
        except OSError:
            time.sleep(0.25)


def print_startup_banner(host: str, port: int, mode: str) -> None:
    cfg = load_config()
    hw = probe_and_select_hardware(profile_override=cfg.get("PERFORMANCE_PROFILE", "auto"))
    paths = get_storage_paths(cfg)
    lan_ips = get_lan_ips()

    print("=" * 78)
    print("  EARS — ZERO-GAP RTSP AUDIO CAPTIONING UTILITY")
    print("=" * 78)
    print(f"  Mode             : {mode}")
    print(f"  Hardware Profile : {hw['banner_badge']} ({hw['cpu_threads']} CPU threads)")
    print(f"  Storage Folder   : {paths['base']}")
    print(f"  Save Audio Clips : {'ON (WAV clips saved)' if cfg.get('SAVE_AUDIO_FILES') else 'OFF (Text transcripts only)'}")
    print(f"  Retention Window : {cfg.get('RETENTION_DAYS', 30)} days")
    print("-" * 78)
    print(f"  Local Web UI     : http://127.0.0.1:{port}")
    if host == "0.0.0.0" and lan_ips:
        for ip in lan_ips:
            print(f"  LAN Web UI       : http://{ip}:{port}  (Password login required on LAN)")
    print("=" * 78, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ears — Standalone RTSP Audio Captioning Utility")
    parser.add_argument("--headless", action="store_true", help="Run Web UI & Captioning Server without System Tray")
    parser.add_argument("--tray", action="store_true", help="Run with Native System Tray icon (default when GUI available)")
    parser.add_argument("--host", type=str, default=None, help="Bind host (default: 0.0.0.0 when LAN access enabled)")
    parser.add_argument("--port", type=int, default=None, help="Web UI port (default from config.json: 8765)")
    parser.add_argument("--open-browser", action="store_true", help="Automatically open the Web UI in default browser")
    parser.add_argument("--log-level", type=str, default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    log_dir = os.path.join(BASE_DIR, "data")
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, "ears_runtime.log")
    handlers: List[logging.Handler] = [
        logging.FileHandler(log_file_path, encoding="utf-8"),
    ]
    if sys.stdout is not None and getattr(sys.stdout, "name", "") != log_file_path:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )

    cfg = load_config()
    port = args.port or int(cfg.get("WEB_PORT", 8765))
    if args.port and args.port != cfg.get("WEB_PORT"):
        save_config({"WEB_PORT": port})

    # Bind to 0.0.0.0 by default so headless hosts (like ConciergeHub) can be configured over the LAN
    # during first-run setup or whenever ALLOW_LAN_ACCESS is enabled.
    if args.host:
        host = args.host
    else:
        first_run = not bool(cfg.get("FIRST_RUN_COMPLETE", False) and cfg.get("WEB_PASSWORD_HASH"))
        allow_lan = bool(cfg.get("ALLOW_LAN_ACCESS", True))
        host = "0.0.0.0" if (first_run or allow_lan or args.headless or not has_gui_display()) else "127.0.0.1"

    use_tray = False
    if args.tray and has_gui_display():
        use_tray = True
    elif not args.headless and has_gui_display():
        try:
            import pystray  # noqa: F401
            use_tray = True
        except ImportError:
            use_tray = False

    mode_label = "System Tray + Background Server" if use_tray else "Headless Server"
    print_startup_banner(host, port, mode_label)

    if args.open_browser and has_gui_display():
        threading.Thread(target=_open_browser_when_ready, args=(port,), daemon=True).start()

    import uvicorn
    from backend.app import app

    if use_tray:
        try:
            from backend.tray import run_system_tray
            run_system_tray(app=app, host=host, port=port)
            return
        except Exception as e:
            logging.warning(f"System tray unavailable ({e}); continuing in headless server mode.")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=args.log_level,
        log_config=None,
        timeout_graceful_shutdown=2,
    )


if __name__ == "__main__":
    main()
