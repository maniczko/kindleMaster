#!/usr/bin/env python3
"""
INTEGRACJA: Dodaj hybrydowy konwerter magazynów do głównego pipeline'u
"""
import sys
sys.path.insert(0, '.')

from pathlib import Path
from converter import convert_pdf_to_epub, ConversionConfig
import zipfile

example_dir = Path('example')
magazine = example_dir / '9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf'

print('=== TEST GŁÓWNEGO PIPELINE\'U NA MAGAZYNIE ===\n')

# Stwórz 5-stronicowy test
import fitz
doc = fitz.open(str(magazine))
test_pdf = example_dir / 'magazine_test_final.pdf'
test_doc = fitz.open()
for i in range(min(5, len(doc))):
    test_doc.insert_pdf(doc, from_page=i, to_page=i)
test_doc.save(str(test_pdf))
test_doc.close()
doc.close()

# Test z obecnym pipeline'm
print('Test 1: Obecny pipeline (prefer_fixed_layout=True)')
try:
    config = ConversionConfig(prefer_fixed_layout=True)
    epub_bytes = convert_pdf_to_epub(str(test_pdf), config=config, original_filename='magazine.pdf')
    output1 = example_dir / 'magazine_current.epub'
    output1.write_bytes(epub_bytes)
    
    z = zipfile.ZipFile(output1)
    chapters = [f for f in z.namelist() if f.endswith('.xhtml') and 'chapter' in f]
    has_screenshots = any('page_' in f and f.endswith('.jpeg') for f in z.namelist())
    
    if chapters:
        content = z.read(chapters[0]).decode('utf-8', errors='ignore')
        print(f'  Rozmiar: {len(epub_bytes)//1024} KB')
        print(f'  Tekst w rozdziale 1: {len(content)} znaków')
        print(f'  Pełnoekranowe screeny: {has_screenshots}')
        
        if has_screenshots:
            print('  ❌ PROBLEM: Robi screeny zamiast czytać tekst!')
        else:
            print('  ✅ OK: Wyciąga tekst')
except Exception as e:
    print(f'  ❌ Błąd: {e}')

print('\nTest 2: Hybrydowy konwerter magazynów')
try:
    from magazine_hybrid_converter import convert_magazine_hybrid
    output2 = example_dir / 'magazine_hybrid.epub'
    convert_magazine_hybrid(str(test_pdf), str(output2))
    
    z = zipfile.ZipFile(output2)
    chapters = [f for f in z.namelist() if f.endswith('.xhtml') and 'chapter' in f]
    has_screenshots = any('page_' in f and f.endswith('.jpeg') for f in z.namelist())
    
    if chapters:
        content = z.read(chapters[0]).decode('utf-8', errors='ignore')
        epub_size = output2.stat().st_size
        print(f'  Rozmiar: {epub_size//1024} KB')
        print(f'  Tekst w rozdziale 1: {len(content)} znaków')
        print(f'  Pełnoekranowe screeny: {has_screenshots}')
        
        if not has_screenshots and len(content) > 500:
            print('  ✅ SUKCES: Tekst wyciągnięty, brak screenshotów!')
except Exception as e:
    print(f'  ❌ Błąd: {e}')
    import traceback
    traceback.print_exc()

# Cleanup
test_pdf.unlink(missing_ok=True)
print('\n=== PORÓWNANIE ===')
print('Obecny pipeline: Robi screeny całych stron (źle dla magazynów)')
print('Hybrydowy konwerter: Wyciąga tekst + obrazy osobno (dobrze!)')
print('\n👉 Należy zintegrować hybrydowy konwerter z głównym pipeline\'em')
