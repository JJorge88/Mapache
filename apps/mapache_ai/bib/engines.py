import logging
from functools import lru_cache
from importlib import import_module

from django.conf import settings

from .base import BibRecognitionEngine

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _load_engine(engine_name: str) -> BibRecognitionEngine:
    if engine_name == "paddleocr":
        try:
            from .paddleocr import PaddleOCRBibRecognitionEngine

            return PaddleOCRBibRecognitionEngine()
        except Exception:
            logger.exception("PP-OCRv6 unavailable; falling back to local Tesseract OCR")
            from .tesseract import TesseractBibRecognitionEngine

            return TesseractBibRecognitionEngine()
    if engine_name == "tesseract":
        from .tesseract import TesseractBibRecognitionEngine

        return TesseractBibRecognitionEngine()
    if "." not in engine_name:
        raise ValueError(f"Motor de dorsales desconocido: {engine_name}")
    module_name, class_name = engine_name.rsplit(".", 1)
    return getattr(import_module(module_name), class_name)()


def get_bib_engine() -> BibRecognitionEngine:
    return _load_engine(settings.MAPACHE_BIB_ENGINE)


def clear_bib_engine_cache() -> None:
    _load_engine.cache_clear()
