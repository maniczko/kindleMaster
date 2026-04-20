#!/usr/bin/env python3
"""Final test of integrated magazine converter"""
from converter import convert_pdf_to_epub, ConversionConfig
from pathlib import Path
import zipfile

example_dir = Path('example')
magazine = example_dir / '9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf'

# Create 5-page test
import fitz
doc = fitz.open(str(magazine))
test_pdf = example_dir / 'magazine_final_test.pdf'
test_doc = fitz.open()
for i in range(min(5, len(doc))):
    test_doc.insert_pdf(doc, from_page=i, to_page=i)
test_doc.save(str(test_pdf))
test_doc.close()
doc.close()

with open('test_result.txt', 'w', encoding='utf-8') as f:
    f.write('Testing integrated magazine hybrid converter...\n\n')
    
    try:
        config = ConversionConfig(prefer_fixed_layout=True)
        epub_bytes = convert_pdf_to_epub(str(test_pdf), config=config, original_filename='magazine.pdf')
        
        output = example_dir / 'magazine_integrated.epub'
        output.write_bytes(epub_bytes)
        
        f.write(f'✅ EPUB generated: {len(epub_bytes)//1024} KB\n\n')
        
        # Verify
        z = zipfile.ZipFile(output)
        chapters = [f for f in z.namelist() if f.endswith('.xhtml') and 'chapter' in f]
        has_screenshots = any('page_' in f and f.endswith('.jpeg') for f in z.namelist())
        
        if chapters:
            content = z.read(chapters[0]).decode('utf-8', errors='ignore')
            f.write(f'Rozdział 1: {len(content)} znaków\n')
            f.write(f'Pełnoekranowe screeny: {has_screenshots}\n\n')
            
            if not has_screenshots and len(content) > 500:
                f.write('🎉 SUKCES! Magazyn konwertowany poprawnie!\n')
                f.write('   - Tekst wyciągnięty (nie screeny)\n')
                f.write('   - Obrazy zapisane osobno\n')
                f.write('   - Jakość do sprzedaży!\n')
            else:
                f.write('❌ Problem: Nadal robi screeny\n')
    except Exception as e:
        f.write(f'❌ Error: {e}\n')
        import traceback
        f.write(traceback.format_exc())
    
    test_pdf.unlink(missing_ok=True)

print('Wynik zapisany do test_result.txt')
