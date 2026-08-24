(function () {
  "use strict";
  const page = document.querySelector("[data-ai-status-url]");
  if (!page) return;

  async function poll() {
    try {
      const response = await fetch(page.dataset.aiStatusUrl, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!response.ok) return;
      const data = await response.json();
      ["face.total", "face.indexed", "face.detected", "face.pending", "bib.total", "bib.indexed", "bib.detected", "bib.pending"].forEach(
        (key) => {
          const target = page.querySelector(`[data-ai-stat="${key}"]`);
          const [module, field] = key.split(".");
          if (target) target.textContent = data[module][field];
        },
      );
      ["face", "bib"].forEach((module) => {
        const state = page.querySelector(`[data-ai-state="${module}"]`);
        if (state) {
          state.textContent = data[module].status_label;
          state.className = `ai-state state-${data[module].status.toLowerCase()}`;
        }
      });
      if ([data.face.status, data.bib.status].some((status) => !["READY", "ERROR", "DISABLED"].includes(status))) {
        window.setTimeout(poll, 4000);
      }
    } catch (_error) {
      window.setTimeout(poll, 8000);
    }
  }
  poll();
})();
