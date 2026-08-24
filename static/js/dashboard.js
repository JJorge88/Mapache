const shell = document.querySelector(".dashboard-shell");
const sidebar = document.querySelector(".sidebar");
const openButton = document.querySelector("[data-menu-open]");
const closeButtons = document.querySelectorAll("[data-menu-close]");
const mobileMenu = window.matchMedia("(max-width: 900px)");

function menuIsOpen() {
  return shell?.classList.contains("menu-active") ?? false;
}

function syncMenuAvailability() {
  if (!sidebar) return;
  if (mobileMenu.matches && !menuIsOpen()) {
    sidebar.setAttribute("inert", "");
  } else {
    sidebar.removeAttribute("inert");
  }
}

function setMenu(open) {
  shell?.classList.toggle("menu-active", open);
  openButton?.setAttribute("aria-expanded", String(open));
  document.body.style.overflow = open ? "hidden" : "";
  syncMenuAvailability();

  if (!mobileMenu.matches) return;
  if (open) {
    window.requestAnimationFrame(() => sidebar?.querySelector("[data-menu-close]")?.focus());
  } else {
    window.requestAnimationFrame(() => openButton?.focus());
  }
}

openButton?.addEventListener("click", () => setMenu(true));
closeButtons.forEach((button) => button.addEventListener("click", () => setMenu(false)));
document.addEventListener("keydown", (event) => {
  if (!mobileMenu.matches || !menuIsOpen()) return;
  if (event.key === "Escape") {
    setMenu(false);
    return;
  }
  if (event.key !== "Tab") return;

  const focusable = [...sidebar.querySelectorAll("a[href], button:not([disabled])")];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

mobileMenu.addEventListener("change", () => {
  if (!mobileMenu.matches) {
    shell?.classList.remove("menu-active");
    openButton?.setAttribute("aria-expanded", "false");
    document.body.style.overflow = "";
  }
  syncMenuAvailability();
});
syncMenuAvailability();
