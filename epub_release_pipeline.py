from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from epub_quality_recovery import main
from epub_quality_recovery import (
    _inventory_epub,
    _prepare_output_paths,
)
from kindle_semantic_cleanup import finalize_epub_for_kindle
from premium_tools import run_epubcheck
from quality_report_markdown import build_manual_review_markdown, build_release_pipeline_report_markdown
from quality_reporting import (
    build_heading_report_payload,
    build_release_pipeline_decision,
    build_release_pipeline_metadata_payload,
    build_release_pipeline_toc_payload,
    compare_heading_snapshots,
    dedupe_review_items,
    summarize_heading_decisions,
)


@dataclass(frozen=True)
class ReleasePipelineConfig:
    input_epub: str | Path
    output_dir: str | Path = "output"
    reports_dir: str | Path = "reports"
    title: str = ""
    author: str = ""
    description: str = ""
    language: str = ""
    publication_profile: str | None = None


def run_release_pipeline(config: ReleasePipelineConfig) -> dict[str, object]:
    source_path = Path(config.input_epub).resolve()
    original_bytes = source_path.read_bytes()
    paths = _prepare_output_paths(output_dir=Path(config.output_dir), reports_dir=Path(config.reports_dir))
    baseline_inventory = _inventory_epub(original_bytes, label="baseline")

    finalized = finalize_epub_for_kindle(
        original_bytes,
        title=config.title or baseline_inventory["metadata"]["primary"].get("title", ""),
        author=config.author or baseline_inventory["metadata"]["primary"].get("creator", ""),
        language=config.language or baseline_inventory["metadata"]["primary"].get("language", "") or "en",
        publication_profile=config.publication_profile,
        return_report=True,
        report_mode="reference",
    )
    if isinstance(finalized, tuple):
        final_bytes, finalize_report = finalized
    else:
        final_bytes, finalize_report = finalized, {}

    final_inventory = _inventory_epub(final_bytes, label="final")
    epubcheck_payload = run_epubcheck(final_bytes)

    heading_decisions: list[dict[str, object]] = []
    manual_review = list(finalize_report.get("manual_review_queue") or [])
    for file_name, before_snapshot in baseline_inventory.get("headings", {}).items():
        after_snapshot = final_inventory.get("headings", {}).get(file_name, [])
        file_decisions, file_review = compare_heading_snapshots(
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            file_name=file_name,
        )
        heading_decisions.extend(file_decisions)
        manual_review.extend(file_review)
    manual_review = dedupe_review_items(manual_review)
    heading_summary = summarize_heading_decisions(heading_decisions, final_inventory)

    metadata_payload = build_release_pipeline_metadata_payload(
        baseline_inventory=baseline_inventory,
        final_inventory=final_inventory,
    )
    toc_payload = build_release_pipeline_toc_payload(final_inventory=final_inventory)
    structural_payload = final_inventory.get("structural_integrity", {})
    decision = build_release_pipeline_decision(
        epubcheck_payload=epubcheck_payload,
        manual_review_count=len(manual_review),
    )

    paths.final_epub.write_bytes(final_bytes)
    paths.metadata_diff.write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.heading_decisions.write_text(
        json.dumps({"summary": heading_summary, "decisions": heading_decisions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths.toc_map.write_text(json.dumps(toc_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.structural_integrity.write_text(json.dumps(structural_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.epubcheck.write_text(json.dumps(epubcheck_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.manual_review_queue.write_text(build_manual_review_markdown(manual_review), encoding="utf-8")
    paths.release_report.write_text(
        build_release_pipeline_report_markdown(
            decision=decision,
            source_epub=str(source_path),
            final_epub=str(paths.final_epub),
            epubcheck_payload=epubcheck_payload,
            metadata_payload=metadata_payload,
            toc_payload=toc_payload,
            heading_summary=heading_summary,
            manual_review_count=len(manual_review),
        ),
        encoding="utf-8",
    )
    return {
        "release_decision": {"decision": decision, "epubcheck_status": epubcheck_payload.get("status", "unavailable")},
        "final_epub": str(paths.final_epub),
        "reports": {
            "metadata_diff": str(paths.metadata_diff),
            "heading_decisions": str(paths.heading_decisions),
            "toc_map": str(paths.toc_map),
            "structural_integrity": str(paths.structural_integrity),
            "epubcheck": str(paths.epubcheck),
            "release_report": str(paths.release_report),
            "manual_review_queue": str(paths.manual_review_queue),
        },
        "gates": {},
    }
if __name__ == "__main__":
    raise SystemExit(main())
