#!/usr/bin/env python3
"""
HYBRYDOWY KONWERTER MAGAZYNÓW v3 - PEŁNA OPTYMALIZACJA
- Tekst + obrazy osobno (BEZ screenshotów!)
- AGRESYWNA KOMPRESJA (max 800px, JPEG 75%)
- Mały rozmiar pliku EPUB
"""

import fitz
import io
import html as html_module
from pathlib import Path
from PIL import Image
from converter import ConversionConfig, _extract_pdf_metadata, strip_emails
from ebooklib import epub


def _sanitize_xhtml_text(text: str) -> str:
    """Remove XML-invalid control characters from extracted text."""
    return ''.join(ch for ch in text if ch in '\t\n\r' or ord(ch) >= 32)


def convert_magazine_optimized(pdf_path, output_path, config=None):
    """
    Optimized hybrid conversion for magazines.
    CRITICAL: Reduces EPUB file size significantly.
    """
    if config is None:
        config = ConversionConfig()
    
    print('=== HYBRYDOWA KONWERSJA MAGAZYNU v3 (ZOPTYMALIZOWANA) ===\n')
    
    doc = fitz.open(pdf_path)
    pdf_metadata = _extract_pdf_metadata(pdf_path)
    
    book = epub.EpubBook()
    book.set_identifier('urn:uuid:' + epub.uuid.uuid4().hex)
    book.set_title(pdf_metadata.get('title', 'Magazine'))
    book.set_language('pl')
    book.add_author(pdf_metadata.get('author', 'Unknown'))
    
    # Compact CSS
    css_content = b'''\
body{font-family:Georgia,serif;line-height:1.6;margin:1em;color:#1a1a1a}
h1{font-size:2em;margin:1em 0 .5em}h2{font-size:1.5em;margin:.8em 0 .4em}
h3{font-size:1.2em;margin:.6em 0 .3em}.figure{text-align:center;margin:1.5em 0}
.figure img{max-width:100%;height:auto;max-height:600px}
.column{column-count:2;column-gap:2em}
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
    total_size_before = 0
    total_size_after = 0
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Extract text
        text_dict = page.get_text('dict', flags=fitz.TEXT_PRESERVE_WHITESPACE)
        
        html_parts = []
        page_text_chars = 0
        
        for block in text_dict.get('blocks', []):
            if block.get('type') != 0:
                continue
            
            block_text = ''
            font_sizes = []
            
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    text = _sanitize_xhtml_text(strip_emails(span.get('text', '')))
                    block_text += text
                    page_text_chars += len(text)
                    font_sizes.append(span.get('size', 12))
            
            if not block_text.strip():
                continue
            
            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12
            
            if avg_font_size > 24:
                html_parts.append(f'<h1>{html_module.escape(block_text.strip())}</h1>')
            elif avg_font_size > 18:
                html_parts.append(f'<h2>{html_module.escape(block_text.strip())}</h2>')
            elif avg_font_size > 14:
                html_parts.append(f'<h3>{html_module.escape(block_text.strip())}</h3>')
            else:
                if len(block_text.strip()) > 200:
                    html_parts.append(f'<div class="column"><p>{html_module.escape(block_text.strip())}</p></div>')
                else:
                    html_parts.append(f'<p>{html_module.escape(block_text.strip())}</p>')
        
        # Extract and AGGRESSIVELY OPTIMIZE images
        page_images = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = page.parent.extract_image(xref)
                if base_image and base_image.get('image'):
                    original_size = len(base_image['image'])
                    total_size_before += original_size
                    
                    # Load image
                    img = Image.open(io.BytesIO(base_image['image']))
                    orig_width, orig_height = img.size
                    
                    # DOWNSCALE to max 800px width (CRITICAL for EPUB size!)
                    max_width = 800
                    if orig_width > max_width:
                        ratio = max_width / orig_width
                        new_width = max_width
                        new_height = int(orig_height * ratio)
                        img = img.resize((new_width, new_height), Image.LANCZOS)
                    
                    # Convert to RGB (for JPEG)
                    if img.mode in ("RGBA", "LA", "P"):
                        background = Image.new("RGB", img.size, (255, 255, 255))
                        if img.mode == "P":
                            img = img.convert("RGBA")
                        background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
                        img = background
                    elif img.mode != "RGB":
                        img = img.convert("RGB")
                    
                    # Save as JPEG with quality 75 (optimal for EPUB)
                    optimized_buffer = io.BytesIO()
                    img.save(optimized_buffer, format="JPEG", quality=75, optimize=True, progressive=True)
                    optimized_data = optimized_buffer.getvalue()
                    
                    optimized_size = len(optimized_data)
                    total_size_after += optimized_size
                    
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
                    
                    # Add to HTML with size constraints for chess diagrams
                    # Chess diagrams should be max 300px wide
                    is_chess = orig_width < 500 and orig_height < 500 and 'chess' in base_image.get('ext', '').lower()
                    max_w = 300 if is_chess else 800
                    
                    html_parts.append(f'<div class="figure"><img src="images/{img_filename}" alt="" style="max-width: {max_w}px; height: auto;"/></div>')
            except Exception as e:
                print(f'  Warning: Could not process image: {e}')
        
        total_text_chars += page_text_chars
        
        # Create chapter
        chapter = epub.EpubHtml(
            title=f'Strona {page_num + 1}',
            file_name=f'chapter_{page_num+1:03d}.xhtml',
            lang='pl',
        )
        
        if not html_parts:
            html_parts.append('<p></p>')

        html_content = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            f'<title>Strona {page_num + 1}</title>'
            '</head><body>\n'
            + '\n'.join(html_parts) +
            '\n</body></html>'
        )
        chapter.content = html_content
        chapter.add_item(css_item)
        book.add_item(chapter)
        chapters.append(chapter)
        
        print(f'  Page {page_num + 1}: {page_text_chars} chars, {len(page_images)} images')
    
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
    
    # Stats
    compression = (1 - total_size_after / total_size_before) * 100 if total_size_before > 0 else 0
    
    print(f'\nSaved EPUB: {output_path}')
    print(f'   Size: {len(epub_bytes)//1024} KB')
    print(f'   Text: {total_text_chars} chars')
    print(f'   Images: {total_images}')
    print(f'   Image compression: {compression:.1f}% (from {total_size_before//1024} KB to {total_size_after//1024} KB)')
    print(f'   Chapters: {len(chapters)}')
    
    return epub_bytes


if __name__ == '__main__':
    example_dir = Path('example')
    magazine = example_dir / '9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf'
    
    import fitz
    doc = fitz.open(str(magazine))
    test_pdf = example_dir / 'magazine_5p_v3.pdf'
    test_doc = fitz.open()
    for i in range(min(5, len(doc))):
        test_doc.insert_pdf(doc, from_page=i, to_page=i)
    test_doc.save(str(test_pdf))
    test_doc.close()
    doc.close()
    
    output = example_dir / 'magazine_v3_optimized.epub'
    convert_magazine_optimized(str(test_pdf), str(output))
    
    test_pdf.unlink(missing_ok=True)
