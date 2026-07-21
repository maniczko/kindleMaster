from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any


REPORT_SCHEMA = "kindlemaster.chess.fen_conflict_audit.v1"
LINKAGE_SCHEMA = "kindlemaster.chess.fen_conflict_linkage.row.v1"
CONFLICT_SCHEMA = "kindlemaster.chess.fen_conflict_adjudication.row.v1"
PIECE_CLASSES = ("empty", "P", "N", "B", "R", "Q", "K", "p", "n", "b", "r", "q", "k")


def audit_fen_conflicts_files(
    *,
    current_manifest: str | Path,
    piece_labels: str | Path,
    source_pdf: str | Path,
    output_dir: str | Path,
    bbox_iou_threshold: float = 0.99,
) -> dict[str, Any]:
    manifest_path = Path(current_manifest).resolve()
    labels_path = Path(piece_labels).resolve()
    source_path = Path(source_pdf).resolve()
    if not source_path.is_file():
        raise ValueError("source_pdf_not_found")

    source_sha = _file_sha256(source_path)
    current = _manifest_rows(_load_json(manifest_path))
    labels = _load_jsonl(labels_path)
    _validate_label_sources(labels, source_sha)
    result = audit_fen_conflicts_records(
        current_rows=current,
        label_rows=labels,
        source_document_sha256=source_sha,
        bbox_iou_threshold=bbox_iou_threshold,
    )

    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "linkage": out / "fen_conflict_linkage.jsonl",
        "conflicts": out / "fen_conflict_adjudication.jsonl",
        "report_json": out / "fen_conflict_audit_report.json",
        "report_markdown": out / "fen_conflict_audit_report.md",
    }
    _write_jsonl(artifacts["linkage"], result["linkage"])
    _write_jsonl(artifacts["conflicts"], result["conflicts"])
    artifacts["report_json"].write_text(
        json.dumps(result["report"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # codeql[py/clear-text-storage-sensitive-data] Local audit output contains chess labels and content hashes, not credentials.
    artifacts["report_markdown"].write_text(
        _report_markdown(result["report"]),
        encoding="utf-8",
    )
    return {
        **result["report"],
        "artifacts": {key: str(path) for key, path in artifacts.items()},
    }


def audit_fen_conflicts_records(
    *,
    current_rows: Iterable[Mapping[str, Any]],
    label_rows: Iterable[Mapping[str, Any]],
    source_document_sha256: str,
    bbox_iou_threshold: float = 0.99,
) -> dict[str, Any]:
    threshold = float(bbox_iou_threshold)
    if not 0.0 < threshold <= 1.0:
        raise ValueError("bbox_iou_threshold_out_of_range")
    source_sha = _normalize_sha(source_document_sha256)
    current = [dict(row) for row in current_rows if isinstance(row, Mapping)]
    labels = [dict(row) for row in label_rows if isinstance(row, Mapping)]
    if not current or not labels:
        raise ValueError("audit_rows_missing")

    current_by_id = _unique_by_id(current, "current")
    labels_by_id = _unique_by_id(labels, "label", key="diagram_id")
    matches: dict[str, tuple[dict[str, Any], str, float]] = {}
    used_current_ids: set[str] = set()

    for label_id, label in labels_by_id.items():
        candidate = current_by_id.get(label_id)
        if candidate is None or not _same_page(label, candidate):
            continue
        score = _iou(_bbox(label), _bbox(candidate))
        if score >= threshold:
            matches[label_id] = (candidate, "exact_id_page_bbox", score)
            used_current_ids.add(label_id)

    unmatched_labels = [row for key, row in labels_by_id.items() if key not in matches]
    unmatched_current = [row for key, row in current_by_id.items() if key not in used_current_ids]
    edges: dict[str, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    reverse_edges: dict[str, list[str]] = defaultdict(list)
    for label in unmatched_labels:
        label_id = str(label.get("diagram_id") or "").strip()
        for candidate in unmatched_current:
            current_id = str(candidate.get("id") or "").strip()
            if not _same_page(label, candidate):
                continue
            score = _iou(_bbox(label), _bbox(candidate))
            if score >= threshold:
                edges[label_id].append((candidate, score))
                reverse_edges[current_id].append(label_id)
    for label_id, candidates in edges.items():
        if len(candidates) != 1:
            continue
        candidate, score = candidates[0]
        current_id = str(candidate.get("id") or "").strip()
        if len(reverse_edges[current_id]) == 1:
            matches[label_id] = (candidate, "page_bbox_one_to_one", score)
            used_current_ids.add(current_id)

    linkage = _build_linkage(current, labels_by_id, matches, source_sha)
    evaluations = {"model": _empty_evaluation(), "template": _empty_evaluation()}
    conflicts: list[dict[str, Any]] = []
    adjudication_counts: Counter[str] = Counter()
    trusted_link_count = 0

    label_match_by_current_id = {
        str(candidate.get("id") or "").strip(): (label_id, labels_by_id[label_id], status, score)
        for label_id, (candidate, status, score) in matches.items()
    }
    for row in current:
        current_id = str(row.get("id") or "").strip()
        model_runtime = row.get("model_runtime") if isinstance(row.get("model_runtime"), Mapping) else {}
        comparison = str(model_runtime.get("template_comparison") or "").strip().lower()
        binding = label_match_by_current_id.get(current_id)
        trusted = bool(binding and _is_trusted_label(binding[1]))
        if trusted:
            trusted_link_count += 1
            gold = _label_cells(binding[1])
            model = _placement_to_cells(model_runtime.get("placement"))
            template = _placement_to_cells(row.get("placement"))
            _evaluate_board(evaluations["model"], gold, model)
            _evaluate_board(evaluations["template"], gold, template)
        if comparison != "conflict":
            continue

        conflict: dict[str, Any] = {
            "schema": CONFLICT_SCHEMA,
            "source_document_sha256": source_sha,
            "diagram_id": current_id,
            "page": _current_page(row),
            "template_placement": str(row.get("placement") or ""),
            "model_placement": str(model_runtime.get("placement") or ""),
            "trusted_label_available": trusted,
            "label_diagram_id": binding[0] if binding else "",
            "link_status": binding[2] if binding else "unmatched",
            "crop_path": str(
                (binding[1] if binding else {}).get("crop_path")
                or (binding[1] if binding else {}).get("board_crop_path")
                or row.get("board_crop_path")
                or ""
            ),
            "verdict": "unadjudicated_no_trusted_label",
        }
        model_cells = _placement_to_cells(model_runtime.get("placement"))
        template_cells = _placement_to_cells(row.get("placement"))
        disagreements = _placement_disagreements(model_cells, template_cells)
        conflict["disagreement_squares"] = disagreements
        conflict["king_related"] = any(
            value in {"K", "k"}
            for disagreement in disagreements
            for value in (disagreement["model"], disagreement["template"])
        )
        if trusted:
            gold = _label_cells(binding[1])
            model_correct = sum(expected == actual for expected, actual in zip(gold, model_cells))
            template_correct = sum(expected == actual for expected, actual in zip(gold, template_cells))
            verdict = _conflict_verdict(model_correct, template_correct)
            for disagreement in disagreements:
                disagreement["gold"] = gold[_square_index(disagreement["square"])] or "empty"
            conflict.update(
                {
                    "gold_placement": _cells_to_placement(gold),
                    "model_correct_squares": model_correct,
                    "template_correct_squares": template_correct,
                    "model_square_accuracy": round(model_correct / 64, 6),
                    "template_square_accuracy": round(template_correct / 64, 6),
                    "verdict": verdict,
                }
            )
        adjudication_counts[conflict["verdict"]] += 1
        conflicts.append(conflict)

    finalized = {name: _finalize_evaluation(value) for name, value in evaluations.items()}
    linked_label_ids = set(matches)
    trusted_labels = [row for row in labels if _is_trusted_label(row)]
    total_conflicts = len(conflicts)
    adjudicated = total_conflicts - adjudication_counts["unadjudicated_no_trusted_label"]
    king_conflicts = [row for row in conflicts if row["king_related"]]
    king_adjudication = Counter(row["verdict"] for row in king_conflicts)
    report = {
        "schema": REPORT_SCHEMA,
        "status": "passed" if adjudicated == total_conflicts else "needs_review",
        "source_document_sha256": source_sha,
        "bbox_iou_threshold": threshold,
        "counts": {
            "current_diagrams": len(current),
            "input_label_rows": len(labels),
            "linked_label_rows": len(linked_label_ids),
            "unlinked_label_rows": len(labels) - len(linked_label_ids),
            "current_diagrams_without_label_row": len(current) - len(linked_label_ids),
            "trusted_label_rows": len(trusted_labels),
            "trusted_linked_boards": trusted_link_count,
            "model_template_conflicts": total_conflicts,
            "adjudicated_conflicts": adjudicated,
            "unadjudicated_conflicts": total_conflicts - adjudicated,
            "king_related_conflicts": len(king_conflicts),
            "adjudicated_king_related_conflicts": len(king_conflicts)
            - king_adjudication["unadjudicated_no_trusted_label"],
        },
        "link_status_counts": dict(sorted(Counter(row["link_status"] for row in linkage).items())),
        "evaluation": finalized,
        "conflict_adjudication": dict(sorted(adjudication_counts.items())),
        "king_conflict_adjudication": dict(sorted(king_adjudication.items())),
        "limitations": [
            "Only label_status=verified, human_verified=true, piece_labels_verified=true rows are ground truth.",
            "Linked draft or rejected rows are excluded from accuracy and conflict adjudication.",
            "Conflicts without trusted labels remain unresolved rather than being inferred from either recognizer.",
        ],
    }
    return {"report": report, "linkage": linkage, "conflicts": conflicts}


def _empty_evaluation() -> dict[str, Any]:
    return {
        "board_count": 0,
        "exact_board_count": 0,
        "correct_squares": 0,
        "confusion": defaultdict(Counter),
    }


def _evaluate_board(evaluation: dict[str, Any], gold: list[str], predicted: list[str]) -> None:
    if len(gold) != 64 or len(predicted) != 64:
        return
    correct = 0
    for expected, actual in zip(gold, predicted):
        expected_label = expected or "empty"
        actual_label = actual or "empty"
        evaluation["confusion"][expected_label][actual_label] += 1
        correct += int(expected_label == actual_label)
    evaluation["board_count"] += 1
    evaluation["correct_squares"] += correct
    evaluation["exact_board_count"] += int(correct == 64)


def _finalize_evaluation(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    board_count = int(evaluation["board_count"])
    square_count = board_count * 64
    confusion = evaluation["confusion"]
    matrix = {
        expected: {actual: int(confusion[expected].get(actual, 0)) for actual in PIECE_CLASSES}
        for expected in PIECE_CLASSES
    }
    per_class: dict[str, Any] = {}
    for piece in PIECE_CLASSES:
        total = sum(matrix[piece].values())
        correct = matrix[piece][piece]
        predicted_total = sum(matrix[expected][piece] for expected in PIECE_CLASSES)
        per_class[piece] = {
            "total": total,
            "correct": correct,
            "recall": round(correct / total, 6) if total else None,
            "predicted_total": predicted_total,
            "precision": round(correct / predicted_total, 6) if predicted_total else None,
        }
    return {
        "board_count": board_count,
        "exact_board_count": int(evaluation["exact_board_count"]),
        "exact_board_accuracy": round(int(evaluation["exact_board_count"]) / board_count, 6) if board_count else 0.0,
        "square_count": square_count,
        "correct_squares": int(evaluation["correct_squares"]),
        "square_accuracy": round(int(evaluation["correct_squares"]) / square_count, 6) if square_count else 0.0,
        "per_class": per_class,
        "king_focus": {"white_king": per_class["K"], "black_king": per_class["k"]},
        "confusion_matrix": matrix,
    }


def _build_linkage(
    current: list[dict[str, Any]],
    labels_by_id: Mapping[str, dict[str, Any]],
    matches: Mapping[str, tuple[dict[str, Any], str, float]],
    source_sha: str,
) -> list[dict[str, Any]]:
    reverse = {
        str(candidate.get("id") or "").strip(): (label_id, status, score)
        for label_id, (candidate, status, score) in matches.items()
    }
    rows: list[dict[str, Any]] = []
    for current_row in current:
        current_id = str(current_row.get("id") or "").strip()
        binding = reverse.get(current_id)
        label = labels_by_id.get(binding[0]) if binding else None
        rows.append(
            {
                "schema": LINKAGE_SCHEMA,
                "source_document_sha256": source_sha,
                "current_diagram_id": current_id,
                "page": _current_page(current_row),
                "label_diagram_id": binding[0] if binding else "",
                "link_status": binding[1] if binding else "current_without_label",
                "geometry_iou": round(binding[2], 6) if binding else None,
                "label_status": str((label or {}).get("label_status") or ""),
                "trusted_ground_truth": bool(label and _is_trusted_label(label)),
            }
        )
    return rows


def _conflict_verdict(model_correct: int, template_correct: int) -> str:
    if model_correct == 64 and template_correct < 64:
        return "model_correct_template_wrong"
    if template_correct == 64 and model_correct < 64:
        return "template_correct_model_wrong"
    if model_correct == template_correct == 64:
        return "both_correct"
    if model_correct > template_correct:
        return "model_closer_both_wrong"
    if template_correct > model_correct:
        return "template_closer_both_wrong"
    return "tie_both_wrong"


def _is_trusted_label(row: Mapping[str, Any]) -> bool:
    return bool(
        str(row.get("label_status") or "").strip().lower() == "verified"
        and row.get("human_verified") is True
        and row.get("piece_labels_verified") is True
        and len(row.get("square_labels") or []) == 64
    )


def _label_cells(row: Mapping[str, Any]) -> list[str]:
    cells: list[str] = []
    for value in row.get("square_labels") or []:
        if isinstance(value, Mapping):
            value = value.get("piece", value.get("class", value.get("label", "")))
        piece = str(value or "").strip()
        if piece == "empty":
            piece = ""
        if piece and piece not in PIECE_CLASSES:
            raise ValueError("invalid_square_label")
        cells.append(piece)
    if len(cells) != 64:
        raise ValueError("trusted_label_must_have_64_squares")
    return cells


def _placement_to_cells(value: Any) -> list[str]:
    placement = str(value or "").strip().split()[0] if str(value or "").strip() else ""
    ranks = placement.split("/")
    if len(ranks) != 8:
        return []
    cells: list[str] = []
    for rank in ranks:
        rank_cells: list[str] = []
        for char in rank:
            if char.isdigit():
                rank_cells.extend([""] * int(char))
            elif char in PIECE_CLASSES:
                rank_cells.append(char)
            else:
                return []
        if len(rank_cells) != 8:
            return []
        cells.extend(rank_cells)
    return cells


def _cells_to_placement(cells: list[str]) -> str:
    ranks: list[str] = []
    for start in range(0, 64, 8):
        empty = 0
        rank = ""
        for piece in cells[start : start + 8]:
            if not piece:
                empty += 1
            else:
                if empty:
                    rank += str(empty)
                    empty = 0
                rank += piece
        if empty:
            rank += str(empty)
        ranks.append(rank)
    return "/".join(ranks)


def _placement_disagreements(model: list[str], template: list[str]) -> list[dict[str, str]]:
    if len(model) != 64 or len(template) != 64:
        return []
    return [
        {
            "square": _square_name(index),
            "model": model_piece or "empty",
            "template": template_piece or "empty",
        }
        for index, (model_piece, template_piece) in enumerate(zip(model, template))
        if model_piece != template_piece
    ]


def _square_name(index: int) -> str:
    return f"{'abcdefgh'[index % 8]}{8 - index // 8}"


def _square_index(square: str) -> int:
    return (8 - int(square[1])) * 8 + "abcdefgh".index(square[0])


def _unique_by_id(rows: Iterable[dict[str, Any]], source: str, *, key: str = "id") -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = str(row.get(key) or "").strip()
        if not identity:
            raise ValueError(f"{source}_diagram_id_missing")
        if identity in result:
            raise ValueError(f"{source}_diagram_id_duplicate")
        result[identity] = row
    return result


def _same_page(label: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    return int(label.get("page") or 0) == _current_page(current)


def _current_page(row: Mapping[str, Any]) -> int:
    return int(row.get("page_number") or row.get("page") or 0)


def _bbox(row: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    value = row.get("bbox")
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if result[2] <= result[0] or result[3] <= result[1]:
        return None
    return result


def _iou(
    left: tuple[float, float, float, float] | None,
    right: tuple[float, float, float, float] | None,
) -> float:
    if left is None or right is None:
        return 0.0
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _validate_label_sources(labels: Iterable[Mapping[str, Any]], expected_sha: str) -> None:
    seen: set[str] = set()
    for row in labels:
        value = str(row.get("source_document_sha256") or "").strip().lower()
        if value:
            seen.add(_normalize_sha(value))
    if not seen:
        raise ValueError("label_source_sha256_missing")
    if seen != {expected_sha}:
        raise ValueError("label_source_sha256_mismatch")


def _normalize_sha(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("invalid_source_sha256")
    return normalized


def _manifest_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = payload.get("records") or payload.get("diagrams") or []
    else:
        rows = []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    # codeql[py/clear-text-storage-sensitive-data] Rows are source-bound chess evidence with no authentication secrets.
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _report_markdown(report: Mapping[str, Any]) -> str:
    counts = report["counts"]
    model = report["evaluation"]["model"]
    template = report["evaluation"]["template"]
    lines = [
        "# Chess FEN conflict audit",
        "",
        f"Status: **{report['status']}**",
        f"Source SHA-256: `{report['source_document_sha256']}`",
        "",
        "## Coverage",
        "",
        f"- Current diagrams: {counts['current_diagrams']}",
        f"- Linked label rows: {counts['linked_label_rows']} / {counts['input_label_rows']}",
        f"- Trusted linked boards: {counts['trusted_linked_boards']}",
        f"- Adjudicated conflicts: {counts['adjudicated_conflicts']} / {counts['model_template_conflicts']}",
        f"- Adjudicated king-related conflicts: {counts['adjudicated_king_related_conflicts']} / {counts['king_related_conflicts']}",
        "",
        "## Accuracy on trusted labels",
        "",
        "| Recognizer | Square accuracy | Exact boards | White king recall | Black king recall |",
        "| --- | ---: | ---: | ---: | ---: |",
        _metric_row("New model", model),
        _metric_row("Template recognizer", template),
        "",
        "## Conflict verdicts",
        "",
    ]
    lines.extend(
        f"- `{name}`: {count}" for name, count in report["conflict_adjudication"].items()
    )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {value}" for value in report["limitations"])
    return "\n".join(lines) + "\n"


def _metric_row(name: str, evaluation: Mapping[str, Any]) -> str:
    white = evaluation["king_focus"]["white_king"]["recall"]
    black = evaluation["king_focus"]["black_king"]["recall"]
    return (
        f"| {name} | {evaluation['square_accuracy']:.2%} | "
        f"{evaluation['exact_board_count']} / {evaluation['board_count']} | "
        f"{(white or 0.0):.2%} | {(black or 0.0):.2%} |"
    )
