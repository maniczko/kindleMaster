from converter import detect_pdf_type

magazine = 'example/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf'
result = detect_pdf_type(magazine)

with open('detection_result.txt', 'w', encoding='utf-8') as f:
    f.write('=== DETEKCJA TYPU MAGAZYNU ===\n\n')
    for key, value in result.items():
        f.write(f'{key}: {value}\n')
    
    f.write('\n=== PROBLEM ===\n')
    if result['is_scanned']:
        f.write('❌ Wykryto jako SKAN - dlatego robi screeny zamiast czytać tekst!\n')
        f.write('   Rozwiązanie: Poprawić logikę detekcji\n')
    else:
        f.write('✅ Wykryto poprawnie jako tekst\n')

print('Wynik zapisany do detection_result.txt')
