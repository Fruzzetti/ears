"""
Ears — FastAPI Web Server, Authentication & First-Run Setup,
Live Server-Sent Events (SSE) Feed, Storage Predictor API, and LAN/Channel Discovery API.
"""

import os
import sys
import json
import time
import hmac
import base64
import hashlib
import secrets
import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Query
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, PlainTextResponse

from backend.hardware import probe_and_select_hardware, PERFORMANCE_PROFILES, WHISPER_MODELS_INFO
from backend.storage import (
    BASE_DIR,
    load_config,
    save_config,
    get_storage_paths,
    query_captions,
    calculate_storage_and_prediction,
    prune_old_records,
    init_db,
)
from backend.discovery import scan_network_for_nvrs, discover_nvr_channels
from backend.captioner import CaptioningSupervisor

logger = logging.getLogger("ears.app")

STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# Secret key generated or persisted per install
_TOKEN_SECRET = secrets.token_hex(32)
PASSWORD_MASK = "********"

# Per-IP failed login rate limiter: ip -> list of failure timestamps
_LOGIN_FAILURES: Dict[str, List[float]] = {}
_LOGIN_MAX_ATTEMPTS = 6
_LOGIN_WINDOW_SEC = 60.0

# Global Captioning Supervisor & SSE broadcast subscribers
supervisor: Optional[CaptioningSupervisor] = None
sse_subscribers: List[asyncio.Queue] = []


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Hash a password using stdlib PBKDF2-HMAC-SHA256 (zero external C-extension required)."""
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against PBKDF2-HMAC-SHA256 or legacy bcrypt hash."""
    if not stored_hash:
        return False
    if stored_hash.startswith("pbkdf2_sha256$"):
        parts = stored_hash.split("$")
        if len(parts) == 3:
            expected = hash_password(password, parts[1])
            return hmac.compare_digest(expected, stored_hash)
    elif stored_hash.startswith("$2"):
        try:
            import bcrypt
            return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
        except Exception:
            pass
    return False


def create_session_token(username: str) -> str:
    payload = json.dumps({"u": username, "exp": int(time.time()) + 86400 * 14}).encode("utf-8")
    b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    sig = hmac.new(_TOKEN_SECRET.encode("ascii"), b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_session_token(token: str) -> Optional[str]:
    if not token or "." not in token:
        return None
    b64, sig = token.split(".", 1)
    expected = hmac.new(_TOKEN_SECRET.encode("ascii"), b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        pad = "=" * ((4 - len(b64) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(b64 + pad).decode("utf-8"))
        if data.get("exp", 0) >= time.time():
            return data.get("u")
    except Exception:
        pass
    return None


def is_localhost_request(request: Request) -> bool:
    client_host = (request.client.host if request.client else "") or ""
    return client_host in ("127.0.0.1", "::1", "localhost", "testclient")


def _mask_config_secrets(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Strip WEB_PASSWORD_HASH and mask NVR RTSP passwords before returning config to browser."""
    safe_cfg = json.loads(json.dumps(cfg))
    safe_cfg.pop("WEB_PASSWORD_HASH", None)
    for nvr in safe_cfg.get("NVRS", []):
        if isinstance(nvr, dict) and nvr.get("password"):
            nvr["password"] = PASSWORD_MASK
    return safe_cfg


def _restore_masked_nvr_passwords(incoming_nvrs: Any, existing_nvrs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """If an NVR entry has password == PASSWORD_MASK, preserve its existing stored password."""
    if not isinstance(incoming_nvrs, list):
        return existing_nvrs
    by_id = {n.get("id"): n.get("password", "") for n in existing_nvrs if isinstance(n, dict) and n.get("id")}
    by_ip = {n.get("ip"): n.get("password", "") for n in existing_nvrs if isinstance(n, dict) and n.get("ip")}
    fallback_pw = next((n.get("password", "") for n in existing_nvrs if isinstance(n, dict) and n.get("password")), "")
    restored = []
    for nvr in incoming_nvrs:
        if not isinstance(nvr, dict):
            continue
        item = dict(nvr)
        if item.get("password") == PASSWORD_MASK:
            item["password"] = by_id.get(item.get("id")) or by_ip.get(item.get("ip")) or fallback_pw
        restored.append(item)
    return restored


async def require_auth(request: Request) -> str:
    """
    Enforce authentication rules:
    1. First-run setup must be completed before accessing protected endpoints.
    2. If connecting from a non-localhost IP and `ALLOW_LAN_ACCESS` is False, deny with 403.
    3. If connecting from localhost (127.0.0.1) AND `AUTO_LOGIN_LOCALHOST` is True, allow automatically.
    4. Otherwise require a valid Bearer session token (or `?token=` query param for audio/SSE streams).
    """
    cfg = load_config()
    if not cfg.get("FIRST_RUN_COMPLETE", False) or not cfg.get("WEB_PASSWORD_HASH"):
        raise HTTPException(status_code=401, detail="First-run setup required")

    is_local = is_localhost_request(request)
    if not is_local and not cfg.get("ALLOW_LAN_ACCESS", False):
        raise HTTPException(status_code=403, detail="LAN access is disabled")

    if is_local and cfg.get("AUTO_LOGIN_LOCALHOST", True):
        return cfg.get("WEB_USERNAME", "admin")

    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    elif "token" in request.query_params:
        token = request.query_params["token"]

    user = verify_session_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _on_new_caption(record: Dict[str, Any]) -> None:
    """Callback invoked from CaptioningSupervisor thread when a new caption is saved."""
    payload = json.dumps({"event": "caption", "data": record})
    for q in list(sse_subscribers):
        try:
            q.put_nowait(payload)
        except Exception:
            pass


app = FastAPI(title="Ears — Audio Captioning Utility", version="0.1.0")


@app.middleware("http")
async def security_and_origin_guard(request: Request, call_next):
    """
    Protect localhost & LAN users from:
    1. Cross-Origin Drive-By Browser Requests (blocks external `Origin` headers on `/api/*`).
    2. DNS-Rebinding attacks against `127.0.0.1` (validates `Host` header on localhost requests).
    3. Clickjacking & MIME-sniffing (`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`).
    """
    host_hdr = (request.headers.get("host") or "").strip().lower()
    host_only = host_hdr.split(":")[0].strip("[]")

    if is_localhost_request(request) and host_only not in ("", "127.0.0.1", "localhost", "::1", "testserver"):
        # Allow valid IPv4 literals (LAN interface accessed locally), block arbitrary external domain names (DNS rebinding)
        parts = host_only.split(".")
        if not (len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)):
            return JSONResponse(status_code=403, content={"status": "error", "message": "Invalid Host header."})

    if request.url.path.startswith("/api/"):
        origin = (request.headers.get("origin") or "").strip()
        if origin and origin != "null":
            parsed_origin = urlparse(origin)
            origin_netloc = (parsed_origin.netloc or "").strip().lower()
            if origin_netloc and host_hdr and origin_netloc != host_hdr:
                return JSONResponse(
                    status_code=403,
                    content={"status": "error", "message": "Cross-origin API access denied."},
                )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.on_event("startup")
async def on_startup():
    global supervisor
    init_db()
    supervisor = CaptioningSupervisor(on_caption_callback=_on_new_caption)
    if not os.environ.get("EARS_SKIP_SUPERVISOR"):
        asyncio.create_task(supervisor.start())


@app.on_event("shutdown")
async def on_shutdown():
    global supervisor
    if supervisor:
        await supervisor.stop()


# ==============================================================================
# AUTH & FIRST-RUN WIZARD ENDPOINTS
# ==============================================================================

@app.get("/api/auth/status")
async def auth_status(request: Request):
    cfg = load_config()
    first_run = not bool(cfg.get("FIRST_RUN_COMPLETE", False) and cfg.get("WEB_PASSWORD_HASH"))
    is_local = is_localhost_request(request)
    auto_login_local = bool(cfg.get("AUTO_LOGIN_LOCALHOST", True))

    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
    valid_token_user = verify_session_token(token)

    authenticated = False
    if first_run:
        authenticated = False
    elif is_local and auto_login_local:
        authenticated = True
    elif valid_token_user:
        authenticated = True

    return {
        "first_run_required": first_run,
        "authenticated": authenticated,
        "is_localhost": is_local,
        "auto_login_localhost": auto_login_local,
        "allow_lan_access": bool(cfg.get("ALLOW_LAN_ACCESS", False)),
        "username": cfg.get("WEB_USERNAME", "admin"),
    }


@app.post("/api/auth/setup")
async def first_run_setup(request: Request):
    cfg = load_config()
    if cfg.get("FIRST_RUN_COMPLETE", False) and cfg.get("WEB_PASSWORD_HASH"):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Setup already completed."})

    has_local_gui = (
        sys.platform in ("win32", "darwin")
        or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    )
    if has_local_gui and not is_localhost_request(request):
        return JSONResponse(
            status_code=403,
            content={"status": "error", "message": "First-run setup on a desktop/laptop must be completed locally on 127.0.0.1."},
        )

    data = await request.json()
    username = (data.get("username") or "admin").strip()
    password = data.get("password") or ""
    auto_login_local = bool(data.get("auto_login_localhost", True))
    allow_lan = bool(data.get("allow_lan_access", True))

    if len(password) < 4:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Password must be at least 4 characters."})

    save_config({
        "WEB_USERNAME": username,
        "WEB_PASSWORD_HASH": hash_password(password),
        "AUTO_LOGIN_LOCALHOST": auto_login_local,
        "ALLOW_LAN_ACCESS": allow_lan,
        "FIRST_RUN_COMPLETE": True,
    })
    token = create_session_token(username)
    return {"status": "success", "token": token, "username": username}


@app.post("/api/auth/login")
async def login(request: Request):
    client_ip = (request.client.host if request.client else "unknown") or "unknown"
    now = time.time()
    recent_fails = [t for t in _LOGIN_FAILURES.get(client_ip, []) if now - t < _LOGIN_WINDOW_SEC]
    _LOGIN_FAILURES[client_ip] = recent_fails
    if len(recent_fails) >= _LOGIN_MAX_ATTEMPTS:
        return JSONResponse(
            status_code=429,
            content={"status": "error", "message": "Too many failed login attempts. Please wait 60 seconds."},
        )

    data = await request.json()
    username = (data.get("username") or "admin").strip()
    password = data.get("password") or ""

    cfg = load_config()
    if not is_localhost_request(request) and not cfg.get("ALLOW_LAN_ACCESS", False):
        return JSONResponse(status_code=403, content={"status": "error", "message": "LAN access is disabled."})

    expected_user = cfg.get("WEB_USERNAME", "admin")
    stored_hash = cfg.get("WEB_PASSWORD_HASH", "")

    if username.lower() != expected_user.lower() or not verify_password(password, stored_hash):
        _LOGIN_FAILURES.setdefault(client_ip, []).append(now)
        return JSONResponse(status_code=401, content={"status": "error", "message": "Invalid username or password."})

    _LOGIN_FAILURES.pop(client_ip, None)
    token = create_session_token(expected_user)
    return {"status": "success", "token": token, "username": expected_user}


@app.post("/api/auth/password")
async def change_password(request: Request, user: str = Depends(require_auth)):
    data = await request.json()
    old_pwd = data.get("oldPassword") or ""
    new_pwd = data.get("newPassword") or ""
    new_user = (data.get("username") or "").strip()

    if len(new_pwd) < 4:
        return JSONResponse(status_code=400, content={"status": "error", "message": "New password must be at least 4 characters."})

    cfg = load_config()
    stored_hash = cfg.get("WEB_PASSWORD_HASH", "")
    if stored_hash and not is_localhost_request(request):
        if not verify_password(old_pwd, stored_hash):
            return JSONResponse(status_code=401, content={"status": "error", "message": "Incorrect current password."})

    updates: Dict[str, Any] = {"WEB_PASSWORD_HASH": hash_password(new_pwd)}
    if new_user:
        updates["WEB_USERNAME"] = new_user
    save_config(updates)
    return {"status": "success", "token": create_session_token(new_user or cfg.get("WEB_USERNAME", "admin"))}


# ==============================================================================
# LIVE STATUS, HARDWARE, & CONFIG ENDPOINTS
# ==============================================================================

@app.get("/api/status")
async def get_status(user: str = Depends(require_auth)):
    cfg = load_config()
    sup_status = supervisor.get_status_summary() if supervisor else {
        "running": False,
        "paused": bool(cfg.get("PAUSED", False)),
        "model_loading": False,
        "active_streams": 0,
        "queue_depth": 0,
        "total_transcribed_segments": 0,
        "backpressure_warning": "",
        "hardware": probe_and_select_hardware(profile_override=cfg.get("PERFORMANCE_PROFILE", "auto")),
        "streams": {},
    }
    return {
        "status": "success",
        "supervisor": sup_status,
        "save_audio_files": bool(cfg.get("SAVE_AUDIO_FILES", False)),
        "storage_path": get_storage_paths(cfg)["base"],
        "retention_days": int(cfg.get("RETENTION_DAYS", 30)),
        "web_port": int(cfg.get("WEB_PORT", 8765)),
    }


@app.get("/api/config")
async def api_get_config(user: str = Depends(require_auth)):
    cfg = load_config()
    return _mask_config_secrets(cfg)


@app.post("/api/config")
async def api_save_config(request: Request, user: str = Depends(require_auth)):
    data = await request.json()
    old_cfg = load_config()
    old_profile = old_cfg.get("PERFORMANCE_PROFILE", "auto")
    old_model = old_cfg.get("CAPTION_MODEL_OVERRIDE", "auto")
    old_dev = old_cfg.get("CAPTION_DEVICE_OVERRIDE", "auto")

    # Preserve password hash and restore any masked NVR RTSP passwords
    data.pop("WEB_PASSWORD_HASH", None)
    if "NVRS" in data:
        data["NVRS"] = _restore_masked_nvr_passwords(data["NVRS"], old_cfg.get("NVRS", []))
    updated = save_config(data)

    # Reconcile camera workers immediately
    if supervisor:
        new_profile = updated.get("PERFORMANCE_PROFILE", "auto")
        new_model = updated.get("CAPTION_MODEL_OVERRIDE", "auto")
        new_dev = updated.get("CAPTION_DEVICE_OVERRIDE", "auto")
        if (new_profile, new_model, new_dev) != (old_profile, old_model, old_dev):
            asyncio.create_task(asyncio.to_thread(supervisor.load_or_reload_model))
        await supervisor.sync_workers_from_config()

    return {"status": "success", "config": _mask_config_secrets(updated)}


@app.post("/api/control/pause")
async def toggle_pause(request: Request, user: str = Depends(require_auth)):
    data = await request.json()
    paused = bool(data.get("paused", False))
    save_config({"PAUSED": paused})
    if supervisor:
        await supervisor.sync_workers_from_config()
    return {"status": "success", "paused": paused}


# ==============================================================================
# NVR NETWORK DISCOVERY & CHANNEL AUTO-DISCOVERY
# ==============================================================================

@app.post("/api/nvr/scan")
async def api_scan_network(user: str = Depends(require_auth)):
    res = await scan_network_for_nvrs(listen_time=2.5, two_pass=True)
    return res


@app.post("/api/nvr/channels")
async def api_discover_channels(request: Request, user: str = Depends(require_auth)):
    data = await request.json()
    ip = (data.get("ip") or "").strip()
    username = (data.get("username") or "admin").strip()
    password = data.get("password") or ""
    brand = (data.get("brand") or "dahua").strip()
    if not ip:
        return JSONResponse(status_code=400, content={"status": "error", "message": "NVR IP address is required."})

    if password == PASSWORD_MASK:
        cfg = load_config()
        existing_nvrs = cfg.get("NVRS", [])
        for existing in existing_nvrs:
            if isinstance(existing, dict) and existing.get("ip") == ip and existing.get("password"):
                password = existing["password"]
                break
        if password == PASSWORD_MASK:
            password = next(
                (n.get("password", "") for n in existing_nvrs if isinstance(n, dict) and n.get("password")),
                "",
            )

    res = await asyncio.to_thread(discover_nvr_channels, ip, username, password, brand, 5.0)
    return res


# ==============================================================================
# STORAGE METER, RETENTION PREDICTOR, & NATIVE FOLDER PICKER
# ==============================================================================

@app.get("/api/storage/estimate")
async def api_storage_estimate(
    retention_days: Optional[int] = Query(None),
    save_audio: Optional[bool] = Query(None),
    storage_path: Optional[str] = Query(None),
    user: str = Depends(require_auth),
):
    est = await asyncio.to_thread(
        calculate_storage_and_prediction,
        proposed_retention_days=retention_days,
        proposed_save_audio=save_audio,
        proposed_storage_path=storage_path,
    )
    return est


@app.post("/api/storage/prune")
async def api_storage_prune(request: Request, user: str = Depends(require_auth)):
    data = await request.json()
    days = data.get("retention_days")
    res = await asyncio.to_thread(prune_old_records, days)
    est = await asyncio.to_thread(calculate_storage_and_prediction)
    return {"status": "success", "pruned": res, "storage": est}


@app.post("/api/storage/browse")
async def api_browse_folder(request: Request, user: str = Depends(require_auth)):
    """
    Open a native OS folder selection dialog ONLY when requested from localhost on a machine with a GUI display.
    Remote LAN browsers or headless Linux servers receive `status: 'headless'` so the Web UI prompts for a path.
    """
    cfg = load_config()
    initial_dir = cfg.get("STORAGE_PATH") or BASE_DIR
    if not is_localhost_request(request):
        return {"status": "headless", "path": initial_dir}

    def _open_dialog() -> Dict[str, Any]:
        if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return {"status": "headless", "path": initial_dir}
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            selected = filedialog.askdirectory(initialdir=initial_dir, title="Select Ears Transcript & Audio Storage Folder")
            root.destroy()
            if selected:
                return {"status": "success", "path": os.path.normpath(selected)}
            return {"status": "cancelled", "path": initial_dir}
        except Exception as e:
            return {"status": "headless", "path": initial_dir, "message": str(e)}

    return await asyncio.to_thread(_open_dialog)


# ==============================================================================
# LIVE TRANSCRIPTS, AUDIO PLAYBACK, EXPORT, & SSE STREAM
# ==============================================================================

@app.get("/api/captions")
async def api_get_captions(
    date: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    nvr: Optional[str] = Query(None),
    camera: Optional[str] = Query(None),
    group_window: Optional[float] = Query(None),
    user: str = Depends(require_auth),
):
    if not date and not q:
        date = datetime.now().strftime("%Y-%m-%d")
    events = await asyncio.to_thread(
        query_captions,
        date_str=date,
        search_query=q,
        nvr_filter=nvr,
        camera_filter=camera,
        group_window_sec=group_window,
    )
    return {"status": "success", "date": date, "events": events}


@app.get("/api/captions/export")
async def api_export_captions(
    date: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    nvr: Optional[str] = Query(None),
    camera: Optional[str] = Query(None),
    fmt: str = Query("txt"),
    user: str = Depends(require_auth),
):
    if not date and not q:
        date = datetime.now().strftime("%Y-%m-%d")
    events = await asyncio.to_thread(
        query_captions,
        date_str=date,
        search_query=q,
        nvr_filter=nvr,
        camera_filter=camera,
    )
    label = date or "all_dates"
    if fmt.lower() == "csv":
        import csv
        import io
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Timestamp", "NVR", "Camera", "Transcript"])
        for ev in events:
            writer.writerow([ev["timestamp"], ev["nvr"], ev["camera"], ev["text"]])
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="ears_transcripts_{label}.csv"'},
        )
    else:
        lines = [f"[{ev['timestamp']}] [{ev['nvr']} - {ev['camera']}] {ev['text']}" for ev in events]
        return Response(
            content="\n".join(lines) + "\n",
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="ears_transcripts_{label}.txt"'},
        )


@app.get("/api/audio/{rel_path:path}")
async def api_serve_audio(rel_path: str, user: str = Depends(require_auth)):
    paths = get_storage_paths()
    base_abs = os.path.abspath(paths["base"])
    safe_rel = os.path.normpath(rel_path).lstrip("\\/")
    full_path = os.path.abspath(os.path.join(base_abs, safe_rel))
    try:
        if os.path.commonpath([full_path, base_abs]) != base_abs:
            raise HTTPException(status_code=403, detail="Access denied")
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not full_path.lower().endswith(".wav") or not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Audio clip not found")
    return FileResponse(full_path, media_type="audio/wav")


@app.get("/api/stream")
async def api_sse_stream(request: Request, user: str = Depends(require_auth)):
    """Server-Sent Events (SSE) stream pushing new captions and stream status updates in real time."""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    sse_subscribers.append(q)

    async def event_generator():
        try:
            yield f"data: {json.dumps({'event': 'connected', 'ts': time.time()})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive heartbeat
                    yield f": heartbeat {int(time.time())}\n\n"
        finally:
            if q in sse_subscribers:
                sse_subscribers.remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ==============================================================================
# STATIC FRONTEND SPA SERVING
# ==============================================================================

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not full_path or full_path == "/":
        full_path = "index.html"
    static_abs = os.path.abspath(STATIC_DIR)
    target = os.path.abspath(os.path.join(static_abs, full_path))
    try:
        is_inside = os.path.commonpath([target, static_abs]) == static_abs
    except ValueError:
        is_inside = False
    if is_inside and os.path.isfile(target):
        return FileResponse(target)
    index_file = os.path.join(static_abs, "index.html")
    if os.path.isfile(index_file):
        return FileResponse(index_file)
    return PlainTextResponse("Ears Web UI static files not found.", status_code=404)
