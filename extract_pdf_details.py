"""Extract all text blocks with positions from PDF"""
import fitz

pdf_path = 'c:/Users/user/Desktop/Wycieczka do Krzywego Lasu.pdf'
doc = fitz.open(pdf_path)

page = doc[0]
print(f"Page dimensions: {page.rect.width:.1f} x {page.rect.height:.1f}")

# Get full text dict with positions
text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

print("\n=== ALL TEXT BLOCKS WITH POSITIONS ===")
for i, block in enumerate(text_dict.get("blocks", [])):
    if block.get("type") == 0:  # Text block
        bbox = block.get("bbox", (0, 0, 0, 0))
        print(f"\nBlock {i}: bbox=({bbox[0]:.1f}, {bbox[1]:.1f}, {bbox[2]:.1f}, {bbox[3]:.1f})")
        for j, line in enumerate(block.get("lines", [])):
            line_bbox = line.get("bbox", (0, 0, 0, 0))
            print(f"  Line {j}: bbox=({line_bbox[0]:.1f}, {line_bbox[1]:.1f}, {line_bbox[2]:.1f}, {line_bbox[3]:.1f})")
            for span in line.get("spans", []):
                text = span.get("text", "")
                font = span.get("font", "Unknown")
                size = span.get("size", 0)
                flags = span.get("flags", 0)
                color = span.get("color", 0)
                span_bbox = span.get("bbox", (0, 0, 0, 0))
                is_bold = bool(flags & 2**4)
                is_italic = bool(flags & 2**1)
                print(f"    Span: '{text}' font={font} size={size} bold={is_bold} italic={is_italic} color={color} bbox=({span_bbox[0]:.1f}, {span_bbox[1]:.1f}, {span_bbox[2]:.1f}, {span_bbox[3]:.1f})")
    elif block.get("type") == 1:  # Image block
        bbox = block.get("bbox", (0, 0, 0, 0))
        print(f"\nBlock {i}: IMAGE bbox=({bbox[0]:.1f}, {bbox[1]:.1f}, {bbox[2]:.1f}, {bbox[3]:.1f})")
        # Check for image data in block
        img_data = block.get("image")
        if img_data:
            print(f"  Inline image data: {len(img_data)} bytes")

# Also try raw text extraction
print("\n=== RAW TEXT (with layout) ===")
print(repr(page.get_text("text")))

print("\n=== RAW TEXT (plain) ===")
print(repr(page.get_text("rawtext")))

print("\n=== RAW TEXT (words) ===")
words = page.get_text("words")
for w in words:
    print(f"  Word: '{w[4]}' at ({w[0]:.1f}, {w[1]:.1f}, {w[2]:.1f}, {w[3]:.1f})")

# Check for drawing/paths
print("\n=== DRAWING/PATHS ===")
paths = page.get_drawings()
print(f"Number of drawing paths: {len(paths)}")
for i, path in enumerate(paths[:10]):  # Show first 10
    print(f"  Path {i}: type={path.get('type')}, fill={path.get('fill')}, rect={path.get('rect')}")
    if len(paths) > 10 and i == 9:
        print(f"  ... and {len(paths) - 10} more")

doc.close()
