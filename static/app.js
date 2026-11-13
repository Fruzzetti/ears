/**
 * Ears — Two-View Dark Glass Frontend Controller (Zero-Node Vanilla JS)
 * Handles:
 * - First-Run Setup Wizard & Localhost/LAN Session Auth
 * - Live Transcripts Timeline, Date Nav, Search, Camera Filter Pills, Inline Audio Player
 * - 5-Second LAN NVR Scanner Drawer + Unified NVR Cards + Auto-Discover Channels + Inline Toggles/Rename
 * - Live Predictive Storage Estimator (updates dynamically as the Retention Days slider moves)
 */

let authToken = localStorage.getItem("ears_token") || "";
let appConfig = null;
let liveStatus = null;
let selectedCameraFilter = "ALL";
let searchDebounceTimer = null;
let storageDebounceTimer = null;
let statusPollTimer = null;
let eventSource = null;
let activeAudioEl = null;

function getTodayStr() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

async function apiFetch(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }
  if (options.body && typeof options.body === "object" && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, Object.assign({}, options, { headers }));
  if (res.status === 401) {
    showAuthModal(false);
    throw new Error("Unauthorized");
  }
  return res;
}

function showToast(msg, duration = 3500) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add("hidden"), duration);
}

// ============================================================================
// INITIALIZATION & AUTH FLOW
// ============================================================================

window.addEventListener("DOMContentLoaded", async () => {
  document.getElementById("filter-date").value = getTodayStr();
  document.getElementById("auth-form").addEventListener("submit", handleAuthSubmit);
  await checkAuthAndBoot();
});

async function checkAuthAndBoot() {
  try {
    const headers = authToken ? { Authorization: `Bearer ${authToken}` } : {};
    const res = await fetch("/api/auth/status", { headers });
    const status = await res.json();

    if (status.first_run_required) {
      showAuthModal(true, status.username || "admin");
      return;
    }
    if (!status.authenticated) {
      showAuthModal(false, status.username || "admin");
      return;
    }

    document.getElementById("auth-overlay").classList.add("hidden");
    const logoutBtn = document.getElementById("logout-btn");
    if (!status.is_localhost || !status.auto_login_localhost) {
      logoutBtn.classList.remove("hidden");
    } else {
      logoutBtn.classList.add("hidden");
    }

    await refreshConfigAndStatus();
    await loadTranscripts();
    connectSseStream();
    if (statusPollTimer) clearInterval(statusPollTimer);
    statusPollTimer = setInterval(refreshStatusOnly, 6000);
  } catch (err) {
    console.error("Boot error:", err);
  }
}

function showAuthModal(isFirstRun, defaultUser = "admin") {
  const overlay = document.getElementById("auth-overlay");
  const title = document.getElementById("auth-title");
  const sub = document.getElementById("auth-subtitle");
  const opts = document.getElementById("auth-setup-options");
  const btn = document.getElementById("auth-submit-btn");
  const errEl = document.getElementById("auth-error");

  overlay.dataset.mode = isFirstRun ? "setup" : "login";
  document.getElementById("auth-username").value = defaultUser;
  document.getElementById("auth-password").value = "";
  errEl.classList.add("hidden");

  if (isFirstRun) {
    title.textContent = "Welcome to Ears — First-Run Setup";
    sub.textContent = "Create your administrator login. Localhost access can bypass login automatically.";
    opts.classList.remove("hidden");
    btn.textContent = "Complete Setup & Launch Ears";
  } else {
    title.textContent = "Sign In to Ears";
    sub.textContent = "Enter your Ears credentials to access transcripts and camera settings.";
    opts.classList.add("hidden");
    btn.textContent = "Sign In";
  }
  overlay.classList.remove("hidden");
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  const overlay = document.getElementById("auth-overlay");
  const isSetup = overlay.dataset.mode === "setup";
  const username = document.getElementById("auth-username").value.trim() || "admin";
  const password = document.getElementById("auth-password").value;
  const errEl = document.getElementById("auth-error");
  errEl.classList.add("hidden");

  try {
    const endpoint = isSetup ? "/api/auth/setup" : "/api/auth/login";
    const payload = { username, password };
    if (isSetup) {
      payload.auto_login_localhost = document.getElementById("auth-auto-local").checked;
      payload.allow_lan_access = document.getElementById("auth-allow-lan").checked;
    }

    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.status === "error") {
      errEl.textContent = data.message || "Authentication failed.";
      errEl.classList.remove("hidden");
      return;
    }

    authToken = data.token;
    localStorage.setItem("ears_token", authToken);
    overlay.classList.add("hidden");
    await checkAuthAndBoot();
  } catch (err) {
    errEl.textContent = "Network error communicating with Ears server.";
    errEl.classList.remove("hidden");
  }
}

function logout() {
  authToken = "";
  localStorage.removeItem("ears_token");
  showAuthModal(false);
}

// ============================================================================
// NAVIGATION & STATUS POLLING
// ============================================================================

function switchView(view) {
  const isTranscripts = view === "transcripts";
  document.getElementById("view-transcripts").classList.toggle("hidden", !isTranscripts);
  document.getElementById("view-settings").classList.toggle("hidden", isTranscripts);
  document.getElementById("tab-transcripts").classList.toggle("active", isTranscripts);
  document.getElementById("tab-settings").classList.toggle("active", !isTranscripts);

  if (!isTranscripts) {
    updateStoragePreview();
  } else {
    loadTranscripts();
  }
}

async function refreshConfigAndStatus() {
  const [cfgRes, statRes] = await Promise.all([
    apiFetch("/api/config"),
    apiFetch("/api/status"),
  ]);
  appConfig = await cfgRes.json();
  liveStatus = await statRes.json();
  renderTopStatus();
  renderCameraFilterPills();
  renderSettingsView();
}

async function refreshStatusOnly() {
  try {
    const res = await apiFetch("/api/status");
    liveStatus = await res.json();
    renderTopStatus();
    updateChannelHealthDots();
  } catch (e) {
    // ignore transient poll errors
  }
}

function renderTopStatus() {
  if (!liveStatus || !liveStatus.supervisor) return;
  const sup = liveStatus.supervisor;
  const hw = sup.hardware || {};

  document.getElementById("hw-badge").textContent =
    hw.banner_badge || `${hw.device || "CPU"} • Model: ${hw.model_size || "small"}`;

  const pill = document.getElementById("status-pill");
  const pillText = document.getElementById("status-pill-text");
  const pauseBtn = document.getElementById("pause-btn");

  if (sup.paused) {
    pill.className = "status-pill status-paused";
    pillText.textContent = "Paused";
    pauseBtn.textContent = "Resume";
  } else {
    pill.className = "status-pill status-active";
    pillText.textContent = `Capturing (${sup.active_streams || 0} Cam${sup.active_streams === 1 ? "" : "s"})`;
    pauseBtn.textContent = "Pause";
  }

  const bp = document.getElementById("backpressure-banner");
  const streamErrors = Object.values(sup.streams || {})
    .filter((s) => s.last_error)
    .map((s) => `${s.camera}: ${s.last_error}`);

  if (sup.model_loading) {
    bp.textContent = "⏳ Loading / downloading Whisper model in background... Captured audio segments are queued and will appear as soon as the model is ready.";
    bp.classList.remove("hidden");
  } else if (streamErrors.length > 0) {
    bp.textContent = `⚠️ Stream Alert — ${streamErrors.join(" | ")}`;
    bp.classList.remove("hidden");
  } else if (sup.backpressure_warning) {
    bp.textContent = `⚠️ ${sup.backpressure_warning}`;
    bp.classList.remove("hidden");
  } else {
    bp.classList.add("hidden");
  }
}

async function togglePause() {
  if (!liveStatus || !liveStatus.supervisor) return;
  const nextPaused = !liveStatus.supervisor.paused;
  await apiFetch("/api/control/pause", {
    method: "POST",
    body: { paused: nextPaused },
  });
  showToast(nextPaused ? "Captioning paused." : "Captioning resumed.");
  await refreshStatusOnly();
}

// ============================================================================
// VIEW 1: LIVE TRANSCRIPTS, FILTERS, & SSE STREAM
// ============================================================================

function renderCameraFilterPills() {
  const container = document.getElementById("camera-pills");
  if (!appConfig) return;

  const pills = [{ key: "ALL", label: "All Cameras" }];
  for (const nvr of appConfig.NVRS || []) {
    for (const ch of nvr.channels || []) {
      if (ch.enabled) {
        const name = ch.name || `CAM ${ch.id}`;
        pills.push({ key: name, label: `${name} (${nvr.name || nvr.ip})` });
      }
    }
  }

  container.innerHTML = pills
    .map(
      (p) =>
        `<button class="pill ${selectedCameraFilter === p.key ? "active" : ""}" onclick="selectCameraPill('${p.key.replace(/'/g, "\\'")}')">${escapeHtml(p.label)}</button>`
    )
    .join("");
}

function selectCameraPill(camKey) {
  selectedCameraFilter = camKey;
  renderCameraFilterPills();
  loadTranscripts();
}

function shiftDate(deltaDays) {
  const input = document.getElementById("filter-date");
  const cur = input.value ? new Date(input.value + "T12:00:00") : new Date();
  cur.setDate(cur.getDate() + deltaDays);
  const y = cur.getFullYear();
  const m = String(cur.getMonth() + 1).padStart(2, "0");
  const d = String(cur.getDate()).padStart(2, "0");
  input.value = `${y}-${m}-${d}`;
  document.getElementById("search-all-dates").checked = false;
  loadTranscripts();
}

function setToday() {
  document.getElementById("filter-date").value = getTodayStr();
  document.getElementById("search-all-dates").checked = false;
  loadTranscripts();
}

function debounceSearch() {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => loadTranscripts(), 260);
}

async function loadTranscripts() {
  const dateVal = document.getElementById("search-all-dates").checked
    ? "all"
    : document.getElementById("filter-date").value || getTodayStr();
  const searchVal = document.getElementById("filter-search").value.trim();
  const camVal = selectedCameraFilter === "ALL" ? "" : selectedCameraFilter;

  const params = new URLSearchParams();
  if (dateVal) params.set("date", dateVal);
  if (searchVal) params.set("q", searchVal);
  if (camVal) params.set("camera", camVal);

  try {
    const res = await apiFetch(`/api/captions?${params.toString()}`);
    const data = await res.json();
    renderTranscriptsList(data.events || []);
  } catch (err) {
    console.error("Failed loading captions:", err);
  }
}

function renderTranscriptsList(events) {
  const listEl = document.getElementById("transcripts-list");
  if (!events.length) {
    const hasNvrs = appConfig && (appConfig.NVRS || []).length > 0;
    listEl.innerHTML = `<div class="empty-state">
      ${
        hasNvrs
          ? "No speech captions recorded for this filter yet. Listening to active camera streams..."
          : 'No NVRs or cameras configured yet. Click <strong>"Devices & Settings"</strong> above to scan your network or add a camera!'
      }
    </div>`;
    return;
  }

  // Show newest at the top for easy live monitoring, or chronological
  const sorted = [...events].reverse();
  listEl.innerHTML = sorted
    .map((ev) => {
      const clips = ev.audio_clips || [];
      const audioHtml = clips.length
        ? `<div class="audio-chips">
            ${clips
              .map(
                (url, i) =>
                  `<button class="audio-play-btn" onclick="playAudioSnippet('${url}')">▶ Play Audio${
                    clips.length > 1 ? " #" + (i + 1) : ""
                  }</button>`
              )
              .join("")}
          </div>`
        : "";

      return `<div class="caption-card">
        <div class="caption-meta">
          <div class="caption-badges">
            <span class="cam-badge">${escapeHtml(ev.camera || "Camera")}</span>
            <span class="nvr-badge">${escapeHtml(ev.nvr || "")}</span>
          </div>
          <span class="caption-time">${escapeHtml(ev.timestamp || "")}</span>
        </div>
        <div class="caption-text">${escapeHtml(ev.text || "")}</div>
        ${audioHtml}
      </div>`;
    })
    .join("");
}

function playAudioSnippet(url) {
  if (activeAudioEl) {
    activeAudioEl.pause();
    activeAudioEl = null;
  }
  const sep = url.includes("?") ? "&" : "?";
  const authUrl = authToken ? `${url}${sep}token=${encodeURIComponent(authToken)}` : url;
  activeAudioEl = new Audio(authUrl);
  activeAudioEl.play().catch((e) => showToast("Could not play audio clip: " + e.message));
}

function exportTranscripts(fmt) {
  const dateVal = document.getElementById("search-all-dates").checked
    ? "all"
    : document.getElementById("filter-date").value || getTodayStr();
  const searchVal = document.getElementById("filter-search").value.trim();
  const camVal = selectedCameraFilter === "ALL" ? "" : selectedCameraFilter;

  const params = new URLSearchParams({ fmt });
  if (dateVal) params.set("date", dateVal);
  if (searchVal) params.set("q", searchVal);
  if (camVal) params.set("camera", camVal);
  if (authToken) params.set("token", authToken);

  window.open(`/api/captions/export?${params.toString()}`, "_blank");
}

function connectSseStream() {
  if (eventSource) {
    eventSource.close();
  }
  const url = authToken ? `/api/stream?token=${encodeURIComponent(authToken)}` : "/api/stream";
  eventSource = new EventSource(url);
  eventSource.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.event === "caption") {
        const curDate = document.getElementById("filter-date").value;
        if (curDate === getTodayStr() || document.getElementById("search-all-dates").checked) {
          loadTranscripts();
        }
      }
    } catch (err) {
      // ignore
    }
  };
}

// ============================================================================
// VIEW 2: DEVICES & SETTINGS — UNIFIED NVR CARDS & LAN SCANNER
// ============================================================================

function renderSettingsView() {
  if (!appConfig) return;
  renderNvrCards();

  document.getElementById("cfg-storage-path").value = appConfig.STORAGE_PATH || "";
  document.getElementById("toggle-save-audio").classList.toggle("active", !!appConfig.SAVE_AUDIO_FILES);
  document.getElementById("text-only-warning")?.classList.toggle("hidden", !!appConfig.SAVE_AUDIO_FILES);

  const retDays = appConfig.RETENTION_DAYS || 30;
  document.getElementById("cfg-retention-days").value = retDays;
  document.getElementById("retention-days-label").textContent = `${retDays} Day${retDays === 1 ? "" : "s"}`;

  document.getElementById("cfg-perf-profile").value = appConfig.PERFORMANCE_PROFILE || "auto";
  document.getElementById("cfg-model-override").value = appConfig.CAPTION_MODEL_OVERRIDE || "auto";
  document.getElementById("cfg-segment-duration").value = appConfig.SEGMENT_DURATION_SECONDS || 30;
  document.getElementById("cfg-group-window").value = appConfig.GROUP_WINDOW_SECONDS ?? 5.0;
  document.getElementById("cfg-vad-threshold").value = appConfig.VAD_THRESHOLD ?? 0.2;
  document.getElementById("cfg-web-port").value = appConfig.WEB_PORT || 8765;

  document.getElementById("toggle-auto-local").classList.toggle("active", appConfig.AUTO_LOGIN_LOCALHOST !== false);
  document.getElementById("toggle-allow-lan").classList.toggle("active", !!appConfig.ALLOW_LAN_ACCESS);

  updateStoragePreview();
}

function buildNvrChannelsMarkup(nvr, nvrIdx) {
  const channels = nvr.channels || [];
  if (!channels.length) {
    return `<div class="muted small" style="padding:10px 0;">
      No channels loaded yet. Enter your NVR Username & Password above and click <strong>"Auto-Discover Channels"</strong>, or click <strong>"+ Add Custom Channel"</strong>.
    </div>`;
  }
  return `<div class="channels-grid">
    ${channels
      .map((ch) => {
        const streamKey = `${nvr.id || nvr.name}_ch${ch.id}`;
        const st = liveStatus?.supervisor?.streams?.[streamKey];
        const healthColor = !ch.enabled
          ? "#64748b"
          : st?.status === "recording"
          ? "#34d399"
          : st?.status === "no_audio"
          ? "#f87171"
          : "#fbbf24";
        const healthTitle = !ch.enabled
          ? "Disabled"
          : st?.status === "recording"
          ? `Capturing audio (${st.segments_captured} segments)`
          : st?.last_error || "Connecting...";

        return `<div class="channel-tile ${ch.enabled ? "enabled" : ""}">
          <div class="channel-left">
            <div class="toggle-switch ${ch.enabled ? "active" : ""}" onclick="toggleChannelEnabled(${nvrIdx}, ${ch.id})">
              <div class="toggle-knob"></div>
            </div>
            <input
              type="text"
              class="channel-name-input"
              value="${escapeAttr(ch.name || `Channel ${ch.id}`)}"
              title="Click to rename camera"
              autocomplete="off"
              spellcheck="false"
              oninput="updateChannelName(${nvrIdx}, ${ch.id}, this.value)"
            />
          </div>
          <div style="display:flex;align-items:center;gap:6px;">
            <span class="ch-id-tag">Ch ${ch.id}</span>
            <span id="ch-dot-${nvrIdx}-${ch.id}" class="pulse-dot" style="background:${healthColor};color:${healthColor}" title="${escapeAttr(healthTitle)}"></span>
          </div>
        </div>`;
      })
      .join("")}
  </div>`;
}

function renderNvrChannelsOnly(nvrIdx) {
  const nvr = appConfig?.NVRS?.[nvrIdx];
  if (!nvr) return;
  const wrap = document.getElementById(`nvr-channels-wrap-${nvrIdx}`);
  if (wrap) {
    wrap.innerHTML = buildNvrChannelsMarkup(nvr, nvrIdx);
  }
  const badge = document.getElementById(`nvr-cam-badge-${nvrIdx}`);
  if (badge) {
    const channels = nvr.channels || [];
    const enabledCount = channels.filter((c) => c.enabled).length;
    badge.textContent = `${enabledCount} / ${channels.length} Active`;
  }
}

function toggleSecretMask(btnEl, inputId) {
  const inp = document.getElementById(inputId);
  if (!inp) return;
  if (inp.value === "********") {
    showToast("Saved password is hidden on the server for security. Type a new password to replace it.");
    return;
  }
  const isMasked = inp.classList.toggle("masked-secret");
  btnEl.textContent = isMasked ? "Show" : "Hide";
}

function syncNvrInputsFromDom() {
  if (!appConfig || !Array.isArray(appConfig.NVRS)) return;
  appConfig.NVRS.forEach((nvr, idx) => {
    const nameEl = document.getElementById(`nvr-name-${idx}`);
    const ipEl = document.getElementById(`nvr-ip-${idx}`);
    const brandEl = document.getElementById(`nvr-brand-${idx}`);
    const userEl = document.getElementById(`nvr-user-${idx}`);
    const passEl = document.getElementById(`nvr-pass-${idx}`);
    if (nameEl) nvr.name = nameEl.value;
    if (ipEl) nvr.ip = ipEl.value.trim();
    if (brandEl) nvr.brand = brandEl.value;
    if (userEl) nvr.username = userEl.value.trim();
    if (passEl) nvr.password = passEl.value;
  });
}

function renderNvrCards() {
  const container = document.getElementById("nvr-cards-container");
  const nvrs = appConfig.NVRS || [];

  if (!nvrs.length) {
    container.innerHTML = `<div class="empty-state">
      No NVRs or cameras added yet. Click <strong>"Scan Network for NVRs (5s)"</strong> to automatically discover cameras on your LAN, or click <strong>"+ Add NVR / Camera"</strong>.
    </div>`;
    return;
  }

  container.innerHTML = nvrs
    .map((nvr, nvrIdx) => {
      const channels = nvr.channels || [];
      const enabledCount = channels.filter((c) => c.enabled).length;
      const channelsHtml = buildNvrChannelsMarkup(nvr, nvrIdx);

      return `<div class="nvr-card">
        <div class="nvr-card-top">
          <div class="nvr-card-title">
            <span id="nvr-title-${nvrIdx}">${escapeHtml(nvr.name || "NVR")}</span>
            <span id="nvr-cam-badge-${nvrIdx}" class="cam-badge">${enabledCount} / ${channels.length} Active</span>
          </div>
          <div style="display:flex;gap:8px;">
            <button type="button" class="btn btn-ghost btn-xs" onclick="enableAllChannels(${nvrIdx}, true)">Enable All</button>
            <button type="button" class="btn btn-ghost btn-xs" onclick="enableAllChannels(${nvrIdx}, false)">Disable All</button>
            <button type="button" class="btn btn-danger btn-xs" onclick="removeNvrCard(${nvrIdx})">Remove NVR</button>
          </div>
        </div>

        <div class="nvr-fields-grid">
          <div class="form-field">
            <label>Friendly NVR Name</label>
            <input id="nvr-name-${nvrIdx}" name="ears_nvr_label_${nvrIdx}" type="text" autocomplete="off" spellcheck="false" value="${escapeAttr(nvr.name ?? "")}" oninput="updateNvrField(${nvrIdx}, 'name', this.value)" placeholder="e.g. Inside NVR" />
          </div>
          <div class="form-field">
            <label>IP Address / Host</label>
            <input id="nvr-ip-${nvrIdx}" name="ears_nvr_host_${nvrIdx}" type="text" autocomplete="off" spellcheck="false" value="${escapeAttr(nvr.ip ?? "")}" oninput="updateNvrField(${nvrIdx}, 'ip', this.value)" placeholder="e.g. 10.0.0.3" />
          </div>
          <div class="form-field">
            <label>Brand / Protocol</label>
            <select id="nvr-brand-${nvrIdx}" onchange="updateNvrField(${nvrIdx}, 'brand', this.value)">
              ${["dahua", "hikvision", "uniview", "reolink", "amcrest", "onvif"]
                .map(
                  (b) =>
                    `<option value="${b}" ${(nvr.brand || "dahua").toLowerCase() === b ? "selected" : ""}>${b.toUpperCase()}</option>`
                )
                .join("")}
            </select>
          </div>
          <div class="form-field">
            <label>RTSP Username</label>
            <input id="nvr-user-${nvrIdx}" name="ears_rtsp_acct_${nvrIdx}" type="text" autocomplete="off" spellcheck="false" data-lpignore="true" data-1p-ignore data-form-type="other" value="${escapeAttr(nvr.username ?? "admin")}" oninput="updateNvrField(${nvrIdx}, 'username', this.value)" placeholder="e.g. admin" />
          </div>
          <div class="form-field">
            <label>RTSP Password</label>
            <div class="secret-input-wrap">
              <input id="nvr-pass-${nvrIdx}" name="ears_rtsp_key_${nvrIdx}" type="text" class="masked-secret" autocomplete="off" spellcheck="false" data-lpignore="true" data-1p-ignore data-form-type="other" value="${escapeAttr(nvr.password ?? "")}" oninput="updateNvrField(${nvrIdx}, 'password', this.value)" placeholder="Camera / NVR Password" />
              <button type="button" class="secret-toggle-btn" onclick="toggleSecretMask(this, 'nvr-pass-${nvrIdx}')" title="Show or hide password">Show</button>
            </div>
          </div>
          <div class="form-field">
            <label>Active Hours (Start – End)</label>
            <div style="display:flex;gap:4px;">
              <input type="time" value="${escapeAttr(nvr.START_TIME || "00:00")}" oninput="updateNvrField(${nvrIdx}, 'START_TIME', this.value)" style="flex:1" />
              <input type="time" value="${escapeAttr(nvr.END_TIME || "23:59")}" oninput="updateNvrField(${nvrIdx}, 'END_TIME', this.value)" style="flex:1" />
            </div>
          </div>
        </div>

        <div class="nvr-discover-bar">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <button type="button" id="disc-btn-${nvrIdx}" class="btn btn-primary btn-sm" onclick="discoverChannelsForNvr(${nvrIdx})">
              🔄 Auto-Discover Channels
            </button>
            <button type="button" class="btn btn-ghost btn-sm" onclick="addCustomChannel(${nvrIdx})">
              + Add Custom Channel
            </button>
            <span id="disc-msg-${nvrIdx}" class="muted small"></span>
          </div>
        </div>

        <div id="nvr-channels-wrap-${nvrIdx}">
          ${channelsHtml}
        </div>
      </div>`;
    })
    .join("");
}

function updateChannelHealthDots() {
  if (!appConfig || document.getElementById("view-settings").classList.contains("hidden")) {
    return;
  }
  const nvrs = appConfig.NVRS || [];
  nvrs.forEach((nvr, nvrIdx) => {
    for (const ch of nvr.channels || []) {
      const dotEl = document.getElementById(`ch-dot-${nvrIdx}-${ch.id}`);
      if (!dotEl) continue;
      const streamKey = `${nvr.id || nvr.name}_ch${ch.id}`;
      const st = liveStatus?.supervisor?.streams?.[streamKey];
      const healthColor = !ch.enabled
        ? "#64748b"
        : st?.status === "recording"
        ? "#34d399"
        : st?.status === "no_audio"
        ? "#f87171"
        : "#fbbf24";
      const healthTitle = !ch.enabled
        ? "Disabled"
        : st?.status === "recording"
        ? `Capturing audio (${st.segments_captured} segments)`
        : st?.last_error || "Connecting...";
      dotEl.style.background = healthColor;
      dotEl.style.color = healthColor;
      dotEl.title = healthTitle;
    }
  });
}

function addNvrCard(prefill = null) {
  syncNvrInputsFromDom();
  if (!appConfig.NVRS) appConfig.NVRS = [];
  // Reuse credentials from first NVR if available for convenience
  const firstNvr = appConfig.NVRS[0] || {};
  const nextIdx = appConfig.NVRS.length + 1;
  appConfig.NVRS.push({
    id: "nvr_" + Date.now() + "_" + nextIdx,
    name: prefill?.name || `NVR ${nextIdx}`,
    ip: prefill?.ip || "10.0.0.100",
    brand: prefill?.brand || "dahua",
    username: prefill?.username ?? firstNvr.username ?? "admin",
    password: prefill?.password ?? "",
    START_TIME: "00:00",
    END_TIME: "23:59",
    channels: [],
  });
  renderNvrCards();
  updateStoragePreview();
}

function removeNvrCard(nvrIdx) {
  syncNvrInputsFromDom();
  const nvr = appConfig.NVRS[nvrIdx];
  if (!confirm(`Remove "${nvr?.name || nvr?.ip}" from Ears?`)) return;
  appConfig.NVRS.splice(nvrIdx, 1);
  renderNvrCards();
  updateStoragePreview();
}

function updateNvrField(nvrIdx, key, val) {
  if (!appConfig.NVRS[nvrIdx]) return;
  appConfig.NVRS[nvrIdx][key] = val;
  if (key === "name") {
    const titleEl = document.getElementById(`nvr-title-${nvrIdx}`);
    if (titleEl) titleEl.textContent = val || "NVR";
  }
}

function toggleChannelEnabled(nvrIdx, chId) {
  const nvr = appConfig.NVRS[nvrIdx];
  if (!nvr) return;
  nvr.channels = (nvr.channels || []).map((c) =>
    c.id === chId ? Object.assign({}, c, { enabled: !c.enabled }) : c
  );
  renderNvrChannelsOnly(nvrIdx);
  updateStoragePreview();
}

function enableAllChannels(nvrIdx, enabledState) {
  const nvr = appConfig.NVRS[nvrIdx];
  if (!nvr) return;
  nvr.channels = (nvr.channels || []).map((c) => Object.assign({}, c, { enabled: enabledState }));
  renderNvrChannelsOnly(nvrIdx);
  updateStoragePreview();
}

function updateChannelName(nvrIdx, chId, newName) {
  const nvr = appConfig.NVRS[nvrIdx];
  if (!nvr) return;
  nvr.channels = (nvr.channels || []).map((c) =>
    c.id === chId ? Object.assign({}, c, { name: newName.trim() || `Channel ${chId}` }) : c
  );
}

function addCustomChannel(nvrIdx) {
  const nvr = appConfig.NVRS[nvrIdx];
  if (!nvr) return;
  if (!nvr.channels) nvr.channels = [];
  const nextId = nvr.channels.reduce((m, c) => Math.max(m, Number(c.id) || 0), 0) + 1;
  nvr.channels.push({ id: nextId, name: `Camera ${nextId}`, enabled: true });
  renderNvrChannelsOnly(nvrIdx);
  updateStoragePreview();
}

async function discoverChannelsForNvr(nvrIdx) {
  syncNvrInputsFromDom();
  const nvr = appConfig.NVRS[nvrIdx];
  if (!nvr) return;
  const btn = document.getElementById(`disc-btn-${nvrIdx}`);
  const msg = document.getElementById(`disc-msg-${nvrIdx}`);
  btn.disabled = true;
  btn.textContent = "Interrogating...";
  msg.textContent = `Querying ${nvr.ip} (${nvr.brand})...`;

  try {
    const res = await apiFetch("/api/nvr/channels", {
      method: "POST",
      body: {
        ip: nvr.ip,
        username: nvr.username,
        password: nvr.password,
        brand: nvr.brand,
      },
    });
    const data = await res.json();
    const list = data.channels || data.fallback || [];
    if (list.length) {
      const existingMap = {};
      for (const exc of nvr.channels || []) {
        existingMap[exc.id] = exc;
      }
      nvr.channels = list.map((c) => ({
        id: c.id,
        name: existingMap[c.id]?.name && !existingMap[c.id].name.startsWith("Channel ")
          ? existingMap[c.id].name
          : c.name,
        enabled: existingMap[c.id] ? !!existingMap[c.id].enabled : false,
      }));
      renderNvrChannelsOnly(nvrIdx);
      msg.textContent = data.message || `Loaded ${list.length} channels.`;
      showToast(
        data.status === "success"
          ? `Discovered ${list.length} channels on ${nvr.name}!`
          : data.message || "Loaded fallback channels."
      );
    }
  } catch (err) {
    msg.textContent = "Failed querying NVR channels.";
  } finally {
    btn.disabled = false;
    btn.textContent = "🔄 Auto-Discover Channels";
  }
}

async function scanLanForNvrs() {
  const btn = document.getElementById("scan-lan-btn");
  const drawer = document.getElementById("scanner-drawer");
  const statusText = document.getElementById("scanner-status-text");
  const resultsEl = document.getElementById("scanner-results");

  drawer.classList.remove("hidden");
  btn.disabled = true;
  statusText.textContent = "Running two-pass 5-second LAN broadcast + TCP sweep (Dahua, Hikvision, ONVIF, Reolink, Uniview)...";
  resultsEl.innerHTML = `<div class="muted small">Probing local subnets...</div>`;

  try {
    const res = await apiFetch("/api/nvr/scan", { method: "POST" });
    const data = await res.json();
    const devs = data.devices || [];
    statusText.textContent = `Scan completed in ${data.elapsed_seconds}s across ${(data.subnets_scanned || []).join(", ")} — Found ${devs.length} device(s)`;

    if (!devs.length) {
      resultsEl.innerHTML = `<div class="muted small">No NVRs or IP cameras responded on the local subnet. You can still click "+ Add NVR / Camera" manually.</div>`;
      return;
    }

    const existingIps = new Set((appConfig.NVRS || []).map((n) => n.ip));
    resultsEl.innerHTML = devs
      .map((d) => {
        const alreadyAdded = existingIps.has(d.ip);
        return `<div class="scan-device-card">
          <div>
            <div class="scan-ip">${escapeHtml(d.ip)}</div>
            <div class="scan-vendor">${escapeHtml(d.display_vendor || d.vendor)}</div>
            <div class="scan-meta">MAC: ${escapeHtml(d.mac)} • Ports: ${(d.ports || []).join(", ")}</div>
            <div class="scan-meta">Via: ${(d.discovery_sources || []).join(", ")}</div>
          </div>
          <button
            type="button"
            class="btn ${alreadyAdded ? "btn-ghost" : "btn-accent"} btn-sm"
            onclick="addDiscoveredDevice('${escapeAttr(d.ip)}', '${escapeAttr(d.brand)}', '${escapeAttr(d.vendor)}')"
          >
            ${alreadyAdded ? "Already Added (+ Add Again)" : "+ Use This Device"}
          </button>
        </div>`;
      })
      .join("");
  } catch (err) {
    statusText.textContent = "Network scan failed: " + err.message;
  } finally {
    btn.disabled = false;
  }
}

function addDiscoveredDevice(ip, brand, vendor) {
  addNvrCard({
    name: `${vendor} (${ip})`,
    ip: ip,
    brand: brand || "dahua",
  });
  showToast(`Added ${vendor} (${ip}). Enter its credentials and click Auto-Discover Channels!`);
}

function closeScannerDrawer() {
  document.getElementById("scanner-drawer").classList.add("hidden");
}

// ============================================================================
// STORAGE, RETENTION SLIDER & LIVE PREDICTIVE DISK METER
// ============================================================================

function toggleSaveAudioSwitch() {
  appConfig.SAVE_AUDIO_FILES = !appConfig.SAVE_AUDIO_FILES;
  document.getElementById("toggle-save-audio").classList.toggle("active", !!appConfig.SAVE_AUDIO_FILES);
  document.getElementById("text-only-warning")?.classList.toggle("hidden", !!appConfig.SAVE_AUDIO_FILES);
  updateStoragePreview();
}

function setRetentionDays(days) {
  document.getElementById("cfg-retention-days").value = days;
  onRetentionSliderMove(days);
}

function onRetentionSliderMove(daysVal) {
  const days = parseInt(daysVal, 10) || 30;
  appConfig.RETENTION_DAYS = days;
  document.getElementById("retention-days-label").textContent = `${days} Day${days === 1 ? "" : "s"}`;
  clearTimeout(storageDebounceTimer);
  storageDebounceTimer = setTimeout(() => updateStoragePreview(), 80);
}

async function updateStoragePreview() {
  if (!appConfig) return;
  const days = parseInt(document.getElementById("cfg-retention-days").value, 10) || 30;
  const saveAudio = !!appConfig.SAVE_AUDIO_FILES;
  const storagePath = document.getElementById("cfg-storage-path").value.trim();

  const params = new URLSearchParams({
    retention_days: String(days),
    save_audio: String(saveAudio),
  });
  if (storagePath) params.set("storage_path", storagePath);

  try {
    const res = await apiFetch(`/api/storage/estimate?${params.toString()}`);
    const est = await res.json();

    document.getElementById("stat-current-total").textContent = est.current.total_human;
    document.getElementById("stat-current-breakdown").textContent =
      `Transcripts: ${est.current.transcripts_human} • Audio Clips: ${est.current.audio_human}`;

    document.getElementById("pred-days-span").textContent = String(days);
    document.getElementById("pred-audio-span").textContent = saveAudio ? "With Audio Clips" : "Text Only";
    document.getElementById("stat-predicted-total").textContent = est.predicted.total_human;
    document.getElementById("stat-predicted-breakdown").textContent =
      `Est. Rate: ${est.predicted.daily_rate_human} (${est.enabled_cameras} active cam${est.enabled_cameras === 1 ? "" : "s"}) • ${est.predicted.percent_of_free_drive}% of free space`;

    document.getElementById("stat-drive-free").textContent = est.drive.free_human;
    document.getElementById("stat-drive-total").textContent = `Total Drive Capacity: ${est.drive.total_human}`;

    const driveTotal = Math.max(1, est.drive.total_bytes);
    const curPct = Math.min(100, Math.max(0.5, (est.current.total_bytes / driveTotal) * 100));
    const predPct = Math.min(100 - curPct, Math.max(0.5, (est.predicted.total_bytes / driveTotal) * 100));
    document.getElementById("meter-bar-current").style.width = `${curPct}%`;
    document.getElementById("meter-bar-predicted").style.width = `${predPct}%`;
  } catch (err) {
    console.error("Storage estimate error:", err);
  }
}

async function browseStorageFolder() {
  try {
    const res = await apiFetch("/api/storage/browse", { method: "POST" });
    const data = await res.json();
    if (data.status === "success" && data.path) {
      document.getElementById("cfg-storage-path").value = data.path;
      appConfig.STORAGE_PATH = data.path;
      updateStoragePreview();
    } else if (data.status === "headless") {
      const manual = prompt("Enter absolute storage folder path on server:", data.path || "");
      if (manual && manual.trim()) {
        document.getElementById("cfg-storage-path").value = manual.trim();
        appConfig.STORAGE_PATH = manual.trim();
        updateStoragePreview();
      }
    }
  } catch (e) {
    showToast("Could not open native folder dialog.");
  }
}

async function pruneRecordsNow() {
  const days = parseInt(document.getElementById("cfg-retention-days").value, 10) || 30;
  const res = await apiFetch("/api/storage/prune", {
    method: "POST",
    body: { retention_days: days },
  });
  const data = await res.json();
  if (data.status === "success") {
    const p = data.pruned;
    showToast(
      `Pruned records older than ${days}d (${p.deleted_db_rows} DB rows, ${p.deleted_txt_files} .txt files, ${p.deleted_audio_files} audio clips).`
    );
    updateStoragePreview();
  }
}

function toggleConfigFlag(key) {
  appConfig[key] = !appConfig[key];
  document.getElementById("toggle-auto-local").classList.toggle("active", appConfig.AUTO_LOGIN_LOCALHOST !== false);
  document.getElementById("toggle-allow-lan").classList.toggle("active", !!appConfig.ALLOW_LAN_ACCESS);
}

async function saveAllSettings() {
  if (!appConfig) return;
  syncNvrInputsFromDom();
  appConfig.STORAGE_PATH = document.getElementById("cfg-storage-path").value.trim() || appConfig.STORAGE_PATH;
  appConfig.RETENTION_DAYS = parseInt(document.getElementById("cfg-retention-days").value, 10) || 30;
  appConfig.PERFORMANCE_PROFILE = document.getElementById("cfg-perf-profile").value;
  appConfig.CAPTION_MODEL_OVERRIDE = document.getElementById("cfg-model-override").value;
  appConfig.SEGMENT_DURATION_SECONDS = parseInt(document.getElementById("cfg-segment-duration").value, 10) || 30;
  appConfig.GROUP_WINDOW_SECONDS = parseFloat(document.getElementById("cfg-group-window").value) || 5.0;
  appConfig.VAD_THRESHOLD = parseFloat(document.getElementById("cfg-vad-threshold").value) || 0.2;
  appConfig.WEB_PORT = parseInt(document.getElementById("cfg-web-port").value, 10) || 8765;

  const msgEl = document.getElementById("save-status-msg");
  msgEl.textContent = "Saving configuration & syncing camera workers...";

  try {
    const res = await apiFetch("/api/config", {
      method: "POST",
      body: appConfig,
    });
    const data = await res.json();
    if (data.status === "success") {
      appConfig = data.config;
      showToast("Configuration saved & applied to active streams!");
      msgEl.textContent = "Saved! All active camera workers updated.";
      await refreshStatusOnly();
      renderCameraFilterPills();
    }
  } catch (err) {
    showToast("Failed saving configuration: " + err.message);
    msgEl.textContent = "Error saving configuration.";
  }
}

// Helpers
function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(str) {
  return escapeHtml(str).replace(/'/g, "&#39;");
}
