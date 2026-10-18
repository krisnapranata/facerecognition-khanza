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
    mode: "server", active: false, stream: null, timer: null, inflight: 0,
    station: "", deviceId: "", fps: 0, latency: 0, error: "", sent: 0, fpsStarted: 0,
    cfg: null, onMode: null,
    tracks: new Map(),  // track_id -> kotak yang dilacak lokal (lihat "Pelacakan lokal")
    raf: 0,
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
    Object.assign(S, { inflight: 0, lastSend: 0, lastApplied: 0, latEma: 0, slow: false, failures: 0, retryAt: 0 });
    S.fpsStarted = performance.now();
    el.toggle.textContent = "Berhenti";
    el.toggle.classList.replace("btn-success", "btn-danger");
    await listDevices();
    loop();
    S.raf = requestAnimationFrame(render);
  }

  function stop() {
    S.active = false;
    clearTimeout(S.timer);
    cancelAnimationFrame(S.raf);
    if (S.stream) S.stream.getTracks().forEach((t) => t.stop());
    S.stream = null;
    S.tracks.clear();
    el.video.srcObject = null;
    el.video.classList.add("d-none");
    el.overlay.classList.add("d-none");
    el.toggle.textContent = "Mulai";
    el.toggle.classList.replace("btn-danger", "btn-success");
    if (S.mode === "usb") msg("Kamera USB berhenti. Klik Mulai untuk mengaktifkan.");
  }

  // --- Pengiriman frame adaptif ----------------------------------------------------------------
  // LAN (latensi rendah): 640 px, JPEG 0,82, satu frame dalam perjalanan.
  // Koneksi jauh/lambat (mis. dari luar RS lewat internet): 480 px, JPEG 0,72 (±separuh data) dan
  // sampai dua frame dalam perjalanan sekaligus, agar throughput tidak dibatasi waktu bolak-balik.
  const SLOW_MS = 220, FAST_MS = 140;

  function isSlowLink() {
    if (S.slow && S.latEma < FAST_MS) S.slow = false;
    else if (!S.slow && S.latEma > SLOW_MS) S.slow = true;
    return S.slow;
  }

  function grabFrame() {
    const v = el.video;
    if (!v.videoWidth) return null;
    const slow = isSlowLink();
    const scale = Math.min(1, (slow ? 480 : MAX_WIDTH) / v.videoWidth);
    // Kanvas baru per frame: aman saat dua frame diproses bersamaan.
    const c = document.createElement("canvas");
    c.width = Math.round(v.videoWidth * scale);
    c.height = Math.round(v.videoHeight * scale);
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    return new Promise((resolve) => c.toBlob(resolve, "image/jpeg", slow ? 0.72 : 0.82));
  }

  async function sendFrame() {
    const t0 = performance.now();
    const blob = await grabFrame();
    if (!blob || !S.active) return;
    const form = new FormData();
    form.append("frame", blob, "frame.jpg");
    const res = await FaceCommon.api(`${S.cfg.stationFrameBase}${encodeURIComponent(S.station)}/frame/`,
      { method: "POST", body: form, timeout: 8000 });
    if (!S.active) return;
    const lat = performance.now() - t0;
    S.latency = Math.round(lat);
    S.latEma = S.latEma ? S.latEma * 0.8 + lat * 0.2 : lat;
    if (res.ok) {
      // Abaikan jawaban yang datang lebih lambat dari jawaban frame yang lebih baru.
      if (t0 >= S.lastApplied) {
        syncTracks(res.data.faces, t0);  // t0 = saat frame ini diambil (untuk kompensasi latensi)
        S.lastApplied = t0;
      }
      S.error = "";
      S.failures = 0;
      S.sent += 1;
      const secs = (performance.now() - S.fpsStarted) / 1000;
      if (secs >= 2) { S.fps = S.sent / secs; S.sent = 0; S.fpsStarted = performance.now(); }
    } else if (res.error.code === "UNAUTHENTICATED") {
      stop(); setStatus("Sesi login berakhir, silakan login ulang.");
    } else if (isTransient(res.error.code)) {
      // Gangguan sesaat (jaringan/timeout/server sibuk): coba ulang diam-diam, tampilkan pesan
      // hanya bila berulang, agar petugas tidak terganggu oleh satu frame yang gagal.
      S.failures = (S.failures || 0) + 1;
      S.totalFailures = (S.totalFailures || 0) + 1;
      S.lastFailure = res.error.code;
      if (S.failures >= 2) S.tracks.clear();
      S.error = S.failures >= 3 ? `Koneksi ke server terganggu (${res.error.message}) — mencoba ulang…` : "";
      S.retryAt = performance.now() + Math.min(5000, 500 * 2 ** (S.failures - 1));  // 0,5 → 1 → 2 → 4 → 5 s
    } else {
      S.tracks.clear();
      S.error = res.error.message;
      S.retryAt = performance.now() + 1000;
    }
  }

  // Penjadwal: kirim frame bila slot tersedia, jeda fps terpenuhi, dan tidak sedang mundur karena gagal.
  function loop() {
    if (!S.active) return;
    const now = performance.now();
    const interval = 1000 / Math.max(0.5, S.cfg.stationMaxFps || 4);
    const maxInflight = isSlowLink() && !S.failures ? 2 : 1;
    if (S.inflight < maxInflight && now - S.lastSend >= interval && now >= (S.retryAt || 0)) {
      S.inflight += 1;
      S.lastSend = now;
      sendFrame().finally(() => { S.inflight -= 1; });
    }
    S.timer = setTimeout(loop, 15);
  }

  function isTransient(code) {
    return code === "NETWORK" || code === "TIMEOUT" || code === "RATE_LIMITED" || /^HTTP_5\d\d$/.test(code);
  }

  // --- Pelacakan lokal di browser -------------------------------------------------------------
  // Server memperbarui kotak ±6x/detik dan tiap jawaban terlambat ±50-150 ms. Agar kotak mengikuti
  // gerakan wajah, browser menggeser kotak sendiri di setiap frame video (±30 fps) dengan template
  // matching pada citra abu-abu kecil (192 px), lalu mengoreksinya dengan jawaban server yang
  // dikompensasi gerakan selama jawaban itu "di perjalanan". Ringan, tanpa library, offline.
  const TRACK_W = 192;    // lebar citra pelacakan (px)
  const GRID = 16;        // titik sampel per sisi template (16x16)
  const RADIUS = 8;       // jangkauan pencarian per frame (px pada citra pelacakan)
  const MAX_SAD = 38;     // selisih rata-rata maksimum agar pergeseran dianggap valid
  const tc = document.createElement("canvas");
  const tctx = tc.getContext("2d", { willReadFrequently: true });
  const probe = new Float32Array(GRID * GRID);
  let gray = null, tmpGray = null, gw = 0, gh = 0;

  function grabGray() {
    const v = el.video;
    if (!v.videoWidth || v.paused) return false;
    gw = TRACK_W;
    gh = Math.max(1, Math.round(v.videoHeight * TRACK_W / v.videoWidth));
    if (tc.width !== gw || tc.height !== gh) { tc.width = gw; tc.height = gh; }
    tctx.drawImage(v, 0, 0, gw, gh);
    const d = tctx.getImageData(0, 0, gw, gh).data;
    if (!gray || gray.length !== gw * gh) { gray = new Float32Array(gw * gh); tmpGray = new Float32Array(gw * gh); }
    for (let i = 0, j = 0; i < gray.length; i++, j += 4) tmpGray[i] = d[j] * 0.299 + d[j + 1] * 0.587 + d[j + 2] * 0.114;
    // Blur 3x3 (box, terpisah horizontal lalu vertikal): pencocokan lebih stabil terhadap derau kamera.
    for (let y = 0; y < gh; y++) {
      const o = y * gw;
      for (let x = 0; x < gw; x++) {
        const a = tmpGray[o + Math.max(0, x - 1)], b = tmpGray[o + x], c = tmpGray[o + Math.min(gw - 1, x + 1)];
        gray[o + x] = (a + b + c) / 3;
      }
    }
    for (let x = 0; x < gw; x++) {
      for (let y = 0; y < gh; y++) {
        tmpGray[y * gw + x] = (gray[Math.max(0, y - 1) * gw + x] + gray[y * gw + x] + gray[Math.min(gh - 1, y + 1) * gw + x]) / 3;
      }
    }
    gray.set(tmpGray);
    return true;
  }

  // Ambil GRID x GRID titik dari bagian tengah kotak (70%), digeser (dx, dy) px. -1 = di luar citra.
  function sampleInto(box, dx, dy, out) {
    const bw = box[2] * gw, bh = box[3] * gh;
    const x0 = box[0] * gw + dx + bw * 0.15, y0 = box[1] * gh + dy + bh * 0.15;
    const sx = (bw * 0.7) / (GRID - 1), sy = (bh * 0.7) / (GRID - 1);
    let k = 0;
    for (let gy = 0; gy < GRID; gy++) {
      const yy = Math.round(y0 + gy * sy);
      for (let gx = 0; gx < GRID; gx++) {
        const xx = Math.round(x0 + gx * sx);
        out[k++] = (xx < 0 || yy < 0 || xx >= gw || yy >= gh) ? -1 : gray[yy * gw + xx];
      }
    }
    return out;
  }

  function sad(a, b) {
    let s = 0, n = 0;
    for (let i = 0; i < a.length; i++) {
      if (a[i] < 0 || b[i] < 0) continue;
      s += Math.abs(a[i] - b[i]);
      n++;
    }
    return n > a.length * 0.5 ? s / n : Infinity;
  }

  function stepTrack(t, now) {
    if (!t.tmpl) return;
    const r = Math.max(3, Math.min(RADIUS, Math.round(t.box[2] * gw * 0.35)));
    // Pusat pencarian = posisi perkiraan dari kecepatan frame sebelumnya (gerakan cepat tetap terkejar).
    const px = Math.round(t.vx || 0), py = Math.round(t.vy || 0);
    let best = Infinity, bx = 0, by = 0;
    for (let dy = py - r; dy <= py + r; dy++) {      // pencarian penuh per piksel (±74 rb operasi/wajah)
      for (let dx = px - r; dx <= px + r; dx++) {
        const d = sad(sampleInto(t.box, dx, dy, probe), t.tmpl);
        if (d < best) { best = d; bx = dx; by = dy; }
      }
    }
    if (best <= MAX_SAD) {
      t.vx = bx * 0.7 + (t.vx || 0) * 0.3;
      t.vy = by * 0.7 + (t.vy || 0) * 0.3;
      t.box[0] += bx / gw;
      t.box[1] += by / gh;
      sampleInto(t.box, 0, 0, probe);                // perbarui template perlahan (cahaya/pose berubah)
      for (let i = 0; i < probe.length; i++) {
        if (probe[i] >= 0 && t.tmpl[i] >= 0) t.tmpl[i] = t.tmpl[i] * 0.85 + probe[i] * 0.15;
      }
    } else {
      // Tidak yakin (wajah tertutup/berubah drastis): diam di tempat, redam kecepatan, tunggu koreksi server.
      t.vx = (t.vx || 0) * 0.5;
      t.vy = (t.vy || 0) * 0.5;
    }
    t.hist.push({ t: now, x: t.box[0], y: t.box[1] });
    while (t.hist.length && now - t.hist[0].t > 2000) t.hist.shift();
  }

  function posAt(t, time) {
    let p = null;
    for (const h of t.hist) { if (h.t <= time) p = h; else break; }
    return p || t.hist[0] || null;
  }

  // Terapkan jawaban server (frame diambil pada tCap): posisi dikompensasi gerakan lokal sejak tCap.
  function syncTracks(faces, tCap) {
    const seen = new Set();
    faces.forEach((f) => {
      seen.add(f.track_id);
      const target = f.bbox.slice();
      let t = S.tracks.get(f.track_id);
      if (t) {
        const p = posAt(t, tCap);
        if (p) { target[0] += t.box[0] - p.x; target[1] += t.box[1] - p.y; }
        const jump = Math.hypot(target[0] - t.box[0], target[1] - t.box[1]);
        const a = jump > 0.12 ? 1 : 0.5;              // lompatan besar: langsung; kecil: haluskan
        const ox = t.box[0], oy = t.box[1];
        for (let i = 0; i < 4; i++) t.box[i] += (target[i] - t.box[i]) * a;
        // Riwayat hanya mencatat gerakan pelacak, jadi geser juga sebesar koreksi ini.
        const cx = t.box[0] - ox, cy = t.box[1] - oy;
        t.hist.forEach((h) => { h.x += cx; h.y += cy; });
      } else {
        t = { box: target, hist: [] };
        S.tracks.set(f.track_id, t);
      }
      t.id = f.track_id;
      t.status = f.match_status;
      t.quality = f.quality;
      t.reasons = f.reasons;
      t.sharpness = f.sharpness;
      t.brightness = f.brightness;
      t.identity = f.identity;
      if (gray && gw) t.tmpl = sampleInto(t.box, 0, 0, t.tmpl || new Float32Array(GRID * GRID));
    });
    for (const id of [...S.tracks.keys()]) if (!seen.has(id)) S.tracks.delete(id);
  }

  function render(now) {
    if (!S.active) return;
    if (S.tracks.size && grabGray()) S.tracks.forEach((t) => stepTrack(t, now));
    draw();
    S.raf = requestAnimationFrame(render);
  }

  function draw() {
    const v = el.video, c = el.overlay;
    const w = v.clientWidth, h = v.clientHeight;
    if (!w || !h) return;
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    ctx.font = "600 13px system-ui, sans-serif";
    S.tracks.forEach((f) => {
      const [x, y, bw, bh] = f.box;
      const status = f.status || (f.quality === "POOR" ? "POOR" : "");
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
        ? `${f.identity}${f.status === "NEEDS_REVIEW" ? " ?" : ""}`
        : `T${String(f.id).split("-").pop()} ${status || "TRACKING"}`;
      const tw = ctx.measureText(label).width + 10;
      ctx.fillStyle = color;
      ctx.fillRect(x * w, Math.max(0, y * h - 20), tw, 20);
      ctx.fillStyle = "#0b1026";
      ctx.fillText(label, x * w + 5, Math.max(14, y * h - 6));
      if (f.quality === "POOR" && f.reasons && f.reasons.length) {
        ctx.fillStyle = "rgba(11,16,38,.75)";
        // Tampilkan angka agar ambang bisa dikalibrasi di lokasi (tajam = ketajaman, terang = kecerahan).
        const r = `${f.reasons[0]} · tajam ${f.sharpness ?? "?"} · terang ${f.brightness ?? "?"}`;
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
    status() {
      return { active: S.active, fps: S.fps, latency: S.latency, faces: S.tracks.size, error: S.error, station: S.station,
               totalFailures: S.totalFailures || 0, lastFailure: S.lastFailure || "" };
    },
  };
})();
