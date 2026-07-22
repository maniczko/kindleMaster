from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import uuid
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from chess_fen_review_corpus import export_fen_review_corpus


PUBLICATION_SCHEMA = "kindlemaster.chess_verified_fen_publication.v1"
VERIFIED_DIAGRAMS_SCHEMA = "kindlemaster.chess_verified_diagrams.v1"
_SHA256_LENGTH = 64
_PAGE_SIZE = 50


class VerifiedFenPublicationError(ValueError):
    pass


class _BoundReviewPayloadClient:
    available = True

    def __init__(self, *, artifact_id: str, payload: Mapping[str, Any]) -> None:
        self._artifact_id = artifact_id
        self._payload = dict(payload)

    def load_review(self, *, artifact_id: str) -> dict[str, Any]:
        if artifact_id != self._artifact_id:
            raise VerifiedFenPublicationError("artifact_id_mismatch")
        return dict(self._payload)


def publish_verified_fen_artifacts(
    *,
    artifact_id: str,
    artifact_root: str | Path,
    review_payload: Mapping[str, Any],
    review_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Publish source-bound human FEN without mutating raw conversion evidence."""
    artifact = str(artifact_id or "").strip()
    root = Path(artifact_root).resolve()
    if not artifact:
        raise VerifiedFenPublicationError("artifact_id_missing")
    if not root.is_dir():
        raise VerifiedFenPublicationError("artifact_root_missing")
    session_status = str(
        review_payload.get("session_status") or review_payload.get("status") or ""
    ).strip().lower()
    if session_status != "complete":
        raise VerifiedFenPublicationError("review_session_not_complete")

    source_file = _single_source_file(root / "input")
    source_digest = _sha256_file(source_file)
    payload_rows = [
        row for row in review_payload.get("rows") or [] if isinstance(row, Mapping)
    ]
    payload_digest = str(
        review_payload.get("source_document_sha256")
        or next(
            (
                row.get("source_document_sha256") or row.get("source_artifact_sha256")
                for row in payload_rows
                if row.get("source_document_sha256") or row.get("source_artifact_sha256")
            ),
            "",
        )
    ).strip().lower()
    if payload_digest != source_digest:
        raise VerifiedFenPublicationError("source_document_sha256_mismatch")

    corpus_report = export_fen_review_corpus(
        artifact_id=artifact,
        out_dir=root,
        review_dir=Path(review_dir).resolve() if review_dir else root / "review",
        cloud_client=_BoundReviewPayloadClient(
            artifact_id=artifact,
            payload=review_payload,
        ),
    )
    if corpus_report.get("status") != "passed":
        raise VerifiedFenPublicationError("verified_fen_corpus_validation_failed")
    labels_path = Path(str((corpus_report.get("artifacts") or {}).get("labels") or ""))
    labels = _read_jsonl(labels_path)
    if not labels:
        raise VerifiedFenPublicationError("verified_fen_labels_missing")

    diagrams_path = root / "report" / "chess_diagrams.json"
    diagrams_payload = _read_json(diagrams_path)
    records = [dict(row) for row in diagrams_payload.get("records") or [] if isinstance(row, Mapping)]
    if not records:
        raise VerifiedFenPublicationError("chess_diagram_records_missing")
    by_id = {_diagram_id(row): row for row in records}
    if "" in by_id or len(by_id) != len(records):
        raise VerifiedFenPublicationError("diagram_ids_invalid_or_duplicate")

    terminal_review_rows: dict[str, Mapping[str, Any]] = {}
    for row in payload_rows:
        status = str(row.get("label_status") or "").strip().lower()
        if status not in {"verified", "placement_verified", "rejected", "unreadable"}:
            continue
        diagram_id = str(row.get("diagram_id") or "").strip()
        if not diagram_id or diagram_id in terminal_review_rows:
            raise VerifiedFenPublicationError("review_diagram_ids_invalid_or_duplicate")
        record = by_id.get(diagram_id)
        if record is None:
            raise VerifiedFenPublicationError(f"diagram_record_missing:{diagram_id}")
        _verify_review_row_binding(
            row,
            record=record,
            artifact_id=artifact,
            source_digest=source_digest,
            artifact_root=root,
        )
        terminal_review_rows[diagram_id] = row

    applied_ids: set[str] = set()
    for label in labels:
        diagram_id = str(label.get("diagram_id") or "").strip()
        record = by_id.get(diagram_id)
        if record is None:
            raise VerifiedFenPublicationError(f"diagram_record_missing:{diagram_id}")
        _verify_label_binding(
            label,
            record=record,
            artifact_id=artifact,
            source_digest=source_digest,
            artifact_root=root,
        )
        _apply_human_verified_fen(record, label)
        applied_ids.add(diagram_id)

    for diagram_id, row in terminal_review_rows.items():
        status = str(row.get("label_status") or "").strip().lower()
        if status == "verified":
            if diagram_id not in applied_ids:
                raise VerifiedFenPublicationError(f"verified_label_missing_from_corpus:{diagram_id}")
            continue
        _apply_human_non_fen_review(by_id[diagram_id], row)

    automatic_before = sum(1 for row in records if _machine_fen_was_accepted(row))
    automatic_after = sum(
        1
        for row in records
        if _machine_fen_was_accepted(row) and _diagram_id(row) not in terminal_review_rows
    )
    status_counts = _terminal_review_status_counts(terminal_review_rows.values())
    publication_records = [
        row for row in records if row.get("publication_included") is not False
    ]
    accepted = len(applied_ids) + automatic_after
    candidate_without_full_fen = len(records) - accepted
    confirmed_without_full_fen = len(publication_records) - accepted
    placement_or_fen = accepted + status_counts["placement_verified"]
    summary = {
        "diagram_candidates_total": len(records),
        "diagrams_total": len(publication_records),
        "confirmed_diagrams_total": len(publication_records),
        "false_positive_candidates": status_counts["rejected"],
        "fen_human_verified": len(applied_ids),
        "fen_automatic": automatic_after,
        "fen_placement_verified": status_counts["placement_verified"],
        "fen_unreadable": status_counts["unreadable"],
        "fen_unrecognized": confirmed_without_full_fen,
        "candidate_without_full_fen": candidate_without_full_fen,
        "fen_automatic_before_override": automatic_before,
        "fen_accepted": accepted,
        "full_fen_coverage": _safe_ratio(accepted, len(publication_records)),
        "placement_or_fen_coverage": _safe_ratio(
            placement_or_fen,
            len(publication_records),
        ),
    }

    verified_payload = {
        "schema": VERIFIED_DIAGRAMS_SCHEMA,
        "artifact_id": artifact,
        "source_document_sha256": source_digest,
        "generated_at": _utc_now(),
        "summary": summary,
        "records": records,
    }
    verified_diagrams_path = root / "report" / "chess_diagrams_verified.json"
    _atomic_write_json(verified_diagrams_path, verified_payload)

    pgn_path = root / "report" / "chess_verified_positions.pgn"
    _atomic_write_text(pgn_path, _verified_positions_pgn(publication_records))
    epub_path = root / "output" / "chess_verified_positions.epub"
    _build_verified_positions_epub(
        epub_path,
        records=publication_records,
        artifact_root=root,
        source_digest=source_digest,
        title=f"{source_file.stem} - verified chess positions",
    )

    report = {
        "schema": PUBLICATION_SCHEMA,
        "status": "published",
        "artifact_id": artifact,
        "source_document_sha256": source_digest,
        "generated_at": _utc_now(),
        "summary": summary,
        "artifacts": {
            "verified_diagrams": str(verified_diagrams_path),
            "verified_positions_pgn": str(pgn_path),
            "verified_positions_epub": str(epub_path),
            "canonical_labels": str(labels_path),
        },
        "policy": (
            "Only complete source-bound human review rows with matching artifact, source, "
            "fingerprint, diagram id and board crop hash may override runtime FEN."
        ),
    }
    report_path = root / "report" / "chess_verified_fen_publication.json"
    report["artifacts"]["publication_report"] = str(report_path)
    _atomic_write_json(report_path, report)
    return report


def _verify_label_binding(
    label: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
    artifact_id: str,
    source_digest: str,
    artifact_root: Path,
) -> None:
    _verify_review_row_binding(
        label,
        record=record,
        artifact_id=artifact_id,
        source_digest=source_digest,
        artifact_root=artifact_root,
    )
    diagram_id = _diagram_id(record)
    if str(label.get("artifact_id") or "").strip() != artifact_id:
        raise VerifiedFenPublicationError("artifact_id_mismatch")
    if str(label.get("source_document_sha256") or "").strip().lower() != source_digest:
        raise VerifiedFenPublicationError("source_document_sha256_mismatch")
    if label.get("human_verified") is not True or label.get("fen_human_verified") is not True:
        raise VerifiedFenPublicationError("human_fen_verification_required")
    if str(label.get("label_status") or "").strip().lower() != "verified":
        raise VerifiedFenPublicationError("verified_label_status_required")



def _verify_review_row_binding(
    row: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
    artifact_id: str,
    source_digest: str,
    artifact_root: Path,
) -> None:
    if str(row.get("artifact_id") or "").strip() != artifact_id:
        raise VerifiedFenPublicationError("artifact_id_mismatch")
    if str(row.get("source_document_sha256") or "").strip().lower() != source_digest:
        raise VerifiedFenPublicationError("source_document_sha256_mismatch")

    diagram_id = _diagram_id(record)
    if str(row.get("diagram_id") or "").strip() != diagram_id:
        raise VerifiedFenPublicationError(f"diagram_id_mismatch:{diagram_id}")
    page = _diagram_page(record)
    expected_fingerprint = _stable_fingerprint(source_digest, diagram_id, page)
    if str(row.get("diagram_fingerprint") or "").strip().lower() != expected_fingerprint:
        raise VerifiedFenPublicationError(f"diagram_fingerprint_mismatch:{diagram_id}")

    source_crop = _resolve_artifact_path(artifact_root, record.get("board_crop_path"))
    if source_crop is None or not source_crop.is_file():
        raise VerifiedFenPublicationError(f"board_crop_missing:{diagram_id}")
    declared_hash = str(row.get("crop_sha256") or "").strip().lower()
    if len(declared_hash) != _SHA256_LENGTH or _sha256_file(source_crop) != declared_hash:
        raise VerifiedFenPublicationError(f"board_crop_sha256_mismatch:{diagram_id}")


def _apply_human_verified_fen(record: dict[str, Any], label: Mapping[str, Any]) -> None:
    fen = str(label.get("fen") or label.get("manual_fen") or "").strip()
    side = fen.split()[1] if len(fen.split()) >= 2 else ""
    record["machine_fen_evidence"] = {
        "full_fen": str(record.get("full_fen") or ""),
        "fen_candidate": str(record.get("fen_candidate") or ""),
        "full_fen_status": str(record.get("full_fen_status") or ""),
        "full_fen_allowed": bool(record.get("full_fen_allowed")),
        "confidence": record.get("fen_confidence"),
    }
    record.update(
        {
            "full_fen": fen,
            "fen_candidate": "",
            "placement": fen.split()[0],
            "placement_fen": fen.split()[0],
            "side_to_move": side,
            "side_to_move_status": "human_verified",
            "side_to_move_evidence": "human_verified",
            "full_fen_allowed": True,
            "full_fen_status": "FEN_HUMAN_VERIFIED",
            "board_placement_status": "human_verified",
            "status": "accepted",
            "requires_review": False,
            "manual_review_required": False,
            "human_verified": True,
            "fen_human_verified": True,
            "placement_human_verified": True,
            "human_review_status": "verified",
            "confirmed_diagram": True,
            "publication_included": True,
            "fen_source": "human_verified_override",
            "verification_source": str(label.get("verification_source") or "human_visual"),
            "verified_by": str(label.get("verified_by") or ""),
            "verified_at": str(label.get("verified_at") or ""),
            "verified_artifact_id": str(label.get("artifact_id") or ""),
            "verified_source_review_artifact_id": str(
                label.get("source_review_artifact_id") or label.get("artifact_id") or ""
            ),
            "verified_source_document_sha256": str(label.get("source_document_sha256") or ""),
            "verified_diagram_fingerprint": str(label.get("diagram_fingerprint") or ""),
            "verified_board_crop_sha256": str(label.get("crop_sha256") or ""),
            "full_fen_blockers": [],
            "fen_suppressed_reason": "",
            "review_reason": "",
        }
    )


def _apply_human_non_fen_review(record: dict[str, Any], row: Mapping[str, Any]) -> None:
    status = str(row.get("label_status") or "").strip().lower()
    if status not in {"placement_verified", "rejected", "unreadable"}:
        raise VerifiedFenPublicationError("unsupported_non_fen_review_status")
    record["machine_fen_evidence"] = {
        "full_fen": str(record.get("full_fen") or ""),
        "fen_candidate": str(record.get("fen_candidate") or ""),
        "full_fen_status": str(record.get("full_fen_status") or ""),
        "full_fen_allowed": bool(record.get("full_fen_allowed")),
        "confidence": record.get("fen_confidence"),
    }
    placement = ""
    if status == "placement_verified":
        placement = str(row.get("manual_placement") or "").strip()
        if not placement:
            placement = _placement_from_square_labels(row.get("square_labels"))
    reason = {
        "placement_verified": "side_to_move_unknown_after_human_review",
        "rejected": "human_confirmed_false_positive",
        "unreadable": "human_confirmed_unreadable_diagram",
    }[status]
    record.update(
        {
            "full_fen": "",
            "fen": "",
            "fen_candidate": "",
            "placement": placement,
            "placement_fen": placement,
            "side_to_move": "unknown",
            "side_to_move_status": "unknown",
            "side_to_move_evidence": "human_review_no_decision",
            "full_fen_allowed": False,
            "full_fen_status": f"FEN_HUMAN_{status.upper()}",
            "board_placement_status": (
                "human_verified" if status == "placement_verified" else status
            ),
            "status": status,
            "requires_review": False,
            "manual_review_required": False,
            "human_verified": True,
            "fen_human_verified": False,
            "placement_human_verified": status == "placement_verified",
            "human_review_status": status,
            "confirmed_diagram": status != "rejected",
            "publication_included": status != "rejected",
            "fen_source": "human_review_no_full_fen",
            "verification_source": str(row.get("verification_source") or "human_visual"),
            "verified_by": str(row.get("verified_by") or ""),
            "verified_at": str(row.get("verified_at") or ""),
            "full_fen_blockers": [reason],
            "fen_suppressed_reason": reason,
            "review_reason": reason,
        }
    )


def _placement_from_square_labels(value: object) -> str:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 64:
        return ""
    ranks: list[str] = []
    for start in range(0, 64, 8):
        rank = ""
        empty = 0
        for raw_piece in value[start : start + 8]:
            piece = str(raw_piece or "")
            if not piece:
                empty += 1
                continue
            if empty:
                rank += str(empty)
                empty = 0
            rank += piece
        if empty:
            rank += str(empty)
        ranks.append(rank)
    return "/".join(ranks)


def _terminal_review_status_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"verified": 0, "placement_verified": 0, "rejected": 0, "unreadable": 0}
    for row in rows:
        status = str(row.get("label_status") or "").strip().lower()
        if status in counts:
            counts[status] += 1
    return counts


def _safe_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _machine_fen_was_accepted(record: Mapping[str, Any]) -> bool:
    evidence = record.get("machine_fen_evidence") if isinstance(record.get("machine_fen_evidence"), Mapping) else record
    return bool(evidence.get("full_fen_allowed") and str(evidence.get("full_fen") or "").strip())


def _verified_positions_pgn(records: Sequence[Mapping[str, Any]]) -> str:
    chunks: list[str] = []
    accepted = [
        record
        for record in records
        if str(record.get("full_fen") or "").strip()
        and (
            record.get("fen_human_verified") is True
            or record.get("full_fen_allowed") is True
        )
    ]
    ordered = sorted(accepted, key=lambda row: (_diagram_page(row), _diagram_id(row)))
    for index, record in enumerate(ordered, start=1):
        diagram_id = _pgn_header(_diagram_id(record) or f"diagram-{index}")
        page = _diagram_page(record)
        fen = _pgn_header(str(record.get("full_fen") or ""))
        source = "Human verified" if record.get("fen_human_verified") is True else "Automatically accepted"
        chunks.append(
            "\n".join(
                [
                    f'[Event "{source} position {diagram_id}"]',
                    f'[Site "Source page {page}"]',
                    '[Date "????.??.??"]',
                    f'[Round "{index}"]',
                    '[White "?"]',
                    '[Black "?"]',
                    '[Result "*"]',
                    '[SetUp "1"]',
                    f'[FEN "{fen}"]',
                    "",
                    "*",
                ]
            )
        )
    return "\n\n".join(chunks) + ("\n" if chunks else "")


def _build_verified_positions_epub(
    output_path: Path,
    *,
    records: Sequence[Mapping[str, Any]],
    artifact_root: Path,
    source_digest: str,
    title: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    publication_id = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, source_digest + ':verified-fen')}"
    pages: list[tuple[str, str]] = []
    image_entries: list[tuple[str, Path, str]] = []
    for page_index, start in enumerate(range(0, len(records), _PAGE_SIZE), start=1):
        page_records = records[start : start + _PAGE_SIZE]
        articles: list[str] = []
        for offset, record in enumerate(page_records, start=start + 1):
            diagram_id = _diagram_id(record)
            crop_path = _resolve_artifact_path(artifact_root, record.get("board_crop_path"))
            image_href = ""
            if crop_path is not None and crop_path.is_file():
                suffix = crop_path.suffix.lower() if crop_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
                image_href = f"images/diagram-{offset:04d}{suffix}"
                image_entries.append((image_href, crop_path, mimetypes.guess_type(image_href)[0] or "image/png"))
            human_verified = record.get("fen_human_verified") is True
            automatic = record.get("full_fen_allowed") is True and not human_verified
            fen = str(record.get("full_fen") or "") if human_verified or automatic else ""
            side = str(record.get("side_to_move") or "")
            alt = f"Chess position {diagram_id}." + (f" {'White' if side == 'w' else 'Black'} to move." if side in {"w", "b"} else "")
            image_html = (
                f'<figure><img src="{html.escape(image_href, quote=True)}" alt="{html.escape(alt, quote=True)}"/></figure>'
                if image_href
                else "<p>Diagram image unavailable.</p>"
            )
            if human_verified:
                fen_html = f'<p class="verified">Human verified FEN</p><code>{html.escape(fen)}</code>'
                fen_source = "human_verified"
            elif automatic:
                fen_html = f'<p class="automatic">Automatically accepted FEN</p><code>{html.escape(fen)}</code>'
                fen_source = "automatic"
            else:
                fen_html = '<p class="review">FEN unavailable or excluded from verified publication.</p>'
                fen_source = "unrecognized"
            articles.append(
                f'<article id="diagram-{html.escape(diagram_id, quote=True)}" data-fen-source="'
                f'{fen_source}"><h2>{offset}. {html.escape(diagram_id)}</h2>'
                f'<p>Source page {_diagram_page(record)}</p>{image_html}{fen_html}</article>'
            )
        name = f"positions-{page_index:03d}.xhtml"
        pages.append((name, _xhtml_document(title, "\n".join(articles))))

    nav_items = "".join(
        f'<li><a href="{html.escape(name, quote=True)}">Positions {(index - 1) * _PAGE_SIZE + 1}-'
        f'{min(index * _PAGE_SIZE, len(records))}</a></li>'
        for index, (name, _content) in enumerate(pages, start=1)
    )
    nav = _xhtml_document(title, f'<nav epub:type="toc" id="toc"><h1>{html.escape(title)}</h1><ol>{nav_items}</ol></nav>', epub_prefix=True)
    manifest_pages = "".join(
        f'<item id="page-{index}" href="{name}" media-type="application/xhtml+xml"/>'
        for index, (name, _content) in enumerate(pages, start=1)
    )
    manifest_images = "".join(
        f'<item id="image-{index}" href="{href}" media-type="{media_type}"/>'
        for index, (href, _path, media_type) in enumerate(image_entries, start=1)
    )
    spine = "".join(f'<itemref idref="page-{index}"/>' for index in range(1, len(pages) + 1))
    package = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id" xml:lang="en">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">{publication_id}</dc:identifier><dc:title>{html.escape(title)}</dc:title>
    <dc:language>en</dc:language><dc:subject>Chess positions</dc:subject>
    <meta property="dcterms:modified">{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}</meta>
  </metadata>
  <manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="styles.css" media-type="text/css"/>{manifest_pages}{manifest_images}</manifest>
  <spine>{spine}</spine>
</package>'''
    container = '''<?xml version="1.0" encoding="utf-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''
    css = (
        "body{font-family:Georgia,serif;line-height:1.5;margin:5%;color:#17130f}"
        "article{break-after:page;margin:0 auto 2rem;max-width:42rem}"
        "img{display:block;max-width:100%;height:auto;margin:auto}"
        "code{display:block;overflow-wrap:anywhere;background:#f3eee5;padding:.75rem}"
        ".verified{font-weight:bold;color:#176b3a}.automatic{font-weight:bold;color:#245f85}.review{color:#8a4b18}"
    )
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("EPUB/package.opf", package)
        archive.writestr("EPUB/nav.xhtml", nav)
        archive.writestr("EPUB/styles.css", css)
        for name, content in pages:
            archive.writestr(f"EPUB/{name}", content)
        for href, path, _media_type in image_entries:
            archive.write(path, f"EPUB/{href}")
    os.replace(temporary, output_path)


def _xhtml_document(title: str, body: str, *, epub_prefix: bool = False) -> str:
    epub_namespace = ' xmlns:epub="http://www.idpf.org/2007/ops"' if epub_prefix else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<html xmlns="http://www.w3.org/1999/xhtml"{epub_namespace} lang="en"><head>'
        f'<title>{html.escape(title)}</title><link rel="stylesheet" href="styles.css"/></head><body>{body}</body></html>'
    )


def _resolve_artifact_path(root: Path, value: object) -> Path | None:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        return None
    candidate = (root / Path(*path.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _single_source_file(directory: Path) -> Path:
    files = sorted(path for path in directory.iterdir() if path.is_file()) if directory.is_dir() else []
    if len(files) != 1:
        raise VerifiedFenPublicationError("single_source_document_required")
    return files[0]


def _diagram_id(record: Mapping[str, Any]) -> str:
    return str(record.get("diagram_id") or record.get("id") or "").strip()


def _diagram_page(record: Mapping[str, Any]) -> int:
    try:
        return max(0, int(record.get("page_number") or record.get("page") or record.get("page_index") or 0))
    except (TypeError, ValueError):
        return 0


def _stable_fingerprint(source_digest: str, diagram_id: str, page: int) -> str:
    return hashlib.sha256(f"{source_digest}:{diagram_id}:{max(0, int(page))}".encode("utf-8")).hexdigest()


def _pgn_header(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerifiedFenPublicationError(f"invalid_json:{path.name}") from error
    if not isinstance(payload, dict):
        raise VerifiedFenPublicationError(f"invalid_json_object:{path.name}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
