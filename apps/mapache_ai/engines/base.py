from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DetectedFace:
    bounding_box: dict[str, int]
    confidence: float
    source: Any = field(repr=False, compare=False)


class FaceEngine(ABC):
    embedding_dimension: int
    metric: str

    @abstractmethod
    def detect_faces(self, image_bytes: bytes) -> list[DetectedFace]:
        """Detect every face in an encoded image."""

    @abstractmethod
    def generate_embedding(self, face: DetectedFace) -> list[float]:
        """Generate one normalized embedding for a detected face."""
