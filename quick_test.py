#!/usr/bin/env python3
"""
QUICK TEST - Test conversion on first 20 pages only
"""
import fitz
import zipfile
from pathlib import Path
from converter import convert_pdf_to_epub, ConversionConfig

example_dir = Path('example')
chess_pdf = example_dir / 'The Woodpecker Method ( PDFDrive ).pdf'

# Create a 20-page test PDF
print('Creating 20-page test PDF...')
doc = fitz.open(str(chess_pdf))
test_pdf = example_dir / 'chess_20pages.pdf'
test_doc = fitz.open()
for i in range(min(20, len(doc))):
    test_doc.insert_pdf(doc, from_page=i, to_page=i)
test_doc.save(str(test_pdf))
test_doc.close()
doc.close()

print(f'Test PDF created: {test_pdf.name}')

# Test with chess-aware extraction
print('\n Converting with chess-aware extraction...')
config = ConversionConfig(prefer_fixed_layout=True)

try:
    from pymupdf_chess_extractor import extract_pdf_with_chess_support
    from converter import build_epub, _extract_pdf_metadata
    
    pdf_metadata = _extract_pdf_metadata(str(test_pdf))
    content = extract_pdf_with_chess_support(str(test_pdf), config, pdf_metadata)
    
    print(f'Chess diagrams found: {content.get("chess_diagram_count", 0)}')
    print(f'Chapters: {len(content["chapters"])}')
    
    epub_bytes = build_epub(content, config, 'Chess Test', pdf_metadata)
    output_path = example_dir / 'chess_20pages_test.epub'
    output_path.write_bytes(epub_bytes)
    
    print(f'✅ EPUB created: {output_path.name}')
    print(f'   Size: {len(epub_bytes)//1024} KB')
    
    # Quick verification
    z = zipfile.ZipFile(output_path)
    chess_files = [f for f in z.namelist() if 'chess' in f.lower()]
    print(f'   Chess diagram images: {len(chess_files)}')
    
    # Check for empty squares
    has_issues = False
    for name in z.namelist():
        if name.endswith('.xhtml'):
            content = z.read(name).decode('utf-8', errors='ignore')
            if '\u25a1' in content:
                has_issues = True
                print(f'   ❌ {name} contains empty squares')
    
    if not has_issues:
        print(f'   ✅ No empty squares detected!')
    
except Exception as e:
    print(f'❌ Error: {e}')
    import traceback
    traceback.print_exc()

# Cleanup
test_pdf.unlink(missing_ok=True)
print('\nDone!')
