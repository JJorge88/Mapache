import os

import pytest

from apps.mapache_ai.engines.opencv_sface import OpenCVSFaceEngine


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("MAPACHE_RUN_FACE_INTEGRATION") != "1",
    reason="Set MAPACHE_RUN_FACE_INTEGRATION=1 to load the real models.",
)
def test_real_opencv_sface_adapter_loads_configured_models():
    engine = OpenCVSFaceEngine()

    assert engine.embedding_dimension == 128
    assert engine.metric == "cosine"
