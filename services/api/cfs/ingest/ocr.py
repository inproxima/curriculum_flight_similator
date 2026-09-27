"""OCR adapter. Uses the local Tesseract binary when available; otherwise reports `unavailable`.

Provider vision models are NOT used here (spec: targeted fallback only, Phase 5).
"""

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

OCR_VERSION = "tesseract-cli-1"


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_pdf_page(data: bytes, page_number: int, resolution: int = 200) -> str | None:
    if not ocr_available():
        return None
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        img = pdf.pages[page_number - 1].to_image(resolution=resolution)
        with tempfile.TemporaryDirectory() as d:
            png = Path(d) / "page.png"
            img.save(png)
            res = subprocess.run(
                ["tesseract", str(png), "stdout", "-l", "eng"], capture_output=True, text=True, timeout=120, check=False
            )
            return res.stdout if res.returncode == 0 else None
