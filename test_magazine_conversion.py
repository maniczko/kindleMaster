#!/usr/bin/env python3
"""
TEST KONWERSJI MAGAZYNU - Sprawdź co się dzieje
"""
from converter import convert_pdf_to_epub, ConversionConfig
from pathlib import Path

example_dir = Path('example')
magazine = example_dir / '9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf'

print('=== KONWERSJA MAGAZYNU (tylko 5 stron dla szybkości) ===\n')

# Stwórz 5-stronicowy test
import fitz
doc = fitz.open(str(magazine))
test_pdf = example_dir / 'magazine_5pages.pdf'
test_doc = fitz.open()
for i in range(min(5, len(doc))):
    test_doc.insert_pdf(doc, from_page=i, to_page=i)
test_doc.save(str(test_pdf))
test_doc.close()
doc.close()

# Konwertuj z różnymi ustawieniami
configs = [
    ('layout_fixed', ConversionConfig(prefer_fixed_layout=True)),
    ('reflowable', ConversionConfig(prefer_fixed_layout=False)),
]

for name, config in configs:
    print(f'\n📝 Test: {name}')
    try:
        epub_bytes = convert_pdf_to_epub(str(test_pdf), config=config, original_filename='magazine_test.pdf')
        output = example_dir / f'magazine_test_{name}.epub'
        output.write_bytes(epub_bytes)
        
        print(f'  ✅ Rozmiar: {len(epub_bytes)//1024} KB')
        
        # Sprawdź jakość
        import zipfile
        z = zipfile.ZipFile(output)
        chapters = [f for f in z.namelist() if f.endswith('.xhtml') and 'chapter' in f]
        
        if chapters:
            content = z.read(chapters[0]).decode('utf-8', errors='ignore')
            text_length = len(content)
            has_images = 'img' in content
            has_text = len(content.strip()) > 100
            
            print(f'  Rozdział 1: {text_length} znaków')
            print(f'  Ma obrazy: {has_images}')
            print(f'  Ma tekst: {has_text}')
            
            # Pokaż fragment
            print(f'  Fragment: {content[:200]}...')
    except Exception as e:
        print(f'  ❌ Błąd: {e}')
        import traceback
        traceback.print_exc()

# Cleanup
test_pdf.unlink(missing_ok=True)
print('\n✅ Test zakończony!')
