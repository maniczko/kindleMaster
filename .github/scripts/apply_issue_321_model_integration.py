from __future__ import annotations

from pathlib import Path


path = Path("chess_exercise_model.py")
text = path.read_text(encoding="utf-8")

replacements = [
    (
        "from chess_exercise_reconciliation import reconcile_exercise_solution_pairs\n",
        "from chess_exercise_reconciliation import reconcile_exercise_solution_pairs\n"
        "from chess_solution_integrity import SolutionIntegrityReport, analyze_solution_integrity\n",
    ),
    (
        "    solution_match: Mapping[str, Any] | None = None\n"
        "    warnings: tuple[ValidationWarning, ...] = ()\n",
        "    solution_match: Mapping[str, Any] | None = None\n"
        "    solution_integrity: Mapping[str, Any] | None = None\n"
        "    warnings: tuple[ValidationWarning, ...] = ()\n",
    ),
    (
        '            "solution_match": dict(self.solution_match) if self.solution_match else None,\n'
        '            "validation": {\n',
        '            "solution_match": dict(self.solution_match) if self.solution_match else None,\n'
        '            "solution_integrity": dict(self.solution_integrity) if self.solution_integrity else None,\n'
        '            "validation": {\n',
    ),
    (
        "            solution_match=dict(value.get(\"solution_match\")) if isinstance(value.get(\"solution_match\"), Mapping) else None,\n"
        "            warnings=tuple(ValidationWarning.from_dict(item) for item in validation.get(\"warnings\") or []),\n",
        "            solution_match=dict(value.get(\"solution_match\")) if isinstance(value.get(\"solution_match\"), Mapping) else None,\n"
        "            solution_integrity=(\n"
        "                dict(value.get(\"solution_integrity\"))\n"
        "                if isinstance(value.get(\"solution_integrity\"), Mapping)\n"
        "                else None\n"
        "            ),\n"
        "            warnings=tuple(ValidationWarning.from_dict(item) for item in validation.get(\"warnings\") or []),\n",
    ),
    (
        "    reconciliation: Mapping[str, Any] | None = None\n"
        "    schema: str = field(default=CHESS_EXERCISE_MODEL_SCHEMA, init=False)\n",
        "    reconciliation: Mapping[str, Any] | None = None\n"
        "    integrity: Mapping[str, Any] | None = None\n"
        "    schema: str = field(default=CHESS_EXERCISE_MODEL_SCHEMA, init=False)\n",
    ),
    (
        '            "solution_reconciliation": dict(self.reconciliation) if self.reconciliation else None,\n'
        '            "exercises": [exercise.to_dict() for exercise in self.exercises],\n',
        '            "solution_reconciliation": dict(self.reconciliation) if self.reconciliation else None,\n'
        '            "solution_integrity": dict(self.integrity) if self.integrity else None,\n'
        '            "exercises": [exercise.to_dict() for exercise in self.exercises],\n',
    ),
    (
        "            reconciliation=(\n"
        "                dict(value.get(\"solution_reconciliation\"))\n"
        "                if isinstance(value.get(\"solution_reconciliation\"), Mapping)\n"
        "                else None\n"
        "            ),\n"
        "        )\n",
        "            reconciliation=(\n"
        "                dict(value.get(\"solution_reconciliation\"))\n"
        "                if isinstance(value.get(\"solution_reconciliation\"), Mapping)\n"
        "                else None\n"
        "            ),\n"
        "            integrity=(\n"
        "                dict(value.get(\"solution_integrity\"))\n"
        "                if isinstance(value.get(\"solution_integrity\"), Mapping)\n"
        "                else None\n"
        "            ),\n"
        "        )\n",
    ),
    (
        "    exercises: list[ChessExercise] = []\n"
        "    for exercise_id in order:\n",
        "    exercises: list[ChessExercise] = []\n"
        "    integrity_records = []\n"
        "    for exercise_id in order:\n",
    ),
    (
        "        confidence_values = [value for value in ((diagram_evidence.fen_confidence if diagram_evidence else None),) if value is not None]\n",
        "        exercise_number = (\n"
        "            exercise.get(\"printed_number\")\n"
        "            or exercise.get(\"exercise_number\")\n"
        "            or (diagram or {}).get(\"printed_number\")\n"
        "            or (diagram or {}).get(\"exercise_number\")\n"
        "        )\n"
        "        integrity_record = analyze_solution_integrity(\n"
        "            exercise_id=exercise_id,\n"
        "            exercise_number=exercise_number,\n"
        "            source_page=source_page,\n"
        "            solution_page=(solution_evidence.source_page_number if solution_evidence else source_page),\n"
        "            text=(\n"
        "                solution_evidence.normalized_notation or solution_evidence.raw_text\n"
        "                if solution_evidence\n"
        "                else \"\"\n"
        "            ),\n"
        "            expected_side_to_move=(diagram_evidence.side_to_move if diagram_evidence else \"unknown\"),\n"
        "            expected_first_move_number=(\n"
        "                (solution or {}).get(\"expected_first_move_number\")\n"
        "                or (solution or {}).get(\"first_move_number\")\n"
        "                or (diagram or {}).get(\"expected_first_move_number\")\n"
        "                or (diagram or {}).get(\"first_move_number\")\n"
        "            ),\n"
        "        )\n"
        "        integrity_records.append(integrity_record)\n"
        "        for finding in integrity_record.findings:\n"
        "            warnings.append(\n"
        "                ValidationWarning(\n"
        "                    finding.code,\n"
        "                    finding.message,\n"
        "                    severity=finding.severity,\n"
        "                )\n"
        "            )\n"
        "\n"
        "        confidence_values = [value for value in ((diagram_evidence.fen_confidence if diagram_evidence else None),) if value is not None]\n",
    ),
    (
        "                solution_match=decision.to_dict() if decision else None,\n"
        "                warnings=tuple(warnings),\n",
        "                solution_match=decision.to_dict() if decision else None,\n"
        "                solution_integrity=integrity_record.to_dict(),\n"
        "                warnings=tuple(warnings),\n",
    ),
    (
        "        reconciliation=reconciliation_report.to_dict(),\n"
        "    )\n",
        "        reconciliation=reconciliation_report.to_dict(),\n"
        "        integrity=SolutionIntegrityReport(records=tuple(integrity_records)).to_dict(),\n"
        "    )\n",
    ),
    (
        "    solution = exercise.get(\"solution\") if isinstance(exercise.get(\"solution\"), Mapping) else {}\n"
        "    exercise_id = str(exercise.get(\"exercise_id\") or \"\")\n",
        "    solution = exercise.get(\"solution\") if isinstance(exercise.get(\"solution\"), Mapping) else {}\n"
        "    integrity = exercise.get(\"solution_integrity\") if isinstance(exercise.get(\"solution_integrity\"), Mapping) else {}\n"
        "    exercise_id = str(exercise.get(\"exercise_id\") or \"\")\n",
    ),
    (
        '        "solution_page": int(solution.get("source_page_number") or 0),\n'
        "    }\n",
        '        "solution_page": int(solution.get("source_page_number") or 0),\n'
        '        "solution_integrity_status": str(integrity.get("status") or "unknown"),\n'
        '        "solution_integrity_findings": [\n'
        '            str(item.get("code") or "")\n'
        '            for item in integrity.get("findings") or []\n'
        '            if isinstance(item, Mapping) and item.get("code")\n'
        '        ],\n'
        "    }\n",
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one model integration anchor, found {count}: {old[:100]!r}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
