param(
    [int]$Port = 5001
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$watchFiles = @(
    "app.py",
    "converter.py",
    "kindle_semantic_cleanup.py",
    "publication_pipeline.py",
    "publication_analysis.py",
    "magazine_kindle_reflow.py",
    "premium_reflow.py",
    "pymupdf_chess_extractor.py"
) | ForEach-Object { Join-Path $repoRoot $_ }

$latestWrite = ($watchFiles | Where-Object { Test-Path $_ } | Get-Item | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonPath
    )

    try {
        $probeOutput = & $PythonPath -c "import importlib.util; required=('flask','fitz','docx'); missing=[name for name in required if importlib.util.find_spec(name) is None]; print('|'.join(missing))" 2>$null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }

        $missing = ($probeOutput | Out-String).Trim()
        return [string]::IsNullOrWhiteSpace($missing)
    } catch {
        return $false
    }
}

function Resolve-PythonPath {
    param(
        [string]$ListenerPythonPath,
        [string]$RepoRoot
    )

    $candidates = @()
    if ($ListenerPythonPath) {
        $candidates += $ListenerPythonPath
    }

    $candidates += "python"

    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $candidates += $venvPython
    }

    foreach ($candidate in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if (Test-PythonCandidate -PythonPath $candidate) {
            return $candidate
        }
    }

    throw "No Python interpreter with required runtime modules (flask, fitz, docx) was found."
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
$listenerPythonPath = $null
if ($listener) {
    try {
        $listenerPythonPath = (Get-Process -Id $listener.OwningProcess).Path
    } catch {
        $listenerPythonPath = $null
    }
}

$pythonPath = Resolve-PythonPath -ListenerPythonPath $listenerPythonPath -RepoRoot $repoRoot

if ($listener) {
    Stop-Process -Id $listener.OwningProcess -Force
    Start-Sleep -Seconds 2
}

$stdoutLog = Join-Path $repoRoot "tmp_server_5001.out.log"
$stderrLog = Join-Path $repoRoot "tmp_server_5001.err.log"
if (Test-Path $stdoutLog) { Remove-Item $stdoutLog -Force }
if (Test-Path $stderrLog) { Remove-Item $stderrLog -Force }

$process = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList "kindlemaster.py", "serve", "--runtime", "waitress", "--port", "$Port" `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$deadline = (Get-Date).AddSeconds(45)
$ready = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 800
    $portReady = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $process.Id }
    if (-not $portReady) {
        continue
    }

    try {
        $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/" -f $Port) -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        continue
    }
}

if (-not $ready) {
    throw "Server failed to become ready on port $Port."
}

$startedProcess = Get-Process -Id $process.Id
if ($startedProcess.StartTime -lt $latestWrite) {
    throw ("Server started at {0}, but latest watched backend file is newer ({1})." -f $startedProcess.StartTime, $latestWrite)
}

[pscustomobject]@{
    Port = $Port
    Pid = $startedProcess.Id
    PythonPath = $pythonPath
    StartTime = $startedProcess.StartTime
    LatestWatchedFileWrite = $latestWrite
    StdoutLog = $stdoutLog
    StderrLog = $stderrLog
} | Format-List
