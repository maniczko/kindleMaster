#!/usr/bin/env python3
"""Test chess diagram fix with 5-page EPUB"""
import fitz
import zipfile
import os
from pymupdf_chess_extractor import extract_pdf_with_chess_support
from converter import build_epub, ConversionConfig, _extract_pdf_metadata

pdf_path = r'c:\Users\user\Downloads\The Woodpecker Method ( PDFDrive ).pdf'
config = ConversionConfig(prefer_fixed_layout=True)
pdf_metadata = _extract_pdf_metadata(pdf_path)

# Extract only first 5 pages (page 5 has chess diagrams)
print("Creating 5-page test PDF...")
doc = fitz.open(pdf_path)
temp_doc = fitz.open()
for i in range(5):
    temp_doc.insert_pdf(doc, from_page=i, to_page=i)
temp_doc.save('test_5pages.pdf')
temp_doc.close()
doc.close()

print('Converting 5-page test...')
content = extract_pdf_with_chess_support('test_5pages.pdf', config, pdf_metadata)
print(f'Chess diagrams found: {content.get("chess_diagram_count", 0)}')

# Build EPUB
epub_bytes = build_epub(content, config, 'Test', pdf_metadata)
with open('test_5pages.epub', 'wb') as f:
    f.write(epub_bytes)
print(f'Test EPUB: {len(epub_bytes)//1024} KB')

# Verify
print('\nVerifying EPUB...')
z = zipfile.ZipFile('test_5pages.epub')
chess_files = [f for f in z.namelist() if 'chess' in f.lower()]
print(f'Chess files in test EPUB: {len(chess_files)}')

# Check chapter 5
ch5 = z.read('EPUB/chapter_005.xhtml').decode('utf-8')
has_chess_img = 'chess_p' in ch5
has_squares = '\u25a1' in ch5
print(f'Chapter 5 has chess images: {has_chess_img}')
print(f'Chapter 5 has empty squares: {has_squares}')

if has_chess_img and not has_squares:
    print('\n✅ SUCCESS: Chess diagrams are images, no empty squares!')
elif has_chess_img and has_squares:
    print('\n⚠️ PARTIAL: Chess images present but empty squares still in text')
else:
    print('\n❌ FAIL: Chess diagrams not properly converted')

# Cleanup
os.remove('test_5pages.pdf')
print('\nDone!')
