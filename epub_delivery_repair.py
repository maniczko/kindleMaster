from __future__ import annotations

import io
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


AUTO_REPAIR_NOT_RUN = "not_run"
AUTO_REPAIR_APPLIED = "applied"
AUTO_REPAIR_REJECTED = "rejected"
AUTO_REPAIR_FAILED = "failed"
AUTO_REPAIR_SKIPPED = "skipped"


def empty_auto_repair_payload(*, status: str = AUTO_REPAIR_NOT_RUN, reason: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "actions": [],
        "quality_selection": {},
        "selected_candidate": "",
        "rejected_candidate": "",
        "selected_score": None,
        "rejected_score": None,
        "before_blockers": [],
        "after_blockers": [],
        "reports": {},
        "error": "",
    }
    if reason:
        payload["reason"] = reason
    return payload


@dataclass
class DeliveryRepairResult:
    status: str
    epub_bytes: bytes
    actions: list[str] = field(default_factory=list)
    quality_selection: dict[str, Any] = field(default_factory=dict)
    reports: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    reason: str = ""

    def to_public_dict(
        self,
        *,
        before_blockers: list[dict[str, Any]] | None = None,
        after_blockers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        selection = dict(self.quality_selection or {})
        selected_candidate = str(selection.get("selected_candidate") or selection.get("selected_stage") or "")
        rejected_candidate = str(selection.get("rejected_candidate") or selection.get("rejected_stage") or "")
        selected_score = _selected_score(selection, selected_candidate)
        rejected_score = _selected_score(selection, rejected_candidate)
        payload = empty_auto_repair_payload(status=self.status, reason=self.reason)
        payload.update(
            {
                "actions": list(dict.fromkeys(self.actions)),
                "quality_selection": selection,
                "selected_candidate": selected_candidate,
                "rejected_candidate": rejected_candidate,
                "selected_score": selected_score,
                "rejected_score": rejected_score,
                "before_blockers": list(before_blockers or []),
                "after_blockers": list(after_blockers or []),
                "reports": dict(self.reports or {}),
                "error": self.error,
            }
        )
        return payload


def has_progressive_jpeg(epub_bytes: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as archive:
            for name in archive.namelist():
                if _is_jpeg_name(name) and _is_progressive_jpeg(archive.read(name)):
                    return True
    except (OSError, zipfile.BadZipFile):
        return False
    return False


def repair_epub_for_delivery(
    epub_bytes: bytes,
    *,
    title_hint: str = "",
    author_hint: str = "",
    language_hint: str = "",
    publication_profile: str | None = None,
    expected_description: str = "",
    strict_premium: bool = False,
    run_package_recovery: bool = True,
) -> DeliveryRepairResult:
    """Build a safe delivery candidate and keep it only if quality does not regress."""

    if not epub_bytes:
        return DeliveryRepairResult(
            status=AUTO_REPAIR_FAILED,
            epub_bytes=epub_bytes,
            error="Brak bajtów EPUB do naprawy.",
        )

    try:
        candidate_bytes, image_actions = _reencode_progressive_jpegs(epub_bytes)
        actions = list(image_actions)
        reports: dict[str, Any] = {}

        if run_package_recovery:
            recovered_bytes, recovery_report = _run_package_recovery(
                candidate_bytes,
                title_hint=title_hint,
                author_hint=author_hint,
                language_hint=language_hint,
                publication_profile=publication_profile,
                expected_description=expected_description,
                strict_premium=strict_premium,
            )
            reports.update(dict(recovery_report.get("reports") or {}))
            if recovered_bytes != candidate_bytes:
                actions.append("metadata_repair")
                actions.append("package_recovery")
                candidate_bytes = recovered_bytes

        actions = list(dict.fromkeys(actions))
        if not actions or candidate_bytes == epub_bytes:
            return DeliveryRepairResult(
                status=AUTO_REPAIR_SKIPPED,
                epub_bytes=epub_bytes,
                actions=actions,
                reports=reports,
                reason="no_safe_delivery_changes",
            )

        from epub_quality_selection import select_epub_by_quality

        selection = select_epub_by_quality(
            epub_bytes,
            candidate_bytes,
            baseline_label="active",
            candidate_label="auto_repair",
        )
        selected_is_candidate = selection.report.get("selected_candidate") == "auto_repair"
        if not selected_is_candidate:
            return DeliveryRepairResult(
                status=AUTO_REPAIR_REJECTED,
                epub_bytes=selection.selected_bytes,
                actions=actions,
                quality_selection=selection.report,
                reports=reports,
                reason="quality_selection_rejected_candidate",
            )
        return DeliveryRepairResult(
            status=AUTO_REPAIR_APPLIED,
            epub_bytes=selection.selected_bytes,
            actions=actions,
            quality_selection=selection.report,
            reports=reports,
        )
    except Exception as error:
        return DeliveryRepairResult(
            status=AUTO_REPAIR_FAILED,
            epub_bytes=epub_bytes,
            error=str(error),
        )


def _run_package_recovery(
    epub_bytes: bytes,
    *,
    title_hint: str,
    author_hint: str,
    language_hint: str,
    publication_profile: str | None,
    expected_description: str,
    strict_premium: bool,
) -> tuple[bytes, dict[str, Any]]:
    from epub_quality_recovery import run_epub_publishing_quality_recovery

    with tempfile.TemporaryDirectory(prefix="kindlemaster_delivery_repair_") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "source.epub"
        source_path.write_bytes(epub_bytes)
        recovery = run_epub_publishing_quality_recovery(
            source_path,
            output_dir=temp_path / "output",
            reports_dir=temp_path / "reports",
            expected_title=title_hint,
            expected_author=author_hint,
            expected_description=expected_description,
            expected_language=language_hint,
            publication_profile=publication_profile,
            strict_premium=strict_premium,
        )
        final_epub = Path(str(recovery.get("final_epub") or ""))
        if final_epub.exists():
            return final_epub.read_bytes(), recovery
        return epub_bytes, recovery


def _reencode_progressive_jpegs(epub_bytes: bytes) -> tuple[bytes, list[str]]:
    actions: list[str] = []
    source_buffer = io.BytesIO(epub_bytes)
    output_buffer = io.BytesIO()
    changed = False
    with zipfile.ZipFile(source_buffer, "r") as source_zip:
        with zipfile.ZipFile(output_buffer, "w") as target_zip:
            for info in source_zip.infolist():
                data = source_zip.read(info.filename)
                if _is_jpeg_name(info.filename) and _is_progressive_jpeg(data):
                    data = _reencode_jpeg_baseline(data)
                    changed = True
                target_info = zipfile.ZipInfo(info.filename, info.date_time)
                target_info.comment = info.comment
                target_info.extra = info.extra
                target_info.internal_attr = info.internal_attr
                target_info.external_attr = info.external_attr
                target_info.create_system = info.create_system
                target_info.compress_type = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
                target_zip.writestr(target_info, data)
    if changed:
        actions.append("reencode_progressive_jpeg")
        return output_buffer.getvalue(), actions
    return epub_bytes, actions


def _reencode_jpeg_baseline(data: bytes) -> bytes:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=92, optimize=True, progressive=False)
        return output.getvalue()


def _is_jpeg_name(name: str) -> bool:
    normalized = name.lower()
    return normalized.endswith(".jpg") or normalized.endswith(".jpeg")


def _is_progressive_jpeg(data: bytes) -> bool:
    if not data.startswith(b"\xff\xd8"):
        return False
    index = 2
    length = len(data)
    while index + 1 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        while index < length and data[index] == 0xFF:
            index += 1
        if index >= length:
            break
        marker = data[index]
        index += 1
        if marker == 0xC2:
            return True
        if marker in {0x01, *range(0xD0, 0xD8)}:
            continue
        if marker in {0xDA, 0xD9}:
            break
        if index + 1 >= length:
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2:
            break
        index += segment_length
    return False


def _selected_score(selection: Mapping[str, Any], label: str) -> float | int | None:
    if not label:
        return None
    for candidate in selection.get("candidates", []) or []:
        if not isinstance(candidate, Mapping):
            continue
        if str(candidate.get("label") or "") == label:
            score = candidate.get("premium_score")
            return score if isinstance(score, (int, float)) else None
    return None
