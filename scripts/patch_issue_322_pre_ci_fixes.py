from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECONCILIATION_PATH = ROOT / "chess_exercise_reconciliation.py"
MODEL_PATH = ROOT / "chess_exercise_model.py"
TEST_PATH = ROOT / "test_chess_exercise_model_reconciliation.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    reconciliation = RECONCILIATION_PATH.read_text(encoding="utf-8")
    reconciliation = replace_once(
        reconciliation,
        '''def _first_value(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in {None, "", (), []}:
            return value
    return None
''',
        '''def _first_value(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, dict, set)) and not value:
            continue
        return value
    return None
''',
        "safe first value",
    )
    RECONCILIATION_PATH.write_text(reconciliation, encoding="utf-8")

    model = MODEL_PATH.read_text(encoding="utf-8")
    model = replace_once(
        model,
        '''            if exercise_id not in components:
                components[exercise_id] = {}
                order.append(exercise_id)
            kind = str(block.get("type"))
            if kind == "solution":
                solution_candidates.append({**dict(block), "_page_number": page_number})
                continue
''',
        '''            kind = str(block.get("type"))
            if kind == "solution":
                solution_candidates.append({**dict(block), "_page_number": page_number})
                continue
            if exercise_id not in components:
                components[exercise_id] = {}
                order.append(exercise_id)
''',
        "avoid solution-only exercises",
    )
    MODEL_PATH.write_text(model, encoding="utf-8")

    tests = TEST_PATH.read_text(encoding="utf-8")
    marker = '''    def test_title_mismatch_adds_error_and_blocks_model_report(self) -> None:
'''
    addition = '''    def test_solution_only_identifier_does_not_create_phantom_exercise(self) -> None:
        model = build_chess_exercise_model(
            [
                {
                    "page_number": 50,
                    "blocks": [
                        {
                            "type": "exercise",
                            "exercise_id": "exercise-500",
                            "printed_number": 500,
                            "raw_title": "A - B, Berlin 2001",
                            "source_page": 50,
                        },
                        {
                            "type": "solution",
                            "exercise_id": "solution-record-500",
                            "solution_number": 500,
                            "solution_title": "A - B, Berlin 2001",
                            "solution_page": 250,
                            "book_line": "1. Qh7+",
                        },
                    ],
                }
            ]
        )

        self.assertEqual([exercise.exercise_id for exercise in model.exercises], ["exercise-500"])
        self.assertEqual(model.exercises[0].solution.raw_text, "1. Qh7+")
        self.assertEqual(model.exercises[0].solution_match["selected_solution_id"], "solution-record-500")

'''
    tests = replace_once(tests, marker, addition + marker, "phantom exercise regression")
    TEST_PATH.write_text(tests, encoding="utf-8")


if __name__ == "__main__":
    main()
