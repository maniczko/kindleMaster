#!/usr/bin/env python3
"""
HYBRYDOWY KONWERTER MAGAZYNÓW v2
- Tekst + obrazy osobno (BEZ screenshotów!)
- OPTYMALIZACJA ROZMIARU (kompresja obrazów)
- Diagramy szachowe z numerkami
"""

import fitz
import io
import html as html_module
from pathlib import Path
from PIL import Image
import numpy as np
from converter import ConversionConfig, _extract_pdf_metadata, optimize_image_data
from ebooklib import epub


def convert_magazine_hybrid(pdf_path, output_path, config=None):
    """
    Hybrid conversion for magazines:
    - Extract text with positions
    - Extract and OPTIMIZE images separately
    - Build EPUB with both (no full-page screenshots!)
    - Reduced file size through compression
    """
    if config is None:
        config = ConversionConfig()
    
    print('=== HYBRYDOWA KONWERSJA MAGAZYNU v2 (zoptymalizowana) ===\n')
    
    doc = fitz.open(pdf_path)
    pdf_metadata = _extract_pdf_metadata(pdf_path)
    
    book = epub.EpubBook()
    book.set_identifier('urn:uuid:' + epub.uuid.uuid4().hex)
    book.set_title(pdf_metadata.get('title', 'Magazine'))
    book.set_language('pl')
    book.add_author(pdf_metadata.get('author', 'Unknown'))
    
    # Add optimized CSS
    css_content = b'''\
body { font-family: Georgia, serif; line-height: 1.6; margin: 1em; color: #1a1a1a; }
h1 { font-size: 2em; margin: 1em 0 0.5em; }
h2 { font-size: 1.5em; margin: 0.8em 0 0.4em; }
h3 { font-size: 1.2em; margin: 0.6em 0 0.3em; }
.figure { text-align: center; margin: 1.5em 0; }
.figure img { max-width: 100%; height: auto; max-height: 600px; }
.chess-diagram { max-width: 300px !important; }
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
    total_image_size_before = 0
    total_image_size_after = 0
    
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
                # Regular paragraph
                if len(block_text.strip()) > 200:
                    html_parts.append(f'<div class="column"><p>{html_module.escape(block_text.strip())}</p></div>')
                else:
                    html_parts.append(f'<p>{html_module.escape(block_text.strip())}</p>')
        
        # Extract and OPTIMIZE images
        page_images = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = page.parent.extract_image(xref)
                if base_image and base_image.get('image'):
                    original_size = len(base_image['image'])
                    total_image_size_before += original_size
                    
                    # OPTIMIZE image
                    optimized_data = optimize_image_data(base_image['image'], config)
                    optimized_size = len(optimized_data)
                    total_image_size_after += optimized_size
                    
                    total_images += 1
                    img_filename = f'img_p{page_num}_{total_images}.jpg'
                    
                    page_images.append({
                        'filename': img_filename,
                        'data': optimized_data,
                        'extension': 'jpeg',
                    })
                    
                    # Add to EPUB
                    img_item = epub.EpubItem(
                        uid=f'image_{page_num}_{total_images}',
                        file_name=f'images/{img_filename}',
                        media_type='image/jpeg',
                        content=optimized_data,
                    )
                    book.add_item(img_item)
                    
                    # Add to HTML with size constraints
                    html_parts.append(f'<div class="figure"><img src="images/{img_filename}" alt="" style="max-width: 100%; height: auto; max-height: 600px;"/></div>')
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
    
    # Compression stats
    compression_ratio = (1 - total_image_size_after / total_image_size_before) * 100 if total_image_size_before > 0 else 0
    
    print(f'\n✅ EPUB zapisany: {output_path}')
    print(f'   Rozmiar: {len(epub_bytes)//1024} KB')
    print(f'   Łączny tekst: {total_text_chars} znaków')
    print(f'   Łączne obrazy: {total_images}')
    print(f'   Kompresja obrazów: {compression_ratio:.1f}% (z {total_image_size_before//1024} KB do {total_image_size_after//1024} KB)')
    print(f'   Rozdziały: {len(chapters)}')
    
    return epub_bytes


# Test if run directly
if __name__ == '__main__':
    from pathlib import Path
    example_dir = Path('example')
    magazine = example_dir / '9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf'
    
    # Create 5-page test
    import fitz
    doc = fitz.open(str(magazine))
    test_pdf = example_dir / 'magazine_5pages_v2.pdf'
    test_doc = fitz.open()
    for i in range(min(5, len(doc))):
        test_doc.insert_pdf(doc, from_page=i, to_page=i)
    test_doc.save(str(test_pdf))
    test_doc.close()
    doc.close()
    
    # Convert
    output = example_dir / 'magazine_5pages_v2.epub'
    convert_magazine_hybrid(str(test_pdf), str(output))
    
    # Cleanup
    test_pdf.unlink(missing_ok=True)
