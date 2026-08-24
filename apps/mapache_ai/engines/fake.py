from ..constants import FACE_EMBEDDING_DIMENSION
from .base import DetectedFace, FaceEngine


class FakeFaceEngine(FaceEngine):
    embedding_dimension = FACE_EMBEDDING_DIMENSION
    metric = "cosine"

    def __init__(self, faces: list[list[float]] | None = None) -> None:
        self.faces = faces if faces is not None else [[1.0] + [0.0] * 127]

    def detect_faces(self, image_bytes: bytes) -> list[DetectedFace]:
        if image_bytes.startswith(b"NO_FACE"):
            return []
        vectors = self.faces
        if image_bytes.startswith(b"MULTI_FACE"):
            vectors = [self.faces[0], [0.0, 1.0] + [0.0] * 126]
        return [
            DetectedFace(
                bounding_box={"x": index * 10, "y": 0, "width": 100, "height": 100},
                confidence=0.99,
                source=vector,
            )
            for index, vector in enumerate(vectors)
        ]

    def generate_embedding(self, face: DetectedFace) -> list[float]:
        return list(face.source)
