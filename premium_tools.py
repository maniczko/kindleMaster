from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any


RUNTIME_REQUIREMENT_MODULES: tuple[tuple[str, str, str], ...] = (
    ("flask", "flask", "Flask"),
    ("PyMuPDF", "fitz", "PyMuPDF"),
    ("ebooklib", "ebooklib", "EbookLib"),
    ("Pillow", "PIL", "Pillow"),
    ("beautifulsoup4", "bs4", "BeautifulSoup4"),
    ("lxml", "lxml", "lxml"),
    ("python-docx", "docx", "python-docx"),
    ("pdfplumber", "pdfplumber", "pdfplumber"),
    ("wordfreq", "wordfreq", "wordfreq"),
    ("pyphen", "pyphen", "pyphen"),
    ("rfc3986", "rfc3986", "rfc3986"),
    ("tldextract", "tldextract", "tldextract"),
)

DEV_REQUIREMENT_MODULES: tuple[tuple[str, str, str], ...] = (
    ("pytest", "pytest", "pytest"),
    ("coverage[toml]", "coverage", "coverage"),
    ("playwright", "playwright", "Playwright"),
    ("waitress", "waitress", "Waitress"),
)


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _command_available(name: str) -> bool:
    return shutil.which(name) is not None


def _first_existing(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _detect_requirement_group(requirements: tuple[tuple[str, str, str], ...]) -> dict[str, Any]:
    packages: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for package_name, module_name, display_name in requirements:
        installed = _module_available(module_name)
        packages[package_name] = {
            "module": module_name,
            "display_name": display_name,
            "installed": installed,
        }
        if not installed:
            missing.append(display_name)
    return {
        "packages": packages,
        "missing_modules": missing,
        "ready": not missing,
    }


def _surface_payload(
    *,
    support_level: str,
    status: str,
    command: str,
    description: str,
    missing_requirements: list[str],
    notes: list[str] | None = None,
    optional_followups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "support_level": support_level,
        "status": status,
        "command": command,
        "description": description,
        "missing_requirements": missing_requirements,
        "notes": notes or [],
    }
    if optional_followups:
        payload["optional_followups"] = optional_followups
    return payload


def _capability_payload(
    *,
    support_level: str,
    status: str,
    description: str,
    missing_requirements: list[str],
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "support_level": support_level,
        "status": status,
        "description": description,
        "missing_requirements": missing_requirements,
        "notes": notes or [],
    }


def find_playwright_chromium_executable() -> Path | None:
    if not _module_available("playwright"):
        return None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None

    try:
        with sync_playwright() as playwright:
            executable_path = getattr(playwright.chromium, "executable_path", "") or ""
    except Exception:
        return None

    if not executable_path:
        return None
    candidate = Path(executable_path)
    return candidate if candidate.exists() else None


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
    runtime_requirements = _detect_requirement_group(RUNTIME_REQUIREMENT_MODULES)
    developer_requirements = _detect_requirement_group(DEV_REQUIREMENT_MODULES)
    java_path = find_java_executable()
    tesseract_path = find_tesseract_executable()
    ocrmypdf_path = find_ocrmypdf_executable()
    qpdf_path = find_qpdf_executable()
    ghostscript_path = find_ghostscript_executable()
    tessdata_dir = find_tessdata_dir()
    tesseract_languages = list_tesseract_languages(tesseract_path, tessdata_dir)
    epubcheck_jar = find_epubcheck_jar()
    pdfbox_jar = find_pdfbox_jar()
    playwright_chromium_path = find_playwright_chromium_executable()
    playwright_module_found = developer_requirements["packages"]["playwright"]["installed"]
    waitress_module_found = developer_requirements["packages"]["waitress"]["installed"]
    ocrmypdf_ready = bool(ocrmypdf_path and tesseract_path and ghostscript_path and qpdf_path)
    browser_missing: list[str] = []
    if not playwright_module_found:
        browser_missing.append("Playwright Python package")
    if not playwright_chromium_path:
        browser_missing.append("Chromium browser")

    runtime_surface_missing = list(browser_missing)
    if not waitress_module_found:
        runtime_surface_missing.append("Waitress Python package")

    quick_surface = _surface_payload(
        support_level="core",
        status="supported" if runtime_requirements["ready"] else "unsupported",
        command="python kindlemaster.py test --suite quick",
        description="Fast Python-only unit and integration checks.",
        missing_requirements=list(runtime_requirements["missing_modules"]),
        notes=["No Playwright, Chromium, or Waitress requirement."],
    )

    browser_surface = _surface_payload(
        support_level="optional",
        status="supported" if not browser_missing else "unsupported",
        command="python kindlemaster.py test --suite browser",
        description="Browser polling harness coverage.",
        missing_requirements=browser_missing,
        notes=["Bootstrap installs the Playwright Python package, but Chromium remains a separate local install."],
    )

    runtime_surface = _surface_payload(
        support_level="optional",
        status="supported" if not runtime_surface_missing else "unsupported",
        command="python kindlemaster.py test --suite runtime",
        description="Live HTTP gate plus browser runtime smoke checks.",
        missing_requirements=runtime_surface_missing,
        notes=["Requires the developer bootstrap profile plus a local Chromium install."],
    )

    corpus_surface = _surface_payload(
        support_level="core",
        status="supported" if runtime_requirements["ready"] else "unsupported",
        command="python kindlemaster.py test --suite corpus",
        description="Corpus-wide smoke plus premium release-proof reports across the expanded fixture bank.",
        missing_requirements=list(runtime_requirements["missing_modules"]),
        notes=["Persists derived corpus gate reports under reports/corpus/ and output/corpus/."],
    )

    release_optional_followups = [
        {
            "surface": "browser",
            "status": browser_surface["status"],
            "missing_requirements": list(browser_surface["missing_requirements"]),
        },
        {
            "surface": "runtime",
            "status": runtime_surface["status"],
            "missing_requirements": list(runtime_surface["missing_requirements"]),
        },
    ]
    release_status = "unsupported"
    release_notes = [
        "Runs the Python release pack, quick smoke, and the corpus-wide gate.",
        "Browser and runtime follow-up suites are optional add-ons and run only when their local toolchains are available.",
    ]
    if runtime_requirements["ready"]:
        release_status = "supported"
        if any(item["status"] != "supported" for item in release_optional_followups):
            release_status = "degraded"

    verification_surfaces = {
        "quick": quick_surface,
        "corpus": corpus_surface,
        "browser": browser_surface,
        "runtime": runtime_surface,
        "release": _surface_payload(
            support_level="core",
            status=release_status,
            command="python kindlemaster.py test --suite release",
            description="Broad Python release suite with optional browser/runtime follow-up checks.",
            missing_requirements=list(runtime_requirements["missing_modules"]),
            notes=release_notes,
            optional_followups=release_optional_followups,
        ),
    }

    epubcheck_missing: list[str] = []
    if not java_path:
        epubcheck_missing.append("Java runtime")
    if not epubcheck_jar:
        epubcheck_missing.append("epubcheck.jar")

    pdfbox_missing: list[str] = []
    if not java_path:
        pdfbox_missing.append("Java runtime")
    if not pdfbox_jar:
        pdfbox_missing.append("pdfbox-app.jar")

    ocr_missing: list[str] = []
    if not tesseract_path:
        ocr_missing.append("Tesseract OCR executable")
    if not ocrmypdf_path:
        ocr_missing.append("OCRmyPDF executable")
    if not ghostscript_path:
        ocr_missing.append("Ghostscript executable")
    if not qpdf_path:
        ocr_missing.append("qpdf executable")

    ocr_status = "supported" if ocrmypdf_ready else "degraded" if tesseract_path else "unavailable"
    ocr_notes = []
    if ocr_status == "degraded":
        ocr_notes.append("The pipeline can fall back to direct Tesseract OCR when OCRmyPDF system dependencies are incomplete.")
    if ocr_status == "unavailable":
        ocr_notes.append("OCR-heavy scanned PDFs will not have the optional OCRmyPDF enhancement path.")

    conversion_capabilities = {
        "core_conversion": _capability_payload(
            support_level="core",
            status="supported" if runtime_requirements["ready"] else "unsupported",
            description="Python conversion/runtime dependencies installed from requirements.txt.",
            missing_requirements=list(runtime_requirements["missing_modules"]),
        ),
        "ocr_pipeline": _capability_payload(
            support_level="optional",
            status=ocr_status,
            description="Optional OCRmyPDF/Tesseract enhancement path for OCR-heavy PDFs.",
            missing_requirements=ocr_missing,
            notes=ocr_notes,
        ),
        "epubcheck_validation": _capability_payload(
            support_level="optional",
            status="supported" if not epubcheck_missing else "unavailable",
            description="External EPUBCheck validation executed through Java + epubcheck.jar.",
            missing_requirements=epubcheck_missing,
            notes=["KindleMaster still runs internal validators even when EPUBCheck is unavailable."],
        ),
        "pdfbox_extraction": _capability_payload(
            support_level="optional",
            status="supported" if not pdfbox_missing else "unavailable",
            description="Optional PDFBox extraction and diagnostics helpers.",
            missing_requirements=pdfbox_missing,
        ),
    }

    return {
        "python_modules": {
            "flask": runtime_requirements["packages"]["flask"]["installed"],
            "fitz": runtime_requirements["packages"]["PyMuPDF"]["installed"],
            "ebooklib": runtime_requirements["packages"]["ebooklib"]["installed"],
            "PIL": runtime_requirements["packages"]["Pillow"]["installed"],
            "bs4": runtime_requirements["packages"]["beautifulsoup4"]["installed"],
            "lxml": runtime_requirements["packages"]["lxml"]["installed"],
            "docx": runtime_requirements["packages"]["python-docx"]["installed"],
            "pdfplumber": _module_available("pdfplumber"),
            "wordfreq": runtime_requirements["packages"]["wordfreq"]["installed"],
            "pyphen": runtime_requirements["packages"]["pyphen"]["installed"],
            "rfc3986": runtime_requirements["packages"]["rfc3986"]["installed"],
            "tldextract": runtime_requirements["packages"]["tldextract"]["installed"],
            "pytest": developer_requirements["packages"]["pytest"]["installed"],
            "coverage": developer_requirements["packages"]["coverage[toml]"]["installed"],
            "playwright": playwright_module_found,
            "waitress": waitress_module_found,
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
            "chromium": bool(playwright_chromium_path),
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
        "playwright": {
            "module_found": playwright_module_found,
            "chromium_found": bool(playwright_chromium_path),
            "chromium_path": str(playwright_chromium_path) if playwright_chromium_path else None,
        },
        "waitress": {
            "module_found": waitress_module_found,
        },
        "epubcheck": {
            "jar_found": bool(epubcheck_jar),
            "jar_path": str(epubcheck_jar) if epubcheck_jar else None,
        },
        "pdfbox": {
            "jar_found": bool(pdfbox_jar),
            "jar_path": str(pdfbox_jar) if pdfbox_jar else None,
        },
        "bootstrap": {
            "entrypoint": "python kindlemaster.py bootstrap",
            "runtime_only_entrypoint": "python kindlemaster.py bootstrap --runtime-only",
            "requirements_files": {
                "runtime": "requirements.txt",
                "developer": "requirements-dev.txt",
            },
            "profiles": {
                "runtime_only": {
                    "support_level": "core",
                    "status": "supported" if runtime_requirements["ready"] else "unsupported",
                    "missing_modules": list(runtime_requirements["missing_modules"]),
                    "notes": [
                        "Installs the Python runtime used by conversion, validation, smoke, and Flask serving.",
                    ],
                },
                "developer": {
                    "support_level": "optional",
                    "status": "supported" if developer_requirements["ready"] else "unsupported",
                    "missing_modules": list(developer_requirements["missing_modules"]),
                    "manual_steps": ["python -m playwright install chromium"],
                    "notes": [
                        "Adds pytest, coverage, Playwright, and Waitress for local verification lanes.",
                        "Chromium remains a separate local install even after requirements-dev.txt is installed.",
                    ],
                },
            },
            "notes": [
                "Bootstrap manages Python packages only.",
                "Java, EPUBCheck, Tesseract, Ghostscript, qpdf, PDFBox, and Chromium remain separately managed local tools.",
            ],
        },
        "verification_surfaces": verification_surfaces,
        "conversion_capabilities": conversion_capabilities,
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
