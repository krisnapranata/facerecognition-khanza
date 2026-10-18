// Dashboard: status worker/kamera/model (polling), stream MJPEG, kandidat terbaru, verifikasi.
(function () {
  const { api, esc, fmtTime } = window.FaceCommon;
  const cfg = window.FACE;
  const video = document.getElementById("video");
  const videoMsg = document.getElementById("video-msg");
  let streamActive = false;
  let currentEvent = null;
  let manualRm = null;

  const STATUS_CLASS = {
    CANDIDATE: "success", NEEDS_REVIEW: "warning", NO_MATCH: "secondary", ERROR: "danger",
  };
  const CONTROL_CLASS = {
    CONTROL_FOUND: "success", CONTROL_NOT_FOUND: "secondary", NEEDS_MANUAL_CHECK: "warning",
    IDENTITY_NOT_VERIFIED: "light", NOT_CHECKED: "light",
  };

  function pill(id, text, kind) {
    const el = document.getElementById(id);
    el.textContent = text;
    el.className = "status-pill status-" + kind;
  }

  function startStream() {
    if (streamActive) return;
    streamActive = true;
    video.src = cfg.streamUrl + "?t=" + Date.now();
    video.classList.remove("d-none");
    videoMsg.classList.add("d-none");
  }

  function stopStream(message) {
    if (streamActive) video.removeAttribute("src");
    streamActive = false;
    video.classList.add("d-none");
    if (window.FaceUSB && FaceUSB.mode === "usb") return;  // pesan dikelola modul kamera USB
    videoMsg.textContent = message;
    videoMsg.classList.remove("d-none");
  }

  video.addEventListener("error", () => stopStream("Stream video terputus. Mencoba lagi…"));

  async function refreshHealth() {
    const res = await api(cfg.healthUrl);
    if (!res.ok) {
      pill("st-worker", "Server: error", "danger");
      return;
    }
    const d = res.data;
    const kz0 = d.khanza || {};
    pill("st-khanza", "Khanza: " + (kz0.enabled ? kz0.message : "simulasi"), kz0.status === "READY" ? "ok" : kz0.enabled ? "danger" : "muted");
    if (window.FaceUSB && FaceUSB.mode === "usb") return usbPills(d);
    const w = d.worker;
    if (w.alive) {
      pill("st-worker", "Worker: berjalan", "ok");
    } else {
      pill("st-worker", "Worker: " + (w.message || "tidak berjalan"), "danger");
    }
    const cam = d.camera || {};
    if (w.alive && cam.connected) {
      pill("st-camera", `Kamera: aktif (${cam.source_type}, ${cam.frame_width}x${cam.frame_height}, ${cam.fps} fps)`, "ok");
    } else if (w.alive) {
      pill("st-camera", "Kamera: terputus – " + (cam.last_error || cam.state), "danger");
    } else {
      pill("st-camera", "Kamera: tidak diketahui (worker mati)", "muted");
    }
    const wm = (d.model && d.model.worker) || {};
    if (w.alive && wm.detector === "READY" && wm.embedder === "READY") {
      pill("st-model", "Model: siap (" + (wm.embedding_backend || "") + ")", "ok");
    } else if (w.alive) {
      pill("st-model", "Model tidak tersedia – pencocokan nonaktif", "danger");
    } else {
      pill("st-model", d.model.available ? "Model: file tersedia" : "Model: file tidak ditemukan", d.model.available ? "muted" : "danger");
    }
    pill("st-faces", "Wajah terdeteksi: " + (w.alive ? w.faces : "–"), w.alive && w.faces ? "info" : "muted");
    pill("st-fps", "Inferensi: " + (w.alive ? w.inference_fps : "–") + " fps", "muted");

    if (w.alive && cam.connected) startStream();
    else stopStream(!w.alive ? "AI worker tidak berjalan. Jalankan scripts/run_demo.sh." : "Kamera terputus. Worker mencoba koneksi ulang…");
  }

  // Mode kamera USB di PC ini: status kamera dari browser, status AI dari server (stasiun).
  function usbPills(d) {
    const s = FaceUSB.status();
    const st = (d.stations || []).find((x) => x.station_id === s.station);
    pill("st-worker", st && st.online ? `Server AI: memproses ${st.processing_ms} ms/frame` : "Server AI: menunggu frame", st && st.online ? "ok" : "muted");
    if (s.active && !s.error) pill("st-camera", `Kamera USB: aktif · stasiun ${s.station}`, "ok");
    else if (s.error) pill("st-camera", "Kamera USB: " + s.error, "danger");
    else pill("st-camera", "Kamera USB: belum dimulai", "muted");
    pill("st-model", d.model.available ? "Model: tersedia di server" : "Model: file tidak ditemukan", d.model.available ? "ok" : "danger");
    pill("st-faces", "Wajah terdeteksi: " + (s.active ? s.faces : "–"), s.active && s.faces ? "info" : "muted");
    const fails = s.totalFailures ? ` · gagal ${s.totalFailures} (${s.lastFailure})` : "";
    pill("st-fps", s.active ? `Kirim ${s.fps.toFixed(1)} fps · ${s.latency} ms${fails}` : "Kirim: – fps",
      s.totalFailures ? "danger" : "muted");
  }

  if (window.FaceUSB) {
    FaceUSB.init(cfg, (mode) => {
      if (mode === "usb") {
        if (streamActive) video.removeAttribute("src");
        streamActive = false;
        video.classList.add("d-none");
      }
      refreshHealth();
    });
  }

  function eventCard(e) {
    const cls = STATUS_CLASS[e.match_status] || "secondary";
    let identity = '<span class="text-secondary">Identitas tidak ditampilkan</span>';
    if (e.candidate) {
      const warn = e.match_status === "NEEDS_REVIEW"
        ? '<div class="text-warning-emphasis small">Ambigu / skor sedang – wajib verifikasi manual</div>' : "";
      identity = `<div class="fw-semibold">${esc(e.candidate.patient_name)}</div>
        <div class="small">No. RM: ${esc(e.candidate.no_rkm_medis)} ${e.candidate.is_test_data ? '<span class="badge text-bg-light">uji</span>' : ""}</div>${warn}`;
    } else if (e.match_status === "NO_MATCH") {
      identity = '<span class="text-secondary">Tidak ada kandidat. Gunakan pencarian manual.</span>';
    }
    if (e.verified_identity) {
      identity = `<div class="fw-semibold">${esc(e.verified_identity.patient_name)}</div>
        <div class="small">No. RM: ${esc(e.verified_identity.no_rkm_medis)} (terverifikasi)</div>`;
    }
    const verifyBtn = cfg.canVerify && e.verification_status === "PENDING"
      ? `<button class="btn btn-sm btn-outline-primary" data-verify="${e.id}">Verifikasi</button>` : "";
    const deactBtn = cfg.canManage && e.candidate && e.candidate.reference_active
      ? `<button class="btn btn-sm btn-outline-danger" data-deactivate="${e.candidate.reference_id}">Nonaktifkan Referensi</button>` : "";
    const verified = ["CONFIRMED", "MANUAL"].includes(e.verification_status);
    const regBtn = cfg.canRegister && verified && !e.khanza_visit
      ? `<button class="btn btn-sm btn-success" data-register="${e.id}">Daftarkan ke Poli</button>` : "";
    const visit = e.khanza_visit
      ? `<div class="small text-success-emphasis">Terdaftar di Khanza: No. Rawat <strong>${esc(e.khanza_visit.no_rawat)}</strong>,
         No. Reg <strong>${esc(e.khanza_visit.no_reg)}</strong> oleh ${esc(e.khanza_visit.registered_by)}</div>` : "";
    const ctl = CONTROL_CLASS[e.control_status] || "light";
    return `<div class="card mb-2 event-card border-${cls}">
      <div class="card-body p-2">
        <div class="d-flex justify-content-between">
          <span class="badge text-bg-${cls}">${esc(e.match_status_label)}</span>
          <small class="text-secondary">${fmtTime(e.updated_at)}</small>
        </div>
        <div class="my-1">${identity}</div>
        <div class="small text-secondary">Kamera ${esc(e.camera_id)} · track ${esc(e.track_id)} ·
          skor ${e.similarity ?? "–"}${e.second_similarity != null ? " / kand.2 " + e.second_similarity : ""}</div>
        <div class="small">Verifikasi: <strong>${esc(e.verification_status_label)}</strong>${e.verified_by ? " oleh " + esc(e.verified_by) : ""}</div>
        <div class="small">Kontrol: <span class="badge text-bg-${ctl}">${esc(e.control_status_label)}</span> ${esc(e.control_detail)}</div>
        ${visit}
        <div class="d-flex flex-wrap gap-1 mt-1">${verifyBtn}${regBtn}${deactBtn}
          <a class="btn btn-sm btn-link" href="${cfg.eventDetailBase}${e.id}/">Detail</a></div>
      </div></div>`;
  }

  let eventsCache = {};
  async function refreshEvents() {
    const res = await api(cfg.eventsUrl + "?limit=15");
    const box = document.getElementById("events");
    if (!res.ok) {
      box.innerHTML = `<div class="text-danger small p-2">${esc(res.error.message)}</div>`;
      return;
    }
    eventsCache = {};
    res.data.events.forEach((e) => (eventsCache[e.id] = e));
    box.innerHTML = res.data.events.length
      ? res.data.events.map(eventCard).join("")
      : '<div class="text-secondary small p-2">Belum ada kejadian identifikasi.</div>';
  }

  // --- verifikasi ---
  const verifyModalEl = document.getElementById("verifyModal");
  const verifyModal = new bootstrap.Modal(verifyModalEl);

  function openVerify(id) {
    currentEvent = eventsCache[id];
    manualRm = null;
    if (!currentEvent) return;
    document.getElementById("v-id").textContent = id;
    document.getElementById("v-note").value = "";
    document.getElementById("v-error").textContent = "";
    document.getElementById("v-manual-results").innerHTML = "";
    const c = currentEvent.candidate;
    document.getElementById("v-summary").innerHTML = c
      ? `<div>Kandidat sistem: <strong>${esc(c.patient_name)}</strong> (No. RM ${esc(c.no_rkm_medis)})</div>
         <div class="small text-secondary">Status: ${esc(currentEvent.match_status_label)} · skor ${currentEvent.similarity}</div>`
      : '<div class="text-secondary">Tidak ada kandidat. Gunakan identifikasi manual.</div>';
    document.getElementById("v-confirm").disabled = !c;
    verifyModal.show();
  }

  async function submitVerify(decision) {
    const body = { decision, note: document.getElementById("v-note").value };
    if (decision === "MANUAL") body.no_rkm_medis = manualRm;
    const res = await api(`/api/face/v1/events/${currentEvent.id}/verify/`, { method: "POST", json: body });
    if (!res.ok) {
      document.getElementById("v-error").textContent = res.error.message;
      return;
    }
    verifyModal.hide();
    refreshEvents();
  }

  async function search(q, target, onPick) {
    const res = await api(cfg.searchUrl + "?q=" + encodeURIComponent(q));
    if (!res.ok) {
      target.innerHTML = `<div class="text-danger">${esc(res.error.message)}</div>`;
      return;
    }
    if (!res.data.results.length) {
      target.innerHTML = '<div class="text-secondary">Tidak ditemukan.</div>';
      return;
    }
    target.innerHTML = res.data.results.map((p) =>
      `<button type="button" class="list-group-item list-group-item-action" data-rm="${esc(p.no_rkm_medis)}">
        ${esc(p.nama)} · ${esc(p.no_rkm_medis)}${p.jk ? " · " + esc(p.jk) : ""}${p.tgl_lahir ? " · lahir " + esc(p.tgl_lahir) : ""}
        <span class="badge text-bg-light">${esc(p.source)}</span></button>`).join("");
    if (onPick) target.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => onPick(b.dataset.rm)));
  }

  document.getElementById("v-confirm").addEventListener("click", () => submitVerify("CONFIRMED"));
  document.getElementById("v-reject").addEventListener("click", () => submitVerify("REJECTED"));
  document.getElementById("v-manual-search").addEventListener("click", () =>
    search(document.getElementById("v-manual-q").value, document.getElementById("v-manual-results"), (rm) => {
      manualRm = rm;
      if (confirm("Tetapkan identitas manual: No. RM " + rm + "?")) submitVerify("MANUAL");
    }));

  const sGo = document.getElementById("s-go");
  if (sGo) {
    sGo.addEventListener("click", () => search(document.getElementById("s-q").value, document.getElementById("s-results")));
    document.getElementById("s-q").addEventListener("keydown", (ev) => { if (ev.key === "Enter") sGo.click(); });
  }

  document.getElementById("events").addEventListener("click", async (ev) => {
    const v = ev.target.closest("[data-verify]");
    if (v) return openVerify(Number(v.dataset.verify));
    const r = ev.target.closest("[data-register]");
    if (r) return openRegister(Number(r.dataset.register));
    const d = ev.target.closest("[data-deactivate]");
    if (d && confirm("Nonaktifkan referensi wajah #" + d.dataset.deactivate + "? Referensi tidak akan dipakai untuk pencocokan.")) {
      const res = await api(`/api/face/v1/references/${d.dataset.deactivate}/?mode=deactivate`, { method: "DELETE" });
      if (!res.ok) alert(res.error.message);
      refreshEvents();
    }
  });

  // --- registrasi kunjungan ke Khanza (reg_periksa) ---
  const regModalEl = document.getElementById("regModal");
  const regModal = new bootstrap.Modal(regModalEl);
  const reg = { eventId: null, options: null, kdPoli: null, kdDokter: null };
  const rErr = document.getElementById("r-error");
  const rPreview = document.getElementById("r-preview");
  const rSubmit = document.getElementById("r-submit");
  const regBase = (id) => `/api/khanza/v1/events/${id}/registration/`;

  const DAYS = [["SENIN", "Sen"], ["SELASA", "Sel"], ["RABU", "Rab"], ["KAMIS", "Kam"], ["JUMAT", "Jum"], ["SABTU", "Sab"], ["AKHAD", "Min"]];
  const DAY_NAMES = { SENIN: "Senin", SELASA: "Selasa", RABU: "Rabu", KAMIS: "Kamis", JUMAT: "Jumat", SABTU: "Sabtu", AKHAD: "Minggu" };

  function showError(msg) {
    rErr.textContent = msg || "";
    rErr.classList.toggle("d-none", !msg);
  }

  function initials(name) {
    return String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  }

  function currentPoli() {
    return reg.options ? reg.options.polies.find((p) => p.kd_poli === reg.kdPoli) : null;
  }

  function renderPolies() {
    const o = reg.options;
    document.getElementById("r-polies").innerHTML = o.polies.length ? o.polies.map((p) => {
      const badge = p.praktik_hari_ini
        ? `<span class="badge rounded-pill text-bg-success">${p.praktik_hari_ini} dokter praktik hari ini</span>`
        : '<span class="badge rounded-pill text-bg-secondary">Tidak ada praktik hari ini</span>';
      return `<button type="button" class="poli-card ${p.kd_poli === reg.kdPoli ? "active" : ""}" data-poli="${esc(p.kd_poli)}"
          ${p.selectable ? "" : "disabled"}>
        <div class="fw-semibold">${esc(p.nm_poli)}</div>
        <div class="d-flex justify-content-between align-items-center mt-1">
          ${badge}<span class="small text-secondary">${p.doctors.length} dokter</span></div></button>`;
    }).join("") : '<div class="reg-empty">Tidak ada poliklinik dengan jadwal dokter.</div>';
  }

  function dayChips(jadwal) {
    const on = new Set(jadwal.map((j) => j.hari));
    return `<div class="day-chips">${DAYS.map(([k, s]) => {
      const title = jadwal.filter((j) => j.hari === k).map((j) => j.jam).join(", ");
      return `<span class="day-chip ${on.has(k) ? "on" : ""} ${k === reg.options.hari ? "today" : ""}" title="${esc(title)}">${s}</span>`;
    }).join("")}</div>`;
  }

  function renderDoctors() {
    const box = document.getElementById("r-doctors");
    const poli = currentPoli();
    document.getElementById("r-poli-name").textContent = poli ? `· ${poli.nm_poli}` : "";
    if (!poli) {
      box.innerHTML = '<div class="col-12"><div class="reg-empty">Pilih poliklinik terlebih dahulu.</div></div>';
      return;
    }
    box.innerHTML = poli.doctors.map((d) => {
      const hours = d.hari_ini.length
        ? `<div class="doc-hours">${esc(d.hari_ini.join(", "))}</div>`
        : `<div class="doc-hours off">Tidak praktik hari ini</div>`;
      let quota = "";
      if (d.kuota) {
        const pct = Math.min(100, Math.round((d.terisi / d.kuota) * 100));
        quota = `<div class="small text-secondary mt-1">Kuota ${d.terisi}/${d.kuota}</div>
          <div class="progress" style="height:4px"><div class="progress-bar ${pct >= 100 ? "bg-danger" : "bg-success"}" style="width:${pct}%"></div></div>`;
      } else if (d.hari_ini.length) {
        quota = `<div class="small text-secondary mt-1">${d.terisi} pasien terdaftar hari ini</div>`;
      }
      const reason = d.reason ? `<div class="small ${d.selectable ? "text-warning-emphasis" : "text-danger"} mt-1">${esc(d.reason)}</div>` : "";
      return `<div class="col-md-6"><button type="button" class="doc-card ${d.kd_dokter === reg.kdDokter ? "active" : ""}"
          data-dokter="${esc(d.kd_dokter)}" ${d.selectable ? "" : "disabled"}>
        <div class="fw-semibold">${esc(d.nm_dokter)}</div>${hours}${dayChips(d.jadwal)}${quota}${reason}</button></div>`;
    }).join("");
  }

  function renderRegChoices() {
    renderPolies();
    renderDoctors();
  }

  function ticket(p, done) {
    return `<div class="reg-ticket"><div class="row g-0">
      <div class="col-md-3 reg-ticket-no d-flex flex-column justify-content-center ${done ? "" : "bg-primary"}">
        <div class="small text-uppercase opacity-75">${done ? "No. Reg" : "Perkiraan No. Reg"}</div>
        <div class="num">${esc(p.no_reg)}</div>
        <div class="small mt-1">${esc(p.no_rawat)}</div>
      </div>
      <div class="col-md-9 p-3"><div class="row">
        <dl class="col-sm-6"><dt>Poliklinik</dt><dd>${esc(p.nm_poli)}</dd><dt>Dokter</dt><dd>${esc(p.nm_dokter)}</dd>
          <dt>Tanggal / jam</dt><dd>${esc(p.tgl_registrasi)} · ${esc(p.jam_reg)}</dd></dl>
        <dl class="col-sm-6"><dt>Cara bayar</dt><dd>${esc(p.png_jawab)}</dd>
          <dt>Biaya registrasi</dt><dd>Rp ${Number(p.biaya_reg).toLocaleString("id-ID")}</dd>
          <dt>Status</dt><dd>${esc(p.stts_daftar)} · poli ${esc(p.status_poli)} · ${esc(p.umurdaftar)} ${esc(p.sttsumur)}</dd></dl>
      </div>
      ${done ? '<div class="alert alert-success py-2 mb-0 small">Berhasil didaftarkan ke SIMRS Khanza.</div>'
             : '<div class="small text-secondary">No. Reg/No. Rawat final ditentukan saat disimpan.</div>'}
      </div></div></div>`;
  }

  async function updatePreview() {
    rSubmit.disabled = true;
    showError("");
    if (!reg.kdPoli || !reg.kdDokter) {
      rPreview.innerHTML = '<div class="reg-empty">Pilih poliklinik dan dokter untuk melihat ringkasan pendaftaran.</div>';
      return;
    }
    rPreview.innerHTML = '<div class="reg-empty">Menghitung nomor registrasi…</div>';
    const res = await api(regBase(reg.eventId) + "preview/", { method: "POST", json: { kd_poli: reg.kdPoli, kd_dokter: reg.kdDokter } });
    if (!res.ok) {
      rPreview.innerHTML = '<div class="reg-empty">Ringkasan tidak tersedia.</div>';
      showError(res.error.message);
      return;
    }
    rPreview.innerHTML = ticket(res.data.plan, false);
    rSubmit.disabled = false;
  }

  async function openRegister(id) {
    Object.assign(reg, { eventId: id, options: null, kdPoli: null, kdDokter: null, suggestions: [] });
    showError("");
    rSubmit.disabled = true;
    rSubmit.classList.remove("d-none");
    rPreview.innerHTML = '<div class="reg-empty">Pilih poliklinik dan dokter untuk melihat ringkasan pendaftaran.</div>';
    document.getElementById("r-polies").innerHTML = '<div class="reg-empty">Memuat jadwal…</div>';
    document.getElementById("r-doctors").innerHTML = "";
    document.getElementById("r-suggest").innerHTML = "";
    regModal.show();
    const res = await api(regBase(id) + "options/");
    if (!res.ok) {
      document.getElementById("r-polies").innerHTML = "";
      showError(res.error.message);
      return;
    }
    const o = res.data;
    reg.options = o;
    document.getElementById("r-avatar").textContent = initials(o.patient.nama);
    document.getElementById("r-patient-name").textContent = o.patient.nama;
    document.getElementById("r-patient-rm").textContent = `No. RM ${o.patient.no_rkm_medis}`;
    document.getElementById("r-date").textContent = `${DAY_NAMES[o.hari] || o.hari}, ${o.tanggal}`;
    document.getElementById("r-mode").textContent = o.mode_jadwal === "hari_ini" ? "Sesuai jadwal hari ini" : "Semua dokter berjadwal";
    const sug = o.suggestions.filter((s) => s.kd_poli || s.kd_dokter);
    reg.suggestions = sug;
    document.getElementById("r-suggest").innerHTML = sug.length
      ? `<div class="alert alert-info mb-0"><div class="fw-semibold mb-1">Ditemukan untuk hari ini</div>${sug.map((s, i) =>
          `<div class="d-flex justify-content-between align-items-center gap-2 mt-1"><span class="small">${esc(s.keterangan)}</span>
           <button class="btn btn-sm btn-primary" data-suggest="${i}">Pakai</button></div>`).join("")}</div>`
      : `<div class="alert alert-light border mb-0 small">Belum ada surat kontrol elektronik / booking untuk hari ini.
         Pilih poliklinik dan dokter sesuai jadwal praktik.</div>`;
    const firstOpen = o.polies.find((p) => p.selectable);
    if (firstOpen && o.polies.filter((p) => p.selectable).length === 1) reg.kdPoli = firstOpen.kd_poli;
    renderRegChoices();
  }

  regModalEl.addEventListener("click", (ev) => {
    const s = ev.target.closest("[data-suggest]");
    if (s) {
      const sg = reg.suggestions[Number(s.dataset.suggest)];
      reg.kdPoli = sg.kd_poli || reg.kdPoli;
      reg.kdDokter = sg.kd_dokter || null;
      renderRegChoices();
      return updatePreview();
    }
    const p = ev.target.closest("[data-poli]");
    if (p && !p.disabled) {
      reg.kdPoli = p.dataset.poli;
      reg.kdDokter = null;
      const poli = currentPoli();
      const open = poli.doctors.filter((d) => d.selectable);
      if (open.length === 1) reg.kdDokter = open[0].kd_dokter;  // satu-satunya dokter: langsung terpilih
      renderRegChoices();
      return updatePreview();
    }
    const d = ev.target.closest("[data-dokter]");
    if (d && !d.disabled) { reg.kdDokter = d.dataset.dokter; renderDoctors(); return updatePreview(); }
  });

  rSubmit.addEventListener("click", async () => {
    rSubmit.disabled = true;
    showError("");
    const res = await api(regBase(reg.eventId), { method: "POST", json: { kd_poli: reg.kdPoli, kd_dokter: reg.kdDokter } });
    if (!res.ok) { showError(res.error.message); rSubmit.disabled = false; return; }
    rPreview.innerHTML = ticket(res.data.plan, true);
    rSubmit.classList.add("d-none");
    document.querySelectorAll("#regModal [data-poli], #regModal [data-dokter], #regModal [data-suggest]")
      .forEach((b) => { b.disabled = true; });
    refreshEvents();
  });

  refreshHealth();
  refreshEvents();
  setInterval(refreshHealth, 2000);
  setInterval(() => {
    if (!verifyModalEl.classList.contains("show") && !regModalEl.classList.contains("show")) refreshEvents();
  }, 2500);
})();
