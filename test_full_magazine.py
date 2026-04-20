#!/usr/bin/env python3
"""Test full magazine conversion"""
import sys
sys.path.insert(0, '.')
from pathlib import Path
from converter import convert_pdf_to_epub, ConversionConfig
import traceback

pdf = Path('example/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf')
print('Testing full magazine conversion (108 pages)...')
config = ConversionConfig(prefer_fixed_layout=True)

try:
    epub = convert_pdf_to_epub(str(pdf), config=config, original_filename='magazine.pdf')
    size_kb = len(epub) // 1024
    print(f'\nSUCCESS: {size_kb} KB')
    if size_kb < 10000:
        print('✅ Small file - optimization working!')
    else:
        print('❌ Still too big')
except Exception as e:
    print(f'\nERROR: {e}')
    traceback.print_exc()
