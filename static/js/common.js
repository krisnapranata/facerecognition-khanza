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
    let resp;
    try {
      resp = await fetch(url, opts);
    } catch (e) {
      return { ok: false, error: { code: "NETWORK", message: "Server tidak dapat dihubungi" } };
    }
    try {
      return await resp.json();
    } catch (e) {
      return { ok: false, error: { code: "HTTP_" + resp.status, message: "Respons tidak valid (HTTP " + resp.status + ")" } };
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
