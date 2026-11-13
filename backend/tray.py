"""
Ears — Cross-Platform Native System Tray Controller (Windows, macOS, Linux Desktop)
Provides dynamic Ear/Waveform icon with status dot, quick toggles, retention & performance
submenus, native folder picker, Start-at-Login registration, and graceful Uvicorn lifecycle.
Supports both embedded Uvicorn server mode AND attaching to an already-running `ears.service` daemon.
"""

import os
import sys
import glob
import time
import socket
import logging
import threading
import subprocess
import webbrowser
from typing import Optional

from backend.storage import BASE_DIR, load_config, save_config, get_storage_paths

logger = logging.getLogger("ears.tray")


def _setup_linux_appindicator_shim() -> None:
    """
    On Ubuntu 22.04 / 24.04 GNOME Desktop:
    1. Ensure `/usr/lib/python3/dist-packages` is on `sys.path` so isolated `venv` can import `gi`.
    2. Canonical replaced `AppIndicator3` with `AyatanaAppIndicator3`, which has an identical API.
       Alias `AyatanaAppIndicator3` as `AppIndicator3` so `pystray`'s `appindicator` backend
       renders natively in the Ubuntu GNOME top panel instead of falling back to invisible `xorg`.
    """
    if not sys.platform.startswith("linux"):
        return

    for dist_dir in ["/usr/lib/python3/dist-packages"] + sorted(glob.glob("/usr/lib/python3.*/dist-packages")):
        if os.path.isdir(dist_dir) and dist_dir not in sys.path:
            sys.path.append(dist_dir)

    try:
        import gi
        orig_req = gi.require_version

        def _patched_require_version(namespace: str, version: str) -> None:
            if namespace == "AppIndicator3":
                try:
                    return orig_req("AppIndicator3", version)
                except ValueError:
                    return orig_req("AyatanaAppIndicator3", version)
            return orig_req(namespace, version)

        gi.require_version = _patched_require_version
        try:
            orig_req("AppIndicator3", "0.1")
        except ValueError:
            orig_req("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3
            import gi.repository
            setattr(gi.repository, "AppIndicator3", AyatanaAppIndicator3)
            sys.modules["gi.repository.AppIndicator3"] = AyatanaAppIndicator3
    except Exception as e:
        logger.debug(f"Linux AppIndicator shim note: {e}")


# ==============================================================================
# START AT LOGIN REGISTRATION (WINDOWS / MACOS / LINUX DESKTOP)
# ==============================================================================

def is_autostart_enabled() -> bool:
    """Check whether Ears is configured to launch automatically at user login."""
    try:
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            )
            try:
                val, _ = winreg.QueryValueEx(key, "EarsAudioCaptioner")
                return bool(val)
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        elif sys.platform == "darwin":
            plist = os.path.expanduser("~/Library/LaunchAgents/com.ears.captioner.plist")
            return os.path.isfile(plist)
        else:
            desktop = os.path.expanduser("~/.config/autostart/ears.desktop")
            return os.path.isfile(desktop)
    except Exception:
        return False


def set_autostart_enabled(enable: bool) -> bool:
    """Enable or disable automatic startup at user login across Windows, macOS, and Linux."""
    ears_script = os.path.join(BASE_DIR, "ears.py")
    python_exe = sys.executable

    try:
        if sys.platform == "win32":
            import winreg
            pythonw = python_exe.replace("python.exe", "pythonw.exe")
            if not os.path.isfile(pythonw):
                pythonw = python_exe
            cmd = f'"{pythonw}" "{ears_script}" --tray'
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                if enable:
                    winreg.SetValueEx(key, "EarsAudioCaptioner", 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, "EarsAudioCaptioner")
                    except FileNotFoundError:
                        pass
            finally:
                winreg.CloseKey(key)
            return True

        elif sys.platform == "darwin":
            plist_dir = os.path.expanduser("~/Library/LaunchAgents")
            plist_path = os.path.join(plist_dir, "com.ears.captioner.plist")
            if not enable:
                if os.path.isfile(plist_path):
                    os.remove(plist_path)
                return True
            os.makedirs(plist_dir, exist_ok=True)
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ears.captioner</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_exe}</string>
        <string>{ears_script}</string>
        <string>--tray</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{BASE_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""
            with open(plist_path, "w", encoding="utf-8") as f:
                f.write(plist_content)
            return True

        else:
            autostart_dir = os.path.expanduser("~/.config/autostart")
            desktop_path = os.path.join(autostart_dir, "ears.desktop")
            if not enable:
                if os.path.isfile(desktop_path):
                    os.remove(desktop_path)
                return True
            os.makedirs(autostart_dir, exist_ok=True)
            desktop_content = f"""[Desktop Entry]
Type=Application
Name=Ears Audio Captioner
Comment=Zero-Gap RTSP Audio Captioning Utility
Exec="{python_exe}" "{ears_script}" --tray
Path={BASE_DIR}
Terminal=false
Categories=AudioVideo;Utility;
"""
            with open(desktop_path, "w", encoding="utf-8") as f:
                f.write(desktop_content)
            return True
    except Exception as e:
        logger.warning(f"Failed to update autostart setting: {e}")
        return False


# ==============================================================================
# DYNAMIC TRAY ICON RENDERER (PIL)
# ==============================================================================

def create_tray_icon_image(state: str = "active"):
    """
    Generate a dynamic tray icon with an audio waveform + status dot:
      - state == 'active'  -> Green dot (#10b981)
      - state == 'paused'  -> Amber dot (#f59e0b)
      - state == 'idle'    -> Slate grey dot (#64748b)
    On macOS Menu Bar (NSStatusBar), resize to 22x22 so it fits the menu bar height cleanly.
    """
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark glass circular badge background (deep obsidian-violet + muted purple ring)
    draw.ellipse([2, 2, 61, 61], fill=(18, 13, 31, 245), outline=(157, 123, 234, 220), width=3)

    # Audio waveform bars in muted royal purple
    bars = [
        (14, 26, 18, 38),
        (22, 18, 26, 46),
        (30, 12, 34, 52),
        (38, 20, 42, 44),
        (46, 25, 50, 39),
    ]
    for x1, y1, x2, y2 in bars:
        draw.rectangle([x1, y1, x2, y2], fill=(157, 123, 234, 255))

    # Status indicator dot in bottom-right corner
    dot_colors = {
        "active": (16, 185, 129, 255),  # Emerald Green
        "paused": (245, 158, 11, 255),  # Amber
        "idle": (148, 163, 184, 255),   # Slate Grey
    }
    dot_fill = dot_colors.get(state, dot_colors["idle"])
    draw.ellipse([42, 42, 62, 62], fill=dot_fill, outline=(18, 13, 31, 255), width=3)

    if sys.platform == "darwin":
        resample = getattr( getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
        return img.resize((22, 22), resample)
    return img


def open_local_folder(folder_path: str) -> None:
    """Open a local folder in Windows Explorer, macOS Finder, or Linux file manager."""
    os.makedirs(folder_path, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(folder_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder_path])
        else:
            subprocess.Popen(["xdg-open", folder_path])
    except Exception as e:
        logger.warning(f"Failed to open folder {folder_path}: {e}")


def _is_port_already_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


# ==============================================================================
# SYSTEM TRAY CONTROLLER
# ==============================================================================

def run_system_tray(app, host: str = "0.0.0.0", port: int = 8765) -> None:
    """
    Start the FastAPI/Uvicorn server in a background daemon thread (or attach to an
    already-running `ears.service` instance on `127.0.0.1:<port>`) and run the
    native system tray event loop on the main thread.
    """
    _setup_linux_appindicator_shim()

    import pystray
    from pystray import MenuItem as Item, Menu
    import uvicorn
    import backend.app as app_module

    dashboard_url = f"http://127.0.0.1:{port}"
    external_server = _is_port_already_listening(port)
    uvicorn_server = None

    if external_server:
        logger.info(
            f"Detected existing Ears server on {dashboard_url} (e.g. systemd ears.service); "
            f"attaching System Tray as live desktop companion."
        )
    else:
        server_config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            log_config=None,
            timeout_graceful_shutdown=2,
        )
        uvicorn_server = uvicorn.Server(server_config)
        server_thread = threading.Thread(target=uvicorn_server.run, name="EarsUvicornThread", daemon=True)
        server_thread.start()

    def _notify_external_server_config() -> None:
        if not external_server:
            return
        try:
            import requests
            requests.post(f"{dashboard_url}/api/config", json=load_config(), timeout=2.0)
        except Exception:
            pass

    def on_open_dashboard(icon, item):
        webbrowser.open(dashboard_url)

    def on_toggle_pause(icon, item):
        cfg = load_config()
        new_paused = not bool(cfg.get("PAUSED", False))
        save_config({"PAUSED": new_paused})
        _notify_external_server_config()
        _update_icon_state(icon)

    def on_toggle_save_audio(icon, item):
        cfg = load_config()
        save_config({"SAVE_AUDIO_FILES": not bool(cfg.get("SAVE_AUDIO_FILES", False))})
        _notify_external_server_config()

    def make_retention_setter(days: int):
        def _setter(icon, item):
            save_config({"RETENTION_DAYS": days})
            _notify_external_server_config()
        return _setter

    def make_retention_checker(days: int):
        def _checker(item):
            return int(load_config().get("RETENTION_DAYS", 30)) == days
        return _checker

    def make_profile_setter(profile_key: str):
        def _setter(icon, item):
            save_config({"PERFORMANCE_PROFILE": profile_key})
            if external_server:
                threading.Thread(target=_notify_external_server_config, daemon=True).start()
            else:
                sup = app_module.supervisor
                if sup:
                    threading.Thread(target=sup.load_or_reload_model, args=(profile_key,), daemon=True).start()
        return _setter

    def make_profile_checker(profile_key: str):
        def _checker(item):
            return load_config().get("PERFORMANCE_PROFILE", "auto") == profile_key
        return _checker

    def on_select_storage_folder(icon, item):
        def _pick():
            try:
                cfg = load_config()
                initial = cfg.get("STORAGE_PATH") or BASE_DIR
                selected = ""
                if sys.platform == "darwin":
                    res = subprocess.run(
                        [
                            "osascript",
                            "-e",
                            'POSIX path of (choose folder with prompt "Select Ears Transcript & Audio Storage Folder")',
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        selected = res.stdout.strip()
                else:
                    import tkinter as tk
                    from tkinter import filedialog
                    root = tk.Tk()
                    root.withdraw()
                    try:
                        root.attributes("-topmost", True)
                    except Exception:
                        pass
                    selected = filedialog.askdirectory(
                        initialdir=initial,
                        title="Select Ears Transcript & Audio Storage Folder",
                    )
                    root.destroy()
                if selected:
                    save_config({"STORAGE_PATH": os.path.normpath(selected)})
                    _notify_external_server_config()
            except Exception as e:
                logger.warning(f"Folder picker failed: {e}")
                webbrowser.open(dashboard_url)

        threading.Thread(target=_pick, daemon=True).start()

    def on_open_transcripts_folder(icon, item):
        paths = get_storage_paths()
        open_local_folder(paths.get("logs_dir", paths["base"]))

    def on_toggle_localhost_autologin(icon, item):
        cfg = load_config()
        save_config({"AUTO_LOGIN_LOCALHOST": not bool(cfg.get("AUTO_LOGIN_LOCALHOST", True))})

    def on_toggle_autostart(icon, item):
        currently = is_autostart_enabled()
        set_autostart_enabled(not currently)

    tray_running = True

    def on_quit(icon, item):
        nonlocal tray_running
        tray_running = False
        logger.info("Shutting down Ears System Tray...")
        if uvicorn_server is not None:
            uvicorn_server.should_exit = True
        icon.stop()

    def _count_enabled_channels(cfg: dict) -> int:
        cnt = 0
        for nvr in cfg.get("NVRS", []):
            for ch in nvr.get("channels", []):
                if ch.get("enabled", False):
                    cnt += 1
        return cnt

    def _current_state_and_count() -> tuple[str, int]:
        cfg = load_config()
        if cfg.get("PAUSED", False):
            return "paused", 0
        sup = app_module.supervisor
        if sup and not external_server:
            cnt = len(sup.workers)
            return ("active" if cnt > 0 else "idle"), cnt
        cnt = _count_enabled_channels(cfg)
        return ("active" if cnt > 0 else "idle"), cnt

    last_rendered = [None, None]

    def _apply_icon_on_ui_thread(icon, new_img, new_title: str) -> None:
        try:
            icon.icon = new_img
            icon.title = new_title
        except Exception:
            pass

    def _update_icon_state(icon, force: bool = False) -> None:
        try:
            state, active_cnt = _current_state_and_count()
            if not force and last_rendered[0] == state and last_rendered[1] == active_cnt:
                return
            last_rendered[0] = state
            last_rendered[1] = active_cnt

            new_img = create_tray_icon_image(state)
            if state == "paused":
                new_title = f"Ears — Paused ({dashboard_url})"
            elif state == "active":
                new_title = f"Ears — Capturing {active_cnt} camera(s) ({dashboard_url})"
            else:
                new_title = f"Ears — Ready ({dashboard_url})"

            if sys.platform == "darwin":
                # macOS AppKit / NSStatusBar strictly requires UI mutations on the Cocoa Main Thread
                try:
                    from PyObjCTools import AppHelper
                    AppHelper.callAfter(_apply_icon_on_ui_thread, icon, new_img, new_title)
                except Exception:
                    pass
            else:
                _apply_icon_on_ui_thread(icon, new_img, new_title)
        except Exception:
            pass

    retention_menu = Menu(
        *[
            Item(
                f"{days} Days",
                make_retention_setter(days),
                checked=make_retention_checker(days),
                radio=True,
            )
            for days in (7, 14, 30, 60, 90, 180, 365)
        ]
    )

    profile_options = [
        ("auto", "Auto (Best Detected Hardware)"),
        ("gpu_high", "High Accuracy GPU (large-v3-turbo)"),
        ("gpu_light", "Light GPU (small float16)"),
        ("cpu_balanced", "CPU Balanced (small int8)"),
        ("cpu_eco", "Eco CPU / Laptop Battery (base int8)"),
    ]
    profile_menu = Menu(
        *[
            Item(
                label,
                make_profile_setter(key),
                checked=make_profile_checker(key),
                radio=True,
            )
            for key, label in profile_options
        ]
    )

    menu = Menu(
        Item(f"Web UI: {dashboard_url}", on_open_dashboard),
        Item("Open Ears Dashboard", on_open_dashboard, default=True),
        Item(
            lambda item: "Resume Captioning" if load_config().get("PAUSED", False) else "Pause Captioning",
            on_toggle_pause,
        ),
        Menu.SEPARATOR,
        Item(
            "Save Audio Files (.wav)",
            on_toggle_save_audio,
            checked=lambda item: bool(load_config().get("SAVE_AUDIO_FILES", False)),
        ),
        Item("Retention Days", retention_menu),
        Item("Performance Profile", profile_menu),
        Item("Select Storage Folder...", on_select_storage_folder),
        Item("Open Transcripts Folder", on_open_transcripts_folder),
        Menu.SEPARATOR,
        Item(
            "Allow No-Login on Localhost",
            on_toggle_localhost_autologin,
            checked=lambda item: bool(load_config().get("AUTO_LOGIN_LOCALHOST", True)),
        ),
        Item(
            "Start Ears at Login",
            on_toggle_autostart,
            checked=lambda item: is_autostart_enabled(),
        ),
        Menu.SEPARATOR,
        Item("Quit Ears Tray" if external_server else "Quit Ears", on_quit),
    )

    init_state, init_cnt = _current_state_and_count()
    last_rendered[0] = init_state
    last_rendered[1] = init_cnt
    tray_icon = pystray.Icon(
        "ears_audio_captioner",
        create_tray_icon_image(init_state),
        f"Ears — Audio Captioner ({dashboard_url})",
        menu,
    )

    def _status_monitor_loop():
        time.sleep(5.0)
        while tray_running and (external_server or uvicorn_server is None or not uvicorn_server.should_exit):
            _update_icon_state(tray_icon)
            time.sleep(4.0)

    monitor_thread = threading.Thread(target=_status_monitor_loop, daemon=True)
    monitor_thread.start()

    tray_icon.run()
