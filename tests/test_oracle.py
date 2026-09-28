"""The solver plus perfect extraction should score every visible item."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidate_package"))

from score import score  # noqa: E402

from tests.oracle import oracle_answer  # noqa: E402


class OracleCeilingTest(unittest.TestCase):
    def test_visible_set_is_solved_exactly(self):
        items = json.loads((ROOT / "candidate_package" / "items.json").read_text(encoding="utf-8"))
        key = json.loads((ROOT / "candidate_package" / "visible_key.json").read_text(encoding="utf-8"))
        answers = {item["id"]: oracle_answer(item) for item in items}
        result = score(key, answers, items)
        self.assertEqual(result["macro_exact_match"], 1.0, result["per_case"])
        self.assertEqual(result["inconsistent_label_only"], 0.0)


if __name__ == "__main__":
    unittest.main()
