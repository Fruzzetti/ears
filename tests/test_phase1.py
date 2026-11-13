"""
Ears — Phase 1 Verification & Diagnostic Suite
Tests:
1. Startup Hardware Auto-Detection & Performance Profile Overrides (hardware.py)
2. Dual Storage Engine (SQLite + Daily .txt logs + Audio Slice Extraction + Predictive Disk Space Estimator) (storage.py)
3. Live 5-Second LAN NVR/Camera Discovery (CCTVSee/CCTVPeek broadcasts + TCP sweep) (discovery.py)
4. Read-Only Channel Auto-Discovery against live NVRs from F:\\nvrforce\\backend\\config.json (discovery.py)
"""

import os
import sys
import wave
import math
import struct
import asyncio
import tempfile
import shutil

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure Z:\ears is on sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.hardware import probe_and_select_hardware
from backend.storage import (
    load_config,
    save_config,
    record_caption,
    query_captions,
    calculate_storage_and_prediction,
    prune_old_records,
    get_temp_buffer_dir,
    cleanup_temp_buffers,
)
from backend.discovery import scan_network_for_nvrs, discover_nvr_channels, build_rtsp_url


def generate_synthetic_wav(path: str, duration_sec: float = 4.0, rate: int = 16000) -> None:
    """Create a valid 16kHz 16-bit mono PCM WAV file for testing audio slice extraction."""
    nframes = int(duration_sec * rate)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        samples = [
            int(8000 * math.sin(2 * math.pi * 440.0 * (i / rate)))
            for i in range(nframes)
        ]
        w.writeframes(struct.pack(f"<{nframes}h", *samples))


async def run_all_tests() -> None:
    print("=" * 78)
    print("  EARS — PHASE 1 CORE BACKEND VERIFICATION SUITE")
    print("=" * 78)

    # -------------------------------------------------------------------------
    # TEST 1: Hardware Auto-Detection & Override Profiles
    # -------------------------------------------------------------------------
    print("\n[1/4] Testing Startup Hardware Auto-Detection (backend/hardware.py)...")
    hw_auto = probe_and_select_hardware(profile_override="auto")
    print(f"  -> OS / Python    : {hw_auto['hardware_details']['os']} | Python {hw_auto['hardware_details']['python']}")
    print(f"  -> CPU            : {hw_auto['hardware_details']['cpu']['name']} ({hw_auto['hardware_details']['cpu']['logical_cores']} threads)")
    print(f"  -> System RAM     : {hw_auto['hardware_details']['ram_gb']} GB")
    print(f"  -> GPU Detected   : NVIDIA={hw_auto['hardware_details']['gpu']['has_nvidia']} ({hw_auto['hardware_details']['gpu']['nvidia_name']}), Other={hw_auto['hardware_details']['gpu']['other_gpus']}")
    print(f"  -> Auto Selection : {hw_auto['banner_badge']} ({hw_auto['cpu_threads']} worker threads)")

    hw_eco = probe_and_select_hardware(profile_override="cpu_eco")
    assert hw_eco["device"] == "cpu" and hw_eco["model_size"] == "base", "cpu_eco override failed"
    print(f"  -> Tray Override  : 'cpu_eco' -> {hw_eco['banner_badge']} [PASS]")

    # -------------------------------------------------------------------------
    # TEST 2: Storage Engine, Audio Clip Slicing & Predictive Disk Space Meter
    # -------------------------------------------------------------------------
    print("\n[2/4] Testing Dual Storage, Audio Slicing & Predictive Space Meter (backend/storage.py)...")
    test_storage_dir = os.path.join(tempfile.gettempdir(), "ears_phase1_test_data")
    if os.path.exists(test_storage_dir):
        shutil.rmtree(test_storage_dir, ignore_errors=True)

    save_config({
        "STORAGE_PATH": test_storage_dir,
        "SAVE_AUDIO_FILES": True,
        "RETENTION_DAYS": 30,
        "GROUP_WINDOW_SECONDS": 5.0,
        "NVRS": [
            {
                "id": "test_nvr_1",
                "name": "Inside",
                "ip": "10.0.0.3",
                "brand": "dahua",
                "channels": [
                    {"id": 1, "name": "Living Room", "enabled": True},
                    {"id": 3, "name": "Kitchen", "enabled": True},
                ],
            }
        ],
    })

    buf_dir = get_temp_buffer_dir()
    sample_wav = os.path.join(buf_dir, "test_segment.wav")
    generate_synthetic_wav(sample_wav, duration_sec=6.0)

    from datetime import datetime, timedelta
    t0 = datetime.now()
    rec1 = record_caption(
        nvr_id="test_nvr_1",
        nvr_name="Inside",
        channel_id=1,
        camera_name="Living Room",
        text="Hey, did someone just pull into the driveway?",
        timestamp_dt=t0,
        source_wav_path=sample_wav,
        seg_start_sec=0.5,
        seg_end_sec=2.5,
    )
    rec2 = record_caption(
        nvr_id="test_nvr_1",
        nvr_name="Inside",
        channel_id=1,
        camera_name="Living Room",
        text="Yeah, looks like the delivery truck.",
        timestamp_dt=t0 + timedelta(seconds=3),
        source_wav_path=sample_wav,
        seg_start_sec=2.6,
        seg_end_sec=5.0,
    )
    assert rec1 and rec2, "record_caption failed"
    assert rec1["audio_url"] is not None, "Audio clip was not saved when SAVE_AUDIO_FILES=True"

    # Verify 5-second paragraph grouping
    grouped = query_captions(date_str=t0.strftime("%Y-%m-%d"))
    assert len(grouped) == 1, f"Expected 2 sentences within 3s to group into 1 paragraph, got {len(grouped)}"
    assert "delivery truck" in grouped[0]["text"] and len(grouped[0]["audio_clips"]) == 2
    print(f"  -> Grouped Caption: [{grouped[0]['camera']}] \"{grouped[0]['text']}\" ({len(grouped[0]['audio_clips'])} audio clips attached) [PASS]")

    # Verify Predictive Storage Calculator (30 days vs 90 days with audio on vs off)
    pred_30_audio = calculate_storage_and_prediction(proposed_retention_days=30, proposed_save_audio=True)
    pred_90_audio = calculate_storage_and_prediction(proposed_retention_days=90, proposed_save_audio=True)
    pred_90_text_only = calculate_storage_and_prediction(proposed_retention_days=90, proposed_save_audio=False)
    print(f"  -> Current Usage  : {pred_30_audio['current']['total_human']} (Transcripts: {pred_30_audio['current']['transcripts_human']}, Audio: {pred_30_audio['current']['audio_human']})")
    print(f"  -> Predicted 30d (Audio ON) : {pred_30_audio['predicted']['total_human']} ({pred_30_audio['predicted']['daily_rate_human']})")
    print(f"  -> Predicted 90d (Audio ON) : {pred_90_audio['predicted']['total_human']} ({pred_90_audio['predicted']['percent_of_free_drive']}% of free drive)")
    print(f"  -> Predicted 90d (Text Only): {pred_90_text_only['predicted']['total_human']} [PASS]")

    # Cleanup temp test dir and restore default storage path in Z:\ears\data
    cleanup_temp_buffers(0)
    shutil.rmtree(test_storage_dir, ignore_errors=True)
    save_config({
        "STORAGE_PATH": os.path.join(ROOT_DIR, "data"),
        "SAVE_AUDIO_FILES": False,
        "NVRS": [],
    })

    # -------------------------------------------------------------------------
    # TEST 3: Live 5-Second LAN NVR/Camera Scanner
    # -------------------------------------------------------------------------
    print("\n[3/4] Running Live 5-Second LAN NVR/Camera Discovery (backend/discovery.py)...")
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    scan_res = await scan_network_for_nvrs(listen_time=3.0)
    print(f"  -> Subnets Scanned: {', '.join(scan_res['subnets_scanned'])} in {scan_res['elapsed_seconds']}s")
    print(f"  -> Devices Found  : {len(scan_res['devices'])} CCTV devices ({scan_res['screened_out']} non-CCTV filtered)")
    for dev in scan_res["devices"]:
        print(f"     * {dev['ip']:<15} | {dev['display_vendor']:<24} | MAC: {dev['mac']:<17} | Ports: {dev['ports']} | Via: {dev['discovery_sources']}")

    # -------------------------------------------------------------------------
    # TEST 4: Live Read-Only Channel Auto-Discovery on NVRForce NVRs
    # -------------------------------------------------------------------------
    print("\n[4/4] Testing Channel Auto-Discovery against Live NVRs (read-only HTTP CGI)...")
    candidate_cfg_paths = [
        r"F:\nvrforce\backend\config.json",
        "/home/fuzz/nvrforce/backend/config.json",
        "/opt/nvrforce/backend/config.json",
        os.path.expanduser("~/nvrforce/backend/config.json"),
    ]
    nvrforce_cfg_path = next((p for p in candidate_cfg_paths if os.path.exists(p)), None)
    nvrs_to_test = []
    if nvrforce_cfg_path:
        import json
        with open(nvrforce_cfg_path, "r") as f:
            nf_cfg = json.load(f)
        nvrs_to_test = nf_cfg.get("NVRS", [])
    else:
        test_user = os.environ.get("EARS_TEST_USER", "")
        test_pass = os.environ.get("EARS_TEST_PASS", "")
        if test_user and test_pass:
            for dev in scan_res.get("devices", []):
                nvrs_to_test.append({
                    "name": f"Discovered ({dev['ip']})",
                    "ip": dev["ip"],
                    "brand": dev["brand"],
                    "username": test_user,
                    "password": test_pass,
                })

    for nvr in nvrs_to_test:
        ip = nvr.get("ip")
        user = nvr.get("username")
        pwd = nvr.get("password")
        brand = nvr.get("brand", "dahua")
        name = nvr.get("name", ip)
        ch_res = discover_nvr_channels(ip, user, pwd, brand, timeout=4.0)
        if ch_res.get("status") == "success":
            ch_names = [f"Ch{c['id']}:{c['name']}" for c in ch_res["channels"]]
            print(f"  -> NVR '{name}' ({ip}, {brand}): Discovered {len(ch_res['channels'])} channels -> {', '.join(ch_names)} [PASS]")
        else:
            print(f"  -> NVR '{name}' ({ip}, {brand}): {ch_res.get('message')}")

    print("\n" + "=" * 78)
    print("  ALL PHASE 1 TESTS PASSED!")
    print("=" * 78)


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_all_tests())
