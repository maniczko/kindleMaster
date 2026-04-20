#!/usr/bin/env python3
"""
POPRAWKA: Magazine conversion - extract text instead of screenshots
Problem: fixed_layout_builder renders entire page as image
Solution: For magazines with text layer, extract text + images separately
"""

import fitz
import io
import html as html_module
from pathlib import Path
from PIL import Image
from converter import ConversionConfig, _extract_pdf_metadata
from ebooklib import epub

def convert_magazine_hybrid(pdf_path, output_path):
    """
    Hybrid conversion for magazines:
    - Extract text with positions
    - Extract images separately
    - Build EPUB with both (no full-page screenshots!)
    """
    print('=== HYBRYDOWA KONWERSJA MAGAZYNU ===\n')
    
    doc = fitz.open(pdf_path)
    pdf_metadata = _extract_pdf_metadata(pdf_path)
    
    book = epub.EpubBook()
    book.set_identifier('urn:uuid:' + epub.uuid.uuid4().hex)
    book.set_title(pdf_metadata.get('title', 'Magazine'))
    book.set_language('pl')
    book.add_author(pdf_metadata.get('author', 'Unknown'))
    
    # Add CSS
    css_content = b'''\
body { font-family: Georgia, serif; line-height: 1.6; margin: 1em; }
h1, h2, h3 { font-family: Arial, sans-serif; }
.figure { text-align: center; margin: 1em 0; }
.figure img { max-width: 100%; height: auto; }
.column { column-count: 2; column-gap: 2em; }
'''
    css_item = epub.EpubItem(
        uid='style',
        file_name='style/default.css',
        media_type='text/css',
        content=css_content,
    )
    book.add_item(css_item)
    
    chapters = []
    total_text_chars = 0
    total_images = 0
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Extract text WITH positions
        text_dict = page.get_text('dict', flags=fitz.TEXT_PRESERVE_WHITESPACE)
        
        # Build HTML from text blocks
        html_parts = []
        page_text_chars = 0
        
        for block in text_dict.get('blocks', []):
            if block.get('type') != 0:  # Text block
                continue
            
            # Get all text from this block
            block_text = ''
            font_sizes = []
            
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    text = span.get('text', '')
                    block_text += text
                    page_text_chars += len(text)
                    font_sizes.append(span.get('size', 12))
            
            if not block_text.strip():
                continue
            
            # Determine if heading based on font size
            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12
            
            if avg_font_size > 24:
                html_parts.append(f'<h1>{html_module.escape(block_text.strip())}</h1>')
            elif avg_font_size > 18:
                html_parts.append(f'<h2>{html_module.escape(block_text.strip())}</h2>')
            elif avg_font_size > 14:
                html_parts.append(f'<h3>{html_module.escape(block_text.strip())}</h3>')
            else:
                # Regular paragraph - check if it looks like a column
                if len(block_text.strip()) > 200:
                    html_parts.append(f'<div class="column"><p>{html_module.escape(block_text.strip())}</p></div>')
                else:
                    html_parts.append(f'<p>{html_module.escape(block_text.strip())}</p>')
        
        # Extract images
        page_images = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = page.parent.extract_image(xref)
                if base_image and base_image.get('image'):
                    total_images += 1
                    img_filename = f'img_p{page_num}_{total_images}.{base_image["ext"]}'
                    page_images.append({
                        'filename': img_filename,
                        'data': base_image['image'],
                        'extension': base_image['ext'],
                    })
                    
                    # Add to EPUB
                    img_item = epub.EpubItem(
                        uid=f'image_{page_num}_{total_images}',
                        file_name=f'images/{img_filename}',
                        media_type=f'image/{base_image["ext"]}',
                        content=base_image['image'],
                    )
                    book.add_item(img_item)
                    
                    # Add to HTML
                    html_parts.append(f'<div class="figure"><img src="images/{img_filename}" alt=""/></div>')
            except Exception as e:
                print(f'  Warning: Could not extract image: {e}')
        
        total_text_chars += page_text_chars
        
        # Create chapter
        chapter = epub.EpubHtml(
            title=f'Strona {page_num + 1}',
            file_name=f'chapter_{page_num+1:03d}.xhtml',
            lang='pl',
        )
        
        html_content = '<html><head></head><body>\n'
        html_content += '\n'.join(html_parts)
        html_content += '\n</body></html>'
        
        chapter.content = html_content
        chapter.add_item(css_item)
        book.add_item(chapter)
        chapters.append(chapter)
        
        print(f'  Strona {page_num + 1}: {page_text_chars} znaków tekstu, {len(page_images)} obrazów')
    
    doc.close()
    
    # Navigation
    book.toc = chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav'] + chapters
    
    # Write EPUB
    epub_buffer = io.BytesIO()
    epub.write_epub(epub_buffer, book)
    epub_buffer.seek(0)
    epub_bytes = epub_buffer.getvalue()
    
    Path(output_path).write_bytes(epub_bytes)
    
    print(f'\n✅ EPUB zapisany: {output_path}')
    print(f'   Rozmiar: {len(epub_bytes)//1024} KB')
    print(f'   Łączny tekst: {total_text_chars} znaków')
    print(f'   Łączne obrazy: {total_images}')
    print(f'   Rozdziały: {len(chapters)}')
    
    return epub_bytes

# Test it
example_dir = Path('example')
magazine = example_dir / '9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf'

# Create 5-page test
import fitz
doc = fitz.open(str(magazine))
test_pdf = example_dir / 'magazine_5pages_hybrid.pdf'
test_doc = fitz.open()
for i in range(min(5, len(doc))):
    test_doc.insert_pdf(doc, from_page=i, to_page=i)
test_doc.save(str(test_pdf))
test_doc.close()
doc.close()

# Convert with hybrid method
output_epub = example_dir / 'magazine_5pages_hybrid.epub'
convert_magazine_hybrid(str(test_pdf), str(output_epub))

# Verify
import zipfile
z = zipfile.ZipFile(output_epub)
chapters = [f for f in z.namelist() if f.endswith('.xhtml') and 'chapter' in f]

print('\n=== WERYFIKACJA JAKOŚCI ===')
if chapters:
    content = z.read(chapters[0]).decode('utf-8')
    print(f'Rozdział 1: {len(content)} znaków')
    print(f'Ma tekst: {len(content) > 500}')
    print(f'Ma obrazy: {"img" in content}')
    
    # Sprawdź czy nie ma pełnoekranowych screenshotów
    has_full_page_images = any('page_' in f and f.endswith('.jpeg') for f in z.namelist())
    print(f'Ma pełnoekranowe screeny: {has_full_page_images}')
    
    if not has_full_page_images and len(content) > 500:
        print('\n✅ SUKCES! Tekst wyciągnięty, brak screenshotów!')
    else:
        print('\n❌ Problem: Nadal robi screeny lub mało tekstu')

# Cleanup
test_pdf.unlink(missing_ok=True)
