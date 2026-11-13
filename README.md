# 👂 Ears — Zero-Gap RTSP Audio Captioning Utility

**Ears** is a lightweight, standalone, cross-platform utility that turns any NVR or IP camera microphone on your local network into a live, searchable, zero-gap audio transcript log — running 100% locally on your own hardware (**Linux**, **Windows**, and **macOS**).

---

## ✨ Key Features

- **Zero-Gap Overlapping RTSP Audio Capture**:
  - Uses an overlapping FFmpeg relay segmenter that starts Segment $N+1$ two seconds before Segment $N$ completes, prepends a **1.5-second PCM tail overlap**, and runs a **boundary word-seam deduplicator** so words spoken across chunk boundaries are never clipped or duplicated.
- **Two-Pass 5-Second LAN NVR & Camera Scanner**:
  - Discovers NVRs and standalone IP cameras across your local subnet in 5 seconds using ARP OUI pre-warming, UDP vendor broadcast probes (**Dahua**, **Hikvision SADP**, **ONVIF WS-Discovery**, **Reolink**, **Uniview**, **Xiongmai**, **Vivotek**, **UniFi**, **SSDP**), and high-speed TCP port sweeps while filtering out non-CCTV HTTP noise.
- **One-Click Channel & Camera Name Auto-Discovery**:
  - Queries your NVR directly (Dahua CGI, Hikvision ISAPI, Uniview LAPI, Reolink CGI, ONVIF) to import all channel numbers and friendly camera names (`Living Room`, `Driveway`, etc.) with inline enable/disable toggle switches.
- **Startup Hardware Auto-Detection**:
  - Automatically probes NVIDIA CUDA GPUs (VRAM capacity), Apple Silicon, or CPU core count & RAM at startup and selects the optimal `faster-whisper` model and compute precision (`float16` vs `int8`) — with instant manual overrides in the System Tray and Web UI.
- **Dual Storage (SQLite + Daily Plaintext `.txt`) & Predictive Disk Space Meter**:
  - Writes human-readable daily `.txt` files (`YYYY-MM-DD_camX_audio.txt`) alongside a fast SQLite search index (`ears.db`).
  - Optional **Save Audio Files (`.wav`)** mode slices and attaches spoken audio clips directly to each transcript card for inline one-click playback (`▶`).
  - Interactive **Retention Days Slider** calculates and displays predicted disk usage in real time before you click Save.
- **Native System Tray + Two-View Dark Glass Web UI**:
  - Runs as a headless background service (`systemd`) on servers or in the **System Tray / macOS Menu Bar** on desktop machines.
  - Zero-Node Vanilla HTML/CSS/JS Dark Glass interface with First-Run Password Setup, optional `Auto-Login on Localhost (127.0.0.1)`, and mandatory password login when accessed from other machines on the LAN.

---

## 🚀 Quick Start & Installation

### 🐧 Linux (Ubuntu / Debian / Fedora / Arch)

- **Graphical Setup Wizard (Desktop Linux — LXQt, GNOME, KDE, XFCE, Mint)**:
  Double-click **`Install_Ears_GUI_Linux.sh`** in your file manager (or run `./Install_Ears_GUI_Linux.sh`). To uninstall via GUI, double-click **`Uninstall_Ears_GUI_Linux.sh`**.
- **Command-Line Install (Terminal / Headless Server / SSH)**:
```bash
chmod +x install.sh uninstall.sh Install_Ears_GUI_Linux.sh Uninstall_Ears_GUI_Linux.sh
./install.sh
```
- Sets up Python virtualenv, FFmpeg, NVIDIA CUDA runtime libraries (if an NVIDIA GPU is present), and enables the `ears.service` systemd daemon.
- Open **`http://127.0.0.1:8765`** (or **`http://<server-lan-ip>:8765`** from another machine).
- **Build a single-file `.run` installer**:
  ```bash
  ./packaging/linux/installerize.sh
  # Produces ./dist/ears-installer.run
  ```
- **Clean Uninstall (Linux)**:
  ```bash
  ./Uninstall_Ears_GUI_Linux.sh  # Graphical uninstaller
  ./uninstall.sh                 # CLI uninstaller (asks whether to keep transcripts)
  ./uninstall.sh --purge         # CLI uninstaller (removes everything including transcripts)
  ```

---

### 🪟 Windows (Windows 10 / 11)

1. Double-click **`Install_Ears_GUI_Windows.bat`** in File Explorer (or run `.\packaging\windows\install_windows.ps1` in PowerShell for CLI mode).
2. The GUI Setup Wizard verifies Python 3 & FFmpeg, installs `faster-whisper` (with CUDA support if an NVIDIA GPU is present), creates the **Ears Audio Captioner** Desktop & Start Menu shortcuts, launches Ears in the Windows System Tray, and automatically opens `http://127.0.0.1:8765` for initial setup.
3. **Build a self-contained portable bundle or `Ears_Setup.exe`**:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\packaging\windows\build_windows.ps1
   ```
4. **Clean Uninstall (Windows)**:
   - Double-click **`Uninstall_Ears_GUI_Windows.bat`** (or use **Add or Remove Programs** if installed via `Ears_Setup.exe`).

---

### 🍎 macOS (Apple Silicon M1/M2/M3/M4 & Intel)

1. **Graphical Setup Wizard (Finder)**:
   - Double-click **`Install_Ears_GUI_Mac.command`** in Finder (or run `./Install_Ears_GUI_Mac.command`).
   - Creates **`/Applications/Ears.app`** and **`/Applications/Uninstall Ears.app`**, launches the macOS Menu Bar icon, and automatically opens `http://127.0.0.1:8765` for initial setup.
2. **Command-Line Install (Terminal)**:
   ```bash
   ./install.sh
   ```
3. **Clean Uninstall (macOS)**:
   - Double-click **`Uninstall_Ears_GUI_Mac.command`** in Finder, open **`/Applications/Uninstall Ears.app`**, or run `./uninstall.sh` in Terminal.

---

## 🛠️ Command-Line Usage (`ears.py`)

```bash
python3 ears.py --headless              # Run FastAPI/Uvicorn server without System Tray
python3 ears.py --tray --open-browser   # Run with System Tray icon and open Web UI
python3 ears.py --port 8765             # Customize Web UI port
```

---

## 🧪 Verification Suites

```bash
cd tests
python3 test_phase1.py   # Hardware auto-detection, SQLite/.txt storage, 5s LAN NVR & channel discovery
python3 test_phase2.py   # Zero-gap overlapping RTSP capture, 1.5s PCM stitcher & Whisper transcription
python3 test_phase3.py   # FastAPI endpoints, First-Run auth wizard, Storage Predictor & Dark Glass SPA
```

---

## ⚖️ Legal, Wiretapping & Evidentiary Notice

- **Wiretapping & Multi-Party Recording Laws**: Capturing, recording, or transcribing spoken conversations is subject to federal, state, provincial, and local wiretapping, eavesdropping, and two-party/all-party consent laws. Many jurisdictions require visible signage (e.g., *"Audio and Video Monitoring in Progress"*) or explicit consent before activating camera microphones. Always verify and comply with the laws in your jurisdiction before deploying Ears.
- **Evidentiary Limitations of AI Captions**: Automatic speech-to-text captions are an indexing and search aid and may contain transcription errors, misheard words, or omissions. Unverified AI transcripts do not have standalone evidentiary value in court—keep **Save Audio Files Alongside Transcripts** enabled (or ensure your NVR retains the original audio stream) if you may need verifiable audio evidence.
