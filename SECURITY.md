# Security Policy

## Supported Versions

Security updates are applied to the latest release on the `main` branch:

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0.0 | :x:                |

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub issues.

Instead, please use GitHub's **[Private Vulnerability Reporting](https://github.com/Fruzzetti/ears/security/advisories/new)** ("Report a vulnerability" on the **Security** tab of this repository).

When reporting, please include:
1. A clear description of the issue and the affected platform (Windows, Linux, or macOS).
2. Steps or a proof-of-concept to reproduce the behavior.
3. Whether the issue is exploitable pre-authentication, over the LAN, or from an external browser tab.

## Threat Model & Scope

Ears is a self-hosted, local-network utility. Please keep the following scope boundaries in mind:

### In Scope
* **Pre-Setup / Authentication Bypasses**: Accessing protected `/api/*` endpoints before first-run setup is completed, or bypassing authentication from a remote LAN IP.
* **Cross-Origin & DNS-Rebinding Attacks**: Bypassing the `Origin` / `Host` header guards to read transcripts, status, or configuration from an untrusted website open in another browser tab.
* **Path Traversal & File Access**: Reading or writing files outside the configured `STORAGE_PATH` or `static/` directory.
* **Command / URI Injection**: Injecting shell commands or arbitrary FFmpeg arguments via RTSP URLs, camera names, or NVR discovery payloads.
* **Credential Exposure**: Exposing saved plaintext NVR passwords via API responses after they have been saved and masked (`********`), or insecure file permissions on `config.json` / `ears.db`.
* **Installer / Uninstaller Privilege Escalation**: Unsafe privilege handling or unintended deletion of pre-existing system packages not listed in `.install_manifest`.

### Out of Scope (By Design)
* **Localhost Auto-Login (`AUTO_LOGIN_LOCALHOST`)**: Bypassing the password screen when accessing `http://127.0.0.1:8765` from the host machine itself while `"Allow No-Login Access on Localhost (127.0.0.1)"` is enabled. This is an explicit user-configurable convenience setting.
* **Attacks Requiring Existing Host/Root Compromise**: If an attacker already has local Administrator/root shell access or physical access to the unlocked host machine, they already control the hardware.
* **Plaintext HTTP on a Trusted LAN**: Ears serves HTTP on the local loopback/LAN by default. Users who wish to access Ears over untrusted networks should place it behind a VPN (such as WireGuard/Tailscale) or a TLS reverse proxy.
* **Upstream NVR / Camera Firmware Bugs**: Vulnerabilities inside third-party camera or NVR firmware.
