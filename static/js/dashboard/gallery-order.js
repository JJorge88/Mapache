(function () {
  "use strict";

  const workspace = document.querySelector(".photo-workspace");
  const grid = workspace?.querySelector("[data-photo-grid]");
  const form = document.getElementById("order-form");
  const orderData = document.getElementById("photo-order-data");
  if (!workspace || !grid || !form || !orderData) return;

  const initialVisible = Array.from(grid.querySelectorAll("[data-photo-uuid]")).map(
    (tile) => tile.dataset.photoUuid,
  );
  const initialFullOrder = JSON.parse(orderData.textContent);
  const originalPositions = initialVisible.map((id) => initialFullOrder.indexOf(id));
  const toolbar = document.querySelector("[data-order-toolbar]");
  const errorBox = document.querySelector("[data-order-error]");
  let dragged = null;

  function currentTiles() {
    return Array.from(grid.querySelectorAll("[data-photo-uuid]"));
  }

  function enterOrderMode() {
    workspace.classList.add("is-ordering");
    if (toolbar) toolbar.hidden = false;
    currentTiles()[0]?.querySelector("[data-drag-handle]")?.focus();
  }

  function cancelOrderMode() {
    const byId = new Map(currentTiles().map((tile) => [tile.dataset.photoUuid, tile]));
    initialVisible.forEach((id) => grid.appendChild(byId.get(id)));
    workspace.classList.remove("is-ordering");
    if (toolbar) toolbar.hidden = true;
    if (errorBox) errorBox.hidden = true;
  }

  function move(tile, offset) {
    const tiles = currentTiles();
    const index = tiles.indexOf(tile);
    const target = tiles[index + offset];
    if (!target) return;
    if (offset < 0) grid.insertBefore(tile, target);
    else grid.insertBefore(target, tile);
    tile.querySelector("[data-drag-handle]")?.focus();
  }

  document.querySelector("[data-order-toggle]")?.addEventListener("click", enterOrderMode);
  document.querySelector("[data-order-cancel]")?.addEventListener("click", cancelOrderMode);
  grid.addEventListener("click", (event) => {
    const previous = event.target.closest("[data-order-before]");
    const next = event.target.closest("[data-order-after]");
    if (previous) move(previous.closest("[data-photo-uuid]"), -1);
    if (next) move(next.closest("[data-photo-uuid]"), 1);
  });

  grid.addEventListener("pointerdown", (event) => {
    const handle = event.target.closest("[data-drag-handle]");
    if (!handle || !workspace.classList.contains("is-ordering")) return;
    dragged = handle.closest("[data-photo-uuid]");
    dragged.classList.add("is-dragging");
    handle.setPointerCapture?.(event.pointerId);
  });
  grid.addEventListener("pointermove", (event) => {
    if (!dragged) return;
    const underPointer = document.elementFromPoint(event.clientX, event.clientY)?.closest("[data-photo-uuid]");
    if (!underPointer || underPointer === dragged || underPointer.parentElement !== grid) return;
    const rect = underPointer.getBoundingClientRect();
    const after = event.clientY > rect.top + rect.height / 2;
    grid.insertBefore(dragged, after ? underPointer.nextSibling : underPointer);
  });
  function stopDragging() {
    dragged?.classList.remove("is-dragging");
    dragged = null;
  }
  grid.addEventListener("pointerup", stopDragging);
  grid.addEventListener("pointercancel", stopDragging);

  document.querySelector("[data-order-save]")?.addEventListener("click", async () => {
    const fullOrder = [...initialFullOrder];
    const visibleOrder = currentTiles().map((tile) => tile.dataset.photoUuid);
    originalPositions.forEach((position, index) => {
      fullOrder[position] = visibleOrder[index];
    });
    if (errorBox) errorBox.hidden = true;
    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": form.querySelector('[name="csrfmiddlewaretoken"]').value,
          Accept: "application/json",
        },
        body: JSON.stringify({ photo_uuids: fullOrder }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "El orden cambió en otra sesión.");
      window.location.reload();
    } catch (error) {
      if (errorBox) {
        errorBox.textContent = error.message;
        errorBox.hidden = false;
      }
    }
  });
})();
