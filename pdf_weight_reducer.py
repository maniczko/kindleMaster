from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import fitz

from premium_tools import find_ghostscript_executable, find_qpdf_executable


VALID_COMPRESSION_PROFILES = frozenset({"safe", "balanced", "aggressive"})


@dataclass(frozen=True)
class PdfCompressionProfile:
    name: str
    color_resolution: int
    gray_resolution: int
    mono_resolution: int
    jpeg_quality: int


@dataclass(frozen=True)
class PdfCompressionResult:
    success: bool
    job_id: str
    status: str
    original_path: str
    output_path: str
    original_size_bytes: int
    compressed_size_bytes: int
    reduction_percent: float
    quality_profile: str
    method: str
    warnings: list[str]
    error: str = ""
    error_code: str = ""


class PdfCompressionUnavailable(RuntimeError):
    pass


class PdfCompressionFailed(RuntimeError):
    pass


COMPRESSION_PROFILES = {
    "safe": PdfCompressionProfile(
        name="safe",
        color_resolution=180,
        gray_resolution=180,
        mono_resolution=300,
        jpeg_quality=86,
    ),
    "balanced": PdfCompressionProfile(
        name="balanced",
        color_resolution=144,
        gray_resolution=144,
        mono_resolution=300,
        jpeg_quality=78,
    ),
    "aggressive": PdfCompressionProfile(
        name="aggressive",
        color_resolution=110,
        gray_resolution=110,
        mono_resolution=220,
        jpeg_quality=66,
    ),
}


def normalize_compression_profile(profile: str | None) -> str:
    normalized = str(profile or "balanced").strip().lower()
    return normalized if normalized in VALID_COMPRESSION_PROFILES else "balanced"


def compress_pdf(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    profile: str | None = "balanced",
    job_id: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ghostscript_path: str | Path | None = None,
    qpdf_path: str | Path | None = None,
) -> PdfCompressionResult:
    source = Path(source_path)
    if not source.is_file():
        raise PdfCompressionFailed("PDF source file does not exist.")

    resolved_profile = COMPRESSION_PROFILES[normalize_compression_profile(profile)]
    resolved_job_id = _safe_job_id(job_id)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    intermediate_path = target_dir / f"{resolved_job_id}.ghostscript.pdf"
    output_path = target_dir / f"{resolved_job_id}.pdf"

    gs = Path(ghostscript_path) if ghostscript_path else find_ghostscript_executable()
    qpdf = Path(qpdf_path) if qpdf_path else find_qpdf_executable()
    missing = []
    if not gs:
        missing.append("Ghostscript")
    if not qpdf:
        missing.append("qpdf")
    if missing:
        raise PdfCompressionUnavailable(f"PDF compression requires: {', '.join(missing)}.")

    original_size = source.stat().st_size
    original_pages = _page_count(source)
    warnings = _profile_warnings(resolved_profile.name)

    _run_command(_ghostscript_command(gs, source, intermediate_path, resolved_profile), runner)
    if not intermediate_path.is_file():
        raise PdfCompressionFailed("Ghostscript did not create a compressed PDF candidate.")

    _run_command(_qpdf_command(qpdf, intermediate_path, output_path), runner)
    if not output_path.is_file():
        raise PdfCompressionFailed("qpdf did not create a final compressed PDF.")
    _safe_unlink(intermediate_path)

    compressed_pages = _page_count(output_path)
    if compressed_pages != original_pages:
        _safe_unlink(output_path)
        raise PdfCompressionFailed(
            f"Compressed PDF page count changed from {original_pages} to {compressed_pages}."
        )

    compressed_size = output_path.stat().st_size
    if compressed_size >= original_size:
        _safe_unlink(output_path)
        reduction_percent = _reduction_percent(original_size, compressed_size)
        return PdfCompressionResult(
            success=False,
            job_id=resolved_job_id,
            status="no_reduction",
            original_path=str(source),
            output_path="",
            original_size_bytes=original_size,
            compressed_size_bytes=compressed_size,
            reduction_percent=reduction_percent,
            quality_profile=resolved_profile.name,
            method="ghostscript+qpdf",
            warnings=["Kompresja nie zmniejszyla pliku; oryginalny PDF pozostaje aktywny.", *warnings],
        )

    return PdfCompressionResult(
        success=True,
        job_id=resolved_job_id,
        status="compressed",
        original_path=str(source),
        output_path=str(output_path),
        original_size_bytes=original_size,
        compressed_size_bytes=compressed_size,
        reduction_percent=_reduction_percent(original_size, compressed_size),
        quality_profile=resolved_profile.name,
        method="ghostscript+qpdf",
        warnings=warnings,
    )


def _ghostscript_command(
    executable: str | Path,
    source: Path,
    output: Path,
    profile: PdfCompressionProfile,
) -> list[str]:
    return [
        str(executable),
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.6",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-dSubsetFonts=true",
        "-dAutoRotatePages=/None",
        "-dColorImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={profile.color_resolution}",
        "-dGrayImageDownsampleType=/Bicubic",
        f"-dGrayImageResolution={profile.gray_resolution}",
        "-dMonoImageDownsampleType=/Subsample",
        f"-dMonoImageResolution={profile.mono_resolution}",
        f"-dJPEGQ={profile.jpeg_quality}",
        f"-sOutputFile={output}",
        str(source),
    ]


def _qpdf_command(executable: str | Path, source: Path, output: Path) -> list[str]:
    return [str(executable), "--linearize", str(source), str(output)]


def _run_command(command: Sequence[str], runner: Callable[..., subprocess.CompletedProcess]) -> None:
    try:
        runner(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        stderr = _decode_process_output(error.stderr)
        stdout = _decode_process_output(error.stdout)
        details = stderr or stdout or str(error)
        raise PdfCompressionFailed(details) from error


def _page_count(path: Path) -> int:
    try:
        with fitz.open(path) as document:
            return int(document.page_count)
    except Exception as error:
        raise PdfCompressionFailed(f"PDF validation failed: {error}") from error


def _reduction_percent(original_size: int, compressed_size: int) -> float:
    if original_size <= 0:
        return 0.0
    return round((1 - (compressed_size / original_size)) * 100, 1)


def _profile_warnings(profile: str) -> list[str]:
    if profile == "aggressive":
        return [
            "Tryb agresywny moze pogorszyc drobny tekst, diagramy, OCR i rozpoznawanie FEN.",
        ]
    if profile == "balanced":
        return [
            "Tryb balanced jest kompromisem: sprawdz czy diagramy i drobny tekst pozostaly czytelne.",
        ]
    return []


def _decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _safe_job_id(value: str | None) -> str:
    candidate = "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in {"-", "_"})
    return candidate or uuid.uuid4().hex


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
