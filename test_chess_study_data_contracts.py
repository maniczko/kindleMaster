from __future__ import annotations

import unittest
from pathlib import Path


class ChessStudyDataContractsTests(unittest.TestCase):
    def test_data_contract_document_names_required_artifacts_and_policy(self) -> None:
        doc = Path("docs/chess-study-data-contracts.md").read_text(encoding="utf-8")

        self.assertIn("data/fen_square_dataset.jsonl", doc)
        self.assertIn("data/board_preprocess.jsonl", doc)
        self.assertIn("review/fen_model_predictions.jsonl", doc)
        self.assertIn("reports/fen_ensemble_eval.json", doc)
        self.assertIn("AI, preprocessing, template matching, and local classifiers create candidates only", doc)


if __name__ == "__main__":
    unittest.main()
