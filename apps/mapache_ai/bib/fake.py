from .base import BibRecognitionEngine, RecognizedBib


class FakeBibRecognitionEngine(BibRecognitionEngine):
    def __init__(self, detections: list[RecognizedBib] | None = None) -> None:
        self.detections = detections

    def recognize_bibs(self, image_bytes: bytes, *, bib_format: str) -> list[RecognizedBib]:
        if self.detections is not None:
            return list(self.detections)
        if image_bytes.startswith(b"NO_BIB"):
            return []
        return [
            RecognizedBib(
                raw_text="247",
                confidence=0.95,
                bounding_box={"x": 0.2, "y": 0.25, "width": 0.3, "height": 0.2},
            )
        ]
