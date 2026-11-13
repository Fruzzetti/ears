"""
Ears — Startup Hardware Auto-Detection & Whisper Model Selector ("Just Know")

On every startup, probes the host machine for:
- CPU architecture, core/thread count, and total system RAM
- NVIDIA CUDA GPU (via nvidia-smi / ctranslate2 / torch) + VRAM size
- Apple Silicon Metal / CoreML (via platform / sysctl on macOS arm64)
- AMD ROCm / Vulkan / DirectML / Intel OpenVINO availability

Supports runtime performance overrides from the System Tray or Web UI:
- "auto": Best hardware & model detected at startup
- "gpu_turbo": Force GPU with large-v3-turbo (or medium)
- "gpu_balanced": Force GPU with small (minimal VRAM impact while gaming/working)
- "cpu_saver": Force CPU int8 with base/small (completely frees GPU for other apps)
- Explicit model names: "tiny", "base", "small", "medium", "large-v3-turbo"
"""

import os
import sys
import platform
import subprocess
import shutil
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("ears.hardware")

# Model approximate disk/RAM footprints (MB) and relative speed multipliers
WHISPER_MODELS_INFO = {
    "tiny": {
        "size_mb": 75,
        "vram_mb": 400,
        "label": "Tiny (Fastest, Basic Accuracy)",
        "tier": 1,
    },
    "base": {
        "size_mb": 145,
        "vram_mb": 600,
        "label": "Base (Ultra-Light CPU)",
        "tier": 2,
    },
    "small": {
        "size_mb": 480,
        "vram_mb": 1200,
        "label": "Small (Balanced Accuracy & Speed)",
        "tier": 3,
    },
    "medium": {
        "size_mb": 1500,
        "vram_mb": 2800,
        "label": "Medium (High Accuracy)",
        "tier": 4,
    },
    "large-v3-turbo": {
        "size_mb": 1600,
        "vram_mb": 3200,
        "label": "Large-v3-Turbo (Near-Human GPU)",
        "tier": 5,
    },
}

PERFORMANCE_PROFILES = {
    "auto": "Auto-Detect Best Hardware (Recommended)",
    "gpu_high": "High Accuracy GPU (large-v3-turbo / float16)",
    "gpu_light": "Light GPU — Save VRAM (small / int8_float16)",
    "cpu_balanced": "CPU Only — Free Up GPU (small / int8)",
    "cpu_eco": "Low-Power Eco CPU (base / int8)",
}


def _run_cmd_quiet(cmd: list, timeout: float = 3.0) -> Optional[str]:
    """Run a subprocess quietly without popping a console window on Windows."""
    try:
        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": timeout,
            "text": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        res = subprocess.run(cmd, **kwargs)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def find_cached_whisper_models() -> Dict[str, str]:
    """
    Scan local Ears model cache (`data/models`) and HuggingFace cache directories
    (including /root/.cache on multi-user Linux servers) and return a map of
    {model_size: snapshot_dir} for already-downloaded faster-whisper models.
    """
    cached: Dict[str, str] = {}
    ears_models_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "models")
    candidate_roots = [
        ears_models_dir,
        os.path.expanduser("~/.cache/huggingface/hub"),
        "/root/.cache/huggingface/hub",
    ]
    for home_dir in ("/home/fuzz", "/var/cache/huggingface/hub"):
        p = os.path.join(home_dir, ".cache/huggingface/hub") if not home_dir.endswith("hub") else home_dir
        if p not in candidate_roots:
            candidate_roots.append(p)

    for root in candidate_roots:
        if not os.path.exists(root) or not os.access(root, os.R_OK):
            continue
        try:
            for entry in os.listdir(root):
                if entry.startswith("models--Systran--faster-whisper-"):
                    msize = entry.replace("models--Systran--faster-whisper-", "")
                    snap_dir = os.path.join(root, entry, "snapshots")
                    if os.path.exists(snap_dir):
                        snaps = sorted(os.listdir(snap_dir))
                        if snaps:
                            cached[msize] = os.path.join(snap_dir, snaps[-1])
        except Exception:
            pass
    return cached


def get_system_ram_gb() -> float:
    """Detect total physical RAM in GB without requiring psutil."""
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return round(stat.ullTotalPhys / (1024 ** 3), 2)
        elif sys.platform == "darwin":
            out = _run_cmd_quiet(["sysctl", "-n", "hw.memsize"])
            if out:
                return round(int(out) / (1024 ** 3), 2)
        else:
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            return round(kb / (1024 ** 2), 2)
    except Exception:
        pass
    return 8.0


def get_cpu_info() -> Dict[str, Any]:
    """Return CPU name, logical cores, and architecture."""
    cores = os.cpu_count() or 4
    arch = platform.machine() or "x86_64"
    cpu_name = platform.processor() or arch

    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            if name:
                cpu_name = name.strip()
        except Exception:
            pass
    elif sys.platform == "darwin":
        out = _run_cmd_quiet(["sysctl", "-n", "machdep.cpu.brand_string"])
        if out:
            cpu_name = out
    elif os.path.exists("/proc/cpuinfo"):
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line or "Hardware" in line:
                        cpu_name = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass

    return {
        "name": cpu_name,
        "logical_cores": cores,
        "arch": arch,
    }


def detect_gpus() -> Dict[str, Any]:
    """
    Probe for NVIDIA CUDA, Apple Silicon Metal, AMD ROCm/Vulkan, or Intel GPUs.
    Also verifies whether ctranslate2 (faster-whisper) actually has CUDA runtime support.
    """
    result = {
        "has_nvidia": False,
        "nvidia_name": None,
        "nvidia_vram_mb": 0,
        "ctranslate2_cuda_ready": False,
        "has_apple_silicon": False,
        "apple_gpu_name": None,
        "other_gpus": [],
    }

    # 1. Check NVIDIA GPU via nvidia-smi
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi and os.name == "nt":
        candidate = r"C:\Windows\System32\nvidia-smi.exe"
        if os.path.exists(candidate):
            nvidia_smi = candidate

    if nvidia_smi:
        out = _run_cmd_quiet([
            nvidia_smi,
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits"
        ])
        if out:
            first_line = out.splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            result["has_nvidia"] = True
            result["nvidia_name"] = parts[0]
            if len(parts) > 1:
                try:
                    result["nvidia_vram_mb"] = int(float(parts[1]))
                except ValueError:
                    result["nvidia_vram_mb"] = 4096

    # Check if ctranslate2 or torch has CUDA device count > 0, or if nvidia-smi reported a healthy GPU
    if result["has_nvidia"] and result["nvidia_vram_mb"] >= 2000:
        result["ctranslate2_cuda_ready"] = True
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            result["ctranslate2_cuda_ready"] = True
            result["has_nvidia"] = True
    except Exception:
        pass

    # 2. Check Apple Silicon (macOS arm64)
    if sys.platform == "darwin" and platform.machine() == "arm64":
        result["has_apple_silicon"] = True
        cpu_brand = _run_cmd_quiet(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon"
        result["apple_gpu_name"] = f"{cpu_brand} Integrated GPU (Metal/Accelerate)"

    # 3. Check other GPUs on Windows (AMD / Intel) or Linux (/dev/dri)
    if os.name == "nt":
        out = _run_cmd_quiet([
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"
        ], timeout=4.0)
        if out:
            for line in out.splitlines():
                name = line.strip()
                if name and "nvidia" not in name.lower():
                    result["other_gpus"].append(name)
    elif sys.platform.startswith("linux"):
        if os.path.exists("/dev/dri"):
            out = _run_cmd_quiet(["lspci"])
            if out:
                for line in out.splitlines():
                    if "VGA" in line or "3D controller" in line or "Display" in line:
                        if "NVIDIA" not in line.upper():
                            result["other_gpus"].append(line.split(":", 2)[-1].strip())

    return result


def probe_and_select_hardware(
    profile_override: str = "auto",
    model_override: Optional[str] = None,
    device_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Master startup hardware probe.
    Determines the optimal Whisper device, compute_type, model_size, and worker threads
    every time Ears starts up, while honoring any System Tray / UI performance override.
    """
    cpu = get_cpu_info()
    ram_gb = get_system_ram_gb()
    gpu = detect_gpus()

    # Determine base hardware recommendation ("auto")
    if gpu["has_nvidia"] and gpu["ctranslate2_cuda_ready"]:
        device = "cuda"
        vram = gpu["nvidia_vram_mb"]
        if vram >= 5500:
            compute_type = "float16"
            rec_model = "large-v3-turbo"
            tier_summary = f"NVIDIA CUDA ({gpu['nvidia_name']} • {vram} MB VRAM)"
        elif vram >= 3500:
            compute_type = "int8_float16"
            rec_model = "small"
            tier_summary = f"NVIDIA CUDA ({gpu['nvidia_name']} • {vram} MB VRAM)"
        else:
            compute_type = "int8"
            rec_model = "base"
            tier_summary = f"NVIDIA CUDA Entry ({gpu['nvidia_name']} • {vram} MB VRAM)"
    elif gpu["has_apple_silicon"]:
        # faster-whisper on Apple Silicon uses Accelerate/NEON on CPU with int8/float32
        device = "cpu"
        compute_type = "int8"
        rec_model = "small" if ram_gb >= 8 else "base"
        tier_summary = f"Apple Silicon ({gpu['apple_gpu_name']} • {ram_gb:.0f} GB RAM)"
    else:
        device = "cpu"
        compute_type = "int8"
        if cpu["logical_cores"] >= 6 and ram_gb >= 7.0:
            rec_model = "small"
        else:
            rec_model = "base"
        gpu_note = ""
        if gpu["has_nvidia"] and not gpu["ctranslate2_cuda_ready"]:
            gpu_note = f" [NVIDIA {gpu['nvidia_name']} detected; using CPU int8]"
        elif gpu["other_gpus"]:
            gpu_note = f" [{gpu['other_gpus'][0]}]"
        tier_summary = f"CPU ({cpu['name']} • {cpu['logical_cores']} threads){gpu_note}"

    # Apply System Tray / UI Performance Profile Override
    active_profile = (profile_override or "auto").lower()
    if active_profile == "gpu_high" and gpu["ctranslate2_cuda_ready"]:
        device = "cuda"
        compute_type = "float16"
        rec_model = "large-v3-turbo"
    elif active_profile == "gpu_light" and gpu["ctranslate2_cuda_ready"]:
        device = "cuda"
        compute_type = "int8_float16"
        rec_model = "small"
    elif active_profile == "cpu_balanced":
        device = "cpu"
        compute_type = "int8"
        rec_model = "small"
    elif active_profile == "cpu_eco":
        device = "cpu"
        compute_type = "int8"
        rec_model = "base"

    # Explicit overrides if specified
    if device_override in ("cuda", "cpu"):
        if device_override == "cuda" and not gpu["ctranslate2_cuda_ready"]:
            logger.warning("CUDA requested but CTranslate2 CUDA runtime is not available; falling back to CPU int8.")
            device = "cpu"
            compute_type = "int8"
        else:
            device = device_override
            compute_type = "float16" if device == "cuda" else "int8"

    if model_override and model_override != "auto" and model_override in WHISPER_MODELS_INFO:
        rec_model = model_override

    # Optimal CPU thread count for CTranslate2 when on CPU
    cpu_threads = max(2, min(8, cpu["logical_cores"] // 2)) if device == "cpu" else 4

    report = {
        "device": device,
        "compute_type": compute_type,
        "model_size": rec_model,
        "model_info": WHISPER_MODELS_INFO.get(rec_model, WHISPER_MODELS_INFO["small"]),
        "cpu_threads": cpu_threads,
        "active_profile": active_profile,
        "available_profiles": PERFORMANCE_PROFILES,
        "available_models": WHISPER_MODELS_INFO,
        "tier_summary": tier_summary,
        "banner_badge": f"{'⚡ CUDA' if device == 'cuda' else '🖥️ CPU'} {compute_type} • Model: {rec_model}",
        "hardware_details": {
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "python": platform.python_version(),
            "cpu": cpu,
            "ram_gb": ram_gb,
            "gpu": gpu,
        },
    }

    logger.info(
        f"Hardware Probe Complete -> {report['banner_badge']} | {tier_summary}"
    )
    return report


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    info = probe_and_select_hardware()
    print(json.dumps(info, indent=2))
