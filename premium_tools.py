from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _command_available(name: str) -> bool:
    return shutil.which(name) is not None


def _first_existing(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def find_java_executable() -> Path | None:
    env_java_home = os.environ.get("JAVA_HOME")
    candidates = []
    if env_java_home:
        candidates.append(Path(env_java_home) / "bin" / "java.exe")
        candidates.append(Path(env_java_home) / "bin" / "java")

    which_java = shutil.which("java")
    if which_java:
        candidates.append(Path(which_java))

    cwd = Path.cwd()
    tools_dir = cwd / "tools"
    if tools_dir.exists():
        candidates.extend(tools_dir.rglob("bin/java.exe"))
        candidates.extend(tools_dir.rglob("bin/java"))

    return _first_existing(candidates)


def find_tesseract_executable() -> Path | None:
    env_tesseract = os.environ.get("TESSERACT_CMD")
    candidates = []
    if env_tesseract:
        candidates.append(Path(env_tesseract))

    which_tesseract = shutil.which("tesseract")
    if which_tesseract:
        candidates.append(Path(which_tesseract))

    candidates.extend(
        [
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ]
    )

    cwd = Path.cwd()
    tools_dir = cwd / "tools"
    if tools_dir.exists():
        candidates.extend(tools_dir.rglob("tesseract.exe"))

    return _first_existing(candidates)


def find_ocrmypdf_executable() -> Path | None:
    candidates = []
    env_ocrmypdf = os.environ.get("OCRMYPDF_CMD")
    if env_ocrmypdf:
        candidates.append(Path(env_ocrmypdf))

    which_ocrmypdf = shutil.which("ocrmypdf")
    if which_ocrmypdf:
        candidates.append(Path(which_ocrmypdf))

    scripts_dir = sysconfig.get_path("scripts")
    if scripts_dir:
        candidates.append(Path(scripts_dir) / "ocrmypdf.exe")
        candidates.append(Path(scripts_dir) / "ocrmypdf")

    python_dir = Path(sys.executable).resolve().parent
    candidates.append(python_dir / "ocrmypdf.exe")
    candidates.append(python_dir / "ocrmypdf")
    candidates.append(python_dir / "Scripts" / "ocrmypdf.exe")

    cwd = Path.cwd()
    tools_dir = cwd / "tools"
    if tools_dir.exists():
        candidates.extend(tools_dir.rglob("ocrmypdf.exe"))
        candidates.extend(tools_dir.rglob("ocrmypdf"))

    return _first_existing(candidates)


def find_qpdf_executable() -> Path | None:
    candidates = []
    which_qpdf = shutil.which("qpdf")
    if which_qpdf:
        candidates.append(Path(which_qpdf))

    candidates.extend(
        sorted(Path(r"C:\Program Files").glob("qpdf*"))
        if Path(r"C:\Program Files").exists()
        else []
    )

    expanded: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir():
            expanded.append(candidate / "bin" / "qpdf.exe")
            expanded.append(candidate / "qpdf.exe")
        else:
            expanded.append(candidate)

    cwd = Path.cwd()
    tools_dir = cwd / "tools"
    if tools_dir.exists():
        expanded.extend(tools_dir.rglob("qpdf.exe"))

    return _first_existing(expanded)


def find_ghostscript_executable() -> Path | None:
    candidates = []
    for name in ("gswin64c", "gswin32c", "gs"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    program_files = Path(r"C:\Program Files")
    if program_files.exists():
        for directory in sorted(program_files.glob("gs/gs*/bin")):
            candidates.append(directory / "gswin64c.exe")
            candidates.append(directory / "gswin32c.exe")
    cwd = Path.cwd()
    tools_dir = cwd / "tools"
    if tools_dir.exists():
        candidates.extend(tools_dir.rglob("gswin64c.exe"))
        candidates.extend(tools_dir.rglob("gswin32c.exe"))
        candidates.extend(tools_dir.rglob("gs"))
    return _first_existing(candidates)


def find_tessdata_dir() -> Path | None:
    env_tessdata = os.environ.get("TESSDATA_PREFIX")
    candidates = []
    if env_tessdata:
        candidates.append(Path(env_tessdata))

    cwd = Path.cwd()
    tools_dir = cwd / "tools"
    if tools_dir.exists():
        candidates.append(tools_dir / "tessdata")
        candidates.extend(tools_dir.rglob("tessdata"))

    tesseract_path = find_tesseract_executable()
    if tesseract_path:
        candidates.append(tesseract_path.parent / "tessdata")

    return _first_existing(candidates)


def list_tesseract_languages(tesseract_path: Path | None = None, tessdata_dir: Path | None = None) -> list[str]:
    if not tesseract_path:
        return []
    command = [str(tesseract_path), "--list-langs"]
    env = os.environ.copy()
    if tessdata_dir:
        env["TESSDATA_PREFIX"] = str(tessdata_dir)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
        )
    except Exception:
        return []

    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        return []
    return [line for line in lines[1:] if line]


def detect_toolchain() -> dict:
    java_path = find_java_executable()
    tesseract_path = find_tesseract_executable()
    ocrmypdf_path = find_ocrmypdf_executable()
    qpdf_path = find_qpdf_executable()
    ghostscript_path = find_ghostscript_executable()
    tessdata_dir = find_tessdata_dir()
    tesseract_languages = list_tesseract_languages(tesseract_path, tessdata_dir)
    epubcheck_jar = find_epubcheck_jar()
    pdfbox_jar = find_pdfbox_jar()
    ocrmypdf_ready = bool(ocrmypdf_path and tesseract_path and ghostscript_path and qpdf_path)
    return {
        "python_modules": {
            "pdfplumber": _module_available("pdfplumber"),
            "ocrmypdf": _module_available("ocrmypdf"),
        },
        "commands": {
            "java": bool(java_path),
            "tesseract": bool(tesseract_path),
            "ocrmypdf": bool(ocrmypdf_path),
            "qpdf": bool(qpdf_path),
            "ghostscript": bool(ghostscript_path),
            "pdftoppm": _command_available("pdftoppm"),
            "surya": _command_available("surya"),
            "pdfbox": _command_available("pdfbox"),
        },
        "java": {
            "found": bool(java_path),
            "path": str(java_path) if java_path else None,
        },
        "tesseract": {
            "found": bool(tesseract_path),
            "path": str(tesseract_path) if tesseract_path else None,
            "tessdata_dir": str(tessdata_dir) if tessdata_dir else None,
            "languages": tesseract_languages,
        },
        "ocrmypdf": {
            "found": bool(ocrmypdf_path),
            "path": str(ocrmypdf_path) if ocrmypdf_path else None,
            "module_found": _module_available("ocrmypdf"),
            "ready": ocrmypdf_ready,
            "notes": [] if ocrmypdf_ready else ["OCRmyPDF wymaga dodatkowych zależności systemowych; bez nich pipeline używa bezpośrednio Tesseract OCR."],
        },
        "ghostscript": {
            "found": bool(ghostscript_path),
            "path": str(ghostscript_path) if ghostscript_path else None,
        },
        "qpdf": {
            "found": bool(qpdf_path),
            "path": str(qpdf_path) if qpdf_path else None,
        },
        "epubcheck": {
            "jar_found": bool(epubcheck_jar),
            "jar_path": str(epubcheck_jar) if epubcheck_jar else None,
        },
        "pdfbox": {
            "jar_found": bool(pdfbox_jar),
            "jar_path": str(pdfbox_jar) if pdfbox_jar else None,
        },
    }


def find_epubcheck_jar() -> Path | None:
    env_path = os.environ.get("EPUBCHECK_JAR")
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    cwd = Path.cwd()
    candidates.extend(cwd.glob("epubcheck*.jar"))
    candidates.extend((cwd / "tools").rglob("epubcheck*.jar") if (cwd / "tools").exists() else [])
    candidates.extend((cwd / "bin").rglob("epubcheck*.jar") if (cwd / "bin").exists() else [])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def find_pdfbox_jar() -> Path | None:
    env_path = os.environ.get("PDFBOX_JAR")
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    cwd = Path.cwd()
    candidates.extend(cwd.glob("pdfbox-app*.jar"))
    candidates.extend((cwd / "tools").rglob("pdfbox-app*.jar") if (cwd / "tools").exists() else [])
    candidates.extend((cwd / "bin").rglob("pdfbox-app*.jar") if (cwd / "bin").exists() else [])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def run_epubcheck(epub_bytes: bytes) -> dict:
    toolchain = detect_toolchain()
    java_path = toolchain["java"]["path"]
    jar_path = toolchain["epubcheck"]["jar_path"]
    if not java_path or not jar_path:
        return {
            "status": "unavailable",
            "tool": "epubcheck",
            "messages": ["Java lub plik epubcheck.jar nie jest dostepny w srodowisku."],
        }

    with tempfile.TemporaryDirectory() as temp_dir:
        epub_path = Path(temp_dir) / "validation.epub"
        epub_path.write_bytes(epub_bytes)
        try:
            completed = subprocess.run(
                [java_path, "-jar", jar_path, str(epub_path)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except Exception as exc:
            return {
                "status": "unavailable",
                "tool": "epubcheck",
                "messages": [f"Nie udalo sie uruchomic EPUBCheck: {exc}"],
            }

    combined = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    messages = [line.strip() for line in combined.splitlines() if line.strip()]
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "tool": "epubcheck",
        "messages": messages[:50],
    }
