(function () {
  "use strict";
  const form = document.querySelector("[data-face-search-form]");
  if (!form) return;
  const fileInput = form.querySelector("[data-library-input]");
  const cameraInput = form.querySelector("[data-camera-input]");
  const fileLabel = form.querySelector("[data-file-label]");
  const cameraButton = form.querySelector("[data-camera-source]");
  const libraryButton = form.querySelector("[data-library-source]");
  const cameraModal = document.querySelector("[data-camera-modal]");
  const cameraVideo = cameraModal?.querySelector("[data-camera-video]");
  const cameraCanvas = cameraModal?.querySelector("[data-camera-canvas]");
  const cameraError = cameraModal?.querySelector("[data-camera-error]");
  const shutterButton = cameraModal?.querySelector("[data-camera-shutter]");
  const capturedPreview = cameraModal?.querySelector("[data-camera-photo]");
  const cameraPreview = cameraModal?.querySelector("[data-camera-preview]");
  const cameraHelp = cameraModal?.querySelector("[data-camera-help]");
  const reviewLabel = cameraModal?.querySelector("[data-camera-review-label]");
  const liveActions = cameraModal?.querySelector("[data-camera-live-actions]");
  const reviewActions = cameraModal?.querySelector("[data-camera-review-actions]");
  const retakeButton = cameraModal?.querySelector("[data-camera-retake]");
  const usePhotoButton = cameraModal?.querySelector("[data-camera-use]");
  let cameraStream = null;
  let pendingCapture = null;
  let previewUrl = "";
  if (fileInput && fileLabel) {
    const showSelectedFile = (input) => {
      const file = input.files && input.files[0];
      fileLabel.textContent = file ? file.name : "Agrega una foto tuya";
      input.closest(".face-upload")?.classList.toggle("has-file", Boolean(file));
    };
    const stopCamera = () => {
      cameraStream?.getTracks().forEach((track) => track.stop());
      cameraStream = null;
      if (cameraVideo) cameraVideo.srcObject = null;
    };
    const resetReview = () => {
      pendingCapture = null;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = "";
      if (capturedPreview) {
        capturedPreview.hidden = true;
        capturedPreview.removeAttribute("src");
      }
      cameraPreview?.classList.remove("is-reviewing");
      if (reviewLabel) reviewLabel.hidden = true;
      if (reviewActions) reviewActions.hidden = true;
      if (liveActions) liveActions.hidden = false;
      if (cameraHelp) cameraHelp.textContent = "Mira de frente, procura tener buena luz y coloca tu rostro dentro de la guía.";
    };
    const closeCamera = () => {
      stopCamera();
      resetReview();
      if (cameraModal) cameraModal.hidden = true;
      document.body.classList.remove("camera-is-open");
      cameraButton?.focus();
    };
    const openCamera = async () => {
      fileInput.value = "";
      if (!navigator.mediaDevices?.getUserMedia || !cameraModal || !cameraVideo) {
        cameraInput?.click();
        return;
      }
      cameraModal.hidden = false;
      document.body.classList.add("camera-is-open");
      resetReview();
      if (shutterButton) shutterButton.disabled = true;
      if (cameraError) cameraError.hidden = true;
      try {
        cameraStream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 960 } },
        });
        cameraVideo.srcObject = cameraStream;
        await cameraVideo.play();
        if (shutterButton) {
          shutterButton.disabled = false;
          shutterButton.focus();
        }
      } catch (_error) {
        if (cameraError) {
          cameraError.textContent = "No pudimos abrir la cámara. Permite su uso en el navegador y vuelve a intentarlo.";
          cameraError.hidden = false;
        }
      }
    };
    cameraButton?.addEventListener("click", openCamera);
    libraryButton?.addEventListener("click", () => {
      if (cameraInput) cameraInput.value = "";
      fileInput.click();
    });
    fileInput.addEventListener("change", () => {
      showSelectedFile(fileInput);
    });
    cameraInput?.addEventListener("change", () => showSelectedFile(cameraInput));
    cameraModal?.querySelectorAll("[data-camera-close]").forEach((button) => {
      button.addEventListener("click", closeCamera);
    });
    shutterButton?.addEventListener("click", () => {
      if (!cameraVideo?.videoWidth || !cameraCanvas) return;
      cameraCanvas.width = cameraVideo.videoWidth;
      cameraCanvas.height = cameraVideo.videoHeight;
      const context = cameraCanvas.getContext("2d");
      context.drawImage(cameraVideo, 0, 0, cameraCanvas.width, cameraCanvas.height);
      cameraCanvas.toBlob((blob) => {
        if (!blob) return;
        pendingCapture = new File([blob], `foto-mapache-${Date.now()}.jpg`, {
          type: "image/jpeg",
        });
        previewUrl = URL.createObjectURL(blob);
        if (capturedPreview) {
          capturedPreview.src = previewUrl;
          capturedPreview.hidden = false;
        }
        cameraPreview?.classList.add("is-reviewing");
        if (reviewLabel) reviewLabel.hidden = false;
        if (liveActions) liveActions.hidden = true;
        if (reviewActions) reviewActions.hidden = false;
        if (cameraHelp) cameraHelp.textContent = "Revisa la foto antes de continuar.";
      }, "image/jpeg", 0.92);
    });
    retakeButton?.addEventListener("click", resetReview);
    usePhotoButton?.addEventListener("click", () => {
      if (!pendingCapture) return;
      const transfer = new DataTransfer();
      transfer.items.add(pendingCapture);
      fileInput.files = transfer.files;
      if (cameraInput) cameraInput.value = "";
      showSelectedFile(fileInput);
      closeCamera();
    });
    document.addEventListener("keydown", (event) => {
      if (!cameraModal || cameraModal.hidden) return;
      if (event.key === "Escape") {
        closeCamera();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [...cameraModal.querySelectorAll("button:not([disabled])")].filter(
        (button) => !button.closest("[hidden]"),
      );
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
  }
  form.addEventListener("submit", () => {
    const searching = form.querySelector("[data-searching]");
    const button = form.querySelector('button[type="submit"]');
    if (searching) searching.hidden = false;
    if (button) button.hidden = true;
  });
})();
