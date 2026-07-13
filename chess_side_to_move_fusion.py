from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


SOURCE_PRIORITY = {
    "trusted_marker": 0,
    "human_verified": 1,
    "text_inferred": 2,
    "pgn_inferred": 3,
}
DECISIVE_SOURCES = set(SOURCE_PRIORITY)
SOURCE_SHA_PATTERN = re.compile(r"[0-9a-f]{64}")
DIAGRAM_FINGERPRINT_PATTERN = re.compile(r"dfp_[0-9a-f]{32}")

CAPTION_PATTERNS = {
    "w": (
        (r"\bwhite\s+(?:is\s+)?to\s+move\b", 0.97, "white_to_move"),
        (r"\bwhite\s+(?:moves|plays|starts)\b", 0.92, "white_moves"),
        (r"\bbiale\s+(?:sa\s+)?na\s+ruchu\b", 0.97, "biale_na_ruchu"),
        (r"\bruch\s+bialych\b", 0.95, "ruch_bialych"),
        (r"\bwei(?:ss|ß)\s+am\s+zug\b", 0.97, "weiss_am_zug"),
    ),
    "b": (
        (r"\bblack\s+(?:is\s+)?to\s+move\b", 0.97, "black_to_move"),
        (r"\bblack\s+(?:moves|plays|starts)\b", 0.92, "black_moves"),
        (r"\bczarne\s+(?:sa\s+)?na\s+ruchu\b", 0.97, "czarne_na_ruchu"),
        (r"\bruch\s+czarnych\b", 0.95, "ruch_czarnych"),
        (r"\bschwarz\s+am\s+zug\b", 0.97, "schwarz_am_zug"),
    ),
}
CAPTION_FIELDS = (
    "caption",
    "caption_text",
    "ocr_caption",
    "diagram_caption",
    "label",
    "solution_heading",
)
PGN_TEXT_FIELDS = (
    "pgn",
    "movetext",
    "linked_pgn",
    "solution_pgn",
    "solution_movetext",
    "solution_text",
    "notation",
)
PGN_MAPPING_FIELDS = (
    "linked_pgn_record",
    "pgn_record",
    "solution_record",
)


def caption_evidence_candidates(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Derive side-to-move candidates from explicit caption phrases."""
    candidates: list[dict[str, Any]] = []
    for field in CAPTION_FIELDS:
        raw = str(record.get(field) or "").strip()
        if not raw:
            continue
        normalized = _normalize_search_text(raw)
        for side, patterns in CAPTION_PATTERNS.items():
            for pattern, confidence, phrase_id in patterns:
                match = re.search(pattern, normalized, flags=re.IGNORECASE)
                if not match:
                    continue
                candidates.append(
                    _candidate(
                        side=side,
                        source="text_inferred",
                        confidence=confidence,
                        kind="caption_phrase",
                        provenance={
                            "field": field,
                            "phrase_id": phrase_id,
                            "matched_text": match.group(0),
                        },
                    )
                )
    return _deduplicate_candidates(candidates)


def pgn_evidence_candidates(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Derive first mover from PGN FEN tags, explicit links, or dot/ellipsis notation."""
    candidates: list[dict[str, Any]] = []
    for field in ("pgn_first_mover", "linked_first_mover", "solution_first_mover"):
        side = _side(record.get(field))
        if side in {"w", "b"}:
            candidates.append(
                _candidate(
                    side=side,
                    source="pgn_inferred",
                    confidence=0.98,
                    kind="linked_first_mover",
                    provenance={"field": field},
                )
            )
    for field in PGN_TEXT_FIELDS:
        candidates.extend(_pgn_text_candidates(str(record.get(field) or ""), field=field))
    for field in PGN_MAPPING_FIELDS:
        value = record.get(field)
        if not isinstance(value, Mapping):
            continue
        candidates.extend(_pgn_mapping_candidates(value, field=field))
    for field in ("pgn_records", "linked_pgn_records"):
        rows = record.get(field)
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        for index, row in enumerate(rows):
            if isinstance(row, Mapping):
                candidates.extend(_pgn_mapping_candidates(row, field=f"{field}[{index}]"))
    return _deduplicate_candidates(candidates)


def verified_side_labels_from_acceptance_manifest(
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build exact-scoped reusable labels from a verified #257 manifest."""
    source = manifest.get("source") if isinstance(manifest.get("source"), Mapping) else {}
    verification = (
        manifest.get("verification")
        if isinstance(manifest.get("verification"), Mapping)
        else {}
    )
    source_sha = _source_sha(source.get("sha256"))
    if (
        not source_sha
        or verification.get("status") != "verified"
        or not str(verification.get("verified_by") or "").strip()
        or not str(verification.get("verified_at") or "").strip()
    ):
        return []
    diagrams = manifest.get("diagrams")
    if not isinstance(diagrams, Iterable) or isinstance(diagrams, (str, bytes, Mapping)):
        return []
    labels: list[dict[str, Any]] = []
    for row in diagrams:
        if not isinstance(row, Mapping):
            continue
        side = _side(row.get("expected_side"))
        fingerprint = _fingerprint(row.get("diagram_fingerprint"))
        if (
            side not in {"w", "b"}
            or not fingerprint
            or row.get("label_status") != "verified"
            or not str(row.get("source_of_truth") or "").strip()
        ):
            continue
        labels.append(
            {
                "source_document_sha256": source_sha,
                "diagram_fingerprint": fingerprint,
                "side_to_move": side,
                "label_status": "verified",
                "human_verified": True,
                "verification_source": str(row.get("source_of_truth") or ""),
                "verified_by": str(verification.get("verified_by") or ""),
                "verified_at": str(verification.get("verified_at") or ""),
                "source_profile": str(manifest.get("source_profile") or ""),
            }
        )
    return labels


def exact_verified_label_candidates(
    record: Mapping[str, Any],
    verified_labels: Iterable[Mapping[str, Any]],
    *,
    source_document_sha256: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return only labels matching both the exact source SHA and diagram fingerprint."""
    runtime_sha = _source_sha(
        source_document_sha256
        or record.get("source_document_sha256")
        or record.get("source_pdf_sha256")
    )
    runtime_fingerprint = _fingerprint(record.get("diagram_fingerprint"))
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, label in enumerate(verified_labels):
        if not isinstance(label, Mapping):
            continue
        label_fingerprint = _fingerprint(label.get("diagram_fingerprint"))
        if not runtime_fingerprint or label_fingerprint != runtime_fingerprint:
            continue
        label_sha = _source_sha(
            label.get("source_document_sha256") or label.get("source_pdf_sha256")
        )
        reason = ""
        if not runtime_sha:
            reason = "runtime_source_sha256_missing"
        elif label_sha != runtime_sha:
            reason = "source_sha256_mismatch"
        elif label.get("label_status") != "verified" and label.get("human_verified") is not True:
            reason = "label_not_verified"
        elif not str(label.get("verification_source") or label.get("source_of_truth") or "").strip():
            reason = "verification_source_missing"
        elif not str(label.get("verified_by") or "").strip():
            reason = "verified_by_missing"
        elif not str(label.get("verified_at") or "").strip():
            reason = "verified_at_missing"
        side = _side(
            label.get("side_to_move")
            or label.get("expected_side")
            or label.get("expected_side_to_move")
        )
        if not reason and side not in {"w", "b"}:
            reason = "verified_side_missing"
        if reason:
            rejected.append(
                {
                    "kind": "verified_label_rejected",
                    "reason": reason,
                    "label_index": index,
                    "diagram_fingerprint": runtime_fingerprint,
                    "runtime_source_document_sha256": runtime_sha,
                    "label_source_document_sha256": label_sha,
                    "blocking": False,
                }
            )
            continue
        candidates.append(
            _candidate(
                side=side,
                source="human_verified",
                confidence=1.0,
                kind="exact_verified_label",
                provenance={
                    "source_document_sha256": runtime_sha,
                    "diagram_fingerprint": runtime_fingerprint,
                    "verification_source": str(
                        label.get("verification_source")
                        or label.get("source_of_truth")
                        or ""
                    ),
                    "verified_by": str(label.get("verified_by") or ""),
                    "verified_at": str(label.get("verified_at") or ""),
                },
            )
        )
    return _deduplicate_candidates(candidates), rejected


def layout_prior_candidate(
    record: Mapping[str, Any],
    *,
    source_profile_layout_prior: Mapping[str, Any] | str | None = None,
) -> dict[str, Any] | None:
    raw: Any = source_profile_layout_prior
    if raw in (None, ""):
        raw = record.get("source_profile_layout_prior") or record.get("layout_prior_side")
    if isinstance(raw, Mapping):
        side = _side(raw.get("side") or raw.get("side_to_move"))
        confidence = _float(raw.get("confidence"), 0.25)
        rule = str(raw.get("rule") or "source_profile_layout_prior")
    else:
        side = _side(raw)
        confidence = 0.25
        rule = "source_profile_layout_prior"
    if side not in {"w", "b"}:
        return None
    return _candidate(
        side=side,
        source="layout_prior",
        confidence=min(0.49, confidence),
        kind="layout_prior",
        provenance={"rule": rule},
        support_only=True,
    )


def fuse_side_to_move_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    rejected_evidence: Iterable[Mapping[str, Any]] = (),
    minimum_decisive_confidence: float = 0.70,
) -> dict[str, Any]:
    """Fuse independent evidence conservatively and make all conflicts explicit."""
    evidence = _deduplicate_candidates(
        [dict(row) for row in candidates if isinstance(row, Mapping)]
    )
    decisive = [
        row
        for row in evidence
        if not row.get("support_only")
        and row.get("source") in DECISIVE_SOURCES
        and row.get("side") in {"w", "b"}
        and _float(row.get("confidence"), 0.0) >= minimum_decisive_confidence
    ]
    rejected = [dict(row) for row in rejected_evidence if isinstance(row, Mapping)]
    sides = {str(row.get("side")) for row in decisive}
    high_conflicts = _opposing_conflicts(decisive)
    if len(sides) > 1:
        return {
            "status": "conflict",
            "side": "unknown",
            "source": "conflict",
            "confidence": round(max(_float(row.get("confidence"), 0.0) for row in decisive), 4),
            "primary_evidence": {},
            "supporting_evidence": evidence,
            "conflicts": [*high_conflicts, *rejected],
        }
    if not decisive:
        return {
            "status": "unknown",
            "side": "unknown",
            "source": "unknown",
            "confidence": 0.0,
            "primary_evidence": {},
            "supporting_evidence": evidence,
            "conflicts": rejected,
        }
    side = next(iter(sides))
    agreeing = [row for row in decisive if row.get("side") == side]
    primary = min(
        agreeing,
        key=lambda row: (
            SOURCE_PRIORITY.get(str(row.get("source")), 99),
            -_float(row.get("confidence"), 0.0),
            str(row.get("kind") or ""),
        ),
    )
    confidence = 1.0
    for row in agreeing:
        confidence *= 1.0 - _float(row.get("confidence"), 0.0)
    confidence = 1.0 - confidence
    supporting_conflicts = [
        {
            "kind": "supporting_prior_disagreement",
            "side": row.get("side"),
            "resolved_side": side,
            "source": row.get("source"),
            "confidence": row.get("confidence"),
            "blocking": False,
        }
        for row in evidence
        if row.get("support_only") and row.get("side") in {"w", "b"} and row.get("side") != side
    ]
    return {
        "status": "resolved",
        "side": side,
        "source": str(primary.get("source") or "unknown"),
        "confidence": round(min(1.0, max(0.0, confidence)), 4),
        "primary_evidence": dict(primary),
        "supporting_evidence": evidence,
        "conflicts": [*supporting_conflicts, *rejected],
    }


def _pgn_mapping_candidates(row: Mapping[str, Any], *, field: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    explicit = _side(row.get("first_mover") or row.get("side_to_move"))
    if explicit in {"w", "b"}:
        candidates.append(
            _candidate(
                side=explicit,
                source="pgn_inferred",
                confidence=0.98,
                kind="linked_first_mover",
                provenance={"field": field},
            )
        )
    for key in ("pgn", "movetext", "fen", "raw_text", "normalized_text"):
        candidates.extend(
            _pgn_text_candidates(str(row.get(key) or ""), field=f"{field}.{key}")
        )
    return candidates


def _pgn_text_candidates(text: str, *, field: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    candidates: list[dict[str, Any]] = []
    fen_match = re.search(
        r"\[FEN\s+[\"']([^\"']+)[\"']\]",
        text,
        flags=re.IGNORECASE,
    )
    if fen_match:
        fen_parts = fen_match.group(1).split()
        side = _side(fen_parts[1] if len(fen_parts) > 1 else "")
        if side in {"w", "b"}:
            candidates.append(
                _candidate(
                    side=side,
                    source="pgn_inferred",
                    confidence=0.99,
                    kind="pgn_fen_tag",
                    provenance={"field": field, "move_number": fen_parts[5] if len(fen_parts) > 5 else ""},
                )
            )
    stripped = re.sub(r"\{[^}]*\}", " ", text, flags=re.DOTALL)
    stripped = re.sub(r";[^\n\r]*", " ", stripped)
    stripped = re.sub(r"\[[^\]]*\]", " ", stripped)
    move_match = re.search(
        r"(?<!\w)(\d{1,3})\s*(\.\.\.|\.(?!\.))\s*(?=[A-Za-z0])",
        stripped,
    )
    if move_match:
        side = "b" if move_match.group(2) == "..." else "w"
        candidates.append(
            _candidate(
                side=side,
                source="pgn_inferred",
                confidence=0.93,
                kind="move_number_notation",
                provenance={
                    "field": field,
                    "move_number": int(move_match.group(1)),
                    "separator": move_match.group(2),
                },
            )
        )
    return candidates


def _candidate(
    *,
    side: str,
    source: str,
    confidence: float,
    kind: str,
    provenance: Mapping[str, Any],
    support_only: bool = False,
) -> dict[str, Any]:
    return {
        "side": _side(side),
        "source": str(source),
        "confidence": round(min(1.0, max(0.0, float(confidence))), 4),
        "kind": str(kind),
        "support_only": bool(support_only),
        "provenance": dict(provenance),
    }


def _deduplicate_candidates(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for raw in candidates:
        row = dict(raw)
        provenance = row.get("provenance") if isinstance(row.get("provenance"), Mapping) else {}
        key = (
            str(row.get("side") or "unknown"),
            str(row.get("source") or "unknown"),
            str(row.get("kind") or ""),
            repr(sorted((str(field), repr(value)) for field, value in provenance.items())),
        )
        existing = unique.get(key)
        if existing is None or _float(row.get("confidence"), 0.0) > _float(
            existing.get("confidence"), 0.0
        ):
            unique[key] = row
    return list(unique.values())


def _opposing_conflicts(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            if left.get("side") == right.get("side"):
                continue
            conflicts.append(
                {
                    "kind": "high_confidence_side_conflict",
                    "left": {
                        "side": left.get("side"),
                        "source": left.get("source"),
                        "kind": left.get("kind"),
                        "confidence": left.get("confidence"),
                    },
                    "right": {
                        "side": right.get("side"),
                        "source": right.get("source"),
                        "kind": right.get("kind"),
                        "confidence": right.get("confidence"),
                    },
                    "blocking": True,
                }
            )
    return conflicts


def _normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(character for character in normalized if not unicodedata.combining(character)).lower()


def _source_sha(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if SOURCE_SHA_PATTERN.fullmatch(normalized) else ""


def _fingerprint(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if DIAGRAM_FINGERPRINT_PATTERN.fullmatch(normalized) else ""


def _side(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"w", "white"}:
        return "w"
    if normalized in {"b", "black"}:
        return "b"
    return "unknown"


def _float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
        if not math.isfinite(parsed):
            return default
        return min(1.0, max(0.0, parsed))
    except (TypeError, ValueError):
        return default
