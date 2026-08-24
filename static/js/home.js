(function () {
  "use strict";

  const rows = document.querySelectorAll("[data-service]");
  const visual = document.querySelector("[data-service-visual]");
  function selectService(row) {
    rows.forEach((candidate) => candidate.classList.toggle("is-active", candidate === row));
    if (visual) visual.dataset.activeService = row.dataset.service;
  }
  rows.forEach((row) => {
    row.addEventListener("mouseenter", () => selectService(row));
    row.addEventListener("focus", () => selectService(row));
    row.addEventListener("click", () => selectService(row));
  });
})();
