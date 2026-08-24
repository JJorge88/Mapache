const photoPage = document.querySelector("[data-photo-status-url]");

function updatePhoto(photo) {
  const card = document.querySelector(`[data-photo-uuid="${photo.uuid}"]`);
  if (!card) return;
  const status = card.querySelector("[data-photo-status]");
  if (status) {
    status.textContent = photo.label;
    status.className = `photo-status status-${photo.status.toLowerCase()}`;
  }
  card.dataset.photoStatusValue = photo.status;
  card.dataset.statusLabel = photo.label;
  if (photo.preview_url) card.dataset.previewUrl = photo.preview_url;
  const preview = card.querySelector(".photo-visual, .managed-photo-preview");
  if (preview && photo.thumbnail_url && !preview.querySelector("img")) {
    const image = document.createElement("img");
    image.src = photo.thumbnail_url;
    image.alt = card.dataset.filename || card.querySelector("strong")?.textContent || "Fotografía";
    preview.replaceChildren(image);
    preview.dataset.photoPreview = "";
  }
}

async function pollStatus() {
  if (!photoPage) return;
  try {
    const response = await fetch(photoPage.dataset.photoStatusUrl, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    if (!response.ok) return;
    const data = await response.json();
    ["total", "ready", "processing", "pending", "error"].forEach((key) => {
      const element = document.querySelector(`[data-stat="${key}"]`);
      if (element) element.textContent = data[key];
    });
    const readyInline = document.querySelector('[data-stat="ready-inline"]');
    if (readyInline) readyInline.textContent = data.ready;
    const progress = document.querySelector("[data-progress]");
    if (progress) progress.style.width = `${data.total ? (data.ready / data.total) * 100 : 0}%`;
    data.photos.forEach(updatePhoto);
    if (data.pending > 0 || data.processing > 0) window.setTimeout(pollStatus, 4000);
  } catch (_error) {
    window.setTimeout(pollStatus, 8000);
  }
}

if (photoPage) pollStatus();
