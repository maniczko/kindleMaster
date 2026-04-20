"""
KindleMaster — Fixed-Layout EPUB Builder
=========================================
Generates fixed-layout EPUB that preserves exact PDF visual appearance.

Approach:
1. Render each PDF page to high-res image
2. Extract text with positions, fonts, colors
3. Generate XHTML with absolute positioning
4. Embed page images as background
5. Overlay positioned text spans

This gives 1:1 visual fidelity for scanned/image-heavy PDFs.
"""

import io
import html as html_module
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF
from ebooklib import epub
from PIL import Image


# Fixed-layout CSS for precise positioning
FIXED_LAYOUT_PAGE_CSS = """\
html, body {
  margin: 0;
  padding: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.page-container {
  position: relative;
  width: 100%;
  height: 100%;
}

.page-background {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 1;
}

.text-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 2;
  pointer-events: none;
}

.text-span {
  position: absolute;
  white-space: pre;
  pointer-events: auto;
  line-height: 1;
}

.image-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 3;
}

.figure-image {
  position: absolute;
  z-index: 3;
}
"""


@dataclass
class PositionedText:
    """A text span with position, font, color info."""
    text: str
    x: float  # Position from left (points)
    y: float  # Position from top (points)
    width: float
    height: float
    font_name: str
    font_size: float
    is_bold: bool
    is_italic: bool
    color: Optional[int]  # RGB integer (0xRRGGBB)
    bbox: tuple  # (x0, y0, x1, y2)


@dataclass
class PositionedImage:
    """An image with position info."""
    data: bytes
    extension: str
    x: float
    y: float
    width: float
    height: float
    bbox: tuple


def render_page_to_image(page: fitz.Page, dpi: int = 200) -> tuple[bytes, int, int]:
    """
    Render a PDF page to JPEG image bytes.
    Returns (image_data, width, height).
    """
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    
    # Convert to PIL for JPEG with quality control
    img_data = pix.tobytes("png")  # PyMuPDF supports png
    # Convert PNG to JPEG using PIL
    from PIL import Image
    img = Image.open(io.BytesIO(img_data))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    jpeg_bytes = io.BytesIO()
    img.save(jpeg_bytes, format="JPEG", quality=92)
    jpeg_bytes.seek(0)
    
    return jpeg_bytes.read(), pix.width, pix.height


def extract_text_with_positions(page: fitz.Page) -> list[PositionedText]:
    """
    Extract text spans with their exact positions, fonts, and colors.
    """
    text_items = []
    
    # Get text with "dict" flags for full detail
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
    
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # Text block
            continue
        
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                
                bbox = span.get("bbox", (0, 0, 0, 0))
                x0, y0, x1, y1 = bbox
                
                text_items.append(PositionedText(
                    text=text,
                    x=x0,
                    y=y0,
                    width=x1 - x0,
                    height=y1 - y0,
                    font_name=span.get("font", "Unknown"),
                    font_size=span.get("size", 12),
                    is_bold=bool(span.get("flags", 0) & (1 << 4)),
                    is_italic=bool(span.get("flags", 0) & (1 << 1)),
                    color=span.get("color"),
                    bbox=bbox,
                ))
    
    return text_items


def extract_images_with_positions(page: fitz.Page) -> list[PositionedImage]:
    """
    Extract images with their positions on the page.
    Uses get_image_rects() for accurate positioning.
    """
    images = []
    page_width = page.rect.width
    page_height = page.rect.height
    
    # Get images with their rectangles on the page
    image_list = page.get_images(full=True)
    
    for img_idx, img_info in enumerate(image_list):
        xref = img_info[0]
        
        # Get the rectangles where this image appears
        rects = page.get_image_rects(xref)
        
        if not rects:
            # No position info - skip or use full page
            continue
        
        # Get image data
        try:
            base_image = page.parent.extract_image(xref)
        except Exception:
            continue
        
        if not base_image or not base_image.get("image"):
            continue
        
        # Use first rect (images can appear multiple times)
        rect = rects[0]
        x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
        
        images.append(PositionedImage(
            data=base_image["image"],
            extension=base_image["ext"],
            x=x0,
            y=y0,
            width=x1 - x0,
            height=y1 - y0,
            bbox=(x0, y0, x1, y1),
        ))
    
    return images


def color_int_to_css(color_int: Optional[int]) -> str:
    """Convert PDF color integer to CSS color string."""
    if color_int is None:
        return "#000000"
    
    # PDF color is 0xRRGGBB
    r = (color_int >> 16) & 0xFF
    g = (color_int >> 8) & 0xFF
    b = color_int & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


def font_fallback_map(pdf_font_name: str) -> str:
    """Map PDF font names to web-safe CSS font families."""
    font_lower = pdf_font_name.lower()
    
    # Common font mappings
    if any(x in font_lower for x in ["aptos", "arial", "helvetica"]):
        return "Arial, Helvetica, sans-serif"
    if any(x in font_lower for x in ["times", "georgia", "palatino"]):
        return "Georgia, 'Times New Roman', serif"
    if any(x in font_lower for x in ["courier", "mono"]):
        return "'Courier New', Courier, monospace"
    if any(x in font_lower for x in ["calibri"]):
        return "Calibri, 'Segoe UI', sans-serif"
    if any(x in font_lower for x in ["verdana"]):
        return "Verdana, Geneva, sans-serif"
    if any(x in font_lower for x in ["trebuchet"]):
        return "'Trebuchet MS', sans-serif"
    
    # Default fallback
    return "Georgia, 'Times New Roman', serif"


def generate_fixed_layout_page_html(
    page_num: int,
    page_width: float,
    page_height: float,
    page_image_data: bytes,
    text_items: list[PositionedText],
    images: list[PositionedImage],
) -> str:
    """
    Generate XHTML for a single page with absolute positioning.
    """
    parts = []
    parts.append('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">')
    parts.append("<head>")
    parts.append(f'<title>Strona {page_num + 1}</title>')
    parts.append('<link href="../style/fixed.css" rel="stylesheet" type="text/css"/>')
    parts.append("</head>")
    parts.append("<body>")
    parts.append('<div class="page-container">')
    
    # Background image (rendered page)
    parts.append(f'<img class="page-background" src="../images/page_{page_num}.jpeg" alt="Strona {page_num + 1}"/>')
    
    # Text overlay layer
    if text_items:
        parts.append('<div class="text-layer">')
        
        for text_item in text_items:
            # Skip very short or whitespace-only text
            if len(text_item.text.strip()) < 1:
                continue
            
            # Calculate CSS positioning (percentage-based for responsiveness)
            left_pct = (text_item.x / page_width) * 100
            top_pct = (text_item.y / page_height) * 100
            font_family = font_fallback_map(text_item.font_name)
            color_css = color_int_to_css(text_item.color)
            
            # Build style attribute
            styles = [
                f"left: {left_pct:.2f}%",
                f"top: {top_pct:.2f}%",
                f"font-size: {text_item.font_size:.1f}px",
                f"font-family: {font_family}",
                f"color: {color_css}",
            ]
            
            if text_item.is_bold:
                styles.append("font-weight: bold")
            if text_item.is_italic:
                styles.append("font-style: italic")
            
            style_str = "; ".join(styles)
            escaped_text = html_module.escape(text_item.text)
            
            parts.append(f'<span class="text-span" style="{style_str}">{escaped_text}</span>')
        
        parts.append('</div>')
    
    # Image overlays
    for idx, img in enumerate(images):
        left_pct = (img.x / page_width) * 100
        top_pct = (img.y / page_height) * 100
        width_pct = (img.width / page_width) * 100
        
        parts.append(
            f'<img class="figure-image" '
            f'src="../images/img_p{page_num}_{idx}.{img.extension}" '
            f'alt="" '
            f'style="left: {left_pct:.2f}%; top: {top_pct:.2f}%; width: {width_pct:.2f}%"/>'
        )
    
    parts.append('</div>')
    parts.append("</body>")
    parts.append("</html>")
    
    return "\n".join(parts)


def build_fixed_layout_epub(
    pdf_path: str,
    config,
    pdf_metadata: dict,
) -> bytes:
    """
    Build a fixed-layout EPUB that preserves PDF visual appearance.
    
    Each page becomes an EPUB page with:
    - Background image of the rendered PDF page
    - Overlay of positioned text spans
    - Overlay of positioned images
    """
    title = pdf_metadata.get("title") or Path(pdf_path).stem
    author = pdf_metadata.get("author") or "Unknown"
    
    doc = fitz.open(pdf_path)
    book = epub.EpubBook()
    book.set_identifier("urn:uuid:" + epub.uuid.uuid4().hex)
    book.set_title(title)
    book.set_language(config.language)
    book.add_author(author)
    
    # Add fixed-layout CSS
    fixed_css = epub.EpubItem(
        uid="fixed_style",
        file_name="style/fixed.css",
        media_type="text/css",
        content=FIXED_LAYOUT_PAGE_CSS.encode("utf-8"),
    )
    book.add_item(fixed_css)
    
    # Also add default typography CSS for fallback
    from converter import EPUB_CSS
    default_css = epub.EpubItem(
        uid="default_style",
        file_name="style/default.css",
        media_type="text/css",
        content=EPUB_CSS.encode("utf-8"),
    )
    book.add_item(default_css)
    
    chapters = []
    page_items_for_toc = []
    
    # Process each page
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_width = page.rect.width
        page_height = page.rect.height
        
        # Render page to image
        page_image_data, img_width, img_height = render_page_to_image(page, dpi=200)
        
        # Add page image to EPUB
        page_img_item = epub.EpubItem(
            uid=f"page_image_{page_num}",
            file_name=f"images/page_{page_num}.jpeg",
            media_type="image/jpeg",
            content=page_image_data,
        )
        book.add_item(page_img_item)
        
        # Extract text with positions
        text_items = extract_text_with_positions(page)
        
        # Extract images with positions
        positioned_images = extract_images_with_positions(page)
        
        # Add positioned images to EPUB
        for idx, pos_img in enumerate(positioned_images):
            img_item = epub.EpubItem(
                uid=f"page_img_{page_num}_{idx}",
                file_name=f"images/img_p{page_num}_{idx}.{pos_img.extension}",
                media_type=f"image/{pos_img.extension}",
                content=pos_img.data,
            )
            book.add_item(img_item)
        
        # Generate XHTML for this page
        page_html = generate_fixed_layout_page_html(
            page_num=page_num,
            page_width=page_width,
            page_height=page_height,
            page_image_data=page_image_data,
            text_items=text_items,
            images=positioned_images,
        )
        
        # Create EPUB page item
        page_item = epub.EpubHtml(
            title=f"Strona {page_num + 1}",
            file_name=f"page_{page_num:03d}.xhtml",
            lang=config.language,
        )
        page_item.content = page_html
        page_item.add_item(fixed_css)
        
        book.add_item(page_item)
        chapters.append(page_item)
        
        # Check for TOC entry
        toc = doc.get_toc()
        for item in toc:
            if item[-1] == page_num + 1:
                page_items_for_toc.append((item[1], page_item))
                break
    
    doc.close()
    
    # Build cover page
    if chapters:
        first_page_img = None
        # Try to get first page image
        try:
            doc2 = fitz.open(pdf_path)
            pix = doc2[0].get_pixmap(dpi=150)
            first_page_img = pix.tobytes("jpeg", quality=90)
            doc2.close()
        except Exception:
            pass
        
        if first_page_img:
            cover_img = epub.EpubItem(
                uid="cover-image",
                file_name="images/cover.jpeg",
                media_type="image/jpeg",
                content=first_page_img,
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
    
    # Navigation
    book.toc = chapters[1:] if len(chapters) > 1 else chapters  # Exclude cover from TOC
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters

    # Fixed-layout metadata (EPUB 3 property attributes)
    book.add_metadata(None, "meta", "pre-paginated", {"property": "rendition:layout"})
    book.add_metadata(None, "meta", "auto", {"property": "rendition:orientation"})
    book.add_metadata(None, "meta", "auto", {"property": "rendition:spread"})
    
    # Write EPUB
    epub_buffer = io.BytesIO()
    epub.write_epub(epub_buffer, book)
    epub_buffer.seek(0)
    
    return epub_buffer.getvalue()
