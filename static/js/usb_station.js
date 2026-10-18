// Stasiun kamera USB di PC loket: browser membaca kamera (getUserMedia), mengirim frame JPEG
// kecil ke server, lalu menggambar kotak wajah dari respons. Frame tidak disimpan di server.
// Membutuhkan HTTPS (secure context) kecuali dibuka dari http://localhost.
(function () {
  const COLORS = { "": "#cbd5e1", POOR: "#fb923c", MATCHING: "#60a5fa", CANDIDATE: "#22c55e",
                   NEEDS_REVIEW: "#facc15", NO_MATCH: "#f43f5e", ERROR: "#a855f7" };
  const MAX_WIDTH = 640;
  const store = {
    get(k, d) { try { return localStorage.getItem("facerec." + k) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("facerec." + k, v); } catch (e) { /* abaikan */ } },
  };

  const S = {
    mode: "server", active: false, stream: null, timer: null, inflight: false,
    station: "", deviceId: "", fps: 0, latency: 0, faces: [], error: "", sent: 0, fpsStarted: 0,
    cfg: null, onMode: null,
  };
  let el = {};

  function msg(text) {
    el.msg.textContent = text || "";
    el.msg.classList.toggle("d-none", !text);
  }

  function setStatus(error) {
    S.error = error || "";
    if (error) msg(error);
  }

  async function listDevices() {
    try {
      const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
      el.device.innerHTML = devices.length
        ? devices.map((d, i) => `<option value="${d.deviceId}">${FaceCommon.esc(d.label || "Kamera " + (i + 1))}</option>`).join("")
        : '<option value="">(kamera tidak ditemukan)</option>';
      if (S.deviceId && devices.some((d) => d.deviceId === S.deviceId)) el.device.value = S.deviceId;
    } catch (e) {
      el.device.innerHTML = '<option value="">(tidak dapat membaca daftar kamera)</option>';
    }
  }

  function validStation(v) { return /^[A-Za-z0-9_.\-]{1,40}$/.test(v); }

  async function start() {
    if (!window.isSecureContext) {
      setStatus("Kamera USB butuh HTTPS. Buka aplikasi lewat https://… (lihat panduan deploy), atau dari http://localhost.");
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("Browser tidak mendukung akses kamera. Gunakan Chrome/Edge terbaru.");
      return;
    }
    S.station = el.station.value.trim() || "loket-1";
    if (!validStation(S.station)) { setStatus("Nama stasiun hanya huruf, angka, titik, garis bawah, tanda hubung."); return; }
    el.station.value = S.station;
    store.set("station", S.station);
    S.deviceId = el.device.value || "";
    const video = { width: { ideal: 1280 }, height: { ideal: 720 } };
    if (S.deviceId) video.deviceId = { exact: S.deviceId };
    try {
      S.stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    } catch (e) {
      const hint = e.name === "NotAllowedError" ? "Izin kamera ditolak — izinkan kamera untuk situs ini."
        : e.name === "NotReadableError" ? "Kamera sedang dipakai aplikasi lain (Zoom/Teams/Camera)."
        : e.name === "OverconstrainedError" ? "Kamera terpilih tidak tersedia, pilih perangkat lain." : e.name;
      setStatus("Kamera USB tidak dapat dibuka: " + hint);
      return;
    }
    const track = S.stream.getVideoTracks()[0];
    S.deviceId = (track.getSettings && track.getSettings().deviceId) || S.deviceId;
    store.set("device", S.deviceId);
    track.addEventListener("ended", () => { stop(); setStatus("Kamera USB terlepas/terputus. Colokkan lagi lalu klik Mulai."); });
    el.video.srcObject = S.stream;
    await el.video.play().catch(() => {});
    el.video.classList.remove("d-none");
    el.overlay.classList.remove("d-none");
    msg("");
    S.active = true;
    S.error = "";
    S.sent = 0;
    S.fpsStarted = performance.now();
    el.toggle.textContent = "Berhenti";
    el.toggle.classList.replace("btn-success", "btn-danger");
    await listDevices();
    loop();
  }

  function stop() {
    S.active = false;
    clearTimeout(S.timer);
    if (S.stream) S.stream.getTracks().forEach((t) => t.stop());
    S.stream = null;
    S.faces = [];
    el.video.srcObject = null;
    el.video.classList.add("d-none");
    el.overlay.classList.add("d-none");
    el.toggle.textContent = "Mulai";
    el.toggle.classList.replace("btn-danger", "btn-success");
    if (S.mode === "usb") msg("Kamera USB berhenti. Klik Mulai untuk mengaktifkan.");
  }

  function grabFrame() {
    const v = el.video;
    if (!v.videoWidth) return null;
    const scale = Math.min(1, MAX_WIDTH / v.videoWidth);
    const c = grabFrame.canvas || (grabFrame.canvas = document.createElement("canvas"));
    c.width = Math.round(v.videoWidth * scale);
    c.height = Math.round(v.videoHeight * scale);
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    return new Promise((resolve) => c.toBlob(resolve, "image/jpeg", 0.82));
  }

  async function loop() {
    if (!S.active) return;
    const t0 = performance.now();
    const blob = await grabFrame();
    if (blob && S.active) {
      const form = new FormData();
      form.append("frame", blob, "frame.jpg");
      const res = await FaceCommon.api(`${S.cfg.stationFrameBase}${encodeURIComponent(S.station)}/frame/`, { method: "POST", body: form });
      S.latency = Math.round(performance.now() - t0);
      if (res.ok) {
        S.faces = res.data.faces;
        S.error = "";
        S.sent += 1;
        const secs = (performance.now() - S.fpsStarted) / 1000;
        if (secs >= 2) { S.fps = S.sent / secs; S.sent = 0; S.fpsStarted = performance.now(); }
      } else {
        S.faces = [];
        S.error = res.error.message;
        if (res.error.code === "UNAUTHENTICATED") { stop(); setStatus("Sesi login berakhir, silakan login ulang."); return; }
      }
      draw();
    }
    const interval = 1000 / Math.max(0.5, S.cfg.stationMaxFps || 4);
    const wait = res_wait(interval - (performance.now() - t0));
    S.timer = setTimeout(loop, wait);
  }

  // Saat error (mis. server sibuk), perlambat pengiriman ke 1 frame/detik.
  function res_wait(ms) { return Math.max(S.error ? 1000 : 0, ms); }

  function draw() {
    const v = el.video, c = el.overlay;
    const w = v.clientWidth, h = v.clientHeight;
    if (!w || !h) return;
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    ctx.font = "600 13px system-ui, sans-serif";
    S.faces.forEach((f) => {
      const [x, y, bw, bh] = f.bbox;
      const status = f.match_status || (f.quality === "POOR" ? "POOR" : "");
      const color = COLORS[status] || COLORS[""];
      ctx.strokeStyle = color;
      ctx.lineWidth = 3;
      ctx.shadowColor = color;
      ctx.shadowBlur = 12;
      ctx.strokeRect(x * w, y * h, bw * w, bh * h);
      ctx.shadowBlur = 0;
      // Nama (No. RM) hanya dikirim server untuk pengguna berizin melihat identitas kandidat.
      // NEEDS_REVIEW diberi "?" agar jelas masih perlu verifikasi petugas.
      const label = f.identity
        ? `${f.identity}${f.match_status === "NEEDS_REVIEW" ? " ?" : ""}`
        : `T${String(f.track_id).split("-").pop()} ${status || "TRACKING"}`;
      const tw = ctx.measureText(label).width + 10;
      ctx.fillStyle = color;
      ctx.fillRect(x * w, Math.max(0, y * h - 20), tw, 20);
      ctx.fillStyle = "#0b1026";
      ctx.fillText(label, x * w + 5, Math.max(14, y * h - 6));
      if (f.quality === "POOR" && f.reasons && f.reasons.length) {
        ctx.fillStyle = "rgba(11,16,38,.75)";
        const r = f.reasons[0];
        const rw = ctx.measureText(r).width + 10;
        ctx.fillRect(x * w, (y + bh) * h, rw, 18);
        ctx.fillStyle = "#fed7aa";
        ctx.fillText(r, x * w + 5, (y + bh) * h + 13);
      }
    });
    if (S.error) {
      ctx.fillStyle = "rgba(244,63,94,.85)";
      ctx.fillRect(0, h - 26, w, 26);
      ctx.fillStyle = "#fff";
      ctx.fillText(S.error, 8, h - 8);
    }
  }

  function setMode(mode) {
    S.mode = mode;
    store.set("camera", mode);
    el.controls.classList.toggle("d-none", mode !== "usb");
    if (mode === "usb") {
      if (!S.active) msg(window.isSecureContext ? "Pilih kamera USB lalu klik Mulai."
        : "Kamera USB butuh HTTPS. Hubungi admin (lihat docs/DEPLOY_PRODUCTION.md).");
    } else {
      stop();
    }
    if (S.onMode) S.onMode(mode);
  }

  window.FaceUSB = {
    init(cfg, onMode) {
      S.cfg = cfg;
      S.onMode = onMode;
      el = {
        controls: document.getElementById("usb-controls"), device: document.getElementById("usb-device"),
        station: document.getElementById("usb-station"), toggle: document.getElementById("usb-toggle"),
        video: document.getElementById("usb-video"), overlay: document.getElementById("usb-overlay"),
        msg: document.getElementById("video-msg"),
      };
      S.station = store.get("station", "");
      S.deviceId = store.get("device", "");
      el.station.value = S.station;
      el.toggle.addEventListener("click", () => (S.active ? stop() : start()));
      window.addEventListener("resize", draw);
      window.addEventListener("beforeunload", stop);
      if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
        navigator.mediaDevices.addEventListener("devicechange", listDevices);
      }
      listDevices();
      const initial = store.get("camera", cfg.defaultCamera || "server");
      document.querySelectorAll('input[name="cam-src"]').forEach((r) => {
        r.checked = r.value === initial;
        r.addEventListener("change", () => setMode(r.value));
      });
      setMode(initial);
      if (initial === "usb" && store.get("autostart", "1") === "1" && window.isSecureContext) start();
    },
    get mode() { return S.mode; },
    get active() { return S.active; },
    status() { return { active: S.active, fps: S.fps, latency: S.latency, faces: S.faces.length, error: S.error, station: S.station }; },
  };
})();
