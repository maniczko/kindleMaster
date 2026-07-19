from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


model_path = Path("chess_exercise_model.py")
model = model_path.read_text(encoding="utf-8")
model = replace_once(
    model,
    "from dataclasses import dataclass, field\n",
    "from dataclasses import dataclass, field, replace\n",
    label="model dataclasses import",
)
model = replace_once(
    model,
    "from chess_exercise_reconciliation import reconcile_exercise_solution_pairs\n"
    "from chess_solution_integrity import SolutionIntegrityReport, analyze_solution_integrity\n",
    "from chess_exercise_navigation import NavigationReport, build_navigation_report\n"
    "from chess_exercise_reconciliation import reconcile_exercise_solution_pairs\n"
    "from chess_solution_integrity import SolutionIntegrityReport, analyze_solution_integrity\n",
    label="model navigation import",
)
model = replace_once(
    model,
    "    solution_match: Mapping[str, Any] | None = None\n"
    "    solution_integrity: Mapping[str, Any] | None = None\n"
    "    warnings: tuple[ValidationWarning, ...] = ()\n",
    "    solution_match: Mapping[str, Any] | None = None\n"
    "    solution_integrity: Mapping[str, Any] | None = None\n"
    "    navigation: Mapping[str, Any] | None = None\n"
    "    warnings: tuple[ValidationWarning, ...] = ()\n",
    label="exercise navigation field",
)
model = replace_once(
    model,
    '            "solution_integrity": dict(self.solution_integrity) if self.solution_integrity else None,\n'
    '            "validation": {\n',
    '            "solution_integrity": dict(self.solution_integrity) if self.solution_integrity else None,\n'
    '            "navigation": dict(self.navigation) if self.navigation else None,\n'
    '            "validation": {\n',
    label="exercise navigation serialization",
)
model = replace_once(
    model,
    "            solution_integrity=(\n"
    "                dict(value.get(\"solution_integrity\"))\n"
    "                if isinstance(value.get(\"solution_integrity\"), Mapping)\n"
    "                else None\n"
    "            ),\n"
    "            warnings=tuple(ValidationWarning.from_dict(item) for item in validation.get(\"warnings\") or []),\n",
    "            solution_integrity=(\n"
    "                dict(value.get(\"solution_integrity\"))\n"
    "                if isinstance(value.get(\"solution_integrity\"), Mapping)\n"
    "                else None\n"
    "            ),\n"
    "            navigation=(\n"
    "                dict(value.get(\"navigation\"))\n"
    "                if isinstance(value.get(\"navigation\"), Mapping)\n"
    "                else None\n"
    "            ),\n"
    "            warnings=tuple(ValidationWarning.from_dict(item) for item in validation.get(\"warnings\") or []),\n",
    label="exercise navigation parsing",
)
model = replace_once(
    model,
    "    reconciliation: Mapping[str, Any] | None = None\n"
    "    integrity: Mapping[str, Any] | None = None\n"
    "    schema: str = field(default=CHESS_EXERCISE_MODEL_SCHEMA, init=False)\n",
    "    reconciliation: Mapping[str, Any] | None = None\n"
    "    integrity: Mapping[str, Any] | None = None\n"
    "    navigation: Mapping[str, Any] | None = None\n"
    "    schema: str = field(default=CHESS_EXERCISE_MODEL_SCHEMA, init=False)\n",
    label="model navigation field",
)
model = replace_once(
    model,
    '            "solution_integrity": dict(self.integrity) if self.integrity else None,\n'
    '            "exercises": [exercise.to_dict() for exercise in self.exercises],\n',
    '            "solution_integrity": dict(self.integrity) if self.integrity else None,\n'
    '            "exercise_navigation": dict(self.navigation) if self.navigation else None,\n'
    '            "exercises": [exercise.to_dict() for exercise in self.exercises],\n',
    label="model navigation serialization",
)
model = replace_once(
    model,
    "            integrity=(\n"
    "                dict(value.get(\"solution_integrity\"))\n"
    "                if isinstance(value.get(\"solution_integrity\"), Mapping)\n"
    "                else None\n"
    "            ),\n"
    "        )\n",
    "            integrity=(\n"
    "                dict(value.get(\"solution_integrity\"))\n"
    "                if isinstance(value.get(\"solution_integrity\"), Mapping)\n"
    "                else None\n"
    "            ),\n"
    "            navigation=(\n"
    "                dict(value.get(\"exercise_navigation\"))\n"
    "                if isinstance(value.get(\"exercise_navigation\"), Mapping)\n"
    "                else None\n"
    "            ),\n"
    "        )\n",
    label="model navigation parsing",
)
model = replace_once(
    model,
    "    return ChessExerciseModel(\n"
    "        exercises=tuple(exercises),\n",
    "    navigation_report = build_navigation_report(\n"
    "        [exercise.to_dict() for exercise in exercises],\n"
    "        default_document=\"reader.xhtml\",\n"
    "    )\n"
    "    navigation_by_id = {record.exercise_id: record for record in navigation_report.records}\n"
    "    exercises = [\n"
    "        replace(\n"
    "            exercise,\n"
    "            navigation=(\n"
    "                navigation_by_id[exercise.exercise_id].to_dict()\n"
    "                if exercise.exercise_id in navigation_by_id\n"
    "                else None\n"
    "            ),\n"
    "        )\n"
    "        for exercise in exercises\n"
    "    ]\n\n"
    "    return ChessExerciseModel(\n"
    "        exercises=tuple(exercises),\n",
    label="navigation report construction",
)
model = replace_once(
    model,
    "        reconciliation=reconciliation_report.to_dict(),\n"
    "        integrity=SolutionIntegrityReport(records=tuple(integrity_records)).to_dict(),\n"
    "    )\n",
    "        reconciliation=reconciliation_report.to_dict(),\n"
    "        integrity=SolutionIntegrityReport(records=tuple(integrity_records)).to_dict(),\n"
    "        navigation=navigation_report.to_dict(),\n"
    "    )\n",
    label="navigation report return",
)
model = replace_once(
    model,
    "    integrity = exercise.get(\"solution_integrity\") if isinstance(exercise.get(\"solution_integrity\"), Mapping) else {}\n"
    "    exercise_id = str(exercise.get(\"exercise_id\") or \"\")\n",
    "    integrity = exercise.get(\"solution_integrity\") if isinstance(exercise.get(\"solution_integrity\"), Mapping) else {}\n"
    "    navigation = exercise.get(\"navigation\") if isinstance(exercise.get(\"navigation\"), Mapping) else {}\n"
    "    exercise_id = str(exercise.get(\"exercise_id\") or \"\")\n",
    label="reader navigation mapping",
)
model = replace_once(
    model,
    '        "solution_integrity_findings": [\n'
    '            str(item.get("code") or "")\n'
    '            for item in integrity.get("findings") or []\n'
    '            if isinstance(item, Mapping) and item.get("code")\n'
    '        ],\n'
    "    }\n",
    '        "solution_integrity_findings": [\n'
    '            str(item.get("code") or "")\n'
    '            for item in integrity.get("findings") or []\n'
    '            if isinstance(item, Mapping) and item.get("code")\n'
    '        ],\n'
    '        "navigation_status": str(navigation.get("status") or "blocked"),\n'
    '        "exercise_anchor": str(navigation.get("exercise_anchor") or ""),\n'
    '        "solution_anchor": str(navigation.get("solution_anchor") or ""),\n'
    '        "solution_href": str(navigation.get("forward_href") or ""),\n'
    '        "exercise_href": str(navigation.get("backlink_href") or ""),\n'
    '        "solution_link_text": str(navigation.get("forward_text") or ""),\n'
    '        "backlink_text": str(navigation.get("backlink_text") or ""),\n'
    '        "navigation_findings": [\n'
    '            str(item.get("code") or "")\n'
    '            for item in navigation.get("findings") or []\n'
    '            if isinstance(item, Mapping) and item.get("code")\n'
    '        ],\n'
    "    }\n",
    label="reader navigation fields",
)
model_path.write_text(model, encoding="utf-8")


renderer_path = Path("chess_study_export.py")
renderer = renderer_path.read_text(encoding="utf-8")
renderer = replace_once(
    renderer,
    '        "exercise_model_warnings": exercise_payload["warnings"],\n'
    '        "exercises": exercise_payload["exercises"],\n',
    '        "exercise_model_warnings": exercise_payload["warnings"],\n'
    '        "exercise_navigation": exercise_payload.get("exercise_navigation"),\n'
    '        "exercises": exercise_payload["exercises"],\n',
    label="semantic book navigation report",
)
renderer = replace_once(
    renderer,
    '            "warnings": list(payload.get("exercise_model_warnings") or []),\n'
    '            "exercises": list(payload.get("exercises") or []),\n',
    '            "warnings": list(payload.get("exercise_model_warnings") or []),\n'
    '            "exercise_navigation": payload.get("exercise_navigation"),\n'
    '            "exercises": list(payload.get("exercises") or []),\n',
    label="semantic report navigation evidence",
)
renderer = replace_once(
    renderer,
    "    pages = [page for page in semantic_book.get(\"pages\") or [] if isinstance(page, Mapping)]\n"
    "    exercises_by_page = _semantic_exercise_items_by_page(semantic_book)\n"
    "    multi_exercise_pages = {page_number for page_number, items in exercises_by_page.items() if len(items) > 1}\n",
    "    pages = [page for page in semantic_book.get(\"pages\") or [] if isinstance(page, Mapping)]\n"
    "    exercise_items = _semantic_exercise_items(semantic_book)\n"
    "    exercises_by_page: dict[int, list[dict[str, Any]]] = {}\n"
    "    for item in exercise_items:\n"
    "        exercises_by_page.setdefault(int(item.get(\"source_page\") or 0), []).append(item)\n"
    "    navigation_by_exercise = {\n"
    "        str(item.get(\"exercise_id\") or \"\"): item\n"
    "        for item in exercise_items\n"
    "        if str(item.get(\"exercise_id\") or \"\")\n"
    "    }\n"
    "    multi_exercise_pages = {page_number for page_number, items in exercises_by_page.items() if len(items) > 1}\n",
    label="semantic flow navigation index",
)
renderer = replace_once(
    renderer,
    "                rendered = _semantic_book_block_html(block, page_number=page_number, block_index=block_index)\n",
    "                rendered_block = dict(block)\n"
    "                if block_type == \"solution\" and block_exercise_id in navigation_by_exercise:\n"
    "                    navigation_item = navigation_by_exercise[block_exercise_id]\n"
    "                    for key in (\n"
    "                        \"navigation_status\",\n"
    "                        \"exercise_anchor\",\n"
    "                        \"solution_anchor\",\n"
    "                        \"solution_href\",\n"
    "                        \"exercise_href\",\n"
    "                        \"solution_link_text\",\n"
    "                        \"backlink_text\",\n"
    "                    ):\n"
    "                        rendered_block[key] = navigation_item.get(key)\n"
    "                rendered = _semantic_book_block_html(rendered_block, page_number=page_number, block_index=block_index)\n",
    label="solution block navigation injection",
)
renderer = replace_once(
    renderer,
    "    if canonical_exercises:\n"
    "        return [\n"
    "            {\n"
    "                **exercise_to_reader_item(item),\n"
    "                \"label\": _semantic_exercise_label(str(item.get(\"exercise_id\") or \"\")),\n"
    "                \"solution_id\": (\n"
    "                    _reader_anchor(\"solution\", item.get(\"exercise_id\"), fallback=\"exercise\")\n"
    "                    if isinstance(item.get(\"solution\"), Mapping)\n"
    "                    else \"\"\n"
    "                ),\n"
    "            }\n"
    "            for item in canonical_exercises\n"
    "        ]\n",
    "    if canonical_exercises:\n"
    "        items: list[dict[str, Any]] = []\n"
    "        for item in canonical_exercises:\n"
    "            reader_item = exercise_to_reader_item(item)\n"
    "            reader_item[\"label\"] = _semantic_exercise_label(str(item.get(\"exercise_id\") or \"\"))\n"
    "            reader_item[\"solution_id\"] = (\n"
    "                str(reader_item.get(\"solution_anchor\") or \"\")\n"
    "                if reader_item.get(\"navigation_status\") == \"accepted\"\n"
    "                else \"\"\n"
    "            )\n"
    "            items.append(reader_item)\n"
    "        return items\n",
    label="canonical exercise navigation items",
)
renderer = replace_once(
    renderer,
    "            {**item, \"_solution_anchor_target\": str(item.get(\"exercise_id\") or \"\") in anchor_ids},\n",
    "            {\n"
    "                **item,\n"
    "                \"_solution_anchor_target\": (\n"
    "                    str(item.get(\"exercise_id\") or \"\") in anchor_ids\n"
    "                    and item.get(\"navigation_status\") == \"accepted\"\n"
    "                    and bool(item.get(\"solution_anchor\"))\n"
    "                ),\n"
    "            },\n",
    label="accepted solution anchor target",
)
renderer = replace_once(
    renderer,
    "    exercise_id = str(item.get(\"exercise_id\") or \"\")\n"
    "    card_id = _reader_anchor(\"exercise\", exercise_id, fallback=\"exercise\") if compact else _reader_anchor(\"study-exercise\", exercise_id, fallback=\"exercise\")\n"
    "    label = str(item.get(\"label\") or _semantic_exercise_label(exercise_id))\n",
    "    exercise_id = str(item.get(\"exercise_id\") or \"\")\n"
    "    navigation_accepted = item.get(\"navigation_status\") == \"accepted\"\n"
    "    canonical_exercise_anchor = str(item.get(\"exercise_anchor\") or \"\") if navigation_accepted else \"\"\n"
    "    canonical_solution_anchor = str(item.get(\"solution_anchor\") or \"\") if navigation_accepted else \"\"\n"
    "    card_id = (\n"
    "        canonical_exercise_anchor\n"
    "        if compact and canonical_exercise_anchor\n"
    "        else (\n"
    "            _reader_anchor(\"exercise\", exercise_id, fallback=\"exercise\")\n"
    "            if compact\n"
    "            else _reader_anchor(\"study-exercise\", exercise_id, fallback=\"exercise\")\n"
    "        )\n"
    "    )\n"
    "    label = str(item.get(\"label\") or _semantic_exercise_label(exercise_id))\n",
    label="exercise canonical anchors",
)
renderer = replace_once(
    renderer,
    "        linked_target=card_id,\n"
    "        original_source=original_crop_path,\n",
    "        linked_target=card_id,\n"
    "        linked_href=str(item.get(\"exercise_href\") or \"\") if navigation_accepted else \"\",\n"
    "        linked_label=str(item.get(\"backlink_text\") or \"Open linked diagram\"),\n"
    "        original_source=original_crop_path,\n",
    label="exercise solution backlink",
)
renderer = replace_once(
    renderer,
    "    analysis_link = f'<a class=\"copy-button secondary\" href=\"#{html.escape(card_id, quote=True)}\">Open analysis / board</a>'\n"
    "    solution_anchor_attr = f' id=\"{html.escape(_reader_anchor(\"solution\", exercise_id, fallback=\"solution\"), quote=True)}\"' if item.get(\"_solution_anchor_target\") else \"\"\n",
    "    solution_link_html = (\n"
    "        f'<a class=\"copy-button secondary semantic-solution-link\" href=\"{html.escape(str(item.get(\"solution_href\") or \"\"), quote=True)}\">'\n"
    "        f'{html.escape(str(item.get(\"solution_link_text\") or f\"Open solution for {label}\"))}</a>'\n"
    "        if navigation_accepted and item.get(\"solution_href\")\n"
    "        else \"\"\n"
    "    )\n"
    "    analysis_link = f'<a class=\"copy-button secondary\" href=\"#{html.escape(card_id, quote=True)}\">Open analysis / board</a>'\n"
    "    solution_anchor_attr = (\n"
    "        f' id=\"{html.escape(canonical_solution_anchor, quote=True)}\"'\n"
    "        if item.get(\"_solution_anchor_target\") and canonical_solution_anchor\n"
    "        else \"\"\n"
    "    )\n",
    label="forward link and solution anchor",
)
renderer = replace_once(
    renderer,
    "      {copy_fen_html}\n"
    "      {analysis_link}\n",
    "      {copy_fen_html}\n"
    "      {solution_link_html}\n"
    "      {analysis_link}\n",
    label="exercise forward action",
)
renderer = replace_once(
    renderer,
    "    linked_target: str = \"\",\n"
    "    original_source: str = \"\",\n",
    "    linked_target: str = \"\",\n"
    "    linked_href: str = \"\",\n"
    "    linked_label: str = \"Open linked diagram\",\n"
    "    original_source: str = \"\",\n",
    label="exercise solution navigation signature",
)
renderer = replace_once(
    renderer,
    "        linked_target=linked_target,\n"
    "        original_source=original_source,\n",
    "        linked_target=linked_target,\n"
    "        linked_href=linked_href,\n"
    "        linked_label=linked_label,\n"
    "        original_source=original_source,\n",
    label="exercise solution navigation pass-through",
)
renderer = replace_once(
    renderer,
    "    exercise_id = str(block.get(\"exercise_id\") or \"\")\n"
    "    label = _semantic_exercise_label(exercise_id)\n"
    "    linked_target = _reader_anchor(\"exercise\", exercise_id, fallback=\"exercise\") if exercise_id else _reader_anchor(\"diagram\", block.get(\"diagram_id\"), fallback=\"diagram\")\n",
    "    exercise_id = str(block.get(\"exercise_id\") or \"\")\n"
    "    label = _semantic_exercise_label(exercise_id)\n"
    "    navigation_accepted = block.get(\"navigation_status\") == \"accepted\"\n"
    "    linked_href = str(block.get(\"exercise_href\") or \"\") if navigation_accepted else \"\"\n"
    "    linked_label = str(block.get(\"backlink_text\") or \"Open linked diagram\")\n"
    "    linked_target = (\n"
    "        \"\"\n"
    "        if linked_href\n"
    "        else (\n"
    "            _reader_anchor(\"exercise\", exercise_id, fallback=\"exercise\")\n"
    "            if exercise_id\n"
    "            else _reader_anchor(\"diagram\", block.get(\"diagram_id\"), fallback=\"diagram\")\n"
    "        )\n"
    "    )\n",
    label="solution canonical backlink",
)
renderer = replace_once(
    renderer,
    "        linked_target=linked_target,\n"
    "        original_source=str(block.get(\"original_source_path\") or \"\"),\n",
    "        linked_target=linked_target,\n"
    "        linked_href=linked_href,\n"
    "        linked_label=linked_label,\n"
    "        original_source=str(block.get(\"original_source_path\") or \"\"),\n",
    label="solution body canonical backlink",
)
renderer = replace_once(
    renderer,
    "    solution_id = _reader_anchor(\"solution\", exercise_id or block.get(\"diagram_id\"), fallback=\"solution\")\n",
    "    solution_id = (\n"
    "        str(block.get(\"solution_anchor\") or \"\")\n"
    "        if navigation_accepted and block.get(\"solution_anchor\")\n"
    "        else _reader_anchor(\"solution\", exercise_id or block.get(\"diagram_id\"), fallback=\"solution\")\n"
    "    )\n",
    label="solution canonical anchor",
)
renderer = replace_once(
    renderer,
    "    linked_target: str,\n"
    "    original_source: str,\n",
    "    linked_target: str,\n"
    "    linked_href: str = \"\",\n"
    "    linked_label: str = \"Open linked diagram\",\n"
    "    original_source: str = \"\",\n",
    label="solution body navigation signature",
)
renderer = replace_once(
    renderer,
    "    if linked_target:\n"
    "        action_parts.append(f'<a class=\"copy-button secondary\" href=\"#{html.escape(linked_target, quote=True)}\">Open linked diagram</a>')\n",
    "    if linked_href:\n"
    "        action_parts.append(\n"
    "            f'<a class=\"copy-button secondary semantic-exercise-backlink\" href=\"{html.escape(linked_href, quote=True)}\">'\n"
    "            f'{html.escape(linked_label)}</a>'\n"
    "        )\n"
    "    elif linked_target:\n"
    "        action_parts.append(\n"
    "            f'<a class=\"copy-button secondary\" href=\"#{html.escape(linked_target, quote=True)}\">'\n"
    "            f'{html.escape(linked_label)}</a>'\n"
    "        )\n",
    label="solution backlink rendering",
)
renderer_path.write_text(renderer, encoding="utf-8")
