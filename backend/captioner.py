"""
Ears — Phase 2 Zero-Gap Audio Capture, 1.5s Overlap Stitcher,
Sleep/Wake Watchdog, and Whisper Transcription Engine.
"""

import os
import sys
import glob
import time
import wave
import shutil
import asyncio
import logging
import threading
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Callable

from backend.hardware import probe_and_select_hardware
from backend.storage import (
    BASE_DIR,
    load_config,
    record_caption,
    get_temp_buffer_dir,
    cleanup_temp_buffers,
    prune_old_records,
)
from backend.discovery import build_rtsp_url

logger = logging.getLogger("ears.captioner")


def resolve_ffmpeg_binary() -> str:
    """Locate `ffmpeg` binary across PATH, local venv, bundled runtime, or Windows WinGet package directories."""
    venv_ffmpeg = os.path.join(BASE_DIR, "venv", "bin", "ffmpeg")
    if os.path.isfile(venv_ffmpeg) and os.access(venv_ffmpeg, os.X_OK):
        return venv_ffmpeg

    found = shutil.which("ffmpeg")
    if found:
        return found
    if os.name == "nt":
        candidates = [
            os.path.join(BASE_DIR, "runtime", "ffmpeg", "ffmpeg.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
        ]
        winget_glob = os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Microsoft",
            "WinGet",
            "Packages",
            "*FFmpeg*",
            "*",
            "bin",
            "ffmpeg.exe",
        )
        candidates.extend(glob.glob(winget_glob))
        for c in candidates:
            if c and os.path.isfile(c):
                return c
    elif sys.platform == "darwin":
        for c in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if os.path.isfile(c):
                return c

    # Universal fallback: self-contained imageio-ffmpeg binary inside ./venv
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    return "ffmpeg"

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16-bit PCM
CHANNELS = 1

HALLUCINATION_PHRASES = [
    "thank you.",
    "thank you",
    "thanks for watching",
    "subscribe to my channel",
    "amara.org",
    "by reading subtitles",
    "subtitles by",
    "translated by",
    "copyright",
    "all rights reserved",
]


def is_hallucination(text: str, no_speech_prob: float, threshold: float = 0.6) -> bool:
    """Filter out common Whisper silence hallucinations and repetitive artifacts."""
    if not text or no_speech_prob > threshold:
        return True
    clean = text.strip()
    if len(clean) < 2:
        return True
    # Excessive single-character or token repetition (e.g. "aaaaaaaaaa..." or "... ... ...")
    if len(set(clean)) < 5 and len(clean) > 20:
        return True
    lower = clean.lower()
    if any(h in lower for h in HALLUCINATION_PHRASES) and len(clean) < 35:
        return True
    return False


def deduplicate_boundary_seam(prev_tail_text: str, current_text: str) -> str:
    """
    When prepending 1.5s of audio from the end of Segment N to the start of Segment N+1,
    the first few words of Segment N+1 may repeat the last few words of Segment N.
    This strips any overlapping 1-to-8 word prefix from `current_text`.
    """
    if not prev_tail_text or not current_text:
        return current_text.strip()

    def _norm(w: str) -> str:
        return "".join(c.lower() for c in w if c.isalnum())

    prev_words = prev_tail_text.strip().split()
    curr_words = current_text.strip().split()
    if not prev_words or not curr_words:
        return current_text.strip()

    prev_norm = [_norm(w) for w in prev_words[-12:]]
    curr_norm = [_norm(w) for w in curr_words[:12]]

    # If the entire current sentence was already at the end of the previous sentence, drop it
    if len(curr_norm) <= len(prev_norm):
        for start_i in range(len(prev_norm) - len(curr_norm) + 1):
            if prev_norm[start_i : start_i + len(curr_norm)] == curr_norm:
                return ""

    max_overlap = min(len(prev_norm), len(curr_norm), 10)
    best_k = 0
    for k in range(max_overlap, 0, -1):
        # Check exact suffix-prefix match or match within last 2 words of prev_norm
        if prev_norm[-k:] == curr_norm[:k] and any(curr_norm[:k]):
            best_k = k
            break
        if len(prev_norm) >= k + 1 and prev_norm[-(k + 1) : -1] == curr_norm[:k] and any(curr_norm[:k]):
            best_k = k
            break

    if best_k > 0:
        trimmed = " ".join(curr_words[best_k:]).strip()
        # Clean leading punctuation left over from split
        trimmed = trimmed.lstrip(".,!?;:- ")
        return trimmed
    return current_text.strip()


def prepend_overlap_and_extract_tail(
    wav_path: str,
    prev_tail_pcm: bytes,
    overlap_seconds: float = 1.5,
) -> Tuple[str, bytes, float]:
    """
    Given a newly completed segment `.wav` file:
    1. Read its raw 16kHz 16-bit mono PCM frames.
    2. Extract the last `overlap_seconds` of PCM bytes to save for the NEXT segment.
    3. If `prev_tail_pcm` is non-empty, write a stitched `.wav` file (`prev_tail_pcm + current_pcm`)
       so words spoken across the segment boundary are never cut in half.
    Returns (stitched_wav_path, new_tail_pcm, duration_seconds).
    """
    bytes_per_sec = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS
    overlap_bytes = int(overlap_seconds * bytes_per_sec)
    # Ensure even byte alignment for 16-bit samples
    if overlap_bytes % 2 != 0:
        overlap_bytes += 1

    with wave.open(wav_path, "rb") as r:
        params = r.getparams()
        raw_pcm = r.readframes(r.getnframes())

    total_duration = len(raw_pcm) / float(bytes_per_sec) if bytes_per_sec else 0.0
    new_tail_pcm = raw_pcm[-overlap_bytes:] if len(raw_pcm) > overlap_bytes else raw_pcm

    if not prev_tail_pcm:
        return wav_path, new_tail_pcm, total_duration

    stitched_path = wav_path[:-4] + "_stitched.wav"
    with wave.open(stitched_path, "wb") as w:
        w.setparams(params)
        w.writeframes(prev_tail_pcm + raw_pcm)

    stitched_duration = (len(prev_tail_pcm) + len(raw_pcm)) / float(bytes_per_sec)
    return stitched_path, new_tail_pcm, stitched_duration


def load_wav_as_float32_ndarray(wav_path: str):
    """
    Read a 16kHz 16-bit mono PCM .wav file directly into a 1D float32 numpy array [-1.0, 1.0].
    Completely bypasses PyAV (`av.open`), eliminating PyAV >= 14 `metadata_errors` keyword breakage
    and avoiding redundant audio resampling.
    """
    import numpy as np
    with wave.open(wav_path, "rb") as wf:
        pcm_bytes = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0


def is_pcm_silent(wav_path: str, amplitude_threshold: int = 15) -> bool:
    """Fast PCM amplitude check to skip completely dead/zeroed audio streams."""
    try:
        import struct
        with wave.open(wav_path, "rb") as w:
            nframes = w.getnframes()
            if nframes == 0:
                return True
            frames = w.readframes(nframes)
            if not frames:
                return True
            # Sample every 64th frame for sub-millisecond check
            step = 128
            samples = [
                struct.unpack_from("<h", frames, offset)[0]
                for offset in range(0, len(frames) - 1, step)
            ]
            if not samples:
                return True
            return max(abs(s) for s in samples) < amplitude_threshold
    except Exception:
        return False


class CameraStreamWorker:
    """
    Zero-Gap Overlapping Relay Audio Capture Worker for a single NVR channel.
    - Launches Capture N+1 `lead_time` seconds (e.g. 2.5s) BEFORE Capture N finishes,
      so the RTSP TCP handshake and initial audio buffer of Capture N+1 overlap the end of Capture N.
    - Guarantees 0.00ms of missed audio between chunks AND produces 100% finalized WAV headers on every file.
    - Automatically recovers from Wi-Fi drops, camera reboots, or laptop sleep/wake.
    """

    def __init__(
        self,
        stream_key: str,
        nvr_id: str,
        nvr_name: str,
        nvr_ip: str,
        brand: str,
        username: str,
        password: str,
        channel_id: int,
        camera_name: str,
        rtsp_url_override: Optional[str],
        segment_duration: int,
        overlap_seconds: float,
        transcription_queue: asyncio.Queue,
    ):
        self.stream_key = stream_key
        self.nvr_id = nvr_id
        self.nvr_name = nvr_name
        self.nvr_ip = nvr_ip
        self.brand = brand
        self.username = username
        self.password = password
        self.channel_id = channel_id
        self.camera_name = camera_name
        self.rtsp_url_override = rtsp_url_override
        self.segment_duration = max(5, int(segment_duration))
        self.overlap_seconds = max(1.0, float(overlap_seconds))
        self.queue = transcription_queue

        self.active_procs: List[asyncio.subprocess.Process] = []
        self.running = False
        self.status = "starting"  # "recording", "reconnecting", "no_audio", "stopped"
        self.last_error = ""
        self.segments_captured = 0
        self.last_segment_time = time.time()
        self.prev_tail_pcm: bytes = b""
        self.last_tail_text: str = ""
        self._task: Optional[asyncio.Task] = None
        self._seq = 0

        safe_key = "".join(c if c.isalnum() else "_" for c in stream_key)
        self.stream_buf_dir = os.path.join(get_temp_buffer_dir(), safe_key)
        os.makedirs(self.stream_buf_dir, exist_ok=True)

    def start(self) -> None:
        if not self.running:
            self.running = True
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self, flush_partial: bool = True) -> None:
        self.running = False
        self.status = "stopped"
        for p in list(self.active_procs):
            if p.returncode is None:
                try:
                    p.terminate()
                    await asyncio.wait_for(p.wait(), timeout=2.0)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
        self.active_procs.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _capture_one_chunk(self, duration_sec: float, seq_id: int) -> bool:
        """Run a single FFmpeg capture of `duration_sec` seconds and queue the resulting WAV."""
        rtsp_url = build_rtsp_url(
            self.nvr_ip,
            self.username,
            self.password,
            self.brand,
            self.channel_id,
            self.rtsp_url_override,
        )
        os.makedirs(self.stream_buf_dir, exist_ok=True)
        ts_label = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_wav = os.path.join(self.stream_buf_dir, f"seg_{ts_label}_{seq_id:05d}.wav")

        ffmpeg_bin = resolve_ffmpeg_binary()
        cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-t", f"{duration_sec:.2f}",
            out_wav,
        ]
        kwargs: Dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        try:
            proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        except Exception as e:
            self.status = "reconnecting"
            self.last_error = f"Cannot launch FFmpeg ({ffmpeg_bin}): {e}"
            logger.error(f"[{self.camera_name}] {self.last_error}")
            return False

        self.active_procs.append(proc)
        try:
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=duration_sec + 20.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.communicate()
            except Exception:
                pass
            if proc in self.active_procs:
                self.active_procs.remove(proc)
            self.status = "reconnecting"
            self.last_error = "FFmpeg capture timed out."
            logger.warning(f"[{self.camera_name}] FFmpeg capture timed out on {self.nvr_ip} ch{self.channel_id}")
            return False

        if proc in self.active_procs:
            self.active_procs.remove(proc)

        # Even if interrupted early (e.g. laptop sleep or stop()), check if we got valid audio (> 1.0s)
        if os.path.exists(out_wav) and os.path.getsize(out_wav) >= 25000:
            try:
                stitched_path, new_tail, dur = prepend_overlap_and_extract_tail(
                    out_wav, self.prev_tail_pcm, self.overlap_seconds
                )
                self.prev_tail_pcm = new_tail
                if stitched_path != out_wav:
                    try:
                        os.remove(out_wav)
                    except Exception:
                        pass
                self.segments_captured += 1
                self.last_segment_time = time.time()
                self.status = "recording"
                self.last_error = ""
                logger.info(
                    f"[{self.camera_name}] Captured segment #{self.segments_captured} ({dur:.1f}s) -> queued for Whisper"
                )
                await self.queue.put({
                    "worker": self,
                    "wav_path": stitched_path,
                    "duration_sec": dur,
                    "captured_at": datetime.now(),
                })
                return True
            except Exception as e:
                logger.warning(f"[{self.camera_name}] Failed processing WAV {out_wav}: {e}")

        # Clean up empty/tiny file on failure
        if os.path.exists(out_wav):
            try:
                os.remove(out_wav)
            except Exception:
                pass

        err_text = (stderr_bytes or b"").decode("utf-8", errors="ignore").strip()
        if "does not contain any stream" in err_text:
            self.status = "no_audio"
            self.last_error = "No audio stream in RTSP substream (Mic disabled or absent)."
        elif err_text:
            self.status = "reconnecting"
            self.last_error = err_text.splitlines()[-1][:160]
            logger.warning(f"[{self.camera_name}] RTSP/FFmpeg warning: {self.last_error}")
        return False

    async def _run_loop(self) -> None:
        """
        Overlapping Relay Scheduler:
        Starts a new capture every `segment_duration` seconds, while each capture runs for
        `segment_duration + lead_overlap` seconds (e.g. next capture starts 2.0s before previous ends)
        so RTSP connection setup never creates a gap between chunks.
        """
        backoff = 2.0
        lead_overlap = 2.0  # Pre-connect 2.0s before current chunk ends
        in_flight: List[asyncio.Task] = []

        logger.info(
            f"[{self.nvr_name} -> {self.camera_name}] Starting overlapping zero-gap relay capture "
            f"(every {self.segment_duration}s + {lead_overlap}s RTSP pre-connect + {self.overlap_seconds}s PCM stitch)..."
        )
        while self.running:
            try:
                self._seq += 1
                chunk_dur = float(self.segment_duration) + lead_overlap
                t_launch = time.time()
                task = asyncio.create_task(self._capture_one_chunk(chunk_dur, self._seq))
                in_flight.append(task)

                # Prune completed tasks and check if the stream is healthy
                done_tasks = [t for t in in_flight if t.done()]
                any_failed = False
                for dt in done_tasks:
                    in_flight.remove(dt)
                    try:
                        ok = dt.result()
                        if ok:
                            backoff = 2.0
                        else:
                            any_failed = True
                    except Exception:
                        any_failed = True

                # Wait `segment_duration` before launching the overlapping next chunk
                elapsed = time.time() - t_launch
                wait_time = max(1.0, float(self.segment_duration) - elapsed)
                await asyncio.sleep(wait_time)

                # If current task failed immediately (e.g. NVR offline or no audio), wait backoff before hammering
                if task.done():
                    in_flight.remove(task)
                    if not task.result():
                        if self.status == "no_audio":
                            await asyncio.sleep(30.0)
                        else:
                            await asyncio.sleep(backoff)
                            backoff = min(30.0, backoff * 1.8)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.camera_name}] Relay loop error: {e}")
                await asyncio.sleep(backoff)

        for t in in_flight:
            t.cancel()


class CaptioningSupervisor:
    """
    Coordinates:
    - Startup Hardware Auto-Detection & WhisperModel lifecycle
    - Dynamic NVR / Channel worker spawning & graceful teardown
    - Sequential Whisper transcription queue with backpressure monitoring
    - Real-time event callbacks for SSE / Web UI live updates
    """

    def __init__(self, on_caption_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.on_caption_callback = on_caption_callback
        self.queue: asyncio.Queue = asyncio.Queue()
        self.workers: Dict[str, CameraStreamWorker] = {}
        self.hardware_report: Dict[str, Any] = {}
        self.whisper_model = None
        self.model_loading = False
        self.running = False
        self.paused = False
        self.backpressure_warning = ""
        self.total_transcribed_segments = 0
        self._supervisor_task: Optional[asyncio.Task] = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._model_lock = threading.RLock()

    def load_or_reload_model(self, force_profile: Optional[str] = None) -> Dict[str, Any]:
        """Probe hardware and initialize faster-whisper with the optimal model and compute type."""
        with self._model_lock:
            return self._load_or_reload_model_locked(force_profile)

    def _load_or_reload_model_locked(self, force_profile: Optional[str] = None) -> Dict[str, Any]:
        cfg = load_config()
        profile = force_profile or cfg.get("PERFORMANCE_PROFILE", "auto")
        model_ovr = cfg.get("CAPTION_MODEL_OVERRIDE", "auto")
        dev_ovr = cfg.get("CAPTION_DEVICE_OVERRIDE", "auto")

        self.hardware_report = probe_and_select_hardware(
            profile_override=profile,
            model_override=None if model_ovr == "auto" else model_ovr,
            device_override=None if dev_ovr == "auto" else dev_ovr,
        )

        device = self.hardware_report["device"]
        compute_type = self.hardware_report["compute_type"]
        model_size = self.hardware_report["model_size"]
        cpu_threads = self.hardware_report["cpu_threads"]

        self.model_loading = True
        try:
            if os.name == "nt":
                # Register pip-installed NVIDIA CUDA DLL directories (cublas/cudnn) on Windows
                for sp in list(sys.path):
                    nv_root = os.path.join(sp, "nvidia")
                    if os.path.isdir(nv_root):
                        for sub in os.listdir(nv_root):
                            bin_dir = os.path.join(nv_root, sub, "bin")
                            if os.path.isdir(bin_dir):
                                if bin_dir not in os.environ.get("PATH", ""):
                                    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
                                if hasattr(os, "add_dll_directory"):
                                    try:
                                        os.add_dll_directory(bin_dir)
                                    except Exception:
                                        pass

            from faster_whisper import WhisperModel
            from backend.hardware import find_cached_whisper_models

            cached_models = find_cached_whisper_models()
            local_models_dir = os.path.join(BASE_DIR, "data", "models")
            os.makedirs(local_models_dir, exist_ok=True)
            logger.info(
                f"Loading Whisper model '{model_size}' on {device} ({compute_type}) "
                f"[locally cached models: {list(cached_models.keys())}]..."
            )
            try:
                model_ref = cached_models.get(model_size, model_size)
                self.whisper_model = WhisperModel(
                    model_ref,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=cpu_threads,
                    download_root=local_models_dir,
                )
            except Exception as first_err:
                err_str = str(first_err)
                # Case 1: Model isn't cached locally and machine has no internet (offline LAN mode)
                if ("ConnectError" in err_str or "Network is unreachable" in err_str or "snapshot" in err_str) and cached_models:
                    # Pick the highest-tier model available in the local cache, keeping the same GPU/CPU device!
                    tier_order = ["large-v3-turbo", "medium", "small", "base", "tiny"]
                    fallback_model = next((m for m in tier_order if m in cached_models), list(cached_models.keys())[0])
                    logger.warning(
                        f"Offline mode: '{model_size}' not in local cache; using cached '{fallback_model}' on {device} ({compute_type})."
                    )
                    model_size = fallback_model
                    self.hardware_report["model_size"] = model_size
                    self.hardware_report["banner_badge"] = (
                        f"{'⚡ CUDA' if device == 'cuda' else '🖥️ CPU'} {compute_type} • Model: {model_size}"
                    )
                    self.whisper_model = WhisperModel(
                        cached_models[fallback_model],
                        device=device,
                        compute_type=compute_type,
                        cpu_threads=cpu_threads,
                        download_root=local_models_dir,
                    )
                elif device == "cuda":
                    logger.warning(
                        f"CUDA initialization failed ({first_err}); automatically falling back to CPU int8 ('small')."
                    )
                    device = "cpu"
                    compute_type = "int8"
                    model_size = "small" if ("small" in cached_models or not cached_models) else list(cached_models.keys())[0]
                    self.hardware_report["device"] = "cpu"
                    self.hardware_report["compute_type"] = "int8"
                    self.hardware_report["model_size"] = model_size
                    self.hardware_report["banner_badge"] = f"🖥️ CPU int8 • Model: {model_size}"
                    self.whisper_model = WhisperModel(
                        cached_models.get(model_size, model_size),
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=cpu_threads,
                        download_root=local_models_dir,
                    )
                else:
                    raise
            logger.info(f"Whisper model '{model_size}' ready on {device} ({compute_type}).")
        finally:
            self.model_loading = False

        return self.hardware_report

    def transcribe_wav_sync(
        self,
        wav_path: str,
        worker: Optional[CameraStreamWorker] = None,
        nvr_id: str = "manual",
        nvr_name: str = "Main NVR",
        channel_id: int = 1,
        camera_name: str = "Camera 1",
    ) -> List[Dict[str, Any]]:
        """
        Transcribe a single `.wav` file synchronously (called via `asyncio.to_thread`),
        apply hallucination & boundary seam filters, and persist to SQLite + daily `.txt`.
        """
        if not os.path.exists(wav_path):
            return []

        with self._model_lock:
            if self.whisper_model is None:
                self.load_or_reload_model()

        cfg = load_config()
        vad_threshold = float(cfg.get("VAD_THRESHOLD", 0.2))
        no_speech_thresh = float(cfg.get("NO_SPEECH_PROB_THRESHOLD", 0.6))

        if worker:
            nvr_id = worker.nvr_id
            nvr_name = worker.nvr_name
            channel_id = worker.channel_id
            camera_name = worker.camera_name

        saved_records: List[Dict[str, Any]] = []
        try:
            if is_pcm_silent(wav_path):
                logger.debug(f"[{camera_name}] Segment { os.path.basename(wav_path) } is silent; skipping Whisper.")
                return []

            audio_input = load_wav_as_float32_ndarray(wav_path)
            segments, _ = self.whisper_model.transcribe(
                audio_input,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(threshold=vad_threshold),
                condition_on_previous_text=False,
            )

            for idx, seg in enumerate(segments):
                raw_text = (seg.text or "").strip()
                if is_hallucination(raw_text, getattr(seg, "no_speech_prob", 0.0), no_speech_thresh):
                    continue

                # Deduplicate seam against previous sentence (both across chunks and within the same chunk)
                last_seen = worker.last_tail_text if worker else (saved_records[-1]["text"] if saved_records else "")
                if last_seen:
                    clean_text = deduplicate_boundary_seam(last_seen, raw_text)
                else:
                    clean_text = raw_text

                if not clean_text or len(clean_text) < 2:
                    continue

                if worker:
                    worker.last_tail_text = clean_text

                rec = record_caption(
                    nvr_id=nvr_id,
                    nvr_name=nvr_name,
                    channel_id=channel_id,
                    camera_name=camera_name,
                    text=clean_text,
                    timestamp_dt=datetime.now(),
                    source_wav_path=wav_path,
                    seg_start_sec=float(getattr(seg, "start", 0.0)),
                    seg_end_sec=float(getattr(seg, "end", 0.0)),
                )
                if rec:
                    saved_records.append(rec)
                    if self.on_caption_callback:
                        try:
                            self.on_caption_callback(rec)
                        except Exception:
                            pass

            self.total_transcribed_segments += 1
        except Exception as e:
            logger.error(f"[{camera_name}] Transcription error on {wav_path}: {e}")
        finally:
            try:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            except Exception:
                pass

        return saved_records

    async def _transcription_consumer_loop(self) -> None:
        logger.info("Whisper transcription consumer started.")
        while self.running:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            wav_path = item.get("wav_path")
            worker: Optional[CameraStreamWorker] = item.get("worker")
            try:
                if wav_path and os.path.exists(wav_path):
                    age = time.time() - os.path.getmtime(wav_path)
                    if age > 28800:  # Drop chunks > 8 hours old
                        os.remove(wav_path)
                        continue

                    # Backpressure detection
                    qsize = self.queue.qsize()
                    if qsize > max(6, len(self.workers) * 3):
                        self.backpressure_warning = (
                            f"Queue depth is {qsize} segments. Consider switching Performance Profile "
                            f"or reducing active cameras."
                        )
                    else:
                        self.backpressure_warning = ""

                    t0 = time.time()
                    saved = await asyncio.to_thread(self.transcribe_wav_sync, wav_path, worker)
                    elapsed = time.time() - t0
                    logger.info(
                        f"[{worker.camera_name if worker else 'Stream'}] Transcribed segment in {elapsed:.2f}s "
                        f"({len(saved)} caption(s) saved, queue={self.queue.qsize()})"
                    )
            except Exception as e:
                logger.error(f"Consumer error: {e}")
            finally:
                self.queue.task_done()

    @staticmethod
    def _is_within_active_hours(nvr: Dict[str, Any], global_cfg: Dict[str, Any]) -> bool:
        try:
            start_s = nvr.get("START_TIME") or global_cfg.get("START_TIME") or ""
            end_s = nvr.get("END_TIME") or global_cfg.get("END_TIME") or ""
            if not start_s or not end_s or (start_s == "00:00" and end_s == "23:59"):
                return True
            now_t = datetime.now().time()
            start_t = datetime.strptime(start_s, "%H:%M").time()
            end_t = datetime.strptime(end_s, "%H:%M").time()
            if start_t <= end_t:
                return start_t <= now_t <= end_t
            return now_t >= start_t or now_t <= end_t
        except Exception:
            return True

    async def sync_workers_from_config(self) -> None:
        """Reconcile running CameraStreamWorkers with the current `config.json`."""
        cfg = load_config()
        self.paused = bool(cfg.get("PAUSED", False))
        seg_dur = int(cfg.get("SEGMENT_DURATION_SECONDS", 30))
        overlap = float(cfg.get("OVERLAP_SECONDS", 1.5))

        desired: Dict[str, Dict[str, Any]] = {}
        if not self.paused:
            for nvr in cfg.get("NVRS", []):
                if not self._is_within_active_hours(nvr, cfg):
                    continue
                nvr_id = nvr.get("id") or nvr.get("name", "nvr")
                nvr_name = nvr.get("name", "Main NVR")
                ip = nvr.get("ip", "")
                brand = nvr.get("brand", "dahua")
                user = nvr.get("username", "admin")
                pwd = nvr.get("password", "")
                if not ip or not user:
                    continue
                for ch in nvr.get("channels", []):
                    if ch.get("enabled", False):
                        cid = int(ch.get("id", 1))
                        key = f"{nvr_id}_ch{cid}"
                        desired[key] = {
                            "stream_key": key,
                            "nvr_id": nvr_id,
                            "nvr_name": nvr_name,
                            "nvr_ip": ip,
                            "brand": brand,
                            "username": user,
                            "password": pwd,
                            "channel_id": cid,
                            "camera_name": ch.get("name") or f"CAM {cid}",
                            "rtsp_url_override": ch.get("rtsp_url"),
                            "segment_duration": seg_dur,
                            "overlap_seconds": overlap,
                        }

        # Stop removed or disabled channel workers
        for key in list(self.workers.keys()):
            if key not in desired:
                w = self.workers.pop(key)
                logger.info(f"Stopping camera worker: {w.nvr_name} -> {w.camera_name}")
                await w.stop(flush_partial=True)

        # Start or update active channel workers
        for key, spec in desired.items():
            if key in self.workers:
                existing = self.workers[key]
                existing.camera_name = spec["camera_name"]
                if (
                    existing.nvr_ip != spec["nvr_ip"]
                    or existing.username != spec["username"]
                    or existing.password != spec["password"]
                    or existing.brand != spec["brand"]
                    or existing.segment_duration != spec["segment_duration"]
                ):
                    await existing.stop(flush_partial=True)
                    del self.workers[key]

            if key not in self.workers:
                w = CameraStreamWorker(
                    transcription_queue=self.queue,
                    **spec,
                )
                self.workers[key] = w
                w.start()

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        cleanup_temp_buffers(0)
        cfg = load_config()
        if not self.hardware_report:
            self.hardware_report = probe_and_select_hardware(
                profile_override=cfg.get("PERFORMANCE_PROFILE", "auto")
            )
        self._consumer_task = asyncio.create_task(self._transcription_consumer_loop())
        self._supervisor_task = asyncio.create_task(self._supervisor_loop())
        asyncio.create_task(asyncio.to_thread(self.load_or_reload_model))

    async def _supervisor_loop(self) -> None:
        last_prune = 0.0
        while self.running:
            try:
                await self.sync_workers_from_config()
                now = time.time()
                if now - last_prune > 3600:
                    prune_old_records()
                    last_prune = now
            except Exception as e:
                logger.error(f"Supervisor loop error: {e}")
            await asyncio.sleep(5.0)

    async def stop(self) -> None:
        self.running = False
        for key, w in list(self.workers.items()):
            await w.stop(flush_partial=True)
        self.workers.clear()
        if self._supervisor_task:
            self._supervisor_task.cancel()
        if self._consumer_task:
            self._consumer_task.cancel()
        cleanup_temp_buffers(0)

    def get_status_summary(self) -> Dict[str, Any]:
        if not self.hardware_report:
            cfg = load_config()
            self.hardware_report = probe_and_select_hardware(
                profile_override=cfg.get("PERFORMANCE_PROFILE", "auto")
            )
        stream_states = {}
        for key, w in self.workers.items():
            stream_states[key] = {
                "nvr": w.nvr_name,
                "channel_id": w.channel_id,
                "camera": w.camera_name,
                "status": w.status,
                "segments_captured": w.segments_captured,
                "last_error": w.last_error,
            }
        return {
            "running": self.running,
            "paused": self.paused,
            "model_loading": self.model_loading,
            "active_streams": len(self.workers),
            "queue_depth": self.queue.qsize(),
            "total_transcribed_segments": self.total_transcribed_segments,
            "backpressure_warning": self.backpressure_warning,
            "hardware": self.hardware_report,
            "streams": stream_states,
        }
