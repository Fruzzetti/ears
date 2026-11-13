#!/usr/bin/env python3
"""
EARS — Multi-Engine Linux GUI Setup & Uninstall Wizard
Tries every available graphical toolkit in priority order before ever falling
back to a command-line terminal:
  1. PyQt5 / PyQt6 (pre-installed on Ubuntu LXQt / Lubuntu & KDE Plasma)
  2. GTK3 via PyGObject (`python3-gi`, pre-installed on Ubuntu GNOME, Mint, Debian, Fedora)
  3. Tkinter (`python3-tk`)
  4. Zero-dependency Localhost Browser Wizard (`http.server` + `xdg-open`)
"""

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
MANIFEST_FILE = os.path.join(APP_DIR, ".install_manifest")


def needs_sudo_password() -> bool:
    if os.geteuid() == 0:
        return False
    try:
        res = subprocess.run(
            ["sudo", "-n", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return res.returncode != 0
    except Exception:
        return True


def verify_sudo_password(pw: str) -> bool:
    try:
        proc = subprocess.run(
            ["sudo", "-S", "-k", "-p", "", "true"],
            input=(pw + "\n").encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=6,
        )
        return proc.returncode == 0
    except Exception:
        return False


def get_manifest_packages() -> list:
    pkgs = []
    if os.path.isfile(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    for prefix in ("APT_PKG:", "DNF_PKG:", "PACMAN_PKG:"):
                        if line.startswith(prefix):
                            pkgs.append(line[len(prefix):])
        except Exception:
            pass
    return pkgs


def parse_progress_line(line: str, current_pct: int):
    clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
    if not clean:
        return current_pct, None
    if clean.startswith("[1/"):
        return 22, clean
    if clean.startswith("[2/"):
        return 48, clean
    if "Installing Ears Python dependencies" in clean or "faster-whisper" in clean:
        return 64, clean
    if clean.startswith("[3/"):
        return 80, clean
    if clean.startswith("[4/") or clean.startswith("[5/"):
        return 92, clean
    if "Selected Profile:" in clean or "Hardware Profile:" in clean:
        return 95, clean
    return current_pct, None


def build_runner_command(
    mode: str,
    create_shortcut: bool,
    enable_autostart: bool,
    delete_data: bool,
    remove_sys_pkgs: bool,
    sudo_pw: str,
):
    if mode == "install":
        script = os.path.join(APP_DIR, "install.sh")
        args = ["bash", script, "--cli"]
        if not create_shortcut:
            args.append("--no-shortcut")
        if not enable_autostart:
            args.append("--no-autostart")
    else:
        script = os.path.join(APP_DIR, "uninstall.sh")
        args = ["bash", script, "--cli"]
        if delete_data:
            args.append("--purge")
        else:
            args.append("--keep-data")
        if remove_sys_pkgs:
            args.append("--remove-sys-pkgs")

    env_vars = [
        f"DISPLAY={os.environ.get('DISPLAY', '')}",
        f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '')}",
        f"XAUTHORITY={os.environ.get('XAUTHORITY', '')}",
        f"SUDO_USER={os.environ.get('USER', 'root')}",
    ]

    if os.geteuid() == 0:
        return ["env"] + env_vars + args, None
    if not needs_sudo_password():
        return ["sudo", "-n", "env"] + env_vars + args, None
    if sudo_pw:
        return ["sudo", "-S", "-p", "", "env"] + env_vars + args, (sudo_pw + "\n")
    if shutil.which("pkexec"):
        return ["pkexec", "env"] + env_vars + args, None
    if shutil.which("lxqt-sudo"):
        return ["lxqt-sudo", "-s", "env"] + env_vars + args, None
    return ["sudo", "-S", "-p", "", "env"] + env_vars + args, "\n"


def launch_post_install_tray_and_browser():
    venv_py = os.path.join(APP_DIR, "venv", "bin", "python3")
    ears_py = os.path.join(APP_DIR, "ears.py")
    if os.path.isfile(venv_py) and os.path.isfile(ears_py):
        try:
            subprocess.run(["pkill", "-f", "ears.py --tray"], stderr=subprocess.DEVNULL)
        except Exception:
            pass
        try:
            subprocess.Popen(
                [venv_py, ears_py, "--tray", "--open-browser"],
                cwd=APP_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except Exception:
            pass
    try:
        webbrowser.open("http://127.0.0.1:8765")
    except Exception:
        pass


# ==============================================================================
# TIER 1: PyQt5 / PyQt6 Native Window (Ubuntu LXQt / Lubuntu / KDE Plasma)
# ==============================================================================
def try_qt_wizard(mode: str) -> bool:
    QtWidgets = QtCore = QtGui = None
    try:
        from PyQt5 import QtCore, QtGui, QtWidgets
    except ImportError:
        try:
            from PyQt6 import QtCore, QtGui, QtWidgets
        except ImportError:
            return False

    app = QtWidgets.QApplication(sys.argv)
    is_install = mode == "install"
    require_pw = needs_sudo_password()
    manifest_pkgs = get_manifest_packages()

    win = QtWidgets.QDialog()
    win.setWindowTitle(
        "Ears Audio Captioner — Setup Wizard"
        if is_install
        else "Ears Audio Captioner — Uninstaller"
    )
    win.setFixedWidth(560)
    win.setStyleSheet(
        """
        QDialog { background-color: #0d0a17; color: #f1f5f9; font-family: 'Segoe UI', Ubuntu, sans-serif; }
        QLabel { color: #f1f5f9; }
        QFrame#Card { background-color: #181228; border: 1px solid #2e2348; border-radius: 10px; }
        QCheckBox { color: #f1f5f9; font-size: 13px; spacing: 8px; }
        QCheckBox:disabled { color: #6e6688; }
        QLineEdit { background-color: #0d0a17; color: #f1f5f9; border: 1px solid #3d2f60; border-radius: 6px; padding: 6px 10px; font-size: 13px; }
        QProgressBar { background-color: #181228; border: 1px solid #2e2348; border-radius: 6px; text-align: center; color: #f1f5f9; height: 20px; }
        QProgressBar::chunk { background-color: #6d28d9; border-radius: 5px; }
        QPlainTextEdit { background-color: #08060f; color: #9d94b8; border: 1px solid #2e2348; border-radius: 6px; font-family: monospace; font-size: 11px; }
        QPushButton#PrimaryInstall { background-color: #6d28d9; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 8px 18px; font-size: 13px; }
        QPushButton#PrimaryInstall:hover { background-color: #7c3aed; }
        QPushButton#PrimaryUninstall { background-color: #dc2626; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 8px 18px; font-size: 13px; }
        QPushButton#PrimarySuccess { background-color: #34d399; color: #0d0a17; font-weight: bold; border-radius: 6px; padding: 8px 18px; font-size: 13px; }
        QPushButton#Secondary { background-color: #181228; color: #cbd5e1; border: 1px solid #2e2348; border-radius: 6px; padding: 8px 14px; font-size: 12px; }
        """
    )

    root_layout = QtWidgets.QVBoxLayout(win)
    root_layout.setContentsMargins(22, 18, 22, 18)
    root_layout.setSpacing(12)

    # Header
    hdr = QtWidgets.QHBoxLayout()
    logo_lbl = QtWidgets.QLabel()
    pix = QtGui.QPixmap(48, 48)
    pix.fill(QtCore.Qt.GlobalColor.transparent if hasattr(QtCore.Qt, "GlobalColor") else QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing if hasattr(QtGui.QPainter, "RenderHint") else QtGui.QPainter.Antialiasing)
    painter.setBrush(QtGui.QColor(24, 18, 40))
    painter.setPen(QtGui.QPen(QtGui.QColor(157, 123, 234), 2.4))
    painter.drawEllipse(2, 2, 43, 43)
    painter.setPen(QtCore.Qt.PenStyle.NoPen if hasattr(QtCore.Qt, "PenStyle") else QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor(157, 123, 234))
    for bx, by, bh in [(11, 19, 10), (17, 13, 22), (23, 9, 30), (29, 15, 18), (35, 19, 10)]:
        painter.drawRect(bx, by, 3, bh)
    painter.setBrush(QtGui.QColor(52, 211, 153))
    painter.drawEllipse(32, 32, 13, 13)
    painter.end()
    logo_lbl.setPixmap(pix)
    logo_lbl.setFixedSize(48, 48)
    hdr.addWidget(logo_lbl)

    title_col = QtWidgets.QVBoxLayout()
    title_lbl = QtWidgets.QLabel(
        "Install Ears Audio Captioner" if is_install else "Uninstall Ears Audio Captioner"
    )
    title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #f1f5f9;")
    sub_lbl = QtWidgets.QLabel(
        "100% local CCTV & IP camera speech-to-text captioning in your System Tray."
        if is_install
        else "Cleanly remove Ears while leaving your pre-existing system packages untouched."
    )
    sub_lbl.setStyleSheet("font-size: 12px; color: #9d94b8;")
    title_col.addWidget(title_lbl)
    title_col.addWidget(sub_lbl)
    hdr.addLayout(title_col, 1)
    root_layout.addLayout(hdr)

    # Options Card
    card = QtWidgets.QFrame()
    card.setObjectName("Card")
    card_layout = QtWidgets.QVBoxLayout(card)
    card_layout.setContentsMargins(16, 14, 16, 14)
    card_layout.setSpacing(10)

    chk1 = QtWidgets.QCheckBox()
    chk2 = QtWidgets.QCheckBox()
    if is_install:
        chk1.setText("Create Desktop & Application Menu shortcuts for Ears")
        chk1.setChecked(True)
        chk2.setText("Start Ears automatically in the System Tray on login")
        chk2.setChecked(True)
        card_layout.addWidget(chk1)
        card_layout.addWidget(chk2)
        note_lbl = QtWidgets.QLabel("Note: The Web Setup Wizard (127.0.0.1:8765) will open automatically.")
        note_lbl.setStyleSheet("font-size: 12px; color: #9d7bea;")
        card_layout.addWidget(note_lbl)
    else:
        chk1.setText("Also delete my saved transcripts, audio clips, and config.json")
        chk1.setChecked(False)
        if manifest_pkgs:
            chk2.setText(f"Also uninstall packages that Ears installed ({', '.join(manifest_pkgs)})")
            chk2.setChecked(True)
        else:
            chk2.setText("System Python & FFmpeg were pre-existing (will not be touched)")
            chk2.setChecked(False)
            chk2.setEnabled(False)
        card_layout.addWidget(chk1)
        card_layout.addWidget(chk2)

    pw_edit = None
    if require_pw:
        pw_row = QtWidgets.QHBoxLayout()
        pw_lbl = QtWidgets.QLabel("Linux Sudo Password:")
        pw_lbl.setStyleSheet("font-size: 12px; color: #9d7bea; font-weight: bold;")
        pw_edit = QtWidgets.QLineEdit()
        pw_edit.setEchoMode(
            QtWidgets.QLineEdit.EchoMode.Password
            if hasattr(QtWidgets.QLineEdit, "EchoMode")
            else QtWidgets.QLineEdit.Password
        )
        pw_edit.setPlaceholderText("Enter Linux password (or leave blank for pkexec prompt)")
        pw_row.addWidget(pw_lbl)
        pw_row.addWidget(pw_edit, 1)
        card_layout.addLayout(pw_row)

    root_layout.addWidget(card)

    # Status & Progress
    status_lbl = QtWidgets.QLabel(f"Folder: {APP_DIR}")
    status_lbl.setStyleSheet("font-size: 12px; color: #9d7bea;")
    root_layout.addWidget(status_lbl)

    pbar = QtWidgets.QProgressBar()
    pbar.setRange(0, 100)
    pbar.setValue(0)
    root_layout.addWidget(pbar)

    # Buttons
    btn_row = QtWidgets.QHBoxLayout()
    btn_log = QtWidgets.QPushButton("Show Technical Log")
    btn_log.setObjectName("Secondary")
    btn_row.addWidget(btn_log)
    btn_row.addStretch(1)

    btn_primary = QtWidgets.QPushButton("Install Ears Now" if is_install else "Uninstall Ears")
    btn_primary.setObjectName("PrimaryInstall" if is_install else "PrimaryUninstall")
    btn_row.addWidget(btn_primary)

    btn_close = QtWidgets.QPushButton("Close")
    btn_close.setObjectName("Secondary")
    btn_close.clicked.connect(win.close)
    btn_row.addWidget(btn_close)
    root_layout.addLayout(btn_row)

    # Collapsible Log
    log_box = QtWidgets.QPlainTextEdit()
    log_box.setReadOnly(True)
    log_box.setFixedHeight(170)
    log_box.setVisible(False)
    root_layout.addWidget(log_box)

    def toggle_log():
        vis = not log_box.isVisible()
        log_box.setVisible(vis)
        btn_log.setText("Hide Technical Log" if vis else "Show Technical Log")
        win.adjustSize()

    btn_log.clicked.connect(toggle_log)

    state = {"finished": False, "running": False, "lines": [], "pct": 0, "status": "", "exit_code": None}

    timer = QtCore.QTimer(win)
    timer.setInterval(150)

    def on_tick():
        while state["lines"]:
            ln = state["lines"].pop(0)
            log_box.appendPlainText(ln)
        pbar.setValue(state["pct"])
        if state["status"]:
            status_lbl.setText(state["status"])
        if state["exit_code"] is not None:
            timer.stop()
            code = state["exit_code"]
            state["exit_code"] = None
            state["running"] = False
            if code == 0:
                state["finished"] = True
                pbar.setValue(100)
                status_lbl.setStyleSheet("font-size: 12px; color: #34d399; font-weight: bold;")
                if is_install:
                    status_lbl.setText("Installation Complete! Opening http://127.0.0.1:8765 for initial setup...")
                    btn_primary.setText("Open Setup Dashboard")
                    btn_primary.setObjectName("PrimarySuccess")
                    btn_primary.setStyle(btn_primary.style())
                    btn_primary.setEnabled(True)
                    launch_post_install_tray_and_browser()
                else:
                    status_lbl.setText("Ears has been cleanly uninstalled from Linux.")
                    btn_primary.setText("Uninstalled")
            else:
                status_lbl.setStyleSheet("font-size: 12px; color: #f87171; font-weight: bold;")
                status_lbl.setText(f"Process exited with code {code}. See Technical Log below.")
                if not log_box.isVisible():
                    toggle_log()
                btn_primary.setText("Retry")
                btn_primary.setEnabled(True)

    timer.timeout.connect(on_tick)

    def start_worker():
        if state["finished"] and is_install:
            webbrowser.open("http://127.0.0.1:8765")
            win.close()
            return
        if state["running"]:
            return

        sudo_pw = pw_edit.text() if pw_edit else ""
        if require_pw and sudo_pw:
            if not verify_sudo_password(sudo_pw):
                status_lbl.setStyleSheet("font-size: 12px; color: #f87171; font-weight: bold;")
                status_lbl.setText("Incorrect sudo password. Please check your password and try again.")
                return

        state["running"] = True
        state["pct"] = 12
        state["status"] = "Starting Ears setup..." if is_install else "Uninstalling Ears..."
        status_lbl.setStyleSheet("font-size: 12px; color: #9d7bea;")
        btn_primary.setEnabled(False)
        log_box.clear()

        cmd, stdin_payload = build_runner_command(
            mode,
            chk1.isChecked(),
            chk2.isChecked(),
            chk1.isChecked(),
            chk2.isChecked(),
            sudo_pw,
        )

        def bg():
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=APP_DIR,
                    stdin=subprocess.PIPE if stdin_payload else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if stdin_payload and proc.stdin:
                    try:
                        proc.stdin.write(stdin_payload)
                        proc.stdin.flush()
                        proc.stdin.close()
                    except Exception:
                        pass
                for raw_line in proc.stdout:
                    clean_ln = re.sub(r"\x1b\[[0-9;]*m", "", raw_line).rstrip("\r\n")
                    state["lines"].append(clean_ln)
                    new_pct, new_st = parse_progress_line(clean_ln, state["pct"])
                    state["pct"] = new_pct
                    if new_st:
                        state["status"] = new_st
                proc.wait()
                state["exit_code"] = proc.returncode
            except Exception as e:
                state["lines"].append(f"Error: {e}")
                state["exit_code"] = 1

        threading.Thread(target=bg, daemon=True).start()
        timer.start()

    btn_primary.clicked.connect(start_worker)
    win.adjustSize()
    win.exec() if hasattr(win, "exec") else win.exec_()
    return True


# ==============================================================================
# TIER 2: GTK3 via PyGObject (`python3-gi`, Ubuntu GNOME / Mint / Debian / Fedora)
# ==============================================================================
def try_gtk_wizard(mode: str) -> bool:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, GLib, Gtk
    except Exception:
        return False

    is_install = mode == "install"
    require_pw = needs_sudo_password()
    manifest_pkgs = get_manifest_packages()

    css = b"""
    window { background-color: #0d0a17; color: #f1f5f9; }
    .card { background-color: #181228; border: 1px solid #2e2348; border-radius: 10px; padding: 14px; }
    .title { font-size: 18px; font-weight: bold; color: #f1f5f9; }
    .sub { font-size: 12px; color: #9d94b8; }
    .accent { font-size: 12px; color: #9d7bea; }
    .btn-primary { background: #6d28d9; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 6px 16px; }
    .btn-danger { background: #dc2626; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 6px 16px; }
    .btn-secondary { background: #181228; color: #cbd5e1; border-radius: 6px; padding: 6px 12px; }
    entry { background: #0d0a17; color: #f1f5f9; border: 1px solid #3d2f60; border-radius: 6px; }
    textview, textview text { background-color: #08060f; color: #9d94b8; font-family: monospace; font-size: 11px; }
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    win = Gtk.Window(
        title="Ears Audio Captioner — Setup Wizard"
        if is_install
        else "Ears Audio Captioner — Uninstaller"
    )
    win.set_default_size(550, -1)
    win.set_position(Gtk.WindowPosition.CENTER)
    win.set_border_width(20)
    win.connect("destroy", Gtk.main_quit)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    win.add(vbox)

    title_lbl = Gtk.Label(
        label="Install Ears Audio Captioner" if is_install else "Uninstall Ears Audio Captioner",
        xalign=0,
    )
    title_lbl.get_style_context().add_class("title")
    sub_lbl = Gtk.Label(
        label="100% local CCTV & IP camera speech-to-text captioning in your System Tray."
        if is_install
        else "Cleanly remove Ears while leaving your pre-existing system packages untouched.",
        xalign=0,
    )
    sub_lbl.get_style_context().add_class("sub")
    vbox.pack_start(title_lbl, False, False, 0)
    vbox.pack_start(sub_lbl, False, False, 0)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    card.get_style_context().add_class("card")
    chk1 = Gtk.CheckButton()
    chk2 = Gtk.CheckButton()
    if is_install:
        chk1.set_label("Create Desktop & Application Menu shortcuts for Ears")
        chk1.set_active(True)
        chk2.set_label("Start Ears automatically in the System Tray on login")
        chk2.set_active(True)
        card.pack_start(chk1, False, False, 0)
        card.pack_start(chk2, False, False, 0)
        note_lbl = Gtk.Label(
            label="Note: The Web Setup Wizard (127.0.0.1:8765) will open automatically.",
            xalign=0,
        )
        note_lbl.get_style_context().add_class("accent")
        card.pack_start(note_lbl, False, False, 0)
    else:
        chk1.set_label("Also delete my saved transcripts, audio clips, and config.json")
        chk1.set_active(False)
        if manifest_pkgs:
            chk2.set_label(f"Also uninstall packages that Ears installed ({', '.join(manifest_pkgs)})")
            chk2.set_active(True)
        else:
            chk2.set_label("System Python & FFmpeg were pre-existing (will not be touched)")
            chk2.set_active(False)
            chk2.set_sensitive(False)
        card.pack_start(chk1, False, False, 0)
        card.pack_start(chk2, False, False, 0)

    pw_entry = None
    if require_pw:
        pw_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pw_lbl = Gtk.Label(label="Linux Sudo Password:", xalign=0)
        pw_lbl.get_style_context().add_class("accent")
        pw_entry = Gtk.Entry()
        pw_entry.set_visibility(False)
        pw_entry.set_placeholder_text("Enter Linux password (or leave blank for pkexec)")
        pw_box.pack_start(pw_lbl, False, False, 0)
        pw_box.pack_start(pw_entry, True, True, 0)
        card.pack_start(pw_box, False, False, 4)

    vbox.pack_start(card, False, False, 0)

    status_lbl = Gtk.Label(label=f"Folder: {APP_DIR}", xalign=0)
    status_lbl.get_style_context().add_class("accent")
    vbox.pack_start(status_lbl, False, False, 0)

    pbar = Gtk.ProgressBar()
    vbox.pack_start(pbar, False, False, 0)

    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btn_log = Gtk.Button(label="Show Technical Log")
    btn_log.get_style_context().add_class("btn-secondary")
    btn_row.pack_start(btn_log, False, False, 0)

    btn_close = Gtk.Button(label="Close")
    btn_close.get_style_context().add_class("btn-secondary")
    btn_close.connect("clicked", lambda *_: Gtk.main_quit())
    btn_row.pack_end(btn_close, False, False, 0)

    btn_primary = Gtk.Button(label="Install Ears Now" if is_install else "Uninstall Ears")
    btn_primary.get_style_context().add_class("btn-primary" if is_install else "btn-danger")
    btn_row.pack_end(btn_primary, False, False, 0)
    vbox.pack_start(btn_row, False, False, 0)

    scroll = Gtk.ScrolledWindow()
    scroll.set_size_request(-1, 160)
    textview = Gtk.TextView()
    textview.set_editable(False)
    scroll.add(textview)
    vbox.pack_start(scroll, True, True, 0)

    def on_toggle_log(*_):
        vis = not scroll.get_visible()
        scroll.set_visible(vis)
        btn_log.set_label("Hide Technical Log" if vis else "Show Technical Log")
        win.resize(550, 1)

    btn_log.connect("clicked", on_toggle_log)

    state = {"finished": False, "running": False}

    def on_primary(*_):
        if state["finished"] and is_install:
            webbrowser.open("http://127.0.0.1:8765")
            Gtk.main_quit()
            return
        if state["running"]:
            return
        sudo_pw = pw_entry.get_text() if pw_entry else ""
        if require_pw and sudo_pw and not verify_sudo_password(sudo_pw):
            status_lbl.set_text("Incorrect sudo password. Please check your password and try again.")
            return

        state["running"] = True
        btn_primary.set_sensitive(False)
        pbar.set_fraction(0.12)
        status_lbl.set_text("Starting Ears setup..." if is_install else "Uninstalling Ears...")
        buf = textview.get_buffer()
        buf.set_text("")

        cmd, stdin_payload = build_runner_command(
            mode,
            chk1.get_active(),
            chk2.get_active(),
            chk1.get_active(),
            chk2.get_active(),
            sudo_pw,
        )

        def bg():
            pct = 12
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=APP_DIR,
                    stdin=subprocess.PIPE if stdin_payload else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if stdin_payload and proc.stdin:
                    try:
                        proc.stdin.write(stdin_payload)
                        proc.stdin.flush()
                        proc.stdin.close()
                    except Exception:
                        pass
                for raw_line in proc.stdout:
                    clean_ln = re.sub(r"\x1b\[[0-9;]*m", "", raw_line).rstrip("\r\n")
                    pct, new_st = parse_progress_line(clean_ln, pct)

                    def ui_update(ln=clean_ln, p=pct, st=new_st):
                        end_iter = buf.get_end_iter()
                        buf.insert(end_iter, ln + "\n")
                        pbar.set_fraction(p / 100.0)
                        if st:
                            status_lbl.set_text(st)
                        return False

                    GLib.idle_add(ui_update)
                proc.wait()
                code = proc.returncode
            except Exception as e:
                code = 1

            def ui_done():
                state["running"] = False
                if code == 0:
                    state["finished"] = True
                    pbar.set_fraction(1.0)
                    if is_install:
                        status_lbl.set_text("Installation Complete! Opening http://127.0.0.1:8765...")
                        btn_primary.set_label("Open Setup Dashboard")
                        btn_primary.set_sensitive(True)
                        launch_post_install_tray_and_browser()
                    else:
                        status_lbl.set_text("Ears has been cleanly uninstalled from Linux.")
                        btn_primary.set_label("Uninstalled")
                else:
                    status_lbl.set_text(f"Process exited with code {code}. See Technical Log below.")
                    if not scroll.get_visible():
                        on_toggle_log()
                    btn_primary.set_label("Retry")
                    btn_primary.set_sensitive(True)
                return False

            GLib.idle_add(ui_done)

        threading.Thread(target=bg, daemon=True).start()

    btn_primary.connect("clicked", on_primary)
    win.show_all()
    scroll.set_visible(False)
    Gtk.main()
    return True


# ==============================================================================
# TIER 3: Tkinter (`python3-tk`) Native Window
# ==============================================================================
def try_tk_wizard(mode: str) -> bool:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return False

    is_install = mode == "install"
    require_pw = needs_sudo_password()
    manifest_pkgs = get_manifest_packages()

    root = tk.Tk()
    root.title(
        "Ears Audio Captioner — Setup Wizard"
        if is_install
        else "Ears Audio Captioner — Uninstaller"
    )
    root.configure(bg="#0d0a17")
    root.geometry("560x350")
    root.resizable(False, False)

    hdr = tk.Frame(root, bg="#0d0a17")
    hdr.pack(fill="x", padx=20, pady=(16, 8))
    tk.Label(
        hdr,
        text="Install Ears Audio Captioner" if is_install else "Uninstall Ears Audio Captioner",
        bg="#0d0a17",
        fg="#f1f5f9",
        font=("Sans", 15, "bold"),
        anchor="w",
    ).pack(fill="x")
    tk.Label(
        hdr,
        text="100% local CCTV & IP camera speech-to-text captioning in your System Tray."
        if is_install
        else "Cleanly remove Ears while leaving your pre-existing system packages untouched.",
        bg="#0d0a17",
        fg="#9d94b8",
        font=("Sans", 9),
        anchor="w",
    ).pack(fill="x")

    card = tk.Frame(root, bg="#181228", bd=1, relief="solid")
    card.pack(fill="x", padx=20, pady=6, ipady=8)

    var1 = tk.BooleanVar(value=True if is_install else False)
    var2 = tk.BooleanVar(value=True if (is_install or manifest_pkgs) else False)

    tk.Checkbutton(
        card,
        text="Create Desktop & Application Menu shortcuts for Ears"
        if is_install
        else "Also delete my saved transcripts, audio clips, and config.json",
        variable=var1,
        bg="#181228",
        fg="#f1f5f9",
        selectcolor="#0d0a17",
        activebackground="#181228",
        activeforeground="#f1f5f9",
        anchor="w",
    ).pack(fill="x", padx=12, pady=2)

    chk2_btn = tk.Checkbutton(
        card,
        text="Start Ears automatically in the System Tray on login"
        if is_install
        else (
            f"Also uninstall packages that Ears installed ({', '.join(manifest_pkgs)})"
            if manifest_pkgs
            else "System Python & FFmpeg were pre-existing (will not be touched)"
        ),
        variable=var2,
        bg="#181228",
        fg="#f1f5f9",
        selectcolor="#0d0a17",
        activebackground="#181228",
        activeforeground="#f1f5f9",
        anchor="w",
    )
    if not is_install and not manifest_pkgs:
        chk2_btn.configure(state="disabled")
    chk2_btn.pack(fill="x", padx=12, pady=2)

    if is_install:
        tk.Label(
            card,
            text="Note: The Web Setup Wizard (127.0.0.1:8765) will open automatically.",
            bg="#181228",
            fg="#9d7bea",
            font=("Sans", 9),
            anchor="w",
        ).pack(fill="x", padx=14, pady=(2, 0))

    pw_var = tk.StringVar()
    if require_pw:
        pw_frame = tk.Frame(card, bg="#181228")
        pw_frame.pack(fill="x", padx=14, pady=(6, 2))
        tk.Label(
            pw_frame,
            text="Linux Sudo Password:",
            bg="#181228",
            fg="#9d7bea",
            font=("Sans", 9, "bold"),
        ).pack(side="left")
        tk.Entry(
            pw_frame,
            textvariable=pw_var,
            show="*",
            bg="#0d0a17",
            fg="#f1f5f9",
            insertbackground="#f1f5f9",
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))

    status_var = tk.StringVar(value=f"Folder: {APP_DIR}")
    status_lbl = tk.Label(root, textvariable=status_var, bg="#0d0a17", fg="#9d7bea", font=("Sans", 9), anchor="w")
    status_lbl.pack(fill="x", padx=20, pady=(6, 2))

    pbar = ttk.Progressbar(root, orient="horizontal", mode="determinate", maximum=100)
    pbar.pack(fill="x", padx=20, pady=4)

    btn_frame = tk.Frame(root, bg="#0d0a17")
    btn_frame.pack(fill="x", padx=20, pady=10)

    log_txt = tk.Text(root, bg="#08060f", fg="#9d94b8", height=10, font=("Monospace", 8))
    log_visible = {"val": False}

    def toggle_log():
        if log_visible["val"]:
            log_txt.pack_forget()
            root.geometry("560x350")
            btn_log.configure(text="Show Technical Log")
            log_visible["val"] = False
        else:
            log_txt.pack(fill="both", expand=True, padx=20, pady=(0, 14))
            root.geometry("560x520")
            btn_log.configure(text="Hide Technical Log")
            log_visible["val"] = True

    btn_log = tk.Button(btn_frame, text="Show Technical Log", bg="#181228", fg="#cbd5e1", command=toggle_log)
    btn_log.pack(side="left")

    btn_close = tk.Button(btn_frame, text="Close", bg="#181228", fg="#cbd5e1", command=root.destroy)
    btn_close.pack(side="right", padx=(8, 0))

    state = {"finished": False, "running": False}

    def start_worker():
        if state["finished"] and is_install:
            webbrowser.open("http://127.0.0.1:8765")
            root.destroy()
            return
        if state["running"]:
            return
        sudo_pw = pw_var.get()
        if require_pw and sudo_pw and not verify_sudo_password(sudo_pw):
            status_var.set("Incorrect sudo password. Please check your password and try again.")
            status_lbl.configure(fg="#f87171")
            return

        state["running"] = True
        btn_primary.configure(state="disabled")
        pbar["value"] = 12
        status_var.set("Starting Ears setup..." if is_install else "Uninstalling Ears...")
        log_txt.delete("1.0", "end")

        cmd, stdin_payload = build_runner_command(
            mode, var1.get(), var2.get(), var1.get(), var2.get(), sudo_pw
        )

        def bg():
            pct = 12
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=APP_DIR,
                    stdin=subprocess.PIPE if stdin_payload else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if stdin_payload and proc.stdin:
                    try:
                        proc.stdin.write(stdin_payload)
                        proc.stdin.flush()
                        proc.stdin.close()
                    except Exception:
                        pass
                for raw_line in proc.stdout:
                    clean_ln = re.sub(r"\x1b\[[0-9;]*m", "", raw_line).rstrip("\r\n")
                    pct, new_st = parse_progress_line(clean_ln, pct)
                    root.after(0, lambda l=clean_ln, p=pct, s=new_st: (
                        log_txt.insert("end", l + "\n"),
                        log_txt.see("end"),
                        pbar.configure(value=p),
                        status_var.set(s) if s else None,
                    ))
                proc.wait()
                code = proc.returncode
            except Exception:
                code = 1

            def finish():
                state["running"] = False
                if code == 0:
                    state["finished"] = True
                    pbar["value"] = 100
                    status_lbl.configure(fg="#34d399")
                    if is_install:
                        status_var.set("Installation Complete! Opening http://127.0.0.1:8765...")
                        btn_primary.configure(text="Open Setup Dashboard", state="normal", bg="#34d399", fg="#0d0a17")
                        launch_post_install_tray_and_browser()
                    else:
                        status_var.set("Ears has been cleanly uninstalled from Linux.")
                        btn_primary.configure(text="Uninstalled")
                else:
                    status_lbl.configure(fg="#f87171")
                    status_var.set(f"Process exited with code {code}. See Technical Log.")
                    if not log_visible["val"]:
                        toggle_log()
                    btn_primary.configure(text="Retry", state="normal")

            root.after(0, finish)

        threading.Thread(target=bg, daemon=True).start()

    btn_primary = tk.Button(
        btn_frame,
        text="Install Ears Now" if is_install else "Uninstall Ears",
        bg="#6d28d9" if is_install else "#dc2626",
        fg="#ffffff",
        command=start_worker,
    )
    btn_primary.pack(side="right")

    root.mainloop()
    return True


# ==============================================================================
# TIER 4: Zero-Dependency Local Browser Setup Wizard (`http.server` + `xdg-open`)
# ==============================================================================
def try_web_wizard(mode: str) -> bool:
    is_install = mode == "install"
    require_pw = needs_sudo_password()
    manifest_pkgs = get_manifest_packages()
    token = secrets.token_hex(16)

    state = {
        "running": False,
        "finished": False,
        "exit_code": None,
        "pct": 0,
        "status": f"Folder: {APP_DIR}",
        "logs": [],
        "shutdown": False,
    }

    html_page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Ears Audio Captioner — {'Setup Wizard' if is_install else 'Uninstaller'}</title>
<style>
  body {{ background: #0d0a17; color: #f1f5f9; font-family: system-ui, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 95vh; margin: 0; }}
  .wiz {{ background: #140f23; border: 1px solid #2e2348; border-radius: 14px; width: 540px; padding: 24px; box-shadow: 0 20px 50px rgba(0,0,0,0.6); }}
  h2 {{ margin: 0 0 4px 0; font-size: 20px; }}
  .sub {{ color: #9d94b8; font-size: 13px; margin-bottom: 16px; }}
  .card {{ background: #181228; border: 1px solid #2e2348; border-radius: 10px; padding: 14px; margin-bottom: 14px; }}
  label {{ display: block; font-size: 13px; margin-bottom: 8px; cursor: pointer; }}
  .note {{ color: #9d7bea; font-size: 12px; margin-top: 6px; }}
  input[type=password] {{ width: 96%; background: #0d0a17; color: #f1f5f9; border: 1px solid #3d2f60; border-radius: 6px; padding: 7px; margin-top: 4px; }}
  .status {{ color: #9d7bea; font-size: 12px; margin-bottom: 6px; }}
  .bar-bg {{ background: #181228; border: 1px solid #2e2348; border-radius: 6px; height: 18px; overflow: hidden; margin-bottom: 14px; }}
  .bar-fill {{ background: #6d28d9; height: 100%; width: 0%; transition: width 0.2s; }}
  .btns {{ display: flex; justify-content: space-between; gap: 10px; }}
  button {{ border: none; border-radius: 6px; padding: 9px 16px; font-weight: bold; cursor: pointer; font-size: 13px; }}
  .btn-sec {{ background: #181228; color: #cbd5e1; border: 1px solid #2e2348; }}
  .btn-pri {{ background: {'#6d28d9' if is_install else '#dc2626'}; color: #fff; }}
  pre {{ background: #08060f; color: #9d94b8; padding: 10px; border-radius: 6px; height: 160px; overflow-y: auto; font-size: 11px; display: none; margin-top: 12px; }}
</style>
</head>
<body>
<div class="wiz">
  <h2>{'Install Ears Audio Captioner' if is_install else 'Uninstall Ears Audio Captioner'}</h2>
  <div class="sub">{'100% local CCTV & IP camera speech-to-text captioning in your System Tray.' if is_install else 'Cleanly remove Ears while leaving your pre-existing system packages untouched.'}</div>
  <div class="card">
    <label><input type="checkbox" id="chk1" {'checked' if is_install else ''}> {'Create Desktop & Application Menu shortcuts for Ears' if is_install else 'Also delete my saved transcripts, audio clips, and config.json'}</label>
    <label><input type="checkbox" id="chk2" {'checked' if (is_install or manifest_pkgs) else 'disabled'}> {'Start Ears automatically in the System Tray on login' if is_install else (f"Also uninstall packages that Ears installed ({', '.join(manifest_pkgs)})" if manifest_pkgs else "System Python & FFmpeg were pre-existing (will not be touched)")}</label>
    {'<div class="note">Note: The Web Setup Wizard (127.0.0.1:8765) will open automatically.</div>' if is_install else ''}
    {'<div style="margin-top:8px;"><span class="note"><strong>Linux Sudo Password:</strong></span><input type="password" id="pw" placeholder="Enter your Linux login password"></div>' if require_pw else ''}
  </div>
  <div id="status" class="status">Folder: {APP_DIR}</div>
  <div class="bar-bg"><div id="bar" class="bar-fill"></div></div>
  <div class="btns">
    <button class="btn-sec" onclick="const l=document.getElementById('log'); l.style.display=l.style.display==='block'?'none':'block';">Show Technical Log</button>
    <button id="pri" class="btn-pri" onclick="runTask()">{'Install Ears Now' if is_install else 'Uninstall Ears'}</button>
  </div>
  <pre id="log"></pre>
</div>
<script>
let finished = false;
async function runTask() {{
  if (finished && {'true' if is_install else 'false'}) {{
    window.location.href = 'http://127.0.0.1:8765';
    return;
  }}
  document.getElementById('pri').disabled = true;
  const res = await fetch('/start?token={token}', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      chk1: document.getElementById('chk1').checked,
      chk2: document.getElementById('chk2').checked,
      pw: document.getElementById('pw') ? document.getElementById('pw').value : ''
    }})
  }});
  const d = await res.json();
  if (d.error) {{
    document.getElementById('status').style.color = '#f87171';
    document.getElementById('status').textContent = d.error;
    document.getElementById('pri').disabled = false;
    return;
  }}
  poll();
}}
async function poll() {{
  const res = await fetch('/status?token={token}');
  const d = await res.json();
  document.getElementById('bar').style.width = d.pct + '%';
  document.getElementById('status').textContent = d.status;
  document.getElementById('log').textContent = d.logs.join('\\n');
  if (d.exit_code !== null) {{
    if (d.exit_code === 0) {{
      finished = true;
      document.getElementById('status').style.color = '#34d399';
      if ({'true' if is_install else 'false'}) {{
        document.getElementById('pri').textContent = 'Continue to Setup ->';
        document.getElementById('pri').style.background = '#34d399';
        document.getElementById('pri').style.color = '#0d0a17';
        document.getElementById('pri').disabled = false;
        setTimeout(() => {{ window.location.href = 'http://127.0.0.1:8765'; }}, 2500);
      }}
    }} else {{
      document.getElementById('status').style.color = '#f87171';
      document.getElementById('log').style.display = 'block';
      document.getElementById('pri').textContent = 'Retry';
      document.getElementById('pri').disabled = false;
    }}
    return;
  }}
  setTimeout(poll, 250);
}}
</script>
</body>
</html>"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("token", [""])[0] != token:
                self.send_response(403)
                self.end_headers()
                return
            if parsed.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html_page.encode("utf-8"))
            elif parsed.path == "/status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(state).encode("utf-8"))

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("token", [""])[0] != token:
                self.send_response(403)
                self.end_headers()
                return
            if parsed.path == "/start":
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                sudo_pw = body.get("pw", "")
                if require_pw and sudo_pw and not verify_sudo_password(sudo_pw):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Incorrect sudo password. Please try again."}).encode("utf-8"))
                    return

                state["running"] = True
                state["exit_code"] = None
                state["pct"] = 12
                state["logs"] = []
                state["status"] = "Starting Ears setup..." if is_install else "Uninstalling Ears..."
                cmd, stdin_payload = build_runner_command(
                    mode, body.get("chk1", True), body.get("chk2", True), body.get("chk1", False), body.get("chk2", True), sudo_pw
                )

                def bg():
                    try:
                        proc = subprocess.Popen(
                            cmd,
                            cwd=APP_DIR,
                            stdin=subprocess.PIPE if stdin_payload else subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1,
                        )
                        if stdin_payload and proc.stdin:
                            try:
                                proc.stdin.write(stdin_payload)
                                proc.stdin.flush()
                                proc.stdin.close()
                            except Exception:
                                pass
                        for raw_line in proc.stdout:
                            clean_ln = re.sub(r"\x1b\[[0-9;]*m", "", raw_line).rstrip("\r\n")
                            state["logs"].append(clean_ln)
                            new_pct, new_st = parse_progress_line(clean_ln, state["pct"])
                            state["pct"] = new_pct
                            if new_st:
                                state["status"] = new_st
                        proc.wait()
                        state["exit_code"] = proc.returncode
                        if proc.returncode == 0:
                            state["pct"] = 100
                            state["status"] = (
                                "Installation Complete! Redirecting to http://127.0.0.1:8765..."
                                if is_install
                                else "Ears has been cleanly uninstalled."
                            )
                            if is_install:
                                launch_post_install_tray_and_browser()
                    except Exception as e:
                        state["logs"].append(str(e))
                        state["exit_code"] = 1

                threading.Thread(target=bg, daemon=True).start()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

    try:
        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_port
        url = f"http://127.0.0.1:{port}/?token={token}"
        if not webbrowser.open(url):
            return False
        start_ts = time.time()
        while time.time() - start_ts < 900:
            server.handle_request()
            if state["exit_code"] == 0:
                for _ in range(4):
                    server.handle_request()
                break
        return True
    except Exception:
        return False


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "install"
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        sys.exit(2)

    if try_qt_wizard(mode):
        sys.exit(0)
    if try_gtk_wizard(mode):
        sys.exit(0)
    if try_tk_wizard(mode):
        sys.exit(0)
    if try_web_wizard(mode):
        sys.exit(0)
    sys.exit(2)


if __name__ == "__main__":
    main()
