param(
    [string]$VenvPath = ".venv",
    [switch]$RuntimeOnly
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $VenvPath)) {
    python -m venv $VenvPath
}

$python = Join-Path $VenvPath "Scripts\\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment python not found at $python"
}

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt

if (-not $RuntimeOnly) {
    & $python -m pip install -r requirements-dev.txt
}

& $python -c "from premium_tools import detect_toolchain; import json; print(json.dumps(detect_toolchain(), ensure_ascii=False, indent=2))"
