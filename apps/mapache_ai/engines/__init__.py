from functools import lru_cache
from importlib import import_module

from django.conf import settings

from .base import FaceEngine


@lru_cache(maxsize=4)
def _load_engine(engine_name: str) -> FaceEngine:
    if engine_name == "opencv_sface":
        from .opencv_sface import OpenCVSFaceEngine

        return OpenCVSFaceEngine()
    if "." not in engine_name:
        raise ValueError(f"Motor facial desconocido: {engine_name}")
    module_name, class_name = engine_name.rsplit(".", 1)
    engine_class = getattr(import_module(module_name), class_name)
    return engine_class()


def get_face_engine() -> FaceEngine:
    return _load_engine(settings.MAPACHE_FACE_ENGINE)


def clear_face_engine_cache() -> None:
    _load_engine.cache_clear()
