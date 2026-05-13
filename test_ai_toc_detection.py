import unittest

from ai_toc_detection import (
    AiTocCandidate,
    AiTocProviderResult,
    DeterministicTocResult,
    detect_ai_toc_if_needed,
)


class FakeTocProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def detect_toc(self, context):
        self.calls.append(context)
        if self.error:
            raise self.error
        return self.result


class AiTocDetectionTests(unittest.TestCase):
    def test_low_deterministic_confidence_triggers_ai_provider(self):
        deterministic = DeterministicTocResult(entries=[{"label": "Old", "href": "old.xhtml"}], confidence=0.41)
        provider = FakeTocProvider(
            AiTocProviderResult(
                entries=[AiTocCandidate(label="Chapter 1", href="chapter1.xhtml", confidence=0.91)],
                confidence=0.9,
                estimated_cost_usd=0.02,
            )
        )

        result = detect_ai_toc_if_needed(deterministic, provider=provider, context={"title": "Book"})

        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result.entries, [{"label": "Chapter 1", "href": "chapter1.xhtml", "confidence": 0.91}])
        self.assertEqual(result.audit["status"], "accepted")
        self.assertEqual(result.audit["provider"], "FakeTocProvider")
        self.assertEqual(result.audit["fallback_reason"], "")
        self.assertGreaterEqual(result.audit["elapsed_ms"], 0)
        self.assertEqual(result.audit["estimated_cost_usd"], 0.02)
        self.assertEqual(result.audit["changed_entries"]["added"], ["Chapter 1"])
        self.assertEqual(result.audit["changed_entries"]["removed"], ["Old"])

    def test_high_deterministic_confidence_skips_ai_provider(self):
        deterministic = DeterministicTocResult(entries=[{"label": "Chapter 1", "href": "chapter1.xhtml"}], confidence=0.86)
        provider = FakeTocProvider()

        result = detect_ai_toc_if_needed(deterministic, provider=provider, context={})

        self.assertEqual(provider.calls, [])
        self.assertEqual(result.entries, deterministic.entries)
        self.assertEqual(result.audit["status"], "skipped")
        self.assertEqual(result.audit["fallback_reason"], "deterministic-confidence-high")

    def test_provider_failure_uses_deterministic_fallback(self):
        deterministic = DeterministicTocResult(entries=[{"label": "Fallback", "href": "fallback.xhtml"}], confidence=0.2)
        provider = FakeTocProvider(error=RuntimeError("offline"))

        result = detect_ai_toc_if_needed(deterministic, provider=provider, context={})

        self.assertEqual(result.entries, deterministic.entries)
        self.assertEqual(result.audit["status"], "fallback")
        self.assertEqual(result.audit["fallback_reason"], "provider-failed: offline")

    def test_low_ai_confidence_uses_deterministic_fallback(self):
        deterministic = DeterministicTocResult(entries=[{"label": "Fallback", "href": "fallback.xhtml"}], confidence=0.2)
        provider = FakeTocProvider(
            AiTocProviderResult(
                entries=[AiTocCandidate(label="Maybe", href="maybe.xhtml", confidence=0.59)],
                confidence=0.59,
                estimated_cost_usd=0.01,
            )
        )

        result = detect_ai_toc_if_needed(deterministic, provider=provider, context={})

        self.assertEqual(result.entries, deterministic.entries)
        self.assertEqual(result.audit["status"], "fallback")
        self.assertEqual(result.audit["fallback_reason"], "ai-confidence-low")
        self.assertEqual(result.audit["estimated_cost_usd"], 0.01)

    def test_rejects_caption_chart_and_ad_labels(self):
        deterministic = DeterministicTocResult(entries=[{"label": "Fallback", "href": "fallback.xhtml"}], confidence=0.1)
        provider = FakeTocProvider(
            AiTocProviderResult(
                entries=[
                    AiTocCandidate(label="Figure 1. Sales by market", href="fig.xhtml", confidence=0.95),
                    AiTocCandidate(label="Chart: Revenue", href="chart.xhtml", confidence=0.94),
                    AiTocCandidate(label="Advertisement", href="ad.xhtml", confidence=0.99),
                    AiTocCandidate(label="Chapter 2: Real Work", href="chapter2.xhtml", confidence=0.9),
                ],
                confidence=0.91,
                estimated_cost_usd=0.03,
            )
        )

        result = detect_ai_toc_if_needed(deterministic, provider=provider, context={})

        self.assertEqual(result.entries, [{"label": "Chapter 2: Real Work", "href": "chapter2.xhtml", "confidence": 0.9}])
        self.assertEqual(
            result.audit["rejected_entries"],
            [
                {"label": "Figure 1. Sales by market", "reason": "non-content-label"},
                {"label": "Chart: Revenue", "reason": "non-content-label"},
                {"label": "Advertisement", "reason": "non-content-label"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
