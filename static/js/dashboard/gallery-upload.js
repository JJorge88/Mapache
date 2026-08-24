(function () {
  "use strict";
  const form = document.querySelector("[data-upload-form]");
  if (!form) return;
  const input = form.querySelector('input[type="file"]');
  const zone = form.querySelector("[data-drop-zone]");
  const selectedPanel = form.querySelector("[data-selected-files]");
  const fileCount = form.querySelector("[data-file-count]");
  const fileList = form.querySelector("[data-file-list]");
  const directList = form.querySelector("[data-direct-file-list]");
  const progress = form.querySelector("[data-upload-progress]");
  const progressBar = form.querySelector("[data-upload-meter]");
  const progressText = form.querySelector("[data-upload-label]");
  const progressPercent = form.querySelector("[data-upload-percent]");
  const submit = form.querySelector("[data-upload-submit]");
  const csrf = form.querySelector('[name="csrfmiddlewaretoken"]').value;
  const direct = form.dataset.directUpload === "true";
  const storageKey = `mapache-upload-${form.dataset.galleryUuid}`;
  const activeRequests = new Map();
  const byteProgress = new Map();
  const itemSizes = new Map();
  let selectedFiles = [];
  let browserTransferActive = false;

  function revealForm() {
    form.hidden = false;
    form.scrollIntoView({ behavior: "smooth", block: "center" });
    zone?.focus();
  }

  function selectFiles(files) {
    selectedFiles = Array.from(files || []);
    selectedPanel.hidden = !selectedFiles.length;
    submit.disabled = !selectedFiles.length;
    fileList.replaceChildren();
    if (!selectedFiles.length) return;
    fileCount.textContent = `${selectedFiles.length} archivo${selectedFiles.length === 1 ? "" : "s"} seleccionado${selectedFiles.length === 1 ? "" : "s"}`;
    selectedFiles.slice(0, 8).forEach((file) => {
      const row = document.createElement("li"); row.textContent = file.name; fileList.appendChild(row);
    });
    if (selectedFiles.length > 8) {
      const row = document.createElement("li"); row.textContent = `y ${selectedFiles.length - 8} más…`; fileList.appendChild(row);
    }
  }

  document.querySelectorAll("[data-upload-toggle]").forEach((button) => button.addEventListener("click", revealForm));
  input.addEventListener("change", () => selectFiles(input.files));
  ["dragenter", "dragover"].forEach((name) => zone?.addEventListener(name, (event) => {
    event.preventDefault(); zone.classList.add("is-dragging");
  }));
  ["dragleave", "drop"].forEach((name) => zone?.addEventListener(name, (event) => {
    event.preventDefault(); zone.classList.remove("is-dragging");
  }));
  zone?.addEventListener("drop", (event) => selectFiles(event.dataTransfer.files));
  zone?.addEventListener("keydown", (event) => {
    if (["Enter", " "].includes(event.key)) { event.preventDefault(); input.click(); }
  });

  function setOverallProgress(loaded, total) {
    const percent = Math.min(100, Math.round((loaded / Math.max(total, 1)) * 100));
    if (progressBar) progressBar.value = percent;
    if (progressPercent) progressPercent.textContent = `${percent}%`;
  }

  function sendTraditionalBatch(files, batchId, completedBytes, totalBytes) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      const payload = new FormData();
      files.forEach((file) => payload.append("photos", file));
      payload.append("batch_id", batchId); payload.append("csrfmiddlewaretoken", csrf);
      request.open("POST", form.action);
      request.setRequestHeader("Accept", "application/json");
      request.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) setOverallProgress(completedBytes + event.loaded, totalBytes);
      });
      request.addEventListener("load", () => {
        let result;
        try { result = JSON.parse(request.responseText); } catch (_error) { reject(new Error("Respuesta inesperada del servidor")); return; }
        if (request.status < 200 || request.status >= 300) { reject(new Error(result.error || "No se pudo completar la carga")); return; }
        resolve(result);
      });
      request.addEventListener("error", () => reject(new Error("Se perdió la conexión")));
      request.send(payload);
    });
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, ...(options.headers || {}) },
      credentials: "same-origin",
    });
    let result = {};
    try { result = await response.json(); } catch (_error) { /* response error below */ }
    if (!response.ok) throw new Error(result.error || "No se pudo completar la operación");
    return result;
  }

  const delay = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  async function retry(operation) {
    let lastError;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try { return await operation(); } catch (error) {
        lastError = error;
        if (attempt < 2) await delay(500 * (2 ** attempt));
      }
    }
    throw lastError;
  }

  function put(url, body, itemId, offset = 0) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest(); activeRequests.set(itemId, request);
      request.open("PUT", url);
      request.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        byteProgress.set(itemId, offset + event.loaded);
        const itemTotal = itemSizes.get(itemId) || event.total;
        setItemState(
          itemId,
          `Subiendo ${Math.min(100, Math.round(((offset + event.loaded) / itemTotal) * 100))}%`,
        );
        const loaded = Array.from(byteProgress.values()).reduce((sum, value) => sum + value, 0);
        const total = selectedFiles.reduce((sum, file) => sum + file.size, 0);
        setOverallProgress(loaded, total);
      });
      request.addEventListener("load", () => {
        activeRequests.delete(itemId);
        if (request.status >= 200 && request.status < 300) resolve(request.getResponseHeader("ETag"));
        else reject(new Error(`R2 rechazó la parte (${request.status})`));
      });
      request.addEventListener("error", () => { activeRequests.delete(itemId); reject(new Error("Se perdió la conexión con R2")); });
      request.addEventListener("abort", () => reject(new Error("Carga cancelada")));
      request.send(body);
    });
  }

  function statusLabel(status) {
    return ({ PENDING: "Esperando", UPLOADING: "Subiendo", UPLOADED: "Subida completa", CONFIRMED: "Procesando", PROCESSING: "Procesando", READY: "Lista", ERROR: "Error", ABORTED: "Cancelada", EXPIRED: "Expirada" })[status] || status;
  }

  function renderDirectRows(items) {
    directList.replaceChildren();
    items.forEach((item) => {
      itemSizes.set(item.upload_item_uuid, item.size);
      const row = document.createElement("li"); row.dataset.uploadItem = item.upload_item_uuid;
      const name = document.createElement("span"); name.textContent = item.name;
      const state = document.createElement("strong"); state.textContent = statusLabel(item.status || "PENDING"); state.dataset.itemState = "";
      const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = "Cancelar";
      cancel.disabled = ["READY", "CONFIRMED", "PROCESSING", "ABORTED", "EXPIRED"].includes(item.status);
      cancel.addEventListener("click", async () => {
        activeRequests.get(item.upload_item_uuid)?.abort();
        try { await api(pattern("abort", item.upload_item_uuid), { method: "POST", body: "{}" }); } catch (_error) { /* best effort */ }
        state.textContent = "Cancelada"; cancel.disabled = true;
      });
      row.append(name, state, cancel); directList.appendChild(row);
    });
  }

  function setItemState(itemId, label) {
    const state = directList.querySelector(`[data-upload-item="${itemId}"] [data-item-state]`);
    if (state) state.textContent = label;
  }

  function pattern(kind, uuid) {
    return form.dataset[`${kind}UrlPattern`].replace("__uuid__", uuid);
  }

  async function initializeDirect(files) {
    let batchUuid = window.localStorage.getItem(storageKey) || form.dataset.activeBatch || null;
    const allItems = [];
    for (let index = 0; index < files.length; index += 250) {
      const chunk = files.slice(index, index + 250);
      const result = await api(form.dataset.directInitUrl, {
        method: "POST",
        body: JSON.stringify({ batch_uuid: batchUuid, files: chunk.map((file) => ({ name: file.name, size: file.size, type: file.type, last_modified: file.lastModified })) }),
      });
      batchUuid = result.batch_uuid; window.localStorage.setItem(storageKey, batchUuid); allItems.push(...result.items);
    }
    return { batchUuid, items: allItems };
  }

  async function uploadDirectFile(file, item) {
    setItemState(item.upload_item_uuid, "Subiendo");
    if (item.mode === "SINGLE") {
      await retry(async () => { byteProgress.set(item.upload_item_uuid, 0); await put(item.upload_url, file, item.upload_item_uuid); });
    } else {
      const uploaded = new Map();
      for (let start = 1; start <= item.total_parts; start += 50) {
        const numbers = Array.from({ length: Math.min(50, item.total_parts - start + 1) }, (_value, index) => start + index);
        const result = await api(pattern("parts", item.upload_item_uuid), { method: "POST", body: JSON.stringify({ part_numbers: numbers }) });
        result.uploaded_parts.forEach((part) => uploaded.set(part.part_number, part.etag));
        for (const part of result.parts) {
          const offset = (part.part_number - 1) * item.part_size;
          const blob = file.slice(offset, Math.min(offset + item.part_size, file.size));
          const etag = await retry(() => put(part.upload_url, blob, item.upload_item_uuid, offset));
          if (!etag) throw new Error("R2 no devolvió el ETag de la parte");
          uploaded.set(part.part_number, etag);
        }
      }
      item.parts = Array.from(uploaded, ([part_number, etag]) => ({ part_number, etag })).sort((a, b) => a.part_number - b.part_number);
    }
    byteProgress.set(item.upload_item_uuid, file.size); setItemState(item.upload_item_uuid, "Subida completa");
    const result = await retry(() => api(pattern("complete", item.upload_item_uuid), { method: "POST", body: JSON.stringify({ parts: item.parts || [] }) }));
    setItemState(item.upload_item_uuid, statusLabel(result.photo_status === "READY" ? "READY" : "PROCESSING"));
  }

  async function runWorkers(files, items) {
    const queue = files.map((file, index) => ({ file, item: items[index] }));
    const failures = [];
    async function worker() {
      while (queue.length) {
        const current = queue.shift();
        try { await uploadDirectFile(current.file, current.item); } catch (error) {
          failures.push(`${current.file.name}: ${error.message}`); setItemState(current.item.upload_item_uuid, "Error");
        }
      }
    }
    const count = Math.min(Math.max(1, Number(form.dataset.concurrency) || 4), queue.length);
    await Promise.all(Array.from({ length: count }, worker));
    return failures;
  }

  async function resumeBatch(batchUuid, announce = false) {
    const result = await api(pattern("resume", batchUuid), { method: "GET" });
    renderDirectRows(result.items);
    if (announce) {
      revealForm(); progress.hidden = false;
      progressText.textContent = result.completed_files === result.total_files ? "La carga anterior ya fue confirmada." : "Carga sin terminar: vuelve a seleccionar los mismos archivos para continuar.";
    }
    if (["COMPLETED", "ABORTED", "EXPIRED", "ERROR"].includes(result.status)) window.localStorage.removeItem(storageKey);
    return result;
  }

  async function pollBatch(batchUuid) {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const result = await resumeBatch(batchUuid);
      if (["COMPLETED", "PARTIAL", "ABORTED", "EXPIRED", "ERROR"].includes(result.status)) return result;
      await delay(3000);
    }
    return null;
  }

  window.addEventListener("beforeunload", (event) => {
    if (!browserTransferActive) return;
    event.preventDefault(); event.returnValue = "";
  });
  form.querySelector("[data-upload-resume]")?.addEventListener("click", () => resumeBatch(form.dataset.activeBatch, true).catch((error) => { progressText.textContent = error.message; }));

  form.addEventListener("submit", async (event) => {
    if (!selectedFiles.length || !window.XMLHttpRequest) return;
    event.preventDefault(); submit.disabled = true; progress.hidden = false; progressText.textContent = "Preparando la carga…"; setOverallProgress(0, 1);
    if (!direct) {
      const batchSize = Math.max(1, Number(form.dataset.batchSize) || 8);
      const totalBytes = selectedFiles.reduce((sum, file) => sum + file.size, 0) || 1;
      const batchId = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
      let completedBytes = 0; let lastResult;
      try {
        for (let index = 0; index < selectedFiles.length; index += batchSize) {
          const batch = selectedFiles.slice(index, index + batchSize);
          lastResult = await sendTraditionalBatch(batch, batchId, completedBytes, totalBytes);
          completedBytes += batch.reduce((sum, file) => sum + file.size, 0);
        }
        window.location.assign(lastResult?.redirect_url || window.location.href);
      } catch (error) { progressText.textContent = error.message; submit.disabled = false; }
      return;
    }
    browserTransferActive = true; byteProgress.clear();
    try {
      const initialized = await initializeDirect(selectedFiles); renderDirectRows(initialized.items);
      progressText.textContent = "Subiendo directamente al almacenamiento…";
      const failures = await runWorkers(selectedFiles, initialized.items); browserTransferActive = false;
      if (failures.length) { progressText.textContent = `${failures.length} archivo(s) necesitan reintento.`; submit.disabled = false; return; }
      setOverallProgress(1, 1); progressText.textContent = "Carga terminada. Procesando fotografías…";
      await pollBatch(initialized.batchUuid); window.location.reload();
    } catch (error) { browserTransferActive = false; progressText.textContent = error.message; submit.disabled = false; }
  });

  const savedBatch = direct && (window.localStorage.getItem(storageKey) || form.dataset.activeBatch);
  if (savedBatch) resumeBatch(savedBatch).catch(() => window.localStorage.removeItem(storageKey));
})();
