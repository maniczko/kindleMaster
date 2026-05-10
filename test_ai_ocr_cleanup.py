import unittest

from ai_ocr_cleanup import (
    AIOcrCleanupProviderResult,
    cleanup_suspicious_ocr_fragments,
    select_suspicious_fragments,
)


class FakeProvider:
    name = "fake-ai"

    def __init__(self, *, text: str = "", confidence: float = 0.91, cost: float = 0.0, error: Exception | None = None):
        self.text = text
        self.confidence = confidence
        self.cost = cost
        self.error = error
        self.calls: list[str] = []

    def cleanup_fragment(self, fragment: str) -> AIOcrCleanupProviderResult:
        self.calls.append(fragment)
        if self.error is not None:
            raise self.error
        return AIOcrCleanupProviderResult(
            text=self.text or fragment.replace("Busi- nessAnalysisPlanning", "Business Analysis Planning"),
            confidence=self.confidence,
            estimated_cost=self.cost,
        )


class AIOcrCleanupTests(unittest.TestCase):
    def test_selects_only_fragments_with_existing_artifact_signals(self) -> None:
        text = (
            "Clean introduction about business analysis.\n\n"
            "Broken fragment has Busi- nessAnalysisPlanning and https : //example.com.\n\n"
            "Another clean paragraph remains outside AI scope."
        )

        fragments = select_suspicious_fragments(text)

        self.assertEqual(len(fragments), 1)
        self.assertEqual(fragments[0].index, 2)
        self.assertIn("Busi- nessAnalysisPlanning", fragments[0].text)
        self.assertGreater(fragments[0].artifact_counts["split_word_count"], 0)
        self.assertGreater(fragments[0].artifact_counts["suspicious_url_fragment_count"], 0)

    def test_provider_failure_falls_back_to_deterministic_text(self) -> None:
        text = "Broken fragment has Busi- nessAnalysisPlanning and OCR junk \u00c4."
        provider = FakeProvider(error=RuntimeError("provider unavailable"))

        result = cleanup_suspicious_ocr_fragments(text, provider=provider)

        self.assertEqual(result.text, text)
        self.assertEqual(result.changed_fragment_count, 0)
        self.assertEqual(result.fallback_reason, "provider-error")
        self.assertTrue(result.deterministic_output_preserved)
        self.assertEqual(result.fragments[0].before, text)
        self.assertEqual(result.fragments[0].after, text)
        self.assertFalse(result.fragments[0].accepted)

    def test_cleanup_calls_provider_only_for_suspicious_fragments(self) -> None:
        text = (
            "Clean introduction about business analysis.\n\n"
            "Broken fragment has Busi- nessAnalysisPlanning.\n\n"
            "Clean conclusion remains deterministic."
        )
        provider = FakeProvider(text="Broken fragment has Business Analysis Planning.", confidence=0.95, cost=0.01)

        result = cleanup_suspicious_ocr_fragments(text, provider=provider)

        self.assertEqual(provider.calls, ["Broken fragment has Busi- nessAnalysisPlanning."])
        self.assertIn("Clean introduction about business analysis.", result.text)
        self.assertIn("Clean conclusion remains deterministic.", result.text)
        self.assertIn("Broken fragment has Business Analysis Planning.", result.text)
        self.assertEqual(result.changed_fragment_count, 1)
        self.assertFalse(result.deterministic_output_preserved)

    def test_low_confidence_provider_result_is_rejected(self) -> None:
        text = "Broken fragment has Busi- nessAnalysisPlanning."
        provider = FakeProvider(text="Broken fragment has Business Analysis Planning.", confidence=0.49, cost=0.02)

        result = cleanup_suspicious_ocr_fragments(text, provider=provider, min_confidence=0.8)

        self.assertEqual(result.text, text)
        self.assertEqual(result.changed_fragment_count, 0)
        self.assertEqual(result.estimated_cost, 0.02)
        self.assertEqual(result.fallback_reason, "low-confidence")
        self.assertTrue(result.deterministic_output_preserved)
        self.assertEqual(result.fragments[0].confidence, 0.49)
        self.assertFalse(result.fragments[0].accepted)

    def test_clean_text_is_noop_and_does_not_call_provider(self) -> None:
        text = "This paragraph is clean, readable, and free from visible OCR artifacts."
        provider = FakeProvider(text="This should never be used.")

        result = cleanup_suspicious_ocr_fragments(text, provider=provider)

        self.assertEqual(result.text, text)
        self.assertEqual(provider.calls, [])
        self.assertEqual(result.fragments, [])
        self.assertEqual(result.changed_fragment_count, 0)
        self.assertEqual(result.fallback_reason, "no-suspicious-fragments")
        self.assertTrue(result.deterministic_output_preserved)


if __name__ == "__main__":
    unittest.main()
