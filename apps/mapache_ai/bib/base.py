from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RecognizedBib:
    raw_text: str
    confidence: float
    bounding_box: dict[str, float]


class BibRecognitionEngine(ABC):
    @abstractmethod
    def recognize_bibs(self, image_bytes: bytes, *, bib_format: str) -> list[RecognizedBib]:
        """Detect candidate text regions and OCR possible bibs from an encoded image."""
