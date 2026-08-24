(function () {
  "use strict";

  const workspace = document.querySelector(".photo-workspace");
  if (!workspace) return;

  const checkboxes = Array.from(workspace.querySelectorAll("[data-photo-checkbox]"));
  const toolbar = document.querySelector("[data-selection-toolbar]");
  const count = document.querySelector("[data-selected-count]");
  const deleteForm = document.querySelector("[data-bulk-delete-form]");
  const deleteInputs = deleteForm?.querySelector("[data-delete-inputs]");

  function selectedIds() {
    return checkboxes.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
  }

  function refreshSelection() {
    const total = selectedIds().length;
    if (count) count.textContent = `${total} seleccionada${total === 1 ? "" : "s"}`;
    const deleteButton = document.querySelector("[data-bulk-delete-open]");
    if (deleteButton) deleteButton.disabled = total === 0;
    workspace.querySelectorAll("[data-photo-uuid]").forEach((tile) => {
      tile.classList.toggle("is-selected", Boolean(tile.querySelector("[data-photo-checkbox]")?.checked));
    });
  }

  function selectionMode(enabled) {
    workspace.classList.toggle("is-selecting", enabled);
    if (toolbar) toolbar.hidden = !enabled;
    if (!enabled) {
      checkboxes.forEach((checkbox) => {
        checkbox.checked = false;
      });
      refreshSelection();
    }
  }

  document.querySelectorAll("[data-selection-toggle]").forEach((button) => {
    button.addEventListener("click", () => selectionMode(!workspace.classList.contains("is-selecting")));
  });
  document.querySelector("[data-selection-cancel]")?.addEventListener("click", () => selectionMode(false));
  document.querySelector("[data-select-page]")?.addEventListener("click", () => {
    checkboxes.forEach((checkbox) => {
      checkbox.checked = true;
    });
    refreshSelection();
  });
  checkboxes.forEach((checkbox) => checkbox.addEventListener("change", refreshSelection));

  function prepareDelete(ids) {
    if (!deleteInputs || !ids.length) return false;
    deleteInputs.replaceChildren();
    ids.forEach((id) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "photo_uuids";
      input.value = id;
      deleteInputs.appendChild(input);
    });
    return true;
  }

  document.querySelector("[data-bulk-delete-open]")?.addEventListener("click", (event) => {
    event.preventDefault();
    if (prepareDelete(selectedIds())) {
      document.querySelector("[data-delete-count]").textContent = selectedIds().length;
      window.MapacheModal.open("delete-modal", event.currentTarget);
    }
  });

  workspace.addEventListener("click", (event) => {
    const button = event.target.closest("[data-photo-preview]");
    if (!button) return;
    const tile = button.closest("[data-photo-uuid]");
    const modal = document.getElementById("photo-preview-modal");
    if (!tile || !modal) return;
    const image = modal.querySelector("[data-preview-image]");
    image.src = tile.dataset.previewUrl;
    image.alt = tile.dataset.filename;
    modal.querySelector("[data-preview-filename]").textContent = tile.dataset.filename;
    modal.querySelector("[data-preview-dimensions]").textContent = tile.dataset.dimensions;
    modal.querySelector("[data-preview-size]").textContent = tile.dataset.size;
    modal.querySelector("[data-preview-orientation]").textContent = tile.dataset.orientation;
    modal.querySelector("[data-preview-status]").textContent = tile.dataset.statusLabel;
    const coverButton = modal.querySelector("[data-preview-cover]");
    coverButton.closest("form").hidden =
      tile.dataset.photoStatusValue !== "READY" || tile.classList.contains("is-cover");
    coverButton.dataset.photoUuid = tile.dataset.photoUuid;
    modal.querySelector("[data-preview-delete]").dataset.photoUuid = tile.dataset.photoUuid;
    window.MapacheModal.open(modal, button);
  });

  document.querySelector("[data-preview-delete]")?.addEventListener("click", (event) => {
    const id = event.currentTarget.dataset.photoUuid;
    window.MapacheModal.close("photo-preview-modal", false);
    if (prepareDelete([id])) {
      document.querySelector("[data-delete-count]").textContent = "1";
      window.MapacheModal.open("delete-modal", event.currentTarget);
    }
  });

  document.querySelector("[data-preview-cover]")?.addEventListener("click", (event) => {
    event.preventDefault();
    const form = document.querySelector("[data-cover-form]");
    if (!form) return;
    form.action = workspace.dataset.coverUrlPattern.replace(
      "__uuid__",
      event.currentTarget.dataset.photoUuid,
    );
    form.requestSubmit();
  });

  refreshSelection();
})();
