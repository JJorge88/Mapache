(() => {
  const root = document.querySelector("[data-gallery-download]");
  if (!root) return;
  const processing = root.querySelector("[data-download-processing]");
  const ready = root.querySelector("[data-download-ready]");
  const error = root.querySelector("[data-download-error]");
  const processed = root.querySelector("[data-download-processed]");
  const total = root.querySelector("[data-download-total]");
  const progress = root.querySelector("[data-download-progress]");
  const readyCount = root.querySelector("[data-ready-count]");
  const readySize = root.querySelector("[data-ready-size]");
  const expiry = root.querySelector("[data-download-expiry]");
  const terminal = new Set(["READY", "ERROR", "EXPIRED"]);

  const formatBytes = (bytes) => {
    if (!bytes) return "0 bytes";
    const units = ["bytes", "KB", "MB", "GB", "TB"];
    const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / 1024 ** unit).toFixed(unit ? 1 : 0)} ${units[unit]}`;
  };

  const render = (data) => {
    processed.textContent = data.processed;
    total.textContent = data.total;
    progress.max = Math.max(data.total, 1);
    progress.value = data.processed;
    processing.hidden = terminal.has(data.status);
    ready.hidden = data.status !== "READY";
    error.hidden = data.status !== "ERROR" && data.status !== "EXPIRED";
    if (data.status === "READY") {
      readyCount.textContent = data.total;
      readySize.textContent = formatBytes(data.file_size);
      if (data.expires_at) {
        expiry.textContent = `Disponible hasta ${new Date(data.expires_at).toLocaleString()}`;
      }
    }
  };

  const poll = async () => {
    try {
      const response = await fetch(root.dataset.statusUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("Estado no disponible");
      const data = await response.json();
      render(data);
      if (!terminal.has(data.status)) window.setTimeout(poll, 4000);
    } catch (_error) {
      window.setTimeout(poll, 5000);
    }
  };
  if (!processing.hidden) window.setTimeout(poll, 1000);
})();
