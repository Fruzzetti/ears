"""
Ears — Unified Configuration, Dual Storage (SQLite + Daily .txt),
Audio Snippet Manager, and Predictive Disk Usage Estimator.
"""

import os
import sys
import json
import time
import socket
import shutil
import sqlite3
import logging
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("ears.storage")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
CONFIG_LOCK = threading.RLock()
DB_LOCK = threading.RLock()

DEFAULT_STORAGE_DIR = os.path.join(BASE_DIR, "data")


def _get_machine_fingerprint() -> str:
    """Return a platform + hostname fingerprint so configs copied across machines auto-reset cleanly."""
    try:
        return f"{sys.platform}:{socket.gethostname().lower()}"
    except Exception:
        return sys.platform

DEFAULT_CONFIG: Dict[str, Any] = {
    "WEB_PORT": 8765,
    "ALLOW_LAN_ACCESS": False,
    "AUTO_LOGIN_LOCALHOST": True,
    "FIRST_RUN_COMPLETE": False,
    "WEB_USERNAME": "admin",
    "WEB_PASSWORD_HASH": "",
    "STORAGE_PATH": DEFAULT_STORAGE_DIR,
    "RETENTION_DAYS": 30,
    "SAVE_AUDIO_FILES": False,
    "AUDIO_FORMAT": "wav",  # "wav" (16kHz mono PCM, ~32KB/sec) or "opus"
    "PERFORMANCE_PROFILE": "auto",  # "auto", "gpu_high", "gpu_light", "cpu_balanced", "cpu_eco"
    "CAPTION_MODEL_OVERRIDE": "auto",  # "auto", "tiny", "base", "small", "medium", "large-v3-turbo"
    "CAPTION_DEVICE_OVERRIDE": "auto",  # "auto", "cuda", "cpu"
    "SEGMENT_DURATION_SECONDS": 30,
    "OVERLAP_SECONDS": 1.5,
    "GROUP_WINDOW_SECONDS": 5.0,
    "VAD_THRESHOLD": 0.2,
    "NO_SPEECH_PROB_THRESHOLD": 0.6,
    "PAUSED": False,
    "START_TIME": "",
    "END_TIME": "",
    "NVRS": [],
}


def _restrict_posix_permissions(path: str, is_dir: bool = False) -> None:
    """Enforce owner-only permissions (0700 for directories, 0600 for files) on Linux/macOS."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o700 if is_dir else 0o600)
    except OSError:
        pass


def get_temp_buffer_dir() -> str:
    """
    Cross-platform temporary segment buffer directory.
    Uses /dev/shm on Linux if available and writable for RAM-disk speed,
    otherwise falls back to tempfile.gettempdir()/ears_buffers.
    """
    if sys.platform.startswith("linux") and os.path.exists("/dev/shm") and os.access("/dev/shm", os.W_OK):
        buf_dir = "/dev/shm/ears_buffers"
    else:
        buf_dir = os.path.join(tempfile.gettempdir(), "ears_buffers")
    os.makedirs(buf_dir, exist_ok=True)
    _restrict_posix_permissions(buf_dir, is_dir=True)
    return buf_dir


def cleanup_temp_buffers(max_age_seconds: float = 0) -> int:
    """
    Clean up temporary segment .wav files on startup, shutdown, or watchdog sweep.
    Returns number of files removed.
    """
    removed = 0
    buf_dir = get_temp_buffer_dir()
    now = time.time()
    try:
        for fname in os.listdir(buf_dir):
            if fname.endswith(".wav") or fname.endswith(".tmp"):
                fpath = os.path.join(buf_dir, fname)
                try:
                    if max_age_seconds <= 0 or (now - os.path.getmtime(fpath)) >= max_age_seconds:
                        os.remove(fpath)
                        removed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return removed


def _is_foreign_storage_path(path_str: str) -> bool:
    """Return True if a storage path string belongs to a different OS (e.g. '/home/...' on Windows)."""
    if not path_str:
        return False
    if os.name == "nt" and path_str.startswith("/"):
        return True
    if os.name != "nt" and len(path_str) >= 2 and path_str[1] == ":":
        return True
    return False


def load_config() -> Dict[str, Any]:
    """Load config.json safely with defaults and automatic foreign-machine test state reset."""
    with CONFIG_LOCK:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["STORAGE_PATH"] = DEFAULT_STORAGE_DIR
        current_fp = _get_machine_fingerprint()

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f)

                saved_fp = user_cfg.get("HOST_MACHINE_ID", "")
                saved_path = user_cfg.get("STORAGE_PATH", "")

                # If config.json was copied over from a different machine/OS or lacks a valid host fingerprint,
                # automatically discard the foreign/test config and start fresh!
                if (not saved_fp) or (saved_fp != current_fp) or _is_foreign_storage_path(saved_path):
                    logger.warning(
                        f"Discarding foreign/test config.json (saved_fp={saved_fp!r}, path={saved_path!r}) "
                        f"on host {current_fp!r} and resetting to clean first-run defaults."
                    )
                    try:
                        os.remove(CONFIG_FILE)
                    except Exception:
                        pass
                    for sub in ("ears.db", "logs", "audio", "phase2_test_output"):
                        p = os.path.join(DEFAULT_STORAGE_DIR, sub)
                        if os.path.isdir(p):
                            shutil.rmtree(p, ignore_errors=True)
                        elif os.path.isfile(p):
                            try:
                                os.remove(p)
                            except Exception:
                                pass
                    if os.name == "nt" and os.path.isdir(r"C:\home\fuzz\Projects\ears"):
                        shutil.rmtree(r"C:\home\fuzz", ignore_errors=True)
                else:
                    cfg.update(user_cfg)
            except Exception as e:
                logger.error(f"Error reading {CONFIG_FILE}: {e}")

        # Ensure storage path is valid for this OS
        if not cfg.get("STORAGE_PATH") or _is_foreign_storage_path(cfg.get("STORAGE_PATH", "")):
            cfg["STORAGE_PATH"] = DEFAULT_STORAGE_DIR

        # Compute dynamic friendly name map for cameras
        channel_names = {}
        for nvr in cfg.get("NVRS", []):
            nvr_name = nvr.get("name", "Main NVR")
            for ch in nvr.get("channels", []):
                cid = ch.get("id")
                cname = ch.get("name")
                if cid is not None and cname:
                    channel_names[f"{nvr_name}:CAM {cid}"] = cname
                    channel_names[f"CAM {cid}"] = cname
        cfg["CHANNEL_NAMES"] = channel_names
        return cfg


def _is_unsafe_storage_path(path_str: str) -> bool:
    """Block sensitive OS or dotfile directories from being used as STORAGE_PATH."""
    if not path_str or _is_foreign_storage_path(path_str):
        return True
    norm = os.path.abspath(path_str).replace("\\", "/").lower().rstrip("/")
    forbidden_exact = {"", "/", "c:", "c:/windows", "/etc", "/bin", "/sbin", "/usr", "/proc", "/sys", "/dev", "/boot", "/root"}
    if norm in forbidden_exact:
        return True
    for prefix in ("/etc/", "/proc/", "/sys/", "/dev/", "/boot/", "c:/windows/", "c:/program files"):
        if norm.startswith(prefix):
            return True
    parts = [p for p in norm.split("/") if p]
    if any(p in (".ssh", ".gnupg", ".aws", ".kube") for p in parts):
        return True
    return False


def _sanitize_nvrs_list(nvrs_raw: Any) -> List[Dict[str, Any]]:
    """Sanitize NVRS configuration entries and reject non-RTSP URL overrides."""
    if not isinstance(nvrs_raw, list):
        return []
    cleaned = []
    for item in nvrs_raw:
        if not isinstance(item, dict):
            continue
        nvr = dict(item)
        ch_list = []
        for ch in nvr.get("channels", []) or []:
            if not isinstance(ch, dict):
                continue
            ch_copy = dict(ch)
            override = (ch_copy.get("rtsp_url") or "").strip()
            if override:
                if not override.lower().startswith(("rtsp://", "rtsps://")) or any(c in override for c in ("\r", "\n", "\x00")):
                    ch_copy.pop("rtsp_url", None)
            ch_list.append(ch_copy)
        nvr["channels"] = ch_list
        cleaned.append(nvr)
    return cleaned


def save_config(new_config: Dict[str, Any]) -> Dict[str, Any]:
    """Persist configuration updates atomically to config.json, stamped with this machine's fingerprint."""
    allowed_keys = set(DEFAULT_CONFIG.keys()) | {"HOST_MACHINE_ID"}
    with CONFIG_LOCK:
        current = load_config()
        for k, v in new_config.items():
            if k in allowed_keys and k != "CHANNEL_NAMES":
                if k == "NVRS":
                    current[k] = _sanitize_nvrs_list(v)
                else:
                    current[k] = v

        current["HOST_MACHINE_ID"] = _get_machine_fingerprint()
        if _is_unsafe_storage_path(current.get("STORAGE_PATH", "")):
            current["STORAGE_PATH"] = DEFAULT_STORAGE_DIR

        # Ensure target storage directories exist with restricted owner-only permissions
        storage_path = current["STORAGE_PATH"]
        os.makedirs(storage_path, exist_ok=True)
        os.makedirs(os.path.join(storage_path, "logs"), exist_ok=True)
        os.makedirs(os.path.join(storage_path, "audio"), exist_ok=True)
        _restrict_posix_permissions(storage_path, is_dir=True)
        _restrict_posix_permissions(os.path.join(storage_path, "logs"), is_dir=True)
        _restrict_posix_permissions(os.path.join(storage_path, "audio"), is_dir=True)

        tmp_file = CONFIG_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=4)
        _restrict_posix_permissions(tmp_file, is_dir=False)
        os.replace(tmp_file, CONFIG_FILE)
        _restrict_posix_permissions(CONFIG_FILE, is_dir=False)
        return load_config()


def get_storage_paths(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Return resolved paths for DB, plaintext logs, and saved audio clips."""
    if cfg is None:
        cfg = load_config()
    base = cfg.get("STORAGE_PATH") or DEFAULT_STORAGE_DIR
    logs_dir = os.path.join(base, "logs")
    audio_dir = os.path.join(base, "audio")
    db_path = os.path.join(base, "ears.db")
    os.makedirs(logs_dir, exist_ok=True)
    os.makedirs(audio_dir, exist_ok=True)
    return {
        "base": base,
        "logs_dir": logs_dir,
        "audio_dir": audio_dir,
        "db_path": db_path,
    }


def get_db(cfg: Optional[Dict[str, Any]] = None) -> sqlite3.Connection:
    """Open a thread-safe SQLite connection to ears.db in the active STORAGE_PATH."""
    paths = get_storage_paths(cfg)
    conn = sqlite3.connect(paths["db_path"], timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(cfg: Optional[Dict[str, Any]] = None) -> None:
    """Initialize SQLite schema for Ears captions and system events."""
    with DB_LOCK:
        conn = get_db(cfg)
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS captions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                date_str TEXT NOT NULL,
                nvr_id TEXT,
                nvr_name TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                camera_name TEXT NOT NULL,
                text TEXT NOT NULL,
                audio_rel_path TEXT,
                duration_sec REAL DEFAULT 0.0,
                confidence REAL DEFAULT 1.0
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_captions_date ON captions(date_str, timestamp);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_captions_cam ON captions(nvr_name, camera_name);"
        )
        conn.commit()
        conn.close()
        paths = get_storage_paths(cfg)
        if os.path.exists(paths["db_path"]):
            _restrict_posix_permissions(paths["db_path"], is_dir=False)


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in (name or "Unknown")).strip("_") or "NVR"


def record_caption(
    nvr_id: str,
    nvr_name: str,
    channel_id: int,
    camera_name: str,
    text: str,
    timestamp_dt: Optional[datetime] = None,
    source_wav_path: Optional[str] = None,
    seg_start_sec: float = 0.0,
    seg_end_sec: float = 0.0,
    confidence: float = 1.0,
) -> Optional[Dict[str, Any]]:
    """
    Save a transcribed caption to:
    1. SQLite `ears.db`
    2. Daily plaintext log `<STORAGE_PATH>/logs/<NVR_Name>/<YYYY-MM-DD>_cam<id>_audio.txt`
    3. Optional audio snippet `<STORAGE_PATH>/audio/<YYYY-MM-DD>/...` if SAVE_AUDIO_FILES is True
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return None

    cfg = load_config()
    paths = get_storage_paths(cfg)
    now = timestamp_dt or datetime.now()
    ts_str = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now.strftime("%Y-%m-%d")

    display_cam = camera_name or f"CAM {channel_id}"
    safe_nvr = _safe_filename(nvr_name)

    # 1. Optional Audio Clip Saving
    audio_rel_path = None
    duration_sec = max(0.0, round(seg_end_sec - seg_start_sec, 2))
    if cfg.get("SAVE_AUDIO_FILES", False) and source_wav_path and os.path.exists(source_wav_path):
        try:
            day_audio_dir = os.path.join(paths["audio_dir"], date_str, safe_nvr)
            os.makedirs(day_audio_dir, exist_ok=True)
            clip_fname = f"{now.strftime('%H%M%S_%f')[:10]}_cam{channel_id}.wav"
            clip_abs_path = os.path.join(day_audio_dir, clip_fname)
            _extract_wav_slice(source_wav_path, clip_abs_path, seg_start_sec, seg_end_sec)
            if os.path.exists(clip_abs_path):
                audio_rel_path = f"audio/{date_str}/{safe_nvr}/{clip_fname}"
        except Exception as e:
            logger.warning(f"Failed to save audio clip: {e}")

    # 2. Write to Daily Plaintext .txt Log (NVRForce compatible format)
    nvr_log_dir = os.path.join(paths["logs_dir"], safe_nvr)
    os.makedirs(nvr_log_dir, exist_ok=True)
    log_file = os.path.join(nvr_log_dir, f"{date_str}_cam{channel_id}_audio.txt")
    line = f"[{ts_str}] [{display_cam}] {clean_text}\n"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.error(f"Failed writing to txt log {log_file}: {e}")

    # 3. Insert into SQLite DB
    row_id = None
    with DB_LOCK:
        try:
            init_db(cfg)
            conn = get_db(cfg)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO captions (
                    timestamp, date_str, nvr_id, nvr_name, channel_id,
                    camera_name, text, audio_rel_path, duration_sec, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_str,
                    date_str,
                    nvr_id,
                    nvr_name,
                    int(channel_id),
                    display_cam,
                    clean_text,
                    audio_rel_path,
                    duration_sec,
                    confidence,
                ),
            )
            row_id = cur.lastrowid
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed inserting caption into SQLite: {e}")

    return {
        "id": row_id,
        "timestamp": ts_str,
        "date_str": date_str,
        "nvr_id": nvr_id,
        "nvr": nvr_name,
        "channel_id": channel_id,
        "camera": display_cam,
        "text": clean_text,
        "audio_url": f"/api/audio/{audio_rel_path}" if audio_rel_path else None,
        "duration_sec": duration_sec,
    }


def _extract_wav_slice(src_wav: str, dst_wav: str, start_sec: float, end_sec: float) -> None:
    """Slice a PCM .wav file with 0.25s padding around the speech segment using stdlib wave."""
    import wave

    with wave.open(src_wav, "rb") as r:
        params = r.getparams()
        rate = r.getframerate()
        nframes = r.getnframes()
        if end_sec <= start_sec or end_sec <= 0:
            # Copy whole file if timestamps aren't specified
            shutil.copy2(src_wav, dst_wav)
            return
        pad_start = max(0.0, start_sec - 0.25)
        pad_end = end_sec + 0.25
        start_frame = min(nframes, int(pad_start * rate))
        end_frame = min(nframes, int(pad_end * rate))
        if end_frame <= start_frame:
            start_frame = 0
            end_frame = nframes
        r.setpos(start_frame)
        frames = r.readframes(end_frame - start_frame)

    with wave.open(dst_wav, "wb") as w:
        w.setparams(params)
        w.writeframes(frames)


def query_captions(
    date_str: Optional[str] = None,
    search_query: Optional[str] = None,
    nvr_filter: Optional[str] = None,
    camera_filter: Optional[str] = None,
    group_window_sec: Optional[float] = None,
    limit: int = 2000,
) -> List[Dict[str, Any]]:
    """
    Fetch captions from SQLite and group consecutive sentences from the same
    NVR + camera within `group_window_sec` (default 5.0s, fine-tunable in config).
    """
    cfg = load_config()
    init_db(cfg)
    if group_window_sec is None:
        group_window_sec = float(cfg.get("GROUP_WINDOW_SECONDS", 5.0))

    clauses = []
    params: List[Any] = []

    if date_str and date_str.lower() != "all":
        clauses.append("date_str = ?")
        params.append(date_str)
    if search_query and search_query.strip():
        clauses.append("(text LIKE ? OR camera_name LIKE ? OR nvr_name LIKE ?)")
        q = f"%{search_query.strip()}%"
        params.extend([q, q, q])
    if nvr_filter and nvr_filter.lower() != "all":
        clauses.append("nvr_name = ?")
        params.append(nvr_filter)
    if camera_filter and camera_filter.lower() != "all":
        clauses.append("camera_name = ?")
        params.append(camera_filter)

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT * FROM captions
        {where_sql}
        ORDER BY timestamp ASC
        LIMIT ?
    """
    params.append(limit)

    raw_events: List[Dict[str, Any]] = []
    with DB_LOCK:
        conn = get_db(cfg)
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        for row in cur.fetchall():
            audio_rel = row["audio_rel_path"]
            raw_events.append({
                "id": row["id"],
                "type": "caption",
                "timestamp": row["timestamp"],
                "start_timestamp": row["timestamp"],
                "date_str": row["date_str"],
                "nvr": row["nvr_name"],
                "channel_id": row["channel_id"],
                "camera": row["camera_name"],
                "text": row["text"],
                "audio_clips": [f"/api/audio/{audio_rel}"] if audio_rel else [],
                "duration_sec": row["duration_sec"] or 0.0,
            })
        conn.close()

    if group_window_sec <= 0:
        return raw_events

    # Group consecutive captions from the same camera within group_window_sec
    grouped: List[Dict[str, Any]] = []
    for ev in raw_events:
        if not grouped:
            grouped.append(ev)
            continue

        prev = grouped[-1]
        if prev["nvr"] == ev["nvr"] and prev["camera"] == ev["camera"]:
            try:
                t1 = datetime.fromisoformat(prev["timestamp"])
                t2 = datetime.fromisoformat(ev["timestamp"])
                diff = abs((t2 - t1).total_seconds())
                if diff <= group_window_sec:
                    prev["text"] += " " + ev["text"]
                    prev["timestamp"] = ev["timestamp"]
                    prev["duration_sec"] = round(prev["duration_sec"] + ev["duration_sec"], 2)
                    if ev["audio_clips"]:
                        prev["audio_clips"].extend(ev["audio_clips"])
                    continue
            except Exception:
                pass
        grouped.append(ev)

    return grouped


def _dir_size_and_oldest(directory: str) -> Tuple[int, Optional[datetime], int]:
    """Return (total_bytes, oldest_mtime_dt, file_count) for a directory tree."""
    total_bytes = 0
    oldest_ts = None
    count = 0
    if not os.path.exists(directory):
        return 0, None, 0
    for root, _, files in os.walk(directory):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                st = os.stat(fpath)
                total_bytes += st.st_size
                count += 1
                if oldest_ts is None or st.st_mtime < oldest_ts:
                    oldest_ts = st.st_mtime
            except Exception:
                pass
    oldest_dt = datetime.fromtimestamp(oldest_ts) if oldest_ts else None
    return total_bytes, oldest_dt, count


def calculate_storage_and_prediction(
    proposed_retention_days: Optional[int] = None,
    proposed_save_audio: Optional[bool] = None,
    proposed_storage_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compute current disk usage for transcripts (.txt + SQLite DB) and saved audio files,
    AND predict how much disk space will be consumed under proposed retention settings
    before the user clicks Save/Apply.
    """
    cfg = load_config()
    if proposed_storage_path:
        cfg = dict(cfg)
        cfg["STORAGE_PATH"] = proposed_storage_path

    paths = get_storage_paths(cfg)
    current_retention = int(cfg.get("RETENTION_DAYS", 30))
    target_retention = int(proposed_retention_days if proposed_retention_days is not None else current_retention)
    save_audio = proposed_save_audio if proposed_save_audio is not None else bool(cfg.get("SAVE_AUDIO_FILES", False))

    # 1. Measure current disk usage
    txt_bytes, txt_oldest, txt_count = _dir_size_and_oldest(paths["logs_dir"])
    audio_bytes, audio_oldest, audio_count = _dir_size_and_oldest(paths["audio_dir"])
    db_bytes = 0
    for suffix in ("", "-wal", "-shm"):
        p = paths["db_path"] + suffix
        if os.path.exists(p):
            try:
                db_bytes += os.path.getsize(p)
            except Exception:
                pass

    transcripts_total_bytes = txt_bytes + db_bytes
    total_used_bytes = transcripts_total_bytes + audio_bytes

    # Disk free/total on target volume
    try:
        usage = shutil.disk_usage(paths["base"])
        drive_total_bytes = usage.total
        drive_free_bytes = usage.free
    except Exception:
        drive_total_bytes = 100 * (1024 ** 3)
        drive_free_bytes = 50 * (1024 ** 3)

    # Count enabled cameras across all NVRs
    enabled_cameras = 0
    for nvr in cfg.get("NVRS", []):
        for ch in nvr.get("channels", []):
            if ch.get("enabled"):
                enabled_cameras += 1
    active_cam_baseline = max(1, enabled_cameras)

    # 2. Calculate observed daily burn rate (or heuristic baseline if fresh install)
    now = datetime.now()
    txt_days_observed = max(1.0, (now - txt_oldest).total_seconds() / 86400.0) if txt_oldest else 0.0
    audio_days_observed = max(1.0, (now - audio_oldest).total_seconds() / 86400.0) if audio_oldest else 0.0

    # Heuristic baseline per active camera per day:
    # - Text + DB: ~150 KB/day per active camera
    # - Saved Audio Clips (speech-only VAD segments, ~45 min of speech/day @ 16kHz 16-bit mono WAV): ~85 MB/day per camera
    heuristic_txt_daily = active_cam_baseline * 150 * 1024
    heuristic_audio_daily = active_cam_baseline * 85 * 1024 * 1024

    if txt_days_observed >= 0.5 and txt_bytes > 50 * 1024:
        daily_txt_rate = transcripts_total_bytes / txt_days_observed
        rate_source = "measured"
    else:
        daily_txt_rate = max(transcripts_total_bytes, heuristic_txt_daily)
        rate_source = "estimated"

    if audio_days_observed >= 0.5 and audio_bytes > 1024 * 1024:
        daily_audio_rate = audio_bytes / audio_days_observed
    else:
        daily_audio_rate = heuristic_audio_daily

    # 3. Predict storage for proposed_retention_days
    predicted_txt_bytes = int(daily_txt_rate * target_retention)
    predicted_audio_bytes = int(daily_audio_rate * target_retention) if save_audio else 0
    predicted_total_bytes = predicted_txt_bytes + predicted_audio_bytes

    def _fmt(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 ** 3:
            return f"{b / (1024 ** 2):.1f} MB"
        return f"{b / (1024 ** 3):.2f} GB"

    return {
        "storage_path": paths["base"],
        "enabled_cameras": enabled_cameras,
        "current_retention_days": current_retention,
        "proposed_retention_days": target_retention,
        "save_audio_files": save_audio,
        "rate_source": rate_source,
        "current": {
            "transcripts_bytes": transcripts_total_bytes,
            "transcripts_human": _fmt(transcripts_total_bytes),
            "audio_bytes": audio_bytes,
            "audio_human": _fmt(audio_bytes),
            "total_bytes": total_used_bytes,
            "total_human": _fmt(total_used_bytes),
            "txt_file_count": txt_count,
            "audio_file_count": audio_count,
        },
        "predicted": {
            "daily_rate_bytes": int(daily_txt_rate + (daily_audio_rate if save_audio else 0)),
            "daily_rate_human": _fmt(int(daily_txt_rate + (daily_audio_rate if save_audio else 0))) + "/day",
            "transcripts_bytes": predicted_txt_bytes,
            "transcripts_human": _fmt(predicted_txt_bytes),
            "audio_bytes": predicted_audio_bytes,
            "audio_human": _fmt(predicted_audio_bytes),
            "total_bytes": predicted_total_bytes,
            "total_human": _fmt(predicted_total_bytes),
            "percent_of_free_drive": round((predicted_total_bytes / max(1, drive_free_bytes)) * 100, 2),
        },
        "drive": {
            "total_bytes": drive_total_bytes,
            "total_human": _fmt(drive_total_bytes),
            "free_bytes": drive_free_bytes,
            "free_human": _fmt(drive_free_bytes),
        },
    }


def prune_old_records(retention_days: Optional[int] = None) -> Dict[str, int]:
    """
    Prune SQLite captions, daily .txt logs, and saved audio clips older than `retention_days`.
    """
    cfg = load_config()
    days = int(retention_days if retention_days is not None else cfg.get("RETENTION_DAYS", 30))
    if days <= 0:
        return {"deleted_db_rows": 0, "deleted_txt_files": 0, "deleted_audio_files": 0}

    cutoff_dt = datetime.now() - timedelta(days=days)
    cutoff_date_str = cutoff_dt.strftime("%Y-%m-%d")
    paths = get_storage_paths(cfg)

    deleted_db = 0
    deleted_txt = 0
    deleted_audio = 0

    # 1. Prune SQLite rows
    with DB_LOCK:
        try:
            init_db(cfg)
            conn = get_db(cfg)
            cur = conn.cursor()
            cur.execute("DELETE FROM captions WHERE date_str < ?", (cutoff_date_str,))
            deleted_db = cur.rowcount
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"DB prune error: {e}")

    # 2. Prune daily .txt files (YYYY-MM-DD_camX_audio.txt)
    for root, _, files in os.walk(paths["logs_dir"]):
        for fname in files:
            if fname.endswith("_audio.txt") and len(fname) >= 10:
                fdate = fname[:10]
                if fdate < cutoff_date_str:
                    try:
                        os.remove(os.path.join(root, fname))
                        deleted_txt += 1
                    except Exception:
                        pass

    # 3. Prune saved audio directories/files
    if os.path.exists(paths["audio_dir"]):
        for entry in os.listdir(paths["audio_dir"]):
            day_dir = os.path.join(paths["audio_dir"], entry)
            if os.path.isdir(day_dir) and len(entry) == 10 and entry < cutoff_date_str:
                try:
                    for _, _, afiles in os.walk(day_dir):
                        deleted_audio += len(afiles)
                    shutil.rmtree(day_dir, ignore_errors=True)
                except Exception:
                    pass

    logger.info(
        f"Pruned records older than {days} days (< {cutoff_date_str}): "
        f"{deleted_db} DB rows, {deleted_txt} txt logs, {deleted_audio} audio clips"
    )
    return {
        "deleted_db_rows": deleted_db,
        "deleted_txt_files": deleted_txt,
        "deleted_audio_files": deleted_audio,
    }
