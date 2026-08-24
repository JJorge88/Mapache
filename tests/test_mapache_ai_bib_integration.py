import io
import os

import pytest
from PIL import Image, ImageDraw, ImageFont

from apps.mapache_ai.bib.tesseract import TesseractBibRecognitionEngine

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("MAPACHE_RUN_BIB_INTEGRATION") != "1",
    reason="Set MAPACHE_RUN_BIB_INTEGRATION=1 to run local Tesseract OCR.",
)
def test_tesseract_recognizes_small_synthetic_247():
    image = Image.new("RGB", (900, 420), "white")
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 220)
    ImageDraw.Draw(image).text((250, 80), "247", fill="black", font=font)
    payload = io.BytesIO()
    image.save(payload, format="JPEG", quality=90)

    detections = TesseractBibRecognitionEngine().recognize_bibs(
        payload.getvalue(), bib_format="NUMERIC"
    )
    assert any(item.raw_text == "247" for item in detections)
