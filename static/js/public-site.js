(function () {
  "use strict";

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const reveals = document.querySelectorAll("[data-reveal], [data-photo-reveal]");
  if (reducedMotion || !("IntersectionObserver" in window)) {
    reveals.forEach((element) => element.classList.add("is-visible"));
  } else {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        });
      },
      { rootMargin: "0px 0px -7%", threshold: 0.1 },
    );
    reveals.forEach((element) => observer.observe(element));
  }

  const heroSlides = [...document.querySelectorAll("[data-hero-slide]")];
  const heroCounter = document.querySelector("[data-hero-counter]");
  if (!reducedMotion && heroSlides.length > 1) {
    let activeHero = 0;
    window.setInterval(() => {
      heroSlides[activeHero].classList.remove("is-active");
      activeHero = (activeHero + 1) % heroSlides.length;
      heroSlides[activeHero].classList.add("is-active");
      if (heroCounter) {
        heroCounter.textContent = `${String(activeHero + 1).padStart(2, "0")} / ${String(heroSlides.length).padStart(2, "0")}`;
      }
    }, 6500);
  }

  const toggle = document.querySelector("[data-menu-toggle]");
  const navigation = document.querySelector("[data-site-navigation]");
  const label = toggle?.querySelector("[data-menu-label]");
  if (!toggle || !navigation) return;

  function setMenu(open, restoreFocus = false) {
    toggle.setAttribute("aria-expanded", String(open));
    navigation.classList.toggle("is-open", open);
    document.body.classList.toggle("menu-open", open);
    if (label) label.textContent = open ? "CERRAR" : "MENÚ";
    if (open) window.setTimeout(() => navigation.querySelector("a")?.focus(), 0);
    else if (restoreFocus) toggle.focus();
  }

  toggle.addEventListener("click", () => {
    setMenu(toggle.getAttribute("aria-expanded") !== "true");
  });
  navigation.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => setMenu(false));
  });
  document.addEventListener("keydown", (event) => {
    if (toggle.getAttribute("aria-expanded") !== "true") return;
    if (event.key === "Escape") {
      setMenu(false, true);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [toggle, ...navigation.querySelectorAll("a")];
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
})();
