# Full FEN Recognition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KindleMaster rozpoznaje FEN dla skanowanych i fontowych diagramów szachowych w sposób mierzalny, bez halucynowania pozycji i bez publikowania FEN poniżej progu pewności.

**Architecture:** Deterministyczny recognizer pozostaje źródłem prawdy. OpenAI/OpenAI Developers służy tylko do reviewer/eval/label-assist dla cropów i przypadków niepewnych; wynik AI nie nadpisuje EPUB bez przejścia lokalnej walidacji FEN, progu confidence i testów corpus. Pipeline ma trzy warstwy: dataset i etykiety pól, klasyfikator/template recognizer 64 pól, integracja EPUB/report z evalami.

**Tech Stack:** Python, PyMuPDF, Pillow, NumPy, istniejący `chess_position_recognizer.py`, `pymupdf_chess_extractor.py`, `converter.py`, `unittest`, opcjonalny OpenAI reviewer przez istniejący wzorzec providerów jakości.

---

## Current Evidence

- Aktualny `Fundamenty 1-1.epub` ma `328` diagramów i `328` widocznych statusów `FEN: wymaga review`.
- `data-fen=0`, czyli nie ma zaakceptowanego deterministycznego FEN.
- OCR tekstu i notacji jest już szeroki: `162` markery OCR, około `158k` znaków i `4680` tokenów notacji.
- Istniejący kod umie:
  - budować i walidować FEN w `chess_position_recognizer.py`,
  - wykrywać kandydatów plansz,
  - rozpoznawać FEN z fontowych rzędów,
  - rozpoznawać obraz tylko wtedy, gdy dostanie kompletny template-set,
  - wyciągać cropy przez `scripts/extract_chess_diagram_crops.py`.
- Największa luka: brak kompletnego, wersjonowanego zestawu template’ów/etykiet figur dla stylu ze skanów, brak eval harness dla cropów, brak raportu per-square confidence w jakości.

## File Structure

- Create: `reference_inputs/chess_fen/templates/fundamenty_merida_like/README.md`
  - Opis zestawu template’ów, format nazw, źródło, kryteria akceptacji i zakaz używania publikacyjnych wyjątków.
- Create: `reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl`
  - Mały seed dataset z cropami i pełnymi FEN-ami z ręcznie zweryfikowanych diagramów.
- Create: `reports/chess_fen/evals/.gitkeep`
  - Miejsce na lokalne wyniki ewaluacji, nie źródło prawdy.
- Create: `scripts/build_chess_piece_templates.py`
  - Buduje template-set pól figur z ręcznie opisanych cropów.
- Create: `scripts/evaluate_chess_fen_recognizer.py`
  - Uruchamia recognizer na labelach i zapisuje accuracy, exact-FEN rate, per-piece confusion i przypadki review.
- Create: `openai_chess_fen_reviewer.py`
  - Opcjonalny provider review zgodny z polityką: audytuje crop i propozycję FEN, ale nie mutuje EPUB.
- Modify: `chess_position_recognizer.py`
  - Dodać wynik per-square, orientację planszy, lepsze confidence, obsługę multiple template variants.
- Modify: `pymupdf_chess_extractor.py`
  - Wpiąć template dir domyślnie dla profilu scanned chess, zapisywać per-diagram quality details, rozdzielić visible final FEN od review note.
- Modify: `converter.py`
  - Dodać konfigurację `chess_fen_template_profile`, `chess_fen_review_provider_enabled`, `chess_fen_emit_review_notes`.
- Modify: `test_chess_fen_recognition.py`
  - Testy datasetu, eval harness, confidence threshold, brak publikowania FEN poniżej progu.
- Modify: `test_smoke_chess_quality.py`
  - Wymagać `chess_fen.diagram_count > 0`; dla fixture z seed labelami wymagać `fen_count > 0`.
- Modify: `reference_inputs/manifest.json`
  - Dodać informację, że `fundamenty_scan_chess_pdf` ma FEN eval seed, ale nie oczekiwać 100% FEN przed pełnym datasetem.

## Target Acceptance

- Fontowe diagramy: `fen_count / diagram_count = 100%` na kontrolowanych testach fontowych, bez OpenAI.
- Skanowane diagramy seed: `exact_fen_accuracy >= 90%` na ręcznie zweryfikowanym seed secie minimum 20 diagramów z `Fundamenty`.
- Skanowany pełny `Fundamenty`: `fen_count > 0` w raporcie i EPUB; FEN tylko dla high-confidence.
- Brak false positive FEN: jeśli wynik nie przejdzie walidacji lub confidence, EPUB ma `requires-review`, nie `data-fen`.
- Raport zawiera: `diagram_count`, `fen_count`, `manual_review_count`, `per_piece_accuracy`, `exact_fen_accuracy`, `low_confidence_count`, `openai_review_count`.
- OpenAI reviewer może oznaczyć problem i zaproponować label, ale nie może sam wstawić FEN do EPUB.

---

### Task 1: Seed Dataset Contract

**Files:**
- Create: `reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl`
- Create: `reference_inputs/chess_fen/templates/fundamenty_merida_like/README.md`
- Test: `test_chess_fen_recognition.py`

- [ ] **Step 1: Write the failing test for label schema**

Add this test to `test_chess_fen_recognition.py`:

```python
def test_fundamenty_seed_labels_have_required_fen_schema(self) -> None:
    import json
    from pathlib import Path

    label_path = Path("reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl")
    self.assertTrue(label_path.exists(), "seed label file must exist")
    records = [json.loads(line) for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    self.assertGreaterEqual(len(records), 3)
    for record in records:
        self.assertIn("crop_path", record)
        self.assertIn("fen", record)
        self.assertIn("source_pdf", record)
        self.assertIn("page", record)
        self.assertIn("diagram_index", record)
        is_valid, warnings = validate_fen(record["fen"])
        self.assertTrue(is_valid, warnings)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_fundamenty_seed_labels_have_required_fen_schema
```

Expected: `FAIL` because the seed label file does not exist.

- [ ] **Step 3: Create the seed label file**

Create `reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl` with at least three verified starter entries. Use crop paths already generated under `reports/chess_fen/fundamenty_crops/` and verify every FEN manually against the source page before committing.

Example format:

```jsonl
{"id":"fundamenty_p014_c1","source_pdf":"reference_inputs/pdf/fundamenty_1_1_scan_chess.pdf","page":14,"diagram_index":1,"crop_path":"reports/chess_fen/fundamenty_crops/fundamenty_1_1_scan_chess_p014_c1.png","fen":"8/8/8/3k4/8/8/4K3/8 w - - 0 1","verified_by":"manual","verified_at":"2026-05-26","notes":"Replace this example FEN with the manually verified diagram position before running acceptance."}
```

Do not keep the example FEN unless it is actually the diagram position.

- [ ] **Step 4: Create template README**

Create `reference_inputs/chess_fen/templates/fundamenty_merida_like/README.md`:

```markdown
# Fundamenty Merida-Like Chess Piece Templates

This directory stores local deterministic templates for scanned chess diagrams.

Naming:
- `empty-light-001.png`, `empty-dark-001.png`
- `K-white-001.png`, `Q-white-001.png`, `R-white-001.png`, `B-white-001.png`, `N-white-001.png`, `P-white-001.png`
- `k-black-001.png`, `q-black-001.png`, `r-black-001.png`, `b-black-001.png`, `n-black-001.png`, `p-black-001.png`

Rules:
- Templates must be cropped square cells, not full boards.
- Every template must come from a labeled crop with a known FEN.
- The recognizer may publish FEN only when the full board validates and confidence is above threshold.
- OpenAI review output is allowed as evidence, but not as the source of truth.
```

- [ ] **Step 5: Run the schema test**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_fundamenty_seed_labels_have_required_fen_schema
```

Expected: `PASS` after replacing example FENs with real verified FENs.

### Task 2: Template Builder

**Files:**
- Create: `scripts/build_chess_piece_templates.py`
- Modify: `test_chess_fen_recognition.py`

- [ ] **Step 1: Write failing unit test for template extraction from labeled FEN**

Add this test to `test_chess_fen_recognition.py`:

```python
def test_template_builder_extracts_64_cells_from_labeled_board(self) -> None:
    import tempfile
    from pathlib import Path
    from scripts.build_chess_piece_templates import build_templates_from_labels

    board = [
        list("rnbqkbnr"),
        list("pppppppp"),
        [""] * 8,
        [""] * 8,
        [""] * 8,
        [""] * 8,
        list("PPPPPPPP"),
        list("RNBQKBNR"),
    ]
    image_data, _ = _labeled_board_png_and_templates(board)

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        crop_path = root / "board.png"
        crop_path.write_bytes(image_data)
        labels_path = root / "labels.jsonl"
        labels_path.write_text(
            '{"crop_path":"' + str(crop_path).replace("\\\\", "\\\\\\\\") + '",'
            '"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",'
            '"source_pdf":"synthetic","page":1,"diagram_index":1}\\n',
            encoding="utf-8",
        )
        output_dir = root / "templates"
        summary = build_templates_from_labels(labels_path, output_dir=output_dir)

        self.assertEqual(summary["boards_processed"], 1)
        self.assertGreaterEqual(summary["template_count"], 64)
        self.assertTrue((output_dir / "K-white-001.png").exists())
        self.assertTrue((output_dir / "empty-light-001.png").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_template_builder_extracts_64_cells_from_labeled_board
```

Expected: `ImportError` because `scripts.build_chess_piece_templates` does not exist.

- [ ] **Step 3: Implement `scripts/build_chess_piece_templates.py`**

Create:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


PIECE_NAMES = {
    "K": "K-white", "Q": "Q-white", "R": "R-white", "B": "B-white", "N": "N-white", "P": "P-white",
    "k": "k-black", "q": "q-black", "r": "r-black", "b": "b-black", "n": "n-black", "p": "p-black",
}


def build_templates_from_labels(labels_path: str | Path, *, output_dir: str | Path) -> dict[str, Any]:
    labels = _read_jsonl(Path(labels_path))
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    boards_processed = 0
    template_count = 0

    for record in labels:
        placement = str(record["fen"]).split()[0]
        board = _placement_to_board(placement)
        crop = Image.open(record["crop_path"]).convert("L")
        crop = ImageOps.autocontrast(crop)
        side = min(crop.size)
        left = max(0, (crop.width - side) // 2)
        top = max(0, (crop.height - side) // 2)
        board_image = crop.crop((left, top, left + side, top + side))
        cell = side / 8.0

        for row in range(8):
            for col in range(8):
                piece = board[row][col]
                label = PIECE_NAMES.get(piece)
                if label is None:
                    square_color = "light" if (row + col) % 2 == 0 else "dark"
                    label = f"empty-{square_color}"
                counts[label] = counts.get(label, 0) + 1
                cell_image = board_image.crop(
                    (
                        int(round(col * cell)),
                        int(round(row * cell)),
                        int(round((col + 1) * cell)),
                        int(round((row + 1) * cell)),
                    )
                ).resize((64, 64), Image.Resampling.LANCZOS)
                filename = f"{label}-{counts[label]:03d}.png"
                cell_image.save(target / filename, format="PNG", optimize=True)
                template_count += 1
        boards_processed += 1

    summary = {
        "status": "ok",
        "labels_path": str(labels_path),
        "output_dir": str(target),
        "boards_processed": boards_processed,
        "template_count": template_count,
        "label_counts": counts,
    }
    (target / "template_manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _placement_to_board(placement: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for rank in placement.split("/"):
        row: list[str] = []
        for char in rank:
            if char.isdigit():
                row.extend([""] * int(char))
            else:
                row.append(char)
        if len(row) != 8:
            raise ValueError(f"Invalid FEN rank width: {rank}")
        rows.append(row)
    if len(rows) != 8:
        raise ValueError("FEN placement must have 8 ranks")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Build chess piece templates from labeled board crops.")
    parser.add_argument("labels")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(build_templates_from_labels(args.labels, output_dir=args.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the builder test**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_template_builder_extracts_64_cells_from_labeled_board
```

Expected: `PASS`.

### Task 3: Recognizer Eval Harness

**Files:**
- Create: `scripts/evaluate_chess_fen_recognizer.py`
- Modify: `test_chess_fen_recognition.py`

- [ ] **Step 1: Write failing eval test**

Add:

```python
def test_evaluate_chess_fen_recognizer_reports_exact_fen_accuracy(self) -> None:
    import tempfile
    from pathlib import Path
    from scripts.build_chess_piece_templates import build_templates_from_labels
    from scripts.evaluate_chess_fen_recognizer import evaluate_chess_fen_recognizer

    board = [
        list("rnbqkbnr"),
        list("pppppppp"),
        [""] * 8,
        [""] * 8,
        [""] * 8,
        [""] * 8,
        list("PPPPPPPP"),
        list("RNBQKBNR"),
    ]
    image_data, _ = _labeled_board_png_and_templates(board)

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        crop_path = root / "board.png"
        crop_path.write_bytes(image_data)
        labels_path = root / "labels.jsonl"
        labels_path.write_text(
            '{"crop_path":"' + str(crop_path).replace("\\\\", "\\\\\\\\") + '",'
            '"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",'
            '"source_pdf":"synthetic","page":1,"diagram_index":1}\\n',
            encoding="utf-8",
        )
        template_dir = root / "templates"
        build_templates_from_labels(labels_path, output_dir=template_dir)
        result = evaluate_chess_fen_recognizer(labels_path, template_dir=template_dir, min_confidence=0.80)

        self.assertEqual(result["case_count"], 1)
        self.assertEqual(result["exact_fen_accuracy"], 1.0)
        self.assertEqual(result["fen_count"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_evaluate_chess_fen_recognizer_reports_exact_fen_accuracy
```

Expected: `ImportError`.

- [ ] **Step 3: Implement evaluator**

Create `scripts/evaluate_chess_fen_recognizer.py`:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from chess_position_recognizer import load_piece_templates, recognize_chess_position_from_image


def evaluate_chess_fen_recognizer(
    labels_path: str | Path,
    *,
    template_dir: str | Path,
    min_confidence: float = 0.85,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    labels = [json.loads(line) for line in Path(labels_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    templates = load_piece_templates(template_dir)
    cases: list[dict[str, Any]] = []
    exact = 0
    with_fen = 0

    for record in labels:
        crop_data = Path(record["crop_path"]).read_bytes()
        result = recognize_chess_position_from_image(
            crop_data,
            piece_templates=templates,
            min_confidence=min_confidence,
        ).to_dict()
        expected = str(record["fen"]).strip()
        actual = str(result.get("fen") or "").strip()
        matched = bool(actual and actual == expected)
        exact += int(matched)
        with_fen += int(bool(actual))
        cases.append(
            {
                "id": record.get("id", ""),
                "crop_path": record["crop_path"],
                "expected_fen": expected,
                "actual_fen": actual,
                "matched": matched,
                "confidence": result.get("confidence", 0.0),
                "warnings": result.get("warnings", []),
                "requires_review": result.get("requires_review", True),
            }
        )

    summary = {
        "status": "passed" if labels and exact == len(labels) else "failed",
        "case_count": len(labels),
        "fen_count": with_fen,
        "exact_fen_count": exact,
        "exact_fen_accuracy": round(exact / max(1, len(labels)), 4),
        "cases": cases,
    }
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic chess FEN recognition.")
    parser.add_argument("labels")
    parser.add_argument("--template-dir", required=True)
    parser.add_argument("--min-confidence", type=float, default=0.85)
    parser.add_argument("--output", default="reports/chess_fen/evals/latest.json")
    args = parser.parse_args()
    result = evaluate_chess_fen_recognizer(
        args.labels,
        template_dir=args.template_dir,
        min_confidence=args.min_confidence,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run evaluator test**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_evaluate_chess_fen_recognizer_reports_exact_fen_accuracy
```

Expected: `PASS`.

### Task 4: Per-Square Confidence and Orientation

**Files:**
- Modify: `chess_position_recognizer.py`
- Modify: `test_chess_fen_recognition.py`

- [ ] **Step 1: Write failing test for per-square details**

Add:

```python
def test_image_template_result_exposes_square_confidence_matrix(self) -> None:
    board = [
        list("rnbqkbnr"),
        list("pppppppp"),
        [""] * 8,
        [""] * 8,
        [""] * 8,
        [""] * 8,
        list("PPPPPPPP"),
        list("RNBQKBNR"),
    ]
    image_data, templates = _labeled_board_png_and_templates(board)

    result = recognize_chess_position_from_image(image_data, piece_templates=templates, min_confidence=0.85)
    payload = result.to_dict()

    self.assertIn("squares", payload)
    self.assertEqual(len(payload["squares"]), 64)
    self.assertEqual(payload["squares"][0]["square"], "a8")
    self.assertEqual(payload["squares"][0]["piece"], "r")
    self.assertGreaterEqual(payload["squares"][0]["confidence"], 0.85)
```

- [ ] **Step 2: Update dataclass**

Modify `ChessFenResult`:

```python
@dataclass(frozen=True)
class ChessFenResult:
    fen: str = ""
    placement: str = ""
    confidence: float = 0.0
    side_to_move: str = "w"
    bbox: tuple[float, float, float, float] | None = None
    method: str = "unavailable"
    warnings: list[str] = field(default_factory=list)
    requires_review: bool = True
    board_detected: bool = False
    squares: list[dict[str, Any]] = field(default_factory=list)
```

Extend `to_dict()`:

```python
"squares": [dict(square) for square in self.squares],
```

- [ ] **Step 3: Return square details from template classification**

Change `_classify_board_cells` to return `(board, template_confidence, squares)`, where each square dict has:

```python
{
    "square": f"{chr(ord('a') + col)}{8 - row}",
    "piece": label,
    "confidence": round(float(confidence), 3),
}
```

Update `_recognize_board_with_templates` to pass `squares=squares`.

- [ ] **Step 4: Run focused test**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_image_template_result_exposes_square_confidence_matrix
```

Expected: `PASS`.

### Task 5: Runtime Template Profile Integration

**Files:**
- Modify: `converter.py`
- Modify: `pymupdf_chess_extractor.py`
- Modify: `test_chess_fen_recognition.py`

- [ ] **Step 1: Write failing config test**

Add:

```python
def test_scanned_chess_default_template_profile_points_to_reference_templates(self) -> None:
    config = ConversionConfig()
    self.assertEqual(config.chess_fen_template_profile, "fundamenty_merida_like")
```

- [ ] **Step 2: Add config fields**

In `ConversionConfig`, add:

```python
chess_fen_template_profile: str = "fundamenty_merida_like"
chess_fen_emit_review_notes: bool = False
chess_fen_review_provider_enabled: bool = False
```

- [ ] **Step 3: Resolve template dir**

In `pymupdf_chess_extractor.py`, add helper:

```python
def _resolve_chess_piece_template_dir(config: ConversionConfig) -> str:
    explicit = str(getattr(config, "chess_fen_piece_template_dir", "") or "").strip()
    if explicit:
        return explicit
    profile = str(getattr(config, "chess_fen_template_profile", "") or "").strip()
    if not profile:
        return ""
    candidate = Path("reference_inputs") / "chess_fen" / "templates" / profile
    return str(candidate) if candidate.exists() else ""
```

Use this helper where `template_dir` is currently read from `config.chess_fen_piece_template_dir`.

- [ ] **Step 4: Hide review notes in final reader output by default**

In scanned diagram HTML assembly, emit review note only when `chess_fen_emit_review_notes` is true:

```python
emit_review = bool(getattr(config, "chess_fen_emit_review_notes", False))
if fen_value:
    fen_note = f'<p class="diagram-fen">FEN: {html_module.escape(fen_value)}</p>'
elif emit_review:
    fen_note = '<p class="diagram-fen diagram-review" data-fen-status="requires-review">FEN: wymaga review - brak deterministycznej pewnosci figur.</p>'
else:
    fen_note = ""
```

- [ ] **Step 5: Run targeted tests**

Run:

```powershell
python -m unittest test_chess_fen_recognition.py test_publication_pipeline.py
```

Expected: `OK`.

### Task 6: OpenAI Reviewer/Eval Provider

**Files:**
- Create: `openai_chess_fen_reviewer.py`
- Create: `scripts/check_openai_chess_fen_reviewer.py`
- Modify: `chess_position_recognizer.py`
- Test: `test_chess_fen_recognition.py`

- [ ] **Step 1: Write no-mutation review test**

Add:

```python
def test_openai_chess_review_payload_never_changes_fen_output(self) -> None:
    from chess_position_recognizer import ChessFenResult, review_chess_fen_candidate

    class FakeProvider:
        name = "fake-openai"

        def review_chess_fen(self, context):
            return {"status": "reviewed", "suggested_fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1"}

    result = ChessFenResult(fen="", confidence=0.2, requires_review=True, board_detected=True)
    review = review_chess_fen_candidate(result, provider=FakeProvider(), context={"source": "unit"})

    self.assertEqual(review["status"], "reviewed")
    self.assertEqual(review["changed_output"], False)
    self.assertEqual(result.fen, "")
```

- [ ] **Step 2: Implement provider wrapper**

Create `openai_chess_fen_reviewer.py` with:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass
class OpenAIChessFenReviewer:
    name: str = "openai-chess-fen-reviewer"
    model: str = "configured-by-env"

    def review_chess_fen(self, context: dict[str, Any]) -> Mapping[str, Any]:
        return {
            "status": "skipped",
            "provider": self.name,
            "reason": "live OpenAI review is opt-in; use KINDLEMASTER_OPENAI_CHESS_FEN_REVIEW=1",
            "changed_output": False,
        }


def build_openai_chess_fen_reviewer_from_env() -> OpenAIChessFenReviewer | None:
    if os.getenv("KINDLEMASTER_OPENAI_CHESS_FEN_REVIEW") != "1":
        return None
    if not os.getenv("OPENAI_API_KEY"):
        return None
    return OpenAIChessFenReviewer(model=os.getenv("KINDLEMASTER_OPENAI_CHESS_FEN_MODEL", "configured-by-env"))
```

This first slice is intentionally non-live. A later slice may call the current official OpenAI API after checking official docs and the API key skill.

- [ ] **Step 3: Add check script**

Create `scripts/check_openai_chess_fen_reviewer.py`:

```python
from __future__ import annotations

import json

from openai_chess_fen_reviewer import build_openai_chess_fen_reviewer_from_env


def main() -> int:
    provider = build_openai_chess_fen_reviewer_from_env()
    payload = {
        "enabled": provider is not None,
        "provider": provider.name if provider else "none",
        "mode": "review_only",
        "mutates_fen": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test and config check**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_openai_chess_review_payload_never_changes_fen_output
python scripts/check_openai_chess_fen_reviewer.py
```

Expected: test `PASS`; script reports `mutates_fen: false`.

### Task 7: Corpus Smoke Gate for FEN

**Files:**
- Modify: `test_smoke_chess_quality.py`
- Modify: `scripts/run_smoke_tests.py`
- Modify: `reference_inputs/manifest.json`

- [ ] **Step 1: Add FEN metrics to smoke output**

In `scripts/run_smoke_tests.py`, extend chess inspection to count:

```python
"data_fen_count": parser.data_fen_count,
"visible_fen_count": parser.visible_fen_count,
"fen_review_count": parser.fen_review_count,
```

The parser should increment:

```python
if "data-fen" in attrs_dict:
    self.data_fen_count += 1
```

- [ ] **Step 2: Add test assertion**

In `test_smoke_chess_quality.py`, add a focused assertion for the fixture report:

```python
def test_scanned_chess_smoke_reports_fen_metrics(self) -> None:
    from scripts.run_smoke_tests import _empty_chess_quality_metrics

    metrics = _empty_chess_quality_metrics()
    self.assertIn("data_fen_count", metrics)
    self.assertIn("fen_review_count", metrics)
```

- [ ] **Step 3: Run smoke-quality tests**

Run:

```powershell
python -m unittest test_smoke_chess_quality.py
```

Expected: `OK`.

### Task 8: Full Verification on Fundamenty

**Files:**
- Generated only: `reports/chess_fen/evals/fundamenty_latest.json`
- Generated only: `output/smoke/fundamenty_scan_chess_pdf.epub`
- Generated only: `reports/smoke/smoke_full.json`

- [ ] **Step 1: Extract/refresh crop manifest**

Run:

```powershell
python scripts/extract_chess_diagram_crops.py reference_inputs\pdf\fundamenty_1_1_scan_chess.pdf --output-dir reports\chess_fen\fundamenty_crops --pages 266 --max-candidates-per-page 4 --min-grid-confidence 0.50
```

Expected: manifest with `crop_count > 0`.

- [ ] **Step 2: Build templates from verified labels**

Run:

```powershell
python scripts/build_chess_piece_templates.py reference_inputs\chess_fen\labels\fundamenty_seed_positions.jsonl --output-dir reference_inputs\chess_fen\templates\fundamenty_merida_like
```

Expected: `template_manifest.json` and PNG templates for empty squares plus all 12 pieces.

- [ ] **Step 3: Evaluate recognizer**

Run:

```powershell
python scripts/evaluate_chess_fen_recognizer.py reference_inputs\chess_fen\labels\fundamenty_seed_positions.jsonl --template-dir reference_inputs\chess_fen\templates\fundamenty_merida_like --min-confidence 0.85 --output reports\chess_fen\evals\fundamenty_latest.json
```

Expected after enough labels: `exact_fen_accuracy >= 0.90`.

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
python -m unittest test_chess_fen_recognition.py test_chess_fix.py test_smoke_chess_quality.py test_publication_pipeline.py
```

Expected: `OK`.

- [ ] **Step 5: Run full smoke**

Run:

```powershell
python kindlemaster.py smoke --mode full --case fundamenty_scan_chess_pdf
```

Expected:
- `overall_status=passed`
- `epubcheck_status=passed`
- `chess_diagram_tag_count=328`
- `data_fen_count > 0`
- `manual_review_count < diagram_count`

- [ ] **Step 6: Audit output EPUB**

Run:

```powershell
python kindlemaster.py audit output\smoke\fundamenty_scan_chess_pdf.epub --language pl
```

Expected:
- `decision=pass_with_review` or better,
- `premium_score >= 9.0`,
- no EPUBCheck errors,
- FEN review remains only for unresolved diagrams.

## Multi-Agent Execution

- Agent 1: Dataset and crop labeling workflow.
  - Owns `reference_inputs/chess_fen/**`, `scripts/build_chess_piece_templates.py`.
- Agent 2: Recognizer improvements.
  - Owns `chess_position_recognizer.py`, per-square confidence, orientation.
- Agent 3: Runtime integration and report output.
  - Owns `pymupdf_chess_extractor.py`, `converter.py`, smoke metrics.
- Agent 4: OpenAI reviewer/evals.
  - Owns `openai_chess_fen_reviewer.py`, check script, no-mutation policy.
- Coordinator:
  - Runs tests after each task, rejects any change that publishes FEN without deterministic proof.

## Risks and Guardrails

- Risk: manual seed FEN labels are wrong.
  - Guardrail: every label must pass `validate_fen`; spot-check with source crop; keep OpenAI review as evidence only.
- Risk: template matching overfits one book.
  - Guardrail: template profile is explicit and versioned; general recognizer supports multiple profiles later.
- Risk: false positive FEN is worse than missing FEN.
  - Guardrail: no `data-fen` below threshold; `requires_review` remains visible in reports.
- Risk: final EPUB shows QA text.
  - Guardrail: `chess_fen_emit_review_notes=False` by default; report keeps review queue.
- Risk: OpenAI API behavior changes.
  - Guardrail: use official docs before live implementation; keep provider optional and disabled by default.

## Self-Review

- Spec coverage: full FEN recognition requires data, recognizer, integration, eval, smoke and review; all are covered.
- Red-flag scan: plan contains no undefined work markers or open-ended "add tests"; each task includes files, code, commands and expected result.
- Type consistency: `fen`, `squares`, `confidence`, `requires_review`, `data-fen`, `chess_fen_template_profile` are named consistently across tasks.
- Remaining uncertainty: actual FEN labels for real `Fundamenty` diagrams require manual verification or trusted external ground truth; implementation can progress without asking, but acceptance cannot claim 90% until those labels exist.
