from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

TEST_FILE_PATTERN = "test*.py"

QUICK_TESTS = [
    "test_agent_config_contracts.py",
    "test_skill_contracts.py",
    "test_skill_guardrails.py",
    "test_github_ready_enforcement.py",
    "test_github_issue_orchestration.py",
    "test_project_status.py",
    "test_pdf_runtime_flow.py",
    "test_kindlemaster_entrypoint.py",
    "test_premium_tools.py",
    "test_flat2_ui_template.py",
    "test_sprint4_ui_contracts.py",
    "test_browser_conversion_outcome_harness.py",
    "test_app_async_convert.py",
    "test_supabase_auth.py",
    "test_supabase_library.py",
    "test_supabase_profile.py",
    "test_supabase_migrations.py",
    "test_email_delivery.py",
    "test_user_profile.py",
    "test_conversion_library.py",
    "test_app_runtime_services.py",
    "test_runtime_job_adapter.py",
    "test_artifact_storage.py",
    "test_ai_ocr_cleanup.py",
    "test_ai_toc_detection.py",
    "test_ai_quality_intelligence.py",
    "test_ai_quality_feedback.py",
    "test_openai_quality_provider.py",
    "test_ai_chess_providers.py",
    "test_app_quality_state_route.py",
    "test_sentry_observability.py",
    "test_docx_conversion.py",
    "test_app_docx_conversion.py",
    "test_epub_validation.py",
    "test_chess_fix.py",
    "test_chess_diagram_visual_quality.py",
    "test_chess_notation_regression.py",
    "test_chess_notation_reflow.py",
    "test_chess_pgn_extraction.py",
    "test_chess_html_audit.py",
    "test_chess_diagram_detection.py",
    "test_chess_glyph_diagnostics.py",
    "test_chess_fen_square_diff.py",
    "test_chess_fen_blockers.py",
    "test_chess_fen_review_blockers.py",
    "test_chess_fen_strict_regression_gate.py",
    "test_chess_fen_strict_report_diff.py",
    "test_chess_fen_strict_readiness.py",
    "test_chess_fen_best_strict_baseline.py",
    "test_chess_fen_accepted_audit.py",
    "test_chess_auto_flow.py",
    "test_ai_consensus_fen_promotion_queue.py",
    "test_ai_tiebreak_fen_review_queue.py",
    "test_chess_fen_hard_cases.py",
    "test_chess_fen_pipeline_hardening.py",
    "test_chess_fen_ml_acceptance.py",
    "test_chess_fen_model_pipeline.py",
    "test_chess_study_data_contracts.py",
    "test_pdf_layout_preview.py",
    "test_deepseek_quality_provider.py",
    "test_chess_study_structure.py",
    "test_chess_study_pipeline.py",
    "test_chess_study_render.py",
    "test_converter_publication_budget.py",
    "test_fixed_layout_render_budget.py",
    "test_converter_fixed_layout_budget_enforcement.py",
    "test_ml_features.py",
    "test_ml_route_model.py",
    "test_ml_datasets.py",
    "test_ml_training_reporting.py",
    "test_ml_quality_verifier.py",
    "test_publication_analysis.py",
    "test_publication_pipeline.py",
    "test_quality_state_service.py",
    "test_quality_cockpit_issues.py",
    "test_quality_cockpit_preview.py",
    "test_sprint1_quality_gates.py",
    "test_run_smoke_tests.py",
    "test_magazine_kindle_reflow.py",
    "test_magazine_epub_quality_gate.py",
    "test_epub_premium_scoring.py",
    "test_smoke_chess_quality.py",
    "test_conversion_cleanup_ttl_contract.py",
    "test_vat_fixture_contracts.py",
    "test_prepare_reference_inputs_ocr_fixture.py",
    "test_pdf_weight_reducer.py",
    "test_reference_inputs_document_like_fixture.py",
    "test_epub_text_artifacts.py",
    "test_text_normalization.py",
    "test_converter_text_cleanup.py",
    "test_semantic_epub_cleanup.py",
    "test_epub_delivery_repair.py",
    "test_epub_quality_selection.py",
    "test_epub_reference_repair.py",
    "test_epub_heading_repair.py",
]

RELEASE_TESTS = [
    "test_toc_segmentation.py",
    "test_epub_quality_selection.py",
    "test_epub_quality_recovery.py",
    "test_release_quality_recovery.py",
    "test_epub_release_pipeline.py",
    "test_app_heading_repair.py",
]

RELEASE_TIMEOUT_RETURN_CODE = 124
RELEASE_STEP_TIMEOUTS_SECONDS = {
    "release-units": 300,
    "corpus-units": 600,
    "corpus-gate-standard": 2700,
    "corpus-gate-ci": 900,
    "browser-followup": 300,
    "runtime-followup": 900,
}

RELEASE_PROOF_PROFILES = {"standard", "ci"}

CORPUS_TESTS = [
    "test_premium_corpus_smoke.py",
    "test_premium_corpus_smoke_batches.py",
    "test_corpus_gate.py",
    "test_chess_fen_recognition.py",
    "test_golden_epub_regression.py",
]

QUALITY_CRITICAL_TESTS = [
    "test_docx_conversion.py",
    "test_converter_core_paths.py",
    "test_converter_fixed_layout_budget_enforcement.py",
    "test_converter_text_cleanup.py",
    "test_text_normalization.py",
    "test_epub_validation.py",
    "test_semantic_epub_cleanup.py",
    "test_app_runtime_services.py",
    "test_epub_delivery_repair.py",
    "test_epub_quality_recovery.py",
    "test_release_quality_recovery.py",
]

QUALITY_CRITICAL_COVERAGE_SOURCES = [
    "converter",
    "docx_conversion",
    "text_cleanup_engine",
    "text_normalization",
    "kindle_semantic_cleanup",
    "epub_validation",
]

QUALITY_CRITICAL_TOTAL_COVERAGE_DEFAULT = 70.0
QUALITY_CRITICAL_CONVERTER_COVERAGE_DEFAULT = 60.0
QUALITY_CRITICAL_TEXT_NORMALIZATION_COVERAGE_DEFAULT = 65.0
QUALITY_CRITICAL_SEMANTIC_CLEANUP_COVERAGE_DEFAULT = 70.0

BROWSER_TESTS = [
    "test_browser_polling_runtime_harness.py",
    "test_react_shell_browser_smoke.py",
]

RUNTIME_TESTS = [
    "test_runtime_waitress_smoke.py",
    "test_browser_polling_e2e.py",
    "test_sprint2_playwright_smoke.py",
    "test_browser_privacy_diagnostics.py",
    "test_ui_state_screenshot_pack.py",
]

DISCOVER_ONLY_TESTS = [
    "test_conversion_api_contracts.py",
    "test_converter_coverage_boost.py",
    "test_converter_metadata_cover.py",
    "test_kindle_semantic_cleanup_coverage_boost.py",
    "test_full_magazine.py",
    "test_integration.py",
    "test_app_pdf_compression.py",
    "test_import_fen_priority_review_batch.py",
    "test_js_artifact_links.py",
    "test_local_hostname_contract.py",
    "test_magazine_conversion.py",
    "test_ocr_module.py",
    "test_babok_dense_handbook_quality.py",
    "test_premium_reflow.py",
    "test_premium_reflow_tables.py",
    "test_quality_report_markdown.py",
    "test_quality_reporting.py",
    "test_chess_fen_workflow_state_model.py",
    "test_chess_full_automation_ready.py",
    "test_chess_fen_dataset_tools.py",
    "test_chess_fen_placement_review_dashboard.py",
    "test_chess_fen_square_debug_artifacts.py",
    "test_chess_fen_template_strategy.py",
    "test_chess_reading_order_audit.py",
    "test_fen_automation_readiness.py",
    "test_scanned_chess_detector.py",
    "test_external_chessimg2pos_provider.py",
    "test_external_pgn_extract_provider.py",
    "test_workflow_runner.py",
]

SUITE_REGISTRY: dict[str, Sequence[str]] = {
    "quick": QUICK_TESTS,
    "release": RELEASE_TESTS,
    "corpus": CORPUS_TESTS,
    "quality-critical": QUALITY_CRITICAL_TESTS,
    "browser": BROWSER_TESTS,
    "runtime": RUNTIME_TESTS,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Standard operational entrypoint for KindleMaster.")
    subparsers = parser.add_subparsers(dest="command")

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Install the supported Python bootstrap profile (runtime-only or developer).",
    )
    bootstrap_parser.add_argument("--runtime-only", action="store_true")

    subparsers.add_parser(
        "doctor",
        help="Print the supported-vs-optional local toolchain matrix and detected availability.",
    )
    subparsers.add_parser("prepare-reference-inputs", help="Copy curated reference fixtures into reference_inputs/.")

    serve_parser = subparsers.add_parser("serve", help="Run the local KindleMaster web app.")
    serve_parser.add_argument("--port", type=int, default=None)
    serve_parser.add_argument("--debug", action="store_true")
    serve_parser.add_argument("--runtime", choices=("flask", "waitress"), default="flask")
    serve_parser.add_argument(
        "--skip-ui-build",
        action="store_true",
        help="Do not auto-build the React UI before serving; /app will fail clearly if the build is missing.",
    )

    convert_parser = subparsers.add_parser("convert", help="Convert a PDF or DOCX file to EPUB.")
    convert_parser.add_argument("input_path")
    convert_parser.add_argument("--output", required=True)
    convert_parser.add_argument("--language", default="pl")
    convert_parser.add_argument("--profile", default="auto-premium")
    convert_parser.add_argument("--route-model-mode", choices=("off", "shadow", "assist"), default="shadow")
    convert_parser.add_argument("--quality-gate-mode", choices=("off", "draft"), default="draft")
    convert_parser.add_argument("--heading-repair", action="store_true")
    convert_parser.add_argument("--domain-dictionary", default="")
    convert_parser.add_argument("--report-json", default="")

    process_parser = subparsers.add_parser("process", help="Run the front-door automatic chess PDF flow.")
    process_parser.add_argument("input_path", nargs="?")
    process_parser.add_argument("--out", default="")
    process_parser.add_argument("--mode", choices=("auto", "auto-strict"), default="auto")
    process_parser.add_argument("--html", default="")
    process_parser.add_argument("--quality-profile", choices=("smoke", "default", "masterkindle"), default="default")
    process_parser.add_argument("--render-pages", action="store_true")
    process_parser.add_argument("--diagram-page-ranges", default="")
    process_parser.add_argument("--glyph-mapping-file", default="")
    process_parser.add_argument("--with-ai", action="store_true", help="Run optional AI candidate passes; AI remains review-only.")
    process_parser.add_argument("--dry-run-ai", action="store_true", help="Write AI request manifests without live API calls.")
    process_parser.add_argument("--ai-limit", type=int, default=0)
    process_parser.add_argument("--ai-pgn-limit", type=int, default=30)
    process_parser.add_argument(
        "--chess-fen-recognition-max-diagrams",
        default="all",
        help="Maximum diagrams to run through runtime FEN acceptance; 0/all means all diagrams.",
    )

    validate_parser = subparsers.add_parser("validate", help="Run EPUB validators or validate an auto chess output directory.")
    validate_parser.add_argument("epub_paths", nargs="+")
    validate_parser.add_argument("--reports-dir", default="reports/validators")
    validate_parser.add_argument("--strict", action="store_true", help="For auto chess output directories, fail on unresolved FEN/PGN review items.")

    report_parser = subparsers.add_parser("report", help="Build or print an auto chess flow report.")
    report_parser.add_argument("out_dir")

    review_parser = subparsers.add_parser("review", help="Build an index of auto chess manual review artifacts.")
    review_parser.add_argument("out_dir")

    smoke_parser = subparsers.add_parser("smoke", help="Run curated smoke tests.")
    smoke_parser.add_argument("--mode", choices=("micro", "quick", "full"), default="quick")
    smoke_parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    smoke_parser.add_argument("--output-dir", default="output/smoke")
    smoke_parser.add_argument("--reports-dir", default="reports/smoke")
    smoke_parser.add_argument("--case", action="append", default=[])

    corpus_parser = subparsers.add_parser("corpus", help="Run the standard corpus-wide proof gate.")
    corpus_parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    corpus_parser.add_argument("--output-root", default="output/corpus")
    corpus_parser.add_argument("--reports-root", default="reports/corpus")
    corpus_parser.add_argument("--proof-profile", choices=("standard", "full", "ci"), default="standard")
    corpus_parser.add_argument("--smoke-case", action="append", default=[])
    corpus_parser.add_argument("--premium-case", action="append", default=[])
    corpus_parser.add_argument(
        "--fen-min-profile-count",
        type=int,
        default=None,
        help=(
            "Override the FEN profile-count gate. By default standard/full corpus proof requires "
            "2 scanned chess FEN profiles; CI proof remains bounded at 1."
        ),
    )
    corpus_parser.add_argument("--fen-min-seed-label-count", type=int, default=20)

    status_parser = subparsers.add_parser("status", help="Generate a derived project status from existing evidence artifacts.")
    status_parser.add_argument("--repo-root", default=".")
    status_parser.add_argument("--reports-root", default="reports")
    status_parser.add_argument("--output-json", default="reports/project_status.json")
    status_parser.add_argument("--output-md", default="reports/project_status.md")

    ml_parser = subparsers.add_parser("ml", help="Build datasets, train, and evaluate local ML route/review helpers.")
    ml_subparsers = ml_parser.add_subparsers(dest="ml_command")
    ml_dataset = ml_subparsers.add_parser("dataset", help="Build ML JSONL datasets from manifest and existing reports.")
    ml_dataset.add_argument("--manifest", default="reference_inputs/manifest.json")
    ml_dataset.add_argument("--labels", default="reference_inputs/ml_labels.json")
    ml_dataset.add_argument("--reports-root", default="reports")
    ml_dataset.add_argument("--output-dir", default="reports/ml/datasets")
    ml_dataset.add_argument("--feedback-log", action="append", default=[])
    ml_dataset.add_argument("--fail-on-collisions", action="store_true")
    ml_dataset.add_argument("--min-examples-per-class", type=int, default=25)

    ml_import = ml_subparsers.add_parser("import-reference", help="Import new reference_inputs PDF/DOCX files into manifest and ML labels.")
    ml_import.add_argument("--manifest", default="reference_inputs/manifest.json")
    ml_import.add_argument("--labels", default="reference_inputs/ml_labels.json")
    ml_import.add_argument("--input-type", action="append", choices=("pdf", "docx"), default=[])
    ml_import.add_argument("--dry-run", action="store_true")

    ml_sample = ml_subparsers.add_parser("sample-reference", help="Create fast ML/corpus samples from large reference PDFs.")
    ml_sample.add_argument("--manifest", default="reference_inputs/manifest.json")
    ml_sample.add_argument("--labels", default="reference_inputs/ml_labels.json")
    ml_sample.add_argument("--input-type", action="append", choices=("pdf",), default=[])
    ml_sample.add_argument("--output-dir", default="reference_inputs/pdf_samples")
    ml_sample.add_argument("--max-pages", type=int, default=80)
    ml_sample.add_argument("--min-pages", type=int, default=150)
    ml_sample.add_argument("--min-size-bytes", type=int, default=20 * 1024 * 1024)
    ml_sample.add_argument("--dry-run", action="store_true")

    ml_feedback = ml_subparsers.add_parser("feedback", help="Log or export local conversion feedback without online learning.")
    ml_feedback.add_argument("--report-json", default="")
    ml_feedback.add_argument("--log", default="reports/ml/feedback/conversion_feedback.jsonl")
    ml_feedback.add_argument("--source", default="")
    ml_feedback.add_argument("--output", default="")
    ml_feedback.add_argument("--case-id", default="")
    ml_feedback.add_argument("--feedback-status", choices=("accepted", "needs_review", "rejected"), default="needs_review")
    ml_feedback.add_argument("--quality-label", choices=("unknown", "premium", "good", "usable", "poor", "blocked"), default="unknown")
    ml_feedback.add_argument("--quality-score", default=None)
    ml_feedback.add_argument("--route-label", default="")
    ml_feedback.add_argument("--issue-tag", action="append", default=[])
    ml_feedback.add_argument("--notes", default="")
    ml_feedback.add_argument("--reviewer", default="")
    ml_feedback.add_argument("--export-dir", default="")
    ml_feedback.add_argument("--include-in-training", action="store_true")

    ml_train = ml_subparsers.add_parser("train", help="Train the local route classifier and export JSON inference weights.")
    ml_train.add_argument("--dataset", default="reports/ml/datasets/route_examples.jsonl")
    ml_train.add_argument("--model", default="")
    ml_train.add_argument("--report", default="")
    ml_train.add_argument("--min-examples-per-class", type=int, default=25)

    ml_evaluate = ml_subparsers.add_parser("evaluate", help="Evaluate a JSON route model without importing scikit-learn.")
    ml_evaluate.add_argument("--dataset", default="reports/ml/datasets/route_examples.jsonl")
    ml_evaluate.add_argument("--model", default="models/route_classifier_v1.json")
    ml_evaluate.add_argument("--report", default="reports/ml/route_classifier_v1.evaluation.json")

    ml_feedback = ml_subparsers.add_parser("feedback-export", help="Export local conversion/user feedback events into an ML JSONL dataset.")
    ml_feedback.add_argument("--feedback-log", default="reports/ml/feedback/conversion_feedback.jsonl")
    ml_feedback.add_argument("--output", default="reports/ml/datasets/quality_feedback_examples.jsonl")

    ml_promote = ml_subparsers.add_parser("promote", help="Promote a candidate route model only after metric and corpus gates pass.")
    ml_promote.add_argument("--candidate", required=True)
    ml_promote.add_argument("--model", default="models/route_classifier_v1.json")
    ml_promote.add_argument("--corpus-report", default="reports/corpus/premium_corpus_smoke_report.json")

    test_parser = subparsers.add_parser("test", help="Run standard KindleMaster test suites.")
    test_parser.add_argument(
        "--suite",
        choices=("quick", "release", "full", "browser", "runtime", "corpus", "quality-critical"),
        default="quick",
    )

    audit_parser = subparsers.add_parser("audit", help="Run release audit on an EPUB.")
    audit_parser.add_argument("epub_path")
    audit_parser.add_argument("--output-dir", default="output")
    audit_parser.add_argument("--reports-dir", default="reports")
    audit_parser.add_argument("--language", default="")
    audit_parser.add_argument("--title", default="")
    audit_parser.add_argument("--author", default="")
    audit_parser.add_argument("--description", default="")
    audit_parser.add_argument("--publication-profile", default="")
    audit_parser.add_argument("--strict-premium", action="store_true")
    audit_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Stop the release audit after N seconds and return partial evidence instead of hanging.",
    )

    chess_study_parser = subparsers.add_parser("chess-study", help="Build a static chess training-book study export.")
    chess_study_subparsers = chess_study_parser.add_subparsers(dest="chess_study_command")
    for command_name in [
        "run-all",
        "audit-current",
        "extract-structure",
        "segment-pages",
        "detect-diagrams",
        "recognize-fen",
        "extract-pgn",
        "link-exercises",
        "validate",
        "render",
        "fen-review",
        "build-fen-templates",
        "evaluate-fen-profile",
        "pgn-review",
        "quality-dashboard",
        "ai-fen-candidates",
        "ai-pgn-candidates",
        "ai-quality-eval",
        "quality-baseline",
        "preprocess-boards",
        "build-square-dataset",
        "train-fen-classifier",
        "evaluate-fen-classifier",
        "recognize-fen-local",
        "evaluate-fen-ensemble",
        "calibrate-fen-confidence",
        "export-fen-corpus-manifest",
    ]:
        stage_parser = chess_study_subparsers.add_parser(command_name, help=f"Run chess-study {command_name}.")
        stage_parser.add_argument("--pdf", default="")
        stage_parser.add_argument("--html", default="")
        stage_parser.add_argument("--out", default="output/yusupov_study")
        stage_parser.add_argument("--diagram-pages", type=int, default=0)
        stage_parser.add_argument("--diagram-page-ranges", default="", help='1-based inclusive diagram sample ranges, for example "10-20,40-45". Overrides --diagram-pages when set.')
        stage_parser.add_argument("--diagram-dpi", type=int, default=160)
        stage_parser.add_argument("--min-grid-confidence", type=float, default=0.50)
        stage_parser.add_argument("--max-candidates-per-page", type=int, default=6)
        stage_parser.add_argument(
            "--low-confidence-diagram-review",
            action="store_true",
            help="Add extra low-confidence diagram candidates to review artifacts only; never to accepted FEN.",
        )
        stage_parser.add_argument("--low-confidence-min-grid-confidence", type=float, default=0.30)
        stage_parser.add_argument("--low-confidence-max-candidates-per-page", type=int, default=12)
        stage_parser.add_argument("--glyph-context-pages", default="", help='Restrict raw glyph context augmentation to 1-based page ranges, for example "9-12". Empty means all pages.')
        stage_parser.add_argument("--review-sample-limit", type=int, default=0, help="Limit rows written to review datasets; 0 writes all rows.")
        stage_parser.add_argument("--fen-review-min-count", type=int, default=50, help="When --diagram-page-ranges is used for fen-review, extend with later diagram pages until this many rows are queued; 0 disables extension.")
        stage_parser.add_argument("--diagram-review-labels", default="", help="CSV or JSONL manual diagram labels exported from review/diagram_review.")
        stage_parser.add_argument("--glyph-mapping-file", default="", help="JSON file with accepted OCR token mappings for chess notation review.")
        stage_parser.add_argument("--diagram-alignment-review", action="store_true", help="Generate crop alignment review variants for manually labeled diagrams.")
        stage_parser.add_argument("--labels", default="", help="Verified/draft FEN labels JSONL for template build or holdout evaluation.")
        stage_parser.add_argument("--profile", default="study_manual_verified", help="FEN template/evaluation profile name.")
        stage_parser.add_argument("--template-output-dir", default="", help="Optional output directory for generated FEN templates.")
        stage_parser.add_argument("--fold-count", type=int, default=5)
        stage_parser.add_argument("--holdout-fold", type=int, default=0)
        stage_parser.add_argument("--quality-profile", choices=("smoke", "default", "masterkindle"), default="default")
        stage_parser.add_argument(
            "--render-pages",
            dest="render_pages",
            action="store_true",
            default=False,
            help="Render source PDF page images for audit/debug; disabled by default for semantic study exports.",
        )
        stage_parser.add_argument("--no-render-pages", dest="render_pages", action="store_false")
        stage_parser.add_argument("--ocr-fallback", action="store_true")
        stage_parser.add_argument("--strict-thresholds", action="store_true")
        stage_parser.add_argument("--dry-run", action="store_true", help="For AI-assisted chess-study commands, write request manifests without live API calls.")
        stage_parser.add_argument("--ai-limit", type=int, default=0, help="Limit AI-assisted FEN candidate rows; 0 means all rows.")
        stage_parser.add_argument("--ai-pgn-limit", type=int, default=30, help="Limit AI-assisted PGN repair rows.")
        stage_parser.add_argument("--model-path", default="", help="Optional local FEN model path for classifier/inference commands.")
        stage_parser.add_argument("--min-confidence", type=float, default=0.92, help="Minimum local/ensemble confidence for review gates.")

    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Run the standard engineering workflow: reproduce, isolate, validate, and compare.",
    )
    workflow_subparsers = workflow_parser.add_subparsers(dest="workflow_command")

    workflow_baseline = workflow_subparsers.add_parser("baseline", help="Create baseline artifacts for a change workflow.")
    workflow_baseline.add_argument("input_path")
    workflow_baseline.add_argument("--change-area", required=True, choices=("app", "converter", "reference", "heading", "text", "semantic", "pipeline", "corpus"))
    workflow_baseline.add_argument("--reports-root", default="reports/workflows")
    workflow_baseline.add_argument("--output-root", default="output/workflows")

    workflow_verify = workflow_subparsers.add_parser("verify", help="Verify a workflow run against an existing baseline.")
    workflow_verify.add_argument("input_path")
    workflow_verify.add_argument("--run-id", required=True)
    workflow_verify.add_argument("--reports-root", default="reports/workflows")
    workflow_verify.add_argument("--output-root", default="output/workflows")

    orchestrate_parser = subparsers.add_parser(
        "orchestrate",
        help="Validate and prepare GitHub Issue contracts for local Codex autopilot.",
    )
    orchestrate_parser.add_argument("--repo-root", default=".")
    orchestrate_subparsers = orchestrate_parser.add_subparsers(dest="orchestrate_command")

    orchestrate_doctor = orchestrate_subparsers.add_parser("doctor", help="Check GitHub autopilot governance files.")
    orchestrate_doctor.add_argument("--repo-root", default=".")

    orchestrate_sync = orchestrate_subparsers.add_parser("sync", help="Validate a GitHub issue JSON payload.")
    orchestrate_sync.add_argument("--issues-json", required=True)
    orchestrate_sync.add_argument("--output-json", default="")

    orchestrate_claim = orchestrate_subparsers.add_parser("claim", help="Prepare or apply a branch claim for one issue.")
    orchestrate_claim.add_argument("--issues-json", required=True)
    orchestrate_claim.add_argument("--issue-number", type=int, default=None)
    orchestrate_claim.add_argument("--output-json", default="")
    orchestrate_claim.add_argument("--apply-branch", action="store_true")
    orchestrate_claim.add_argument("--repo-root", default=".")

    orchestrate_execute = orchestrate_subparsers.add_parser("execute", help="Build the local agent execution contract.")
    orchestrate_execute.add_argument("--issues-json", required=True)
    orchestrate_execute.add_argument("--issue-number", type=int, default=None)
    orchestrate_execute.add_argument("--output-json", default="")

    orchestrate_report = orchestrate_subparsers.add_parser("report", help="Build a PR/issue-ready evidence summary.")
    orchestrate_report.add_argument("--issues-json", required=True)
    orchestrate_report.add_argument("--issue-number", type=int, default=None)
    orchestrate_report.add_argument("--evidence", action="append", default=[])
    orchestrate_report.add_argument("--output-json", default="")
    orchestrate_report.add_argument("--output-md", default="")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "bootstrap":
        return _run_bootstrap(runtime_only=args.runtime_only)
    if args.command == "doctor":
        from premium_tools import detect_toolchain

        started = time.perf_counter()
        payload = detect_toolchain(refresh=True)
        _write_governance_artifact(
            lane="doctor",
            payload=_governance_artifact_payload(
                command="python kindlemaster.py doctor",
                status=_derive_doctor_artifact_status(payload),
                returncode=0,
                started=started,
                notes=["Toolchain and agent-readiness detection completed."],
                extra={
                    "verification_surfaces": payload.get("verification_surfaces", {}),
                    "agent_readiness": payload.get("agent_readiness", {}),
                    "payload": payload,
                },
            ),
        )
        _print_json(payload)
        return 0 if payload.get("overall_status") != "failed" else 1
    if args.command == "prepare-reference-inputs":
        from scripts.prepare_reference_inputs import prepare_reference_inputs

        _print_json(prepare_reference_inputs())
        return 0
    if args.command == "serve":
        return _run_serve(
            port=args.port,
            debug=args.debug,
            runtime=args.runtime,
            skip_ui_build=args.skip_ui_build,
        )
    if args.command == "convert":
        return _run_convert(
            input_path=args.input_path,
            output_path=args.output,
            language=args.language,
            profile=args.profile,
            route_model_mode=args.route_model_mode,
            quality_gate_mode=args.quality_gate_mode,
            heading_repair=args.heading_repair,
            domain_dictionary=args.domain_dictionary,
            report_json=args.report_json,
        )
    if args.command == "process":
        from chess_auto_flow import non_strict_python_chess_notice, run_auto_chess_process, strict_python_chess_preflight

        if args.mode == "auto-strict":
            preflight = strict_python_chess_preflight()
            if preflight.get("status") == "failed":
                _print_json(preflight)
                return 1
        else:
            notice = non_strict_python_chess_notice()
            if notice.get("manual_review_required") and not args.input_path:
                payload = {
                    "status": "requires_review",
                    "manual_review_required": True,
                    "warnings": list(notice.get("warnings") or []),
                    "python_chess": notice.get("python_chess"),
                }
                _print_json(payload)
                return 0
        if not args.input_path or not args.out:
            _print_json(
                {
                    "status": "failed",
                    "error_code": "missing_process_input_or_output",
                    "message": "process requires input_path and --out unless strict dependency preflight already failed.",
                }
            )
            return 1

        payload = run_auto_chess_process(
            args.input_path,
            out_dir=args.out,
            mode=args.mode,
            html_path=args.html or None,
            quality_profile=args.quality_profile,
            render_pages=args.render_pages,
            with_ai=args.with_ai,
            dry_run_ai=args.dry_run_ai,
            ai_limit=args.ai_limit,
            ai_pgn_limit=args.ai_pgn_limit,
            chess_fen_recognition_max_diagrams=args.chess_fen_recognition_max_diagrams,
            diagram_page_ranges=args.diagram_page_ranges,
            glyph_mapping_file=args.glyph_mapping_file or None,
        )
        _print_json(payload)
        return 1 if payload.get("strict_failed") or payload.get("status") == "AUTO_FAILED_WITH_REASON" else 0
    if args.command == "validate":
        from chess_auto_flow import is_auto_chess_output, validate_auto_chess_output

        if args.strict:
            from chess_auto_flow import strict_python_chess_preflight

            preflight = strict_python_chess_preflight()
            if preflight.get("status") == "failed":
                payload = {"overall_status": "failed", **preflight}
                _print_json(payload)
                return 1
        if len(args.epub_paths) == 1 and is_auto_chess_output(args.epub_paths[0]):
            payload = validate_auto_chess_output(args.epub_paths[0], strict=args.strict)
            _print_json(payload)
            return 0 if payload.get("overall_status") != "failed" else 1
        from scripts.run_epub_validators import run_epub_validators

        payload = run_epub_validators(args.epub_paths, reports_dir=args.reports_dir)
        _print_json(payload)
        return 0 if payload["overall_status"] != "failed" else 1
    if args.command == "report":
        from chess_auto_flow import report_auto_chess_output

        payload = report_auto_chess_output(args.out_dir)
        _print_json(payload)
        return 0 if payload.get("status") not in {"AUTO_FAILED_WITH_REASON", "failed"} else 1
    if args.command == "review":
        from chess_auto_flow import review_auto_chess_output

        payload = review_auto_chess_output(args.out_dir)
        _print_json(payload)
        return 0 if payload.get("status") != "failed" else 1
    if args.command == "smoke":
        from scripts.run_smoke_tests import run_smoke_tests

        payload = run_smoke_tests(
            manifest_path=args.manifest,
            mode=args.mode,
            output_dir=args.output_dir,
            reports_dir=args.reports_dir,
            case_filters=args.case,
        )
        _print_json(payload)
        return 0 if payload["summary"]["overall_status"] != "failed" else 1
    if args.command == "corpus":
        from scripts.run_corpus_gate import run_corpus_gate

        payload = run_corpus_gate(
            manifest_path=args.manifest,
            output_root=args.output_root,
            reports_root=args.reports_root,
            proof_profile=args.proof_profile,
            smoke_case_filters=args.smoke_case,
            premium_case_filters=args.premium_case,
            fen_min_profile_count=args.fen_min_profile_count,
            fen_min_seed_label_count=args.fen_min_seed_label_count,
        )
        _print_json(payload)
        return 0
    if args.command == "status":
        from scripts.generate_project_status import generate_project_status

        payload = generate_project_status(
            repo_root=args.repo_root,
            reports_root=args.reports_root,
            output_json=args.output_json,
            output_md=args.output_md,
        )
        _print_json(payload)
        return 0
    if args.command == "ml":
        return _run_ml(args)
    if args.command == "test":
        return _run_tests(args.suite)
    if args.command == "audit":
        command = [
            sys.executable,
            "scripts/run_release_audit.py",
            args.epub_path,
            "--output-dir",
            args.output_dir,
            "--reports-dir",
            args.reports_dir,
        ]
        if args.language:
            command.extend(["--language", args.language])
        if args.title:
            command.extend(["--title", args.title])
        if args.author:
            command.extend(["--author", args.author])
        if args.description:
            command.extend(["--description", args.description])
        if args.publication_profile:
            command.extend(["--publication-profile", args.publication_profile])
        if args.strict_premium:
            command.append("--strict-premium")
        if args.timeout_seconds and args.timeout_seconds > 0:
            command.extend(["--timeout-seconds", str(args.timeout_seconds)])
        try:
            return subprocess.run(
                command,
                check=False,
                timeout=args.timeout_seconds if args.timeout_seconds and args.timeout_seconds > 0 else None,
            ).returncode
        except subprocess.TimeoutExpired as error:
            partial_payload = _audit_timeout_payload(
                epub_path=args.epub_path,
                strict_premium=bool(args.strict_premium),
                timeout_seconds=int(args.timeout_seconds or 0),
                command=command,
                reports_dir=args.reports_dir,
                captured_stdout=error.stdout,
                captured_stderr=error.stderr,
            )
            _print_json(partial_payload)
            return RELEASE_TIMEOUT_RETURN_CODE
    if args.command == "chess-study":
        return _run_chess_study(args)
    if args.command == "workflow":
        from workflow_runner import run_workflow_baseline, run_workflow_verify

        if args.workflow_command == "baseline":
            payload = run_workflow_baseline(
                args.input_path,
                change_area=args.change_area,
                reports_root=args.reports_root,
                output_root=args.output_root,
            )
            _print_json(payload)
            return 0
        if args.workflow_command == "verify":
            payload = run_workflow_verify(
                args.input_path,
                run_id=args.run_id,
                reports_root=args.reports_root,
                output_root=args.output_root,
            )
            _print_json(payload)
            return 0 if payload.get("status") in {"passed", "passed_with_warnings"} else 1
        workflow_parser.print_help()
        return 1
    if args.command == "orchestrate":
        from github_issue_orchestration import run_orchestration_command

        if not args.orchestrate_command:
            orchestrate_parser.print_help()
            return 1
        try:
            payload = run_orchestration_command(args)
        except Exception as error:
            payload = {"status": "failed", "error": type(error).__name__, "message": str(error)}
            _print_json(payload)
            return 1
        _print_json(payload)
        return 0 if payload.get("status") in {"passed", "passed_with_warnings", "ready"} else 1
    parser.print_help()
    return 0


def _run_chess_study(args: argparse.Namespace) -> int:
    from chess_study_export import (
        ChessStudyConfig,
        audit_current_html,
        build_chess_fen_manual_review,
        build_chess_fen_templates,
        build_chess_pgn_review,
        build_chess_quality_dashboard,
        build_ai_assisted_quality_eval,
        build_ai_fen_candidates,
        build_ai_pgn_candidates,
        build_chess_quality_baseline,
        build_fen_square_dataset,
        build_study_exercises,
        build_study_final_test,
        build_study_pgn,
        build_study_positions,
        calibrate_fen_confidence,
        detect_study_diagrams,
        evaluate_fen_ensemble,
        evaluate_chess_fen_profile,
        export_fen_corpus_manifest,
        extract_study_structure,
        extract_study_notation_fragments,
        ingest_study_pdf,
        preprocess_chess_board_crops,
        recognize_fen_local,
        render_qa_html,
        render_semantic_source_reader,
        render_study_html,
        run_chess_study_export,
        segment_study_pages,
        train_fen_square_classifier,
        validate_study_export,
    )

    if not args.chess_study_command:
        _print_json({"status": "failed", "error": "Missing chess-study subcommand."})
        return 1
    pdf = Path(args.pdf) if str(args.pdf or "").strip() else _default_chess_study_pdf()
    out = Path(args.out)
    html_path = Path(args.html) if str(args.html or "").strip() else None
    if args.chess_study_command == "run-all":
        payload = run_chess_study_export(
            pdf,
            html_path=html_path,
            out_dir=out,
            diagram_pages=args.diagram_pages,
            diagram_page_ranges=args.diagram_page_ranges,
            diagram_dpi=args.diagram_dpi,
            min_grid_confidence=args.min_grid_confidence,
            max_candidates_per_page=args.max_candidates_per_page,
            quality_profile=args.quality_profile,
            render_pages=args.render_pages,
            ocr_fallback=args.ocr_fallback,
            strict_thresholds=args.strict_thresholds,
            low_confidence_diagram_review=args.low_confidence_diagram_review,
            low_confidence_min_grid_confidence=args.low_confidence_min_grid_confidence,
            low_confidence_max_candidates_per_page=args.low_confidence_max_candidates_per_page,
            glyph_context_pages=args.glyph_context_pages,
            review_sample_limit=args.review_sample_limit,
            diagram_review_labels=args.diagram_review_labels or None,
            glyph_mapping_file=args.glyph_mapping_file or None,
            diagram_alignment_review=args.diagram_alignment_review,
        )
        _print_json(payload)
        return 0 if payload.get("status") != "FAIL" else 1

    config = ChessStudyConfig(
        pdf=pdf,
        html=html_path,
        out=out,
        diagram_pages=args.diagram_pages,
        diagram_page_ranges=args.diagram_page_ranges,
        diagram_dpi=args.diagram_dpi,
        min_grid_confidence=args.min_grid_confidence,
        max_candidates_per_page=args.max_candidates_per_page,
        quality_profile=args.quality_profile,
        render_pages=args.render_pages,
        ocr_fallback=args.ocr_fallback,
        strict_thresholds=args.strict_thresholds,
        low_confidence_diagram_review=args.low_confidence_diagram_review,
        low_confidence_min_grid_confidence=args.low_confidence_min_grid_confidence,
        low_confidence_max_candidates_per_page=args.low_confidence_max_candidates_per_page,
        glyph_context_pages=args.glyph_context_pages,
        review_sample_limit=args.review_sample_limit,
        diagram_review_labels=Path(args.diagram_review_labels) if str(args.diagram_review_labels or "").strip() else None,
        glyph_mapping_file=Path(args.glyph_mapping_file) if str(args.glyph_mapping_file or "").strip() else None,
        diagram_alignment_review=args.diagram_alignment_review,
    )
    if args.chess_study_command == "fen-review":
        payload = build_chess_fen_manual_review(
            config.out,
            html_path=config.html,
            pdf_path=config.pdf,
            review_sample_limit=config.review_sample_limit,
            page_ranges=config.diagram_page_ranges,
            min_count=args.fen_review_min_count,
        )
    elif args.chess_study_command == "build-fen-templates":
        if not str(args.labels or "").strip():
            _print_json({"status": "failed", "error": "Provide --labels for build-fen-templates."})
            return 1
        payload = build_chess_fen_templates(
            args.labels,
            out_dir=config.out,
            profile=args.profile,
            template_output_dir=args.template_output_dir or None,
        )
    elif args.chess_study_command == "evaluate-fen-profile":
        if not str(args.labels or "").strip():
            _print_json({"status": "failed", "error": "Provide --labels for evaluate-fen-profile."})
            return 1
        payload = evaluate_chess_fen_profile(
            args.labels,
            out_dir=config.out,
            profile=args.profile,
            fold_count=args.fold_count,
            holdout_fold=args.holdout_fold,
        )
    elif args.chess_study_command == "pgn-review":
        payload = build_chess_pgn_review(
            config.out,
            glyph_mapping_file=config.glyph_mapping_file,
        )
    elif args.chess_study_command == "quality-dashboard":
        payload = build_chess_quality_dashboard(config.out)
    elif args.chess_study_command == "ai-fen-candidates":
        payload = build_ai_fen_candidates(
            config.out,
            limit=args.ai_limit,
            dry_run=args.dry_run,
        )
    elif args.chess_study_command == "ai-pgn-candidates":
        payload = build_ai_pgn_candidates(
            config.out,
            glyph_mapping_file=config.glyph_mapping_file,
            limit=args.ai_pgn_limit,
            dry_run=args.dry_run,
        )
    elif args.chess_study_command == "ai-quality-eval":
        payload = build_ai_assisted_quality_eval(config.out)
    elif args.chess_study_command == "quality-baseline":
        payload = build_chess_quality_baseline(config.out)
    elif args.chess_study_command == "preprocess-boards":
        payload = preprocess_chess_board_crops(
            config.out,
            labels_path=args.labels or None,
            limit=args.review_sample_limit,
        )
    elif args.chess_study_command == "build-square-dataset":
        if not str(args.labels or "").strip():
            _print_json({"status": "failed", "error": "Provide --labels for build-square-dataset."})
            return 1
        payload = build_fen_square_dataset(
            args.labels,
            out_dir=config.out,
            fold_count=args.fold_count,
            holdout_fold=args.holdout_fold,
        )
    elif args.chess_study_command in {"train-fen-classifier", "evaluate-fen-classifier"}:
        payload = train_fen_square_classifier(
            config.out,
            dataset_path=args.labels or None,
            model_name=Path(args.model_path).stem if str(args.model_path or "").strip() else "chess_fen_square_v1",
        )
    elif args.chess_study_command == "recognize-fen-local":
        payload = recognize_fen_local(
            config.out,
            model_path=args.model_path or None,
            limit=args.review_sample_limit,
        )
    elif args.chess_study_command == "evaluate-fen-ensemble":
        payload = evaluate_fen_ensemble(config.out, min_confidence=args.min_confidence)
    elif args.chess_study_command == "calibrate-fen-confidence":
        payload = calibrate_fen_confidence(config.out)
    elif args.chess_study_command == "export-fen-corpus-manifest":
        payload = export_fen_corpus_manifest(config.out)
    elif args.chess_study_command == "audit-current":
        if not config.html:
            _print_json({"status": "failed", "error": "Provide --html for audit-current."})
            return 1
        payload = audit_current_html(config)
    elif args.chess_study_command == "extract-structure":
        payload = extract_study_structure(config.pdf, config.out, html_path=config.html)
    elif args.chess_study_command == "segment-pages":
        structure = _read_json(config.out / "chapters.json") if (config.out / "chapters.json").is_file() else extract_study_structure(config.pdf, config.out, html_path=config.html)
        payload = segment_study_pages(config.pdf, structure, config.out, html_path=config.html)
    elif args.chess_study_command == "detect-diagrams":
        payload = detect_study_diagrams(config)
    elif args.chess_study_command == "render" and (config.out / "data" / "book.json").is_file():
        payload = render_semantic_source_reader(config.out)
    elif args.chess_study_command in {"recognize-fen", "extract-pgn", "link-exercises", "validate", "render"}:
        page_model = ingest_study_pdf(config)
        structure = _read_json(config.out / "chapters.json") if (config.out / "chapters.json").is_file() else extract_study_structure(config.pdf, config.out, html_path=config.html)
        segments = _read_json(config.out / "page_segments.json") if (config.out / "page_segments.json").is_file() else segment_study_pages(config.pdf, structure, config.out, html_path=config.html)
        diagrams = _read_json(config.out / "chess_diagrams.json") if (config.out / "chess_diagrams.json").is_file() else detect_study_diagrams(config)
        positions = build_study_positions(diagrams, segments, config.out)
        notation_fragments = extract_study_notation_fragments(
            page_model,
            positions,
            config.out,
            glyph_context_pages=config.glyph_context_pages,
            glyph_mapping_file=config.glyph_mapping_file,
        )
        pgn_payload = build_study_pgn(positions, config.out, notation_fragments=notation_fragments)
        exercises = build_study_exercises(positions, config.out)
        final_test = build_study_final_test(positions, config.out)
        payload = validate_study_export(
            config,
            current_audit={"status": "not_provided", "final_html_status": "NOT_ACCEPTABLE_AS_FINAL"},
            structure=structure,
            segments=segments,
            diagrams=diagrams,
            positions=positions,
            page_model=page_model,
            notation_fragments=notation_fragments,
            pgn_payload=pgn_payload,
            exercises=exercises,
            final_test=final_test,
        )
        if args.chess_study_command == "render":
            render_study_html(
                config.out,
                structure=structure,
                positions=positions,
                qa_report=payload,
                page_model=page_model,
                notation_fragments=notation_fragments,
            )
            render_qa_html(config.out, payload)
    else:
        payload = {"status": "failed", "error": f"Unsupported chess-study command: {args.chess_study_command}"}
    _print_json(payload)
    return 0 if payload.get("status") not in {"failed", "FAIL"} else 1


def _default_chess_study_pdf() -> Path:
    candidates = [
        Path("input") / "Yusupov_Build up your Chess 1_The Fundamentals(1).pdf",
        Path.home() / "Downloads" / "Yusupov_Build up your Chess 1_The Fundamentals.pdf",
        Path.home() / "Downloads" / "Fundamenty 1-1.pdf",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_bootstrap(*, runtime_only: bool) -> int:
    commands: list[list[str]] = [
        [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
    ]
    if not runtime_only:
        commands.append([sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"])
    for command in commands:
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            return completed.returncode
    git_hooks_payload = _maybe_install_git_hooks(runtime_only=runtime_only)

    from premium_tools import detect_toolchain

    payload = detect_toolchain(refresh=True)
    payload["bootstrap_run"] = {
        "requested_profile": "runtime_only" if runtime_only else "developer",
        "installed_requirements_files": ["requirements.txt"] if runtime_only else ["requirements.txt", "requirements-dev.txt"],
        "git_hooks": git_hooks_payload,
        "notes": [
            "Use `python kindlemaster.py doctor` to re-check the local toolchain later without reinstalling packages.",
        ],
    }
    _print_json(payload)
    return 0


def _maybe_install_git_hooks(*, runtime_only: bool) -> dict[str, Any]:
    if runtime_only:
        return {"status": "skipped", "reason": "runtime_only"}
    if os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}:
        return {"status": "skipped", "reason": "ci"}
    if os.environ.get("KINDLEMASTER_SKIP_GIT_HOOKS", "").strip():
        return {"status": "skipped", "reason": "env"}

    from scripts.install_git_hooks import install_git_hooks

    return install_git_hooks(Path(__file__).resolve().parent)


def _run_ml(args: argparse.Namespace) -> int:
    if args.ml_command == "import-reference":
        from scripts.import_reference_inputs import import_reference_inputs

        payload = import_reference_inputs(
            manifest_path=args.manifest,
            labels_path=args.labels,
            input_types=tuple(args.input_type or ("pdf", "docx")),
            dry_run=args.dry_run,
        )
        _print_json(payload)
        return 0
    if args.ml_command == "sample-reference":
        from scripts.sample_reference_inputs import sample_reference_inputs

        payload = sample_reference_inputs(
            manifest_path=args.manifest,
            labels_path=args.labels,
            input_types=tuple(args.input_type or ("pdf",)),
            output_dir=args.output_dir,
            max_pages=args.max_pages,
            min_pages=args.min_pages,
            min_size_bytes=args.min_size_bytes,
            dry_run=args.dry_run,
        )
        _print_json(payload)
        return 0
    if args.ml_command == "dataset":
        from scripts.build_ml_datasets import build_ml_datasets

        payload = build_ml_datasets(
            manifest_path=args.manifest,
            labels_path=args.labels,
            reports_root=args.reports_root,
            output_dir=args.output_dir,
            feedback_log_paths=args.feedback_log,
            fail_on_collisions=args.fail_on_collisions,
            min_examples_per_class=args.min_examples_per_class,
        )
        _print_json(payload)
        if args.fail_on_collisions and payload.get("status") == "blocked_feature_collision":
            return 2
        return 0 if payload.get("status") != "failed" else 1
    if args.ml_command == "feedback":
        return _run_ml_feedback(args)
    if args.ml_command == "feedback-export":
        from ml_feedback import export_feedback_dataset

        payload = export_feedback_dataset(
            feedback_log=args.feedback_log,
            output_path=args.output,
        )
        _print_json(payload)
        return 0 if payload.get("status") == "exported" else 1
    if args.ml_command == "train":
        from scripts.train_route_classifier import train_route_classifier

        payload = train_route_classifier(
            dataset_path=args.dataset,
            model_path=args.model or None,
            report_path=args.report or None,
            min_examples_per_class=args.min_examples_per_class,
        )
        _print_json(payload)
        return 0 if payload.get("status") == "candidate_trained" else 1
    if args.ml_command == "evaluate":
        from scripts.train_route_classifier import evaluate_route_classifier

        payload = evaluate_route_classifier(
            dataset_path=args.dataset,
            model_path=args.model,
            report_path=args.report,
        )
        _print_json(payload)
        return 0 if payload.get("status") != "failed" else 1
    if args.ml_command == "promote":
        from scripts.train_route_classifier import promote_route_classifier

        payload = promote_route_classifier(
            candidate_path=args.candidate,
            model_path=args.model,
            corpus_report_path=args.corpus_report,
        )
        _print_json(payload)
        return 0 if payload.get("status") == "promoted" else 1
    _print_json({"status": "failed", "error": "Missing ml subcommand. Use dataset, feedback, feedback-export, train, evaluate, or promote."})
    return 1


def _run_ml_feedback(args: argparse.Namespace) -> int:
    from ml_feedback import append_conversion_feedback_from_report, export_feedback_datasets

    payload: dict[str, Any] = {
        "status": "completed",
        "online_learning": False,
        "actions": [],
    }
    failed = False
    if args.report_json:
        logged = append_conversion_feedback_from_report(
            report_path=args.report_json,
            log_path=args.log,
            source_path=args.source or None,
            output_path=args.output or None,
            case_id=args.case_id,
            feedback_status=args.feedback_status,
            quality_label=args.quality_label,
            quality_score=args.quality_score,
            route_label=args.route_label,
            issue_tags=args.issue_tag,
            notes=args.notes,
            reviewer=args.reviewer,
            include_in_training=args.include_in_training,
        )
        payload["actions"].append("log")
        payload["logged"] = logged
        failed = failed or logged.get("status") == "failed"
    if args.export_dir:
        exported = export_feedback_datasets(
            log_paths=[args.log],
            output_dir=args.export_dir,
        )
        payload["actions"].append("export")
        payload["exported"] = exported
        failed = failed or exported.get("status") == "failed"
    if not payload["actions"]:
        payload = {
            "status": "failed",
            "error": "Provide --report-json to log feedback, --export-dir to export feedback datasets, or both.",
            "online_learning": False,
        }
        failed = True
    _print_json(payload)
    return 1 if failed else 0


def _react_ui_build_inputs(repo_root: Path) -> list[Path]:
    inputs: list[Path] = []
    frontend_root = repo_root / "frontend"
    if frontend_root.is_dir():
        inputs.extend(path for path in frontend_root.rglob("*") if path.is_file())
    inputs.extend(
        path
        for path in [
            repo_root / "package.json",
            repo_root / "package-lock.json",
            repo_root / "vite.config.ts",
            repo_root / "vite.config.js",
        ]
        if path.is_file()
    )
    return inputs


def _react_ui_build_required(repo_root: Path) -> bool:
    react_index = repo_root / "static" / "react" / "index.html"
    if not react_index.is_file():
        return True
    try:
        build_mtime = react_index.stat().st_mtime
    except OSError:
        return True
    for path in _react_ui_build_inputs(repo_root):
        try:
            if path.stat().st_mtime > build_mtime:
                return True
        except OSError:
            continue
    return False


def _skip_react_ui_build(skip_ui_build: bool) -> bool:
    value = os.environ.get("KINDLEMASTER_SKIP_UI_BUILD", "")
    return skip_ui_build or value.strip().lower() in {"1", "true", "yes", "on"}


def _ensure_react_ui_build(*, repo_root: Path, skip_ui_build: bool) -> int:
    if _skip_react_ui_build(skip_ui_build):
        return 0
    if not _react_ui_build_required(repo_root):
        return 0
    if shutil.which("npm") is None:
        print("Skipping React UI build because npm is not available on PATH.", flush=True)
        return 0
    print("Building KindleMaster React UI (npm run build:ui)...", flush=True)
    try:
        completed = subprocess.run(["npm", "run", "build:ui"], check=False, cwd=repo_root)
    except FileNotFoundError:
        print("Skipping React UI build because npm could not be executed.", flush=True)
        return 0
    return completed.returncode


def _run_serve(*, port: int | None, debug: bool, runtime: str, skip_ui_build: bool = False) -> int:
    repo_root = Path(__file__).resolve().parent
    ui_build_returncode = _ensure_react_ui_build(repo_root=repo_root, skip_ui_build=skip_ui_build)
    if ui_build_returncode != 0:
        return ui_build_returncode

    from app import app
    from app_runtime_services import (
        build_local_app_url,
        resolve_debug_mode,
        resolve_server_host,
        resolve_server_port,
        serve_http_app,
    )

    effective_port = port if port is not None else resolve_server_port()
    effective_host = resolve_server_host()
    effective_debug = debug or resolve_debug_mode()
    display_url = os.environ.get("KINDLEMASTER_PUBLIC_BASE_URL") or build_local_app_url(effective_port)
    if runtime == "waitress":
        print(
            (
                f"Starting KindleMaster on {display_url} "
                f"(bind={effective_host}, runtime=waitress, debug={effective_debug})"
            ),
            flush=True,
        )
        return serve_http_app(app, host=effective_host, port=effective_port, debug=effective_debug, runtime=runtime)

    print(
        (
            f"Starting KindleMaster on {display_url} "
            f"(bind={effective_host}, runtime=flask, debug={effective_debug})"
        ),
        flush=True,
    )
    return serve_http_app(app, host=effective_host, port=effective_port, debug=effective_debug, runtime=runtime)


def _run_tests(suite: str) -> int:
    repo_root = Path(__file__).resolve().parent
    verification_surfaces: dict[str, Any] = {}
    if suite in {"browser", "runtime", "release"}:
        from premium_tools import detect_toolchain

        verification_surfaces = detect_toolchain().get("verification_surfaces", {})

    if suite == "browser":
        surface = verification_surfaces.get("browser", {})
        if surface.get("status") != "supported":
            _print_json(
                {
                    "suite": "browser",
                    "status": "unavailable",
                    "missing_requirements": surface.get("missing_requirements", []),
                    "notes": surface.get("notes", []),
                }
            )
            return 1
        return subprocess.run(
            [sys.executable, "-m", "unittest", *SUITE_REGISTRY["browser"]],
            check=False,
            cwd=repo_root,
        ).returncode
    if suite == "runtime":
        surface = verification_surfaces.get("runtime", {})
        if surface.get("status") != "supported":
            _print_json(
                {
                    "suite": "runtime",
                    "status": "unavailable",
                    "missing_requirements": surface.get("missing_requirements", []),
                    "notes": surface.get("notes", []),
                }
            )
            return 1
        return subprocess.run(
            [sys.executable, "-m", "unittest", *SUITE_REGISTRY["runtime"]],
            check=False,
            cwd=repo_root,
        ).returncode
    if suite == "corpus":
        from chess_auto_flow import strict_python_chess_preflight

        preflight = strict_python_chess_preflight()
        if preflight.get("status") == "failed":
            payload = {**preflight, "suite": "corpus", "status": "unavailable"}
            _print_json(payload)
            return 1
        commands: list[Sequence[str]] = [
            [sys.executable, "-m", "unittest", *SUITE_REGISTRY["corpus"]],
            [sys.executable, "kindlemaster.py", "corpus"],
        ]
        for command in commands:
            completed = subprocess.run(command, check=False, cwd=repo_root)
            if completed.returncode != 0:
                return completed.returncode
        return 0
    if suite == "quality-critical":
        return _run_quality_critical_suite(repo_root=repo_root)
    if suite == "release":
        return _run_release_suite(repo_root=repo_root, release_surface=verification_surfaces.get("release", {}))
    if suite == "full":
        command: Sequence[str] = [sys.executable, "-m", "unittest", "discover", "-p", TEST_FILE_PATTERN]
        return subprocess.run(command, check=False, cwd=repo_root).returncode
    if suite == "quick":
        command = [sys.executable, "-m", "unittest", *SUITE_REGISTRY["quick"]]
        started = time.perf_counter()
        completed = subprocess.run(command, check=False, cwd=repo_root)
        payload = _governance_artifact_payload(
            command="python kindlemaster.py test --suite quick",
            status="passed" if completed.returncode == 0 else "failed",
            returncode=completed.returncode,
            started=started,
            notes=["Quick suite completed."],
            extra={"suite": "quick"},
        )
        _write_governance_artifact(lane="quick", payload=payload, repo_root=repo_root)
        return completed.returncode
    else:
        command = [sys.executable, "-m", "unittest", *SUITE_REGISTRY["quick"]]
    return subprocess.run(command, check=False, cwd=repo_root).returncode


def _coverage_threshold_from_env(name: str, default: float) -> float:
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


def _quality_critical_coverage_payload(
    coverage_json: dict[str, Any],
    *,
    total_threshold: float,
    converter_threshold: float,
    text_normalization_threshold: float,
    semantic_cleanup_threshold: float,
) -> dict[str, Any]:
    totals = coverage_json.get("totals", {}) if isinstance(coverage_json, dict) else {}
    files = coverage_json.get("files", {}) if isinstance(coverage_json, dict) else {}
    total_coverage = round(float(totals.get("percent_covered", 0.0) or 0.0), 2)

    file_thresholds = {
        "converter.py": converter_threshold,
        "text_normalization.py": text_normalization_threshold,
        "kindle_semantic_cleanup.py": semantic_cleanup_threshold,
    }
    file_payload: dict[str, dict[str, float]] = {}
    missing_actions: list[str] = []
    if total_coverage < total_threshold:
        missing_actions.append(f"total coverage {total_coverage}% is below {total_threshold}%")
    for file_name, threshold in file_thresholds.items():
        file_summary = files.get(file_name, {}).get("summary", {}) if isinstance(files, dict) else {}
        coverage = round(float(file_summary.get("percent_covered", 0.0) or 0.0), 2)
        file_payload[file_name] = {"coverage": coverage, "threshold": threshold}
        if coverage < threshold:
            missing_actions.append(f"{file_name} coverage {coverage}% is below {threshold}%")

    return {
        "suite": "quality-critical",
        "status": "passed" if not missing_actions else "failed",
        "thresholds": {
            "total": total_threshold,
            "converter.py": converter_threshold,
            "text_normalization.py": text_normalization_threshold,
            "kindle_semantic_cleanup.py": semantic_cleanup_threshold,
        },
        "total_coverage": total_coverage,
        "files": file_payload,
        "missing_actions": missing_actions,
    }


def _run_quality_critical_suite(*, repo_root: Path) -> int:
    started = time.perf_counter()
    coverage_dir = repo_root / "reports" / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    coverage_json_path = coverage_dir / "quality-critical.json"
    total_threshold = _coverage_threshold_from_env(
        "CORE_CONVERSION_COVERAGE_FAIL_UNDER",
        QUALITY_CRITICAL_TOTAL_COVERAGE_DEFAULT,
    )
    converter_threshold = _coverage_threshold_from_env(
        "CONVERTER_COVERAGE_FAIL_UNDER",
        QUALITY_CRITICAL_CONVERTER_COVERAGE_DEFAULT,
    )
    text_normalization_threshold = _coverage_threshold_from_env(
        "TEXT_NORMALIZATION_COVERAGE_FAIL_UNDER",
        QUALITY_CRITICAL_TEXT_NORMALIZATION_COVERAGE_DEFAULT,
    )
    semantic_threshold = _coverage_threshold_from_env(
        "SEMANTIC_CLEANUP_COVERAGE_FAIL_UNDER",
        QUALITY_CRITICAL_SEMANTIC_CLEANUP_COVERAGE_DEFAULT,
    )
    commands: list[tuple[str, Sequence[str]]] = [
        ("coverage-erase", [sys.executable, "-m", "coverage", "erase"]),
        (
            "quality-critical-tests",
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--source={','.join(QUALITY_CRITICAL_COVERAGE_SOURCES)}",
                "-m",
                "unittest",
                *QUALITY_CRITICAL_TESTS,
            ],
        ),
        ("coverage-report", [sys.executable, "-m", "coverage", "report"]),
        ("coverage-json", [sys.executable, "-m", "coverage", "json", "-o", str(coverage_json_path)]),
    ]
    step_results: list[dict[str, Any]] = []
    for label, command in commands:
        completed = subprocess.run(command, check=False, cwd=repo_root)
        step_results.append({"label": label, "command": list(command), "returncode": completed.returncode})
        if completed.returncode != 0:
            payload = _governance_artifact_payload(
                command="python kindlemaster.py test --suite quality-critical",
                status="failed",
                returncode=completed.returncode,
                started=started,
                notes=[f"Quality-critical failed during `{label}`."],
                extra={"suite": "quality-critical", "failed_step": label, "steps": step_results},
            )
            _write_governance_artifact(lane="quality-critical", payload=payload, repo_root=repo_root)
            _print_json(payload)
            return completed.returncode

    try:
        coverage_json = json.loads(coverage_json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        coverage_payload = {
            "suite": "quality-critical",
            "status": "failed",
            "missing_actions": [f"Could not read coverage JSON: {exc}"],
        }
    else:
        coverage_payload = _quality_critical_coverage_payload(
            coverage_json,
            total_threshold=total_threshold,
            converter_threshold=converter_threshold,
            text_normalization_threshold=text_normalization_threshold,
            semantic_cleanup_threshold=semantic_threshold,
        )
    status = str(coverage_payload.get("status", "failed"))
    returncode = 0 if status == "passed" else 1
    payload = _governance_artifact_payload(
        command="python kindlemaster.py test --suite quality-critical",
        status=status,
        returncode=returncode,
        started=started,
        notes=[
            "Quality-critical suite protects core conversion coverage without slowing the quick lane.",
        ],
        extra={**coverage_payload, "steps": step_results, "coverage_json": str(coverage_json_path)},
    )
    _write_governance_artifact(lane="quality-critical", payload=payload, repo_root=repo_root)
    _print_json(payload)
    return returncode


def _run_release_suite(*, repo_root: Path, release_surface: dict[str, Any]) -> int:
    started = time.perf_counter()
    release_notes = _release_suite_notes(release_surface)
    proof_profile = _release_proof_profile()
    from chess_auto_flow import strict_python_chess_preflight

    preflight = strict_python_chess_preflight()
    if preflight.get("status") == "failed":
        payload = {**preflight, "suite": "release", "status": "unavailable"}
        _write_governance_artifact(
            lane="release",
            payload=_governance_artifact_payload(
                command="python kindlemaster.py test --suite release",
                status="failed",
                returncode=1,
                started=started,
                notes=list(preflight.get("notes") or []),
                extra=payload,
            ),
            repo_root=repo_root,
        )
        _print_json(payload)
        return 1
    if proof_profile != "standard":
        release_notes.append(f"Release corpus gate is using `{proof_profile}` proof profile.")
    if release_surface.get("status") == "unsupported":
        payload = {
            "suite": "release",
            "status": "unavailable",
            "missing_requirements": release_surface.get("missing_requirements", []),
            "notes": release_notes,
        }
        _write_governance_artifact(
            lane="release",
            payload=_governance_artifact_payload(
                command="python kindlemaster.py test --suite release",
                status="failed",
                returncode=1,
                started=started,
                notes=release_notes,
                extra=payload,
            ),
            repo_root=repo_root,
        )
        _print_json(payload)
        return 1

    commands: list[tuple[str, Sequence[str]]] = [
        ("release-units", [sys.executable, "-m", "unittest", *SUITE_REGISTRY["release"]]),
        ("corpus-units", [sys.executable, "-m", "unittest", *SUITE_REGISTRY["corpus"]]),
        (
            f"corpus-gate-{proof_profile}",
            [sys.executable, "kindlemaster.py", "corpus", "--proof-profile", proof_profile],
        ),
    ]
    optional_followups = release_surface.get("optional_followups", [])
    for followup in optional_followups:
        surface_name = followup.get("surface")
        status = followup.get("status")
        if surface_name == "browser" and status == "supported":
            commands.append(("browser-followup", [sys.executable, "-m", "unittest", *SUITE_REGISTRY["browser"]]))
        if surface_name == "runtime" and status == "supported":
            commands.append(("runtime-followup", [sys.executable, "-m", "unittest", *SUITE_REGISTRY["runtime"]]))

    skipped_followups = [
        {
            "surface": followup.get("surface"),
            "missing_requirements": followup.get("missing_requirements", []),
        }
        for followup in optional_followups
        if followup.get("status") != "supported"
    ]
    step_results: list[dict[str, Any]] = []
    for label, command in commands:
        result = _run_bounded_command(
            command,
            cwd=repo_root,
            label=label,
            timeout_seconds=RELEASE_STEP_TIMEOUTS_SECONDS.get(label, 300),
        )
        step_results.append(result)
        if result["returncode"] != 0:
            payload = {
                "suite": "release",
                "status": "failed",
                "failed_step": label,
                "steps": step_results,
                "notes": release_notes,
                "skipped_optional_surfaces": skipped_followups,
            }
            _write_governance_artifact(
                lane="release",
                payload=_governance_artifact_payload(
                    command="python kindlemaster.py test --suite release",
                    status="failed",
                    returncode=1,
                    started=started,
                    notes=release_notes,
                    extra=payload,
                ),
                repo_root=repo_root,
            )
            _print_json(payload)
            return 1

    corpus_summary = _load_corpus_gate_summary(repo_root / "reports" / "corpus" / "corpus_gate.json")
    warning_reasons: list[str] = []
    if corpus_summary.get("overall_status") == "passed_with_warnings":
        warning_reasons.append("corpus_gate_passed_with_warnings")
    if skipped_followups:
        warning_reasons.append("optional_followups_skipped")
    status = "passed_with_warnings" if warning_reasons else "passed"
    payload = {
        "suite": "release",
        "status": status,
        "warning_reasons": warning_reasons,
        "corpus_gate": corpus_summary,
        "steps": step_results,
        "notes": release_notes,
        "skipped_optional_surfaces": skipped_followups,
    }
    _write_governance_artifact(
        lane="release",
        payload=_governance_artifact_payload(
            command="python kindlemaster.py test --suite release",
            status=status,
            returncode=0,
            started=started,
            notes=release_notes,
            extra=payload,
        ),
        repo_root=repo_root,
    )
    _print_json(payload)
    return 0


def _release_proof_profile() -> str:
    requested = os.environ.get("KINDLEMASTER_RELEASE_PROOF_PROFILE", "standard").strip().lower()
    if requested in RELEASE_PROOF_PROFILES:
        return requested
    return "standard"


def _release_suite_notes(release_surface: dict[str, Any]) -> list[str]:
    notes = [
        "Runs bounded release-specific unit shards plus the configured corpus gate.",
        "Does not duplicate the quick suite; run `python kindlemaster.py test --suite quick` before clean release claims.",
    ]
    if release_surface.get("optional_followups"):
        notes.append("Browser and runtime follow-up suites run only when their local toolchains are supported.")
    return notes


def _run_bounded_command(
    command: Sequence[str],
    *,
    cwd: Path,
    label: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    process = _start_process(command, cwd=cwd)
    try:
        returncode = process.wait(timeout=timeout_seconds)
        status = "passed" if returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        returncode = RELEASE_TIMEOUT_RETURN_CODE
        status = "timed_out"
    return {
        "label": label,
        "command": list(command),
        "status": status,
        "returncode": returncode,
        "timeout_seconds": timeout_seconds,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


def _start_process(command: Sequence[str], *, cwd: Path) -> subprocess.Popen[Any]:
    kwargs: dict[str, Any] = {"cwd": cwd}
    if os.name == "nt":
        creation_flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creation_flag:
            kwargs["creationflags"] = creation_flag
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        return

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


def _load_corpus_gate_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "available": False,
            "overall_status": "unknown",
            "path": str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "overall_status": "unknown",
            "path": str(path),
            "error": str(exc),
        }
    premium = payload.get("premium_corpus") or {}
    premium_overall = premium.get("overall") or {}
    smoke = payload.get("smoke") or {}
    return {
        "available": True,
        "path": str(path),
        "overall_status": payload.get("overall_status", "unknown"),
        "proof_profile": payload.get("proof_profile", "unknown"),
        "smoke_status": (smoke.get("summary") or {}).get("overall_status", "unknown"),
        "premium_status": premium.get("overall_status") or premium_overall.get("overall_status", "unknown"),
        "grade_counts": premium_overall.get("grade_counts", {}),
        "blocker_counts": premium_overall.get("blocker_counts", {}),
        "warning_counts": premium_overall.get("warning_counts", {}),
        "benchmark": payload.get("benchmark", {}),
    }


def _governance_artifact_payload(
    *,
    command: str,
    status: str,
    returncode: int,
    started: float,
    notes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "status": status,
        "returncode": returncode,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "notes": notes or [],
        **(extra or {}),
    }


def _write_governance_artifact(
    *,
    lane: str,
    payload: dict[str, Any],
    repo_root: Path | None = None,
) -> Path:
    resolved_root = repo_root or Path(__file__).resolve().parent
    report_path = resolved_root / "reports" / "governance" / f"{lane}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_json_text(payload), encoding="utf-8")
    return report_path


def _derive_doctor_artifact_status(payload: dict[str, Any]) -> str:
    status_priority = {
        "supported": 0,
        "passed": 0,
        "degraded": 1,
        "passed_with_warnings": 1,
        "unsupported": 2,
        "failed": 2,
        "unavailable": 1,
    }
    statuses: list[str] = []
    surfaces = payload.get("verification_surfaces")
    if isinstance(surfaces, dict):
        for surface_name in ("quick", "corpus", "release"):
            surface = surfaces.get(surface_name)
            if isinstance(surface, dict):
                statuses.append(str(surface.get("status", "unavailable")))
    agent_readiness = payload.get("agent_readiness")
    if isinstance(agent_readiness, dict):
        statuses.append(str(agent_readiness.get("status", "unavailable")))

    if not statuses:
        return "passed"
    worst = max(statuses, key=lambda item: status_priority.get(item, 1))
    if status_priority.get(worst, 1) >= 2:
        return "failed"
    if status_priority.get(worst, 1) == 1:
        return "passed_with_warnings"
    return "passed"


def _audit_timeout_payload(
    *,
    epub_path: str,
    strict_premium: bool,
    timeout_seconds: int,
    command: Sequence[str],
    reports_dir: str,
    captured_stdout: bytes | str | None,
    captured_stderr: bytes | str | None,
) -> dict[str, Any]:
    stage_evidence = _read_audit_stage_evidence(Path(reports_dir))
    completed_stages = list(stage_evidence.get("completed_stages") or ["audit_subprocess_started"])
    failed_stage = str(stage_evidence.get("current_stage") or "release_audit")
    return {
        "status": "incomplete",
        "decision": "pass_with_review",
        "release_verdict": "ready_with_review",
        "epub_path": epub_path,
        "strict_premium": strict_premium,
        "timeout_seconds": timeout_seconds,
        "completed_stages": completed_stages,
        "failed_stage": failed_stage,
        "stage_evidence": stage_evidence,
        "issues": [
            {
                "severity": "review",
                "code": "strict_audit_stage_timeout",
                "message": f"Strict release audit exceeded {timeout_seconds} seconds and returned partial evidence.",
                "suggested_action": "Use direct strict scoring or rerun audit with a larger timeout after performance tuning.",
            }
        ],
        "command": list(command),
        "captured_stdout_tail": _decode_tail(captured_stdout),
        "captured_stderr_tail": _decode_tail(captured_stderr),
    }


def _read_audit_stage_evidence(reports_dir: Path) -> dict[str, Any]:
    stage_path = reports_dir / "release_audit_stage_report.json"
    try:
        payload = json.loads(stage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _decode_tail(value: bytes | str | None, *, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text[-limit:]


def _json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json_text(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, indent=2)


def _print_json(value: Any) -> None:
    rendered = _json_text(value)
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        stream.write((rendered + "\n").encode("utf-8", errors="replace"))
        stream.flush()
        return
    print(rendered)


def _run_convert(
    *,
    input_path: str,
    output_path: str,
    language: str,
    profile: str,
    heading_repair: bool,
    route_model_mode: str = "shadow",
    quality_gate_mode: str = "draft",
    domain_dictionary: str = "",
    report_json: str = "",
) -> int:
    from app_runtime_services import ConversionRequest, run_document_conversion
    from converter import convert_document_to_epub_with_report
    from epub_heading_repair import repair_epub_headings_and_toc

    resolved_input = Path(input_path).resolve()
    if not resolved_input.exists():
        _print_json({"error": f"Input not found: {resolved_input}"})
        return 1

    resolved_output = Path(output_path).resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    source_suffix = resolved_input.suffix.lower()
    source_type = source_suffix.lstrip(".") if source_suffix in {".pdf", ".docx"} else None
    outcome = run_document_conversion(
        ConversionRequest(
            source_path=str(resolved_input),
            source_type=source_type,
            original_filename=resolved_input.name,
            profile=profile,
            route_model_mode=route_model_mode,
            quality_gate_mode=quality_gate_mode,
            language=language,
            heading_repair_enabled=heading_repair,
            text_cleanup_domain_dictionary_path=domain_dictionary or None,
        ),
        convert_impl=convert_document_to_epub_with_report,
        heading_repair_impl=repair_epub_headings_and_toc,
    )

    resolved_output.write_bytes(outcome.epub_bytes)

    payload = {
        **outcome.result,
        "output_path": str(resolved_output),
        "heading_repair": outcome.heading_repair_report,
    }
    payload["epub_bytes"] = f"<{len(outcome.epub_bytes)} bytes>"
    payload = _json_safe(payload)
    if report_json:
        report_path = Path(report_json).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_json_text(payload), encoding="utf-8")
    _print_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
