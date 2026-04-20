"""
KindleMaster — OCR Module
=========================
OCR extraction for scanned PDFs using multiple engines with fallbacks.

Priority order:
1. OCRmyPDF + Tesseract - best full-document fallback for scanned PDFs
2. Tesseract OCR (via pytesseract) - best page-level fallback for Polish
3. EasyOCR - PyTorch-based, good accuracy, no system deps
4. PyMuPDF page rendering + text heuristics - last resort
"""

import io
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from PIL import Image, ImageOps
import fitz  # PyMuPDF
from premium_tools import (
    detect_toolchain,
    find_ocrmypdf_executable,
    find_qpdf_executable,
    find_ghostscript_executable,
    find_tesseract_executable,
    find_tessdata_dir,
    list_tesseract_languages,
)


@dataclass
class OCRPageResult:
    """Result of OCR processing for a single page."""
    page_num: int
    text: str  # Extracted text
    confidence: float  # 0.0 to 1.0
    image_data: bytes  # Rendered page image (for embedding in EPUB)
    image_width: int
    image_height: int
    words: list = None  # Optional: word-level bounding boxes


@dataclass
class OCRResult:
    """Result of full PDF OCR processing."""
    pages: list  # List of OCRPageResult
    engine_used: str  # "tesseract", "easyocr", "pymupdf_fallback"
    total_pages: int
    success_rate: float  # 0.0 to 1.0


def get_best_available_engine() -> str:
    if check_tesseract_available():
        return "tesseract"
    if check_easyocr_available():
        return "easyocr"
    return "none"


def check_tesseract_available() -> bool:
    """Check if Tesseract OCR is installed on the system."""
    try:
        import pytesseract
        configure_tesseract(pytesseract)
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def check_ocrmypdf_ready() -> bool:
    toolchain = detect_toolchain()
    return bool((toolchain.get("ocrmypdf") or {}).get("ready"))


def configure_tesseract(pytesseract_module) -> tuple[Optional[str], Optional[str]]:
    """Point pytesseract at a known executable and local tessdata when available."""
    tesseract_path = find_tesseract_executable()
    tessdata_dir = find_tessdata_dir()
    if tesseract_path:
        pytesseract_module.pytesseract.tesseract_cmd = str(tesseract_path)
    if tessdata_dir:
        os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
    return str(tesseract_path) if tesseract_path else None, str(tessdata_dir) if tessdata_dir else None


def get_available_tesseract_languages() -> list[str]:
    try:
        tesseract_path = find_tesseract_executable()
        tessdata_dir = find_tessdata_dir()
        languages = list_tesseract_languages(tesseract_path, tessdata_dir)
        return sorted({lang.strip() for lang in languages if lang.strip()})
    except Exception:
        return []


def resolve_ocr_language(requested_language: str) -> str:
    requested = (requested_language or "eng").strip().lower()
    available = set(get_available_tesseract_languages())
    if "+" in requested:
        requested_parts = [part for part in requested.split("+") if part]
        available_parts = [part for part in requested_parts if part in available]
        if available_parts:
            return "+".join(available_parts)
    fallback_map = {
        "pl": ("pol+eng", "pol", "eng"),
        "pol": ("pol+eng", "pol", "eng"),
        "en": ("eng",),
        "eng": ("eng",),
    }
    for candidate in fallback_map.get(requested, (requested, "eng")):
        if "+" in candidate:
            parts = [part for part in candidate.split("+") if part]
            if parts and all(part in available for part in parts):
                return candidate
        elif candidate in available:
            return candidate
    return "eng"


def _build_ocrmypdf_env() -> dict[str, str]:
    env = os.environ.copy()
    tesseract_path = find_tesseract_executable()
    tessdata_dir = find_tessdata_dir()
    qpdf_path = find_qpdf_executable()
    ghostscript_path = find_ghostscript_executable()

    path_entries = []
    for tool_path in (tesseract_path, qpdf_path, ghostscript_path):
        if tool_path:
            path_entries.append(str(Path(tool_path).resolve().parent))
    if path_entries:
        env["PATH"] = os.pathsep.join(path_entries + [env.get("PATH", "")])
    selected_tessdata = None
    candidate_dirs: list[Path] = []
    if tessdata_dir:
        candidate_dirs.append(Path(tessdata_dir))
    if tesseract_path:
        candidate_dirs.append(Path(tesseract_path).resolve().parent / "tessdata")
    for candidate in candidate_dirs:
        if not candidate.exists():
            continue
        if (candidate / "configs" / "hocr").exists() and (candidate / "configs" / "txt").exists():
            selected_tessdata = candidate
            break
    if selected_tessdata:
        env["TESSDATA_PREFIX"] = str(selected_tessdata)
    return env


def ocr_pdf_with_ocrmypdf(pdf_path: str, language: str = "pol") -> Optional[Path]:
    if not check_ocrmypdf_ready():
        return None

    ocrmypdf_path = find_ocrmypdf_executable()
    if not ocrmypdf_path:
        return None

    output_dir = Path(tempfile.mkdtemp(prefix="kindlemaster-ocrmypdf-"))
    output_pdf = output_dir / "ocr_output.pdf"
    resolved_language = resolve_ocr_language(language)
    command = [
        str(ocrmypdf_path),
        "--skip-text",
        "--rotate-pages",
        "--deskew",
        "--jobs",
        "1",
        "--language",
        resolved_language,
        pdf_path,
        str(output_pdf),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
        env=_build_ocrmypdf_env(),
    )
    if completed.returncode != 0 or not output_pdf.exists():
        return None
    return output_pdf


def _ocr_result_from_pdf_text(pdf_path: str, *, engine_used: str, dpi: int = 300) -> OCRResult:
    doc = fitz.open(pdf_path)
    pages: list[OCRPageResult] = []
    recognized_pages = 0

    try:
        for page_index, page in enumerate(doc):
            text = (page.get_text("text", sort=True) or "").replace("\r\n", "\n").replace("\r", "\n")
            text = text.replace("-\n", "")
            text = "\n".join(line.rstrip() for line in text.splitlines())
            image = render_page_to_image(page, dpi=dpi)
            image_buffer = io.BytesIO()
            image.convert("RGB").save(image_buffer, format="JPEG", quality=85, optimize=True)
            confidence = 0.84 if len(text.strip()) >= 80 else 0.58 if text.strip() else 0.0
            if text.strip():
                recognized_pages += 1
            pages.append(
                OCRPageResult(
                    page_num=page_index,
                    text=text,
                    confidence=confidence,
                    image_data=image_buffer.getvalue(),
                    image_width=image.width,
                    image_height=image.height,
                )
            )
    finally:
        doc.close()

    total_pages = len(pages)
    return OCRResult(
        pages=pages,
        engine_used=engine_used,
        total_pages=total_pages,
        success_rate=(recognized_pages / total_pages) if total_pages else 0.0,
    )


def check_easyocr_available() -> bool:
    """Check if EasyOCR is installed and working."""
    try:
        import easyocr
        # Quick instantiation check (don't load models yet)
        return True
    except ImportError:
        return False


def render_page_to_image(page: fitz.Page, dpi: int = 200) -> Image.Image:
    """
    Render a PDF page to a PIL Image.
    
    Args:
        page: PyMuPDF page object
        dpi: Resolution for rendering (higher = better OCR but slower)
    
    Returns:
        PIL Image object
    """
    # Calculate zoom factor for desired DPI (72 DPI is default)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    
    # Render page to pixmap
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    
    # Convert to PIL Image
    img_data = pix.tobytes("png")
    return Image.open(io.BytesIO(img_data))


def _prepare_image_for_ocr(img: Image.Image) -> Image.Image:
    """Lightweight OCR preprocessing that stays generic across publications."""
    prepared = img.convert("L")
    prepared = ImageOps.autocontrast(prepared)
    return prepared


def _tesseract_config_string(tessdata_dir: Optional[str], *, psm: int = 4) -> str:
    parts = []
    if tessdata_dir:
        parts.extend(["--tessdata-dir", tessdata_dir])
    parts.extend(["--oem", "1", "--psm", str(psm), "-c", "preserve_interword_spaces=1"])
    return " ".join(parts)


def ocr_with_tesseract(img: Image.Image, language: str = "pol") -> tuple[str, float]:
    """
    Perform OCR using Tesseract.
    
    Returns:
        (text, confidence) tuple
    """
    import pytesseract
    _tesseract_path, tessdata_dir = configure_tesseract(pytesseract)
    resolved_language = resolve_ocr_language(language)
    prepared = _prepare_image_for_ocr(img)
    config = _tesseract_config_string(tessdata_dir)
    
    # Get detailed OCR output for confidence calculation
    try:
        text = pytesseract.image_to_string(
            prepared,
            lang=resolved_language,
            config=config,
        )
        data = pytesseract.image_to_data(
            prepared,
            lang=resolved_language,
            config=config,
            output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractError:
        fallback_language = "eng" if resolved_language != "eng" else resolved_language
        text = pytesseract.image_to_string(
            prepared,
            lang=fallback_language,
            config=config,
        )
        data = pytesseract.image_to_data(
            prepared,
            lang=fallback_language,
            config=config,
            output_type=pytesseract.Output.DICT,
        )
    
    # Filter out low-confidence words
    words = []
    confidences = []
    for i, word in enumerate(data["text"]):
        raw_conf = str(data["conf"][i]).strip()
        try:
            conf = int(float(raw_conf))
        except Exception:
            conf = -1
        if conf > 30 and word.strip():  # Tesseract confidence 0-100
            words.append(word)
            confidences.append(conf / 100.0)
    
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("-\n", "")
    text = "\n".join(line.rstrip() for line in text.splitlines())
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    
    return text, avg_confidence


def ocr_with_easyocr(img: Image.Image, language: str = "pl") -> tuple[str, float]:
    """
    Perform OCR using EasyOCR.
    
    Returns:
        (text, confidence) tuple
    """
    import easyocr
    
    # Initialize reader (this loads models - do it once per session ideally)
    reader = easyocr.Reader([language], gpu=False, verbose=False)
    
    # Convert PIL to numpy array
    import numpy as np
    img_array = np.array(img)
    
    # Perform OCR
    results = reader.readtext(img_array, paragraph=True)
    
    # Extract text and confidence
    texts = []
    confidences = []
    for (bbox, text, conf) in results:
        if conf > 0.3:
            texts.append(text)
            confidences.append(conf)
    
    full_text = "\n".join(texts)
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    
    return full_text, avg_confidence


def run_ocr_on_page(
    page: fitz.Page,
    *,
    page_num: int,
    language: str = "pol",
    dpi: int = 200,
    engine: Optional[str] = None,
) -> OCRPageResult:
    """OCR a single page and return text plus a rendered page image."""
    selected_engine = engine or get_best_available_engine()
    img = render_page_to_image(page, dpi=dpi)

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format="JPEG", quality=88)
    img_data = img_byte_arr.getvalue()

    if selected_engine == "tesseract":
        text, confidence = ocr_with_tesseract(img, language)
    elif selected_engine == "easyocr":
        text, confidence = ocr_with_easyocr(img, language)
    else:
        text, confidence = "", 0.0

    return OCRPageResult(
        page_num=page_num,
        text=text,
        confidence=confidence,
        image_data=img_data,
        image_width=img.width,
        image_height=img.height,
    )


def run_ocr_on_pdf(pdf_path: str, language: str = "pol", dpi: int = 300) -> OCRResult:
    """
    Main OCR entry point.
    
    Processes all pages in the PDF using the best available OCR engine.
    
    Args:
        pdf_path: Path to the PDF file
        language: OCR language code (pol, eng, etc.)
        dpi: Resolution for page rendering
    
    Returns:
        OCRResult with all pages processed
    """
    ocrmypdf_output = ocr_pdf_with_ocrmypdf(pdf_path, language=language)
    if ocrmypdf_output is not None:
        return _ocr_result_from_pdf_text(str(ocrmypdf_output), engine_used="ocrmypdf", dpi=dpi)

    doc = fitz.open(pdf_path)
    pages_results = []

    engine = get_best_available_engine()
    total_confidence = 0.0

    for page_num in range(len(doc)):
        page = doc[page_num]

        page_result = run_ocr_on_page(
            page,
            page_num=page_num,
            language=language,
            dpi=dpi,
            engine=engine,
        )
        pages_results.append(page_result)
        total_confidence += page_result.confidence

    doc.close()

    avg_success = total_confidence / len(pages_results) if pages_results else 0.0

    return OCRResult(
        pages=pages_results,
        engine_used=engine,
        total_pages=len(pages_results),
        success_rate=avg_success,
    )


def install_ocr_instructions() -> str:
    """Return instructions for installing OCR engines."""
    return """
=== OCR Engine Installation Instructions ===

OPTION 1: Tesseract OCR (Recommended for Polish)
-------------------------------------------------
Windows:
1. Download installer: https://github.com/UB-Mannheim/tesseract/wiki
2. Run installer (choose Polish language data during install)
3. Add to PATH: C:\\Program Files\\Tesseract-OCR
4. Install Python wrapper:
   .venv\\Scripts\\activate
   pip install pytesseract

Mac:
   brew install tesseract tesseract-lang
   pip install pytesseract

Linux (Ubuntu/Debian):
   sudo apt install tesseract-ocr tesseract-ocr-pol
   pip install pytesseract

Verify installation:
   tesseract --version
   python -c "import pytesseract; print(pytesseract.get_tesseract_version())"


OPTION 2: EasyOCR (Python-only, no system dependencies)
-------------------------------------------------------
.venv\\Scripts\\activate
pip install easyocr

Note: EasyOCR uses PyTorch and may require 1-2 GB disk space for models.
First run will download language models automatically.


OPTION 3: No OCR (fallback mode)
---------------------------------
If no OCR engine is available, the converter will extract only 
selectable text from the PDF. Scanned/image-based text will be lost.
Quality will be significantly reduced for scanned PDFs.
"""
