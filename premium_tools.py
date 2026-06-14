from __future__ import annotations

import importlib.util
import copy
import hashlib
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from functools import lru_cache
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
    ("scikit-learn", "sklearn", "scikit-learn"),
)

KINDLEMASTER_SKILL_NAMES: tuple[str, ...] = (
    "kindlemaster-epub-release-auditor",
    "kindlemaster-reference-repair",
    "kindlemaster-heading-toc-recovery",
    "kindlemaster-text-normalization-pl-en",
    "kindlemaster-corpus-smoke",
    "kindlemaster-workflow-operator",
    "kindlemaster-ui-runtime-debug",
)

REQUIRED_CODEX_PLUGINS: tuple[str, ...] = (
    "github@openai-curated",
    "linear@openai-curated",
    "build-web-apps@openai-curated",
    "browser-use@openai-bundled",
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


def clear_toolchain_cache() -> None:
    _detect_toolchain_cached.cache_clear()


def _read_git_hooks_path(repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip().strip('"').replace("\\", "/").rstrip("/")


def _read_codex_config(config_path: Path) -> tuple[dict[str, Any] | None, str]:
    if not config_path.exists():
        return None, "missing"
    try:
        return tomllib.loads(config_path.read_text(encoding="utf-8")), ""
    except Exception as exc:
        return None, f"parse_error: {exc}"


def _agent_check(status: str, *, details: dict[str, Any] | None = None, notes: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "details": details or {},
        "notes": notes or [],
    }


def _plugin_enabled(config: dict[str, Any], plugin_name: str) -> bool:
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return False
    payload = plugins.get(plugin_name)
    return isinstance(payload, dict) and payload.get("enabled") is True


def detect_agent_readiness(*, repo_root: str | Path | None = None) -> dict[str, Any]:
    resolved_root = Path(repo_root or Path.cwd()).resolve()
    config_path = resolved_root / ".codex" / "config.toml"
    config, config_error = _read_codex_config(config_path)
    checks: dict[str, dict[str, Any]] = {}

    checks["codex_config"] = _agent_check(
        "supported" if config is not None else "unsupported",
        details={"path": str(config_path), "present": config_path.exists(), "parsable": config is not None},
        notes=[] if config is not None else [config_error],
    )

    playwright_args: list[str] = []
    if config:
        mcp_servers = config.get("mcp_servers")
        if isinstance(mcp_servers, dict):
            playwright = mcp_servers.get("playwright")
            if isinstance(playwright, dict) and isinstance(playwright.get("args"), list):
                playwright_args = [str(item) for item in playwright["args"]]
    has_pinned_playwright_mcp = any(arg.startswith("@playwright/mcp@") and arg != "@playwright/mcp@latest" for arg in playwright_args)
    checks["playwright_mcp_pin"] = _agent_check(
        "supported" if has_pinned_playwright_mcp else "unsupported",
        details={"args": playwright_args},
        notes=[] if has_pinned_playwright_mcp else ["Playwright MCP must be pinned and must not use @latest."],
    )

    missing_plugins = [plugin for plugin in REQUIRED_CODEX_PLUGINS if not (config and _plugin_enabled(config, plugin))]
    checks["plugins"] = _agent_check(
        "supported" if not missing_plugins else "unsupported",
        details={"required": list(REQUIRED_CODEX_PLUGINS), "missing": missing_plugins},
    )

    skills_root = Path.home() / ".codex" / "skills"
    missing_skills = [skill for skill in KINDLEMASTER_SKILL_NAMES if not (skills_root / skill).exists()]
    checks["skills"] = _agent_check(
        "supported" if not missing_skills else "degraded",
        details={"root": str(skills_root), "required": list(KINDLEMASTER_SKILL_NAMES), "missing": missing_skills},
        notes=[] if not missing_skills else ["Some KindleMaster Codex skills are not installed for this user."],
    )

    hooks_path = _read_git_hooks_path(resolved_root)
    hook_files = {
        "pre-commit": (resolved_root / ".githooks" / "pre-commit").is_file(),
        "pre-push": (resolved_root / ".githooks" / "pre-push").is_file(),
    }
    hooks_ready = hooks_path == ".githooks" and all(hook_files.values())
    checks["git_hooks"] = _agent_check(
        "supported" if hooks_ready else "degraded",
        details={"core_hooks_path": hooks_path, "expected": ".githooks", "hook_files": hook_files},
        notes=[] if hooks_ready else ["Run `python scripts/install_git_hooks.py --install`."],
    )

    claude_local = resolved_root / ".claude" / "settings.local.json"
    if claude_local.exists():
        try:
            claude_text = claude_local.read_text(encoding="utf-8")
            stale_markers = [marker for marker in ("python app.py", "localhost:5000") if marker in claude_text]
            claude_status = "supported" if not stale_markers else "degraded"
            claude_notes = [] if not stale_markers else [f"Stale Claude local markers: {', '.join(stale_markers)}."]
            claude_details = {"path": str(claude_local), "present": True, "stale_markers": stale_markers}
        except Exception as exc:
            claude_status = "degraded"
            claude_notes = [f"Could not read Claude local settings: {exc}"]
            claude_details = {"path": str(claude_local), "present": True, "stale_markers": []}
    else:
        claude_status = "supported"
        claude_notes = ["No local Claude settings file was found; this is acceptable."]
        claude_details = {"path": str(claude_local), "present": False, "stale_markers": []}
    checks["claude_local_settings"] = _agent_check(claude_status, details=claude_details, notes=claude_notes)

    statuses = [check["status"] for check in checks.values()]
    if any(status == "unsupported" for status in statuses):
        overall_status = "unsupported"
    elif any(status == "degraded" for status in statuses):
        overall_status = "degraded"
    else:
        overall_status = "supported"

    return {
        "status": overall_status,
        "checks": checks,
        "notes": [
            "Agent readiness covers Codex config, local hooks, installed KindleMaster skills, and stale local agent settings.",
        ],
    }


def detect_toolchain(*, refresh: bool = False) -> dict:
    if refresh:
        clear_toolchain_cache()
    return copy.deepcopy(_detect_toolchain_cached())


@lru_cache(maxsize=1)
def _detect_toolchain_cached() -> dict:
    return _detect_toolchain_uncached()


def _detect_toolchain_uncached() -> dict:
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

    agent_readiness = detect_agent_readiness(repo_root=Path.cwd())

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
    try:
        from openai_quality_provider import openai_quality_configuration_status

        openai_quality = openai_quality_configuration_status(cwd=Path.cwd())
    except Exception as error:
        openai_quality = {
            "enabled": False,
            "api_key_present": False,
            "model": "",
            "base_url": "",
            "mode": "evidence_only",
            "evidence_only": True,
            "full_document_upload": False,
            "error": str(error),
        }
    openai_quality_enabled = bool(openai_quality.get("enabled"))
    openai_quality_key_present = bool(openai_quality.get("api_key_present"))
    if openai_quality_enabled and openai_quality_key_present:
        openai_quality_status = "supported"
        openai_quality_missing: list[str] = []
    elif openai_quality_enabled:
        openai_quality_status = "degraded"
        openai_quality_missing = ["OPENAI_API_KEY"]
    else:
        openai_quality_status = "unavailable"
        openai_quality_missing = ["KINDLEMASTER_OPENAI_QUALITY=1"]
    openai_quality_notes = [
        "Optional evidence-only reviewer for short OCR/TOC/flow samples.",
        "It never mutates EPUB bytes automatically.",
    ]
    try:
        from deepseek_quality_provider import deepseek_audit_configuration_status

        deepseek_audit = deepseek_audit_configuration_status(cwd=Path.cwd())
    except Exception as error:
        deepseek_audit = {
            "enabled": False,
            "api_key_present": False,
            "model": "",
            "base_url": "",
            "mode": "evidence_only",
            "evidence_only": True,
            "full_document_upload": False,
            "mutates_output": False,
            "error": str(error),
        }
    deepseek_audit_enabled = bool(deepseek_audit.get("enabled"))
    deepseek_audit_key_present = bool(deepseek_audit.get("api_key_present"))
    if deepseek_audit_enabled and deepseek_audit_key_present:
        deepseek_audit_status = "supported"
        deepseek_audit_missing: list[str] = []
    elif deepseek_audit_enabled:
        deepseek_audit_status = "degraded"
        deepseek_audit_missing = ["DEEPSEEK_API_KEY"]
    else:
        deepseek_audit_status = "unavailable"
        deepseek_audit_missing = ["KINDLEMASTER_DEEPSEEK_AUDIT=1"]
    deepseek_audit_notes = [
        "Optional audit-first reviewer for glyph, chess layout, and compact quality evidence.",
        "It never mutates EPUB, PGN, FEN, or TOC output.",
    ]

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
        "openai_quality_review": _capability_payload(
            support_level="optional",
            status=openai_quality_status,
            description="Optional OpenAI reviewer for short quality samples; evidence-only and opt-in.",
            missing_requirements=openai_quality_missing,
            notes=openai_quality_notes,
        ),
        "deepseek_audit_review": _capability_payload(
            support_level="optional",
            status=deepseek_audit_status,
            description="Optional DeepSeek audit reviewer for bounded diagnostics; evidence-only and opt-in.",
            missing_requirements=deepseek_audit_missing,
            notes=deepseek_audit_notes,
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
            "sklearn": developer_requirements["packages"]["scikit-learn"]["installed"],
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
        "openai_quality": openai_quality,
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
                        "Adds pytest, coverage, Playwright, Waitress, and scikit-learn for local verification and ML training lanes.",
                        "Chromium remains a separate local install even after requirements-dev.txt is installed.",
                    ],
                },
            },
            "notes": [
                "Bootstrap manages Python packages only.",
                "Java, EPUBCheck, Tesseract, Ghostscript, qpdf, PDFBox, and Chromium remain separately managed local tools.",
            ],
        },
        "agent_readiness": agent_readiness,
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


_EPUBCHECK_RESULT_CACHE: dict[str, dict] = {}


def clear_epubcheck_cache() -> None:
    _EPUBCHECK_RESULT_CACHE.clear()


def run_epubcheck(epub_bytes: bytes) -> dict:
    digest = hashlib.sha256(epub_bytes).hexdigest()
    cached = _EPUBCHECK_RESULT_CACHE.get(digest)
    if cached is not None:
        return copy.deepcopy(cached)

    toolchain = detect_toolchain()
    java_path = toolchain["java"]["path"]
    jar_path = toolchain["epubcheck"]["jar_path"]
    if not java_path or not jar_path:
        result = {
            "status": "unavailable",
            "tool": "epubcheck",
            "messages": ["Java lub plik epubcheck.jar nie jest dostepny w srodowisku."],
        }
        _EPUBCHECK_RESULT_CACHE[digest] = copy.deepcopy(result)
        return result

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
            result = {
                "status": "unavailable",
                "tool": "epubcheck",
                "messages": [f"Nie udalo sie uruchomic EPUBCheck: {exc}"],
            }
            _EPUBCHECK_RESULT_CACHE[digest] = copy.deepcopy(result)
            return result

    combined = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    messages = [line.strip() for line in combined.splitlines() if line.strip()]
    result = {
        "status": "passed" if completed.returncode == 0 else "failed",
        "tool": "epubcheck",
        "messages": messages[:50],
    }
    _EPUBCHECK_RESULT_CACHE[digest] = copy.deepcopy(result)
    return result
