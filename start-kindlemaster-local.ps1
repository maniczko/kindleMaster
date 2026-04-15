Set-Location $PSScriptRoot

$connections = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
if ($connections) {
  $ids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($id in $ids) {
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Seconds 1
}

$scriptPath = (Resolve-Path ".\kindlemaster_local_server.py").Path
python $scriptPath --host 127.0.0.1 --port 5000
