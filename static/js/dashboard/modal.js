(function () {
  "use strict";

  let activeModal = null;
  let returnFocus = null;

  function focusableElements(modal) {
    return Array.from(
      modal.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((element) => !element.hidden && element.offsetParent !== null);
  }

  function open(modalOrId, trigger) {
    const modal =
      typeof modalOrId === "string" ? document.getElementById(modalOrId) : modalOrId;
    if (!modal) return;
    if (activeModal && activeModal !== modal) close(activeModal, false);
    returnFocus = trigger || document.activeElement;
    activeModal = modal;
    modal.hidden = false;
    document.body.classList.add("has-modal");
    const target = modal.querySelector("[autofocus]") || focusableElements(modal)[0];
    if (target) window.requestAnimationFrame(() => target.focus());
  }

  function close(modalOrId, restoreFocus = true) {
    const modal =
      typeof modalOrId === "string" ? document.getElementById(modalOrId) : modalOrId;
    if (!modal) return;
    modal.hidden = true;
    if (activeModal === modal) activeModal = null;
    document.body.classList.remove("has-modal");
    if (restoreFocus && returnFocus && typeof returnFocus.focus === "function") {
      returnFocus.focus();
    }
  }

  async function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const input = document.createElement("textarea");
    input.value = value;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  }

  document.addEventListener("click", async (event) => {
    const opener = event.target.closest("[data-modal-open]");
    if (opener) {
      event.preventDefault();
      open(opener.dataset.modalOpen, opener);
      return;
    }

    const closer = event.target.closest("[data-modal-close]");
    if (closer) {
      event.preventDefault();
      close(closer.closest(".modal"));
      return;
    }

    if (event.target.matches(".modal")) close(event.target);

    const copyButton = event.target.closest("[data-copy-value]");
    if (copyButton) {
      event.preventDefault();
      const original = copyButton.textContent;
      try {
        await copyText(copyButton.dataset.copyValue || "");
        copyButton.textContent = "Copiado";
      } catch (_error) {
        copyButton.textContent = "No se pudo copiar";
      }
      window.setTimeout(() => {
        copyButton.textContent = original;
      }, 1800);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (!activeModal) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close(activeModal);
      return;
    }
    if (event.key !== "Tab") return;
    const elements = focusableElements(activeModal);
    if (!elements.length) return;
    const first = elements[0];
    const last = elements[elements.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  window.MapacheModal = { open, close };
  window.mapacheCopy = copyText;

  document.querySelectorAll("[data-modal-autopen]").forEach((modal) => open(modal));
})();
