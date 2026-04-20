import unittest

from premium_reflow import _repair_mojibake


class PremiumReflowGeneralizationTests(unittest.TestCase):
    def test_repair_mojibake_repairs_generic_registered_mark_suffix(self):
        self.assertEqual(_repair_mojibake("ACME\u0139\u02dd guide"), "ACME\u00ae guide")

    def test_repair_mojibake_preserves_plain_text_without_sample_specific_dependency(self):
        self.assertEqual(_repair_mojibake("Omega report"), "Omega report")


if __name__ == "__main__":
    unittest.main()
