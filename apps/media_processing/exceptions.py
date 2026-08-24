class MediaProcessingError(Exception):
    """Error base del procesamiento de fotografías."""


class PermanentImageError(MediaProcessingError):
    """La imagen no puede procesarse y reintentar no cambiará el resultado."""


class TransientProcessingError(MediaProcessingError):
    """Un recurso temporal impidió procesar la fotografía."""
