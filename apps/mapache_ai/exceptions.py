class FaceEngineError(Exception):
    """Base error for facial engine operations."""


class FaceEngineUnavailable(FaceEngineError):
    """Raised when the configured engine or model files cannot be loaded."""


class NoFaceDetected(FaceEngineError):
    """Raised when a query image contains no detectable face."""


class MultipleFacesDetected(FaceEngineError):
    """Raised when a query image contains more than one face."""
