#!/usr/bin/env python3
"""Final verification test"""
from pathlib import Path
import fitz
from converter import convert_pdf_to_epub, ConversionConfig

pdf = Path('example/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf')
doc = fitz.open(str(pdf))
test = Path('example/verify_5p.pdf')
td = fitz.open()
for i in range(5):
    td.insert_pdf(doc, from_page=i, to_page=i)
td.save(str(test))
td.close()
doc.close()

with open('verify_result.txt', 'w', encoding='utf-8') as f:
    f.write('Testing with 5-page magazine...\n\n')
    
    try:
        config = ConversionConfig(prefer_fixed_layout=True)
        epub = convert_pdf_to_epub(str(test), config=config, original_filename='test.pdf')
        
        size_kb = len(epub) // 1024
        f.write(f'Result: {size_kb} KB\n\n')
        
        if size_kb < 2000:
            f.write('✅ NOWA WERSJA - mały plik (HYBRID v3 użyty)\n')
            f.write('Serwer używa najnowszego kodu!\n')
        else:
            f.write(f'❌ STARA WERSJA - duży plik ({size_kb} KB) - nadal screenshoty!\n')
            f.write('Serwer NIE używa najnowszego kodu!\n')
    except Exception as e:
        f.write(f'Error: {e}\n')
        import traceback
        f.write(traceback.format_exc())

test.unlink(missing_ok=True)
print('Wynik zapisany do verify_result.txt')
