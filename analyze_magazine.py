#!/usr/bin/env python3
"""
ANALIZA MAGAZYNU - Dlaczego konwersja działa słabo?
Magazine: 9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf
"""
import fitz
from pathlib import Path

example_dir = Path('example')
magazine_pdf = example_dir / '9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf'

print('=== SZCZEGÓŁOWA ANALIZA MAGAZYNU ===\n')

doc = fitz.open(str(magazine_pdf))
print(f'PDF: {magazine_pdf.name}')
print(f'Strony: {len(doc)}')
print(f'Rozmiar: {magazine_pdf.stat().st_size:,} bytes\n')

# Analiza szczegółowa pierwszej strony
page = doc[0]
print('STRONA 1 - SZCZEGÓŁY:')
print(f'  Wymiary: {page.rect.width} x {page.rect.height}')

# Pobierz tekst z pozycjami
text_dict = page.get_text('dict', flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)

# Policz bloki tekstowe vs obrazkowe
text_blocks = 0
image_blocks = 0
total_chars = 0
fonts_used = {}

for block in text_dict.get('blocks', []):
    if block.get('type') == 0:  # Text
        text_blocks += 1
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                text = span.get('text', '')
                total_chars += len(text)
                font = span.get('font', 'Unknown')
                fonts_used[font] = fonts_used.get(font, 0) + 1
    elif block.get('type') == 1:  # Image
        image_blocks += 1

print(f'\n  Bloki tekstowe: {text_blocks}')
print(f'  Bloki obrazkowe: {image_blocks}')
print(f'  Łączna liczba znaków: {total_chars}')
print(f'\n  Fonty ({len(fonts_used)}):')
for font, count in sorted(fonts_used.items(), key=lambda x: -x[1])[:10]:
    print(f'    {font}: {count} wystąpień')

# Sprawdź czy tekst jest "prawdziwy" czy tylko metadane
sample_text = page.get_text()[:500]
print(f'\n  Przykładowy tekst:')
print(f'  {repr(sample_text[:200])}')

# Sprawdź stronę z artykułem (strona 5)
if len(doc) > 4:
    page5 = doc[4]
    text5 = page5.get_text()
    print(f'\n\nSTRONA 5 - ARTYKUŁ:')
    print(f'  Tekst: {len(text5)} znaków')
    print(f'  Przykład: {repr(text5[:300])}')

doc.close()

print('\n=== WNIOSKI ===')
if total_chars > 500:
    print('✅ Magazyn MA warstwę tekstową - można przeczytać tekst!')
    print('   Problem: Konwerter prawdopodobnie traktuje go jako skan.')
else:
    print('❌ Magazyn ma mało tekstu - może być skanem.')
