(function () {
  "use strict";
  const root = document.querySelector("[data-gallery-lightbox-root]");
  const lightbox = document.querySelector("[data-gallery-lightbox]");
  if (!root || !lightbox) return;

  const items = [...root.querySelectorAll("[data-lightbox-item]")];
  const image = lightbox.querySelector("[data-lightbox-image]");
  const download = lightbox.querySelector("[data-lightbox-download]");
  const closeButton = lightbox.querySelector("[data-lightbox-close]");
  let activeIndex = 0;
  let trigger = null;

  function render(index) {
    activeIndex = (index + items.length) % items.length;
    const item = items[activeIndex];
    image.src = item.dataset.imageUrl;
    image.alt = item.dataset.imageAlt || "Fotografía ampliada";
    if (item.dataset.downloadUrl) {
      download.href = item.dataset.downloadUrl;
      download.hidden = false;
    } else {
      download.hidden = true;
      download.removeAttribute("href");
    }
  }

  function open(index, source) {
    trigger = source;
    items.forEach((item) => {
      item.setAttribute("aria-pressed", "false");
      item.closest("figure")?.classList.remove("is-selected");
    });
    source.setAttribute("aria-pressed", "true");
    source.closest("figure")?.classList.add("is-selected");
    render(index);
    lightbox.hidden = false;
    document.body.classList.add("lightbox-open");
    closeButton.focus();
  }

  function close() {
    lightbox.hidden = true;
    image.removeAttribute("src");
    document.body.classList.remove("lightbox-open");
    trigger?.focus();
  }

  items.forEach((item, index) => item.addEventListener("click", () => open(index, item)));
  lightbox.querySelector("[data-lightbox-prev]").addEventListener("click", () => render(activeIndex - 1));
  lightbox.querySelector("[data-lightbox-next]").addEventListener("click", () => render(activeIndex + 1));
  closeButton.addEventListener("click", close);
  lightbox.addEventListener("click", (event) => { if (event.target === lightbox) close(); });
  document.addEventListener("keydown", (event) => {
    if (lightbox.hidden) return;
    if (event.key === "Escape") close();
    if (event.key === "ArrowLeft") render(activeIndex - 1);
    if (event.key === "ArrowRight") render(activeIndex + 1);
  });
})();
