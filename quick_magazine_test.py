#!/usr/bin/env python3
"""Quick test of magazine conversion"""
from pathlib import Path
import fitz
from converter import convert_pdf_to_epub, ConversionConfig, detect_pdf_type
import traceback

pdf = Path('example/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf')
print('Testing magazine PDF conversion...\n')

# Check detection
t = detect_pdf_type(str(pdf))
print(f'Detection:')
for k, v in t.items():
    print(f'  {k}: {v}')

# Create 3-page test
doc = fitz.open(str(pdf))
test = Path('example/test3.pdf')
td = fitz.open()
for i in range(3):
    td.insert_pdf(doc, from_page=i, to_page=i)
td.save(str(test))
td.close()
doc.close()

# Convert
try:
    config = ConversionConfig(prefer_fixed_layout=True)
    epub = convert_pdf_to_epub(str(test), config=config, original_filename='test.pdf')
    size_kb = len(epub) // 1024
    print(f'\nResult: {size_kb} KB')
    
    # Check size
    if size_kb < 2000:
        print('✅ SUCCESS - small file (hybrid v3 used)')
    else:
        print(f'❌ FAIL - too big ({size_kb} KB) - still using screenshots!')
except Exception as e:
    print(f'Error: {e}')
    traceback.print_exc()

test.unlink(missing_ok=True)
