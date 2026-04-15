param(
  [string]$PdfPath = "samples\\pdf\\strefa-pmi-52-2026.pdf"
)

Set-Location $PSScriptRoot
python kindlemaster_end_to_end.py --pdf $PdfPath
