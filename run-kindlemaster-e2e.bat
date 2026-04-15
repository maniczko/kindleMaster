@echo off
cd /d "%~dp0"
set PDF_PATH=%~1
if "%PDF_PATH%"=="" set PDF_PATH=samples\pdf\strefa-pmi-52-2026.pdf
python kindlemaster_end_to_end.py --pdf "%PDF_PATH%"
