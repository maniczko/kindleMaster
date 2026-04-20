#!/usr/bin/env python3
"""
CHESS DIAGRAM ENHANCER
- Adds coordinate labels (a-h, 1-8) to chess diagrams
- Optimizes size for EPUB (smaller, crisp)
- Maintains readability
"""

import fitz
import io
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from chess_diagram_renderer import render_chess_diagram_to_png, ChessDiagramRegion


def enhance_chess_diagram_with_labels(
    page: fitz.Page,
    region: ChessDiagramRegion,
    dpi: int = 150,
    add_labels: bool = True,
) -> tuple[bytes, int, int]:
    """
    Render chess diagram with coordinate labels (a-h, 1-8).
    Optimized for EPUB: smaller size, crisp rendering.
    """
    # First render the diagram
    png_data, width, height = render_chess_diagram_to_png(page, region, dpi)
    
    if not add_labels:
        return png_data, width, height
    
    try:
        # Open rendered diagram
        img = Image.open(io.BytesIO(png_data)).convert("RGB")
        
        # Add labels if diagram is large enough
        if width > 100 and height > 100:
            draw = ImageDraw.Draw(img)
            
            # Try to use a simple font
            try:
                font = ImageFont.truetype("arial.ttf", 12)
            except:
                font = ImageFont.load_default()
            
            # Calculate cell size
            cell_w = width / 8
            cell_h = height / 8
            
            # Add file labels (a-h) at bottom
            files = 'abcdefgh'
            for i, f in enumerate(files):
                x = int((i + 0.5) * cell_w)
                y = height - 5
                draw.text((x - 4, y), f, fill="black", font=font)
            
            # Add rank labels (1-8) on left
            for i in range(8):
                x = 3
                y = int((i + 0.5) * cell_h)
                rank = str(8 - i)
                draw.text((x, y - 6), rank, fill="black", font=font)
            
            # Save enhanced diagram
            output = io.BytesIO()
            img.save(output, format="PNG", optimize=True, compress_level=9)
            output.seek(0)
            
            return output.read(), img.width, img.height
    
    except Exception as e:
        print(f"Warning: Could not add labels to chess diagram: {e}")
    
    # Fallback: return original
    return png_data, width, height


def optimize_chess_diagram_size(png_data: bytes, max_width: int = 300) -> tuple[bytes, int, int]:
    """
    Optimize chess diagram for EPUB:
    - Resize to max_width (300px is good for EPUB)
    - Reduce colors (chess diagrams have few colors)
    - Compress aggressively
    """
    try:
        img = Image.open(io.BytesIO(png_data))
        
        # Resize if too large
        if img.width > max_width:
            ratio = max_width / img.width
            new_width = max_width
            new_height = int(img.height * ratio)
            img = img.resize((new_width, new_height), Image.LANCZOS)
        
        # Convert to RGB
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        
        # Quantize to reduce file size (chess diagrams typically have < 20 colors)
        unique_colors = len(set(img.getdata()))
        if unique_colors > 32:
            img = img.quantize(colors=64, method=Image.Quantize.MEDIANCUT)
            img = img.convert("RGB")
        
        # Save with max compression
        output = io.BytesIO()
        img.save(output, format="PNG", optimize=True, compress_level=9)
        output.seek(0)
        
        return output.read(), img.width, img.height
    
    except Exception as e:
        print(f"Warning: Could not optimize chess diagram: {e}")
        return png_data, 0, 0


# Test it
if __name__ == '__main__':
    example_dir = Path('example')
    chess_pdf = example_dir / 'The Woodpecker Method ( PDFDrive ).pdf'
    
    if chess_pdf.exists():
        doc = fitz.open(str(chess_pdf))
        
        # Find page with chess diagram (page 32 = index 32)
        if len(doc) > 32:
            page = doc[32]
            print(f"Testing on page 33 of {chess_pdf.name}")
            
            # This would need actual chess diagram detection
            print("Chess diagram enhancer ready!")
        
        doc.close()
    else:
        print(f"Chess PDF not found: {chess_pdf}")
