from __future__ import annotations

import json
import os
import re
import subprocess
import zipfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any


ACCEPTANCE_MANIFEST_SCHEMA = "kindlemaster.chess.marker_acceptance_manifest.v1"
ACCEPTANCE_PROFILE_SCHEMA = "kindlemaster.chess.marker_acceptance_profile.v1"
ACCEPTANCE_REPORT_SCHEMA = "kindlemaster.chess.marker_acceptance_report.v1"
DEFAULT_PROFILE = "yusupov-fundamentals"
PROFILE_ROOT = Path("reference_inputs/chess_marker_acceptance/profiles")
SECURE_CORPUS_ENV = "KINDLEMASTER_CHESS_ACCEPTANCE_CORPUS_ROOT"
SECURE_MANIFEST_ENV = "KINDLEMASTER_CHESS_ACCEPTANCE_MANIFEST"
SECURE_JOB_OUTPUT_ENV = "KINDLEMASTER_CHESS_ACCEPTANCE_JOB_OUTPUT"

SPLITS = {"train", "calibration", "holdout"}
MARKER_STATUSES = {"present", "absent"}
CROP_QUALITIES = {"clear", "damaged", "ambiguous", "unusable"}
OWNERSHIP_STATUSES = {"assigned", "ambiguous", "unassigned"}
FALLBACK_SOURCES = {"text", "pgn", "human_review", "none"}
VERIFIED_SOURCES = {"human_visual", "dual_human", "manual_verified"}
REQUIRED_HARD_NEGATIVE_KINDS = {
    "coordinates",
    "letters",
    "borders",
    "arrows",
    "captions",
    "neighboring_diagrams",
}

EVIDENCE_PATHS = (
    PurePosixPath("chess_diagrams.json"),
    PurePosixPath("positions.json"),
    PurePosixPath("reports/chess_fen/side_marker_assignment.json"),
    PurePosixPath("reports/chess_fen/side_to_move_coverage_dashboard.json"),
    PurePosixPath("reports/chess_fen/expected_diagram_recall.json"),
    PurePosixPath("reports/chess_fen/diagram_recall.json"),
    PurePosixPath("data/artifact_manifest.json"),
    PurePosixPath("artifact_manifest.json"),
)


def load_acceptance_profile(
    source_profile: str,
    *,
    repo_root: str | Path = ".",
    profile_path: str | Path | None = None,
) -> dict[str, Any]:
    path = (
        Path(profile_path)
        if profile_path is not None
        else Path(repo_root) / PROFILE_ROOT / f"{source_profile}.json"
    )
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def resolve_secure_manifest_path(
    source_profile: str,
    *,
    repo_root: str | Path = ".",
    manifest_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    env = os.environ if environ is None else environ
    candidates: list[Path] = []
    if manifest_path is not None:
        candidates.append(Path(manifest_path))
    configured_manifest = str(env.get(SECURE_MANIFEST_ENV, "") or "").strip()
    if configured_manifest:
        candidates.append(Path(configured_manifest))
    configured_root = str(env.get(SECURE_CORPUS_ENV, "") or "").strip()
    if configured_root:
        candidates.append(Path(configured_root) / source_profile / "manifest.json")
    candidates.append(
        Path(repo_root)
        / "reference_inputs"
        / "chess_marker_acceptance"
        / "private"
        / source_profile
        / "manifest.json"
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def validate_acceptance_manifest(
    manifest: Mapping[str, Any],
    *,
    source_profile: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if manifest.get("schema") != ACCEPTANCE_MANIFEST_SCHEMA:
        errors.append("manifest_schema_invalid")
    if str(manifest.get("source_profile") or "") != source_profile:
        errors.append("source_profile_mismatch")

    source = manifest.get("source") if isinstance(manifest.get("source"), Mapping) else {}
    source_digest = _normalize_sha(source.get("sha256"))
    if not source_digest:
        errors.append("source_sha256_missing_or_invalid")
    if str(source.get("kind") or "") != "fixed_edition_pdf":
        errors.append("source_kind_must_be_fixed_edition_pdf")
    if source.get("copyright_content_committed") is not False:
        errors.append("copyright_content_committed_must_be_false")

    verification = (
        manifest.get("verification")
        if isinstance(manifest.get("verification"), Mapping)
        else {}
    )
    if verification.get("status") != "verified":
        errors.append("manifest_not_verified")
    if not str(verification.get("verified_by") or "").strip():
        errors.append("verified_by_missing")
    if not str(verification.get("verified_at") or "").strip():
        errors.append("verified_at_missing")

    diagrams = _mapping_rows(manifest.get("diagrams"))
    if not diagrams:
        errors.append("verified_diagrams_missing")
    fingerprints: set[str] = set()
    split_pages: dict[int, str] = {}
    split_chapters: dict[str, str] = {}
    represented_splits: set[str] = set()
    for index, row in enumerate(diagrams):
        prefix = f"diagram[{index}]"
        fingerprint = str(row.get("diagram_fingerprint") or "").strip()
        if not re.fullmatch(r"dfp_[0-9a-f]{32}", fingerprint):
            errors.append(f"{prefix}:diagram_fingerprint_invalid")
        elif fingerprint in fingerprints:
            errors.append(f"{prefix}:diagram_fingerprint_duplicate")
        fingerprints.add(fingerprint)
        page = _positive_int(row.get("page"))
        chapter = str(row.get("chapter_id") or "").strip()
        split = str(row.get("split") or "").strip()
        if page is None:
            errors.append(f"{prefix}:page_invalid")
        if not chapter:
            errors.append(f"{prefix}:chapter_id_missing")
        if split not in SPLITS:
            errors.append(f"{prefix}:split_invalid")
        else:
            represented_splits.add(split)
            _check_split_isolation(
                errors,
                prefix=prefix,
                page=page,
                chapter=chapter,
                split=split,
                split_pages=split_pages,
                split_chapters=split_chapters,
            )
            if split == "holdout" and row.get("allowed_for_tuning") is not False:
                errors.append(f"{prefix}:holdout_must_forbid_tuning")
        if row.get("label_status") != "verified":
            errors.append(f"{prefix}:label_not_verified")
        if str(row.get("source_of_truth") or "") not in VERIFIED_SOURCES:
            errors.append(f"{prefix}:source_of_truth_invalid")
        marker_status = str(row.get("marker_status") or "")
        crop_quality = str(row.get("crop_quality") or "")
        ownership = str(row.get("marker_ownership") or "")
        expected_side_raw = str(row.get("expected_side") or "").strip().lower()
        expected_side = _side(expected_side_raw)
        if marker_status not in MARKER_STATUSES:
            errors.append(f"{prefix}:marker_status_invalid")
        if crop_quality not in CROP_QUALITIES:
            errors.append(f"{prefix}:crop_quality_invalid")
        if ownership not in OWNERSHIP_STATUSES:
            errors.append(f"{prefix}:marker_ownership_invalid")
        if expected_side_raw not in {"w", "b", "unknown"}:
            errors.append(f"{prefix}:expected_side_invalid")
        if str(row.get("expected_fallback_source") or "") not in FALLBACK_SOURCES:
            errors.append(f"{prefix}:expected_fallback_source_invalid")
        if marker_status == "present" and crop_quality == "clear":
            if expected_side not in {"w", "b"}:
                errors.append(f"{prefix}:clear_marker_side_missing")
            if ownership != "assigned":
                errors.append(f"{prefix}:clear_marker_not_assigned")
        errors.extend(
            _fingerprint_component_errors(
                row,
                prefix=prefix,
                source_sha256=source_digest,
            )
        )

    missing_splits = sorted(SPLITS - represented_splits)
    if missing_splits:
        errors.append("diagram_splits_missing:" + ",".join(missing_splits))

    hard_negatives = _mapping_rows(manifest.get("hard_negatives"))
    negative_kinds: set[str] = set()
    negative_fingerprints: set[str] = set()
    for index, row in enumerate(hard_negatives):
        prefix = f"hard_negative[{index}]"
        fingerprint = str(row.get("hard_negative_fingerprint") or "").strip()
        if not re.fullmatch(r"hnf_[0-9a-f]{32}", fingerprint):
            errors.append(f"{prefix}:fingerprint_invalid")
        elif fingerprint in negative_fingerprints:
            errors.append(f"{prefix}:fingerprint_duplicate")
        negative_fingerprints.add(fingerprint)
        kind = str(row.get("kind") or "").strip()
        if kind not in REQUIRED_HARD_NEGATIVE_KINDS:
            errors.append(f"{prefix}:kind_invalid")
        else:
            negative_kinds.add(kind)
        if row.get("label_status") != "verified":
            errors.append(f"{prefix}:label_not_verified")
        if str(row.get("source_of_truth") or "") not in VERIFIED_SOURCES:
            errors.append(f"{prefix}:source_of_truth_invalid")
        if row.get("expected_disposition") != "reject":
            errors.append(f"{prefix}:expected_disposition_must_be_reject")
        negative_split = str(row.get("split") or "")
        negative_page = _positive_int(row.get("page"))
        negative_chapter = str(row.get("chapter_id") or "").strip()
        if negative_split not in SPLITS:
            errors.append(f"{prefix}:split_invalid")
        else:
            _check_split_isolation(
                errors,
                prefix=prefix,
                page=negative_page,
                chapter=negative_chapter,
                split=negative_split,
                split_pages=split_pages,
                split_chapters=split_chapters,
            )
        if negative_split == "holdout" and row.get("allowed_for_tuning") is not False:
            errors.append(f"{prefix}:holdout_must_forbid_tuning")
        if negative_page is None:
            errors.append(f"{prefix}:page_invalid")
        if not negative_chapter:
            errors.append(f"{prefix}:chapter_id_missing")
        if not _valid_bbox(row.get("normalized_bbox_xyxy")):
            errors.append(f"{prefix}:normalized_bbox_invalid")
    missing_negative_kinds = sorted(REQUIRED_HARD_NEGATIVE_KINDS - negative_kinds)
    if missing_negative_kinds:
        errors.append("hard_negative_kinds_missing:" + ",".join(missing_negative_kinds))

    return {
        "status": "valid" if not errors else "invalid",
        "errors": errors,
        "source_document_sha256": source_digest,
        "diagram_count": len(diagrams),
        "hard_negative_count": len(hard_negatives),
        "split_counts": {
            split: len([row for row in diagrams if row.get("split") == split])
            for split in sorted(SPLITS)
        },
        "hard_negative_kinds": sorted(negative_kinds),
    }


def load_job_evidence(job_output: str | Path) -> dict[str, Any]:
    source = Path(job_output)
    payloads: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    if source.is_dir():
        root = source.resolve()
        for relative in EVIDENCE_PATHS:
            candidate = root.joinpath(*relative.parts)
            if not candidate.is_file() or candidate.is_symlink():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                errors.append(f"{relative.as_posix()}:{type(error).__name__}")
                continue
            if isinstance(payload, Mapping):
                payloads[relative.as_posix()] = dict(payload)
    elif source.is_file() and source.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source) as archive:
                names = set(archive.namelist())
                for relative in EVIDENCE_PATHS:
                    name = relative.as_posix()
                    if name not in names:
                        continue
                    try:
                        payload = json.loads(archive.read(name).decode("utf-8"))
                    except (KeyError, UnicodeError, json.JSONDecodeError) as error:
                        errors.append(f"{name}:{type(error).__name__}")
                        continue
                    if isinstance(payload, Mapping):
                        payloads[name] = dict(payload)
        except (OSError, zipfile.BadZipFile) as error:
            errors.append(f"job_output_zip:{type(error).__name__}")
    else:
        errors.append("job_output_missing_or_unsupported")

    records_by_key: dict[str, dict[str, Any]] = {}
    record_key_by_id: dict[str, str] = {}
    for path in EVIDENCE_PATHS:
        payload = payloads.get(path.as_posix(), {})
        for row in _payload_records(payload):
            fingerprint = str(row.get("diagram_fingerprint") or "").strip()
            diagram_id = str(row.get("diagram_id") or row.get("id") or "").strip()
            key = f"fingerprint:{fingerprint}" if fingerprint else record_key_by_id.get(diagram_id, "")
            if not key:
                key = _record_key(row)
            if not key:
                continue
            existing = records_by_key.setdefault(key, {})
            existing.update(
                {key_name: value for key_name, value in row.items() if value not in (None, "", [], {})}
            )
            if diagram_id:
                record_key_by_id[diagram_id] = key

    source_digests = sorted(
        {
            digest
            for payload in payloads.values()
            for digest in _find_sha_values(payload, source_keys=True)
            if digest
        }
    )
    commit_digests = sorted(
        {
            digest
            for payload in payloads.values()
            for digest in _find_sha_values(payload, source_keys=False)
            if digest
        }
    )
    if len(source_digests) > 1:
        errors.append("conflicting_source_sha256_values")
    if len(commit_digests) > 1:
        errors.append("conflicting_runtime_commit_values")
    return {
        "status": "loaded" if payloads and not errors else "invalid" if errors else "empty",
        "job_output": str(source),
        "included_files": sorted(payloads),
        "records": list(records_by_key.values()),
        "source_document_sha256": source_digests[0] if len(source_digests) == 1 else "",
        "runtime_commit_sha": commit_digests[0] if len(commit_digests) == 1 else "",
        "errors": errors,
    }


def evaluate_acceptance(
    manifest: Mapping[str, Any],
    *,
    detected_records: Iterable[Mapping[str, Any]],
    source_document_sha256: str,
    runtime_commit_sha: str,
    validator_commit_sha: str,
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    expected = _mapping_rows(manifest.get("diagrams"))
    detected = [dict(row) for row in detected_records if isinstance(row, Mapping)]
    detected_by_fingerprint = {
        _fingerprint(row.get("diagram_fingerprint")): row
        for row in detected
        if _fingerprint(row.get("diagram_fingerprint"))
    }
    expected_fingerprints = {
        _fingerprint(row.get("diagram_fingerprint")) for row in expected
    }
    matched = [
        row
        for row in expected
        if _fingerprint(row.get("diagram_fingerprint")) in detected_by_fingerprint
    ]
    visible = [row for row in expected if str(row.get("marker_status") or "") == "present"]
    candidate_hits = len(
        [
            row
            for row in visible
            if _candidate_present(
                detected_by_fingerprint.get(_fingerprint(row.get("diagram_fingerprint")), {})
            )
        ]
    )
    ownership_rows = [row for row in expected if row.get("marker_ownership") in OWNERSHIP_STATUSES]
    ownership_hits = len(
        [
            row
            for row in ownership_rows
            if _predicted_ownership(
                detected_by_fingerprint.get(_fingerprint(row.get("diagram_fingerprint")), {})
            )
            == row.get("marker_ownership")
        ]
    )
    clear = [
        row
        for row in expected
        if row.get("marker_status") == "present" and row.get("crop_quality") == "clear"
    ]
    clear_hits = len(
        [
            row
            for row in clear
            if _trusted_and_correct(
                detected_by_fingerprint.get(_fingerprint(row.get("diagram_fingerprint")), {}),
                expected_side=_side(row.get("expected_side")),
            )
        ]
    )

    false_trusted: list[dict[str, Any]] = []
    for row in expected:
        fingerprint = _fingerprint(row.get("diagram_fingerprint"))
        actual = detected_by_fingerprint.get(fingerprint, {})
        if not _trusted_marker(actual):
            continue
        safe_expected = (
            row.get("marker_status") == "present"
            and row.get("crop_quality") == "clear"
            and row.get("marker_ownership") == "assigned"
            and _predicted_marker_side(actual) == _side(row.get("expected_side"))
        )
        if not safe_expected:
            false_trusted.append(
                {
                    "diagram_fingerprint": fingerprint,
                    "page": row.get("page"),
                    "reason": "trusted_marker_conflicts_with_verified_label",
                    "expected_side": row.get("expected_side"),
                    "actual_side": _predicted_marker_side(actual),
                }
            )
    for row in detected:
        fingerprint = _fingerprint(row.get("diagram_fingerprint"))
        if fingerprint not in expected_fingerprints and _trusted_marker(row):
            false_trusted.append(
                {
                    "diagram_fingerprint": fingerprint,
                    "page": row.get("page"),
                    "reason": "trusted_marker_outside_complete_expected_manifest",
                }
            )

    covered_count = len(
        [
            row
            for row in expected
            if _predicted_side(
                detected_by_fingerprint.get(_fingerprint(row.get("diagram_fingerprint")), {})
            )
            in {"w", "b"}
        ]
    )
    trusted_count = len(
        [
            row
            for row in expected
            if _trusted_marker(
                detected_by_fingerprint.get(_fingerprint(row.get("diagram_fingerprint")), {})
            )
        ]
    )
    full_fen_count = len(
        [
            row
            for row in expected
            if _full_fen_allowed(
                detected_by_fingerprint.get(_fingerprint(row.get("diagram_fingerprint")), {})
            )
        ]
    )
    total = len(expected)
    metrics = {
        "expected_diagram_recall": _rate(len(matched), total),
        "marker_candidate_recall_visible_subset": _rate(candidate_hits, len(visible)),
        "marker_ownership_accuracy": _rate(ownership_hits, len(ownership_rows)),
        "clear_marker_classification_accuracy": _rate(clear_hits, len(clear)),
        "false_trusted_marker_count": len(false_trusted),
        "trusted_marker_rate": _rate(trusted_count, total),
        "side_to_move_coverage_rate": _rate(covered_count, total),
        "unknown_count": max(0, total - covered_count),
        "full_fen_safe_acceptance_rate": _rate(full_fen_count, total),
    }
    checks = _threshold_checks(metrics, thresholds)
    expected_source = _normalize_sha(
        (manifest.get("source") or {}).get("sha256")
        if isinstance(manifest.get("source"), Mapping)
        else ""
    )
    source_match = bool(expected_source and expected_source == _normalize_sha(source_document_sha256))
    commit_match = bool(
        _normalize_commit(runtime_commit_sha)
        and _normalize_commit(runtime_commit_sha) == _normalize_commit(validator_commit_sha)
    )
    provenance_checks = [
        {
            "name": "source_document_sha256_match",
            "passed": source_match,
            "expected": expected_source,
            "actual": _normalize_sha(source_document_sha256),
        },
        {
            "name": "runtime_commit_matches_validator",
            "passed": commit_match,
            "expected": _normalize_commit(validator_commit_sha),
            "actual": _normalize_commit(runtime_commit_sha),
        },
    ]
    all_checks = [*provenance_checks, *checks]
    return {
        "status": "passed" if all(check.get("passed") for check in all_checks) else "failed",
        "source_document_sha256": _normalize_sha(source_document_sha256),
        "expected_source_document_sha256": expected_source,
        "runtime_commit_sha": _normalize_commit(runtime_commit_sha),
        "validator_commit_sha": _normalize_commit(validator_commit_sha),
        "metrics": metrics,
        "checks": all_checks,
        "subsets": {
            "clear": _subset_metrics(clear, detected_by_fingerprint),
            "damaged_ambiguous": _subset_metrics(
                [
                    row
                    for row in expected
                    if row.get("crop_quality") in {"damaged", "ambiguous"}
                ],
                detected_by_fingerprint,
            ),
            "all": _subset_metrics(expected, detected_by_fingerprint),
        },
        "false_trusted_markers": false_trusted,
        "missing_expected_fingerprints": [
            _fingerprint(row.get("diagram_fingerprint"))
            for row in expected
            if _fingerprint(row.get("diagram_fingerprint")) not in detected_by_fingerprint
        ],
        "closing_evidence_eligible": all(check.get("passed") for check in all_checks),
    }


def run_fixed_edition_acceptance(
    *,
    source_profile: str,
    job_output: str | Path,
    repo_root: str | Path = ".",
    manifest_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    report_dir: str | Path = "reports/chess_fen/side_marker_acceptance",
    validator_commit_sha: str = "",
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    profile = load_acceptance_profile(
        source_profile,
        repo_root=repo_root,
        profile_path=profile_path,
    )
    resolved_manifest = resolve_secure_manifest_path(
        source_profile,
        repo_root=repo_root,
        manifest_path=manifest_path,
        environ=environ,
    )
    base: dict[str, Any] = {
        "schema": ACCEPTANCE_REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_profile": source_profile,
        "job_output_supplied": bool(str(job_output)),
        "secure_manifest_available": resolved_manifest is not None,
        "synthetic_fixture_claim_allowed": False,
        "profile": profile,
    }
    if profile.get("schema") != ACCEPTANCE_PROFILE_SCHEMA:
        payload = {**base, "status": "failed", "errors": ["acceptance_profile_missing_or_invalid"]}
        return _write_acceptance_reports(payload, report_dir=report_dir, source_profile=source_profile)
    if resolved_manifest is None:
        payload = {
            **base,
            "status": "corpus_unavailable",
            "errors": ["secure_verified_manifest_not_found"],
            "next_actions": [
                f"set {SECURE_CORPUS_ENV} to the non-committed verified corpus root",
                "create <root>/<source-profile>/manifest.json from a fresh fixed-edition audit export",
                "do not claim real-edition or 100% acceptance from synthetic fixtures",
            ],
        }
        return _write_acceptance_reports(payload, report_dir=report_dir, source_profile=source_profile)
    try:
        manifest_payload = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        payload = {**base, "status": "failed", "errors": [f"manifest_read_failed:{type(error).__name__}"]}
        return _write_acceptance_reports(payload, report_dir=report_dir, source_profile=source_profile)
    manifest = dict(manifest_payload) if isinstance(manifest_payload, Mapping) else {}
    validation = validate_acceptance_manifest(manifest, source_profile=source_profile)
    evidence = load_job_evidence(job_output)
    if validation.get("status") != "valid" or evidence.get("status") != "loaded":
        payload = {
            **base,
            "status": "failed",
            "manifest_validation": validation,
            "job_evidence": {key: value for key, value in evidence.items() if key != "records"},
            "errors": [
                *list(validation.get("errors") or []),
                *list(evidence.get("errors") or []),
                *(
                    ["job_evidence_records_missing"]
                    if not evidence.get("records")
                    else []
                ),
            ],
        }
        return _write_acceptance_reports(payload, report_dir=report_dir, source_profile=source_profile)
    current_commit = validator_commit_sha or _current_main_commit(repo_root)
    evaluation = evaluate_acceptance(
        manifest,
        detected_records=evidence.get("records") or [],
        source_document_sha256=str(evidence.get("source_document_sha256") or ""),
        runtime_commit_sha=str(evidence.get("runtime_commit_sha") or ""),
        validator_commit_sha=current_commit,
        thresholds=profile.get("thresholds") or {},
    )
    payload = {
        **base,
        **evaluation,
        "manifest_validation": validation,
        "job_evidence": {key: value for key, value in evidence.items() if key != "records"},
        "errors": (
            []
            if evaluation.get("status") == "passed"
            else [
                str(check.get("name"))
                for check in evaluation.get("checks") or []
                if not check.get("passed")
            ]
        ),
    }
    return _write_acceptance_reports(payload, report_dir=report_dir, source_profile=source_profile)


def secure_acceptance_for_quick(
    *,
    repo_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    manifest = resolve_secure_manifest_path(DEFAULT_PROFILE, repo_root=repo_root, environ=env)
    if manifest is None:
        return {"status": "corpus_unavailable", "enforced": False}
    job_output = str(env.get(SECURE_JOB_OUTPUT_ENV, "") or "").strip()
    if not job_output:
        return {
            "status": "failed",
            "enforced": True,
            "errors": [f"{SECURE_JOB_OUTPUT_ENV}_missing_for_available_secure_corpus"],
        }
    payload = run_fixed_edition_acceptance(
        source_profile=DEFAULT_PROFILE,
        job_output=job_output,
        repo_root=repo_root,
        manifest_path=manifest,
        environ=env,
    )
    return {**payload, "enforced": True}


def acceptance_report_markdown(report: Mapping[str, Any]) -> str:
    metrics = report.get("metrics") if isinstance(report.get("metrics"), Mapping) else {}
    lines = [
        "# Fixed-edition side-marker acceptance",
        "",
        f"- status: `{report.get('status') or 'unknown'}`",
        f"- source profile: `{report.get('source_profile') or ''}`",
        f"- source SHA256: `{report.get('source_document_sha256') or ''}`",
        f"- runtime commit: `{report.get('runtime_commit_sha') or ''}`",
        f"- validator commit: `{report.get('validator_commit_sha') or ''}`",
        f"- closing evidence eligible: `{bool(report.get('closing_evidence_eligible'))}`",
        "- synthetic fixtures may claim real acceptance: `false`",
        "",
        "## Required metrics",
        "",
    ]
    for name in (
        "expected_diagram_recall",
        "marker_candidate_recall_visible_subset",
        "marker_ownership_accuracy",
        "clear_marker_classification_accuracy",
        "false_trusted_marker_count",
        "trusted_marker_rate",
        "side_to_move_coverage_rate",
        "unknown_count",
        "full_fen_safe_acceptance_rate",
    ):
        lines.append(f"- {name}: `{metrics.get(name)}`")
    lines.extend(
        [
            "",
            "## Subsets",
            "",
            "| Subset | Expected | Matched | Trusted | Covered | Unknown | False trusted |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, subset in (report.get("subsets") or {}).items():
        lines.append(
            f"| {name} | {subset.get('expected_count')} | {subset.get('matched_count')} | "
            f"{subset.get('trusted_count')} | {subset.get('covered_count')} | "
            f"{subset.get('unknown_count')} | {subset.get('false_trusted_count')} |"
        )
    errors = list(report.get("errors") or [])
    lines.extend(["", "## Blockers", ""])
    lines.extend([f"- `{error}`" for error in errors] or ["- None"])
    return "\n".join(lines).rstrip() + "\n"


def _write_acceptance_reports(
    payload: dict[str, Any],
    *,
    report_dir: str | Path,
    source_profile: str,
) -> dict[str, Any]:
    target = Path(report_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / f"{source_profile}.json"
    md_path = target / f"{source_profile}.md"
    persisted_payload = dict(payload)
    job_evidence = persisted_payload.get("job_evidence")
    if isinstance(job_evidence, Mapping):
        persisted_payload["job_evidence"] = {
            key: value
            for key, value in job_evidence.items()
            if key not in {"job_output", "records"}
        }
    json_path.write_text(
        json.dumps(persisted_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(acceptance_report_markdown(persisted_payload), encoding="utf-8")
    return {**payload, "report_json": str(json_path), "report_markdown": str(md_path)}


def _fingerprint_component_errors(
    row: Mapping[str, Any],
    *,
    prefix: str,
    source_sha256: str,
) -> list[str]:
    errors: list[str] = []
    bbox = row.get("normalized_bbox_xyxy")
    if not _valid_bbox(bbox):
        return [f"{prefix}:normalized_bbox_invalid"]
    perceptual_hash = str(row.get("board_perceptual_hash") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{16}", perceptual_hash):
        errors.append(f"{prefix}:board_perceptual_hash_invalid")
        return errors
    if _normalize_sha(row.get("source_document_sha256")) != source_sha256:
        errors.append(f"{prefix}:source_sha256_mismatch")
    quantized = [int(round(float(value) * 64)) for value in list(bbox)[:4]]
    material = json.dumps(
        {
            "source_sha256": source_sha256,
            "page": _positive_int(row.get("page")) or 0,
            "bbox_grid": tuple(quantized),
            "perceptual_hash": perceptual_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    expected = "dfp_" + sha256(material.encode("utf-8")).hexdigest()[:32]
    if _fingerprint(row.get("diagram_fingerprint")) != expected:
        errors.append(f"{prefix}:diagram_fingerprint_components_mismatch")
    return errors


def _check_split_isolation(
    errors: list[str],
    *,
    prefix: str,
    page: int | None,
    chapter: str,
    split: str,
    split_pages: dict[int, str],
    split_chapters: dict[str, str],
) -> None:
    if page is not None and page in split_pages and split_pages[page] != split:
        errors.append(f"{prefix}:page_split_leakage")
    elif page is not None:
        split_pages[page] = split
    if chapter and chapter in split_chapters and split_chapters[chapter] != split:
        errors.append(f"{prefix}:chapter_split_leakage")
    elif chapter:
        split_chapters[chapter] = split


def _payload_records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "diagrams", "positions", "records", "expected_diagrams"):
        value = payload.get(key)
        if isinstance(value, list):
            return _mapping_rows(value)
    return []


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value or [] if isinstance(row, Mapping)] if isinstance(value, list) else []


def _record_key(row: Mapping[str, Any]) -> str:
    fingerprint = str(row.get("diagram_fingerprint") or "").strip()
    if fingerprint:
        return f"fingerprint:{fingerprint}"
    diagram_id = str(row.get("diagram_id") or row.get("id") or "").strip()
    return f"id:{diagram_id}" if diagram_id else ""


def _find_sha_values(value: Any, *, source_keys: bool) -> list[str]:
    keys = (
        {"source_document_sha256", "source_pdf_sha256", "pdf_sha256", "source_sha256"}
        if source_keys
        else {"commit_sha", "git_commit", "runtime_commit_sha"}
    )
    results: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in keys:
                normalized = _normalize_sha(item) if source_keys else _normalize_commit(item)
                if normalized:
                    results.append(normalized)
            elif isinstance(item, (Mapping, list)):
                results.extend(_find_sha_values(item, source_keys=source_keys))
    elif isinstance(value, list):
        for item in value:
            results.extend(_find_sha_values(item, source_keys=source_keys))
    return results


def _subset_metrics(
    expected: list[dict[str, Any]],
    detected_by_fingerprint: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    actual = [
        detected_by_fingerprint.get(_fingerprint(row.get("diagram_fingerprint")), {})
        for row in expected
    ]
    trusted = [row for row in actual if _trusted_marker(row)]
    covered = [row for row in actual if _predicted_side(row) in {"w", "b"}]
    false_trusted = 0
    for expected_row, actual_row in zip(expected, actual):
        if _trusted_marker(actual_row) and not (
            expected_row.get("marker_status") == "present"
            and expected_row.get("crop_quality") == "clear"
            and expected_row.get("marker_ownership") == "assigned"
            and _predicted_marker_side(actual_row) == _side(expected_row.get("expected_side"))
        ):
            false_trusted += 1
    return {
        "expected_count": len(expected),
        "matched_count": len([row for row in actual if row]),
        "trusted_count": len(trusted),
        "covered_count": len(covered),
        "unknown_count": len(expected) - len(covered),
        "false_trusted_count": false_trusted,
    }


def _threshold_checks(metrics: Mapping[str, Any], thresholds: Mapping[str, Any]) -> list[dict[str, Any]]:
    specs = (
        ("expected_diagram_recall", "minimum_expected_diagram_recall", ">="),
        ("marker_candidate_recall_visible_subset", "minimum_marker_candidate_recall_visible_subset", ">="),
        ("marker_ownership_accuracy", "minimum_marker_ownership_accuracy", ">="),
        ("clear_marker_classification_accuracy", "minimum_clear_marker_classification_accuracy", ">="),
        ("false_trusted_marker_count", "maximum_false_trusted_marker_count", "<="),
        ("side_to_move_coverage_rate", "minimum_side_to_move_coverage_rate", ">="),
        ("unknown_count", "maximum_unknown_count", "<="),
    )
    checks = []
    for metric, threshold_key, operator in specs:
        expected = _optional_float(thresholds.get(threshold_key))
        actual = _optional_float(metrics.get(metric))
        passed = False
        if expected is not None and actual is not None:
            passed = actual >= expected if operator == ">=" else actual <= expected
        checks.append(
            {
                "name": threshold_key,
                "metric": metric,
                "operator": operator,
                "expected": expected,
                "actual": actual,
                "passed": passed,
            }
        )
    return checks


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _candidate_present(row: Mapping[str, Any]) -> bool:
    status = str(row.get("side_marker_status") or row.get("marker_semantic_status") or "").lower()
    if status in {"", "missing", "marker_missing", "none"}:
        return bool(
            row.get("marker_bbox")
            or row.get("side_marker_bbox")
            or row.get("side_marker_crop_path")
            or row.get("marker_candidates")
        )
    return True


def _trusted_marker(row: Mapping[str, Any]) -> bool:
    semantic_status = str(row.get("marker_semantic_status") or "").strip().lower()
    if semantic_status:
        return semantic_status == "trusted"
    return str(row.get("side_marker_status") or "").strip().lower() in {
        "trusted",
        "trusted_marker",
        "trusted_side_marker",
    }


def _predicted_marker_side(row: Mapping[str, Any]) -> str:
    return _side(row.get("marker_semantic_side") or row.get("side_to_move")) if _trusted_marker(row) else "unknown"


def _predicted_side(row: Mapping[str, Any]) -> str:
    return _side(row.get("side_to_move") or row.get("marker_semantic_side"))


def _predicted_ownership(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("marker_ownership_status") or "").strip().lower()
    if explicit in OWNERSHIP_STATUSES:
        return explicit
    assignment = str(row.get("marker_assignment_status") or "").strip().lower()
    if assignment == "assigned" or _trusted_marker(row):
        return "assigned"
    if "ambiguous" in assignment or "conflict" in assignment:
        return "ambiguous"
    return "unassigned"


def _trusted_and_correct(row: Mapping[str, Any], *, expected_side: str) -> bool:
    return _trusted_marker(row) and _predicted_marker_side(row) == expected_side


def _full_fen_allowed(row: Mapping[str, Any]) -> bool:
    if "full_fen_allowed" in row:
        return row.get("full_fen_allowed") is True
    status = str(row.get("full_fen_status") or row.get("runtime_status") or "").strip().lower()
    return status in {"accepted", "fen_machine_accepted", "fen_corpus_verified", "full_fen_accepted"}


def _normalize_sha(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1]
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else ""


def _normalize_commit(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{40}", text) else ""


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _valid_bbox(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return False
    try:
        x0, y0, x1, y1 = (float(item) for item in value[:4])
    except (TypeError, ValueError):
        return False
    return 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


def _side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"w", "white"}:
        return "w"
    if text in {"b", "black"}:
        return "b"
    return "unknown"


def _fingerprint(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _current_main_commit(repo_root: str | Path) -> str:
    for revision in ("main", "HEAD"):
        try:
            completed = subprocess.run(
                ["git", "rev-parse", revision],
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        commit = _normalize_commit(completed.stdout)
        if commit:
            return commit
    return ""
