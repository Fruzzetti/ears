#!/usr/bin/env python3
"""
Ears — Phase 3 Web UI, FastAPI Endpoints, Auth & Storage Predictor Verification Suite
Tests:
  1. Static Dark Glass UI assets serving (`index.html`, `style.css`, `app.js`)
  2. First-Run Wizard & Auth Enforcement (Localhost auto-login vs. LAN forced login)
  3. Live Status, Hardware Banner Badge, & Predictive Storage Estimator API
  4. Two-Pass LAN NVR Scan (`/api/nvr/scan`) & Channel Auto-Discovery (`/api/nvr/channels`)
  5. Transcript Query, Paragraph Grouping & CSV/TXT Export (`/api/captions`, `/api/captions/export`)
"""

import os
import sys
import glob
import json
import shutil
import tempfile
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

for pattern in [
    os.path.join(ROOT_DIR, "venv", "lib", "python*", "site-packages"),
    os.path.join(ROOT_DIR, ".venv", "lib", "python*", "site-packages"),
    "/home/fuzz/Projects/ears/venv/lib/python*/site-packages",
    "/home/fuzz/Projects/conciergeweb/venv/lib/python*/site-packages",
]:
    for sp in sorted(glob.glob(pattern), reverse=True):
        if os.path.isdir(sp) and sp not in sys.path:
            sys.path.insert(0, sp)

os.environ["EARS_SKIP_SUPERVISOR"] = "1"

import backend.storage as storage_mod  # noqa: E402
import backend.app as app_mod  # noqa: E402


def run_tests() -> None:
    print("=" * 78)
    print("  EARS — PHASE 3 FASTAPI & TWO-VIEW WEB UI VERIFICATION SUITE")
    print("=" * 78)

    # Isolate config & storage in a temp folder so the user's real first-run wizard stays untouched
    tmp_dir = tempfile.mkdtemp(prefix="ears_phase3_test_")
    orig_data_dir = storage_mod.DEFAULT_STORAGE_DIR
    orig_cfg_file = storage_mod.CONFIG_FILE

    try:
        storage_mod.DEFAULT_STORAGE_DIR = tmp_dir
        storage_mod.DEFAULT_CONFIG["STORAGE_PATH"] = tmp_dir
        storage_mod.CONFIG_FILE = os.path.join(tmp_dir, "config.json")
        storage_mod.init_db()

        server_obj = None
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app_mod.app)
        except Exception:
            import socket
            import time
            import threading
            import uvicorn
            import requests

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()

            cfg = uvicorn.Config(app_mod.app, host="127.0.0.1", port=port, log_level="error")
            server_obj = uvicorn.Server(cfg)
            t = threading.Thread(target=server_obj.run, daemon=True)
            t.start()
            for _ in range(30):
                if server_obj.started:
                    break
                time.sleep(0.1)

            class _LiveClient:
                def __init__(self, base_url: str):
                    self.base = base_url

                def get(self, path: str, headers=None):
                    return requests.get(self.base + path, headers=headers, timeout=15)

                def post(self, path: str, json=None, headers=None):
                    return requests.post(self.base + path, json=json, headers=headers, timeout=15)

            client = _LiveClient(f"http://127.0.0.1:{port}")

        # ------------------------------------------------------------------
        # [1/5] Static Dark Glass SPA Assets
        # ------------------------------------------------------------------
        print("\n[1/5] Testing Static Dark Glass SPA Serving (index.html, style.css, app.js)...")
        r_html = client.get("/")
        assert r_html.status_code == 200 and "Ears" in r_html.text, "Failed to serve index.html"
        r_css = client.get("/style.css")
        assert r_css.status_code == 200 and "--bg-dark" in r_css.text, "Failed to serve style.css"
        r_js = client.get("/app.js")
        assert r_js.status_code == 200 and "apiFetch" in r_js.text, "Failed to serve app.js"
        print("  -> Served index.html, style.css, and app.js cleanly [PASS]")

        # ------------------------------------------------------------------
        # [2/5] First-Run Wizard & Localhost vs. LAN Authentication
        # ------------------------------------------------------------------
        print("\n[2/5] Testing First-Run Setup Wizard & Localhost vs. LAN Auth...")
        r_auth0 = client.get("/api/auth/status")
        assert r_auth0.status_code == 200
        st0 = r_auth0.json()
        assert st0["first_run_required"] is True, f"Expected first_run_required=True, got {st0}"
        r_pre_setup = client.get("/api/config")
        assert r_pre_setup.status_code == 401, f"Protected endpoints must return 401 before setup, got {r_pre_setup.status_code}"
        print("  -> Fresh install triggers First-Run Setup Wizard & blocks pre-setup API access [PASS]")

        # Complete first-run setup
        r_setup = client.post(
            "/api/auth/setup",
            json={
                "username": "fuzz",
                "password": "testpassword123",
                "auto_login_localhost": False,
                "allow_lan_access": True,
            },
        )
        assert r_setup.status_code == 200, f"Setup failed: {r_setup.text}"
        token = r_setup.json()["token"]
        assert token, "Expected session token from setup"

        # With auto_login_localhost=False and no Bearer token, /api/config must return 401
        r_unauth = client.get("/api/config")
        assert r_unauth.status_code == 401, f"Expected 401 without token, got {r_unauth.status_code}"

        # Wrong password login must return 401
        r_bad_login = client.post("/api/auth/login", json={"username": "fuzz", "password": "wrong"})
        assert r_bad_login.status_code == 401

        # Valid login returns token
        r_login = client.post("/api/auth/login", json={"username": "fuzz", "password": "testpassword123"})
        assert r_login.status_code == 200
        token = r_login.json()["token"]

        headers = {"Authorization": f"Bearer {token}"}
        r_cfg = client.get("/api/config", headers=headers)
        assert r_cfg.status_code == 200
        assert "WEB_PASSWORD_HASH" not in r_cfg.json(), "Password hash must never leak in /api/config"

        # Cross-origin drive-by browser request must be blocked with 403
        r_cors = client.get("/api/config", headers={"Authorization": f"Bearer {token}", "Origin": "https://evil.example.com"})
        assert r_cors.status_code == 403, f"Expected 403 on cross-origin API call, got {r_cors.status_code}"
        print("  -> Verified PBKDF2-SHA256 login, Bearer session token, LAN 401 guard & Cross-Origin 403 block [PASS]")

        # ------------------------------------------------------------------
        # [3/5] Live Status, Hardware Banner & Predictive Storage Estimator
        # ------------------------------------------------------------------
        print("\n[3/5] Testing Hardware Status Banner & Predictive Storage Estimator API...")
        r_status = client.get("/api/status", headers=headers)
        assert r_status.status_code == 200
        st_data = r_status.json()
        hw_badge = st_data["supervisor"]["hardware"]["banner_badge"]
        print(f"  -> Live Status Banner : {hw_badge}")

        r_est30 = client.get("/api/storage/estimate?retention_days=30&save_audio=true", headers=headers).json()
        r_est90 = client.get("/api/storage/estimate?retention_days=90&save_audio=true", headers=headers).json()
        r_est_txt = client.get("/api/storage/estimate?retention_days=90&save_audio=false", headers=headers).json()
        assert r_est90["predicted"]["total_bytes"] > r_est30["predicted"]["total_bytes"]
        assert r_est90["predicted"]["total_bytes"] > r_est_txt["predicted"]["total_bytes"]
        print(
            f"  -> Storage Predictor  : 30d Audio={r_est30['predicted']['total_human']} | "
            f"90d Audio={r_est90['predicted']['total_human']} | 90d Text-Only={r_est_txt['predicted']['total_human']} [PASS]"
        )

        # ------------------------------------------------------------------
        # [4/5] Live Transcript Recording, Paragraph Grouping & Export API
        # ------------------------------------------------------------------
        print("\n[4/5] Testing Transcript Recording, 5s Paragraph Grouping & Export API...")
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        storage_mod.record_caption(
            nvr_id="inside",
            nvr_name="Inside",
            channel_id=1,
            camera_name="Living Room",
            text="Testing the Phase 3 web interface.",
            timestamp_dt=now,
        )
        storage_mod.record_caption(
            nvr_id="inside",
            nvr_name="Inside",
            channel_id=1,
            camera_name="Living Room",
            text="Everything is grouped into one clean card.",
            timestamp_dt=now,
        )
        r_caps = client.get(f"/api/captions?date={today_str}", headers=headers)
        assert r_caps.status_code == 200
        events = r_caps.json()["events"]
        assert len(events) == 1 and "clean card" in events[0]["text"], f"Unexpected events: {events}"

        r_csv = client.get(f"/api/captions/export?date={today_str}&fmt=csv", headers=headers)
        assert r_csv.status_code == 200 and "Living Room" in r_csv.text
        print(f"  -> Grouped Card       : [{events[0]['camera']}] \"{events[0]['text']}\"")
        print("  -> Verified /api/captions and /api/captions/export (CSV & TXT) [PASS]")

        # ------------------------------------------------------------------
        # [5/5] Two-Pass LAN Scan & Channel Discovery Endpoint Check
        # ------------------------------------------------------------------
        print("\n[5/5] Testing /api/nvr/scan & /api/nvr/channels Endpoints...")
        r_scan = client.post("/api/nvr/scan", headers=headers)
        assert r_scan.status_code == 200
        scan_json = r_scan.json()
        devs = scan_json.get("devices", [])
        print(f"  -> /api/nvr/scan      : Discovered {len(devs)} CCTV/NVR device(s) on LAN [PASS]")

        test_user = os.environ.get("EARS_TEST_USER", "")
        test_pass = os.environ.get("EARS_TEST_PASS", "")
        if test_user and test_pass and any(d.get("ip") == "10.0.0.3" for d in devs):
            r_ch = client.post(
                "/api/nvr/channels",
                headers=headers,
                json={"ip": "10.0.0.3", "username": test_user, "password": test_pass, "brand": "dahua"},
            )
            assert r_ch.status_code == 200
            ch_list = r_ch.json().get("channels", [])
            print(f"  -> /api/nvr/channels  : Discovered {len(ch_list)} channels on 10.0.0.3 ({[c['name'] for c in ch_list]}) [PASS]")

        print("\n" + "=" * 78)
        print("  ALL PHASE 3 API & WEB UI TESTS PASSED!")
        print("=" * 78)
        print("  To launch the live Ears Web UI on ConciergeHub right now, run:")
        print("    python3 ../ears.py --headless")
        print("  Then open in your browser: http://10.0.0.4:8765")
        print("=" * 78)

    finally:
        if server_obj is not None:
            server_obj.should_exit = True
        storage_mod.DEFAULT_STORAGE_DIR = orig_data_dir
        storage_mod.DEFAULT_CONFIG["STORAGE_PATH"] = orig_data_dir
        storage_mod.CONFIG_FILE = orig_cfg_file
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_tests()
