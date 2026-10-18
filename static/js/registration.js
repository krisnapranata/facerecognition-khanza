// Registrasi referensi wajah: pratinjau, ambil foto, cek kualitas, simpan, batal.
(function () {
  const { api, esc } = window.FaceCommon;
  const cfg = window.FACE_REG;
  const liveWorker = document.getElementById("live-worker");
  const liveBrowser = document.getElementById("live-browser");
  const captured = document.getElementById("captured");
  const camMsg = document.getElementById("cam-msg");
  const btnCapture = document.getElementById("btn-capture");
  const btnRetake = document.getElementById("btn-retake");
  const btnSave = document.getElementById("btn-save");
  const quality = document.getElementById("quality");
  const result = document.getElementById("reg-result");
  const deviceRow = document.getElementById("browser-device-row");
  const deviceSelect = document.getElementById("browser-device");
  const checkedSrc = document.querySelector('input[name="src"]:checked');
  let source = checkedSrc ? checkedSrc.value : "worker";
  let token = null;
  let mediaStream = null;

  function savedDevice() { try { return localStorage.getItem("facerec.device") || ""; } catch (e) { return ""; } }

  async function listDevices() {
    try {
      const cams = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
      const current = deviceSelect.value || savedDevice();
      deviceSelect.innerHTML = cams.map((d, i) =>
        `<option value="${d.deviceId}">${esc(d.label || "Kamera " + (i + 1))}</option>`).join("") || '<option value="">(kamera tidak ditemukan)</option>';
      if (cams.some((d) => d.deviceId === current)) deviceSelect.value = current;
    } catch (e) { /* daftar perangkat tidak tersedia */ }
  }
  deviceSelect.addEventListener("change", () => {
    try { localStorage.setItem("facerec.device", deviceSelect.value); } catch (e) { /* abaikan */ }
    if (source === "browser") { stopBrowserCamera(); showLive(); }
  });

  function message(text) {
    camMsg.textContent = text;
    camMsg.classList.toggle("d-none", !text);
  }

  function stopBrowserCamera() {
    if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
    mediaStream = null;
    liveBrowser.srcObject = null;
  }

  async function showLive() {
    captured.classList.add("d-none");
    message("");
    deviceRow.classList.toggle("d-none", source !== "browser");
    if (source === "worker") {
      stopBrowserCamera();
      liveBrowser.classList.add("d-none");
      liveWorker.classList.remove("d-none");
      liveWorker.src = cfg.streamUrl + "?t=" + Date.now();
    } else {
      liveWorker.removeAttribute("src");
      liveWorker.classList.add("d-none");
      liveBrowser.classList.remove("d-none");
      if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        message("Kamera USB butuh HTTPS (atau http://localhost) dan browser Chrome/Edge terbaru.");
        return;
      }
      // Resolusi lebih tinggi untuk foto referensi; server memeriksa kualitas & ukuran wajah.
      const video = { width: { ideal: 1280 }, height: { ideal: 720 } };
      const wanted = deviceSelect.value || savedDevice();
      if (wanted) video.deviceId = { exact: wanted };
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
        liveBrowser.srcObject = mediaStream;
        await listDevices();
      } catch (e) {
        const hint = e.name === "NotReadableError" ? "kamera sedang dipakai aplikasi/tab lain (mis. dashboard)."
          : e.name === "NotAllowedError" ? "izin kamera ditolak untuk situs ini." : e.name;
        message("Kamera USB tidak dapat dibuka: " + hint);
        await listDevices();
      }
    }
  }

  liveWorker.addEventListener("error", () => {
    if (source === "worker" && liveWorker.getAttribute("src")) {
      message("Stream AI worker tidak tersedia. Jalankan worker atau pilih 'Kamera USB di PC ini'.");
    }
  });

  document.querySelectorAll('input[name="src"]').forEach((r) =>
    r.addEventListener("change", () => { source = r.value; resetCapture(); showLive(); }));

  function renderQuality(data) {
    if (!data.capture_token && data.face_count !== 1) {
      quality.innerHTML = `<span class="text-danger">${esc(data.message)}</span>`;
      return;
    }
    const q = data.quality || {};
    const m = q.metrics || {};
    const badge = data.acceptable ? '<span class="badge text-bg-success">BAIK</span>' : '<span class="badge text-bg-danger">KURANG</span>';
    quality.innerHTML = `${badge} ${esc(data.message)}
      <ul class="mb-0 mt-1">
        <li>Ukuran wajah: ${m.face_size ?? "–"} px</li>
        <li>Ketajaman (varians Laplacian): ${m.blur_variance ?? "–"}</li>
        <li>Kecerahan: ${m.brightness ?? "–"}</li>
        <li>Kemiringan: ${m.roll_degrees ?? "–"}° · rasio yaw: ${m.yaw_ratio ?? "–"}</li>
        <li>Skor deteksi: ${m.detection_score ?? "–"}</li>
      </ul>`;
  }

  async function capture() {
    btnCapture.disabled = true;
    result.innerHTML = "";
    quality.textContent = "Memproses foto…";
    let res;
    if (source === "worker") {
      res = await api(cfg.captureUrl + "?source=worker", { method: "POST" });
    } else {
      if (!mediaStream) { quality.textContent = "Kamera browser belum aktif."; btnCapture.disabled = false; return; }
      const canvas = document.createElement("canvas");
      canvas.width = liveBrowser.videoWidth;
      canvas.height = liveBrowser.videoHeight;
      canvas.getContext("2d").drawImage(liveBrowser, 0, 0);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
      const form = new FormData();
      form.append("image", blob, "capture.jpg");
      form.append("source", "upload");
      res = await api(cfg.captureUrl + "?source=upload", { method: "POST", body: form });
    }
    btnCapture.disabled = false;
    if (!res.ok) {
      quality.innerHTML = `<span class="text-danger">${esc(res.error.message)}</span>`;
      return;
    }
    renderQuality(res.data);
    if (res.data.capture_token) {
      token = res.data.capture_token;
      captured.src = cfg.captureUrl + encodeURIComponent(token) + "/?t=" + Date.now();
      captured.classList.remove("d-none");
      liveWorker.classList.add("d-none");
      liveWorker.removeAttribute("src");
      liveBrowser.classList.add("d-none");
      btnRetake.disabled = false;
      btnCapture.disabled = true;
      btnSave.disabled = !res.data.acceptable;
    }
  }

  async function resetCapture() {
    if (token) await api(cfg.captureUrl + encodeURIComponent(token) + "/", { method: "DELETE" });
    token = null;
    btnSave.disabled = true;
    btnRetake.disabled = true;
    btnCapture.disabled = false;
    quality.textContent = "Belum ada foto.";
  }

  function clearErrors() {
    document.querySelectorAll(".is-invalid").forEach((el) => el.classList.remove("is-invalid"));
  }

  async function save(ev) {
    ev.preventDefault();
    clearErrors();
    btnSave.disabled = true;
    const payload = {
      patient_name: document.getElementById("patient_name").value,
      no_rkm_medis: document.getElementById("no_rkm_medis").value,
      processing_basis: document.getElementById("processing_basis").value,
      consent_confirmed: document.getElementById("consent_confirmed").checked,
      capture_token: token,
    };
    const res = await api(cfg.referencesUrl, { method: "POST", json: payload });
    if (!res.ok) {
      btnSave.disabled = false;
      const fields = res.error.fields || {};
      Object.entries(fields).forEach(([name, msg]) => {
        const input = document.getElementById(name);
        const fb = document.querySelector(`[data-field="${name}"]`);
        if (input) input.classList.add("is-invalid");
        if (fb) fb.textContent = msg;
      });
      result.innerHTML = `<div class="alert alert-danger small">${esc(res.error.message)}</div>`;
      if (["CAPTURE_NOT_FOUND", "CAPTURE_EXPIRED"].includes(res.error.code)) { token = null; btnRetake.disabled = false; }
      return;
    }
    token = null; // file sementara sudah dibersihkan server
    result.innerHTML = `<div class="alert alert-success small">Referensi #${res.data.id} tersimpan
      (${esc(res.data.patient_name)}, ${esc(res.data.no_rkm_medis)}). Embedding disimpan terenkripsi.
      <a href="${cfg.listUrl}">Lihat daftar referensi</a></div>`;
    document.getElementById("reg-form").reset();
    btnRetake.disabled = true;
    btnCapture.disabled = false;
    quality.textContent = "Belum ada foto.";
    showLive();
  }

  // Mode Khanza: nama diambil dari data pasien Khanza berdasarkan No. RM (server juga memvalidasi ulang).
  const btnLookup = document.getElementById("btn-lookup");
  if (btnLookup) {
    const rmInput = document.getElementById("no_rkm_medis");
    const nameInput = document.getElementById("patient_name");
    const info = document.getElementById("patient_info");
    rmInput.addEventListener("input", () => { nameInput.value = ""; info.textContent = ""; });
    btnLookup.addEventListener("click", async () => {
      const rm = rmInput.value.trim();
      nameInput.value = "";
      if (rm.length < 2) { info.textContent = "Isi No. RM minimal 2 karakter."; return; }
      info.textContent = "Mencari…";
      const res = await api(cfg.searchUrl + "?q=" + encodeURIComponent(rm));
      if (!res.ok) { info.innerHTML = `<span class="text-danger">${esc(res.error.message)}</span>`; return; }
      const p = res.data.results.find((r) => r.no_rkm_medis.toUpperCase() === rm.toUpperCase());
      if (!p) { info.innerHTML = '<span class="text-danger">No. RM tidak ditemukan di Khanza.</span>'; return; }
      rmInput.value = p.no_rkm_medis;
      nameInput.value = p.nama;
      info.textContent = `JK: ${p.jk || "-"} · Tgl lahir: ${p.tgl_lahir || "-"} — cocokkan dengan dokumen pasien.`;
    });
  }

  btnCapture.addEventListener("click", capture);
  btnRetake.addEventListener("click", async () => { await resetCapture(); showLive(); });
  document.getElementById("btn-cancel").addEventListener("click", async () => {
    await resetCapture();
    document.getElementById("reg-form").reset();
    clearErrors();
    result.innerHTML = "";
    showLive();
  });
  document.getElementById("reg-form").addEventListener("submit", save);
  window.addEventListener("beforeunload", () => {
    stopBrowserCamera();
    if (token) navigator.sendBeacon && fetch(cfg.captureUrl + encodeURIComponent(token) + "/", {
      method: "DELETE", credentials: "same-origin", keepalive: true,
      headers: { "X-CSRFToken": FaceCommon.getCookie("facerec_csrftoken") },
    });
  });
  showLive();
})();
