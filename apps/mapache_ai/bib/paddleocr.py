from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.conf import settings

from .base import BibRecognitionEngine, RecognizedBib


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def recognized_bibs_from_payload(
    payload: Mapping[str, Any], *, image_width: int, image_height: int
) -> list[RecognizedBib]:
    """Translate PaddleOCR's public JSON result into Mapache's engine-neutral format."""
    result = payload.get("res", payload)
    texts = result.get("rec_texts", [])
    scores = result.get("rec_scores", [])
    boxes = result.get("rec_boxes", [])
    recognized: list[RecognizedBib] = []

    if image_width <= 0 or image_height <= 0:
        return recognized
    for text, score, box in zip(texts, scores, boxes, strict=False):
        raw = str(text).strip()
        if not raw or len(box) < 4:
            continue
        try:
            x1, y1, x2, y2 = (float(value) for value in box[:4])
            confidence = _clamp(float(score))
        except (TypeError, ValueError):
            continue
        left = _clamp(x1 / image_width)
        top = _clamp(y1 / image_height)
        right = _clamp(x2 / image_width)
        bottom = _clamp(y2 / image_height)
        recognized.append(
            RecognizedBib(
                raw_text=raw,
                confidence=confidence,
                bounding_box={
                    "x": round(left, 6),
                    "y": round(top, 6),
                    "width": round(max(0.0, right - left), 6),
                    "height": round(max(0.0, bottom - top), 6),
                },
            )
        )
    return recognized


class PaddleOCRBibRecognitionEngine(BibRecognitionEngine):
    """Local PP-OCRv6 adapter; images never leave Mapache's infrastructure."""

    def __init__(self, pipeline=None):
        if pipeline is None:
            from paddleocr import PaddleOCR

            pipeline = PaddleOCR(
                text_detection_model_name=settings.MAPACHE_BIB_PADDLE_DET_MODEL,
                text_recognition_model_name=settings.MAPACHE_BIB_PADDLE_REC_MODEL,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device=settings.MAPACHE_BIB_PADDLE_DEVICE,
            )
        self.pipeline = pipeline

    def recognize_bibs(self, image_bytes: bytes, *, bib_format: str) -> list[RecognizedBib]:
        import cv2
        import numpy as np

        encoded = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("La imagen optimizada no pudo decodificarse.")
        height, width = image.shape[:2]
        recognized: list[RecognizedBib] = []
        for output in self.pipeline.predict(image):
            payload = output.json if hasattr(output, "json") else output
            recognized.extend(
                recognized_bibs_from_payload(
                    payload,
                    image_width=width,
                    image_height=height,
                )
            )
        return recognized
