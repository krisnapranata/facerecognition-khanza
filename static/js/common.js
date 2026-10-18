// Helper bersama: CSRF, fetch JSON, escape HTML.
(function () {
  function getCookie(name) {
    const m = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[2]) : "";
  }

  async function api(url, options = {}) {
    const opts = Object.assign({ credentials: "same-origin", headers: {} }, options);
    if (opts.method && opts.method !== "GET") {
      opts.headers["X-CSRFToken"] = getCookie("facerec_csrftoken");
    }
    if (opts.json !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    // Batas waktu agar permintaan yang menggantung tidak menghentikan antrean (default 15 detik).
    const timeoutMs = opts.timeout || 15000;
    delete opts.timeout;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    opts.signal = ctrl.signal;
    let resp;
    try {
      resp = await fetch(url, opts);
    } catch (e) {
      clearTimeout(timer);
      if (e.name === "AbortError") {
        return { ok: false, error: { code: "TIMEOUT", message: `Server tidak merespons dalam ${Math.round(timeoutMs / 1000)} detik` } };
      }
      console.warn("Permintaan gagal:", url, e);
      return { ok: false, error: { code: "NETWORK", message: "Server tidak dapat dihubungi", detail: String(e) } };
    }
    try {
      return await resp.json();
    } catch (e) {
      return { ok: false, error: { code: "HTTP_" + resp.status, message: "Respons tidak valid (HTTP " + resp.status + ")" } };
    } finally {
      clearTimeout(timer);
    }
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function fmtTime(iso) {
    if (!iso) return "–";
    const d = new Date(iso);
    return d.toLocaleString("id-ID", { hour12: false });
  }

  window.FaceCommon = { api, esc, fmtTime, getCookie };
})();
