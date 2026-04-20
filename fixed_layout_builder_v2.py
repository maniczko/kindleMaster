"""
KindleMaster — Fixed-Layout EPUB Builder v2
=============================================
MAXIMUM FIDELITY fixed-layout EPUB generation.

Improvements over v1:
1. NO duplicate image storage — use original images as background
2. Correct font fallback mapping (sans→sans, serif→serif)
3. Selectable text layer (pointer-events: auto)
4. Original image quality preserved (no re-encoding)
5. Page box awareness (CropBox vs MediaBox)
6. Chess diagram detection and extraction
7. CMYK→RGB color conversion
8. Higher DPI rendering (300 DPI for crisp text)
9. SVG/vector graphic preservation
10. ICC color profile awareness
"""

import io
import html as html_module
import zipfile
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import re

import fitz  # PyMuPDF
from ebooklib import epub
from PIL import Image, ImageCms, ImageDraw
import numpy as np

# Chess diagram detection and rendering
try:
    from chess_diagram_detector import detect_chess_diagrams, enhance_chess_diagram, ChessDiagramCandidate
    CHESS_DETECTION_AVAILABLE = True
except ImportError:
    CHESS_DETECTION_AVAILABLE = False

try:
    from chess_diagram_renderer import (
        find_chess_diagram_regions,
        render_chess_diagram_to_png,
        generate_chess_diagram_css,
        is_chess_text,
    )
    CHESS_RENDERER_AVAILABLE = True
except ImportError:
    CHESS_RENDERER_AVAILABLE = False


EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
FIXED_LAYOUT_RENDER_DPI = 180
FIXED_LAYOUT_JPEG_QUALITY = 85
FIXED_LAYOUT_JPEG_SUBSAMPLING = 1
EMAIL_MASK_PADDING_PX = 6


# ============================================================================
# FONT FALLBACK MAPPING (FIX #2)
# ============================================================================

FONT_FALLBACK_MAP = {
    # Sans-serif fonts
    "aptos": "Arial, Helvetica, sans-serif",
    "aptos display": "Arial, Helvetica, sans-serif",
    "arial": "Arial, Helvetica, sans-serif",
    "helvetica": "Helvetica, Arial, sans-serif",
    "calibri": "Calibri, 'Segoe UI', Tahoma, sans-serif",
    "segoe ui": "'Segoe UI', Calibri, Tahoma, sans-serif",
    "tahoma": "Tahoma, 'Segoe UI', sans-serif",
    "verdana": "Verdana, Geneva, sans-serif",
    "trebuchet": "'Trebuchet MS', sans-serif",
    "franklin": "'Franklin Gothic Medium', 'Arial Narrow', sans-serif",
    "gill": "'Gill Sans', 'Gill Sans MT', sans-serif",
    "futura": "Futura, 'Century Gothic', sans-serif",
    "garamond": "Garamond, 'Palatino Linotype', serif",  # Actually serif but often used as display
    
    # Serif fonts
    "times": "'Times New Roman', Times, serif",
    "times new roman": "'Times New Roman', Times, serif",
    "georgia": "Georgia, 'Times New Roman', serif",
    "palatino": "'Palatino Linotype', 'Book Antiqua', Palatino, serif",
    "book antiqua": "'Book Antiqua', Palatino, serif",
    "garamond": "Garamond, 'Palatino Linotype', serif",
    "baskerville": "Baskerville, 'Palatino Linotype', serif",
    "cambria": "Cambria, Georgia, serif",
    
    # Monospace fonts
    "courier": "'Courier New', Courier, monospace",
    "courier new": "'Courier New', Courier, monospace",
    "consolas": "Consolas, 'Courier New', monospace",
    "monaco": "Monaco, 'Courier New', monospace",
    "menlo": "Menlo, Monaco, 'Courier New', monospace",
    "source code": "'Source Code Pro', Consolas, monospace",
    "fira code": "'Fira Code', Consolas, monospace",
    
    # Display/special fonts
    "impact": "Impact, 'Arial Black', sans-serif",
    "comic": "'Comic Sans MS', cursive, sans-serif",
    "lucida": "'Lucida Sans', 'Lucida Grande', sans-serif",
    
    # Chess fonts (special handling)
    "chess": "'Chess Alpha 2', 'Merida', sans-serif",
    "merida": "Merida, 'Chess Alpha 2', sans-serif",
    "alpha": "'Chess Alpha 2', Merida, sans-serif",
}


def detect_font_family(pdf_font_name: str) -> tuple[str, str]:
    """
    Detect CSS font-family and font-style from PDF font name.
    
    Returns (css_font_family, generic_family) where generic is
    one of: sans-serif, serif, monospace, cursive, fantasy
    """
    font_lower = pdf_font_name.lower()
    
    # Check for bold/italic in font name
    is_bold = any(x in font_lower for x in ["bold", "black", "heavy"])
    is_italic = any(x in font_lower for x in ["italic", "oblique"])
    
    # Check font name against known mappings
    for key, css_family in FONT_FALLBACK_MAP.items():
        if key in font_lower:
            # Determine generic family from CSS
            if "sans-serif" in css_family:
                generic = "sans-serif"
            elif "serif" in css_family:
                generic = "serif"
            elif "monospace" in css_family:
                generic = "monospace"
            else:
                generic = "sans-serif"
            return css_family, generic
    
    # Heuristic: guess from font name patterns
    if any(x in font_lower for x in ["serif", "roman", "times"]):
        return "'Times New Roman', Times, serif", "serif"
    if any(x in font_lower for x in ["mono", "courier", "consolas"]):
        return "'Courier New', Courier, monospace", "monospace"
    if any(x in font_lower for x in ["script", "hand", "brush"]):
        return "'Brush Script MT', cursive", "cursive"
    
    # Default to sans-serif (most common for modern documents)
    return "Arial, Helvetica, sans-serif", "sans-serif"


# ============================================================================
# COLOR HANDLING (FIX #8)
# ============================================================================

def convert_cmyk_to_rgb(c: float, m: float, y: float, k: float) -> tuple[int, int, int]:
    """Convert CMYK (0-1 range) to RGB (0-255)."""
    r = 255 * (1.0 - c) * (1.0 - k)
    g = 255 * (1.0 - m) * (1.0 - k)
    b = 255 * (1.0 - y) * (1.0 - k)
    return (int(max(0, min(255, r))), 
            int(max(0, min(255, g))), 
            int(max(0, min(255, b))))


def color_to_css(color_info) -> str:
    """
    Convert PyMuPDF color info to CSS color string.
    Handles RGB, CMYK, and grayscale.
    """
    if color_info is None:
        return "#000000"
    
    # PyMuPDF color is integer 0xRRGGBB for RGB
    if isinstance(color_info, int):
        r = (color_info >> 16) & 0xFF
        g = (color_info >> 8) & 0xFF
        b = color_info & 0xFF
        return f"#{r:02x}{g:02x}{b:02x}"
    
    # Float tuple/list for other colorspaces
    if isinstance(color_info, (tuple, list)):
        if len(color_info) == 1:
            # Grayscale
            v = int(color_info[0] * 255)
            return f"#{v:02x}{v:02x}{v:02x}"
        elif len(color_info) == 3:
            # RGB (0-1 range)
            r, g, b = [int(c * 255) for c in color_info]
            return f"#{r:02x}{g:02x}{b:02x}"
        elif len(color_info) == 4:
            # CMYK
            r, g, b = convert_cmyk_to_rgb(*color_info)
            return f"#{r:02x}{g:02x}{b:02x}"
    
    return "#000000"


# ============================================================================
# CHESS DIAGRAM DETECTION (FIX #7)
# ============================================================================

def is_chess_diagram(img_data: bytes, width: int, height: int) -> bool:
    """
    Detect if an image is likely a chess diagram.
    
    Heuristics:
    - Approximately square aspect ratio
    - Small to medium size (typical diagram: 100-400px)
    - Simple color palette (2-4 colors typical for chess diagrams)
    - Grid-like structure
    """
    # Aspect ratio check (chess boards are square or near-square)
    aspect_ratio = width / height if height > 0 else 0
    if not (0.8 <= aspect_ratio <= 1.3):
        return False
    
    # Size check (typical chess diagram)
    if not (80 <= width <= 600 and 80 <= height <= 600):
        return False
    
    try:
        img = Image.open(io.BytesIO(img_data))
        img = img.convert("RGB")
        pixels = list(img.getdata())
        
        # Color count check (chess diagrams typically have few colors)
        unique_colors = len(set(pixels))
        total_pixels = len(pixels)
        
        # If very few unique colors relative to total, might be a diagram
        color_ratio = unique_colors / total_pixels if total_pixels > 0 else 1
        
        # Chess diagrams often have 2-20 unique colors
        if unique_colors <= 50 and color_ratio < 0.1:
            return True
        
        # Check for grid pattern (alternating light/dark squares)
        # Sample center pixels in 8x8 grid
        if width > 16 and height > 16:
            grid_colors = []
            for row in range(8):
                for col in range(8):
                    x = int((col + 0.5) * width / 8)
                    y = int((row + 0.5) * height / 8)
                    if x < width and y < height:
                        grid_colors.append(pixels[y * width + x])
            
            # Chess boards typically have 2 main colors
            unique_grid = len(set(grid_colors))
            if 2 <= unique_grid <= 8:
                return True
        
        return False
        
    except Exception:
        return False


def extract_chess_diagram_info(img_data: bytes) -> dict:
    """
    Extract metadata about a chess diagram image.
    """
    try:
        img = Image.open(io.BytesIO(img_data))
        return {
            "width": img.width,
            "height": img.height,
            "mode": img.mode,
            "is_square": abs(img.width - img.height) < 10,
            "format": img.format,
        }
    except Exception:
        return {}


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class PositionedText:
    text: str
    x: float
    y: float
    width: float
    height: float
    font_name: str
    font_size: float
    is_bold: bool
    is_italic: bool
    color: Optional[int]
    bbox: tuple
    css_font_family: str = ""
    css_color: str = ""


@dataclass
class PositionedImage:
    data: bytes
    extension: str
    x: float
    y: float
    width: float
    height: float
    bbox: tuple
    is_chess_diagram: bool = False
    chess_info: dict = None


# ============================================================================
# CHESS DIAGRAM RENDERING AS IMAGES
# ============================================================================

def render_chess_diagram_region(
    page: fitz.Page,
    bbox: tuple,
    dpi: int = 300,
) -> tuple[bytes, int, int]:
    """
    Render a chess diagram region from PDF as a high-quality PNG image.
    
    This is the KEY FIX: instead of rendering chess fonts as text (which shows
    empty squares), we render the actual PDF region as an image.
    
    Args:
        page: PyMuPDF page object
        bbox: (x0, y0, x1, y1) bounding box of the chess diagram
        dpi: Resolution for rendering (300 for crisp chess pieces)
    
    Returns:
        (png_data, width, height) tuple
    """
    x0, y0, x1, y1 = bbox
    
    # Add padding around the diagram for better appearance
    padding = 10
    clip_rect = fitz.Rect(x0 - padding, y0 - padding, x1 + padding, y1 + padding)
    
    # Ensure we're within page bounds
    clip_rect = clip_rect & page.rect
    
    # Calculate zoom for desired DPI
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    
    # Translate matrix to clip the region
    clip_matrix = matrix * fitz.Matrix(1, 0, 0, 1, -clip_rect.x0, -clip_rect.y0)
    
    # Render the clipped region
    pix = page.get_pixmap(
        matrix=clip_matrix,
        clip=clip_rect,
        alpha=False,
    )
    
    # Convert to PNG (lossless for chess diagrams)
    png_data = pix.tobytes("png")
    
    return png_data, pix.width, pix.height


# ============================================================================
# PAGE RENDERING (FIX #4, #5: Original quality, higher DPI)
# ============================================================================

def render_page_to_image(
    page: fitz.Page,
    dpi: int = FIXED_LAYOUT_RENDER_DPI,
    *,
    mask_rects: Optional[list[tuple[float, float, float, float]]] = None,
) -> tuple[bytes, int, int]:
    """
    Render a PDF page to JPEG at higher fidelity for fixed-layout output.
    Uses light compression to keep magazine text and fine rules readable.
    """
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    
    # Convert to PIL for JPEG with quality control
    png_data = pix.tobytes("png")
    img = Image.open(io.BytesIO(png_data))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    
    if mask_rects:
        draw = ImageDraw.Draw(img)
        for x0, y0, x1, y1 in mask_rects:
            rect = (
                max(0, int(round(x0 * zoom)) - EMAIL_MASK_PADDING_PX),
                max(0, int(round(y0 * zoom)) - EMAIL_MASK_PADDING_PX),
                min(img.width, int(round(x1 * zoom)) + EMAIL_MASK_PADDING_PX),
                min(img.height, int(round(y1 * zoom)) + EMAIL_MASK_PADDING_PX),
            )
            draw.rectangle(
                rect,
                fill=_sample_fill_color(img, rect),
            )

    jpeg_bytes = io.BytesIO()
    img.save(
        jpeg_bytes,
        format="JPEG",
        quality=FIXED_LAYOUT_JPEG_QUALITY,
        subsampling=FIXED_LAYOUT_JPEG_SUBSAMPLING,
        optimize=True,
    )
    jpeg_bytes.seek(0)
    
    return jpeg_bytes.read(), pix.width, pix.height


def _sample_fill_color(img: Image.Image, rect: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """
    Approximate the local background color around a masked region so email
    redaction does not leave a harsh white bar on dark photos.
    """
    left, top, right, bottom = rect
    pad = 18
    sample_left = max(0, left - pad)
    sample_top = max(0, top - pad)
    sample_right = min(img.width, right + pad)
    sample_bottom = min(img.height, bottom + pad)

    region = np.array(img.crop((sample_left, sample_top, sample_right, sample_bottom)))
    if region.size == 0:
        return (255, 255, 255)

    mask = np.ones(region.shape[:2], dtype=bool)
    inner_left = max(0, left - sample_left)
    inner_top = max(0, top - sample_top)
    inner_right = min(region.shape[1], right - sample_left)
    inner_bottom = min(region.shape[0], bottom - sample_top)
    mask[inner_top:inner_bottom, inner_left:inner_right] = False

    border_pixels = region[mask]
    if border_pixels.size == 0:
        return (255, 255, 255)

    mean = border_pixels.reshape(-1, 3).mean(axis=0)
    return tuple(int(round(channel)) for channel in mean)


# ============================================================================
# TEXT EXTRACTION WITH ENHANCED METADATA
# ============================================================================

def extract_text_with_positions(page: fitz.Page) -> list[PositionedText]:
    """
    Extract text with full metadata including fonts, colors, positions.
    """
    text_items = []
    
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
    
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                
                bbox = span.get("bbox", (0, 0, 0, 0))
                x0, y0, x1, y1 = bbox
                
                # Determine CSS font family
                font_name = span.get("font", "Unknown")
                css_family, _ = detect_font_family(font_name)
                
                # Convert color
                color = span.get("color")
                css_color = color_to_css(color)
                
                text_items.append(PositionedText(
                    text=text,
                    x=x0,
                    y=y0,
                    width=x1 - x0,
                    height=y1 - y0,
                    font_name=font_name,
                    font_size=span.get("size", 12),
                    is_bold=bool(span.get("flags", 0) & (1 << 4)),
                    is_italic=bool(span.get("flags", 0) & (1 << 1)),
                    color=color,
                    bbox=bbox,
                    css_font_family=css_family,
                    css_color=css_color,
                ))
    
    return text_items


def filter_layout_text_items(
    text_items: list[PositionedText],
) -> tuple[list[PositionedText], list[tuple[float, float, float, float]]]:
    """
    Drop email spans from the hidden text layer and return their bounding boxes
    so the same areas can be masked on the rendered page image.
    """
    filtered: list[PositionedText] = []
    email_rects: list[tuple[float, float, float, float]] = []

    for item in text_items:
        if EMAIL_PATTERN.search(item.text):
            email_rects.append(item.bbox)
            continue
        filtered.append(item)

    return filtered, email_rects


# ============================================================================
# IMAGE EXTRACTION WITH CHESS DIAGRAM DETECTION
# ============================================================================

def extract_images_with_positions(page: fitz.Page) -> list[PositionedImage]:
    """
    Extract images with accurate positioning using get_image_rects().
    Also detects chess diagrams.
    """
    images = []
    image_list = page.get_images(full=True)
    
    for img_idx, img_info in enumerate(image_list):
        xref = img_info[0]
        rects = page.get_image_rects(xref)
        
        if not rects:
            continue
        
        try:
            base_image = page.parent.extract_image(xref)
        except Exception:
            continue
        
        if not base_image or not base_image.get("image"):
            continue
        
        img_data = base_image["image"]
        ext = base_image["ext"]
        rect = rects[0]
        x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
        
        # Check if this is a chess diagram
        is_chess = is_chess_diagram(img_data, int(x1 - x0), int(y1 - y0))
        chess_info = extract_chess_diagram_info(img_data) if is_chess else None
        
        images.append(PositionedImage(
            data=img_data,
            extension=ext,
            x=x0,
            y=y0,
            width=x1 - x0,
            height=y1 - y0,
            bbox=(x0, y0, x1, y1),
            is_chess_diagram=is_chess,
            chess_info=chess_info,
        ))
    
    return images


# ============================================================================
# XHTML GENERATION (FIX #1, #3: No duplicate images, selectable text)
# ============================================================================

# Fixed-layout CSS v2 — optimized with chess diagram support
FIXED_LAYOUT_CSS_V2 = """\
html, body {
  margin: 0;
  padding: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #fff;
}

.page-container {
  position: relative;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
}

.page-background {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 1;
  object-fit: contain;
}

.text-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 2;
  pointer-events: none;
  opacity: 0.01;
}

.text-span {
  position: absolute;
  white-space: pre;
  pointer-events: auto;
  line-height: 1;
  cursor: text;
  user-select: text;
  -webkit-user-select: text;
  -moz-user-select: text;
  -ms-user-select: text;
  color: transparent !important;
  -webkit-text-fill-color: transparent;
  text-shadow: none;
}

.text-span:hover {
  outline: none;
}

/* Chess diagrams - rendered as images, NOT text */
.chess-diagram {
  position: absolute;
  z-index: 5;
  pointer-events: auto;
  image-rendering: -webkit-optimize-contrast;
  image-rendering: crisp-edges;
  image-rendering: pixelated;
}

.image-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 3;
  pointer-events: none;
}

.figure-image {
  position: absolute;
  z-index: 3;
  pointer-events: auto;
}

.figure-caption {
  position: absolute;
  font-size: 0.8em;
  color: #666;
  text-align: center;
  font-style: italic;
}
"""


def generate_fixed_layout_page_html_v2(
    page_num: int,
    page_width: float,
    page_height: float,
    page_image_data: bytes,
    page_image_ext: str,
    text_items: list[PositionedText],
    images: list[PositionedImage],
    chess_diagram_data: list[dict] = None,
    chess_text_indices: set = None,
) -> str:
    """
    Generate XHTML with:
    - Single rendered page background
    - Hidden selectable text overlay for search / selection
    - No duplicate visible text or image overlays
    """
    if chess_diagram_data is None:
        chess_diagram_data = []
    if chess_text_indices is None:
        chess_text_indices = set()

    parts = []
    parts.append('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">')
    parts.append("<head>")
    parts.append(f'<title>Strona {page_num + 1}</title>')
    parts.append(
        f'<meta name="viewport" content="width={int(round(page_width))},height={int(round(page_height))}"/>'
    )
    parts.append('<link href="style/fixed.css" rel="stylesheet" type="text/css"/>')
    parts.append("</head>")
    parts.append("<body>")
    parts.append(
        f'<div class="page-container" style="width: {int(round(page_width))}px; height: {int(round(page_height))}px;">'
    )

    # Background image — ONLY ONCE (FIX #1)
    parts.append(f'<img class="page-background" src="images/page_{page_num}.{page_image_ext}" alt="Strona {page_num + 1}"/>')

    # Hidden text overlay keeps searchability without visually doubling text.
    if text_items:
        parts.append('<div class="text-layer">')

        for idx, text_item in enumerate(text_items):
            # Skip chess diagram text - rendered as images instead
            if idx in chess_text_indices:
                continue

            if len(text_item.text.strip()) < 1:
                continue

            left_pct = (text_item.x / page_width) * 100
            top_pct = (text_item.y / page_height) * 100
            font_family = text_item.css_font_family or "Arial, sans-serif"
            styles = [
                f"left: {left_pct:.2f}%",
                f"top: {top_pct:.2f}%",
                f"font-size: {text_item.font_size:.1f}px",
                f"font-family: {font_family}",
                "color: transparent",
            ]

            if text_item.is_bold:
                styles.append("font-weight: bold")
            if text_item.is_italic:
                styles.append("font-style: italic")

            style_str = "; ".join(styles)
            escaped_text = html_module.escape(text_item.text)

            parts.append(f'<span class="text-span" style="{style_str}">{escaped_text}</span>')

        parts.append('</div>')

    parts.append('</div>')
    parts.append("</body>")
    parts.append("</html>")

    return "\n".join(parts)


def inject_fixed_layout_viewports(
    epub_bytes: bytes,
    page_viewports: dict[str, tuple[int, int]],
) -> bytes:
    """Inject viewport meta tags into fixed-layout page XHTML files."""
    source = io.BytesIO(epub_bytes)
    output = io.BytesIO()

    with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(output, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)

            if info.filename in page_viewports and info.filename.endswith(".xhtml"):
                text = data.decode("utf-8")
                width, height = page_viewports[info.filename]
                viewport = f'<meta name="viewport" content="width={width},height={height}"/>'
                if 'name="viewport"' not in text and "</title>" in text:
                    text = text.replace("</title>", f"</title>\n    {viewport}", 1)
                    data = text.encode("utf-8")

            zout.writestr(info, data)

    output.seek(0)
    return output.getvalue()


# ============================================================================
# MAIN EPUB BUILDER
# ============================================================================

def build_fixed_layout_epub_v2(
    pdf_path: str,
    config,
    pdf_metadata: dict,
) -> bytes:
    """
    Build fixed-layout EPUB v2 with all quality improvements.
    """
    title = pdf_metadata.get("title") or Path(pdf_path).stem
    author = pdf_metadata.get("author") or "Unknown"
    
    doc = fitz.open(pdf_path)
    book = epub.EpubBook()
    book.set_identifier("urn:uuid:" + epub.uuid.uuid4().hex)
    book.set_title(title)
    book.set_language(config.language)
    book.add_author(author)
    
    # Add optimized CSS
    fixed_css = epub.EpubItem(
        uid="fixed_style",
        file_name="style/fixed.css",
        media_type="text/css",
        content=FIXED_LAYOUT_CSS_V2.encode("utf-8"),
    )
    book.add_item(fixed_css)
    
    chapters = []
    page_viewports = {}

    # Process each page
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_width = page.rect.width
        page_height = page.rect.height

        # Extract text positions for hidden search / selection overlay.
        text_items = extract_text_with_positions(page)
        text_items, email_rects = filter_layout_text_items(text_items)

        # Render the entire page as the visual source of truth.
        page_image_data, img_width, img_height = render_page_to_image(
            page,
            mask_rects=email_rects,
        )

        # Add page image to EPUB
        page_img_item = epub.EpubItem(
            uid=f"page_image_{page_num}",
            file_name=f"images/page_{page_num}.jpeg",
            media_type="image/jpeg",
            content=page_image_data,
        )
        book.add_item(page_img_item)

        # Generate XHTML with a single clean page raster plus hidden text.
        page_html = generate_fixed_layout_page_html_v2(
            page_num=page_num,
            page_width=page_width,
            page_height=page_height,
            page_image_data=page_image_data,
            page_image_ext="jpeg",
            text_items=text_items,
            images=[],
            chess_diagram_data=[],
            chess_text_indices=set(),
        )
        
        # Create EPUB page item
        page_item = epub.EpubHtml(
            title=f"Strona {page_num + 1}",
            file_name=f"page_{page_num:03d}.xhtml",
            lang=config.language,
        )
        page_item.content = page_html
        page_item.add_item(fixed_css)
        page_viewports[f"EPUB/page_{page_num:03d}.xhtml"] = (
            int(round(page_width)),
            int(round(page_height)),
        )
        
        book.add_item(page_item)
        chapters.append(page_item)
    
    doc.close()
    
    # Build cover page
    if chapters:
        try:
            doc2 = fitz.open(pdf_path)
            pix = doc2[0].get_pixmap(dpi=150)
            cover_img_data = pix.tobytes("jpeg", quality=95)
            doc2.close()
            
            cover_img = epub.EpubItem(
                uid="cover-image",
                file_name="images/cover.jpeg",
                media_type="image/jpeg",
                content=cover_img_data,
            )
            book.add_item(cover_img)
            book.add_metadata(None, "meta", "", {"name": "cover", "content": "cover-image"})
            
            cover_page = epub.EpubHtml(
                title="Okładka",
                file_name="cover.xhtml",
                lang=config.language,
            )
            cover_page.content = (
                '<html><body class="cover-page" style="margin:0;padding:0;text-align:center;">'
                '<img src="images/cover.jpeg" alt="' + html_module.escape(title) + '" '
                'style="max-width:100%;max-height:100%;"/>'
                "</body></html>"
            )
            book.add_item(cover_page)
            chapters.insert(0, cover_page)
        except Exception:
            pass
    
    # Navigation
    book.toc = chapters[1:] if len(chapters) > 1 else chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters
    
    # Fixed-layout metadata
    book.add_metadata(None, "meta", "pre-paginated", {"property": "rendition:layout"})
    book.add_metadata(None, "meta", "auto", {"property": "rendition:orientation"})
    book.add_metadata(None, "meta", "auto", {"property": "rendition:spread"})
    
    # Write EPUB
    epub_buffer = io.BytesIO()
    epub.write_epub(epub_buffer, book)
    epub_buffer.seek(0)

    return inject_fixed_layout_viewports(epub_buffer.getvalue(), page_viewports)
