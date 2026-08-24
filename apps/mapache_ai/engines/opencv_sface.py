from pathlib import Path

from django.conf import settings

from ..constants import FACE_EMBEDDING_DIMENSION
from ..exceptions import FaceEngineError, FaceEngineUnavailable
from .base import DetectedFace, FaceEngine


class OpenCVSFaceEngine(FaceEngine):
    embedding_dimension = FACE_EMBEDDING_DIMENSION
    metric = "cosine"

    def __init__(self) -> None:
        detector_path = Path(settings.MAPACHE_FACE_DETECTOR_MODEL)
        recognizer_path = Path(settings.MAPACHE_FACE_RECOGNIZER_MODEL)
        if not detector_path.is_file() or not recognizer_path.is_file():
            raise FaceEngineUnavailable(
                "Los modelos YuNet/SFace no están instalados en las rutas configuradas."
            )
        try:
            import cv2

            self.cv2 = cv2
            self.detector = cv2.FaceDetectorYN.create(
                str(detector_path), "", (320, 320), 0.9, 0.3, 5000
            )
            self.recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
        except Exception as exc:  # Import and OpenCV expose environment-specific errors.
            raise FaceEngineUnavailable("OpenCV SFace no pudo inicializarse.") from exc

    def detect_faces(self, image_bytes: bytes) -> list[DetectedFace]:
        try:
            import numpy as np

            image = self.cv2.imdecode(
                np.frombuffer(image_bytes, dtype=np.uint8), self.cv2.IMREAD_COLOR
            )
            if image is None:
                raise FaceEngineError("OpenCV no pudo decodificar la imagen.")
            height, width = image.shape[:2]
            self.detector.setInputSize((width, height))
            _result, detections = self.detector.detect(image)
        except FaceEngineError:
            raise
        except (ValueError, self.cv2.error) as exc:
            raise FaceEngineError("Falló la detección facial.") from exc
        if detections is None:
            return []
        faces = []
        for detection in detections:
            x, y, width, height = (int(round(value)) for value in detection[:4])
            faces.append(
                DetectedFace(
                    bounding_box={"x": x, "y": y, "width": width, "height": height},
                    confidence=float(detection[-1]),
                    source=(image, detection.copy()),
                )
            )
        return faces

    def generate_embedding(self, face: DetectedFace) -> list[float]:
        try:
            import numpy as np

            image, detection = face.source
            aligned = self.recognizer.alignCrop(image, detection)
            vector = self.recognizer.feature(aligned).flatten().astype("float32")
            norm = float(np.linalg.norm(vector))
            if norm == 0:
                raise FaceEngineError("SFace generó un vector facial vacío.")
            normalized = vector / norm
        except FaceEngineError:
            raise
        except (ValueError, self.cv2.error) as exc:
            raise FaceEngineError("Falló la generación de la representación facial.") from exc
        if normalized.size != self.embedding_dimension:
            raise FaceEngineError("La dimensión producida por SFace no es la configurada.")
        return normalized.tolist()
