from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


study_path = Path("chess_study_export.py")
study = study_path.read_text(encoding="utf-8")
study = replace_once(
    study,
    "from chess_exercise_model import build_chess_exercise_model, exercise_to_reader_item\n",
    "from chess_exercise_model import build_chess_exercise_model, exercise_to_reader_item\n"
    "from chess_semantic_release_gate import run_output_semantic_release_gate\n",
    "study import",
)
study = replace_once(
    study,
    "            source_gate=source_gate,\n        )\n    return qa_report\n",
    "            source_gate=source_gate,\n"
    "            integrity_mode=(\"strict\" if config.strict_thresholds else None),\n"
    "        )\n"
    "    return qa_report\n",
    "run-all rebuild call",
)
study = replace_once(
    study,
    "    source_gate: dict[str, Any] | None = None,\n) -> dict[str, Any]:\n",
    "    source_gate: dict[str, Any] | None = None,\n"
    "    integrity_mode: str | None = None,\n"
    ") -> dict[str, Any]:\n",
    "rebuild signature",
)
study = replace_once(
    study,
    "    _write_chess_reader_semantic_book_reports(out, semantic_book)\n"
    "    _write_artifact_manifest(data_dir / \"artifact_manifest.json\", artifact_manifest)\n"
    "    (out / \"styles.css\").write_text(_semantic_source_styles_css(), encoding=\"utf-8\")\n"
    "    (out / \"app.js\").write_text(_semantic_source_app_js(), encoding=\"utf-8\")\n"
    "    (out / \"index.html\").write_text(_semantic_source_index_html(book_payload), encoding=\"utf-8\")\n",
    "    _write_chess_reader_semantic_book_reports(out, semantic_book)\n"
    "    _write_artifact_manifest(data_dir / \"artifact_manifest.json\", artifact_manifest)\n"
    "    reader_html = _semantic_source_index_html(book_payload)\n"
    "    integrity_report = run_output_semantic_release_gate(\n"
    "        semantic_book,\n"
    "        out_dir=out,\n"
    "        mode=integrity_mode,\n"
    "        book_payload=book_payload,\n"
    "        documents={\"index.html\": reader_html},\n"
    "    )\n"
    "    book_payload[\"semantic_release_gate\"] = integrity_report.to_dict()\n"
    "    if integrity_report.exit_code:\n"
    "        return {\n"
    "            **book_payload,\n"
    "            \"status\": \"failed\",\n"
    "            \"final_reader_missing\": True,\n"
    "            \"blocked_before_write\": True,\n"
    "        }\n"
    "    (out / \"styles.css\").write_text(_semantic_source_styles_css(), encoding=\"utf-8\")\n"
    "    (out / \"app.js\").write_text(_semantic_source_app_js(), encoding=\"utf-8\")\n"
    "    (out / \"index.html\").write_text(reader_html, encoding=\"utf-8\")\n",
    "rebuild pre-write gate",
)
study = replace_once(
    study,
    "def render_semantic_source_reader(out_dir: str | Path) -> dict[str, Any]:\n",
    "def render_semantic_source_reader(\n"
    "    out_dir: str | Path,\n"
    "    *,\n"
    "    integrity_mode: str | None = None,\n"
    ") -> dict[str, Any]:\n",
    "render signature",
)
study = replace_once(
    study,
    "    _write_chess_reader_semantic_book_reports(out, semantic_book)\n"
    "    (out / \"styles.css\").write_text(_semantic_source_styles_css(), encoding=\"utf-8\")\n"
    "    (out / \"app.js\").write_text(_semantic_source_app_js(), encoding=\"utf-8\")\n"
    "    (out / \"index.html\").write_text(_semantic_source_index_html(book), encoding=\"utf-8\")\n"
    "    page_count = len([page for page in book.get(\"pages\") or [] if _semantic_source_page_elements(page)])\n",
    "    _write_chess_reader_semantic_book_reports(out, semantic_book)\n"
    "    reader_html = _semantic_source_index_html(book)\n"
    "    integrity_report = run_output_semantic_release_gate(\n"
    "        semantic_book,\n"
    "        out_dir=out,\n"
    "        mode=integrity_mode,\n"
    "        book_payload=book,\n"
    "        documents={\"index.html\": reader_html},\n"
    "    )\n"
    "    if integrity_report.exit_code:\n"
    "        return {\n"
    "            \"status\": \"failed\",\n"
    "            \"schema\": \"kindlemaster.semantic_source_reader.v1\",\n"
    "            \"out_dir\": str(out),\n"
    "            \"blocked_before_write\": True,\n"
    "            \"semantic_release_gate\": integrity_report.to_dict(),\n"
    "        }\n"
    "    (out / \"styles.css\").write_text(_semantic_source_styles_css(), encoding=\"utf-8\")\n"
    "    (out / \"app.js\").write_text(_semantic_source_app_js(), encoding=\"utf-8\")\n"
    "    (out / \"index.html\").write_text(reader_html, encoding=\"utf-8\")\n"
    "    page_count = len([page for page in book.get(\"pages\") or [] if _semantic_source_page_elements(page)])\n",
    "render pre-write gate",
)
study_path.write_text(study, encoding="utf-8")

entry_path = Path("kindlemaster.py")
entry = entry_path.read_text(encoding="utf-8")
entry = replace_once(
    entry,
    "    \"test_epub_release_pipeline.py\",\n",
    "    \"test_epub_release_pipeline.py\",\n"
    "    \"tests.chess.test_semantic_release_gate\",\n"
    "    \"tests.chess.test_semantic_release_gate_hook\",\n",
    "release suite registration",
)
entry = replace_once(
    entry,
    "    \"test_release_quality_recovery.py\",\n]\n\nQUALITY_CRITICAL_COVERAGE_SOURCES",
    "    \"test_release_quality_recovery.py\",\n"
    "    \"tests.chess.test_semantic_release_gate\",\n"
    "    \"tests.chess.test_semantic_release_gate_hook\",\n"
    "]\n\nQUALITY_CRITICAL_COVERAGE_SOURCES",
    "quality-critical suite registration",
)
entry = replace_once(
    entry,
    "    validate_parser.add_argument(\"--strict\", action=\"store_true\", help=\"For auto chess output directories, fail on unresolved FEN/PGN review items.\")\n\n"
    "    report_parser = subparsers.add_parser(\"report\", help=\"Build or print an auto chess flow report.\")\n",
    "    validate_parser.add_argument(\"--strict\", action=\"store_true\", help=\"For auto chess output directories, fail on unresolved FEN/PGN review items.\")\n\n"
    "    chess_release_gate_parser = subparsers.add_parser(\n"
    "        \"chess-release-gate\",\n"
    "        help=\"Validate semantic chess integrity before publication.\",\n"
    "    )\n"
    "    chess_release_gate_parser.add_argument(\"semantic_json\")\n"
    "    chess_release_gate_parser.add_argument(\"--mode\", choices=(\"development\", \"strict\", \"release\"), default=\"development\")\n"
    "    chess_release_gate_parser.add_argument(\"--reports-dir\", default=\"reports/chess_reader\")\n"
    "    chess_release_gate_parser.add_argument(\"--expected-counts-json\", default=\"\")\n"
    "    chess_release_gate_parser.add_argument(\"--metadata-json\", default=\"\")\n"
    "    chess_release_gate_parser.add_argument(\"--toc-report-json\", default=\"\")\n"
    "    chess_release_gate_parser.add_argument(\"--fen-release-report-json\", default=\"\")\n"
    "    chess_release_gate_parser.add_argument(\"--documents-root\", default=\"\")\n"
    "    chess_release_gate_parser.add_argument(\"--allow-warning\", action=\"append\", default=[])\n\n"
    "    report_parser = subparsers.add_parser(\"report\", help=\"Build or print an auto chess flow report.\")\n",
    "CLI parser",
)
entry = replace_once(
    entry,
    "    if args.command == \"report\":\n"
    "        from chess_auto_flow import report_auto_chess_output\n",
    "    if args.command == \"chess-release-gate\":\n"
    "        from chess_semantic_release_gate import DEFAULT_ALLOWED_WARNINGS, run_gate_from_files\n\n"
    "        allowlist = set(DEFAULT_ALLOWED_WARNINGS)\n"
    "        allowlist.update(args.allow_warning)\n"
    "        payload = run_gate_from_files(\n"
    "            args.semantic_json,\n"
    "            mode=args.mode,\n"
    "            reports_dir=args.reports_dir,\n"
    "            expected_counts_json=args.expected_counts_json or None,\n"
    "            metadata_json=args.metadata_json or None,\n"
    "            toc_report_json=args.toc_report_json or None,\n"
    "            fen_release_report_json=args.fen_release_report_json or None,\n"
    "            documents_root=args.documents_root or None,\n"
    "            allowed_warnings=allowlist,\n"
    "        )\n"
    "        _print_json(payload.to_dict())\n"
    "        return payload.exit_code\n"
    "    if args.command == \"report\":\n"
    "        from chess_auto_flow import report_auto_chess_output\n",
    "CLI dispatch",
)
entry_path.write_text(entry, encoding="utf-8")
