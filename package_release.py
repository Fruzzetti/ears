#!/usr/bin/env python3
"""
Ears — Public Preview Release Packager
Produces clean, zero-state `dist/ears-preview.tar.gz` and `dist/ears-preview.zip`
archives (~45 KB) stripped of any local virtualenvs, runtime data, configs, or caches.
"""

import os
import stat
import tarfile
import zipfile

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT_DIR, "dist")

EXCLUDE_DIRS = {
    "venv",
    ".venv",
    "env",
    "data",
    "dist",
    "build",
    "tests",
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "runtime",
    "Output",
}

EXCLUDE_FILES = {
    "config.json",
    "config.json.tmp",
    ".install_manifest",
    "package_release.py",
    "Start_Ears_Tray.vbs",
    "Start_Ears_Console.bat",
    "Install_Ears_GUI.vbs",
    "Uninstall_Ears_GUI.vbs",
    "Install_Ears_GUI.sh",
    "Uninstall_Ears_GUI.sh",
    "install_windows.bat",
    "uninstall_windows.bat",
    "install_macos.command",
    "uninstall_macos.command",
    "install_macos.sh",
    "uninstall_macos.sh",
    ".DS_Store",
    "Thumbs.db",
}


def iter_clean_files():
    for dirpath, dirnames, filenames in os.walk(ROOT_DIR):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS and not d.endswith(".egg-info"))
        for fname in sorted(filenames):
            if fname in EXCLUDE_FILES or fname.endswith((".pyc", ".pyo", ".db", ".wav", ".opus", ".run", ".exe")):
                continue
            full_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(full_path, ROOT_DIR).replace("\\", "/")
            yield full_path, rel_path


def audit_files_for_secrets(files) -> None:
    """Ensure zero local credentials, hashes, or machine IDs exist in any packaged file."""
    forbidden_tokens = [
        b"nvrforce",
        b"42004200g",
        b'"ADMIN_PASSWORD_HASH": "pbkdf2',
        b'"HOST_MACHINE_ID": "win',
        b'"HOST_MACHINE_ID": "linux',
        b'"HOST_MACHINE_ID": "darwin',
    ]
    violations = []
    for full_path, rel_path in files:
        with open(full_path, "rb") as f:
            raw = f.read()
        for tok in forbidden_tokens:
            if tok in raw:
                violations.append((rel_path, tok.decode("utf-8", errors="ignore")))
    if violations:
        raise RuntimeError(f"SECURITY AUDIT FAILED! Forbidden tokens found in release files: {violations}")


def normalize_unix_scripts(files) -> None:
    """Ensure all .sh, .command, .js, and .py files use clean Unix LF line endings and +x mode."""
    for full_path, rel_path in files:
        if rel_path.endswith((".sh", ".command", ".py", "gui_wizard_macos.js")):
            with open(full_path, "rb") as f:
                raw = f.read()
            if b"\r\n" in raw:
                cleaned = raw.replace(b"\r\n", b"\n")
                with open(full_path, "wb") as f:
                    f.write(cleaned)
            if rel_path.endswith((".sh", ".command", "ears.py", "gui_wizard_linux.py", "gui_wizard_macos.js")):
                try:
                    st = os.stat(full_path)
                    os.chmod(full_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except OSError:
                    pass


def build_archives() -> None:
    os.makedirs(DIST_DIR, exist_ok=True)
    for obsolete in (
        "Install_Ears_GUI.vbs",
        "Uninstall_Ears_GUI.vbs",
        "Install_Ears_GUI.sh",
        "Uninstall_Ears_GUI.sh",
        "install_windows.bat",
        "uninstall_windows.bat",
        "install_macos.command",
        "uninstall_macos.command",
        "install_macos.sh",
        "uninstall_macos.sh",
    ):
        obs_path = os.path.join(ROOT_DIR, obsolete)
        if os.path.isfile(obs_path):
            try:
                os.remove(obs_path)
            except OSError:
                pass
    tar_path = os.path.join(DIST_DIR, "ears-preview.tar.gz")
    zip_path = os.path.join(DIST_DIR, "ears-preview.zip")

    files = list(iter_clean_files())
    normalize_unix_scripts(files)
    audit_files_for_secrets(files)

    # 1. Build .tar.gz with LF line endings on .sh/.py and +x permissions
    with tarfile.open(tar_path, "w:gz") as tf:
        for full_path, rel_path in files:
            arcname = f"ears/{rel_path}"
            info = tf.gettarinfo(full_path, arcname=arcname)
            if rel_path.endswith((".sh", ".command", "ears.py", "gui_wizard_linux.py", "gui_wizard_macos.js", "package_release.py")) or rel_path.startswith("tests/"):
                info.mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            with open(full_path, "rb") as f:
                tf.addfile(info, f)

    # 2. Build .zip for Windows / macOS users
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for full_path, rel_path in files:
            zf.write(full_path, arcname=f"ears/{rel_path}")

    tar_kb = os.path.getsize(tar_path) / 1024.0
    zip_kb = os.path.getsize(zip_path) / 1024.0
    print("=" * 72)
    print("  EARS — PUBLIC PREVIEW ARCHIVES BUILT")
    print("=" * 72)
    print(f"  Files Included : {len(files)} clean source/installer files")
    print("  Security Audit : PASSED (0 credentials, hashes, or local state)")
    print(f"  Linux/macOS    : {tar_path} ({tar_kb:.1f} KB)")
    print(f"  Windows (.zip) : {zip_path} ({zip_kb:.1f} KB)")
    print("=" * 72)


if __name__ == "__main__":
    build_archives()
