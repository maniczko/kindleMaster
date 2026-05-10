from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PASSED = "passed"
FAILED = "failed"
BLOCKED = "blocked"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SmokeContractResult:
    status: str
    reason: str
    required_evidence: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "required_evidence": list(self.required_evidence),
            "missing_evidence": list(self.missing_evidence),
            "notes": list(self.notes),
        }


REQUIRED_RUNTIME_EVIDENCE = (
    "upload_selected",
    "convert_start_accepted",
    "status_ready_or_terminal",
    "quality_audit_available",
    "download_attempted",
)


def unavailable_result(*, missing_requirements: list[str], notes: list[str] | None = None) -> SmokeContractResult:
    clean_missing = tuple(str(item) for item in missing_requirements if str(item).strip())
    return SmokeContractResult(
        status=UNAVAILABLE,
        reason="runtime_tooling_unavailable",
        required_evidence=REQUIRED_RUNTIME_EVIDENCE,
        missing_evidence=clean_missing or ("unknown_runtime_tooling",),
        notes=tuple(notes or ()),
    )


def classify_smoke_contract(
    *,
    status_payload: dict[str, Any] | None,
    upload_selected: bool,
    convert_start_accepted: bool,
    download_attempted: bool,
    quality_rendered: bool,
    console_errors: list[str] | None = None,
) -> SmokeContractResult:
    console_errors = console_errors or []
    missing = _missing_evidence(
        upload_selected=upload_selected,
        convert_start_accepted=convert_start_accepted,
        status_payload=status_payload,
        quality_rendered=quality_rendered,
        download_attempted=download_attempted,
    )
    if missing:
        return SmokeContractResult(
            status=FAILED,
            reason="required_evidence_missing",
            required_evidence=REQUIRED_RUNTIME_EVIDENCE,
            missing_evidence=tuple(missing),
        )
    if console_errors:
        return SmokeContractResult(
            status=FAILED,
            reason="browser_console_errors",
            required_evidence=REQUIRED_RUNTIME_EVIDENCE,
            notes=tuple(console_errors),
        )

    status = str((status_payload or {}).get("status") or "")
    quality_state = _quality_state(status_payload)
    release_verdict = str(quality_state.get("release_verdict") or (status_payload or {}).get("release_verdict") or "")
    release_blocked = bool(quality_state.get("release_blocked") or release_verdict == "release_blocked")
    quality_blockers = quality_state.get("quality_blockers")
    if not isinstance(quality_blockers, list):
        quality_blockers = []

    if status in {"failed", "timed_out"}:
        return SmokeContractResult(status=FAILED, reason=f"conversion_{status}", required_evidence=REQUIRED_RUNTIME_EVIDENCE)
    if release_blocked or quality_blockers:
        return SmokeContractResult(status=BLOCKED, reason="release_blocked", required_evidence=REQUIRED_RUNTIME_EVIDENCE)
    if status == "ready":
        return SmokeContractResult(status=PASSED, reason="runtime_roundtrip_ready", required_evidence=REQUIRED_RUNTIME_EVIDENCE)
    return SmokeContractResult(status=FAILED, reason=f"unexpected_status:{status or 'missing'}", required_evidence=REQUIRED_RUNTIME_EVIDENCE)


def _missing_evidence(
    *,
    upload_selected: bool,
    convert_start_accepted: bool,
    status_payload: dict[str, Any] | None,
    quality_rendered: bool,
    download_attempted: bool,
) -> list[str]:
    missing: list[str] = []
    if not upload_selected:
        missing.append("upload_selected")
    if not convert_start_accepted:
        missing.append("convert_start_accepted")
    if not status_payload:
        missing.append("status_ready_or_terminal")
    if not quality_rendered:
        missing.append("quality_audit_available")
    if not download_attempted:
        missing.append("download_attempted")
    return missing


def _quality_state(status_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not status_payload:
        return {}
    quality_state = status_payload.get("quality_state")
    return quality_state if isinstance(quality_state, dict) else {}
