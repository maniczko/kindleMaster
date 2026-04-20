#!/usr/bin/env python3
"""Analyze PDFs in example folder"""
import fitz
from pathlib import Path

example_dir = Path('example')
pdfs = list(example_dir.glob('*.pdf'))

with open('pdf_analysis.txt', 'w', encoding='utf-8') as f:
    f.write('=== ANALIZA PDF W FOLDERZE EXAMPLE ===\n\n')
    
    for pdf_path in pdfs:
        f.write(f'\n{"="*60}\n')
        f.write(f'PDF: {pdf_path.name}\n')
        f.write(f'{"="*60}\n')
        
        try:
            doc = fitz.open(str(pdf_path))
            f.write(f'Liczba stron: {len(doc)}\n')
            f.write(f'Rozmiar pliku: {pdf_path.stat().st_size:,} bytes\n')
            
            # Sprawdź pierwszą stronę
            page = doc[0]
            text = page.get_text()
            images = page.get_images(full=True)
            
            # Zbierz fonty
            fonts = set()
            text_dict = page.get_text('dict')
            for block in text_dict.get('blocks', []):
                if block.get('type') != 0:
                    continue
                for line in block.get('lines', []):
                    for span in line.get('spans', []):
                        fonts.add(span.get('font', ''))
            
            f.write(f'\nStrona 1:\n')
            f.write(f'  Tekst: {len(text)} znaków\n')
            f.write(f'  Obrazy: {len(images)}\n')
            f.write(f'  Fonty: {list(fonts)[:10]}\n')
            
            has_chess = any('chess' in ft.lower() or 'merida' in ft.lower() for ft in fonts)
            if has_chess:
                f.write(f'  ⚠️ WYKRYTO DIAGRAMY SZACHOWE!\n')
            
            # Sprawdź stronę 33 (z diagramami)
            if len(doc) > 32:
                page33 = doc[32]
                text33 = page33.get_text()
                fonts33 = set()
                td33 = page33.get_text('dict')
                for block in td33.get('blocks', []):
                    if block.get('type') != 0:
                        continue
                    for line in block.get('lines', []):
                        for span in line.get('spans', []):
                            fonts33.add(span.get('font', ''))
                
                f.write(f'\nStrona 33 (diagramy):\n')
                f.write(f'  Tekst: {len(text33)} znaków\n')
                f.write(f'  Fonty: {list(fonts33)[:10]}\n')
                has_chess33 = any('chess' in ft.lower() or 'merida' in ft.lower() for ft in fonts33)
                if has_chess33:
                    f.write(f'  ✅ POTWIERDZONE DIAGRAMY SZACHOWE!\n')
            
            doc.close()
        except Exception as e:
            f.write(f'BŁĄD: {e}\n')

print('Analiza zapisana do pdf_analysis.txt')
