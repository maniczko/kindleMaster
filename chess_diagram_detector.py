"""
KindleMaster — Chess Diagram Detection & Extraction Module
===========================================================
Detects chess diagrams in PDFs and extracts them as high-quality images.

Detection methods (combined):
1. Image-based: 8x8 grid pattern detection, square aspect ratio
2. Font-based: Chess font detection (Merida, Alpha, Leipzig, etc.)
3. Text-based: Chess notation patterns (FEN, algebraic, "diagram", "mate in")
4. Layout-based: Regular grid structures, typical chess diagram dimensions

Each detected diagram is:
- Extracted at maximum quality (PNG lossless or original format)
- Embedded in EPUB as a proper image asset
- Wrapped with caption/preserving context
- Never rendered as text/glyphs
"""

import io
import re
import math
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageFilter, ImageEnhance


# ============================================================================
# CHESS FONT DETECTION
# ============================================================================

CHESS_FONT_PATTERNS = [
    "chess", "merida", "leipzig", "casablanca", "skak", "alpha",
    "chesspieces", "chess alpha", "merida chess", "leipzig chess",
    "cbases", "chessbase", "fantasy chess", "maya chess",
]

CHESS_UNICODE_RANGES = [
    # Standard chess symbols
    (0x2654, 0x265F),  # ♔♕♖♗♘♙♚♛♜♝♞♟
    # Regional indicators sometimes used
    (0x1FA00, 0x1FA6F),  # Chess symbols extended
]


def is_chess_font(font_name: str) -> bool:
    """Check if a font name indicates a chess font."""
    font_lower = font_name.lower()
    return any(pattern in font_lower for pattern in CHESS_FONT_PATTERNS)


def text_contains_chess_unicode(text: str) -> bool:
    """Check if text contains chess Unicode characters."""
    for char in text:
        code = ord(char)
        for start, end in CHESS_UNICODE_RANGES:
            if start <= code <= end:
                return True
    return False


# ============================================================================
# CHESS NOTATION PATTERNS
# ============================================================================

CHESS_TEXT_PATTERNS = [
    # Algebraic notation
    r'\b[KQRBN]?[a-h][1-8][+#x]?\b',  # e4, Nf3, Qh5+, Bxf7#, etc.
    r'\b[0-9]+\.\s*[KQRBN]?[a-h][1-8]',  # 1. e4, 12. Nf3
    r'\b0-0-0\b|\b0-0\b',  # Castling
    
    # FEN-like patterns
    r'[rnbqkpRNBQKP1-8]{8}/[rnbqkpRNBQKP1-8/]{6,}',
    
    # Diagram labels
    r'\b(diagram|dia\.?|position|stellung)\s*\d*\b',
    r'\b(white|black|białe|czarne)\s+(to\s+move|move|wykonuje\s+ruch)\b',
    r'\bmate\s+in\s+\d+\b',
    r'\bmat\s+w\s+\d+\s+(ruch|posuni|move)\b',
    r'\b(solution|rozwiązanie|Lösung)\b',
    r'\b(white\s+wins|black\s+wins|białe\s+wygrywają|czarne\s+wygrywają)\b',
    
    # Chess-specific terms
    r'\b(en\s+passant|rokada|promocja|pat|remis)\b',
    r'\b(checkmate|szach-mat|pat|remis)\b',
    
    # Piece names in various languages
    r'\b(king|queen|rook|bishop|knight|pawn)\b',
    r'\b(król|hetman|wieża|goniec|skoczek|pion)\b',
]


def text_contains_chess_notation(text: str) -> tuple[bool, list[str]]:
    """
    Check if text contains chess notation patterns.
    Returns (is_chess_related, matched_patterns).
    """
    matches = []
    for pattern in CHESS_TEXT_PATTERNS:
        found = re.findall(pattern, text, re.IGNORECASE)
        if found:
            matches.extend(found[:3])  # Limit to first 3 matches per pattern
    
    # Require multiple matches for confidence
    is_chess = len(matches) >= 2
    return is_chess, matches


# ============================================================================
# IMAGE-BASED CHESS BOARD DETECTION
# ============================================================================

@dataclass
class ChessDiagramCandidate:
    """A detected chess diagram candidate."""
    page_num: int
    bbox: tuple  # (x0, y0, x1, y1)
    width: float
    height: float
    aspect_ratio: float
    detection_method: str  # "image", "font", "text", "combined"
    confidence: float  # 0.0 to 1.0
    image_data: Optional[bytes] = None
    image_format: str = ""
    caption_text: str = ""
    surrounding_text: str = ""
    requires_review: bool = False


def is_square_enough(aspect_ratio: float, tolerance: float = 0.15) -> bool:
    """Check if aspect ratio is close enough to 1.0 (square)."""
    return abs(aspect_ratio - 1.0) <= tolerance


def detect_grid_pattern(image_data: bytes, grid_size: int = 8) -> tuple[bool, float]:
    """
    Detect if an image contains a regular grid pattern (like chess board).
    
    Returns (is_grid, confidence).
    """
    try:
        img = Image.open(io.BytesIO(image_data)).convert("L")  # Grayscale
        pixels = np.array(img)
        height, width = pixels.shape
        
        if width < 32 or height < 32:
            return False, 0.0
        
        # Sample grid points
        cell_w = width / grid_size
        cell_h = height / grid_size
        
        # Sample center of each cell
        cell_centers = []
        for row in range(grid_size):
            for col in range(grid_size):
                cx = int((col + 0.5) * cell_w)
                cy = int((row + 0.5) * cell_h)
                if cx < width and cy < height:
                    cell_centers.append(pixels[cy, cx])
        
        if len(cell_centers) < grid_size * grid_size:
            return False, 0.0
        
        # Check for alternating pattern (light/dark squares)
        # In a chess board, adjacent cells should have different brightness
        cell_array = np.array(cell_centers).reshape(grid_size, grid_size)
        
        # Calculate variance within "light" vs "dark" groups
        light_squares = []
        dark_squares = []
        for row in range(grid_size):
            for col in range(grid_size):
                if (row + col) % 2 == 0:
                    light_squares.append(cell_array[row, col])
                else:
                    dark_squares.append(cell_array[row, col])
        
        if not light_squares or not dark_squares:
            return False, 0.0
        
        light_mean = np.mean(light_squares)
        dark_mean = np.mean(dark_squares)
        contrast = abs(light_mean - dark_mean)
        
        # High contrast between alternating squares = likely chess board
        # Typical chess board: light squares ~200-255, dark squares ~50-150
        contrast_ratio = contrast / 255.0
        
        # Also check consistency within each group
        light_std = np.std(light_squares) if len(light_squares) > 1 else 0
        dark_std = np.std(dark_squares) if len(dark_squares) > 1 else 0
        
        # Good chess board: high contrast, low variance within groups
        confidence = 0.0
        if contrast_ratio > 0.3:  # At least 30% contrast
            confidence += 0.5
        if light_std < 50 and dark_std < 50:  # Consistent colors
            confidence += 0.3
        if contrast_ratio > 0.5:  # Strong contrast
            confidence += 0.2
        
        is_grid = confidence >= 0.6
        return is_grid, min(confidence, 1.0)
        
    except Exception:
        return False, 0.0


def detect_chess_board_in_image(
    image_data: bytes,
    width: float,
    height: float,
) -> tuple[bool, float]:
    """
    Combined detection: aspect ratio + grid pattern + size heuristics.
    
    Returns (is_chess_board, confidence).
    """
    aspect = width / height if height > 0 else 0
    
    # Quick reject: not square-ish
    if not is_square_enough(aspect, tolerance=0.2):
        return False, 0.0
    
    # Size check: typical chess diagram
    # Too small = probably not a diagram
    # Too large = probably something else
    min_size = 60
    max_size = 800
    if not (min_size <= width <= max_size and min_size <= height <= max_size):
        return False, 0.0
    
    # Grid pattern detection
    is_grid, grid_confidence = detect_grid_pattern(image_data)
    
    # Color count heuristic (chess boards have few distinct colors)
    try:
        img = Image.open(io.BytesIO(image_data)).convert("RGB")
        colors = len(set(img.getdata()))
        total_pixels = img.width * img.height
        color_ratio = colors / total_pixels if total_pixels > 0 else 1
        
        # Chess diagrams typically have 2-30 colors
        if colors <= 50 and color_ratio < 0.15:
            grid_confidence += 0.1
    except Exception:
        pass
    
    is_chess = is_grid and grid_confidence >= 0.5
    return is_chess, min(grid_confidence, 1.0)


# ============================================================================
# FONT-BASED CHESS DIAGRAM DETECTION
# ============================================================================

def detect_chess_font_diagrams(page: fitz.Page) -> list[ChessDiagramCandidate]:
    """
    Detect diagrams that use chess fonts (Merida, Alpha, etc.).
    These appear as text blocks but should be treated as images.
    """
    candidates = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # Not text
            continue
        
        # Check all spans in this block for chess fonts
        has_chess_font = False
        chess_spans = []
        
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font_name = span.get("font", "")
                if is_chess_font(font_name):
                    has_chess_font = True
                    chess_spans.append(span)
        
        if has_chess_font and chess_spans:
            # Calculate bounding box
            x0 = min(s["bbox"][0] for s in chess_spans)
            y0 = min(s["bbox"][1] for s in chess_spans)
            x1 = max(s["bbox"][2] for s in chess_spans)
            y1 = max(s["bbox"][3] for s in chess_spans)
            
            width = x1 - x0
            height = y1 - y0
            
            # Chess font diagrams are often compact blocks
            candidates.append(ChessDiagramCandidate(
                page_num=page.number,
                bbox=(x0, y0, x1, y1),
                width=width,
                height=height,
                aspect_ratio=width / height if height > 0 else 0,
                detection_method="font",
                confidence=0.9 if is_square_enough(width / height if height > 0 else 0) else 0.7,
                requires_review=not is_square_enough(width / height if height > 0 else 0),
            ))
    
    return candidates


# ============================================================================
# TEXT-CONTEXT CHESS DETECTION
# ============================================================================

def detect_chess_by_text_context(
    page: fitz.Page,
    nearby_distance: float = 50.0,
) -> list[ChessDiagramCandidate]:
    """
    Detect images near chess-related text (captions, descriptions).
    """
    candidates = []
    text = page.get_text()
    is_chess_text, matches = text_contains_chess_notation(text)
    
    if not is_chess_text:
        return candidates
    
    # Get all images on page
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        rects = page.get_image_rects(xref)
        
        for rect in rects:
            # Check if chess text is nearby
            nearby_text = get_text_near_rect(page, rect, nearby_distance)
            nearby_chess, _ = text_contains_chess_notation(nearby_text)
            
            if nearby_chess:
                # Extract image
                try:
                    img_data = page.parent.extract_image(xref)
                    if not img_data or not img_data.get("image"):
                        continue
                    
                    candidates.append(ChessDiagramCandidate(
                        page_num=page.number,
                        bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                        width=rect.width,
                        height=rect.height,
                        aspect_ratio=rect.width / rect.height if rect.height > 0 else 0,
                        detection_method="text",
                        confidence=0.7,
                        image_data=img_data["image"],
                        image_format=img_data["ext"],
                        caption_text=nearby_text[:200],
                        requires_review=True,
                    ))
                except Exception:
                    pass
    
    return candidates


def get_text_near_rect(page: fitz.Page, rect: fitz.Rect, distance: float) -> str:
    """Get text within a certain distance of a rectangle."""
    expanded = rect + (-distance, -distance, distance, distance)
    return page.get_text("text", clip=expanded)


# ============================================================================
# MAIN DETECTION PIPELINE
# ============================================================================

def detect_chess_diagrams(
    pdf_path: str,
    min_confidence: float = 0.5,
) -> list[ChessDiagramCandidate]:
    """
    Main entry point: detect all chess diagrams in a PDF.
    
    Uses combined detection:
    1. Font-based (chess fonts)
    2. Image-based (grid pattern, aspect ratio)
    3. Text-context (nearby chess notation)
    
    Returns list of ChessDiagramCandidate sorted by confidence.
    """
    doc = fitz.open(pdf_path)
    all_candidates = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Method 1: Font-based detection
        font_candidates = detect_chess_font_diagrams(page)
        all_candidates.extend(font_candidates)
        
        # Method 2: Image-based detection
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            rects = page.get_image_rects(xref)
            
            for rect in rects:
                try:
                    img_data = page.parent.extract_image(xref)
                    if not img_data or not img_data.get("image"):
                        continue
                    
                    is_chess, confidence = detect_chess_board_in_image(
                        img_data["image"],
                        rect.width,
                        rect.height,
                    )
                    
                    if is_chess and confidence >= min_confidence:
                        # Get nearby text for caption
                        nearby_text = get_text_near_rect(page, rect, 40.0)
                        
                        all_candidates.append(ChessDiagramCandidate(
                            page_num=page_num,
                            bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                            width=rect.width,
                            height=rect.height,
                            aspect_ratio=rect.width / rect.height if rect.height > 0 else 0,
                            detection_method="image",
                            confidence=confidence,
                            image_data=img_data["image"],
                            image_format=img_data["ext"],
                            caption_text=nearby_text.strip()[:300],
                        ))
                except Exception:
                    pass
        
        # Method 3: Text-context detection
        text_candidates = detect_chess_by_text_context(page)
        all_candidates.extend(text_candidates)
    
    doc.close()
    
    # Sort by confidence (highest first)
    all_candidates.sort(key=lambda c: c.confidence, reverse=True)
    
    # Deduplicate: remove overlapping candidates on same page
    unique_candidates = []
    for candidate in all_candidates:
        is_duplicate = False
        for existing in unique_candidates:
            if existing.page_num == candidate.page_num:
                # Check if bounding boxes overlap significantly
                if boxes_overlap(existing.bbox, candidate.bbox, threshold=0.5):
                    is_duplicate = True
                    break
        if not is_duplicate:
            unique_candidates.append(candidate)
    
    return unique_candidates


def boxes_overlap(box1: tuple, box2: tuple, threshold: float = 0.5) -> bool:
    """Check if two bounding boxes overlap by more than threshold."""
    x0_1, y0_1, x1_1, y1_1 = box1
    x0_2, y0_2, x1_2, y1_2 = box2
    
    # Calculate intersection
    x0_i = max(x0_1, x0_2)
    y0_i = max(y0_1, y0_2)
    x1_i = min(x1_1, x1_2)
    y1_i = min(y1_1, y1_2)
    
    if x1_i <= x0_i or y1_i <= y0_i:
        return False
    
    intersection = (x1_i - x0_i) * (y1_i - y0_i)
    area1 = (x1_1 - x0_1) * (y1_1 - y0_1)
    area2 = (x1_2 - x0_2) * (y1_2 - y0_2)
    
    overlap_ratio = intersection / max(area1, area2) if max(area1, area2) > 0 else 0
    return overlap_ratio >= threshold


# ============================================================================
# IMAGE POST-PROCESSING
# ============================================================================

def enhance_chess_diagram(
    image_data: bytes,
    sharpen: bool = True,
    contrast_boost: float = 1.1,
) -> tuple[bytes, str]:
    """
    Optional post-processing for chess diagrams.
    
    Applies subtle enhancements to improve readability:
    - Sharpening (if enabled)
    - Slight contrast boost
    - No aggressive processing
    """
    try:
        img = Image.open(io.BytesIO(image_data))
        
        if contrast_boost != 1.0:
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(contrast_boost)
        
        if sharpen:
            img = img.filter(ImageFilter.SHARPEN)
        
        # Convert to RGB if necessary for PNG
        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = background
        
        # Save as PNG (lossless)
        output = io.BytesIO()
        img.save(output, format="PNG", optimize=True)
        output.seek(0)
        
        return output.read(), "png"
        
    except Exception as e:
        print(f"Warning: Could not enhance image: {e}")
        return image_data, "png"


# ============================================================================
# DIAGNOSTIC REPORT
# ============================================================================

def generate_chess_detection_report(
    candidates: list[ChessDiagramCandidate],
    pdf_path: str,
) -> str:
    """Generate a human-readable report of chess diagram detection."""
    lines = []
    lines.append(f"Chess Diagram Detection Report")
    lines.append(f"{'='*60}")
    lines.append(f"PDF: {pdf_path}")
    lines.append(f"Total candidates: {len(candidates)}")
    lines.append("")
    
    if not candidates:
        lines.append("No chess diagrams detected in this PDF.")
        lines.append("")
        lines.append("Detection methods used:")
        lines.append("  1. Image-based: 8x8 grid pattern, square aspect ratio")
        lines.append("  2. Font-based: Chess font detection (Merida, Alpha, etc.)")
        lines.append("  3. Text-context: Nearby chess notation patterns")
        return "\n".join(lines)
    
    for idx, cand in enumerate(candidates, 1):
        lines.append(f"Diagram #{idx}:")
        lines.append(f"  Page: {cand.page_num + 1}")
        lines.append(f"  Position: ({cand.bbox[0]:.1f}, {cand.bbox[1]:.1f}) to ({cand.bbox[2]:.1f}, {cand.bbox[3]:.1f})")
        lines.append(f"  Size: {cand.width:.1f} x {cand.height:.1f}")
        lines.append(f"  Aspect ratio: {cand.aspect_ratio:.2f}")
        lines.append(f"  Detection method: {cand.detection_method}")
        lines.append(f"  Confidence: {cand.confidence:.1%}")
        lines.append(f"  Image format: {cand.image_format or 'N/A'}")
        lines.append(f"  Requires review: {'Yes' if cand.requires_review else 'No'}")
        
        if cand.caption_text:
            lines.append(f"  Caption: {cand.caption_text[:100]}...")
        
        lines.append("")
    
    # Summary
    high_conf = [c for c in candidates if c.confidence >= 0.8]
    medium_conf = [c for c in candidates if 0.5 <= c.confidence < 0.8]
    low_conf = [c for c in candidates if c.confidence < 0.5]
    
    lines.append(f"Summary:")
    lines.append(f"  High confidence (≥80%): {len(high_conf)}")
    lines.append(f"  Medium confidence (50-80%): {len(medium_conf)}")
    lines.append(f"  Low confidence (<50%): {len(low_conf)}")
    
    return "\n".join(lines)
