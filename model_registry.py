from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping


MODEL_REGISTRY_SCHEMA = "kindlemaster.model_registry.v1"
MODEL_CARD_SCHEMA = "kindlemaster.model_card.v1"
PROMOTION_EVENT_SCHEMA = "kindlemaster.model_promotion_event.v1"
DEFAULT_REGISTRY_PATH = Path("models/registry.json")
DEFAULT_PROMOTION_HISTORY_PATH = Path("reports/ml/promotions/promotion_history.jsonl")
DEFAULT_ROLLBACK_SNAPSHOT_DIR = Path("reports/ml/promotions/rollback_snapshots")
DEFAULT_MODEL_CARD_DIR = Path("reports/ml/model_cards")


def load_model_registry(
    *,
    repo_root: str | Path = ".",
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    path = _resolve(root, registry_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        payload = default_model_registry(repo_root=root)
    if not isinstance(payload, dict) or payload.get("schema") != MODEL_REGISTRY_SCHEMA:
        payload = default_model_registry(repo_root=root)
    payload.setdefault("registry_path", _portable_path(path, root))
    return payload


def ensure_model_registry(
    *,
    repo_root: str | Path = ".",
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    path = _resolve(root, registry_path)
    if path.exists():
        return load_model_registry(repo_root=root, registry_path=path)
    payload = default_model_registry(repo_root=root)
    _write_json_atomic(path, payload)
    return payload


def default_model_registry(*, repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    active_models = {
        "route_classifier": _active_model_from_path(root, "models/route_classifier_v1.json"),
        "quality_verifier": _active_model_from_path(root, "models/quality_verifier_v1.json"),
        "chess_fen_profile": {
            "profile_name": "default",
            "profile_version": "chess-fen-profile-bootstrap",
            "profile_path": "reference_inputs/chess_fen/templates/fundamenty_merida_like",
        },
    }
    return {
        "schema": MODEL_REGISTRY_SCHEMA,
        "registry_version": _registry_version(active_models=active_models, last_promotion={}),
        "generated_at": _utc_now(),
        "active_models": active_models,
        "last_promotion": {},
        "rollback_available": False,
    }


def build_runtime_model_attribution(
    *,
    repo_root: str | Path = ".",
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    route_decision: Mapping[str, Any] | None = None,
    ai_quality_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    registry = load_model_registry(repo_root=root, registry_path=registry_path)
    active = _mapping(registry.get("active_models"))
    route = _mapping(active.get("route_classifier"))
    quality = _mapping(active.get("quality_verifier"))
    chess = _mapping(active.get("chess_fen_profile"))
    runtime_route = _mapping(route_decision)
    runtime_quality = _mapping(ai_quality_verification)
    return {
        "schema": "kindlemaster.model_attribution.v1",
        "route_model_version": str(runtime_route.get("model_version") or route.get("model_version") or ""),
        "route_model_path": str(route.get("model_path") or ""),
        "quality_verifier_version": str(runtime_quality.get("model_version") or quality.get("model_version") or ""),
        "quality_verifier_path": str(quality.get("model_path") or ""),
        "chess_fen_profile_version": str(
            chess.get("profile_version") or chess.get("model_version") or chess.get("profile_name") or ""
        ),
        "chess_fen_profile_name": str(chess.get("profile_name") or ""),
        "model_registry_version": str(registry.get("registry_version") or ""),
        "model_registry_path": str(registry.get("registry_path") or _portable_path(_resolve(root, registry_path), root)),
    }


def create_rollback_snapshot(
    *,
    model_name: str,
    model_path: str | Path,
    repo_root: str | Path = ".",
    snapshot_dir: str | Path = DEFAULT_ROLLBACK_SNAPSHOT_DIR,
) -> str:
    root = Path(repo_root).resolve()
    source = _resolve(root, model_path)
    if not source.exists():
        return ""
    current_model = _read_json(source)
    version = str(_mapping(current_model).get("model_version") or source.stem)
    target_dir = _resolve(root, snapshot_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_slug(model_name)}_{_slug(version)}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json"
    shutil.copy2(source, target)
    return _portable_path(target, root)


def register_model_promotion(
    *,
    model_name: str,
    model_path: str | Path,
    candidate_path: str | Path,
    promotion_payload: Mapping[str, Any],
    repo_root: str | Path = ".",
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    promotion_history_path: str | Path = DEFAULT_PROMOTION_HISTORY_PATH,
    model_card_dir: str | Path = DEFAULT_MODEL_CARD_DIR,
    rollback_snapshot_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    registry_file = _resolve(root, registry_path)
    registry = ensure_model_registry(repo_root=root, registry_path=registry_file)
    active = dict(_mapping(registry.get("active_models")))
    model_file = _resolve(root, model_path)
    candidate_file = _resolve(root, candidate_path)
    promoted_model = _read_json(model_file) or _read_json(candidate_file)
    metrics = _mapping(promoted_model.get("metrics"))
    dataset_version = str(
        promoted_model.get("dataset_version")
        or promotion_payload.get("dataset_version")
        or _mapping(metrics.get("dataset_readiness")).get("dataset_version")
        or ""
    )
    promoted_at = str(promoted_model.get("promoted_at") or promotion_payload.get("promoted_at") or _utc_now())
    model_version = str(promoted_model.get("model_version") or candidate_file.stem)
    card = write_model_card(
        model_name=model_name,
        model=promoted_model,
        model_path=model_file,
        dataset_version=dataset_version,
        rollback_path=str(rollback_snapshot_path or promotion_payload.get("rollback_snapshot") or ""),
        promotion_payload=promotion_payload,
        repo_root=root,
        model_card_dir=model_card_dir,
    )
    previous_active = _mapping(active.get(model_name))
    active[model_name] = {
        "model_path": _portable_path(model_file, root),
        "model_version": model_version,
        "model_type": str(promoted_model.get("model_type") or ""),
        "dataset_version": dataset_version,
        "promoted_at": promoted_at,
        "model_card_json_path": card["model_card_json_path"],
        "model_card_md_path": card["model_card_md_path"],
    }
    event = {
        "schema": PROMOTION_EVENT_SCHEMA,
        "event_type": "model_promoted",
        "created_at": _utc_now(),
        "model_name": model_name,
        "model_path": _portable_path(model_file, root),
        "candidate_path": _portable_path(candidate_file, root),
        "model_version_before": str(previous_active.get("model_version") or ""),
        "model_version_after": model_version,
        "dataset_version": dataset_version,
        "metric_gates": _mapping(promotion_payload.get("metric_gates")),
        "corpus_gate": _mapping(promotion_payload.get("corpus_gate")),
        "rollback_snapshot": str(rollback_snapshot_path or promotion_payload.get("rollback_snapshot") or ""),
        "model_card_json_path": card["model_card_json_path"],
        "model_card_md_path": card["model_card_md_path"],
    }
    registry["active_models"] = active
    registry["last_promotion"] = event
    registry["rollback_available"] = bool(event["rollback_snapshot"])
    registry["registry_version"] = _registry_version(active_models=active, last_promotion=event)
    registry["updated_at"] = _utc_now()
    _write_json_atomic(registry_file, registry)
    history_file = _resolve(root, promotion_history_path)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with history_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "status": "registered",
        "registry_path": _portable_path(registry_file, root),
        "registry_version": registry["registry_version"],
        "promotion_history_path": _portable_path(history_file, root),
        "promotion_event": event,
        **card,
    }


def write_model_card(
    *,
    model_name: str,
    model: Mapping[str, Any],
    model_path: str | Path,
    dataset_version: str = "",
    rollback_path: str = "",
    promotion_payload: Mapping[str, Any] | None = None,
    repo_root: str | Path = ".",
    model_card_dir: str | Path = DEFAULT_MODEL_CARD_DIR,
) -> dict[str, str]:
    root = Path(repo_root).resolve()
    payload = build_model_card_payload(
        model_name=model_name,
        model=model,
        model_path=model_path,
        dataset_version=dataset_version,
        rollback_path=rollback_path,
        promotion_payload=promotion_payload or {},
        repo_root=root,
    )
    model_version = str(payload.get("model_version") or "unknown")
    card_dir = _resolve(root, model_card_dir) / _slug(model_name)
    card_dir.mkdir(parents=True, exist_ok=True)
    json_path = card_dir / f"{_slug(model_version)}.model_card.json"
    md_path = card_dir / f"{_slug(model_version)}.model_card.md"
    _write_json_atomic(json_path, payload)
    md_path.write_text(_model_card_markdown(payload), encoding="utf-8")
    return {
        "model_card_json_path": _portable_path(json_path, root),
        "model_card_md_path": _portable_path(md_path, root),
    }


def build_model_card_payload(
    *,
    model_name: str,
    model: Mapping[str, Any],
    model_path: str | Path,
    dataset_version: str = "",
    rollback_path: str = "",
    promotion_payload: Mapping[str, Any] | None = None,
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    metrics = _mapping(model.get("metrics"))
    readiness = _mapping(metrics.get("dataset_readiness"))
    promotion = _mapping(promotion_payload)
    return {
        "schema": MODEL_CARD_SCHEMA,
        "generated_at": _utc_now(),
        "model_name": model_name,
        "model_version": str(model.get("model_version") or Path(str(model_path)).stem),
        "model_type": str(model.get("model_type") or ""),
        "model_path": _portable_path(_resolve(root, model_path), root),
        "dataset_version": str(dataset_version or model.get("dataset_version") or readiness.get("dataset_version") or ""),
        "training_data_counts": {
            "example_count": _int(metrics.get("example_count")),
            "train_example_count": _int(metrics.get("train_example_count")),
            "holdout_example_count": _int(metrics.get("holdout_example_count")),
            "label_counts": dict(_mapping(metrics.get("label_counts"))),
        },
        "holdout_metrics": {
            "accuracy": metrics.get("accuracy"),
            "macro_f1": metrics.get("macro_f1"),
            "coverage": metrics.get("coverage"),
            "calibration_bins": list(metrics.get("calibration_bins") or []),
        },
        "protected_class_metrics": {
            "per_class_recall": dict(_mapping(metrics.get("per_class_recall"))),
            "protected_recall": dict(_mapping(_mapping(metrics.get("promotion_gates")).get("values")).get("protected_recall") or {}),
        },
        "known_limitations": [
            "Local JSON model only; no online learning happens during conversion.",
            "Promotion requires explicit offline gates and corpus evidence.",
            "Runtime must fall back to deterministic heuristics when confidence or model availability is insufficient.",
        ],
        "privacy_notes": {
            "stores_text": False,
            "stores_source_file": False,
            "stores_fingerprints_only": True,
            "notes": "Model cards summarize counts, metrics, and hashes; source text is not stored here.",
        },
        "promotion_gates": promotion.get("metric_gates") or metrics.get("promotion_gates") or {},
        "corpus_gate": promotion.get("corpus_gate") or {},
        "rollback_path": str(rollback_path or ""),
        "compatible_code_version": _git_commit_sha(root),
    }


def rollback_model(
    *,
    model_name: str,
    to_version: str,
    repo_root: str | Path = ".",
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    snapshot_dir: str | Path = DEFAULT_ROLLBACK_SNAPSHOT_DIR,
    promotion_history_path: str | Path = DEFAULT_PROMOTION_HISTORY_PATH,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    registry_file = _resolve(root, registry_path)
    registry = ensure_model_registry(repo_root=root, registry_path=registry_file)
    active_models = dict(_mapping(registry.get("active_models")))
    active = _mapping(active_models.get(model_name))
    model_path = _resolve(root, active.get("model_path") or f"models/{model_name}.json")
    snapshot = _find_snapshot_for_version(root=root, snapshot_dir=snapshot_dir, to_version=to_version)
    if not snapshot:
        return {
            "status": "failed",
            "error": "rollback_snapshot_not_found",
            "model_name": model_name,
            "to_version": to_version,
        }
    before_snapshot = create_rollback_snapshot(model_name=model_name, model_path=model_path, repo_root=root, snapshot_dir=snapshot_dir)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snapshot, model_path)
    restored_model = _read_json(model_path)
    restored_version = str(_mapping(restored_model).get("model_version") or to_version)
    event = {
        "schema": PROMOTION_EVENT_SCHEMA,
        "event_type": "model_rollback",
        "created_at": _utc_now(),
        "model_name": model_name,
        "model_path": _portable_path(model_path, root),
        "model_version_before": str(active.get("model_version") or ""),
        "model_version_after": restored_version,
        "rollback_source": _portable_path(snapshot, root),
        "rollback_snapshot_before_rollback": before_snapshot,
    }
    updated_active = dict(active)
    updated_active.update(
        {
            "model_path": _portable_path(model_path, root),
            "model_version": restored_version,
            "model_type": str(_mapping(restored_model).get("model_type") or updated_active.get("model_type") or ""),
            "promoted_at": event["created_at"],
        }
    )
    active_models[model_name] = updated_active
    registry["active_models"] = active_models
    registry["last_promotion"] = event
    registry["rollback_available"] = bool(before_snapshot)
    registry["registry_version"] = _registry_version(active_models=active_models, last_promotion=event)
    registry["updated_at"] = _utc_now()
    _write_json_atomic(registry_file, registry)
    history_file = _resolve(root, promotion_history_path)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with history_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "status": "rolled_back",
        "model_name": model_name,
        "model_path": _portable_path(model_path, root),
        "model_version": restored_version,
        "registry_path": _portable_path(registry_file, root),
        "registry_version": registry["registry_version"],
        "rollback_source": _portable_path(snapshot, root),
        "rollback_snapshot_before_rollback": before_snapshot,
        "promotion_history_path": _portable_path(history_file, root),
    }


def _active_model_from_path(root: Path, path: str) -> dict[str, Any]:
    model_path = root / path
    model = _mapping(_read_json(model_path))
    return {
        "model_path": path,
        "model_version": str(model.get("model_version") or ""),
        "model_type": str(model.get("model_type") or ""),
        "dataset_version": str(model.get("dataset_version") or ""),
        "promoted_at": str(model.get("promoted_at") or model.get("trained_at") or ""),
    }


def _find_snapshot_for_version(*, root: Path, snapshot_dir: str | Path, to_version: str) -> Path | None:
    target_dir = _resolve(root, snapshot_dir)
    candidates = sorted(
        target_dir.glob("*.json"),
        key=lambda path: path.stat().st_mtime_ns if path.exists() else 0,
        reverse=True,
    )
    for candidate in candidates:
        payload = _mapping(_read_json(candidate))
        if str(payload.get("model_version") or "") == to_version:
            return candidate
    return None


def _model_card_markdown(payload: Mapping[str, Any]) -> str:
    counts = _mapping(payload.get("training_data_counts"))
    holdout = _mapping(payload.get("holdout_metrics"))
    privacy = _mapping(payload.get("privacy_notes"))
    lines = [
        f"# Model Card: {payload.get('model_name')} {payload.get('model_version')}",
        "",
        f"- Model type: `{payload.get('model_type')}`",
        f"- Dataset version: `{payload.get('dataset_version')}`",
        f"- Model path: `{payload.get('model_path')}`",
        f"- Example count: `{counts.get('example_count')}`",
        f"- Holdout accuracy: `{holdout.get('accuracy')}`",
        f"- Holdout macro F1: `{holdout.get('macro_f1')}`",
        f"- Rollback path: `{payload.get('rollback_path')}`",
        f"- Compatible code version: `{payload.get('compatible_code_version')}`",
        "",
        "## Privacy",
        f"- Stores source text: `{privacy.get('stores_text')}`",
        f"- Stores source files: `{privacy.get('stores_source_file')}`",
        f"- Fingerprints only: `{privacy.get('stores_fingerprints_only')}`",
        "",
        "## Known Limitations",
    ]
    lines.extend(f"- {item}" for item in payload.get("known_limitations", []) or [])
    lines.append("")
    return "\n".join(lines)


def _registry_version(*, active_models: Mapping[str, Any], last_promotion: Mapping[str, Any]) -> str:
    raw = json.dumps(
        {"active_models": _json_safe(active_models), "last_promotion": _json_safe(last_promotion)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"registry_{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _resolve(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _portable_path(path: str | Path, root: Path) -> str:
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(candidate)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _slug(value: Any) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "unknown"))
    text = "-".join(part for part in text.split("-") if part)
    return text[:96] or "unknown"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git_commit_sha(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
