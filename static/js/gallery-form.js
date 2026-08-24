const visibility = document.querySelector("#id_visibility");
const pinField = document.querySelector('[data-field="pin"]');

function updatePinVisibility() {
  if (!visibility || !pinField) return;
  const isPrivate = visibility.value === "PRIVATE_PIN";
  pinField.hidden = !isPrivate;
  const input = pinField.querySelector("input");
  if (input) input.disabled = !isPrivate;
}

visibility?.addEventListener("change", updatePinVisibility);
updatePinVisibility();
