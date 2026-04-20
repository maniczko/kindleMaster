#!/usr/bin/env python3
"""
ITERACYJNY PIPELINE TESTOWY KONWERSJI PDF -> EPUB
Testuje różne metody i porównuje jakość aż do poziomu "do sprzedaży"
"""
import fitz
import zipfile
import os
from pathlib import Path
from converter import convert_pdf_to_epub, ConversionConfig, _extract_pdf_metadata
from pymupdf_chess_extractor import extract_pdf_with_chess_support

example_dir = Path('example')

def analyze_epub_quality(epub_path, pdf_path, iteration_name):
    """Analyze EPUB quality metrics"""
    z = zipfile.ZipFile(epub_path)
    
    # File structure
    total_files = len(z.namelist())
    xhtml_files = len([f for f in z.namelist() if f.endswith('.xhtml')])
    image_files = len([f for f in z.namelist() if '/images/' in f])
    chess_images = len([f for f in z.namelist() if 'chess' in f.lower()])
    
    # Check first chapter quality
    first_chapter = None
    for name in z.namelist():
        if name.startswith('EPUB/chapter_') and name.endswith('.xhtml'):
            first_chapter = name
            break
    
    issues = []
    quality_score = 100
    
    if first_chapter:
        content = z.read(first_chapter).decode('utf-8', errors='ignore')
        
        # Check for empty squares (chess diagrams not fixed)
        if '\u25a1' in content:
            issues.append('❌ Empty squares (□) in text - chess diagrams not fixed')
            quality_score -= 30
        
        # Check if text is readable
        if len(content) < 100:
            issues.append('⚠️ Very little text content')
            quality_score -= 20
        
        # Check for positioning (fixed layout)
        has_positioning = 'left:' in content and 'top:' in content
        if not has_positioning:
            issues.append('⚠️ No fixed layout positioning')
            quality_score -= 10
    
    # Size check
    epub_size = epub_path.stat().st_size
    if epub_size > 50_000_000:  # > 50MB
        issues.append(f'⚠️ Very large file: {epub_size//1024//1024} MB')
        quality_score -= 10
    
    result = {
        'name': iteration_name,
        'size_kb': epub_size // 1024,
        'total_files': total_files,
        'xhtml_files': xhtml_files,
        'image_files': image_files,
        'chess_images': chess_images,
        'issues': issues,
        'quality_score': max(0, quality_score)
    }
    
    return result

def print_comparison(results):
    """Print comparison table"""
    print('\n' + '='*80)
    print('PORÓWNANIE WERSJI EPUB')
    print('='*80)
    print(f'{"Wersja":<30} {"Rozmiar":<10} {"Pliki":<8} {"Obrazy":<8} {"Szachy":<8} {"Jakość":<10}')
    print('-'*80)
    
    for r in results:
        issues_str = ' | '.join(r['issues'][:2]) if r['issues'] else '✅ OK'
        print(f'{r["name"]:<30} {r["size_kb"]:>6} KB {r["total_files"]:>6} {r["image_files"]:>6} {r["chess_images"]:>6} {r["quality_score"]:>6}/100')
        if r['issues']:
            print(f'  Problemy: {issues_str}')
    
    print('='*80)

# Test different configurations
test_configs = [
    ('v1_baseline', ConversionConfig(prefer_fixed_layout=False, force_ocr=False)),
    ('v2_fixed_layout', ConversionConfig(prefer_fixed_layout=True, force_ocr=False)),
    ('v3_high_quality', ConversionConfig(prefer_fixed_layout=True, image_quality=95)),
]

# Test with chess book first
print('\n🔍 TESTOWANIE NA KSIĄŻCE SZACHOWEJ...')
chess_pdf = example_dir / 'The Woodpecker Method ( PDFDrive ).pdf'
results = []

for name, config in test_configs:
    print(f'\n📝 Konwersja: {name}')
    try:
        epub_path = example_dir / f'test_{name}.epub'
        epub_bytes = convert_pdf_to_epub(str(chess_pdf), config=config, original_filename='chess_book.pdf')
        epub_path.write_bytes(epub_bytes)
        
        result = analyze_epub_quality(epub_path, chess_pdf, name)
        results.append(result)
        print(f'  ✅ Gotowe: {result["size_kb"]} KB, jakość: {result["quality_score"]}/100')
    except Exception as e:
        print(f'  ❌ Błąd: {e}')
        results.append({
            'name': name,
            'size_kb': 0,
            'total_files': 0,
            'image_files': 0,
            'chess_images': 0,
            'issues': [f'❌ Conversion failed: {str(e)[:50]}'],
            'quality_score': 0
        })

print_comparison(results)

# Find best version
best = max(results, key=lambda x: x['quality_score'])
print(f'\n🏆 NAJLEPSZA WERSJA: {best["name"]} (jakość: {best["quality_score"]}/100)')

if best['quality_score'] < 90:
    print('\n⚠️ WYMAGA POPRAWEK - kontynuuję iterację...')
    # Continue improving...
else:
    print('\n✅ JAKOŚĆ DO SPRZEDAŻY OSIĄGNIĘTA!')
