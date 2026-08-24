from django.conf import settings

from .base import BibRecognitionEngine, RecognizedBib


class TesseractBibRecognitionEngine(BibRecognitionEngine):
    """CPU adapter using Tesseract's sparse-text detector and word-level OCR."""

    def recognize_bibs(self, image_bytes: bytes, *, bib_format: str) -> list[RecognizedBib]:
        import cv2
        import numpy as np
        import pytesseract

        encoded = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("La imagen optimizada no pudo decodificarse.")
        original_height, original_width = image.shape[:2]
        longest = max(original_height, original_width)
        scale = min(1.0, 2400 / longest)
        if longest < 1200:
            scale = min(2.0, 1200 / longest)
        if scale != 1.0:
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
        threshold = cv2.adaptiveThreshold(
            clahe,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11,
        )
        whitelist = (
            "0123456789" if bib_format == "NUMERIC" else "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        )
        config = (
            f"--oem 1 --psm 11 -c tessedit_char_whitelist={whitelist} "
            "-c user_defined_dpi=300 -c classify_bln_numeric_mode=1"
        )
        candidates: list[RecognizedBib] = []
        for variant in (cv2.cvtColor(image, cv2.COLOR_BGR2RGB), clahe, threshold):
            data = pytesseract.image_to_data(
                variant,
                config=config,
                output_type=pytesseract.Output.DICT,
                timeout=settings.MAPACHE_BIB_OCR_TIMEOUT,
            )
            height, width = variant.shape[:2]
            for index, text in enumerate(data["text"]):
                raw = text.strip()
                try:
                    confidence = float(data["conf"][index]) / 100.0
                except (TypeError, ValueError):
                    continue
                if not raw or confidence < 0:
                    continue
                candidates.append(
                    RecognizedBib(
                        raw_text=raw,
                        confidence=max(0.0, min(confidence, 1.0)),
                        bounding_box={
                            "x": round(data["left"][index] / width, 6),
                            "y": round(data["top"][index] / height, 6),
                            "width": round(data["width"][index] / width, 6),
                            "height": round(data["height"][index] / height, 6),
                        },
                    )
                )
        return candidates
