(function () {
  "use strict";

  function closeAll(except) {
    document.querySelectorAll(".context-menu[data-open]").forEach((menu) => {
      if (menu === except) return;
      delete menu.dataset.open;
      menu.querySelector("[data-menu-trigger]")?.setAttribute("aria-expanded", "false");
    });
  }

  function toggle(menu, forceOpen) {
    const shouldOpen = forceOpen === undefined ? !menu.dataset.open : forceOpen;
    closeAll(shouldOpen ? menu : null);
    const trigger = menu.querySelector("[data-menu-trigger]");
    if (shouldOpen) {
      menu.dataset.open = "true";
      trigger?.setAttribute("aria-expanded", "true");
    } else {
      delete menu.dataset.open;
      trigger?.setAttribute("aria-expanded", "false");
    }
  }

  document.addEventListener("click", async (event) => {
    const trigger = event.target.closest("[data-menu-trigger]");
    if (trigger) {
      event.preventDefault();
      event.stopPropagation();
      toggle(trigger.closest(".context-menu"));
      return;
    }

    const copyButton = event.target.closest("[data-copy-url]");
    if (copyButton) {
      event.preventDefault();
      const original = copyButton.textContent;
      try {
        const response = await fetch(copyButton.dataset.copyUrl, {
          headers: { Accept: "application/json" },
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "No disponible");
        await window.mapacheCopy(payload.url);
        copyButton.textContent = "Enlace copiado";
      } catch (_error) {
        copyButton.textContent = "No se pudo copiar";
      }
      window.setTimeout(() => {
        copyButton.textContent = original;
      }, 1800);
      closeAll();
      return;
    }

    if (!event.target.closest(".context-menu")) closeAll();
  });

  document.addEventListener("keydown", (event) => {
    const trigger = event.target.closest("[data-menu-trigger]");
    if (trigger && ["Enter", " ", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      const menu = trigger.closest(".context-menu");
      toggle(menu, true);
      menu.querySelector('[role="menuitem"]')?.focus();
      return;
    }
    if (event.key === "Escape") {
      const openMenu = document.querySelector(".context-menu[data-open]");
      if (openMenu) {
        event.preventDefault();
        const openTrigger = openMenu.querySelector("[data-menu-trigger]");
        closeAll();
        openTrigger?.focus();
      }
    }
  });
})();
